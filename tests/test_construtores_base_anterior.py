import copy
import unittest

import pandas as pd

from app.core import construtores


class TestBaseAnteriorManejo(unittest.TestCase):
    def setUp(self):
        # Dados fora de ordem, dois talhões e dois cenários.
        self.df = pd.DataFrame({
            'talhao': ['A', 'B', 'A', 'A', 'A', 'A'],
            'cenario': [1, 1, 1, 2, 2, 1],
            'idade_simulada': [10, 10, 9, 9, 10, 11],
            'evento_manejo': ['Raleio', 'Raleio', None, None, 'Raleio', None],
            'intensidade_evento': [.5, .5, None, None, .5, None],
            'dap_med_atual': [30., 90., 20., 40., 50., 35.],
            'remocao_absoluta': [5., 7., 0., 0., 6., 0.],
        }, index=[20, 30, 40, 50, 60, 70])
        self.nos = {
            1: {'tipo': 'coluna', 'coluna': 'dap_med_atual', 'rotulo': 'DAP'},
            2: {'tipo': 'coluna', 'coluna': 'idade_simulada', 'rotulo': 'Idade'},
            3: {'tipo': 'coluna', 'coluna': 'remocao_absoluta', 'rotulo': 'Remoção'},
            4: {'tipo': 'saida', 'rotulo': 'Compartilhado',
                'entradas': [{'operador': '+'}, {'operador': '*'}]},
            5: {'tipo': 'saida', 'rotulo': 'Desbaste',
                'usar_base_anterior_manejo': True,
                'entradas': [{'operador': '+'}, {'operador': '*'}]},
            6: {'tipo': 'saida', 'rotulo': 'Remanescente',
                'entradas': [{'operador': '+'}]},
        }
        self.conexoes = [
            {'origem': 1, 'destino': 4, 'entrada_idx': 0},
            {'origem': 2, 'destino': 4, 'entrada_idx': 1},
            {'origem': 4, 'destino': 5, 'entrada_idx': 0},
            {'origem': 3, 'destino': 5, 'entrada_idx': 1},
            {'origem': 4, 'destino': 6, 'entrada_idx': 0},
        ]

    def avaliar(self):
        return construtores.avaliar_grafo(
            self.df, self.nos, self.conexoes, coluna_talhao='talhao')

    def test_contextos_isolados_idade_remocao_e_grupos(self):
        original = self.df.copy(deep=True)
        grafo = copy.deepcopy(self.nos)
        valores, erros = self.avaliar()
        self.assertEqual(erros, [])
        self.assertEqual(valores[5].loc[20], 20 * 10 * 5)
        self.assertEqual(valores[5].loc[60], 40 * 10 * 6)
        self.assertTrue(pd.isna(valores[5].loc[30]))  # B não tem idade anterior.
        self.assertEqual(valores[6].loc[20], 30 * 10)
        self.assertEqual(valores[4].loc[20], 30 * 10)
        self.assertEqual(valores[5].loc[70], 0)
        pd.testing.assert_frame_equal(self.df, original)
        self.assertEqual(self.nos, grafo)

    def test_duas_saidas_habilitadas_nao_recuam_duas_vezes(self):
        self.nos[4]['usar_base_anterior_manejo'] = True
        valores, erros = self.avaliar()
        self.assertEqual(erros, [])
        self.assertEqual(valores[5].loc[20], 20 * 10 * 5)

    def test_evento_sem_intensidade_nao_troca_base(self):
        self.df.loc[20, 'intensidade_evento'] = 0
        valores, erros = self.avaliar()
        self.assertEqual(erros, [])
        self.assertEqual(valores[5].loc[20], 30 * 10 * 5)

    def test_falta_de_talhao_nao_impede_saida_normal(self):
        valores, erros = construtores.avaliar_grafo(self.df, self.nos, self.conexoes)
        self.assertNotIn(5, valores)
        self.assertIn('talhão', erros[0])
        self.assertEqual(valores[6].loc[20], 300)

    def test_idades_ambiguas_reportam_erro(self):
        self.df.loc[70, 'idade_simulada'] = 10
        valores, erros = self.avaliar()
        self.assertNotIn(5, valores)
        self.assertIn('mais de uma linha', erros[0])

    def test_resultado_por_classe_e_somatorio(self):
        self.nos[7] = {'tipo': 'classe_diametrica', 'rotulo': 'Classe'}
        self.nos[8] = {
            'tipo': 'modelo', 'rotulo': 'Volume', 'variaveis': ['x1', 'x2'],
            'variantes': [{'equacao': 'x1*x2', 'coeficientes': {},
                           'estrato_coluna': None, 'estrato': None}]}
        self.nos[9] = {'tipo': 'calculo', 'rotulo': 'Total',
                       'passos': [{'operador': 'soma_classes'}]}
        self.conexoes = [c for c in self.conexoes if c['destino'] != 4] + [
            {'origem': 7, 'destino': 8, 'entrada_idx': 0},
            {'origem': 1, 'destino': 8, 'entrada_idx': 1},
            {'origem': 8, 'destino': 4, 'entrada_idx': 0},
            {'origem': 2, 'destino': 4, 'entrada_idx': 1},
            {'origem': 5, 'destino': 9, 'entrada_idx': 0},
        ]
        valores, erros = construtores.avaliar_grafo(
            self.df, self.nos, self.conexoes, [10., 20.], coluna_talhao='talhao')
        self.assertEqual(erros, [])
        self.assertEqual(valores[5][10.].loc[20], 10 * 20 * 10 * 5)
        self.assertEqual(valores[6][10.].loc[20], 10 * 30 * 10)
        self.assertEqual(valores[9].loc[20], (10 + 20) * 20 * 10 * 5)


if __name__ == '__main__':
    unittest.main()
