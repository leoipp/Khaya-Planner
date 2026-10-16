import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from app.core import projeto
from app.core.db import conectar
from app.screens.toragem import TelaToragem


class ToragemUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        fonte = Path('C:/Windows/Fonts/segoeui.ttf')
        if fonte.exists():
            QFontDatabase.addApplicationFont(str(fonte))

    def test_modelo_toretes_selecao_e_persistencia(self):
        with tempfile.TemporaryDirectory() as td:
            projeto.novo_projeto(Path(td)/'toragem.mogno')
            t = None
            try:
                conn = conectar()
                conn.execute('INSERT INTO configuracoes(id,comprimento_tora,diametro_minimo_tora) '
                             'VALUES(1,4,10)')
                conn.execute('INSERT INTO modelos(nome,tipo,variaveis_x,equacao,coeficientes) '
                             'VALUES(?,?,?,?,?)', ('Cone teste', 'Afilamento / Taper',
                             json.dumps(['x1','x2','x3']), 'x1*(x3-x2)/(x3-1.3)', '{}'))
                conn.commit()
                conn.close()
                t = TelaToragem()
                t.resize(1150,850)
                t.show()
                self.app.processEvents()
                self.assertTrue(t._calcular())
                self.assertEqual(t.tabela.rowCount(), 4)
                t.tabela.selectRow(1)
                self.assertEqual(t.vista.selecionado, 1)
                self.assertAlmostEqual(t.secao.diametro,
                                       t._perfil['toretes'][1]['diametro_corte'])
                self.assertTrue(t.secao.pecas)
                t.dap.setValue(42)
                t.rendimento.setValue(32)
                t._salvar()
                t.recarregar_lista()
                self.assertEqual(t.dap.value(),42)
                self.assertEqual(t.rendimento.value(),32)
                self.assertTrue(t._calcular())
                Path('.ui_previews').mkdir(exist_ok=True)
                t.grab().save('.ui_previews/toragem.png')
            finally:
                if t:
                    t.close()
                    t.deleteLater()
                    self.app.processEvents()
                projeto.fechar_projeto()


if __name__ == '__main__':
    unittest.main()
