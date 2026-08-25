# -*- coding: utf-8 -*-
"""
Tela "Modelos": cadastro dos modelos matemáticos (afilamento, volume,
distribuição diamétrica, crescimento...) usados pela simulação. Porte
completo de app/screens/modelos.py (Tkinter) — mesma lógica de CRUD e
importação de planilha, só a camada de widgets trocada para Qt (ver
app/widgets/tabela.py, cartao.py, importacao_dialogs.py).
"""
import io
import json
import re

import pandas as pd
from matplotlib.mathtext import math_to_image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QHeaderView, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from ..core import projeto, simulacao
from ..core.db import conectar
from ..core.equacao_latex import equacao_para_mathtext
from ..core.importador import NOME_TABELA_BASE_IFC, ler_planilha, listar_abas
from ..theme import icones, manager as tema, qss
from ..theme import tokens
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from ..widgets.importacao_dialogs import escolher_aba, escolher_cabecalho, escolher_mapeamento_colunas
from ..widgets.tabela import IID_RESUMO, Tabela
from .base import TelaBase

CAMPOS_IMPORTACAO_MODELOS = [
    ("nome", "Nome", True),
    ("tipo", "Tipo", True),
    ("estrato_coluna", "Coluna do estrato", False),
    ("estrato", "Valor do estrato", False),
    ("equacao", "Equação", False),
    ("coeficientes", "Coeficientes (uma linha \"nome = valor\" por linha de texto)", False),
    ("observacoes", "Observações", False),
]

TIPOS = [
    "Afilamento / Taper",
    "Volume",
    "Distribuição Diamétrica (Weibull)",
    "Crescimento",
    "Outro",
]

ROTULO_TODOS = "Todos"

DICA_VARIAVEIS_X = (
    "Só define QUANTAS entradas o modelo tem — cada uma vira x (se só "
    "tiver uma) ou x1, x2, ... (se tiver mais de uma), nomes fixos "
    "que você usa na equação abaixo. Não precisa (nem adianta) "
    "escolher aqui uma coluna real: a ligação de cada x com uma "
    "coluna de verdade é feita depois, arrastando o fio na tela "
    "Construtor de Variáveis."
)

DICA_EQUACAO = (
    "Descreva a fórmula do modelo em texto livre, usando x (ou x1, x2, ... se "
    "tiver mais de uma variável) pra representar as entradas — os nomes reais "
    "das colunas não entram aqui: quem liga cada x a uma coluna de verdade é a "
    "tela Construtor de Variáveis, arrastando o fio. Um modelo pode ter "
    "quantas variáveis forem necessárias.\n\n"
    "Exemplo (1 variável):\n"
    "f(x) = (forma/escala)*(x/escala)^(forma-1)*EXP(-(x/escala)^forma)\n\n"
    "Exemplo (2 variáveis):\n"
    "f(x1, x2) = a*exp(b/(x1*x2))"
)

DICA_COEFICIENTES = (
    "Um coeficiente por linha, no formato:\n"
    "nome = valor\n\n"
    "Exemplo:\n"
    "escala_base = 12.5\n"
    "forma_base = 2.3"
)


