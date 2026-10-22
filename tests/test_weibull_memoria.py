import sqlite3
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.core import db, weibull_fit as w


class TestWeibullMemoria(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript(db.SCHEMA)
        self.conn.execute('PRAGMA temp_store = MEMORY')
        self.dados = pd.DataFrame([
            (talhao, 10., 20., 30., etapa, dap, 'Remanescente')
            for dap in (12., 17., None, 24., 0., 32.)
            for talhao in ('B', 'A')
            for etapa in ('Após raleio', 'Após 1º desbaste')
        ], columns=['talhao', 'int_raleio', 'int_desbaste_1', 'int_desbaste_2',
                    'etapa', 'dap', 'situacao'])
        self.dados.to_sql(w.TABELA_DETALHAMENTO, self.conn, index=False)
        colunas = ['talhao', 'int_raleio', 'int_desbaste_1', 'int_desbaste_2']
        for etapa in w.ETAPAS:
            colunas += [f'{nome}_{etapa}' for nome in (
                'dap_med_apos', 'dap_max_apos', 'dap_min_apos', 'ht_med_apos',
                'fustes_ha_removidos', 'vtcc_ha_removido')]
        resumo = pd.DataFrame([['A', 10., 20., 30.] + [None]*(len(colunas)-4)], columns=colunas)
        resumo.to_sql(w.TABELA_RESUMO_TALHAO, self.conn, index=False)

    def test_ajuste_equivalente_sem_carregar_detalhamento_em_pandas(self):
        ler = pd.read_sql_query
        def leitura(sql, *args, **kwargs):
            self.assertNotIn(w.TABELA_DETALHAMENTO, sql)
            return ler(sql, *args, **kwargs)
        progresso = []
        with patch.object(w.pd, 'read_sql_query', side_effect=leitura):
            resultado = w.ajustar_a_partir_da_simulacao(
                self.conn, lambda numero, total: progresso.append((numero, total)))
        self.assertEqual(resultado['ajustados'], 4)
        esperado = w.ajustar_grupo(np.array([12., 17., np.nan, 24., 0., 32.]))
        for escala, forma in self.conn.execute('SELECT escala, forma FROM parametros_weibull_manejo'):
            self.assertAlmostEqual(escala, esperado['scale'], places=10)
            self.assertAlmostEqual(forma, esperado['shape'], places=10)
        self.assertEqual(progresso, [(i, 4) for i in range(1, 5)])
        self.assertEqual(self.conn.execute('PRAGMA temp_store').fetchone()[0], 2)

    def test_restaura_configuracao_ao_interromper_leitura(self):
        with self.assertRaisesRegex(RuntimeError, 'interrompido'):
            with w._grupos_remanescentes(self.conn) as (total, grupos):
                self.assertEqual(total, 4)
                self.assertEqual(self.conn.execute('PRAGMA temp_store').fetchone()[0], 1)
                next(grupos)
                raise RuntimeError('interrompido')
        self.assertEqual(self.conn.execute('PRAGMA temp_store').fetchone()[0], 2)

    def test_nao_ha_remanescentes(self):
        self.conn.execute(f'UPDATE "{w.TABELA_DETALHAMENTO}" SET situacao = ?', ('Removida',))
        with self.assertRaisesRegex(ValueError, 'remanescentes'):
            w.ajustar_a_partir_da_simulacao(self.conn)


if __name__ == '__main__':
    unittest.main()
