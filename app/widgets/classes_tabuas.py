"""Tabela e formulário de classes de tábua em Sortimentos."""
import sqlite3
from PySide6.QtWidgets import (QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from ..core import projeto, classes_tabuas
from ..core.db import conectar
from ..theme import qss
from .layout_fluxo import GradeCampos
from .classes_cerne import EditorClassesCerne


class CadastroClassesTabuas(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.id_atual = None
        self._dados = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.tabela = QTableWidget(0,6)
        self.tabela.setHorizontalHeaderLabels(['Classe de tábua','Comprimento do torete (m)','Espessura (cm)','Largura (cm)','Preço (R$/m³)','Rendimento (%)'])
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabela.setMinimumHeight(130)
        layout.addWidget(self.tabela,1)
        grade = GradeCampos(espacamento=12)
        layout.addLayout(grade)
        self.nome = QLineEdit()
        self.campos = {}
        for rotulo,campo in [('Classe de tábua',self.nome)]:
            coluna = QVBoxLayout();coluna.addWidget(QLabel(rotulo));coluna.addWidget(campo);grade.addLayout(coluna,1)
        for chave,rotulo,inicio,maximo in [('comprimento','Comprimento do torete (m)',4,100),
                ('espessura','Espessura (cm)',2.5,100),('largura','Largura (cm)',10,300),('preco','Preço (R$/m³)',0,1e9),('rendimento','Rendimento (%)',35,100)]:
            campo = QDoubleSpinBox();campo.setDecimals(2)
            campo.setRange(0 if chave in ('preco','rendimento') else .01,maximo);campo.setValue(inicio)
            self.campos[chave] = campo
            coluna = QVBoxLayout();coluna.addWidget(QLabel(rotulo));coluna.addWidget(campo);grade.addLayout(coluna,1)
        self.campos['rendimento'].setToolTip('Rendimento de referência usado no Desdobro para limitar a previsão de tábuas.')
        self.campos['comprimento'].setToolTip('Comprimento da tábua e do torete usado no Desdobro.')
        barra = QHBoxLayout()
        for texto,funcao in [('Adicionar',self.adicionar),('Salvar',self.salvar),('Excluir',self.excluir),('Limpar',self.limpar)]:
            botao = QPushButton(texto);botao.clicked.connect(funcao);barra.addWidget(botao)
            if texto == 'Excluir':qss.aplicar_variante(botao,'perigo')
        barra.addStretch();layout.addLayout(barra)
        self.status = QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.tabela.itemSelectionChanged.connect(self._selecionar)
        self.classificacao_cerne = EditorClassesCerne()
        layout.addWidget(self.classificacao_cerne)

    def recarregar(self, recarregar_cerne=True):
        if recarregar_cerne:
            self.classificacao_cerne.recarregar()
        try:
            conn = conectar()
        except RuntimeError:
            self._dados = []
        else:
            try:self._dados = classes_tabuas.listar(conn)
            finally:conn.close()
        self.tabela.setRowCount(len(self._dados))
        for row,dados in enumerate(self._dados):
            valores = [dados['nome']] + [f"{dados[k]:.2f}".replace('.',',') for k in ('comprimento','espessura','largura','preco','rendimento')]
            for col,valor in enumerate(valores):self.tabela.setItem(row,col,QTableWidgetItem(valor))

    def limpar(self):
        self.id_atual = None;self.tabela.clearSelection();self.nome.clear()
        for chave,valor in dict(comprimento=4,espessura=2.5,largura=10,preco=0,rendimento=35).items():self.campos[chave].setValue(valor)
        self.status.clear()

    def _selecionar(self):
        row = self.tabela.currentRow()
        if not self.tabela.selectedItems() or not 0 <= row < len(self._dados):return
        dados = self._dados[row];self.id_atual = dados['id'];self.nome.setText(dados['nome'])
        for chave,campo in self.campos.items():campo.setValue(dados[chave])

    def _gravar(self,id_classe):
        try:
            conn = conectar()
            try:
                with conn:
                    id_classe = classes_tabuas.salvar(conn,self.nome.text(),**{k:c.value() for k,c in self.campos.items()},id_classe=id_classe)
            finally:conn.close()
            projeto.sincronizar();self.recarregar(recarregar_cerne=False);self.id_atual = id_classe
            for row,dados in enumerate(self._dados):
                if dados['id'] == id_classe:self.tabela.selectRow(row);break
            self.status.setText('Classe de tábua salva no projeto.')
        except (RuntimeError,ValueError,sqlite3.Error,OSError) as e:
            QMessageBox.warning(self,'Classes de tábua',str(e))

    def adicionar(self):self._gravar(None)

    def salvar(self):
        if self.id_atual is None:
            QMessageBox.warning(self,'Classes de tábua','Selecione uma classe para editar ou use Adicionar.');return
        self._gravar(self.id_atual)

    def excluir(self):
        if self.id_atual is None:return
        if QMessageBox.question(self,'Classes de tábua','Excluir a classe selecionada?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:return
        try:
            conn = conectar()
            try:
                with conn:conn.execute('DELETE FROM classes_tabuas WHERE id=?',(self.id_atual,))
            finally:conn.close()
            projeto.sincronizar();self.limpar();self.recarregar(recarregar_cerne=False)
        except (RuntimeError,sqlite3.Error,OSError) as e:QMessageBox.warning(self,'Classes de tábua',str(e))
