import os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from app.core import planejamento,preferencias,otimizacao_mista as om


class OtimizacaoConfiguravel(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.banco=str(Path(self.tmp.name,'base.sqlite'))
        self.cfg=dict(modelo='configuravel',tabela='origem',vpl='valor',talhao='grupo',area='area',vpl_ha=False,volume_ha=False,
            sentido='max',selecao_grupo='exatamente_um',tipo_variavel='B',restricoes=[],capacidade_ativa=False,
            inicio=2027,fim=2027,demanda=100.,prefixo='teste',tempo_vpl=20,gap_vpl=0.,threads=1)
        self.exe=preferencias.carregar().get('cplex_executavel','')
    def gravar(self,df):
        with sqlite3.connect(self.banco) as c:df.to_sql('origem',c,index=False,if_exists='replace')
    def dados(self):
        self.gravar(pd.DataFrame(dict(grupo=['A','A','B','B'],valor=[10.,30.,10.,30.],**{'2027':[40.,60.,40.,60.]})))
    def test_sem_demanda_e_capacidade_maxima_nao_impoe_minimo(self):
        self.dados();d=om.preparar(self.banco,self.cfg)
        self.assertEqual(len(d[5]),2)
        cfg=dict(self.cfg,capacidade_ativa=True);d=om.preparar(self.banco,cfg)
        self.assertEqual(d[5][-1]['operador'],'<=')
        om.conferir(dict(x0=1,x2=1),d) # 80 atende teto 100, sem obrigar 100.
        with self.assertRaisesRegex(RuntimeError,'Capacidade'):om.conferir(dict(x1=1,x3=1),d)
    def test_cplex_capacidade_maximizacao_e_sem_capacidade(self):
        if not Path(self.exe).is_file():self.skipTest('CPLEX não instalado')
        self.dados()
        r=planejamento.otimizar(self.banco,self.exe,dict(self.cfg,capacidade_ativa=True))
        self.assertAlmostEqual(r['resumo']['VALOR_OBJETIVO'],40.)
        self.assertAlmostEqual(r['producao'][0]['VOLUME_TAPER'],100.)
        r=planejamento.otimizar(self.banco,self.exe,dict(self.cfg,prefixo='sem_capacidade'))
        self.assertAlmostEqual(r['resumo']['VALOR_OBJETIVO'],60.)
        r=planejamento.otimizar(self.banco,self.exe,dict(self.cfg,prefixo='menor',sentido='min'))
        self.assertAlmostEqual(r['resumo']['VALOR_OBJETIVO'],20.)
    def test_cplex_modelo_misto_com_tres_dominios(self):
        if not Path(self.exe).is_file():self.skipTest('CPLEX não instalado')
        self.gravar(pd.DataFrame(dict(valor=[20.,3.,2.5],uso=[5.,1.,1.],exigir=[0.,0.,1.],tipo=['B','I','C'])))
        cfg=dict(self.cfg,selecao_grupo='livre',coluna_tipo='tipo',limite_superior=10.,restricoes=[dict(coluna='uso',operador='<=',limite=7.),dict(coluna='exigir',operador='>=',limite=.5)])
        r=planejamento.otimizar(self.banco,self.exe,cfg)
        self.assertAlmostEqual(r['resumo']['VALOR_OBJETIVO'],25.5)
        self.assertEqual(len(r['restricoes']),2)
        with sqlite3.connect(self.banco) as c:
            x=c.execute('select DECISAO_OTIMA from resultado_otimizacao_teste').fetchall()
        self.assertEqual([v[0] for v in x],[1.,1.,1.])
    def test_tela_executa_modelo_configuravel_e_exibe_folgas(self):
        if not Path(self.exe).is_file():self.skipTest('CPLEX não instalado')
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        import time
        from PySide6.QtWidgets import QApplication,QMainWindow
        from app.screens.planejamento import TelaPlanejamento
        app=QApplication.instance() or QApplication([]);janela=QMainWindow();janela.travar_navegacao=lambda estado:None
        self.dados();tela=TelaPlanejamento();janela.setCentralWidget(tela);tela._carregar_banco(self.banco)
        tela.colunas['vpl'].setCurrentText('valor');tela.colunas['talhao'].setCurrentText('grupo')
        tela.capacidade_ativa.setChecked(True);tela.campos['inicio'].setValue(2027);tela.campos['fim'].setValue(2027);tela.campos['demanda'].setValue(100.)
        with patch('app.screens.planejamento.preferencias.carregar',return_value={'cplex_executavel':self.exe}),patch('app.screens.planejamento.QMessageBox.warning') as erro:
            tela._executar();prazo=time.monotonic()+20
            while tela.rodando and time.monotonic()<prazo:app.processEvents();time.sleep(.01)
            self.assertFalse(tela.rodando);erro.assert_not_called()
        self.assertEqual(tela.conferencia.rowCount(),3);self.assertEqual(tela.producao.rowCount(),1)
        self.assertIn('40.0000',tela.status.text());janela.close()

    def test_inviabilidade_capacidade_publica_diagnostico(self):
        if not Path(self.exe).is_file():self.skipTest('CPLEX não instalado')
        self.dados()
        with self.assertRaises(planejamento.ModeloInviavel):planejamento.otimizar(self.banco,self.exe,dict(self.cfg,capacidade_ativa=True,demanda=70.))
        with sqlite3.connect(self.banco) as c:self.assertGreater(c.execute('select count(*) from conflitos_teste').fetchone()[0],0)
