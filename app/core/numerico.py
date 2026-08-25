# -*- coding: utf-8 -*-
"""Conversão numérica/data tolerante a texto — compartilhada por qualquer
módulo que leia colunas da base_ifc (gravadas como TEXT bruto, ver
importador.py) e precise interpretar tanto '12,50'/'1.234,56' (padrão
BR) quanto '12.50'/'1234.56' (padrão US), ou datas em formatos
diferentes linha a linha."""
from datetime import datetime

import pandas as pd
from dateutil import parser as dateutil_parser


def converter_numero(serie: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce")

    texto = serie.astype(str).str.strip().str.replace(" ", "", regex=False)

    possui_virgula = texto.str.contains(",", regex=False, na=False).any()
    if possui_virgula:
        texto = texto.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)

    return pd.to_numeric(texto, errors="coerce")


def _converter_uma_data(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return pd.NaT
    if isinstance(valor, (pd.Timestamp, datetime)):
        return pd.Timestamp(valor)
    texto = str(valor).strip()
    if not texto:
        return pd.NaT
    # ISO primeiro (sem ambiguidade — "2015-03-15"), só cai pro dateutil
    # com dayfirst=True (formato brasileiro "15/03/2015") se não for ISO,
    # porque dayfirst=True aplicado numa string ISO troca mês e dia. Linha
    # a linha (não um pd.to_datetime(serie) só) porque esse, ao receber a
    # série inteira, tenta inferir UM formato pra todas as linhas e vira
    # NaT em qualquer uma que não bata — uma planilha real bem comumente
    # mistura os dois formatos entre linhas (mesmo raciocínio de
    # app/widgets/tabela.py:_converter, que já lida com isso célula a
    # célula pro tipo "Data" da Tabela).
    try:
        return pd.Timestamp(datetime.fromisoformat(texto))
    except ValueError:
        pass
    try:
        return pd.Timestamp(dateutil_parser.parse(texto, dayfirst=True))
    except (ValueError, OverflowError, TypeError):
        return pd.NaT


def converter_data(serie: pd.Series) -> pd.Series:
    """Conversão de data tolerante, mesma ideia de converter_numero mas pra
    coluna de data da base_ifc (também gravada como TEXT bruto) — aceita
    ISO e formato brasileiro, inclusive misturados entre linhas; valores
    que não derem pra interpretar viram NaT (Not a Time), não erro."""
    return serie.apply(_converter_uma_data)
