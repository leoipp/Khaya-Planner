# -*- coding: utf-8 -*-
"""
Tela "Ingressos e Curvas": junta as duas telas que liam a distribuição
diamétrica idade a idade (simulacao_distribuicao_diametrica) e por isso
compartilhavam o mesmo seletor de talhão/forma de leitura de dados. Porte
completo de app/screens/ingressos_curvas_distribuicao.py (Tkinter).

- Ingressos Percentuais (MIP): pra cada talhão e idade simulada, lê o
  Diâmetro Diferenciador (DD), o Ingresso Percentual (IP) e o Ingresso
  Percentual Médio (IPM) já calculados e persistidos por
  app/core/simulacao.py:calcular_mip_continuo (rodado dentro de "Gerar
  simulação") — ver lá pra definição exata do método (densidade da
  Weibull por classe diamétrica, comparando cada idade contra a idade
  IMEDIATAMENTE anterior simulada, não uma base fixa). IP/IPM vêm em
  fração (0-1); 1/IP e 1/IPM são calculados aqui na tela (não persistidos —
  são só o inverso, NaN onde IP/IPM = 0). Tabela + 1 scatterplot (idade ×
  IP, com 1/IP sobreposto num 2º eixo Y — ver GraficoDispersao). A tela só
  LÊ o que já foi calculado — não tem opção de recalcular com outra coluna
  de forma/escala aqui (a fdp que gera DD/IP já vem de forma_atual/
  escala_atual, a mesma usada em "Gerar simulação"). O seletor de talhão
  aqui só FILTRA o que já foi calculado/lido, não dispara um novo cálculo.

- Curvas de Distribuição: pra um talhão escolhido, sobrepõe a curva de
  distribuição diamétrica (classe × probabilidade) de cada idade simulada
  DENTRO do intervalo [início, fim] escolhido nos sliders do painel de
  Ajuste (mesmo controle que recorta os pontos do ajuste logístico
  abaixo — ver "Ajuste logístico / ITD") — mostra visualmente como a
  distribuição "caminha" com a idade. Isso é inerentemente por-talhão
  (não dá pra sobrepor curvas de talhões diferentes sem confundir "efeito
  da idade" com "efeito do talhão"), por isso o combo de talhão tem uma
  opção extra "Todos os talhões" que só afeta os Ingressos (a curva exige
  um talhão específico).

- Ajuste logístico / ITD: modelo logístico y = a/(1 + b·exp(-c·Idade))
  ajustado (mínimos quadrados não-lineares, ver simulacao.ajustar_logistico)
  sobre pontos (idade, 1/IP) ou (idade, 1/IPM) — conforme "Base do ajuste
  logístico" (Configurações) — respeita o filtro de talhão: talhão
  específico usa os pontos desse talhão (já 1 por idade); "Todos os
  talhões" sempre usa a média geral por idade (não os pontos brutos de
  todos os talhões misturados, que não formam uma curva única coerente).
  Os 2 sliders (início/fim, 1 até a idade máxima de manejo — Configurações)
  recortam quais idades entram no ajuste E quais idades aparecem no
  gráfico de Curvas de Distribuição acima — os dois painéis sempre mostram
  o mesmo recorte de idades. Refeito no cliente a cada movimento de
  slider (sobre os pontos já lidos/calculados, sem recalcular DD/IP do
  zero) — por isso ITD aqui pode diferir da coluna "itd" persistida em
  simulacao_mip_ajuste_logistico, que é sempre o ajuste com TODAS as
  idades (sliders no início/fim de curso). ITD = ln(b)/c, o ponto de
  inflexão da curva ajustada (onde 1/IP cresce mais rápido, i.e. onde IP
  declina mais rápido) — pós-processamento sobre os parâmetros a/b/c
  ajustados, ao contrário do antigo modelo expolinear (onde ITD saía
  direto como um dos parâmetros).

O gráfico de dispersão (Ingressos) e o gráfico de curvas ficam em
app/widgets/grafico_curvas.py (GraficoDispersao/GraficoCurvasDistribuicao)
— cada um já se redesenha sozinho quando o tema muda (mesmo padrão de
app/widgets/grafico_weibull.py), então esta tela não precisa de um
ouvinte de tema próprio como o original (`tema.registrar_ouvinte_tema`)
tinha.

Layout fixo (QHBoxLayout/QVBoxLayout com proporção por stretch, sem
QSplitter) — o tamanho da tela não muda, não precisa de painéis
redimensionáveis à mão pelo usuário. Talhão + "média geral" + exportar
ficam numa única linha de cabeçalho no topo, acima de tudo (tabela de
Ingressos, gráficos, Curvas de Distribuição).
"""
import numpy as np
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core import simulacao
from ..core.db import conectar
from ..theme import icones, qss
from ..widgets.grafico_curvas import GraficoCurvasDistribuicao, GraficoDispersao
from ..widgets.slider_intervalo import SliderIntervalo
from ..widgets.tabela import Tabela
from .base import TelaBase

