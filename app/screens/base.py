# -*- coding: utf-8 -*-
"""
Contrato mínimo que toda tela precisa cumprir pra JanelaPrincipal poder
gerenciá-la (ver app/window.py: mostrar_tela/_ao_trocar_projeto) — mesmo
contrato implícito que as telas Tkinter originais já seguiam.
"""
from PySide6.QtWidgets import QWidget


class TelaBase(QWidget):
    def recarregar_lista(self):
        """Chamado toda vez que a tela vai ser exibida (ver
        JanelaPrincipal.mostrar_tela) — recarrega dados que outra tela
        pode ter alterado enquanto esta ficava em segundo plano."""

    def novo_registro(self):
        """Chamado ao trocar de projeto (ver
        JanelaPrincipal._ao_trocar_projeto) — limpa o formulário/seleção
        atual, já que os dados do projeto anterior não fazem mais
        sentido."""
