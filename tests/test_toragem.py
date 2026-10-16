import math
import unittest

from app.core.toragem import tracar_arvore
from app.core.desdobro import simular


class ToragemTest(unittest.TestCase):
    def perfil(self, **kwargs):
        params = dict(equacao='x1*(1-x2/x3)', coeficientes={},
                      variaveis=['x1', 'x2', 'x3'], dap=40, altura=20,
                      comprimento=4, diametro_minimo=10, toco=0)
        params.update(kwargs)
        return tracar_arvore(**params)

    def test_cone_extremos_e_limite_comercial(self):
        p = self.perfil()
        self.assertEqual(len(p['toretes']), 5)
        self.assertEqual(sum(t['comercial'] for t in p['toretes']), 3)
        self.assertAlmostEqual(p['toretes'][0]['diametro_base'], 40)
        self.assertAlmostEqual(p['toretes'][0]['diametro_topo'], 32)
        self.assertAlmostEqual(p['toretes'][0]['diametro_corte'], 32)
        self.assertAlmostEqual(p['sobra'], 8)
        self.assertFalse(p['perfil_nao_monotono'])

    def test_toco_comprimento_e_sobra(self):
        p = self.perfil(toco=.2, altura=19, diametro_minimo=0)
        self.assertEqual(len(p['toretes']), 4)
        self.assertAlmostEqual(p['toretes'][0]['inicio'], .2)
        self.assertAlmostEqual(p['toretes'][-1]['fim'], 16.2)
        self.assertAlmostEqual(p['sobra'], 2.8)

    def test_producao_usa_ponta_fina_e_volume_afilado(self):
        t = self.perfil()['toretes'][0]
        r = simular(t['diametro_corte'], 10, 2.5, 4, 4, 50, 3, 35, t['volume'])
        self.assertAlmostEqual(t['volume'], math.pi/80000*(40**2+32**2)*4)
        self.assertEqual(r['quantidade_prevista'], min(r['quantidade'],
                         math.floor(.35*t['volume']/.01)))
        for x, y, w, h in r['pecas']:
            self.assertLessEqual(max((x+dx)**2+(y+dy)**2
                                     for dx in (0,w) for dy in (0,h)), 16**2+1e-8)

    def test_estreitamento_interior_limita_corte(self):
        p = self.perfil(equacao='20 + 20*(x2-2)^2', altura=4, comprimento=4,
                        diametro_minimo=0)
        t = p['toretes'][0]
        self.assertLess(t['diametro_corte'], t['ponta_fina'])
        self.assertAlmostEqual(t['diametro_corte'], 20)
        self.assertTrue(p['perfil_nao_monotono'])

    def test_modelos_invalidos(self):
        for kwargs in (dict(variaveis=['x']), dict(equacao='-1'),
                       dict(equacao='sqrt(-1)'), dict(toco=20), dict(comprimento=0)):
            with self.assertRaises(ValueError):
                self.perfil(**kwargs)


if __name__ == '__main__':
    unittest.main()
