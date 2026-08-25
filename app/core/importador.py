# -*- coding: utf-8 -*-
"""
Importa uma planilha (.xls/.xlsx) pra dentro de uma tabela do projeto,
usando os cabeçalhos da planilha como nome das colunas. A tabela é
recriada do zero a cada importação (substitui o conteúdo anterior).

Tudo é gravado como TEXT — a planilha de origem pode misturar tipos por
coluna (número, texto, célula vazia), então guardamos o valor bruto como
texto pra não perder nada na importação; conversão numérica fica por
conta de quem for consumir os dados depois.
"""
import csv
import re

import pandas as pd

from .db import conectar_caminho

NOME_TABELA_BASE_IFC = "base_ifc_talhao"
NOME_TABELA_BASE_IFC_BYTREE = "base_ifc_arvore"

TAMANHO_LOTE_IMPORTACAO_CSV = 50_000  # linhas por lote — grande o bastante pra
# poucas idas e vindas ao SQLite (um INSERT em lote por chunk, não um por
# linha), pequeno o bastante pra nunca materializar um CSV de vários GB
# inteiro em memória (pandas.read_csv(chunksize=...) já garante isso; este
# número só controla o tamanho de cada pedaço mantido em RAM por vez).


def _normalizar_nome_coluna(nome, usados):
    nome = str(nome).strip()
    nome = re.sub(r"\s+", "_", nome)
    nome = re.sub(r"[^0-9A-Za-zÀ-ÿ_]", "", nome)
    if not nome:
        nome = "coluna"
    if nome[0].isdigit():
        nome = f"c_{nome}"

    base = nome
    contador = 1
    while nome.lower() in usados:
        contador += 1
        nome = f"{base}_{contador}"
    usados.add(nome.lower())
    return nome


def _eh_csv(caminho_arquivo):
    return str(caminho_arquivo).lower().endswith(".csv")


def listar_abas(caminho_arquivo):
    """CSV não tem conceito de aba — retorna uma lista com um único
    marcador (None) pra que o restante do fluxo de importação (que
    espera uma lista de abas) funcione igual pros dois formatos."""
    if _eh_csv(caminho_arquivo):
        return [None]
    return pd.ExcelFile(caminho_arquivo).sheet_names


def _ler_csv(caminho_arquivo, **kwargs):
    """CSV exportado do Excel no Windows em pt-BR normalmente vem em
    cp1252, não UTF-8 — tenta UTF-8 primeiro (mais comum vindo de outras
    fontes) e cai pra cp1252 se decodificar como UTF-8 falhar, em vez de
    estourar erro pro usuário por causa de acento."""
    try:
        return pd.read_csv(caminho_arquivo, encoding="utf-8-sig", sep=None, engine="python", **kwargs)
    except UnicodeDecodeError:
        return pd.read_csv(caminho_arquivo, encoding="cp1252", sep=None, engine="python", **kwargs)


