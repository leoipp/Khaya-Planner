# -*- coding: utf-8 -*-
"""
Simulação sequencial de manejo (raleio + 1º e 2º desbaste) sobre a base
IFC ByTree do projeto atual.

Adaptado do protótipo standalone (Intensidades.py, na raiz do repo), que
lia um Excel fixo com nomes de coluna hardcoded e exportava pra .xlsx
respeitando o limite de 1.048.576 linhas por aba. Aqui:

- a base de árvores vem da tabela `base_ifc_arvore` do projeto, que tem
  schema livre (colunas = cabeçalhos de qualquer planilha que o usuário
  tenha importado em "Base IFC ByTree") — por isso quem chama informa o
  mapeamento campo -> coluna real (ver CAMPOS_MAPEAMENTO), em vez de
  nomes de coluna fixos;
- passo/mínima/máxima de intensidade vêm da tela Configurações, não de
  constantes no topo do arquivo;
- o resultado é gravado direto nas tabelas do projeto (SQLite não tem o
  limite de linhas do Excel), então as antigas opções de liga/desliga
  pra cada saída (resumo por parcela, detalhamento, remanescentes e
  removidas por etapa) deixam de existir — sempre gera tudo;
- o resumo por talhão, que no script original era uma agregação em
  pandas de ~150 linhas, vira uma única consulta SQL (GROUP BY) sobre o
  resumo por parcela já gravado no banco.
"""
import sqlite3
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .numerico import converter_numero

NOME_TABELA_ORIGEM = "base_ifc_arvore"

TABELA_RESUMO_TALHAO = "intensidades_resumo_talhao"
TABELA_RESUMO_PARCELA = "intensidades_resumo_parcela"
TABELA_DETALHAMENTO = "intensidades_detalhamento"

ETAPAS = ["raleio", "desbaste_1", "desbaste_2"]

# (chave, rótulo exibido, obrigatório) — formato esperado por
# widgets.importacao_dialogs.escolher_mapeamento_colunas.
CAMPOS_MAPEAMENTO = [
    ("talhao", "Talhão", True),
    ("parcela", "Parcela", True),
    ("dap", "DAP", True),
    ("ab", "AB (área basal)", True),
    ("area_parcela", "Área da parcela", True),
    ("ht", "Ht (altura)", True),
    ("vtcc", "VTCC (volume total com casca)", True),
]


# ==========================================================
# SCHEMA DAS TABELAS DE RESULTADO
# ==========================================================

def _colunas_resumo_parcela() -> List[Tuple[str, str]]:
    colunas = [
        ("talhao", "TEXT"),
        ("parcela", "TEXT"),
        ("area_parcela", "REAL"),
        ("int_raleio", "REAL"),
        ("int_desbaste_1", "REAL"),
        ("int_desbaste_2", "REAL"),
        ("dap_med_original", "REAL"),
        ("ht_med_original", "REAL"),
        ("ab_med_original", "REAL"),
        ("ab_total_original", "REAL"),
        ("fustes_originais", "INTEGER"),
    ]
    for etapa in ETAPAS:
        colunas += [
            (f"dap_med_apos_{etapa}", "REAL"),
            (f"dap_max_apos_{etapa}", "REAL"),
            (f"dap_min_apos_{etapa}", "REAL"),
            (f"ht_med_apos_{etapa}", "REAL"),
            (f"ab_med_apos_{etapa}", "REAL"),
            (f"ab_total_apos_{etapa}", "REAL"),
            (f"fustes_apos_{etapa}", "INTEGER"),
            (f"ab_antes_{etapa}", "REAL"),
            (f"ab_removida_{etapa}", "REAL"),
            (f"int_real_{etapa}", "REAL"),
            (f"fustes_removidos_{etapa}", "INTEGER"),
            (f"fustes_ha_removidos_{etapa}", "REAL"),
            (f"vtcc_ha_removido_{etapa}", "REAL"),
        ]
    colunas += [
        ("ab_removida_total", "REAL"),
        ("fustes_removidos_total", "INTEGER"),
        ("fustes_ha_removidos_total", "REAL"),
        ("vtcc_ha_removido_total", "REAL"),
    ]
    return colunas


