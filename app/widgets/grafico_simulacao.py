# -*- coding: utf-8 -*-
"""
Gráficos da aba "Gráfico de resultados" da tela Simulação — dois widgets:

- `GraficoResultadoSimulacao`: barras agrupadas por idade, uma curva
  (série) por evento de manejo (modo "coluna comum") ou por sortimento
  cadastrado (modo "coluna por classe → sortimento"). Usado na aba
  "Gráfico".
- `GraficoPorClasseSimulacao`: uma curva por evento de manejo, classe
  diamétrica no eixo x. Usado na aba "Gráfico por classe".

Porte de app/screens/simulacao.py (Tkinter, FigureCanvasTkAgg) —
_desenhar_grafico_resultado/_mostrar_mensagem_grafico e
_desenhar_grafico_classe/_mostrar_mensagem_grafico_classe de lá viraram
métodos destes dois QWidget, mesmo padrão de
app/widgets/grafico_weibull.py (guarda a última chamada de desenho pra
redesenhar do zero quando o tema mudar — matplotlib não sabe nada sobre o
QSS/tema do app). Sem o workaround de debounce de resize do original (era
um vazamento de handle GDI específico do Tk no Windows — não existe
equivalente no backend Qt).
"""
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..core.simulacao import EVENTO_CORTE_RASO, EVENTO_DESBASTE_1, EVENTO_DESBASTE_2, EVENTO_RALEIO
from ..theme import manager as tema

# Gráfico de resultados: ordem fixa das curvas no modo "por evento" (não
# alfabética — "Em pé" primeiro, depois a ordem cronológica natural dos
# eventos de manejo) e paleta categórica própria (o tema só tem cores pra
# 1-2 séries fixas — "dados"/"ajuste" — nada pensado pra N curvas
# discretas). Cicla por índice se houver mais curvas que cores (ex: muitos
# sortimentos cadastrados).
_ORDEM_SERIE_EVENTO_GRAFICO = [
    "Em pé", EVENTO_RALEIO, EVENTO_DESBASTE_1, EVENTO_DESBASTE_2, EVENTO_CORTE_RASO,
]
_PALETA_CURVAS_GRAFICO = [
    "#2a78d6", "#e07b39", "#4caf50", "#a83279", "#c9a227", "#5b5ea6", "#d64545", "#3ba3a0",
]

_ORDEM_EVENTOS_MANEJO = (EVENTO_RALEIO, EVENTO_DESBASTE_1, EVENTO_DESBASTE_2, EVENTO_CORTE_RASO)


