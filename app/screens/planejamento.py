"""Formulário de planejamento florestal sem código, com execução em QThread."""
import json

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import planejamento, preferencias, projeto
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
        raiz = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        raiz.addWidget(scroll)
        corpo = QWidget()
        scroll.setWidget(corpo)
        layout = QVBoxLayout(corpo)
        titulo = QLabel("Otimização de Planejamento")
        layout.addWidget(titulo)
        explicacao = QLabel("Escolhe um cenário por talhão. Primeiro minimiza o maior desvio anual da demanda; "
                            "depois maximiza o VPL dentro da faixa encontrada.")
        explicacao.setWordWrap(True)
        layout.addWidget(explicacao)
        self.formulario = QWidget()
        layout.addWidget(self.formulario)
        form = QFormLayout(self.formulario)
        self.banco = QLineEdit()
        self.banco.setReadOnly(True)
        linha = QHBoxLayout()
        linha.addWidget(self.banco, 1)
        escolher = QPushButton("Escolher SQLite…")
        escolher.clicked.connect(self._escolher_banco)
        linha.addWidget(escolher)
        atual = QPushButton("Usar projeto aberto")
        atual.clicked.connect(self._usar_projeto)
        linha.addWidget(atual)
        form.addRow("Banco de origem e resultados", linha)
        self.tabela = QComboBox()
        self.tabela.currentTextChanged.connect(self._carregar_colunas)
        form.addRow("Tabela de cenários", self.tabela)
        self.colunas = {}
        for chave, rotulo in [('talhao', 'Talhão'), ('cenario', 'Cenário'), ('area', 'Área'),
                              ('idade', 'Idade'), ('vpl', 'VPL')]:
            combo = QComboBox()
            self.colunas[chave] = combo
            form.addRow(f'Coluna: {rotulo}', combo)
        self.campos = {}
        for chave, rotulo, padrao, minimo, maximo, inteiro in [
            ('inicio', 'Ano inicial', 2026, 1900, 2200, True),
            ('fim', 'Ano final', 2052, 1900, 2200, True),
            ('regulacao', 'Primeiro ano regulado', 2027, 1900, 2200, True),
            ('demanda', 'Demanda anual (m³)', 30000, 0.000001, 1e12, False),
            ('alpha_max', 'Desvio máximo permitido (%)', 500, 0, 10000, False),
            ('folga', 'Folga da etapa 2 (pontos percentuais)', 0, 0, 10000, False),
            ('tolerancia', 'Tolerância numérica (pontos percentuais)', .001, 0, 100, False),
            ('gap_alpha', 'Gap da etapa 1 (%)', .01, 0, 100, False),
            ('gap_vpl', 'Gap da etapa 2 (%)', .1, 0, 100, False),
            ('tempo_alpha', 'Tempo limite da etapa 1 (s)', 1000, 1, 86400, True),
            ('tempo_vpl', 'Tempo limite da etapa 2 (s)', 1000, 1, 86400, True),
            ('threads', 'Threads (0 = automático)', 0, 0, 256, True),
        ]:
            campo = QSpinBox() if inteiro else QDoubleSpinBox()
            if not inteiro:
                campo.setDecimals(6)
            campo.setRange(minimo, maximo)
            campo.setValue(padrao)
            self.campos[chave] = campo
            form.addRow(rotulo, campo)
        self.volume_ha = QCheckBox("Multiplicar os volumes anuais pela área")
        self.vpl_ha = QCheckBox("Multiplicar o VPL pela área")
        form.addRow("Volumes informados por hectare", self.volume_ha)
        form.addRow("VPL informado por hectare", self.vpl_ha)
        self.prefixo = QLineEdit('vpl_taper_2etapas')
        form.addRow("Sufixo das tabelas de resultado", self.prefixo)
        dica = QLabel("Restrições adicionais: limite a soma da coluna nos cenários escolhidos. "
                      "Para um limite anual, selecione a coluna do ano. Essas restrições valem nas duas etapas.")
        dica.setWordWrap(True)
        form.addRow(dica)
        self.restricoes = QTableWidget(0, 4)
        self.restricoes.setHorizontalHeaderLabels(['Coluna', 'Relação', 'Limite', 'Por hectare'])
        self.restricoes.setMinimumHeight(140)
        self.restricoes.horizontalHeader().setStretchLastSection(True)
        form.addRow(self.restricoes)
        acoes = QHBoxLayout()
        adicionar = QPushButton("Adicionar restrição")
        adicionar.clicked.connect(self._adicionar_restricao)
        acoes.addWidget(adicionar)
        remover = QPushButton("Remover selecionada")
        remover.clicked.connect(lambda: self.restricoes.removeRow(self.restricoes.currentRow())
                                if self.restricoes.currentRow() >= 0 else None)
        acoes.addWidget(remover)
        form.addRow(acoes)
        parametros = QHBoxLayout()
        salvar = QPushButton("Salvar modelo…")
        salvar.clicked.connect(self._salvar_modelo)
        carregar = QPushButton("Carregar modelo…")
        carregar.clicked.connect(self._carregar_modelo)
        parametros.addWidget(salvar)
        parametros.addWidget(carregar)
        form.addRow(parametros)
        self.executar = QPushButton("Otimizar em duas etapas")
        self.executar.clicked.connect(self._executar)
        layout.addWidget(self.executar)
        self.status = QLabel("Configure o executável CPLEX em Configurações.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.producao = QTableWidget(0, 4)
        self.producao.setHorizontalHeaderLabels(['Ano', 'Volume taper (m³)', 'Desvio (%)', 'Regulado'])
        layout.addWidget(self.producao)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(10000)
        layout.addWidget(self.log)
        self.recarregar_lista()

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
