# -*- coding: utf-8 -*-
"""
Painel com um histograma dos dados reais (barras) sobreposto pela curva
da Weibull 2P ajustada, pra conferir visualmente a qualidade do ajuste.
Porte de app/widgets/grafico_weibull.py (Tkinter, FigureCanvasTkAgg) — a
única mudança de fundo é o backend (FigureCanvasQTAgg); não precisa do
workaround de debounce de resize do original (era um vazamento de handle
GDI específico do Tk no Windows, ver app/widgets/grafico_utils.py de lá —
não existe equivalente no backend Qt).
"""
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget
from scipy.stats import weibull_min

from ..theme import manager as tema


class GraficoAjusteWeibull(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Guarda a última chamada de desenho (mostrar_mensagem ou
        # atualizar, com os mesmos argumentos) pra poder redesenhar do
        # zero quando o tema mudar — matplotlib não sabe nada sobre o
        # QSS/tema do app, então sem isso o gráfico ficaria com as cores
        # do tema antigo até o usuário interagir de novo.
        self._ultimo_desenho = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.figure = Figure(figsize=(5, 4), dpi=100, layout="constrained")
        self.eixo = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        tema.obter().themeChanged.connect(self._ao_mudar_tema)
        self.mostrar_mensagem("Selecione um grupo na lista pra ver o gráfico.")

    def _ao_mudar_tema(self, _modo):
        if self._ultimo_desenho is not None:
            metodo, args = self._ultimo_desenho
            getattr(self, metodo)(*args)

    def mostrar_mensagem(self, mensagem):
        self._ultimo_desenho = ("mostrar_mensagem", (mensagem,))
        gerenciador = tema.obter()
        cor_superficie = gerenciador.cor_grafico("superficie")
        self.figure.set_facecolor(cor_superficie)
        self.eixo.clear()
        self.eixo.set_facecolor(cor_superficie)
        self.eixo.text(
            0.5, 0.5, mensagem, ha="center", va="center", transform=self.eixo.transAxes,
            color=gerenciador.cor_grafico("tinta_muted"), fontsize=10, wrap=True,
        )
        self.eixo.set_xticks([])
        self.eixo.set_yticks([])
        for spine in self.eixo.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def atualizar(self, valores, shape=None, scale=None, titulo=""):
        self._ultimo_desenho = ("atualizar", (valores, shape, scale, titulo))

        valores = np.asarray(valores, dtype=float)
        valores = valores[~np.isnan(valores)]

        if len(valores) == 0:
            self.mostrar_mensagem("Sem dados reais pra este grupo.")
            return

        gerenciador = tema.obter()
        cor_superficie = gerenciador.cor_grafico("superficie")
        cor_tinta_primaria = gerenciador.cor_grafico("tinta_primaria")
        cor_tinta_muted = gerenciador.cor_grafico("tinta_muted")
        cor_grade = gerenciador.cor_grafico("grade")

        self.figure.set_facecolor(cor_superficie)
        self.eixo.clear()
        self.eixo.set_facecolor(cor_superficie)

        quantidade_classes = max(5, min(30, int(np.sqrt(len(valores)))))
        self.eixo.hist(
            valores, bins=quantidade_classes, density=True,
            color=gerenciador.cor_grafico("dados"), alpha=0.75, edgecolor=cor_superficie, linewidth=0.5,
            label="Dados reais",
        )

        if shape is not None and scale is not None and shape > 0 and scale > 0:
            x_max = max(float(valores.max()), scale * 2.0) * 1.05
            x = np.linspace(0.0, x_max, 200)
            y = weibull_min.pdf(x, c=shape, scale=scale)
            self.eixo.plot(
                x, y, color=gerenciador.cor_grafico("ajuste"), linewidth=2,
                label=f"Weibull ajustada (forma={shape:.3f}, escala={scale:.3f})",
            )

        self.eixo.set_title(titulo, fontsize=9, color=cor_tinta_primaria)
        self.eixo.set_xlabel("Valor", fontsize=9, color=cor_tinta_muted)
        self.eixo.set_ylabel("Densidade", fontsize=9, color=cor_tinta_muted)
        self.eixo.tick_params(colors=cor_tinta_muted, labelsize=8)
        self.eixo.grid(axis="y", color=cor_grade, linewidth=0.8, zorder=0)
        self.eixo.set_axisbelow(True)
        for spine in ("top", "right"):
            self.eixo.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            self.eixo.spines[spine].set_color(cor_grade)
        legenda = self.eixo.legend(fontsize=8, frameon=False)
        for texto_legenda in legenda.get_texts():
            texto_legenda.set_color(cor_tinta_primaria)

        self.canvas.draw_idle()
