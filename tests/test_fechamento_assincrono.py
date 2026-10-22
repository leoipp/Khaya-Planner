import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from app.window import JanelaPrincipal
from app.core import projeto


class FechamentoAssincrono(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_salvamento_lento_nao_bloqueia_eventos_e_fecha_ao_terminar(self):
        self._verificar(False)

    def test_falha_mantem_janela_aberta(self):
        self._verificar(True)

    def _verificar(self, falhar):
        # Usa o closeEvent real sem carregar telas nem bancos do usuário.
        janela = JanelaPrincipal.__new__(JanelaPrincipal)
        QMainWindow.__init__(janela)
        janela.telas = {}
        janela.show()
        liberar = threading.Event()
        iniciou = threading.Event()
        pulsos = []
        timer = QTimer()
        timer.timeout.connect(lambda: pulsos.append(1))
        timer.start(5)

        def salvar(pendente):
            self.assertTrue(pendente)
            iniciou.set()
            liberar.wait(3)
            if falhar:
                raise OSError('disco cheio')

        with patch.object(projeto, 'projeto_aberto', return_value=True), \
             patch.object(projeto, 'preparar_fechamento', return_value=True), \
             patch.object(projeto, 'fechar_projeto', side_effect=salvar) as fechar, \
             patch('app.window.QMessageBox.warning') as aviso:
            try:
                janela.close()
                limite = time.monotonic() + 2
                while (not iniciou.is_set() or len(pulsos) < 3) and time.monotonic() < limite:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertTrue(iniciou.is_set())
                self.assertGreaterEqual(len(pulsos), 3)
                self.assertTrue(janela.isVisible())
                janela.close()  # Segundo clique não dispara outro salvamento.
                self.assertEqual(fechar.call_count, 1)
                liberar.set()
                limite = time.monotonic() + 3
                while janela._fechando and time.monotonic() < limite:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertFalse(janela._fechando)
                self.assertEqual(janela.isVisible(), falhar)
                self.assertEqual(aviso.call_count, int(falhar))
            finally:
                liberar.set()
                timer.stop()
                janela._fechamento_concluido = True
                janela.close()
                janela.deleteLater()
