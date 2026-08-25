# -*- coding: utf-8 -*-
"""
Tela "Ajustes de Weibull por manejo": CRUD manual + importação de
planilha + ajuste em lote a partir da Simulação de Intensidades
(parametros_weibull_manejo). Porte completo de app/screens/weibull.py
(Tkinter). Também é reaproveitada como a aba "Por Simulação" de
TelaWeibullIFC (ver app/screens/weibull_ifc.py) — mesma classe, só
embutida num QTabWidget em vez de ser a tela raiz.
"""
import pandas as pd
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from ..core import intensidades, projeto, simulacao, weibull_fit
from ..core.db import conectar, conectar_caminho
from ..core.importador import ler_planilha, listar_abas
from ..theme import icones, qss
from ..widgets.cartao import Cartao
from ..widgets.importacao_dialogs import escolher_aba, escolher_cabecalho, escolher_mapeamento_colunas
from .base import TelaBase
from .configuracoes import TelaConfiguracoes

MANEJOS = ["1º Raleio", "1º Desbaste", "2º Desbaste", "Corte Raso"]
INTENSIDADES = ["1", "10", "20", "30", "40", "50"]

# xlsxwriter em vez do padrão do pandas pra .xlsx (openpyxl) — mais rápido em
# planilhas grandes (ver app/screens/simulacao.py, mesma constante, onde foi
# medido e documentado por que NÃO usar a opção "constant_memory" do
# xlsxwriter, apesar de mais rápida ainda — corrompe dado silenciosamente).
_ENGINE_KWARGS_EXCEL = {"engine": "xlsxwriter"}


class _ThreadAjustarViaSimulacao(QThread):
    """Roda inteiramente numa thread de fundo — nada aqui toca em widget
    nenhum (ver core/weibull_fit.py:ajustar_a_partir_da_simulacao);
    progresso/resultado só via sinais Qt, entregues automaticamente na
    thread da GUI."""

    progresso = Signal(int, int)
    concluido = Signal(dict)
    falhou = Signal(object)

    def __init__(self, caminho_trabalho, truncar_esquerda, parent=None):
        super().__init__(parent)
        self._caminho_trabalho = caminho_trabalho
        self._truncar_esquerda = truncar_esquerda

    def run(self):
        try:
            conn = conectar_caminho(self._caminho_trabalho)
            try:
                resultado = weibull_fit.ajustar_a_partir_da_simulacao(
                    conn,
                    progress_callback=lambda numero, total: self.progresso.emit(numero, total),
                    truncar_esquerda=self._truncar_esquerda,
                )
            finally:
                conn.close()
            self.concluido.emit(resultado)
        except Exception as e:
            self.falhou.emit(e)