TODOS_TALHOES = "Todos os talhões"

# xlsxwriter em vez do padrão do pandas pra .xlsx (openpyxl) — mais rápido em
# planilhas grandes (ver app/screens/simulacao.py, mesma constante, onde foi
# medido e documentado por que NÃO usar a opção "constant_memory" do
# xlsxwriter, apesar de mais rápida ainda — corrompe dado silenciosamente).
_ENGINE_KWARGS_EXCEL = {"engine": "xlsxwriter"}

NOMES_COLUNAS_EXPORTACAO = {
    "talhao": "Talhão",
    "idade": "Idade",
    "diametro_diferenciador": "Diâmetro Diferenciador",
    "ingresso_percentual": "Ingresso Percentual",
    "ingresso_percentual_medio": "Ingresso Percentual Médio (IPM)",
    "inverso_ip": "1/IP",
    "inverso_ipm": "1/IPM",
}

# Planilha extra da exportação (aba "Ingressos por Classe"): mesmo DD/IP
# de simulacao_mip_continuo, "explodido" contra cada classe diamétrica
# configurada (Configurações) — DD/IP são valores por (talhão, idade), não
# por classe, então se repetem em todas as linhas de classe daquele
# (talhão, idade); serve pra quem quer pivotar/plotar por classe no Excel.
NOMES_COLUNAS_POR_CLASSE = {
    "classe": "Classe",
    "talhao": "Talhão",
    "idade": "Idade",
    "diametro_diferenciador": "Diâmetro Diferenciador",
    "ingresso_percentual": "Ingresso Percentual",
}


