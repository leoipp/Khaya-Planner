import copy
import os
import time
from contextlib import closing
import shutil
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.core import planejamento as p


class TestPlanejamento(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.banco = str(Path(self.tmp.name, 'base.sqlite'))
        self.df = pd.DataFrame({'TALHÃO': ['A', 'A', 'B', 'B'], 'cenario': ['1', '2', '1', '2'],
                                'Area': [2., 2., 3., 3.], 'Idade': [10]*4,
                                'VPL': [10., 30., 10., 30.], '2026': [0., 0., 0., 0.],
                                '2027': [40., 60., 40., 60.], '2028': [40., 60., 40., 60.]})
        self.gravar()
        self.cfg = dict(inicio=2026, fim=2028, regulacao=2027, demanda=100.,
                        tempo_alpha=10, tempo_vpl=10, alpha_max=5., folga=0., tolerancia=1e-5,
                        gap_alpha=0.0001, gap_vpl=0.001, threads=1, tabela='origem',
                        talhao='TALHÃO', cenario='cenario', area='Area', idade='Idade', vpl='VPL',
                        volume_ha=False, vpl_ha=False, restricoes=[], prefixo='teste')

    def gravar(self):
        with closing(sqlite3.connect(self.banco)) as conn:
            self.df.to_sql('origem', conn, if_exists='replace', index=False)

    def test_validacao_e_conversao_hectare(self):
        cfg = {**self.cfg, 'volume_ha': True, 'vpl_ha': True}
        df, grupos, vpl, volumes = p.preparar(self.banco, cfg)
        self.assertEqual(volumes[2027].tolist(), [80., 120., 120., 180.])
        self.assertEqual(vpl.tolist(), [20., 60., 30., 90.])
        self.assertEqual(len(grupos), 2)

    def test_rejeita_duplicadas(self):
        self.df.loc[1, 'cenario'] = '1'
        self.gravar()
        with self.assertRaisesRegex(ValueError, 'duplicadas'):
            p.preparar(self.banco, self.cfg)

    def test_rejeita_numeros_invalidos(self):
        for valor in (float('inf'), float('nan'), -1):
            self.df.loc[0, '2027'] = valor
            self.gravar()
            with self.assertRaises(ValueError):
                p.preparar(self.banco, self.cfg)

    def test_rejeita_ano_ausente(self):
        with self.assertRaisesRegex(ValueError, 'Colunas ausentes'):
            p.preparar(self.banco, {**self.cfg, 'fim': 2029})

    def test_verifica_prescricao_e_restricoes(self):
        df, grupos, vpl, volumes = p.preparar(self.banco, self.cfg)
        with self.assertRaisesRegex(RuntimeError, 'exatamente um'):
            p.conferir({}, self.cfg, grupos, df, volumes, (100, 100))
        cfg = {**self.cfg, 'restricoes': [dict(coluna='VPL', operador='>=', limite=100)]}
        with self.assertRaisesRegex(RuntimeError, 'restrição'):
            p.conferir({'x0': 1, 'x3': 1}, cfg, grupos, df, volumes, (100, 100))

    def test_lp_preserva_transicao_e_restricoes(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['restricoes'] = [dict(coluna='VPL', operador='>=', limite=30, por_ha=False)]
        df, grupos, vpl, volumes = p.preparar(self.banco, cfg)
        arquivo = Path(self.tmp.name, 'modelo.lp')
        p.escrever_lp(arquivo, cfg, df, grupos, vpl, volumes)
        texto = arquivo.read_text()
        self.assertNotIn('min_2026', texto)
        self.assertIn('min_2027', texto)
        self.assertIn('adicional_0:', texto)
        self.assertIn('alpha <= 5', texto)

    def test_ano_sem_volume_exige_alpha_de_cem_porcento(self):
        df, grupos, vpl, volumes = p.preparar(self.banco, self.cfg)
        volumes[2028][:] = 0
        with self.assertRaisesRegex(ValueError, '2028.*') as erro:
            p.validar_faixa_demanda({**self.cfg, 'alpha_max': .05}, grupos, volumes)
        self.assertIn('100.0000%', str(erro.exception))
        self.assertIn('500%', str(erro.exception))
        p.validar_faixa_demanda({**self.cfg, 'alpha_max': 1.}, grupos, volumes)
        p.validar_faixa_demanda(self.cfg, grupos, volumes)

    def test_verificacao_demanda_ignora_anos_de_transicao(self):
        _, grupos, _, volumes = p.preparar(self.banco, self.cfg)
        # O volume zero de 2026 não deve exigir relaxação.
        p.validar_faixa_demanda({**self.cfg, 'alpha_max': .2}, grupos, volumes)

    def test_log_entregue_durante_execucao(self):
        marcador = Path(self.tmp.name, 'liberar')
        mensagens = []
        def progresso(linha):
            mensagens.append(linha)
            marcador.touch()
        script = ("import pathlib,time; print('iniciou', flush=True); "
                  f"p=pathlib.Path({str(marcador)!r}); "
                  "exec('while not p.exists(): time.sleep(.01)'); print('terminou', flush=True)")
        codigo, log = p._executar_com_log([sys.executable, '-c', script], self.tmp.name, 5, progresso)
        self.assertEqual(codigo, 0)
        self.assertEqual(mensagens, ['iniciou', 'terminou'])
        self.assertIn('terminou', log)

    @unittest.skipUnless(shutil.which('cplex'), 'CPLEX não instalado')
    def test_alpha_relaxa_demanda_e_vpl_preserva_faixa(self):
        # Demanda exata é impossível: a menor relaxação é 20%.
        # O cenário de maior VPL só pode ser escolhido após preservar essa faixa.
        self.df['2027'] = [40., 40., 40., 40.]
        self.df['2028'] = [120., 40., 40., 40.]
        self.gravar()
        resultado = p.otimizar(self.banco, shutil.which('cplex'), self.cfg)
        resumo = resultado['resumo']
        self.assertAlmostEqual(resumo['ALPHA_ENCONTRADO'], .2)
        self.assertAlmostEqual(resumo['VPL_FINAL'], 60.)
        self.assertAlmostEqual(resumo['VOLUME_MIN'], 79.999)
        self.assertAlmostEqual(resumo['VOLUME_MAX'], 120.001)
        self.assertIn('MIP start', resultado['etapas'][1]['LOG'])
        for ano in resultado['producao'][1:]:
            self.assertGreaterEqual(ano['VOLUME_TAPER'], ano['LIMITE_MIN'])
            self.assertLessEqual(ano['VOLUME_TAPER'], ano['LIMITE_MAX'])

    @unittest.skipUnless(shutil.which('cplex'), 'CPLEX não instalado')
    def test_cplex_real_duas_etapas_e_publicacao(self):
        resultado = p.otimizar(self.banco, shutil.which('cplex'), self.cfg)
        self.assertAlmostEqual(resultado['resumo']['ALPHA_ENCONTRADO'], 0)
        self.assertAlmostEqual(resultado['resumo']['VPL_FINAL'], 40)
        self.assertEqual(len(resultado['tabelas']), 6)
        with closing(sqlite3.connect(self.banco)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM cenarios_escolhidos_teste').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT count(*) FROM origem').fetchone()[0], 4)
            self.assertEqual(conn.execute('SELECT VOLUME_TAPER FROM producao_anual_teste WHERE ANO=2027').fetchone()[0], 100)
        with self.assertRaisesRegex(ValueError, 'Já existem tabelas'):
            p.otimizar(self.banco, shutil.which('cplex'), self.cfg)

    def test_falha_na_segunda_etapa_nao_publica_resultados(self):
        fake = Path(self.tmp.name, 'cplex.exe')
        fake.touch()
        m1 = dict(BOUND=0, GAP=0, TEMPO_S=0)
        with patch.object(p, 'executar_solver', side_effect=[({'alpha': 0, 'x0': 1, 'x3': 1}, m1), RuntimeError('falha')]):
            with self.assertRaisesRegex(RuntimeError, 'falha'):
                p.otimizar(self.banco, str(fake), self.cfg)
        self.assertEqual(list(p.inspecionar(self.banco)), ['origem'])

    @unittest.skipUnless(shutil.which('cplex'), 'CPLEX não instalado')
    def test_cplex_inviavel_salva_conflitos(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['restricoes'] = [dict(coluna='VPL', operador='>=', limite=100)]
        with self.assertRaisesRegex(p.ModeloInviavel, 'Diagnóstico salvo'):
            p.otimizar(self.banco, shutil.which('cplex'), cfg)
        self.assertEqual(set(p.inspecionar(self.banco)), {'origem', 'conflitos_teste'})
        with closing(sqlite3.connect(self.banco)) as conn:
            nomes = [r[0] for r in conn.execute('SELECT RESTRICAO FROM conflitos_teste')]
            detalhe = conn.execute('SELECT DETALHAMENTO FROM conflitos_teste').fetchone()[0]
            self.assertIn('adicional_0', nomes, detalhe)

    def test_publicacao_atomica(self):
        with self.assertRaises(sqlite3.OperationalError):
            p.publicar(self.banco, {'nova': pd.DataFrame({'n': [1]}), 'origem': self.df})
        self.assertEqual(list(p.inspecionar(self.banco)), ['origem'])

    @unittest.skipUnless(shutil.which('cplex'), 'CPLEX não instalado')
    def test_tela_executa_em_thread_e_exibe_resultado(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication, QMainWindow
        from app.screens.planejamento import TelaPlanejamento
        app = QApplication.instance() or QApplication([])
        janela = QMainWindow()
        estados = []
        janela.travar_navegacao = estados.append
        tela = TelaPlanejamento()
        janela.setCentralWidget(tela)
        tela.modelo.setCurrentIndex(tela.modelo.findData('legado'))
        tela._carregar_banco(self.banco)
        for k, widget in tela.campos.items():
            valor = self.cfg[k] * (100 if k in ('alpha_max', 'folga', 'tolerancia', 'gap_alpha', 'gap_vpl') else 1)
            widget.setValue(valor)
        self.assertEqual(tela._cfg()['vpl'], 'VPL')
        with patch('app.screens.planejamento.preferencias.carregar', return_value={'cplex_executavel': shutil.which('cplex')}):
            tela._executar()
            limite = time.monotonic() + 20
            while tela.rodando and time.monotonic() < limite:
                app.processEvents()
                time.sleep(.01)
        self.assertFalse(tela.rodando)
        self.assertEqual(estados, [True, False])
        self.assertEqual(tela.producao.rowCount(), 3)
        self.assertIn('Concluído', tela.status.text())
        janela.close()


if __name__ == '__main__':
    unittest.main()
