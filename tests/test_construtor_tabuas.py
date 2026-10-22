import copy
import json
import math
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from app.core import construtores, db, classes_tabuas, projeto
from app.core.desdobro import CLASSES_MADEIRA_PADRAO
from app.core.db import conectar
from app.core.tabuas_grafo import saidas_tabuas


def grafo():
    config=dict(geometricas=['x1','x2','x3'],dummies=['x4','x5'],produtos=[
        dict(nome=n,papel=p,valores={'x4':a,'x5':b}) for n,p,a,b in
        [('CC','com_casca',0,0),('SC','sem_casca',1,0),('Cerne','cerne',1,1)]])
    nos={1:dict(tipo='classe_diametrica',rotulo='D'),
        5:dict(tipo='coluna',rotulo='Altura',coluna='Ht'),
        2:dict(tipo='modelo',rotulo='H',variaveis=['d','H'],variantes=[dict(equacao='H+0*d',coeficientes={})]),
        3:dict(tipo='afilamento',rotulo='Taper',nome='Teste',variaveis=['x1','x2','x3','x4','x5'],configuracao_produtos=config,
            variantes=[dict(equacao='x1*exp(ln(.8)*x4+ln(.625)*x5)',coeficientes={})]),
        4:dict(tipo='tabuas',rotulo='Tábuas',nome_saida='t',variaveis=[],classes_tabuas=[dict(id=1,nome='C1',classe_cerne='C1')])}
    conexoes=[dict(origem=1,destino=2,entrada_idx=0),dict(origem=5,destino=2,entrada_idx=1),
        dict(origem=2,destino=3,entrada_idx=0),dict(origem=3,destino=4,entrada_idx=0,saida_idx=2)]
    dim=dict(diametro_minimo_tora=0,comprimento_tora=4,classes_tabuas=[dict(id=1,nome='C1',comprimento=4,espessura=22,largura=22,preco=2000,rendimento=100)],classificacao_cerne=copy.deepcopy(CLASSES_MADEIRA_PADRAO))
    return nos,conexoes,dim


