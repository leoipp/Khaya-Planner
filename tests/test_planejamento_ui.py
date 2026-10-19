"""Preserva os parâmetros do planejamento e a navegação após a reorganização."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

from app.screens.planejamento import TelaPlanejamento
from app.theme import qss


class PlanejamentoUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')

    def setUp(self):
        self.anterior = self.app.styleSheet()
        with patch('app.screens.planejamento.projeto.projeto_aberto', return_value=False):
            self.tela = TelaPlanejamento()
        self.tela.show()

    def tearDown(self):
        self.tela.close()
        self.tela.deleteLater()
        self.app.processEvents()
        self.app.setStyleSheet(self.anterior)

    def estabilizar(self):
        for _ in range(4):
            self.app.processEvents()

    def test_parametros_e_restricoes_preservados_entre_abas(self):
        t = self.tela
        t._esquema = {'origem': ['TALHÃO', 'cenario', 'Area', 'Idade', 'VPL', '2027']}
        t.tabela.addItem('origem')
        t.campos['demanda'].setValue(12345.6789)
        t.campos['gap_alpha'].setValue(.02)
        t.volume_ha.setChecked(True)
        t._adicionar_restricao(dados=dict(coluna='2027', operador='<=', limite=1000, por_ha=True))
        antes = t._cfg()
        for aba in (1, 2, 0):
            t.abas.setCurrentIndex(aba)
            self.estabilizar()
            self.assertEqual(t._cfg(), antes)
            self.assertTrue(t.executar.isVisible())
        self.assertAlmostEqual(antes['demanda'], 12345.6789)
        self.assertAlmostEqual(antes['gap_alpha'], .0002)
        self.assertEqual(antes['restricoes'], [dict(coluna='2027', operador='<=', limite=1000, por_ha=True)])

    def test_layout_sem_transbordamento_horizontal(self):
        t = self.tela
        for tema in ('light', 'dark'):
            self.app.setStyleSheet(qss.gerar(tema))
            for largura in (760, 980, 1300):
                t.resize(largura, 740)
                for aba in range(3):
                    t.abas.setCurrentIndex(aba)
                    self.estabilizar()
                    self.assertEqual(t.width(), largura)
                    scroll = t.abas.currentWidget()
                    self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
                    for campo in t.findChildren(QLineEdit) + t.findChildren(QComboBox):
                        if campo.isVisible() and scroll.widget().isAncestorOf(campo):
                            pos = campo.mapTo(scroll.widget(), QPoint(0, 0))
                            self.assertGreaterEqual(pos.x(), 0)
                            self.assertLessEqual(pos.x()+campo.width(), scroll.widget().width())

    def test_execucao_bloqueia_configuracao_e_mantem_resultados_acessiveis(self):
        t = self.tela
        t.banco.setText('teste.sqlite')
        t.tabela.addItem('origem')
        t.travar_navegacao = lambda travada: None
        with patch('app.screens.planejamento.planejamento.validar_solver', return_value='cplex'), \
             patch('app.screens.planejamento.projeto.finalizar_sincronizacao_pendente'), \
             patch('app.screens.planejamento._ThreadPlanejamento') as thread:
            t._executar()
            self.assertTrue(t.rodando)
            self.assertFalse(t.formulario.isEnabled())
            self.assertFalse(t.opcoes_avancadas.isEnabled())
            self.assertEqual(t.abas.currentIndex(), 2)
            self.assertTrue(t.log.isEnabled())
            thread.return_value.start.assert_called_once()
            thread.return_value.erro = 'Falha simulada para verificar desbloqueio.'
            with patch('app.screens.planejamento.QMessageBox.warning'), \
                 patch('app.screens.planejamento.projeto.projeto_aberto', return_value=False):
                t._finalizar()
            self.assertFalse(t.rodando)
            self.assertTrue(t.formulario.isEnabled())
            self.assertTrue(t.opcoes_avancadas.isEnabled())


if __name__ == '__main__':
    unittest.main()
