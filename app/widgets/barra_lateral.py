# -*- coding: utf-8 -*-
"""
Barra de navegação lateral (esquerda): um cartão flutuante com cantos
arredondados (QFrame + QSS + QGraphicsDropShadowEffect, ver
app/widgets/cartao.py — mesma técnica) contendo os botões de projeto
(Novo/Abrir) e de troca de tela, mais um botão fixo fora do cartão
(BotaoAlternarBarraLateral, montado no "trilho" pelo caller — ver
app/window.py) pra recolher/reabrir a barra inteira sem perder o acesso a
ele quando o cartão está escondido.

Porte de app/widgets/barra_lateral.py (Tkinter, desenho manual em
tk.Canvas): em Qt os 3 estados visuais de cada botão (normal/hover/
selecionado) e o corpo arredondado do cartão são só QSS — nada precisa ser
redesenhado à mão em cada resize. Toda cor mora em app/theme/qss.py
(seletores `#BarraLateral`/`#BarraLateralBotao`/`#BarraLateralToggle`) —
este widget só marca `objectName`, não define cor localmente."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..theme import icones
from ..theme import qss as tema_qss
from ..theme import tokens

_LARGURA_LOGO_CABECALHO = 190

LARGURA_CARTAO = 260
_TAMANHO_ICONE_NAV = 16


class BotaoAlternarBarraLateral(QPushButton):
    """Ícone (◅ recolher / ▻ expandir, do catálogo em app/theme/icones.py)
    fora do cartão da barra lateral — sempre visível mesmo com o cartão
    recolhido, senão não teria como reabri-lo. Mostra o EFEITO do clique
    (recolher enquanto aberto, expandir enquanto recolhido), mesma
    convenção do AlternadorTema (sol/lua mostra o destino, não o estado
    atual)."""

    TAMANHO = 22

    def __init__(self, command, parent=None):
        super().__init__("", parent)
        self.setObjectName("BarraLateralToggle")
        self.setFixedSize(self.TAMANHO, self.TAMANHO)
        self.setIconSize(QSize(14, 14))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.clicked.connect(command)

        self._aberto = True
        self._atualizar_icone()

    def definir_aberto(self, aberto):
        self._aberto = aberto
        self._atualizar_icone()

    def _atualizar_icone(self):
        nome = "recolher_barra_lateral" if self._aberto else "expandir_barra_lateral"
        # O trilho tem o mesmo fundo escuro e fixo da barra lateral nos
        # dois temas. Usar a cor de primeiro plano do tema deixava o
        # ícone escuro sobre esse fundo no tema claro.
        self.setIcon(icones.icone(nome, cor=tokens.COR_BARRA_LATERAL["texto"]))


class BarraLateral(QFrame):
    """O cartão flutuante em si. `itens_navegacao` é uma lista
    [(chave, rótulo), ...] — a mesma chave usada em
    JanelaPrincipal._registrar_tela/mostrar_tela (e em
    app/theme/icones.py, pro ícone de cada item). `ao_selecionar_tela(chave)`,
    `ao_novo_projeto()` e `ao_abrir_projeto()` são callbacks repassados
    pelo chamador, sem acoplar este widget à lógica de projeto."""

    def __init__(self, itens_navegacao, ao_selecionar_tela, ao_novo_projeto, ao_abrir_projeto, parent=None):
        super().__init__(parent)
        self.setObjectName("BarraLateral")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(LARGURA_CARTAO)
        tema_qss.aplicar_sombra(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        cor_icone = tokens.COR_BARRA_LATERAL["texto"]

        # Um pouco mais de espaço acima do que embaixo — desce a logo
        # visualmente em vez de ficar colada no topo do cartão (pedido do
        # usuário; o cartão em si já não tem padding vertical próprio, ver
        # `layout.setContentsMargins` acima).
        layout.addSpacing(16)

        cabecalho = QHBoxLayout()
        cabecalho.addStretch(1)
        if tokens.CAMINHO_LOGO_BARRA_LATERAL.exists():
            # Logo por extenso (marca + "Khaya Planner", já sem precisar de
            # rótulo de texto ao lado — a palavra já está na imagem) — bem
            # mais larga que alta (lockup horizontal), por isso escalada
            # por LARGURA (altura derivada mantendo a proporção), ao
            # contrário do ícone quadrado antigo que escalava um quadrado.
            rotulo_logo = QLabel()
            pixmap = QPixmap(str(tokens.CAMINHO_LOGO_BARRA_LATERAL)).scaledToWidth(
                _LARGURA_LOGO_CABECALHO, Qt.TransformationMode.SmoothTransformation)
            rotulo_logo.setPixmap(pixmap)
            cabecalho.addWidget(rotulo_logo)
        cabecalho.addStretch(1)
        layout.addLayout(cabecalho)
        layout.addSpacing(8)

        # Estica acima do bloco Novo/Abrir projeto + navegação (o stretch
        # já existente no fim do layout faz o par) — sem isso o bloco
        # inteiro fica colado no topo, logo abaixo do cabeçalho; com os
        # dois, ele fica centralizado verticalmente no espaço sobrando
        # abaixo do cabeçalho, que continua fixo no topo (não faz parte
        # deste centro).
        layout.addStretch(1)

        self.botao_novo_projeto = QPushButton("  Novo projeto")
        self.botao_novo_projeto.setObjectName("BarraLateralBotao")
        self.botao_novo_projeto.setIconSize(QSize(_TAMANHO_ICONE_NAV, _TAMANHO_ICONE_NAV))
        icones.aplicar_icone(self.botao_novo_projeto, "novo_projeto", cor=cor_icone)
        self.botao_novo_projeto.clicked.connect(ao_novo_projeto)
        layout.addWidget(self.botao_novo_projeto)

        self.botao_abrir_projeto = QPushButton("  Abrir projeto")
        self.botao_abrir_projeto.setObjectName("BarraLateralBotao")
        self.botao_abrir_projeto.setIconSize(QSize(_TAMANHO_ICONE_NAV, _TAMANHO_ICONE_NAV))
        icones.aplicar_icone(self.botao_abrir_projeto, "abrir_projeto", cor=cor_icone)
        self.botao_abrir_projeto.clicked.connect(ao_abrir_projeto)
        layout.addWidget(self.botao_abrir_projeto)

        separador = QFrame()
        separador.setFixedHeight(1)
        tema_qss.aplicar_variante(separador, "separador")
        layout.addWidget(separador)
        layout.addSpacing(4)

        self._botoes_projeto = [self.botao_novo_projeto, self.botao_abrir_projeto]

        self._grupo_nav = QButtonGroup(self)
        self._grupo_nav.setExclusive(True)
        self._botoes_nav = {}
        for chave, rotulo in itens_navegacao:
            botao = QPushButton(f"  {rotulo}")
            botao.setObjectName("BarraLateralBotao")
            botao.setIconSize(QSize(_TAMANHO_ICONE_NAV, _TAMANHO_ICONE_NAV))
            botao.setCheckable(True)
            icones.aplicar_icone(botao, chave, cor=cor_icone)
            botao.clicked.connect(lambda _checked=False, c=chave: ao_selecionar_tela(c))
            layout.addWidget(botao)
            self._grupo_nav.addButton(botao)
            self._botoes_nav[chave] = botao

        layout.addStretch(1)

    def selecionar_tela(self, chave):
        for k, botao in self._botoes_nav.items():
            botao.setChecked(k == chave)

    def definir_estado_projeto(self, travada):
        for botao in self._botoes_projeto:
            botao.setEnabled(not travada)

    def definir_navegacao_travada(self, travada):
        """Desabilita TROCAR de tela também (além dos botões de projeto,
        já cobertos por definir_estado_projeto) — chamada junto dela por
        JanelaPrincipal.travar_navegacao. Sem isso, era possível abrir
        outra tela (ex: Configurações) enquanto uma tela de fundo
        (Simulação/Ajuste Weibull) segurava o banco de trabalho numa
        operação pesada (DROP+CREATE de tabela grande, INSERT em lote,
        ALTER TABLE) — a conexão da tela nova esbarrava nesse DDL/DML em
        andamento e estourava com "database is locked" ou "database
        schema has changed" (SQLite invalida todo prepared statement de
        TODA conexão quando o schema muda no meio de uma leitura de
        outra)."""
        for botao in self._botoes_nav.values():
            botao.setEnabled(not travada)
