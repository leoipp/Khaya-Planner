"""Formulário de planejamento florestal sem código, com execução em QThread."""
import json

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QTabWidget,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import planejamento, preferencias, projeto
from ..theme import qss, icones
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.painel_operacao import PainelOperacao
from .base import TelaBase


class _ThreadPlanejamento(QThread):
    progresso = Signal(str)

    def __init__(self, banco, solver, cfg, parent):
        super().__init__(parent)
        self.banco, self.solver, self.cfg = banco, solver, cfg
        self.resultado = None
        self.erro = None

    def run(self):
        try:
            self.resultado = planejamento.otimizar(self.banco, self.solver, self.cfg, self.progresso.emit)
        except Exception as erro:
            self.erro = str(erro)


class TelaPlanejamento(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rodando = False
        self._thread = None
        self._esquema = {}
        self.setProperty('interface', 'operacao')
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 0)
        raiz.setSpacing(12)
        raiz.addWidget(CabecalhoTela('Otimização de Planejamento',
            'Equilibre a produção anual e selecione o cenário de maior retorno para cada talhão.'))
        self.abas = QTabWidget()
        self.abas.setObjectName('AbasOperacao')
        self.abas.setDocumentMode(True)
        self.abas.tabBar().setDrawBase(False)
        raiz.addWidget(self.abas, 1)
        self.formulario, config = self._nova_aba('Configuração')
        self.opcoes_avancadas, avancado = self._nova_aba('Dados e solver')
        _, resultados = self._nova_aba('Resultados')
        barra_modelo = QHBoxLayout()
        dica = QLabel('Defina a origem dos cenários e as metas de produção.')
        qss.aplicar_variante(dica, 'dica')
        dica.setWordWrap(True)
        barra_modelo.addWidget(dica, 1)
        salvar = QPushButton('Salvar modelo…')
        salvar.clicked.connect(self._salvar_modelo)
        carregar = QPushButton('Carregar modelo…')
        carregar.clicked.connect(self._carregar_modelo)
        barra_modelo.addWidget(carregar)
        barra_modelo.addWidget(salvar)
        config.addLayout(barra_modelo)
        self._grid_config = QGridLayout()
        self._grid_config.setSpacing(16)
        config.addLayout(self._grid_config)
        self.painel_origem, origem = self._painel('Origem dos cenários',
            'Use os cenários do projeto aberto ou selecione um banco SQLite.')
        self.painel_colunas, colunas = self._painel('Mapeamento das colunas',
            'Associe as informações da tabela aos campos do planejamento.')
        self.painel_metas, metas = self._painel('Horizonte e demanda',
            'Defina o período de planejamento e a produção anual desejada.')
        self.painel_unidades, unidades = self._painel('Unidades e saída',
            'Confira a base dos valores e identifique as tabelas de resultado.')
        self._paineis_config = (self.painel_origem, self.painel_metas, self.painel_unidades)
        self._duas_colunas = None
        self.banco = QLineEdit()
        self.banco.setReadOnly(True)
        self.banco.setPlaceholderText('Nenhum banco selecionado')
        origem.addRow('Banco de dados', self.banco)
        linha = QHBoxLayout()
        escolher = QPushButton('Escolher SQLite…')
        escolher.clicked.connect(self._escolher_banco)
        atual = QPushButton('Usar projeto aberto')
        atual.clicked.connect(self._usar_projeto)
        linha.addWidget(atual)
        linha.addWidget(escolher)
        linha.addStretch(1)
        origem.addRow(linha)
        self.tabela = QComboBox()
        self.tabela.currentTextChanged.connect(self._carregar_colunas)
        origem.addRow('Tabela de cenários', self.tabela)
        self.colunas = {}
        for chave, rotulo in [('talhao', 'Talhão'), ('cenario', 'Cenário'), ('area', 'Área'),
                              ('idade', 'Idade'), ('vpl', 'VPL')]:
            combo = QComboBox()
            combo.setMinimumContentsLength(5)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.colunas[chave] = combo
            colunas.addRow(rotulo, combo)
        painel_solver, solver = self._painel('Parâmetros do solver',
            'A etapa 1 reduz o maior desvio anual. A etapa 2 maximiza o VPL dentro da faixa encontrada.')
        avancado.addWidget(self.painel_colunas)
        avancado.addWidget(painel_solver)
        self.campos = {}
        for chave, rotulo, padrao, minimo, maximo, inteiro in [
            ('inicio', 'Ano inicial', 2026, 1900, 2200, True),
            ('fim', 'Ano final', 2052, 1900, 2200, True),
            ('regulacao', 'Primeiro ano regulado', 2027, 1900, 2200, True),
            ('demanda', 'Demanda anual (m³)', 30000, 0.000001, 1e12, False),
            ('alpha_max', 'Desvio máximo permitido (%)', 500, 0, 10000, False),
            ('folga', 'Folga da etapa 2 (p.p.)', 0, 0, 10000, False),
            ('tolerancia', 'Tolerância numérica (p.p.)', .001, 0, 100, False),
            ('gap_alpha', 'Gap da etapa 1 (%)', .01, 0, 100, False),
            ('gap_vpl', 'Gap da etapa 2 (%)', .1, 0, 100, False),
            ('tempo_alpha', 'Tempo limite da etapa 1 (s)', 1000, 1, 86400, True),
            ('tempo_vpl', 'Tempo limite da etapa 2 (s)', 1000, 1, 86400, True),
            ('threads', 'Threads (0 = automático)', 0, 0, 256, True),
        ]:
            campo = QSpinBox() if inteiro else QDoubleSpinBox()
            if not inteiro:
                campo.setDecimals(6)
            campo.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
            campo.setProperty('controles', 'mais_menos')
            campo.setRange(minimo, maximo)
            campo.setValue(padrao)
            self.campos[chave] = campo
            (metas if chave in ('inicio', 'fim', 'regulacao', 'demanda', 'alpha_max') else solver).addRow(rotulo, campo)
        self.volume_ha = QCheckBox('Volumes anuais por hectare')
        self.volume_ha.setToolTip('Multiplica os volumes anuais pela área do talhão.')
        self.vpl_ha = QCheckBox('VPL por hectare')
        self.vpl_ha.setToolTip('Multiplica o VPL pela área do talhão.')
        unidades.addRow(self.volume_ha)
        unidades.addRow(self.vpl_ha)
        self.prefixo = QLineEdit('vpl_taper_2etapas')
        unidades.addRow('Sufixo dos resultados', self.prefixo)
        explicacao = QLabel('Marque apenas os valores que precisam ser multiplicados pela área do talhão.')
        explicacao.setWordWrap(True)
        qss.aplicar_variante(explicacao, 'dica')
        unidades.addRow(explicacao)
        config.addStretch(1)
        painel_restricoes, form_restricoes = self._painel('Restrições adicionais',
            'Limite a soma de uma coluna nos cenários escolhidos. Para um limite anual, '
            'selecione a coluna do ano. As restrições valem nas duas etapas.')
        avancado.addWidget(painel_restricoes)
        self.restricoes = QTableWidget(0, 4)
        self.restricoes.setHorizontalHeaderLabels(['Coluna', 'Relação', 'Limite', 'Por hectare'])
        self.restricoes.setMinimumHeight(160)
        self.restricoes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.restricoes.setAlternatingRowColors(True)
        form_restricoes.addRow(self.restricoes)
        acoes = QHBoxLayout()
        adicionar = QPushButton('Adicionar restrição')
        adicionar.clicked.connect(self._adicionar_restricao)
        acoes.addWidget(adicionar)
        remover = QPushButton('Remover selecionada')
        remover.clicked.connect(lambda: self.restricoes.removeRow(self.restricoes.currentRow())
                                if self.restricoes.currentRow() >= 0 else None)
        acoes.addWidget(remover)
        acoes.addStretch(1)
        form_restricoes.addRow(acoes)
        avancado.addStretch(1)
        self.painel_producao, form_producao = self._painel('Produção anual',
            'Após a otimização, acompanhe os volumes e os desvios em relação à demanda.')
        resultados.addWidget(self.painel_producao)
        self.producao = QTableWidget(0, 4)
        self.producao.setHorizontalHeaderLabels(['Ano', 'Volume taper (m³)', 'Desvio (%)', 'Regulado'])
        self.producao.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.producao.setAlternatingRowColors(True)
        self.producao.verticalHeader().hide()
        self.producao.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.producao.setMinimumHeight(220)
        form_producao.addRow(self.producao)
        painel_log, form_log = self._painel('Registro de execução')
        resultados.addWidget(painel_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText('As mensagens do solver aparecerão aqui durante a execução.')
        self.log.setMaximumBlockCount(10000)
        self.log.setMinimumHeight(160)
        form_log.addRow(self.log)
        resultados.addStretch(1)
        rodape = QWidget()
        rodape.setObjectName('BarraExecucao')
        barra = QVBoxLayout(rodape)
        barra.setContentsMargins(16, 12, 16, 12)
        self.status = QLabel('Configure o executável CPLEX em Configurações.')
        self.status.setWordWrap(True)
        qss.aplicar_variante(self.status, 'dica')
        barra.addWidget(self.status)
        linha = QHBoxLayout()
        ajuda = QLabel('Objetivo: regular a produção → maximizar o VPL')
        qss.aplicar_variante(ajuda, 'dica')
        ajuda.setWordWrap(True)
        linha.addWidget(ajuda, 1)
        self.executar = QPushButton('Otimizar planejamento')
        qss.aplicar_variante(self.executar, 'salvar')
        icones.aplicar_icone(self.executar, 'gerar', cor='white')
        self.executar.clicked.connect(self._executar)
        linha.addWidget(self.executar)
        barra.addLayout(linha)
        raiz.addWidget(rodape)
        self._ajustar_paineis()
        self.recarregar_lista()

    def _nova_aba(self, titulo):
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        corpo = QWidget()
        layout = QVBoxLayout(corpo)
        layout.setContentsMargins(0, 12, 4, 16)
        layout.setSpacing(16)
        scroll.setWidget(corpo)
        self.abas.addTab(scroll, titulo)
        return corpo, layout

    def _painel(self, titulo, descricao=None):
        painel = PainelOperacao(titulo, descricao=descricao)
        form = QFormLayout(painel.corpo)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        return painel, form

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        self._ajustar_paineis()

    def _ajustar_paineis(self):
        duas = self.width() >= 940
        if duas == self._duas_colunas:
            return
        self._duas_colunas = duas
        for painel in self._paineis_config:
            self._grid_config.removeWidget(painel)
        self._grid_config.setColumnStretch(0, 1)
        self._grid_config.setColumnStretch(1, 1 if duas else 0)
        if duas:
            self._grid_config.addWidget(self.painel_origem, 0, 0, 1, 2)
            self._grid_config.addWidget(self.painel_metas, 1, 0)
            self._grid_config.addWidget(self.painel_unidades, 1, 1)
        else:
            for i, painel in enumerate(self._paineis_config):
                self._grid_config.addWidget(painel, i, 0)

    def _escolher_banco(self):
        caminho, _ = QFileDialog.getOpenFileName(self, "Banco de planejamento", '',
                                               "SQLite (*.sqlite *.sqlite3 *.db);;Todos os arquivos (*)")
        if caminho:
            self._carregar_banco(caminho)

    def _usar_projeto(self):
        try:
            self._carregar_banco(str(projeto.caminho_trabalho()))
        except Exception as erro:
            QMessageBox.warning(self, 'Planejamento', str(erro))

    def _carregar_banco(self, caminho):
        try:
            esquema = planejamento.inspecionar(caminho)
        except Exception as erro:
            QMessageBox.warning(self, 'Banco de planejamento', str(erro))
            return
        self._esquema = esquema
        self.banco.setText(caminho)
        self.tabela.clear()
        self.tabela.addItems(list(esquema))
        if 'resumo_cenario_talhao' in esquema:
            self.tabela.setCurrentText('resumo_cenario_talhao')
        self._carregar_colunas()

    def _carregar_colunas(self):
        colunas = self._esquema.get(self.tabela.currentText(), [])
        for chave, combo in self.colunas.items():
            anterior = combo.currentText()
            combo.clear()
            combo.addItems(colunas)
            candidatos = [anterior, {'talhao': 'TALHÃO', 'cenario': 'cenario', 'area': 'Area',
                                     'idade': 'Idade', 'vpl': 'VPL'}[chave]]
            for candidato in candidatos:
                if candidato in colunas:
                    combo.setCurrentText(candidato)
                    break
        self.restricoes.setRowCount(0)

    def _adicionar_restricao(self, checked=False, dados=None):
        dados = dados or {}
        i = self.restricoes.rowCount()
        self.restricoes.insertRow(i)
        coluna = QComboBox()
        coluna.addItems(self._esquema.get(self.tabela.currentText(), []))
        coluna.setCurrentText(dados.get('coluna', coluna.currentText()))
        operador = QComboBox()
        operador.addItems(['>=', '<=', '='])
        operador.setCurrentText(dados.get('operador', '>='))
        limite = QDoubleSpinBox()
        limite.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        limite.setProperty('controles', 'mais_menos')
        limite.setDecimals(6)
        limite.setRange(-1e15, 1e15)
        limite.setValue(dados.get('limite', 0))
        ha = QCheckBox()
        ha.setChecked(dados.get('por_ha', False))
        for j, widget in enumerate([coluna, operador, limite, ha]):
            self.restricoes.setCellWidget(i, j, widget)

    def _cfg(self):
        cfg = {k: campo.value() for k, campo in self.campos.items()}
        for k in ('alpha_max', 'folga', 'tolerancia', 'gap_alpha', 'gap_vpl'):
            cfg[k] /= 100
        cfg.update({k: combo.currentText() for k, combo in self.colunas.items()})
        cfg.update(tabela=self.tabela.currentText(), prefixo=self.prefixo.text().strip(),
                   volume_ha=self.volume_ha.isChecked(), vpl_ha=self.vpl_ha.isChecked(), restricoes=[])
        for i in range(self.restricoes.rowCount()):
            cfg['restricoes'].append(dict(coluna=self.restricoes.cellWidget(i, 0).currentText(),
                                          operador=self.restricoes.cellWidget(i, 1).currentText(),
                                          limite=self.restricoes.cellWidget(i, 2).value(),
                                          por_ha=self.restricoes.cellWidget(i, 3).isChecked()))
        return cfg

    def _salvar_modelo(self):
        caminho, _ = QFileDialog.getSaveFileName(self, 'Salvar modelo de planejamento', '', 'Modelo (*.json)')
        if caminho:
            try:
                with open(caminho, 'w', encoding='utf-8') as arquivo:
                    json.dump(self._cfg(), arquivo, ensure_ascii=False, indent=2)
            except Exception as erro:
                QMessageBox.warning(self, 'Salvar modelo', str(erro))

    def _carregar_modelo(self):
        caminho, _ = QFileDialog.getOpenFileName(self, 'Carregar modelo', '', 'Modelo (*.json)')
        if not caminho:
            return
        try:
            with open(caminho, encoding='utf-8') as arquivo:
                cfg = json.load(arquivo)
            if cfg['tabela'] not in self._esquema:
                raise ValueError('Selecione primeiro um banco que contenha a tabela do modelo.')
            for chave in self.colunas:
                if cfg[chave] not in self._esquema[cfg['tabela']]:
                    raise ValueError(f'Coluna ausente: {cfg[chave]}')
            for r in cfg.get('restricoes', []):
                if r['coluna'] not in self._esquema[cfg['tabela']]:
                    raise ValueError(f'Coluna ausente: {r["coluna"]}')
            self.tabela.setCurrentText(cfg['tabela'])
            for k, campo in self.campos.items():
                valor = cfg[k] * (100 if k in ('alpha_max', 'folga', 'tolerancia', 'gap_alpha', 'gap_vpl') else 1)
                campo.setValue(valor)
            for k, combo in self.colunas.items():
                combo.setCurrentText(cfg[k])
            self.volume_ha.setChecked(cfg['volume_ha'])
            self.vpl_ha.setChecked(cfg['vpl_ha'])
            self.prefixo.setText(cfg['prefixo'])
            self.restricoes.setRowCount(0)
            for r in cfg.get('restricoes', []):
                self._adicionar_restricao(dados=r)
        except Exception as erro:
            QMessageBox.warning(self, 'Carregar modelo', str(erro))

    def _executar(self):
        if self.rodando:
            return
        try:
            solver = planejamento.validar_solver(preferencias.carregar().get('cplex_executavel', ''))
            if not self.banco.text() or not self.tabela.currentText():
                raise ValueError('Selecione o banco e a tabela de cenários.')
            projeto.finalizar_sincronizacao_pendente()
        except Exception as erro:
            QMessageBox.warning(self, 'Planejamento', str(erro))
            return
        self.rodando = True
        self.formulario.setEnabled(False)
        self.opcoes_avancadas.setEnabled(False)
        self.abas.setCurrentIndex(2)
        self.executar.setEnabled(False)
        self.log.clear()
        self.producao.setRowCount(0)
        self.window().travar_navegacao(True)
        self._thread = _ThreadPlanejamento(self.banco.text(), solver, self._cfg(), self)
        self._thread.progresso.connect(self._progresso)
        self._thread.finished.connect(self._finalizar)
        self._thread.start()

    def _progresso(self, mensagem):
        self.status.setText(mensagem)
        self.log.appendPlainText(mensagem)

    def _finalizar(self):
        thread = self._thread
        self.rodando = False
        self.formulario.setEnabled(True)
        self.opcoes_avancadas.setEnabled(True)
        self.executar.setEnabled(True)
        self.window().travar_navegacao(False)
        if thread.erro:
            if projeto.projeto_aberto() and self.banco.text() == str(projeto.caminho_trabalho()):
                # Pode haver um diagnóstico de inviabilidade recém-gravado.
                projeto.sincronizar()
            self.status.setText('Otimização não concluída. Nenhuma tabela de resultado foi publicada.')
            self.log.appendPlainText(thread.erro)
            QMessageBox.warning(self, 'Planejamento', thread.erro)
        else:
            resultado = thread.resultado
            if projeto.projeto_aberto() and self.banco.text() == str(projeto.caminho_trabalho()):
                projeto.sincronizar()
            resumo = resultado['resumo']
            self.status.setText(f'Concluído: alpha {resumo["ALPHA_ENCONTRADO"]*100:.4f}% | '
                                f'VPL {resumo["VPL_FINAL"]:,.2f} | {resumo["NUM_TALHOES"]} talhões.')
            for etapa in resultado['etapas']:
                self.log.appendPlainText(f'Etapa {etapa["ETAPA"]}: {etapa["STATUS"]}\n{etapa["LOG"]}')
            self.log.appendPlainText('Tabelas criadas:\n' + '\n'.join(resultado['tabelas']))
            self.producao.setRowCount(len(resultado['producao']))
            for i, r in enumerate(resultado['producao']):
                for j, valor in enumerate([r['ANO'], f'{r["VOLUME_TAPER"]:,.2f}',
                                           f'{r["DESVIO_PERCENTUAL"]:.4f}', 'Sim' if r['REGULADO'] else 'Transição']):
                    self.producao.setItem(i, j, QTableWidgetItem(str(valor)))
            self.producao.resizeColumnsToContents()
        thread.deleteLater()
        self._thread = None

    def novo_registro(self):
        if not self.rodando:
            self.banco.clear()
            self._esquema = {}
            self.tabela.clear()
            self.log.clear()
            self.producao.setRowCount(0)

    def recarregar_lista(self):
        if not self.rodando and not self.banco.text() and projeto.projeto_aberto():
            self._usar_projeto()