class TelaModelos(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.registro_atual_id = None

        layout_raiz = QVBoxLayout(self)
        layout_raiz.setContentsMargins(
            tokens.ESPACO_SM, tokens.ESPACO_SM,
            tokens.ESPACO_SM, tokens.ESPACO_SM)
        layout_raiz.setSpacing(tokens.ESPACO_SM)

        cabecalho = CabecalhoTela("Modelos")
        cabecalho.adicionar_acao(
            "Importar planilha", self.importar_modelos, icone="importar")
        layout_raiz.addWidget(cabecalho)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._montar_lista())
        splitter.addWidget(self._montar_form())
        # Lista (tabela "Modelos cadastrados") mais larga que o formulário
        # já ao abrir a tela — stretch sozinho só define a proporção ao
        # REDIMENSIONAR depois, não o tamanho inicial; setSizes garante a
        # largura maior da lista de cara.
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 450])
        layout_raiz.addWidget(splitter)

        self.recarregar_lista()

    # ---------------- painel esquerdo: lista ----------------

    def _montar_lista(self):
        painel = QWidget()
        layout = QVBoxLayout(painel)
        layout.setContentsMargins(0, 0, 0, 0)
        # Mesmo espaçamento do painel direito (ver _montar_form) — título
        # + conteúdo abaixo em ambos os painéis, pra bater o topo da
        # tabela com o topo do cartão "Identificação" ao lado.
        layout.setSpacing(12)

        rotulo = QLabel("Modelos cadastrados")
        qss.aplicar_variante(rotulo, "titulo")
        layout.addWidget(rotulo)

        self.tabela = Tabela(
            colunas=("nome", "equacao", "estrato"),
            rotulos={"nome": "Nome", "equacao": "Equação", "estrato": "Estrato"},
            distribuir_igualmente=True)
        self.tabela.view.setObjectName("TabelaModelos")
        self.tabela.view.setShowGrid(False)
        self.tabela.definir_cantos_arredondados(8)
        self.tabela.definir_alinhamento_celulas(Qt.AlignmentFlag.AlignCenter)
        self.tabela.view.setWordWrap(True)
        self.tabela.view.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.tabela.selecaoAlterada.connect(self._ao_selecionar)
        layout.addWidget(self.tabela)

        return painel

    # ---------------- painel direito: formulário ----------------

    def _montar_form(self):
        painel = QWidget()
        layout_painel = QVBoxLayout(painel)
        layout_painel.setContentsMargins(0, 0, 0, 0)
        layout_painel.setSpacing(10)

        # QScrollArea em vez de só um QWidget — o formulário tem vários
        # campos grandes (Equação, Coeficientes, Observações) e uma janela
        # menor que isso cortaria os últimos sem nenhum jeito de rolar.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        conteudo = QWidget()
        layout = QVBoxLayout(conteudo)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        titulo = QLabel("Detalhes do modelo")
        qss.aplicar_variante(titulo, "titulo")
        layout.addWidget(titulo)

        cartao_identificacao = Cartao("Identificação")
        layout.addWidget(cartao_identificacao)
        self._montar_secao_identificacao(cartao_identificacao.corpo)

        cartao_variaveis = Cartao("Variáveis e equação")
        layout.addWidget(cartao_variaveis)
        self._montar_secao_variaveis_equacao(cartao_variaveis.corpo)

        cartao_coeficientes = Cartao("Coeficientes")
        layout.addWidget(cartao_coeficientes)
        self._montar_secao_coeficientes(cartao_coeficientes.corpo)

        cartao_observacoes = Cartao("Observações")
        layout.addWidget(cartao_observacoes)
        self._montar_secao_observacoes(cartao_observacoes.corpo)
        layout.addStretch(1)

        scroll.setWidget(conteudo)
        layout_painel.addWidget(scroll)

        # Botões fora da área rolável, fixos no rodapé do painel — igual
        # ao botão "Importar planilha..." no painel esquerdo (ver
        # _montar_lista), pra as bordas inferiores dos dois painéis
        # baterem certinho e pra sempre dar pra salvar/excluir sem
        # precisar rolar o formulário até o fim.
        botoes = QHBoxLayout()
        botao_salvar = QPushButton("Salvar")
        qss.aplicar_variante(botao_salvar, "salvar")
        icones.aplicar_icone(botao_salvar, "salvar", cor="white")
        botao_salvar.clicked.connect(self.salvar_registro)
        botoes.addWidget(botao_salvar)
        botao_adicionar = QPushButton("Salvar como novo")
        icones.aplicar_icone(botao_adicionar, "adicionar")
        botao_adicionar.clicked.connect(self.adicionar_registro)
        botoes.addWidget(botao_adicionar)
        botao_excluir = QPushButton("Excluir")
        qss.aplicar_variante(botao_excluir, "perigo")
        icones.aplicar_icone(botao_excluir, "excluir")
        botao_excluir.clicked.connect(self.excluir_registro)
        botoes.addWidget(botao_excluir)
        botao_limpar = QPushButton("Limpar")
        icones.aplicar_icone(botao_limpar, "limpar")
        botao_limpar.clicked.connect(self.novo_registro)
        botoes.addWidget(botao_limpar)
        botoes.addStretch(1)
        layout_painel.addLayout(botoes)

        self._atualizar_estado_estrato()
        return painel

    def _montar_secao_identificacao(self, container):
        layout = QVBoxLayout(container)

        # Nome/Tipo (esquerda) e Coluna/Valor do estrato (direita) lado a
        # lado, mesmas linhas (Nome↔Coluna, Tipo↔Valor) — um grid só, com
        # o painel de estrato ocupando a coluna 2 (rowspan 2) em vez de um
        # bloco embaixo.
        grade_nome_tipo = QGridLayout()
        grade_nome_tipo.setColumnStretch(1, 1)
        grade_nome_tipo.setColumnStretch(2, 1)

        grade_nome_tipo.addWidget(QLabel("Nome"), 0, 0)
        self.entry_nome = QLineEdit()
        grade_nome_tipo.addWidget(self.entry_nome, 0, 1)

        grade_nome_tipo.addWidget(QLabel("Tipo"), 1, 0)
        self.combo_tipo = QComboBox()
        self.combo_tipo.addItem("")
        self.combo_tipo.addItems(TIPOS)
        grade_nome_tipo.addWidget(self.combo_tipo, 1, 1)

        self.painel_estrato = QWidget()
        qss.aplicar_variante(self.painel_estrato, "transparente")
        grade_estrato = QGridLayout(self.painel_estrato)
        grade_estrato.setContentsMargins(0, 0, 0, 0)
        grade_estrato.setColumnStretch(1, 1)

        grade_estrato.addWidget(QLabel("Coluna do estrato"), 0, 0)
        self.combo_estrato_coluna = QComboBox()
        self.combo_estrato_coluna.addItem("")
        self.combo_estrato_coluna.textActivated.connect(self._ao_mudar_coluna_estrato)
        grade_estrato.addWidget(self.combo_estrato_coluna, 0, 1)

        grade_estrato.addWidget(QLabel("Valor do estrato"), 1, 0)
        self.combo_estrato_valor = QComboBox()
        self.combo_estrato_valor.addItem("")
        grade_estrato.addWidget(self.combo_estrato_valor, 1, 1)

        grade_nome_tipo.addWidget(self.painel_estrato, 0, 2, 2, 1)
        layout.addLayout(grade_nome_tipo)

        # "Todos" (padrão): modelo vale pra base inteira e o painel de
        # coluna/valor do estrato acima fica escondido; desmarcar "abre"
        # mostrando os dois campos (ver _atualizar_estado_estrato).
        self.checkbox_estrato_todos = QCheckBox("Global")
        self.checkbox_estrato_todos.setChecked(True)
        self.checkbox_estrato_todos.toggled.connect(self._ao_mudar_estrato_todos)
        layout.addWidget(self.checkbox_estrato_todos)

    def _montar_secao_variaveis_equacao(self, container):
        layout = QHBoxLayout(container)

        coluna_equacao = QVBoxLayout()
        coluna_equacao.addWidget(QLabel("Equação"))
        self.txt_equacao = QPlainTextEdit()
        self.txt_equacao.setFixedHeight(90)
        self.txt_equacao.setToolTip(DICA_EQUACAO)
        coluna_equacao.addWidget(self.txt_equacao)

        # Pré-visualização renderizada (mathtext via matplotlib — ver
        # core/equacao_latex.py) logo abaixo do quadro da equação, na MESMA
        # linha que os botões "Adicionar variável"/"Remover selecionada" da
        # coluna ao lado (mesmo padrão label+caixa de altura igual nas duas
        # colunas antes deste ponto — ver comentário do addStretch abaixo).
        # Debounce de 250ms (_timer_preview_equacao) em vez de renderizar a
        # cada tecla: digitar rápido dispara textChanged várias vezes por
        # segundo, e cada render é um math_to_image (rasteriza fonte,
        # monta PNG) — sem debounce, isso empilharia trabalho descartado a
        # cada tecla nova.
        self.label_equacao_renderizada = QLabel()
        self.label_equacao_renderizada.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label_equacao_renderizada.setMinimumHeight(32)
        self.label_equacao_renderizada.setToolTip(
            "Pré-visualização da equação acima, só pra conferir — não afeta o que é salvo/"
            "avaliado (isso continua sendo o texto digitado, avaliado por core/motor_modelos.py).")
        coluna_equacao.addWidget(self.label_equacao_renderizada)

        self._timer_preview_equacao = QTimer(self)
        self._timer_preview_equacao.setSingleShot(True)
        self._timer_preview_equacao.setInterval(250)
        self._timer_preview_equacao.timeout.connect(self._atualizar_preview_equacao)
        self.txt_equacao.textChanged.connect(self._timer_preview_equacao.start)
        tema.obter().themeChanged.connect(lambda _modo=None: self._atualizar_preview_equacao())

        # addStretch no fim de cada coluna — sem isso, a coluna mais curta
        # (a que sobra altura, já que as duas dividem a mesma altura de
        # linha do QHBoxLayout) fica com um vão entre o rótulo e a caixa
        # em vez do vão sobrar embaixo, desalinhando as duas caixas.
        coluna_equacao.addStretch(1)
        layout.addLayout(coluna_equacao, 2)

        coluna_variaveis = QVBoxLayout()
        coluna_variaveis.addWidget(QLabel("Variáveis independentes"))
        self.lista_variaveis_x = QListWidget()
        self.lista_variaveis_x.setFixedHeight(90)
        self.lista_variaveis_x.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lista_variaveis_x.setToolTip(DICA_VARIAVEIS_X)
        coluna_variaveis.addWidget(self.lista_variaveis_x)

        # Lado a lado — a largura da coluna (e portanto da lista acima)
        # acompanha o que os dois botões precisam pra caber numa linha só.
        botoes_variaveis = QHBoxLayout()
        botao_adicionar_var = QPushButton("Adicionar variável")
        qss.aplicar_tamanho(botao_adicionar_var, "pequeno")
        icones.aplicar_icone(botao_adicionar_var, "adicionar")
        botao_adicionar_var.clicked.connect(self._adicionar_variavel_x)
        botoes_variaveis.addWidget(botao_adicionar_var)
        botao_remover_var = QPushButton("Remover selecionada")
        qss.aplicar_variante(botao_remover_var, "leve")
        qss.aplicar_tamanho(botao_remover_var, "pequeno")
        # Só este botão: ícone vermelho (perigo) mesmo com "remover" ficando
        # neutro por padrão no catálogo (ver app/theme/icones.py) — por
        # isso recolore manualmente em vez de `aplicar_icone(..., "remover")`
        # puro, mas ainda acompanhando troca de tema (cor "perigo" muda
        # entre claro/escuro, ver tokens.PALETA_STATUS).
        def _recolorir_icone_remover_var(_modo=None):
            botao_remover_var.setIcon(icones.icone("remover", cor=tema.obter().cor_status("perigo")))
        _recolorir_icone_remover_var()
        tema.obter().themeChanged.connect(_recolorir_icone_remover_var)
        botao_remover_var.clicked.connect(self._remover_variavel_x)
        botoes_variaveis.addWidget(botao_remover_var)
        coluna_variaveis.addLayout(botoes_variaveis)
        coluna_variaveis.addStretch(1)
        layout.addLayout(coluna_variaveis, 1)

    def _atualizar_preview_equacao(self):
        """Redesenha self.label_equacao_renderizada a partir do texto ATUAL
        de txt_equacao (ver core/equacao_latex.py) — chamada pelo debounce
        de _timer_preview_equacao a cada edição, e direto numa troca de
        tema (a cor do texto renderizado precisa acompanhar, já que é um
        PNG rasterizado, não uma QLabel de texto normal que a stylesheet
        recolore sozinha).

        Equação vazia limpa a prévia; equação inválida/incompleta (usuário
        no meio de digitar, ex: um parêntese ainda sem fechar) MANTÉM a
        última prévia válida em vez de piscar em branco a cada tecla —
        equacao_para_mathtext devolve None nesses dois casos, então só o
        "vazia" precisa de tratamento explícito aqui (todo o resto do texto
        sendo inválido é indistinguível de "ainda não mudou o suficiente
        pra render de novo", e ficar com o último desenho é o resultado
        certo dos dois jeitos)."""
        texto = self.txt_equacao.toPlainText()
        if not texto.strip():
            self.label_equacao_renderizada.clear()
            return

        mathtext = equacao_para_mathtext(texto)
        if mathtext is None:
            return

        try:
            buffer = io.BytesIO()
            math_to_image(
                mathtext, buffer, dpi=130, format="png", color=tema.obter().cor_ui("fg"))
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.getvalue(), "PNG")
        except Exception:
            # mathtext malformado que passou pelo ast.parse (raro, mas
            # ast.parse é só sintaxe Python — não garante que todo nome/
            # combinação vira mathtext válido) — mantém a prévia anterior
            # em vez de derrubar a tela por causa de uma pré-visualização.
            return

        self.label_equacao_renderizada.setPixmap(pixmap)

    def _montar_secao_coeficientes(self, container):
        layout = QVBoxLayout(container)
        self.txt_coeficientes = QPlainTextEdit()
        self.txt_coeficientes.setFixedHeight(120)
        self.txt_coeficientes.setToolTip(DICA_COEFICIENTES)
        layout.addWidget(self.txt_coeficientes)

    def _montar_secao_observacoes(self, container):
        layout = QVBoxLayout(container)
        self.txt_observacoes = QPlainTextEdit()
        self.txt_observacoes.setFixedHeight(90)
        layout.addWidget(self.txt_observacoes)

    # ---------------- lógica ----------------

    def recarregar_lista(self):
        try:
            conn = conectar()
        except RuntimeError:
            self.tabela.definir_linhas([])
            self.combo_estrato_coluna.clear()
            self.combo_estrato_coluna.addItem("")
            return

        try:
            self._atualizar_opcoes_formulario(conn)
            linhas = conn.execute(
                "SELECT id, nome, equacao, estrato_coluna, estrato FROM modelos ORDER BY nome"
            ).fetchall()
        finally:
            conn.close()

        ids = [str(r[0]) for r in linhas]
        valores = [(r[1], r[2] or "", _rotulo_estrato(r[3], r[4])) for r in linhas]
        self.tabela.definir_linhas(valores, ids=ids)

    def _atualizar_opcoes_formulario(self, conn):
        try:
            colunas_talhao = simulacao.colunas_base_ifc_talhao(conn)
        except Exception:
            colunas_talhao = []

        valor_atual = self.combo_estrato_coluna.currentText()
        self.combo_estrato_coluna.blockSignals(True)
        self.combo_estrato_coluna.clear()
        self.combo_estrato_coluna.addItem("")
        self.combo_estrato_coluna.addItems(colunas_talhao)
        if valor_atual in colunas_talhao:
            self.combo_estrato_coluna.setCurrentText(valor_atual)
        else:
            self.combo_estrato_coluna.setCurrentText("")
            self.combo_estrato_valor.clear()
            self.combo_estrato_valor.addItem("")
        self.combo_estrato_coluna.blockSignals(False)

    @staticmethod
    def _valores_estrato(conn, coluna):
        if not coluna:
            return []
        linhas = conn.execute(
            f'SELECT DISTINCT "{coluna}" FROM "{NOME_TABELA_BASE_IFC}" '
            f'WHERE "{coluna}" IS NOT NULL AND "{coluna}" != \'\' ORDER BY "{coluna}" LIMIT 1000'
        ).fetchall()
        return [str(linha[0]) for linha in linhas]

    def _ao_mudar_estrato_todos(self, _marcado=None):
        self._atualizar_estado_estrato()

    def _atualizar_estado_estrato(self):
        todos = self.checkbox_estrato_todos.isChecked()
        self.painel_estrato.setVisible(not todos)

    def _ao_mudar_coluna_estrato(self, _texto=None):
        self.combo_estrato_valor.clear()
        self.combo_estrato_valor.addItem("")
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            valores = self._valores_estrato(conn, self.combo_estrato_coluna.currentText())
        finally:
            conn.close()
        self.combo_estrato_valor.addItems(valores)

    def _definir_variaveis_x(self, variaveis):
        self.lista_variaveis_x.clear()
        self.lista_variaveis_x.addItems(variaveis)

    def _adicionar_variavel_x(self):
        """Nome da variável é sempre gerado (x / x1, x2, ...) — não faz
        sentido pedir pra escolher uma coluna real aqui: quem liga cada x a
        uma coluna de verdade é a tela Construtor de Variáveis, então o
        nome só precisa bater com o que a equação usa."""
        existentes = [self.lista_variaveis_x.item(i).text() for i in range(self.lista_variaveis_x.count())]
        if not existentes:
            self.lista_variaveis_x.addItem("x")
        elif existentes == ["x"]:
            self.lista_variaveis_x.clear()
            self.lista_variaveis_x.addItem("x1")
            self.lista_variaveis_x.addItem("x2")
        else:
            self.lista_variaveis_x.addItem(f"x{len(existentes) + 1}")

    def _remover_variavel_x(self):
        for item in self.lista_variaveis_x.selectedItems():
            self.lista_variaveis_x.takeItem(self.lista_variaveis_x.row(item))

    def _ao_selecionar(self):
        selecao = self.tabela.selecionados()
        if not selecao or selecao[0] == IID_RESUMO:
            return
        id_modelo = int(selecao[0])
        conn = conectar()
        try:
            row = conn.execute(
                "SELECT id, nome, tipo, estrato_coluna, estrato, variavel_x, variaveis_x, "
                "equacao, coeficientes, observacoes FROM modelos WHERE id = ?", (id_modelo,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return

        self.registro_atual_id = row[0]
        self.entry_nome.setText(row[1] or "")
        self.combo_tipo.setCurrentText(row[2] or "")

        estrato_coluna = row[3] or ""
        self.checkbox_estrato_todos.setChecked(not estrato_coluna)
        self.combo_estrato_coluna.setCurrentText(estrato_coluna)
        if estrato_coluna:
            self._ao_mudar_coluna_estrato()
        self.combo_estrato_valor.setCurrentText(row[4] or "")
        self._atualizar_estado_estrato()

        variaveis_x = []
        if row[6]:
            try:
                variaveis_x = json.loads(row[6])
            except json.JSONDecodeError:
                variaveis_x = []
        if not variaveis_x and row[5]:
            # Projeto salvo antes da lista de variáveis existir (só variavel_x).
            variaveis_x = [row[5]]
        self._definir_variaveis_x(variaveis_x)

        self.txt_equacao.setPlainText(row[7] or "")

        coeficientes = {}
        if row[8]:
            try:
                coeficientes = json.loads(row[8])
            except json.JSONDecodeError:
                coeficientes = {}
        texto_coef = "\n".join(f"{k} = {v}" for k, v in coeficientes.items())
        self.txt_coeficientes.setPlainText(texto_coef)

        self.txt_observacoes.setPlainText(row[9] or "")

    def adicionar_registro(self):
        """Cria um modelo novo. Se o formulário já tem algo digitado (Nome
        preenchido — ex: usuário editou um modelo carregado, ou já
        preencheu tudo do zero, antes de clicar aqui), grava exatamente o
        que está nos campos — nunca descarta o que foi digitado. Só cai
        no atalho de nome automático + registro em branco (comportamento
        antigo) quando o Nome está vazio, pra continuar servindo o caso
        de "só clicar e já ganhar uma linha nova pra preencher"."""
        if self.entry_nome.text().strip():
            dados = self._coletar_dados_formulario()
            if dados is None:
                return
            try:
                conn = conectar()
            except RuntimeError as e:
                QMessageBox.warning(self, "Modelos", str(e))
                return
            try:
                cursor = conn.execute(
                    "INSERT INTO modelos "
                    "(nome, tipo, estrato_coluna, estrato, variaveis_x, equacao, coeficientes, observacoes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", dados
                )
                self.registro_atual_id = cursor.lastrowid
                conn.commit()
            finally:
                conn.close()

            projeto.sincronizar()
            self.recarregar_lista()
            self.tabela.selecionar_id(self.registro_atual_id)
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Modelos", str(e))
            return
        try:
            nomes_existentes = {
                r[0] for r in conn.execute("SELECT nome FROM modelos").fetchall()
            }
            nome = "Novo modelo"
            sufixo = 1
            while nome in nomes_existentes:
                sufixo += 1
                nome = f"Novo modelo {sufixo}"
            cursor = conn.execute(
                "INSERT INTO modelos (nome, tipo, coeficientes) VALUES (?, ?, ?)",
                (nome, TIPOS[0], json.dumps({})),
            )
            novo_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        self.recarregar_lista()
        self.tabela.selecionar_id(novo_id)
        self._ao_selecionar()
        self.entry_nome.setFocus()
        self.entry_nome.selectAll()

    def importar_modelos(self):
        """Importa vários modelos de uma vez a partir de uma planilha:
        cada linha vira um registro em `modelos`, com as colunas
        associadas pelo usuário (mesmo diálogo de mapeamento usado nos
        Ajustes de Weibull). "Variáveis (x)" não vem da planilha — cada
        modelo importado ainda precisa dessa etapa manual aqui no
        programa, porque é ela que amarra o nome da variável ao nome
        real da coluna da base de simulação."""
        try:
            projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, "Modelos", str(e))
            return

        caminho_planilha, _ = QFileDialog.getOpenFileName(
            self, "Importar modelos de planilha", "",
            "Planilhas Excel (*.xlsx *.xls);;CSV (*.csv)")
        if not caminho_planilha:
            return

        try:
            abas = listar_abas(caminho_planilha)
        except Exception as e:
            QMessageBox.critical(self, "Modelos", f"Não foi possível ler a planilha:\n{e}")
            return

        if len(abas) == 1:
            aba_escolhida = abas[0]
        else:
            aba_escolhida = escolher_aba(self, "Escolher aba da planilha", abas)
            if aba_escolhida is None:
                return

        linha_cabecalho = escolher_cabecalho(self, "Modelos", caminho_planilha, aba_escolhida)
        if linha_cabecalho is None:
            return

        try:
            df = ler_planilha(caminho_planilha, aba=aba_escolhida, linha_cabecalho=linha_cabecalho)
        except Exception as e:
            QMessageBox.critical(self, "Modelos", f"Não foi possível ler a planilha:\n{e}")
            return

        if df.empty:
            QMessageBox.warning(self, "Modelos", "A planilha não tem linhas de dados.")
            return

        mapeamento = escolher_mapeamento_colunas(
            self, "Associar colunas da planilha aos campos do modelo",
            list(df.columns), CAMPOS_IMPORTACAO_MODELOS)
        if mapeamento is None:
            return

        def valor(valores, campo):
            coluna = mapeamento.get(campo)
            if coluna is None:
                return None
            bruto = valores.get(coluna)
            if bruto is None or pd.isna(bruto):
                return None
            texto = str(bruto).strip()
            return texto or None

        linhas_para_inserir = []
        avisos = []
        for numero, linha in enumerate(df.itertuples(index=False), start=linha_cabecalho + 2):
            valores = dict(zip(df.columns, linha))

            nome = valor(valores, "nome")
            tipo = valor(valores, "tipo")
            if not nome or not tipo:
                avisos.append(f"Linha {numero}: sem Nome e/ou Tipo — ignorada.")
                continue

            coeficientes = {}
            texto_coeficientes = valor(valores, "coeficientes")
            if texto_coeficientes:
                coeficientes, erro = self._analisar_coeficientes(texto_coeficientes)
                if erro:
                    avisos.append(f"Linha {numero} ({nome}): {erro} — coeficientes ignorados.")
                    coeficientes = {}

            linhas_para_inserir.append((
                nome, tipo, valor(valores, "estrato_coluna"), valor(valores, "estrato"),
                valor(valores, "equacao"), json.dumps(coeficientes, ensure_ascii=False),
                valor(valores, "observacoes"),
            ))

        if not linhas_para_inserir:
            QMessageBox.warning(
                self, "Modelos",
                "Nenhuma linha válida pra importar (Nome e Tipo são obrigatórios em todas).")
            return

        conn = conectar()
        try:
            conn.executemany(
                "INSERT INTO modelos "
                "(nome, tipo, estrato_coluna, estrato, equacao, coeficientes, observacoes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                linhas_para_inserir,
            )
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        self.recarregar_lista()

        mensagem = f"{len(linhas_para_inserir)} modelo(s) importado(s)."
        if avisos:
            mensagem += f"\n\n{len(avisos)} linha(s) com aviso:\n" + "\n".join(avisos[:10])
            if len(avisos) > 10:
                mensagem += f"\n... e mais {len(avisos) - 10}."
        mensagem += (
            "\n\nAs \"Variáveis independentes\" não vêm da planilha — selecione a coluna da base "
            "de simulação de cada modelo importado aqui no programa antes de usá-lo.")
        QMessageBox.information(self, "Modelos", mensagem)

    def novo_registro(self):
        self.registro_atual_id = None
        self.tabela.limpar_selecao()
        self.entry_nome.setText("")
        self.combo_tipo.setCurrentText("")
        self.checkbox_estrato_todos.setChecked(True)
        self.combo_estrato_coluna.setCurrentText("")
        self.combo_estrato_valor.clear()
        self.combo_estrato_valor.addItem("")
        self._atualizar_estado_estrato()
        self._definir_variaveis_x([])
        self.txt_equacao.setPlainText("")
        self.txt_coeficientes.setPlainText("")
        self.txt_observacoes.setPlainText("")

    def _coletar_dados_formulario(self):
        """Valida os campos do formulário e monta a tupla (nome, tipo,
        estrato_coluna, estrato, variaveis_x, equacao, coeficientes,
        observacoes) pronta pro INSERT/UPDATE em `modelos`. Mostra o
        QMessageBox de erro e devolve None se algo for inválido — quem
        chama só precisa checar `is None`. Compartilhado por Salvar e
        Adicionar (quando o Nome já está preenchido) pra não duplicar
        validação."""
        nome = self.entry_nome.text().strip()
        tipo = self.combo_tipo.currentText().strip()
        if not nome or not tipo:
            QMessageBox.warning(self, "Modelos", "Nome e Tipo são obrigatórios.")
            return None

        if self.checkbox_estrato_todos.isChecked():
            estrato_coluna = None
            estrato_valor = None
        else:
            estrato_coluna = self.combo_estrato_coluna.currentText().strip()
            estrato_valor = self.combo_estrato_valor.currentText().strip()
            if not estrato_coluna or not estrato_valor:
                QMessageBox.warning(
                    self, "Modelos", "Marque \"Todos\" ou escolha a coluna e o valor do estrato.")
                return None

        coeficientes, erro = self._analisar_coeficientes(self.txt_coeficientes.toPlainText())
        if erro:
            QMessageBox.warning(self, "Modelos", erro)
            return None

        variaveis_x = [self.lista_variaveis_x.item(i).text() for i in range(self.lista_variaveis_x.count())]

        return (
            nome,
            tipo,
            estrato_coluna,
            estrato_valor,
            json.dumps(variaveis_x, ensure_ascii=False) if variaveis_x else None,
            self.txt_equacao.toPlainText().strip() or None,
            json.dumps(coeficientes, ensure_ascii=False),
            self.txt_observacoes.toPlainText().strip() or None,
        )

    def salvar_registro(self):
        """Atualiza o modelo selecionado (ou cria um, se nada estiver
        selecionado) — se um modelo já estava carregado, o Nome editado
        só renomeia esse mesmo registro."""
        dados = self._coletar_dados_formulario()
        if dados is None:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Modelos", str(e))
            return
        try:
            if self.registro_atual_id is None:
                cursor = conn.execute(
                    "INSERT INTO modelos "
                    "(nome, tipo, estrato_coluna, estrato, variaveis_x, equacao, coeficientes, observacoes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", dados
                )
                self.registro_atual_id = cursor.lastrowid
            else:
                conn.execute(
                    "UPDATE modelos SET nome=?, tipo=?, estrato_coluna=?, estrato=?, variaveis_x=?, "
                    "equacao=?, coeficientes=?, observacoes=? WHERE id=?",
                    dados + (self.registro_atual_id,)
                )
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        self.recarregar_lista()
        self.tabela.selecionar_id(self.registro_atual_id)

    def excluir_registro(self):
        """Exclui todos os modelos selecionados na lista à esquerda — a
        seleção permite Ctrl/Shift-clique pra selecionar vários de uma
        vez, então Excluir precisa apagar todos os selecionados, não só
        o último carregado no formulário."""
        selecionados = [s for s in self.tabela.selecionados() if s != IID_RESUMO]
        if not selecionados:
            return

        if len(selecionados) == 1:
            pergunta = "Excluir este modelo?"
        else:
            pergunta = f"Excluir os {len(selecionados)} modelos selecionados?"
        resposta = QMessageBox.question(
            self, "Modelos", pergunta,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if resposta != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Modelos", str(e))
            return
        try:
            ids = [int(s) for s in selecionados]
            marcadores = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM modelos WHERE id IN ({marcadores})", ids)
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self.novo_registro()
        self.recarregar_lista()

    @staticmethod
    def _analisar_coeficientes(texto):
        """Aceita tanto um par por linha ('nome = valor', formato do
        formulário) quanto vários pares separados por espaço numa linha só
        ('a=1.07 b=-0.07', formato usado na coluna Coeficientes de planilhas
        importadas) — normaliza removendo os espaços em volta de "=" e
        depois tokeniza por qualquer espaço em branco (inclusive quebra de
        linha), então os dois formatos viram a mesma lista de tokens."""
        coeficientes = {}
        texto_normalizado = re.sub(r"\s*=\s*", "=", texto)
        for token in texto_normalizado.split():
            if "=" not in token:
                return None, f"Trecho de coeficiente inválido (esperado 'nome=valor'): '{token}'"
            nome, valor = token.split("=", 1)
            nome = nome.strip()
            valor = valor.strip()
            try:
                valor_num = float(valor.replace(",", "."))
            except ValueError:
                return None, f"Valor não numérico no coeficiente '{nome}': '{valor}'"
            coeficientes[nome] = valor_num
        return coeficientes, None


def _rotulo_estrato(estrato_coluna, estrato_valor):
    if not estrato_coluna:
        return ROTULO_TODOS
    return f"{estrato_coluna} = {estrato_valor}"
