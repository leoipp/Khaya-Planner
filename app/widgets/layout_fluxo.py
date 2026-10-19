"""Barra de ferramentas com quebra de linha, sem comprimir os controles."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


class LayoutFluxo(QLayout):
    def __init__(self, parent=None, espacamento=8):
        super().__init__(parent)
        self._itens = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(espacamento)

    def addItem(self, item):
        self._itens.append(item)

    def count(self):
        return len(self._itens)

    def itemAt(self, index):
        return self._itens[index] if 0 <= index < len(self._itens) else None

    def takeAt(self, index):
        return self._itens.pop(index) if 0 <= index < len(self._itens) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._dispor(QRect(0, 0, width, 0), medir=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._dispor(rect)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._itens:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left()+m.right(), m.top()+m.bottom())

    def _dispor(self, rect, medir=False):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, altura = area.x(), area.y(), 0
        for item in self._itens:
            if item.isEmpty():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            largura = min(hint.width(), max(1, area.width()))
            if x > area.x() and x+largura > area.right()+1:
                x, y, altura = area.x(), y+altura+self.spacing(), 0
            if not medir:
                item.setGeometry(QRect(x, y, largura, hint.height()))
            x += largura+self.spacing()
            altura = max(altura, hint.height())
        return y+altura-rect.y()+m.bottom()


class GradeCampos(LayoutFluxo):
    """Campos com rótulo acima, em até três colunas conforme a largura."""
    def addLayout(self, layout, stretch=0):
        self.addChildLayout(layout)
        self.addItem(layout)

    def _dispor(self, rect, medir=False):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        itens = [item for item in self._itens if not item.isEmpty()]
        if not itens:
            return m.top()+m.bottom()
        colunas = min(3, len(itens), max(1, (area.width()+self.spacing()) // 210))
        largura = max(1, (area.width()-(colunas-1)*self.spacing()) // colunas)
        altura = max(item.sizeHint().expandedTo(item.minimumSize()).height() for item in itens)
        for i, item in enumerate(itens):
            if not medir:
                item.setGeometry(QRect(area.x()+(i%colunas)*(largura+self.spacing()),
                                       area.y()+(i//colunas)*(altura+self.spacing()), largura, altura))
        linhas = (len(itens)+colunas-1)//colunas
        return m.top()+m.bottom()+linhas*altura+(linhas-1)*self.spacing()
