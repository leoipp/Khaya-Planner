# -*- coding: utf-8 -*-
"""Cabeçalho reutilizável para dar hierarquia consistente às telas."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..theme import icones, qss, tokens


class CabecalhoTela(QWidget):
    """Título, texto de apoio e ações contextuais de uma tela.

    As ações ficam à direita em janelas largas e o bloco textual pode
    encolher sem perder o título. O componente não conhece regras de
    negócio; recebe apenas os callbacks fornecidos pela tela.
    """

    def __init__(self, titulo, descricao="", parent=None):
        super().__init__(parent)
        self.setObjectName("CabecalhoTela")
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.ESPACO_LG)

        bloco_texto = QVBoxLayout()
        bloco_texto.setSpacing(tokens.ESPACO_XS)

        rotulo_titulo = QLabel(titulo)
        qss.aplicar_variante(rotulo_titulo, "titulo-secao")
        bloco_texto.addWidget(rotulo_titulo)

        if descricao:
            rotulo_descricao = QLabel(descricao)
            rotulo_descricao.setWordWrap(True)
            qss.aplicar_variante(rotulo_descricao, "descricao-tela")
            bloco_texto.addWidget(rotulo_descricao)

        layout.addLayout(bloco_texto, 1)

        self._acoes = QHBoxLayout()
        self._acoes.setSpacing(tokens.ESPACO_SM)
        self._acoes.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(self._acoes)

    def adicionar_acao(self, texto, callback, icone=None, destaque=False):
        botao = QPushButton(texto)
        if destaque:
            qss.aplicar_variante(botao, "accent")
        if icone:
            cor = "white" if destaque else None
            icones.aplicar_icone(botao, icone, cor=cor)
        botao.clicked.connect(callback)
        self._acoes.addWidget(botao)
        return botao
