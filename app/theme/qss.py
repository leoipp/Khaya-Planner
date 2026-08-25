# -*- coding: utf-8 -*-
"""
Gera a stylesheet Qt (QSS) do app a partir dos tokens de app/theme/tokens.py
— substitui o `ttk.Style` customizado (setup_styles) do app/tema.py
original. Aplicada uma vez em toda a QApplication (`app.setStyleSheet(...)`)
a cada troca de tema; cascateia pra toda a árvore de widgets sozinha (ao
contrário do Tk, não precisa de retema manual widget a widget).

Variantes de botão (Accent/Salvar) são selecionadas por propriedade Qt
dinâmica em vez de "estilo" ttk nomeado — ver widgets que usam
`botao.setProperty("variante", "accent")` + `botao.style().polish(botao)`.
"""
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from . import tokens


def aplicar_sombra(widget, blur=8):
    """Sombra padrão do app — spec de design em CSS (`box-shadow: 0 2px 8px
    rgba(22, 35, 42, 0.06)`, ver tokens.SOMBRA_PADRAO), usada no Cartão e na
    Barra Lateral (os dois flutuantes com sombra). QGraphicsDropShadowEffect
    não é QSS de verdade (efeito gráfico, não stylesheet), mas os valores
    vêm dos mesmos tokens pra não duplicar a constante em cada widget —
    `blur` já nasce igual ao blur-radius do CSS (8px)."""
    sombra = QGraphicsDropShadowEffect(widget)
    sombra.setBlurRadius(blur)
    sombra.setOffset(0, tokens.SOMBRA_PADRAO["deslocamento_y"])
    cor = QColor(*tokens.SOMBRA_PADRAO["rgb"])
    cor.setAlphaF(tokens.SOMBRA_PADRAO["alpha"])
    sombra.setColor(cor)
    widget.setGraphicsEffect(sombra)


def aplicar_variante(widget, nome):
    """Marca um widget com uma variante de estilo (ver seletores
    QPushButton[variante="..."]/QLabel[variante="..."] em `gerar`) e força
    o Qt a reavaliar o QSS pra ela — setProperty sozinho não repinta um
    widget que já teve seu estilo resolvido (ex: um botão recém-criado que
    já foi mostrado)."""
    widget.setProperty("variante", nome)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def aplicar_status(widget, chave):
    """Marca um QLabel com uma cor de status (neutro/sucesso/aviso — ver
    QLabel[status="..."] em `gerar`) — substitui os
    `label.setStyleSheet(f"color: {tema.obter().cor_status(chave)}")`
    espalhados pelas telas: a cor concreta fica só aqui (tokens.py +
    `gerar`), a tela só marca QUAL status; a troca de tema reaplica a cor
    certa sozinha (a propriedade continua "sucesso" depois de trocar de
    tema, e a stylesheet inteira é regerada a cada troca — não precisa de
    um ouvinte de tema por label)."""
    widget.setProperty("status", chave)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def aplicar_tamanho(widget, nome):
    """Marca um widget com um tamanho de fonte (ver QPushButton[tamanho="..."]
    em `gerar`) — eixo independente de `aplicar_variante` (cor/ênfase), pra
    dar pra combinar os dois num botão só (ex: "Remover selecionada" é
    `variante="leve"` + `tamanho="pequeno"` ao mesmo tempo)."""
    widget.setProperty("tamanho", nome)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _fonte(chave):
    """CSS de `font-size`/`font-weight` pro papel de texto `chave` (ver
    tokens.TIPOGRAFIA — escala tipográfica única do app: título de tela,
    de painel, de cartão, label de campo, texto normal/auxiliar, entrada,
    botão, cabeçalho/conteúdo de tabela)."""
    t = tokens.TIPOGRAFIA[chave]
    return f"font-size: {t['tamanho']}px; font-weight: {t['peso']};"


