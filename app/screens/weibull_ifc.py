# -*- coding: utf-8 -*-
"""
Tela única de Weibull, com três abas — todas as formas de obter
forma/escala Weibull que o app tem hoje, num lugar só:

- "Por Talhão" / "Por Parcela (Plot)": ajuste por chave livre a partir
  da base IFC ByTree, cada uma com sua própria chave/coluna de valores
  definida pelo usuário. Ao selecionar um grupo ajustado, mostra um
  histograma dos dados reais desse grupo sobreposto pela curva da
  Weibull ajustada. A lógica de ajuste fica em core/weibull_ifc.py; esta
  tela só cuida da UI. As duas abas são a mesma classe de painel
  (PainelAjusteWeibullIFC) reaproveitada duas vezes — o que muda entre
  elas é só a tabela de destino e o título.
- "Por Simulação": CRUD manual + importação de planilha + ajuste em
  lote a partir da Simulação de Intensidades (parametros_weibull_manejo)
  — é a tela TelaWeibull (app/screens/weibull.py) de sempre, só
  reposicionada como aba em vez de tela própria.

Porte completo de app/screens/weibull_ifc.py (Tkinter).
"""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..core import projeto, weibull_ifc
from ..core.db import conectar, conectar_caminho
from ..theme import icones, qss
from ..widgets.grafico_weibull import GraficoAjusteWeibull
from ..widgets.importacao_dialogs import escolher_configuracao_ajuste_weibull
from .base import TelaBase
from .weibull import TelaWeibull


def _limpar_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class _ThreadAjustarPorChave(QThread):
    """Roda inteiramente numa thread de fundo — nada aqui toca em widget
    nenhum (ver core/weibull_ifc.py:ajustar_por_chave); progresso/
    resultado só via sinais Qt, entregues automaticamente na thread da
    GUI."""

    progresso = Signal(int, int)
    concluido = Signal(dict)
    falhou = Signal(object)

    def __init__(
        self, caminho_trabalho, nome_tabela, colunas_chave, coluna_valores,
        minimo_observacoes, remover_nao_positivos, parent=None,
    ):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho
        self._nome_tabela = nome_tabela
        self._colunas_chave = colunas_chave
        self._coluna_valores = coluna_valores
        self._minimo_observacoes = minimo_observacoes
        self._remover_nao_positivos = remover_nao_positivos

    def run(self):
        try:
            conn = conectar_caminho(self._caminho_trabalho)
            try:
                resultado = weibull_ifc.ajustar_por_chave(
                    conn, self._nome_tabela, self._colunas_chave, self._coluna_valores,
                    minimo_observacoes=self._minimo_observacoes,
                    remover_nao_positivos=self._remover_nao_positivos,
                    progress_callback=lambda numero, total: self.progresso.emit(numero, total),
                )
            finally:
                conn.close()
            self.concluido.emit(resultado)
        except Exception as e:
            self.falhou.emit(e)


