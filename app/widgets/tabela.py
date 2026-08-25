# -*- coding: utf-8 -*-
"""
Tabela "com bateria inclusa" (QTableView + QAbstractTableModel próprio):
ordenação por coluna (clique no cabeçalho) e, no botão direito do
cabeçalho, três ações por coluna: Filtrar, Tipo da coluna (Texto/Inteiro/
Float/Data — muda ordenação E como o valor é exibido) e Resumir coluna
(Soma/Média — o resultado aparece numa linha separada no final da
tabela). Porte de app/widgets/tabela.py (Treeview) do projeto Tkinter
original — mesmo comportamento, mesma API de alto nível
(redefinir_colunas/definir_linhas), só que com QTableView no lugar do
Treeview (o modelo dá virtualização de graça, o Treeview original não
tinha — ver ressalva sobre `parametros_weibull_manejo` no app original).
"""
from datetime import datetime

from dateutil import parser as dateutil_parser
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QInputDialog, QMenu, QTableView, QVBoxLayout,
    QWidget,
)

from ..theme import manager as tema

TIPOS = ["Texto", "Inteiro", "Float", "Data"]
IID_RESUMO = "__resumo__"


class _ViewTabela(QTableView):
    """QTableView com recorte opcional que inclui seus subcontroles.

    O ``border-radius`` do QSS pinta a borda, mas não recorta o cabeçalho
    nem as barras internas. A máscara acompanha o redimensionamento e faz
    o recorte real quando uma tela solicita cantos arredondados.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raio_recorte = 0

    def definir_raio_recorte(self, raio):
        self._raio_recorte = max(0, int(raio))
        self._atualizar_mascara()

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        self._atualizar_mascara()

    def _atualizar_mascara(self):
        if not self._raio_recorte or self.width() <= 0 or self.height() <= 0:
            self.clearMask()
            return
        caminho = QPainterPath()
        caminho.addRoundedRect(
            QRectF(self.rect()), self._raio_recorte, self._raio_recorte)
        self.setMask(QRegion(caminho.toFillPolygon().toPolygon()))


class _FiltroRecorteTabela(QObject):
    """Mantém a máscara arredondada em views Qt criadas fora de Tabela."""

    def __init__(self, view, raio):
        super().__init__(view)
        self._raio = raio

    def eventFilter(self, objeto, evento):
        if evento.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            caminho = QPainterPath()
            caminho.addRoundedRect(QRectF(objeto.rect()), self._raio, self._raio)
            objeto.setMask(QRegion(caminho.toFillPolygon().toPolygon()))
        return False


def emoldurar_tabela(view, raio=8):
    """Envolve QTableWidget/QTableView numa borda arredondada real."""
    moldura = QWidget()
    moldura.setObjectName("MolduraTabelaArredondada")
    moldura.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(moldura)
    layout.setContentsMargins(1, 1, 1, 1)
    layout.setSpacing(0)

    view.setProperty("cantosArredondados", True)
    filtro = _FiltroRecorteTabela(view, max(0, raio - 1))
    view.installEventFilter(filtro)
    view._filtro_recorte_arredondado = filtro
    layout.addWidget(view)
    return moldura


def _converter(valor, tipo):
    if valor is None or valor == "":
        return None
    texto = str(valor).strip()
    if tipo == "Inteiro":
        try:
            return int(float(texto.replace(",", ".")))
        except ValueError:
            return texto
    if tipo == "Float":
        try:
            return float(texto.replace(",", "."))
        except ValueError:
            return texto
    if tipo == "Data":
        # tenta ISO primeiro (ano-mês-dia, sem ambiguidade — cobre o caso de
        # timestamp do Excel/pandas com microssegundos); só cai pro dateutil
        # com dayfirst=True (formato brasileiro dd/mm/aaaa) se não for ISO,
        # porque dayfirst=True aplicado numa string ISO troca mês e dia.
        try:
            return datetime.fromisoformat(texto)
        except ValueError:
            pass
        try:
            return dateutil_parser.parse(texto, dayfirst=True)
        except (ValueError, OverflowError, TypeError):
            return texto
    return texto


def _formatar(valor, tipo):
    convertido = _converter(valor, tipo)
    if convertido is None:
        return ""
    if tipo == "Float" and isinstance(convertido, float):
        return f"{convertido:.2f}"
    if tipo == "Data" and isinstance(convertido, datetime):
        return convertido.strftime("%d/%m/%Y")
    return str(convertido)


class _ModeloTabela(QAbstractTableModel):
    """Só lê o estado já calculado por Tabela._visiveis — toda a lógica
    de filtro/ordenação/resumo vive na própria Tabela (mesmo espírito do
    Treeview original: o "modelo" aqui existe só porque QTableView exige
    um, não porque a lógica precisasse ficar separada)."""

    def __init__(self, tabela):
        super().__init__()
        self._tabela = tabela

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._tabela._visiveis)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._tabela.colunas)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        _iid, valores, eh_resumo = self._tabela._visiveis[index.row()]
        if role == Qt.DisplayRole:
            return valores[index.column()]
        if role == Qt.TextAlignmentRole and self._tabela._alinhamento_celulas is not None:
            return self._tabela._alinhamento_celulas
        if eh_resumo and role == Qt.BackgroundRole:
            return QColor(tema.obter().cor_resumo_tabela()["bg"])
        if eh_resumo and role == Qt.ForegroundRole:
            return QColor(tema.obter().cor_resumo_tabela()["fg"])
        if eh_resumo and role == Qt.FontRole:
            fonte = QFont()
            fonte.setBold(True)
            return fonte
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        coluna = self._tabela.colunas[section]
        rotulo = self._tabela.rotulos.get(coluna, coluna)
        marcador = " 🔎" if self._tabela.filtros_coluna.get(coluna) else ""
        return f"{rotulo}{marcador}"


class Tabela(QWidget):
    """`colunas`/`larguras`/`tipos_iniciais` com a mesma forma do widget
    Tkinter original. `selecaoAlterada` é emitido a cada mudança de
    seleção (equivalente ao bind de `<<TreeviewSelect>>` de lá); use
    `selecionados()` pra ler os ids selecionados no momento.
    `conteudoAtualizado` é emitido toda vez que as linhas exibidas mudam
    de verdade (definir_linhas, ordenar por coluna, filtrar, ligar/
    desligar resumo — todos passam por `_atualizar_exibicao`, que faz
    `beginResetModel`/`endResetModel`) — use pra re-embutir widgets por
    linha via `view.setIndexWidget` (ex: um botão "+" numa coluna), já
    que resetar o modelo derruba qualquer index widget encaixado antes."""

    selecaoAlterada = Signal()
    conteudoAtualizado = Signal()

    def __init__(self, colunas, larguras=None, tipos_iniciais=None, rotulos=None,
                 distribuir_igualmente=False, parent=None):
        super().__init__(parent)
        self.colunas = list(colunas)
        self.tipos_coluna = {c: (tipos_iniciais or {}).get(c, "Texto") for c in self.colunas}
        # Rótulo de cabeçalho por coluna (ex: "equacao" -> "Equação") — cai
        # pra chave da coluna sem tradução se não tiver entrada aqui.
        self.rotulos = dict(rotulos or {})
        # True: todas as colunas dividem igualmente o espaço horizontal
        # disponível (QHeaderView.Stretch em todas, `larguras` ignorado —
        # Stretch recalcula a largura de cada seção sozinho), sem
        # redimensionamento manual. Sem isso (default False), tabelas mais
        # estreitas que o espaço disponível deixam uma faixa cinza vazia à
        # direita da última coluna, ex: Sortimentos/Custo de formação
        # (tela Configurações).
        self._distribuir_igualmente = distribuir_igualmente
        self.resumo_coluna = {}
        self.filtros_coluna = {}
        self.linhas_originais = []
        self._visiveis = []
        self._ordem_coluna = None
        self._ordem_crescente = True
        self._alinhamento_celulas = None

        self._layout_raiz = QVBoxLayout(self)
        self._layout_raiz.setContentsMargins(0, 0, 0, 0)
        self._layout_raiz.setSpacing(4)

        self.view = _ViewTabela(self)
        self.view.setModel(_ModeloTabela(self))
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionsClickable(True)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.view.horizontalHeader().sectionClicked.connect(self._ordenar)
        self.view.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.horizontalHeader().customContextMenuRequested.connect(self._menu_coluna)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.selecaoAlterada.emit())
        # Ctrl+A seleciona a tabela inteira, Ctrl+C copia a seleção pro
        # clipboard como texto separado por tab (cola direto no Excel, ou
        # aqui no chat) — QAbstractItemView não tem esses atalhos por
        # padrão, então intercepta via eventFilter em vez de reimplementar
        # keyPressEvent (evita subclassear QTableView só pra isso).
        self.view.installEventFilter(self)
        self._layout_raiz.addWidget(self.view)

        # Aparência padrão de toda Tabela; telas ainda podem especializar
        # sua grade e seus seletores sem refazer a moldura.
        self.definir_cantos_arredondados(8)

        self._aplicar_larguras(larguras or {})
        tema.obter().themeChanged.connect(self._ao_mudar_tema)

    def definir_cantos_arredondados(self, raio=8):
        """Ativa moldura externa e recorta o conteúdo por dentro dela."""
        self.setObjectName("MolduraTabelaArredondada")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout_raiz.setContentsMargins(1, 1, 1, 1)
        self._layout_raiz.setSpacing(0)
        self.view.setProperty("cantosArredondados", True)
        self.view.definir_raio_recorte(max(0, raio - 1))
        self.style().unpolish(self)
        self.style().polish(self)

    def definir_alinhamento_celulas(self, alinhamento):
        """Define o alinhamento de todas as células e atualiza a exibição."""
        self._alinhamento_celulas = alinhamento
        if self.view.model().rowCount():
            inicio = self.view.model().index(0, 0)
            fim = self.view.model().index(
                self.view.model().rowCount() - 1,
                max(0, self.view.model().columnCount() - 1))
            self.view.model().dataChanged.emit(inicio, fim, [Qt.TextAlignmentRole])

    def eventFilter(self, obj, event):
        if obj is self.view and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.SelectAll):
                self.view.selectAll()
                return True
            if event.matches(QKeySequence.StandardKey.Copy):
                self._copiar_selecao()
                return True
        return super().eventFilter(obj, event)

    def _copiar_selecao(self):
        """Copia a seleção atual (linhas inteiras, já que a tabela seleciona
        por linha) pro clipboard como TSV: uma linha de cabeçalho (rótulo de
        cada coluna) + uma linha por registro selecionado, célula formatada
        exatamente como aparece na tela (_visiveis já tem os valores
        formatados por _atualizar_exibicao) — cola limpo num Excel."""
        selecionados = self.view.selectionModel().selectedIndexes()
        if not selecionados:
            return
        linhas = sorted({indice.row() for indice in selecionados})
        colunas = sorted({indice.column() for indice in selecionados})
        cabecalho = [self.rotulos.get(self.colunas[c], self.colunas[c]) for c in colunas]
        texto = ["\t".join(cabecalho)]
        for linha in linhas:
            _iid, valores, _resumo = self._visiveis[linha]
            texto.append("\t".join(str(valores[c]) for c in colunas))
        QApplication.clipboard().setText("\n".join(texto))

    def _ao_mudar_tema(self, _modo):
        # A cor do rótulo de dica já é reaplicada sozinha (QSS global,
        # `status="neutro"` continua marcado) — só falta isto aqui: a
        # linha de resumo lê BackgroundRole/ForegroundRole do tema atual
        # (ver _ModeloTabela.data), que QSS não alcança (é um model role,
        # não uma propriedade de widget).
        self.view.viewport().update()

    def _aplicar_larguras(self, larguras):
        cabecalho = self.view.horizontalHeader()
        if self._distribuir_igualmente:
            cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            return
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for idx, c in enumerate(self.colunas):
            self.view.setColumnWidth(idx, larguras.get(c, 110))

    # ---------------- API pública ----------------

    def redefinir_colunas(self, colunas, larguras=None, tipos_iniciais=None, rotulos=None):
        """Redefine o conjunto de colunas (usado quando a planilha
        importada muda o schema, ex: Base IFC ByTalhao)."""
        self.colunas = list(colunas)
        self.tipos_coluna = {c: (tipos_iniciais or {}).get(c, "Texto") for c in self.colunas}
        self.rotulos = dict(rotulos or {})
        self.resumo_coluna = {}
        self.filtros_coluna = {}
        self._ordem_coluna = None
        self.linhas_originais = []

        modelo = self.view.model()
        modelo.beginResetModel()
        self._visiveis = []
        modelo.endResetModel()
        self._aplicar_larguras(larguras or {})

    def definir_linhas(self, linhas, ids=None):
        """linhas: lista de tuplas de valores (mesma ordem de self.colunas).
        ids: lista paralela de id de cada linha (ex: id do registro no
        banco); se None, usa o índice da linha."""
        if ids is None:
            ids = [str(i) for i in range(len(linhas))]
        self.linhas_originais = list(zip(ids, linhas))
        self._atualizar_exibicao()

    def selecionados(self):
        """Ids das linhas selecionadas no momento, na ordem em que
        aparecem na tabela (pode incluir IID_RESUMO se a linha de resumo
        estiver selecionada — mesmo comportamento do Treeview original;
        quem chama já filtra isso, ver telas)."""
        linhas = sorted({indice.row() for indice in self.view.selectionModel().selectedRows()})
        return [self._visiveis[linha][0] for linha in linhas]

    def selecionar_id(self, id_):
        alvo = str(id_)
        for linha, (iid, _valores, _resumo) in enumerate(self._visiveis):
            if iid == alvo:
                self.view.selectRow(linha)
                return

    def limpar_selecao(self):
        self.view.clearSelection()

    def linhas_visiveis(self):
        """Lista de (iid, valores_formatados, eh_resumo) na ORDEM exibida
        agora (já filtrada/ordenada) — pra quem precisa saber a linha
        física de cada iid (ex: pra embutir um widget via
        `view.setIndexWidget`, ver `conteudoAtualizado`)."""
        return list(self._visiveis)

    # ---------------- exibição (filtro + formatação + resumo) ----------------

    def _atualizar_exibicao(self):
        visiveis = []
        for iid, linha in self.linhas_originais:
            oculta = False
            for idx, c in enumerate(self.colunas):
                termo = self.filtros_coluna.get(c, "").strip().lower()
                if not termo:
                    continue
                if termo not in _formatar(linha[idx], self.tipos_coluna[c]).lower():
                    oculta = True
                    break
            if not oculta:
                visiveis.append((iid, linha))

        linhas_modelo = []
        for iid, linha in visiveis:
            valores = [_formatar(v, self.tipos_coluna[c]) for v, c in zip(linha, self.colunas)]
            linhas_modelo.append((iid, valores, False))

        if self.resumo_coluna:
            valores_resumo = []
            for idx, c in enumerate(self.colunas):
                opcao = self.resumo_coluna.get(c)
                if not opcao:
                    valores_resumo.append("")
                    continue
                numeros = [n for n in (_converter(linha[idx], "Float") for _iid, linha in visiveis)
                           if isinstance(n, (int, float))]
                if not numeros:
                    valores_resumo.append("(n/d)")
                elif opcao == "Soma":
                    valores_resumo.append(f"Soma={sum(numeros):g}")
                elif opcao == "Média":
                    valores_resumo.append(f"Média={sum(numeros) / len(numeros):g}")
            linhas_modelo.append((IID_RESUMO, valores_resumo, True))

        modelo = self.view.model()
        modelo.beginResetModel()
        self._visiveis = linhas_modelo
        modelo.endResetModel()
        self.conteudoAtualizado.emit()

    # ---------------- ordenação ----------------

    def _ordenar(self, indice_coluna):
        if indice_coluna < 0 or indice_coluna >= len(self.colunas):
            return
        coluna = self.colunas[indice_coluna]
        tipo = self.tipos_coluna[coluna]
        crescente = not (self._ordem_coluna == coluna and self._ordem_crescente)
        self._ordem_coluna = coluna
        self._ordem_crescente = crescente

        def chave(par):
            _iid, linha = par
            valor = _converter(linha[indice_coluna], tipo)
            return (valor is None, valor if valor is not None else "")

        self.linhas_originais.sort(key=chave, reverse=not crescente)
        self._atualizar_exibicao()

    # ---------------- menu do cabeçalho: filtro + tipo + resumo ----------------

    def _menu_coluna(self, pos):
        indice = self.view.horizontalHeader().logicalIndexAt(pos)
        if indice < 0 or indice >= len(self.colunas):
            return
        coluna = self.colunas[indice]

        menu = QMenu(self)
        rotulo_filtro = "Filtrar..." if not self.filtros_coluna.get(coluna) else "Filtrar... (ativo)"
        menu.addAction(rotulo_filtro, lambda: self._pedir_filtro(coluna))
        acao_limpar = menu.addAction(
            "Limpar filtro desta coluna", lambda: self._definir_filtro(coluna, ""))
        acao_limpar.setEnabled(bool(self.filtros_coluna.get(coluna)))
        menu.addSeparator()

        submenu_tipo = menu.addMenu("Tipo da coluna")
        for tipo in TIPOS:
            marca = "● " if self.tipos_coluna[coluna] == tipo else "    "
            submenu_tipo.addAction(f"{marca}{tipo}", lambda t=tipo: self._definir_tipo(coluna, t))

        submenu_resumo = menu.addMenu("Resumir coluna")
        atual = self.resumo_coluna.get(coluna, "Nenhum")
        for opcao in ("Nenhum", "Soma", "Média"):
            marca = "● " if atual == opcao else "    "
            submenu_resumo.addAction(f"{marca}{opcao}", lambda o=opcao: self._definir_resumo(coluna, o))

        menu.exec(self.view.horizontalHeader().mapToGlobal(pos))

    def _pedir_filtro(self, coluna):
        termo, ok = QInputDialog.getText(
            self, f"Filtrar '{coluna}'",
            "Mostrar só linhas em que esta coluna contém:",
            text=self.filtros_coluna.get(coluna, ""))
        if not ok:
            return  # cancelou
        self._definir_filtro(coluna, termo)

    def _definir_filtro(self, coluna, termo):
        termo = termo.strip()
        if termo:
            self.filtros_coluna[coluna] = termo
        else:
            self.filtros_coluna.pop(coluna, None)
        self._atualizar_exibicao()

    def _definir_tipo(self, coluna, tipo):
        self.tipos_coluna[coluna] = tipo
        self._atualizar_exibicao()

    def _definir_resumo(self, coluna, opcao):
        if opcao == "Nenhum":
            self.resumo_coluna.pop(coluna, None)
        else:
            self.resumo_coluna[coluna] = opcao
        self._atualizar_exibicao()
