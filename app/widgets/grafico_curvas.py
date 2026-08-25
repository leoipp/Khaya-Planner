# -*- coding: utf-8 -*-
"""
Gráficos matplotlib embutidos usados pela tela "Ingressos e Curvas de
Distribuição" (ver app/screens/ingressos_curvas_distribuicao.py):
`GraficoDispersao` é o scatterplot genérico (Idade × IP, com 1/IP opcional
sobreposto num 2º eixo Y — ver `atualizar`), e `GraficoCurvasDistribuicao`
é o gráfico de curvas de distribuição diamétrica sobrepostas por idade
(com barra de cor sequencial). Porte do que era
`_montar_figura`/`_atualizar_dispersao`/`_desenhar_curvas` em
app/screens/ingressos_curvas_distribuicao.py (Tkinter, FigureCanvasTkAgg) —
mesmo padrão de app/widgets/grafico_weibull.py: redesenho completo a
partir do último estado guardado (pra reagir a troca de tema), sem o
workaround de debounce de resize do Tk (era um vazamento de handle GDI
específico do backend Tk no Windows — não existe equivalente no backend
Qt).

Idade é uma grandeza ordenada/contínua, não uma identidade categórica —
por isso as curvas usam cor SEQUENCIAL (com barra de cor) em vez de uma
paleta categórica ciclada, que sugeriria "séries diferentes" em vez de
"pontos ao longo do tempo" e ficaria sem cor nova depois de ~10-12 idades.
Mas sequencial não precisa dizer "um matiz só claro→escuro" — com muitas
idades selecionadas, variações de tom de uma cor só ficam parecidas
demais entre si. "viridis" é sequencial multi-matiz (roxo→azul→verde→
amarelo) — a ordem continua legível (a claridade sobe monotonicamente ao
longo do mapa) mas cada faixa de idade fica bem mais distinguível das
vizinhas do que variando só claro/escuro de um único tom, além de ser
seguro pra daltonismo.
"""
import matplotlib as mpl
import matplotlib.colors as mcolors
import numpy as np
from matplotlib import cm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget
from scipy.interpolate import make_interp_spline

from ..theme import manager as tema

MAPA_COR_SEQUENCIAL = "viridis"
# Amostra só [0.0, 0.88] do colormap — a ponta final do "viridis" (amarelo
# bem claro) fica com contraste ruim contra o fundo quase branco do gráfico.
FAIXA_COLORMAP = (0.0, 0.88)
# Pontos por curva depois do smooth (a distribuição só tem um ponto por
# classe diamétrica — poucas dezenas — o que faz ela parecer "quebrada" em
# segmentos retos; interpolar numa grade mais fina só pra desenhar deixa a
# curva visualmente suave sem alterar o dado em si).
PONTOS_SUAVIZACAO = 200

# Identidade visual fixa pedida para os gráficos desta tela. O fundo
# branco garante contraste para eixos/textos sempre pretos mesmo quando o
# restante do aplicativo estiver no tema escuro.
COR_FUNDO_GRAFICO = "#ffffff"
COR_TEXTO_GRAFICO = "#000000"
COR_GRADE_GRAFICO = "#d3d3d3"


def _suavizar(x, y):
    """Interpola (x, y) numa grade mais fina só pra desenhar — spline
    cúbica (não PCHIP: PCHIP evita "estourar" além dos pontos originais, o
    que trava a curva e deixa ela mais angulosa perto dos picos; uma
    spline cúbica comum arredonda de verdade). Como spline cúbica pode
    "estourar" um pouco abaixo de zero entre dois pontos baixos, o
    resultado é limitado a >= 0 (é uma probabilidade, nunca pode ficar
    negativa). Com poucos pontos (< 4) a interpolação não ajuda muito e
    pode ficar estranha — desenha reto mesmo nesse caso."""
    if len(x) < 4:
        return x, y
    spline = make_interp_spline(x, y, k=3)
    x_fino = np.linspace(x[0], x[-1], PONTOS_SUAVIZACAO)
    return x_fino, np.clip(spline(x_fino), 0, None)


