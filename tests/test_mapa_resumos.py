import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import shapefile
from PySide6.QtWidgets import QApplication

from app.core import camadas, mapa_dados, projeto, resumos_cenarios
from app.core.db import conectar
from app.screens.resumos_cenarios import TelaResumosCenarios
from app.widgets.mapa_dialogs import DialogoConsultaSimulacao, DialogoJoin


class MapaResumos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.temp.name)
        self.arquivo = self.raiz / 'resumos.mogno'
        projeto.novo_projeto(self.arquivo)
        conn = conectar()
        try:
            conn.executemany("INSERT INTO simulacao_cenarios (id, nome, idade_corte_raso, status) VALUES (?, ?, 20, 'Gerado')", [(1, 'C1'), (2, 'C2'), (3, 'C3')])
            conn.commit()
        finally:
            conn.close()
        self.mock = patch.object(resumos_cenarios, 'carregar_fonte', side_effect=self.fonte)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        projeto.fechar_projeto()
        self.temp.cleanup()

    @staticmethod
    def fonte(conn, cid, tipo):
        return pd.DataFrame(dict(cenario=[f'C{cid}'] * 4, talhao=['1', '1', '2', '2'],
                                 idade=[10, 20, 10, 20], volume=[10.0 + cid * 100, 20.0 + cid * 100, 30.0 + cid * 100, 40.0 + cid * 100],
                                 classe=['A', 'A', 'B', 'B']))

    @staticmethod
    def cfg(filtro=None, pivo=False):
        return dict(fonte_a='populacao', fonte_b=None, tipo_join='left', chaves_a=[], chaves_b=[],
                    grupos=['populacao.cenario', 'populacao.talhao'],
                    metricas=[dict(coluna='populacao.volume', agregacao='sum', alias='volume_total', arredondar=1),
                              dict(coluna='populacao.classe', agregacao='first', alias='classe')],
                    filtro_chaves=filtro or {},
                    pivo=dict(coluna='populacao.idade', valor='populacao.volume', agregacao='sum', preencher=0) if pivo else {})

    def importar_shape(self):
        arquivo = self.raiz / 'talhoes'
        with shapefile.Writer(str(arquivo), shapeType=shapefile.POINT) as writer:
            writer.field('talhao', 'C')
            for i, chave in enumerate(['1', '2', 'X']):
                writer.point(i, 0)
                writer.record(chave)
        return camadas.importar(arquivo.with_suffix('.shp'))

    def test_tabela_completa_inclui_cenarios_fora_da_previa(self):
        conn = conectar()
        try:
            config = self.cfg()
            previa = resumos_cenarios.processar(conn, config, limite_cenarios=2)
            self.assertEqual(len(previa), 4)
            resultado = resumos_cenarios.disponibilizar_no_mapa(conn, 'resumo_talhoes', config)
            self.assertEqual(resultado['linhas'], 6)
            self.assertIn('resumo_talhoes', mapa_dados.fontes_simulacao(conn))
            self.assertEqual(conn.execute('SELECT DISTINCT cenario FROM resumo_talhoes ORDER BY cenario').fetchall(), [('C1',), ('C2',), ('C3',)])
        finally:
            conn.close()

    def test_join_do_resumo_filtrado_indicadores_pivo_e_reabertura(self):
        camada = self.importar_shape()
        filtro = dict(colunas=['populacao.cenario'], valores=[['C3']])
        config = self.cfg(filtro, pivo=True)
        conn = conectar()
        try:
            self.assertTrue(resumos_cenarios.processar(conn, config, limite_cenarios=2).empty)
            resumos_cenarios.disponibilizar_no_mapa(conn, 'resumo_filtrado', config, 'Resumo C3')
            esperado = resumos_cenarios.processar(conn, config)
            real = pd.read_sql_query('SELECT * FROM resumo_filtrado', conn)
            pd.testing.assert_frame_equal(real, esperado, check_dtype=False)
            resultado = mapa_dados.aplicar_join(conn, camada, 'resumo_filtrado', 'talhao', 'talhao', ['volume_total', 'classe', '10', '20'])
            self.assertEqual(resultado['correspondencias'], 2)
            self.assertEqual(resultado['sem_correspondencia'], 1)
            self.assertEqual(conn.execute(f'SELECT sim_volume_total, sim_classe FROM "{camada}" ORDER BY id').fetchall(), [(630, 'A'), (670, 'B'), (None, None)])
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        consulta = DialogoConsultaSimulacao()
        self.assertEqual(consulta.fonte.currentText(), 'resumo_filtrado')
        consulta.adicionar_filtro(dict(coluna='talhao', operador='=', valor='1'))
        consulta.salvar()
        join = DialogoJoin(camada)
        self.assertEqual(join.fonte.currentText(), 'resumo_filtrado')
        join.recorte.setCurrentIndex(1)
        self.assertEqual(join.configuracao()['filtros'][0]['valor'], '1')
        projeto.fechar_projeto()
        projeto.confirmar_abertura(*projeto.abrir_projeto(self.arquivo))
        conn = conectar()
        try:
            self.assertIn('resumo_filtrado', mapa_dados.fontes_simulacao(conn))
            self.assertEqual(json.loads(conn.execute('SELECT configuracao_json FROM mapa_fontes_resumos').fetchone()[0]), config)
            self.assertEqual(mapa_dados.obter_estado(conn, 'consulta_simulacao')['filtros'][0]['valor'], '1')
        finally:
            conn.close()
        consulta.deleteLater()
        join.deleteLater()

    def test_botao_dos_resumos_disponibiliza_a_mesma_configuracao(self):
        tela = TelaResumosCenarios()
        config = self.cfg(dict(colunas=['populacao.cenario'], valores=[['C3']]))
        tela.nome_tabela.setText('resumo_da_tela')
        tela.nome_config.setText('Configuração do mapa')
        with patch.object(tela, '_cfg', return_value=config), patch('app.screens.resumos_cenarios.QMessageBox.critical') as erro:
            tela._disponibilizar_mapa()
            erro.assert_not_called()
        self.assertEqual(tela.tabela_previa.rowCount(), 2)
        self.assertIn('resumo_da_tela disponível no mapa', tela.label_status.text())
        consulta = DialogoConsultaSimulacao()
        self.assertEqual(consulta.fonte.currentText(), 'resumo_da_tela')
        self.assertIn('volume_total', consulta.tabela.colunas)
        tela.deleteLater()
        consulta.deleteLater()

    def test_republicar_atualiza_tabela_e_protege_tabelas_originais(self):
        conn = conectar()
        try:
            resumos_cenarios.disponibilizar_no_mapa(conn, 'resumo_atual', self.cfg())
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM resumo_atual').fetchone()[0], 6)
            cfg = self.cfg(dict(colunas=['populacao.cenario', 'populacao.talhao'], valores=[['C3', '1']]))
            resumos_cenarios.disponibilizar_no_mapa(conn, 'resumo_atual', cfg)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM resumo_atual').fetchone()[0], 1)
            with self.assertRaisesRegex(ValueError, 'outra tabela'):
                resumos_cenarios.disponibilizar_no_mapa(conn, 'simulacao_cenarios', cfg)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM simulacao_cenarios').fetchone()[0], 3)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
