"""Planejamento exploratório de tábuas por classe de torete."""
import json
from math import sqrt

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSlider, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget,
)

from ..core import projeto
from ..core.db import conectar
from ..core.desdobro import simular
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from ..theme import qss
from .base import TelaBase


class SecaoTorete(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(260, 260)
        self.diametro, self.cerne, self.pecas = 30, 50, []

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Escala fixa até 60 cm: variar o diâmetro varia o círculo visivelmente.
        escala = (min(self.width(), self.height()) - 30) / max(60, self.diametro)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(escala, escala)
        r = self.diametro / 2
        p.setPen(QPen(QColor('#785438'), 1 / escala))
        p.setBrush(QColor('#ebcf94'))
        p.drawEllipse(QRectF(-r, -r, 2*r, 2*r))
        rc = r * sqrt(self.cerne / 100)
        p.setBrush(QColor('#a76b43'))
        p.drawEllipse(QRectF(-rc, -rc, 2*rc, 2*rc))
        p.setPen(QPen(QColor('#155e75'), 1 / escala))
        p.setBrush(QColor(60, 180, 200, 65))
        for x, y, w, h in self.pecas:
            p.drawRect(QRectF(x, y, w, h))
        p.end()


class TelaDesdobro(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._carregando = True
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 16)
        raiz.setSpacing(16)
        cabecalho = CabecalhoTela('Desdobro de Toretes',
            'Compare produtos, visualize os cortes e estime a produção de cada lote.')
        cabecalho.adicionar_acao('Salvar estudo', self._salvar, icone='salvar', destaque=True)
        raiz.addWidget(cabecalho)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        raiz.addWidget(split, 1)
        esquerda = QWidget()
        painel_secao = Cartao('Seção de corte')
        painel_secao.layout().setStretch(1, 1)
        painel_secao.layout().setStretch(2, 0)
        split.addWidget(esquerda)
        split.addWidget(painel_secao)
        split.setSizes([520, 420])
        le, ld = QVBoxLayout(esquerda), QVBoxLayout(painel_secao.corpo)
        le.setContentsMargins(0, 0, 0, 0)
        ld.setContentsMargins(0, 0, 0, 0)
        self.comprimento = QLabel()
        self.comprimento.setWordWrap(True)
        qss.aplicar_variante(self.comprimento, 'dica')
        le.addWidget(self.comprimento)
        abas = QTabWidget()
        abas.setDocumentMode(True)
        abas.tabBar().setDrawBase(False)
        le.addWidget(abas, 1)
        for titulo, nome, colunas, padrao in (
                ('Produtos', 'produtos', ['Nome', 'Compr. (m)', 'Larg. (cm)', 'Esp. (cm)'],
                 ['Tábua', '', '10', '2,5']),
                ('Lote de toretes', 'lote', ['Diâm. (cm)', 'Cerne (%)', 'Nº toras', 'Prev./tora', 'Prev./lote'],
                 ['30', '50', '1'])):
            painel = QWidget()
            pagina = QVBoxLayout(painel)
            pagina.setContentsMargins(0, 12, 0, 0)
            dica = QLabel('Selecione um produto para simular o encaixe. Edite as dimensões na tabela.'
                          if nome == 'produtos' else 'Informe o diâmetro sem casca, o cerne em % da área e a quantidade.')
            dica.setWordWrap(True)
            qss.aplicar_variante(dica, 'dica')
            pagina.addWidget(dica)
            tabela = self._tabela(colunas)
            setattr(self, nome, tabela)
            pagina.addWidget(tabela, 1)
            self._botoes(pagina, tabela, padrao)
            abas.addTab(painel, titulo)
            if nome == 'lote':
                self.resumo_lote = QLabel()
                self.resumo_lote.setWordWrap(True)
                pagina.addWidget(self.resumo_lote)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(10)
        ld.addLayout(form)
        self.diametro = self._spin(1, 200, 30)
        self.cerne = self._spin(0, 100, 50)
        self.serra = self._spin(0, 20, 3)
        self.rendimento_serraria = self._spin(0, 100, 35)
        form.addRow('Diâmetro do torete (cm)', self.diametro)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(10, 2000)
        self.slider.setValue(300)
        form.addRow(self.slider)
        form.addRow('Cerne (% da área)', self.cerne)
        form.addRow('Espessura da serra (mm)', self.serra)
        form.addRow('Rendimento da serraria (%)', self.rendimento_serraria)
        self.rendimento_serraria.setToolTip(
            'Percentual do volume da tora convertido em produto comercial. '
            'Referência inicial: 35%. A produção também respeita o limite geométrico.')
        self.canvas = SecaoTorete()
        self.canvas.setMinimumSize(220, 180)
        ld.addWidget(self.canvas, 1)
        legenda = QLabel('Marrom: cerne · Bege: alburno · Azul: encaixe geométrico teórico')
        legenda.setWordWrap(True)
        ld.addWidget(legenda)
        self.resultado = QLabel()
        self.resultado.setWordWrap(True)
        ld.addWidget(self.resultado)
        nota = QLabel('Estimativa por cortes paralelos, com rotação de 90°. '
            'Seção circular e cerne concêntrico, constantes no comprimento. '
            'Não considera conicidade ou defeitos; não garante o máximo global. '
            'Produção prevista limitada pelo rendimento informado e pelo encaixe, '
            'arredondada para tábuas inteiras por tora. Esse ajuste agregado representa '
            'perdas do processo, sem localizar os defeitos ou as peças rejeitadas. '
            'Classificação de cerne/alburno referente ao encaixe teórico.')
        nota.setWordWrap(True)
        qss.aplicar_variante(nota, 'dica')
        detalhes = QPushButton('Sobre a estimativa')
        detalhes.setCheckable(True)
        qss.aplicar_variante(detalhes, 'leve')
        detalhes.toggled.connect(nota.setVisible)
        nota.hide()
        le.addWidget(detalhes)
        le.addWidget(nota)
        self.status = QLabel()
        raiz.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._calcular)
        for tabela in (self.produtos, self.lote):
            tabela.itemChanged.connect(self._agendar)
        self.produtos.itemSelectionChanged.connect(self._agendar)
        self.lote.itemSelectionChanged.connect(self._selecionar_lote)
        for spin in (self.diametro, self.cerne, self.serra, self.rendimento_serraria):
            spin.valueChanged.connect(self._agendar)
        self.slider.valueChanged.connect(lambda v: self.diametro.setValue(v / 10))
        self.diametro.valueChanged.connect(lambda v: self.slider.setValue(round(v * 10)))
        self.recarregar_lista()

    def _spin(self, minimo, maximo, valor):
        spin = QDoubleSpinBox()
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        spin.setProperty('controles', 'mais_menos')
        spin.setRange(minimo, maximo)
        spin.setDecimals(2)
        spin.setValue(valor)
        return spin

    def _tabela(self, nomes):
        t = QTableWidget(0, len(nomes))
        t.setHorizontalHeaderLabels(nomes)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(32)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        return t

    def _inserir(self, tabela, valores):
        row = tabela.rowCount()
        tabela.insertRow(row)
        for col, valor in enumerate(valores):
            tabela.setItem(row, col, QTableWidgetItem(str(valor)))
        tabela.selectRow(row)
        self._agendar()

    def _botoes(self, layout, tabela, padrao):
        barra = QHBoxLayout()
        for nome, acao in [('Adicionar', lambda: self._inserir(tabela,
                [padrao[0], str(self._comprimento or ''), *padrao[2:]]
                if tabela is self.produtos else padrao)),
                ('Remover selecionado', lambda: self._remover(tabela))]:
            b = QPushButton(nome)
            b.clicked.connect(acao)
            barra.addWidget(b)
        barra.addStretch(1)
        layout.addLayout(barra)

    def _remover(self, tabela):
        if tabela.currentRow() >= 0:
            tabela.removeRow(tabela.currentRow())
            self._agendar()

    def _agendar(self, *args):
        if not self._carregando:
            self.status.setText('Alterações ainda não salvas.')
            self._timer.start()

    def _dados(self, tabela):
        return [[tabela.item(r, c).text() if tabela.item(r, c) else ''
                 for c in range(3 if tabela is self.lote else tabela.columnCount())]
                for r in range(tabela.rowCount())]

    @staticmethod
    def _numero(texto):
        from math import isfinite
        try:
            n = float(texto.replace(',', '.'))
        except ValueError:
            raise ValueError('Preencha as dimensões e os valores das tabelas com números válidos.')
        if not isfinite(n):
            raise ValueError('Número não finito.')
        return n

    def _selecionar_lote(self):
        r = self.lote.currentRow()
        if r < 0 or self._carregando:
            return
        try:
            d, c, _ = map(self._numero, self._dados(self.lote)[r])
            if not 1 <= d <= 200 or not 0 <= c <= 100:
                return
            self.diametro.setValue(d)
            self.cerne.setValue(c)
        except ValueError:
            pass

    def _calcular(self):
        self.canvas.diametro = self.diametro.value()
        self.canvas.cerne = self.cerne.value()
        self.canvas.pecas = []
        self.lote.blockSignals(True)
        for row in range(self.lote.rowCount()):
            for col in (3, 4):
                item = QTableWidgetItem('—')
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.lote.setItem(row, col, item)
        self.lote.blockSignals(False)
        try:
            produtos = []
            for nome, comp, larg, esp in self._dados(self.produtos):
                valores = [self._numero(v) for v in (comp, larg, esp)]
                if not nome.strip() or min(valores) <= 0:
                    raise ValueError('Preencha nome e dimensões positivas em todos os produtos.')
                produtos.append((nome, *valores))
            totais = [0, 0, 0]
            lote = []
            for linha in self._dados(self.lote):
                d, c, n = map(self._numero, linha)
                if not 1 <= d <= 200 or not 0 <= c <= 100 or n < 0 or not n.is_integer():
                    raise ValueError('Lote: diâmetro de 1 a 200 cm, cerne de 0 a 100% e quantidade inteira ≥ 0.')
                totais[0 if c > 50 else 1 if c < 50 else 2] += int(n)
                lote.append((d, c, int(n)))
            self.resumo_lote.setText(f'Toras: {sum(totais)} · Mais cerne: {totais[0]} · '
                                    f'Mais alburno: {totais[1]} · 50/50: {totais[2]}')
            if not self._comprimento or self._comprimento <= 0:
                raise ValueError('Configure e salve o comprimento da tora em Sortimentos.')
            r = self.produtos.currentRow()
            if not 0 <= r < len(produtos):
                self.resultado.setText('Cadastre e selecione uma tábua.')
                return
            nome, comp, larg, esp = produtos[r]
            def calcular(d, c):
                return simular(d, larg, esp, comp, self._comprimento, c, self.serra.value(),
                               self.rendimento_serraria.value())
            resultado = calcular(self.diametro.value(), self.cerne.value())
            total = 0
            self.lote.blockSignals(True)
            try:
                for row, (d, c, n) in enumerate(lote):
                    quantidade = calcular(d, c)['quantidade_prevista']
                    total += quantidade * n
                    self.lote.item(row, 3).setText(str(quantidade))
                    self.lote.item(row, 4).setText(str(quantidade * n))
            finally:
                self.lote.blockSignals(False)
            self.canvas.pecas = resultado['pecas']
            self.resultado.setText(
                f'{nome}: previsão de {resultado["quantidade_prevista"]} tábuas por tora\n'
                f'Volume previsto: {resultado["volume_previsto"]:.4f} m³ · '
                f'Rendimento previsto: {resultado["rendimento_previsto"]:.1f}% '
                f'(referência: {self.rendimento_serraria.value():.1f}%)\n'
                f'Encaixe teórico: {resultado["quantidade"]} tábuas · '
                f'Aproveitamento geométrico: {resultado["rendimento"]:.1f}%\n'
                f'No encaixe teórico — mais cerne: {resultado["cerne"]} · Mais alburno: {resultado["alburno"]} '
                f'· Equilibradas: {resultado["equilibradas"]}\n'
                f'Previsão do lote para este produto: {total} tábuas.')
            return True
        except (ValueError, OverflowError) as e:
            self.resultado.setText(str(e) or 'Preencha a tabela com números válidos.')
            self.resumo_lote.setText('Revise os dados das tabelas.')
            return False
        finally:
            self.canvas.update()

    def novo_registro(self):
        self._carregando = True
        self._timer.stop()
        self.produtos.setRowCount(0)
        self.lote.setRowCount(0)
        self.diametro.setValue(30)
        self.cerne.setValue(50)
        self.serra.setValue(3)
        self.rendimento_serraria.setValue(35)
        self._comprimento = None
        self._carregando = False

    def recarregar_lista(self):
        self.novo_registro()
        self._carregando = True
        estado = {}
        try:
            conn = conectar()
            try:
                row = conn.execute('SELECT comprimento_tora FROM configuracoes WHERE id=1').fetchone()
                self._comprimento = row[0] if row else None
                row = conn.execute('SELECT dados FROM desdobro_estado WHERE id=1').fetchone()
                estado = json.loads(row[0]) if row else {}
            finally:
                conn.close()
        except RuntimeError:
            pass
        self.comprimento.setText(f'Comprimento da tora (Sortimentos): {self._comprimento or "não configurado"} m')
        for valores in estado.get('produtos', [['Tábua', str(self._comprimento or ''), '10', '2,5']]):
            self._inserir(self.produtos, valores)
        for valores in estado.get('lote', []):
            self._inserir(self.lote, valores)
        self.diametro.setValue(estado.get('diametro', 30))
        self.cerne.setValue(estado.get('cerne', 50))
        self.serra.setValue(estado.get('serra', 3))
        self.rendimento_serraria.setValue(estado.get('rendimento_serraria', 35))
        self._carregando = False
        self._calcular()
        self.status.setText('Estudo carregado.' if estado else 'Novo estudo.')

    def _salvar(self):
        if not self._calcular():
            return
        dados = dict(produtos=self._dados(self.produtos), lote=self._dados(self.lote),
                     diametro=self.diametro.value(), cerne=self.cerne.value(), serra=self.serra.value(),
                     rendimento_serraria=self.rendimento_serraria.value())
        try:
            conn = conectar()
            try:
                conn.execute('INSERT INTO desdobro_estado(id,dados) VALUES(1,?) '
                             'ON CONFLICT(id) DO UPDATE SET dados=excluded.dados',
                             (json.dumps(dados, ensure_ascii=False),))
                conn.commit()
            finally:
                conn.close()
            projeto.sincronizar()
            self.status.setText('Estudo salvo no projeto.')
        except (RuntimeError, OSError) as e:
            QMessageBox.warning(self, 'Salvar estudo', str(e))
