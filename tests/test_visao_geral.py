import sqlite3
import unittest

from app.core.db import SCHEMA
from app.core.visao_geral import consultar_projeto


class VisaoGeralTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_tabelas_vazias_nao_significam_etapas_presentes(self):
        self.conn.execute('CREATE TABLE resultado_otimizacao_vazio(x INTEGER)')
        estado = consultar_projeto(self.conn)
        self.assertEqual(estado['cenarios'], 0)
        self.assertTrue(all(not e['presente'] for e in estado['etapas']))

    def test_presenca_de_dados_e_cenarios_com_erro(self):
        c = self.conn
        c.execute('CREATE TABLE base_ifc_talhao(talhao TEXT)')
        c.execute("INSERT INTO base_ifc_talhao VALUES('A')")
        c.execute("INSERT INTO modelos(nome,tipo) VALUES('Taper','Afilamento / Taper')")
        c.execute("INSERT INTO sortimentos(nome) VALUES('Serraria')")
        c.execute('INSERT INTO configuracoes(id,comprimento_tora,diametro_minimo_tora) VALUES(1,4,10)')
        c.execute('CREATE TABLE weibull_ifc_talhao(forma REAL)')
        c.execute('INSERT INTO weibull_ifc_talhao VALUES(2)')
        c.executemany('INSERT INTO simulacao_cenarios(nome,status) VALUES(?,?)',
                      [('A','Gerado'), ('B','Pendente'), ('C','Erro: teste')])
        c.execute('CREATE TABLE resultado_otimizacao_exemplo(x INTEGER)')
        c.execute('INSERT INTO resultado_otimizacao_exemplo VALUES(1)')
        consultas = []
        c.set_trace_callback(consultas.append)
        estado = consultar_projeto(c)
        self.assertEqual((estado['modelos'], estado['sortimentos'], estado['cenarios']), (1,1,3))
        self.assertEqual((estado['gerados'], estado['erros']), (1,1))
        self.assertTrue(all(e['presente'] for e in estado['etapas']))
        # O painel não faz COUNT(*) nem carrega as linhas do inventário.
        base = [q for q in consultas if 'FROM "base_ifc_talhao"' in q]
        self.assertEqual(base, ['SELECT 1 FROM "base_ifc_talhao" LIMIT 1'])

    def test_sem_dimensoes_sortimento_continua_a_configurar(self):
        self.conn.execute("INSERT INTO sortimentos(nome) VALUES('Madeira')")
        estado = consultar_projeto(self.conn)
        etapa = next(e for e in estado['etapas'] if e['chave'] == 'sortimentos')
        self.assertFalse(etapa['presente'])

    def test_simulacao_unica_sem_lista_de_cenarios(self):
        self.conn.execute('CREATE TABLE simulacao_talhao_idade(idade INTEGER)')
        self.conn.execute('INSERT INTO simulacao_talhao_idade VALUES(1)')
        estado = consultar_projeto(self.conn)
        etapa = next(e for e in estado['etapas'] if e['chave'] == 'simulacao')
        self.assertTrue(etapa['presente'])
        self.assertEqual(estado['cenarios'], 0)