class ConstrutorTabuasTest(unittest.TestCase):
    def setUp(self):
        self.nos,self.con,self.dim=grafo()
        self.df=pd.DataFrame(dict(id=[1,2],Ht=[8.,np.nan]),index=[7,9])

    def avaliar(self):
        return construtores.avaliar_grafo(self.df,self.nos,self.con,[20.,40.],dimensoes_tora=self.dim)

    def test_volume_receita_por_classe_e_diametro_e_altura_ausente(self):
        original=copy.deepcopy(self.nos)
        v,e=self.avaliar();self.assertEqual(e,[])
        # Cilindro SC de 32 cm: uma tábua de 22x22 cm por torete de 4 m, dois toretes.
        self.assertAlmostEqual(v[4]['volume_1'][40.].loc[7],2*.22*.22*4)
        self.assertAlmostEqual(v[4]['receita_1'][40.].loc[7],774.4)
        self.assertEqual(v[4]['volume_1'][20.].loc[7],0)
        self.assertTrue(pd.isna(v[4]['volume_1'][40.].loc[9]))
        saidas=construtores.saidas_nomeadas(self.nos,v)
        self.assertEqual(len(saidas),4)
        self.assertIn('t_volume_C1_1_40',saidas)
        self.assertEqual(self.nos,original)

    def test_v4_limite_oficial_contagem_e_receita(self):
        self.nos[4]['versao_producao']=4
        self.df['V']=.5;self.df['N']=2.
        self.nos[6]=dict(tipo='modelo',rotulo='V',variaveis=['d','v'],variantes=[dict(equacao='v+0*d',coeficientes={})])
        self.nos[7]=dict(tipo='coluna',rotulo='V',coluna='V')
        self.nos[8]=dict(tipo='modelo',rotulo='N',variaveis=['d','n'],variantes=[dict(equacao='n+0*d',coeficientes={})])
        self.nos[9]=dict(tipo='coluna',rotulo='N',coluna='N')
        self.con.extend([dict(origem=1,destino=6,entrada_idx=0),dict(origem=7,destino=6,entrada_idx=1),
            dict(origem=1,destino=8,entrada_idx=0),dict(origem=9,destino=8,entrada_idx=1),
            dict(origem=6,destino=4,entrada_idx=1),dict(origem=8,destino=4,entrada_idx=2)])
        v,e=self.avaliar();self.assertEqual(e,[])
        # 0,25 m³ por árvore admite uma peça inteira de 0,1936 m³.
        self.assertAlmostEqual(v[4]['quantidade_1'][40.].loc[7],2.)
        self.assertAlmostEqual(v[4]['volume_1'][40.].loc[7],.3872)
        self.assertLessEqual(v[4]['volume_1'][40.].loc[7],.5)
        self.assertAlmostEqual(v[4]['receita_1'][40.].loc[7],774.4)
        self.df['V']=0.;v,e=self.avaliar();self.assertEqual(e,[])
        self.assertEqual(v[4]['quantidade_1'][40.].loc[7],0.)
        self.df['V']=.5;self.df['N']=0.;v,e=self.avaliar()
        self.assertTrue(any('sem árvores' in erro for erro in e))

    def test_v4_exige_volume_e_arvores(self):
        self.nos[4]['versao_producao']=4
        _,e=self.avaliar()
        self.assertTrue(any('volume aproveitável e árvores' in erro for erro in e))

    def test_mistura_conjunta_por_classe_no_grafo(self):
        from app.core.tabuas_grafo import _producao_arvore
        _producao_arvore.cache_clear()
        self.addCleanup(_producao_arvore.cache_clear)
        self.nos[3]['variantes'][0]['equacao']='x1*exp(ln(.8)*x4+ln(.1875)*x5)'
        self.nos[4]['classes_tabuas']=[dict(id=i,nome=f'C{i}',classe_cerne=f'C{i}') for i in (1,2,3)]
        self.dim['classes_tabuas']=[dict(id=i,nome=f'C{i}',comprimento=4,largura=2,espessura=2,preco=4000/i,rendimento=100) for i in (1,2,3)]
        pecas=[(-1.,-1.,2.,2.),(2.5,-1.,2.,2.),(-5.,-1.,2.,2.),(-1.,4.,2.,2.)]
        with patch('app.core.corte_conjunto.encaixar',return_value=pecas):
            v,e=self.avaliar()
        self.assertEqual(e,[])
        for i,volume in [(1,.0032),(2,.0032),(3,.0064)]:
            self.assertAlmostEqual(v[4][f'volume_{i}'][40.].loc[7],volume)
            self.assertAlmostEqual(v[4][f'receita_{i}'][40.].loc[7],volume*4000/i)
        self.assertAlmostEqual(sum(v[4][f'volume_{i}'][40.].loc[7] for i in (1,2,3)),.0128)

    def test_taper_dummies_volume_existente_e_cache_tabela(self):
        self.con=self.con[:-1]
        self.nos.pop(4)
        self.nos[3].update(nome_saida_aproveitavel='vt',nome_saida_biomassa='residuo')
        self.dim['usar_tabela_afilamento']=True
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertAlmostEqual(v[3]['aproveitavel'][40.].loc[7],math.pi*.2**2*8,places=7)
        self.assertAlmostEqual(v[3]['biomassa'][40.].loc[7],0,places=7)
        self.assertIsNotNone(construtores._resolver_valor_saida(self.nos[3],v[3],2))
        self.assertEqual(construtores.preaquecer_cache_afilamento([dict(nos=self.nos)], [40],self.dim),{})

    def test_filtro_cerne_rendimento_e_preco_atualizado(self):
        self.nos[4]['classes_tabuas'][0].update(nome='C3',classe_cerne='C3')
        self.dim['classes_tabuas'][0]['nome']='C3'
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertEqual(v[4]['volume_1'][40.].loc[7],0)
        self.nos[4]['classes_tabuas'][0].update(nome='C1',classe_cerne='C1')
        self.dim['classes_tabuas'][0]['nome']='C1'
        self.dim['classes_tabuas'][0]['preco']=1000
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertAlmostEqual(v[4]['receita_1'][40.].loc[7],387.2)
        self.dim['classes_tabuas'][0]['rendimento']=35
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertEqual(v[4]['volume_1'][40.].loc[7],0)

    def test_comprimento_da_classe_independente_da_tora_global(self):
        self.dim['comprimento_tora']=None
        self.dim['classes_tabuas'][0]['comprimento']=3
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertAlmostEqual(v[4]['volume_1'][40.].loc[7],2*.22*.22*3)

    def test_estratos(self):
        self.df['estrato']=['A','B'];self.df.loc[9,'Ht']=8
        self.nos[3]['variantes']=[dict(equacao='x1*exp(ln(.8)*x4+ln(.625)*x5)',coeficientes={},estrato_coluna='estrato',estrato='A'),
            dict(equacao='x1*.5*exp(ln(.8)*x4+ln(.625)*x5)',coeficientes={},estrato_coluna='estrato',estrato='B')]
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertGreater(v[4]['volume_1'][40.].loc[7],0)
        self.assertEqual(v[4]['volume_1'][40.].loc[9],0)

    def test_erros_de_configuracao_e_ligacao(self):
        for alteracao,trecho in [('dummies','botão direito'),('porta','saída Modelo'),('cadastro','excluída')]:
            self.nos,self.con,self.dim=grafo()
            if alteracao=='dummies':self.nos[3].pop('configuracao_produtos')
            if alteracao=='porta':self.con[-1]['saida_idx']=0
            if alteracao=='cadastro':self.dim['classes_tabuas']=[]
            v,e=self.avaliar()
            self.assertTrue(any(trecho in erro for erro in e),e)
            self.assertNotIn(4,v)

    def test_modelo_antigo_sem_dummies(self):
        self.nos[3].update(variaveis=['D','h','H'],variantes=[dict(equacao='D',coeficientes={})])
        self.nos[3].pop('configuracao_produtos')
        v,e=self.avaliar()
        self.assertTrue(any('Sem casca e Cerne' in erro for erro in e),e)
        # O taper clássico continua calculando volumes; a classificação nova exige cerne previsto.
        self.nos.pop(4);self.con=self.con[:-1]
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertGreater(v[3]['aproveitavel'][40.].loc[7],0)

    def test_saida_com_quantidade_de_arvores(self):
        self.df['arvores']=10
        self.nos[6]=dict(tipo='coluna',coluna='arvores',rotulo='Árvores')
        self.nos[7]=dict(tipo='saida',rotulo='Volume total',nome_saida='total',entradas=[dict(operador='+'),dict(operador='*')])
        self.con.extend([dict(origem=4,destino=7,entrada_idx=0,saida_idx=0),dict(origem=6,destino=7,entrada_idx=1)])
        v,e=self.avaliar();self.assertEqual(e,[])
        self.assertAlmostEqual(v[7][40.].loc[7],3.872)
        self.assertIn('total_40',construtores.saidas_nomeadas(self.nos,v))

    def test_pipeline_em_memoria_usa_cadastro_atual(self):
        c=sqlite3.connect(':memory:');self.addCleanup(c.close);c.executescript(db.SCHEMA)
        construtores.salvar_construtor(c,'Tábuas','dados',dict(nos=self.nos,conexoes=self.con))
        novo,resumo=construtores.aplicar_construtores_em_memoria(c,self.df,'dados',[40],[],None,None,self.dim,[], 'aditiva',[])
        self.assertEqual(resumo['falhas'],[])
        self.assertEqual(resumo['executados'],1)
        self.assertAlmostEqual(novo.loc[7,'t_receita_C1_1_40'],774.4)
        self.assertIn('t_volume_C1_1_40',resumo['colunas_adicionadas'])

    def test_gravacao_e_roundtrip_do_grafo(self):
        c=sqlite3.connect(':memory:');self.addCleanup(c.close);c.executescript(db.SCHEMA)
        id_=construtores.salvar_construtor(c,'Tábuas','dados',dict(nos=self.nos,conexoes=self.con))
        g=construtores.obter_construtor(c,id_)['grafo']
        v,e=construtores.avaliar_grafo(self.df,g['nos'],g['conexoes'],[40],dimensoes_tora=self.dim)
        self.assertEqual(e,[])
        self.df.to_sql('dados',c,index=False)
        construtores.gravar_saidas_como_colunas(c,'dados',self.df,construtores.saidas_nomeadas(g['nos'],v))
        self.assertAlmostEqual(c.execute('SELECT t_receita_C1_1_40 FROM dados WHERE id=1').fetchone()[0],774.4)


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from app.screens.construtor_variaveis import TelaConstrutorVariaveis,_n_saidas
from app.screens.produtos_taper import DialogoProdutosTaper
from app.screens.tabuas_no import DialogoNoTabuas


class ConstrutorTabuasUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        cls.app.setFont(QFont('Segoe UI',10))

    def test_nos_configuracao_dummies_classe_e_render(self):
        with tempfile.TemporaryDirectory() as td:
            projeto.novo_projeto(Path(td)/'nos.mogno')
            tela=None
            try:
                c=conectar()
                classes_tabuas.salvar(c,'C1',4,22,22,2000,rendimento=100)
                c.execute("INSERT INTO modelos(nome,tipo,variaveis_x,equacao,coeficientes) VALUES(?,?,?,?,?)",
                    ('Novo taper','Afilamento / Taper',json.dumps(['x1','x2','x3','x4','x5']),'x1*exp(ln(.8)*x4+ln(.625)*x5)','{}'))
                c.commit();c.close()
                with patch("sys.excepthook") as excecoes_qt:
                    tela=TelaConstrutorVariaveis()
                excecoes_qt.assert_not_called()
                self.assertEqual(tela.combo_estilo_ligacoes.currentData(),"curvas")
                tela._adicionar_no_afilamento(tela._afilamento_disponiveis[0]);taper=max(tela.nos)
                self.assertEqual(_n_saidas(tela.nos[taper]),3)
                def confirmar(d):d._confirmar();return d.result()
                with patch.object(DialogoProdutosTaper,'exec',confirmar):tela._configurar_dummies_taper(taper)
                self.assertEqual(len(tela.nos[taper]['configuracao_produtos']['dummies']),2)
                tela._adicionar_no_tabuas();tabuas=max(tela.nos)
                self.assertEqual(_n_saidas(tela.nos[tabuas]),3)
                with patch.object(DialogoNoTabuas,'exec',confirmar):tela._configurar_tabuas(tabuas)
                self.assertEqual(tela.nos[tabuas]['classes_tabuas'][0]['classe_cerne'],'C1')
                tela.conexoes.append(dict(origem=taper,destino=tabuas,entrada_idx=0,saida_idx=2))
                tela.nos[taper].update(x=100,y=120);tela.nos[tabuas].update(x=420,y=120)
                tela.resize(1300,850);tela.show();self.app.processEvents();tela._redesenhar()
                Path('.ui_previews').mkdir(exist_ok=True)
                tela.grab().save('.ui_previews/construtor_tabuas.png')
            finally:
                if tela:tela.close();tela.deleteLater();self.app.processEvents()
                projeto.fechar_projeto()
