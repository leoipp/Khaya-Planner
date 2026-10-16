import math
import unittest

from app.core.desdobro import encaixar, simular


class DesdobroTest(unittest.TestCase):
    def test_referencia_volumetrica_limita_producao_sem_descontar_duas_vezes(self):
        r = simular(30, 10, 2.5, 4, 4, 50, 3, 35)
        volume_tora = math.pi * .15 ** 2 * 4
        esperado = min(r['quantidade'], math.floor(volume_tora * .35 / .01))
        self.assertEqual(r['quantidade_prevista'], esperado)
        self.assertLessEqual(r['rendimento_previsto'], 35)
        self.assertGreater(r['rendimento'], r['rendimento_previsto'])
        self.assertEqual(simular(30, 10, 2.5, 4, 4, 50, 3, 0)['quantidade_prevista'], 0)
        r = simular(30, 10, 2.5, 4, 4, 50, 3, 100)
        self.assertEqual(r['quantidade_prevista'], r['quantidade'])
        for valor in (-1, 101, math.nan):
            with self.assertRaises(ValueError):
                simular(30, 10, 2.5, 4, 4, 50, 3, valor)

    def test_pecas_dentro_do_circulo_sem_sobreposicao(self):
        pecas = encaixar(35, 12, 2.5, 3)
        self.assertGreater(len(pecas), 0)
        for x, y, w, h in pecas:
            for xx in (x, x+w):
                for yy in (y, y+h):
                    self.assertLessEqual(xx*xx + yy*yy, (35/2)**2 + 1e-8)
        for i, (x, y, w, h) in enumerate(pecas):
            for a, b, c, d in pecas[i+1:]:
                self.assertTrue(x+w+.3 <= a+1e-8 or a+c+.3 <= x+1e-8 or
                                y+h+.3 <= b+1e-8 or b+d+.3 <= y+1e-8)

    def test_comprimento_e_extremos_cerne(self):
        for cerne in (0, 100):
            r = simular(30, 10, 2.5, 2, 4.01, cerne, 3)
            self.assertEqual(r['longitudinais'], 2)
            self.assertGreater(r['quantidade'], 0)
            self.assertEqual(r['quantidade'], r['cerne' if cerne else 'alburno'])
            self.assertLessEqual(r['rendimento'], 100)
        self.assertEqual(simular(30, 10, 2.5, 5, 4, 50, 3)['quantidade'], 0)
        self.assertEqual(simular(30, 10, 2.5, 2, 4, 50, 3)['longitudinais'], 1)

    def test_invalido_e_produto_grande(self):
        self.assertEqual(encaixar(10, 20, 2), [])
        for largura in (0, -1, math.nan):
            with self.assertRaises(ValueError):
                encaixar(30, largura, 2)


if __name__ == '__main__':
    unittest.main()
