# -*- coding: utf-8 -*-
"""
Construtor de Variáveis: editor visual (estilo grafo de nós, tipo ComfyUI) pra
montar colunas novas a partir dos modelos cadastrados em "Modelos"
(app/screens/modelos.py) sem escrever código — arrasta uma coluna de uma tabela
de origem e um modelo pro canvas, liga a saída da coluna numa das "Variáveis
(x)" declaradas no modelo, e o modelo produz uma saída que também pode virar
entrada de outro modelo (encadeamento) ou ser marcada pra virar coluna nova.

O grafo (nós + ligações) é salvo em `construtores_variaveis` — "Salvar
construtor" persiste E aplica na hora; depois disso, toda vez que a tabela de
origem for regenerada do zero (ex: "Gerar simulação" recria
simulacao_talhao_idade), o construtor salvo é reaplicado automaticamente (ver
core/construtores.py:aplicar_construtores_salvos, chamado em
app/screens/simulacao.py) — sem isso, as colunas geradas aqui sumiriam a cada
nova simulação. A avaliação de fato da equação é feita por core/motor_modelos.py
via core/construtores.py (módulo compartilhado com a reaplicação automática).

Porte completo de app/screens/construtor_variaveis.py (Tkinter, tk.Canvas
redesenhado por inteiro a cada interação + hit-testing manual via tags). Em
Qt, o canvas vira um QWidget comum (`_CanvasConstrutor`) com paintEvent
customizado (QPainter) — continua immediate-mode (redesenha tudo a cada
`self.canvas.update()`) e hit-testing manual (`_item_no_ponto`), só que
contra geometria calculada na hora em vez de tags de item de Canvas. Zoom/pan
são a MESMA matemática do original (mundo <-> tela), só trocando eventos Tk
por QMouseEvent/QWheelEvent.
"""
import json
import math
import time

import pandas as pd
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QHBoxLayout,
    QDoubleSpinBox, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)

from ..core import construtores, preferencias, projeto, simulacao
from ..core.db import conectar
from ..core.importador import NOME_TABELA_BASE_IFC, NOME_TABELA_BASE_IFC_BYTREE
from ..theme import icones
from ..theme import manager as tema
from ..theme import qss as tema_qss
from ..widgets.tabela import Tabela
from .base import TelaBase

TABELAS_ORIGEM = [simulacao.TABELA_POPULACAO, NOME_TABELA_BASE_IFC, NOME_TABELA_BASE_IFC_BYTREE]

PREVIEW_LINHAS = 200

LARGURA_NO = 170
ALTURA_COLUNA = 40
# 44 (não 26) — dá espaço pro cabeçalho "Tipo:" + campo estilo input +
# até 1 linha extra (ver _campo_nome_no/_desenhar_campo_nome, que por isso
# limita cada nó a NO MÁXIMO 1 linha extra) sem espremer a 1ª linha de
# entrada/saída logo abaixo.
ALTURA_TITULO_MODELO = 44
ALTURA_POR_ENTRADA = 20
RAIO_PINO = 6
RAIO_CANTO_NO = 8

COR_SELECAO = "#3a8fd4"
COR_GRUPO_PADRAO = "#888888"

COR_COLUNA = "#3a6ea5"
COR_CLASSE_DIAMETRICA = "#b8860b"
COR_MODELO = "#a5583a"
COR_SAIDA = "#3a7d44"
COR_SAIDA_NAO_GRAVA = "#8fae94"
COR_CALCULO = "#7a4fa0"
COR_CALCULO_NAO_GRAVA = "#b7a0cc"
COR_DISTRIBUICAO = "#1f7a6c"
COR_ACUMULADO = "#2f5f8a"
COR_ACUMULADO_NAO_GRAVA = "#8fa8c2"
COR_RENDIMENTO_SORTIMENTO = "#4a7a5a"
COR_RENDIMENTO_SORTIMENTO_NAO_GRAVA = "#a3c2ac"
COR_RECEITA_SORTIMENTO = "#8a6d1f"
COR_RECEITA_SORTIMENTO_NAO_GRAVA = "#c2b98f"
COR_VPL_SORTIMENTO = "#a02040"
COR_VPL_SORTIMENTO_NAO_GRAVA = "#c98fa3"
COR_VET_SORTIMENTO = "#6a2c91"
COR_VET_SORTIMENTO_NAO_GRAVA = "#b79bc9"
COR_AFILAMENTO = "#1f5c8a"
COR_AFILAMENTO_NAO_GRAVA = "#8fadc2"
COR_RECUPERACAO_WEIBULL = "#3a8a6d"
COR_RECUPERACAO_WEIBULL_NAO_GRAVA = "#9bc4b2"
COR_CUSTO_COLHEITA = "#8a3a3a"
COR_CUSTO_COLHEITA_NAO_GRAVA = "#c2a3a3"
COR_CUSTO_FORMACAO = "#a56a1f"
COR_CUSTO_FORMACAO_NAO_GRAVA = "#c9ac8f"
COR_FIO = "#555555"
COR_FIO_TEMP = "#aaaaaa"
COR_PINO_LIVRE = "#ffffff"
COR_PINO_LIGADO = "#2e7d32"
COR_PINO_VAZIO_SAIDA = "#dddddd"
COR_OPERADOR = "#ffffff"

_CORES_BASE = {
    "coluna": COR_COLUNA, "classe_diametrica": COR_CLASSE_DIAMETRICA,
    "modelo": COR_MODELO, "saida": COR_SAIDA, "calculo": COR_CALCULO,
    "distribuicao": COR_DISTRIBUICAO, "acumulado": COR_ACUMULADO,
    "rendimento_sortimento": COR_RENDIMENTO_SORTIMENTO,
    "receita_sortimento": COR_RECEITA_SORTIMENTO, "vpl_sortimento": COR_VPL_SORTIMENTO,
    "vet_sortimento": COR_VET_SORTIMENTO, "afilamento": COR_AFILAMENTO,
    "recuperacao_weibull": COR_RECUPERACAO_WEIBULL, "custo_colheita": COR_CUSTO_COLHEITA,
    "custo_formacao": COR_CUSTO_FORMACAO,
}
_CORES_NAO_GRAVA = {
    "saida": COR_SAIDA_NAO_GRAVA, "calculo": COR_CALCULO_NAO_GRAVA, "acumulado": COR_ACUMULADO_NAO_GRAVA,
    "rendimento_sortimento": COR_RENDIMENTO_SORTIMENTO_NAO_GRAVA,
    "receita_sortimento": COR_RECEITA_SORTIMENTO_NAO_GRAVA, "vpl_sortimento": COR_VPL_SORTIMENTO_NAO_GRAVA,
    "vet_sortimento": COR_VET_SORTIMENTO_NAO_GRAVA, "afilamento": COR_AFILAMENTO_NAO_GRAVA,
    "recuperacao_weibull": COR_RECUPERACAO_WEIBULL_NAO_GRAVA,
    "custo_colheita": COR_CUSTO_COLHEITA_NAO_GRAVA, "custo_formacao": COR_CUSTO_FORMACAO_NAO_GRAVA,
}

# "ou" não é aritmético (ver core/construtores.py:_OPERADORES) — mescla
# duas entradas de uma "Saída" usando a 1ª onde tiver valor, senão a 2ª;
# pensado pra juntar, na mesma coluna por classe, duas saídas de nós
# financeiros configuradas pra eventos diferentes (ex: VET só de Corte
# Raso + VET só de Raleio/Desbaste).
OPERADORES = ["+", "-", "*", "/", "^", "ou"]
SIMBOLO_OPERADOR = {"+": "+", "-": "−", "*": "×", "/": "÷", "^": "^", "ou": "OU"}
_ROTULO_OPERADOR_MENU = {"ou": "OU (usa a 1ª que tiver valor, senão a 2ª)"}

ROTULO_REDUCAO_CLASSE = {"soma_classes": "Σ todas as classes", "media_classes": "x̄ todas as classes"}

ZOOM_MINIMO = 0.3
ZOOM_MAXIMO = 3.0
ZOOM_PASSO_RODA = 1.1
ZOOM_PASSO_BOTAO = 1.25

_ROTULOS_SAIDA = {
    "afilamento": ("Aproveitável", "Biomassa"),
    "recuperacao_weibull": ("Forma", "Escala"),
}


def _rotulo_passo(passo):
    if "valor" in passo:
        return f"{SIMBOLO_OPERADOR[passo['operador']]} {passo['valor']:g}"
    return ROTULO_REDUCAO_CLASSE[passo["operador"]]


def _n_saidas(no):
    """Nós comuns têm 1 saída; "afilamento" e "recuperacao_weibull" têm 2
    (Aproveitável/Biomassa e Forma/Escala — ver core/construtores.py:
    avaliar_grafo)."""
    return 2 if no["tipo"] in ("afilamento", "recuperacao_weibull") else 1


def _n_entradas(no):
    """Quantos pinos de ENTRADA o nó desenha — usado pro hit-test e pro
    desenho dos pinos."""
    if no["tipo"] in ("coluna", "classe_diametrica"):
        return 0
    if no["tipo"] == "saida":
        return len(no["entradas"]) + 1  # +1: pino vazio sempre disponível no fim
    if no["tipo"] in ("modelo", "distribuicao", "vpl_sortimento", "recuperacao_weibull"):
        return len(no["variaveis"])
    return 1  # calculo, acumulado, rendimento/receita/vet_sortimento, afilamento, custo_formacao


def _cor_no(no):
    # "cor_fundo" — override individual do nó (botão direito > "Cor do
    # nó..."), tem prioridade sobre a cor automática por tipo/"gravar".
    if no.get("cor_fundo"):
        return no["cor_fundo"]
    if not no.get("gravar", True) and no["tipo"] in _CORES_NAO_GRAVA:
        return _CORES_NAO_GRAVA[no["tipo"]]
    return _CORES_BASE[no["tipo"]]


def _distancia_ponto_segmento(px, py, ax, ay, bx, by):
    """Distância de (px,py) ao segmento (ax,ay)-(bx,by) — usado pro
    hit-test de "clicar perto de um fio" (ver _item_no_ponto)."""
    dx, dy = bx - ax, by - ay
    comprimento2 = dx * dx + dy * dy
    if comprimento2 <= 1e-9:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / comprimento2))
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _rotulo_modelo(nome, variantes):
    """Nome exibido pra um modelo na lista de origem e no rótulo do nó no
    canvas. Modelos cadastrados em Modelos com o mesmo nome (uma linha
    por estrato) são agrupados num nó só — o rótulo mostra a coluna do
    estrato e quantas variantes foram agrupadas."""
    if len(variantes) == 1 and not variantes[0].get("estrato_coluna"):
        return nome
    colunas_estrato = {v.get("estrato_coluna") for v in variantes if v.get("estrato_coluna")}
    if len(colunas_estrato) == 1:
        coluna = next(iter(colunas_estrato))
        return f"{nome} — {coluna} ({len(variantes)} estratos)"
    return f"{nome} ({len(variantes)} variantes)"


def _titulo_no_automatico(no):
    """Texto (multi-linha) gerado a partir do tipo/nome de saída/estado de
    "gravar" do nó — porte literal do bloco if/elif de
    app/screens/construtor_variaveis.py:_desenhar_no. Ver _titulo_no, que
    prepende o nome personalizado (se houver) em cima disto."""
    tipo = no["tipo"]
    if tipo == "saida":
        nome = no.get("nome_saida")
        if not nome:
            return "Saída (sem nome)"
        return f"Saída: {nome}" if no.get("gravar", True) else f"Saída: {nome}\n(não grava)"
    if tipo == "calculo":
        nome = no.get("nome_saida")
        if not nome:
            return "Cálculo"
        return f"Cálculo: {nome}" if no.get("gravar", True) else f"Cálculo: {nome}\n(não grava)"
    if tipo == "rendimento_sortimento":
        nome = no.get("nome_saida")
        if not nome:
            return "Rendimento Serraria"
        return (f"Rendimento Serraria: {nome}" if no.get("gravar", True)
                else f"Rendimento Serraria: {nome}\n(não grava)")
    if tipo == "receita_sortimento":
        nome = no.get("nome_saida")
        if not nome:
            return "Receita Total"
        return (f"Receita Total: {nome}" if no.get("gravar", True)
                else f"Receita Total: {nome}\n(não grava)")
    if tipo == "vpl_sortimento":
        nome = no.get("nome_saida")
        if not nome:
            return "VPL"
        return (f"VPL: {nome}" if no.get("gravar", True)
                else f"VPL: {nome}\n(não grava)")
    if tipo == "vet_sortimento":
        nome = no.get("nome_saida")
        if not nome:
            return "VET"
        return (f"VET: {nome}" if no.get("gravar", True)
                else f"VET: {nome}\n(não grava)")
    if tipo == "afilamento":
        nome_a = no.get("nome_saida_aproveitavel")
        nome_b = no.get("nome_saida_biomassa")
        titulo = f"Afilamento: {no.get('nome', '')}"
        if nome_a or nome_b:
            titulo += f"\n→ {nome_a or '?'} / {nome_b or '?'}"
        if not no.get("gravar", True):
            titulo += "\n(não grava)"
        return titulo
    if tipo == "recuperacao_weibull":
        nome_f = no.get("nome_saida_forma")
        nome_e = no.get("nome_saida_escala")
        titulo = "Recuperação Weibull"
        if nome_f or nome_e:
            titulo += f"\n→ {nome_f or '?'} / {nome_e or '?'}"
        if not no.get("gravar", True):
            titulo += "\n(não grava)"
        return titulo
    titulo = no["rotulo"]
    if no.get("nome_saida"):
        if no.get("gravar", True):
            titulo += f"\n→ {no['nome_saida']}"
        else:
            titulo += f"\n→ {no['nome_saida']} (não grava)"
    return titulo


# Rótulo do "Tipo:" desenhado em cima do campo estilo input (ver
# _campo_nome_no/_desenhar_campo_nome) — só os tipos daqui entram nesse
# desenho; os demais (ex: "classe_diametrica", que não tem nome próprio)
# caem de volta no título de sempre (_titulo_no_automatico).
_ROTULO_TIPO_NO = {
    "coluna": "Coluna", "modelo": "Modelo", "afilamento": "Taper", "saida": "Saída",
    "calculo": "Cálculo", "distribuicao": "Distribuição", "acumulado": "Acumulado",
    "rendimento_sortimento": "Rendimento Serraria", "receita_sortimento": "Receita Total",
    "vpl_sortimento": "VPL", "vet_sortimento": "VET", "custo_colheita": "Custo Colheita",
    "custo_formacao": "Custo Formação",
}

# Tipos com um checkbox "Gravar na tabela" clicável direto no corpo do nó
# (ver _desenhar_checkbox_gravar/_item_no_ponto, tipo "gravar") — os mesmos
# que já tinham esse toggle no menu de botão direito, exceto "coluna"
# (não tem saída própria) e "modelo"/"recuperacao_weibull" (mantidos só no
# menu — o corpo já usa a única linha extra disponível pra outra coisa,
# ver _campo_nome_no).
_TIPOS_COM_GRAVAR_NO_CORPO = frozenset((
    "saida", "calculo", "distribuicao", "acumulado", "receita_sortimento",
    "rendimento_sortimento", "vpl_sortimento", "vet_sortimento", "custo_colheita",
    "custo_formacao", "afilamento",
))


def _campo_nome_no(no):
    """(rótulo do tipo, [valor(es) pra mostrar num campo estilo input],
    [linhas extras abaixo]) — None quando o nó não tem um "nome" próprio
    pra mostrar assim (cai de volta no título simples de sempre, ver
    _titulo_no_automatico). Usado por _desenhar_no/_desenhar_campo_nome.
    Estado de "gravar" NÃO entra mais aqui como texto — nos tipos de
    _TIPOS_COM_GRAVAR_NO_CORPO ele vira um checkbox clicável desenhado por
    cima (ver _desenhar_checkbox_gravar), que ocupa a mesma linha que essa
    função reservaria pra uma linha extra de texto."""
    tipo = no["tipo"]
    rotulo_tipo = _ROTULO_TIPO_NO.get(tipo)
    if rotulo_tipo is None:
        return None
    if tipo == "coluna":
        return rotulo_tipo, [no.get("coluna", "")], []
    if tipo == "modelo":
        extra = [f"{len(no['variantes'])} estratos"] if len(no.get("variantes", [])) > 1 else []
        return rotulo_tipo, [no.get("nome", "")], extra
    if tipo == "afilamento":
        return rotulo_tipo, [no.get("nome", "")], []
    valor = no.get("nome_saida") or "(sem nome)"
    return rotulo_tipo, [valor], []


