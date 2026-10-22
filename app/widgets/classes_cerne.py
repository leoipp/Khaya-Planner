"""Editor das faixas e cores de cerne, cadastrado em Sortimentos."""
import sqlite3
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QHeaderView, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from ..core import projeto, classes_tabuas
from ..core.db import conectar
from ..core.desdobro import CLASSES_MADEIRA_PADRAO


class EditorClassesCerne(QWidget):
    alterado = Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        titulo = QLabel('Classificação por cerne (?)')
        titulo.setToolTip('Percentual de cerne na área da tábua na seção de corte. Defina faixas sem lacunas ou sobreposição. '
            'Marque se cada limite está incluído. Duplo clique na cor para escolher. A regra vale para todas as dimensões de tábua do projeto.')
        layout.addWidget(titulo)
        self.classes = QTableWidget(0,6)
        self.classes.setHorizontalHeaderLabels(['Classe','Mín. %','Máx. %','Inclui mín.','Inclui máx.','Cor'])
        self.classes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.classes.setMinimumHeight(140)
        layout.addWidget(self.classes)
        barra = QHBoxLayout()
        for texto,funcao in [('Adicionar faixa',self._adicionar_classe),('Remover faixa',self._remover_classe),('Salvar classificação',self.salvar)]:
            botao = QPushButton(texto);botao.clicked.connect(funcao);barra.addWidget(botao)
        barra.addStretch();layout.addLayout(barra)
        self.status = QLabel();layout.addWidget(self.status)
        self.classes.cellDoubleClicked.connect(self._cor_classe)
        self.classes.itemChanged.connect(lambda: self.alterado.emit())
        self.alterado.connect(lambda: self.status.setText('Alterações da classificação ainda não salvas.'))
        self._definir_classes(CLASSES_MADEIRA_PADRAO)

    def recarregar(self):
        try:
            conn = conectar()
        except RuntimeError:
            self._definir_classes(CLASSES_MADEIRA_PADRAO)
        else:
            try:self._definir_classes(classes_tabuas.carregar_classificacao_cerne(conn))
            finally:conn.close()
        self.status.clear()

    def salvar(self):
        try:
            regras = self._ler_classes()
            conn = conectar()
            try:
                with conn:classes_tabuas.salvar_classificacao_cerne(conn,regras)
            finally:conn.close()
            projeto.sincronizar()
            self.status.setText('Classificação de cerne salva no projeto.')
        except (RuntimeError,ValueError,OSError,sqlite3.Error) as e:
            QMessageBox.warning(self,'Classificação de cerne',str(e))

    def _definir_classes(self, classes):
        self.classes.blockSignals(True)
        try:
            self.classes.setRowCount(0)
            for c in classes:
                self._adicionar_classe(False, c)
        finally:
            self.classes.blockSignals(False)

    def _adicionar_classe(self, checked=False, classe=None):
        c = classe or dict(nome=f'C{self.classes.rowCount()+1}', minimo=0, maximo=100,
                          inclui_minimo=True, inclui_maximo=True, cor='#6c8cdb')
        row = self.classes.rowCount()
        self.classes.insertRow(row)
        for col, chave in enumerate(('nome', 'minimo', 'maximo', 'inclui_minimo', 'inclui_maximo', 'cor')):
            item = QTableWidgetItem(str(c[chave]))
            if col in (3, 4):
                item.setText('')
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if c[chave] else Qt.CheckState.Unchecked)
            if col == 5:
                item.setBackground(QColor(c['cor']))
                item.setForeground(QColor('#ffffff' if QColor(c['cor']).lightness() < 140 else '#172b3a'))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.classes.setItem(row, col, item)
        if classe is None:
            self.alterado.emit()

    def _remover_classe(self):
        row = self.classes.currentRow()
        if row >= 0:
            self.classes.removeRow(row)
            self.alterado.emit()

    def _cor_classe(self, row, col):
        if col != 5:
            return
        item = self.classes.item(row, col)
        cor = QColorDialog.getColor(QColor(item.text()), self, 'Cor da classe')
        if cor.isValid():
            item.setText(cor.name())
            item.setBackground(cor)
            item.setForeground(QColor('#ffffff' if cor.lightness() < 140 else '#172b3a'))

    def _ler_classes(self):
        classes = []
        for row in range(self.classes.rowCount()):
            try:
                c = dict(nome=self.classes.item(row,0).text().strip(),
                         minimo=float(self.classes.item(row,1).text().replace(',', '.')),
                         maximo=float(self.classes.item(row,2).text().replace(',', '.')),
                         inclui_minimo=self.classes.item(row,3).checkState() == Qt.CheckState.Checked,
                         inclui_maximo=self.classes.item(row,4).checkState() == Qt.CheckState.Checked,
                         cor=self.classes.item(row,5).text())
            except (AttributeError, ValueError):
                raise ValueError('Preencha os nomes e limites das classes com valores válidos.')
            if not QColor(c['cor']).isValid():
                raise ValueError('Cor inválida na classificação.')
            classes.append(c)
        return classes
