import unittest
import numpy as np
from app.core.tabuas_lote import produzir_lote
from app.core.corte_conjunto import produzir_arvore
from app.core.desdobro import CLASSES_MADEIRA_PADRAO


class TabuasLoteTest(unittest.TestCase):
    def test_lote_equivale_a_corte_detalhado_com_cerne_e_limites(self):
        config=dict(geometricas=['x1','x2','x3'],dummies=['x4','x5'],produtos=[
            dict(nome=n,papel=p,valores={'x4':a,'x5':b}) for n,p,a,b in
            [('CC','com_casca',0,0),('SC','sem_casca',1,0),('Cerne','cerne',1,1)]])
        classes=[dict(id=i,nome=f'C{i}',classe_cerne=f'C{i}',comprimento=2.3,largura=w,espessura=4.,preco=p,rendimento=35.)
                 for i,w,p in [(1,20.,4000.),(2,15.,2600.),(3,10.,1600.)]]
        eq='a*x1*(1+b*ln(1-c*(x2/x3)^d))*exp(-(e*x4+f*x5)/x1)'
        coefs=dict(a=2.596021,b=.157843,c=.998216,d=.007721,e=1.421417,f=9.45757)
        hs=np.array([1.,2.3,4.6,8.,12.21,20.,25.17,30.01])
        limites=np.array([0.,.01,.12,.5,1.,3.,10.,.9])
        for diametro in [12.,20.,40.,60.]:
            atual=produzir_lote(eq,coefs,list(config['geometricas']+config['dummies']),config,diametro,hs,limites,2.3,11.,3.,classes,CLASSES_MADEIRA_PADRAO)
            esperado=[]
            for h,limite in zip(hs,limites):
                r=produzir_arvore(eq,coefs,list(config['geometricas']+config['dummies']),config,diametro,h,11.,3.,0.,classes,CLASSES_MADEIRA_PADRAO,limite,2.3)
                esperado.append([r['volumes'][c['id']] for c in classes])
            np.testing.assert_allclose(atual,esperado,rtol=0,atol=1e-12)
            self.assertTrue(np.all(atual.sum(axis=1)<=limites+1e-9))

    def test_intervalos_de_orcamento_reutilizam_resultado_exato(self):
        from unittest.mock import patch
        from app.core import tabuas_lote
        config=dict(geometricas=['D','h','H'],dummies=['a','b'],produtos=[
            dict(nome=n,papel=p,valores={'a':a,'b':b}) for n,p,a,b in
            [('CC','com_casca',0,0),('SC','sem_casca',1,0),('Cerne','cerne',1,1)]])
        classes=[dict(id=i,nome=f'C{i}',classe_cerne=f'C{i}',comprimento=4.,largura=w,espessura=3.,preco=p,rendimento=35.)
                 for i,w,p in [(1,8.,4000.),(2,5.,2600.),(3,3.,1600.)]]
        eq='D*exp(ln(.8)*a+ln(.625)*b)'
        tabuas_lote._CACHE_PRODUCAO.clear()
        for limite in np.random.default_rng(1).uniform(0.,1.5,40):
            atual=produzir_lote(eq,{},['D','h','H','a','b'],config,40.,[8.],[limite],4.,0.,3.,classes,CLASSES_MADEIRA_PADRAO)
            ref=produzir_arvore(eq,{},['D','h','H','a','b'],config,40.,8.,0.,3.,0.,classes,CLASSES_MADEIRA_PADRAO,limite,4.)
            np.testing.assert_allclose(atual[0],[ref['volumes'][c['id']] for c in classes],rtol=0,atol=1e-12)
        baixo,alto,volumes=list(tabuas_lote._CACHE_PRODUCAO.values())[-1][-1]
        limite=(baixo+alto)/2 if np.isfinite(alto) else baixo+1.
        with patch.object(tabuas_lote,'_produzir',side_effect=AssertionError('Não deveria recalcular o encaixe')):
            atual=produzir_lote(eq,{},['D','h','H','a','b'],config,40.,[8.],[limite],4.,0.,3.,classes,CLASSES_MADEIRA_PADRAO)
        np.testing.assert_allclose(atual[0],volumes,rtol=0,atol=0)
        alteradas=[dict(c,preco=c['preco']*2) for c in classes]
        with patch.object(tabuas_lote,'_produzir',wraps=tabuas_lote._produzir) as calcular:
            produzir_lote(eq,{},['D','h','H','a','b'],config,40.,[8.],[limite],4.,0.,3.,alteradas,CLASSES_MADEIRA_PADRAO)
            calcular.assert_called_once()

    def test_encaixe_compilado_mesmas_grades_da_referencia(self):
        from app.core.tabuas_lote import _grade
        from app.core.desdobro import encaixar
        for d in [10.,20.,39.123,60.]:
            for w,h in [(2.,2.),(4.,15.),(20.,4.)]:
                np.testing.assert_allclose(_grade(d,w,h,.3),np.array(encaixar(d,w,h,3.)).reshape(-1,4),rtol=0,atol=1e-12)