def _cor_para_idade(idade, idade_min, idade_max):
    if idade_max == idade_min:
        t = 1.0
    else:
        t = (idade - idade_min) / (idade_max - idade_min)
    inicio, fim = FAIXA_COLORMAP
    return mpl.colormaps[MAPA_COR_SEQUENCIAL](inicio + t * (fim - inicio))


class GraficoDispersao(QWidget):
    """Scatterplot idade × IP, com idade × 1/IP opcional sobreposto num 2º
    eixo Y (ver `atualizar`) — era `_montar_figura` + `_atualizar_dispersao`
    no original (Tkinter)."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Guarda a última chamada de desenho (mostrar_mensagem ou
        # atualizar, com os mesmos argumentos) pra poder redesenhar do
        # zero quando o tema mudar.
        self._ultimo_desenho = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        gerenciador = tema.obter()
        self.figure = Figure(
            figsize=(4, 3), dpi=100, facecolor=COR_FUNDO_GRAFICO,
            layout="constrained")
        self.eixo = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        tema.obter().themeChanged.connect(self._ao_mudar_tema)

    def _ao_mudar_tema(self, _modo):
        if self._ultimo_desenho is not None:
            metodo, args = self._ultimo_desenho
            getattr(self, metodo)(*args)

    def mostrar_mensagem(self, mensagem):
        self._ultimo_desenho = ("mostrar_mensagem", (mensagem,))
        gerenciador = tema.obter()
        # figura inteira (não só eixo.clear()) — um desenho anterior com 2ª
        # série (ver atualizar) pode ter deixado um eixo twinx() por trás,
        # que eixo.clear() sozinho não remove.
        self.figure.clf()
        self.figure.set_facecolor(COR_FUNDO_GRAFICO)
        self.eixo = self.figure.add_subplot(111)
        self.eixo.set_facecolor(COR_FUNDO_GRAFICO)
        self.eixo.text(
            0.5, 0.5, mensagem, ha="center", va="center", transform=self.eixo.transAxes,
            color=COR_TEXTO_GRAFICO, fontsize=9, wrap=True,
        )
        self.eixo.set_xticks([])
        self.eixo.set_yticks([])
        for spine in self.eixo.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def atualizar(
        self, x, y, titulo, rotulo_y, x2=None, y2=None, rotulo_y2=None,
        curva_x=None, curva_y=None, rotulo_curva=None, ponto_itd=None, rotulo_itd=None,
    ):
        """`x2`/`y2`/`rotulo_y2` (opcionais, todos ou nenhum): 2ª série num
        eixo Y próprio (ax.twinx()), sobreposta na mesma área — pensado
        pra IP (eixo esquerdo) + 1/IP (eixo direito), escalas bem
        diferentes (1/IP pode disparar quando IP fica perto de zero) que
        não caberiam juntas num eixo linear só.

        `curva_x`/`curva_y`/`rotulo_curva` (opcionais, só fazem sentido
        junto com x2/y2): linha contínua no MESMO eixo direito de x2/y2
        (não um 3º eixo) — pensada pro modelo logístico ajustado sobre os
        pontos de 1/IP (ver ingressos_curvas_distribuicao.py), tracejada
        pra não se confundir com os pontos observados (mesma cor, "^" vs
        linha).

        `ponto_itd`/`rotulo_itd` (opcionais, só fazem sentido junto com
        curva_x/curva_y): marca o ponto de inflexão (idade, a/2) da curva
        ajustada — estrela + texto do valor de ITD ao lado, sem entrar na
        legenda (o texto já é o próprio rótulo)."""
        self._ultimo_desenho = (
            "atualizar",
            (x, y, titulo, rotulo_y, x2, y2, rotulo_y2, curva_x, curva_y, rotulo_curva,
             ponto_itd, rotulo_itd))

        if len(x) == 0:
            self.mostrar_mensagem("Nenhum ponto com Diâmetro Diferenciador encontrado.")
            return

        gerenciador = tema.obter()
        cor_superficie = COR_FUNDO_GRAFICO
        cor_tinta_primaria = COR_TEXTO_GRAFICO
        cor_grade = COR_GRADE_GRAFICO
        cor_dados = gerenciador.cor_grafico("dados")

        self.figure.clf()
        self.figure.set_facecolor(cor_superficie)
        self.eixo = self.figure.add_subplot(111)
        self.eixo.set_facecolor(cor_superficie)
        pontos = self.eixo.scatter(
            x, y, color=cor_dados, alpha=0.75, edgecolor=cor_superficie, linewidth=0.5,
            label=rotulo_y)
        self.eixo.set_title(titulo, fontsize=9, color=cor_tinta_primaria)
        self.eixo.set_xlabel("Idade", fontsize=8, color=COR_TEXTO_GRAFICO)
        self.eixo.set_ylabel(rotulo_y, fontsize=8, color=COR_TEXTO_GRAFICO)
        self.eixo.tick_params(axis="both", colors=COR_TEXTO_GRAFICO, labelsize=8)
        self.eixo.grid(color=cor_grade, linestyle="--", linewidth=0.8, zorder=0)
        self.eixo.set_axisbelow(True)
        for spine in ("top", "right"):
            self.eixo.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            self.eixo.spines[spine].set_color(COR_TEXTO_GRAFICO)

        artistas, rotulos = [pontos], [rotulo_y]
        if x2 is not None and len(x2) > 0:
            cor_ajuste = gerenciador.cor_grafico("ajuste")
            eixo2 = self.eixo.twinx()
            eixo2.set_facecolor("none")
            pontos2 = eixo2.scatter(
                x2, y2, color=cor_ajuste, alpha=0.75, marker="^",
                edgecolor=cor_superficie, linewidth=0.5, label=rotulo_y2)
            eixo2.set_ylabel(rotulo_y2, fontsize=8, color=COR_TEXTO_GRAFICO)
            eixo2.tick_params(axis="y", colors=COR_TEXTO_GRAFICO, labelsize=8)
            eixo2.spines["top"].set_visible(False)
            eixo2.spines["right"].set_color(COR_TEXTO_GRAFICO)
            artistas.append(pontos2)
            rotulos.append(rotulo_y2)

            if curva_x is not None and len(curva_x) > 0:
                (linha_ajuste,) = eixo2.plot(
                    curva_x, curva_y, color=cor_ajuste, linewidth=1.5, linestyle="--",
                    label=rotulo_curva)
                artistas.append(linha_ajuste)
                rotulos.append(rotulo_curva)

                if ponto_itd is not None:
                    x_itd, y_itd = ponto_itd
                    eixo2.scatter(
                        [x_itd], [y_itd], color=cor_ajuste, marker="*", s=140,
                        edgecolor=cor_tinta_primaria, linewidth=1, zorder=5)
                    eixo2.annotate(
                        rotulo_itd, (x_itd, y_itd), textcoords="offset points", xytext=(6, 6),
                        fontsize=7, color=cor_tinta_primaria)

        if len(artistas) > 1:
            legenda = self.eixo.legend(
                artistas, rotulos, loc="upper left", fontsize=7, framealpha=0.85)
            legenda.get_frame().set_facecolor(cor_superficie)
            legenda.get_frame().set_edgecolor(cor_grade)
            for texto in legenda.get_texts():
                texto.set_color(cor_tinta_primaria)

        self.canvas.draw_idle()


class GraficoCurvasDistribuicao(QWidget):
    """Curvas de distribuição diamétrica (classe × probabilidade)
    sobrepostas por idade, com barra de cor sequencial (viridis) — era
    `_desenhar_curvas`/`_mostrar_mensagem_curva`/`_limpar_figura_curva`
    no original."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ultimo_desenho = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        gerenciador = tema.obter()
        self.figure = Figure(
            figsize=(4, 3), dpi=100, facecolor=COR_FUNDO_GRAFICO,
            layout="constrained")
        self.eixo = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        tema.obter().themeChanged.connect(self._ao_mudar_tema)

    def _ao_mudar_tema(self, _modo):
        if self._ultimo_desenho is not None:
            metodo, args = self._ultimo_desenho
            getattr(self, metodo)(*args)

    def _limpar_figura(self):
        # Recria a figura inteira (não só limpa o conteúdo do eixo
        # principal) porque Figure.colorbar(ax=self.eixo) ENCOLHE o eixo
        # principal pra abrir espaço pra barra — eixo.clear() não desfaz
        # esse encolhimento, então a cada atualização o gráfico ficava
        # menor e mais deslocado pra esquerda (a barra de cor da vez
        # anterior nunca "devolvia" o espaço que tinha tomado).
        gerenciador = tema.obter()
        self.figure.clf()
        self.figure.set_facecolor(COR_FUNDO_GRAFICO)
        self.eixo = self.figure.add_subplot(111)
        self.eixo.set_facecolor(COR_FUNDO_GRAFICO)

    def mostrar_mensagem(self, mensagem):
        self._ultimo_desenho = ("mostrar_mensagem", (mensagem,))
        self._limpar_figura()
        gerenciador = tema.obter()
        self.eixo.text(
            0.5, 0.5, mensagem, ha="center", va="center", transform=self.eixo.transAxes,
            color=COR_TEXTO_GRAFICO, fontsize=10, wrap=True,
        )
        self.eixo.set_xticks([])
        self.eixo.set_yticks([])
        for spine in self.eixo.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def atualizar(self, df, idades_escolhidas, talhao):
        self._ultimo_desenho = ("atualizar", (df, idades_escolhidas, talhao))
        self._limpar_figura()

        # Normaliza a cor pelo min/max do que está SENDO exibido, não pelo
        # min/max de todas as idades do talhão — selecionar um recorte (ex:
        # 5 de 20 idades) não pode espremer as cores numa pontinha quase
        # igual do gradiente só porque o talhão como um todo cobre uma
        # faixa bem maior.
        idade_min, idade_max = min(idades_escolhidas), max(idades_escolhidas)
        mapa = mpl.colormaps[MAPA_COR_SEQUENCIAL]

        for idade in idades_escolhidas:
            sub = df[df["idade"] == idade].sort_values("classe_diametrica")
            x_suave, y_suave = _suavizar(
                sub["classe_diametrica"].to_numpy(), sub["probabilidade"].to_numpy())
            self.eixo.plot(
                x_suave, y_suave,
                color=_cor_para_idade(idade, idade_min, idade_max), linewidth=2)

        gerenciador = tema.obter()
        cor_tinta_primaria = COR_TEXTO_GRAFICO
        cor_grade = COR_GRADE_GRAFICO

        normalizador = mcolors.Normalize(vmin=idade_min, vmax=idade_max)
        mapeavel = cm.ScalarMappable(norm=normalizador, cmap=mapa)
        mapeavel.set_array([])
        barra = self.figure.colorbar(mapeavel, ax=self.eixo)
        barra.set_label("Idade", fontsize=8, color=COR_TEXTO_GRAFICO)
        barra.ax.tick_params(colors=COR_TEXTO_GRAFICO, labelsize=8)
        barra.outline.set_edgecolor(COR_TEXTO_GRAFICO)

        self.eixo.set_title(
            f"Distribuição diamétrica por idade — talhão {talhao}",
            fontsize=9, color=cor_tinta_primaria)
        self.eixo.set_xlabel("Classe diamétrica", fontsize=9, color=COR_TEXTO_GRAFICO)
        self.eixo.set_ylabel("Probabilidade", fontsize=9, color=COR_TEXTO_GRAFICO)
        self.eixo.tick_params(colors=COR_TEXTO_GRAFICO, labelsize=8)
        self.eixo.grid(color=cor_grade, linestyle="--", linewidth=0.8, zorder=0)
        self.eixo.set_axisbelow(True)
        for spine in ("top", "right"):
            self.eixo.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            self.eixo.spines[spine].set_color(COR_TEXTO_GRAFICO)

        self.canvas.draw_idle()