def _colunas_resumo_talhao() -> List[Tuple[str, str]]:
    colunas = [
        ("talhao", "TEXT"),
        ("int_raleio", "REAL"),
        ("int_desbaste_1", "REAL"),
        ("int_desbaste_2", "REAL"),
        ("area_total", "REAL"),
        ("parcelas", "INTEGER"),
        ("dap_med_original", "REAL"),
        ("ht_med_original", "REAL"),
        ("ab_med_original", "REAL"),
        ("ab_total_original", "REAL"),
        ("fustes_originais", "INTEGER"),
    ]
    for etapa in ETAPAS:
        colunas += [
            (f"dap_med_apos_{etapa}", "REAL"),
            (f"dap_max_apos_{etapa}", "REAL"),
            (f"dap_min_apos_{etapa}", "REAL"),
            (f"ht_med_apos_{etapa}", "REAL"),
            (f"ab_med_apos_{etapa}", "REAL"),
            (f"ab_total_apos_{etapa}", "REAL"),
            (f"fustes_apos_{etapa}", "INTEGER"),
            (f"ab_antes_{etapa}", "REAL"),
            (f"ab_removida_{etapa}", "REAL"),
            (f"int_real_{etapa}", "REAL"),
            (f"fustes_removidos_{etapa}", "INTEGER"),
            (f"fustes_ha_removidos_{etapa}", "REAL"),
            (f"vtcc_ha_removido_{etapa}", "REAL"),
        ]
    colunas += [
        ("ab_removida_total", "REAL"),
        ("fustes_removidos_total", "INTEGER"),
        ("fustes_ha_removidos_total", "REAL"),
        ("vtcc_ha_removido_total", "REAL"),
    ]
    return colunas


_COLUNAS_DETALHAMENTO = [
    ("talhao", "TEXT"),
    ("parcela", "TEXT"),
    ("area_parcela", "REAL"),
    ("arvore_id", "INTEGER"),
    ("dap", "REAL"),
    ("ab", "REAL"),
    ("int_raleio", "REAL"),
    ("int_desbaste_1", "REAL"),
    ("int_desbaste_2", "REAL"),
    ("etapa", "TEXT"),
    ("situacao", "TEXT"),
]

_COLUNAS_RESUMO_PARCELA = _colunas_resumo_parcela()
_COLUNAS_RESUMO_TALHAO = _colunas_resumo_talhao()


def _expressao_agregada_talhao(coluna: str) -> str:
    """Expressão SQL que agrega uma coluna do resumo por parcela pro
    resumo por talhão. Os nomes de coluna seguem prefixos consistentes
    (dap_med_/ab_med_/ht_med_ são médias ponderadas pela área; dap_max_/
    dap_min_ são o maior/menor valor entre as parcelas; ab_total_/fustes_
    são somas; fustes_ha_removidos_/vtcc_ha_removido_ são média simples —
    mesmo critério do script original), então dá pra decidir a agregação
    só pelo nome."""
    if coluna == "talhao":
        return "talhao"
    if coluna in ("int_raleio", "int_desbaste_1", "int_desbaste_2"):
        return coluna
    if coluna == "area_total":
        return "SUM(area_parcela)"
    if coluna == "parcelas":
        return "COUNT(*)"
    if coluna.startswith("dap_med_") or coluna.startswith("ab_med_") or coluna.startswith("ht_med_"):
        return f"SUM(COALESCE({coluna}, 0) * area_parcela) / SUM(area_parcela)"
    if coluna.startswith("dap_max_"):
        return f"MAX({coluna})"
    if coluna.startswith("dap_min_"):
        return f"MIN({coluna})"
    if coluna.startswith("int_real_"):
        etapa = coluna[len("int_real_"):]
        return f"SUM(ab_removida_{etapa}) / SUM(ab_antes_{etapa})"
    if coluna.startswith("fustes_ha_removidos_") or coluna.startswith("vtcc_ha_removido_"):
        return f"AVG({coluna})"
    # ab_total_original, fustes_originais, ab_total_apos_*, fustes_apos_*,
    # ab_antes_*, ab_removida_*, fustes_removidos_*, *_total: soma simples.
    return f"SUM({coluna})"


