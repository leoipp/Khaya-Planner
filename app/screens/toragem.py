"""Taper cadastrado, vista lateral e desdobro limitado pela ponta fina."""
import json
import html
import sqlite3

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QScrollArea, QSplitter, QTableWidget,
    QPushButton, QDialog,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import projeto, classes_tabuas
from ..core.db import conectar
from ..core.desdobro import simular, validar_classes, classificar_tabuas
from ..core.toragem import tracar_arvore, indicadores_casca, configuracao_produtos_padrao
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from ..theme import qss, manager as tema
from .base import TelaBase
from .desdobro import SecaoTorete
from .produtos_taper import DialogoProdutosTaper


CORES_PRODUTOS = ['#7356b5', '#198aa0', '#d77429', '#be4b87']


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
        sx = (self.width() - 145) / max(max(float(max(ds)) for ds in perfil.get('perfis', {'base':perfil['diametros']}).values()), 1)
        def yy(h):
            return fundo-h*sy
        camadas = perfil.get('perfis', {'com_casca': perfil['diametros']})
        estilos = [('com_casca', '#785438'), ('sem_casca', '#ebcf94'), ('cerne', '#a76b43')]
        extras = [k for k in camadas if k not in ('com_casca','sem_casca','cerne')]
        estilos += [(k, CORES_PRODUTOS[i % len(CORES_PRODUTOS)]) for i,k in enumerate(extras)]
        tem_camada = any(k in camadas for k in ('com_casca','sem_casca','cerne'))
        for nome, cor in estilos:
            if nome not in camadas:
                continue
            ds = camadas[nome]
            pontos = [QPointF(centro-d*sx/2, yy(h)) for h,d in zip(perfil['alturas'], ds)]
            pontos += [QPointF(centro+d*sx/2, yy(h)) for h,d in reversed(list(zip(perfil['alturas'],ds)))]
            p.setPen(QPen(QColor('#785438'), 1))
            if nome in extras and tem_camada:
                p.setPen(QPen(QColor(cor), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                p.setBrush(QColor(cor))
                tem_camada = True
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
        self._configuracoes_produtos = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._calcular)
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(20, 20, 20, 16)
        raiz.setSpacing(16)
        cab = CabecalhoTela('Desdobro',
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
        self.configurar_produtos = QPushButton('Variáveis dummy e produtos…')
        self.configurar_produtos.clicked.connect(self._configurar_produtos)
        form.addRow(self.configurar_produtos)
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
        form_produto.addRow('Classe de tábua', self.produtos)
        self.comprimento = self._spin(.01, 100, 4)
        self.largura = self._spin(.01, 300, 10)
        self.espessura = self._spin(.01, 100, 2.5)
        self.serra = self._spin(0, 20, 3)
        self.rendimento = self._spin(0, 100, 35)
        self.cerne = self._spin(0, 100, 50)
        for rotulo, spin in [('Tábua: comprimento (m)', self.comprimento),
                             ('Largura (cm)', self.largura), ('Espessura (cm)', self.espessura),
                             ('Serra (mm)', self.serra), ('Rendimento referência (%)', self.rendimento),
                             ('Cerne (% da área)', self.cerne)]:
            form_produto.addRow(rotulo, spin)
        referencia_classes = QLabel('Classificação de cerne: Sortimentos → Classes de tábua.')
        referencia_classes.setWordWrap(True)
        le.addWidget(referencia_classes)
        nota = QLabel('Em Variáveis dummy e produtos, indique as entradas DAP, h e H e cadastre '
            'as combinações das dummies para cada produto. Saída do modelo: diâmetro em cm. '
            'Volumes por Smalian nos mesmos trechos. O perfil com casca define o limite comercial, '
            'quando cadastrado; na ausência dele, usa-se o perfil sem casca ou o primeiro produto. '
            'O encaixe e o rendimento referência usam o produto sem casca, quando disponível. '
            'Classificação por área de cerne na seção, não pelo volume ao longo da tábua. '
            'Quantidades previstas por classe são proporcionais ao encaixe teórico.')
        nota.setWordWrap(True)
        le.addWidget(nota)
        le.addStretch()
        self.vista = PerfilArvore()
        self.vista.setMinimumSize(180, 180)
        lm.addWidget(self.vista, 1)
        legenda = QLabel('Marrom escuro: casca · Bege: alburno · Marrom: cerne\nTracejado azul: comercial · Vermelho: fora do limite')
        legenda.setWordWrap(True)
        self.legenda_perfil = legenda
        lm.addWidget(legenda)
        self.secao = SecaoTorete()
        self.secao.setMinimumSize(180, 180)
        ld.addWidget(self.secao, 1)
        self.legenda_classes = QLabel()
        self.legenda_classes.setWordWrap(True)
        ld.addWidget(self.legenda_classes)
        self.info_secao = QLabel('Selecione um torete na tabela.')
        self.info_secao.setWordWrap(True)
        ld.addWidget(self.info_secao)
        self.tabela = QTableWidget(0, 12)
        self.tabela.setHorizontalHeaderLabels([
            'Torete', 'De (m)', 'Até (m)', 'D base (cm)', 'D topo (cm)',
            'D corte (cm)', 'Vol. CC (m³)', 'Vol. SC (m³)', 'Vol. cerne (m³)',
            'Tábuas prev.', 'Classes: quantidade / volume previsto', 'Situação'])
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

    def _variaveis_disponiveis(self, modelo):
        variaveis = list(modelo['variaveis'])
        try:
            seletores = indicadores_casca(modelo['equacao'], variaveis)
        except ValueError:
            seletores = {}
        return list(dict.fromkeys(variaveis + list(seletores)))

    def _configuracao_modelo(self, modelo):
        config = self._configuracoes_produtos.get(str(modelo['id']))
        if config is not None:
            return config
        variaveis = self._variaveis_disponiveis(modelo)
        try:
            conhecidos = indicadores_casca(modelo['equacao'], variaveis)
        except ValueError:
            conhecidos = {}
        if conhecidos and len([v for v in variaveis if v not in conhecidos]) == 3:
            return configuracao_produtos_padrao(modelo['equacao'], variaveis)
        if len(variaveis) > 3:
            self.configurar_produtos.setToolTip('Configure quais variáveis são dummies e suas combinações por produto.')
            raise ValueError('Defina Variáveis dummy e produtos para este modelo.')
        return None

    def _configurar_produtos(self):
        modelo = self.modelos.currentData()
        if not modelo:
            return
        variaveis = self._variaveis_disponiveis(modelo)
        config = self._configuracoes_produtos.get(str(modelo['id']))
        if config is None:
            config = configuracao_produtos_padrao(modelo['equacao'], variaveis)
        dialogo = DialogoProdutosTaper(modelo['nome'], variaveis, config, self)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._configuracoes_produtos[str(modelo['id'])] = dialogo.configuracao
            self._agendar()
            self._calcular()

    def _ler_classes(self):
        conn = conectar()
        try:
            return classes_tabuas.carregar_classificacao_cerne(conn)
        finally:
            conn.close()

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
        self.rendimento.setEnabled(not isinstance(produto, dict))
        if isinstance(produto, dict):
            self.rendimento.setValue(produto['rendimento'])
        if produto:
            valores = [produto[k] for k in ('comprimento','largura','espessura')] if isinstance(produto, dict) else produto
            for campo, valor in zip((self.comprimento, self.largura, self.espessura), valores):
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
        self._configuracoes_produtos = {}
        self.configurar_produtos.setToolTip('Defina as variáveis dummy e as combinações de produtos do modelo selecionado.')
        self._comprimento_tora = self._diametro_minimo = None
        for nome, valor in dict(dap=30, altura=20, toco=.2, comprimento=4,
                                largura=10, espessura=2.5, serra=3, rendimento=35, cerne=50).items():
            self._campos()[nome].setValue(valor)
        self.tabela.setRowCount(0)
        self.vista.perfil = None
        self.vista.update()
        self.secao.pecas = []
        self.secao.cores_pecas = []
        self.secao.diametro_com_casca = None
        self.secao.contornos_produtos = []
        self.legenda_classes.clear()
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
                for classe in classes_tabuas.listar(conn):
                    rotulo = f"{classe['nome']} · {classe['comprimento']:g} m · {classe['espessura']:g} × {classe['largura']:g} cm"
                    self.produtos.addItem(rotulo, classe)
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
        for i in range(self.produtos.count()):
            produto = self.produtos.itemData(i)
            if isinstance(produto, dict) and produto['id'] == estado.get('classe_tabua_id'):
                self.produtos.setCurrentIndex(i)
                break
        if self._comprimento_tora and self._comprimento_tora > 0:
            self.comprimento.setValue(self._comprimento_tora)
        for nome, campo in self._campos().items():
            if nome in estado:
                campo.setValue(estado[nome])
        self._configuracoes_produtos = estado.get('produtos_por_modelo', {})
        if isinstance(self.produtos.currentData(), dict):
            self._produto()
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
        self.secao.cores_pecas = []
        self.secao.diametro_com_casca = None
        self.secao.contornos_produtos = []
        self.info_secao.clear()
        self.tabela.setRowCount(0)
        try:
            modelo = self.modelos.currentData()
            if not modelo:
                raise ValueError('Cadastre um modelo Afilamento / Taper na tela Modelos.')
            self.info_modelo.setText(f'{modelo["estrato"]}\n{modelo["equacao"] or "Equação vazia"}\n'
                                    'Entradas: DAP (cm), h (m), H (m). Saída: diâmetro (cm).')
            produto = self.produtos.currentData()
            comprimento_tora = produto['comprimento'] if isinstance(produto, dict) else self._comprimento_tora
            if not comprimento_tora or comprimento_tora <= 0 or self._diametro_minimo is None:
                raise ValueError('Configure comprimento da tora e diâmetro mínimo em Sortimentos.')
            classes = self._ler_classes()
            validar_classes(classes)
            self.legenda_classes.setText(' · '.join(f"<span style=\"color:{c['cor']}\">■</span> {html.escape(c['nome'])}" for c in classes))
            self.dimensoes.setText(f'Torete: {comprimento_tora:g} m; diâmetro mínimo: {self._diametro_minimo:g} cm.' +
                                  (' Comprimento da classe de tábua selecionada.' if isinstance(produto, dict) else ''))
            config = self._configuracao_modelo(modelo)
            variaveis = self._variaveis_disponiveis(modelo)
            perfil = tracar_arvore(modelo['equacao'], modelo['coeficientes'], variaveis,
                self.dap.value(), self.altura.value(), comprimento_tora,
                self._diametro_minimo, self.toco.value(), configuracao_produtos=config)
            multiplo = 'perfis' in perfil
            self.cerne.setEnabled('cerne' not in perfil.get('perfis', {}))
            self.rendimento.setToolTip('Referência sobre o volume sem casca.' if 'sem_casca' in perfil.get('perfis', {})
                                      else 'Referência sobre o volume do perfil de corte.')
            if config:
                self.configurar_produtos.setToolTip(f"Dummies: {', '.join(config['dummies']) or 'nenhuma'}. " +
                    '; '.join(p['nome'] + ': ' + ', '.join(f'{k}={v:g}' for k,v in p['valores'].items())
                              for p in config['produtos']))
            else:
                self.configurar_produtos.setToolTip('Perfil único. Configure as variáveis e produtos para personalizar.')
            extras = [k for k in perfil.get('perfis', {}) if k.startswith('produto:')]
            self.tabela.setColumnCount(12+len(extras))
            rotulo_base = perfil.get('produtos', {}).get(perfil.get('produto_base'), {}).get('nome', 'Perfil cadastrado')
            if multiplo:
                self.legenda_perfil.setText('Perfis: ' + ' · '.join(meta['nome'] for meta in perfil['produtos'].values()) + '\nTracejado azul: comercial · Vermelho: fora do limite')
            else:
                self.legenda_perfil.setText('Perfil cadastrado · Tracejado azul: comercial · Vermelho: fora do limite')
            self.tabela.setHorizontalHeaderLabels(['Torete','De (m)','Até (m)','D base (cm)','D topo (cm)',
                'D corte (cm)','Vol. CC (m³)','Vol. SC (m³)','Vol. cerne (m³)','Tábuas prev.',
                'Classes: quantidade / volume previsto','Situação'] +
                [f"Vol. {perfil['produtos'][k]['nome']} (m³)" for k in extras])
            resultados = []
            for t in perfil['toretes']:
                r = None
                if t['comercial'] and t.get('diametro_madeira', t['diametro_corte']) > 1e-8:
                    r = simular(t.get('diametro_madeira', t['diametro_corte']), self.largura.value(), self.espessura.value(),
                        self.comprimento.value(), t['comprimento'], t.get('cerne_percentual', self.cerne.value()),
                        self.serra.value(), self.rendimento.value(), t.get('volumes', {}).get(perfil.get('produto_madeira'), t['volume']))
                    classificar_tabuas(r, classes, self.largura.value()*self.espessura.value()/10000*self.comprimento.value())
                resultados.append(r)
            self._perfil, self._resultados = perfil, resultados
            self.vista.perfil = perfil
            self.tabela.setRowCount(len(perfil['toretes']))
            for row, (t, r) in enumerate(zip(perfil['toretes'], resultados)):
                valores = [str(t['numero']), f'{t["inicio"]:.2f}', f'{t["fim"]:.2f}',
                    f'{t["diametro_base"]:.2f}', f'{t["diametro_topo"]:.2f}',
                    f'{t.get("diametro_madeira", t["diametro_corte"]):.2f}',
                    f'{t["volumes"]["com_casca"]:.4f}' if 'com_casca' in t.get('volumes', {}) else (f'{t["volume"]:.4f}' if not multiplo else '—'),
                    f'{t["volumes"]["sem_casca"]:.4f}' if 'sem_casca' in t.get('volumes', {}) else '—',
                    f'{t["volumes"]["cerne"]:.4f}' if 'cerne' in t.get('volumes', {}) else '—',
                    str(r['quantidade_prevista']) if r else '0',
                    '; '.join(f"{c['nome']}: {c['quantidade_prevista']:.1f} / {c['volume_previsto']:.4f} m³" for c in r['classes']) if r else '—',
                    ('Comercial' if r else 'Sem seção de madeira') if t['comercial'] else 'Não comercial']
                valores += [f'{t["volumes"][k]:.4f}' for k in extras]
                for col, valor in enumerate(valores):
                    self.tabela.setItem(row, col, QTableWidgetItem(valor))
            comerciais = [t for t in perfil['toretes'] if t['comercial']]
            volume = sum(t['volume'] for t in comerciais)
            serrado = sum(r['volume_previsto'] for r in resultados if r)
            quantidade = sum(r['quantidade_prevista'] for r in resultados if r)
            rendimento = 100*serrado/volume if volume else 0
            aviso = (' Perfil não monotônico: usa também o menor diâmetro amostrado no interior '
                     '(80 intervalos por torete).' if perfil['perfil_nao_monotono'] else '')
            self.resumo.setText(f'{len(comerciais)} toretes comerciais · {volume:.4f} m³ ({rotulo_base}) · '
                f'{quantidade} tábuas previstas · {serrado:.4f} m³ serrados · Rendimento: {rendimento:.1f}% '
                f'sobre os toretes comerciais. Ponta/sobra acima do último comercial: {perfil["sobra"]:.2f} m.' + aviso)
            if multiplo:
                totais = '; '.join(f"{meta['nome']}: {sum(t['volumes'][k] for t in comerciais):.4f} m³"
                                   for k,meta in perfil['produtos'].items())
                self.resumo.setText(self.resumo.text() + ' Volumes comerciais: ' + totais + '.')
            totais_classes = []
            for c in classes:
                itens = [v for r in resultados if r for v in r['classes'] if v['nome'] == c['nome']]
                totais_classes.append(f"{c['nome']}: {sum(v['quantidade_prevista'] for v in itens):.1f} tábuas equivalentes / {sum(v['volume_previsto'] for v in itens):.4f} m³")
            self.resumo.setText(self.resumo.text() + ' Classes (estimativa proporcional): ' + '; '.join(totais_classes))
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
        self.secao.diametro = max(t.get('diametro_madeira', t['diametro_corte']), .01)
        self.secao.diametro_com_casca = t.get('diametros_secao', {}).get('com_casca')
        extras = [(k,v) for k,v in t.get('diametros_secao', {}).items() if k.startswith('produto:')]
        self.secao.contornos_produtos = [(v,CORES_PRODUTOS[i % len(CORES_PRODUTOS)]) for i,(k,v) in enumerate(extras)]
        self.secao.cerne = t.get('cerne_percentual', self.cerne.value())
        self.secao.cores_pecas = r.get('cores_pecas', []) if r else []
        self.secao.pecas = r['pecas'] if r else []
        self.secao.update()
        texto = (f'Torete {t["numero"]}: base Ø {t["diametro_base"]:.2f} cm → '
                 f'topo Ø {t["diametro_topo"]:.2f} cm.\n'
                 f'Seção de corte limitante: Ø {t.get("diametro_madeira", t["diametro_corte"]):.2f} cm.\n')
        if r:
            texto += (f'Cores por classe: encaixe teórico de {r["quantidade"]} tábuas.\n'
                      f'Previsão: {r["quantidade_prevista"]} tábuas; {r["rendimento_previsto"]:.1f}% '
                      f'do volume de corte deste torete (referência {self.rendimento.value():.1f}%).')
        else:
            texto += 'Sem produção prevista para este torete.'
        if 'diametros_secao' in t:
            ds = t['diametros_secao']
            texto += f"\nSeção a {t['altura_corte']:.2f} m: " + '; '.join(f"{self._perfil['produtos'][k]['nome']} Ø {d:.2f} cm" for k,d in ds.items())
        self.info_secao.setText(texto)

    def _salvar(self):
        self._timer.stop()
        if not self._calcular():
            return
        estado = {nome: campo.value() for nome, campo in self._campos().items()}
        estado['modelo_id'] = self.modelos.currentData()['id']
        estado['classes_madeira'] = self._ler_classes()
        estado['produtos_por_modelo'] = self._configuracoes_produtos
        produto = self.produtos.currentData()
        estado['classe_tabua_id'] = produto['id'] if isinstance(produto, dict) else None
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
