import copy
import json
import math
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
from app.core.toragem import tracar_arvore, validar_configuracao_produtos


def config(geometricas, dummies, linhas):
    return dict(geometricas=geometricas, dummies=dummies,
        produtos=[dict(nome=nome,papel=papel,valores=dict(zip(dummies,valores))) for nome,papel,valores in linhas])


class ProdutosTaperTest(unittest.TestCase):
    def test_uma_dummy_nome_livre_sem_cerne(self):
        c = config(['DAP','h','H'],['material'],[('Casca','com_casca',[0]),('Madeira','sem_casca',[1])])
        p = tracar_arvore('DAP*exp(-ln(2)*material)',{},['DAP','h','H','material'],40,8,4,10,0,c)
        self.assertEqual(set(p['perfis']),{'com_casca','sem_casca'})
        self.assertNotIn('cerne_percentual',p['toretes'][0])
        self.assertAlmostEqual(p['toretes'][0]['volumes']['sem_casca'],math.pi*.1**2*4)

    def test_tres_dummies_ordem_livre_e_quarto_produto(self):
        c = config(['D','hs','ht'],['a','b','c'],[
            ('Externo','com_casca',[0,0,0]),('Lenho','sem_casca',[1,0,0]),
            ('Interior','cerne',[1,1,0]),('Especial','outro',[1,1,1])])
        p = tracar_arvore('D*exp(-ln(2)*(a+b+c))',{},['a','ht','D','b','hs','c'],40,8,4,10,0,c)
        self.assertEqual(len(p['perfis']),4)
        ds = p['toretes'][0]['diametros_secao']
        np.testing.assert_allclose([ds[k] for k in ['com_casca','sem_casca','cerne','produto:Especial']],[40,20,10,5])
        self.assertAlmostEqual(p['toretes'][0]['cerne_percentual'],25)

    def test_codificacao_one_hot_tres_dummies(self):
        c = config(['D','h','H'],['i','j','k'],[
            ('CC','com_casca',[1,0,0]),('SC','sem_casca',[0,1,0]),('Cerne','cerne',[0,0,1])])
        p = tracar_arvore('D*(i+.8*j+.5*k)',{},['D','h','H','i','j','k'],40,8,4,10,0,c)
        ds = p['toretes'][0]['diametros_secao']
        np.testing.assert_allclose([ds['com_casca'],ds['sem_casca'],ds['cerne']],[40,32,20])

    def test_produto_unico_e_codigos_nao_binarios(self):
        c = config(['D','h','H'],['tipo'],[('Madeira','sem_casca',[2])])
        p = tracar_arvore('D/tipo',{},['D','h','H','tipo'],40,8,4,10,0,c)
        self.assertEqual(p['produto_base'],'sem_casca')
        self.assertEqual(p['produto_madeira'],'sem_casca')
        self.assertEqual(set(p['perfis']),{'sem_casca'})
        self.assertEqual(p['toretes'][0]['diametro_madeira'],20)

    def test_sem_dummy_produto_unico(self):
        c = config(['D','h','H'],[],[('Sem casca','sem_casca',[])])
        p = tracar_arvore('D',{},['D','h','H'],40,8,4,10,0,c)
        self.assertEqual(p['produto_base'],'sem_casca')

    def test_validacao_configuracoes_incompletas_e_repetidas(self):
        c = config(['D','h','H'],['tipo'],[('CC','com_casca',[0]),('SC','sem_casca',[1])])
        casos = []
        v = copy.deepcopy(c);v['produtos'][1]['valores']['tipo'] = 0;casos.append(v)
        v = copy.deepcopy(c);v['produtos'][1]['valores']['tipo'] = float('nan');casos.append(v)
        v = copy.deepcopy(c);v['produtos'][1]['valores'] = {};casos.append(v)
        v = copy.deepcopy(c);v['geometricas'] = ['D','D','H'];casos.append(v)
        v = copy.deepcopy(c);v['produtos'][1]['papel'] = 'com_casca';casos.append(v)
        for v in casos:
            with self.assertRaises(ValueError):
                validar_configuracao_produtos(v,['D','h','H','tipo'])
        with self.assertRaises(ValueError):
            validar_configuracao_produtos(c,['D','h','H','variavel_renomeada'])


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from app.core import projeto
from app.core.db import conectar
from app.screens.toragem import TelaToragem
from app.screens.produtos_taper import DialogoProdutosTaper


class ProdutosTaperUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialogo_dummies_e_fronteira_do_mapeamento(self):
        c = config(['D','h','H'],['tipo'],[('CC','com_casca',[0]),('SC','sem_casca',[1])])
        d = DialogoProdutosTaper('Modelo livre',['D','h','H','tipo'],c)
        try:
            self.assertEqual(d.coletar(),c)
            self.assertEqual(d.produtos.columnCount(),3)
            d.produtos.item(1,2).setText('2,5')
            self.assertEqual(d.coletar()['produtos'][1]['valores']['tipo'],2.5)
            d.funcoes['D'].setCurrentIndex(d.funcoes['D'].findData('dummy'))
            self.assertEqual(d.produtos.columnCount(),4)
            with self.assertRaises(ValueError):
                d.coletar()
        finally:
            d.close()

    def test_config_por_modelo_persistencia_e_produtos_parciais(self):
        with tempfile.TemporaryDirectory() as td:
            projeto.novo_projeto(Path(td)/'produtos.mogno')
            t = None
            try:
                c1 = config(['D','h','H'],['tipo'],[('CC','com_casca',[0]),('SC','sem_casca',[1])])
                c2 = config(['D','h','H'],['a','b','c'],[
                    ('CC','com_casca',[0,0,0]),('SC','sem_casca',[1,0,0]),
                    ('Cerne','cerne',[1,1,0]),('Especial','outro',[1,1,1])])
                conn = conectar()
                conn.execute('INSERT INTO configuracoes(id,comprimento_tora,diametro_minimo_tora) VALUES(1,4,10)')
                ids = []
                for nome,vs,eq in [('A',['D','h','H','tipo'],'D*exp(-ln(2)*tipo)'),
                                   ('B',['D','h','H','a','b','c'],'D*exp(-ln(2)*(a+b+c))')]:
                    ids.append(conn.execute('INSERT INTO modelos(nome,tipo,variaveis_x,equacao,coeficientes) VALUES(?,?,?,?,?)',
                        (nome,'Afilamento / Taper',json.dumps(vs),eq,'{}')).lastrowid)
                conn.commit();conn.close()
                t = TelaToragem()
                self.assertFalse(t._calcular())
                self.assertIn('dummy',t.resumo.text())
                def confirmar(dialogo):
                    dialogo.produtos.item(0,0).setText('CC')
                    dialogo.produtos.item(1,0).setText('SC')
                    dialogo._confirmar()
                    return dialogo.result()
                with patch.object(DialogoProdutosTaper,'exec',confirmar):
                    t._configurar_produtos()
                self.assertEqual(t._configuracoes_produtos[str(ids[0])], c1)
                self.assertTrue(t._calcular(),t.resumo.text())
                self.assertTrue(t.cerne.isEnabled())
                self.assertEqual(t.tabela.item(0,8).text(),'—')
                t._configuracoes_produtos[str(ids[1])] = c2
                t.modelos.setCurrentIndex(1)
                self.assertTrue(t._calcular(),t.resumo.text())
                self.assertFalse(t.cerne.isEnabled())
                self.assertEqual(t.tabela.columnCount(),13)
                self.assertIn('Especial',t.tabela.horizontalHeaderItem(12).text())
                t.tabela.selectRow(0)
                self.assertEqual(len(t.secao.contornos_produtos),1)
                t._salvar()
                t.recarregar_lista()
                self.assertEqual(t._configuracoes_produtos,{str(ids[0]):c1,str(ids[1]):c2})
                self.assertTrue(t._calcular(),t.resumo.text())
                t.modelos.setCurrentIndex(0)
                self.assertTrue(t._calcular(),t.resumo.text())
                self.assertEqual(t.tabela.columnCount(),12)
                self.assertTrue(t.cerne.isEnabled())
                t.resize(1400,900)
                t.show()
                self.app.processEvents()
                Path('.ui_previews').mkdir(exist_ok=True)
                t.grab().save('.ui_previews/toragem_produtos.png')
            finally:
                if t:
                    t.close();t.deleteLater();self.app.processEvents()
                projeto.fechar_projeto()
