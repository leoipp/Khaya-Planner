# -*- coding: utf-8 -*-
"""
Motor de tema do Khaya Planner (Qt).

Substitui o `registrar_ouvinte_tema`/lista de callbacks do app/tema.py
original por um sinal Qt de verdade (`ThemeManager.themeChanged`) — a
maioria dos widgets nem precisa se inscrever nele, porque QSS/QPalette já
cascateiam a troca de tema automaticamente (ver app/theme/qss.py, aplicado
uma vez em toda a QApplication). Só quem PINTA a própria aparência fora do
alcance da stylesheet (cartão, barra lateral, alternador de tema, e no
futuro os gráficos matplotlib embutidos) precisa escutar o sinal pra se
redesenhar.
"""
from PySide6.QtCore import QObject, Signal

from ..core import preferencias
from . import tokens


class ThemeManager(QObject):
    themeChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self._modo = "light"

    def modo_atual(self):
        return self._modo

    def aplicar(self, modo, salvar=True):
        """Troca o tema em vigor e avisa os ouvintes — chamar uma vez no
        boot do app (salvar=False, só confirmando o que já leu de
        preferencias.json) e de novo a cada clique no alternador de tema
        (salvar=True, ver widgets/alternador_tema.py)."""
        self._modo = modo
        if salvar:
            preferencias.salvar_tema(modo)
        self.themeChanged.emit(modo)

    def cor_status(self, chave):
        return tokens.PALETA_STATUS[self._modo][chave]

    def cor_grafico(self, chave):
        return tokens.PALETA_GRAFICO[self._modo][chave]

    def cor_resumo_tabela(self):
        return tokens.COR_RESUMO_TABELA[self._modo]

    def cor_tooltip(self):
        return tokens.COR_TOOLTIP[self._modo]

    def cor_canvas_construtor(self):
        """Fundo do canvas do Construtor de Variáveis — widget que pinta a
        própria aparência via QPainter, não alcançado pela stylesheet Qt."""
        return tokens.COR_CANVAS_CONSTRUTOR[self._modo]

    def cor_fundo(self):
        """Cor de fundo "casca" da UI — pra widgets que pintam a própria
        aparência (QPainter) e precisam combinar com o fundo do resto da
        tela."""
        return tokens.PALETA_UI[self._modo]["bg"]

    def cor_ui(self, chave):
        """Acesso genérico a um token de PALETA_UI (bg_widget, borda,
        accent, accent_hover, accent_fg, hover, fg, fg_muted, selecao_bg,
        ...) — pros widgets que desenham a própria "casca" (ex:
        app/widgets/barra_lateral.py)."""
        return tokens.PALETA_UI[self._modo][chave]


# Singleton — um ThemeManager só pra QApplication inteira, mesmo espírito
# do módulo `tema` original (estado global de módulo), só que agora como
# QObject pra poder emitir themeChanged de verdade.
_instancia = ThemeManager()


def obter():
    return _instancia
