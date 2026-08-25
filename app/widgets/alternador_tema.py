# -*- coding: utf-8 -*-
"""
Botão de alternância de tema claro/escuro — sol/lua desenhados com
QPainter (vetorial, mesmo espírito do tk.Canvas original: evita depender
de a fonte do sistema ter o glifo de emoji ☀️/🌙).

O ícone mostra o DESTINO do clique, não o estado atual (modo claro em
vigor mostra uma lua — clique leva pro escuro; modo escuro em vigor mostra
um sol — clique leva pro claro), mesma convenção do widget original.
"""
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import manager as tema

TAMANHO = 28

_COR_SOL = {"light": "#f2a900", "dark": "#ffd166"}
_COR_LUA = {"light": "#4a4a4a", "dark": "#dcdcdc"}


class AlternadorTema(QWidget):
    def __init__(self, command, parent=None):
        super().__init__(parent)
        self._command = command
        self.setFixedSize(TAMANHO, TAMANHO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        tema.obter().themeChanged.connect(lambda _modo: self.update())

    def mousePressEvent(self, evento):
        if evento.button() == Qt.MouseButton.LeftButton:
            self._command()

    def paintEvent(self, _evento):
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        pintor.fillRect(self.rect(), QColor(tema.obter().cor_fundo()))
        centro = TAMANHO / 2
        if tema.obter().modo_atual() == "light":
            self._desenhar_lua(pintor, centro)
        else:
            self._desenhar_sol(pintor, centro)
        pintor.end()

    def _desenhar_sol(self, pintor, centro):
        cor = QColor(_COR_SOL[tema.obter().modo_atual()])
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(cor)
        raio_disco = TAMANHO * 0.22
        pintor.drawEllipse(QRectF(
            centro - raio_disco, centro - raio_disco, 2 * raio_disco, 2 * raio_disco))

        caneta = QPen(cor, 2)
        caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
        pintor.setPen(caneta)
        raio_interno = TAMANHO * 0.32
        raio_externo = TAMANHO * 0.46
        for i in range(8):
            angulo = math.radians(i * 45)
            x1 = centro + raio_interno * math.cos(angulo)
            y1 = centro + raio_interno * math.sin(angulo)
            x2 = centro + raio_externo * math.cos(angulo)
            y2 = centro + raio_externo * math.sin(angulo)
            pintor.drawLine(int(x1), int(y1), int(x2), int(y2))

    def _desenhar_lua(self, pintor, centro):
        # Crescente = círculo cheio menos um segundo círculo pintado na cor
        # de fundo, deslocado — técnica padrão pra "recortar" uma lua
        # crescente sem depender de composição booleana de verdade.
        cor = QColor(_COR_LUA[tema.obter().modo_atual()])
        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(cor)
        raio = TAMANHO * 0.30
        pintor.drawEllipse(QRectF(centro - raio, centro - raio, 2 * raio, 2 * raio))

        pintor.setBrush(QColor(tema.obter().cor_fundo()))
        dx, dy = raio * 0.6, -raio * 0.15
        pintor.drawEllipse(QRectF(centro - raio + dx, centro - raio + dy, 2 * raio, 2 * raio))
