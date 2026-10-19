# -*- coding: utf-8 -*-
"""Navegação agrupada por área, com rolagem e configurações fixas.

O trilho de recolher fica fora da barra e permanece acessível quando a
navegação está oculta. As chaves e os callbacks das telas são preservados.
"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..theme import icones
from ..theme import qss as tema_qss
from ..theme import tokens

_LARGURA_LOGO_CABECALHO = 174

LARGURA_CARTAO = 244
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

    def __init__(self, itens_navegacao, ao_selecionar_tela, ao_novo_projeto, ao_abrir_projeto,
                 parent=None, ao_buscar_tela=None):
        super().__init__(parent)
        self.setObjectName("BarraLateral")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(LARGURA_CARTAO)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 24, 12, 16)
        layout.setSpacing(12)
        cor_icone = tokens.COR_BARRA_LATERAL['texto']
        logo = QLabel()
        if tokens.CAMINHO_LOGO_BARRA_LATERAL.exists():
            logo.setPixmap(QPixmap(str(tokens.CAMINHO_LOGO_BARRA_LATERAL)).scaledToWidth(
                _LARGURA_LOGO_CABECALHO, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText(tokens.NOME_APP)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        subtitulo = QLabel('PLANEJAMENTO FLORESTAL')
        subtitulo.setObjectName('BarraLateralDescricao')
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitulo)
        projeto_layout = QHBoxLayout()
        projeto_layout.setSpacing(8)
        self.botao_novo_projeto = QPushButton(' Novo projeto')
        self.botao_abrir_projeto = QPushButton(' Abrir')
        self._botoes_projeto = (self.botao_novo_projeto, self.botao_abrir_projeto)
        for botao, icone_nome, callback in (
                (self.botao_novo_projeto, 'novo_projeto', ao_novo_projeto),
                (self.botao_abrir_projeto, 'abrir_projeto', ao_abrir_projeto)):
            botao.setObjectName('BarraLateralProjeto')
            botao.setIconSize(QSize(16, 16))
            icones.aplicar_icone(botao, icone_nome, cor=cor_icone)
            botao.clicked.connect(callback)
            projeto_layout.addWidget(botao)
        layout.addLayout(projeto_layout)
        self.botao_buscar = QPushButton(' Buscar tela     Ctrl+K')
        self.botao_buscar.setObjectName('BarraLateralProjeto')
        icones.aplicar_icone(self.botao_buscar, 'buscar', cor=cor_icone)
        self.botao_buscar.setEnabled(ao_buscar_tela is not None)
        if ao_buscar_tela is not None:
            self.botao_buscar.clicked.connect(ao_buscar_tela)
        self._busca_disponivel = ao_buscar_tela is not None
        layout.addWidget(self.botao_buscar)
        self._scroll_nav = QScrollArea()
        self._scroll_nav.setObjectName('NavegacaoRolavel')
        self._scroll_nav.setWidgetResizable(True)
        self._scroll_nav.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        conteudo = QWidget()
        conteudo.setObjectName('NavegacaoConteudo')
        nav = QVBoxLayout(conteudo)
        nav.setContentsMargins(0, 0, 3, 0)
        nav.setSpacing(3)
        self._scroll_nav.setWidget(conteudo)
        layout.addWidget(self._scroll_nav, 1)
        self._grupo_nav = QButtonGroup(self)
        self._grupo_nav.setExclusive(True)
        self._botoes_nav = {}
        itens = dict(itens_navegacao)
        if 'visao_geral' in itens:
            self._adicionar_botao_nav(nav, 'visao_geral', itens['visao_geral'], ao_selecionar_tela, cor_icone)
        grupos = [
            ('DADOS E CADASTROS', ('mapa', 'sortimentos', 'custos')),
            ('MODELAGEM', ('modelos', 'weibull_ifc', 'construtor_variaveis')),
            ('SIMULAÇÃO E DESDOBRO', ('simulacao', 'toragem', 'desdobro', 'ingressos_curvas')),
            ('PLANEJAMENTO', ('resumos_cenarios', 'planejamento')),
        ]
        usados = {'visao_geral'}
        for titulo, chaves in grupos:
            rotulo = QLabel(titulo)
            rotulo.setObjectName('BarraLateralGrupo')
            nav.addWidget(rotulo)
            for chave in chaves:
                if chave in itens:
                    self._adicionar_botao_nav(nav, chave, itens[chave], ao_selecionar_tela, cor_icone)
                    usados.add(chave)
        for chave, rotulo in itens_navegacao:
            if chave not in usados and chave != 'configuracoes':
                self._adicionar_botao_nav(nav, chave, rotulo, ao_selecionar_tela, cor_icone)
        nav.addStretch(1)
        if 'configuracoes' in itens:
            self._adicionar_botao_nav(layout, 'configuracoes', itens['configuracoes'],
                                     ao_selecionar_tela, cor_icone)

    def _adicionar_botao_nav(self, layout, chave, rotulo, callback, cor):
        nomes = {'planejamento': 'Otimização', 'construtor_variaveis': 'Construtor de variáveis',
                 'desdobro': 'Desdobro de toretes', 'resumos_cenarios': 'Resumos de cenários'}
        botao = QPushButton('  ' + nomes.get(chave, rotulo))
        botao.setToolTip(rotulo)
        botao.setObjectName('BarraLateralBotao')
        botao.setIconSize(QSize(_TAMANHO_ICONE_NAV, _TAMANHO_ICONE_NAV))
        botao.setCheckable(True)
        botao.setCursor(Qt.CursorShape.PointingHandCursor)
        icones.aplicar_icone(botao, chave, cor=cor)
        botao.clicked.connect(lambda _checked=False, c=chave: callback(c))
        layout.addWidget(botao)
        self._grupo_nav.addButton(botao)
        self._botoes_nav[chave] = botao

    def selecionar_tela(self, chave):
        for k, botao in self._botoes_nav.items():
            botao.setChecked(k == chave)
            if k == chave and self._scroll_nav.widget().isAncestorOf(botao):
                self._scroll_nav.ensureWidgetVisible(botao, 0, 12)

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
        self.botao_buscar.setEnabled(not travada and self._busca_disponivel)
        for botao in self._botoes_nav.values():
            botao.setEnabled(not travada)
