# -*- coding: utf-8 -*-
"""Painel compartilhado: título, descrição opcional e corpo de conteúdo.

As superfícies e bordas acompanham o tema via QSS. O contrato ``corpo`` e
os índices do layout são preservados para os formulários existentes.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..theme import qss as tema_qss

PADDING_HORIZONTAL = 16
PADDING_TOPO = 14
PADDING_BASE = 16


class Cartao(QFrame):
    def __init__(self, titulo, parent=None, descricao=None):
        super().__init__(parent)
        self.setObjectName("Cartao")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PADDING_HORIZONTAL, PADDING_TOPO, PADDING_HORIZONTAL, PADDING_BASE)
        layout.setSpacing(10)

        rotulo_titulo = QLabel(titulo, self)
        rotulo_titulo.setObjectName("CartaoTitulo")
        layout.addWidget(rotulo_titulo)

        if descricao:
            dica = QLabel(descricao)
            dica.setWordWrap(True)
            tema_qss.aplicar_variante(dica, 'dica')
            layout.addWidget(dica)

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