def _sql_criar_tabela(nome_tabela: str, colunas: List[Tuple[str, str]]) -> str:
    definicoes = ", ".join(f'"{nome}" {tipo}' for nome, tipo in colunas)
    return f'CREATE TABLE "{nome_tabela}" (id INTEGER PRIMARY KEY AUTOINCREMENT, {definicoes})'


def _sql_inserir(nome_tabela: str, colunas: List[Tuple[str, str]]) -> str:
    nomes = ", ".join(f'"{nome}"' for nome, _ in colunas)
    marcadores = ", ".join("?" for _ in colunas)
    return f'INSERT INTO "{nome_tabela}" ({nomes}) VALUES ({marcadores})'


def _sql_agregar_talhao() -> str:
    nomes = ", ".join(f'"{nome}"' for nome, _ in _COLUNAS_RESUMO_TALHAO)
    expressoes = ", ".join(_expressao_agregada_talhao(nome) for nome, _ in _COLUNAS_RESUMO_TALHAO)
    return (
        f'INSERT INTO "{TABELA_RESUMO_TALHAO}" ({nomes}) '
        f'SELECT {expressoes} FROM "{TABELA_RESUMO_PARCELA}" '
        "GROUP BY talhao, int_raleio, int_desbaste_1, int_desbaste_2"
    )


def _criar_tabelas_resultado(conn: sqlite3.Connection) -> None:
    for nome_tabela, colunas in (
        (TABELA_RESUMO_PARCELA, _COLUNAS_RESUMO_PARCELA),
        (TABELA_RESUMO_TALHAO, _COLUNAS_RESUMO_TALHAO),
        (TABELA_DETALHAMENTO, _COLUNAS_DETALHAMENTO),
    ):
        conn.execute(f'DROP TABLE IF EXISTS "{nome_tabela}"')
        conn.execute(_sql_criar_tabela(nome_tabela, colunas))


# ==========================================================
# LEITURA E VALIDAÇÃO DA BASE
# ==========================================================

def colunas_base_arvore(conn: sqlite3.Connection) -> List[str]:
    """Colunas disponíveis em base_ifc_arvore (sem o id interno). Levanta
    sqlite3.OperationalError se a tabela ainda não existir (base ainda
    não importada)."""
    cursor = conn.execute(f'SELECT * FROM "{NOME_TABELA_ORIGEM}" LIMIT 0')
    return [d[0] for d in cursor.description if d[0] != "id"]


def obter_intensidades(conn: sqlite3.Connection) -> np.ndarray:
    """Monta a lista de intensidades a testar a partir da configuração do
    projeto (passo/mínima/máxima, em %)."""
    row = conn.execute(
        "SELECT passo_intensidade, intensidade_minima, intensidade_maxima "
        "FROM configuracoes WHERE id = 1"
    ).fetchone()

    if row is None or any(v is None for v in row):
        raise ValueError(
            "Configure o passo, a intensidade mínima e a intensidade máxima "
            "na tela Configurações antes de rodar a simulação."
        )

    passo, minima, maxima = (float(v) / 100.0 for v in row)

    if passo <= 0:
        raise ValueError("O passo da intensidade precisa ser maior que zero.")
    if maxima < minima:
        raise ValueError("A intensidade máxima precisa ser maior ou igual à mínima.")

    return np.round(np.arange(minima, maxima + passo / 2, passo), 4)


def _validar_area_parcelas(df: pd.DataFrame) -> None:
    """Cada Talhão/Parcela só pode ter um valor de área de parcela."""
    quantidade_areas = (
        df.groupby(["talhao", "parcela"], dropna=False)["area_parcela"].nunique(dropna=False)
    )
    inconsistentes = quantidade_areas[quantidade_areas > 1]
    if not inconsistentes.empty:
        raise ValueError(
            "Existem parcelas com mais de uma área informada.\n\n"
            f"{inconsistentes.head(20)}"
        )


