import copy
import math
import unittest
from unittest.mock import patch
from app.core.corte_conjunto import planejar_tora,produzir_arvore,validar_vinculos
from app.core.desdobro import CLASSES_MADEIRA_PADRAO


PECAS=[(-1.,-1.,2.,2.),(2.5,-1.,2.,2.),(-5.,-1.,2.,2.),(-1.,4.,2.,2.)]


def classes():
    return [dict(id=i,nome=f'C{i}',classe_cerne=f'C{i}',comprimento=4.,largura=2.,espessura=2.,preco=4000/i,rendimento=100.) for i in (1,2,3)]


class CorteConjuntoTest(unittest.TestCase):
    def planejar(self,cerne,volume=.12,cs=None):
        with patch('app.core.corte_conjunto.encaixar',return_value=PECAS):
            return planejar_tora(20,cerne,4,volume,cs or classes(),CLASSES_MADEIRA_PADRAO,0)

    def test_v4_nao_usa_sobra_fora_dos_trechos_comerciais(self):
        config=dict(geometricas=['x1','x2','x3'],dummies=['x4','x5'],produtos=[
            dict(nome=n,papel=p,valores={'x4':a,'x5':b}) for n,p,a,b in
            [('CC','com_casca',0,0),('SC','sem_casca',1,0),('Cerne','cerne',1,1)]])
        cs=classes()
        for c in cs:c['comprimento']=2.
        with patch('app.core.corte_conjunto.encaixar',return_value=PECAS):
            p=produzir_arvore('x1',{},['x1','x2','x3','x4','x5'],config,
                40.,10.,0.,0.,0.,cs,CLASSES_MADEIRA_PADRAO,
                limite_volume=100.,comprimento_comercial=4.)
        self.assertEqual(len(p['toretes']),4)
        self.assertEqual(p['toretes'][-1]['fim'],8.)

    def test_integracao_em_lote_preserva_referencia_e_classes(self):
        import numpy as np
        from app.core.desdobro import fracao_cerne, fracoes_cerne, classificar_percentual
        rng=np.random.default_rng(42)
        pecas=[tuple(v) for v in rng.uniform([ -15,-15,.1,.1],[15,15,10,10],size=(700,4))]
        for raio in [0.,3.,10.,50.]:
            esperado=[fracao_cerne(p,raio) for p in pecas]
            recebido=fracoes_cerne(pecas,raio)
            np.testing.assert_allclose(recebido,esperado,rtol=0,atol=1e-14)
            self.assertEqual([classificar_percentual(100*x,CLASSES_MADEIRA_PADRAO)['nome'] for x in esperado],
                [classificar_percentual(100*x,CLASSES_MADEIRA_PADRAO)['nome'] for x in recebido])
        self.assertEqual(fracoes_cerne([],3.),[])

    def test_quatro_tabuas_podem_ser_todas_c1(self):
        p=self.planejar(20)
        self.assertEqual(len(p['pecas']),4)
        self.assertEqual([v['classe'] for v in p['pecas']],['C1']*4)
        self.assertAlmostEqual(p['volume'],4*2*2/10000*4)

    def test_quatro_tabuas_se_dividem_por_cerne_sem_duplicar(self):
        p=self.planejar(6)
        self.assertEqual(len(p['pecas']),4)
        self.assertEqual({k:sum(v['classe']==k for v in p['pecas']) for k in ('C1','C2','C3')},{'C1':1,'C2':1,'C3':2})
        self.assertEqual(len({v['retangulo'] for v in p['pecas']}),4)
        self.assertAlmostEqual(p['receita'],.0016*(4000+2000+2*4000/3))
        for v in p['pecas']:
            self.assertEqual(v['classe'],v['classe_cerne'])

    def test_classe_mais_cara_nao_passa_sem_cerne_correto(self):
        p=self.planejar(0)
        self.assertEqual(len(p['pecas']),4)
        self.assertTrue(all(v['classe']=='C3' for v in p['pecas']))
        cs=classes();cs[0]['classe_cerne']='C2'
        with self.assertRaisesRegex(ValueError,'C1 exige'):
            self.planejar(6,cs=cs)
        cs=classes();cs[0]['classe_cerne']='*'
        with self.assertRaisesRegex(ValueError,'Vincule'):
            self.planejar(6,cs=cs)

    def test_rendimento_e_orcamento_compartilhado_com_pecas_inteiras(self):
        cs=classes()
        for c in cs:c['rendimento']=50
        p=self.planejar(6,volume=.0064,cs=cs)
        self.assertEqual(len(p['pecas']),2)
        self.assertAlmostEqual(p['volume'],.0032)
        self.assertLessEqual(p['consumo_madeira'],.0064+1e-10)

    def test_corte_real_mistura_dimensoes_sem_sobrepor_ou_sair_do_disco(self):
        cs=classes();cs[0].update(largura=8,espessura=3);cs[1].update(largura=5,espessura=2)
        p=planejar_tora(30,18,4,math.pi*.15**2*4,cs,CLASSES_MADEIRA_PADRAO,3)
        self.assertTrue(p['pecas'])
        for i,a in enumerate(p['pecas']):
            x,y,w,h=a['retangulo']
            self.assertTrue(all((x+dx)**2+(y+dy)**2<=15**2+1e-8 for dx in (0,w) for dy in (0,h)))
            c=next(c for c in cs if c['id']==a['id_classe'])
            self.assertEqual(sorted([w,h]),sorted([c['largura'],c['espessura']]))
            for b in p['pecas'][i+1:]:
                xx,yy,ww,hh=b['retangulo']
                self.assertTrue(x+w+.3<=xx+1e-8 or xx+ww+.3<=x+1e-8 or y+h+.3<=yy+1e-8 or yy+hh+.3<=y+1e-8)

    def test_comprimentos_diferentes_disputam_fuste_unico(self):
        cs=[dict(id=1,nome='C1',classe_cerne='C1',comprimento=4.,largura=22.,espessura=22.,preco=2000.,rendimento=100.),
            dict(id=2,nome='C1',classe_cerne='C1',comprimento=3.,largura=22.,espessura=22.,preco=1000.,rendimento=100.)]
        config=dict(geometricas=['D','h','H'],dummies=['a','b'],produtos=[dict(nome=n,papel=p,valores={'a':a,'b':b})
            for n,p,a,b in [('CC','com_casca',0,0),('SC','sem_casca',1,0),('Cerne','cerne',1,1)]])
        r=produzir_arvore('D*exp(ln(.8)*a+ln(.625)*b)',{},['D','h','H','a','b'],config,40,8,0,3,0,cs,CLASSES_MADEIRA_PADRAO)
        self.assertAlmostEqual(r['volumes'][1],.3872)
        self.assertEqual(r['volumes'][2],0)
        self.assertEqual([(t['inicio'],t['fim']) for t in r['toretes']],[(0,4),(4,8)])