class TelaWeibull(TelaBase):
    _definir_status = staticmethod(TelaConfiguracoes._definir_status)
    _salvar_passo_min_max_intensidade = TelaConfiguracoes._salvar_passo_min_max_intensidade
    _rodar_intensidades = TelaConfiguracoes._rodar_intensidades
    _iniciar_execucao_intensidades = TelaConfiguracoes._iniciar_execucao_intensidades
    _ao_progredir_simulacao = TelaConfiguracoes._ao_progredir_simulacao
    _finalizar_execucao_intensidades = TelaConfiguracoes._finalizar_execucao_intensidades
    _atualizar_contagens_intensidades = TelaConfiguracoes._atualizar_contagens_intensidades
    _atualizar_faixa_intensidades = TelaConfiguracoes._atualizar_faixa_intensidades

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registro_atual_id = None
        self._ajustando = False
        self._thread_ajuste = None
        self._simulacao_rodando = False
        self._thread_simulacao = None

        layout_raiz = QVBoxLayout(self)
        layout_raiz.setContentsMargins(8, 8, 8, 8)
        layout_raiz.setSpacing(8)

        layout_raiz.addWidget(self._montar_card_intensidades())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._montar_lista())
        splitter.addWidget(self._montar_form())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout_raiz.addWidget(splitter)

        self.recarregar_lista()

    def _montar_card_intensidades(self):
        cartao = Cartao("Simulação de intensidades")
        cartao.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(cartao.corpo)
        layout.setSpacing(6)

        campos = QHBoxLayout()
        for rotulo, atributo in (
            ("Passo (%)", "entry_passo_intensidade"),
            ("Mínima (%)", "entry_intensidade_minima"),
            ("Máxima (%)", "entry_intensidade_maxima"),
        ):
            campos.addWidget(QLabel(rotulo))
            entrada = QLineEdit()
            entrada.setMaximumWidth(90)
            setattr(self, atributo, entrada)
            campos.addWidget(entrada)
        self.botao_rodar_intensidades = QPushButton("Rodar simulação de intensidades...")
        icones.aplicar_icone(self.botao_rodar_intensidades, "gerar")
        self.botao_rodar_intensidades.clicked.connect(self._rodar_intensidades)
        campos.addWidget(self.botao_rodar_intensidades)
        self.label_status_intensidades = QLabel("")
        campos.addWidget(self.label_status_intensidades)
        campos.addStretch(1)
        layout.addLayout(campos)

        self.checkbox_truncar_esquerda = QCheckBox(
            "Ajustar Weibull com truncagem à esquerda")
        self.checkbox_truncar_esquerda.setToolTip(
            "Usa o DAP mínimo remanescente de cada grupo como ponto de corte no ajuste "
            "Weibull por simulação; indicado quando o desbaste remove as menores árvores.")
        self.checkbox_truncar_esquerda.toggled.connect(self._salvar_truncagem_esquerda)
        layout.addWidget(self.checkbox_truncar_esquerda)

        self.progressbar_intensidades = QProgressBar()
        self.progressbar_intensidades.setVisible(False)
        layout.addWidget(self.progressbar_intensidades)
        self.label_faixa_intensidades = QLabel("")
        self.label_faixa_intensidades.setWordWrap(True)
        layout.addWidget(self.label_faixa_intensidades)

        contagens = QHBoxLayout()
        self._labels_contagem_intensidades = {}
        for rotulo, nome_tabela in (
            ("Árvore", intensidades.TABELA_DETALHAMENTO),
            ("Parcela", intensidades.TABELA_RESUMO_PARCELA),
            ("Talhão", intensidades.TABELA_RESUMO_TALHAO),
        ):
            contagens.addWidget(QLabel(f"{rotulo}:"))
            label = QLabel("—")
            self._labels_contagem_intensidades[nome_tabela] = label
            contagens.addWidget(label)
        contagens.addStretch(1)
        layout.addLayout(contagens)
        return cartao

    def _atualizar_estado_botoes(self):
        ocupada = self._simulacao_rodando or self._ajustando
        self.botao_rodar_intensidades.setEnabled(not ocupada)
        self.botao_ajustar_simulacao.setEnabled(not ocupada)
        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(ocupada)

    def _carregar_config_intensidades(self):
        if self._simulacao_rodando:
            return
        try:
            conn = conectar()
        except RuntimeError:
            for entrada in (self.entry_passo_intensidade, self.entry_intensidade_minima,
                            self.entry_intensidade_maxima):
                entrada.clear()
            self.checkbox_truncar_esquerda.blockSignals(True)
            self.checkbox_truncar_esquerda.setChecked(False)
            self.checkbox_truncar_esquerda.blockSignals(False)
            self._atualizar_contagens_intensidades()
            return
        try:
            row = conn.execute(
                "SELECT passo_intensidade, intensidade_minima, intensidade_maxima, "
                "truncar_esquerda_padrao "
                "FROM configuracoes WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row:
            for entrada, valor in zip(
                (self.entry_passo_intensidade, self.entry_intensidade_minima,
                 self.entry_intensidade_maxima), row[:3]):
                entrada.setText("" if valor is None else f"{valor:g}")
            self.checkbox_truncar_esquerda.blockSignals(True)
            self.checkbox_truncar_esquerda.setChecked(bool(row[3]))
            self.checkbox_truncar_esquerda.blockSignals(False)
        self._atualizar_contagens_intensidades()

    def _salvar_truncagem_esquerda(self, marcada):
        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            conn.execute(
                "INSERT INTO configuracoes (id, truncar_esquerda_padrao) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "truncar_esquerda_padrao=excluded.truncar_esquerda_padrao",
                (int(marcada),))
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()

    # ---------------- painel esquerdo: lista ----------------

    def _montar_lista(self):
        painel = QWidget()
        layout = QVBoxLayout(painel)
        layout.setContentsMargins(0, 0, 0, 0)

        rotulo = QLabel("Parâmetros de Weibull por manejo")
        qss.aplicar_variante(rotulo, "titulo")
        layout.addWidget(rotulo)

        barra_acoes = QHBoxLayout()
        self.botao_ajustar_simulacao = QPushButton("Ajustar com base na simulação de intensidades...")
        icones.aplicar_icone(self.botao_ajustar_simulacao, "ajustar")
        self.botao_ajustar_simulacao.clicked.connect(self.ajustar_via_simulacao)
        barra_acoes.addWidget(self.botao_ajustar_simulacao)
        self.botao_importar = QPushButton("Importar ajustes (Excel/CSV)...")
        icones.aplicar_icone(self.botao_importar, "importar")
        self.botao_importar.clicked.connect(self.importar_ajustes)
        barra_acoes.addWidget(self.botao_importar)
        barra_acoes.addStretch(1)
        botao_exportar = QPushButton("Exportar para Excel...")
        icones.aplicar_icone(botao_exportar, "exportar")
        botao_exportar.clicked.connect(self.exportar_excel)
        barra_acoes.addWidget(botao_exportar)
        layout.addLayout(barra_acoes)

        self.label_status_ajuste = QLabel("")
        layout.addWidget(self.label_status_ajuste)

        self.progressbar_ajuste = QProgressBar()
        self.progressbar_ajuste.setVisible(False)
        layout.addWidget(self.progressbar_ajuste)

        self.label_contagem = QLabel("")
        layout.addWidget(self.label_contagem)

        # Em vez de listar todo registro numa tabela — parametros_weibull_manejo
        # pode chegar a centenas de milhares de linhas depois de "Ajustar com
        # base na simulação de intensidades" (uma por talhão/etapa/combinação
        # de intensidades testada) —, uma busca em cascata (Talhão -> Manejo
        # -> Intensidade) carrega um registro existente pro formulário de
        # edição ao lado; "Exportar para Excel" acima dá acesso à tabela
        # inteira pra quem quiser conferir tudo.
        rotulo_busca = QLabel("Buscar registro pra editar")
        qss.aplicar_variante(rotulo_busca, "titulo")
        layout.addWidget(rotulo_busca)

        painel_busca = QGridLayout()
        self._colunas_busca = ("talhao", "manejo", "intensidade")
        self._combos_busca = []
        self._mapas_busca = [{}, {}, {}]
        for indice, rotulo_coluna in enumerate(("Talhão", "Manejo", "Intensidade")):
            painel_busca.addWidget(QLabel(rotulo_coluna), 0, indice * 2)
            combo = QComboBox()
            combo.textActivated.connect(lambda _texto, i=indice: self._ao_buscar_valor(i))
            painel_busca.addWidget(combo, 0, indice * 2 + 1)
            self._combos_busca.append(combo)
        layout.addLayout(painel_busca)
        layout.addStretch(1)
        return painel

    # ---------------- painel direito: formulário ----------------

    def _montar_form(self):
        painel = QWidget()
        layout = QGridLayout(painel)
        layout.setColumnStretch(1, 1)

        titulo = QLabel("Detalhes do ajuste")
        qss.aplicar_variante(titulo, "titulo")
        layout.addWidget(titulo, 0, 0, 1, 2)

        layout.addWidget(QLabel("Talhão"), 1, 0)
        self.entry_talhao = QLineEdit()
        layout.addWidget(self.entry_talhao, 1, 1)

        layout.addWidget(QLabel("Manejo"), 2, 0)
        self.combo_manejo = QComboBox()
        self.combo_manejo.addItem("")
        self.combo_manejo.addItems(MANEJOS)
        layout.addWidget(self.combo_manejo, 2, 1)

        layout.addWidget(QLabel("Intensidade (%)"), 3, 0)
        self.combo_intensidade = QComboBox()
        self.combo_intensidade.addItem("")
        self.combo_intensidade.addItems(INTENSIDADES)
        layout.addWidget(self.combo_intensidade, 3, 1)

        layout.addWidget(QLabel("Escala"), 4, 0)
        self.entry_escala = QLineEdit()
        layout.addWidget(self.entry_escala, 4, 1)

        layout.addWidget(QLabel("Forma"), 5, 0)
        self.entry_forma = QLineEdit()
        layout.addWidget(self.entry_forma, 5, 1)

        layout.addWidget(QLabel("Observações"), 6, 0, Qt.AlignmentFlag.AlignTop)
        self.txt_observacoes = QPlainTextEdit()
        self.txt_observacoes.setFixedHeight(120)
        layout.addWidget(self.txt_observacoes, 6, 1)

        botoes = QHBoxLayout()
        botao_salvar = QPushButton("Salvar")
        icones.aplicar_icone(botao_salvar, "salvar")
        botao_salvar.clicked.connect(self.salvar_registro)
        botoes.addWidget(botao_salvar)
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
        layout.addLayout(botoes, 7, 0, 1, 2)
        layout.setRowStretch(8, 1)
        return painel

    # ---------------- lógica ----------------

    def recarregar_lista(self):
        self._carregar_config_intensidades()
        try:
            conn = conectar()
        except RuntimeError:
            self.label_contagem.setText("Nenhum projeto aberto.")
            qss.aplicar_status(self.label_contagem, "neutro")
            self._limpar_busca()
            return
        try:
            total = conn.execute("SELECT COUNT(*) FROM parametros_weibull_manejo").fetchone()[0]
            self.label_contagem.setText(f"{total:,} registro(s).")
            qss.aplicar_status(self.label_contagem, "sucesso")
            self._popular_busca(conn, 0, {})
        finally:
            conn.close()

    def _limpar_busca(self):
        for combo in self._combos_busca:
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)

    def _popular_busca(self, conn, indice, filtros):
        """Preenche o combobox `indice` da busca só com os valores que de
        fato aparecem em parametros_weibull_manejo na combinação já
        escolhida nos combobox anteriores (`filtros`)."""
        coluna = self._colunas_busca[indice]
        condicoes = " AND ".join(f'"{c}" = ?' for c in filtros)
        where = f"WHERE {condicoes}" if condicoes else ""
        linhas = conn.execute(
            f'SELECT DISTINCT "{coluna}" FROM parametros_weibull_manejo {where} ORDER BY "{coluna}"',
            tuple(filtros.values()),
        ).fetchall()

        mapa = {("(vazio)" if v[0] is None else str(v[0])): v[0] for v in linhas}
        self._mapas_busca[indice] = mapa
        combo = self._combos_busca[indice]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(mapa.keys()))
        combo.blockSignals(False)

        for combo_seguinte in self._combos_busca[indice + 1:]:
            combo_seguinte.blockSignals(True)
            combo_seguinte.clear()
            combo_seguinte.blockSignals(False)

    def _ao_buscar_valor(self, indice):
        if not self._combos_busca[indice].currentText():
            return

        try:
            conn = conectar()
        except RuntimeError:
            return
        try:
            filtros = {
                self._colunas_busca[i]: self._mapas_busca[i][self._combos_busca[i].currentText()]
                for i in range(indice + 1)
            }

            if indice + 1 < len(self._colunas_busca):
                self._popular_busca(conn, indice + 1, filtros)
                return

            # Se houver mais de um registro pra essa combinação (ex: mais
            # de uma linha manual/importada com o mesmo talhão/manejo/
            # intensidade), carrega o primeiro — mesma ambiguidade que já
            # existia escolhendo por linha na Treeview antiga.
            condicoes = " AND ".join(f'"{c}" = ?' for c in filtros)
            row = conn.execute(
                "SELECT id, talhao, manejo, intensidade, escala, forma, observacoes "
                f"FROM parametros_weibull_manejo WHERE {condicoes} LIMIT 1",
                tuple(filtros.values()),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return

        self.registro_atual_id = row[0]
        self.entry_talhao.setText(row[1] or "")
        self.combo_manejo.setCurrentText(row[2] or "")
        self.combo_intensidade.setCurrentText(_formatar_numero(row[3]))
        self.entry_escala.setText(_formatar_numero(row[4]))
        self.entry_forma.setText(_formatar_numero(row[5]))
        self.txt_observacoes.setPlainText(row[6] or "")

    def novo_registro(self):
        self.registro_atual_id = None
        self._limpar_busca()
        self.entry_talhao.setText("")
        self.combo_manejo.setCurrentText("")
        self.combo_intensidade.setCurrentText("")
        self.entry_escala.setText("")
        self.entry_forma.setText("")
        self.txt_observacoes.setPlainText("")

    def salvar_registro(self):
        talhao = self.entry_talhao.text().strip()
        manejo = self.combo_manejo.currentText().strip()
        if not talhao:
            QMessageBox.warning(self, "Ajustes de Weibull", "Talhão é obrigatório.")
            return
        if not manejo:
            QMessageBox.warning(self, "Ajustes de Weibull", "Manejo é obrigatório.")
            return
        try:
            intensidade = float(self.combo_intensidade.currentText().strip().replace(",", "."))
            escala = float(self.entry_escala.text().strip().replace(",", "."))
            forma = float(self.entry_forma.text().strip().replace(",", "."))
        except ValueError:
            QMessageBox.warning(
                self, "Ajustes de Weibull", "Intensidade, Escala e Forma precisam ser números.")
            return

        dados = (
            talhao, manejo, intensidade, escala, forma,
            self.txt_observacoes.toPlainText().strip() or None,
        )

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Ajustes de Weibull", str(e))
            return
        try:
            if self.registro_atual_id is None:
                cursor = conn.execute(
                    "INSERT INTO parametros_weibull_manejo "
                    "(talhao, manejo, intensidade, escala, forma, observacoes) "
                    "VALUES (?, ?, ?, ?, ?, ?)", dados
                )
                self.registro_atual_id = cursor.lastrowid
            else:
                conn.execute(
                    "UPDATE parametros_weibull_manejo "
                    "SET talhao=?, manejo=?, intensidade=?, escala=?, forma=?, observacoes=? WHERE id=?",
                    dados + (self.registro_atual_id,)
                )
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        self.recarregar_lista()

    def exportar_excel(self):
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Ajustes de Weibull", str(e))
            return
        try:
            df = pd.read_sql_query(
                "SELECT talhao, manejo, intensidade, int_raleio, int_desbaste_1, int_desbaste_2, "
                "escala, forma, dap_med, dap_max, dap_min, ht_med, fustes_ha_removidos, "
                "vtcc_ha_removido, cv_dap, dg, observacoes "
                "FROM parametros_weibull_manejo "
                "ORDER BY talhao, manejo, intensidade, int_raleio, int_desbaste_1, int_desbaste_2",
                conn,
            )
        finally:
            conn.close()

        if df.empty:
            QMessageBox.warning(self, "Ajustes de Weibull", "Nenhum registro pra exportar ainda.")
            return

        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar Ajustes de Weibull", "", "Planilha Excel (*.xlsx)")
        if not caminho:
            return
        if not caminho.endswith(".xlsx"):
            caminho += ".xlsx"

        try:
            df.to_excel(caminho, index=False, **_ENGINE_KWARGS_EXCEL)
        except Exception as e:
            QMessageBox.critical(self, "Ajustes de Weibull", f"Não foi possível exportar:\n{e}")
            return

        QMessageBox.information(
            self, "Ajustes de Weibull", f"Exportado: {len(df):,} linha(s) em\n{caminho}")

    def excluir_registro(self):
        if self.registro_atual_id is None:
            return
        if QMessageBox.question(
            self, "Ajustes de Weibull", "Excluir este ajuste?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Ajustes de Weibull", str(e))
            return
        try:
            conn.execute("DELETE FROM parametros_weibull_manejo WHERE id=?", (self.registro_atual_id,))
            conn.commit()
        finally:
            conn.close()
        projeto.sincronizar()
        self.novo_registro()
        self.recarregar_lista()

    # ---------------- importação de planilha/CSV ----------------

    def importar_ajustes(self):
        try:
            projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, "Ajustes de Weibull", str(e))
            return

        caminho_arquivo, _ = QFileDialog.getOpenFileName(
            self, "Importar ajustes de Weibull", "",
            "Planilhas e CSV (*.xlsx *.xls *.csv);;Excel (*.xlsx *.xls);;CSV (*.csv)")
        if not caminho_arquivo:
            return

        try:
            abas = listar_abas(caminho_arquivo)
        except Exception as e:
            QMessageBox.critical(self, "Ajustes de Weibull", f"Não foi possível ler o arquivo:\n{e}")
            return

        if len(abas) == 1:
            aba_escolhida = abas[0]
        else:
            aba_escolhida = escolher_aba(self, "Escolher aba da planilha", abas)
            if aba_escolhida is None:
                return

        linha_cabecalho = escolher_cabecalho(self, "Ajustes de Weibull", caminho_arquivo, aba_escolhida)
        if linha_cabecalho is None:
            return

        try:
            df = ler_planilha(caminho_arquivo, aba=aba_escolhida, linha_cabecalho=linha_cabecalho)
        except Exception as e:
            QMessageBox.critical(self, "Ajustes de Weibull", f"Não foi possível ler o arquivo:\n{e}")
            return

        colunas_disponiveis = [str(c) for c in df.columns]
        campos_alvo = [
            ("talhao", "Talhão", True),
            ("manejo", "Manejo", True),
            ("intensidade", "Intensidade (%)", True),
            ("escala", "Escala", True),
            ("forma", "Forma", True),
            ("observacoes", "Observações", False),
        ]
        mapeamento = escolher_mapeamento_colunas(
            self, "Importar ajustes de Weibull", colunas_disponiveis, campos_alvo)
        if mapeamento is None:
            return

        registros_validos = []
        linhas_ignoradas = 0
        for _, linha in df.iterrows():
            talhao = _texto(linha.get(mapeamento["talhao"]))
            manejo = _texto(linha.get(mapeamento["manejo"]))
            intensidade = _para_float(linha.get(mapeamento["intensidade"]))
            escala = _para_float(linha.get(mapeamento["escala"]))
            forma = _para_float(linha.get(mapeamento["forma"]))
            observacoes = (
                _texto(linha.get(mapeamento["observacoes"])) if mapeamento["observacoes"] else ""
            )

            if not talhao or not manejo or intensidade is None or escala is None or forma is None:
                linhas_ignoradas += 1
                continue

            registros_validos.append((talhao, manejo, intensidade, escala, forma, observacoes or None))

        if not registros_validos:
            QMessageBox.warning(
                self, "Ajustes de Weibull",
                "Nenhuma linha válida encontrada — confira o mapeamento de colunas "
                "e o conteúdo do arquivo.")
            return

        aviso_ignoradas = (
            f"\n{linhas_ignoradas} linha(s) ignorada(s) por dado obrigatório ausente/inválido."
            if linhas_ignoradas else ""
        )

        if QMessageBox.question(
            self, "Ajustes de Weibull",
            f"{len(registros_validos)} registro(s) serão importados.{aviso_ignoradas}\n\n"
            "Isso substitui TODOS os ajustes de Weibull já cadastrados. Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = conectar()
        except RuntimeError as e:
            QMessageBox.warning(self, "Ajustes de Weibull", str(e))
            return
        try:
            conn.execute("DELETE FROM parametros_weibull_manejo")
            conn.executemany(
                "INSERT INTO parametros_weibull_manejo "
                "(talhao, manejo, intensidade, escala, forma, observacoes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                registros_validos,
            )
            conn.commit()
        finally:
            conn.close()

        projeto.sincronizar()
        self.novo_registro()
        self.recarregar_lista()
        QMessageBox.information(
            self, "Ajustes de Weibull",
            f"Importação concluída: {len(registros_validos)} registro(s) importado(s)."
            f"{aviso_ignoradas}")

    # ---------------- ajuste a partir da simulação de intensidades ----------------

    def ajustar_via_simulacao(self):
        if self._ajustando:
            return

        try:
            caminho_trabalho = projeto.caminho_trabalho()
        except RuntimeError as e:
            QMessageBox.warning(self, "Ajustes de Weibull", str(e))
            return

        if QMessageBox.question(
            self, "Ajustes de Weibull",
            "Isso ajusta uma Weibull 2P por talhão/intensidades/etapa a partir das árvores "
            "remanescentes da simulação de intensidades, e substitui TODOS os ajustes de "
            "Weibull já cadastrados (manuais ou importados). Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        # Resolve qualquer sincronização pendente ANTES de começar — essa
        # operação pode levar minutos e escreve pesado no banco de
        # trabalho; deixar uma sincronização agendada disparar no meio
        # (mesma Connection.backup(), mesmo arquivo) arrisca "database is
        # locked" se o backup demorar mais que o busy_timeout.
        projeto.finalizar_sincronizacao_pendente()

        self._ajustando = True
        self.botao_ajustar_simulacao.setEnabled(False)
        self.botao_importar.setEnabled(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(True)

        self.label_status_ajuste.setText("Lendo a simulação de intensidades...")
        self.progressbar_ajuste.setRange(0, 0)
        self.progressbar_ajuste.setVisible(True)

        # Config persistida na tela Configurações (não é mais um checkbox
        # local desta tela) — lida aqui, antes de disparar a thread de
        # fundo (ver simulacao.obter_truncar_esquerda_padrao).
        conn = conectar_caminho(caminho_trabalho)
        try:
            truncar_esquerda = simulacao.obter_truncar_esquerda_padrao(conn)
        finally:
            conn.close()

        thread = _ThreadAjustarViaSimulacao(caminho_trabalho, truncar_esquerda, parent=self)
        self._thread_ajuste = thread
        thread.progresso.connect(self._ao_progredir_ajuste)
        thread.concluido.connect(self._finalizar_ajuste)
        thread.falhou.connect(lambda erro: self._finalizar_ajuste(erro=erro))
        thread.start()

    def _ao_progredir_ajuste(self, numero, total):
        self.label_status_ajuste.setText(f"Ajustando grupo {numero:,}/{total:,}...")
        self.progressbar_ajuste.setRange(0, total)
        self.progressbar_ajuste.setValue(numero)

    def _finalizar_ajuste(self, resultado=None, erro=None):
        self._ajustando = False
        self._thread_ajuste = None
        self.botao_ajustar_simulacao.setEnabled(True)
        self.botao_importar.setEnabled(True)
        self.progressbar_ajuste.setVisible(False)

        janela = self.window()
        if hasattr(janela, "travar_navegacao"):
            janela.travar_navegacao(False)

        if erro is not None:
            self.label_status_ajuste.setText("")
            if isinstance(erro, ValueError):
                QMessageBox.warning(self, "Ajustes de Weibull", str(erro))
            else:
                QMessageBox.critical(self, "Ajustes de Weibull", f"Falha ao ajustar a Weibull:\n{erro}")
            return

        projeto.sincronizar()
        self.novo_registro()
        self.recarregar_lista()
        self.label_status_ajuste.setText(f"{resultado['ajustados']:,} grupo(s) ajustado(s).")

        aviso_falhas = ""
        if resultado["sem_ajuste"]:
            exemplos = "\n".join(
                f"- {f['talhao']} / {f['etapa']}: {f['status']} ({f['mensagem']})"
                for f in resultado["falhas"][:5]
            )
            aviso_falhas = (
                f"\n\n{resultado['sem_ajuste']:,} grupo(s) sem ajuste:\n{exemplos}"
                + ("\n..." if resultado["sem_ajuste"] > 5 else "")
            )

        QMessageBox.information(
            self, "Ajustes de Weibull",
            f"Ajuste concluído.\n\n"
            f"Grupos avaliados: {resultado['total_grupos']:,}\n"
            f"Ajustados com sucesso: {resultado['ajustados']:,}\n"
            f"Sem ajuste: {resultado['sem_ajuste']:,}"
            f"{aviso_falhas}")


def _texto(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    return "" if texto.lower() == "nan" else texto


def _para_float(valor):
    texto = _texto(valor)
    if not texto:
        return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


def _formatar_numero(valor):
    if valor is None:
        return ""
    if float(valor).is_integer():
        return str(int(valor))
    return str(valor)
