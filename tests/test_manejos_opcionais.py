import itertools
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd
import numpy as np
from app.core import db, simulacao


class ManejosOpcionais(unittest.TestCase):
    def test_migracao_preserva_filhos_indices_e_sequencia(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript('''
            CREATE TABLE simulacao_cenarios (id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT, idade_corte_raso INTEGER NOT NULL);
            CREATE INDEX indice_nome ON simulacao_cenarios(nome);
            CREATE TABLE filhos (cenario_id INTEGER REFERENCES simulacao_cenarios(id) ON DELETE CASCADE, valor BLOB);
            INSERT INTO simulacao_cenarios VALUES (5, 'Original', 25);
            INSERT INTO simulacao_cenarios VALUES (20, 'Excluído', 30);
            DELETE FROM simulacao_cenarios WHERE id=20;
            INSERT INTO filhos VALUES (5, x'1234');
        ''')
        db._permitir_cenarios_sem_corte_raso(conn)
        db._permitir_cenarios_sem_corte_raso(conn)
        self.assertEqual(conn.execute('SELECT * FROM filhos').fetchall(), [(5, b'\x12\x34')])
        self.assertEqual(conn.execute('SELECT * FROM simulacao_cenarios').fetchall(), [(5, 'Original', 25)])
        self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())
        self.assertTrue(conn.execute("SELECT name FROM sqlite_master WHERE name='indice_nome'").fetchone())
        novo = conn.execute('INSERT INTO simulacao_cenarios(nome, idade_corte_raso) VALUES (?, NULL)', ('Sem manejo',))
        self.assertGreater(novo.lastrowid, 20)

    def test_motor_todas_combinacoes_de_manejos(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.executescript(db.SCHEMA)
        for tabela, colunas in db.COLUNAS_NOVAS.items():
            db._adicionar_colunas_faltantes(conn, tabela, colunas)
        conn.execute('INSERT INTO configuracoes(id, numero_minimo_arvores_ha) VALUES (1, 0)')
        pd.DataFrame({'talhao': ['A'], 'fustes': [1000]}).to_sql(simulacao.NOME_TABELA_BASE_IFC, conn, index=False)
        ajuste = pd.DataFrame({'talhao': ['A'], 'forma': [3.0], 'escala': [10.0]})
        campos = ('forma', 'escala', 'dap_med', 'dap_max', 'dap_min', 'ht_med',
                  'fustes_ha_removidos', 'vtcc_ha_removido', 'cv_dap', 'dg', 'truncado_esquerda')
        etapas = pd.DataFrame({'talhao': ['A']})
        for i, sufixo in enumerate(simulacao._SUFIXO_POR_ETAPA.values(), 1):
            for campo in campos:
                etapas[f'{campo}_{sufixo}'] = (100. if campo == 'fustes_ha_removidos' else
                    3. + i if campo == 'forma' else 10. if campo == 'escala' else 0.)
        disponiveis = {k: [.2] for k in ('int_raleio', 'int_desbaste_1', 'int_desbaste_2')}
        obter_ajuste_real = simulacao.obter_ajuste_weibull_por_simulacao
        with patch.object(simulacao, 'obter_ajuste_weibull_por_talhao', return_value=('talhao', ajuste)), \
             patch.object(simulacao, 'obter_intensidades_disponiveis', return_value=disponiveis), \
             patch.object(simulacao, 'obter_idade_maxima_manejo', return_value=6), \
             patch.object(simulacao, 'obter_classes_diametricas', return_value=np.array([5., 10., 15.])), \
             patch.object(simulacao, 'obter_ajuste_weibull_por_simulacao', return_value=etapas):
            for flags in itertools.product((False, True), repeat=4):
                with self.subTest(flags=flags):
                    idades = [i+1 if ativo else None for i, ativo in enumerate(flags)]
                    result = simulacao.gerar_populacao(conn, 'talhao', 'fustes',
                        idades[0], .2, idades[1], .2, idades[2], .2, idades[3], persistir=False)
                    df = result['_df_populacao']
                    self.assertEqual(df['evento_manejo'].notna().sum(), sum(flags))
                    for _, row in df.iterrows():
                        aplicados = [i for i in range(3) if flags[i] and i+1 <= row['idade_simulada']]
                        self.assertEqual(row['fustes_atual'], 1000 - 100 * len(aplicados))
                        self.assertEqual(row['forma_atual'], 4. + max(aplicados) if aplicados else 3.)
            with patch.object(simulacao, 'obter_ajuste_weibull_por_simulacao', side_effect=obter_ajuste_real), \
                 patch.object(simulacao, 'obter_intensidades_disponiveis', side_effect=sqlite3.OperationalError('ausente')):
                result = simulacao.gerar_populacao(conn, 'talhao', 'fustes',
                    None, 0, None, 0, None, 0, None, persistir=False)
                df = result['_df_populacao']
                self.assertTrue(df['evento_manejo'].isna().all())
                self.assertTrue((df['fustes_atual'] == 1000).all())

    def test_sem_manejo_dispensa_ajuste_por_simulacao(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        ajuste = simulacao.obter_ajuste_weibull_por_simulacao(conn, 0, 0, 0)
        self.assertTrue(ajuste.empty)
        self.assertIn('forma_apos_desbaste_2', ajuste.columns)