class PainelAjusteWeibullIFC(QWidget):
    def __init__(self, nome_tabela, titulo, parent=None):
        super().__init__(parent)
        self.nome_tabela = nome_tabela
        self.titulo = titulo
        self._ajustando = False
        self._thread_ajuste = None
        self._colunas_chave_atual = []
        self._combos_chave = []
        self._mapas_chave = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        barra = QHBoxLayout()
        self.botao_ajustar = QPushButton("Configurar e ajustar...")
        icones.aplicar_icone(self.botao_ajustar, "ajustar")
        self.botao_ajustar.clicked.connect(self.ajustar)
        barra.addWidget(self.botao_ajustar)

        self.label_status = QLabel("")
        barra.addWidget(self.label_status)

        self.progressbar = QProgressBar()
        self.progressbar.setVisible(False)
        barra.addWidget(self.progressbar, 1)
        layout.addLayout(barra)

        # Em vez de listar todo grupo ajustado numa tabela (uma tabela
        # "Por Talhão"/"Por Parcela" pode ter centenas de milhares de
        # linhas), um combobox por coluna-chave, em cascata: escolher um
        # valor popula o próximo combobox só com as combinações que de
        # fato existem, e ao escolher o último, busca o grupo exato e
        # desenha o gráfico.
        self.painel_selecao = QGridLayout()
        layout.addLayout(self.painel_selecao)

        self.grafico = GraficoAjusteWeibull()
        layout.addWidget(self.grafico, 1)

        self.recarregar_lista()

    def novo_registro(self):
        # este painel não tem formulário de edição manual — existe só pra
        # manter a mesma interface das outras telas (chamada ao trocar de
        # projeto, antes de recarregar_lista popular com os dados reais)
        pass

    # ---------------- seleção de grupo por coluna-chave ----------------

    def recarregar_lista(self):
        if self._ajustando:
            return

        try:
            conn = conectar()
        except RuntimeError:
            self.label_status.setText("Nenhum projeto aberto.")
            qss.aplicar_status(self.label_status, "neutro")
            self._definir_colunas_chave([])
            self.grafico.mostrar_mensagem("Selecione um valor pra cada coluna-chave pra ver o gráfico.")
            return

        try:
            existe = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (self.nome_tabela,)
            ).fetchone()
            if existe is None:
                self.label_status.setText("Nenhum ajuste executado ainda.")
                qss.aplicar_status(self.label_status, "neutro")
                self._definir_colunas_chave([])
                return

            metadados = weibull_ifc.carregar_metadados(conn, self.nome_tabela)
            colunas_chave = metadados["colunas_chave_destino"] if metadados else []
            self._definir_colunas_chave(colunas_chave)

            total = conn.execute(f'SELECT COUNT(*) FROM "{self.nome_tabela}"').fetchone()[0]
            self.label_status.setText(f"{total:,} grupo(s).")
            qss.aplicar_status(self.label_status, "sucesso")

            if colunas_chave:
                self._popular_combo(conn, 0, {})
        finally:
            conn.close()

        self.grafico.mostrar_mensagem("Selecione um valor pra cada coluna-chave pra ver o gráfico.")

    def _definir_colunas_chave(self, colunas_chave):
        """Recria os comboboxes de seleção só quando o conjunto de
        colunas-chave muda de verdade (ex: um novo "Configurar e
        ajustar..." com colunas diferentes) — reajustar sempre que
        `recarregar_lista` roda (troca de aba, por exemplo) destruiria a
        seleção do usuário à toa."""
        if colunas_chave == self._colunas_chave_atual:
            for combo in self._combos_chave:
                combo.blockSignals(True)
                combo.clear()
                combo.blockSignals(False)
            self._mapas_chave = [{} for _ in self._colunas_chave_atual]
            return

        _limpar_layout(self.painel_selecao)
        self._colunas_chave_atual = list(colunas_chave)
        self._combos_chave = []
        self._mapas_chave = [{} for _ in colunas_chave]

        for indice, coluna in enumerate(colunas_chave):
            self.painel_selecao.addWidget(QLabel(coluna), 0, indice * 2)
            combo = QComboBox()
            combo.textActivated.connect(lambda _texto, i=indice: self._ao_selecionar_valor(i))
            self.painel_selecao.addWidget(combo, 0, indice * 2 + 1)
            self._combos_chave.append(combo)

    def _popular_combo(self, conn, indice, filtros):
        """Preenche o combobox `indice` só com os valores que de fato
        aparecem em `nome_tabela` na combinação já escolhida nos
        combobox anteriores (`filtros`) — evita oferecer uma combinação
        que não existe."""
        coluna = self._colunas_chave_atual[indice]
        condicoes = " AND ".join(f'"{c}" = ?' for c in filtros)
        where = f"WHERE {condicoes}" if condicoes else ""
        linhas = conn.execute(
            f'SELECT DISTINCT "{coluna}" FROM "{self.nome_tabela}" {where} ORDER BY "{coluna}"',
            tuple(filtros.values()),
        ).fetchall()

        mapa = {("(vazio)" if v[0] is None else str(v[0])): v[0] for v in linhas}
        self._mapas_chave[indice] = mapa
        combo = self._combos_chave[indice]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(mapa.keys()))
        combo.blockSignals(False)

        for combo_seguinte in self._combos_chave[indice + 1:]:
            combo_seguinte.blockSignals(True)
            combo_seguinte.clear()
            combo_seguinte.blockSignals(False)

    def _ao_selecionar_valor(self, indice):
        if not self._combos_chave[indice].currentText():
            return

        try:
            conn = conectar()
        except RuntimeError:
            return

        try:
            filtros = {
                self._colunas_chave_atual[i]: self._mapas_chave[i][self._combos_chave[i].currentText()]
                for i in range(indice + 1)
            }

            if indice + 1 < len(self._colunas_chave_atual):
                self._popular_combo(conn, indice + 1, filtros)
                self.grafico.mostrar_mensagem("Selecione um valor pra cada coluna-chave pra ver o gráfico.")
                return

            condicoes = " AND ".join(f'"{c}" = ?' for c in filtros)
            cursor = conn.execute(
                f'SELECT * FROM "{self.nome_tabela}" WHERE {condicoes}', tuple(filtros.values())
            )
            linha = cursor.fetchone()
            if linha is None:
                self.grafico.mostrar_mensagem("Nenhum grupo encontrado pra essa combinação.")
                return
            nomes = [d[0] for d in cursor.description]
            registro = dict(zip(nomes, linha))

            valores_reais = weibull_ifc.buscar_valores_do_grupo(conn, self.nome_tabela, filtros)
        except Exception as e:
            QMessageBox.critical(self, self.titulo, f"Não foi possível carregar os dados do grupo:\n{e}")
            return
        finally:
            conn.close()

        titulo_grafico = " / ".join(f"{c}={v}" for c, v in filtros.items())
        self.grafico.atualizar(
            valores_reais, shape=registro.get("forma"), scale=registro.get("escala"), titulo=titulo_grafico
        )

    # ---------------- ação principal: configurar e ajustar ----------------

    def ajustar(self):
        if self._ajustando:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, self.titulo, str(e))
            return

        try:
            try:
                colunas_disponiveis = weibull_ifc.colunas_base_arvore(conn)
            except Exception:
                QMessageBox.warning(
                    self, self.titulo,
                    "Nenhuma base IFC ByTree importada ainda.\n\n"
                    "Importe a base em Configurações antes de ajustar.")
                return

            configuracao_previa = weibull_ifc.carregar_metadados(conn, self.nome_tabela)
            configuracao = escolher_configuracao_ajuste_weibull(
                self, f"{self.titulo} — configurar ajuste", colunas_disponiveis, configuracao_previa)
            if configuracao is None:
                return

            if QMessageBox.question(
                self, self.titulo,
                f"Isso substitui o resultado anterior de \"{self.titulo}\", se houver. Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        finally:
            conn.close()

        caminho_trabalho = projeto.caminho_trabalho()
        self._iniciar_execucao(caminho_trabalho, configuracao)

    def _iniciar_execucao(self, caminho_trabalho, configuracao):
        # Mesmo motivo de weibull.py:_ao_clicar_ajustar_simulacao — este
        # ajuste também escreve pesado no banco de trabalho.
        projeto.finalizar_sincronizacao_pendente()

        self._ajustando = True
        self.botao_ajustar.setEnabled(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(True)

        self.label_status.setText("Ajustando...")
        qss.aplicar_status(self.label_status, "neutro")
        self.progressbar.setRange(0, 0)
        self.progressbar.setVisible(True)

        thread = _ThreadAjustarPorChave(
            caminho_trabalho, self.nome_tabela, configuracao["colunas_chave"],
            configuracao["coluna_valores"], configuracao["minimo_observacoes"],
            configuracao["remover_nao_positivos"], parent=self)
        self._thread_ajuste = thread
        thread.progresso.connect(self._ao_progredir)
        thread.concluido.connect(self._finalizar)
        thread.falhou.connect(lambda erro: self._finalizar(erro=erro))
        thread.start()

    def _ao_progredir(self, numero, total):
        self.label_status.setText(f"Ajustando grupo {numero:,}/{total:,}...")
        self.progressbar.setRange(0, total)
        self.progressbar.setValue(numero)

    def _finalizar(self, resultado=None, erro=None):
        self._ajustando = False
        self._thread_ajuste = None
        self.botao_ajustar.setEnabled(True)
        self.progressbar.setVisible(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(False)

        if erro is not None:
            self.label_status.setText("")
            if isinstance(erro, ValueError):
                QMessageBox.warning(self, self.titulo, str(erro))
            else:
                QMessageBox.critical(self, self.titulo, f"Falha ao ajustar:\n{erro}")
            return

        projeto.sincronizar()
        self.recarregar_lista()

        QMessageBox.information(
            self, self.titulo,
            "Ajuste concluído.\n\n"
            f"Grupos: {resultado['total_grupos']:,}\n"
            f"Ajustados com sucesso: {resultado['ajustados']:,}\n"
            f"Sem ajuste: {resultado['sem_ajuste']:,}")


class TelaWeibullIFC(TelaBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        abas = QTabWidget()
        layout.addWidget(abas)

        self.painel_talhao = PainelAjusteWeibullIFC(weibull_ifc.TABELA_TALHAO, "Weibull por Talhão")
        self.painel_plot = PainelAjusteWeibullIFC(weibull_ifc.TABELA_PLOT, "Weibull por Parcela")
        self.painel_simulacao = TelaWeibull()

        abas.addTab(self.painel_talhao, "Por Talhão")
        abas.addTab(self.painel_plot, "Por Parcela (Plot)")
        abas.addTab(self.painel_simulacao, "Por Simulação")

    def novo_registro(self):
        self.painel_talhao.novo_registro()
        self.painel_plot.novo_registro()
        self.painel_simulacao.novo_registro()

    def recarregar_lista(self):
        self.painel_talhao.recarregar_lista()
        self.painel_plot.recarregar_lista()
        self.painel_simulacao.recarregar_lista()
