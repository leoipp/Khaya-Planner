# -*- coding: utf-8 -*-
"""
Ajuste de Weibull 2P por chave definida pelo usuário, a partir da base
IFC ByTree (base_ifc_arvore) do projeto — pra explorar visualmente como
o ajuste fica em relação aos dados reais (histograma + curva).

Diferente do ajuste automático a partir da simulação de intensidades
(weibull_fit.ajustar_a_partir_da_simulacao, chave fixa), aqui o usuário
escolhe livremente quais colunas da base formam a chave de agrupamento
e qual coluna tem os valores — por isso dá pra gerar tanto uma tabela
"por talhão" quanto "por parcela" (ou qualquer outro nível), cada uma
com sua própria chave, sem repetir lógica: as duas usam esta mesma
função, só muda a tabela de destino e a configuração.

`ajustar_grupo` (a estimação em si, por máxima verossimilhança) vem de
weibull_fit.py — é o mesmo motivo pelo qual o ajuste a partir da
simulação existe.
"""
import json
import sqlite3
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from .importador import NOME_TABELA_BASE_IFC_BYTREE
from .numerico import converter_numero
from .weibull_fit import ajustar_grupo

NOME_TABELA_ORIGEM = NOME_TABELA_BASE_IFC_BYTREE

TABELA_TALHAO = "weibull_ifc_talhao"
TABELA_PLOT = "weibull_ifc_plot"

TABELA_METADADOS = "weibull_ifc_metadados"

COLUNAS_FIXAS = [
    ("n_original", "INTEGER"),
    ("n_valido", "INTEGER"),
    ("n_removido", "INTEGER"),
    ("n_nao_positivo", "INTEGER"),
    ("forma", "REAL"),
    ("escala", "REAL"),
    ("status", "TEXT"),
    ("mensagem", "TEXT"),
]

_NOMES_FIXOS_RESERVADOS = {nome for nome, _ in COLUNAS_FIXAS} | {"id"}


def colunas_base_arvore(conn: sqlite3.Connection) -> List[str]:
    """Colunas disponíveis em base_ifc_arvore (sem o id interno). Levanta
    sqlite3.OperationalError se a tabela ainda não existir (base ainda
    não importada)."""
    cursor = conn.execute(f'SELECT * FROM "{NOME_TABELA_ORIGEM}" LIMIT 0')
    return [d[0] for d in cursor.description if d[0] != "id"]


def _nomes_coluna_chave_destino(colunas_chave: List[str]) -> List[str]:
    """Nomes de coluna pra guardar a chave na tabela de destino. As
    colunas de base_ifc_arvore já são identificadores SQL válidos (o
    importador normaliza isso na importação) — só precisa evitar colidir
    com uma coluna fixa (ex: usuário escolhe uma coluna chamada
    "status") ou com outra coluna-chave repetida."""
    usados = set(_NOMES_FIXOS_RESERVADOS)
    nomes = []
    for coluna in colunas_chave:
        nome = coluna
        while nome.lower() in usados:
            nome = f"chave_{nome}"
        usados.add(nome.lower())
        nomes.append(nome)
    return nomes


# ==========================================================
# METADADOS (pra saber, depois, de onde vieram as colunas-chave)
# ==========================================================

def salvar_metadados(
    conn: sqlite3.Connection, nome_tabela: str, colunas_chave_origem: List[str],
    colunas_chave_destino: List[str], coluna_valores: str,
    minimo_observacoes: int, remover_nao_positivos: bool,
) -> None:
    conn.execute(
        "INSERT INTO weibull_ifc_metadados "
        "(tabela, colunas_chave_origem, colunas_chave_destino, coluna_valores, "
        "minimo_observacoes, remover_nao_positivos) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(tabela) DO UPDATE SET "
        "colunas_chave_origem=excluded.colunas_chave_origem, "
        "colunas_chave_destino=excluded.colunas_chave_destino, "
        "coluna_valores=excluded.coluna_valores, "
        "minimo_observacoes=excluded.minimo_observacoes, "
        "remover_nao_positivos=excluded.remover_nao_positivos",
        (
            nome_tabela, json.dumps(colunas_chave_origem), json.dumps(colunas_chave_destino),
            coluna_valores, minimo_observacoes, int(remover_nao_positivos),
        ),
    )


def carregar_metadados(conn: sqlite3.Connection, nome_tabela: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT colunas_chave_origem, colunas_chave_destino, coluna_valores, "
        "minimo_observacoes, remover_nao_positivos FROM weibull_ifc_metadados WHERE tabela = ?",
        (nome_tabela,),
    ).fetchone()
    if row is None:
        return None
    return {
        "colunas_chave_origem": json.loads(row[0]),
        "colunas_chave_destino": json.loads(row[1]),
        "coluna_valores": row[2],
        "minimo_observacoes": row[3],
        "remover_nao_positivos": bool(row[4]),
    }


