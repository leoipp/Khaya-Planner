"""Mapeamento de entradas e combinações de produto de um modelo de taper."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core.toragem import PAPEIS_PRODUTO, validar_configuracao_produtos


class DialogoProdutosTaper(QDialog):
    def __init__(self, nome_modelo, variaveis, configuracao, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Variáveis e produtos do taper')
        self.resize(850, 650)
        self.variaveis = list(variaveis)
        self.configuracao = None
        self._dummies = []
        layout = QVBoxLayout(self)
        titulo = QLabel(f'Modelo: {nome_modelo}')
        titulo.setWordWrap(True)
        layout.addWidget(titulo)
        dica_variaveis = ('Indique a função de cada variável do modelo. As variáveis dummy identificam '
            'o produto pela combinação de valores cadastrada abaixo. Os nomes e a quantidade de dummies são livres.')
        ajuda_variaveis = QLabel('Variáveis ⓘ')
        ajuda_variaveis.setToolTip(dica_variaveis)
        layout.addWidget(ajuda_variaveis)
        self.mapeamento = QTableWidget(len(variaveis), 2)
        self.mapeamento.setToolTip(dica_variaveis)
        # A célula deve acomodar a fonte e o padding do controle no tema atual.
        self.mapeamento.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.mapeamento.setHorizontalHeaderLabels(['Variável cadastrada', 'Função na toragem'])
        self.mapeamento.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.mapeamento.setMaximumHeight(240)
        self.funcoes = {}
        for row, nome in enumerate(variaveis):
            item = QTableWidgetItem(nome)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.mapeamento.setItem(row, 0, item)
            combo = QComboBox()
            combo.setToolTip(dica_variaveis)
            for texto, valor in [('DAP (cm)','dap'),('Altura da seção (m)','h'),('Altura total (m)','H'),('Dummy do produto','dummy')]:
                combo.addItem(texto, valor)
            geo = configuracao.get('geometricas', [])
            papel = ('dap','h','H')[geo.index(nome)] if nome in geo else 'dummy'
            combo.setCurrentIndex(combo.findData(papel))
            self.mapeamento.setCellWidget(row, 1, combo)
            self.funcoes[nome] = combo
        layout.addWidget(self.mapeamento)
        self.info_dummies = QLabel()
        layout.addWidget(self.info_dummies)
        self._dica_produtos = ('Cadastre uma linha por produto e preencha os valores de todas as dummies. '
            'O papel permite desenhar casca/alburno/cerne e usar a seção sem casca no desdobro. '
            'Use Outro produto para perfis adicionais. Não é necessário cadastrar os três papéis.')
        ajuda_produtos = QLabel('Produtos ⓘ')
        ajuda_produtos.setToolTip(self._dica_produtos)
        layout.addWidget(ajuda_produtos)
        self.produtos = QTableWidget(0, 2)
        self.produtos.setToolTip(self._dica_produtos)
        # A célula deve acomodar a fonte e o padding do controle no tema atual.
        self.produtos.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.produtos.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.produtos, 1)
        botoes = QHBoxLayout()
        adicionar = QPushButton('Adicionar produto')
        remover = QPushButton('Remover produto')
        adicionar.clicked.connect(lambda: self._adicionar_produto())
        remover.clicked.connect(self._remover_produto)
        botoes.addWidget(adicionar)
        botoes.addWidget(remover)
        botoes.addStretch()
        layout.addLayout(botoes)
        caixa = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        caixa.accepted.connect(self._confirmar)
        caixa.rejected.connect(self.reject)
        layout.addWidget(caixa)
        self._atualizar_dummies(configuracao.get('produtos', []))
        for combo in self.funcoes.values():
            combo.currentIndexChanged.connect(lambda: self._atualizar_dummies())

    def _snapshot_produtos(self):
        produtos = []
        for row in range(self.produtos.rowCount()):
            produtos.append(dict(nome=self.produtos.item(row, 0).text(),
                papel=self.produtos.cellWidget(row, 1).currentData(),
                valores={v:self.produtos.item(row, col+2).text() for col,v in enumerate(self._dummies)}))
        return produtos

    def _atualizar_dummies(self, produtos=None):
        if produtos is None:
            produtos = self._snapshot_produtos()
        self._dummies = [v for v,c in self.funcoes.items() if c.currentData() == 'dummy']
        self.produtos.setRowCount(0)
        self.produtos.setColumnCount(2+len(self._dummies))
        self.produtos.setHorizontalHeaderLabels(['Produto', 'Papel no perfil'] + self._dummies)
        self.produtos.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.produtos.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        for produto in produtos:
            self._adicionar_produto(produto)
        self.info_dummies.setText(f'{len(self._dummies)} variável(is) dummy: ' + (', '.join(self._dummies) or 'nenhuma'))

    def _adicionar_produto(self, produto=None):
        p = produto or dict(nome=f'Produto {self.produtos.rowCount()+1}', papel='outro', valores={})
        row = self.produtos.rowCount()
        self.produtos.insertRow(row)
        self.produtos.setItem(row, 0, QTableWidgetItem(p['nome']))
        combo = QComboBox()
        combo.setToolTip(self._dica_produtos)
        for valor,texto in PAPEIS_PRODUTO.items():
            combo.addItem(texto, valor)
        combo.setCurrentIndex(combo.findData(p['papel']))
        self.produtos.setCellWidget(row,1,combo)
        for col,v in enumerate(self._dummies):
            self.produtos.setItem(row,col+2,QTableWidgetItem(str(p['valores'].get(v,0))))

    def _remover_produto(self):
        if self.produtos.currentRow() >= 0:
            self.produtos.removeRow(self.produtos.currentRow())

    def coletar(self):
        geometricas = []
        for papel in ('dap','h','H'):
            nomes = [v for v,c in self.funcoes.items() if c.currentData() == papel]
            if len(nomes) != 1:
                raise ValueError('Escolha exatamente uma variável para DAP, uma para h e uma para H.')
            geometricas.append(nomes[0])
        produtos = self._snapshot_produtos()
        try:
            for p in produtos:
                p['nome'] = p['nome'].strip()
                p['valores'] = {v:float(valor.replace(',', '.')) for v,valor in p['valores'].items()}
        except ValueError:
            raise ValueError('Os valores das dummies devem ser numéricos.')
        config = dict(geometricas=geometricas, dummies=list(self._dummies), produtos=produtos)
        validar_configuracao_produtos(config,self.variaveis)
        return config

    def _confirmar(self):
        try:
            self.configuracao = self.coletar()
        except ValueError as e:
            QMessageBox.warning(self,'Produtos do taper',str(e))
            return
        self.accept()
