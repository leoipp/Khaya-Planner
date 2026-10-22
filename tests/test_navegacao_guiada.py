import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core import projeto
from app.core.db import conectar
from app.screens.visao_geral import TelaVisaoGeral
from app.widgets.busca_navegacao import BuscaNavegacao
from app.window import ITENS_NAVEGACAO, JanelaPrincipal


class NavegacaoGuiada(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')

    def test_busca_por_assunto_sem_acentos_e_teclado(self):
        d = BuscaNavegacao(ITENS_NAVEGACAO)
        d.show()
        self.app.processEvents()
        escolhidas = []
        d.telaEscolhida.connect(escolhidas.append)
        try:
            for termo, chave in [('otimizacao', 'planejamento'), ('ponta fina', 'toragem'),
                                 ('serraria', 'toragem')]:
                d.busca.setText(termo)
                self.assertEqual(d.resultados.count(), 1)
                self.assertEqual(d.resultados.currentItem().data(Qt.ItemDataRole.UserRole), chave)
            d.busca.clear()
            QTest.keyClick(d.busca, Qt.Key.Key_Down)
            self.assertEqual(d.resultados.currentRow(), 1)
            d.busca.setText('taper')
            QTest.keyClick(d.busca, Qt.Key.Key_Return)
            self.assertEqual(escolhidas, ['toragem'])
        finally:
            d.close()
            d.deleteLater()

    def test_busca_sem_resultado_nao_navega(self):
        d = BuscaNavegacao(ITENS_NAVEGACAO)
        escolhidas = []
        d.telaEscolhida.connect(escolhidas.append)
        d.show()
        try:
            d.busca.setText('inexistente123')
            QTest.keyClick(d.busca, Qt.Key.Key_Return)
            self.assertEqual(d.resultados.count(), 0)
            self.assertEqual(escolhidas, [])
            self.assertTrue(d.isVisible())
        finally:
            d.close()
            d.deleteLater()

    def test_inicio_sem_projeto_e_atalhos_respeitam_trava(self):
        with patch('app.window.core_projeto.limpar_arquivos_orfaos'):
            w = JanelaPrincipal()
        try:
            self.assertEqual(w._tela_atual, 'visao_geral')
            self.assertEqual(set(w.telas), {'visao_geral'})
            w.travar_navegacao(True)
            w.mostrar_tela('modelos')
            w._abrir_busca()
            self.assertEqual(w._tela_atual, 'visao_geral')
            self.assertIsNone(w._dialogo_navegacao)
            self.assertFalse(w._atalho_busca.isEnabled())
            self.assertFalse(w._barra_lateral.botao_buscar.isEnabled())
            w.travar_navegacao(False)
            w._navegar_pela_visao('configuracoes')
            self.assertEqual(w._tela_atual, 'configuracoes')
            self.assertEqual(w.telas['configuracoes'].abas_configuracoes.currentIndex(), 1)
        finally:
            w.close()
            w.deleteLater()
            self.app.processEvents()

    def test_painel_atualiza_troca_de_projeto_e_falha_de_consulta(self):
        t = TelaVisaoGeral()
        criar = []
        t.novoProjetoSolicitado.connect(lambda: criar.append(True))
        t._proximo()
        self.assertEqual(criar, [True])
        with tempfile.TemporaryDirectory() as td:
            try:
                projeto.novo_projeto(Path(td)/'teste.mogno')
                t.recarregar_lista()
                self.assertEqual(t.estado['modelos'], 0)
                conn = conectar()
                conn.execute("INSERT INTO modelos(nome,tipo) VALUES('Modelo','Outro')")
                conn.commit()
                conn.close()
                t.recarregar_lista()
                self.assertEqual(t.estado['modelos'], 1)
                with patch('app.screens.visao_geral.conectar', side_effect=sqlite3.OperationalError('Falha de teste')):
                    t.recarregar_lista()
                self.assertIsNone(t.estado)
                self.assertFalse(t.botao_proximo.isEnabled())
                projeto.fechar_projeto()
                t.recarregar_lista()
                self.assertIsNone(t.estado)
                self.assertEqual(t.botao_proximo.text(), 'Criar projeto')
            finally:
                projeto.fechar_projeto()
                t.close()
                t.deleteLater()
                self.app.processEvents()
