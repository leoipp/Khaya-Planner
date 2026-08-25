# -*- coding: utf-8 -*-
"""
Preferências globais do app (não confundir com a tabela `configuracoes`
do banco de cada projeto .mogno — aquela é por projeto; isto aqui é do
usuário/máquina, existe mesmo sem nenhum projeto aberto, ex: tema
claro/escuro escolhido antes de abrir qualquer .mogno).

Guardado como JSON simples em %APPDATA%/KhayaPlannerV2/preferencias.json.
Pasta separada da versão Tkinter original (KhayaPlanner) de propósito —
as duas convivem lado a lado durante o desenvolvimento da versão Qt sem
disputar o mesmo arquivo de preferências.
"""
import json
import os
from pathlib import Path

NOME_ARQUIVO = "preferencias.json"
# "transparent" (não uma cor opaca) — padrão de fábrica da borda dos nós
# no Construtor de Variáveis; QColor("transparent") é um nome válido do Qt
# (alpha=0), ver app/screens/construtor_variaveis.py:_desenhar_no.
PADRAO = {
    "tema": "light",
    "cor_borda_no": "transparent",
    "espessura_borda_no": 1.5,
}


def _caminho_preferencias():
    base = Path(os.environ.get("APPDATA", Path.home())) / "KhayaPlannerV2"
    base.mkdir(parents=True, exist_ok=True)
    return base / NOME_ARQUIVO


def carregar():
    try:
        with open(_caminho_preferencias(), "r", encoding="utf-8") as f:
            dados = json.load(f)
        return {**PADRAO, **dados}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(PADRAO)


def salvar_tema(modo):
    caminho = _caminho_preferencias()
    dados = carregar()
    dados["tema"] = modo
    # escrita atômica (arquivo .tmp + replace) — ao contrário do .mogno,
    # este arquivo não tem nenhuma lógica de sincronização própria; sem
    # isso, uma queda de energia no meio do json.dump deixaria um JSON
    # corrompido que carregar() teria que descartar silenciosamente.
    tmp = caminho.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2)
    os.replace(tmp, caminho)


def obter_tema():
    return carregar().get("tema", "light")


def salvar_cor_borda_no(cor):
    """Cor da borda dos nós no canvas do Construtor de Variáveis (ver
    app/screens/construtor_variaveis.py:_desenhar_no) — preferência de
    usuário/máquina, não do projeto, mesmo motivo de `tema` (o canvas é
    pintado via QPainter, fora do alcance de QSS)."""
    caminho = _caminho_preferencias()
    dados = carregar()
    dados["cor_borda_no"] = cor
    tmp = caminho.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2)
    os.replace(tmp, caminho)


def obter_cor_borda_no():
    return carregar().get("cor_borda_no", PADRAO["cor_borda_no"])


def salvar_espessura_borda_no(espessura):
    caminho = _caminho_preferencias()
    dados = carregar()
    dados["espessura_borda_no"] = float(espessura)
    tmp = caminho.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2)
    os.replace(tmp, caminho)


def obter_espessura_borda_no():
    return float(carregar().get("espessura_borda_no", PADRAO["espessura_borda_no"]))
