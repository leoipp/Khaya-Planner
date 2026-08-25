# -*- coding: utf-8 -*-
"""
Slider de intervalo [início, fim] — uma trilha só, com 2 bolinhas
arrastáveis independentes (Qt não tem um "range slider" nativo; `QSlider`
só tem 1 alça). Desenho manual via QPainter, mesmo espírito de
app/widgets/alternador_tema.py (widget pequeno, redesenha sozinho a cada
troca de tema).

API parecida com `QSlider` (`setMinimum`/`setMaximum`/`setEnabled`) mais
`values()`/`setValues()` pro par (início, fim) e um sinal só
(`valoresAlterados`), disparado quando qualquer uma das duas bolinhas se
move (arrastando ou clicando direto na trilha, que move a bolinha mais
próxima do clique)."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import manager as tema

_RAIO_BOLINHA = 7
_ALTURA_TRILHA = 4


class SliderIntervalo(QWidget):
    valoresAlterados = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimo = 1
        self._maximo = 1
        self._inicio = 1
        self._fim = 1
        self._arrastando = None  # "inicio" | "fim" | None
        self.setMinimumHeight(2 * _RAIO_BOLINHA + 4)
        self.setEnabled(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        tema.obter().themeChanged.connect(lambda _modo: self.update())

    # ---------------- API pública (espelha QSlider + values/setValues) ----------------

    def setMinimum(self, valor):
        self._minimo = valor
        self._inicio = max(self._minimo, min(self._inicio, self._maximo))
        self._fim = max(self._minimo, min(self._fim, self._maximo))
        self.update()

    def setMaximum(self, valor):
        self._maximo = valor
        self._inicio = max(self._minimo, min(self._inicio, self._maximo))
        self._fim = max(self._minimo, min(self._fim, self._maximo))
        self.update()

    def values(self):
        return self._inicio, self._fim

    def setValues(self, inicio, fim, emitir=False):
        inicio = max(self._minimo, min(inicio, self._maximo))
        fim = max(self._minimo, min(fim, self._maximo))
        if inicio > fim:
            inicio = fim
        mudou = (inicio, fim) != (self._inicio, self._fim)
        self._inicio, self._fim = inicio, fim
        self.update()
        if mudou and emitir:
            self.valoresAlterados.emit(self._inicio, self._fim)

    # ---------------- interação ----------------

    def mousePressEvent(self, evento):
        if not self.isEnabled() or evento.button() != Qt.MouseButton.LeftButton:
            return
        x = evento.position().x()
        # A bolinha mais próxima do clique começa a ser arrastada — cobre
        # tanto arrastar uma bolinha já visível quanto clicar direto na
        # trilha longe das duas (aproxima a mais perto).
        distancia_inicio = abs(x - self._valor_para_x(self._inicio))
        distancia_fim = abs(x - self._valor_para_x(self._fim))
        self._arrastando = "inicio" if distancia_inicio <= distancia_fim else "fim"
        self._mover_para(x)

    def mouseMoveEvent(self, evento):
        if self._arrastando is not None:
            self._mover_para(evento.position().x())

    def mouseReleaseEvent(self, _evento):
        self._arrastando = None

    def _mover_para(self, x):
        valor = self._x_para_valor(x)
        if self._arrastando == "inicio":
            novo = (min(valor, self._fim), self._fim)
        else:
            novo = (self._inicio, max(valor, self._inicio))
        if novo == (self._inicio, self._fim):
            return
        self._inicio, self._fim = novo
        self.update()
        self.valoresAlterados.emit(self._inicio, self._fim)

    def _x_para_valor(self, x):
        largura = max(self.width() - 2 * _RAIO_BOLINHA, 1)
        fracao = (x - _RAIO_BOLINHA) / largura
        fracao = min(max(fracao, 0.0), 1.0)
        return round(self._minimo + fracao * (self._maximo - self._minimo))

    def _valor_para_x(self, valor):
        largura = max(self.width() - 2 * _RAIO_BOLINHA, 1)
        if self._maximo == self._minimo:
            fracao = 0.0
        else:
            fracao = (valor - self._minimo) / (self._maximo - self._minimo)
        return _RAIO_BOLINHA + fracao * largura

    # ---------------- desenho ----------------

    def paintEvent(self, _evento):
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        gerenciador = tema.obter()
        y_centro = self.height() / 2

        cor_trilha = QColor(gerenciador.cor_ui("borda"))
        cor_preenchido = QColor(gerenciador.cor_ui("accent"))
        cor_bolinha = cor_preenchido if self.isEnabled() else QColor(gerenciador.cor_ui("fg_muted"))
        cor_contorno_bolinha = QColor(gerenciador.cor_fundo())

        pintor.setPen(Qt.PenStyle.NoPen)
        pintor.setBrush(cor_trilha)
        pintor.drawRoundedRect(
            QRectF(_RAIO_BOLINHA, y_centro - _ALTURA_TRILHA / 2,
                   max(self.width() - 2 * _RAIO_BOLINHA, 0), _ALTURA_TRILHA),
            _ALTURA_TRILHA / 2, _ALTURA_TRILHA / 2)

        x_inicio = self._valor_para_x(self._inicio)
        x_fim = self._valor_para_x(self._fim)
        if self.isEnabled():
            pintor.setBrush(cor_preenchido)
            pintor.drawRoundedRect(
                QRectF(x_inicio, y_centro - _ALTURA_TRILHA / 2, x_fim - x_inicio, _ALTURA_TRILHA),
                _ALTURA_TRILHA / 2, _ALTURA_TRILHA / 2)

        pintor.setBrush(cor_bolinha)
        pintor.setPen(QPen(cor_contorno_bolinha, 1.5))
        for x in (x_inicio, x_fim):
            pintor.drawEllipse(QPointF(x, y_centro), _RAIO_BOLINHA, _RAIO_BOLINHA)

        pintor.end()