def carregar_base(conn: sqlite3.Connection, mapeamento: Dict[str, str]) -> Tuple[pd.DataFrame, int]:
    """Lê base_ifc_arvore aplicando o mapeamento campo->coluna, converte
    os campos numéricos, descarta registros inválidos e ordena as
    árvores. Retorna (base_limpa, quantidade_de_registros_descartados)."""
    colunas_sql = ", ".join(f'"{mapeamento[chave]}" AS {chave}' for chave, _, _ in CAMPOS_MAPEAMENTO)
    df = pd.read_sql_query(f'SELECT {colunas_sql} FROM "{NOME_TABELA_ORIGEM}"', conn)

    for coluna in ("dap", "ab", "area_parcela", "ht", "vtcc"):
        df[coluna] = converter_numero(df[coluna])

    quantidade_inicial = len(df)

    df = df.dropna(subset=["talhao", "parcela", "dap", "ab", "area_parcela", "ht", "vtcc"]).copy()
    df = df[
        (df["dap"] > 0) & (df["ab"] > 0) & (df["area_parcela"] > 0)
        & (df["ht"] > 0) & (df["vtcc"] > 0)
    ].copy()

    quantidade_removida = quantidade_inicial - len(df)

    if df.empty:
        raise ValueError("Nenhuma árvore válida permaneceu após a limpeza dos dados.")

    _validar_area_parcelas(df)

    df["arvore_id"] = np.arange(1, len(df) + 1)
    df = (
        df.sort_values(by=["talhao", "parcela", "dap", "arvore_id"], kind="mergesort")
        .reset_index(drop=True)
    )

    return df, quantidade_removida


# ==========================================================
# APLICAÇÃO DA INTERVENÇÃO (vetorizado — todas as combinações de
# raleio/desbaste_1/desbaste_2 de uma parcela de uma vez, em vez de um
# laço triplo em Python chamando pandas por combinação)
# ==========================================================
#
# Como cada intervenção sempre remove as árvores de menor DAP primeiro
# (prefixo do array já ordenado por dap), a árvore remanescente de
# QUALQUER combinação de intensidades é sempre um sufixo do array
# ordenado da parcela inteira — isso permite resolver os pontos de corte
# de todas as N³ combinações de uma vez com busca binária vetorizada
# (np.searchsorted) sobre um único array de AB acumulada global, em vez
# de reordenar/recomputar soma acumulada a cada uma das N³ combinações.

def _proximo_corte(ab_acumulada_global: np.ndarray, indice_anterior, intensidade, n: int):
    """Índice de corte (na parcela ordenada por dap ascendente) depois de
    remover `intensidade` da AB restante a partir de `indice_anterior` —
    mesmo critério de `searchsorted(ab_restante.cumsum(), alvo, 'right')`
    usado árvore a árvore antes, só que contra `ab_acumulada_global`
    (soma acumulada de AB de TODA a parcela, ab_acumulada_global[i] =
    soma de AB dos i primeiros elementos, tamanho n+1). `indice_anterior`
    e `intensidade` podem ser arrays (broadcastáveis entre si), pra
    resolver muitas combinações de uma vez."""
    ab_antes = ab_acumulada_global[n] - ab_acumulada_global[indice_anterior]
    alvo_absoluto = ab_acumulada_global[indice_anterior] + intensidade * ab_antes
    corte = np.searchsorted(ab_acumulada_global, alvo_absoluto, side="right") - 1
    return np.clip(corte, indice_anterior, n)


