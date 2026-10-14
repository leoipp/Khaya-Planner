import unittest
from unittest.mock import patch

import pandas as pd

from app.core import resumos_cenarios


class ResumosMemoria(unittest.TestCase):
    def test_manejo_nulo_inicial_nao_separa_talhao_nem_perde_receita(self):
        campo = 'populacao.fustes_ha_removidos_apos_raleio'
        df = pd.DataFrame({'populacao.talhao': ['A', 'A', 'B', 'B', 'C', 'C', 'C'],
                           campo: [None, 12., None, None, None, 4., 5.],
                           'populacao.ano': [2020, 2021, 2020, 2021, 2020, 2021, 2022],
                           'populacao.receita': [-10., 30., -5., 20., -1., 2., 3.]})
        cfg = dict(grupos=['populacao.talhao', campo],
                   metricas=[dict(coluna='populacao.receita', agregacao='sum', alias='VPL')],
                   pivo=dict(coluna='populacao.ano', valor='populacao.receita'))
        with patch.object(resumos_cenarios, '_juntar', return_value=df), patch.object(
                resumos_cenarios.simulacao, 'obter_coluna_talhao', return_value='talhao'):
            resultado = resumos_cenarios.processar_cenario(None, 1, cfg)
        a = resultado.loc[resultado.talhao == 'A']
        self.assertEqual(len(a), 1)
        self.assertEqual(a.iloc[0]['VPL'], 20.)
        self.assertEqual(a.iloc[0]['2020'], -10.)
        self.assertEqual(a.iloc[0]['2021'], 30.)
        self.assertEqual(a.iloc[0]['fustes_ha_removidos_apos_raleio'], 12.)
        b = resultado.loc[resultado.talhao == 'B']
        self.assertEqual(len(b), 1)
        self.assertTrue(b['fustes_ha_removidos_apos_raleio'].isna().all())
        # Não inventa um valor quando há remoções distintas no mesmo talhão.
        self.assertEqual(len(resultado.loc[resultado.talhao == 'C']), 3)

    def test_pivo_preserva_apenas_combinacoes_observadas(self):
        n = 120
        grupos = ['populacao.talhao', 'populacao.fustes_ha_remover_apos_raleio',
                  'populacao.fustes_ha_remover_apos_desbaste', 'populacao.vtcc_removido']
        df = pd.DataFrame({g: list(range(n)) * 2 for g in grupos})
        df['populacao.idade'] = [10] * n + [20] * n
        df['populacao.volume'] = [2.] * n + [3.] * n
        # Inclui um grupo com chave nula e categoria não observada.
        df.loc[[0, n], grupos[-1]] = float('nan')
        df[grupos[0]] = pd.Categorical(df[grupos[0]], categories=range(n + 1))
        cfg = dict(grupos=grupos, metricas=[dict(coluna='populacao.volume',
                   agregacao='sum', alias='total')],
                   pivo=dict(coluna='populacao.idade', valor='populacao.volume'))
        with patch.object(resumos_cenarios, '_juntar', return_value=df):
            resultado = resumos_cenarios.processar_cenario(None, 1, cfg)
        self.assertEqual(len(resultado), n)
        self.assertTrue((resultado['total'] == 5).all())
        self.assertTrue((resultado['10'] == 2).all())
        self.assertTrue((resultado['20'] == 3).all())
        self.assertEqual(resultado['vtcc_removido'].isna().sum(), 1)


if __name__ == '__main__':
    unittest.main()
