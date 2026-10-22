import copy
import math
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.core.toragem import tracar_arvore
from app.core.desdobro import (CLASSES_MADEIRA_PADRAO, classificar_percentual,
                               validar_classes, classificar_tabuas, simular)

EQUACAO = 'DAP*exp(-ln(2)*Tx1-ln(2)*Tx2)'


class CamadasTest(unittest.TestCase):
    def test_cilindros_volumes_e_mesma_secao(self):
        p = tracar_arvore(EQUACAO, {}, ['DAP', 'h', 'H', 'Tx1', 'Tx2'], 40, 8, 4, 10, 0)
        self.assertEqual(len(p['toretes']), 2)
        for t in p['toretes']:
            self.assertEqual(t['diametros_secao'], dict(com_casca=40., sem_casca=20., cerne=10.))
            self.assertAlmostEqual(t['volumes']['com_casca'], math.pi*.2**2*4)
            self.assertAlmostEqual(t['volumes']['sem_casca'], math.pi*.1**2*4)
            self.assertAlmostEqual(t['volumes']['cerne'], math.pi*.05**2*4)
            self.assertAlmostEqual(t['cerne_percentual'], 25.)
        self.assertEqual(set(p['perfis']), {'com_casca', 'sem_casca', 'cerne'})

    def test_x4_x5_equivalentes_a_tx1_tx2(self):
        antigo = tracar_arvore(EQUACAO, {}, ['DAP', 'h', 'H', 'Tx1', 'Tx2'], 40,8,4,10,0)
        eq = 'x1*exp(-ln(2)*x4-ln(2)*x5)'
        novo = tracar_arvore(eq, {}, ['x1','x2','x3','x4','x5'],40,8,4,10,0)
        for nome in antigo['perfis']:
            np.testing.assert_array_equal(antigo['perfis'][nome],novo['perfis'][nome])
        self.assertEqual(antigo['toretes'],novo['toretes'])
        with self.assertRaises(ValueError):
            tracar_arvore('x1*x4', {}, ['x1','x2','x3','x4'],40,8,4,10,0)

    def test_rejeita_perfis_invertidos_e_seletor_incompleto(self):
        for eq in ['DAP*exp(Tx1+Tx2)', 'DAP+Tx1']:
            with self.assertRaises(ValueError):
                tracar_arvore(eq, {}, ['DAP','h','H'], 40, 8, 4, 10)

    def test_garay_figura(self):
        eq = '2.596021*DAP*(1+.157843*ln(1-.998216*(h/H)^.007721))*exp(-(1.421417*Tx1+9.45757*Tx2)/DAP)'
        p = tracar_arvore(eq, {}, ['DAP','h','H'], 30,20,4,10)
        t = p['toretes'][0]
        self.assertAlmostEqual(t['diametros_secao']['com_casca'], 25.1755338888)
        self.assertAlmostEqual(t['diametros_secao']['sem_casca'], 24.0105202683)
        self.assertAlmostEqual(t['diametros_secao']['cerne'], 17.5181895763)

    def test_classes_fronteiras_lacunas_sobreposicoes(self):
        classes = copy.deepcopy(CLASSES_MADEIRA_PADRAO)
        validar_classes(classes)
        for v, nome in [(0,'C3'),(19.99,'C3'),(20,'C2'),(50,'C2'),(50.01,'C1'),(100,'C1')]:
            self.assertEqual(classificar_percentual(v, classes)['nome'], nome)
        for inc in [False,True]:
            alteradas = copy.deepcopy(classes)
            alteradas[1]['inclui_minimo'] = inc
            alteradas[2]['inclui_maximo'] = inc
            with self.assertRaises(ValueError):
                validar_classes(alteradas)

    def test_classes_totais_preservam_producao(self):
        r = simular(30,10,2.5,4,4,50,3,35)
        resumo = classificar_tabuas(r, CLASSES_MADEIRA_PADRAO, .01)
        self.assertEqual(sum(c['quantidade_teorica'] for c in resumo),r['quantidade'])
        self.assertAlmostEqual(sum(c['quantidade_prevista'] for c in resumo),r['quantidade_prevista'])
        self.assertAlmostEqual(sum(c['volume_previsto'] for c in resumo),r['volume_previsto'])
        self.assertEqual(len(r['cores_pecas']),len(r['pecas']))


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from app.core import projeto, classes_tabuas
from app.core.db import conectar
from app.screens.toragem import TelaToragem
from app.screens.modelos import TelaModelos


class CamadasUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        fonte = Path('C:/Windows/Fonts/segoeui.ttf')
        if fonte.exists():
            QFontDatabase.addApplicationFont(str(fonte))
            cls.app.setFont(QFont('Segoe UI', 10))

    def test_cadastro_tres_perfis_classes_e_persistencia(self):
        with tempfile.TemporaryDirectory() as td:
            projeto.novo_projeto(Path(td)/'teste.mogno')
            t = m = None
            try:
                m = TelaModelos()
                m._exemplo_garay()
                self.assertEqual([m.lista_variaveis_x.item(i).text() for i in range(5)], ['x1','x2','x3','x4','x5'])
                self.assertIn('e*x4+f*x5',m.txt_equacao.toPlainText())
                dados = m._coletar_dados_formulario()
                self.assertIsNotNone(dados)
                conn = conectar()
                conn.execute('INSERT INTO configuracoes(id,comprimento_tora,diametro_minimo_tora) VALUES(1,4,10)')
                conn.execute('INSERT INTO modelos(nome,tipo,estrato_coluna,estrato,variaveis_x,equacao,coeficientes,observacoes) VALUES(?,?,?,?,?,?,?,?)',dados)
                conn.commit()
                conn.close()
                t = TelaToragem()
                t.resize(1450,1000)
                t.show()
                self.app.processEvents()
                self.assertTrue(t._calcular(),t.resumo.text())
                self.assertFalse(t.cerne.isEnabled())
                self.assertEqual(t.tabela.columnCount(),12)
                self.assertNotEqual(t.tabela.item(0,7).text(),'—')
                t.tabela.selectRow(0)
                self.assertTrue(t.secao.cores_pecas)
                self.assertGreater(t.secao.diametro_com_casca,t.secao.diametro)
                conn = conectar()
                regras = classes_tabuas.carregar_classificacao_cerne(conn)
                regras[0].update(nome='Especial',cor='#0000ff')
                classes_tabuas.salvar_classificacao_cerne(conn,regras)
                conn.commit();conn.close()
                t._salvar()
                t.recarregar_lista()
                self.assertEqual(t._ler_classes()[0]['nome'],'Especial')
                self.assertEqual(t._ler_classes()[0]['cor'],'#0000ff')
                self.assertTrue(t._calcular(),t.resumo.text())
                self.app.processEvents()
                Path('.ui_previews').mkdir(exist_ok=True)
                t.grab().save('.ui_previews/toragem_camadas.png')
            finally:
                for w in (t,m):
                    if w:
                        w.close()
                        w.deleteLater()
                self.app.processEvents()
                projeto.fechar_projeto()
