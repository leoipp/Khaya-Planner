"""Adia a construção e o desenho de gráficos até ficarem visíveis."""
from importlib import import_module

from PySide6.QtWidgets import QVBoxLayout, QWidget


class GraficoSobDemanda(QWidget):
    def __init__(self, modulo, classe, parent=None):
        super().__init__(parent)
        self._modulo = modulo
        self._classe = classe
        self._grafico = None
        self._pendente = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def mostrar_mensagem(self, mensagem):
        self._atualizar('mostrar_mensagem', (mensagem,))

    def desenhar(self, *args):
        self._atualizar('desenhar', args)

    def _atualizar(self, metodo, args):
        self._pendente = (metodo, args)
        if self.isVisible():
            self._aplicar_pendente()

    def _aplicar_pendente(self):
        if self._grafico is None:
            classe = getattr(import_module(self._modulo), self._classe)
            self._grafico = classe()
            self._layout.addWidget(self._grafico)
        if self._pendente is not None:
            metodo, args = self._pendente
            self._pendente = None
            getattr(self._grafico, metodo)(*args)

    def showEvent(self, evento):
        super().showEvent(evento)
        self._aplicar_pendente()
