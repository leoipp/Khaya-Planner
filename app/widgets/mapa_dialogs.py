"""Consulta da simulação, join e edição de cores por valor único."""
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core import mapa_dados, projeto
from ..core.db import conectar, conectar_caminho
from .tabela import Tabela


class DialogoConsultaSimulacao(QDialog):
    """Filtros aplicados no banco inteiro; a grade é paginada."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Tabela de cenários para o mapa e filtros')
        self.resize(1000, 650)
        self._pagina = 0
        self._linhas_filtro = []
        conn = conectar()
        try:
            fontes = mapa_dados.fontes_simulacao(conn)
            estado = mapa_dados.obter_estado(conn, 'consulta_simulacao')
        finally:
            conn.close()
        layout = QVBoxLayout(self)
        topo = QHBoxLayout()
        topo.addWidget(QLabel('Tabela:'))
        self.fonte = QComboBox()
        self.fonte.addItems(fontes)
        self.fonte.setCurrentText(estado.get('fonte', ''))
        topo.addWidget(self.fonte, 1)
        topo.addWidget(QLabel('Combinar filtros:'))
        self.combinacao = QComboBox()
        self.combinacao.addItems(['E', 'OU'])
        self.combinacao.setCurrentText(estado.get('combinacao', 'E'))
        topo.addWidget(self.combinacao)
        adicionar = QPushButton('Adicionar filtro')
        adicionar.clicked.connect(lambda: self.adicionar_filtro())
        topo.addWidget(adicionar)
        layout.addLayout(topo)
        explicacao = QLabel('E: todas as condições; OU: qualquer condição. Em listas, separe valores com ;. Os filtros abrangem toda a tabela, inclusive linhas fora da página exibida.')
        explicacao.setWordWrap(True)
        layout.addWidget(explicacao)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(180)
        container = QWidget()
        self.filtros_layout = QVBoxLayout(container)
        self.filtros_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(container)
        layout.addWidget(scroll)
        consulta = QHBoxLayout()
        atualizar = QPushButton('Consultar')
        atualizar.clicked.connect(lambda: self.atualizar())
        consulta.addWidget(atualizar)
        limpar = QPushButton('Limpar filtros')
        limpar.clicked.connect(self.limpar_filtros)
        consulta.addWidget(limpar)
        consulta.addStretch(1)
        self.anterior = QPushButton('Página anterior')
        self.anterior.clicked.connect(lambda: self.atualizar(max(0, self._pagina - 1)))
        consulta.addWidget(self.anterior)
        self.proxima = QPushButton('Próxima página')
        self.proxima.clicked.connect(lambda: self.atualizar(self._pagina + 1))
        consulta.addWidget(self.proxima)
        layout.addLayout(consulta)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabela = Tabela(colunas=[])
        self.tabela.view.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.tabela.view.horizontalHeader().setToolTip('Use os filtros acima para consultar e unir o resultado completo.')
        layout.addWidget(self.tabela, 1)
        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        botoes.button(QDialogButtonBox.StandardButton.Save).setText('Aplicar filtros para o join')
        botoes.accepted.connect(self.salvar)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)
        self.fonte.currentTextChanged.connect(self._trocar_fonte)
        if estado.get('fonte') == self.fonte.currentText():
            for filtro in estado.get('filtros', []):
                self.adicionar_filtro(filtro)
        self.atualizar()

    def _colunas(self):
        conn = conectar()
        try:
            return [c[0] for c in mapa_dados.colunas(conn, self.fonte.currentText())]
        finally:
            conn.close()

    def adicionar_filtro(self, estado=None):
        try:
            colunas = self._colunas()
        except ValueError as erro:
            self.status.setText(str(erro))
            return
        widget = QWidget()
        linha = QHBoxLayout(widget)
        linha.setContentsMargins(0, 0, 0, 0)
        coluna, op, valor = QComboBox(), QComboBox(), QLineEdit()
        coluna.addItems(colunas)
        op.addItems(mapa_dados.OPERADORES)
        valor.setPlaceholderText('Valor; em listas: A;B;C')
        if estado:
            if estado.get('coluna') not in colunas:
                coluna.addItem(estado.get('coluna', ''))
            coluna.setCurrentText(estado.get('coluna', ''))
            op.setCurrentText(estado.get('operador', '='))
            valor.setText(str(estado.get('valor', '')))
        op.currentTextChanged.connect(lambda texto: valor.setEnabled(texto not in ('é nulo', 'não é nulo')))
        valor.setEnabled(op.currentText() not in ('é nulo', 'não é nulo'))
        linha.addWidget(coluna, 2)
        linha.addWidget(op, 1)
        linha.addWidget(valor, 2)
        remover = QPushButton('Remover')
        entrada = (widget, coluna, op, valor)
        remover.clicked.connect(lambda: self._remover_filtro(entrada))
        linha.addWidget(remover)
        self._linhas_filtro.append(entrada)
        self.filtros_layout.addWidget(widget)

    def _remover_filtro(self, entrada):
        self._linhas_filtro.remove(entrada)
        entrada[0].deleteLater()

    def limpar_filtros(self):
        for entrada in list(self._linhas_filtro):
            self._remover_filtro(entrada)
        self.atualizar()

    def _trocar_fonte(self, _texto):
        self.limpar_filtros()

    def configuracao(self):
        return dict(fonte=self.fonte.currentText(), combinacao=self.combinacao.currentText(),
                    filtros=[dict(coluna=c.currentText(), operador=o.currentText(), valor=v.text())
                             for _, c, o, v in self._linhas_filtro])

    def atualizar(self, pagina=0):
        config = self.configuracao()
        conn = conectar()
        try:
            cols, linhas, total = mapa_dados.consultar(
                conn, config['fonte'], config['filtros'], config['combinacao'], pagina=pagina)
        except ValueError as erro:
            self.status.setText(str(erro))
            self.tabela.redefinir_colunas([])
            self.anterior.setEnabled(False)
            self.proxima.setEnabled(False)
            return False
        finally:
            conn.close()
        self._pagina = pagina
        self.tabela.redefinir_colunas(cols)
        self.tabela.definir_linhas(linhas)
        inicio = pagina * 200 + 1 if total else 0
        fim = pagina * 200 + len(linhas)
        self.status.setText(f'{total:,} linhas no recorte · exibindo {inicio}–{fim}.')
        self.anterior.setEnabled(pagina > 0)
        self.proxima.setEnabled(fim < total)
        return True

    def salvar(self):
        if self.atualizar():
            mapa_dados.salvar_estado('consulta_simulacao', self.configuracao())
            self.accept()


class _ThreadJoin(QThread):
    def __init__(self, caminho, config, parent):
        super().__init__(parent)
        self.caminho, self.config = caminho, config
        self.resultado = None
        self.erro = None

    def run(self):
        try:
            conn = conectar_caminho(self.caminho)
            try:
                self.resultado = mapa_dados.aplicar_join(conn, **self.config)
                conn.commit()
            finally:
                conn.close()
        except Exception as erro:
            self.erro = str(erro)


class DialogoJoin(QDialog):
    def __init__(self, camada, parent=None):
        super().__init__(parent)
        self.camada = camada
        self.thread = None
        self.setWindowTitle('Join do shapefile com a tabela de cenários')
        self.resize(650, 640)
        conn = conectar()
        try:
            self._shape_colunas = [c[0] for c in mapa_dados.colunas(conn, camada)]
            fontes = mapa_dados.fontes_simulacao(conn)
            self._consulta = mapa_dados.obter_estado(conn, 'consulta_simulacao')
            self._anterior = mapa_dados.obter_estado(conn, 'join:' + camada)
        finally:
            conn.close()
        layout = QVBoxLayout(self)
        self.formulario = QWidget()
        form = QFormLayout(self.formulario)
        self.fonte = QComboBox()
        self.fonte.addItems(fontes)
        self.fonte.setCurrentText(self._consulta.get('fonte', self._anterior.get('fonte', '')))
        form.addRow('Tabela de cenários ou simulação:', self.fonte)
        self.recorte = QComboBox()
        self.recorte.addItems(['Tabela inteira', 'Recorte filtrado da tabela'])
        form.addRow('Linhas para o join:', self.recorte)
        editar = QPushButton('Consultar tabela e definir filtros...')
        editar.clicked.connect(self.editar_filtros)
        form.addRow(editar)
        self.info_filtros = QLabel()
        self.info_filtros.setWordWrap(True)
        form.addRow(self.info_filtros)
        self.chave_shape, self.chave_sim = QComboBox(), QComboBox()
        self.chave_shape.addItems(self._shape_colunas)
        self.chave_shape.setCurrentText(self._anterior.get('chave_shape', ''))
        form.addRow('Coluna do shapefile:', self.chave_shape)
        form.addRow('Coluna da tabela:', self.chave_sim)
        self.modo = QComboBox()
        self.modo.addItem('Texto (preserva zeros à esquerda)', 'texto')
        self.modo.addItem('Número (01, 1 e 1,0 são equivalentes)', 'numero')
        self.modo.setCurrentIndex(max(0, self.modo.findData(self._anterior.get('modo_chave', 'texto'))))
        form.addRow('Comparar chaves como:', self.modo)
        self.repeticoes = QComboBox()
        for label, valor in [('Exigir uma linha por chave', 'unico'), ('Primeira linha (menor id)', 'primeiro'),
                             ('Última linha (maior id)', 'ultimo'), ('Soma', 'soma'), ('Média', 'media'),
                             ('Mínimo', 'min'), ('Máximo', 'max')]:
            self.repeticoes.addItem(label, valor)
        self.repeticoes.setCurrentIndex(max(0, self.repeticoes.findData(self._anterior.get('repeticoes', 'unico'))))
        form.addRow('Chaves repetidas:', self.repeticoes)
        nota = QLabel('As tabelas disponibilizadas em Resumos de Cenários já incluem os filtros, agrupamentos, indicadores e pivôs definidos ali. Soma, média, mínimo e máximo exigem colunas numéricas. O join mantém todas as feições e salva os atributos em colunas sim_*.')
        nota.setWordWrap(True)
        form.addRow(nota)
        form.addRow(QLabel('Colunas a trazer:'))
        self.colunas = QListWidget()
        self.colunas.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        form.addRow(self.colunas)
        selecao = QHBoxLayout()
        for label, marcado in [('Marcar todas', True), ('Desmarcar todas', False)]:
            botao = QPushButton(label)
            botao.clicked.connect(lambda _checked=False, m=marcado: self._marcar(m))
            selecao.addWidget(botao)
        form.addRow(selecao)
        layout.addWidget(self.formulario)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.botoes.button(QDialogButtonBox.StandardButton.Ok).setText('Aplicar join')
        self.botoes.accepted.connect(self.aplicar)
        self.botoes.rejected.connect(self.reject)
        layout.addWidget(self.botoes)
        self.fonte.currentTextChanged.connect(self._atualizar_fonte)
        self._atualizar_fonte()

    def _marcar(self, marcado):
        for i in range(self.colunas.count()):
            self.colunas.item(i).setCheckState(Qt.CheckState.Checked if marcado else Qt.CheckState.Unchecked)

    def _atualizar_fonte(self, _texto=None):
        chave_atual = self.chave_sim.currentText()
        selecionadas = ({self.colunas.item(i).text() for i in range(self.colunas.count())
                         if self.colunas.item(i).checkState() == Qt.CheckState.Checked}
                        if self.colunas.count() else None)
        conn = conectar()
        try:
            nomes = [c[0] for c in mapa_dados.colunas(conn, self.fonte.currentText())]
        except ValueError as erro:
            nomes = []
            self.info_filtros.setText(str(erro))
        finally:
            conn.close()
        self.chave_sim.clear()
        self.chave_sim.addItems(nomes)
        preferida = chave_atual or self._anterior.get('chave_simulacao', '')
        if preferida in nomes:
            self.chave_sim.setCurrentText(preferida)
        else:
            comum = next((n for n in nomes if n != 'id' and n in self._shape_colunas), None)
            if comum:
                self.chave_sim.setCurrentText(comum)
                self.chave_shape.setCurrentText(comum)
        self.colunas.clear()
        for nome in nomes:
            item = QListWidgetItem(nome, self.colunas)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if selecionadas is not None:
                marcado = nome in selecionadas
            else:
                marcado = nome in self._anterior.get('colunas', {}) if self._anterior else nome not in ('id', self.chave_sim.currentText())
            item.setCheckState(Qt.CheckState.Checked if marcado else Qt.CheckState.Unchecked)
        if nomes:
            filtros = self._consulta.get('filtros', [])
            self.info_filtros.setText(f'Recorte salvo: {self._consulta.get("fonte", "nenhum")} · {len(filtros)} filtro(s), combinação {self._consulta.get("combinacao", "E")}.')

    def editar_filtros(self):
        dialogo = DialogoConsultaSimulacao(self)
        dialogo.fonte.setCurrentText(self.fonte.currentText())
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._consulta = dialogo.configuracao()
            self.fonte.setCurrentText(self._consulta['fonte'])
            self._atualizar_fonte()
            self.recorte.setCurrentIndex(1)
        dialogo.deleteLater()

    def configuracao(self):
        filtrado = self.recorte.currentIndex() == 1
        if filtrado and self._consulta.get('fonte') != self.fonte.currentText():
            raise ValueError('Defina os filtros para a tabela selecionada antes de usar o recorte.')
        return dict(camada=self.camada, fonte=self.fonte.currentText(),
                    chave_shape=self.chave_shape.currentText(), chave_simulacao=self.chave_sim.currentText(),
                    selecionadas=[self.colunas.item(i).text() for i in range(self.colunas.count())
                                  if self.colunas.item(i).checkState() == Qt.CheckState.Checked],
                    filtros=self._consulta.get('filtros', []) if filtrado else [],
                    combinacao=self._consulta.get('combinacao', 'E'),
                    repeticoes=self.repeticoes.currentData(), modo_chave=self.modo.currentData())

    def aplicar(self):
        if self.thread:
            return
        try:
            config = self.configuracao()
            if not config['selecionadas']:
                raise ValueError('Selecione pelo menos uma coluna a trazer.')
            projeto.finalizar_sincronizacao_pendente()
        except Exception as erro:
            QMessageBox.warning(self, 'Join', str(erro))
            return
        self.formulario.setEnabled(False)
        self.botoes.setEnabled(False)
        self.status.setText('Aplicando join…')
        janela = self.parent().window() if self.parent() else None
        if hasattr(janela, 'travar_navegacao'):
            janela.travar_navegacao(True)
        self.thread = _ThreadJoin(projeto.caminho_trabalho(), config, self)
        self.thread.finished.connect(self._finalizar)
        self.thread.start()

    def _finalizar(self):
        worker = self.thread
        self.thread = None
        janela = self.parent().window() if self.parent() else None
        if hasattr(janela, 'travar_navegacao'):
            janela.travar_navegacao(False)
        self.formulario.setEnabled(True)
        self.botoes.setEnabled(True)
        if worker.erro:
            self.status.setText(worker.erro)
            QMessageBox.warning(self, 'Join', worker.erro)
        else:
            projeto.sincronizar()
            self.accept()
        worker.deleteLater()

    def reject(self):
        if not self.thread:
            super().reject()

    def closeEvent(self, evento):
        if self.thread:
            evento.ignore()
        else:
            super().closeEvent(evento)


def cor_categoria(indice):
    return QColor.fromHsvF((.12 + indice * .61803398875) % 1, .58, .82).name()


class DialogoValoresUnicos(QDialog):
    def __init__(self, colunas, linhas, estado=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Colorir por valores únicos')
        self.resize(650, 550)
        self._colunas, self._linhas = colunas, linhas
        self._estado = estado or {}
        self._cores = {}
        self._chaves = []
        layout = QVBoxLayout(self)
        topo = QHBoxLayout()
        topo.addWidget(QLabel('Coluna:'))
        self.coluna = QComboBox()
        self.coluna.addItems(colunas)
        self.coluna.setCurrentText(self._estado.get('coluna', ''))
        topo.addWidget(self.coluna, 1)
        layout.addLayout(topo)
        nota = QLabel('Escolha a coluna e clique na cor de cada valor. Desmarque valores para usar a cor dos demais; valores nulos aparecem como “Sem valor”.')
        nota.setWordWrap(True)
        layout.addWidget(nota)
        self.tabela = QTableWidget(0, 3)
        self.tabela.setHorizontalHeaderLabels(['Valor', 'Feições', 'Cor'])
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.tabela, 1)
        resto = QHBoxLayout()
        self.cor_demais = self._estado.get('cor_demais', '#b0b0b0')
        self.botao_demais = QPushButton('Cor dos demais valores')
        self.botao_demais.clicked.connect(self._escolher_demais)
        resto.addWidget(self.botao_demais)
        paleta = QPushButton('Gerar nova paleta')
        paleta.clicked.connect(self._nova_paleta)
        resto.addWidget(paleta)
        layout.addLayout(resto)
        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botoes.button(QDialogButtonBox.StandardButton.Ok).setText('Aplicar cores')
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)
        self.coluna.currentTextChanged.connect(self._atualizar)
        self._atualizar()

    def _atualizar(self, _texto=None):
        coluna = self.coluna.currentText()
        if coluna not in self._colunas:
            return
        indice = self._colunas.index(coluna)
        self._categorias = mapa_dados.categorias(linha[indice] for linha in self._linhas)
        self._chaves = list(self._categorias)
        salvas = self._estado.get('cores', {}) if self._estado.get('coluna') == coluna else {}
        self._cores = {k: salvas.get(k, '#b0b0b0' if k == 'null' else cor_categoria(i)) for i, k in enumerate(self._chaves)}
        self.tabela.setRowCount(len(self._chaves))
        for i, chave in enumerate(self._chaves):
            info = self._categorias[chave]
            item = QTableWidgetItem(info['valor'])
            item.setToolTip(chave)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ativo = chave in salvas if self._estado.get('coluna') == coluna else True
            item.setCheckState(Qt.CheckState.Checked if ativo else Qt.CheckState.Unchecked)
            self.tabela.setItem(i, 0, item)
            self.tabela.setItem(i, 1, QTableWidgetItem(str(info['quantidade'])))
            botao = QPushButton(self._cores[chave])
            botao.clicked.connect(lambda _checked=False, k=chave, b=botao: self._escolher_cor(k, b))
            self._pintar_botao(botao, self._cores[chave])
            self.tabela.setCellWidget(i, 2, botao)
        self.tabela.resizeColumnToContents(0)

    @staticmethod
    def _pintar_botao(botao, cor):
        texto = '#ffffff' if QColor(cor).lightnessF() < .5 else '#111111'
        botao.setStyleSheet(f'background-color: {cor}; color: {texto};')

    def _escolher_cor(self, chave, botao):
        cor = QColorDialog.getColor(QColor(self._cores[chave]), self, 'Cor do valor')
        if cor.isValid():
            self._cores[chave] = cor.name()
            botao.setText(cor.name())
            self._pintar_botao(botao, cor.name())

    def _escolher_demais(self):
        cor = QColorDialog.getColor(QColor(self.cor_demais), self, 'Cor dos demais valores')
        if cor.isValid():
            self.cor_demais = cor.name()
            self._pintar_botao(self.botao_demais, cor.name())

    def _nova_paleta(self):
        for i, chave in enumerate(self._chaves):
            self._cores[chave] = cor_categoria(i + 3)
            botao = self.tabela.cellWidget(i, 2)
            botao.setText(self._cores[chave])
            self._pintar_botao(botao, self._cores[chave])
            self.tabela.item(i, 0).setCheckState(Qt.CheckState.Checked)

    def configuracao(self):
        return dict(coluna=self.coluna.currentText(), cor_demais=self.cor_demais,
                    cores={k: self._cores[k] for i, k in enumerate(self._chaves)
                           if self.tabela.item(i, 0).checkState() == Qt.CheckState.Checked})
