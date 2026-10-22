"""Integração dos modos e do layout da Simulação, com projeto temporário."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase

from app.core import projeto, simulacao
from app.core.db import conectar
from app.screens.simulacao import TelaSimulacao
from app.theme import qss


class SimulacaoUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for nome in ('segoeui.ttf', 'segoeuib.ttf'):
            fonte = Path('C:/Windows/Fonts', nome)
            if fonte.exists():
                QFontDatabase.addApplicationFont(str(fonte))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        projeto.novo_projeto(Path(self.temp.name, 'ui.mogno'))
        self.tela = TelaSimulacao()
        self.tela.resize(980, 740)
        self.tela.show()
        self.estabilizar()

    def tearDown(self):
        self.tela.dialogo_graficos.close()
        self.tela.close()
        self.tela.deleteLater()
        self.app.processEvents()
        projeto.fechar_projeto()
        self.temp.cleanup()

    def estabilizar(self):
        for _ in range(10):
            self.app.processEvents()

    def test_resultados_ocultos_nao_consultam_e_grafico_abre_sob_demanda(self):
        self.tela._opcoes_grafico_coluna = {'': ('volume', False)}
        with patch('app.screens.simulacao.simulacao.dados_grafico_resultado') as consulta:
            self.tela._atualizar_grafico_resultado()
            consulta.assert_not_called()
        self.assertIsNone(self.tela.grafico_resultado._grafico)
        self.tela._opcoes_grafico_coluna = {'': (None, False)}
        self.tela._abrir_janela_graficos()
        self.estabilizar()
        self.assertIsNotNone(self.tela.grafico_resultado._grafico)
        self.assertTrue(self.tela.dialogo_graficos.isVisible())

    def test_workspace_filtra_cenarios_e_acoes_acompanham_selecao(self):
        conn = conectar()
        conn.executemany(
            "INSERT INTO simulacao_cenarios (nome, status) VALUES (?, ?)",
            [('Referência', 'Pendente'), ('Ciclo curto', 'Gerado'), ('Ciclo longo', 'Erro: exemplo')])
        conn.commit()
        conn.close()
        self.tela._carregar_cenarios()
        self.assertFalse(self.tela.botao_excluir_cenario.isEnabled())
        self.tela.busca_cenarios.setText('ciclo')
        self.assertEqual(len(self.tela.tabela_cenarios.linhas_originais), 2)
        self.tela.filtro_status_cenarios.setCurrentText('Gerado')
        linhas = self.tela.tabela_cenarios.linhas_originais
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0][1][0], 'Ciclo curto')
        self.tela.tabela_cenarios.selecionar_id(linhas[0][0])
        self.assertTrue(self.tela.botao_ativar_cenario.isEnabled())
        self.tela.busca_cenarios.setText('inexistente')
        self.assertFalse(self.tela.botao_ativar_cenario.isEnabled())
        self.tela.busca_cenarios.clear()
        self.tela.filtro_status_cenarios.setCurrentIndex(0)
        self.assertEqual(len(self.tela.tabela_cenarios.linhas_originais), 3)

    def test_workspace_lateral_e_grade_sem_comprimir_formulario(self):
        self.tela.resize(1440, 900)
        self.tela.combo_modo_simulacao.setCurrentIndex(1)
        self.estabilizar()
        self.assertEqual(self.tela._grid.getItemPosition(
            self.tela._grid.indexOf(self.tela.cartao_eventos))[:2], (0, 1))
        self.assertGreater(self.tela.tabela_cenarios.height(), 220)
        self.tela.combo_modo_simulacao.setCurrentIndex(2)
        self.estabilizar()
        self.assertEqual(self.tela._grid.getItemPosition(
            self.tela._grid.indexOf(self.tela.cartao_eventos))[:2], (0, 0))

    def test_modos_preservam_valores_e_exibem_formulario_correto(self):
        self.tela.entry_idade_corte_raso.setText('25')
        self.tela.entry_idade_min_corte_raso.setText('20')
        for indice in [1, 2, 0, 2, 1]:
            self.tela.combo_modo_simulacao.setCurrentIndex(indice)
            self.estabilizar()
            self.assertEqual(self.tela.combo_modo_simulacao.currentIndex(), indice)
            self.assertEqual(self.tela.checkbox_multiplos_cenarios.isChecked(), indice == 1)
            self.assertEqual(self.tela.checkbox_grade_automatica.isChecked(), indice == 2)
            self.assertEqual(self.tela.painel_cenario_manual.isVisible(), indice == 1)
            self.assertEqual(self.tela.painel_grade_cenarios.isVisible(), indice == 2)
            self.assertEqual(self.tela._painel_eventos_manuais.isVisible(), indice != 2)
            self.assertEqual(self.tela.botao_gerar.isVisible(), indice == 0)
            self.assertEqual(self.tela.entry_idade_corte_raso.text(), '25')
            self.assertEqual(self.tela.entry_idade_min_corte_raso.text(), '20')

    def test_cadastro_manual_grava_os_eventos_do_formulario(self):
        self.tela.combo_modo_simulacao.setCurrentIndex(1)
        self.tela.entry_idade_raleio.setText('6')
        self.tela.entry_idade_desbaste_1.setText('12')
        self.tela.entry_idade_desbaste_2.setText('18')
        self.tela.entry_idade_corte_raso.setText('25')
        for combo, mapa in [
            (self.tela.combo_intensidade_raleio, self.tela._mapa_intensidade_raleio),
            (self.tela.combo_intensidade_desbaste_1, self.tela._mapa_intensidade_desbaste_1),
            (self.tela.combo_intensidade_desbaste_2, self.tela._mapa_intensidade_desbaste_2),
        ]:
            combo.clear()
            combo.addItem('20%')
            mapa['20%'] = 20.
        self.tela.entry_nome_cenario.setText('Manejo teste')
        with patch.object(QMessageBox, 'warning') as aviso:
            self.tela._adicionar_cenario()
            aviso.assert_not_called()
        conn = conectar()
        try:
            linha = conn.execute('SELECT nome, idade_raleio, idade_desbaste_1, idade_desbaste_2, '
                                 'idade_corte_raso, intensidade_raleio FROM simulacao_cenarios').fetchone()
            self.assertEqual(tuple(linha), ('Manejo teste', 6, 12, 18, 25, 20))
        finally:
            conn.close()
        self.assertIn('cadastrados: 1', self.tela.label_resumo_cenarios.text())
        self.assertFalse(self.tela.entry_nome_cenario.text())
        self.tela.abas_simulacao.setCurrentIndex(2)
        self.estabilizar()
        self.assertTrue(self.tela.painel_ranking_kpi.isVisible())
        self.assertFalse(self.tela.cartao_cenarios.isVisible())

    def test_sem_manejo_nos_tres_modos(self):
        for check in self.tela.checks_manejos.values():
            check.setChecked(False)
        self.tela.entry_idade_raleio.setText('inválido preservado')
        with patch.object(QMessageBox, 'warning') as aviso:
            valores = self.tela._ler_valores_evento()
            self.assertTrue(all(v is None for k, v in valores.items() if k.startswith('idade_')))
            self.assertTrue(all(v == 0 for k, v in valores.items() if k.startswith('intensidade_')))
            self.tela.combo_modo_simulacao.setCurrentIndex(1)
            self.tela.entry_nome_cenario.setText('Sem manejo')
            self.tela._adicionar_cenario()
            aviso.assert_not_called()
        conn = conectar()
        self.assertIsNone(conn.execute('SELECT idade_corte_raso FROM simulacao_cenarios').fetchone()[0])
        conn.close()
        self.tela.combo_modo_simulacao.setCurrentIndex(2)
        for check in self.tela.checks_manejos_grade.values():
            check.setChecked(False)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'warning') as aviso:
            self.tela._gerar_grade_cenarios()
            aviso.assert_not_called()
        conn = conectar()
        linhas = conn.execute('SELECT idade_raleio, idade_desbaste_1, idade_desbaste_2, idade_corte_raso FROM simulacao_cenarios').fetchall()
        conn.close()
        self.assertEqual([tuple(r) for r in linhas], [(None, None, None, None)])
        self.assertEqual(self.tela.entry_idade_raleio.text(), 'inválido preservado')
        self.assertFalse(self.tela.entry_idade_raleio.isEnabled())

    def test_grade_nao_inventa_idades_de_manejos_omitidos(self):
        self.tela.combo_modo_simulacao.setCurrentIndex(2)
        for chave, check in self.tela.checks_manejos_grade.items():
            check.setChecked(chave in ('desbaste_1', 'corte_raso'))
        for prefixo, idade in [('desbaste_1', '1'), ('corte_raso', '2')]:
            for parte in ('min', 'max'):
                getattr(self.tela, f'entry_idade_{parte}_{prefixo}').setText(idade)
            getattr(self.tela, f'entry_idade_passo_{prefixo}').setText('1')
        for parte in ('min', 'max'):
            combo = getattr(self.tela, f'combo_intensidade_{parte}_desbaste_1')
            combo.addItem('20%')
        self.tela._mapa_intensidade_desbaste_1['20%'] = .2
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'warning') as aviso:
            self.tela._gerar_grade_cenarios()
            aviso.assert_not_called()
        conn = conectar()
        linha = conn.execute('SELECT idade_raleio, idade_desbaste_1, idade_desbaste_2, idade_corte_raso FROM simulacao_cenarios').fetchone()
        conn.close()
        self.assertEqual(tuple(linha), (None, 1, None, 2))

    def test_grade_inclui_com_e_sem_manejo_sem_duplicar_ausencia(self):
        self.tela.combo_modo_simulacao.setCurrentIndex(2)
        for chave, check in self.tela.checks_manejos_grade.items():
            check.setChecked(chave in ('desbaste_1', 'corte_raso'))
        self.tela.checks_sem_manejo_grade['raleio'].setChecked(True)
        self.tela.checks_sem_manejo_grade['desbaste_1'].setChecked(True)
        self.tela.checks_sem_manejo_grade['corte_raso'].setChecked(True)
        for prefixo, minimo, maximo, passo in [('desbaste_1', '4', '6', '2'), ('corte_raso', '15', '15', '1')]:
            for parte, valor in [('min', minimo), ('max', maximo), ('passo', passo)]:
                getattr(self.tela, f'entry_idade_{parte}_{prefixo}').setText(valor)
        self.tela._mapa_intensidade_desbaste_1 = {'20%': .2, '30%': .3}
        for parte, escolhido in [('min', '20%'), ('max', '30%')]:
            combo = getattr(self.tela, f'combo_intensidade_{parte}_desbaste_1')
            combo.clear();combo.addItems(['20%', '30%']);combo.setCurrentText(escolhido)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'warning') as aviso:
            self.tela._gerar_grade_cenarios()
            aviso.assert_not_called()
        conn = conectar()
        try:
            linhas = [tuple(r) for r in conn.execute('SELECT idade_raleio, intensidade_raleio, idade_desbaste_1, intensidade_desbaste_1, idade_corte_raso FROM simulacao_cenarios')]
        finally:conn.close()
        self.assertEqual(len(linhas), 10)
        self.assertEqual(len(set(linhas)), 10)
        self.assertTrue(all(r[:2] == (None, 0.) for r in linhas))
        self.assertEqual(sum(r[2] is None for r in linhas), 2)
        self.assertIn((None, 0., None, 0., 15), linhas)
        self.assertIn((None, 0., None, 0., None), linhas)
        self.assertIn((None, 0., 6, .3, 15), linhas)

    def test_exportar_excel_em_multiplos_inclui_todos_os_cenarios(self):
        import pandas as pd
        from PySide6.QtWidgets import QFileDialog
        conn = conectar()
        try:
            conn.execute(f'CREATE TABLE "{simulacao.TABELA_LOTE_POPULACAO}" (id INTEGER, cenario_id INTEGER, volume REAL)')
            conn.execute(f'CREATE TABLE "{simulacao.TABELA_LOTE_VOLUME_SORTIMENTO}" (id INTEGER, cenario_id INTEGER, volume REAL)')
            for i in range(1, 4):
                conn.execute("INSERT INTO simulacao_cenarios(id,nome,idade_corte_raso,status) VALUES(?,?,?,?)", (i, f'Manual {i}', 14+i, 'Gerado' if i < 3 else 'Pendente'))
                conn.execute(f'INSERT INTO "{simulacao.TABELA_LOTE_POPULACAO}" VALUES(?,?,?)', (1, i, i*100.))
                conn.execute(f'INSERT INTO "{simulacao.TABELA_LOTE_VOLUME_SORTIMENTO}" VALUES(?,?,?)', (1, i, i*80.))
            conn.commit()
        finally:conn.close()
        for modo, quantidade in [(1, 2), (2, 3)]:
            if quantidade == 3:
                conn = conectar()
                try:conn.execute("UPDATE simulacao_cenarios SET status='Gerado' WHERE id=3");conn.commit()
                finally:conn.close()
            self.tela.combo_modo_simulacao.setCurrentIndex(modo)
            destino = Path(self.temp.name, f'exportacao_{modo}.xlsx')
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(destino), '')), \
                 patch.object(QMessageBox, 'information'), patch.object(QMessageBox, 'warning') as aviso, \
                 patch.object(QMessageBox, 'critical') as erro:
                self.tela.exportar_excel()
                aviso.assert_not_called();erro.assert_not_called()
            with pd.ExcelFile(destino) as livro:
                for aba, fator in [('Simulação', 100.), ('Volume por Sortimento', 80.)]:
                    df = pd.read_excel(livro, sheet_name=aba)
                    self.assertEqual(df['cenario'].tolist(), [f'Manual {i}' for i in range(1, quantidade+1)])
                    self.assertEqual(df['volume'].tolist(), [i*fator for i in range(1, quantidade+1)])
                    self.assertEqual(df['idade_corte_raso'].tolist(), [14+i for i in range(1, quantidade+1)])
            self.assertIn('todos os cenários', self.tela.botao_exportar.text())
        self.tela.combo_modo_simulacao.setCurrentIndex(0)
        self.assertIn('cenário ativo', self.tela.botao_exportar.text())
        with patch.object(self.tela, 'exportar_todos_cenarios') as todos, \
             patch.object(self.tela, '_carregar_dados_geracao', return_value=(None, None)) as ativo, \
             patch.object(QMessageBox, 'warning'):
            self.tela.exportar_excel()
            todos.assert_not_called();ativo.assert_called_once()

    def test_formularios_cabem_nas_larguras_e_temas(self):
        anterior = self.app.styleSheet()
        try:
            for tema in ('light', 'dark'):
                self.app.setStyleSheet(qss.gerar(tema))
                for largura in (760, 980, 1300):
                    self.tela.resize(largura, 740)
                    for modo in (0, 1, 2):
                        self.tela.combo_modo_simulacao.setCurrentIndex(modo)
                        for aba in range(3):
                            self.tela.abas_simulacao.setCurrentIndex(aba)
                            self.estabilizar()
                            self.assertEqual(self.tela.width(), largura)
                            scroll = self.tela.abas_simulacao.currentWidget()
                            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
                            # Conteúdo pode rolar verticalmente; campos não podem
                            # escapar horizontalmente do seu painel.
                            for campo in self.tela.findChildren(QLineEdit) + self.tela.findChildren(QComboBox):
                                if not campo.isVisible() or self.tela.dialogo_graficos.isAncestorOf(campo):
                                    continue
                                self.assertGreaterEqual(campo.x(), 0)
                                self.assertLessEqual(campo.geometry().right(), campo.parentWidget().width())
                                if scroll.widget().isAncestorOf(campo):
                                    posicao = campo.mapTo(scroll.widget(), QPoint(0, 0))
                                    self.assertGreaterEqual(posicao.x(), 0)
                                    self.assertLessEqual(posicao.x() + campo.width(), scroll.widget().width())
        finally:
            self.app.setStyleSheet(anterior)

    def test_agregacao_recarrega_o_painel_salvo(self):
        self.tela.abas_simulacao.setCurrentIndex(1)
        self.tela.checkbox_tabela_agregacao.setChecked(True)
        self.estabilizar()
        self.assertTrue(self.tela._moldura_volume_classes.isVisible())
        self.tela.checkbox_tabela_agregacao.setChecked(False)
        self.assertTrue(self.tela._moldura_volume_classes.isHidden())
        conn = conectar()
        try:
            simulacao.salvar_usar_tabela_agregacao_volume(conn, True)
            conn.commit()
        finally:
            conn.close()
        self.tela.recarregar_lista()
        self.estabilizar()
        self.assertTrue(self.tela._moldura_volume_classes.isVisible())


if __name__ == '__main__':
    unittest.main()