class _CanvasConstrutor(QWidget):
    """Superfície de desenho/eventos — só encaminha pra TelaConstrutorVariaveis
    (que guarda TODO o estado: nós, conexões, zoom/pan), mesmo espírito do
    `tk.Canvas` original (um widget "burro", a lógica mora na tela)."""

    def __init__(self, tela, parent=None):
        super().__init__(parent)
        self._tela = tela
        self.setMinimumHeight(200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

    def paintEvent(self, _evento):
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._tela._pintar(pintor)
        pintor.end()

    def mousePressEvent(self, evento):
        if evento.button() == Qt.MouseButton.LeftButton:
            self._tela._ao_pressionar(evento)
        elif evento.button() == Qt.MouseButton.MiddleButton:
            self._tela._iniciar_pan(evento)
        elif evento.button() == Qt.MouseButton.RightButton:
            self._tela._ao_botao_direito(evento)

    def mouseMoveEvent(self, evento):
        if evento.buttons() & Qt.MouseButton.LeftButton:
            self._tela._ao_arrastar(evento)
        elif evento.buttons() & Qt.MouseButton.MiddleButton:
            self._tela._mover_pan(evento)

    def mouseReleaseEvent(self, evento):
        if evento.button() == Qt.MouseButton.LeftButton:
            self._tela._ao_soltar(evento)
        elif evento.button() == Qt.MouseButton.MiddleButton:
            self._tela._parar_pan(evento)

    def mouseDoubleClickEvent(self, evento):
        if evento.button() == Qt.MouseButton.LeftButton:
            self._tela._ao_duplo_clique(evento)

    def wheelEvent(self, evento):
        self._tela._ao_rolar(evento)


class TelaConstrutorVariaveis(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.nos = {}
        self.conexoes = []
        self._proximo_id = 1
        self._contador_posicoes = 0

        # Caixas de agrupamento (retângulo pontilhado, botão direito no
        # canvas vazio) — arrastar pela faixa de título move junto todo nó
        # cuja posição estiver dentro da caixa (ver _iniciar_arraste_grupo).
        self.grupos = {}
        self._proximo_grupo_id = 1

        # Seleção múltipla de nós (clique arrasta uma "borracha" no canvas
        # vazio pra selecionar por área; Shift-clique acrescenta/remove um
        # nó da seleção) — arrastar qualquer nó selecionado move todos
        # juntos (ver _iniciar_arraste_nos/_ao_arrastar).
        self._selecionados = set()
        self._selecao_retangulo = None  # (x0, y0, x1, y1) em coord. de mundo, durante o arraste
        self._arrastando_nos = False
        self._arraste_inicio_mundo = None
        self._posicoes_iniciais_arraste = {}
        self._arrastando_grupo = None
        self._grupo_posicao_inicial = None
        self._redimensionando_grupo = None
        self._redim_inicio = None
        self._arrastando_fio = None  # índice em self.conexoes sendo "curvado" (ver "dobra")

        self._pino_origem = None
        self._fio_temp_destino = None

        # Zoom/pan do canvas: transformação manual (mundo -> tela) reaplicada
        # a cada _pintar (chamado a cada repaint), em vez de depender de
        # scroll nativo — ver _mundo_para_tela/_tela_para_mundo.
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._pan_arraste_inicio = None

        self._colunas_disponiveis = []
        self._modelos_disponiveis = []
        self._modelos_gerais_disponiveis = []
        self._afilamento_disponiveis = []
        self._nome_por_modelo_id = {}
        self._construtores_disponiveis = []
        self.construtor_atual_id = None
        self._canvas_maximizado = False

        # Cor da borda dos nós/pinos no canvas — preferência de usuário/
        # máquina (ver core/preferencias.py), não do projeto; cacheada aqui
        # pra não reler o preferencias.json a cada repaint (paintEvent roda
        # a cada interação) — só recarregada quando o usuário troca a cor
        # (ver _escolher_cor_borda_no).
        self._cor_borda_no = QColor(preferencias.obter_cor_borda_no())
        self._espessura_borda_no = preferencias.obter_espessura_borda_no()

        layout_raiz = QVBoxLayout(self)
        layout_raiz.setContentsMargins(8, 8, 8, 8)

        # Painel lateral (fontes de nó + construtores salvos) foi
        # substituído por menus flutuantes/botões na barra acima do canvas
        # (ver _abrir_menu_colunas_disponiveis/_abrir_menu_modelos/
        # _abrir_menu_nos_especiais/_abrir_menu_construtores) — sem ele,
        # sobra só canvas + prévia, sem precisar de QSplitter.
        layout_raiz.addWidget(self._montar_painel_principal())

        tema.obter().themeChanged.connect(lambda _modo: self.canvas.update())

        self.recarregar_lista()

    # ---------------- nós especiais: menu flutuante a partir da barra de zoom ----------------

    def _definir_nos_especiais(self):
        """(rótulo, callback, dica) de cada nó especial — usado por
        _abrir_menu_nos_especiais pra montar o menu flutuante que abre a
        partir do botão "Nós Especiais" na barra acima do canvas (clicar
        num item do menu já adiciona o nó, mesmo callback de antes, só
        que agora escolhido num menu em vez de um botão fixo no painel
        lateral — a lista de nós especiais cresceu demais pra caber ali
        sem rolagem)."""
        return [
            ("Classe Diamétrica", self._adicionar_no_classe_diametrica, (
                "Fonte de dado, igual uma Coluna: liga por fio numa entrada de um nó Modelo. "
                "Isso faz o modelo ser calculado uma vez pra cada classe diamétrica (primeira "
                "até a última, configuradas em Configurações), gerando uma coluna de saída "
                "por classe em vez de uma só.")),
            ("Distribuição Diamétrica", self._adicionar_no_distribuicao, (
                "Probabilidade por classe diamétrica (a mesma FDP da Weibull usada em \"Gerar "
                "simulação\"): liga \"forma\" e \"escala\" (um valor só por linha, ex: "
                "forma_atual/escala_atual) e o nó calcula, pra cada classe configurada em "
                "Configurações, a probabilidade entre -0,5 e +0,5 do centro da classe — uma "
                "coluna de saída por classe, pronta pra virar entrada de outro nó (ex: "
                "multiplicar pela contagem de árvores).")),
            ("Recuperação Weibull (Momentos)", self._adicionar_no_recuperacao_weibull, (
                "Recupera forma/escala da Weibull casando média e CV (em vez de regredir "
                "forma/escala cada um por si): liga \"media\" (ex: dap_med_atual) e \"cv\" "
                "(ex: cv_dap_atual) — os dois já devem estar previstos/localizados na "
                "idade-alvo antes de chegar aqui. Garante que a distribuição recuperada bate "
                "exatamente com a média ligada, ao contrário de duas regressões "
                "independentes. Duas saídas nomeadas: Forma e Escala, configuráveis pelo "
                "botão direito no nó.")),
            ("Acumulado", self._adicionar_no_acumulado, (
                "Soma acumulada de uma entrada, agrupada por uma coluna da base (ex: talhão) "
                "e ordenada por outra (ex: idade simulada) — não são ligadas por fio, "
                "configure pelo botão direito no nó depois de criado. Útil pra \"carregar\" "
                "um valor entre linhas do mesmo grupo (ex: somar o VTCC removido em todos os "
                "eventos de manejo anteriores do mesmo talhão, pra chegar no volume líquido = "
                "VTCC em pé + esse acumulado).")),
            ("Rendimento Serraria", self._adicionar_no_rendimento_sortimento, (
                "Liga uma entrada por classe diamétrica (ex: a saída Aproveitável de um nó "
                "Afilamento) e multiplica cada classe pelo rendimento (tela Configurações, "
                "percentual — 30 cadastrado quer dizer 30%, já convertido pra fração aqui) do "
                "sortimento cuja faixa cobre aquela classe — o resultado continua uma coluna "
                "por classe, agora em volume de produto.")),
            ("Receita Total", self._adicionar_no_receita_sortimento, (
                "Liga uma entrada por classe diamétrica (ex: VTCC de um Modelo ligado em "
                "Classe Diamétrica) e multiplica cada classe pelo preço (tela Configurações) "
                "do sortimento cuja faixa cobre aquela classe — o resultado continua uma "
                "coluna por classe, agora em receita. Botão direito no nó (depois de criado) "
                "escolhe qual dos dois preços cadastrados usar: Madeira Serrada (padrão — "
                "produto já desdobrado) ou Madeira em Pé (árvore em pé antes da colheita/"
                "desdobro).")),
            ("VPL", self._adicionar_no_vpl_sortimento, (
                "Liga \"rt\" (ex: a saída de um Receita Total) e \"periodo\" (um valor só por "
                "linha) e calcula RT/(1+taxa)^n — taxa de desconto vem da tela Configurações. "
                "\"rt\" aceita tanto uma entrada por classe diamétrica (resultado também por "
                "classe) quanto já agregada, uma Series só (resultado também uma Series só) — a "
                "conta é a mesma nos dois casos. O que \"n\" significa, o que ligar em "
                "\"periodo\", e se desconta PIS+COFINS, depende de \"Base do período do VPL\" "
                "(Configurações): \"Ano de referência\" (padrão) — liga ano_simulado, n = "
                "ano_simulado - ano_referência (sem valor absoluto: receita ANTES do ano de "
                "referência é composta pra frente em vez de descontada), E desconta a fração de "
                "PIS+COFINS (RT/(1+taxa)^n * (1-pis-cofins), pressupõe receita de venda "
                "tributável); \"Ano Zero\" — liga idade_simulada, VPL = RT/(1+taxa)^idade_simulada "
                "(desconta contra o plantio do talhão, não um ano-calendário fixo), SEM "
                "PIS+COFINS — não precisa de ano de referência nem PIS/COFINS configurados nesse "
                "modo.")),
            ("VET", self._adicionar_no_vet_sortimento, (
                "Liga \"vpl\" (ex: a saída de um VPL) e calcula "
                "VPL/(1-(1+taxa)^-idade_corte_raso) — taxa de desconto vem da tela "
                "Configurações, idade do Corte Raso vem da última \"Gerar simulação\" (tela "
                "Simulação, Eventos de manejo). Aceita \"vpl\" por classe diamétrica ou já "
                "agregado, igual VPL — resultado no mesmo formato da entrada.")),
            ("Custo de Colheita", self._adicionar_no_custo_colheita, (
                "Liga uma entrada por classe diamétrica (ex: volume de alguma operação) e "
                "multiplica cada classe pelo Custo Efetivo (R$/m³) — Custo Hora Máquina / "
                "(Produtividade da classe × Disponibilidade Mecânica × Eficiência Operacional) "
                "— do custo de colheita cadastrado em Configurações (harvester, motosserra "
                "etc, o que estiver cadastrado) escolhido pelo botão direito no nó depois de "
                "criado. Resultado também por classe.")),
            ("Custo de Formação", self._adicionar_no_custo_formacao, (
                "Soma (R$/ha) todo custo de formação florestal (tela Configurações — preparo de "
                "solo, plantio, manutenção etc, cada um com um ano de idade do povoamento) cujo "
                "ano bate com \"idade_simulada\" da linha, 0 nas idades sem custo cadastrado — só "
                "funciona rodando sobre a população simulada (simulacao_talhao_idade), a única "
                "tabela com essa coluna. Tem um pino de entrada OPCIONAL: se ligado (ex: um "
                "\"Coluna\" de área do talhão), multiplica o custo/ha de cada idade por esse "
                "valor antes de gravar a saída — sem nada ligado, sai só o custo/ha, igual antes. "
                "A entrada precisa ser um valor por linha (não por classe diamétrica) e continua "
                "valendo nas linhas de formação (idade <= 0, ver abaixo) mesmo se vier de um "
                "\"Coluna\"/cadeia que normalmente seria zerada ali. Ao contrário de Custo de "
                "Colheita, não é mascarado por evento de manejo (a formação acontece pela idade, "
                "não por um evento específico). Ter esse nó no grafo faz \"Salvar construtor\"/"
                "\"Gerar simulação\" criarem, por talhão, uma linha nova de idade <= 0 pra cada "
                "custo com ano negativo/zero cadastrado — custo incorrido ANTES da 1ª idade "
                "simulada (idade_simulada=1). Essas linhas só têm talhão/idade/ano/"
                "custo_formacao preenchidos, mais qualquer coluna original da Base IFC ByTalhao "
                "(ex: área) repetida por talhão; por padrão, TODO OUTRO nó do grafo (fora a "
                "entrada deste nó) vira NaN nelas (botão direito: \"Excluir idades de formação "
                "de outros cálculos\", ligado por padrão — desligue só com motivo específico). "
                "Nome de saída já vem preenchido com \"custo_formacao\" — mantenha esse nome pra "
                "alimentar \"Volume\" na tela Simulação do jeito de sempre; renomeie (botão "
                "direito) só se for usar o valor como entrada de outro nó.")),
        ]

    def _abrir_menu_nos_especiais(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        # Taper (Afilamento) é o único nó especial que exige escolher UM
        # modelo (cadastrado em Modelos, tipo "Afilamento / Taper") antes
        # de criar o nó — por isso vira um submenu (">>"), não uma ação
        # direta como os outros: cada modelo cadastrado é 1 item, clicar
        # já adiciona o nó com aquele modelo (equivalente a selecionar na
        # lista antiga + "Adicionar nó de Afilamento", agora num passo só).
        submenu_taper = menu.addMenu("Taper")
        if not self._afilamento_disponiveis:
            acao_vazia = submenu_taper.addAction(
                "Nenhum modelo \"Afilamento / Taper\" cadastrado (tela Modelos)")
            acao_vazia.setEnabled(False)
        else:
            for grupo in self._afilamento_disponiveis:
                rotulo = _rotulo_modelo(grupo["nome"], grupo["variantes"])
                submenu_taper.addAction(rotulo, lambda g=grupo: self._adicionar_no_afilamento(g))
        submenu_taper.setToolTip(
            "Só modelos cadastrados em Modelos com tipo \"Afilamento / Taper\" e exatamente 3 "
            "Variáveis (x), lidas por posição: DAP (vem da classe diamétrica, sem fio), h "
            "(varrida internamente de 0 até Ht em passos de 0,1 m, sem fio) e H/Ht — único pino "
            "de entrada do nó, precisa vir de um Modelo hipsométrico ligado em Classe "
            "Diamétrica. Gera duas saídas por classe: volume aproveitável (toras inteiras, "
            "segundo Comprimento/Diâmetro mínimo da tora em Configurações) e volume de "
            "biomassa (resíduo = total do fuste menos aproveitável).")
        menu.addSeparator()

        for rotulo, callback, dica in self._definir_nos_especiais():
            acao = menu.addAction(rotulo, callback)
            acao.setToolTip(dica)
        botao = self.botao_nos_especiais
        menu.exec(botao.mapToGlobal(botao.rect().bottomLeft()))

    def _abrir_menu_colunas_disponiveis(self):
        """Menu flutuante com as colunas da tabela de origem escolhida no
        combo ao lado (ver _atualizar_colunas_disponiveis) — clicar numa
        coluna já adiciona um nó "coluna" pra ela, mesmo efeito de
        selecionar na lista antiga + "Adicionar coluna", num passo só."""
        menu = QMenu(self)
        if not self._colunas_disponiveis:
            acao_vazia = menu.addAction("Nenhuma coluna disponível nesta tabela")
            acao_vazia.setEnabled(False)
        else:
            for nome_coluna in self._colunas_disponiveis:
                menu.addAction(nome_coluna, lambda nome=nome_coluna: self._adicionar_no_coluna(nome))
        botao = self.botao_colunas_disponiveis
        menu.exec(botao.mapToGlobal(botao.rect().bottomLeft()))

    def _abrir_menu_modelos(self):
        """Menu flutuante com os modelos cadastrados na tela Modelos (ver
        _atualizar_modelos_disponiveis) — exceto os do tipo "Afilamento /
        Taper", que ficam no submenu "Taper" do botão "Nós Especiais" (ver
        _abrir_menu_nos_especiais). Clicar num modelo já adiciona o nó
        "modelo" pra ele."""
        menu = QMenu(self)
        if not self._modelos_gerais_disponiveis:
            acao_vazia = menu.addAction("Nenhum modelo cadastrado (tela Modelos)")
            acao_vazia.setEnabled(False)
        else:
            for grupo in self._modelos_gerais_disponiveis:
                rotulo = f"{_rotulo_modelo(grupo['nome'], grupo['variantes'])} ({grupo['tipo']})"
                menu.addAction(rotulo, lambda g=grupo: self._adicionar_no_modelo(g))
        botao = self.botao_modelos_cadastrados
        menu.exec(botao.mapToGlobal(botao.rect().bottomLeft()))


    # ---------------- painel principal: canvas + prévia ----------------

    @staticmethod
    def _botao_icone(nome_icone, dica):
        botao = QPushButton()
        icones.aplicar_icone(botao, nome_icone)
        botao.setToolTip(dica)
        return botao

    def _montar_painel_principal(self):
        self._splitter_vertical = QSplitter(Qt.Orientation.Vertical)

        area_canvas = QWidget()
        layout_canvas = QVBoxLayout(area_canvas)
        layout_canvas.setContentsMargins(0, 0, 0, 0)

        # Só ícone (sem texto) — o rótulo que cada botão tinha antes vira
        # tooltip (ver _botao_icone), pra barra ficar compacta.
        barra_zoom = QHBoxLayout()
        self.combo_tabela_origem = QComboBox()
        self.combo_tabela_origem.addItems(TABELAS_ORIGEM)
        self.combo_tabela_origem.textActivated.connect(lambda _t: self._atualizar_colunas_disponiveis())
        barra_zoom.addWidget(self.combo_tabela_origem)
        self.botao_colunas_disponiveis = self._botao_icone("colunas", "Colunas disponíveis")
        self.botao_colunas_disponiveis.clicked.connect(self._abrir_menu_colunas_disponiveis)
        barra_zoom.addWidget(self.botao_colunas_disponiveis)
        self.botao_modelos_cadastrados = self._botao_icone("modelos", "Modelos cadastrados")
        self.botao_modelos_cadastrados.clicked.connect(self._abrir_menu_modelos)
        barra_zoom.addWidget(self.botao_modelos_cadastrados)
        self.botao_nos_especiais = self._botao_icone("nos_especiais", "Nós Especiais")
        self.botao_nos_especiais.clicked.connect(self._abrir_menu_nos_especiais)
        barra_zoom.addWidget(self.botao_nos_especiais)
        botao_menos = self._botao_icone("zoom_menos", "− Zoom")
        botao_menos.clicked.connect(lambda: self._aplicar_zoom(1 / ZOOM_PASSO_BOTAO))
        barra_zoom.addWidget(botao_menos)
        botao_mais = self._botao_icone("zoom_mais", "+ Zoom")
        botao_mais.clicked.connect(lambda: self._aplicar_zoom(ZOOM_PASSO_BOTAO))
        barra_zoom.addWidget(botao_mais)
        botao_redefinir = self._botao_icone("redefinir", "Redefinir vista")
        botao_redefinir.clicked.connect(self._redefinir_vista)
        barra_zoom.addWidget(botao_redefinir)
        botao_cor_borda = self._botao_icone("cor_borda", "Cor da borda dos nós...")
        botao_cor_borda.clicked.connect(self._escolher_cor_borda_no)
        barra_zoom.addWidget(botao_cor_borda)
        self.botao_maximizar_canvas = self._botao_icone("maximizar", "Maximizar canvas")
        self.botao_maximizar_canvas.clicked.connect(self._alternar_maximizar_canvas)
        barra_zoom.addWidget(self.botao_maximizar_canvas)
        self.botao_construtores = self._botao_icone("abrir", "Construtores")
        self.botao_construtores.clicked.connect(self._abrir_dialogo_construtores)
        self.botao_construtores.setToolTip(
            "Gerenciar construtores salvos")
        barra_zoom.addWidget(self.botao_construtores)
        botao_limpar = self._botao_icone("limpar", "Limpar canvas")
        botao_limpar.clicked.connect(self._limpar_canvas)
        barra_zoom.addWidget(botao_limpar)
        botao_previa = self._botao_icone("previa", "Prévia")
        botao_previa.clicked.connect(self.testar)
        barra_zoom.addWidget(botao_previa)
        botao_salvar = self._botao_icone("salvar", "Salvar construtor")
        botao_salvar.clicked.connect(self.salvar_construtor)
        barra_zoom.addWidget(botao_salvar)
        botao_gerar_avulsa = self._botao_icone("gerar", "Gerar tabela nova (só desta vez)...")
        botao_gerar_avulsa.clicked.connect(self.gerar_tabela_nova_avulsa)
        barra_zoom.addWidget(botao_gerar_avulsa)
        barra_zoom.addStretch(1)
        layout_canvas.addLayout(barra_zoom)

        self.canvas = _CanvasConstrutor(self)
        layout_canvas.addWidget(self.canvas, 1)

        # Escondida até a 1ª "Prévia" (ver testar) — sem isso, fica um
        # espaço em branco ocupando a metade de baixo do canvas antes de
        # o usuário pedir alguma prévia. Reaparece a cada "Prévia" clicada
        # (mesmo se o usuário já tinha fechado no X); se já estiver
        # aberta, só atualiza o conteúdo (mostrar() de novo não faz nada).
        self._area_previa_widget = QWidget()
        layout_previa = QVBoxLayout(self._area_previa_widget)
        layout_previa.setContentsMargins(0, 8, 0, 0)
        linha_titulo_previa = QHBoxLayout()
        linha_titulo_previa.addWidget(QLabel("Prévia"))
        linha_titulo_previa.addStretch(1)
        botao_fechar_previa = QPushButton()
        icones.aplicar_icone(botao_fechar_previa, "fechar")
        botao_fechar_previa.setToolTip("Fechar prévia")
        botao_fechar_previa.setFlat(True)
        botao_fechar_previa.clicked.connect(self._area_previa_widget.hide)
        linha_titulo_previa.addWidget(botao_fechar_previa)
        layout_previa.addLayout(linha_titulo_previa)
        self.label_status = QLabel("")
        self.label_status.setWordWrap(True)
        layout_previa.addWidget(self.label_status)
        self.tabela_previa = Tabela(colunas=())
        layout_previa.addWidget(self.tabela_previa)
        self._area_previa_widget.hide()

        self._splitter_vertical.addWidget(area_canvas)
        self._splitter_vertical.addWidget(self._area_previa_widget)
        self._splitter_vertical.setStretchFactor(0, 3)
        self._splitter_vertical.setStretchFactor(1, 2)
        return self._splitter_vertical

    def _alternar_maximizar_canvas(self):
        """Alterna entre o layout normal e o canvas ocupando a janela
        inteira — some com a prévia e a barra de navegação/rodapé do app,
        deixando só o canvas e esta barra de zoom visíveis. É só
        visibilidade: zoom/pan e o grafo continuam intactos, dá pra
        restaurar clicando de novo no mesmo botão."""
        self._canvas_maximizado = not self._canvas_maximizado
        janela = self.window()
        if self._canvas_maximizado:
            # Lembra se a prévia estava aberta antes de maximizar — sem
            # isso, restaurar sempre reabriria ela (mesmo se o usuário
            # nunca tinha clicado em "Prévia", ou tinha fechado no X).
            self._previa_visivel_antes_maximizar = self._area_previa_widget.isVisible()
            self._area_previa_widget.hide()
            self.botao_maximizar_canvas.setToolTip("Restaurar layout")
            icones.aplicar_icone(self.botao_maximizar_canvas, "restaurar")
            if hasattr(janela, "definir_chrome_visivel"):
                janela.definir_chrome_visivel(False)
        else:
            self._area_previa_widget.setVisible(getattr(self, "_previa_visivel_antes_maximizar", False))
            self.botao_maximizar_canvas.setToolTip("Maximizar canvas")
            icones.aplicar_icone(self.botao_maximizar_canvas, "maximizar")
            if hasattr(janela, "definir_chrome_visivel"):
                janela.definir_chrome_visivel(True)

    # ---------------- ciclo de vida da tela ----------------

    def novo_registro(self):
        # Grafo é por projeto — trocar de projeto sem limpar misturaria nós
        # ligados a colunas/modelos que nem existem no projeto novo.
        self._limpar_canvas()

    def recarregar_lista(self):
        self._atualizar_colunas_disponiveis()
        self._atualizar_modelos_disponiveis()
        self._atualizar_afilamento_disponiveis()
        self._atualizar_construtores_disponiveis()

    def _atualizar_colunas_disponiveis(self):
        # Só popula self._colunas_disponiveis — sem lista própria no
        # painel lateral (ver _abrir_menu_colunas_disponiveis, que lê essa
        # lista na hora de montar o menu flutuante do botão "Colunas
        # disponíveis" na barra acima do canvas).
        try:
            conn = conectar()
        except RuntimeError:
            self._colunas_disponiveis = []
            return
        try:
            tabela = self.combo_tabela_origem.currentText()
            cursor = conn.execute(f'SELECT * FROM "{tabela}" LIMIT 0')
            self._colunas_disponiveis = [d[0] for d in cursor.description if d[0] != "id"]
        except Exception:
            self._colunas_disponiveis = []
        finally:
            conn.close()

    def _atualizar_modelos_disponiveis(self):
        """Popula `self._modelos_disponiveis` — uma entrada por NOME de
        modelo, agrupando todas as linhas de Modelos que compartilham esse
        nome (uma por estrato) numa lista "variantes". Tipo "Afilamento /
        Taper" fica de fora de `self._modelos_gerais_disponiveis` — só
        entra como nó "afilamento" dedicado (3 entradas fixas, 2 saídas,
        ver _atualizar_afilamento_disponiveis), nunca como nó "modelo"
        genérico (1 entrada, 1 saída — semântica errada pra ele). Sem
        lista própria no painel lateral — ver _abrir_menu_modelos, que lê
        `_modelos_gerais_disponiveis` na hora de montar o menu flutuante
        do botão "Modelos cadastrados" na barra acima do canvas."""
        self._modelos_disponiveis = []
        self._modelos_gerais_disponiveis = []
        self._nome_por_modelo_id = {}
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            linhas = conn.execute(
                "SELECT id, nome, tipo, estrato_coluna, estrato, variavel_x, variaveis_x, "
                "equacao, coeficientes FROM modelos ORDER BY nome, id"
            ).fetchall()
        except Exception:
            linhas = []
        finally:
            conn.close()

        grupos = {}
        ordem_nomes = []
        for (id_modelo, nome, tipo, estrato_coluna, estrato, variavel_x, variaveis_x_json,
             equacao, coeficientes_json) in linhas:
            variaveis = []
            if variaveis_x_json:
                try:
                    variaveis = json.loads(variaveis_x_json)
                except json.JSONDecodeError:
                    variaveis = []
            if not variaveis and variavel_x:
                variaveis = [variavel_x]

            coeficientes = {}
            if coeficientes_json:
                try:
                    coeficientes = json.loads(coeficientes_json)
                except json.JSONDecodeError:
                    coeficientes = {}

            self._nome_por_modelo_id[id_modelo] = nome

            grupo = grupos.get(nome)
            if grupo is None:
                grupo = {"nome": nome, "tipo": tipo, "variaveis": variaveis, "ids": [], "variantes": []}
                grupos[nome] = grupo
                ordem_nomes.append(nome)
            grupo["ids"].append(id_modelo)
            grupo["variantes"].append({
                "estrato_coluna": estrato_coluna or None, "estrato": estrato or None,
                "equacao": equacao or "", "coeficientes": coeficientes,
            })

        for nome in ordem_nomes:
            grupo = grupos[nome]
            self._modelos_disponiveis.append(grupo)
            if grupo["tipo"] == "Afilamento / Taper":
                continue
            self._modelos_gerais_disponiveis.append(grupo)

    def _atualizar_afilamento_disponiveis(self):
        # Só popula self._afilamento_disponiveis — sem lista própria no
        # painel lateral (ver _abrir_menu_nos_especiais, submenu "Taper",
        # que lê essa lista na hora de montar o menu).
        self._afilamento_disponiveis = [
            grupo for grupo in self._modelos_disponiveis if grupo["tipo"] == "Afilamento / Taper"]

    def _custos_colheita_cadastrados(self):
        """Lista (id, nome) dos custos de colheita cadastrados na tela
        Configurações (harvester, motosserra etc, o que estiver cadastrado)
        — consultada na hora a cada abertura do menu de botão direito de um
        nó "custo_colheita" (não cacheada como `_modelos_disponiveis`: a
        lista de custos é pequena e raramente muda no meio de uma sessão do
        Construtor)."""
        try:
            conn = conectar()
        except RuntimeError:
            return []
        try:
            return conn.execute("SELECT id, nome FROM custos_colheita ORDER BY nome").fetchall()
        except Exception:
            return []
        finally:
            conn.close()

    def _atualizar_construtores_disponiveis(self):
        # Sem lista própria no painel lateral (Duplicar/Excluir/Ativar-
        # Desativar viraram tela Configurações — ver
        # _montar_secao_construtores lá) — aqui só popula
        # self._construtores_disponiveis, pro menu flutuante do botão
        # "Construtores" (ver _abrir_menu_construtores) escolher qual
        # abrir no canvas.
        self._construtores_disponiveis = []
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            self._construtores_disponiveis = construtores.listar_construtores(conn)
        except Exception:
            self._construtores_disponiveis = []
        finally:
            conn.close()

        # Exclusão agora é feita na tela Configurações, sem essa tela
        # saber na hora — se o construtor aberto no canvas foi excluído
        # de lá, esquece o id antigo, senão "Salvar construtor" tentaria
        # fazer UPDATE num id que não existe mais (SQLite não erra, só não
        # afeta nenhuma linha, e o construtor "salvo" some silenciosamente
        # em vez de virar um INSERT novo).
        if self.construtor_atual_id is not None and not any(
                c["id"] == self.construtor_atual_id for c in self._construtores_disponiveis):
            self.construtor_atual_id = None

    # ---------------- adicionar nós ----------------

    def _proxima_posicao(self):
        n = self._contador_posicoes
        self._contador_posicoes += 1
        return 30 + (n % 6) * 45, 30 + (n // 6) * 120

    def _adicionar_no_coluna(self, nome_coluna):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {"tipo": "coluna", "x": x, "y": y, "rotulo": nome_coluna, "coluna": nome_coluna}
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_modelo(self, grupo):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "modelo", "x": x, "y": y,
            "nome": grupo["nome"],
            "rotulo": _rotulo_modelo(grupo["nome"], grupo["variantes"]),
            "modelo_ids": list(grupo["ids"]),
            "variaveis": list(grupo["variaveis"]),
            "variantes": [dict(v) for v in grupo["variantes"]],
            "nome_saida": "",
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_afilamento(self, grupo):
        if len(grupo["variaveis"]) != 3:
            QMessageBox.warning(
                self, "Construtor de Variáveis",
                f"\"{grupo['nome']}\" tem {len(grupo['variaveis'])} Variável(is) (x) cadastrada(s) "
                "em Modelos — um nó de Afilamento precisa de exatamente 3 (DAP, h, H, nessa "
                "ordem). Ajuste em Modelos antes de adicionar.")
            return
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "afilamento", "x": x, "y": y,
            "nome": grupo["nome"],
            "rotulo": f"Afilamento: {grupo['nome']}",
            "modelo_ids": list(grupo["ids"]),
            "variaveis": list(grupo["variaveis"]),
            "variantes": [dict(v) for v in grupo["variantes"]],
            "nome_saida_aproveitavel": "", "nome_saida_biomassa": "", "gravar": True,
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_classe_diametrica(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {"tipo": "classe_diametrica", "x": x, "y": y, "rotulo": "Classe Diamétrica"}
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_distribuicao(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "distribuicao", "x": x, "y": y, "rotulo": "Distribuição Diamétrica",
            "variaveis": ["forma", "escala"], "nome_saida": "", "gravar": True,
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_recuperacao_weibull(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "recuperacao_weibull", "x": x, "y": y, "rotulo": "Recuperação Weibull",
            "variaveis": ["media", "cv"],
            "nome_saida_forma": "", "nome_saida_escala": "", "gravar": True,
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_acumulado(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "acumulado", "x": x, "y": y, "rotulo": "Acumulado",
            "coluna_grupo": None, "coluna_ordem": None, "nome_saida": "", "gravar": True,
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_rendimento_sortimento(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "rendimento_sortimento", "x": x, "y": y, "rotulo": "Rendimento Serraria",
            "nome_saida": "", "gravar": True, "eventos_manejo": [],
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_receita_sortimento(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "receita_sortimento", "x": x, "y": y, "rotulo": "Receita Total",
            "nome_saida": "", "gravar": True, "eventos_manejo": [], "tipo_preco": "serrada",
            "deduzir_tributos": False,
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_vpl_sortimento(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "vpl_sortimento", "x": x, "y": y, "rotulo": "VPL",
            # "periodo" (não "ano_simulado") — o rótulo do pino, porque o
            # que se liga ali muda conforme "Base do período do VPL" (tela
            # Configurações): ano_simulado no modo padrão, idade_simulada
            # no modo "Ano Zero" (ver core/construtores.py,
            # avaliar_grafo/ramo "vpl_sortimento").
            "variaveis": ["rt", "periodo"], "nome_saida": "", "gravar": True,
            "eventos_manejo": [],
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_vet_sortimento(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "vet_sortimento", "x": x, "y": y, "rotulo": "VET",
            "nome_saida": "", "gravar": True, "eventos_manejo": [],
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_custo_colheita(self):
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "custo_colheita", "x": x, "y": y, "rotulo": "Custo de Colheita",
            "custo_colheita_id": None, "custo_colheita_nome": None,
            "nome_saida": "", "gravar": True, "eventos_manejo": [],
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _adicionar_no_custo_formacao(self):
        # nome_saida já vem preenchido (ao contrário dos outros nós
        # especiais, que começam em branco) — "custo_formacao" é o nome
        # fixo que core/simulacao.py:calcular_volume_por_sortimento lê pra
        # alimentar "Volume" na tela Simulação, mesmo nome que a coluna
        # tinha quando isso era calculado direto em gerar_populacao.
        no_id = self._proximo_id
        self._proximo_id += 1
        x, y = self._proxima_posicao()
        self.nos[no_id] = {
            "tipo": "custo_formacao", "x": x, "y": y, "rotulo": "Custo de Formação",
            "nome_saida": "custo_formacao", "gravar": True, "excluir_outras_contas": True,
        }
        self._centralizar_em_mundo(x, y)
        self._redesenhar()

    def _limpar_canvas(self):
        self.nos = {}
        self.conexoes = []
        self.grupos = {}
        self._selecionados = set()
        self._selecao_retangulo = None
        self._arrastando_nos = False
        self._arrastando_grupo = None
        self._redimensionando_grupo = None
        self._arrastando_fio = None
        self._pino_origem = None
        self._fio_temp_destino = None
        self.construtor_atual_id = None
        self._redesenhar()
        self.tabela_previa.redefinir_colunas([])
        self.label_status.setText("")

    # ---------------- zoom / pan ----------------

    def _mundo_para_tela(self, x, y):
        return (x - self._pan_x) * self._zoom, (y - self._pan_y) * self._zoom

    def _tela_para_mundo(self, x, y):
        return x / self._zoom + self._pan_x, y / self._zoom + self._pan_y

    def _centralizar_em_mundo(self, x, y):
        largura = max(self.canvas.width(), 1)
        altura = max(self.canvas.height(), 1)
        self._pan_x = x - (largura / 2) / self._zoom
        self._pan_y = y - (altura / 2) / self._zoom

    def _aplicar_zoom(self, fator, x_ancora_tela=None, y_ancora_tela=None):
        novo_zoom = min(ZOOM_MAXIMO, max(ZOOM_MINIMO, self._zoom * fator))
        if novo_zoom == self._zoom:
            return
        if x_ancora_tela is None:
            x_ancora_tela = max(self.canvas.width(), 1) / 2
            y_ancora_tela = max(self.canvas.height(), 1) / 2
        mx, my = self._tela_para_mundo(x_ancora_tela, y_ancora_tela)
        self._zoom = novo_zoom
        mx_novo, my_novo = self._tela_para_mundo(x_ancora_tela, y_ancora_tela)
        self._pan_x += mx - mx_novo
        self._pan_y += my - my_novo
        self._redesenhar()

    def _redefinir_vista(self):
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._redesenhar()

    def _escolher_cor_borda_no(self):
        """Estilo da borda de todo nó/pino no canvas — preferência de usuário/
        máquina (ver core/preferencias.py), não do construtor/projeto atual.
        Aplica na hora (redesenha) e persiste pra próxima vez que o app
        abrir."""
        resultado = self._dialogo_estilo_borda(
            self._cor_borda_no, self._espessura_borda_no, "Borda dos nós")
        if resultado is None:
            return
        self._cor_borda_no, self._espessura_borda_no = resultado
        preferencias.salvar_cor_borda_no(
            self._cor_borda_no.name(QColor.NameFormat.HexArgb))
        preferencias.salvar_espessura_borda_no(self._espessura_borda_no)
        self._redesenhar()

    def _dialogo_estilo_borda(self, cor_inicial, espessura_inicial, titulo):
        """Editor conjunto de cor, transparência e espessura da borda."""
        dialogo = QDialog(self)
        dialogo.setWindowTitle(titulo)
        layout = QVBoxLayout(dialogo)

        cor = QColor(cor_inicial)
        botao_cor = QPushButton()

        def atualizar_amostra():
            botao_cor.setText(
                f"Escolher cor…   {cor.name()}   {cor.alphaF() * 100:.0f}% opaca")
            botao_cor.setStyleSheet(
                f"background-color: rgba({cor.red()}, {cor.green()}, {cor.blue()}, {cor.alpha()});")

        def escolher_cor():
            nonlocal cor
            seletor = QColorDialog(cor, dialogo)
            seletor.setWindowTitle("Cor e transparência da borda")
            seletor.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
            # Força o diálogo Qt (em vez do nativo) para podermos corrigir
            # os controles Hue/Sat/Val/R/G/B/Alpha. Com o QSS geral, os
            # indicadores padrão deles encolhiam e pareciam pontinhos.
            seletor.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            for campo in seletor.findChildren(QSpinBox):
                campo.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
            # Só o Alpha precisa de espaço adicional: o texto do valor e
            # os dois botões dividem o mesmo campo e, na largura padrão,
            # parte de números com três dígitos ficava encoberta.
            for rotulo in seletor.findChildren(QLabel):
                if "alpha channel" in rotulo.text().replace("&", "").lower():
                    campo_alpha = rotulo.buddy()
                    if isinstance(campo_alpha, QSpinBox):
                        campo_alpha.setMinimumWidth(130)
                    break
            if seletor.exec() != QDialog.DialogCode.Accepted:
                return
            escolhida = seletor.selectedColor()
            if escolhida.isValid():
                cor = escolhida
                atualizar_amostra()

        atualizar_amostra()
        botao_cor.clicked.connect(escolher_cor)
        layout.addWidget(QLabel("Cor e transparência"))
        layout.addWidget(botao_cor)

        espessura = QDoubleSpinBox()
        espessura.setRange(0.5, 8.0)
        espessura.setSingleStep(0.5)
        espessura.setDecimals(1)
        espessura.setSuffix(" px")
        espessura.setValue(float(espessura_inicial))
        espessura.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        layout.addWidget(QLabel("Espessura"))
        layout.addWidget(espessura)

        botoes = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botoes.accepted.connect(dialogo.accept)
        botoes.rejected.connect(dialogo.reject)
        layout.addWidget(botoes)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return None
        return cor, espessura.value()

    def _ao_rolar(self, evento):
        delta = evento.angleDelta().y()
        if delta == 0:
            return
        modificadores = evento.modifiers()
        pos = evento.position()
        if modificadores & Qt.KeyboardModifier.ControlModifier:
            fator = ZOOM_PASSO_RODA if delta > 0 else 1 / ZOOM_PASSO_RODA
            self._aplicar_zoom(fator, pos.x(), pos.y())
        elif modificadores & Qt.KeyboardModifier.ShiftModifier:
            self._pan_x -= (delta / 120) * 40 / self._zoom
            self._redesenhar()
        else:
            self._pan_y -= (delta / 120) * 40 / self._zoom
            self._redesenhar()

    def _iniciar_pan(self, evento):
        pos = evento.position()
        self._pan_arraste_inicio = (pos.x(), pos.y(), self._pan_x, self._pan_y)

    def _mover_pan(self, evento):
        if self._pan_arraste_inicio is None:
            return
        x0, y0, pan_x0, pan_y0 = self._pan_arraste_inicio
        pos = evento.position()
        self._pan_x = pan_x0 - (pos.x() - x0) / self._zoom
        self._pan_y = pan_y0 - (pos.y() - y0) / self._zoom
        self._redesenhar()

    def _parar_pan(self, _evento):
        self._pan_arraste_inicio = None

    # ---------------- geometria dos nós/pinos ----------------

    def _altura_no(self, no):
        if no["tipo"] in ("coluna", "classe_diametrica"):
            return ALTURA_COLUNA
        if no["tipo"] == "saida":
            n_entradas = len(no["entradas"]) + 1
            return ALTURA_TITULO_MODELO + n_entradas * ALTURA_POR_ENTRADA + 8
        if no["tipo"] == "calculo":
            n_passos = len(no.get("passos", []))
            return ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * (1 + n_passos) + 8
        if no["tipo"] == "acumulado":
            return ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * 3 + 8
        if no["tipo"] == "receita_sortimento":
            return ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * 2 + 8
        if no["tipo"] in ("rendimento_sortimento", "vet_sortimento"):
            return ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * 1 + 8
        if no["tipo"] == "custo_colheita":
            return ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * 2 + 8
        if no["tipo"] == "custo_formacao":
            return ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * 1 + 8
        if no["tipo"] == "afilamento":
            return ALTURA_TITULO_MODELO + max(1, _n_saidas(no)) * ALTURA_POR_ENTRADA + 8
        n_entradas = max(len(no["variaveis"]), _n_saidas(no), 1)
        return ALTURA_TITULO_MODELO + n_entradas * ALTURA_POR_ENTRADA + 8

    def _posicao_pino_saida(self, no_id, saida_idx=0):
        no = self.nos[no_id]
        if _n_saidas(no) <= 1:
            altura = self._altura_no(no)
            return no["x"] + LARGURA_NO, no["y"] + altura / 2
        y = no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * saida_idx + ALTURA_POR_ENTRADA / 2
        return no["x"] + LARGURA_NO, y

    def _posicao_pino_entrada(self, no_id, indice):
        no = self.nos[no_id]
        y = no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * indice + ALTURA_POR_ENTRADA / 2
        return no["x"], y

    def _saida_ligada(self, no_id, saida_idx=0):
        return any(
            c["origem"] == no_id and c.get("saida_idx", 0) == saida_idx for c in self.conexoes)

    # ---------------- desenho ----------------

    def _redesenhar(self):
        self.canvas.update()

    def _desenhar_texto(self, pintor, x, y, texto, cor, tamanho_px, negrito=False, ancora="center", largura=200):
        # setPixelSize (não setPointSizeF) — tamanho_px já vem calculado em
        # pixels de tela (round(base * self._zoom) nos chamadores), igual à
        # geometria do resto do nó (pinos, retângulo etc, que também usam
        # `* self._zoom` sem conversão de unidade). Usar "pontos" (que
        # dependem do DPI do sistema) description criava um descompasso
        # entre o tamanho da fonte e o tamanho do nó — ficava visível em
        # zoom baixo, onde os chamadores ainda impunham um piso mínimo
        # (~6pt) que não encolhia junto com o nó, "vazando" texto/pílula
        # pra fora dele. Piso de 1px aqui é só pra nunca mandar 0/negativo
        # pro Qt — os chamadores não têm mais piso próprio (ver
        # _desenhar_no/_desenhar_campo_nome), então o texto agora encolhe
        # na mesma proporção do nó em qualquer zoom.
        fonte = QFont()
        fonte.setPixelSize(max(1, round(tamanho_px)))
        fonte.setBold(negrito)
        pintor.setFont(fonte)
        pintor.setPen(QColor(cor))
        n_linhas = texto.count("\n") + 1
        altura = pintor.fontMetrics().height() * n_linhas + 4
        if ancora == "w":
            retangulo = QRectF(x, y - altura / 2, largura, altura)
            alinhamento = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        elif ancora == "e":
            retangulo = QRectF(x - largura, y - altura / 2, largura, altura)
            alinhamento = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        else:
            retangulo = QRectF(x - largura / 2, y - altura / 2, largura, altura)
            alinhamento = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        pintor.drawText(retangulo, int(alinhamento) | int(Qt.TextFlag.TextWordWrap), texto)

    def _pintar(self, pintor):
        pintor.fillRect(self.canvas.rect(), QColor(tema.obter().cor_canvas_construtor()))
        for grupo_id, grupo in self.grupos.items():
            self._desenhar_grupo(pintor, grupo_id, grupo)
        for con in self.conexoes:
            self._desenhar_fio(pintor, con)
        if self._pino_origem is not None and self._fio_temp_destino is not None:
            x1, y1 = self._mundo_para_tela(*self._posicao_pino_saida(*self._pino_origem))
            x2, y2 = self._mundo_para_tela(*self._fio_temp_destino)
            caneta = QPen(QColor(COR_FIO_TEMP), 2, Qt.PenStyle.DashLine)
            pintor.setPen(caneta)
            pintor.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        for no_id, no in self.nos.items():
            self._desenhar_no(pintor, no_id, no)
        if self._selecao_retangulo is not None:
            self._desenhar_selecao_retangulo(pintor)

    def _desenhar_grupo(self, pintor, grupo_id, grupo):
        """Caixa de agrupamento — retângulo pontilhado com uma faixa de
        título (arrastar/botão direito nela, ver _item_no_ponto) e uma
        alça de redimensionar no canto inferior direito. Não guarda quais
        nós "pertencem" a ela — quem está dentro é recalculado na hora de
        arrastar (ver _iniciar_arraste_grupo), então só desenha o retângulo
        em si, os nós desenham por cima normalmente."""
        x0, y0 = self._mundo_para_tela(grupo["x"], grupo["y"])
        largura = grupo["largura"] * self._zoom
        altura = grupo["altura"] * self._zoom
        cor = QColor(grupo.get("cor_borda") or COR_GRUPO_PADRAO)

        fundo = QColor(cor)
        fundo.setAlpha(20)
        pintor.setPen(QPen(cor, 1.5, Qt.PenStyle.DashLine))
        pintor.setBrush(fundo)
        pintor.drawRoundedRect(QRectF(x0, y0, largura, altura), 10, 10)

        altura_cabecalho = min(22 * self._zoom, altura)
        fundo_cabecalho = QColor(cor)
        fundo_cabecalho.setAlpha(70)
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(fundo_cabecalho)
        pintor.drawRoundedRect(QRectF(x0, y0, largura, altura_cabecalho), 10, 10)
        pintor.setBrush(Qt.BrushStyle.NoBrush)

        self._desenhar_texto(
            pintor, x0 + 8 * self._zoom, y0 + altura_cabecalho / 2, grupo.get("titulo") or "Grupo",
            "white", max(7, round(9 * self._zoom)), negrito=True, ancora="w",
            largura=largura - 30 * self._zoom)

        alca = 10 * self._zoom
        pintor.setPen(QPen(cor, 1.5))
        pintor.drawLine(
            QPointF(x0 + largura - alca, y0 + altura), QPointF(x0 + largura, y0 + altura - alca))

    def _desenhar_selecao_retangulo(self, pintor):
        x0, y0, x1, y1 = self._selecao_retangulo
        tx0, ty0 = self._mundo_para_tela(min(x0, x1), min(y0, y1))
        tx1, ty1 = self._mundo_para_tela(max(x0, x1), max(y0, y1))
        cor = QColor(COR_SELECAO)
        fundo = QColor(cor)
        fundo.setAlpha(40)
        pintor.setPen(QPen(cor, 1, Qt.PenStyle.DashLine))
        pintor.setBrush(fundo)
        pintor.drawRect(QRectF(tx0, ty0, tx1 - tx0, ty1 - ty0))

    def _desenhar_fio(self, pintor, con):
        x1, y1 = self._mundo_para_tela(*self._posicao_pino_saida(con["origem"], con.get("saida_idx", 0)))
        x2, y2 = self._mundo_para_tela(*self._posicao_pino_entrada(con["destino"], con["entrada_idx"]))
        # "dobra" — posição (0..1) do trecho vertical do fio entre x1 e x2,
        # 0.5 = meio (padrão) — arrastável pelo usuário (ver
        # _item_no_ponto/_ao_arrastar, tipo "fio") pra "movimentar" a linha.
        dobra = con.get("dobra", 0.5)
        meio = x1 + (x2 - x1) * dobra
        caminho = QPainterPath()
        caminho.moveTo(x1, y1)
        caminho.lineTo(meio, y1)
        caminho.lineTo(meio, y2)
        caminho.lineTo(x2, y2)
        relacionado_selecao = (
            con["origem"] in self._selecionados or con["destino"] in self._selecionados)
        ha_selecao = bool(self._selecionados)
        cor_fio = QColor(con.get("cor") or COR_FIO)
        if ha_selecao and not relacionado_selecao:
            cor_fio.setAlpha(55)
        pintor.setPen(QPen(cor_fio, 3 if relacionado_selecao else 2))
        # NoBrush explícito — sem isso, um fio desenhado logo depois de
        # outro que aponta pra um nó "Saída" (que deixa o pincel com
        # COR_OPERADOR = branco setado pra pintar a bolinha do operador,
        # ver drawEllipse abaixo) herda esse preenchimento e vira um
        # "polígono" branco em vez de só a linha.
        pintor.setBrush(Qt.BrushStyle.NoBrush)
        pintor.drawPath(caminho)

        # Pequena seta no destino deixa explícito o sentido do fluxo. Em
        # grafos densos isso evita ter de seguir visualmente o fio até
        # descobrir qual ponta é entrada e qual é saída.
        tamanho_seta = 5 * self._zoom
        pintor.setPen(QPen(cor_fio, 2 if relacionado_selecao else 1.5))
        pintor.drawLine(QPointF(x2, y2), QPointF(x2 - tamanho_seta, y2 - tamanho_seta * 0.65))
        pintor.drawLine(QPointF(x2, y2), QPointF(x2 - tamanho_seta, y2 + tamanho_seta * 0.65))

        no_destino = self.nos.get(con["destino"])
        if no_destino is not None and no_destino["tipo"] == "saida":
            entrada = no_destino["entradas"][con["entrada_idx"]]
            inverso = entrada.get("inverso", False)
            if con["entrada_idx"] > 0:
                operador = entrada.get("operador", "+")
                texto = f"1/x,{SIMBOLO_OPERADOR[operador]}" if inverso else SIMBOLO_OPERADOR[operador]
            else:
                texto = "1/x" if inverso else "·"
            mx, my = meio, (y1 + y2) / 2
            raio = (RAIO_PINO + 3) * self._zoom
            pintor.setPen(QPen(self._cor_borda_no, 1.5))
            pintor.setBrush(QColor(COR_OPERADOR))
            pintor.drawEllipse(QPointF(mx, my), raio, raio)
            self._desenhar_texto(
                pintor, mx, my, texto, "#000000", round(7 * self._zoom), negrito=True,
                ancora="center", largura=raio * 4)

    def _cor_borda_no_efetiva(self, no):
        """Cor da borda do NÓ (e dos pinos/textos desenhados dentro dele) —
        override individual (botão direito > "Cor da borda...") se houver,
        senão a cor global de preferência (ver _escolher_cor_borda_no)."""
        cor = no.get("cor_borda")
        return QColor(cor) if cor else self._cor_borda_no

    def _espessura_borda_no_efetiva(self, no):
        return float(no.get("espessura_borda", self._espessura_borda_no))

    def _layout_campo_nome(self, tx, ty, larg_tela):
        """Geometria (screen-space) do cabeçalho "Tipo: [pílula]" + linha
        extra/checkbox — SEM piso mínimo nos tamanhos (só `* self._zoom`),
        de propósito: um piso fixo (ex: "nunca menor que 6px") não encolhe
        junto com o retângulo do nó (que não tem piso — ver _desenhar_no),
        e em zoom baixo o cabeçalho passava a ser MAIOR que o próprio nó,
        "vazando" a pílula pra fora dele. Extraído da lógica de desenho
        (_desenhar_campo_nome) pra também servir de base pro hit-test do
        checkbox "Gravar" (ver _retangulo_checkbox_gravar), sem duplicar
        (e desalinhar) essa matemática em dois lugares."""
        tam_rotulo = round(7 * self._zoom)
        y_rotulo = ty + tam_rotulo + 3 * self._zoom
        altura_pill = max(1, round(14 * self._zoom))
        y_pill = y_rotulo + tam_rotulo * 0.9 + 4 * self._zoom
        y_extra = y_pill + altura_pill + 2 * self._zoom
        return {"tam_rotulo": tam_rotulo, "y_rotulo": y_rotulo, "altura_pill": altura_pill, "y_pill": y_pill,
                "y_extra": y_extra}

    def _desenhar_campo_nome(self, pintor, no, tx, ty, larg_tela, cor_borda_no, rotulo_tipo, valores, linhas_extra):
        """"Coluna: [Nome]" — rótulo do tipo em cima de um campo estilo
        input arredondado com o valor (nome da coluna/saída/modelo/etc). A
        linha abaixo da pílula é OU o checkbox "Gravar" (nós de
        _TIPOS_COM_GRAVAR_NO_CORPO) OU as linhas extra de `linhas_extra`
        (ex: "N estratos") — nunca as duas, só cabe uma linha ali (ver
        ALTURA_TITULO_MODELO). Ver _campo_nome_no."""
        layout = self._layout_campo_nome(tx, ty, larg_tela)
        tam_rotulo = layout["tam_rotulo"]
        self._desenhar_texto(
            pintor, tx + 6 * self._zoom, layout["y_rotulo"], f"{rotulo_tipo}:", "white", tam_rotulo,
            negrito=True, ancora="w", largura=larg_tela - 12 * self._zoom)

        altura_pill = layout["altura_pill"]
        y_pill = layout["y_pill"]
        margem = 6 * self._zoom
        gap = 4 * self._zoom
        n = max(1, len(valores))
        largura_pill = (larg_tela - 2 * margem - gap * (n - 1)) / n
        tam_valor = round(7.5 * self._zoom)
        fonte_valor = QFont()
        fonte_valor.setPixelSize(max(1, tam_valor))
        for i, valor in enumerate(valores):
            x0 = tx + margem + i * (largura_pill + gap)
            retangulo = QRectF(x0, y_pill, largura_pill, altura_pill)
            pintor.setPen(QPen(cor_borda_no, 1))
            pintor.setBrush(QColor("#ffffff"))
            pintor.drawRoundedRect(retangulo, altura_pill / 2, altura_pill / 2)
            pintor.setPen(QColor("#222222"))
            pintor.setFont(fonte_valor)
            texto = pintor.fontMetrics().elidedText(
                valor or "", Qt.TextElideMode.ElideRight, max(1, int(largura_pill - 8 * self._zoom)))
            pintor.drawText(
                retangulo.adjusted(4 * self._zoom, 0, -4 * self._zoom, 0),
                int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextSingleLine), texto)

        y_extra = layout["y_extra"]
        if no["tipo"] in _TIPOS_COM_GRAVAR_NO_CORPO:
            self._desenhar_checkbox_gravar(pintor, no, tx, y_extra, larg_tela)
            return
        tam_extra = round(7 * self._zoom)
        for linha in linhas_extra:
            self._desenhar_texto(
                pintor, tx + larg_tela / 2, y_extra, linha, "white", tam_extra,
                ancora="center", largura=larg_tela - 8 * self._zoom)
            y_extra += tam_extra + 3 * self._zoom

    def _desenhar_checkbox_gravar(self, pintor, no, tx, y, larg_tela):
        """Checkbox "Gravar na tabela" desenhado direto no corpo do nó
        (mesma linha que _desenhar_campo_nome reservaria pra uma linha
        extra de texto) — clicável, ver _retangulo_checkbox_gravar/
        _item_no_ponto (tipo "gravar") e _ao_pressionar, que chama
        _alternar_gravar na hora, sem precisar do menu de botão direito."""
        lado = max(1, round(14 * self._zoom))
        x0 = tx + 6 * self._zoom
        gravar = no.get("gravar", True)
        quadrado = QRectF(x0, y, lado, lado)
        pintor.setPen(QPen(QColor("#ffffff"), max(1.0, 1.2 * self._zoom)))
        pintor.setBrush(QColor("#ffffff") if gravar else Qt.BrushStyle.NoBrush)
        pintor.drawRect(quadrado)
        if gravar:
            pintor.setPen(QPen(QColor("#222222"), max(1.0, 1.6 * self._zoom)))
            pintor.drawLine(
                QPointF(x0 + lado * 0.2, y + lado * 0.55), QPointF(x0 + lado * 0.42, y + lado * 0.82))
            pintor.drawLine(
                QPointF(x0 + lado * 0.42, y + lado * 0.82), QPointF(x0 + lado * 0.85, y + lado * 0.18))
        self._desenhar_texto(
            pintor, x0 + lado + 4 * self._zoom, y + lado / 2, "Gravar na tabela", "white",
            round(8 * self._zoom), ancora="w", largura=larg_tela - (lado + 16 * self._zoom))

    def _retangulo_checkbox_gravar(self, no):
        """Retângulo (screen-space) do checkbox "Gravar" pra hit-test (ver
        _item_no_ponto) — None se este nó não mostra o checkbox no corpo
        (ver _TIPOS_COM_GRAVAR_NO_CORPO). Mesma matemática de
        _layout_campo_nome/_desenhar_checkbox_gravar, mantida em sincronia
        de propósito (ver docstring de _layout_campo_nome)."""
        if no["tipo"] not in _TIPOS_COM_GRAVAR_NO_CORPO:
            return None
        tx, ty = self._mundo_para_tela(no["x"], no["y"])
        larg_tela = LARGURA_NO * self._zoom
        if (no.get("nome_personalizado") or "").strip():
            ty += 12 * self._zoom
        layout = self._layout_campo_nome(tx, ty, larg_tela)
        lado = max(1, round(14 * self._zoom))
        altura_clique = max(lado, 22 * self._zoom)
        return QRectF(
            tx + 4 * self._zoom, layout["y_extra"] - (altura_clique - lado) / 2,
            larg_tela - 8 * self._zoom, altura_clique)

    def _retangulo_checkbox_tributos(self, no):
        """Área clicável da dedução tributária exibida no nó Receita Total."""
        if no["tipo"] != "receita_sortimento":
            return None
        tx, _ = self._mundo_para_tela(no["x"], no["y"])
        _, y = self._mundo_para_tela(
            no["x"], no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA)
        return QRectF(
            tx + 4 * self._zoom, y, LARGURA_NO * self._zoom - 8 * self._zoom,
            ALTURA_POR_ENTRADA * self._zoom)

    def _desenhar_checkbox_tributos(self, pintor, no, tx, y, larg_tela):
        lado = max(1, round(12 * self._zoom))
        x0 = tx + 6 * self._zoom
        y0 = y + (ALTURA_POR_ENTRADA * self._zoom - lado) / 2
        marcado = no.get("deduzir_tributos", False)
        quadrado = QRectF(x0, y0, lado, lado)
        pintor.setPen(QPen(QColor("#ffffff"), max(1.0, 1.2 * self._zoom)))
        pintor.setBrush(QColor("#ffffff") if marcado else Qt.BrushStyle.NoBrush)
        pintor.drawRect(quadrado)
        if marcado:
            pintor.setPen(QPen(QColor("#222222"), max(1.0, 1.5 * self._zoom)))
            pintor.drawLine(QPointF(x0 + lado * .2, y0 + lado * .55), QPointF(x0 + lado * .42, y0 + lado * .82))
            pintor.drawLine(QPointF(x0 + lado * .42, y0 + lado * .82), QPointF(x0 + lado * .85, y0 + lado * .18))
        self._desenhar_texto(
            pintor, x0 + lado + 4 * self._zoom, y0 + lado / 2,
            "Deduzir PIS, COFINS e FUNRURAL", "white", round(7 * self._zoom),
            ancora="w", largura=larg_tela - (lado + 16 * self._zoom))

    def _retangulo_campo_nome(self, no):
        """Área da pílula branca que mostra o nome/“(sem nome)” do nó."""
        if _campo_nome_no(no) is None:
            return None
        tx, ty = self._mundo_para_tela(no["x"], no["y"])
        larg_tela = LARGURA_NO * self._zoom
        if (no.get("nome_personalizado") or "").strip():
            ty += 12 * self._zoom
        geometria = self._layout_campo_nome(tx, ty, larg_tela)
        margem = 6 * self._zoom
        return QRectF(
            tx + margem, geometria["y_pill"], larg_tela - 2 * margem,
            geometria["altura_pill"])

    def _desenhar_no(self, pintor, no_id, no):
        altura = self._altura_no(no)
        tx, ty = self._mundo_para_tela(no["x"], no["y"])
        larg_tela = LARGURA_NO * self._zoom
        alt_tela = altura * self._zoom
        raio = RAIO_PINO * self._zoom
        raio_canto = RAIO_CANTO_NO * self._zoom
        cor_borda_no = self._cor_borda_no_efetiva(no)
        espessura_borda = max(0.5, self._espessura_borda_no_efetiva(no) * self._zoom)

        cor = _cor_no(no)
        pintor.setPen(QPen(cor_borda_no, espessura_borda))
        pintor.setBrush(QColor(cor))
        pintor.drawRoundedRect(QRectF(tx, ty, larg_tela, alt_tela), raio_canto, raio_canto)

        if no_id in self._selecionados:
            pintor.setPen(QPen(QColor(COR_SELECAO), 3))
            pintor.setBrush(Qt.BrushStyle.NoBrush)
            pintor.drawRoundedRect(QRectF(tx - 2, ty - 2, larg_tela + 4, alt_tela + 4), raio_canto, raio_canto)

        # ID estável do nó: aparece discretamente no canto e corresponde
        # ao identificador usado nas conexões/diagnósticos. É especialmente
        # útil quando há vários nós com o mesmo modelo ou nome de saída.
        self._desenhar_texto(
            pintor, tx + larg_tela - 5 * self._zoom, ty + 7 * self._zoom, f"#{no_id}",
            "#ffffff", round(6.5 * self._zoom), ancora="e", largura=38 * self._zoom)

        nome_personalizado = (no.get("nome_personalizado") or "").strip()
        campo_nome = _campo_nome_no(no)
        if nome_personalizado:
            self._desenhar_texto(
                pintor, tx + larg_tela / 2, ty + 9 * self._zoom, nome_personalizado, "white",
                round(8 * self._zoom), negrito=True, ancora="center", largura=larg_tela - 12 * self._zoom)
            deslocamento = 12 * self._zoom
        else:
            deslocamento = 0
        if campo_nome is None:
            self._desenhar_texto(
                pintor, tx + larg_tela / 2, ty + deslocamento + 13 * self._zoom, _titulo_no_automatico(no),
                "white", round(9 * self._zoom), negrito=True, ancora="center",
                largura=larg_tela - 12 * self._zoom)
        else:
            rotulo_tipo, valores, linhas_extra = campo_nome
            self._desenhar_campo_nome(
                pintor, no, tx, ty + deslocamento, larg_tela, cor_borda_no, rotulo_tipo, valores, linhas_extra)

        tamanho_pino = round(8 * self._zoom)
        rotulos_saida = _ROTULOS_SAIDA.get(no["tipo"])
        for i in range(_n_saidas(no)):
            sx, sy = self._mundo_para_tela(*self._posicao_pino_saida(no_id, i))
            pintor.setPen(QPen(cor_borda_no, espessura_borda))
            pintor.setBrush(QColor(COR_PINO_LIGADO if self._saida_ligada(no_id, i) else COR_PINO_LIVRE))
            pintor.drawEllipse(QPointF(sx, sy), raio, raio)
            if rotulos_saida:
                self._desenhar_texto(
                    pintor, sx - raio - 4, sy, rotulos_saida[i], "black", tamanho_pino, ancora="e", largura=80)

        if no["tipo"] in ("modelo", "distribuicao", "vpl_sortimento", "recuperacao_weibull"):
            for i, nome_var in enumerate(no["variaveis"]):
                ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, i))
                conexao = next(
                    (c for c in self.conexoes if c["destino"] == no_id and c["entrada_idx"] == i), None)
                eh_classe = (
                    conexao is not None
                    and self.nos.get(conexao["origem"], {}).get("tipo") == "classe_diametrica")
                pintor.setPen(QPen(cor_borda_no, espessura_borda))
                pintor.setBrush(QColor(COR_PINO_LIGADO if conexao is not None else COR_PINO_LIVRE))
                pintor.drawEllipse(QPointF(ex, ey), raio, raio)
                texto_var = f"{nome_var} (classe)" if eh_classe else nome_var
                cor_texto = COR_CLASSE_DIAMETRICA if eh_classe else "black"
                self._desenhar_texto(pintor, ex + raio + 4, ey, texto_var, cor_texto, tamanho_pino, ancora="w")

        elif no["tipo"] == "saida":
            n_entradas = len(no["entradas"])
            for i in range(n_entradas + 1):
                ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, i))
                vazio = i == n_entradas
                pintor.setPen(QPen(
                    cor_borda_no, 1.5,
                    Qt.PenStyle.DashLine if vazio else Qt.PenStyle.SolidLine))
                pintor.setBrush(QColor(COR_PINO_VAZIO_SAIDA if vazio else COR_PINO_LIGADO))
                pintor.drawEllipse(QPointF(ex, ey), raio, raio)
                if vazio:
                    texto_entrada = f"{i + 1}  + entrada"
                else:
                    entrada = no["entradas"][i]
                    operador = "·" if i == 0 else SIMBOLO_OPERADOR[entrada.get("operador", "+")]
                    inverso = entrada.get("inverso", False)
                    operacao = f"1/x  {operador}" if inverso else operador
                    texto_entrada = f"{i + 1}  {operacao}"
                self._desenhar_texto(
                    pintor, ex + raio + 4, ey, texto_entrada,
                    "#555555" if vazio else "black", tamanho_pino,
                    negrito=not vazio, ancora="w", largura=90 * self._zoom)

        elif no["tipo"] == "calculo":
            ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, 0))
            ligado = any(c["destino"] == no_id and c["entrada_idx"] == 0 for c in self.conexoes)
            pintor.setPen(QPen(cor_borda_no, espessura_borda))
            pintor.setBrush(QColor(COR_PINO_LIGADO if ligado else COR_PINO_LIVRE))
            pintor.drawEllipse(QPointF(ex, ey), raio, raio)
            self._desenhar_texto(pintor, ex + raio + 4, ey, "x", "black", tamanho_pino, ancora="w")
            for i, passo in enumerate(no.get("passos", [])):
                mundo_ty = (
                    no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * (1 + i) + ALTURA_POR_ENTRADA / 2)
                _, py = self._mundo_para_tela(no["x"], mundo_ty)
                self._desenhar_texto(
                    pintor, tx + larg_tela / 2, py, _rotulo_passo(passo), "white", tamanho_pino,
                    negrito=True, ancora="center", largura=larg_tela)

        elif no["tipo"] == "acumulado":
            ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, 0))
            ligado = any(c["destino"] == no_id and c["entrada_idx"] == 0 for c in self.conexoes)
            pintor.setPen(QPen(cor_borda_no, espessura_borda))
            pintor.setBrush(QColor(COR_PINO_LIGADO if ligado else COR_PINO_LIVRE))
            pintor.drawEllipse(QPointF(ex, ey), raio, raio)
            self._desenhar_texto(pintor, ex + raio + 4, ey, "valor", "black", tamanho_pino, ancora="w")
            for i, texto_config in enumerate((
                f"Grupo: {no.get('coluna_grupo') or '(config. pendente)'}",
                f"Ordem: {no.get('coluna_ordem') or '(config. pendente)'}",
            )):
                mundo_ty = (
                    no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * (1 + i) + ALTURA_POR_ENTRADA / 2)
                _, py = self._mundo_para_tela(no["x"], mundo_ty)
                self._desenhar_texto(
                    pintor, tx + larg_tela / 2, py, texto_config, "white", round(7 * self._zoom),
                    ancora="center", largura=larg_tela - 8 * self._zoom)

        elif no["tipo"] == "custo_colheita":
            ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, 0))
            ligado = any(c["destino"] == no_id and c["entrada_idx"] == 0 for c in self.conexoes)
            pintor.setPen(QPen(cor_borda_no, espessura_borda))
            pintor.setBrush(QColor(COR_PINO_LIGADO if ligado else COR_PINO_LIVRE))
            pintor.drawEllipse(QPointF(ex, ey), raio, raio)
            self._desenhar_texto(pintor, ex + raio + 4, ey, "valor (classe)", "black", tamanho_pino, ancora="w")
            mundo_ty = no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA * 1 + ALTURA_POR_ENTRADA / 2
            _, py = self._mundo_para_tela(no["x"], mundo_ty)
            texto_custo = f"Custo: {no.get('custo_colheita_nome') or '(config. pendente)'}"
            self._desenhar_texto(
                pintor, tx + larg_tela / 2, py, texto_custo, "white", round(7 * self._zoom),
                ancora="center", largura=larg_tela - 8 * self._zoom)

        elif no["tipo"] == "custo_formacao":
            ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, 0))
            ligado = any(c["destino"] == no_id and c["entrada_idx"] == 0 for c in self.conexoes)
            pintor.setPen(QPen(cor_borda_no, espessura_borda))
            pintor.setBrush(QColor(COR_PINO_LIGADO if ligado else COR_PINO_LIVRE))
            pintor.drawEllipse(QPointF(ex, ey), raio, raio)
            self._desenhar_texto(
                pintor, ex + raio + 4, ey, "multiplicador (opcional)", "black", tamanho_pino, ancora="w")

        elif no["tipo"] in (
                "rendimento_sortimento", "receita_sortimento", "vet_sortimento", "afilamento"):
            rotulo_entrada = {
                "rendimento_sortimento": "volume (classe)",
                "receita_sortimento": "vtcc (classe)",
                "vet_sortimento": "vpl (classe)", "afilamento": "H (Ht, classe)",
            }[no["tipo"]]
            ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, 0))
            ligado = any(c["destino"] == no_id and c["entrada_idx"] == 0 for c in self.conexoes)
            pintor.setPen(QPen(cor_borda_no, espessura_borda))
            pintor.setBrush(QColor(COR_PINO_LIGADO if ligado else COR_PINO_LIVRE))
            pintor.drawEllipse(QPointF(ex, ey), raio, raio)
            self._desenhar_texto(pintor, ex + raio + 4, ey, rotulo_entrada, "black", tamanho_pino, ancora="w")
            if no["tipo"] == "receita_sortimento":
                _, y_tributos = self._mundo_para_tela(
                    no["x"], no["y"] + ALTURA_TITULO_MODELO + ALTURA_POR_ENTRADA)
                self._desenhar_checkbox_tributos(pintor, no, tx, y_tributos, larg_tela)

    # ---------------- interação: arrastar nó / ligar pino ----------------

    @staticmethod
    def _pos_evento(evento):
        pos = evento.position()
        return pos.x(), pos.y()

    def _item_no_ponto(self, x, y):
        """Devolve uma tupla (tipo, no_id, indice) equivalente ao (item,
        tags) do Canvas original — tipo em ("pino_saida", "pino_entrada",
        "operador_fio", "fio", "grupo_resize", "grupo", "no"), ou None se
        nada foi clicado (canvas vazio — ou, se dentro de uma caixa de
        agrupamento fora da faixa de título, ainda "vazio" pra fins de
        clique, ver _desenhar_grupo)."""
        raio_clique = max(RAIO_PINO * self._zoom, 6) + 2
        ids_ordem = list(self.nos.keys())

        for no_id in reversed(ids_ordem):
            no = self.nos[no_id]
            for i in range(_n_saidas(no)):
                sx, sy = self._mundo_para_tela(*self._posicao_pino_saida(no_id, i))
                if (sx - x) ** 2 + (sy - y) ** 2 <= raio_clique ** 2:
                    return ("pino_saida", no_id, i)
            for i in range(_n_entradas(no)):
                ex, ey = self._mundo_para_tela(*self._posicao_pino_entrada(no_id, i))
                if (ex - x) ** 2 + (ey - y) ** 2 <= raio_clique ** 2:
                    return ("pino_entrada", no_id, i)

        raio_marcador = (RAIO_PINO + 3) * self._zoom + 2
        for con in reversed(self.conexoes):
            no_destino = self.nos.get(con["destino"])
            if no_destino is None or no_destino["tipo"] != "saida":
                continue
            x1, y1 = self._mundo_para_tela(*self._posicao_pino_saida(con["origem"], con.get("saida_idx", 0)))
            x2, y2 = self._mundo_para_tela(*self._posicao_pino_entrada(con["destino"], con["entrada_idx"]))
            dobra = con.get("dobra", 0.5)
            mx, my = x1 + (x2 - x1) * dobra, (y1 + y2) / 2
            if (mx - x) ** 2 + (my - y) ** 2 <= raio_marcador ** 2:
                return ("operador_fio", con["destino"], con["entrada_idx"])

        # Fio genérico — clicar/arrastar em qualquer trecho do traçado
        # (fora do marcador de operador acima), pra mudar a cor ou
        # "arrastar" a linha (reposicionar a dobra, ver _ao_arrastar).
        raio_fio = 5 + max(2, self._zoom)
        for i, con in enumerate(self.conexoes):
            x1, y1 = self._mundo_para_tela(*self._posicao_pino_saida(con["origem"], con.get("saida_idx", 0)))
            x2, y2 = self._mundo_para_tela(*self._posicao_pino_entrada(con["destino"], con["entrada_idx"]))
            dobra = con.get("dobra", 0.5)
            meio = x1 + (x2 - x1) * dobra
            for ax, ay, bx, by in ((x1, y1, meio, y1), (meio, y1, meio, y2), (meio, y2, x2, y2)):
                if _distancia_ponto_segmento(x, y, ax, ay, bx, by) <= raio_fio:
                    return ("fio", i, None)

        for grupo_id, grupo in self.grupos.items():
            gx0, gy0 = self._mundo_para_tela(grupo["x"], grupo["y"])
            largura = grupo["largura"] * self._zoom
            altura = grupo["altura"] * self._zoom
            alca = 14 * self._zoom
            if gx0 + largura - alca <= x <= gx0 + largura + 4 and gy0 + altura - alca <= y <= gy0 + altura + 4:
                return ("grupo_resize", grupo_id, None)
            altura_cabecalho = min(22 * self._zoom, altura)
            if gx0 <= x <= gx0 + largura and gy0 <= y <= gy0 + altura_cabecalho:
                return ("grupo", grupo_id, None)

        for no_id in reversed(ids_ordem):
            no = self.nos[no_id]
            retangulo_tributos = self._retangulo_checkbox_tributos(no)
            if retangulo_tributos is not None and retangulo_tributos.contains(QPointF(x, y)):
                return ("deduzir_tributos", no_id, None)
            retangulo_gravar = self._retangulo_checkbox_gravar(no)
            if retangulo_gravar is not None and retangulo_gravar.adjusted(-2, -2, 2, 2).contains(QPointF(x, y)):
                return ("gravar", no_id, None)

        for no_id in reversed(ids_ordem):
            no = self.nos[no_id]
            tx, ty = self._mundo_para_tela(no["x"], no["y"])
            largura = LARGURA_NO * self._zoom
            altura = self._altura_no(no) * self._zoom
            if tx <= x <= tx + largura and ty <= y <= ty + altura:
                return ("no", no_id, None)

        return None

    def _ao_pressionar(self, evento):
        x, y = self._pos_evento(evento)
        modificadores = evento.modifiers()
        item = self._item_no_ponto(x, y)

        if item is None:
            # Canvas vazio (ou dentro do corpo de uma caixa, fora da faixa
            # de título) — início de uma seleção por retângulo; Shift
            # preserva a seleção atual (soma), senão começa do zero.
            if not (modificadores & Qt.KeyboardModifier.ShiftModifier):
                self._selecionados = set()
            mx, my = self._tela_para_mundo(x, y)
            self._selecao_retangulo = (mx, my, mx, my)
            self._redesenhar()
            return

        tipo_item, no_id, indice = item

        if tipo_item == "gravar":
            self._alternar_gravar(no_id)
            return

        if tipo_item == "deduzir_tributos":
            no = self.nos[no_id]
            no["deduzir_tributos"] = not no.get("deduzir_tributos", False)
            self._redesenhar()
            return

        if tipo_item == "fio":
            self._arrastando_fio = no_id
            return

        if tipo_item == "grupo_resize":
            grupo = self.grupos[no_id]
            self._redimensionando_grupo = no_id
            mx, my = self._tela_para_mundo(x, y)
            self._redim_inicio = (mx, my, grupo["largura"], grupo["altura"])
            return

        if tipo_item == "grupo":
            self._iniciar_arraste_grupo(no_id, x, y)
            return

        if tipo_item == "operador_fio":
            if indice == 0:
                self._alternar_inverso(no_id, indice)
            else:
                self._alternar_operador(no_id, indice)
            return

        if tipo_item == "pino_saida":
            self._pino_origem = (no_id, indice)
            self._fio_temp_destino = self._tela_para_mundo(x, y)
            return

        if tipo_item == "pino_entrada":
            no = self.nos[no_id]
            if no["tipo"] == "saida":
                if indice < len(no["entradas"]):
                    self._remover_entrada(no_id, indice)
                return
            antes = len(self.conexoes)
            self.conexoes = [
                c for c in self.conexoes if not (c["destino"] == no_id and c["entrada_idx"] == indice)]
            if len(self.conexoes) != antes:
                self._redesenhar()
            return

        if tipo_item == "no":
            if modificadores & Qt.KeyboardModifier.ShiftModifier:
                if no_id in self._selecionados:
                    self._selecionados.discard(no_id)
                else:
                    self._selecionados.add(no_id)
                self._redesenhar()
                return
            if no_id not in self._selecionados:
                self._selecionados = {no_id}
                self._redesenhar()
            self._iniciar_arraste_nos(x, y)

    def _iniciar_arraste_nos(self, x, y):
        """Início de arraste de todos os nós selecionados juntos (se o nó
        clicado já fazia parte de uma seleção múltipla) — só guarda a
        posição de mundo inicial do mouse e a posição inicial de cada nó
        selecionado; _ao_arrastar aplica o delta a todos."""
        mx, my = self._tela_para_mundo(x, y)
        self._arraste_inicio_mundo = (mx, my)
        self._posicoes_iniciais_arraste = {
            nid: (self.nos[nid]["x"], self.nos[nid]["y"]) for nid in self._selecionados}
        self._arrastando_nos = True

    def _iniciar_arraste_grupo(self, grupo_id, x, y):
        """Início de arraste de uma caixa de agrupamento — recalcula quais
        nós estão "dentro" dela NESTE momento (pelo centro do nó, ver
        abaixo) e guarda a posição inicial de todos (caixa + nós) pra
        _ao_arrastar aplicar o mesmo delta."""
        grupo = self.grupos[grupo_id]
        mx, my = self._tela_para_mundo(x, y)
        self._arrastando_grupo = grupo_id
        self._arraste_inicio_mundo = (mx, my)
        self._grupo_posicao_inicial = (grupo["x"], grupo["y"])
        gx0, gy0 = grupo["x"], grupo["y"]
        gx1, gy1 = gx0 + grupo["largura"], gy0 + grupo["altura"]
        contidos = []
        for nid, no in self.nos.items():
            cx = no["x"] + LARGURA_NO / 2
            cy = no["y"] + self._altura_no(no) / 2
            if gx0 <= cx <= gx1 and gy0 <= cy <= gy1:
                contidos.append(nid)
        self._posicoes_iniciais_arraste = {nid: (self.nos[nid]["x"], self.nos[nid]["y"]) for nid in contidos}

    def _ao_arrastar(self, evento):
        x, y = self._pos_evento(evento)

        if self._arrastando_fio is not None:
            mx, _my = self._tela_para_mundo(x, y)
            con = self.conexoes[self._arrastando_fio]
            x1, _ = self._posicao_pino_saida(con["origem"], con.get("saida_idx", 0))
            x2, _ = self._posicao_pino_entrada(con["destino"], con["entrada_idx"])
            if x2 != x1:
                con["dobra"] = min(1.0, max(0.0, (mx - x1) / (x2 - x1)))
                self._redesenhar()
            return

        if self._redimensionando_grupo is not None:
            mx, my = self._tela_para_mundo(x, y)
            mx0, my0, larg0, alt0 = self._redim_inicio
            grupo = self.grupos[self._redimensionando_grupo]
            grupo["largura"] = max(100, larg0 + (mx - mx0))
            grupo["altura"] = max(70, alt0 + (my - my0))
            self._redesenhar()
            return

        if self._arrastando_grupo is not None or self._arrastando_nos:
            mx, my = self._tela_para_mundo(x, y)
            mx0, my0 = self._arraste_inicio_mundo
            dx, dy = mx - mx0, my - my0
            if self._arrastando_grupo is not None:
                gx0, gy0 = self._grupo_posicao_inicial
                grupo = self.grupos[self._arrastando_grupo]
                grupo["x"] = gx0 + dx
                grupo["y"] = gy0 + dy
            for nid, (x0, y0) in self._posicoes_iniciais_arraste.items():
                self.nos[nid]["x"] = x0 + dx
                self.nos[nid]["y"] = y0 + dy
            self._redesenhar()
            return

        if self._selecao_retangulo is not None:
            mx, my = self._tela_para_mundo(x, y)
            x0, y0, _x1, _y1 = self._selecao_retangulo
            self._selecao_retangulo = (x0, y0, mx, my)
            self._redesenhar()
            return

        if self._pino_origem is not None:
            self._fio_temp_destino = self._tela_para_mundo(x, y)
            self._redesenhar()

    def _ao_soltar(self, evento):
        x, y = self._pos_evento(evento)

        if self._selecao_retangulo is not None:
            x0, y0, x1, y1 = self._selecao_retangulo
            rx0, rx1 = min(x0, x1), max(x0, x1)
            ry0, ry1 = min(y0, y1), max(y0, y1)
            if (rx1 - rx0) > 2 or (ry1 - ry0) > 2:
                for nid, no in self.nos.items():
                    altura = self._altura_no(no)
                    if (no["x"] < rx1 and no["x"] + LARGURA_NO > rx0
                            and no["y"] < ry1 and no["y"] + altura > ry0):
                        self._selecionados.add(nid)
            self._selecao_retangulo = None
            self._redesenhar()
            return

        if self._arrastando_fio is not None:
            self._arrastando_fio = None
            return

        if self._redimensionando_grupo is not None:
            self._redimensionando_grupo = None
            return

        if self._arrastando_grupo is not None:
            self._arrastando_grupo = None
            return

        if self._arrastando_nos:
            self._arrastando_nos = False
            return

        if self._pino_origem is not None:
            origem_id, origem_saida_idx = self._pino_origem
            item = self._item_no_ponto(x, y)

            if item is not None and item[0] == "pino_entrada":
                _, destino_id, idx = item
                if destino_id != origem_id:
                    no_destino = self.nos[destino_id]
                    if no_destino["tipo"] == "saida":
                        if idx == len(no_destino["entradas"]):
                            no_destino["entradas"].append({"operador": "+"})
                            self.conexoes.append({
                                "origem": origem_id, "destino": destino_id, "entrada_idx": idx,
                                "saida_idx": origem_saida_idx})
                            if idx > 0:
                                ponto_global = self.canvas.mapToGlobal(evento.position().toPoint())
                                self._pino_origem = None
                                self._fio_temp_destino = None
                                self._redesenhar()
                                self._escolher_operador_popup(destino_id, idx, ponto_global)
                                return
                    else:
                        self.conexoes = [
                            c for c in self.conexoes
                            if not (c["destino"] == destino_id and c["entrada_idx"] == idx)]
                        self.conexoes.append({
                            "origem": origem_id, "destino": destino_id, "entrada_idx": idx,
                            "saida_idx": origem_saida_idx})
                self._pino_origem = None
                self._fio_temp_destino = None
                self._redesenhar()
                return

            # Soltou em cima do CORPO de uma Coluna ou Modelo — cria uma
            # Saída ali na hora, já combinando os dois nós (ver
            # _combinar_em_nova_saida).
            if item is not None and item[0] == "no":
                destino_id = item[1]
                if destino_id != origem_id and self.nos[destino_id]["tipo"] in ("coluna", "modelo"):
                    self._pino_origem = None
                    self._fio_temp_destino = None
                    mx, my = self._tela_para_mundo(x, y)
                    self._combinar_em_nova_saida(destino_id, origem_id, origem_saida_idx, mx, my)
                    return

            self._pino_origem = None
            self._fio_temp_destino = None
            self._redesenhar()

    def _ao_duplo_clique(self, evento):
        """Edita diretamente o nome mostrado na pílula branca do nó."""
        x, y = self._pos_evento(evento)
        item = self._item_no_ponto(x, y)
        if item is None or item[0] != "no":
            return
        no_id = item[1]
        no = self.nos[no_id]
        retangulo = self._retangulo_campo_nome(no)
        if retangulo is None or not retangulo.contains(QPointF(x, y)):
            return

        if no["tipo"] == "afilamento":
            self._configurar_nomes_afilamento(no_id)
        elif no["tipo"] == "recuperacao_weibull":
            self._configurar_nomes_recuperacao_weibull(no_id)
        elif no["tipo"] in (
                "saida", "calculo", "distribuicao", "acumulado", "receita_sortimento",
                "rendimento_sortimento", "vpl_sortimento", "vet_sortimento",
                "custo_colheita", "custo_formacao"):
            self._renomear_saida(no_id)

    def _combinar_em_nova_saida(self, no_a_id, no_b_id, saida_idx_b, x, y):
        novo_id = self._proximo_id
        self._proximo_id += 1
        self.nos[novo_id] = {
            "tipo": "saida", "x": x, "y": y, "rotulo": "Saída",
            "entradas": [{"operador": "+"}, {"operador": "+"}], "nome_saida": "", "gravar": True,
        }
        self.conexoes.append({"origem": no_a_id, "destino": novo_id, "entrada_idx": 0, "saida_idx": 0})
        self.conexoes.append({
            "origem": no_b_id, "destino": novo_id, "entrada_idx": 1, "saida_idx": saida_idx_b})
        self._redesenhar()
        self._renomear_saida(novo_id)

    def _montar_menu_operador(self, no_id, idx):
        entrada = self.nos[no_id]["entradas"][idx]
        menu = QMenu(self)
        if idx > 0:
            for operador in OPERADORES:
                rotulo = _ROTULO_OPERADOR_MENU.get(operador, f"{SIMBOLO_OPERADOR[operador]} ({operador})")
                menu.addAction(rotulo, lambda op=operador: self._definir_operador(no_id, idx, op))
            menu.addSeparator()
        marca = "✓ " if entrada.get("inverso") else ""
        menu.addAction(f"{marca}Usar 1/x nesta entrada", lambda: self._alternar_inverso(no_id, idx))
        return menu

    def _escolher_operador_popup(self, no_id, indice, ponto_global):
        self._montar_menu_operador(no_id, indice).exec(ponto_global)

    def _ao_botao_direito(self, evento):
        x, y = self._pos_evento(evento)
        item = self._item_no_ponto(x, y)
        ponto_global = self.canvas.mapToGlobal(evento.position().toPoint())

        if item is None:
            self._abrir_menu_canvas_vazio(x, y, ponto_global)
            return

        tipo_item, no_id, indice = item
        if tipo_item in ("gravar", "deduzir_tributos"):
            # Checkbox "Gravar" fica dentro do corpo do nó — botão direito
            # ali abre o menu normal do nó, igual clicar em qualquer outro
            # ponto do corpo (o toggle rápido é só no clique esquerdo, ver
            # _ao_pressionar).
            tipo_item = "no"

        if tipo_item == "fio":
            self._abrir_menu_fio(no_id, ponto_global)
            return

        if tipo_item == "grupo":
            self._abrir_menu_grupo(no_id, ponto_global)
            return

        if tipo_item == "grupo_resize":
            return

        if tipo_item == "operador_fio":
            self._montar_menu_operador(no_id, indice).exec(ponto_global)
            return

        if tipo_item == "pino_entrada":
            no = self.nos[no_id]
            if no["tipo"] in (
                    "calculo", "distribuicao", "acumulado", "receita_sortimento",
                    "rendimento_sortimento", "vpl_sortimento", "vet_sortimento",
                    "recuperacao_weibull", "custo_colheita"):
                return
            if no["tipo"] == "saida":
                if indice >= len(no["entradas"]):
                    return
                rotulo_entrada = f"entrada {indice + 1}"
            else:
                rotulo_entrada = f"\"{no['variaveis'][indice]}\""
            menu = QMenu(self)
            menu.addAction(f"Remover {rotulo_entrada}", lambda: self._remover_entrada(no_id, indice))
            menu.exec(ponto_global)
            return

        if tipo_item != "no":
            return
        no = self.nos[no_id]

        menu = QMenu(self)
        menu.addAction("Adicionar saída...", lambda: self._adicionar_saida(no_id))
        menu.addAction("Adicionar cálculo...", lambda: self._adicionar_calculo(no_id))
        if no["tipo"] == "modelo":
            menu.addAction("Adicionar entrada...", lambda: self._adicionar_entrada(no_id))
        if no["tipo"] in (
                "modelo", "saida", "calculo", "distribuicao", "acumulado", "receita_sortimento",
                "rendimento_sortimento", "vpl_sortimento", "vet_sortimento", "custo_colheita",
                "custo_formacao"):
            menu.addAction("Renomear coluna de saída...", lambda: self._renomear_saida(no_id))
            marca = "✓ " if no.get("gravar", True) else ""
            menu.addAction(f"{marca}Gravar na tabela ao salvar", lambda: self._alternar_gravar(no_id))
        if no["tipo"] == "acumulado":
            menu.addAction(
                "Configurar agrupamento (grupo/ordem)...", lambda: self._configurar_acumulado(no_id))
        if no["tipo"] == "receita_sortimento":
            acao_tributos = menu.addAction("Deduzir PIS, COFINS e FUNRURAL")
            acao_tributos.setCheckable(True)
            acao_tributos.setChecked(no.get("deduzir_tributos", False))
            acao_tributos.triggered.connect(
                lambda marcado: self._definir_deducao_tributos_receita(no_id, marcado))
            tipo_preco_atual = no.get("tipo_preco", "serrada")
            submenu_preco = menu.addMenu(
                "Preço: "
                + ("Madeira em Pé" if tipo_preco_atual == "pe" else "Madeira Serrada"))
            for valor, rotulo in (("serrada", "Madeira Serrada (R$/m³)"), ("pe", "Madeira em Pé (R$/m³)")):
                acao_preco = submenu_preco.addAction(
                    rotulo, lambda v=valor: self._definir_tipo_preco_receita(no_id, v))
                acao_preco.setCheckable(True)
                acao_preco.setChecked(tipo_preco_atual == valor)
        if no["tipo"] == "custo_colheita":
            submenu_custo = menu.addMenu(
                f"Custo de colheita: {no.get('custo_colheita_nome') or '(nenhum selecionado)'}")
            custos_cadastrados = self._custos_colheita_cadastrados()
            if not custos_cadastrados:
                acao_vazia = submenu_custo.addAction("Nenhum custo cadastrado (tela Configurações)")
                acao_vazia.setEnabled(False)
            else:
                for custo_id, nome_custo in custos_cadastrados:
                    acao = submenu_custo.addAction(
                        nome_custo,
                        lambda cid=custo_id, nm=nome_custo: self._definir_custo_colheita(no_id, cid, nm))
                    acao.setCheckable(True)
                    acao.setChecked(no.get("custo_colheita_id") == custo_id)
        if no["tipo"] == "custo_formacao":
            acao_excluir = menu.addAction(
                "Excluir idades de formação (idade <= 0) de outros cálculos do construtor",
                lambda: self._alternar_excluir_outras_contas_formacao(no_id))
            acao_excluir.setCheckable(True)
            acao_excluir.setChecked(no.get("excluir_outras_contas", True))
            acao_excluir.setToolTip(
                "Ligado (padrão): as linhas de idade <= 0 que este nó insere (custo de "
                "formação anterior ao plantio) viram NaN em TODO OUTRO nó do grafo — elas só "
                "têm valor de verdade no próprio Custo de Formação (sem distribuição "
                "diamétrica, volume por classe etc, calcular algo ali daria lixo). Desligado: "
                "outros nós tentam calcular normalmente nessas linhas também (quase sempre "
                "vira NaN de qualquer jeito, já que os dados que alimentam esses nós também "
                "estão vazios ali — só desligue se tiver um motivo específico).")
        if no["tipo"] in (
                "receita_sortimento", "rendimento_sortimento", "vpl_sortimento", "vet_sortimento",
                "custo_colheita", "saida"):
            eventos_no = no.get("eventos_manejo")
            if eventos_no:
                rotulo_eventos = f" ({', '.join(eventos_no)})"
            elif no["tipo"] in ("saida", "vpl_sortimento", "vet_sortimento"):
                rotulo_eventos = " (sem restrição — todas as idades)"
            else:
                rotulo_eventos = " (todos)"
            menu.addAction(
                f"Configurar eventos...{rotulo_eventos}", lambda: self._configurar_eventos_no(no_id))
        if no["tipo"] == "afilamento":
            menu.addAction(
                "Configurar saídas (nomes)...", lambda: self._configurar_nomes_afilamento(no_id))
            marca = "✓ " if no.get("gravar", True) else ""
            menu.addAction(f"{marca}Gravar na tabela ao salvar", lambda: self._alternar_gravar(no_id))
        if no["tipo"] == "recuperacao_weibull":
            menu.addAction(
                "Configurar saídas (nomes)...", lambda: self._configurar_nomes_recuperacao_weibull(no_id))
            marca = "✓ " if no.get("gravar", True) else ""
            menu.addAction(f"{marca}Gravar na tabela ao salvar", lambda: self._alternar_gravar(no_id))
        if no["tipo"] == "calculo":
            menu.addSeparator()
            menu.addAction("Adicionar passo...", lambda: self._popup_novo_passo(no_id, ponto_global))
            for i, passo in enumerate(no.get("passos", [])):
                submenu = menu.addMenu(f"Passo {i + 1}: {_rotulo_passo(passo)}")
                if "valor" in passo:
                    submenu.addAction("Editar valor...", lambda i=i: self._editar_valor_passo(no_id, i))
                submenu.addAction("Remover", lambda i=i: self._remover_passo(no_id, i))

        menu.addSeparator()
        # Se o nó clicado faz parte de uma seleção múltipla, a cor
        # escolhida aplica em todos os selecionados de uma vez — senão,
        # só neste nó (ver _escolher_cor_borda_no_individual/
        # _escolher_cor_fundo_no).
        ids_alvo = self._selecionados if (no_id in self._selecionados and len(self._selecionados) > 1) \
            else [no_id]
        rotulo_alvo = f"{len(ids_alvo)} nós selecionados" if len(ids_alvo) > 1 else "este nó"
        menu.addAction(
            f"Borda: cor, transparência e espessura ({rotulo_alvo})...",
            lambda: self._escolher_cor_borda_no_individual(ids_alvo))
        menu.addAction(f"Cor do nó ({rotulo_alvo})...", lambda: self._escolher_cor_fundo_no(ids_alvo))
        if any(
                self.nos[i].get("cor_borda") or self.nos[i].get("cor_fundo")
                or "espessura_borda" in self.nos[i] for i in ids_alvo):
            menu.addAction("Redefinir cores personalizadas", lambda: self._redefinir_cores_no(ids_alvo))

        menu.addAction("Renomear nó...", lambda: self._renomear_no(no_id))
        menu.addAction("Excluir nó", lambda: self._excluir_no(no_id))
        menu.exec(ponto_global)

    def _escolher_cor_borda_no_individual(self, ids):
        primeiro = self.nos[ids[0]]
        cor_inicial = QColor(
            primeiro.get("cor_borda") or self._cor_borda_no.name(QColor.NameFormat.HexArgb))
        espessura_inicial = primeiro.get("espessura_borda", self._espessura_borda_no)
        resultado = self._dialogo_estilo_borda(
            cor_inicial, espessura_inicial, "Borda do nó")
        if resultado is None:
            return
        cor, espessura = resultado
        for nid in ids:
            self.nos[nid]["cor_borda"] = cor.name(QColor.NameFormat.HexArgb)
            self.nos[nid]["espessura_borda"] = espessura
        self._redesenhar()

    def _escolher_cor_fundo_no(self, ids):
        cor_inicial = QColor(self.nos[ids[0]].get("cor_fundo") or _cor_no(self.nos[ids[0]]))
        cor = QColorDialog.getColor(cor_inicial, self, "Cor do nó")
        if not cor.isValid():
            return
        for nid in ids:
            self.nos[nid]["cor_fundo"] = cor.name()
        self._redesenhar()

    def _redefinir_cores_no(self, ids):
        for nid in ids:
            self.nos[nid].pop("cor_borda", None)
            self.nos[nid].pop("espessura_borda", None)
            self.nos[nid].pop("cor_fundo", None)
        self._redesenhar()

    # ---------------- caixas de agrupamento ----------------

    def _abrir_menu_canvas_vazio(self, x, y, ponto_global):
        menu = QMenu(self)
        mx, my = self._tela_para_mundo(x, y)
        if self._selecionados:
            menu.addAction(
                f"Criar caixa de agrupamento ao redor de {len(self._selecionados)} nó(s) selecionado(s)",
                lambda: self._criar_grupo(ao_redor_selecao=True))
        menu.addAction("Criar caixa de agrupamento aqui", lambda: self._criar_grupo(x_mundo=mx, y_mundo=my))
        menu.exec(ponto_global)

    def _criar_grupo(self, x_mundo=0.0, y_mundo=0.0, ao_redor_selecao=False):
        """Caixa pontilhada — arrastar pela faixa de título (ver
        _item_no_ponto/_iniciar_arraste_grupo) move junto todo nó cuja
        posição estiver dentro dela NAQUELE momento (não guarda uma lista
        fixa de membros)."""
        grupo_id = self._proximo_grupo_id
        self._proximo_grupo_id += 1
        if ao_redor_selecao and self._selecionados:
            padding = 30
            xs0 = [self.nos[nid]["x"] for nid in self._selecionados]
            ys0 = [self.nos[nid]["y"] for nid in self._selecionados]
            xs1 = [self.nos[nid]["x"] + LARGURA_NO for nid in self._selecionados]
            ys1 = [self.nos[nid]["y"] + self._altura_no(self.nos[nid]) for nid in self._selecionados]
            x0, y0 = min(xs0) - padding, min(ys0) - padding - 22
            largura, altura = max(xs1) - x0 + padding, max(ys1) - y0 + padding
        else:
            x0, y0, largura, altura = x_mundo - 20, y_mundo - 20, 320, 220
        self.grupos[grupo_id] = {
            "titulo": "Grupo", "x": x0, "y": y0, "largura": largura, "altura": altura, "cor_borda": None,
        }
        self._redesenhar()

    def _abrir_menu_grupo(self, grupo_id, ponto_global):
        menu = QMenu(self)
        menu.addAction("Renomear caixa...", lambda: self._renomear_grupo(grupo_id))
        menu.addAction("Cor da caixa...", lambda: self._escolher_cor_grupo(grupo_id))
        menu.addAction("Excluir caixa (mantém os nós)", lambda: self._excluir_grupo(grupo_id))
        menu.exec(ponto_global)

    def _renomear_grupo(self, grupo_id):
        grupo = self.grupos[grupo_id]
        nome, ok = QInputDialog.getText(self, "Renomear caixa", "Título da caixa:", text=grupo.get("titulo", ""))
        if ok:
            grupo["titulo"] = nome.strip()
            self._redesenhar()

    def _escolher_cor_grupo(self, grupo_id):
        grupo = self.grupos[grupo_id]
        cor_inicial = QColor(grupo.get("cor_borda") or COR_GRUPO_PADRAO)
        cor = QColorDialog.getColor(cor_inicial, self, "Cor da caixa")
        if not cor.isValid():
            return
        grupo["cor_borda"] = cor.name()
        self._redesenhar()

    def _excluir_grupo(self, grupo_id):
        del self.grupos[grupo_id]
        self._redesenhar()

    # ---------------- fios: cor, curva, exclusão ----------------

    def _abrir_menu_fio(self, indice_conexao, ponto_global):
        con = self.conexoes[indice_conexao]
        menu = QMenu(self)
        menu.addAction("Cor da linha...", lambda: self._escolher_cor_fio(indice_conexao))
        if con.get("cor"):
            menu.addAction("Redefinir cor da linha", lambda: self._redefinir_cor_fio(indice_conexao))
        if con.get("dobra") is not None:
            menu.addAction(
                "Redefinir posição da linha (arrastada)", lambda: self._redefinir_dobra_fio(indice_conexao))
        menu.addSeparator()
        menu.addAction("Excluir esta ligação", lambda: self._excluir_conexao(indice_conexao))
        menu.exec(ponto_global)

    def _escolher_cor_fio(self, indice_conexao):
        con = self.conexoes[indice_conexao]
        cor_inicial = QColor(con.get("cor") or COR_FIO)
        cor = QColorDialog.getColor(cor_inicial, self, "Cor da linha")
        if not cor.isValid():
            return
        con["cor"] = cor.name()
        self._redesenhar()

    def _redefinir_cor_fio(self, indice_conexao):
        self.conexoes[indice_conexao].pop("cor", None)
        self._redesenhar()

    def _redefinir_dobra_fio(self, indice_conexao):
        self.conexoes[indice_conexao].pop("dobra", None)
        self._redesenhar()

    def _excluir_conexao(self, indice_conexao):
        if indice_conexao >= len(self.conexoes):
            return
        con = self.conexoes[indice_conexao]
        no_destino = self.nos.get(con["destino"])
        if no_destino is not None and no_destino["tipo"] == "saida":
            self._remover_entrada(con["destino"], con["entrada_idx"])
            return
        self.conexoes = [c for c in self.conexoes if c is not con]
        self._redesenhar()

    def _adicionar_saida(self, no_origem_id):
        no_origem = self.nos[no_origem_id]
        novo_id = self._proximo_id
        self._proximo_id += 1
        self.nos[novo_id] = {
            "tipo": "saida", "x": no_origem["x"] + LARGURA_NO + 60, "y": no_origem["y"],
            "rotulo": "Saída", "entradas": [{"operador": "+"}], "nome_saida": "", "gravar": True,
        }
        self.conexoes.append({"origem": no_origem_id, "destino": novo_id, "entrada_idx": 0, "saida_idx": 0})
        self._redesenhar()
        self._renomear_saida(novo_id)

    def _adicionar_calculo(self, no_origem_id):
        no_origem = self.nos[no_origem_id]
        novo_id = self._proximo_id
        self._proximo_id += 1
        self.nos[novo_id] = {
            "tipo": "calculo", "x": no_origem["x"] + LARGURA_NO + 60, "y": no_origem["y"],
            "rotulo": "Cálculo", "passos": [], "nome_saida": "", "gravar": True,
        }
        self.conexoes.append({"origem": no_origem_id, "destino": novo_id, "entrada_idx": 0, "saida_idx": 0})
        self._redesenhar()

    def _popup_novo_passo(self, no_id, ponto_global):
        menu = QMenu(self)
        menu.addAction("² (elevar ao quadrado)", lambda: self._acrescentar_passo(no_id, "^", 2.0))
        menu.addAction("× π", lambda: self._acrescentar_passo(no_id, "*", math.pi))
        menu.addSeparator()
        for operador in OPERADORES:
            menu.addAction(
                f"{SIMBOLO_OPERADOR[operador]} (constante)...",
                lambda op=operador: self._pedir_valor_passo(no_id, op))
        menu.addSeparator()
        for operador, rotulo in ROTULO_REDUCAO_CLASSE.items():
            menu.addAction(rotulo, lambda op=operador: self._acrescentar_passo_reducao(no_id, op))
        menu.exec(ponto_global)

    def _pedir_valor_passo(self, no_id, operador, indice=None):
        atual = self.nos[no_id]["passos"][indice]["valor"] if indice is not None else 0.0
        valor, ok = QInputDialog.getDouble(
            self, "Passo do Cálculo",
            f"Valor da constante pra aplicar com \"{SIMBOLO_OPERADOR[operador]}\":",
            atual, -1e12, 1e12, 6)
        if not ok:
            return
        self._acrescentar_passo(no_id, operador, valor, indice)

    def _acrescentar_passo(self, no_id, operador, valor, indice=None):
        passos = self.nos[no_id].setdefault("passos", [])
        if indice is None:
            passos.append({"operador": operador, "valor": valor})
        else:
            passos[indice] = {"operador": operador, "valor": valor}
        self._redesenhar()

    def _acrescentar_passo_reducao(self, no_id, operador):
        self.nos[no_id].setdefault("passos", []).append({"operador": operador})
        self._redesenhar()

    def _editar_valor_passo(self, no_id, indice):
        operador = self.nos[no_id]["passos"][indice]["operador"]
        self._pedir_valor_passo(no_id, operador, indice)

    def _remover_passo(self, no_id, indice):
        del self.nos[no_id]["passos"][indice]
        self._redesenhar()

    def _renomear_no(self, no_id):
        """Nome próprio do nó (ver _titulo_no) — só rótulo visual no canvas,
        independente do nome da coluna de saída (_renomear_saida, logo
        abaixo) e de `no["rotulo"]` (usado nas mensagens de erro do motor,
        ver core/construtores.py:avaliar_grafo) — não muda nenhum dos
        dois."""
        no = self.nos[no_id]
        nome, ok = QInputDialog.getText(
            self, "Renomear nó",
            "Nome próprio pra este nó no canvas (em branco = sem nome, só o "
            "título automático):",
            text=no.get("nome_personalizado", ""))
        if ok:
            no["nome_personalizado"] = nome.strip()
            self._redesenhar()

    def _renomear_saida(self, no_id):
        no = self.nos[no_id]
        nome, ok = QInputDialog.getText(
            self, "Nome da coluna de saída",
            "Nome da coluna que essa saída vai gerar (em branco = sem nome — "
            "não grava, mesmo marcada):",
            text=no.get("nome_saida", ""))
        if ok:
            no["nome_saida"] = nome.strip()
            self._redesenhar()

    def _alternar_gravar(self, no_id):
        no = self.nos[no_id]
        no["gravar"] = not no.get("gravar", True)
        self._redesenhar()

    def _alternar_excluir_outras_contas_formacao(self, no_id):
        no = self.nos[no_id]
        no["excluir_outras_contas"] = not no.get("excluir_outras_contas", True)
        self._redesenhar()

    def _alternar_operador(self, no_id, indice):
        no = self.nos[no_id]
        atual = no["entradas"][indice].get("operador", "+")
        proximo = OPERADORES[(OPERADORES.index(atual) + 1) % len(OPERADORES)]
        self._definir_operador(no_id, indice, proximo)

    def _alternar_inverso(self, no_id, indice):
        entrada = self.nos[no_id]["entradas"][indice]
        entrada["inverso"] = not entrada.get("inverso", False)
        self._redesenhar()

    def _definir_operador(self, no_id, indice, operador):
        self.nos[no_id]["entradas"][indice]["operador"] = operador
        self._redesenhar()

    def _adicionar_entrada(self, no_id):
        no = self.nos[no_id]
        nome, ok = QInputDialog.getText(
            self, "Nova entrada",
            f"Nome da variável de entrada de \"{no['rotulo']}\" (use o mesmo nome que a "
            "equação do modelo espera, ex: \"x\", \"x1\", \"dap_med_atual\"):")
        if not ok or not nome.strip():
            return
        nome = nome.strip()
        if nome in no["variaveis"]:
            QMessageBox.warning(self, "Construtor de Variáveis", f"\"{nome}\" já é uma entrada desse nó.")
            return
        no["variaveis"].append(nome)
        self._redesenhar()

    def _remover_entrada(self, no_id, indice):
        no = self.nos[no_id]
        lista = no["entradas"] if no["tipo"] == "saida" else no["variaveis"]
        del lista[indice]
        novas_conexoes = []
        for c in self.conexoes:
            if c["destino"] == no_id:
                if c["entrada_idx"] == indice:
                    continue
                if c["entrada_idx"] > indice:
                    c = dict(c, entrada_idx=c["entrada_idx"] - 1)
            novas_conexoes.append(c)
        self.conexoes = novas_conexoes
        self._redesenhar()

    def _excluir_no(self, no_id):
        del self.nos[no_id]
        self.conexoes = [c for c in self.conexoes if c["origem"] != no_id and c["destino"] != no_id]
        self._selecionados.discard(no_id)
        self._redesenhar()

    # ---------------- diálogos de configuração por tipo de nó ----------------

    def _definir_custo_colheita(self, no_id, custo_id, nome):
        no = self.nos[no_id]
        no["custo_colheita_id"] = custo_id
        no["custo_colheita_nome"] = nome
        self._redesenhar()

    def _definir_tipo_preco_receita(self, no_id, tipo_preco):
        """"Preço" do nó "Receita Total" (botão direito) — "serrada"
        (padrão, coluna `preco` de sortimentos) ou "pe" (coluna
        `preco_pe`) — ver core/construtores.py:_preco_sortimento_da_classe.
        Sem escolha salva no nó, "serrada" é o padrão (mesmo preço único
        de sempre, antes de existir Madeira em Pé)."""
        no = self.nos[no_id]
        no["tipo_preco"] = tipo_preco
        self._redesenhar()

    def _definir_deducao_tributos_receita(self, no_id, habilitada):
        self.nos[no_id]["deduzir_tributos"] = bool(habilitada)
        self._redesenhar()

    def _configurar_acumulado(self, no_id):
        no = self.nos[no_id]
        colunas = list(self._colunas_disponiveis)
        if not colunas:
            QMessageBox.warning(
                self, "Construtor de Variáveis",
                "Nenhuma coluna disponível pra escolher — selecione a tabela de origem primeiro.")
            return

        dialogo = QDialog(self)
        dialogo.setWindowTitle("Configurar Acumulado")
        layout = QVBoxLayout(dialogo)
        layout.addWidget(QLabel("Coluna de grupo (ex: talhão)"))
        combo_grupo = QComboBox()
        combo_grupo.addItems(colunas)
        if no.get("coluna_grupo") in colunas:
            combo_grupo.setCurrentText(no["coluna_grupo"])
        layout.addWidget(combo_grupo)
        layout.addWidget(QLabel("Coluna de ordem (ex: idade simulada)"))
        combo_ordem = QComboBox()
        combo_ordem.addItems(colunas)
        if no.get("coluna_ordem") in colunas:
            combo_ordem.setCurrentText(no["coluna_ordem"])
        layout.addWidget(combo_ordem)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(botoes)

        def confirmar():
            no["coluna_grupo"] = combo_grupo.currentText()
            no["coluna_ordem"] = combo_ordem.currentText()
            dialogo.accept()

        botoes.accepted.connect(confirmar)
        botoes.rejected.connect(dialogo.reject)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._redesenhar()

    def _configurar_nomes_afilamento(self, no_id):
        no = self.nos[no_id]
        dialogo = QDialog(self)
        dialogo.setWindowTitle("Configurar saídas — Afilamento")
        layout = QVBoxLayout(dialogo)
        layout.addWidget(QLabel("Nome da coluna — Volume aproveitável"))
        entry_a = QLineEdit(no.get("nome_saida_aproveitavel", ""))
        layout.addWidget(entry_a)
        layout.addWidget(QLabel("Nome da coluna — Volume de biomassa"))
        entry_b = QLineEdit(no.get("nome_saida_biomassa", ""))
        layout.addWidget(entry_b)
        checkbox_gravar = QCheckBox("Gravar na tabela ao salvar")
        checkbox_gravar.setChecked(no.get("gravar", True))
        layout.addWidget(checkbox_gravar)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(botoes)

        def confirmar():
            no["nome_saida_aproveitavel"] = entry_a.text().strip()
            no["nome_saida_biomassa"] = entry_b.text().strip()
            no["gravar"] = checkbox_gravar.isChecked()
            dialogo.accept()

        botoes.accepted.connect(confirmar)
        botoes.rejected.connect(dialogo.reject)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._redesenhar()

    def _configurar_nomes_recuperacao_weibull(self, no_id):
        no = self.nos[no_id]
        dialogo = QDialog(self)
        dialogo.setWindowTitle("Configurar saídas — Recuperação Weibull")
        layout = QVBoxLayout(dialogo)
        layout.addWidget(QLabel("Nome da coluna — Forma"))
        entry_forma = QLineEdit(no.get("nome_saida_forma", ""))
        layout.addWidget(entry_forma)
        layout.addWidget(QLabel("Nome da coluna — Escala"))
        entry_escala = QLineEdit(no.get("nome_saida_escala", ""))
        layout.addWidget(entry_escala)
        checkbox_gravar = QCheckBox("Gravar na tabela ao salvar")
        checkbox_gravar.setChecked(no.get("gravar", True))
        layout.addWidget(checkbox_gravar)

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(botoes)

        def confirmar():
            no["nome_saida_forma"] = entry_forma.text().strip()
            no["nome_saida_escala"] = entry_escala.text().strip()
            no["gravar"] = checkbox_gravar.isChecked()
            dialogo.accept()

        botoes.accepted.connect(confirmar)
        botoes.rejected.connect(dialogo.reject)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._redesenhar()

    def _configurar_eventos_no(self, no_id):
        no = self.nos[no_id]
        selecionados_atuais = set(no.get("eventos_manejo") or [])

        dialogo = QDialog(self)
        dialogo.setWindowTitle("Configurar eventos")
        layout = QVBoxLayout(dialogo)
        rotulo = QLabel(
            "Calcular só nas idades destes eventos (nenhum marcado = qualquer evento preenchido):")
        rotulo.setWordWrap(True)
        layout.addWidget(rotulo)

        checkboxes = {}
        for evento in construtores.EVENTOS_MANEJO_CONFIGURAVEIS:
            checkbox = QCheckBox(evento)
            checkbox.setChecked(evento in selecionados_atuais)
            layout.addWidget(checkbox)
            checkboxes[evento] = checkbox

        botoes = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(botoes)

        def confirmar():
            no["eventos_manejo"] = [evento for evento, cb in checkboxes.items() if cb.isChecked()]
            dialogo.accept()

        botoes.accepted.connect(confirmar)
        botoes.rejected.connect(dialogo.reject)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self._redesenhar()

    # ---------------- avaliação do grafo (delega pra core/construtores.py) ----------------

    def _avaliar_grafo(self, conn, debug_tempos=None):
        tabela = self.combo_tabela_origem.currentText()
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{tabela}"', conn)
        except Exception as e:
            raise ValueError(f"Não foi possível ler \"{tabela}\": {e}")
        if "id" not in df.columns:
            raise ValueError(f"A tabela \"{tabela}\" não tem coluna \"id\" — não dá pra gravar de volta.")

        try:
            classes_diametricas = simulacao.obter_classes_diametricas(conn)
        except ValueError:
            classes_diametricas = None

        sortimentos = conn.execute(
            "SELECT nome, limite_inferior, limite_superior, rendimento, preco, preco_pe "
            "FROM sortimentos ORDER BY limite_inferior, nome"
        ).fetchall()
        config_financeiro = construtores.obter_config_financeiro(conn)
        idade_corte_raso = simulacao.obter_idade_corte_raso(conn)
        dimensoes_tora = construtores.obter_dimensoes_tora(conn)
        custos_colheita = construtores.obter_custos_colheita(conn)
        custos_formacao = construtores.obter_custos_formacao(conn)
        tipo_normalizacao_weibull = simulacao.obter_tipo_normalizacao_weibull(conn)

        valores, erros = construtores.avaliar_grafo(
            df, self.nos, self.conexoes, classes_diametricas, sortimentos, config_financeiro,
            idade_corte_raso, dimensoes_tora, custos_colheita,
            tipo_normalizacao_weibull=tipo_normalizacao_weibull, custos_formacao=custos_formacao,
            debug_tempos=debug_tempos)
        return df, valores, erros

    def _resumo_tempos(self, debug_tempos, minimo_segundos=0.05, top_n=3):
        if not debug_tempos:
            return ""
        itens_ordenados = sorted(debug_tempos.items(), key=lambda item: item[1], reverse=True)
        mais_lentos = itens_ordenados[:top_n]
        partes = [
            f"{self.nos[no_id]['rotulo']} ({tempo:.2f}s)"
            for no_id, tempo in mais_lentos if tempo >= minimo_segundos
        ]
        if not partes:
            return ""
        restantes = itens_ordenados[top_n:]
        tempo_restante = sum(tempo for _, tempo in restantes)
        if restantes and tempo_restante >= minimo_segundos:
            partes.append(f"outros {len(restantes)} nó(s) ({tempo_restante:.2f}s)")
        return f"Mais lentos: {'; '.join(partes)}"

    # ---------------- prévia ----------------

    def testar(self):
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Construtor de Variáveis", str(e))
            return
        self._area_previa_widget.show()
        debug_tempos = {}
        try:
            try:
                df, valores, erros = self._avaliar_grafo(conn, debug_tempos=debug_tempos)
            except ValueError as e:
                QMessageBox.warning(self, "Construtor de Variáveis", str(e))
                return
        finally:
            conn.close()

        saidas = construtores.saidas_nomeadas(self.nos, valores)

        if not saidas:
            self.tabela_previa.redefinir_colunas([])
            texto = (
                "Nada pra mostrar ainda — nenhum nó com nome de saída definido (botão "
                "direito no nó) e marcado \"Gravar na tabela ao salvar\".")
            if erros:
                texto += " " + "; ".join(erros)
            resumo_tempos = self._resumo_tempos(debug_tempos)
            if resumo_tempos:
                texto += f" {resumo_tempos}"
            self.label_status.setText(texto)
            tema_qss.aplicar_status(self.label_status, "aviso")
            return

        colunas = ["id"] + list(saidas.keys())
        self.tabela_previa.redefinir_colunas(colunas, tipos_iniciais={c: "Float" for c in saidas})

        n = min(len(df), PREVIEW_LINHAS)
        linhas = [
            (int(df["id"].iloc[i]),) + tuple(
                None if pd.isna(serie.iloc[i]) else float(serie.iloc[i]) for serie in saidas.values()
            )
            for i in range(n)
        ]
        self.tabela_previa.definir_linhas(linhas)

        aviso = f" (mostrando as primeiras {PREVIEW_LINHAS:,})" if len(df) > PREVIEW_LINHAS else ""
        texto = f"Prévia: {n:,} de {len(df):,} linha(s){aviso}, {len(saidas)} saída(s)."
        if erros:
            texto += " Pendências: " + "; ".join(erros)
        resumo_tempos = self._resumo_tempos(debug_tempos)
        if resumo_tempos:
            texto += f" — {resumo_tempos}"
        self.label_status.setText(texto)
        tema_qss.aplicar_status(self.label_status, "aviso" if erros else "sucesso")

    # ---------------- construtores salvos: abrir no canvas ----------------
    # (Duplicar/Excluir/Ativar-Desativar viraram tela Configurações — ver
    # app/screens/configuracoes.py:_montar_secao_construtores — esta tela
    # só abre um construtor salvo pra editar, ver _abrir_menu_construtores)

    def abrir_construtor(self, resumo):
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Construtor de Variáveis", str(e))
            return
        try:
            construtor = construtores.obter_construtor(conn, resumo["id"])
        except ValueError as e:
            QMessageBox.warning(self, "Construtor de Variáveis", str(e))
            self._atualizar_construtores_disponiveis()
            return
        finally:
            conn.close()

        self.nos = construtor["grafo"]["nos"]
        self.conexoes = construtor["grafo"]["conexoes"]
        # "grupos" (caixas de agrupamento) não existe em construtores salvos
        # antes dessa funcionalidade — {} pra esses casos. Chaves vêm como
        # string do JSON (json.loads), igual acontecia com "nos" antes de
        # obter_construtor converter (ver core/construtores.py:_desserializar_linha).
        self.grupos = {int(k): v for k, v in construtor["grafo"].get("grupos", {}).items()}
        self._proximo_grupo_id = (max(self.grupos.keys()) + 1) if self.grupos else 1
        self._selecionados = set()
        self._proximo_id = (max(self.nos.keys()) + 1) if self.nos else 1
        self.construtor_atual_id = construtor["id"]
        self.combo_tabela_origem.setCurrentText(construtor["tabela_origem"])
        self._atualizar_colunas_disponiveis()
        self._refrescar_modelos_dos_nos()
        self._redesenhar()
        self._area_previa_widget.show()
        self.label_status.setText(f"Construtor \"{construtor['nome']}\" carregado.")
        tema_qss.aplicar_status(self.label_status, "sucesso")

    def _abrir_menu_construtores(self):
        """Menu flutuante com os construtores salvos (✓/✗ = reaplicado
        automaticamente ou não — ver construtores.definir_ativo) — clicar
        num item já carrega o grafo dele no canvas (equivalente ao antigo
        botão "Abrir" + seleção na lista)."""
        menu = QMenu(self)
        if not self._construtores_disponiveis:
            acao_vazia = menu.addAction("Nenhum construtor salvo ainda")
            acao_vazia.setEnabled(False)
        else:
            for resumo in self._construtores_disponiveis:
                marcador = "✓" if resumo["ativo"] else "✗"
                rotulo = f"{marcador} {resumo['nome']} ({resumo['tabela_origem']})"
                menu.addAction(rotulo, lambda r=resumo: self.abrir_construtor(r))
        botao = self.botao_construtores
        menu.exec(botao.mapToGlobal(botao.rect().bottomLeft()))

    def _abrir_dialogo_construtores(self):
        """Gerencia os construtores salvos sem sair do editor visual."""
        dialogo = QDialog(self)
        dialogo.setWindowTitle("Construtores salvos")
        dialogo.resize(680, 390)
        layout = QVBoxLayout(dialogo)

        tabela = Tabela(
            colunas=("nome", "tabela_origem", "ativo"),
            rotulos={"nome": "Nome", "tabela_origem": "Tabela de origem", "ativo": "Ativo"},
            distribuir_igualmente=True)
        layout.addWidget(tabela, 1)

        def atualizar(selecionar_id=None):
            self._atualizar_construtores_disponiveis()
            itens = self._construtores_disponiveis
            tabela.definir_linhas(
                [(c["nome"], c["tabela_origem"], "Sim" if c["ativo"] else "Não") for c in itens],
                ids=[str(c["id"]) for c in itens])
            if selecionar_id is not None:
                tabela.selecionar_id(selecionar_id)

        def selecionado():
            ids = tabela.selecionados()
            if not ids:
                QMessageBox.information(dialogo, "Construtores", "Selecione um construtor.")
                return None
            id_ = int(ids[0])
            return next((c for c in self._construtores_disponiveis if c["id"] == id_), None)

        def abrir():
            resumo = selecionado()
            if resumo is None:
                return
            self.abrir_construtor(resumo)
            dialogo.accept()

        def duplicar():
            resumo = selecionado()
            if resumo is None:
                return
            nome, ok = QInputDialog.getText(
                dialogo, "Duplicar construtor", "Nome da cópia:",
                text=f"{resumo['nome']} (cópia)")
            if not ok or not nome.strip():
                return
            try:
                conn = conectar()
            except RuntimeError as e:
                QMessageBox.warning(dialogo, "Construtores", str(e))
                return
            try:
                novo_id = construtores.salvar_construtor(
                    conn, nome.strip(), resumo["tabela_origem"], resumo["grafo"],
                    construtor_id=None)
                construtores.definir_ativo(conn, novo_id, False)
            finally:
                conn.close()
            projeto.sincronizar()
            atualizar(novo_id)

        def excluir():
            resumo = selecionado()
            if resumo is None:
                return
            if QMessageBox.question(
                dialogo, "Construtores", f"Excluir o construtor \"{resumo['nome']}\"?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                conn = conectar()
            except RuntimeError as e:
                QMessageBox.warning(dialogo, "Construtores", str(e))
                return
            try:
                construtores.excluir_construtor(conn, resumo["id"])
            finally:
                conn.close()
            projeto.sincronizar()
            atualizar()

        def alternar_ativo():
            resumo = selecionado()
            if resumo is None:
                return
            try:
                conn = conectar()
            except RuntimeError as e:
                QMessageBox.warning(dialogo, "Construtores", str(e))
                return
            try:
                construtores.definir_ativo(conn, resumo["id"], not resumo["ativo"])
            finally:
                conn.close()
            projeto.sincronizar()
            atualizar(resumo["id"])

        botoes = QHBoxLayout()
        botao_abrir = QPushButton("Abrir no canvas")
        tema_qss.aplicar_variante(botao_abrir, "accent")
        icones.aplicar_icone(botao_abrir, "abrir", cor="white")
        botao_abrir.clicked.connect(abrir)
        botoes.addWidget(botao_abrir)
        botao_duplicar = QPushButton("Duplicar")
        icones.aplicar_icone(botao_duplicar, "duplicar")
        botao_duplicar.clicked.connect(duplicar)
        botoes.addWidget(botao_duplicar)
        botao_excluir = QPushButton("Excluir")
        tema_qss.aplicar_variante(botao_excluir, "perigo")
        icones.aplicar_icone(botao_excluir, "excluir")
        botao_excluir.clicked.connect(excluir)
        botoes.addWidget(botao_excluir)
        botao_ativo = QPushButton("Ativar/Desativar")
        icones.aplicar_icone(botao_ativo, "ativar_desativar")
        botao_ativo.clicked.connect(alternar_ativo)
        botoes.addWidget(botao_ativo)
        botoes.addStretch(1)
        fechar = QPushButton("Fechar")
        fechar.clicked.connect(dialogo.reject)
        botoes.addWidget(fechar)
        layout.addLayout(botoes)

        tabela.view.doubleClicked.connect(lambda _indice: abrir())
        atualizar()
        dialogo.exec()

    def _migrar_no_modelo_legado(self, no):
        if "variantes" in no:
            return
        modelo_id = no.pop("modelo_id", None)
        no["variantes"] = [{
            "estrato_coluna": no.pop("estrato_coluna", None),
            "estrato": no.pop("estrato", None),
            "equacao": no.pop("equacao", ""),
            "coeficientes": no.pop("coeficientes", {}),
        }]
        no["modelo_ids"] = [modelo_id] if modelo_id is not None else []
        no["nome"] = self._nome_por_modelo_id.get(modelo_id, no.get("rotulo"))

    def _refrescar_modelos_dos_nos(self):
        por_nome = {g["nome"]: g for g in self._modelos_disponiveis}
        for no in self.nos.values():
            if no["tipo"] != "modelo":
                continue
            self._migrar_no_modelo_legado(no)
            atual = por_nome.get(no.get("nome"))
            if atual is not None:
                no["modelo_ids"] = list(atual["ids"])
                no["variantes"] = [dict(v) for v in atual["variantes"]]
                no["rotulo"] = _rotulo_modelo(atual["nome"], atual["variantes"])

    # ---------------- salvar construtor (persiste + aplica na hora) ----------------

    def salvar_construtor(self):
        if not self.nos:
            QMessageBox.warning(self, "Construtor de Variáveis", "O canvas está vazio.")
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Construtor de Variáveis", str(e))
            return

        debug_tempos = {}
        try:
            # Se o grafo tiver um nó "Custo de Formação", garante as
            # linhas de idade <= 0 (custo anterior ao plantio) ANTES de
            # avaliar — sem isso, salvar aqui não pegaria as idades
            # negativas até a próxima "Gerar simulação" reaplicar tudo de
            # novo (ver core/construtores.py:sincronizar_linhas_formacao;
            # no-op se o grafo não tiver esse nó, ou a tabela de origem
            # não tiver "idade_simulada"). "Prévia"/"Gerar tabela nova
            # avulsa" NÃO sincronizam — só leem, não persistem nada.
            if construtores.grafo_tem_no_custo_formacao([{"nos": self.nos}]):
                construtores.sincronizar_linhas_formacao(
                    conn, self.combo_tabela_origem.currentText(),
                    simulacao.obter_coluna_talhao(conn), construtores.obter_custos_formacao(conn))
            try:
                df, valores, erros = self._avaliar_grafo(conn, debug_tempos=debug_tempos)
            except ValueError as e:
                QMessageBox.warning(self, "Construtor de Variáveis", str(e))
                return

            saidas = construtores.saidas_nomeadas(self.nos, valores)
            if not saidas:
                mensagem = (
                    "Nenhum modelo com nome de saída definido (botão direito no nó) e "
                    "totalmente ligado.")
                if erros:
                    mensagem += "\n\nPendências:\n" + "\n".join(erros)
                QMessageBox.warning(self, "Construtor de Variáveis", mensagem)
                return

            try:
                construtores.verificar_colisao_saidas(saidas)
            except ValueError as e:
                QMessageBox.warning(self, "Construtor de Variáveis", str(e))
                return

            if erros and QMessageBox.question(
                self, "Construtor de Variáveis",
                "Alguns nós não puderam ser calculados:\n\n" + "\n".join(erros) +
                "\n\nSalvar e aplicar só as saídas que deram certo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return

            nome_atual = None
            if self.construtor_atual_id is not None:
                nome_atual = next(
                    (c["nome"] for c in self._construtores_disponiveis
                     if c["id"] == self.construtor_atual_id), None)

            nome, ok = QInputDialog.getText(
                self, "Salvar construtor", "Nome deste construtor:", text=nome_atual or "")
            if not ok or not nome.strip():
                return
            nome = nome.strip()

            tabela_origem = self.combo_tabela_origem.currentText()
            grafo = {"nos": self.nos, "conexoes": self.conexoes, "grupos": self.grupos}
            self.construtor_atual_id = construtores.salvar_construtor(
                conn, nome, tabela_origem, grafo, construtor_id=self.construtor_atual_id)

            inicio_gravacao = time.perf_counter()
            construtores.gravar_saidas_como_colunas(conn, tabela_origem, df, saidas)
            duracao_gravacao = time.perf_counter() - inicio_gravacao
        finally:
            conn.close()

        projeto.sincronizar()
        self._atualizar_construtores_disponiveis()
        mensagem_final = (
            f"Construtor \"{nome}\" salvo e aplicado — {len(saidas)} coluna(s) em "
            f"\"{tabela_origem}\".\n\nEle será reaplicado automaticamente sempre que essa tabela for "
            "regenerada (ex: rodar \"Gerar simulação\" de novo).")
        if duracao_gravacao >= 1.0:
            mensagem_final += f"\n\nGravação no banco: {duracao_gravacao:.2f}s"
        resumo_tempos = self._resumo_tempos(debug_tempos)
        if resumo_tempos:
            mensagem_final += f"\n\n{resumo_tempos}"
        QMessageBox.information(self, "Construtor de Variáveis", mensagem_final)

    # ---------------- gerar tabela nova avulsa (não persiste) ----------------

    def gerar_tabela_nova_avulsa(self):
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Construtor de Variáveis", str(e))
            return

        try:
            try:
                df, valores, erros = self._avaliar_grafo(conn)
            except ValueError as e:
                QMessageBox.warning(self, "Construtor de Variáveis", str(e))
                return

            saidas = construtores.saidas_nomeadas(self.nos, valores)
            if not saidas:
                mensagem = (
                    "Nenhum modelo com nome de saída definido (botão direito no nó) e "
                    "totalmente ligado.")
                if erros:
                    mensagem += "\n\nPendências:\n" + "\n".join(erros)
                QMessageBox.warning(self, "Construtor de Variáveis", mensagem)
                return

            try:
                construtores.verificar_colisao_saidas(saidas)
            except ValueError as e:
                QMessageBox.warning(self, "Construtor de Variáveis", str(e))
                return

            if erros and QMessageBox.question(
                self, "Construtor de Variáveis",
                "Alguns nós não puderam ser calculados:\n\n" + "\n".join(erros) +
                "\n\nContinuar só com as saídas que deram certo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return

            nome_tabela, ok = QInputDialog.getText(self, "Nova tabela", "Nome da tabela nova:")
            if not ok or not nome_tabela.strip():
                return
            construtores.gravar_saidas_como_tabela_nova(conn, nome_tabela.strip(), df, saidas)
        finally:
            conn.close()

        projeto.sincronizar()
        QMessageBox.information(
            self, "Construtor de Variáveis",
            "Tabela gerada — só desta vez (não fica salva como construtor pra reaplicar depois; "
            "pra isso, use \"Salvar construtor\").")