def _detectar_encoding_e_separador_csv(caminho_arquivo, tamanho_amostra=1_000_000):
    """Lê só uma amostra do início do arquivo (nunca o arquivo inteiro — um
    CSV de vários GB não cabe, nem precisa caber, em memória só pra decidir
    encoding/separador) pra descobrir a codificação (mesma lógica de
    _ler_csv: utf-8 primeiro, cp1252 se falhar) e o separador (csv.Sniffer
    — a mesma ferramenta que o sep=None do pandas usa por baixo dos panos).
    Assume codificação/separador consistentes do início ao fim do arquivo —
    a mesma premissa que a leitura não streamada (_ler_csv) já fazia."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(caminho_arquivo, "r", encoding=encoding) as f:
                amostra = f.read(tamanho_amostra)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Não foi possível detectar a codificação do CSV (utf-8/cp1252).")

    try:
        separador = csv.Sniffer().sniff(amostra, delimiters=",;\t|").delimiter
    except csv.Error:
        separador = ","  # amostra pequena/1 coluna só — Sniffer não decide; vírgula é o padrão
    return encoding, separador


def pre_visualizar(caminho_arquivo, aba, n_linhas=12):
    """Lê as primeiras linhas cruas (sem interpretar cabeçalho) — usado
    pra deixar o usuário escolher visualmente qual linha é o cabeçalho
    de verdade antes de importar."""
    if _eh_csv(caminho_arquivo):
        return _ler_csv(caminho_arquivo, header=None, nrows=n_linhas, dtype=str)
    return pd.read_excel(caminho_arquivo, sheet_name=aba, header=None, nrows=n_linhas, dtype=str)


def ler_planilha(caminho_arquivo, aba=0, linha_cabecalho=0):
    """Lê o arquivo (planilha ou CSV) inteiro com o cabeçalho na linha
    indicada, tudo como texto bruto — usado pelos fluxos de importação
    que fazem seu próprio mapeamento de colunas (ex: Ajustes de Weibull),
    em vez de recriar uma tabela genérica como `importar_planilha`."""
    if _eh_csv(caminho_arquivo):
        return _ler_csv(caminho_arquivo, header=linha_cabecalho, dtype=str)
    return pd.read_excel(caminho_arquivo, sheet_name=aba, header=linha_cabecalho, dtype=str)


def _importar_csv_em_lotes(caminho_arquivo, caminho_banco, nome_tabela, linha_cabecalho,
                            progress_callback=None):
    """Lê o CSV em pedaços (engine C — bem mais rápido que o engine python
    usado por _ler_csv, que precisa dele pra sniffar sep=None linha a
    linha; aqui o separador já foi decidido antes, uma vez só, sobre uma
    amostra) e insere um lote por vez, tudo dentro de UMA transação só
    (nenhum commit() intermediário): o banco de trabalho já roda com
    synchronous=OFF/journal_mode=MEMORY (ver app/db.py) e é descartável,
    então segurar a transação até o fim não custa nada em durabilidade
    real, e mantém a importação atômica — se uma linha no meio do arquivo
    falhar (ex: encoding inconsistente), a tabela de destino fica
    exatamente como estava antes (nada parcial), igual ao comportamento
    anterior de ler tudo de uma vez. Retorna (n_linhas, colunas_sql)."""
    encoding, separador = _detectar_encoding_e_separador_csv(caminho_arquivo)
    leitor = pd.read_csv(
        caminho_arquivo, encoding=encoding, sep=separador, engine="c",
        header=linha_cabecalho, dtype=str, chunksize=TAMANHO_LOTE_IMPORTACAO_CSV,
    )

    conn = conectar_caminho(caminho_banco)
    try:
        # BEGIN explícito: o módulo sqlite3 do Python fecha (commita) a
        # transação implícita antes de rodar DDL (CREATE/DROP TABLE) — sem
        # isso, um rollback() depois de um erro no meio do arquivo desfaria
        # só os INSERTs, deixando "{nome_tabela}" criada (vazia ou
        # parcialmente populada) em vez de voltar ao estado anterior.
        conn.execute("BEGIN")
        conn.execute(f'DROP TABLE IF EXISTS "{nome_tabela}"')

        colunas_sql = None
        sql_insert = None
        total_linhas = 0
        try:
            for chunk in leitor:
                if colunas_sql is None:
                    usados = set()
                    colunas_sql = [_normalizar_nome_coluna(c, usados) for c in chunk.columns]
                    definicao = ", ".join(f'"{c}" TEXT' for c in colunas_sql)
                    conn.execute(
                        f'CREATE TABLE "{nome_tabela}" '
                        f'(id INTEGER PRIMARY KEY AUTOINCREMENT, {definicao})')
                    colunas_join = ", ".join(f'"{c}"' for c in colunas_sql)
                    marcadores = ", ".join("?" for _ in colunas_sql)
                    sql_insert = (
                        f'INSERT INTO "{nome_tabela}" ({colunas_join}) VALUES ({marcadores})')

                linhas = [
                    tuple(None if pd.isna(v) else str(v) for v in linha)
                    for linha in chunk.itertuples(index=False, name=None)
                ]
                conn.executemany(sql_insert, linhas)
                total_linhas += len(linhas)
                if progress_callback is not None:
                    progress_callback(total_linhas, None)
        except UnicodeDecodeError as e:
            raise ValueError(f"Falha ao decodificar o CSV (tentativa com {encoding}): {e}") from e

        if colunas_sql is None:
            raise ValueError("A planilha CSV está vazia.")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return total_linhas, colunas_sql


def importar_planilha(caminho_arquivo, caminho_banco, nome_tabela, aba=0, linha_cabecalho=0,
                       progress_callback=None):
    """Lê a planilha e (re)cria `nome_tabela` no banco em caminho_banco.
    `aba` é o nome (ou índice) da planilha a importar — sem isso, o
    pandas usa a primeira aba do arquivo por padrão, o que costuma ser a
    aba errada em workbooks com várias abas. `linha_cabecalho` é o
    índice (0-based) da linha com os nomes das colunas — sem isso, o
    pandas assume a linha 1, o que já causou import errado numa planilha
    com cabeçalho composto em várias linhas. Retorna (n_linhas, colunas_sql).

    `progress_callback(linhas, total)` é chamado a cada lote só no caminho
    CSV — `total` sempre None (contar linhas de antemão exigiria varrer o
    arquivo inteiro uma vez a mais só pra isso). CSV é lido e inserido em
    chunks (ver _importar_csv_em_lotes): é o formato mais provável pra
    exports externos muito grandes. .xlsx continua lido de uma vez só —
    pandas/openpyxl não oferece leitura em streaming real pra Excel (o
    parser sempre materializa a planilha inteira), e workbooks Excel têm
    limite de 1.048.576 linhas por aba, então não chegam à escala de um
    CSV de vários GB; progress_callback nunca é chamado nesse caminho."""
    if _eh_csv(caminho_arquivo):
        return _importar_csv_em_lotes(
            caminho_arquivo, caminho_banco, nome_tabela, linha_cabecalho, progress_callback)

    df = pd.read_excel(caminho_arquivo, sheet_name=aba, header=linha_cabecalho, dtype=str)

    usados = set()
    colunas_sql = [_normalizar_nome_coluna(c, usados) for c in df.columns]

    conn = conectar_caminho(caminho_banco)
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{nome_tabela}"')
        definicao = ", ".join(f'"{c}" TEXT' for c in colunas_sql)
        conn.execute(
            f'CREATE TABLE "{nome_tabela}" (id INTEGER PRIMARY KEY AUTOINCREMENT, {definicao})'
        )

        colunas_join = ", ".join(f'"{c}"' for c in colunas_sql)
        marcadores = ", ".join("?" for _ in colunas_sql)
        linhas = [
            tuple(None if pd.isna(v) else str(v) for v in linha)
            for linha in df.itertuples(index=False, name=None)
        ]
        conn.executemany(
            f'INSERT INTO "{nome_tabela}" ({colunas_join}) VALUES ({marcadores})', linhas
        )
        conn.commit()
    finally:
        conn.close()

    return len(df), colunas_sql
