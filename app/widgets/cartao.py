# -*- coding: utf-8 -*-
"""
Cartão de seção (título em negrito + área de campos) — usado nos
formulários das telas Modelos, Simulação e Configurações pra agrupar
campos relacionados. Fundo branco arredondado com sombra suave, cor fixa
(tokens.COR_CARTAO) independente do tema claro/escuro — mesmo espírito de
app/widgets/barra_lateral.py (cor própria, não a paleta clara/escura).

Porte do Cartao Tkinter original (Canvas com retângulo arredondado +
sombra desenhados à mão): em Qt vira só um QFrame com `border-radius` via
QSS + QGraphicsDropShadowEffect nativo — não precisa redesenhar nada em
cada resize. `cartao.corpo` é o container onde quem chama monta os campos
da seção (equivalente a usar o próprio Cartao como master no Tkinter).

A cor (fixa, não muda com o tema) fica só em app/theme/qss.py, seletores
`#Cartao`/`#CartaoTitulo`/`#CartaoCorpo` — este widget só marca os
`objectName`, não define nenhuma cor localmente (ver pedido de
estilização centralizada num arquivo só). `#CartaoCorpo` precisa de fundo
transparente explícito: sem isso, a regra genérica `QWidget { background:
... }` da stylesheet global pinta `corpo` com a cor de fundo comum do
tema — que não é branca — aparecendo como uma caixa por cima do cartão
branco."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..theme import qss as tema_qss

PADDING_HORIZONTAL = 16
PADDING_TOPO = 14
PADDING_BASE = 16


class Cartao(QFrame):
    def __init__(self, titulo, parent=None):
        super().__init__(parent)
        self.setObjectName("Cartao")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tema_qss.aplicar_sombra(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PADDING_HORIZONTAL, PADDING_TOPO, PADDING_HORIZONTAL, PADDING_BASE)
        layout.setSpacing(10)

        rotulo_titulo = QLabel(titulo, self)
        rotulo_titulo.setObjectName("CartaoTitulo")
        layout.addWidget(rotulo_titulo)

        self.corpo = QWidget(self)
        self.corpo.setObjectName("CartaoCorpo")
        layout.addWidget(self.corpo)
        # Cartão ao lado de um irmão mais alto (mesma linha de grid, ex:
        # "Distribuição diamétrica" ao lado de "Colunas da Base IFC" em
        # simulacao.py) é esticado pra bater a altura do maior — sem essa
        # sobra indo pro fim, título/corpo (nenhum dos dois com policy
        # Expanding) dividiam o espaço extra entre si e o título acabava
        # flutuando longe do topo, centralizado no meio do vazio.
        layout.addStretch(1)
