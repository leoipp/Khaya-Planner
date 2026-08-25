# -*- coding: utf-8 -*-
import sys
import warnings

from PySide6.QtWidgets import QApplication

from app.core import preferencias
from app.theme import manager as tema
from app.theme import qss
from app.window import JanelaPrincipal

# Aviso cosmético do Matplotlib: os gráficos embutidos (grafico_curvas.py,
# grafico_weibull.py, grafico_simulacao.py) usam layout="constrained", que
# dispara esse aviso sempre que uma figura é desenhada com o widget Qt
# ainda em tamanho zero (comum logo na abertura de uma tela, antes do
# QSplitter terminar de calcular os tamanhos dos painéis) — inofensivo, o
# layout é reaplicado normalmente no redesenho seguinte, mas polui o
# console a cada tela aberta.
warnings.filterwarnings(
    "ignore", message="constrained_layout not applied because axes sizes collapsed to zero")


def main():
    app = QApplication(sys.argv)

    gerenciador_tema = tema.obter()
    # salvar=False: aqui só confirma o que já leu de preferencias.json,
    # não é uma troca de tema de verdade — evita regravar o arquivo à toa
    # a cada boot do app.
    gerenciador_tema.aplicar(preferencias.obter_tema(), salvar=False)
    app.setStyleSheet(qss.gerar(gerenciador_tema.modo_atual()))
    gerenciador_tema.themeChanged.connect(lambda modo: app.setStyleSheet(qss.gerar(modo)))

    janela = JanelaPrincipal()
    janela.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
