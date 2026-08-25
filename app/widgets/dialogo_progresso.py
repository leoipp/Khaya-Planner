# -*- coding: utf-8 -*-
"""
Diálogo modal com barra de progresso — usado por operações longas demais
pra travar a janela principal, mas que também não fazem sentido deixar o
usuário continuar navegando enquanto rodam (ex: abrir um projeto .mogno
grande, que troca o estado do app inteiro). Sem botão de cancelar: as
operações que usam isso hoje não têm um jeito seguro de abortar no meio
(decodificar um arquivo pela metade não ajuda em nada) — por isso também
tira o botão de fechar (X) da barra de título.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from ..theme import qss as tema_qss


class DialogoProgresso(QDialog):
    def __init__(self, titulo, mensagem, parent=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        layout.addWidget(QLabel(mensagem))

        self.progressbar = QProgressBar()
        self.progressbar.setMinimumWidth(320)
        self.progressbar.setRange(0, 0)  # indeterminado até o primeiro atualizar() com total conhecido
        layout.addWidget(self.progressbar)

        self.label_detalhe = QLabel("")
        tema_qss.aplicar_status(self.label_detalhe, "neutro")
        layout.addWidget(self.label_detalhe)

    def showEvent(self, evento):
        super().showEvent(evento)
        pai = self.parentWidget()
        if pai is not None:
            geo = pai.geometry()
            self.move(
                max(geo.x() + (geo.width() - self.width()) // 2, 0),
                max(geo.y() + (geo.height() - self.height()) // 2, 0))

    def atualizar(self, feito, total):
        if total:
            # Barra em permilagem (0-1000), não no valor bruto de
            # feito/total: QProgressBar.setRange/setValue também usam int
            # C++ de 32 bits, e feito/total (bytes de um .mogno grande)
            # podem passar de 2 GB — permilagem fica sempre dentro do
            # range independente do tamanho do arquivo.
            permil = min(1000, max(0, round(feito * 1000 / total)))
            self.progressbar.setRange(0, 1000)
            self.progressbar.setValue(permil)
            self.label_detalhe.setText(
                f"{feito / (1024 * 1024):,.0f} MB / {total / (1024 * 1024):,.0f} MB")
        else:
            self.progressbar.setRange(0, 0)
            self.label_detalhe.setText(f"{feito / (1024 * 1024):,.0f} MB")
