"""Integração shapefile -> projeto -> mapa -> variáveis, sem janela visível."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import shapefile
from PySide6.QtWidgets import QApplication

from app.core import camadas, construtores, mapa_dados, projeto, simulacao
from app.core.db import conectar
from app.screens.mapa import TelaMapa
from app.screens.construtor_variaveis import TelaConstrutorVariaveis


class IntegracaoMapa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.temp.name)
        self.arquivo = self.raiz / 'teste.mogno'
        projeto.novo_projeto(self.arquivo)

    def tearDown(self):
        projeto.fechar_projeto()
        self.temp.cleanup()

    def criar_shape(self, nome='Talhões', tipo=shapefile.POLYGON):
        destino = self.raiz / nome
        with shapefile.Writer(str(destino), shapeType=tipo, encoding='cp1252') as w:
            w.field('ID', 'C')
            w.field('area', 'N', decimal=2)
            w.field('nome', 'C')
            for i in range(2):
                if tipo == shapefile.POLYGON:
                    w.poly([[[i * 20, 0], [i * 20, 10], [i * 20 + 10, 10], [i * 20 + 10, 0], [i * 20, 0]]])
                elif tipo == shapefile.POINT:
                    w.point(i * 20, 0)
                elif tipo == shapefile.POLYLINE:
                    w.line([[[i * 20, 0], [i * 20 + 10, 10]]])
                w.record('repetido', 12.5 + i, 'São João')
        destino.with_suffix('.cpg').write_text('1252')
        return destino.with_suffix('.shp')

    def test_atributos_calculo_e_reabertura(self):
        tabela = camadas.importar(self.criar_shape())
        conn = conectar()
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{tabela}"', conn)
            self.assertEqual(df['atributo_id'].tolist(), ['repetido', 'repetido'])
            self.assertEqual(df['nome'].tolist(), ['São João', 'São João'])
            construtores.gravar_saidas_como_colunas(conn, tabela, df, {'dobro': df['area'] * 2})
        finally:
            conn.close()
        projeto.sincronizar()
        mapa = TelaMapa()
        self.assertEqual(len(mapa.canvas.scene().items()), 2)
        self.assertIn('dobro', mapa.tabela.colunas)
        mapa.botao_atributos.setChecked(True)
        self.assertFalse(mapa.painel.isHidden())
        builder = TelaConstrutorVariaveis()
        self.assertGreaterEqual(builder.combo_tabela_origem.findText(tabela), 0)
        builder.combo_tabela_origem.setCurrentText(tabela)
        builder._atualizar_colunas_disponiveis()
        self.assertIn('area', builder._colunas_disponiveis)
        self.assertIn('atributo_id', builder._colunas_disponiveis)
        projeto.fechar_projeto()
        projeto.confirmar_abertura(*projeto.abrir_projeto(self.arquivo))
        mapa.recarregar_lista()
        self.assertEqual(len(mapa.canvas.scene().items()), 2)
        conn = conectar()
        try:
            self.assertEqual(conn.execute(f'SELECT dobro FROM "{tabela}" ORDER BY id').fetchall(), [(25.0,), (27.0,)])
        finally:
            conn.close()
        mapa.deleteLater()
        builder.deleteLater()

    def test_pontos_linhas_e_troca_projeto(self):
        for tipo in (shapefile.POINT, shapefile.POLYLINE):
            camadas.importar(self.criar_shape(str(tipo), tipo))
        mapa = TelaMapa()
        self.assertEqual(mapa.combo.count(), 2)
        mapa.combo.setCurrentIndex(1)
        self.assertEqual(len(mapa.canvas.scene().items()), 2)
        projeto.novo_projeto(self.raiz / 'outro.mogno')
        mapa.novo_registro()
        mapa.recarregar_lista()
        self.assertEqual(mapa.combo.count(), 0)
        self.assertEqual(len(mapa.canvas.scene().items()), 0)
        mapa.deleteLater()

    def test_importacao_invalida_nao_cria_camada(self):
        with self.assertRaises(ValueError):
            camadas.importar(self.raiz / 'ausente.shp')
        conn = conectar()
        try:
            self.assertEqual(camadas.listar(conn), [])
        finally:
            conn.close()

    def test_visao_geral_inicial_e_acesso_ao_mapa(self):
        from app.window import JanelaPrincipal
        # A limpeza de órfãos do boot não deve apagar o projeto do teste.
        with patch.object(projeto, 'limpar_arquivos_orfaos'):
            janela = JanelaPrincipal()
        self.assertEqual(janela._tela_atual, 'visao_geral')
        self.assertNotIn('mapa', janela.telas)
        janela.mostrar_tela('mapa')
        self.assertIsInstance(janela.telas['mapa'], TelaMapa)
        janela.close()
        janela.deleteLater()

    def test_join_pela_interface_cores_e_persistencia(self):
        from app.widgets.mapa_dialogs import DialogoConsultaSimulacao, DialogoJoin, DialogoValoresUnicos
        tabela = camadas.importar(self.criar_shape())
        conn = conectar()
        try:
            fonte = simulacao.TABELA_POPULACAO
            conn.execute(f'CREATE TABLE "{fonte}" (id INTEGER PRIMARY KEY, talhao TEXT, idade INTEGER, volume REAL, classe TEXT)')
            conn.executemany(f'INSERT INTO "{fonte}" VALUES (?, ?, ?, ?, ?)',
                             [(1, '12.5', 10, 100, 'A'), (2, '13.5', 10, 200, 'B'),
                              (3, '12.5', 20, 300, 'C'), (4, '13.5', 20, 400, 'D')])
            conn.commit()
        finally:
            conn.close()
        consulta = DialogoConsultaSimulacao()
        consulta.adicionar_filtro(dict(coluna='idade', operador='=', valor='20'))
        consulta.salvar()
        self.assertEqual(len(consulta.tabela.linhas_originais), 2)
        mapa = TelaMapa()
        join = DialogoJoin(tabela, mapa)
        join.chave_shape.setCurrentText('area')
        join.chave_sim.setCurrentText('talhao')
        join.modo.setCurrentIndex(1)
        join.recorte.setCurrentIndex(1)
        join._marcar(False)
        for i in range(join.colunas.count()):
            item = join.colunas.item(i)
            if item.text() in ('volume', 'classe'):
                from PySide6.QtCore import Qt
                item.setCheckState(Qt.CheckState.Checked)
        with patch('app.widgets.mapa_dialogs.QMessageBox.warning') as aviso:
            join.aplicar()
            worker = join.thread
            self.assertIsNotNone(worker)
            self.assertTrue(worker.wait(10000))
            self.app.processEvents()
            aviso.assert_not_called()
        self.assertEqual(join.result(), join.DialogCode.Accepted)
        mapa.recarregar_lista()
        self.assertIn('sim_classe', mapa._colunas)
        indice = mapa._colunas.index('sim_volume')
        self.assertEqual([r[indice] for r in mapa._linhas], [300, 400])
        cores = DialogoValoresUnicos(mapa._colunas, mapa._linhas, parent=mapa)
        cores.coluna.setCurrentText('sim_classe')
        cores._cores['texto:C'] = '#ff0000'
        cores._cores['texto:D'] = '#0000ff'
        mapa._estilo = cores.configuracao()
        mapa_dados.salvar_estado('estilo:' + tabela, mapa._estilo)
        mapa._aplicar_estilo()
        self.assertEqual(mapa.canvas.itens_feicoes[1].brush().color().name(), '#ff0000')
        self.assertEqual(mapa.canvas.itens_feicoes[2].brush().color().name(), '#0000ff')
        self.assertEqual(mapa.legenda.topLevelItemCount(), 2)
        # Ordenar a tabela não pode trocar as cores entre geometrias.
        mapa.tabela._ordenar(indice)
        mapa.tabela._ordenar(indice)
        mapa._aplicar_estilo()
        self.assertEqual(mapa.canvas.itens_feicoes[1].brush().color().name(), '#ff0000')
        projeto.fechar_projeto()
        projeto.confirmar_abertura(*projeto.abrir_projeto(self.arquivo))
        mapa.recarregar_lista()
        self.assertEqual(mapa.canvas.itens_feicoes[2].brush().color().name(), '#0000ff')
        consulta_reaberta = DialogoConsultaSimulacao()
        self.assertEqual(len(consulta_reaberta._linhas_filtro), 1)
        self.assertEqual(len(consulta_reaberta.tabela.linhas_originais), 2)
        for widget in (consulta, consulta_reaberta, cores, join, mapa):
            widget.deleteLater()

    def test_consulta_sem_simulacao_e_limpeza_de_estilo_ao_trocar_projeto(self):
        from app.widgets.mapa_dialogs import DialogoConsultaSimulacao
        consulta = DialogoConsultaSimulacao()
        self.assertEqual(consulta.fonte.count(), 0)
        self.assertFalse(consulta.atualizar())
        tabela = camadas.importar(self.criar_shape())
        mapa_dados.salvar_estado('estilo:' + tabela, dict(coluna='nome', cores={'texto:São João': '#123456'}))
        mapa = TelaMapa()
        self.assertFalse(mapa.legenda.isHidden())
        projeto.novo_projeto(self.raiz / 'vazio.mogno')
        mapa.novo_registro()
        mapa.recarregar_lista()
        self.assertTrue(mapa.legenda.isHidden())
        self.assertEqual(mapa._estilo, {})
        consulta.deleteLater()
        mapa.deleteLater()

    def test_simulacao_oferece_filtros_e_bloqueia_consulta_durante_geracao(self):
        from app.screens.simulacao import TelaSimulacao
        tela = TelaSimulacao()
        self.assertEqual(tela.botao_consulta_mapa.text(), 'Tabela e filtros...')
        tela._gerando = True
        with patch('app.screens.simulacao.QMessageBox.information') as aviso:
            tela._abrir_consulta_mapa()
            aviso.assert_called_once()
        tela.deleteLater()

    def test_filtro_salvo_com_coluna_removida_nao_e_reinterpretado(self):
        from app.widgets.mapa_dialogs import DialogoConsultaSimulacao
        fonte = simulacao.TABELA_POPULACAO
        conn = conectar()
        try:
            conn.execute(f'CREATE TABLE "{fonte}" (id INTEGER PRIMARY KEY, idade INTEGER)')
            conn.commit()
        finally:
            conn.close()
        mapa_dados.salvar_estado('consulta_simulacao', dict(fonte=fonte, combinacao='E',
            filtros=[dict(coluna='coluna_removida', operador='=', valor='10')]))
        consulta = DialogoConsultaSimulacao()
        self.assertEqual(consulta.configuracao()['filtros'][0]['coluna'], 'coluna_removida')
        self.assertIn('Filtro inválido', consulta.status.text())
        consulta.deleteLater()


if __name__ == '__main__':
    unittest.main()