class TelaIngressosCurvasDistribuicao(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._ultimo_df_ip = None
        self._mensagem_ip = None
        self._modo_tabela_ip_atual = None
        self._df_talhao_atual = None
        self._df_ip_para_ajuste = None
        self._idade_maxima_manejo = None
        self._base_ajuste_logistico = "ip"

        layout_raiz = QVBoxLayout(self)
        layout_raiz.setContentsMargins(8, 8, 8, 8)

        # ---- cabeçalho: talhão + média geral (mesma linha) + exportar ----
        linha_cabecalho = QHBoxLayout()
        linha_cabecalho.addWidget(QLabel("Talhão"))
        self.combo_talhao = QComboBox()
        self.combo_talhao.setMinimumWidth(220)
        self.combo_talhao.textActivated.connect(self._ao_mudar_talhao)
        linha_cabecalho.addWidget(self.combo_talhao)
        self.checkbox_media_geral = QCheckBox("Ingressos: média geral por idade (entre talhões)")
        self.checkbox_media_geral.setChecked(True)
        self.checkbox_media_geral.toggled.connect(self._ao_alternar_media_geral)
        linha_cabecalho.addWidget(self.checkbox_media_geral)
        linha_cabecalho.addStretch(1)
        botao_exportar = QPushButton("Exportar Ingressos para Excel...")
        icones.aplicar_icone(botao_exportar, "exportar")
        botao_exportar.clicked.connect(self.exportar_excel)
        linha_cabecalho.addWidget(botao_exportar)
        layout_raiz.addLayout(linha_cabecalho)

        # Layout fixo (sem QSplitter) — o tamanho da tela não muda, não
        # precisa de painéis redimensionáveis pelo usuário.
        layout_conteudo = QHBoxLayout()
        layout_conteudo.setSpacing(8)
        layout_raiz.addLayout(layout_conteudo, 1)

        layout_tabelas = QVBoxLayout()
        layout_graficos = QVBoxLayout()
        layout_conteudo.addLayout(layout_tabelas, 1)
        layout_conteudo.addLayout(layout_graficos, 1)

        # ---- tabela de Ingressos + painel de ajuste logístico/ITD ----
        self.label_status_ip = QLabel("")
        layout_tabelas.addWidget(self.label_status_ip)
        self.tabela_ip = Tabela(
            colunas=("talhao", "idade", "diametro_diferenciador",
                     "ingresso_percentual", "ingresso_percentual_medio",
                     "inverso_ip", "inverso_ipm"),
            tipos_iniciais={
                "idade": "Inteiro", "diametro_diferenciador": "Float",
                "ingresso_percentual": "Float", "ingresso_percentual_medio": "Float",
                "inverso_ip": "Float", "inverso_ipm": "Float",
            },
            rotulos=NOMES_COLUNAS_EXPORTACAO,
            distribuir_igualmente=True,
        )
        # Painel de ajuste virou só o filtro de idade (1 slider, altura
        # fixa) — sem stretch, pra tabela ganhar todo o espaço vertical
        # sobrando (stretch=1).
        layout_tabelas.addWidget(self.tabela_ip, 1)
        layout_tabelas.addWidget(self._montar_painel_ajuste_itd())

        # ---- gráficos ----
        self.grafico_ip = GraficoDispersao()
        layout_graficos.addWidget(self.grafico_ip, 1)

        self.label_status_curva = QLabel("")
        layout_graficos.addWidget(self.label_status_curva)
        self.grafico_curvas = GraficoCurvasDistribuicao()
        layout_graficos.addWidget(self.grafico_curvas, 1)

        self.recarregar_lista()

    def _montar_painel_ajuste_itd(self):
        painel = QWidget()
        layout = QVBoxLayout(painel)
        layout.setContentsMargins(0, 8, 0, 0)

        # ---- slider de intervalo [início, fim] — 1 trilha, 2 bolinhas
        # (SliderIntervalo, ver app/widgets/slider_intervalo.py — Qt não
        # tem range slider nativo). Recorta os pontos do ajuste, as
        # idades exibidas nas Curvas de Distribuição (gráfico à direita) E
        # o intervalo plotado no gráfico Idade × IP/1-IP — todos os 3
        # sempre mostram o mesmo recorte de idades (ver
        # _ao_mover_slider_intervalo).
        layout.addWidget(QLabel("Filtro (Idade):"))
        linha_slider = QHBoxLayout()
        self.label_idade_inicio = QLabel("—")
        linha_slider.addWidget(self.label_idade_inicio)
        self.slider_idade = SliderIntervalo()
        self.slider_idade.valoresAlterados.connect(self._ao_mover_slider_intervalo)
        linha_slider.addWidget(self.slider_idade, 1)
        self.label_idade_fim = QLabel("—")
        linha_slider.addWidget(self.label_idade_fim)
        layout.addLayout(linha_slider)

        return painel

    # ---------------- ciclo de vida da tela ----------------

    def novo_registro(self):
        # tela só de leitura/cálculo — existe pra manter a mesma interface
        # das outras telas (chamada ao trocar de projeto).
        pass

    def recarregar_lista(self):
        try:
            conn = conectar()
        except RuntimeError:
            self.combo_talhao.clear()
            self._configurar_sliders_idade(None)
            self._definir_estado_ip(None, "Nenhum projeto aberto.")
            self._ao_mudar_talhao()
            return

        try:
            talhoes = simulacao.obter_talhoes_disponiveis(conn)
            try:
                idade_maxima_manejo = simulacao.obter_idade_maxima_manejo(conn)
            except ValueError:
                idade_maxima_manejo = None
            self._base_ajuste_logistico = simulacao.obter_base_ajuste_logistico(conn)
        finally:
            conn.close()

        valores_talhao = [TODOS_TALHOES] + talhoes
        talhao_atual = self.combo_talhao.currentText()
        self.combo_talhao.blockSignals(True)
        self.combo_talhao.clear()
        self.combo_talhao.addItems(valores_talhao)
        self.combo_talhao.setCurrentText(talhao_atual if talhao_atual in valores_talhao else TODOS_TALHOES)
        self.combo_talhao.blockSignals(False)

        self._configurar_sliders_idade(idade_maxima_manejo)
        self._recalcular_ip()
        self._ao_mudar_talhao()

    # ---------------- Ingressos Percentuais (tabela + gráfico) ----------------

    def _recalcular_ip(self):
        try:
            conn = conectar()
        except RuntimeError:
            self._definir_estado_ip(None, "Nenhum projeto aberto.")
            return
        try:
            try:
                # Só lê o que "Gerar simulação" já calculou e persistiu
                # (calcular_mip_continuo) — não recalcula nada aqui.
                df = simulacao.obter_mip_continuo(conn)
            except ValueError as e:
                self._definir_estado_ip(None, str(e))
                return
        finally:
            conn.close()
        self._definir_estado_ip(df, None)

    def _definir_estado_ip(self, df, mensagem_erro):
        self._ultimo_df_ip = df
        self._mensagem_ip = mensagem_erro
        if df is None:
            self.tabela_ip.definir_linhas([])
            self.label_status_ip.setText(mensagem_erro or "")
            qss.aplicar_status(self.label_status_ip, "aviso" if mensagem_erro else "neutro")
            mensagem = mensagem_erro or "Nenhum projeto aberto."
            self.grafico_ip.mostrar_mensagem(mensagem)
            self._df_ip_para_ajuste = None
        else:
            self._atualizar_tabela_e_graficos_ip()

    def _ao_alternar_media_geral(self, _marcado=None):
        # Só troca a exibição (tabela + gráficos) a partir do que já foi
        # calculado — não precisa reconectar no banco nem recalcular DD/IP.
        self._atualizar_tabela_e_graficos_ip()

    def _df_ip_talhao_filtrado(self):
        df = self._ultimo_df_ip
        if df is None:
            return None
        talhao = self.combo_talhao.currentText()
        if talhao and talhao != TODOS_TALHOES:
            return df[df["talhao"] == talhao]
        return df

    @staticmethod
    def _adicionar_colunas_inversas(df):
        """1/IP e 1/IPM — calculados aqui na tela (não persistidos em
        banco), NaN onde IP/IPM = 0 (1/0 não é definido)."""
        df = df.copy()
        df["inverso_ip"] = np.where(
            df["ingresso_percentual"] != 0, 1.0 / df["ingresso_percentual"], np.nan)
        df["inverso_ipm"] = np.where(
            df["ingresso_percentual_medio"] != 0, 1.0 / df["ingresso_percentual_medio"], np.nan)
        return df

    @classmethod
    def _agrupar_ip_para_exibicao(cls, df, media_geral):
        """DataFrame de fato mostrado na tabela/gráficos — usado também por
        exportar_excel, pra exportar exatamente o que está na tela (média
        geral por idade ou detalhado por talhão × idade) em vez de sempre
        exportar o detalhado bruto."""
        if media_geral:
            agrupado = df.groupby("idade", as_index=False)[
                ["diametro_diferenciador", "ingresso_percentual", "ingresso_percentual_medio"]
            ].mean()
            return cls._adicionar_colunas_inversas(agrupado)
        detalhado = df[["talhao", "idade", "diametro_diferenciador",
                         "ingresso_percentual", "ingresso_percentual_medio"]]
        return cls._adicionar_colunas_inversas(detalhado)

    def _atualizar_tabela_e_graficos_ip(self):
        df = self._df_ip_talhao_filtrado()
        if df is None:
            return

        talhao_sel = self.combo_talhao.currentText()
        contexto = f"talhão {talhao_sel}" if talhao_sel and talhao_sel != TODOS_TALHOES else "todos os talhões"
        media_geral = self.checkbox_media_geral.isChecked()
        df_exibicao = self._agrupar_ip_para_exibicao(df, media_geral)

        if media_geral:
            self._garantir_colunas_tabela_ip("media")
            linhas = list(df_exibicao.itertuples(index=False, name=None))
            self.tabela_ip.definir_linhas(linhas)
            self.label_status_ip.setText(f"{len(df_exibicao):,} idade(s) — média geral ({contexto}).")
            qss.aplicar_status(self.label_status_ip, "sucesso")
        else:
            self._garantir_colunas_tabela_ip("detalhado")
            linhas = list(df_exibicao.itertuples(index=False, name=None))
            self.tabela_ip.definir_linhas(linhas)
            self.label_status_ip.setText(f"{len(df):,} linha(s) (talhão × idade) — {contexto}.")
            qss.aplicar_status(self.label_status_ip, "sucesso")

        # O ajuste logístico/ITD sempre olha pro mesmo recorte por talhão
        # (df, ainda sem o groupby de exibição) — não pro df_exibicao
        # abaixo, que depende do checkbox "média geral": com "Todos os
        # talhões" selecionado, o ajuste usa a média geral por idade de
        # qualquer forma (não os pontos brutos de todos os talhões
        # misturados, que não formam uma curva logística única coerente);
        # com um talhão específico, tanto faz — já é 1 ponto por idade dos
        # dois jeitos. Roda ANTES do gráfico pra poder sobrepor a curva
        # ajustada nele (ver _atualizar_grafico_ip).
        self._df_ip_para_ajuste = self._calcular_df_ajuste(df)
        resultado_ajuste = self._atualizar_ajuste_itd()

        self._atualizar_grafico_ip(df_exibicao, contexto, resultado_ajuste)

    def _atualizar_grafico_ip(self, df_exibicao, contexto, resultado_ajuste):
        inicio, fim = self.slider_idade.values()
        df_filtrado = df_exibicao[(df_exibicao["idade"] >= inicio) & (df_exibicao["idade"] <= fim)]
        sufixo_titulo = f" — {contexto} (intervalo {inicio}–{fim})"
        # dropna separado do IP: inverso_ip é NaN onde IP = 0 (1/0
        # indefinido, ver _adicionar_colunas_inversas) mesmo em linhas
        # onde IP em si tem valor — um dropna só de "ingresso_percentual"
        # deixaria passar NaN pro eixo direito (1/IP).
        df_valido_ip = df_filtrado.dropna(subset=["ingresso_percentual"])
        df_valido_inv_ip = df_filtrado.dropna(subset=["inverso_ip"])

        # Curva do modelo ajustado sobreposta no eixo direito (1/IP) — só
        # quando o ajuste rodou sobre IP (base "ip"): se foi sobre IPM, a
        # curva prevê 1/IPM, não comparável aos pontos de 1/IP deste
        # gráfico. Legenda já traz os parâmetros ajustados; o ponto de
        # inflexão (idade=ITD, y=a/2 — definição do ponto de inflexão da
        # logística, ver core/simulacao.py:modelo_logistico) fica marcado
        # à parte, com o valor de ITD do lado.
        curva_x = curva_y = rotulo_curva = ponto_itd = rotulo_itd = None
        if resultado_ajuste is not None and self._base_ajuste_logistico == "ip":
            a, b, c, itd, r2 = resultado_ajuste
            curva_x = np.linspace(inicio, fim, 100)
            curva_y = simulacao.modelo_logistico(curva_x, a, b, c)
            rotulo_curva = f"Ajuste Logístico (a={a:.4f}; b={b:.4f}; c={c:.4f}; R²={r2:.4f})"
            ponto_itd = (itd, a / 2.0)
            idade_maxima = self._idade_maxima_manejo or fim
            aviso_itd = (
                " ⚠ fora do intervalo" if simulacao.itd_fora_do_intervalo(itd, idade_maxima) else "")
            rotulo_itd = f"ITD = {itd:.2f}{aviso_itd}"

        self.grafico_ip.atualizar(
            df_valido_ip["idade"], df_valido_ip["ingresso_percentual"],
            f"Idade × Ingresso Percentual (IP){sufixo_titulo}", "IP",
            df_valido_inv_ip["idade"], df_valido_inv_ip["inverso_ip"], "1/IP",
            curva_x, curva_y, rotulo_curva, ponto_itd, rotulo_itd)

    def _garantir_colunas_tabela_ip(self, modo):
        """Só redefine as colunas da tabela (o que reseta filtro/ordenação/
        resumo dela) quando o modo muda de fato — alternar entre
        detalhado/média geral (ou trocar de talhão) não deve mexer nisso à
        toa a cada atualização."""
        if self._modo_tabela_ip_atual == modo:
            return
        self._modo_tabela_ip_atual = modo

        if modo == "media":
            self.tabela_ip.redefinir_colunas(
                colunas=("idade", "diametro_diferenciador",
                         "ingresso_percentual", "ingresso_percentual_medio",
                         "inverso_ip", "inverso_ipm"),
                tipos_iniciais={
                    "idade": "Inteiro", "diametro_diferenciador": "Float",
                    "ingresso_percentual": "Float", "ingresso_percentual_medio": "Float",
                    "inverso_ip": "Float", "inverso_ipm": "Float",
                },
                rotulos=NOMES_COLUNAS_EXPORTACAO,
            )
        else:
            self.tabela_ip.redefinir_colunas(
                colunas=("talhao", "idade", "diametro_diferenciador",
                         "ingresso_percentual", "ingresso_percentual_medio",
                         "inverso_ip", "inverso_ipm"),
                tipos_iniciais={
                    "idade": "Inteiro", "diametro_diferenciador": "Float",
                    "ingresso_percentual": "Float", "ingresso_percentual_medio": "Float",
                    "inverso_ip": "Float", "inverso_ipm": "Float",
                },
                rotulos=NOMES_COLUNAS_EXPORTACAO,
            )

    # ---------------- Ajuste logístico / ITD ----------------

    def _calcular_df_ajuste(self, df):
        """Pontos (idade, IP, IPM) que alimentam o ajuste logístico — mesma
        regra "talhão específico vs. Todos" do gráfico Idade × IP, mas
        SEMPRE via média geral por idade quando é "Todos os talhões"
        (independente do checkbox "mostrar média geral", que só afeta o
        formato da tabela/gráfico de Ingressos). Mantém as duas colunas
        (IP e IPM) — _atualizar_ajuste_itd escolhe qual alimenta o ajuste
        conforme "Base do ajuste logístico" (Configurações)."""
        talhao_sel = self.combo_talhao.currentText()
        colunas_base = ["ingresso_percentual", "ingresso_percentual_medio"]
        if talhao_sel and talhao_sel != TODOS_TALHOES:
            base = df[["idade"] + colunas_base]
        else:
            base = df.groupby("idade", as_index=False)[colunas_base].mean()
        return base.dropna(subset=["idade"]).sort_values("idade")

    def _configurar_sliders_idade(self, idade_maxima_manejo):
        """Reconfigura o slider de intervalo (início/fim) quando a idade
        máxima de manejo (Configurações) é lida de novo — mas só reseta a
        faixa do usuário pro máximo (1 até idade_maxima_manejo, ou seja
        "usa tudo") quando ainda não havia uma faixa válida antes (1ª vez
        que a tela carrega ou o valor configurado mudou de tal forma que
        a faixa atual ficou fora da nova); só trocar de talhão/projeto
        sem mexer em Configurações não deve resetar o que o usuário já
        tinha escolhido. Esse intervalo [início, fim] recorta os pontos
        do ajuste logístico, as idades exibidas nas Curvas de
        Distribuição E o recorte plotado no gráfico Idade × IP/1-IP (ver
        _atualizar_ajuste_itd/_atualizar_curva/_atualizar_grafico_ip)."""
        inicio_atual, fim_atual = self.slider_idade.values()
        faixa_valida = (
            self._idade_maxima_manejo is not None
            and idade_maxima_manejo is not None
            and 1 <= inicio_atual <= fim_atual <= idade_maxima_manejo
        )
        self._idade_maxima_manejo = idade_maxima_manejo

        if idade_maxima_manejo is None:
            self.slider_idade.setMinimum(1)
            self.slider_idade.setMaximum(1)
            self.slider_idade.setValues(1, 1)
            self.slider_idade.setEnabled(False)
            self.label_idade_inicio.setText("—")
            self.label_idade_fim.setText("—")
            return

        maximo = max(1, int(round(idade_maxima_manejo)))
        self.slider_idade.setMinimum(1)
        self.slider_idade.setMaximum(maximo)
        self.slider_idade.setEnabled(True)
        if not faixa_valida:
            self.slider_idade.setValues(1, maximo)
        self._atualizar_label_slider()

    def _ao_mover_slider_intervalo(self, _inicio, _fim):
        self._atualizar_label_slider()
        self._atualizar_tabela_e_graficos_ip()
        self._atualizar_curva()

    def _atualizar_label_slider(self):
        inicio, fim = self.slider_idade.values()
        self.label_idade_inicio.setText(str(inicio))
        self.label_idade_fim.setText(str(fim))

    def _atualizar_ajuste_itd(self):
        """Roda o ajuste logístico sobre o recorte [início, fim] atual do
        slider. Devolve (a, b, c, itd, r2) — usado por
        _atualizar_grafico_ip pra sobrepor a curva ajustada e o ponto de
        ITD no gráfico de IP (legenda/rótulo carregam os parâmetros; sem
        painel de texto próprio) — ou None se não deu pra ajustar (dados
        insuficientes/curve_fit não converge, nesse caso a curva/ponto
        simplesmente não aparecem no gráfico)."""
        df = self._df_ip_para_ajuste
        if df is None:
            return None

        coluna_alvo = (
            "ingresso_percentual" if self._base_ajuste_logistico == "ip"
            else "ingresso_percentual_medio")

        inicio, fim = self.slider_idade.values()
        dados = df[(df["idade"] >= inicio) & (df["idade"] <= fim)].dropna(subset=[coluna_alvo])
        # != 0: 1/0 não é definido — idade sem ingresso não entra no
        # ajuste (mesma regra de core/simulacao.py:calcular_mip_continuo).
        dados = dados[dados[coluna_alvo] != 0]
        if len(dados) < simulacao.MINIMO_PONTOS_AJUSTE_LOGISTICO:
            return None

        x = dados["idade"].to_numpy(dtype=float)
        y = 1.0 / dados[coluna_alvo].to_numpy(dtype=float)
        idade_maxima = self._idade_maxima_manejo or float(x.max())

        try:
            return simulacao.ajustar_logistico(x, y, idade_maxima)
        except RuntimeError:
            return None

    def exportar_excel(self):
        df = self._df_ip_talhao_filtrado()
        if df is None or df.empty:
            QMessageBox.warning(
                self, "Ingressos Percentuais",
                "Nada pra exportar ainda — gere a simulação primeiro.")
            return

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Ingressos Percentuais", "", "Planilha Excel (*.xlsx)")
        if not caminho:
            return
        if not caminho.endswith(".xlsx"):
            caminho += ".xlsx"

        df_exibicao = self._agrupar_ip_para_exibicao(df, self.checkbox_media_geral.isChecked())
        df_exportar = df_exibicao.rename(columns=NOMES_COLUNAS_EXPORTACAO)
        df_por_classe = self._montar_df_por_classe(df)

        try:
            with pd.ExcelWriter(caminho, **_ENGINE_KWARGS_EXCEL) as writer:
                df_exportar.to_excel(writer, sheet_name="Ingressos", index=False)
                if df_por_classe is not None:
                    df_por_classe.to_excel(writer, sheet_name="Ingressos por Classe", index=False)
        except Exception as e:
            QMessageBox.critical(self, "Ingressos Percentuais", f"Não foi possível exportar:\n{e}")
            return

        texto_classe = f" + {len(df_por_classe):,} linha(s) por classe" if df_por_classe is not None else ""
        QMessageBox.information(
            self, "Ingressos Percentuais",
            f"Exportado: {len(df_exportar):,} linha(s){texto_classe} em\n{caminho}")

    @staticmethod
    def _montar_df_por_classe(df):
        """Uma linha por (talhão, idade, classe diamétrica), com DD/IP
        repetidos em todas as classes daquele (talhão, idade) — pra a aba
        "Ingressos por Classe" da exportação. `df` é sempre o detalhado
        (talhão × idade, já filtrado por talhão), independente do
        checkbox "média geral" (que só afeta a aba "Ingressos"), já que a
        quebra por classe exige a granularidade de talhão. None se as
        classes diamétricas não estiverem configuradas."""
        try:
            conn = conectar()
        except RuntimeError:
            return None
        try:
            try:
                classes = simulacao.obter_classes_diametricas(conn)
            except ValueError:
                return None
        finally:
            conn.close()
        if len(classes) == 0:
            return None

        base = df[["talhao", "idade", "diametro_diferenciador", "ingresso_percentual"]]
        por_classe = base.merge(pd.DataFrame({"classe": classes}), how="cross")
        por_classe = por_classe[
            ["classe", "talhao", "idade", "diametro_diferenciador", "ingresso_percentual"]
        ].sort_values(["talhao", "idade", "classe"])
        return por_classe.rename(columns=NOMES_COLUNAS_POR_CLASSE)

    # ---------------- Curvas de Distribuição (tabela + gráfico, por talhão) ----------------

    def _ao_mudar_talhao(self, _texto=None):
        talhao = self.combo_talhao.currentText()

        if not talhao:
            mensagem = "Nenhum projeto aberto."
        elif talhao == TODOS_TALHOES:
            mensagem = ('Selecione um talhão específico (não "Todos os talhões") pra ver as '
                        "curvas de distribuição.")
        else:
            mensagem = None

        if mensagem is not None:
            self._df_talhao_atual = None
            self.grafico_curvas.mostrar_mensagem(mensagem)
            self.label_status_curva.setText("")
            self._atualizar_tabela_e_graficos_ip()
            return

        try:
            conn = conectar()
        except RuntimeError:
            self._df_talhao_atual = None
            self.grafico_curvas.mostrar_mensagem("Nenhum projeto aberto.")
            self._atualizar_tabela_e_graficos_ip()
            return
        try:
            try:
                df = simulacao.obter_distribuicoes_por_talhao(conn, talhao)
            except ValueError as e:
                self._df_talhao_atual = None
                self.grafico_curvas.mostrar_mensagem(str(e))
                self._atualizar_tabela_e_graficos_ip()
                return
        finally:
            conn.close()

        self._df_talhao_atual = df
        self._atualizar_curva()
        self._atualizar_tabela_e_graficos_ip()

    def _atualizar_curva(self):
        df = self._df_talhao_atual
        if df is None or df.empty:
            return

        idades_disponiveis = sorted(df["idade"].unique().tolist())
        inicio, fim = self.slider_idade.values()
        idades_escolhidas = [idade for idade in idades_disponiveis if inicio <= idade <= fim]

        if not idades_escolhidas:
            self.grafico_curvas.mostrar_mensagem(
                "Nenhuma idade simulada no intervalo [início, fim] escolhido (painel de "
                "Ajuste logístico).")
            self.label_status_curva.setText("")
            return

        self.label_status_curva.setText(
            f"{len(idades_escolhidas)} de {len(idades_disponiveis)} idade(s) exibida(s) "
            f"(intervalo {inicio}–{fim}).")
        qss.aplicar_status(self.label_status_curva, "sucesso")

        self.grafico_curvas.atualizar(df, idades_escolhidas, self.combo_talhao.currentText())
