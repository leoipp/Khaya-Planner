import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QWidget

from app.widgets.grafico_sob_demanda import GraficoSobDemanda


class GraficoSobDemandaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_importa_uma_vez_e_aplica_apenas_o_ultimo_estado_ao_exibir(self):
        chamadas = []

        class GraficoTeste(QWidget):
            def mostrar_mensagem(self, texto):
                chamadas.append(texto)

            def desenhar(self, *args):
                chamadas.append(args)

        with patch('app.widgets.grafico_sob_demanda.import_module',
                   return_value=SimpleNamespace(Grafico=GraficoTeste)) as importar:
            grafico = GraficoSobDemanda('modulo_teste', 'Grafico')
            try:
                grafico.mostrar_mensagem('Antigo')
                grafico.desenhar([1, 2], True)
                importar.assert_not_called()
                grafico.show()
                self.app.processEvents()
                importar.assert_called_once_with('modulo_teste')
                self.assertEqual(chamadas, [([1, 2], True)])
                grafico.hide()
                grafico.mostrar_mensagem('Projeto fechado')
                self.assertEqual(len(chamadas), 1)
                grafico.show()
                self.app.processEvents()
                self.assertEqual(chamadas[-1], 'Projeto fechado')
                self.assertEqual(importar.call_count, 1)
            finally:
                grafico.close()
                grafico.deleteLater()


if __name__ == '__main__':
    unittest.main()
