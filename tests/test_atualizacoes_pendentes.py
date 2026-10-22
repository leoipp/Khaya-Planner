import os,sqlite3,tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QComboBox,QTableWidget,QWidget,QVBoxLayout
from PySide6.QtCore import Qt
from app.core import projeto,resumos_cenarios
from app.core.db import conectar
from app.screens.resumos_cenarios import TelaResumosCenarios
from app.theme import qss
from app.widgets.combos_legiveis import instalar


class SegurancaSalvamento(unittest.TestCase):
    def test_falta_de_espaco_preserva_original_e_bloqueia_fechamento(self):
        with tempfile.TemporaryDirectory() as td:
            trabalho=Path(td,'trabalho.sqlite');original=Path(td,'projeto.mogno')
            with sqlite3.connect(trabalho) as c:c.execute('create table dados(x)')
            c.close()
            original.write_bytes(b'original')
            with patch.object(projeto,'_caminho_projeto',original),patch.object(projeto,'_caminho_trabalho',trabalho),patch.object(projeto,'_erro_sincronizacao',None),patch.object(projeto,'_avisos_salvamento',None),patch.object(projeto.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
                projeto._executar_sincronizacao_segura(original,trabalho)
                self.assertEqual(original.read_bytes(),b'original')
                self.assertTrue(trabalho.exists())
                self.assertTrue(trabalho.with_name(trabalho.name+'.recovery.json').exists())
                with self.assertRaises(OSError):projeto.fechar_projeto()
                self.assertTrue(trabalho.exists())
    def test_falha_durante_codificacao_remove_so_copias_incompletas(self):
        with tempfile.TemporaryDirectory() as td:
            trabalho=Path(td,'trabalho.sqlite');original=Path(td,'projeto.mogno')
            with sqlite3.connect(trabalho) as c:c.execute('create table dados(x)')
            c.close()
            original.write_bytes(b'original')
            def falhar(origem,destino):Path(destino).write_bytes(b'incompleto');raise OSError(28,'disco cheio')
            with patch.object(projeto,'_erro_sincronizacao',None),patch.object(projeto.codificacao,'codificar_arquivo',side_effect=falhar):
                with self.assertRaises(OSError):projeto._sincronizar_agora(original,trabalho)
                self.assertEqual(original.read_bytes(),b'original');self.assertTrue(trabalho.exists())
                self.assertFalse(Path(str(original)+'.tmp').exists());self.assertFalse(Path(str(trabalho)+'.backup.tmp').exists())
    def test_inicio_nao_remove_bancos_de_sessoes_interrompidas(self):
        with tempfile.TemporaryDirectory() as td:
            pasta=Path(td,'khaya_planner_v2');pasta.mkdir();banco=pasta/'recuperavel.sqlite';banco.write_bytes(b'dados')
            with patch.object(projeto,'pasta_temporaria',return_value=Path(td)):projeto.limpar_arquivos_orfaos()
            self.assertTrue(banco.exists())


class AtualizacoesUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_configuracao_resumo_reabre_com_todos_campos(self):
        with tempfile.TemporaryDirectory() as td,patch.object(projeto,'_temporizador',None):
            arquivo=Path(td,'teste.mogno');projeto.novo_projeto(arquivo)
            try:
                with patch.object(resumos_cenarios,'colunas_fonte',return_value=['cenario','talhao','ano','valor']):
                    tela=TelaResumosCenarios();tela.nome_config.setText('Minha configuração');tela.nome_tabela.setText('resumo_customizado')
                    tela._adicionar_metrica(dict(coluna='populacao.valor',agregacao='sum',alias='receita',arredondar=2))
                    tela.combo_pivo_coluna.setCurrentIndex(tela.combo_pivo_coluna.findData('populacao.ano'))
                    tela.combo_pivo_valor.setCurrentIndex(tela.combo_pivo_valor.findData('populacao.valor'))
                    tela.filtro_colunas.setText('populacao.talhao');tela.filtro_valores.setPlainText('A\nB')
                    esperado=tela._cfg();tela._salvar();tela.deleteLater()
                    projeto.fechar_projeto();projeto.confirmar_abertura(*projeto.abrir_projeto(arquivo))
                    nova=TelaResumosCenarios()
                    self.assertEqual(nova._cfg(),esperado);self.assertEqual(nova.nome_config.text(),'Minha configuração')
                    nova.recarregar_lista();self.assertEqual(nova._cfg(),esperado)
                    with patch.object(resumos_cenarios,'colunas_fonte',return_value=[]):
                        sem_dados=TelaResumosCenarios();self.assertEqual(sem_dados._cfg(),esperado)
                        sem_dados.recarregar_lista();self.assertEqual(sem_dados._cfg(),esperado);sem_dados.deleteLater()
                    projeto.novo_projeto(Path(td,'outro.mogno'))
                    nova.recarregar_lista();self.assertEqual(nova.tabela_metricas.rowCount(),0);self.assertEqual(nova.nome_config.text(),'');nova.deleteLater()
            finally:projeto.fechar_projeto()
    def test_combos_em_tabela_tem_altura_para_fonte_e_opcoes(self):
        instalar(self.app)
        for modo in ('light','dark'):
            self.app.setStyleSheet(qss.gerar(modo))
            tela=QWidget();layout=QVBoxLayout(tela);tabela=QTableWidget(1,1);layout.addWidget(tabela)
            combo=QComboBox();combo.addItems(['Máxima capacidade da serraria','Classificação de cerne']);tabela.setCellWidget(0,0,combo)
            tela.resize(640,300);tela.show();self.app.processEvents()
            self.assertGreaterEqual(combo.height(),combo.fontMetrics().height()+14)
            self.assertGreaterEqual(tabela.rowHeight(0),combo.minimumHeight())
            tela.close();tela.deleteLater();self.app.processEvents()
