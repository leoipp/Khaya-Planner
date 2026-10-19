# -*- coding: utf-8 -*-
"""Tela dedicada aos custos de formação florestal e de colheita."""

from PySide6.QtWidgets import QTabWidget, QFrame, QScrollArea, QVBoxLayout, QWidget

from ..theme import tokens
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from .base import TelaBase
from .configuracoes import TelaConfiguracoes


class TelaCustos(TelaBase):
    """Agrupa os dois cadastros de custo que antes ficavam em Configurações.

    Os métodos de CRUD são reaproveitados da implementação já consolidada
    para manter validações, persistência e produtividade por classe com o
    mesmo comportamento.
    """

    _campo_empilhado = staticmethod(TelaConfiguracoes._campo_empilhado)
    _montar_secao_custo_formacao = TelaConfiguracoes._montar_secao_custo_formacao
    _montar_secao_custo_colheita = TelaConfiguracoes._montar_secao_custo_colheita
    _custo_efetivo_colheita = staticmethod(TelaConfiguracoes._custo_efetivo_colheita)
    _atualizar_botoes_produtividade = TelaConfiguracoes._atualizar_botoes_produtividade
    _ao_clicar_botao_produtividade = TelaConfiguracoes._ao_clicar_botao_produtividade
    _abrir_dialogo_produtividade = TelaConfiguracoes._abrir_dialogo_produtividade

    _carregar_custos_formacao = TelaConfiguracoes._carregar_custos_formacao
    _ao_selecionar_custo_formacao = TelaConfiguracoes._ao_selecionar_custo_formacao
    _coletar_dados_custo_formacao = TelaConfiguracoes._coletar_dados_custo_formacao
    _adicionar_custo_formacao = TelaConfiguracoes._adicionar_custo_formacao
    _salvar_custo_formacao = TelaConfiguracoes._salvar_custo_formacao
    _excluir_custo_formacao = TelaConfiguracoes._excluir_custo_formacao
    _limpar_form_custo_formacao = TelaConfiguracoes._limpar_form_custo_formacao

    _carregar_custos_colheita = TelaConfiguracoes._carregar_custos_colheita
    _ao_selecionar_custo_colheita = TelaConfiguracoes._ao_selecionar_custo_colheita
    _coletar_dados_custo_colheita = TelaConfiguracoes._coletar_dados_custo_colheita
    _adicionar_custo_colheita = TelaConfiguracoes._adicionar_custo_colheita
    _salvar_custo_colheita = TelaConfiguracoes._salvar_custo_colheita
    _excluir_custo_colheita = TelaConfiguracoes._excluir_custo_colheita
    _limpar_form_custo_colheita = TelaConfiguracoes._limpar_form_custo_colheita

    def __init__(self, parent=None):
        super().__init__(parent)
        self._custo_formacao_atual_id = None
        self._custo_colheita_atual_id = None

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(
            20, 20, 20, 20)
        raiz.setSpacing(16)
        raiz.addWidget(CabecalhoTela("Custos", "Organize os custos de formação e colheita utilizados nas análises econômicas."))

        abas = QTabWidget()
        abas.setDocumentMode(True)
        abas.tabBar().setDrawBase(False)
        raiz.addWidget(abas, 1)
        for titulo, montar in (('Formação florestal', self._adicionar_card_formacao),
                                ('Colheita', self._adicionar_card_colheita)):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            conteudo = QWidget()
            layout_conteudo = QVBoxLayout(conteudo)
            layout_conteudo.setContentsMargins(0, 12, 4, 0)
            montar(layout_conteudo)
            scroll.setWidget(conteudo)
            abas.addTab(scroll, titulo)
        self.recarregar_lista()

    def _adicionar_card_formacao(self, layout):
        cartao = Cartao("Custos de formação florestal")
        layout.addWidget(cartao, 1)
        self._montar_secao_custo_formacao(cartao.corpo)
        self.tabela_custo_formacao.setMinimumHeight(200)
        self.tabela_custo_formacao.setMaximumHeight(16777215)
        cartao.layout().setStretch(1, 1)
        cartao.layout().setStretch(2, 0)

    def _adicionar_card_colheita(self, layout):
        cartao = Cartao("Custos de colheita")
        layout.addWidget(cartao, 1)
        self._montar_secao_custo_colheita(cartao.corpo)
        self.tabela_custo_colheita.setMinimumHeight(200)
        self.tabela_custo_colheita.setMaximumHeight(16777215)
        cartao.layout().setStretch(1, 1)
        cartao.layout().setStretch(2, 0)

    def novo_registro(self):
        self._limpar_form_custo_formacao()
        self._limpar_form_custo_colheita()

    def recarregar_lista(self):
        self.novo_registro()
        self._carregar_custos_formacao()
        self._carregar_custos_colheita()
