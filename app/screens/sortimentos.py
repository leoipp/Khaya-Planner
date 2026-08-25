# -*- coding: utf-8 -*-
"""Tela dedicada aos sortimentos comerciais e dimensões das toras."""

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..core import projeto
from ..core.db import conectar
from ..theme import icones, qss, tokens
from ..widgets.cabecalho_tela import CabecalhoTela
from ..widgets.cartao import Cartao
from .base import TelaBase
from .configuracoes import TelaConfiguracoes, _formatar_numero


class TelaSortimentos(TelaBase):
    _linha_campo = staticmethod(TelaConfiguracoes._linha_campo)
    _campo_empilhado = staticmethod(TelaConfiguracoes._campo_empilhado)
    _montar_secao_sortimentos = TelaConfiguracoes._montar_secao_sortimentos
    _montar_secao_dimensoes_tora = TelaConfiguracoes._montar_secao_dimensoes_tora
    _carregar_sortimentos = TelaConfiguracoes._carregar_sortimentos
    _ao_selecionar_sortimento = TelaConfiguracoes._ao_selecionar_sortimento
    _coletar_dados_sortimento = TelaConfiguracoes._coletar_dados_sortimento
    _adicionar_sortimento = TelaConfiguracoes._adicionar_sortimento
    _salvar_sortimento = TelaConfiguracoes._salvar_sortimento
    _excluir_sortimento = TelaConfiguracoes._excluir_sortimento
    _limpar_form_sortimento = TelaConfiguracoes._limpar_form_sortimento

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sortimento_atual_id = None

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(tokens.ESPACO_SM, tokens.ESPACO_SM,
                                tokens.ESPACO_SM, tokens.ESPACO_SM)
        raiz.setSpacing(tokens.ESPACO_LG)
        raiz.addWidget(CabecalhoTela("Sortimentos"))

        cartao_sortimentos = Cartao("Sortimentos (classificação por diâmetro)")
        raiz.addWidget(cartao_sortimentos, 1)
        self._montar_secao_sortimentos(cartao_sortimentos.corpo)
        self.tabela_sortimentos.setMinimumHeight(240)
        self.tabela_sortimentos.setMaximumHeight(16777215)
        cartao_sortimentos.layout().setStretch(1, 1)
        cartao_sortimentos.layout().setStretch(2, 0)

        cartao_dimensoes = Cartao("Dimensões da tora")
        raiz.addWidget(cartao_dimensoes)
        self._montar_secao_dimensoes_tora(cartao_dimensoes.corpo)
        barra = QHBoxLayout()
        barra.addStretch(1)
        salvar = QPushButton("Salvar dimensões")
        qss.aplicar_variante(salvar, "salvar")
        icones.aplicar_icone(salvar, "salvar", cor="white")
        salvar.clicked.connect(self._salvar_dimensoes)
        barra.addWidget(salvar)
        cartao_dimensoes.layout().insertLayout(2, barra)

        self.recarregar_lista()

    def novo_registro(self):
        self._limpar_form_sortimento()
        self.entry_comprimento_tora.clear()
        self.entry_diametro_minimo_tora.clear()
        self.checkbox_usar_tabela_afilamento.setChecked(False)

    def recarregar_lista(self):
        self.novo_registro()
        self._carregar_sortimentos()
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            row = conn.execute(
                "SELECT comprimento_tora, diametro_minimo_tora, usar_tabela_afilamento "
                "FROM configuracoes WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row:
            self.entry_comprimento_tora.setText(_formatar_numero(row[0]))
            self.entry_diametro_minimo_tora.setText(_formatar_numero(row[1]))
            self.checkbox_usar_tabela_afilamento.setChecked(bool(row[2]))

    def _salvar_dimensoes(self):
        valores = []
        for rotulo, campo in (
            ("Comprimento da tora", self.entry_comprimento_tora),
            ("Diâmetro mínimo", self.entry_diametro_minimo_tora),
        ):
            texto = campo.text().strip()
            if not texto:
                valores.append(None)
                continue
            try:
                valores.append(float(texto.replace(",", ".")))
            except ValueError:
                QMessageBox.warning(self, "Sortimentos", f"{rotulo} inválido: '{texto}'.")
                return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Sortimentos", str(e))
            return
        try:
            conn.execute(
                "INSERT INTO configuracoes "
                "(id, comprimento_tora, diametro_minimo_tora, usar_tabela_afilamento) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "comprimento_tora=excluded.comprimento_tora, "
                "diametro_minimo_tora=excluded.diametro_minimo_tora, "
                "usar_tabela_afilamento=excluded.usar_tabela_afilamento",
                (valores[0], valores[1], int(self.checkbox_usar_tabela_afilamento.isChecked())))
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        QMessageBox.information(self, "Sortimentos", "Dimensões da tora salvas.")
