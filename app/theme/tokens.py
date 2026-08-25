# -*- coding: utf-8 -*-
"""
Tokens de design do Khaya Planner (Qt) — cores, tipografia e espaçamento.

Portado de app/tema.py do projeto Tkinter original: os MESMOS valores de
paleta (light/dark), só que consumidos por app/theme/qss.py (gera a
stylesheet Qt) em vez de um ttk.Style customizado. Widgets que pintam a
própria aparência (cartão, barra lateral, alternador de tema) consultam
estes tokens diretamente via app/theme/manager.py.
"""
from pathlib import Path

_PASTA_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
# Ícone da janela/taskbar (Windows só aceita .ico aqui) — window.py e
# barra_lateral.py leem os DOIS daqui, em vez de recalcular
# `Path(__file__).resolve().parent...` cada um no seu arquivo: os dois
# módulos moram em profundidades diferentes dentro de app/, então duplicar
# essa conta é como já rolou de um dos dois apontar pra pasta errada
# (app/assets em vez de assets/, na raiz do projeto).
CAMINHO_ICONE_APP = _PASTA_ASSETS / "icon.ico"
# PNG de origem (fundo transparente, sem a reamostragem que o container
# .ico já embute nos tamanhos pequenos) — usado onde o ícone é desenhado
# em UI comum (ex: cabeçalho da barra lateral) e não precisa ser .ico.
CAMINHO_ICONE_PNG = _PASTA_ASSETS / "icon.png"
# Logo por extenso (marca + "Khaya Planner", fundo transparente) do
# cabeçalho da barra lateral — substitui o ícone quadrado sozinho de lá
# (ver widgets/barra_lateral.py); bem mais largo que alto (lockup
# horizontal), por isso escalado por LARGURA, não altura, lá.
CAMINHO_LOGO_BARRA_LATERAL = _PASTA_ASSETS / "logo_barra_lateral.png"

FONTE_PRINCIPAL = "Manrope"
# Cai pra Segoe UI (fonte de sistema do Windows moderno) se a máquina não
# tiver a Manrope instalada — o Qt escolhe a primeira família disponível
# nessa lista sozinho, não precisa de nenhuma lógica extra aqui. Manrope é
# uma fonte livre (Google Fonts); instalando ela no Windows o app já passa
# a usá-la automaticamente, sem precisar mudar código nenhum.
PILHA_FONTES = f'"{FONTE_PRINCIPAL}", "Segoe UI", Arial, sans-serif'

# Escala tipográfica — tamanho em px e peso (100-900, escala CSS/QFont::Weight
# moderna) por papel de texto na UI. `variante="..."` (telas) ou seletor
# direto (campos/tabela/botão) em app/theme/qss.py:gerar aplicam cada um.
TIPOGRAFIA = {
    "titulo_tela": {"tamanho": 20, "peso": 700},      # título principal da tela
    "titulo_painel": {"tamanho": 16, "peso": 700},    # título de painel (ex: "Modelos cadastrados")
    "titulo_cartao": {"tamanho": 14, "peso": 700},    # título de card/seção (ver widgets/cartao.py)
    "label_campo": {"tamanho": 12, "peso": 600},      # rótulo de campo (ex: "Nome", "Talhão")
    "texto_normal": {"tamanho": 13, "peso": 400},     # texto corrido / base do app
    "entrada": {"tamanho": 13, "peso": 400},          # QLineEdit/QComboBox/QPlainTextEdit/...
    "botao": {"tamanho": 13, "peso": 600},            # QPushButton
    "cabecalho_tabela": {"tamanho": 11, "peso": 700},  # QHeaderView::section
    "conteudo_tabela": {"tamanho": 12, "peso": 400},   # células da Tabela
    "texto_auxiliar": {"tamanho": 11, "peso": 400},    # dica/status — texto pequeno de apoio
    "placeholder": {"tamanho": 12, "peso": 400},       # sem suporte nativo no QSS, ver nota em qss.py
}

