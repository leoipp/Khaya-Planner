"""Configuração das classes calculadas pelo nó Tábuas."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView,QComboBox,QDialog,QDialogButtonBox,
    QDoubleSpinBox,QFormLayout,QHeaderView,QLabel,QLineEdit,QMessageBox,QTableWidget,
    QTableWidgetItem,QVBoxLayout)


class DialogoNoTabuas(QDialog):
    def __init__(self,no,cadastro,regras,parent=None):
        super().__init__(parent)
        self.setWindowTitle('Tábuas — classes e saídas')
        self.resize(940,560)
        self.cadastro=cadastro
        self.configuracao=None
        layout=QVBoxLayout(self)
        nota=QLabel('Produção conjunta por tora — dimensões e cerne obrigatórios (?)')
        nota.setWordWrap(True)
        nota.setToolTip('Cada tábua é atribuída a uma única classe que atenda às dimensões e à faixa de cerne. '
            'O plano compara encaixes por receita, sem duplicar peças ou volume. '
            'O rendimento limita a produção conjunta e as quantidades são inteiras. '
            'Comprimentos diferentes competem pelo trecho do fuste. É uma heurística de corte, não um ótimo global. '
            + ('No V4, ligue volume aproveitável e árvores por diâmetro na mesma base de área. '
             'As saídas são quantidade estimada, volume e receita por classe e diâmetro; a quantidade pode ser fracionária. '
             if no.get('versao_producao')==4 else 'As saídas são por árvore e diâmetro. ')
            + 'Receita bruta = volume da classe × preço.')
        layout.addWidget(nota)
        self.tabela=QTableWidget(len(cadastro),5)
        self.tabela.setHorizontalHeaderLabels(['Calcular / classe','Dimensões','Rendimento (%)','Preço (R$/m³)','Filtro de cerne'])
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabela.horizontalHeader().setStretchLastSection(True)
        self.tabela.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        selecionadas={c['id']:c for c in no.get('classes_tabuas',[])}
        nomes={c['nome'] for c in regras}
        for row,c in enumerate(cadastro):
            item=QTableWidgetItem(c['nome'])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c['id'] in selecionadas else Qt.CheckState.Unchecked)
            self.tabela.setItem(row,0,item)
            for col,valor in enumerate([f"{c['comprimento']:g} m · {c['espessura']:g} × {c['largura']:g} cm",f"{c['rendimento']:g}",f"{c['preco']:.2f}"],1):
                self.tabela.setItem(row,col,QTableWidgetItem(valor))
            combo=QComboBox();combo.addItem('Selecione a classe de cerne',None)
            for regra in regras:combo.addItem(regra['nome'],regra['nome'])
            filtro=selecionadas.get(c['id'],{}).get('classe_cerne',c['nome'] if c['nome'] in nomes else None)
            if c['nome'] in nomes:
                filtro=c['nome']
                combo.setEnabled(False)
            combo.setCurrentIndex(max(0,combo.findData(filtro)))
            self.tabela.setCellWidget(row,4,combo)
        layout.addWidget(self.tabela,1)
        form=QFormLayout()
        self.prefixo=QLineEdit(no.get('nome_saida') or 'tabuas')
        self.serra=QDoubleSpinBox();self.serra.setRange(0,20);self.serra.setValue(no.get('serra_mm',3))
        self.toco=QDoubleSpinBox();self.toco.setRange(0,5);self.toco.setValue(no.get('toco',0))
        form.addRow('Prefixo das colunas',self.prefixo);form.addRow('Serra (mm)',self.serra);form.addRow('Toco (m)',self.toco)
        layout.addLayout(form)
        botoes=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        botoes.accepted.connect(self._confirmar);botoes.rejected.connect(self.reject);layout.addWidget(botoes)

    def coletar(self):
        classes=[]
        for row,c in enumerate(self.cadastro):
            if self.tabela.item(row,0).checkState()==Qt.CheckState.Checked:
                cerne=self.tabela.cellWidget(row,4).currentData()
                if cerne is None:raise ValueError(f"Selecione a classe de cerne de {c['nome']}.")
                classes.append(dict(id=c['id'],nome=c['nome'],classe_cerne=cerne))
        if not classes:raise ValueError('Selecione ao menos uma classe de tábua.')
        if not self.prefixo.text().strip():raise ValueError('Informe o prefixo das saídas.')
        return dict(modo_producao='conjunto',classes_tabuas=classes,nome_saida=self.prefixo.text().strip(),serra_mm=self.serra.value(),toco=self.toco.value())

    def _confirmar(self):
        try:self.configuracao=self.coletar()
        except ValueError as e:
            QMessageBox.warning(self,'Tábuas',str(e));return
        self.accept()
