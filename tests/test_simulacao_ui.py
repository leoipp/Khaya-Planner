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