# Grade de 4px — todo padding usado em qss.py é múltiplo disso, pra manter
# o respiro visual consistente entre os elementos da UI.
ESPACO_XS = 4
ESPACO_SM = 8
ESPACO_MD = 12
ESPACO_LG = 16
ESPACO_XL = 24

NOME_APP = "Khaya Planner"

PALETA_STATUS = {
    "light": {"neutro": "#888888", "sucesso": "#008000", "aviso": "#a06000", "perigo": "#D92D20"},
    "dark": {"neutro": "#a0a0a0", "sucesso": "#4caf50", "aviso": "#d29922", "perigo": "#ef5350"},
}

PALETA_GRAFICO = {
    "light": {
        "tinta_primaria": "#0b0b0b",
        "tinta_muted": "#898781",
        "grade": "#e1e0d9",
        "superficie": "#fcfcfb",
        "dados": "#2a78d6",
        "ajuste": "#008300",
    },
    "dark": {
        "tinta_primaria": "#e8e6e1",
        "tinta_muted": "#9a9890",
        "grade": "#3a3a38",
        "superficie": "#1c1c1c",
        "dados": "#5b9bd5",
        "ajuste": "#4caf50",
    },
}

COR_RESUMO_TABELA = {
    "light": {"bg": "#e6ecff", "fg": "#0b0b0b"},
    "dark": {"bg": "#2b3450", "fg": "#e8e6e1"},
}

COR_TOOLTIP = {
    "light": {"bg": "#ffffe0", "fg": "#0b0b0b"},
    "dark": {"bg": "#3a3a20", "fg": "#f0f0d0"},
}

# Fundo do canvas do Construtor de Variáveis (app/screens/construtor_variaveis.py)
# — widget que pinta a própria aparência via QPainter, não alcançado pela
# stylesheet Qt. Mesmos valores do app/tema.py original (_CANVAS_CONSTRUTOR).
COR_CANVAS_CONSTRUTOR = {"light": "#f2f2f2", "dark": "#232323"}

# Barra lateral: cor FIXA, igual nos dois temas — peça de identidade
# visual própria (sidebar escura), não acompanha claro/escuro.
COR_BARRA_LATERAL = {
    "fundo": "#16232a",
    "fundo_hover": "#1f323c",
    "borda": "#2c4a58",
    "texto": "#F7F9F9",
    "texto_muted": "#6d828c",
}

# Cartão de seção: mesma lógica de cor FIXA da barra lateral acima.
COR_CARTAO = {"fundo": "#ffffff", "borda": "#e8ecee", "texto": "#16232A"}

SOMBRA_PADRAO = {"rgb": (22, 35, 42), "alpha": 0.06, "deslocamento_y": 2, "expansao": 4}

# Tokens de cor pros widgets em si (botões, campos, grade, abas...).
# `fg_cabecalho` é um tom só pra cabeçalho de tabela — um pouco mais
# apagado que `fg` (texto normal) mas mais forte que `fg_muted` (dica/
# status), igual ao spec de design (Cabeçalho da tabela #34454D vs texto
# normal #16232A vs texto auxiliar #66757C).
PALETA_UI = {
    "light": {
        "bg": "#f7f9f9", "bg_widget": "#ffffff", "fg": "#16232A", "fg_muted": "#66757C",
        "fg_cabecalho": "#34454D",
        "borda": "#d4d2cb", "accent": "#075056", "accent_hover": "#09666d", "accent_fg": "#ffffff",
        "selecao_bg": "#d7e7e8", "trough": "#e5e3dd", "hover": "#ececea", "disabled_fg": "#a8a6a0",
    },
    "dark": {
        "bg": "#1c1c1c", "bg_widget": "#242424", "fg": "#e8e6e1", "fg_muted": "#9a9890",
        "fg_cabecalho": "#b8bfc4",
        "borda": "#3a3a38", "accent": "#075056", "accent_hover": "#09666d", "accent_fg": "#ffffff",
        "selecao_bg": "#173f42", "trough": "#333331", "hover": "#2c2c2c", "disabled_fg": "#5a5a57",
    },
}