# ==========================================================
# AJUSTE EM LOTE POR CHAVE DEFINIDA PELO USUÁRIO
# ==========================================================

def ajustar_por_chave(
    conn: sqlite3.Connection,
    nome_tabela_destino: str,
    colunas_chave: List[str],
    coluna_valores: str,
    minimo_observacoes: int = 2,
    remover_nao_positivos: bool = True,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict:
    """Ajusta uma Weibull 2P por (colunas_chave), usando coluna_valores
    como os dados, e substitui totalmente `nome_tabela_destino` pelo
    resultado — um registro por grupo, inclusive os que não deram pra
    ajustar (com forma/escala nulos e status explicando o motivo), pra
    dar pra inspecionar todo grupo visualmente mesmo quando o ajuste
    falhou."""
    if not colunas_chave:
        raise ValueError("Selecione ao menos uma coluna-chave.")

    colunas_sql = ", ".join(f'"{c}"' for c in (list(colunas_chave) + [coluna_valores]))
    df = pd.read_sql_query(f'SELECT {colunas_sql} FROM "{NOME_TABELA_ORIGEM}"', conn)
    df[coluna_valores] = converter_numero(df[coluna_valores])

    grupos = df.groupby(list(colunas_chave), sort=True, dropna=False)
    total_grupos = grupos.ngroups

    colunas_chave_destino = _nomes_coluna_chave_destino(colunas_chave)

    linhas: List[tuple] = []
    for numero, (chave, grupo) in enumerate(grupos, start=1):
        # Agrupar por uma lista de colunas (mesmo com um único elemento)
        # sempre retorna uma chave em tupla no pandas >= 2.0.
        valores_chave = chave if isinstance(chave, tuple) else (chave,)

        ajuste = ajustar_grupo(
            grupo[coluna_valores].to_numpy(),
            minimo_observacoes=minimo_observacoes,
            remover_nao_positivos=remover_nao_positivos,
        )

        linhas.append(tuple(valores_chave) + (
            ajuste["n_original"], ajuste["n_valido"], ajuste["n_removido"], ajuste["n_nao_positivo"],
            ajuste["shape"], ajuste["scale"], ajuste["status"], ajuste["mensagem"],
        ))

        if progress_callback is not None:
            progress_callback(numero, total_grupos)

    colunas_tabela = (
        [(nome, "TEXT") for nome in colunas_chave_destino] + COLUNAS_FIXAS
    )

    conn.execute(f'DROP TABLE IF EXISTS "{nome_tabela_destino}"')
    definicoes = ", ".join(f'"{nome}" {tipo}' for nome, tipo in colunas_tabela)
    conn.execute(f'CREATE TABLE "{nome_tabela_destino}" (id INTEGER PRIMARY KEY AUTOINCREMENT, {definicoes})')

    nomes_insert = ", ".join(f'"{nome}"' for nome, _ in colunas_tabela)
    marcadores = ", ".join("?" for _ in colunas_tabela)
    conn.executemany(
        f'INSERT INTO "{nome_tabela_destino}" ({nomes_insert}) VALUES ({marcadores})', linhas
    )

    salvar_metadados(
        conn, nome_tabela_destino, list(colunas_chave), colunas_chave_destino, coluna_valores,
        minimo_observacoes, remover_nao_positivos,
    )
    conn.commit()

    quantidade_ok = sum(1 for linha in linhas if linha[-2] == "OK")

    return {
        "total_grupos": total_grupos,
        "ajustados": quantidade_ok,
        "sem_ajuste": total_grupos - quantidade_ok,
    }


# ==========================================================
# DADOS REAIS DE UM GRUPO (pra desenhar o histograma no gráfico)
# ==========================================================

def buscar_valores_do_grupo(
    conn: sqlite3.Connection, nome_tabela: str, valores_chave_destino: Dict[str, object],
) -> np.ndarray:
    """Retorna os valores reais (já numéricos) de base_ifc_arvore que
    formam um grupo específico, a partir dos valores-chave lidos da
    linha selecionada na tabela de resultado (`nome_tabela`)."""
    metadados = carregar_metadados(conn, nome_tabela)
    if metadados is None:
        raise ValueError("Não há metadados salvos pra esta tabela de ajuste.")

    condicoes = []
    parametros = []
    for origem, destino in zip(metadados["colunas_chave_origem"], metadados["colunas_chave_destino"]):
        valor = valores_chave_destino.get(destino)
        if valor is None:
            condicoes.append(f'"{origem}" IS NULL')
        else:
            condicoes.append(f'"{origem}" = ?')
            parametros.append(valor)

    where = " AND ".join(condicoes) if condicoes else "1=1"
    coluna_valores = metadados["coluna_valores"]
    serie = pd.read_sql_query(
        f'SELECT "{coluna_valores}" FROM "{NOME_TABELA_ORIGEM}" WHERE {where}', conn, params=parametros,
    )[coluna_valores]

    return converter_numero(serie).dropna().to_numpy(dtype=float)