class GraficoResultadoSimulacao(QWidget):
    """Barras agrupadas por idade — uma barra por curva/série, todas com a
    mesma cor fixa dela (mesma cor na legenda), lado a lado dentro da faixa
    de cada idade. Idade vira posição categórica (0, 1, 2, ...), não o
    valor real no eixo — evita barras com espaçamento estranho quando as
    idades da simulação não são uniformes (ex: eventos concentrados numas
    idades só)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ultimo_desenho = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.figure = Figure(figsize=(8, 3.5), dpi=100, layout="constrained")
        self.eixo = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        tema.obter().themeChanged.connect(self._ao_mudar_tema)
        self.mostrar_mensagem("Escolha uma coluna pra ver o gráfico.")

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
            color=gerenciador.cor_grafico("tinta_muted"), fontsize=9, wrap=True)
        self.eixo.set_xticks([])
        self.eixo.set_yticks([])
        for spine in self.eixo.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def desenhar(self, df, por_classe):
        self._ultimo_desenho = ("desenhar", (df, por_classe))

        gerenciador = tema.obter()
        cor_primaria = gerenciador.cor_grafico("tinta_primaria")
        cor_muted = gerenciador.cor_grafico("tinta_muted")
        cor_grade = gerenciador.cor_grafico("grade")
        cor_superficie = gerenciador.cor_grafico("superficie")

        self.figure.set_facecolor(cor_superficie)
        self.eixo.clear()
        self.eixo.set_facecolor(cor_superficie)

        if por_classe:
            # Ordem de 1ª aparição no DataFrame = ordem dos sortimentos
            # (ORDER BY limite_inferior, nome — ver
            # core/simulacao.py:dados_grafico_resultado), não alfabética.
            ordem_series = list(dict.fromkeys(df["serie"]))
        else:
            presentes = set(df["serie"])
            ordem_series = [s for s in _ORDEM_SERIE_EVENTO_GRAFICO if s in presentes]
            # Defensivo — não deveria haver evento_manejo fora da lista
            # fixa, mas se houver (ex: valor digitado manualmente no
            # banco), ainda aparece, só não na ordem "certa".
            ordem_series += sorted(presentes - set(ordem_series))

        idades = sorted(df["idade_simulada"].unique())
        indice_idade = {idade: pos for pos, idade in enumerate(idades)}
        n_series = len(ordem_series)
        largura_total = 0.8
        largura_barra = largura_total / max(n_series, 1)

        for i, serie in enumerate(ordem_series):
            sub = df[df["serie"] == serie]
            cor = _PALETA_CURVAS_GRAFICO[i % len(_PALETA_CURVAS_GRAFICO)]
            posicoes = [
                indice_idade[idade] + (i - (n_series - 1) / 2) * largura_barra
                for idade in sub["idade_simulada"]
            ]
            self.eixo.bar(posicoes, sub["valor"], width=largura_barra, color=cor, label=serie)

        self.eixo.set_xticks(range(len(idades)))
        self.eixo.set_xticklabels([f"{int(idade)}" for idade in idades])
        self.eixo.set_xlabel("Idade simulada", fontsize=9, color=cor_muted)
        self.eixo.set_ylabel("Valor", fontsize=9, color=cor_muted)
        self.eixo.tick_params(colors=cor_muted, labelsize=8)
        self.eixo.grid(axis="y", color=cor_grade, linewidth=0.8, zorder=0)
        self.eixo.set_axisbelow(True)
        for spine in ("top", "right"):
            self.eixo.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            self.eixo.spines[spine].set_color(cor_grade)
        self.eixo.legend(
            fontsize=8, facecolor=cor_superficie, edgecolor=cor_grade, labelcolor=cor_primaria)

        self.canvas.draw_idle()


class GraficoPorClasseSimulacao(QWidget):
    """Uma curva por evento de manejo (Raleio/1º Desbaste/2º Desbaste/
    Corte Raso), classe diamétrica no eixo x. Diferente de
    GraficoResultadoSimulacao (que soma entre classes pra virar uma curva
    por sortimento), aqui a classe é o próprio eixo x — útil pra ver como
    um valor (ex: VET, RT, VTCC SIMULADO) se distribui pelas classes em
    cada evento, sem colapsar nelas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ultimo_desenho = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.figure = Figure(figsize=(8, 3.5), dpi=100, layout="constrained")
        self.eixo = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        tema.obter().themeChanged.connect(self._ao_mudar_tema)
        self.mostrar_mensagem("Escolha uma coluna pra ver o gráfico.")

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
            color=gerenciador.cor_grafico("tinta_muted"), fontsize=9, wrap=True)
        self.eixo.set_xticks([])
        self.eixo.set_yticks([])
        for spine in self.eixo.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def desenhar(self, df):
        self._ultimo_desenho = ("desenhar", (df,))

        gerenciador = tema.obter()
        cor_primaria = gerenciador.cor_grafico("tinta_primaria")
        cor_muted = gerenciador.cor_grafico("tinta_muted")
        cor_grade = gerenciador.cor_grafico("grade")
        cor_superficie = gerenciador.cor_grafico("superficie")

        self.figure.set_facecolor(cor_superficie)
        self.eixo.clear()
        self.eixo.set_facecolor(cor_superficie)

        eventos_presentes = set(df["evento"])
        ordem_eventos = [e for e in _ORDEM_EVENTOS_MANEJO if e in eventos_presentes]
        ordem_eventos += sorted(eventos_presentes - set(ordem_eventos))

        for i, evento in enumerate(ordem_eventos):
            sub = df[df["evento"] == evento].sort_values("classe")
            cor = _PALETA_CURVAS_GRAFICO[i % len(_PALETA_CURVAS_GRAFICO)]
            self.eixo.plot(
                sub["classe"], sub["valor"], color=cor, label=evento, marker="o", markersize=3,
                linewidth=1.5)

        self.eixo.set_xlabel("Classe diamétrica", fontsize=9, color=cor_muted)
        self.eixo.set_ylabel("Valor", fontsize=9, color=cor_muted)
        self.eixo.tick_params(colors=cor_muted, labelsize=8)
        self.eixo.grid(color=cor_grade, linewidth=0.8, zorder=0)
        self.eixo.set_axisbelow(True)
        for spine in ("top", "right"):
            self.eixo.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            self.eixo.spines[spine].set_color(cor_grade)
        self.eixo.legend(
            fontsize=8, facecolor=cor_superficie, edgecolor=cor_grade, labelcolor=cor_primaria)

        self.canvas.draw_idle()
