# -*- coding: utf-8 -*-
"""
Janela principal: barra lateral (trilho fixo + cartão flutuante
colapsável) à esquerda, rodapé (projeto aberto + alternador de tema)
embaixo, pilha de telas (QStackedWidget) no resto. Porte de app/window.py
(Tkinter) — mesma responsabilidade (montar o chrome, gerenciar troca de
tela, orquestrar abrir/criar projeto), com o fluxo de abertura de projeto
em thread+queue+`after()`-polling do original trocado por QThread +
sinais Qt (sem polling, ver _ThreadAbrirProjeto)."""
from pathlib import Path
from importlib import import_module

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

from .core import projeto as core_projeto
from .theme import manager as tema
from .theme import qss as tema_qss
from .theme import tokens
from .widgets.alternador_tema import AlternadorTema
from .widgets.barra_lateral import BarraLateral, BotaoAlternarBarraLateral
from .widgets.dialogo_progresso import DialogoProgresso

ITENS_NAVEGACAO = [
    ("modelos", "Modelos"),
    ("sortimentos", "Sortimentos"),
    ("custos", "Custos"),
    ("weibull_ifc", "Weibull"),
    ("simulacao", "Simulação"),
    ("construtor_variaveis", "Construtor de Variáveis"),
    ("ingressos_curvas", "Ingressos e Curvas"),
    ("resumos_cenarios", "Resumos de Cenários"),
    ("configuracoes", "Configurações"),
]

# Importação e construção sob demanda. Além de evitar montar centenas de
# widgets invisíveis na abertura, adia imports pesados (matplotlib/pandas)
# até a primeira visita à funcionalidade correspondente.
FABRICAS_TELAS = {
    "modelos": ("app.screens.modelos", "TelaModelos"),
    "sortimentos": ("app.screens.sortimentos", "TelaSortimentos"),
    "custos": ("app.screens.custos", "TelaCustos"),
    "weibull_ifc": ("app.screens.weibull_ifc", "TelaWeibullIFC"),
    "simulacao": ("app.screens.simulacao", "TelaSimulacao"),
    "construtor_variaveis": ("app.screens.construtor_variaveis", "TelaConstrutorVariaveis"),
    "ingressos_curvas": ("app.screens.ingressos_curvas_distribuicao", "TelaIngressosCurvasDistribuicao"),
    "resumos_cenarios": ("app.screens.resumos_cenarios", "TelaResumosCenarios"),
    "configuracoes": ("app.screens.configuracoes", "TelaConfiguracoes"),
}

# Largura da coluna fixa à esquerda que só guarda o botão de
# recolher/reabrir a barra lateral — fica FORA do cartão flutuante de
# propósito, pra continuar clicável mesmo com o cartão escondido (senão
# não teria como reabrir).
LARGURA_TRILHO = 24


class _ThreadAbrirProjeto(QThread):
    """Decodifica o .mogno numa thread de verdade — nada aqui toca em
    widget nenhum (ver core/projeto.py:abrir_projeto). progresso/concluido/
    falhou são sinais Qt: emitidos nesta thread, entregues automaticamente
    na thread da GUI (conexão em fila, já que o receptor mora lá) — sem
    precisar de fila/`after()`-polling manual como no app original."""

    # object, não int: feito/total são bytes do .mogno (ver
    # codificacao._xor_arquivo) e projetos grandes passam de 2 GB —
    # Signal(int, int) usa o int C++ de 32 bits (máx. ~2,15 bilhões) e
    # estoura (OverflowError) assim que o arquivo passa desse tamanho.
    progresso = Signal(object, object)
    concluido = Signal(object, object)
    falhou = Signal(str)

    def __init__(self, caminho, parent=None):
        super().__init__(parent)
        self._caminho = caminho

    def run(self):
        try:
            caminho_mogno, trabalho = core_projeto.abrir_projeto(
                self._caminho,
                progress_callback=lambda feito, total: self.progresso.emit(feito, total))
            self.concluido.emit(caminho_mogno, trabalho)
        except Exception as e:
            self.falhou.emit(str(e))


class JanelaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tokens.NOME_APP)
        # 1150 (largura antiga) não sobra espaço suficiente pra barra de
        # navegação — os rótulos mais longos ("Construtor de Variáveis",
        # "Ingressos e Curvas de Distribuição") já deixavam isso apertado.
        self.resize(1300, 700)
        self.setMinimumSize(1050, 550)
        self._aplicar_icone()

        core_projeto.definir_agendador_qt()
        # Limpa cópias de trabalho órfãs de uma sessão anterior que não
        # fechou normalmente — antes de qualquer projeto ser aberto nesta
        # sessão, tudo em %TEMP%/khaya_planner_v2 é garantidamente órfão.
        core_projeto.limpar_arquivos_orfaos()

        self._thread_abertura = None
        self.telas = {}
        self._revisao_telas = {}
        self._recargas_agendadas = set()
        self._tela_atual = None
        self._barra_lateral_visivel = True

        self._montar_central()

        self.mostrar_tela("modelos")
        self._atualizar_titulo()

    def _aplicar_icone(self):
        if tokens.CAMINHO_ICONE_APP.exists():
            self.setWindowIcon(QIcon(str(tokens.CAMINHO_ICONE_APP)))

    def _montar_central(self):
        central = QWidget()
        layout_raiz = QHBoxLayout(central)
        layout_raiz.setContentsMargins(0, 0, 0, 0)
        layout_raiz.setSpacing(0)

        # Trilho: coluna fixa só com o botão de recolher/reabrir, FORA do
        # cartão da barra lateral (ver módulo widgets/barra_lateral.py) —
        # mesma cor de fundo dela (objectName + WA_StyledBackground, ver
        # QWidget#Trilho em app/theme/qss.py — QWidget não pinta fundo por
        # QSS sozinho sem esse atributo).
        self._trilho = QWidget()
        self._trilho.setObjectName("Trilho")
        self._trilho.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._trilho.setFixedWidth(LARGURA_TRILHO)
        layout_trilho = QVBoxLayout(self._trilho)
        layout_trilho.setContentsMargins(0, tokens.ESPACO_MD, 0, 0)
        self._botao_alternar_barra_lateral = BotaoAlternarBarraLateral(self._alternar_barra_lateral)
        layout_trilho.addWidget(
            self._botao_alternar_barra_lateral, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout_trilho.addStretch(1)
        layout_raiz.addWidget(self._trilho)

        self._barra_lateral = BarraLateral(
            ITENS_NAVEGACAO, ao_selecionar_tela=self.mostrar_tela,
            ao_novo_projeto=self.novo_projeto, ao_abrir_projeto=self.abrir_projeto)
        self._container_barra_lateral = QWidget()
        layout_wrap = QVBoxLayout(self._container_barra_lateral)
        layout_wrap.setContentsMargins(0, 0, tokens.ESPACO_MD, 0)
        layout_wrap.addWidget(self._barra_lateral)
        layout_raiz.addWidget(self._container_barra_lateral)

        container_conteudo = QWidget()
        layout_conteudo = QVBoxLayout(container_conteudo)
        layout_conteudo.setContentsMargins(0, 0, 0, 0)
        layout_conteudo.setSpacing(0)

        self._pilha = QStackedWidget()
        layout_conteudo.addWidget(self._pilha, 1)

        self._separador_rodape = QFrame()
        self._separador_rodape.setFixedHeight(1)
        tema_qss.aplicar_variante(self._separador_rodape, "separador")
        layout_conteudo.addWidget(self._separador_rodape)

        self._rodape = QWidget()
        layout_rodape = QHBoxLayout(self._rodape)
        layout_rodape.setContentsMargins(8, 4, 8, 4)
        self.label_projeto = QLabel("Nenhum projeto aberto")
        layout_rodape.addWidget(self.label_projeto)
        layout_rodape.addStretch(1)
        self.alternador_tema = AlternadorTema(self._alternar_tema)
        layout_rodape.addWidget(self.alternador_tema)
        layout_conteudo.addWidget(self._rodape)

        layout_raiz.addWidget(container_conteudo, 1)
        self.setCentralWidget(central)

    def _alternar_barra_lateral(self):
        """Recolhe/reabre o cartão da barra lateral (não o trilho, que
        continua fixo) — útil pra ganhar espaço horizontal nas telas mais
        cheias sem perder o acesso rápido de volta."""
        self._barra_lateral_visivel = not self._barra_lateral_visivel
        self._container_barra_lateral.setVisible(self._barra_lateral_visivel)
        self._botao_alternar_barra_lateral.definir_aberto(self._barra_lateral_visivel)

    def _alternar_tema(self):
        novo_modo = "dark" if tema.obter().modo_atual() == "light" else "light"
        tema.obter().aplicar(novo_modo)

    def definir_chrome_visivel(self, visivel):
        """Mostra/esconde a barra lateral (trilho + cartão) e o rodapé —
        usado pelo botão "Maximizar canvas" do Construtor de Variáveis, que
        quer só o canvas visível na janela. Respeita `_barra_lateral_visivel`
        ao reexibir — se o usuário tinha recolhido o cartão antes de
        maximizar o canvas, "desmaximizar" não deve reabri-lo sozinho por
        baixo."""
        self._trilho.setVisible(visivel)
        self._container_barra_lateral.setVisible(visivel and self._barra_lateral_visivel)
        self._rodape.setVisible(visivel)
        self._separador_rodape.setVisible(visivel)

    def _registrar_tela(self, chave, tela):
        self._pilha.addWidget(tela)
        self.telas[chave] = tela

    def _obter_ou_criar_tela(self, chave):
        tela = self.telas.get(chave)
        if tela is not None:
            return tela
        modulo_nome, classe_nome = FABRICAS_TELAS[chave]
        classe = getattr(import_module(modulo_nome), classe_nome)
        tela = classe()
        self._registrar_tela(chave, tela)
        # Os construtores atuais já carregam seu estado inicial. Marca a
        # revisão presente para não repetir a mesma carga logo em seguida.
        self._revisao_telas[chave] = core_projeto.revisao_dados()
        return tela

    def mostrar_tela(self, chave):
        if chave == self._tela_atual:
            return
        tela = self._obter_ou_criar_tela(chave)
        self._pilha.setCurrentWidget(tela)
        self._tela_atual = chave
        self._barra_lateral.selecionar_tela(chave)
        revisao_atual = core_projeto.revisao_dados()
        if self._revisao_telas.get(chave) != revisao_atual and chave not in self._recargas_agendadas:
            # Primeiro troca visualmente a página; a recarga começa no
            # próximo ciclo do event loop. Assim o clique responde na hora
            # e uma tela já construída pode exibir seu estado anterior
            # enquanto consulta a revisão nova, em vez de congelar a tela
            # antiga até todo o trabalho síncrono terminar.
            self._recargas_agendadas.add(chave)
            QTimer.singleShot(0, lambda c=chave, r=revisao_atual: self._recarregar_tela_agendada(c, r))

    def _recarregar_tela_agendada(self, chave, revisao_alvo):
        self._recargas_agendadas.discard(chave)
        if chave != self._tela_atual:
            return
        tela = self.telas.get(chave)
        if tela is None or self._revisao_telas.get(chave) == core_projeto.revisao_dados():
            return
        tela.recarregar_lista()
        # Se outra gravação ocorreu durante a carga, mantém a tela
        # invalidada para uma próxima visita em vez de declarar como atual
        # um conteúdo obtido no meio da alteração.
        if core_projeto.revisao_dados() == revisao_alvo:
            self._revisao_telas[chave] = revisao_alvo

    def travar_navegacao(self, travada):
        """Desabilita trocar/criar/abrir projeto E trocar de TELA enquanto
        uma tela roda uma operação em segundo plano que segura o banco do
        projeto atual. Trocar de projeto no meio dessa operação faria a
        sincronização final gravar no arquivo errado; trocar de TELA
        (ex: ir pra Configurações durante um "Gerar todos os cenários")
        abriria uma segunda conexão sqlite3 no MESMO arquivo de trabalho
        enquanto a thread de fundo ainda escreve nele — na melhor das
        hipóteses "database is locked" (o busy_timeout de 30s da nova
        conexão eventualmente estoura numa operação longa o bastante,
        ex: sincronizar_linhas_formacao reconstruindo simulacao_talhao_
        idade inteira), na pior "database schema has changed" (um DDL —
        DROP+CREATE, ALTER TABLE — no meio de uma leitura da tela nova
        invalida os prepared statements de QUALQUER conexão aberta no
        arquivo, não só a que fez o DDL). mostrar_tela (chamada só quando
        o usuário efetivamente clica um botão de navegação habilitado)
        continua sem checar nada aqui — a trava é só desabilitar o
        clique, não bloquear a troca em si."""
        self._barra_lateral.definir_estado_projeto(travada)
        self._barra_lateral.definir_navegacao_travada(travada)

    # ---------------- projeto ----------------

    def novo_projeto(self):
        caminho, _ = QFileDialog.getSaveFileName(
            self, "Criar novo projeto", "", f"Projeto Khaya Planner (*{core_projeto.EXTENSAO})")
        if not caminho:
            return
        if not caminho.endswith(core_projeto.EXTENSAO):
            caminho += core_projeto.EXTENSAO
        try:
            core_projeto.novo_projeto(caminho)
        except Exception as e:
            QMessageBox.critical(self, "Novo projeto", f"Não foi possível criar o projeto:\n{e}")
            return
        self._ao_trocar_projeto()

    def abrir_projeto(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Abrir projeto", "", f"Projeto Khaya Planner (*{core_projeto.EXTENSAO})")
        if not caminho:
            return

        # Finaliza sincronização pendente do projeto atual (se houver) na
        # hora, antes de largar a thread de fundo — decodificar um .mogno
        # grande (streamed, mas ainda O(tamanho do arquivo) de I/O+CPU;
        # pode levar dezenas de segundos a minutos) travaria a janela
        # inteira se rodasse aqui na thread principal.
        try:
            core_projeto.preparar_abertura()
        except Exception as e:
            QMessageBox.critical(self, "Abrir projeto", f"Não foi possível abrir o projeto:\n{e}")
            return

        self.travar_navegacao(True)
        dialogo = DialogoProgresso(
            "Abrindo projeto", f'Decodificando "{Path(caminho).name}"...', parent=self)

        thread = _ThreadAbrirProjeto(caminho, parent=self)
        self._thread_abertura = thread
        thread.progresso.connect(dialogo.atualizar)
        thread.concluido.connect(lambda cm, ct: self._finalizar_abertura_projeto(dialogo, cm, ct))
        thread.falhou.connect(lambda msg: self._finalizar_abertura_projeto(dialogo, None, None, erro=msg))
        thread.start()
        # Bloqueia aqui (sem travar a UI: exec() continua processando o
        # loop de eventos, inclusive os sinais entregues pela thread acima)
        # até dialogo.accept() ser chamado por _finalizar_abertura_projeto.
        dialogo.exec()

    def _finalizar_abertura_projeto(self, dialogo, caminho_mogno, caminho_trabalho, erro=None):
        self.travar_navegacao(False)
        dialogo.accept()
        self._thread_abertura = None

        if erro is not None:
            QMessageBox.critical(self, "Abrir projeto", f"Não foi possível abrir o projeto:\n{erro}")
            return

        core_projeto.confirmar_abertura(caminho_mogno, caminho_trabalho)
        self._ao_trocar_projeto()

    def _ao_trocar_projeto(self):
        self._atualizar_titulo()
        for tela in self.telas.values():
            tela.novo_registro()
        # Recarrega só a tela visível agora (leve) — as outras recarregam
        # sozinhas na próxima vez que o usuário navegar até elas
        # (mostrar_tela sempre chama recarregar_lista antes de exibir).
        if self._tela_atual is not None:
            self.telas[self._tela_atual].recarregar_lista()
            self._revisao_telas[self._tela_atual] = core_projeto.revisao_dados()

    def closeEvent(self, evento):
        core_projeto.fechar_projeto()
        super().closeEvent(evento)

    def _atualizar_titulo(self):
        nome = core_projeto.nome_projeto()
        if nome:
            self.setWindowTitle(f"{tokens.NOME_APP} — {nome}")
            self.label_projeto.setText(f"Projeto: {nome}")
            tema_qss.aplicar_status(self.label_projeto, "sucesso")
        else:
            self.setWindowTitle(f"{tokens.NOME_APP} (nenhum projeto aberto)")
            self.label_projeto.setText("Nenhum projeto aberto")
            tema_qss.aplicar_status(self.label_projeto, "neutro")
