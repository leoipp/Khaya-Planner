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
            'Defina objetivo, decisões e restrições para problemas lineares e inteiros mistos.'))
        self.abas = QTabWidget()
        self.abas.setObjectName('AbasOperacao')
        self.abas.setDocumentMode(True)
        self.abas.tabBar().setDrawBase(False)
        raiz.addWidget(self.abas, 1)
        self.formulario, config = self._nova_aba('Problema')
        self.opcoes_avancadas, avancado = self._nova_aba('Restrições e solver')
        _, resultados = self._nova_aba('Resultados')
        barra_modelo = QHBoxLayout()
        dica = QLabel('Configure o problema e cadastre apenas as restrições necessárias.')
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
        self.painel_origem, origem = self._painel('1. Origem dos dados',
            'Use os cenários do projeto aberto ou selecione um banco SQLite.')
        self.painel_colunas, colunas = self._painel('Mapeamento das colunas',
            'Associe as informações da tabela aos campos do planejamento.')
        self.painel_metas, metas = self._painel('Horizonte de planejamento',
            'A capacidade opcional vale do ano inicial ao final. O primeiro ano regulado é usado somente no modelo legado.')
        self.painel_demanda, demanda = self._painel('Capacidade de processamento / regulação',
            'Capacidade é um teto opcional de entrada na serraria. O modelo de regulação em duas etapas permanece disponível separadamente.')
        self.painel_unidades, unidades = self._painel('Unidades e saída',
            'Confira a base dos valores e identifique as tabelas de resultado.')
        self._paineis_config = (self.painel_metas,
                               self.painel_demanda, self.painel_unidades)
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
                              ('idade', 'Idade'), ('vpl', 'Indicador do objetivo')]:
            combo = QComboBox()
            combo.setMinimumContentsLength(5)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.colunas[chave] = combo
            colunas.addRow(rotulo, combo)
        painel_solver, solver = self._painel('Parâmetros do solver',
            'Configure limites de execução e tolerâncias. Os parâmetros de alfa são exclusivos da regulação legada.')
        avancado.addWidget(self.painel_colunas)
        avancado.addWidget(painel_solver)
        self.campos = {}
        for chave, rotulo, padrao, minimo, maximo, inteiro in [
            ('inicio', 'Ano inicial', 2026, 1900, 2200, True),
            ('fim', 'Ano final', 2052, 1900, 2200, True),
            ('regulacao', 'Primeiro ano regulado', 2027, 1900, 2200, True),
            ('demanda', 'Capacidade anual / meta (m³)', 30000, 0.000001, 1e12, False),
            ('alpha_max', 'Limite superior de alfa (%)', 500, 0, 10000, False),
            ('folga', 'Folga da etapa 2 (p.p.)', 0, 0, 10000, False),
            ('tolerancia', 'Tolerância numérica (p.p.)', .001, 0, 100, False),
            ('gap_alpha', 'Gap da etapa 1 (%)', .01, 0, 100, False),
            ('gap_vpl', 'Gap da otimização (%)', .1, 0, 100, False),
            ('tempo_alpha', 'Tempo limite da etapa 1 (s)', 1000, 1, 86400, True),
            ('tempo_vpl', 'Tempo limite da otimização (s)', 1000, 1, 86400, True),
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
            destino = (metas if chave in ('inicio', 'fim', 'regulacao') else
                       demanda if chave in ('demanda', 'alpha_max') else solver)
            destino.addRow(rotulo, campo)
        self.capacidade_ativa=QCheckBox('Aplicar capacidade máxima da serraria por ano')
        self.capacidade_ativa.setToolTip('Usa as colunas numéricas nomeadas por ano (ex.: 2027) como volume de entrada na serraria. Para limites diferentes por produto ou período, cadastre linhas específicas em Restrições e solver.')
        demanda.insertRow(0,self.capacidade_ativa)
        self.faixa_demanda = QLabel()
        self.faixa_demanda.setWordWrap(True)
        demanda.addRow(self.faixa_demanda)
        self.campos['demanda'].valueChanged.connect(self._atualizar_demanda)
        self.campos['alpha_max'].valueChanged.connect(self._atualizar_demanda)
        self._atualizar_demanda()
        self.campos['alpha_max'].setToolTip(
            'Teto da variável alfa, não um desvio fixo. 500% corresponde a ALPHA_MAX = 5 no script.')
        self.volume_ha = QCheckBox('Volumes anuais por hectare')
        self.volume_ha.setToolTip('Multiplica os volumes anuais pela área do talhão.')
        self.vpl_ha = QCheckBox('Indicador do objetivo por hectare')
        self.vpl_ha.setToolTip('Multiplica o VPL pela área do talhão.')
        unidades.addRow(self.volume_ha)
        unidades.addRow(self.vpl_ha)
        self.prefixo = QLineEdit('otimizacao')
        unidades.addRow('Sufixo dos resultados', self.prefixo)
        explicacao = QLabel('Marque apenas os valores que precisam ser multiplicados pela área do talhão.')
        explicacao.setWordWrap(True)
        qss.aplicar_variante(explicacao, 'dica')
        unidades.addRow(explicacao)
        config.addStretch(1)
        painel_restricoes, form_restricoes = self._painel('Restrições adicionais',
            'Limite a soma de uma coluna nos cenários escolhidos. Para um limite anual, '
            'selecione a coluna do ano. Sem linhas cadastradas, nenhuma restrição adicional é imposta.')
        avancado.addWidget(painel_restricoes)
        self.restricoes = QTableWidget(0, 4)
        # A célula deve acomodar a fonte e o padding do controle no tema atual.
        self.restricoes.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
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
            'Volumes processados e limites anuais cadastrados.')
        resultados.addWidget(self.painel_producao)
        acesso=QHBoxLayout();self.tabelas_resultado=QComboBox();self.tabelas_resultado.setMinimumContentsLength(8)
        self.tabelas_resultado.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        acesso.addWidget(QLabel('Tabela de resultado'));acesso.addWidget(self.tabelas_resultado,1)
        abrir_dados=QPushButton('Ver tabela, Excel e gráficos');abrir_dados.clicked.connect(self._abrir_dados);acesso.addWidget(abrir_dados)
        resultados.insertLayout(0,acesso)
        self.producao = QTableWidget(0, 7)
        self.producao.setHorizontalHeaderLabels(
            ['Ano', 'Volume processado (m³)', 'Capacidade / meta (m³)', 'Mínimo (m³)', 'Máximo (m³)', 'Desvio (%)', 'Regulado'])
        self.producao.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.producao.setAlternatingRowColors(True)
        self.producao.verticalHeader().hide()
        self.producao.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.producao.setMinimumHeight(220)
        form_producao.addRow(self.producao)
        painel_etapas, form_etapas = self._painel('Convergência por etapa',
            'Confira o status, o melhor bound e o gap. Uma solução factível no limite de tempo '
            'pode ainda não ter atingido o gap solicitado.')
        resultados.insertWidget(0, painel_etapas)
        self.etapas = QTableWidget(0, 5)
        self.etapas.setHorizontalHeaderLabels(['Objetivo', 'Resultado', 'Melhor bound', 'Gap (%)', 'Status'])
        self.etapas.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.etapas.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.etapas.setMinimumHeight(130)
        form_etapas.addRow(self.etapas)
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
        ajuda = QLabel('Objetivo → decisões → restrições opcionais → solução e folgas')
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
        painel_modelo,form_modelo=self._painel('2. Objetivo e variáveis de decisão',
            'Cada linha da tabela é uma variável. Use colunas de coeficientes para representar produção, transporte, orçamento, demanda e outros modelos lineares.')
        config.insertWidget(1,self.painel_origem)
        config.insertWidget(2,painel_modelo)
        self.modelo=QComboBox();self.modelo.addItem('Modelo linear / inteiro misto configurável','configuravel');self.modelo.addItem('Regulação de produção e VPL em duas etapas (legado)','legado')
        form_modelo.addRow('Formulação',self.modelo)
        self.sentido=QComboBox();self.sentido.addItem('Maximizar','max');self.sentido.addItem('Minimizar','min');form_modelo.addRow('Objetivo',self.sentido)
        self.indicador=QComboBox();form_modelo.addRow('Coluna do indicador',self.indicador)
        self.indicador.currentTextChanged.connect(self.colunas['vpl'].setCurrentText)
        self.colunas['vpl'].currentTextChanged.connect(self.indicador.setCurrentText)
        self.unidade_objetivo=QLineEdit();self.unidade_objetivo.setPlaceholderText('R$, m³, horas, …');form_modelo.addRow('Unidade do objetivo',self.unidade_objetivo)
        self.selecao_grupo=QComboBox()
        for titulo,valor in [('Uma alternativa por grupo / talhão','exatamente_um'),('No máximo uma alternativa por grupo','no_maximo_um'),('Variáveis independentes, sem seleção por grupo','livre')]:self.selecao_grupo.addItem(titulo,valor)
        form_modelo.addRow('Regra de decisão',self.selecao_grupo)
        self.tipo_variavel=QComboBox()
        for titulo,valor in [('Binárias (0 ou 1)','B'),('Inteiras','I'),('Contínuas','C')]:self.tipo_variavel.addItem(titulo,valor)
        form_modelo.addRow('Tipo padrão',self.tipo_variavel)
        self.coluna_tipo=QComboBox();self.coluna_min=QComboBox();self.coluna_max=QComboBox()
        form_modelo.addRow('Tipos por coluna (B/I/C)',self.coluna_tipo)
        self.limite_inferior=QDoubleSpinBox();self.limite_superior=QDoubleSpinBox()
        for campo in (self.limite_inferior,self.limite_superior):campo.setRange(-1e15,1e15);campo.setDecimals(6)
        self.limite_superior.setValue(1e9)
        form_modelo.addRow('Limite inferior padrão',self.limite_inferior);form_modelo.addRow('Limite superior padrão',self.limite_superior)
        form_modelo.addRow('Limite inferior por coluna',self.coluna_min);form_modelo.addRow('Limite superior por coluna',self.coluna_max)
        self.tipo_variavel.setToolTip('Limites numéricos valem para variáveis inteiras e contínuas; binárias ficam entre 0 e 1.')
        self.selecao_grupo.currentIndexChanged.connect(self._atualizar_modo)
        self.modelo.currentIndexChanged.connect(self._atualizar_modo)
        self.capacidade_ativa.toggled.connect(self._atualizar_modo)
        self.conferencia=QTableWidget(0,6);self.conferencia.setHorizontalHeaderLabels(['Restrição','Relação','Limite','Atingido','Folga','Atendida'])
        self.conferencia.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.conferencia.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.conferencia.setMinimumHeight(180)
        painel_conferencia,form_conferencia=self._painel('Conferência das restrições','Confira os limites, valores atingidos e folgas da solução.')
        form_conferencia.addRow(self.conferencia);resultados.insertWidget(1,painel_conferencia)
        self._atualizar_modo()
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

    def _atualizar_demanda(self):
        d=self.campos['demanda'].value()
        if not hasattr(self,'modelo') or self.modelo.currentData()=='legado':
            self.faixa_demanda.setText('Regulação legada: minimizar alfa e depois maximizar VPL.\n'+f'Exemplo com α = 10%: {d*.9:,.2f} a {d*1.1:,.2f} m³/ano.')
        else:
            self.faixa_demanda.setText(f'Capacidade ativa: volume processado ≤ {d:,.2f} m³ em cada ano do horizonte.' if self.capacidade_ativa.isChecked() else 'Sem restrição automática de demanda ou capacidade. Cadastre as restrições necessárias em Dados e solver.')

    def _atualizar_modo(self,*args):
        legado=self.modelo.currentData()=='legado'
        for chave in ('alpha_max','folga','tolerancia','gap_alpha','tempo_alpha','regulacao'):
            campo=self.campos[chave];campo.setVisible(legado)
            label=campo.parentWidget().layout().labelForField(campo)
            if label:label.setVisible(legado)
        for widget in (self.sentido,self.indicador,self.unidade_objetivo,self.selecao_grupo,self.tipo_variavel,self.coluna_tipo,self.coluna_min,self.coluna_max,self.limite_inferior,self.limite_superior):widget.setEnabled(not legado)
        self.capacidade_ativa.setVisible(not legado)
        for chave in ('idade','cenario'):
            campo=self.colunas[chave];campo.setVisible(legado)
            label=campo.parentWidget().layout().labelForField(campo)
            if label:label.setVisible(legado)
        self.colunas['talhao'].setEnabled(legado or self.selecao_grupo.currentData()!='livre')
        self.painel_producao.setVisible(legado or self.capacidade_ativa.isChecked())
        self.campos['demanda'].setEnabled(legado or self.capacidade_ativa.isChecked())
        self._atualizar_demanda()

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
            self._grid_config.addWidget(self.painel_metas, 0, 0)
            self._grid_config.addWidget(self.painel_unidades, 0, 1)
            self._grid_config.addWidget(self.painel_demanda, 1, 0, 1, 2)
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
        if hasattr(self,'indicador'):
            objetivo_atual=self.colunas['vpl'].currentText()
            self.indicador.blockSignals(True)
            self.indicador.clear();self.indicador.addItems(colunas);self.indicador.setCurrentText(objetivo_atual)
            self.indicador.blockSignals(False)
            for combo in (self.coluna_tipo,self.coluna_min,self.coluna_max):
                atual=combo.currentData();combo.clear();combo.addItem('(usar padrão)',None)
                for c in colunas:combo.addItem(c,c)
                combo.setCurrentIndex(max(0,combo.findData(atual)))
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
        cfg.update(modelo=self.modelo.currentData(),sentido=self.sentido.currentData(),selecao_grupo=self.selecao_grupo.currentData(),tipo_variavel=self.tipo_variavel.currentData(),coluna_tipo=self.coluna_tipo.currentData(),coluna_min=self.coluna_min.currentData(),coluna_max=self.coluna_max.currentData(),limite_inferior=self.limite_inferior.value(),limite_superior=self.limite_superior.value(),capacidade_ativa=self.capacidade_ativa.isChecked(),unidade_objetivo=self.unidade_objetivo.text().strip())
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
                if cfg.get('modelo')=='configuravel' and chave!='vpl':continue
                if cfg[chave] not in self._esquema[cfg['tabela']]:
                    raise ValueError(f'Coluna ausente: {cfg[chave]}')
            for r in cfg.get('restricoes', []):
                if r['coluna'] not in self._esquema[cfg['tabela']]:
                    raise ValueError(f'Coluna ausente: {r["coluna"]}')
            self.tabela.setCurrentText(cfg['tabela'])
            for campo,chave,padrao in [(self.modelo,'modelo','legado'),(self.sentido,'sentido','max'),(self.selecao_grupo,'selecao_grupo','exatamente_um'),(self.tipo_variavel,'tipo_variavel','B')]:campo.setCurrentIndex(max(0,campo.findData(cfg.get(chave,padrao))))
            for combo,chave in [(self.coluna_tipo,'coluna_tipo'),(self.coluna_min,'coluna_min'),(self.coluna_max,'coluna_max')]:combo.setCurrentIndex(max(0,combo.findData(cfg.get(chave))))
            self.limite_inferior.setValue(cfg.get('limite_inferior',0));self.limite_superior.setValue(cfg.get('limite_superior',1e9))
            self.capacidade_ativa.setChecked(cfg.get('capacidade_ativa',False));self.unidade_objetivo.setText(cfg.get('unidade_objetivo',''))
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
        self.etapas.setRowCount(0)
        self.window().travar_navegacao(True)
        self._thread = _ThreadPlanejamento(self.banco.text(), solver, self._cfg(), self)
        self._thread.progresso.connect(self._progresso)
        self._thread.finished.connect(self._finalizar)
        self._thread.start()

    def _progresso(self, mensagem):
        if mensagem.startswith(('Etapa ', 'Validando ', 'Gravando ')):
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
            self.tabelas_resultado.clear();self.tabelas_resultado.addItems(resultado['tabelas'])
            escolhida=next((n for n in resultado['tabelas'] if n.startswith('cenarios_escolhidos_')),resultado['tabelas'][0])
            self.tabelas_resultado.setCurrentText(escolhida)
            resumo = resultado['resumo']
            if 'VALOR_OBJETIVO' in resumo:
                self.status.setText(f'Concluído: {resumo["OBJETIVO"]} = {resumo["VALOR_OBJETIVO"]:,.4f}; {resumo["NUM_RESTRICOES"]} restrições atendidas.')
            else:
                self.status.setText(f'Concluído: alpha {resumo["ALPHA_ENCONTRADO"]*100:.4f}% | VPL {resumo["VPL_FINAL"]:,.2f}.')
            avaliadas=resultado.get('restricoes',[]);self.conferencia.setRowCount(len(avaliadas))
            for i,r in enumerate(avaliadas):
                for j,chave in enumerate(['RESTRICAO','RELACAO','LIMITE','ATINGIDO','FOLGA','ATENDIDA']):self.conferencia.setItem(i,j,QTableWidgetItem(str(r[chave])))
            for etapa in resultado['etapas']:
                i = self.etapas.rowCount()
                self.etapas.insertRow(i)
                percentual = etapa['ETAPA'] == 1 and 'ALPHA_ENCONTRADO' in resumo
                def formatar(v):
                    return '—' if v is None else (f'{v*100:.4f}%' if percentual else f'{v:,.2f}')
                valores = [etapa['OBJETIVO'], formatar(etapa['RESULTADO']), formatar(etapa['BOUND']),
                           '—' if etapa['GAP'] is None else f'{etapa["GAP"]*100:.4f}', etapa['STATUS']]
                for j, valor in enumerate(valores):
                    self.etapas.setItem(i, j, QTableWidgetItem(str(valor)))
            self.log.appendPlainText('Tabelas criadas:\n' + '\n'.join(resultado['tabelas']))
            self.producao.setRowCount(len(resultado['producao']))
            for i, r in enumerate(resultado['producao']):
                for j, valor in enumerate([r['ANO'], f'{r["VOLUME_TAPER"]:,.2f}',
                                           f'{r["DEMANDA"]:,.2f}',
                                           '—' if r['LIMITE_MIN'] is None else f'{r["LIMITE_MIN"]:,.2f}',
                                           '—' if r['LIMITE_MAX'] is None else f'{r["LIMITE_MAX"]:,.2f}',
                                           f'{r["DESVIO_PERCENTUAL"]:.4f}', 'Sim' if r['REGULADO'] else 'Transição']):
                    self.producao.setItem(i, j, QTableWidgetItem(str(valor)))
            self.producao.resizeColumnsToContents()
        thread.deleteLater()
        self._thread = None

    def _abrir_dados(self):
        if self.rodando:return
        janela=self.window()
        if hasattr(janela,'mostrar_tela'):
            janela.mostrar_tela('explorador_dados')
            tela=janela.telas['explorador_dados']
            if self.banco.text():tela.abrir_tabela(self.banco.text(),self.tabelas_resultado.currentText() or None)

    def novo_registro(self):
        if not self.rodando:
            self.tabelas_resultado.clear()
            self.banco.clear()
            self._esquema = {}
            self.tabela.clear()
            self.log.clear()
            self.producao.setRowCount(0)
            self.etapas.setRowCount(0)

    def recarregar_lista(self):
        if not self.rodando and not self.banco.text() and projeto.projeto_aberto():
            self._usar_projeto()