def _estatisticas_por_corte(
    ab_acumulada_global: np.ndarray, soma_dap_sufixo: np.ndarray, soma_ht_sufixo: np.ndarray,
    soma_vtcc_sufixo: np.ndarray, dap: np.ndarray,
    indice_anterior, indice_corte, ab_antes, n: int,
) -> Dict[str, np.ndarray]:
    """Estatísticas de uma etapa (remanescentes/removidas) a partir só
    dos índices de corte — remanescentes de um corte `k` são sempre
    `dap[k:n]`/`ab[k:n]` (sufixo do array ordenado), então médias/somas
    saem de arrays de soma acumulada precomputados uma vez por parcela,
    sem precisar reconstruir o sufixo a cada combinação. `ht_med` segue o
    mesmo padrão de `dap_med` (média das remanescentes); `vtcc_ha_removido`
    segue o de `fustes_ha_removidos` (soma do VTCC das árvores removidas
    NESTA etapa — o bloco [indice_anterior, k) do array ordenado — via
    soma_vtcc_sufixo[indice_anterior] - soma_vtcc_sufixo[k], já que
    soma_vtcc_sufixo[i] = soma de vtcc[i:n])."""
    k = np.minimum(indice_corte, n)
    vazio = k >= n
    divisor_seguro = np.maximum(n - k, 1)

    dap_max_valor = dap[n - 1] if n > 0 else np.nan
    dap_min_indice = np.minimum(k, max(n - 1, 0))

    ab_total_apos = ab_acumulada_global[n] - ab_acumulada_global[k]
    ab_removida = ab_acumulada_global[k] - ab_acumulada_global[indice_anterior]
    ab_antes_seguro = np.where(ab_antes > 0, ab_antes, 1.0)

    return {
        "dap_med": np.where(vazio, np.nan, soma_dap_sufixo[k] / divisor_seguro),
        "dap_max": np.where(vazio, np.nan, dap_max_valor),
        "dap_min": np.where(vazio, np.nan, dap[dap_min_indice]),
        "ht_med": np.where(vazio, np.nan, soma_ht_sufixo[k] / divisor_seguro),
        "ab_med": np.where(vazio, np.nan, ab_total_apos / divisor_seguro),
        "ab_total": ab_total_apos,
        "fustes": n - k,
        "ab_antes": np.broadcast_to(np.asarray(ab_antes, dtype=float), np.shape(k)).copy(),
        "ab_removida": ab_removida,
        "int_real": np.where(ab_antes > 0, ab_removida / ab_antes_seguro, 0.0),
        "fustes_removidos": k - indice_anterior,
        "fustes_ha_removidos": (k - indice_anterior) * 10_000.0,  # dividido pela área da parcela depois
        "vtcc_ha_removido": (
            (soma_vtcc_sufixo[indice_anterior] - soma_vtcc_sufixo[k]) * 10_000.0
        ),  # dividido pela área da parcela depois
    }


def _checar_intensidade_real(stats: Dict[str, np.ndarray], intensidade_planejada: np.ndarray, rotulo: str) -> None:
    """Mesma checagem de sanidade de antes (intensidade real não pode
    ultrapassar a planejada), agora vetorizada sobre a combinação
    inteira — só é violada por um bug no cálculo do corte, nunca por
    dado de entrada."""
    real, planejada = np.broadcast_arrays(stats["int_real"], intensidade_planejada)
    violacao = real > planejada + 1e-12
    if np.any(violacao):
        posicao = tuple(np.argwhere(violacao)[0])
        raise RuntimeError(
            f"A intensidade real ultrapassou a planejada ({rotulo}).\n"
            f"Planejada: {float(planejada[posicao]):.12f}\nReal: {float(real[posicao]):.12f}"
        )


def _lista(array: np.ndarray, forma_final, nan_para_none: bool = False) -> list:
    """Achata um array (broadcasting pra `forma_final` se precisar) em
    lista de tipos Python nativos — `.tolist()` converte int64/float64 do
    numpy pra int/float puros, que é o que sqlite3 espera pro bind de
    parâmetro (numpy.int64 não é aceito diretamente)."""
    valores = np.broadcast_to(array, forma_final).ravel().tolist()
    if nan_para_none:
        valores = [None if isinstance(v, float) and v != v else v for v in valores]
    return valores


# ==========================================================
# PROCESSAMENTO DE UMA PARCELA
# ==========================================================

