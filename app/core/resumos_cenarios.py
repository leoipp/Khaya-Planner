# -*- coding: utf-8 -*-
"""Junção e resumo configurável dos cenários armazenados em Parquet."""
import io
import json
import re
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import simulacao
from .numerico import converter_numero

FONTES = {
    "populacao": "População por talhão e idade",
    "distribuicao": "Distribuição diamétrica",
    "volume_sortimento": "Volume por sortimento",
    "cenarios": "Metadados do cenário",
}

AGREGACOES = {
    "Soma": "sum", "Média": "mean", "Mínimo": "min", "Máximo": "max",
    "Primeiro": "first", "Último": "last", "Contagem": "count",
    "Distintos": "nunique",
}

_AGREGACOES_NUMERICAS = {"sum", "mean", "min", "max"}


def _cenario(conn, cenario_id):
    row = conn.execute(
        "SELECT id, nome, idade_raleio, intensidade_raleio, idade_desbaste_1, "
        "intensidade_desbaste_1, idade_desbaste_2, intensidade_desbaste_2, "
        "idade_corte_raso FROM simulacao_cenarios WHERE id=?", (cenario_id,)).fetchone()
    if not row:
        raise ValueError(f"Cenário {cenario_id} não encontrado.")
    nomes = ("cenario_id", "cenario", "idade_raleio", "intensidade_raleio",
             "idade_desbaste_1", "intensidade_desbaste_1", "idade_desbaste_2",
             "intensidade_desbaste_2", "idade_corte_raso")
    return dict(zip(nomes, row))


def carregar_fonte(conn: sqlite3.Connection, cenario_id: int, fonte: str) -> pd.DataFrame:
    meta = _cenario(conn, cenario_id)
    if fonte == "cenarios":
        return pd.DataFrame([meta])
    resultado = simulacao.carregar_cenario_parquet(conn, cenario_id)
    if resultado is None:
        raise ValueError(f'O cenário "{meta["cenario"]}" não possui dados Parquet.')
    if fonte == "populacao":
        df = resultado["_df_populacao"].copy()
    elif fonte == "distribuicao":
        linhas = resultado.get("_linhas_distribuicao", ())
        df = (pd.DataFrame({
            "populacao_id": linhas[0], "classe_diametrica": linhas[1],
            "probabilidade": linhas[2], "densidade": linhas[3],
        }) if linhas else pd.DataFrame(columns=(
            "populacao_id", "classe_diametrica", "probabilidade", "densidade")))
    elif fonte == "volume_sortimento":
        persistir = resultado.get("resultado_volume_sortimento", {}).get("_persistir_volume")
        if not persistir or not persistir[3]:
            df = pd.DataFrame()
        else:
            df = pd.DataFrame.from_records(persistir[3], columns=persistir[1])
    else:
        raise ValueError(f"Fonte desconhecida: {fonte}")
    # Toda linha pertence a um único cenário. Propaga também os parâmetros
    # de manejo para que possam ser usados diretamente como dimensões ou
    # indicadores, sem obrigar o usuário a montar uma segunda junção apenas
    # para recuperar os metadados do cenário.
    for posicao, (coluna, valor) in enumerate(meta.items()):
        if coluna not in df.columns:
            df.insert(min(posicao, len(df.columns)), coluna, valor)
    return df


def _prefixar(df: pd.DataFrame, fonte: str) -> pd.DataFrame:
    return df.rename(columns={c: f"{fonte}.{c}" for c in df.columns})


def cenarios_gerados(conn):
    return conn.execute(
        "SELECT id, nome FROM simulacao_cenarios WHERE status='Gerado' ORDER BY id").fetchall()


def colunas_fonte(conn, fonte: str) -> List[str]:
    cenarios = cenarios_gerados(conn)
    for cenario_id, _nome in cenarios:
        colunas = list(carregar_fonte(conn, cenario_id, fonte).columns)
        if colunas:
            return colunas
    return []


def _juntar(conn, cenario_id: int, cfg: Dict) -> pd.DataFrame:
    fonte_a = cfg["fonte_a"]
    a = _prefixar(carregar_fonte(conn, cenario_id, fonte_a), fonte_a)
    fonte_b = cfg.get("fonte_b")
    if not fonte_b:
        return a
    b = _prefixar(carregar_fonte(conn, cenario_id, fonte_b), fonte_b)
    chaves_a = [f"{fonte_a}.{c}" for c in cfg.get("chaves_a", [])]
    chaves_b = [f"{fonte_b}.{c}" for c in cfg.get("chaves_b", [])]
    if not chaves_a or len(chaves_a) != len(chaves_b):
        raise ValueError("Informe a mesma quantidade de chaves nas duas fontes.")
    return a.merge(b, left_on=chaves_a, right_on=chaves_b,
                   how=cfg.get("tipo_join", "left"), sort=False, copy=False)


