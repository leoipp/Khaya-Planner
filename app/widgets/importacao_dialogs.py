# -*- coding: utf-8 -*-
"""
Diálogos reutilizados pelos fluxos de importação de planilha/CSV: escolher
aba, escolher linha de cabeçalho e mapear colunas da planilha pra campos de
destino fixos. Usado por Modelos (e, nas próximas fases, Base IFC ByTalhao
e Ajustes de Weibull).

Porte de app/widgets/importacao_dialogs.py (Tkinter, `tk.Toplevel` +
`wait_window()` bloqueante + um dict fechado por closure pra devolver o
valor escolhido): em Qt cada diálogo é um `QDialog` de verdade,
`dialogo.exec()` já bloqueia e devolve o código de resultado — não precisa
do truque do dict.
"""
import re
import unicodedata

import pandas as pd
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..core.importador import pre_visualizar
from .tabela import emoldurar_tabela


def escolher_aba(pai, titulo, abas, indice_padrao=0):
    """Diálogo simples de escolha de aba pra workbooks com mais de uma
    planilha — sem isso, o pandas pegaria a primeira aba do arquivo por
    padrão, que raramente é a certa. Retorna o nome escolhido ou None se
    cancelado."""
    dialogo = QDialog(pai)
    dialogo.setWindowTitle(titulo)
    layout = QVBoxLayout(dialogo)

    layout.addWidget(QLabel("Essa planilha tem várias abas. Qual delas importar?"))

    lista = QListWidget()
    lista.addItems(abas)
    lista.setCurrentRow(indice_padrao)
    layout.addWidget(lista)

    botoes = QDialogButtonBox()
    botoes.addButton("Importar", QDialogButtonBox.ButtonRole.AcceptRole)
    botoes.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
    botoes.accepted.connect(dialogo.accept)
    botoes.rejected.connect(dialogo.reject)
    layout.addWidget(botoes)

    if dialogo.exec() != QDialog.DialogCode.Accepted:
        return None
    item = lista.currentItem()
    return item.text() if item is not None else None


def escolher_cabecalho(pai, titulo, caminho_arquivo, aba):
    """Mostra as primeiras linhas cruas (sem interpretar cabeçalho) e deixa
    o usuário clicar em qual delas tem os nomes das colunas de verdade —
    sem isso, o pandas assume a linha 1, o que já causou import errado numa
    planilha com cabeçalho composto em várias linhas. Retorna o índice
    0-based escolhido, ou None se cancelado."""
    try:
        previa = pre_visualizar(caminho_arquivo, aba)
    except Exception as e:
        QMessageBox.critical(pai, titulo, f"Não foi possível pré-visualizar o arquivo:\n{e}")
        return None

    dialogo = QDialog(pai)
    dialogo.setWindowTitle("Escolher linha de cabeçalho")
    dialogo.resize(950, 400)
    layout = QVBoxLayout(dialogo)

    layout.addWidget(QLabel(
        "Clique na linha que tem os nomes das colunas de verdade "
        "(ex: talhão, manejo, escala) e depois em \"Usar esta linha\"."))

    tabela = QTableWidget(len(previa), previa.shape[1])
    tabela.setVerticalHeaderLabels([f"Linha {i + 1}" for i in range(len(previa))])
    tabela.setHorizontalHeaderLabels(["" for _ in range(previa.shape[1])])
    tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for i in range(len(previa)):
        for j in range(previa.shape[1]):
            valor = previa.iat[i, j]
            texto = "" if pd.isna(valor) else str(valor)
            tabela.setItem(i, j, QTableWidgetItem(texto))
    layout.addWidget(emoldurar_tabela(tabela))

    botoes = QDialogButtonBox()
    botoes.addButton("Usar esta linha", QDialogButtonBox.ButtonRole.AcceptRole)
    botoes.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
    botoes.accepted.connect(dialogo.accept)
    botoes.rejected.connect(dialogo.reject)
    layout.addWidget(botoes)

    if dialogo.exec() != QDialog.DialogCode.Accepted:
        return None
    linhas_selecionadas = tabela.selectionModel().selectedRows()
    return linhas_selecionadas[0].row() if linhas_selecionadas else None


def escolher_mapeamento_colunas(pai, titulo, colunas_disponiveis, campos_alvo):
    """Deixa o usuário associar colunas da planilha (`colunas_disponiveis`)
    a campos de destino fixos. `campos_alvo` é uma lista de tuplas
    (chave, rótulo, obrigatório). Retorna um dict {chave: nome_coluna} —
    campos opcionais não preenchidos vêm como None — ou None se o usuário
    cancelou ou deixou um campo obrigatório sem coluna."""
    opcoes = ["(ignorar)"] + list(colunas_disponiveis)

    dialogo = QDialog(pai)
    dialogo.setWindowTitle(titulo)
    layout = QGridLayout(dialogo)

    layout.addWidget(
        QLabel("Associe cada campo à coluna correspondente da planilha importada:"), 0, 0, 1, 2)

    combos = {}
    for i, (chave, rotulo, obrigatorio) in enumerate(campos_alvo, start=1):
        texto = f"{rotulo} *" if obrigatorio else rotulo
        layout.addWidget(QLabel(texto), i, 0)
        combo = QComboBox()
        combo.addItems(opcoes)
        sugestao = _sugerir_coluna(chave, rotulo, colunas_disponiveis)
        combo.setCurrentText(sugestao or "(ignorar)")
        layout.addWidget(combo, i, 1)
        combos[chave] = combo

    resultado = {}

    def confirmar():
        mapeamento = {}
        for chave, rotulo, obrigatorio in campos_alvo:
            valor = combos[chave].currentText()
            if valor == "(ignorar)" or not valor:
                if obrigatorio:
                    QMessageBox.warning(dialogo, titulo, f'O campo "{rotulo}" é obrigatório.')
                    return
                mapeamento[chave] = None
            else:
                mapeamento[chave] = valor
        resultado["valor"] = mapeamento
        dialogo.accept()

    botoes = QDialogButtonBox()
    botoes.addButton("Concluir", QDialogButtonBox.ButtonRole.AcceptRole)
    botoes.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
    botoes.accepted.connect(confirmar)
    botoes.rejected.connect(dialogo.reject)
    layout.addWidget(botoes, len(campos_alvo) + 1, 0, 1, 2)

    if dialogo.exec() != QDialog.DialogCode.Accepted:
        return None
    return resultado.get("valor")


