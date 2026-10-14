import sqlite3
import unittest

from app.core import mapa_dados, simulacao


class JoinMapa(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE camadas_mapa (tabela TEXT, nome TEXT, geometrias TEXT, projecao TEXT)')
        self.conn.execute("INSERT INTO camadas_mapa VALUES ('shape_teste', 'Teste', '[]', '')")
        self.conn.execute('CREATE TABLE shape_teste (id INTEGER PRIMARY KEY, talhao TEXT, sim_volume REAL)')
        self.conn.executemany('INSERT INTO shape_teste VALUES (?, ?, 777)', [(1, '1'), (2, '2'), (3, 'X'), (4, None)])
        self.fonte = simulacao.TABELA_POPULACAO
        self.conn.execute(f'CREATE TABLE "{self.fonte}" (id INTEGER PRIMARY KEY, talhao TEXT, idade INTEGER, cenario TEXT, volume REAL, classe TEXT)')
        self.conn.executemany(f'INSERT INTO "{self.fonte}" VALUES (?, ?, ?, ?, ?, ?)',
                              [(1, '1', 10, 'C1', 10, 'A'), (2, '1', 20, 'C1', 20, 'B'),
                               (3, '2', 10, 'C1', 30, 'B'), (4, '2', 10, 'C2', 40, 'C'),
                               (5, None, 10, 'C1', 999, 'D')])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def join(self, **kwargs):
        config = dict(camada='shape_teste', fonte=self.fonte, chave_shape='talhao',
                      chave_simulacao='talhao', selecionadas=['volume', 'classe'])
        config.update(kwargs)
        return mapa_dados.aplicar_join(self.conn, **config)

    def test_join_filtrado_preserva_geometria_e_colunas_originais(self):
        filtros = [dict(coluna='idade', operador='=', valor='10'), dict(coluna='cenario', operador='=', valor='C1')]
        resultado = self.join(filtros=filtros)
        self.assertEqual(resultado['linhas_fonte'], 3)
        self.assertEqual(resultado['correspondencias'], 2)
        self.assertEqual(resultado['sem_correspondencia'], 2)
        self.assertEqual(resultado['colunas']['volume'], 'sim_volume_join')
        self.assertEqual(self.conn.execute('SELECT sim_volume, sim_volume_join, sim_classe FROM shape_teste ORDER BY id').fetchall(),
                         [(777, 10, 'A'), (777, 30, 'B'), (777, None, None), (777, None, None)])
        self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM "{self.fonte}"').fetchone()[0], 5)
        self.assertEqual(mapa_dados.obter_estado(self.conn, 'join:shape_teste'), resultado)

    def test_repeticoes_exigem_escolha_e_nao_alteram_camada(self):
        with self.assertRaisesRegex(ValueError, 'várias linhas'):
            self.join()
        self.assertNotIn('sim_classe', dict(mapa_dados.colunas(self.conn, 'shape_teste')))
        self.assertEqual(self.conn.execute('SELECT sim_volume FROM shape_teste').fetchall(), [(777,)] * 4)

    def test_join_inteiro_primeiro_ultimo_e_soma(self):
        self.join(repeticoes='primeiro')
        self.assertEqual(self.conn.execute('SELECT sim_volume_join FROM shape_teste ORDER BY id').fetchall(), [(10,), (30,), (None,), (None,)])
        self.join(repeticoes='ultimo')
        self.assertEqual(self.conn.execute('SELECT sim_volume_join FROM shape_teste ORDER BY id').fetchall(), [(20,), (40,), (None,), (None,)])
        self.join(repeticoes='soma', selecionadas=['volume'])
        self.assertEqual(self.conn.execute('SELECT sim_volume_join, sim_classe FROM shape_teste ORDER BY id').fetchall(), [(30, None), (70, None), (None, None), (None, None)])

    def test_media_min_max(self):
        for modo, esperado in [('media', [15, 35]), ('min', [10, 30]), ('max', [20, 40])]:
            self.join(repeticoes=modo, selecionadas=['volume'])
            self.assertEqual([r[0] for r in self.conn.execute('SELECT sim_volume_join FROM shape_teste ORDER BY id LIMIT 2')], esperado)

    def test_agregacao_textual_e_recorte_vazio_sao_recusados(self):
        with self.assertRaisesRegex(ValueError, 'numéricas'):
            self.join(repeticoes='soma')
        with self.assertRaisesRegex(ValueError, 'não contém linhas'):
            self.join(filtros=[dict(coluna='idade', operador='=', valor='999')])

    def test_normalizacao_numerica_nao_cria_matches_com_texto_invalido(self):
        self.conn.execute("UPDATE shape_teste SET talhao='001' WHERE id=1")
        texto = self.join(repeticoes='primeiro')
        self.assertEqual(texto['correspondencias'], 1)
        numeros = self.join(repeticoes='primeiro', modo_chave='numero')
        self.assertEqual(numeros['correspondencias'], 2)
        self.assertIsNone(mapa_dados.normalizar_chave('ABC', 'numero'))
        self.assertIsNone(mapa_dados.normalizar_chave('nan', 'numero'))
        self.assertEqual(mapa_dados.normalizar_chave('1,0', 'numero'), '1')

    def test_zero_correspondencias_preserva_join_anterior(self):
        self.join(repeticoes='primeiro')
        antes = self.conn.execute('SELECT * FROM shape_teste ORDER BY id').fetchall()
        with self.assertRaisesRegex(ValueError, 'Nenhuma chave'):
            self.join(repeticoes='primeiro', chave_simulacao='classe')
        self.assertEqual(self.conn.execute('SELECT * FROM shape_teste ORDER BY id').fetchall(), antes)

    def test_filtros_combinados_listas_nulos_e_paginacao(self):
        filtros = [dict(coluna='idade', operador='>=', valor='20'), dict(coluna='cenario', operador='=', valor='C2')]
        _, linhas, total = mapa_dados.consultar(self.conn, self.fonte, filtros, 'OU')
        self.assertEqual(total, 2)
        self.assertEqual([r[0] for r in linhas], [2, 4])
        _, linhas, total = mapa_dados.consultar(self.conn, self.fonte, [dict(coluna='classe', operador='em lista', valor='A;C')])
        self.assertEqual([r[0] for r in linhas], [1, 4])
        _, linhas, total = mapa_dados.consultar(self.conn, self.fonte, [dict(coluna='talhao', operador='é nulo')])
        self.assertEqual(total, 1)
        self.assertEqual(linhas[0][0], 5)
        _, linhas, total = mapa_dados.consultar(self.conn, self.fonte, limite=2, pagina=1)
        self.assertEqual(total, 5)
        self.assertEqual([r[0] for r in linhas], [3, 4])

    def test_like_literal_e_identificadores_validados(self):
        self.conn.execute(f'UPDATE "{self.fonte}" SET classe=? WHERE id=1', ('A_%',))
        _, linhas, _ = mapa_dados.consultar(self.conn, self.fonte, [dict(coluna='classe', operador='contém', valor='_%')])
        self.assertEqual([r[0] for r in linhas], [1])
        with self.assertRaises(ValueError):
            mapa_dados.consultar(self.conn, self.fonte, [dict(coluna='inexistente', operador='=', valor='1')])
        with self.assertRaisesRegex(ValueError, 'número'):
            mapa_dados.consultar(self.conn, self.fonte, [dict(coluna='idade', operador='=', valor='dez')])

    def test_categorias_preservam_tipos_e_nulos(self):
        cats = mapa_dados.categorias([None, float('nan'), 1, 1.0, '1', 'A'])
        self.assertEqual(cats['null']['quantidade'], 2)
        self.assertEqual(cats['numero:1']['quantidade'], 2)
        self.assertEqual(cats['texto:1']['quantidade'], 1)

    def test_primeiro_ultimo_sem_coluna_id(self):
        self.conn.execute(f'DROP TABLE "{self.fonte}"')
        self.conn.execute(f'CREATE TABLE "{self.fonte}" (talhao TEXT, volume REAL)')
        self.conn.executemany(f'INSERT INTO "{self.fonte}" VALUES (?, ?)', [('1', 5), ('1', 15), ('2', 25)])
        self.join(repeticoes='primeiro', selecionadas=['volume'])
        self.assertEqual(self.conn.execute('SELECT sim_volume_join FROM shape_teste ORDER BY id LIMIT 2').fetchall(), [(5,), (25,)])
        self.join(repeticoes='ultimo', selecionadas=['volume'])
        self.assertEqual(self.conn.execute('SELECT sim_volume_join FROM shape_teste ORDER BY id LIMIT 2').fetchall(), [(15,), (25,)])


if __name__ == '__main__':
    unittest.main()