def _nome_grupo(coluna):
    return coluna.split(".", 1)[-1]


def _normalizar_chave(valor):
    """Representação estável para comparar chaves vindas do Parquet e da UI."""
    if pd.isna(valor):
        return ""
    if isinstance(valor, (float, np.floating)) and float(valor).is_integer():
        return str(int(valor))
    return str(valor).strip()


def _aplicar_filtro_chaves(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    filtro = cfg.get("filtro_chaves") or {}
    colunas = [c for c in filtro.get("colunas", []) if c]
    valores = filtro.get("valores", [])
    if not colunas or not valores:
        return df
    faltantes = [c for c in colunas if c not in df.columns]
    if faltantes:
        raise ValueError("Colunas do filtro de chaves ausentes: " + ", ".join(faltantes))
    invalidas = [i + 1 for i, linha in enumerate(valores) if len(linha) != len(colunas)]
    if invalidas:
        raise ValueError(
            "Cada chave permitida deve ter exatamente "
            f"{len(colunas)} valor(es). Verifique a(s) linha(s): "
            + ", ".join(map(str, invalidas)))
    chaves = {
        tuple(_normalizar_chave(v) for v in linha)
        for linha in valores
    }
    if not chaves:
        raise ValueError("Nenhuma chave válida foi informada para o filtro.")
    serie_chaves = pd.Series(
        zip(*(df[c].map(_normalizar_chave) for c in colunas)), index=df.index)
    return df.loc[serie_chaves.isin(chaves)].copy()


def _coluna_auxiliar(df: pd.DataFrame, base: str) -> str:
    """Cria um nome interno que não colida com uma coluna da fonte."""
    nome = base
    while nome in df.columns:
        nome += "_"
    return nome


def processar_cenario(conn, cenario_id: int, cfg: Dict) -> pd.DataFrame:
    df = _juntar(conn, cenario_id, cfg)
    df = _aplicar_filtro_chaves(df, cfg)
    grupos = cfg.get("grupos", [])
    if not grupos:
        raise ValueError("Selecione ao menos uma coluna de agrupamento.")
    faltantes = [c for c in grupos if c not in df.columns]
    if faltantes:
        raise ValueError("Colunas de agrupamento ausentes: " + ", ".join(faltantes))

    metricas = cfg.get("metricas", [])
    especificacao = {}
    for indice, metrica in enumerate(metricas):
        coluna = metrica["coluna"]
        if coluna not in df.columns:
            raise ValueError(f'Coluna de indicador ausente: "{coluna}".')
        alias = metrica.get("alias") or _nome_grupo(coluna)
        agregacao = metrica["agregacao"]
        coluna_agregacao = coluna
        if agregacao in _AGREGACOES_NUMERICAS:
            # Colunas originais da Base IFC podem chegar como TEXT/object
            # mesmo quando visualmente contêm números (inclusive "12,5").
            # mean/sum do pandas não convertem isso de forma tolerante e
            # levantam "agg function failed [dtype->object]". Usa a mesma
            # conversão numérica do restante do aplicativo somente para
            # operações que exigem números; first/last/count/nunique
            # preservam o conteúdo textual original.
            coluna_agregacao = _coluna_auxiliar(df, f"__indicador_numerico_{indice}")
            df[coluna_agregacao] = converter_numero(df[coluna])
        especificacao[alias] = pd.NamedAgg(column=coluna_agregacao, aggfunc=agregacao)
    if especificacao:
        resumo = df.groupby(grupos, dropna=False, sort=False).agg(**especificacao).reset_index()
        # Pós-processamento independente da agregação: permite, por exemplo,
        # calcular a média com precisão completa e só então arredondar a
        # coluna apresentada/exportada. Configurações antigas não possuem
        # "arredondar" e seguem sem alteração.
        for metrica in metricas:
            casas = metrica.get("arredondar")
            alias = metrica.get("alias") or _nome_grupo(metrica["coluna"])
            if casas is not None and alias in resumo.columns:
                resumo[alias] = converter_numero(resumo[alias]).round(int(casas))
    else:
        resumo = df[grupos].drop_duplicates().reset_index(drop=True)

    pivo = cfg.get("pivo") or {}
    if pivo.get("coluna") and pivo.get("valor"):
        if pivo["coluna"] not in df.columns or pivo["valor"] not in df.columns:
            raise ValueError("A coluna ou o valor configurado no pivô não existe.")
        agregacao_pivo = pivo.get("agregacao", "sum")
        valor_pivo = pivo["valor"]
        if agregacao_pivo in _AGREGACOES_NUMERICAS:
            valor_pivo = _coluna_auxiliar(df, "__pivo_valor_numerico")
            df[valor_pivo] = converter_numero(df[pivo["valor"]])
        tabela_pivo = pd.pivot_table(
            df, index=grupos, columns=pivo["coluna"], values=valor_pivo,
            aggfunc=agregacao_pivo, fill_value=pivo.get("preencher", 0),
            dropna=False, observed=False).reset_index()
        nomes_grupo = set(grupos)
        tabela_pivo.columns = [
            c if c in nomes_grupo else str(int(c)) if isinstance(c, (int, float, np.number))
            and float(c).is_integer() else str(c) for c in tabela_pivo.columns]
        resumo = resumo.merge(tabela_pivo, on=grupos, how="outer", sort=False)

    renomear = {g: _nome_grupo(g) for g in grupos}
    # Se duas fontes fornecerem dimensões homônimas, mantém o prefixo para
    # evitar colisão silenciosa no banco exportado.
    valores = list(renomear.values())
    for g, nome in list(renomear.items()):
        if valores.count(nome) > 1:
            renomear[g] = g.replace(".", "_")
    return resumo.rename(columns=renomear)


def processar(conn, cfg: Dict, limite_cenarios: Optional[int] = None) -> pd.DataFrame:
    cenarios = cenarios_gerados(conn)
    if limite_cenarios is not None:
        cenarios = cenarios[:limite_cenarios]
    partes = [processar_cenario(conn, cid, cfg) for cid, _nome in cenarios]
    return pd.concat(partes, ignore_index=True, sort=False) if partes else pd.DataFrame()


def exportar_sqlite(conn_origem, caminho_destino: str, tabela: str, cfg: Dict,
                    progresso=None) -> Dict:
    if not re.fullmatch(r"[\wÀ-ÿ .-]+", tabela or ""):
        raise ValueError("Nome da tabela de destino inválido.")
    cenarios = cenarios_gerados(conn_origem)
    destino = sqlite3.connect(caminho_destino)
    try:
        destino.execute("PRAGMA journal_mode=OFF")
        destino.execute("PRAGMA synchronous=OFF")
        destino.execute("PRAGMA locking_mode=EXCLUSIVE")
        partes = []
        for numero, (cid, nome) in enumerate(cenarios, 1):
            parte = processar_cenario(conn_origem, cid, cfg)
            partes.append(parte)
            if progresso:
                progresso(numero, len(cenarios), nome)
        resumo = pd.concat(partes, ignore_index=True, sort=False) if partes else pd.DataFrame()
        destino.execute(f'DROP TABLE IF EXISTS "{tabela}"')
        if resumo.empty and not len(resumo.columns):
            raise ValueError("O resumo não produziu nenhuma coluna para exportar.")
        # A união acontece só depois de todos os cenários para incorporar
        # anos/categorias de pivô que não existiam no primeiro. O objeto em
        # memória é o resumo (tipicamente cenário × talhão), nunca as
        # centenas de milhares de linhas brutas.
        n_colunas = max(len(resumo.columns), 1)
        chunksize = max(1, min(1000, 30000 // n_colunas))
        resumo.to_sql(tabela, destino, if_exists="replace", index=False,
                      chunksize=chunksize, method="multi")
        destino.commit()
        return {"cenarios": len(cenarios), "linhas": len(resumo), "tabela": tabela}
    finally:
        destino.close()


def listar_configuracoes(conn):
    _garantir_tabela_config(conn)
    return conn.execute("SELECT id, nome, configuracao_json FROM resumos_cenarios_config ORDER BY nome").fetchall()


def salvar_configuracao(conn, nome: str, cfg: Dict):
    _garantir_tabela_config(conn)
    conn.execute(
        "INSERT INTO resumos_cenarios_config(nome, configuracao_json, atualizado_em) "
        "VALUES(?, ?, datetime('now','localtime')) ON CONFLICT(nome) DO UPDATE SET "
        "configuracao_json=excluded.configuracao_json, atualizado_em=excluded.atualizado_em",
        (nome, json.dumps(cfg, ensure_ascii=False)))
    conn.commit()


def _garantir_tabela_config(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS resumos_cenarios_config ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE, "
        "configuracao_json TEXT NOT NULL, "
        "atualizado_em TEXT DEFAULT (datetime('now','localtime')))"
    )