def gerar(modo):
    c = tokens.PALETA_UI[modo]
    espaco_sm, espaco_md, espaco_xs, espaco_xl = (
        tokens.ESPACO_SM, tokens.ESPACO_MD, tokens.ESPACO_XS, tokens.ESPACO_XL)

    return f"""
    * {{
        font-family: {tokens.PILHA_FONTES};
        {_fonte("texto_normal")}
    }}

    QWidget {{
        background: {c["bg"]};
        color: {c["fg"]};
    }}

    QMainWindow, QDialog {{
        background: {c["bg"]};
    }}

    /* Rótulo sem variante = label de campo por padrão (ex: "Nome",
       "Talhão") — é o papel mais comum de QLabel solto nas telas; título/
       dica/status abaixo sobrescrevem quando marcados. */
    QLabel {{
        background: transparent;
        {_fonte("label_campo")}
    }}

    QLabel[variante="titulo"] {{
        {_fonte("titulo_painel")}
    }}
    QLabel[variante="titulo-secao"] {{
        {_fonte("titulo_tela")}
    }}
    QLabel[variante="descricao-tela"] {{
        color: {c["fg_muted"]};
        {_fonte("texto_normal")}
    }}
    QLabel[variante="dica"] {{
        color: {c["fg_muted"]};
        {_fonte("texto_auxiliar")}
    }}

    QLabel[status="neutro"] {{
        color: {tokens.PALETA_STATUS[modo]["neutro"]};
        {_fonte("texto_auxiliar")}
    }}
    QLabel[status="sucesso"] {{
        color: {tokens.PALETA_STATUS[modo]["sucesso"]};
        {_fonte("texto_auxiliar")}
    }}
    QLabel[status="aviso"] {{
        color: {tokens.PALETA_STATUS[modo]["aviso"]};
        {_fonte("texto_auxiliar")}
    }}
    QLabel[status="perigo"] {{
        color: {tokens.PALETA_STATUS[modo]["perigo"]};
        {_fonte("texto_auxiliar")}
    }}

    QFrame[variante="separador"] {{
        background: {c["borda"]};
        max-height: 1px;
        min-height: 1px;
    }}

    /* ---- cartão de seção (ver widgets/cartao.py) — cor FIXA, não muda
       com o tema (spec de design própria, mesmo espírito da barra
       lateral abaixo) ------------------------------------------------- */
    QFrame#Cartao {{
        background: {tokens.COR_CARTAO["fundo"]};
        border: 1px solid {tokens.COR_CARTAO["borda"]};
        border-radius: 12px;
    }}
    QLabel#CartaoTitulo {{
        background: transparent;
        color: {tokens.COR_CARTAO["texto"]};
        {_fonte("titulo_cartao")}
    }}
    QWidget#CartaoCorpo {{
        background: transparent;
    }}
    QWidget[variante="transparente"] {{
        background: transparent;
    }}

    QWidget#CabecalhoTela {{
        background: transparent;
    }}

    /* ---- barra lateral (ver widgets/barra_lateral.py) — cor FIXA nos
       dois temas, identidade visual própria -------------------------- */
    QFrame#BarraLateral {{
        background: {tokens.COR_BARRA_LATERAL["fundo"]};
        border: none;
        /* Só os cantos da direita — a esquerda encosta no trilho (mesma
           cor de fundo, ver QWidget#Trilho abaixo), sem borda visível
           entre os dois; arredondar a esquerda também deixaria um
           triângulo da cor de fundo da janela aparecendo por trás. */
        border-top-left-radius: 0;
        border-bottom-left-radius: 0;
        border-top-right-radius: 12px;
        border-bottom-right-radius: 12px;
    }}
    /* Trilho (ver window.py:_montar_central) — mesma cor de fundo do
       cartão da barra lateral, sem borda/cantos (é uma faixa fixa colada
       na borda esquerda da janela, não um cartão flutuante). */
    QWidget#Trilho {{
        background: {tokens.COR_BARRA_LATERAL["fundo"]};
    }}
    QPushButton#BarraLateralBotao {{
        background: {tokens.COR_BARRA_LATERAL["fundo"]};
        color: {tokens.COR_BARRA_LATERAL["texto"]};
        border: none;
        border-radius: 8px;
        text-align: left;
        padding: {espaco_sm}px 14px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton#BarraLateralBotao:hover {{
        background: {tokens.COR_BARRA_LATERAL["fundo_hover"]};
    }}
    QPushButton#BarraLateralBotao:checked {{
        background: {c["accent"]};
        color: {c["accent_fg"]};
        font-weight: 600;
    }}
    QPushButton#BarraLateralBotao:disabled {{
        color: {tokens.COR_BARRA_LATERAL["texto_muted"]};
    }}
    QPushButton#BarraLateralToggle {{
        background: transparent;
        border: none;
        color: {c["fg_muted"]};
        font-size: 16px;
        font-weight: 700;
    }}
    QPushButton#BarraLateralToggle:hover {{
        color: {c["fg"]};
    }}

    QToolTip {{
        background: {tokens.COR_TOOLTIP[modo]["bg"]};
        color: {tokens.COR_TOOLTIP[modo]["fg"]};
        border: 1px solid {c["borda"]};
        padding: {espaco_xs}px;
    }}

    /* ---- botões --------------------------------------------------- */
    QPushButton {{
        background: {c["bg_widget"]};
        color: {c["fg"]};
        border: 1px solid {c["borda"]};
        border-radius: 6px;
        padding: {espaco_sm}px {espaco_md}px;
        {_fonte("botao")}
    }}
    QPushButton:hover {{
        background: {c["hover"]};
    }}
    QPushButton:pressed {{
        background: {c["hover"]};
    }}
    QPushButton:disabled {{
        background: {c["bg"]};
        color: {c["disabled_fg"]};
    }}

    QPushButton[variante="accent"] {{
        background: {c["accent"]};
        color: {c["accent_fg"]};
        border: 1px solid {c["accent"]};
    }}
    QPushButton[variante="accent"]:hover,
    QPushButton[variante="accent"]:pressed {{
        background: {c["accent_hover"]};
        border-color: {c["accent_hover"]};
    }}

    QPushButton[variante="salvar"] {{
        background: {c["accent"]};
        color: {c["accent_fg"]};
        border: 1px solid {c["accent"]};
    }}
    QPushButton[variante="salvar"]:hover,
    QPushButton[variante="salvar"]:pressed {{
        background: {c["accent_hover"]};
        border-color: {c["accent_hover"]};
    }}

    /* Botão destrutivo (Excluir de verdade — apaga um registro) — só o
       texto/ícone ficam vermelhos, fundo/borda continuam neutros (não é
       "tão grave" quanto um botão sólido vermelho pra uma ação que ainda
       pede confirmação antes de executar). */
    QPushButton[variante="perigo"] {{
        color: {tokens.PALETA_STATUS[modo]["perigo"]};
    }}

    /* Botão "leve" (ex: Remover selecionada — tira algo de uma lista em
       edição, não apaga nada do banco ainda) — texto apagado, sem chamar
       tanta atenção quanto um botão comum. */
    QPushButton[variante="leve"] {{
        color: {c["fg_muted"]};
        font-weight: 500;
    }}

    /* Eixo de TAMANHO, independente da variante de cor acima (dá pra
       combinar os dois num botão só — ver qss.aplicar_tamanho). */
    QPushButton[tamanho="pequeno"] {{
        font-size: 11px;
        padding: {espaco_xs}px {espaco_sm}px;
    }}

    /* ---- campos de entrada ------------------------------------------ */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
        background: {c["bg_widget"]};
        color: {c["fg"]};
        border: 1px solid {c["borda"]};
        border-radius: 4px;
        padding: {espaco_xs}px {espaco_sm}px;
        selection-background-color: {c["selecao_bg"]};
        {_fonte("entrada")}
    }}
    /* Placeholder (12px/400 no spec) não tem seletor próprio no Qt Style
       Sheets — QLineEdit/QPlainTextEdit não expõem `::placeholder`; o
       texto de placeholder usa a cor/fonte que o Qt decide sozinho. */
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {c["accent"]};
    }}
    QLineEdit:disabled, QTextEdit:disabled {{
        background: {c["bg"]};
        color: {c["disabled_fg"]};
    }}

    QComboBox {{
        background: {c["bg_widget"]};
        color: {c["fg"]};
        border: 1px solid {c["borda"]};
        border-radius: 4px;
        padding: {espaco_xs}px {espaco_sm}px;
        {_fonte("entrada")}
    }}
    QComboBox:focus {{
        border: 1px solid {c["accent"]};
    }}
    QComboBox:disabled {{
        background: {c["bg"]};
        color: {c["disabled_fg"]};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background: {c["bg_widget"]};
        color: {c["fg"]};
        selection-background-color: {c["selecao_bg"]};
        selection-color: {c["fg"]};
        border: 1px solid {c["borda"]};
        outline: none;
    }}

    QListWidget, QListView {{
        background: {c["bg_widget"]};
        color: {c["fg"]};
        border: 1px solid {c["borda"]};
        border-radius: 4px;
        selection-background-color: {c["selecao_bg"]};
        selection-color: {c["fg"]};
        {_fonte("entrada")}
    }}

    QCheckBox {{
        spacing: {espaco_xs}px;
        background: transparent;
        {_fonte("texto_normal")}
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c["borda"]};
        border-radius: 3px;
        background: {c["bg_widget"]};
    }}
    QCheckBox::indicator:checked {{
        background: {c["accent"]};
        border-color: {c["accent"]};
    }}

    /* ---- tabela (ver widgets/tabela.py) ----------------------------- */
    QTableView {{
        background: {c["bg_widget"]};
        alternate-background-color: {c["bg_widget"]};
        color: {c["fg"]};
        border: 1px solid {c["borda"]};
        border-radius: 0;
        gridline-color: {c["borda"]};
        selection-background-color: {c["selecao_bg"]};
        selection-color: {c["fg"]};
        {_fonte("conteudo_tabela")}
    }}
    QTableView::item {{
        padding: {espaco_xs}px;
    }}
    QTableView#TabelaModelos::item {{
        border: none;
        border-bottom: 1px solid {c["borda"]};
    }}
    QTableView[cantosArredondados="true"] {{
        border: none;
        border-radius: 7px;
    }}
    QWidget#MolduraTabelaArredondada {{
        background: {c["borda"]};
        border: none;
        border-radius: 8px;
    }}
    QHeaderView::section {{
        background: {c["bg"]};
        color: {c["fg_cabecalho"]};
        border: none;
        border-right: 1px solid {c["borda"]};
        border-bottom: 1px solid {c["borda"]};
        padding: {espaco_xs}px {espaco_sm}px;
        {_fonte("cabecalho_tabela")}
    }}
    QTableCornerButton::section {{
        background: {c["bg"]};
        border: none;
        border-right: 1px solid {c["borda"]};
        border-bottom: 1px solid {c["borda"]};
    }}
    QHeaderView::section:hover {{
        background: {c["hover"]};
    }}

    /* ---- abas -------------------------------------------------------- */
    QTabWidget::pane {{
        border: 1px solid {c["borda"]};
        top: -1px;
    }}
    QTabBar::tab {{
        background: {c["bg_widget"]};
        color: {c["fg_muted"]};
        border: 1px solid {c["borda"]};
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        margin-right: 2px;
        padding: {espaco_sm}px {espaco_md}px;
        {_fonte("botao")}
    }}
    QTabBar::tab:selected {{
        background: {c["bg"]};
        color: {c["fg"]};
        font-weight: 700;
    }}
    QTabBar::tab:hover:!selected {{
        background: {c["hover"]};
    }}

    /* ---- grupos/painéis (equivalente ao TLabelframe) ------------------ */
    QGroupBox {{
        border: 1px solid {c["borda"]};
        border-radius: 6px;
        margin-top: {espaco_md}px;
        padding-top: {espaco_md}px;
        {_fonte("titulo_cartao")}
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: {espaco_sm}px;
        padding: 0 {espaco_xs}px;
    }}

    QProgressBar {{
        background: {c["trough"]};
        border: 1px solid {c["borda"]};
        border-radius: 4px;
        text-align: center;
        color: {c["fg"]};
    }}
    QProgressBar::chunk {{
        background: {c["accent"]};
        border-radius: 3px;
    }}

    QScrollArea {{
        border: none;
    }}

    /* Pilha de telas (ver window.py:self._pilha) — sem isso, o estilo
       nativo do Windows (windowsvista) desenha uma borda de painel em
       volta do QStackedWidget, visível como uma linha entre ele e a
       barra lateral/trilho ao lado. */
    QStackedWidget {{
        border: none;
        background: transparent;
    }}

    QScrollBar:vertical {{
        background: {c["bg"]};
        width: 12px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {tokens.COR_BARRA_LATERAL["fundo"]};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {tokens.COR_BARRA_LATERAL["fundo_hover"]};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    QScrollBar:horizontal {{
        background: {c["bg"]};
        height: 12px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {tokens.COR_BARRA_LATERAL["fundo"]};
        border-radius: 5px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {tokens.COR_BARRA_LATERAL["fundo_hover"]};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    QMenu {{
        background: {c["bg_widget"]};
        color: {c["fg"]};
        border: 1px solid {c["borda"]};
    }}
    QMenu::item:selected {{
        background: {c["selecao_bg"]};
    }}

    QStatusBar {{
        background: {c["bg"]};
        border-top: 1px solid {c["borda"]};
    }}
    """
