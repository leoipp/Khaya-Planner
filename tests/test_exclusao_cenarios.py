import os
import sqlite3
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from app.core import simulacao
from app.screens.simulacao import TelaSimulacao


class ConexaoLimitada(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if len(parameters) > 999:
            raise sqlite3.OperationalError('too many SQL variables')
        return super().execute(sql, parameters)


class ExclusaoCenariosTest(unittest.TestCase):
    def test_muitos_cenarios_em_todas_tabelas_sem_commit_implicito(self):
        conn = sqlite3.connect(':memory:', factory=ConexaoLimitada)
        self.addCleanup(conn.close)
        tabelas = [simulacao.TABELA_LOTE_DISTRIBUICAO, simulacao.TABELA_LOTE_POPULACAO,
                   simulacao.TABELA_LOTE_VOLUME_SORTIMENTO, simulacao.TABELA_LOTE_MIP_CONTINUO,
                   simulacao.TABELA_LOTE_MIP_AJUSTE_LOGISTICO, simulacao.TABELA_CENARIOS_PARQUET]
        for tabela in tabelas:
            conn.execute(f'CREATE TABLE "{tabela}" (cenario_id INTEGER)')
            conn.executemany(f'INSERT INTO "{tabela}" VALUES (?)', [(i,) for i in range(1206)])
        conn.execute('CREATE TABLE simulacao_cenarios (id INTEGER)')
        conn.executemany('INSERT INTO simulacao_cenarios VALUES (?)', [(i,) for i in range(1206)])
        conn.commit()
        TelaSimulacao._excluir_cenarios_por_id(conn, list(range(1,1206)))
        for tabela in tabelas + ['simulacao_cenarios']:
            self.assertEqual(conn.execute(f'SELECT * FROM "{tabela}"').fetchall(), [(0,)])
        conn.rollback()
        for tabela in tabelas + ['simulacao_cenarios']:
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{tabela}"').fetchone()[0],1206)

    def test_sem_tabelas_opcionais_e_lista_vazia(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE simulacao_cenarios (id INTEGER)')
            conn.execute('INSERT INTO simulacao_cenarios VALUES (1)')
            TelaSimulacao._excluir_cenarios_por_id(conn, [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM simulacao_cenarios').fetchone()[0],1)
            TelaSimulacao._excluir_cenarios_por_id(conn, [1])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM simulacao_cenarios').fetchone()[0],0)