def _processar_parcela(grupo: pd.DataFrame, intensidades: np.ndarray) -> Tuple[List[tuple], List[tuple]]:
    """Processa todas as combinações de manejo pra uma parcela. Cada
    intervenção é aplicada sobre as árvores remanescentes da anterior —
    resolvido pra todas as combinações de uma vez (ver _proximo_corte)."""
    grupo = grupo.sort_values(by=["dap", "arvore_id"], kind="mergesort").reset_index(drop=True)

    talhao = grupo["talhao"].iloc[0]
    parcela = grupo["parcela"].iloc[0]
    area_parcela = float(grupo["area_parcela"].iloc[0])

    dap = grupo["dap"].to_numpy(dtype=float)
    ab = grupo["ab"].to_numpy(dtype=float)
    ht = grupo["ht"].to_numpy(dtype=float)
    vtcc = grupo["vtcc"].to_numpy(dtype=float)
    arvore_id = grupo["arvore_id"].to_numpy(dtype=np.int64).tolist()
    n = len(grupo)

    fustes_originais = n
    ab_total_original = float(ab.sum())
    dap_med_original = float(dap.mean())
    ht_med_original = float(ht.mean())
    ab_med_original = ab_total_original / n

    # ab_acumulada_global[i] = soma de AB dos i primeiros elementos
    # (menor DAP primeiro); tamanho n+1, ab_acumulada_global[0] = 0.
    ab_acumulada_global = np.empty(n + 1, dtype=float)
    ab_acumulada_global[0] = 0.0
    np.cumsum(ab, out=ab_acumulada_global[1:])

    # soma_dap_sufixo[k]/soma_ht_sufixo[k] = soma de dap[k:n]/ht[k:n]
    # (usado pro DAP/Ht médio das remanescentes, que são sempre um
    # sufixo do array ordenado). soma_vtcc_sufixo é o mesmo formato, mas
    # usado pra somar o VTCC das árvores REMOVIDAS num bloco [i, k) —
    # soma_vtcc_sufixo[i] - soma_vtcc_sufixo[k] (ver _estatisticas_por_corte).
    soma_dap_sufixo = np.concatenate(([0.0], np.cumsum(dap[::-1])))[::-1]
    soma_ht_sufixo = np.concatenate(([0.0], np.cumsum(ht[::-1])))[::-1]
    soma_vtcc_sufixo = np.concatenate(([0.0], np.cumsum(vtcc[::-1])))[::-1]

    intens = np.asarray(intensidades, dtype=float)
    N = len(intens)
    forma_final = (N, N, N)

    eixo_raleio = intens.reshape(N, 1, 1)
    eixo_d1 = intens.reshape(1, N, 1)
    eixo_d2 = intens.reshape(1, 1, N)

    ab_antes_raleio = ab_acumulada_global[n]  # AB total da parcela, igual pra todo mundo
    k1 = _proximo_corte(ab_acumulada_global, 0, eixo_raleio, n)
    stats_raleio = _estatisticas_por_corte(
        ab_acumulada_global, soma_dap_sufixo, soma_ht_sufixo, soma_vtcc_sufixo, dap,
        0, k1, ab_antes_raleio, n)
    _checar_intensidade_real(stats_raleio, eixo_raleio, "Raleio")

    ab_antes_d1 = ab_acumulada_global[n] - ab_acumulada_global[k1]
    k2 = _proximo_corte(ab_acumulada_global, k1, eixo_d1, n)
    stats_d1 = _estatisticas_por_corte(
        ab_acumulada_global, soma_dap_sufixo, soma_ht_sufixo, soma_vtcc_sufixo, dap,
        k1, k2, ab_antes_d1, n)
    _checar_intensidade_real(stats_d1, eixo_d1, "1º Desbaste")

    ab_antes_d2 = ab_acumulada_global[n] - ab_acumulada_global[k2]
    k3 = _proximo_corte(ab_acumulada_global, k2, eixo_d2, n)
    stats_d2 = _estatisticas_por_corte(
        ab_acumulada_global, soma_dap_sufixo, soma_ht_sufixo, soma_vtcc_sufixo, dap,
        k2, k3, ab_antes_d2, n)
    _checar_intensidade_real(stats_d2, eixo_d2, "2º Desbaste")

    for stats in (stats_raleio, stats_d1, stats_d2):
        stats["fustes_ha_removidos"] = stats["fustes_ha_removidos"] / area_parcela
        stats["vtcc_ha_removido"] = stats["vtcc_ha_removido"] / area_parcela

    ab_removida_total = (
        np.broadcast_to(stats_raleio["ab_removida"], forma_final)
        + np.broadcast_to(stats_d1["ab_removida"], forma_final)
        + stats_d2["ab_removida"]
    )
    fustes_removidos_total = (
        np.broadcast_to(stats_raleio["fustes_removidos"], forma_final)
        + np.broadcast_to(stats_d1["fustes_removidos"], forma_final)
        + stats_d2["fustes_removidos"]
    )
    fustes_ha_removidos_total = fustes_removidos_total * 10_000.0 / area_parcela
    vtcc_ha_removido_total = (
        np.broadcast_to(stats_raleio["vtcc_ha_removido"], forma_final)
        + np.broadcast_to(stats_d1["vtcc_ha_removido"], forma_final)
        + stats_d2["vtcc_ha_removido"]
    )

    total = N ** 3
    colunas_valores = [
        [talhao] * total, [parcela] * total, [area_parcela] * total,
        _lista(eixo_raleio, forma_final), _lista(eixo_d1, forma_final), _lista(eixo_d2, forma_final),
        [dap_med_original] * total, [ht_med_original] * total, [ab_med_original] * total,
        [ab_total_original] * total, [fustes_originais] * total,
    ]
    for stats in (stats_raleio, stats_d1, stats_d2):
        colunas_valores += [
            _lista(stats["dap_med"], forma_final, nan_para_none=True),
            _lista(stats["dap_max"], forma_final, nan_para_none=True),
            _lista(stats["dap_min"], forma_final, nan_para_none=True),
            _lista(stats["ht_med"], forma_final, nan_para_none=True),
            _lista(stats["ab_med"], forma_final, nan_para_none=True),
            _lista(stats["ab_total"], forma_final),
            _lista(stats["fustes"], forma_final),
            _lista(stats["ab_antes"], forma_final),
            _lista(stats["ab_removida"], forma_final),
            _lista(stats["int_real"], forma_final),
            _lista(stats["fustes_removidos"], forma_final),
            _lista(stats["fustes_ha_removidos"], forma_final),
            _lista(stats["vtcc_ha_removido"], forma_final),
        ]
    colunas_valores += [
        _lista(ab_removida_total, forma_final),
        _lista(fustes_removidos_total, forma_final),
        _lista(fustes_ha_removidos_total, forma_final),
        _lista(vtcc_ha_removido_total, forma_final),
    ]
    linhas_resumo = list(zip(*colunas_valores))

    # ------------------------------------------------------------
    # Detalhamento árvore por árvore: ainda precisa de um laço por
    # combinação (cada uma remove um grupo de árvores de tamanho
    # diferente — não dá pra vetorizar sem "espichar" tudo em memória),
    # mas sem tocar em pandas dentro do laço (só fatiamento de listas
    # Python já extraídas uma vez), que é onde a maior parte do custo
    # por combinação estava (sort_values, .iloc[].copy(), itertuples()).
    # ------------------------------------------------------------
    k1_flat = _lista(k1, forma_final)
    k2_flat = _lista(k2, forma_final)
    k3_flat = _lista(k3, forma_final)
    int_raleio_flat = colunas_valores[3]
    int_d1_flat = colunas_valores[4]
    int_d2_flat = colunas_valores[5]
    dap_lista = dap.tolist()
    ab_lista = ab.tolist()

    linhas_detalhe: List[tuple] = []
    for indice in range(total):
        k_a, k_b, k_c = k1_flat[indice], k2_flat[indice], k3_flat[indice]
        ir, id1, id2 = int_raleio_flat[indice], int_d1_flat[indice], int_d2_flat[indice]

        for inicio, fim, etapa, situacao in (
            (k_a, n, "Após raleio", "Remanescente"),
            (k_b, n, "Após 1º desbaste", "Remanescente"),
            (k_c, n, "Após 2º desbaste", "Remanescente"),
            (0, k_a, "Raleio", "Removida"),
            (k_a, k_b, "1º desbaste", "Removida"),
            (k_b, k_c, "2º desbaste", "Removida"),
        ):
            if fim <= inicio:
                continue
            linhas_detalhe.extend(
                (talhao, parcela, area_parcela, arvore_id[i], dap_lista[i], ab_lista[i],
                 ir, id1, id2, etapa, situacao)
                for i in range(inicio, fim)
            )

    return linhas_resumo, linhas_detalhe


