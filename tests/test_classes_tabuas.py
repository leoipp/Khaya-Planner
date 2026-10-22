import math
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import classes_tabuas, db, projeto
from app.core.db import conectar


class ClassesTabuasTest(unittest.TestCase):
    def test_cadastro_validacao_e_atualizacao(self):
        c = sqlite3.connect(':memory:')
        self.addCleanup(c.close)
        c.executescript(db.SCHEMA)
        id_classe = classes_tabuas.salvar(c,'C1',4,2.5,10,2000)
        self.assertEqual(classes_tabuas.listar(c)[0]['preco'],2000)
        classes_tabuas.salvar(c,'C2',3,3,12,2500,id_classe)
        self.assertEqual(classes_tabuas.listar(c),[dict(id=id_classe,nome='C2',comprimento=3,espessura=3,largura=12,preco=2500,rendimento=35)])
        for args in [('',4,2,10,1),('C1',0,2,10,1),('C1',4,2,10,-1),('C1',math.inf,2,10,1)]:
            with self.assertRaises(ValueError):classes_tabuas.salvar(c,*args)
        self.assertIn('classes_tabuas',db.TABELAS_ESSENCIAIS)
        for rendimento in (-1,101,float('nan')):
            with self.assertRaises(ValueError):
                classes_tabuas.salvar(c,'Inválido',4,2,10,2000,rendimento=rendimento)


    def test_migracao_de_classes_existentes(self):
        with tempfile.TemporaryDirectory() as td:
            caminho = Path(td)/'antigo.sqlite'
            c = sqlite3.connect(caminho)
            c.execute('CREATE TABLE classes_tabuas(id INTEGER PRIMARY KEY, nome TEXT, comprimento REAL, espessura REAL, largura REAL, preco REAL)')
            c.execute("INSERT INTO classes_tabuas VALUES(7,'C1',4,2.5,10,2000)")
            c.commit();c.close()
            db.inicializar_schema(caminho)
            c = sqlite3.connect(caminho)
            try:
                classe = classes_tabuas.listar(c)[0]
                self.assertEqual((classe['id'],classe['preco'],classe['rendimento']),(7,2000,35))
            finally:c.close()


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtGui import QFontDatabase
from app.screens.sortimentos import TelaSortimentos
from app.screens.toragem import TelaToragem
from app.theme import qss


class ClassesTabuasUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')

    def test_cadastro_persistencia_e_uso_no_desdobro(self):
        with tempfile.TemporaryDirectory() as td:
            projeto.novo_projeto(Path(td)/'classes.mogno')
            s=t=None
            try:
                conn = conectar()
                conn.execute('INSERT INTO configuracoes(id,comprimento_tora,diametro_minimo_tora) VALUES(1,4,10)')
                conn.execute("INSERT INTO modelos(nome,tipo,variaveis_x,equacao,coeficientes) VALUES('Cilindro','Afilamento / Taper','[\"x1\",\"x2\",\"x3\"]','x1','{}')")
                conn.execute("INSERT INTO sortimentos(nome,preco,preco_pe) VALUES('Legado',1234,500)")
                conn.commit();conn.close()
                s=TelaSortimentos()
                self.assertEqual(s.tabela_sortimentos.view.model().columnCount(),4)
                self.assertFalse(s.entry_sortimento_preco.isVisible())
                self.assertEqual(s.abas.tabText(2),'Classes de tábua')
                w=s.classes_tabuas
                w.nome.setText('C1')
                for k,v in dict(comprimento=3,espessura=3,largura=12,preco=2300,rendimento=42).items():w.campos[k].setValue(v)
                editor=w.classificacao_cerne
                editor.classes.item(0,0).setText('Premium')
                editor.classes.item(0,5).setText('#1565c0')
                w.adicionar()
                self.assertEqual(editor.classes.item(0,0).text(),'Premium')
                editor.salvar()
                self.assertEqual(w.tabela.rowCount(),1)
                id_classe=w.id_atual
                w.campos['preco'].setValue(2500);w.salvar()
                s.recarregar_lista()
                w.tabela.selectRow(0)
                self.assertEqual(w.campos['preco'].value(),2500)
                self.assertEqual(w.classificacao_cerne.classes.item(0,0).text(),'Premium')
                # O preço legado permanece no banco e não reaparece no formulário de Sortimentos.
                s.tabela_sortimentos.selecionar_id(1)
                s.entry_sortimento_nome.setText('Legado editado');s._salvar_sortimento()
                conn=conectar()
                self.assertEqual(conn.execute('SELECT preco FROM sortimentos WHERE id=1').fetchone()[0],1234)
                conn.close()
                t=TelaToragem()
                self.assertEqual(t._ler_classes()[0]['nome'],'Premium')
                self.assertEqual(t._ler_classes()[0]['cor'],'#1565c0')
                t.produtos.setCurrentIndex(1)
                self.assertTrue(t._calcular(),t.resumo.text())
                self.assertEqual(t.rendimento.value(),42)
                self.assertFalse(t.rendimento.isEnabled())
                self.assertEqual(t.comprimento.value(),3)
                self.assertEqual(t.largura.value(),12)
                self.assertEqual(t.espessura.value(),3)
                self.assertEqual(t._perfil['toretes'][0]['comprimento'],3)
                t._salvar();t.recarregar_lista()
                self.assertEqual(t.produtos.currentData()['id'],id_classe)
                self.assertEqual(t.rendimento.value(),42)
                self.assertEqual(t._perfil['toretes'][0]['comprimento'],3)
                s.abas.setCurrentIndex(2);s.resize(1100,740);s.show();self.app.processEvents()
                Path('.ui_previews').mkdir(exist_ok=True)
                anterior=self.app.styleSheet()
                try:
                    self.app.setStyleSheet(qss.gerar('light'));self.app.processEvents()
                    s.grab().save('.ui_previews/classes_tabuas.png')
                finally:self.app.setStyleSheet(anterior)
                with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):w.excluir()
                self.assertEqual(w.tabela.rowCount(),0)
            finally:
                for tela in (t,s):
                    if tela:tela.close();tela.deleteLater()
                self.app.processEvents();projeto.fechar_projeto()
