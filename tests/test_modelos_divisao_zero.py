import unittest
import warnings
import numpy as np
import pandas as pd
from app.core import construtores, motor_modelos


class DivisaoZero(unittest.TestCase):
    def test_modelo_identifica_formula_e_no_sem_repetir_avisos(self):
        for por_classe in (False, True):
            with self.subTest(por_classe=por_classe):
                nos = {1:dict(tipo='coluna',rotulo='DAP',coluna='dap'),
                       2:dict(tipo='modelo',rotulo='Meu modelo',variaveis=['x'],
                              variantes=[dict(equacao='1/x',coeficientes={})])}
                conexoes=[dict(origem=1,destino=2,entrada_idx=0)]
                if por_classe:
                    nos[3]=dict(tipo='classe_diametrica',rotulo='Classe')
                    nos[2]['variaveis']=['x','d']
                    conexoes.append(dict(origem=3,destino=2,entrada_idx=1))
                with warnings.catch_warnings(record=True) as avisos:
                    warnings.simplefilter('always')
                    valores,erros=construtores.avaliar_grafo(pd.DataFrame({'dap':[0.,2.]}),nos,conexoes,[10.,20.])
                self.assertEqual(avisos,[])
                self.assertNotIn(2,valores)
                self.assertTrue(any('Meu modelo' in e and '1/x' in e and 'zero' in e for e in erros),erros)

    def test_limite_intermediario_valido_e_estado_numpy_preservado(self):
        anterior=np.geterr().copy()
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter('always')
            r=motor_modelos.avaliar_expressao_array('exp(-1/x)',{}, {'x':np.array([0.,1.])})
        np.testing.assert_allclose(r,[0.,np.exp(-1)])
        self.assertEqual(avisos,[])
        self.assertEqual(np.geterr(),anterior)
