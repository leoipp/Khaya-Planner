"""Taper cadastrado, vista lateral e desdobro limitado pela ponta fina."""
import json
import sqlite3

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import projeto
from ..core.db import conectar
from ..core.desdobro import simular
from ..core.toragem import tracar_arvore
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from ..theme import qss, manager as tema
from .base import TelaBase
from .desdobro import SecaoTorete


class PerfilArvore(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(240, 240)
        self.perfil = None
        self.selecionado = -1

    def paintEvent(self, event):
        if not self.perfil:
            return
        perfil = self.perfil
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        topo, fundo = 22, self.height() - 30
        centro = self.width() / 2
        sy = (fundo-topo) / perfil['altura']
        sx = (self.width() - 145) / max(float(max(perfil['diametros'])), 1)
        def yy(h):
            return fundo-h*sy
        pontos = [QPointF(centro-d*sx/2, yy(h))
                  for h, d in zip(perfil['alturas'], perfil['diametros'])]
        pontos += [QPointF(centro+d*sx/2, yy(h))
                   for h, d in reversed(list(zip(perfil['alturas'], perfil['diametros'])))]
        p.setPen(QPen(QColor('#785438'), 1.5))
        p.setBrush(QColor('#d8ac73'))
        p.drawPolygon(QPolygonF(pontos))
        for i, t in enumerate(perfil['toretes']):
            if i == self.selecionado:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(20, 150, 180, 55))
                p.drawRect(QRectF(centro-self.width()/2+8, yy(t['fim']),
                                 self.width()-16, (t['fim']-t['inicio'])*sy))
            escuro = tema.obter().modo_atual() == 'dark'
            cor = QColor(('#72ced3' if escuro else '#155e75') if t['comercial']
                         else ('#f29b9b' if escuro else '#b64747'))
            p.setPen(QPen(cor, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(centro-t['diametro_topo']*sx/2-12, yy(t['fim'])),
                       QPointF(centro+t['diametro_topo']*sx/2+12, yy(t['fim'])))
            p.setPen(cor)
            p.drawText(QRectF(3, yy(t['fim'])-10, 68, 20),
                       Qt.AlignmentFlag.AlignLeft, f'{t["fim"]:.1f} m')
            p.drawText(QRectF(self.width()-62, yy((t['inicio']+t['fim'])/2)-10, 60, 20),
                       Qt.AlignmentFlag.AlignRight, f'T{t["numero"]}')
        p.setPen(self.palette().text().color())
        p.drawLine(QPointF(centro-25, yy(perfil['toco'])),
                   QPointF(centro+25, yy(perfil['toco'])))
        p.drawText(5, 16, f'H = {perfil["altura"]:.2f} m')
        p.drawText(5, self.height()-8, 'Perfil do fuste · largura ampliada')
        p.end()


class TelaToragem(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._carregando = True
        self._perfil = None
        self._resultados = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._calcular)
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 16)
        raiz.setSpacing(16)
        cab = CabecalhoTela('Taper e Toragem',
            'DAP → perfil do fuste → toretes. O encaixe das tábuas usa a menor seção de cada torete.')
        cab.adicionar_acao('Salvar estudo', self._salvar, icone='salvar', destaque=True)
        raiz.addWidget(cab)
        split = QSplitter(Qt.Orientation.Horizontal)
        raiz.addWidget(split, 1)
        cartao_controles = Cartao('Árvore e produto')
        cartao_perfil = Cartao('Perfil do fuste')
        cartao_corte = Cartao('Seção limitante')
        for cartao in (cartao_perfil, cartao_corte):
            cartao.layout().setStretch(1, 1)
            cartao.layout().setStretch(2, 0)
        esquerda, meio, direita = cartao_controles.corpo, cartao_perfil.corpo, cartao_corte.corpo
        painel_controles = QScrollArea()
        painel_controles.setWidgetResizable(True)
        painel_controles.setWidget(cartao_controles)
        painel_controles.setMinimumWidth(260)
        for w in (painel_controles, cartao_perfil, cartao_corte):
            split.addWidget(w)
        split.setSizes([280, 300, 360])
        split.setChildrenCollapsible(False)
        le, lm, ld = QVBoxLayout(esquerda), QVBoxLayout(meio), QVBoxLayout(direita)
        for layout in (le, lm, ld):
            layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setVerticalSpacing(8)
        le.addLayout(form)
        self.modelos = QComboBox()
        form.addRow('Modelo de taper', self.modelos)
        self.dap = self._spin(1, 300, 30)
        self.altura = self._spin(1.31, 100, 20)
        self.toco = self._spin(0, 5, .2)
        form.addRow('DAP (cm, a 1,30 m)', self.dap)
        form.addRow('Altura total (m)', self.altura)
        form.addRow('Altura do toco (m)', self.toco)
        self.dimensoes = QLabel()
        self.dimensoes.setWordWrap(True)
        le.addWidget(self.dimensoes)
        self.info_modelo = QLabel()
        self.info_modelo.setWordWrap(True)
        le.addWidget(self.info_modelo)
        self.produtos = QComboBox()
        self.produtos.addItem('Dimensões manuais', None)
        form_produto = QFormLayout()
        form_produto.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form_produto.setVerticalSpacing(8)
        le.addLayout(form_produto)
        form_produto.addRow('Produto salvo', self.produtos)
        self.comprimento = self._spin(.01, 30, 4)
        self.largura = self._spin(.1, 100, 10)
        self.espessura = self._spin(.1, 100, 2.5)
        self.serra = self._spin(0, 20, 3)
        self.rendimento = self._spin(0, 100, 35)
        self.cerne = self._spin(0, 100, 50)
        for rotulo, spin in [('Tábua: comprimento (m)', self.comprimento),
                             ('Largura (cm)', self.largura), ('Espessura (cm)', self.espessura),
                             ('Serra (mm)', self.serra), ('Rendimento referência (%)', self.rendimento),
                             ('Cerne (% da área)', self.cerne)]:
            form_produto.addRow(rotulo, spin)
        nota = QLabel('Modelo: 3 entradas na ordem DAP, h, H; saída em cm, '
            'como no Construtor de Variáveis. Altura total informada: DAP sozinho não define o perfil. '
            'Use DAP e modelo com a mesma base de casca. Cerne informado é constante por seção. '
            'Volume bruto por Smalian; previsão limitada pela geometria e pelo rendimento. '
            'Cortes longitudinais não incluem a espessura do corte de traçamento.')
        nota.setWordWrap(True)
        le.addWidget(nota)
        le.addStretch()
        self.vista = PerfilArvore()
        self.vista.setMinimumSize(180, 180)
        lm.addWidget(self.vista, 1)
        legenda = QLabel('Tracejado azul: torete comercial\nVermelho: fora do limite comercial')
        legenda.setWordWrap(True)
        lm.addWidget(legenda)
        self.secao = SecaoTorete()
        self.secao.setMinimumSize(180, 180)
        ld.addWidget(self.secao, 1)
        self.info_secao = QLabel('Selecione um torete na tabela.')
        self.info_secao.setWordWrap(True)
        ld.addWidget(self.info_secao)
        self.tabela = QTableWidget(0, 9)
        self.tabela.setHorizontalHeaderLabels([
            'Torete', 'De (m)', 'Até (m)', 'D base (cm)', 'D topo (cm)',
            'D corte (cm)', 'Vol. (m³)', 'Tábuas prev.', 'Situação'])
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabela.horizontalHeader().setStretchLastSection(True)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.setShowGrid(False)
        self.tabela.verticalHeader().hide()
        self.tabela.setMinimumHeight(150)
        self.tabela.setMaximumHeight(230)
        raiz.addWidget(self.tabela)
        self.resumo = QLabel()
        self.resumo.setWordWrap(True)
        qss.aplicar_variante(self.resumo, 'dica')
        raiz.addWidget(self.resumo)
        self.status = QLabel()
        raiz.addWidget(self.status)
        self.tabela.itemSelectionChanged.connect(self._selecionar)
        self.modelos.currentIndexChanged.connect(self._agendar)
        self.produtos.currentIndexChanged.connect(self._produto)
        for spin in self._campos().values():
            spin.valueChanged.connect(self._agendar)
        self.recarregar_lista()

    def _campos(self):
        return {nome: getattr(self, nome) for nome in ('dap', 'altura', 'toco', 'comprimento',
                'largura', 'espessura', 'serra', 'rendimento', 'cerne')}

    def _spin(self, minimo, maximo, valor):
        w = QDoubleSpinBox()
        w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        w.setProperty('controles', 'mais_menos')
        w.setRange(minimo, maximo)
        w.setDecimals(2)
        w.setValue(valor)
        return w

    def _agendar(self, *args):
        if not self._carregando:
            self.status.setText('Alterações ainda não salvas.')
            self._timer.start()

    def _produto(self):
        produto = self.produtos.currentData()
        if produto:
            for campo, valor in zip((self.comprimento, self.largura, self.espessura), produto):
                campo.setValue(float(str(valor).replace(',', '.')))
        self._agendar()

    def novo_registro(self):
        self._carregando = True
        self._timer.stop()
        self._perfil = None
        self._resultados = []
        self.modelos.clear()
        self.produtos.clear()
        self.produtos.addItem('Dimensões manuais', None)
        self._comprimento_tora = self._diametro_minimo = None
        for nome, valor in dict(dap=30, altura=20, toco=.2, comprimento=4,
                                largura=10, espessura=2.5, serra=3, rendimento=35, cerne=50).items():
            self._campos()[nome].setValue(valor)
        self.tabela.setRowCount(0)
        self.vista.perfil = None
        self.vista.update()
        self.secao.pecas = []
        self.secao.update()
        self._carregando = False

    def recarregar_lista(self):
        self.novo_registro()
        self._carregando = True
        estado = {}
        try:
            conn = conectar()
            try:
                row = conn.execute('SELECT comprimento_tora, diametro_minimo_tora '
                                   'FROM configuracoes WHERE id=1').fetchone()
                if row:
                    self._comprimento_tora, self._diametro_minimo = row
                for row in conn.execute('SELECT id,nome,tipo,variaveis_x,equacao,coeficientes,'
                                        'estrato_coluna,estrato FROM modelos ORDER BY nome'):
                    if 'afilamento' not in row[2].lower() and 'taper' not in row[2].lower():
                        continue
                    modelo = dict(id=row[0], nome=row[1], variaveis=json.loads(row[3] or '[]'),
                                  equacao=row[4], coeficientes=json.loads(row[5] or '{}'),
                                  estrato=f'{row[6]} = {row[7]}' if row[6] else 'Todos')
                    self.modelos.addItem(f'{row[1]} · {modelo["estrato"]}', modelo)
                row = conn.execute('SELECT dados FROM desdobro_estado WHERE id=1').fetchone()
                if row:
                    desdobro = json.loads(row[0])
                    self.serra.setValue(desdobro.get('serra', 3))
                    self.rendimento.setValue(desdobro.get('rendimento_serraria', 35))
                    for nome, comp, larg, esp in desdobro.get('produtos', []):
                        try:
                            valores = [float(str(v).replace(',', '.')) for v in (comp, larg, esp)]
                            if all(v > 0 for v in valores):
                                self.produtos.addItem(nome, valores)
                        except ValueError:
                            continue
                row = conn.execute('SELECT dados FROM toragem_estado WHERE id=1').fetchone()
                estado = json.loads(row[0]) if row else {}
            finally:
                conn.close()
        except RuntimeError:
            pass
        if self._comprimento_tora and self._comprimento_tora > 0:
            self.comprimento.setValue(self._comprimento_tora)
        for nome, campo in self._campos().items():
            if nome in estado:
                campo.setValue(estado[nome])
        for i in range(self.modelos.count()):
            if self.modelos.itemData(i)['id'] == estado.get('modelo_id'):
                self.modelos.setCurrentIndex(i)
        self.dimensoes.setText(f'Sortimentos: torete de {self._comprimento_tora or "—"} m; '
                              f'diâmetro mínimo de {self._diametro_minimo if self._diametro_minimo is not None else "—"} cm.')
        self._carregando = False
        self._calcular()
        self.status.setText('Estudo carregado.' if estado else 'Novo estudo.')

    def _calcular(self):
        selecionado = max(0, self.tabela.currentRow())
        self._perfil = None
        self._resultados = []
        self.vista.perfil = None
        self.secao.pecas = []
        self.info_secao.clear()
        self.tabela.setRowCount(0)
        try:
            modelo = self.modelos.currentData()
            if not modelo:
                raise ValueError('Cadastre um modelo Afilamento / Taper na tela Modelos.')
            self.info_modelo.setText(f'{modelo["estrato"]}\n{modelo["equacao"] or "Equação vazia"}\n'
                                    'Entradas: DAP (cm), h (m), H (m). Saída: diâmetro (cm).')
            if not self._comprimento_tora or self._comprimento_tora <= 0 or self._diametro_minimo is None:
                raise ValueError('Configure comprimento da tora e diâmetro mínimo em Sortimentos.')
            perfil = tracar_arvore(modelo['equacao'], modelo['coeficientes'], modelo['variaveis'],
                self.dap.value(), self.altura.value(), self._comprimento_tora,
                self._diametro_minimo, self.toco.value())
            resultados = []
            for t in perfil['toretes']:
                r = None
                if t['comercial']:
                    r = simular(t['diametro_corte'], self.largura.value(), self.espessura.value(),
                        self.comprimento.value(), t['comprimento'], self.cerne.value(),
                        self.serra.value(), self.rendimento.value(), t['volume'])
                resultados.append(r)
            self._perfil, self._resultados = perfil, resultados
            self.vista.perfil = perfil
            self.tabela.setRowCount(len(perfil['toretes']))
            for row, (t, r) in enumerate(zip(perfil['toretes'], resultados)):
                valores = [str(t['numero']), f'{t["inicio"]:.2f}', f'{t["fim"]:.2f}',
                    f'{t["diametro_base"]:.2f}', f'{t["diametro_topo"]:.2f}',
                    f'{t["diametro_corte"]:.2f}', f'{t["volume"]:.4f}',
                    str(r['quantidade_prevista']) if r else '0', 'Comercial' if r else 'Não comercial']
                for col, valor in enumerate(valores):
                    self.tabela.setItem(row, col, QTableWidgetItem(valor))
            comerciais = [t for t in perfil['toretes'] if t['comercial']]
            volume = sum(t['volume'] for t in comerciais)
            serrado = sum(r['volume_previsto'] for r in resultados if r)
            quantidade = sum(r['quantidade_prevista'] for r in resultados if r)
            rendimento = 100*serrado/volume if volume else 0
            aviso = (' Perfil não monotônico: usa também o menor diâmetro amostrado no interior '
                     '(80 intervalos por torete).' if perfil['perfil_nao_monotono'] else '')
            self.resumo.setText(f'{len(comerciais)} toretes comerciais · {volume:.4f} m³ brutos · '
                f'{quantidade} tábuas previstas · {serrado:.4f} m³ serrados · Rendimento: {rendimento:.1f}% '
                f'sobre os toretes comerciais. Ponta/sobra acima do último comercial: {perfil["sobra"]:.2f} m.' + aviso)
            if self.tabela.rowCount():
                self.tabela.selectRow(min(selecionado, self.tabela.rowCount()-1))
            return True
        except (ValueError, TypeError, OverflowError) as e:
            self.resumo.setText(str(e))
            return False
        finally:
            self.vista.update()
            self.secao.update()

    def _selecionar(self):
        row = self.tabela.currentRow()
        if not self._perfil or not 0 <= row < len(self._resultados):
            return
        t, r = self._perfil['toretes'][row], self._resultados[row]
        self.vista.selecionado = row
        self.vista.update()
        self.secao.diametro = max(t['diametro_corte'], .01)
        self.secao.cerne = self.cerne.value()
        self.secao.pecas = r['pecas'] if r else []
        self.secao.update()
        texto = (f'Torete {t["numero"]}: base Ø {t["diametro_base"]:.2f} cm → '
                 f'topo Ø {t["diametro_topo"]:.2f} cm.\n'
                 f'Seção de corte limitante: Ø {t["diametro_corte"]:.2f} cm.\n')
        if r:
            texto += (f'Azul: encaixe teórico de {r["quantidade"]} tábuas.\n'
                      f'Previsão: {r["quantidade_prevista"]} tábuas; {r["rendimento_previsto"]:.1f}% '
                      f'do volume bruto deste torete (referência {self.rendimento.value():.1f}%).')
        else:
            texto += 'Fora do limite comercial; sem produção prevista.'
        self.info_secao.setText(texto)

    def _salvar(self):
        self._timer.stop()
        if not self._calcular():
            return
        estado = {nome: campo.value() for nome, campo in self._campos().items()}
        estado['modelo_id'] = self.modelos.currentData()['id']
        try:
            conn = conectar()
            try:
                conn.execute('INSERT INTO toragem_estado(id,dados) VALUES(1,?) '
                    'ON CONFLICT(id) DO UPDATE SET dados=excluded.dados',
                    (json.dumps(estado, ensure_ascii=False),))
                conn.commit()
            finally:
                conn.close()
            projeto.sincronizar()
            self.status.setText('Estudo salvo no projeto.')
        except (RuntimeError, OSError, sqlite3.Error) as e:
            QMessageBox.warning(self, 'Salvar toragem', str(e))
