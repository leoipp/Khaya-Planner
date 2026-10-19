"""Contrato de navegação e de tamanho das telas em ambos os temas."""
import os
import unittest
from importlib import import_module
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QRect
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.theme import manager, qss
from app.widgets.barra_lateral import BarraLateral
from app.widgets.cabecalho_tela import CabecalhoTela
from app.widgets.layout_fluxo import LayoutFluxo
from app.window import FABRICAS_TELAS, ITENS_NAVEGACAO


class InterfaceVisual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')

    def setUp(self):
        self.estilo_anterior = self.app.styleSheet()
        self.tema_anterior = manager.obter().modo_atual()

    def tearDown(self):
        manager.obter().aplicar(self.tema_anterior, salvar=False)
        self.app.setStyleSheet(self.estilo_anterior)
        self.app.processEvents()

    def estabilizar(self):
        for _ in range(3):
            self.app.processEvents()

    def test_telas_abrem_sem_forcar_janela_maior(self):
        # Mantém este teste isolado de projetos ou bases reais da máquina.
        with patch('app.core.projeto.projeto_aberto', return_value=False), \
             patch('app.core.projeto.caminho_trabalho', side_effect=RuntimeError('Nenhum projeto aberto')):
            for chave, (modulo, classe) in FABRICAS_TELAS.items():
                with self.subTest(tela=chave):
                    tela = getattr(import_module(modulo), classe)()
                    try:
                        self.assertTrue(tela.findChildren(CabecalhoTela))
                        tela.show()
                        for tema in ('light', 'dark'):
                            manager.obter().aplicar(tema, salvar=False)
                            self.app.setStyleSheet(qss.gerar(tema))
                            tela.resize(760, 680)
                            self.estabilizar()
                            self.assertEqual((tela.width(), tela.height()), (760, 680))
                    finally:
                        tela.close()
                        tela.deleteLater()
                        self.estabilizar()

    def test_navegacao_preserva_todas_as_telas_e_travas(self):
        selecionadas = []
        barra = BarraLateral(ITENS_NAVEGACAO, selecionadas.append, lambda: None, lambda: None)
        barra.resize(244, 600)
        barra.show()
        self.estabilizar()
        try:
            self.assertEqual(set(barra._botoes_nav), set(FABRICAS_TELAS))
            for chave, botao in barra._botoes_nav.items():
                barra.selecionar_tela(chave)
                botao.click()
                self.assertEqual(selecionadas[-1], chave)
                self.assertEqual(sum(b.isChecked() for b in barra._botoes_nav.values()), 1)
            barra.definir_navegacao_travada(True)
            self.assertTrue(all(not b.isEnabled() for b in barra._botoes_nav.values()))
            barra.definir_estado_projeto(True)
            self.assertFalse(barra.botao_novo_projeto.isEnabled())
            self.assertFalse(barra.botao_abrir_projeto.isEnabled())
        finally:
            barra.close()
            barra.deleteLater()

    def test_barra_de_ferramentas_quebra_linha_sem_sobrepor(self):
        painel = QWidget()
        fluxo = LayoutFluxo(painel)
        for i in range(6):
            fluxo.addWidget(QPushButton(f'Ferramenta {i}'))
        painel.resize(320, 180)
        painel.show()
        self.estabilizar()
        try:
            fluxo.setGeometry(QRect(0, 0, 320, 180))
            retangulos = [fluxo.itemAt(i).geometry() for i in range(fluxo.count())]
            self.assertGreater(len({r.y() for r in retangulos}), 1)
            for i, rect in enumerate(retangulos):
                self.assertLessEqual(rect.right(), 319)
                for outro in retangulos[i+1:]:
                    self.assertFalse(rect.intersects(outro))
        finally:
            painel.close()
            painel.deleteLater()


if __name__ == '__main__':
    unittest.main()