# ==========================================================
# EXECUÇÃO PRINCIPAL
# ==========================================================

def rodar_simulacao(
    conn: sqlite3.Connection,
    mapeamento: Dict[str, str],
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict:
    """Roda a simulação completa e grava o resultado nas tabelas do
    projeto (substituindo qualquer resultado anterior). Não sincroniza o
    projeto nem fecha a conexão — isso é responsabilidade de quem chama."""
    intensidades = obter_intensidades(conn)
    df, quantidade_removida = carregar_base(conn, mapeamento)

    _criar_tabelas_resultado(conn)

    sql_inserir_parcela = _sql_inserir(TABELA_RESUMO_PARCELA, _COLUNAS_RESUMO_PARCELA)
    sql_inserir_detalhe = _sql_inserir(TABELA_DETALHAMENTO, _COLUNAS_DETALHAMENTO)

    grupos = df.groupby(["talhao", "parcela"], sort=False, dropna=False)
    total_parcelas = grupos.ngroups

    for numero, (_, grupo) in enumerate(grupos, start=1):
        linhas_resumo, linhas_detalhe = _processar_parcela(grupo, intensidades)

        conn.executemany(sql_inserir_parcela, linhas_resumo)
        if linhas_detalhe:
            conn.executemany(sql_inserir_detalhe, linhas_detalhe)

        if progress_callback is not None:
            progress_callback(numero, total_parcelas)

    # Índice criado só agora (depois do bulk insert, não antes) — criar
    # antes encareceria cada INSERT com manutenção incremental do índice;
    # bem mais rápido construir de uma vez sobre a tabela já populada.
    # Acelera o GROUP BY de _sql_agregar_talhao logo abaixo.
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS idx_resumo_parcela_chave ON "{TABELA_RESUMO_PARCELA}" '
        '(talhao, int_raleio, int_desbaste_1, int_desbaste_2)'
    )
    conn.execute(_sql_agregar_talhao())

    # Acelera as consultas repetidas sobre o resumo por talhão: os 3
    # SELECT DISTINCT de simulacao.obter_intensidades_disponiveis (rodam
    # toda vez que a tela de Simulação reavalia se está pronta pra gerar)
    # e o WHERE por combinação exata de intensidades em
    # weibull_fit.ajustar_a_partir_da_simulacao.
    for coluna in ("int_raleio", "int_desbaste_1", "int_desbaste_2"):
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_resumo_talhao_{coluna} ON '
            f'"{TABELA_RESUMO_TALHAO}" ("{coluna}")'
        )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS idx_resumo_talhao_chave ON "{TABELA_RESUMO_TALHAO}" '
        '(talhao, int_raleio, int_desbaste_1, int_desbaste_2)'
    )

    conn.commit()

    def _contar(nome_tabela):
        return conn.execute(f'SELECT COUNT(*) FROM "{nome_tabela}"').fetchone()[0]

    return {
        "arvores_validas": len(df),
        "arvores_removidas_invalidas": quantidade_removida,
        "talhoes": int(df["talhao"].nunique(dropna=False)),
        "parcelas": total_parcelas,
        "combinacoes_por_parcela": len(intensidades) ** 3,
        "linhas_resumo_parcela": _contar(TABELA_RESUMO_PARCELA),
        "linhas_resumo_talhao": _contar(TABELA_RESUMO_TALHAO),
        "linhas_detalhamento": _contar(TABELA_DETALHAMENTO),
    }