def escolher_configuracao_ajuste_weibull(pai, titulo, colunas_disponiveis, configuracao_previa=None):
    """Diálogo pra configurar um ajuste de Weibull por chave livre: quais
    colunas formam a chave de agrupamento (multi-seleção) e qual coluna
    tem os valores a ajustar. `configuracao_previa`, se informada, é um
    dict no mesmo formato do retorno (ver core.weibull_ifc.carregar_metadados)
    usado pra pré-selecionar a última configuração usada nessa tabela.
    Retorna um dict {"colunas_chave": [...], "coluna_valores": ...,
    "minimo_observacoes": int, "remover_nao_positivos": bool}, ou None se
    cancelado ou se a chave/coluna de valores ficarem vazias."""
    configuracao_previa = configuracao_previa or {}

    dialogo = QDialog(pai)
    dialogo.setWindowTitle(titulo)
    dialogo.resize(420, 480)
    layout = QVBoxLayout(dialogo)

    layout.addWidget(QLabel("Colunas-chave (Ctrl/Shift-clique pra selecionar mais de uma):"))
    lista = QListWidget()
    lista.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    lista.addItems(colunas_disponiveis)
    chave_previa = set(configuracao_previa.get("colunas_chave_origem", []))
    for indice, coluna in enumerate(colunas_disponiveis):
        if coluna in chave_previa:
            lista.item(indice).setSelected(True)
    layout.addWidget(lista, 1)

    layout.addWidget(QLabel("Coluna de valores (ex: DAP):"))
    combo_valores = QComboBox()
    combo_valores.addItems(list(colunas_disponiveis))
    sugestao = configuracao_previa.get("coluna_valores") or _sugerir_coluna(
        "dap", "DAP", colunas_disponiveis)
    if sugestao:
        combo_valores.setCurrentText(sugestao)
    layout.addWidget(combo_valores)

    layout.addWidget(QLabel("Mínimo de observações por grupo:"))
    entry_minimo = QLineEdit(str(configuracao_previa.get("minimo_observacoes", 2)))
    layout.addWidget(entry_minimo)

    checkbox_remover = QCheckBox("Remover valores menores ou iguais a zero automaticamente")
    checkbox_remover.setChecked(configuracao_previa.get("remover_nao_positivos", True))
    layout.addWidget(checkbox_remover)

    resultado = {}

    def confirmar():
        indices = [lista.row(item) for item in lista.selectedItems()]
        if not indices:
            QMessageBox.warning(dialogo, titulo, "Selecione ao menos uma coluna-chave.")
            return
        if not combo_valores.currentText():
            QMessageBox.warning(dialogo, titulo, "Selecione a coluna de valores.")
            return
        try:
            minimo = int(entry_minimo.text().strip())
            if minimo < 2:
                raise ValueError
        except ValueError:
            QMessageBox.warning(dialogo, titulo, "O mínimo de observações precisa ser um inteiro >= 2.")
            return

        resultado["valor"] = {
            "colunas_chave": [colunas_disponiveis[i] for i in indices],
            "coluna_valores": combo_valores.currentText(),
            "minimo_observacoes": minimo,
            "remover_nao_positivos": checkbox_remover.isChecked(),
        }
        dialogo.accept()

    botoes = QDialogButtonBox()
    botoes.addButton("Ajustar", QDialogButtonBox.ButtonRole.AcceptRole)
    botoes.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
    botoes.accepted.connect(confirmar)
    botoes.rejected.connect(dialogo.reject)
    layout.addWidget(botoes)

    if dialogo.exec() != QDialog.DialogCode.Accepted:
        return None
    return resultado.get("valor")


def _sugerir_coluna(chave, rotulo, colunas_disponiveis):
    """Pré-seleciona a coluna da planilha cujo nome mais parece com o
    campo de destino (comparação frouxa, ignorando acentos/caixa/espaços),
    pra poupar cliques quando os nomes já batem ou quase batem."""
    alvo = _normalizar(chave)
    alvo_rotulo = _normalizar(rotulo)
    for c in colunas_disponiveis:
        normalizado = _normalizar(c)
        if normalizado == alvo or normalizado == alvo_rotulo:
            return c
    for c in colunas_disponiveis:
        normalizado = _normalizar(c)
        if alvo in normalizado or normalizado in alvo:
            return c
    return None


def _normalizar(texto):
    """Reduz a string a só letras/números minúsculos, sem acento — pra que
    'Intens (%)' e 'Escala (m)' ainda batam com 'intensidade' e 'escala'
    apesar da unidade/pontuação extra no cabeçalho da planilha."""
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", texto.lower())
