# -*- coding: utf-8 -*-
"""Tela de junção, agregação, pivô e exportação dos cenários Parquet."""
import json

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import projeto, resumos_cenarios
from ..core.db import conectar, conectar_caminho
from ..theme import icones, qss
from ..widgets.cartao import Cartao
from ..widgets.tabela import emoldurar_tabela
from .base import TelaBase


class _ThreadExportarResumo(QThread):
    progresso = Signal(int, int, str)
    concluido = Signal(dict)
    falhou = Signal(str)

    def __init__(self, origem, destino, tabela, cfg, parent=None):
        super().__init__(parent)
        self.origem, self.destino, self.tabela, self.cfg = origem, destino, tabela, cfg

    def run(self):
        conn = None
        try:
            conn = conectar_caminho(self.origem)
            resultado = resumos_cenarios.exportar_sqlite(
                conn, self.destino, self.tabela, self.cfg,
                lambda a, b, c: self.progresso.emit(a, b, c))
            self.concluido.emit(resultado)
        except Exception as e:
            self.falhou.emit(str(e))
        finally:
            if conn is not None:
                conn.close()


class TelaResumosCenarios(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._colunas = {}
        self._configs = {}
        self._thread = None
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(8, 8, 8, 8)
        titulo = QLabel("Resumos de cenários")
        qss.aplicar_variante(titulo, "titulo")
        raiz.addWidget(titulo)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._montar_configuracao())
        splitter.addWidget(self._montar_previa())
        splitter.setSizes([480, 760])
        raiz.addWidget(splitter, 1)
        self.recarregar_lista()

    def _montar_configuracao(self):
        # O formulário é deliberadamente mais alto que muitas janelas. Sem
        # rolagem, o QSplitter comprime todos os cards e suas tabelas até os
        # campos ficarem praticamente ilegíveis.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(430)
        painel = QWidget()
        painel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(painel)
        lay.setContentsMargins(0, 0, 10, 10)
        lay.setSpacing(14)
        cartao = Cartao("Fontes e junção"); lay.addWidget(cartao)
        form = QFormLayout(cartao.corpo)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        self.combo_a = QComboBox(); self.combo_b = QComboBox()
        self.combo_b.addItem("(sem segunda fonte)", None)
        for chave, rotulo in resumos_cenarios.FONTES.items():
            self.combo_a.addItem(rotulo, chave); self.combo_b.addItem(rotulo, chave)
        self.combo_join = QComboBox()
        for rotulo, valor in (("Esquerda (left)", "left"), ("Somente correspondentes (inner)", "inner"),
                              ("Direita (right)", "right"), ("Todos (outer)", "outer")):
            self.combo_join.addItem(rotulo, valor)
        self.chaves_a = QLineEdit(); self.chaves_b = QLineEdit()
        self.chaves_a.setPlaceholderText("Ex.: id, TALHÃO")
        self.chaves_b.setPlaceholderText("Ex.: populacao_id, TALHÃO")
        form.addRow("Fonte A", self.combo_a); form.addRow("Fonte B", self.combo_b)
        form.addRow("Tipo de junção", self.combo_join)
        form.addRow("Chaves A (separadas por vírgula)", self.chaves_a)
        form.addRow("Chaves B (separadas por vírgula)", self.chaves_b)
        self.combo_a.currentIndexChanged.connect(self._atualizar_colunas)
        self.combo_b.currentIndexChanged.connect(self._atualizar_colunas)

        cartao_grupo = Cartao("Agrupamento"); lay.addWidget(cartao_grupo)
        gl = QVBoxLayout(cartao_grupo.corpo)
        gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(8)
        self.lista_grupos = QListWidget(); self.lista_grupos.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.lista_grupos.setMinimumHeight(190)
        gl.addWidget(self.lista_grupos)

        cartao_filtro = Cartao("Filtro por chaves (opcional)"); lay.addWidget(cartao_filtro)
        ff = QFormLayout(cartao_filtro.corpo)
        ff.setVerticalSpacing(10); ff.setHorizontalSpacing(12)
        self.filtro_colunas = QLineEdit()
        self.filtro_colunas.setPlaceholderText("Ex.: populacao.cenario, populacao.TALHÃO")
        self.filtro_valores = QPlainTextEdit()
        self.filtro_valores.setPlaceholderText(
            "Uma chave por linha. Ex.:\nCenário 1 + Talhão 01\nCenário 2 + Talhão 08")
        self.filtro_valores.setMinimumHeight(120)
        ff.addRow("Colunas da chave", self.filtro_colunas)
        ff.addRow("Chaves permitidas", self.filtro_valores)

        cartao_metricas = Cartao("Indicadores"); lay.addWidget(cartao_metricas)
        ml = QVBoxLayout(cartao_metricas.corpo)
        ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(10)
        self.tabela_metricas = QTableWidget(0, 4)
        self.tabela_metricas.setMinimumHeight(190)
        self.tabela_metricas.verticalHeader().setDefaultSectionSize(34)
        self.tabela_metricas.setHorizontalHeaderLabels(
            ["Coluna", "Agregação", "Nome de saída", "Arred."])
        cabecalho = self.tabela_metricas.horizontalHeader()
        cabecalho.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        cabecalho.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        cabecalho.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        ml.addWidget(emoldurar_tabela(self.tabela_metricas))
        botoes = QHBoxLayout(); add = QPushButton("Adicionar"); rem = QPushButton("Remover")
        add.clicked.connect(self._adicionar_metrica); rem.clicked.connect(self._remover_metrica)
        botoes.addWidget(add); botoes.addWidget(rem); botoes.addStretch(); ml.addLayout(botoes)

        cartao_pivo = Cartao("Pivô opcional"); lay.addWidget(cartao_pivo)
        pf = QFormLayout(cartao_pivo.corpo)
        pf.setVerticalSpacing(10); pf.setHorizontalSpacing(12)
        self.combo_pivo_coluna = QComboBox(); self.combo_pivo_valor = QComboBox(); self.combo_pivo_agg = QComboBox()
        for rotulo, funcao in resumos_cenarios.AGREGACOES.items(): self.combo_pivo_agg.addItem(rotulo, funcao)
        pf.addRow("Coluna que vira cabeçalho", self.combo_pivo_coluna)
        pf.addRow("Coluna de valores", self.combo_pivo_valor); pf.addRow("Agregação", self.combo_pivo_agg)

        cartao_salvar = Cartao("Configuração e destino"); lay.addWidget(cartao_salvar)
        sf = QFormLayout(cartao_salvar.corpo)
        sf.setVerticalSpacing(10); sf.setHorizontalSpacing(12)
        self.combo_configs = QComboBox(); self.nome_config = QLineEdit(); self.nome_tabela = QLineEdit("resumo_cenario_talhao")
        sf.addRow("Configuração salva", self.combo_configs); sf.addRow("Nome da configuração", self.nome_config)
        sf.addRow("Tabela no banco", self.nome_tabela)
        linha = QHBoxLayout(); salvar = QPushButton("Salvar configuração"); carregar = QPushButton("Carregar")
        salvar.clicked.connect(self._salvar); carregar.clicked.connect(self._carregar_config)
        linha.addWidget(salvar); linha.addWidget(carregar); sf.addRow(linha)
        acoes = QHBoxLayout(); previa = QPushButton("Gerar prévia"); exportar = QPushButton("Exportar SQLite...")
        icones.aplicar_icone(previa, "atualizar"); icones.aplicar_icone(exportar, "exportar")
        previa.clicked.connect(self._gerar_previa); exportar.clicked.connect(self._exportar)
        acoes.addWidget(previa); acoes.addWidget(exportar); lay.addLayout(acoes); lay.addStretch()
        scroll.setWidget(painel)
        return scroll

    def _montar_previa(self):
        painel = QWidget(); lay = QVBoxLayout(painel); lay.setContentsMargins(6, 0, 0, 0)
        self.label_status = QLabel("Configure o resumo e gere uma prévia."); lay.addWidget(self.label_status)
        self.tabela_previa = QTableWidget(); self.tabela_previa.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(emoldurar_tabela(self.tabela_previa), 1); return painel

    def recarregar_lista(self):
        try: conn = conectar()
        except RuntimeError:
            self._colunas = {}; self._atualizar_colunas(); return
        try:
            self._colunas = {f: resumos_cenarios.colunas_fonte(conn, f) for f in resumos_cenarios.FONTES}
            self._configs = {row[1]: json.loads(row[2]) for row in resumos_cenarios.listar_configuracoes(conn)}
        finally: conn.close()
        self.combo_configs.clear(); self.combo_configs.addItems(self._configs)
        self._atualizar_colunas()

    def novo_registro(self):
        self.tabela_previa.clear(); self.label_status.setText("Configure o resumo e gere uma prévia.")

    def _todas_colunas(self):
        fontes = [self.combo_a.currentData(), self.combo_b.currentData()]
        return [f"{f}.{c}" for f in fontes if f for c in self._colunas.get(f, [])]

    def _atualizar_colunas(self):
        atuais = {self.lista_grupos.item(i).text(): self.lista_grupos.item(i).checkState()
                  for i in range(self.lista_grupos.count())}
        self.lista_grupos.clear()
        colunas = self._todas_colunas()
        fonte_a = self.combo_a.currentData()
        for coluna in colunas:
            item = QListWidgetItem(coluna); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            padrao = coluna == f"{fonte_a}.cenario" or coluna.upper() == f"{fonte_a}.TALHÃO".upper()
            item.setCheckState(atuais.get(coluna, Qt.CheckState.Checked if padrao else Qt.CheckState.Unchecked))
            self.lista_grupos.addItem(item)
        for combo in (self.combo_pivo_coluna, self.combo_pivo_valor):
            atual = combo.currentData(); combo.clear(); combo.addItem("(nenhum)", None)
            for c in colunas: combo.addItem(c, c)
            idx = combo.findData(atual); combo.setCurrentIndex(max(idx, 0))
        for linha in range(self.tabela_metricas.rowCount()):
            combo = self.tabela_metricas.cellWidget(linha, 0); atual = combo.currentData(); combo.clear()
            for c in colunas: combo.addItem(c, c)
            combo.setCurrentIndex(max(combo.findData(atual), 0))

    def _adicionar_metrica(self, dados=None):
        linha = self.tabela_metricas.rowCount(); self.tabela_metricas.insertRow(linha)
        coluna = QComboBox(); agg = QComboBox(); alias = QLineEdit(); arred = QComboBox()
        for c in self._todas_colunas(): coluna.addItem(c, c)
        for rotulo, funcao in resumos_cenarios.AGREGACOES.items(): agg.addItem(rotulo, funcao)
        arred.addItem("Sem arredondar", None)
        for casas in range(7): arred.addItem(str(casas), casas)
        self.tabela_metricas.setCellWidget(linha, 0, coluna); self.tabela_metricas.setCellWidget(linha, 1, agg)
        self.tabela_metricas.setCellWidget(linha, 2, alias)
        self.tabela_metricas.setCellWidget(linha, 3, arred)
        if dados:
            coluna.setCurrentIndex(max(coluna.findData(dados["coluna"]), 0))
            agg.setCurrentIndex(max(agg.findData(dados["agregacao"]), 0)); alias.setText(dados.get("alias", ""))
            arred.setCurrentIndex(max(arred.findData(dados.get("arredondar")), 0))

    def _remover_metrica(self):
        linhas = sorted({i.row() for i in self.tabela_metricas.selectedIndexes()}, reverse=True)
        for linha in linhas: self.tabela_metricas.removeRow(linha)

    @staticmethod
    def _lista(texto): return [x.strip() for x in texto.split(",") if x.strip()]

    @staticmethod
    def _linhas_chaves(texto):
        linhas = []
        for linha in texto.splitlines():
            linha = linha.strip()
            if not linha: continue
            separador = "\t" if "\t" in linha else ";" if ";" in linha else "+" if "+" in linha else ","
            linhas.append([valor.strip() for valor in linha.split(separador)])
        return linhas

    def _cfg(self):
        metricas=[]
        for i in range(self.tabela_metricas.rowCount()):
            metricas.append({"coluna":self.tabela_metricas.cellWidget(i,0).currentData(),
                             "agregacao":self.tabela_metricas.cellWidget(i,1).currentData(),
                             "alias":self.tabela_metricas.cellWidget(i,2).text().strip(),
                             "arredondar":self.tabela_metricas.cellWidget(i,3).currentData()})
        grupos=[self.lista_grupos.item(i).text() for i in range(self.lista_grupos.count())
                if self.lista_grupos.item(i).checkState()==Qt.CheckState.Checked]
        pivo={"coluna":self.combo_pivo_coluna.currentData(),"valor":self.combo_pivo_valor.currentData(),
              "agregacao":self.combo_pivo_agg.currentData(),"preencher":0}
        filtro={"colunas":self._lista(self.filtro_colunas.text()),
                "valores":self._linhas_chaves(self.filtro_valores.toPlainText())}
        return {"fonte_a":self.combo_a.currentData(),"fonte_b":self.combo_b.currentData(),
                "tipo_join":self.combo_join.currentData(),"chaves_a":self._lista(self.chaves_a.text()),
                "chaves_b":self._lista(self.chaves_b.text()),"grupos":grupos,"metricas":metricas,
                "pivo":pivo,"filtro_chaves":filtro}

    def _salvar(self):
        nome=self.nome_config.text().strip()
        if not nome: QMessageBox.warning(self,"Resumos","Informe um nome para a configuração."); return
        try:
            conn=conectar(); resumos_cenarios.salvar_configuracao(conn,nome,self._cfg()); conn.close(); projeto.sincronizar()
            self.recarregar_lista()
        except Exception as e: QMessageBox.critical(self,"Resumos",str(e))

    def _carregar_config(self):
        cfg=self._configs.get(self.combo_configs.currentText())
        if not cfg: return
        self.combo_a.setCurrentIndex(max(self.combo_a.findData(cfg.get("fonte_a")),0))
        self.combo_b.setCurrentIndex(max(self.combo_b.findData(cfg.get("fonte_b")),0)); self._atualizar_colunas()
        self.combo_join.setCurrentIndex(max(self.combo_join.findData(cfg.get("tipo_join")),0))
        self.chaves_a.setText(", ".join(cfg.get("chaves_a",[]))); self.chaves_b.setText(", ".join(cfg.get("chaves_b",[])))
        filtro=cfg.get("filtro_chaves",{})
        self.filtro_colunas.setText(", ".join(filtro.get("colunas",[])))
        self.filtro_valores.setPlainText("\n".join(" + ".join(map(str, linha)) for linha in filtro.get("valores",[])))
        grupos=set(cfg.get("grupos",[]))
        for i in range(self.lista_grupos.count()): self.lista_grupos.item(i).setCheckState(Qt.CheckState.Checked if self.lista_grupos.item(i).text() in grupos else Qt.CheckState.Unchecked)
        self.tabela_metricas.setRowCount(0)
        for m in cfg.get("metricas",[]): self._adicionar_metrica(m)
        p=cfg.get("pivo",{}); self.combo_pivo_coluna.setCurrentIndex(max(self.combo_pivo_coluna.findData(p.get("coluna")),0)); self.combo_pivo_valor.setCurrentIndex(max(self.combo_pivo_valor.findData(p.get("valor")),0)); self.combo_pivo_agg.setCurrentIndex(max(self.combo_pivo_agg.findData(p.get("agregacao")),0)); self.nome_config.setText(self.combo_configs.currentText())

    def _gerar_previa(self):
        try:
            conn=conectar(); df=resumos_cenarios.processar(conn,self._cfg(),limite_cenarios=2); conn.close()
        except Exception as e: QMessageBox.critical(self,"Prévia",str(e)); return
        amostra=df.head(100); self.tabela_previa.setRowCount(len(amostra)); self.tabela_previa.setColumnCount(len(amostra.columns)); self.tabela_previa.setHorizontalHeaderLabels(list(map(str,amostra.columns)))
        for i,linha in enumerate(amostra.itertuples(index=False,name=None)):
            for j,v in enumerate(linha): self.tabela_previa.setItem(i,j,QTableWidgetItem("" if v is None else str(v)))
        self.label_status.setText(f"Prévia: {len(df)} linha(s) nos primeiros 2 cenários; mostrando até 100.")

    def _exportar(self):
        caminho,_=QFileDialog.getSaveFileName(self,"Exportar resumo","","SQLite (*.sqlite *.db)")
        if not caminho: return
        self.label_status.setText("Exportando...")
        self._thread=_ThreadExportarResumo(str(projeto.caminho_trabalho()),caminho,self.nome_tabela.text().strip(),self._cfg(),self)
        self._thread.progresso.connect(lambda a,b,n:self.label_status.setText(f"Exportando {a}/{b}: {n}"))
        self._thread.concluido.connect(self._fim_exportacao); self._thread.falhou.connect(lambda e: QMessageBox.critical(self,"Exportação",e)); self._thread.start()

    def _fim_exportacao(self,r):
        self.label_status.setText(f"Concluído: {r['linhas']} linhas em {r['tabela']}.")
        QMessageBox.information(self,"Exportação",self.label_status.text())
