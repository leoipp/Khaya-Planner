# -*- coding: utf-8 -*-
"""
Geração do "esqueleto" idade a idade da simulação de manejo: cada
talhão da base IFC ByTalhao (base_ifc_talhao) é repetido pra cada idade
de 1 até a idade máxima de manejo (Configurações), marcando em qual
idade cai cada intervenção — Raleio, 1º Desbaste, 2º Desbaste — com a
idade e a intensidade que o usuário definir nesta simulação.

Cada talhão também recebe o forma/escala do ajuste Weibull "Por Talhão"
(app/weibull_ifc.py, TABELA_TALHAO — ajustado sobre a base IFC ByTree),
cruzado pelo valor de uma coluna de talhão da Base IFC ByTalhao que o
usuário escolhe (guardada em `simulacao_metadados`, lembrada entre
execuções) contra a única coluna-chave desse ajuste — por isso o ajuste
"Por Talhão" precisa ter sido configurado com uma única coluna-chave:
só assim dá pra saber contra qual valor cruzar.

Além disso, cada talhão recebe três outros pares forma/escala — um pra
depois do Raleio, um pra depois do 1º Desbaste, um pra depois do 2º
Desbaste — vindos do ajuste Weibull "Por Simulação"
(parametros_weibull_manejo, populado por
weibull_fit.ajustar_a_partir_da_simulacao). A chave desse ajuste é
(talhao, int_raleio, int_desbaste_1, int_desbaste_2, manejo/etapa), e
"talhao" ali vem da própria base IFC ByTree (mesmo mapeamento usado na
Simulação de Intensidades) — por isso cruza direto pelo valor de
`coluna_talhao_ifc`, sem precisar de outro mapeamento: é a mesma noção
de talhão já usada pro ajuste "Por Talhão". int_raleio/int_desbaste_1/
int_desbaste_2 são exatamente as três intensidades escolhidas nesta
simulação (ver combobox), então a busca é sempre por uma combinação
específica, não por todas as combinações testadas.

As intensidades de cada intervenção vêm de um combobox alimentado pelos
valores de int_raleio/int_desbaste_1/int_desbaste_2 já testados na
Simulação de Intensidades (intensidades_resumo_talhao) — que são
frações (0.0–0.5), não percentual. `intensidade_evento` guarda essa
mesma fração (sem reconverter pra percentual), pra poder ser comparada
depois, sem perda de precisão, com parametros_weibull_manejo (que usa a
mesma escala, populado por weibull_fit.ajustar_a_partir_da_simulacao).

Pra cada linha (talhão, idade) também é escolhido qual par forma/escala
está "em vigor" naquela idade — antes de qualquer manejo, o original
("Por Talhão"); a partir da idade de cada manejo (inclusive), o daquela
etapa ("Por Simulação"): idade < idade do Raleio usa o original; idade
>= Raleio (e < 1º Desbaste) usa apos_raleio; idade >= 1º Desbaste (e <
2º Desbaste) usa apos_desbaste_1; idade >= 2º Desbaste usa
apos_desbaste_2. Esse par "vigente" (forma_atual/escala_atual) é o que
alimenta a distribuição diamétrica: pra cada classe de diâmetro
configurada (Configurações: primeira/última classe, intervalo), a
probabilidade da classe é

    P(classe) = S(classe - 0,5) - S(classe + 0,5)

com S(x) = exp(-((max(x, 0) / escala) ** forma)) a função de
sobrevivência da Weibull (Location = 0) — o "± 0,5" é fixo (não segue o
intervalo de classe configurado): é a metade de largura padrão de uma
classe diamétrica de 1 cm, prática usual pra converter a densidade
contínua da Weibull em massa de probabilidade por diâmetro inteiro. Ao
lado, também é guardada a densidade (PDF) da Weibull avaliada exatamente
no valor da classe (sem integrar numa janela):

    f(classe) = (forma/escala) * (classe/escala)^(forma-1) * exp(-(classe/escala)^forma)

— a "altura instantânea" da curva ali (`densidade_weibull`), não uma
massa de probabilidade, então não é normalizada pra somar 1 por linha
como `probabilidade` é. Guardado em `simulacao_distribuicao_diametrica`
(colunas `probabilidade` e `densidade`), uma linha por (linha de
simulacao_talhao_idade, classe), referenciando
`simulacao_talhao_idade.id` — evita repetir todas as colunas de
talhão/idade pra cada classe.

Essa tabela é a base sobre a qual as próximas etapas da simulação
(crescimento por idade, volume, financeiro) vão trabalhar.
"""
import json
import io
import pickle
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from .importador import NOME_TABELA_BASE_IFC
from .intensidades import TABELA_RESUMO_TALHAO
from .numerico import converter_data, converter_numero
from .weibull_ifc import TABELA_TALHAO as TABELA_WEIBULL_TALHAO, carregar_metadados

TABELA_POPULACAO = "simulacao_talhao_idade"
TABELA_DISTRIBUICAO = "simulacao_distribuicao_diametrica"
TABELA_VOLUME_SORTIMENTO = "simulacao_volume_sortimento"
TABELA_PARAMETROS_WEIBULL_MANEJO = "parametros_weibull_manejo"
TABELA_MIP_CONTINUO = "simulacao_mip_continuo"
TABELA_MIP_AJUSTE_LOGISTICO = "simulacao_mip_ajuste_logistico"

# Tabelas UNIFICADAS do lote "Múltiplos cenários"/"Grade automática" (ver
# persistir_cenario_no_lote mais abaixo) — substituem o antigo esquema de
# uma tabela "__cenarioN" por cenário (milhares de DROP+CREATE num lote
# grande, ver o comentário de _garantir_tabelas_lote pra motivação
# completa). Uma linha por (cenário, talhão, idade[, classe]), com uma
# coluna cenario_id em vez de um sufixo de nome de tabela. Nomes
# escolhidos pra não colidir com os canônicos acima (só o cenário
# "ativado" usa esses) nem com NOME_TABELA_EXPORTACAO_CENARIOS
# ("simulacao_todos_cenarios", app/screens/simulacao.py — feature
# diferente: exporta pra um arquivo .sqlite NOVO, à parte).
TABELA_LOTE_POPULACAO = "simulacao_lote_populacao"
TABELA_LOTE_DISTRIBUICAO = "simulacao_lote_distribuicao_diametrica"
TABELA_LOTE_VOLUME_SORTIMENTO = "simulacao_lote_volume_sortimento"
TABELA_LOTE_MIP_CONTINUO = "simulacao_lote_mip_continuo"
TABELA_LOTE_MIP_AJUSTE_LOGISTICO = "simulacao_lote_mip_ajuste_logistico"
TABELA_CENARIOS_PARQUET = "simulacao_cenarios_parquet"


def _garantir_tabela_cenarios_parquet(conn: sqlite3.Connection) -> None:
    """Cria o armazenamento colunar também em projetos já abertos.

    ``db.inicializar_schema`` cobre novas aberturas, mas durante o
    desenvolvimento/atualização o processo pode continuar com uma cópia
    de trabalho criada antes dessa tabela existir. Manter a garantia junto
    ao primeiro uso evita exigir reinício do aplicativo.
    """
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{TABELA_CENARIOS_PARQUET}" ('
        "cenario_id INTEGER PRIMARY KEY, "
        "formato_populacao TEXT NOT NULL DEFAULT 'parquet', "
        "populacao BLOB NOT NULL, distribuicao BLOB NOT NULL, metadados BLOB NOT NULL, "
        "atualizado_em TEXT DEFAULT (datetime('now', 'localtime')), "
        "FOREIGN KEY (cenario_id) REFERENCES simulacao_cenarios(id) ON DELETE CASCADE)"
    )


def _existe_tabela_cenarios_parquet(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABELA_CENARIOS_PARQUET,),
    ).fetchone() is not None

# Mínimo de pontos (idade, 1/IP ou 1/IPM) pra tentar o ajuste logístico —
# o modelo tem 3 parâmetros livres (a, b, c); com menos que isso o ajuste
# é instável ou nem converge.
MINIMO_PONTOS_AJUSTE_LOGISTICO = 4

EVENTO_RALEIO = "Raleio"
EVENTO_DESBASTE_1 = "1º Desbaste"
EVENTO_DESBASTE_2 = "2º Desbaste"
EVENTO_CORTE_RASO = "Corte Raso"

# Rótulos de etapa gravados em parametros_weibull_manejo.manejo por
# weibull_fit.ajustar_a_partir_da_simulacao (ver app/intensidades.py,
# onde as árvores remanescentes de cada etapa recebem esses mesmos
# rótulos) — mapeados aqui pro sufixo de coluna correspondente.
_SUFIXO_POR_ETAPA = {
    "Após raleio": "apos_raleio",
    "Após 1º desbaste": "apos_desbaste_1",
    "Após 2º desbaste": "apos_desbaste_2",
}


def _nome_coluna_destino(nome: str, reservados: set) -> str:
    """Evita colidir com uma coluna que já exista em base_ifc_talhao
    (ex: a planilha importada já tinha uma coluna chamada "Forma")."""
    candidato = nome
    while candidato.lower() in reservados:
        candidato = f"talhao_{candidato}"
    reservados.add(candidato.lower())
    return candidato


# ==========================================================
# CONFIGURAÇÃO DA SIMULAÇÃO (lembrada entre execuções)
# ==========================================================

def colunas_base_ifc_talhao(conn: sqlite3.Connection) -> List[str]:
    """Colunas disponíveis em base_ifc_talhao (sem o id interno). Levanta
    sqlite3.OperationalError se a tabela ainda não existir."""
    cursor = conn.execute(f'SELECT * FROM "{NOME_TABELA_BASE_IFC}" LIMIT 0')
    return [d[0] for d in cursor.description if d[0] != "id"]


def obter_coluna_talhao(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT coluna_talhao_ifc FROM simulacao_metadados WHERE id = 1").fetchone()
    return row[0] if row else None


def salvar_coluna_talhao(conn: sqlite3.Connection, coluna: str) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_talhao_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET coluna_talhao_ifc = excluded.coluna_talhao_ifc",
        (coluna,),
    )


def obter_coluna_fustes_observados(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_fustes_observados_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_fustes_observados(conn: sqlite3.Connection, coluna: str) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_fustes_observados_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_fustes_observados_ifc = excluded.coluna_fustes_observados_ifc",
        (coluna,),
    )


# Mapeamento opcional das colunas de DAP médio/máximo/mínimo observados
# (baseline antes de qualquer manejo, mesmo papel que coluna_fustes_observados
# tem pra fustes_atual — ver gerar_populacao). Ficam None se o usuário não
# mapear: nesse caso dap_med_atual/dap_max_atual/dap_min_atual continuam
# vazios antes do primeiro manejo, como sempre foi.

def obter_coluna_dap_med_observado(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_dap_med_observado_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_dap_med_observado(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_dap_med_observado_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_dap_med_observado_ifc = excluded.coluna_dap_med_observado_ifc",
        (coluna,),
    )


def obter_coluna_dap_max_observado(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_dap_max_observado_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_dap_max_observado(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_dap_max_observado_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_dap_max_observado_ifc = excluded.coluna_dap_max_observado_ifc",
        (coluna,),
    )


def obter_coluna_dap_min_observado(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_dap_min_observado_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_dap_min_observado(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_dap_min_observado_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_dap_min_observado_ifc = excluded.coluna_dap_min_observado_ifc",
        (coluna,),
    )


# Ht médio observado — mesmo papel de baseline que os três acima têm pra
# DAP: opcional, se mapeada, ht_atual vem preenchida com esse valor antes
# do primeiro manejo, em vez de vazia. A partir do primeiro manejo,
# ht_atual passa a vir de ht_med_apos_<etapa> (ver gerar_populacao) —
# mesmo tratamento de "substituição direta por etapa" que dap_med_atual
# já tem, não a subtração acumulada que fustes_atual/vtcc_atual usam.

def obter_coluna_ht_observado(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_ht_observado_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_ht_observado(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_ht_observado_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_ht_observado_ifc = excluded.coluna_ht_observado_ifc",
        (coluna,),
    )


# VTCC observado — baseline de volume/ha antes de qualquer manejo.
# Diferente do Ht acima, vtcc_atual usa o mesmo tratamento de fustes_atual:
# a cada etapa, vtcc_ha_removido_apos_<etapa> (quanto foi removido) é
# SUBTRAÍDO do valor vigente (que começa nesta coluna observada), não
# substituído direto — ver gerar_populacao.

def obter_coluna_vtcc_observado(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_vtcc_observado_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_vtcc_observado(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_vtcc_observado_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_vtcc_observado_ifc = excluded.coluna_vtcc_observado_ifc",
        (coluna,),
    )


# CV do DAP observado — baseline pra cv_dap_atual, mesmo papel que Ht
# observado tem pra ht_atual (substituição direta por etapa, não
# acumulada): opcional, sem coluna mapeada, cv_dap_atual continua NaN
# antes do primeiro manejo (comportamento de antes desta coluna existir).
# Diferente de dap_med/dap_max/dap_min/ht/vtcc observados, a Base IFC
# ByTalhao normalmente não tem essa métrica pronta — precisa ter sido
# calculada por fora (ex: CV de uma amostra de DAP no talhão) e importada
# como coluna própria antes de aparecer aqui pra mapear.

def obter_coluna_cv_dap_observado(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_cv_dap_observado_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_cv_dap_observado(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_cv_dap_observado_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_cv_dap_observado_ifc = excluded.coluna_cv_dap_observado_ifc",
        (coluna,),
    )


# Data de plantio — opcional, mesmo papel de baseline que as colunas
# acima têm, mas alimenta ano_simulado em vez de um "<campo>_atual": o
# ano de cada linha de simulacao_talhao_idade é o ano do plantio + a
# idade_simulada daquela linha (ver gerar_populacao). Sem coluna mapeada,
# ano_simulado fica vazio (não trava a geração).

def obter_coluna_data_plantio(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_data_plantio_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_data_plantio(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_data_plantio_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_data_plantio_ifc = excluded.coluna_data_plantio_ifc",
        (coluna,),
    )


# Data de medição — cadastrada junto da data de plantio (par natural na
# Base IFC ByTalhao), mas hoje só guardada/lembrada: nenhum cálculo de
# gerar_populacao usa esse valor ainda (ano_simulado usa só a data de
# plantio, ver acima). Persistida à parte, direto pela tela (mesmo padrão
# de coluna_forma_distribuicao/coluna_escala_distribuicao — não passa por
# gerar_populacao), não por não ter uso nenhum na base, e sim porque não
# tem uso NESTA conta específica ainda.

def obter_coluna_data_medicao(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_data_medicao_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_data_medicao(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_data_medicao_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_data_medicao_ifc = excluded.coluna_data_medicao_ifc",
        (coluna,),
    )


# Apontamento opcional de qual coluna de simulacao_talhao_idade usar como
# forma/escala na distribuição diamétrica (ver calcular_distribuicao_diametrica
# mais abaixo) — em branco/None usa forma_atual/escala_atual (o par calculado
# por gerar_populacao a partir do pipeline Weibull); apontado, pode ser
# qualquer coluna numérica ali, inclusive uma gerada por um construtor de
# variáveis salvo (app/construtores.py) depois de reaplicado.

def obter_coluna_forma_distribuicao(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_forma_distribuicao_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_forma_distribuicao(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_forma_distribuicao_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_forma_distribuicao_ifc = excluded.coluna_forma_distribuicao_ifc",
        (coluna,),
    )


def obter_coluna_escala_distribuicao(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT coluna_escala_distribuicao_ifc FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def salvar_coluna_escala_distribuicao(conn: sqlite3.Connection, coluna: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_escala_distribuicao_ifc) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_escala_distribuicao_ifc = excluded.coluna_escala_distribuicao_ifc",
        (coluna,),
    )


# Apontamento opcional de qual variável por classe diamétrica (nome-base
# das colunas "base_<classe>" que um Modelo ligado no nó Classe Diamétrica
# do Construtor de Variáveis gera em simulacao_talhao_idade, ex: "vtcc" pra
# "vtcc_5"/"vtcc_7"/...) representa volume, pra agregar por sortimento (ver
# calcular_volume_por_sortimento mais abaixo) — em branco/None, a etapa é
# pulada (nenhuma tabela de volume por sortimento é gerada).

def obter_colunas_base_volume_classes(conn: sqlite3.Connection) -> List[str]:
    """Nomes-base de volume por classe escolhidos no campo "Volume" (tela
    Simulação — multi-seleção: dá pra marcar mais de um, ex: volume total E
    volume de biomassa, cada um virando seu próprio jogo de colunas por
    sortimento em calcular_volume_por_sortimento). Persistido como lista
    JSON-codificada na mesma coluna TEXT `coluna_base_volume_classes` de
    `simulacao_metadados` (nome mantido no singular por compatibilidade com
    o schema já existente). Tolera o valor salvo por versões anteriores
    (um nome solto, sem JSON, de antes da multi-seleção existir) tratando
    como lista de 1 item. Lista vazia se nada selecionado/cadastrado
    ainda."""
    row = conn.execute(
        "SELECT coluna_base_volume_classes FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    if not row or not row[0]:
        return []
    bruto = row[0]
    try:
        valor = json.loads(bruto)
    except (TypeError, ValueError):
        return [bruto]
    if isinstance(valor, list):
        return [v for v in valor if v]
    return [bruto]


def salvar_colunas_base_volume_classes(conn: sqlite3.Connection, colunas: Optional[List[str]]) -> None:
    valor = json.dumps(list(colunas)) if colunas else None
    conn.execute(
        "INSERT INTO simulacao_metadados (id, coluna_base_volume_classes) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "coluna_base_volume_classes = excluded.coluna_base_volume_classes",
        (valor,),
    )


def obter_tipo_agregacao_volume(conn: sqlite3.Connection) -> Dict[str, str]:
    """Tipo de agregação (Soma/Média) por variável de volume marcada no
    campo "Volume" (tela Simulação) — {nome-base: "Soma"/"Média"},
    independente por variável (ex: soma pro volume total, média pro
    volume de biomassa). Persistido como dict JSON-codificado na coluna
    TEXT `tipo_agregacao_volume` de simulacao_metadados. O valor salvo por
    versões anteriores (uma string solta "Soma"/"Média", de quando a
    agregação era uma só pra todas as variáveis) não é convertido — {}
    nesse caso, e a tela aplica "Soma" como padrão pra cada variável; a
    próxima "Gerar simulação" já regrava no formato novo."""
    row = conn.execute(
        "SELECT tipo_agregacao_volume FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        valor = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def salvar_tipo_agregacao_volume(conn: sqlite3.Connection, tipos: Optional[Dict[str, str]]) -> None:
    valor = json.dumps(tipos) if tipos else None
    conn.execute(
        "INSERT INTO simulacao_metadados (id, tipo_agregacao_volume) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "tipo_agregacao_volume = excluded.tipo_agregacao_volume",
        (valor,),
    )


def obter_usar_tabela_agregacao_volume(conn: sqlite3.Connection) -> bool:
    """Checkbox "Tabela de agregação" (tela Simulação, cartão "Parâmetros
    da Simulação") — desligado por padrão. Desligado, a tabela de
    variável/agregação (colunas_base_volume_classes/tipo_agregacao_volume)
    fica escondida E a etapa de volume por sortimento é pulada inteira em
    self.gerar(), mesmo que já existam linhas marcadas nela de uma sessão
    anterior — a seleção continua salva, só não é lida/aplicada enquanto o
    checkbox estiver desligado (ver TelaSimulacao._colunas_volume_classes_
    marcadas/_tipos_agregacao_volume_marcadas)."""
    row = conn.execute(
        "SELECT usar_tabela_agregacao_volume FROM simulacao_metadados WHERE id = 1"
    ).fetchone()
    return bool(row[0]) if row else False


def salvar_usar_tabela_agregacao_volume(conn: sqlite3.Connection, usar: bool) -> None:
    conn.execute(
        "INSERT INTO simulacao_metadados (id, usar_tabela_agregacao_volume) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "usar_tabela_agregacao_volume = excluded.usar_tabela_agregacao_volume",
        (1 if usar else 0,),
    )


# ==========================================================
# INTENSIDADES DISPONÍVEIS (Simulação de Intensidades)
# ==========================================================

def obter_intensidades_disponiveis(conn: sqlite3.Connection) -> Dict[str, List[float]]:
    """Valores distintos de int_raleio/int_desbaste_1/int_desbaste_2 já
    testados na Simulação de Intensidades (frações, não percentual).
    Levanta sqlite3.OperationalError se a tabela ainda não existir."""
    resultado = {}
    for coluna in ("int_raleio", "int_desbaste_1", "int_desbaste_2"):
        linhas = conn.execute(
            f'SELECT DISTINCT "{coluna}" FROM "{TABELA_RESUMO_TALHAO}" ORDER BY "{coluna}"'
        ).fetchall()
        resultado[coluna] = [float(linha[0]) for linha in linhas if linha[0] is not None]
    return resultado


# ==========================================================
# AJUSTE WEIBULL "POR TALHÃO"
# ==========================================================

def obter_ajuste_weibull_por_talhao(conn: sqlite3.Connection) -> Tuple[str, pd.DataFrame]:
    """Valida que o ajuste Weibull "Por Talhão" existe e foi configurado
    com uma única coluna-chave, e retorna (nome_da_coluna_chave,
    DataFrame [coluna_chave, forma, escala] só com os grupos OK)."""
    metadados = carregar_metadados(conn, TABELA_WEIBULL_TALHAO)
    if metadados is None:
        raise ValueError(
            "Nenhum ajuste Weibull \"Por Talhão\" encontrado. Rode o ajuste na aba "
            "\"Por Talhão\" da tela Weibull antes de gerar a simulação."
        )

    colunas_chave = metadados["colunas_chave_destino"]
    if len(colunas_chave) != 1:
        raise ValueError(
            "O ajuste Weibull \"Por Talhão\" precisa ter uma única coluna-chave (o talhão) "
            f"pra ser usado na simulação — hoje está configurado com {len(colunas_chave)}: "
            f"{', '.join(colunas_chave) or '(nenhuma)'}. Reconfigure o ajuste na aba \"Por Talhão\"."
        )

    coluna_chave = colunas_chave[0]
    df = pd.read_sql_query(
        f'SELECT "{coluna_chave}", forma, escala FROM "{TABELA_WEIBULL_TALHAO}" WHERE status = \'OK\'',
        conn,
    )
    return coluna_chave, df


# ==========================================================
# AJUSTE WEIBULL "POR SIMULAÇÃO" (por etapa, na combinação de
# intensidades escolhida)
# ==========================================================

def obter_ajuste_weibull_por_simulacao(
    conn: sqlite3.Connection,
    int_raleio: float, int_desbaste_1: float, int_desbaste_2: float,
) -> pd.DataFrame:
    """Retorna um DataFrame com uma linha por talhão e, pra cada etapa
    (apos_raleio/apos_desbaste_1/apos_desbaste_2), as colunas
    forma_<etapa>/escala_<etapa>/dap_med_<etapa>/dap_max_<etapa>/
    dap_min_<etapa>/ht_med_<etapa>/fustes_ha_removidos_<etapa>/
    vtcc_ha_removido_<etapa>/cv_dap_<etapa>/dg_<etapa>/
    truncado_esquerda_<etapa> — ajustadas/calculadas na aba "Por
    Simulação" da tela Weibull (parametros_weibull_manejo) pra cada
    etapa, na combinação exata de intensidades passada.
    `truncado_esquerda_<etapa>` vem de weibull_fit.ajustar_grupo (gravado
    por ajuste, não depende do valor atual do checkbox em Configurações)
    — diz se a Weibull daquela etapa foi de fato ajustada com MLE
    truncado à esquerda, em `dap_min_<etapa>` como ponto de corte; usado
    por calcular_distribuicao_diametrica/densidade_weibull pra saber se
    precisa aplicar a correção de truncagem na área/densidade por classe.
    Um talhão sem ajuste numa etapa específica (ex: grupo sem dados
    suficientes) fica com NaN só naquela etapa — não impede achar as
    outras. Levanta ValueError se parametros_weibull_manejo estiver vazia
    (ajuste nunca rodado)."""
    df = pd.read_sql_query(
        f'SELECT talhao, manejo, forma, escala, dap_med, dap_max, dap_min, fustes_ha_removidos, '
        f'ht_med, vtcc_ha_removido, cv_dap, dg, truncado_esquerda '
        f'FROM "{TABELA_PARAMETROS_WEIBULL_MANEJO}" '
        "WHERE int_raleio = ? AND int_desbaste_1 = ? AND int_desbaste_2 = ?",
        conn, params=(int_raleio, int_desbaste_1, int_desbaste_2),
    )

    if df.empty:
        total = conn.execute(f'SELECT COUNT(*) FROM "{TABELA_PARAMETROS_WEIBULL_MANEJO}"').fetchone()[0]
        if total == 0:
            raise ValueError(
                "Nenhum ajuste Weibull \"Por Simulação\" encontrado. Rode \"Ajustar com base na "
                "simulação de intensidades\" na aba \"Por Simulação\" da tela Weibull antes de "
                "gerar a simulação."
            )
        raise ValueError(
            "O ajuste Weibull \"Por Simulação\" não tem nenhum resultado pra essa combinação "
            "exata de intensidades (Raleio/1º Desbaste/2º Desbaste). Rode \"Ajustar com base na "
            "simulação de intensidades\" novamente se a Simulação de Intensidades mudou."
        )

    talhoes = sorted(df["talhao"].unique())
    resultado = pd.DataFrame({"talhao": talhoes}).set_index("talhao")

    for etapa, sufixo in _SUFIXO_POR_ETAPA.items():
        por_etapa = df[df["manejo"] == etapa].set_index("talhao")
        resultado[f"forma_{sufixo}"] = por_etapa["forma"]
        resultado[f"escala_{sufixo}"] = por_etapa["escala"]
        resultado[f"dap_med_{sufixo}"] = por_etapa["dap_med"]
        resultado[f"dap_max_{sufixo}"] = por_etapa["dap_max"]
        resultado[f"dap_min_{sufixo}"] = por_etapa["dap_min"]
        resultado[f"ht_med_{sufixo}"] = por_etapa["ht_med"]
        resultado[f"fustes_ha_removidos_{sufixo}"] = por_etapa["fustes_ha_removidos"]
        resultado[f"vtcc_ha_removido_{sufixo}"] = por_etapa["vtcc_ha_removido"]
        resultado[f"cv_dap_{sufixo}"] = por_etapa["cv_dap"]
        resultado[f"dg_{sufixo}"] = por_etapa["dg"]
        resultado[f"truncado_esquerda_{sufixo}"] = por_etapa["truncado_esquerda"]

    return resultado.reset_index()


# ==========================================================
# IDADE MÁXIMA DE MANEJO (Configurações)
# ==========================================================

def obter_idade_maxima_manejo(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT idade_maxima_manejo FROM configuracoes WHERE id = 1").fetchone()
    if row is None or row[0] is None:
        raise ValueError(
            "Configure a \"Idade máxima de manejo\" na tela Configurações antes de gerar a simulação."
        )
    idade_maxima = float(row[0])
    if idade_maxima < 1:
        raise ValueError("A idade máxima de manejo precisa ser pelo menos 1.")
    return idade_maxima


def obter_numero_minimo_arvores(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT numero_minimo_arvores_ha FROM configuracoes WHERE id = 1").fetchone()
    if row is None or row[0] is None:
        raise ValueError(
            "Configure o \"Número mínimo de árvores/ha ao final do manejo\" na tela "
            "Configurações antes de gerar a simulação."
        )
    numero_minimo = float(row[0])
    if numero_minimo < 0:
        raise ValueError(
            "O número mínimo de árvores/ha ao final do manejo precisa ser maior ou igual a zero."
        )
    return numero_minimo


def obter_truncar_esquerda_padrao(conn: sqlite3.Connection) -> bool:
    """Se o ajuste Weibull "Por Simulação" (tela Weibull, botão "Ajustar
    com base na simulação de intensidades...") deve usar truncagem à
    esquerda por padrão (ver weibull_fit.ajustar_a_partir_da_simulacao,
    pensado pra desbaste por área basal, que corta os menores primeiro).
    Diferente de idade_maxima_manejo/numero_minimo_arvores acima, essa
    config não é obrigatória — ausente (projeto novo ou tabela ainda sem
    a coluna) equivale a desligado (False), não levanta ValueError."""
    row = conn.execute("SELECT truncar_esquerda_padrao FROM configuracoes WHERE id = 1").fetchone()
    return bool(row[0]) if row and row[0] is not None else False


def obter_tipo_normalizacao_weibull(conn: sqlite3.Connection) -> str:
    """Como normalizar `probabilidades_por_classe` pra somar 1 por linha
    (ver TIPOS_NORMALIZACAO_WEIBULL pra explicação de cada tipo) —
    "aditiva" (padrão, inclusive quando a config ainda não foi definida)
    ou "proporcional", configurável na tela Configurações."""
    row = conn.execute("SELECT tipo_normalizacao_weibull FROM configuracoes WHERE id = 1").fetchone()
    if row and row[0] in TIPOS_NORMALIZACAO_WEIBULL:
        return row[0]
    return "aditiva"


def obter_ajuste_manejo_padrao(conn: sqlite3.Connection) -> bool:
    """Se `gerar_populacao` deve empurrar a idade de Raleio/1º Desbaste/
    2º Desbaste pra não cair num ano-calendário já passado (ver
    `ano_referencia`/`__ano_plantio__` em gerar_populacao) — desligado
    por padrão (mesmo comportamento de sempre: idade configurada usada
    ao pé da letra, igual pra todo talhão)."""
    row = conn.execute("SELECT ajuste_manejo_padrao FROM configuracoes WHERE id = 1").fetchone()
    return bool(row[0]) if row and row[0] is not None else False


def obter_ano_referencia(conn: sqlite3.Connection) -> Optional[int]:
    """Ano de referência configurado (tela Configurações) — hoje também
    usado pro desconto financeiro (VPL, ver construtores.py). None se
    ainda não configurado."""
    row = conn.execute("SELECT ano_referencia FROM configuracoes WHERE id = 1").fetchone()
    return int(row[0]) if row and row[0] is not None else None


BASES_AJUSTE_LOGISTICO = ("ip", "ipm")


def obter_base_ajuste_logistico(conn: sqlite3.Connection) -> str:
    """Se o ajuste logístico (ITD, ver ajustar_logistico/
    calcular_mip_continuo) usa (idade, 1/Ingresso Percentual) ou (idade,
    1/Ingresso Percentual Médio) como pontos — "ip" (padrão, inclusive
    quando a config ainda não foi definida) ou "ipm", configurável na
    tela Configurações. Como IPM = IP/idade, IPM tende a já vir
    decrescente desde a 1ª idade simulada (a divisão por uma idade sempre
    crescente esconde o platô que pode existir no IP bruto antes do
    povoamento realmente começar a se estabilizar) — nesse caso o ajuste
    sobre 1/IPM empurra a ITD pra perto de zero mesmo quando o IP bruto
    só começa a declinar bem mais tarde; "ip" (padrão) evita essa
    distorção."""
    row = conn.execute("SELECT base_ajuste_logistico FROM configuracoes WHERE id = 1").fetchone()
    if row and row[0] in BASES_AJUSTE_LOGISTICO:
        return row[0]
    return "ip"


BASES_CALCULO_MIP = ("fdp", "classe")


def obter_base_calculo_mip(conn: sqlite3.Connection) -> str:
    """Se o DD/IP do MIP (ver _calcular_mip_talhao/calcular_mip_continuo)
    compara idades consecutivas pela DENSIDADE (fdp, `densidade_weibull` —
    "fdp", padrão, inclusive quando a config ainda não foi definida) ou
    pela PROBABILIDADE por classe já normalizada (`probabilidades_por_classe`,
    mesma conta de simulacao_distribuicao_diametrica — "classe"), configurável
    na tela Configurações. "fdp" segue a Figura 3 de Helfenstein (2020,
    curvas de densidade sobrepostas por idade, DD = ponto de cruzamento);
    "classe" segue mais de perto o texto de Leite et al. (2005), que
    descreve o método em cima de F(x) (acumulada) por classe — usa
    `obter_tipo_normalizacao_weibull` pra normalização (aditiva/
    proporcional), igual à distribuição diamétrica."""
    row = conn.execute("SELECT base_calculo_mip FROM configuracoes WHERE id = 1").fetchone()
    if row and row[0] in BASES_CALCULO_MIP:
        return row[0]
    return "fdp"


# ==========================================================
# CLASSES DIAMÉTRICAS E DISTRIBUIÇÃO WEIBULL (Configurações)
# ==========================================================

def obter_classes_diametricas(conn: sqlite3.Connection) -> np.ndarray:
    """Classes de diâmetro configuradas (primeira/última classe,
    intervalo — tela Configurações), como array de centros de classe."""
    row = conn.execute(
        "SELECT primeira_classe_diametrica, ultima_classe_diametrica, intervalo_classe "
        "FROM configuracoes WHERE id = 1"
    ).fetchone()
    if row is None or any(v is None for v in row):
        raise ValueError(
            "Configure a primeira classe, a última classe e o intervalo de classe diamétrica "
            "na tela Configurações antes de gerar a simulação."
        )

    primeira, ultima, intervalo = (float(v) for v in row)

    if intervalo <= 0:
        raise ValueError("O intervalo de classe diamétrica precisa ser maior que zero.")
    if ultima < primeira:
        raise ValueError("A última classe diamétrica precisa ser maior ou igual à primeira.")

    return np.round(np.arange(primeira, ultima + intervalo / 2, intervalo), 4)


# ==========================================================
# PERFIL DO CASO (diagnóstico de performance de lote)
# ==========================================================

def obter_perfil_caso(conn: sqlite3.Connection) -> Dict:
    """Resumo do "tamanho" do caso (nº de talhões, idade máxima de manejo,
    nº de classes diamétricas) — só pra dar contexto ao diagnóstico de
    performance de um lote (ver app/screens/simulacao.py:_ThreadGerarLote,
    TelaSimulacao._finalizar_geracao_lote); não usado por nenhum cálculo.
    `idade_maxima_manejo`/`n_classes_diametricas` saem None se essas
    configurações ainda não foram preenchidas (mesmo ValueError que
    gerar_populacao levantaria) — um perfil incompleto não deve impedir o
    lote de rodar nem de mostrar o resto do diagnóstico."""
    n_talhoes = conn.execute(f'SELECT COUNT(*) FROM "{NOME_TABELA_BASE_IFC}"').fetchone()[0]
    try:
        idade_maxima_manejo = obter_idade_maxima_manejo(conn)
    except ValueError:
        idade_maxima_manejo = None
    try:
        n_classes_diametricas = len(obter_classes_diametricas(conn))
    except ValueError:
        n_classes_diametricas = None
    return {
        "n_talhoes": n_talhoes,
        "idade_maxima_manejo": idade_maxima_manejo,
        "n_classes_diametricas": n_classes_diametricas,
    }


def sobrevivencia_weibull(x, forma, escala):
    """S(x) = P(diâmetro > x) da Weibull (Location = 0). x é limitado a
    >= 0 porque a Weibull não tem massa de probabilidade abaixo de
    zero — sem isso, classes próximas de zero (classe - 0,5 < 0)
    quebrariam elevando um número negativo a um expoente fracionário.
    Nome público (sem "_"): também usado por app/construtores.py pro nó
    "Distribuição Diamétrica" do Construtor de Variáveis.

    `escala` <= 0 é degenerado (Weibull sem escala não existe de
    verdade — ex: forma/escala recuperados/preditos numa linha sem dado
    suficiente) mas não precisa derrubar nada: o limite matemático já dá
    o resultado certo sozinho (x/0 -> +inf -> exp(-inf) = 0 pra x > 0;
    0/0 -> NaN só no ponto x = 0, genuinamente indefinido ali) — só o
    numpy avisa (RuntimeWarning) no caminho até lá. errstate silencia
    esse aviso esperado sem mudar o valor calculado."""
    x = np.maximum(x, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.exp(-((x / escala) ** forma))


def densidade_weibull(x, forma, escala, limite_truncamento=None):
    """f(x) = densidade de probabilidade da Weibull (Location = 0):
    (forma/escala) * (x/escala)^(forma-1) * exp(-(x/escala)^forma), x >= 0
    (zero fora do suporte). Usada pelo MIP (calcular_mip_continuo/
    _calcular_mip_talhao) pra achar o Diâmetro Diferenciador — a primeira
    classe, percorrendo do menor pro maior diâmetro, em que
    fdp(idade) - fdp(idade-1) muda de negativo pra positivo — e pra montar
    a coluna `densidade` da distribuição exportada (ver
    _calcular_matriz_distribuicao).

    `limite_truncamento` (opcional): ponto de corte L de uma Weibull
    truncada à esquerda (ver weibull_fit.ajustar_grupo/
    `truncado_esquerda_<etapa>` — grupos ajustados com MLE truncado, ex.
    desbaste por área basal). Precisa já vir num shape "broadcastável"
    contra `x`/`forma`/`escala` (mesma convenção deles aqui — o chamador
    decide o shape, esta função não reformata); NaN numa posição = sem
    truncamento ali (cai na fórmula comum). Onde há truncamento:

        f_T(x) = 0,          x < L
        f_T(x) = f(x) / S(L), x >= L

    (densidade condicional a X >= L — ver comentário em
    weibull_fit.py:111-114).

    `escala` <= 0 é degenerado (ver sobrevivencia_weibull) — o resultado
    (0 ou NaN, conforme o limite matemático) sai certo sozinho, só o
    numpy avisa (RuntimeWarning) no caminho; errstate silencia."""
    x = np.maximum(x, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        f = (forma / escala) * (x / escala) ** (forma - 1.0) * np.exp(-((x / escala) ** forma))
    if limite_truncamento is None:
        return f

    L = np.asarray(limite_truncamento, dtype=float)
    com_truncamento = ~np.isnan(L)
    L_seguro = np.where(com_truncamento, L, 0.0)
    denominador = np.where(com_truncamento, sobrevivencia_weibull(L_seguro, forma, escala), 1.0)
    f_truncada = np.where(x < L_seguro, 0.0, f / denominador)
    return np.where(com_truncamento, f_truncada, f)


TIPOS_NORMALIZACAO_WEIBULL = ("aditiva", "proporcional")

# Contra o que o nó "VPL" (core/construtores.py:avaliar_grafo, ramo
# "vpl_sortimento") conta "n" (o expoente do desconto financeiro),
# tela Configurações:
# - "ano_referencia" (padrão): n = ano_simulado - ano_referencia (sem
#   abs — negativo quando ano_simulado é ANTES do ano de referência,
#   compondo a receita passada pra frente até lá em vez de descontá-la;
#   positivo quando é DEPOIS, descontando normalmente).
# - "ano_zero": n = idade_simulada direto — VPL = FC/(1+taxa)^idade_simulada,
#   descontando contra o plantio do próprio talhão (idade_simulada = 0)
#   em vez de um ano-calendário fixo igual pra todos; a 2ª entrada do nó,
#   nesse modo, é lida como idade (não mais ano-calendário).
BASES_PERIODO_VPL = ("ano_referencia", "ano_zero")


def probabilidades_por_classe(
    forma: np.ndarray, escala: np.ndarray, classes_diametricas: np.ndarray,
    tipo_normalizacao: str = "aditiva", limite_truncamento: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Matriz (linhas × classes) de probabilidade por classe diamétrica —
    uma linha por par (forma, escala), cada classe com P(classe-0.5 <
    diâmetro <= classe+0.5) segundo a Weibull daquela linha, já
    normalizada pra somar exatamente 1 por linha (as classes configuradas
    não cobrem a cauda inteira da Weibull, então a soma bruta normalmente
    fecha abaixo de 1). `forma`/`escala` já alinhados (mesmo tamanho, sem
    NaN).

    `limite_truncamento` (opcional, um valor por linha — mesmo tamanho de
    `forma`/`escala`; NaN numa posição = sem truncamento naquela linha):
    ponto de corte L de uma Weibull truncada à esquerda (ver
    densidade_weibull, weibull_fit.ajustar_grupo/
    `truncado_esquerda_<etapa>`). Onde há truncamento, a probabilidade
    bruta da classe passa a ser

        P_T(classe) = [S(max(classe-0,5, L)) - S(classe+0,5)] / S(L)

    limitada a >= 0 (cobre o caso degenerado de uma classe inteira abaixo
    de L, onde a fórmula pura dá negativo) — calculada ANTES da
    normalização aditiva/proporcional abaixo, que segue igual, em cima
    dessas probabilidades já truncadas.

    `tipo_normalizacao` (ver TIPOS_NORMALIZACAO_WEIBULL,
    obter_tipo_normalizacao_weibull/Configurações):
    - "aditiva" (padrão): soma o déficit (1 - soma bruta) IGUALMENTE em
      cada classe (déficit/N) — preserva a diferença absoluta entre
      classes, mas distorce a forma relativa quando o déficit é grande
      (ex: Weibull com shape baixo, que perde bastante massa fora da
      faixa de classes configurada — uma classe de cauda quase zerada
      pode ganhar peso desproporcional).
    - "proporcional": reescala cada classe por 1/soma_bruta — preserva a
      forma relativa da curva (a proporção entre classes não muda), mas
      amplia mais as classes que já tinham maior probabilidade bruta.
      Linha com soma bruta zero (Weibull sem nenhuma massa nas classes
      configuradas) cai pra uniforme (1/N em cada classe), pra não
      dividir por zero."""
    if len(forma) == 0 or len(classes_diametricas) == 0:
        return np.zeros((len(forma), len(classes_diametricas)))

    classe_inferior = classes_diametricas[None, :] - 0.5
    classe_superior = classes_diametricas[None, :] + 0.5
    forma_col = forma[:, None]
    escala_col = escala[:, None]

    # elegivel: marca, por (linha, classe), se aquela classe PODE receber
    # massa — todas podem, exceto quando há truncamento e a classe cai
    # inteiramente abaixo de L (classe_superior < L: fisicamente não
    # existe árvore ali, foi cortada pelo desbaste). Guardado pra a
    # normalização aditiva não "ressuscitar" essa massa (ver abaixo) —
    # a proporcional já preserva os zeros sozinha (zero vezes qualquer
    # fator continua zero).
    elegivel = np.ones((len(forma), len(classes_diametricas)), dtype=bool)

    if limite_truncamento is not None:
        L = np.asarray(limite_truncamento, dtype=float)[:, None]
        com_truncamento = ~np.isnan(L)
        L_seguro = np.where(com_truncamento, L, 0.0)
        classe_inferior_efetiva = np.where(
            com_truncamento, np.maximum(classe_inferior, L_seguro), classe_inferior)
        s_inferior = sobrevivencia_weibull(classe_inferior_efetiva, forma_col, escala_col)
        s_superior = sobrevivencia_weibull(classe_superior, forma_col, escala_col)
        denominador = np.where(
            com_truncamento, sobrevivencia_weibull(L_seguro, forma_col, escala_col), 1.0)
        # denominador pode ser 0 (escala degenerada — ver
        # sobrevivencia_weibull), dando 0/0 = NaN — resultado esperado
        # nesse caso (nenhuma distribuição de verdade pra normalizar
        # contra), só o numpy avisa no caminho; errstate silencia.
        with np.errstate(divide="ignore", invalid="ignore"):
            probabilidades = np.maximum((s_inferior - s_superior) / denominador, 0.0)
        elegivel = np.where(com_truncamento, classe_superior >= L_seguro, True)
    else:
        s_inferior = sobrevivencia_weibull(classe_inferior, forma_col, escala_col)
        s_superior = sobrevivencia_weibull(classe_superior, forma_col, escala_col)
        probabilidades = s_inferior - s_superior

    if tipo_normalizacao == "proporcional":
        n_classes = len(classes_diametricas)
        soma_por_linha = probabilidades.sum(axis=1, keepdims=True)
        sem_massa = soma_por_linha[:, 0] == 0
        resultado = np.full_like(probabilidades, 1.0 / n_classes)
        resultado[~sem_massa] = probabilidades[~sem_massa] / soma_por_linha[~sem_massa]
        return resultado

    if tipo_normalizacao != "aditiva":
        raise ValueError(
            f"Tipo de normalização \"{tipo_normalizacao}\" desconhecido "
            f"(esperado um de {TIPOS_NORMALIZACAO_WEIBULL}).")

    # Déficit espalhado só entre as classes elegíveis (>= L, quando há
    # truncamento) — sem isso, o déficit "vazaria" de volta pras classes
    # abaixo do corte, ressuscitando uma massa que a truncagem zerou de
    # propósito.
    diferenca_por_linha = 1.0 - probabilidades.sum(axis=1, keepdims=True)
    n_elegiveis_por_linha = np.maximum(elegivel.sum(axis=1, keepdims=True), 1)
    correcao = np.where(elegivel, diferenca_por_linha / n_elegiveis_por_linha, 0.0)
    return probabilidades + correcao


# ==========================================================
# CHECAGEM DE PRONTIDÃO
# ==========================================================

def verificar_prontidao(
    conn: sqlite3.Connection,
    coluna_talhao_ifc: Optional[str] = None,
    coluna_fustes_observados: Optional[str] = None,
) -> Dict:
    """Confere se todas as tabelas/configurações necessárias pra gerar a
    simulação já existem, reaproveitando as mesmas funções de validação
    usadas dentro de gerar_populacao (nenhuma checagem duplicada — só
    capturada aqui como pendências legíveis em vez de deixar estourar).

    `coluna_talhao_ifc`/`coluna_fustes_observados`, se informadas, são
    usadas no lugar do valor persistido em `simulacao_metadados` — só são
    gravadas de fato quando `gerar_populacao` roda, então a tela passa
    aqui o valor que está selecionado no combobox (ainda não salvo) pra
    não travar o botão "Gerar simulação" à toa enquanto o usuário só está
    escolhendo.

    Retorna {"pronta": bool, "pendencias": [str, ...]}."""
    pendencias = []

    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (NOME_TABELA_BASE_IFC,)
    ).fetchone() is None:
        pendencias.append("Base IFC ByTalhao não importada (Configurações)")

    try:
        obter_idade_maxima_manejo(conn)
    except ValueError as e:
        pendencias.append(str(e))

    try:
        obter_numero_minimo_arvores(conn)
    except ValueError as e:
        pendencias.append(str(e))

    try:
        obter_classes_diametricas(conn)
    except ValueError as e:
        pendencias.append(str(e))

    try:
        obter_ajuste_weibull_por_talhao(conn)
    except ValueError as e:
        pendencias.append(str(e))

    total_por_simulacao = conn.execute(
        f'SELECT COUNT(*) FROM "{TABELA_PARAMETROS_WEIBULL_MANEJO}"'
    ).fetchone()[0]
    if total_por_simulacao == 0:
        pendencias.append(
            "Ajuste Weibull \"Por Simulação\" não executado (aba \"Por Simulação\" da tela Weibull)")

    try:
        disponiveis = obter_intensidades_disponiveis(conn)
        if not all(disponiveis.values()):
            pendencias.append(
                "Simulação de Intensidades sem resultados (rode em Configurações)")
    except sqlite3.OperationalError:
        pendencias.append(
            "Simulação de Intensidades não executada (rode em Configurações)")

    coluna_efetiva = coluna_talhao_ifc if coluna_talhao_ifc else obter_coluna_talhao(conn)
    if not coluna_efetiva:
        pendencias.append("Coluna de talhão da Base IFC ByTalhao ainda não selecionada")

    coluna_fustes_efetiva = (
        coluna_fustes_observados if coluna_fustes_observados else obter_coluna_fustes_observados(conn)
    )
    if not coluna_fustes_efetiva:
        pendencias.append("Coluna de fustes observados da Base IFC ByTalhao ainda não selecionada")

    return {"pronta": not pendencias, "pendencias": pendencias}


# ==========================================================
# GERAÇÃO DA POPULAÇÃO
# ==========================================================

def _preparar_baseline_populacao(
    conn: sqlite3.Connection,
    coluna_talhao_ifc: str,
    coluna_fustes_observados: str,
    coluna_dap_med_observado: Optional[str] = None,
    coluna_dap_max_observado: Optional[str] = None,
    coluna_dap_min_observado: Optional[str] = None,
    coluna_ht_observado: Optional[str] = None,
    coluna_vtcc_observado: Optional[str] = None,
    coluna_cv_dap_observado: Optional[str] = None,
    coluna_data_plantio: Optional[str] = None,
) -> Dict:
    """Parte de `gerar_populacao` que NÃO depende da idade/intensidade de
    manejo de um cenário específico — validações de existência da base/
    colunas, leitura de `base_ifc_talhao` inteira, LEFT JOIN com o ajuste
    Weibull "Por Talhão", conversão das colunas observadas, e os nomes de
    coluna de destino (`_nome_coluna_destino`). Chamada uma vez por
    `gerar_populacao` no modo de cenário único (sempre recalcula, já que
    não há lote pra amortizar o custo), ou uma vez só pro LOTE inteiro via
    `preparar_contexto_lote` (modo "Múltiplos cenários" —
    `app/screens/simulacao.py:_ThreadGerarLote`), que reaproveita o
    resultado pra todo cenário do lote em vez de reler `base_ifc_talhao` e
    refazer esse merge em cada um.

    Retorna um dict com tudo que o restante de `gerar_populacao`/
    `calcular_distribuicao_diametrica` precisa. Levanta ValueError nos
    mesmos casos de sempre (base não importada, coluna mapeada
    inexistente, base vazia)."""
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (NOME_TABELA_BASE_IFC,)
    ).fetchone()
    if existe is None:
        raise ValueError(
            "Nenhuma base IFC ByTalhao importada. Importe a base em "
            "Configurações antes de gerar a simulação."
        )

    idade_maxima = obter_idade_maxima_manejo(conn)
    numero_minimo_arvores = obter_numero_minimo_arvores(conn)
    # Capturado aqui (não só validado) pra ser reaproveitado por
    # calcular_distribuicao_diametrica via contexto_lote, em vez de reler
    # de Configurações a cada cenário do lote.
    classes_diametricas = obter_classes_diametricas(conn)
    tipo_normalizacao_weibull = obter_tipo_normalizacao_weibull(conn)

    try:
        disponiveis = obter_intensidades_disponiveis(conn)
    except sqlite3.OperationalError:
        raise ValueError(
            "Nenhuma Simulação de Intensidades encontrada. Rode a simulação em "
            "Configurações antes de gerar esta simulação."
        )

    coluna_chave_weibull, df_weibull = obter_ajuste_weibull_por_talhao(conn)

    # Uma leitura só de colunas_base_ifc_talhao, reaproveitada em todas as
    # checagens de coluna mapeada abaixo (antes: uma consulta por checagem).
    colunas_disponiveis = colunas_base_ifc_talhao(conn)
    if coluna_talhao_ifc not in colunas_disponiveis:
        raise ValueError(f"A coluna de talhão \"{coluna_talhao_ifc}\" não existe na Base IFC ByTalhao.")

    if coluna_fustes_observados not in colunas_disponiveis:
        raise ValueError(
            f"A coluna de fustes observados \"{coluna_fustes_observados}\" não existe na Base IFC ByTalhao."
        )

    for rotulo, coluna in (
        ("DAP médio observado", coluna_dap_med_observado),
        ("DAP máximo observado", coluna_dap_max_observado),
        ("DAP mínimo observado", coluna_dap_min_observado),
        ("Ht observado", coluna_ht_observado),
        ("VTCC observado", coluna_vtcc_observado),
        ("CV do DAP observado", coluna_cv_dap_observado),
        ("Data de plantio", coluna_data_plantio),
    ):
        if coluna and coluna not in colunas_disponiveis:
            raise ValueError(f"A coluna de {rotulo} \"{coluna}\" não existe na Base IFC ByTalhao.")

    df_talhao = pd.read_sql_query(f'SELECT * FROM "{NOME_TABELA_BASE_IFC}"', conn)
    df_talhao = df_talhao.drop(columns=["id"], errors="ignore")

    if df_talhao.empty:
        raise ValueError("A base IFC ByTalhao está vazia.")

    talhoes_total = int(df_talhao.shape[0])
    colunas_originais = list(df_talhao.columns)

    reservados = {c.lower() for c in df_talhao.columns} | {"id"}
    coluna_forma = _nome_coluna_destino("forma", reservados)
    coluna_escala = _nome_coluna_destino("escala", reservados)
    coluna_fustes_observado = _nome_coluna_destino("fustes_observado", reservados)
    coluna_dap_med_observado_dest = _nome_coluna_destino("dap_med_observado", reservados)
    coluna_dap_max_observado_dest = _nome_coluna_destino("dap_max_observado", reservados)
    coluna_dap_min_observado_dest = _nome_coluna_destino("dap_min_observado", reservados)
    coluna_ht_observado_dest = _nome_coluna_destino("ht_observado", reservados)
    coluna_vtcc_observado_dest = _nome_coluna_destino("vtcc_observado", reservados)
    coluna_cv_dap_observado_dest = _nome_coluna_destino("cv_dap_observado", reservados)
    colunas_por_etapa = {
        sufixo: (
            _nome_coluna_destino(f"forma_{sufixo}", reservados),
            _nome_coluna_destino(f"escala_{sufixo}", reservados),
            _nome_coluna_destino(f"dap_med_{sufixo}", reservados),
            _nome_coluna_destino(f"dap_max_{sufixo}", reservados),
            _nome_coluna_destino(f"dap_min_{sufixo}", reservados),
            _nome_coluna_destino(f"ht_med_{sufixo}", reservados),
            _nome_coluna_destino(f"fustes_ha_removidos_{sufixo}", reservados),
            _nome_coluna_destino(f"vtcc_ha_removido_{sufixo}", reservados),
            _nome_coluna_destino(f"cv_dap_{sufixo}", reservados),
            _nome_coluna_destino(f"dg_{sufixo}", reservados),
            _nome_coluna_destino(f"truncado_esquerda_{sufixo}", reservados),
        )
        for sufixo in _SUFIXO_POR_ETAPA.values()
    }
    coluna_idade = _nome_coluna_destino("idade_simulada", reservados)
    coluna_ano_simulado = _nome_coluna_destino("ano_simulado", reservados)
    coluna_evento = _nome_coluna_destino("evento_manejo", reservados)
    coluna_intensidade = _nome_coluna_destino("intensidade_evento", reservados)
    coluna_remocao_absoluta = _nome_coluna_destino("remocao_absoluta", reservados)
    coluna_remocao_vtcc = _nome_coluna_destino("remocao_vtcc", reservados)
    coluna_forma_atual = _nome_coluna_destino("forma_atual", reservados)
    coluna_escala_atual = _nome_coluna_destino("escala_atual", reservados)
    coluna_dap_med_atual = _nome_coluna_destino("dap_med_atual", reservados)
    coluna_dap_max_atual = _nome_coluna_destino("dap_max_atual", reservados)
    coluna_dap_min_atual = _nome_coluna_destino("dap_min_atual", reservados)
    coluna_fustes_atual = _nome_coluna_destino("fustes_atual", reservados)
    coluna_ht_atual = _nome_coluna_destino("ht_atual", reservados)
    coluna_vtcc_atual = _nome_coluna_destino("vtcc_atual", reservados)
    coluna_cv_dap_atual = _nome_coluna_destino("cv_dap_atual", reservados)
    coluna_dg_atual = _nome_coluna_destino("dg_atual", reservados)
    coluna_truncado_esquerda_atual = _nome_coluna_destino("truncado_esquerda_atual", reservados)

    # Traz forma/escala do ajuste "Por Talhão", cruzando pelo valor da
    # coluna de talhão escolhida — LEFT JOIN, ver talhoes_sem_weibull.
    df_weibull_renomeado = df_weibull.rename(columns={coluna_chave_weibull: "__chave_weibull__"})
    df_talhao_com_weibull = (
        df_talhao.merge(df_weibull_renomeado, left_on=coluna_talhao_ifc,
                         right_on="__chave_weibull__", how="left")
        .drop(columns=["__chave_weibull__"])
        .rename(columns={"forma": coluna_forma, "escala": coluna_escala})
    )

    talhoes_com_weibull = int(df_talhao_com_weibull[coluna_forma].notna().sum())
    talhoes_sem_weibull = int(df_talhao.shape[0]) - talhoes_com_weibull

    # Fustes/ha observados (baseline antes de qualquer manejo), coluna
    # mapeada da própria Base IFC ByTalhao — texto livre, precisa de
    # conversão numérica tolerante (mesmo helper usado em intensidades.py).
    df_talhao_com_weibull[coluna_fustes_observado] = converter_numero(
        df_talhao_com_weibull[coluna_fustes_observados]
    )
    talhoes_sem_fustes_observado = int(df_talhao_com_weibull[coluna_fustes_observado].isna().sum())

    # DAP médio/máximo/mínimo observados (mesmo papel de baseline que
    # fustes_observado tem acima) — opcionais: coluna não mapeada vira
    # NaN pra todo mundo, preservando o comportamento anterior (vazio
    # antes do primeiro manejo) pra quem não tiver essa informação na
    # Base IFC ByTalhao.
    for coluna_origem, coluna_destino in (
        (coluna_dap_med_observado, coluna_dap_med_observado_dest),
        (coluna_dap_max_observado, coluna_dap_max_observado_dest),
        (coluna_dap_min_observado, coluna_dap_min_observado_dest),
        (coluna_ht_observado, coluna_ht_observado_dest),
        (coluna_vtcc_observado, coluna_vtcc_observado_dest),
        (coluna_cv_dap_observado, coluna_cv_dap_observado_dest),
    ):
        df_talhao_com_weibull[coluna_destino] = (
            converter_numero(df_talhao_com_weibull[coluna_origem]) if coluna_origem else np.nan
        )

    # Ano de plantio (baseline pra ano_simulado, ver mais abaixo, depois
    # do cross join com as idades) — mesmo padrão de opcional das colunas
    # acima: sem coluna mapeada, ou com data que converter_data não
    # consiga interpretar, vira NaN pra aquele talhão (não trava a
    # geração). Fica num nome de coluna fixo (não passa por
    # _nome_coluna_destino) porque é só temporário, nunca gravado —
    # mesmo raciocínio das colunas "__efetivo_*__"/"__remocao_*__" mais
    # abaixo.
    df_talhao_com_weibull["__ano_plantio__"] = (
        converter_data(df_talhao_com_weibull[coluna_data_plantio]).dt.year
        if coluna_data_plantio else np.nan
    )

    ajuste_manejo_ativo = obter_ajuste_manejo_padrao(conn)
    ano_referencia = obter_ano_referencia(conn)

    return {
        "idade_maxima": idade_maxima,
        "numero_minimo_arvores": numero_minimo_arvores,
        "classes_diametricas": classes_diametricas,
        "tipo_normalizacao_weibull": tipo_normalizacao_weibull,
        "disponiveis": disponiveis,
        "colunas_originais": colunas_originais,
        "talhoes_total": talhoes_total,
        "df_talhao_com_weibull": df_talhao_com_weibull,
        "talhoes_com_weibull": talhoes_com_weibull,
        "talhoes_sem_weibull": talhoes_sem_weibull,
        "talhoes_sem_fustes_observado": talhoes_sem_fustes_observado,
        "ajuste_manejo_ativo": ajuste_manejo_ativo,
        "ano_referencia": ano_referencia,
        "coluna_forma": coluna_forma,
        "coluna_escala": coluna_escala,
        "coluna_fustes_observado": coluna_fustes_observado,
        "coluna_dap_med_observado_dest": coluna_dap_med_observado_dest,
        "coluna_dap_max_observado_dest": coluna_dap_max_observado_dest,
        "coluna_dap_min_observado_dest": coluna_dap_min_observado_dest,
        "coluna_ht_observado_dest": coluna_ht_observado_dest,
        "coluna_vtcc_observado_dest": coluna_vtcc_observado_dest,
        "coluna_cv_dap_observado_dest": coluna_cv_dap_observado_dest,
        "colunas_por_etapa": colunas_por_etapa,
        "coluna_idade": coluna_idade,
        "coluna_ano_simulado": coluna_ano_simulado,
        "coluna_evento": coluna_evento,
        "coluna_intensidade": coluna_intensidade,
        "coluna_remocao_absoluta": coluna_remocao_absoluta,
        "coluna_remocao_vtcc": coluna_remocao_vtcc,
        "coluna_forma_atual": coluna_forma_atual,
        "coluna_escala_atual": coluna_escala_atual,
        "coluna_dap_med_atual": coluna_dap_med_atual,
        "coluna_dap_max_atual": coluna_dap_max_atual,
        "coluna_dap_min_atual": coluna_dap_min_atual,
        "coluna_fustes_atual": coluna_fustes_atual,
        "coluna_ht_atual": coluna_ht_atual,
        "coluna_vtcc_atual": coluna_vtcc_atual,
        "coluna_cv_dap_atual": coluna_cv_dap_atual,
        "coluna_dg_atual": coluna_dg_atual,
        "coluna_truncado_esquerda_atual": coluna_truncado_esquerda_atual,
    }


def preparar_contexto_lote(
    conn: sqlite3.Connection,
    coluna_talhao_ifc: str,
    coluna_fustes_observados: str,
    coluna_dap_med_observado: Optional[str] = None,
    coluna_dap_max_observado: Optional[str] = None,
    coluna_dap_min_observado: Optional[str] = None,
    coluna_ht_observado: Optional[str] = None,
    coluna_vtcc_observado: Optional[str] = None,
    coluna_cv_dap_observado: Optional[str] = None,
    coluna_data_plantio: Optional[str] = None,
) -> Dict:
    """Monta, UMA VEZ para o lote inteiro, tudo que `gerar_populacao`/
    `calcular_distribuicao_diametrica` normalmente recalculariam a cada
    cenário — usado pelo modo "Múltiplos cenários"
    (`app/screens/simulacao.py:_ThreadGerarLote`), que chama isto antes do
    laço e passa o resultado (`contexto_lote=...`) pra cada chamada de
    `gerar_populacao` dentro do lote. Também inclui um cache vazio pro
    ajuste Weibull "Por Simulação" (`obter_ajuste_weibull_por_simulacao`),
    que varia por intensidade mas se repete entre cenários que testam
    idades diferentes na MESMA combinação de intensidades — `gerar_populacao`
    popula esse cache sob demanda (uma leitura por combinação de
    intensidade distinta, não uma por cenário).

    Coluna mapeada errada aqui levanta o mesmo ValueError de sempre — só
    que uma vez só, antes do lote inteiro começar, em vez de no meio do
    primeiro cenário."""
    return {
        "baseline": _preparar_baseline_populacao(
            conn, coluna_talhao_ifc, coluna_fustes_observados,
            coluna_dap_med_observado, coluna_dap_max_observado, coluna_dap_min_observado,
            coluna_ht_observado, coluna_vtcc_observado, coluna_cv_dap_observado, coluna_data_plantio,
        ),
        "cache_weibull_simulacao": {},
    }


def _persistir_populacao(
    conn: sqlite3.Connection, tabela_populacao: str, tabela_distribuicao: str,
    create_table_sql: str, populacao: pd.DataFrame, colunas_insert: list,
    colunas_extra: Optional[list] = None, commit: bool = True,
) -> None:
    """DROP+CREATE+INSERT de `tabela_populacao` a partir do DataFrame já
    montado — extraído de gerar_populacao pra poder ser chamado
    separadamente do cálculo (ver `persistir` nela, e
    app/screens/simulacao.py:_gerar_uma_simulacao, que calcula várias
    etapas em memória e só grava tudo no final). Dropa `tabela_distribuicao`
    junto (mesma ordem de sempre — FK dela aponta pra `tabela_populacao`,
    que está sendo recriada).

    `colunas_extra` (opcional): nomes de colunas presentes em `populacao`
    ALÉM de `colunas_insert` — saídas de construtores de variáveis
    mescladas em memória (ver
    app/core/construtores.py:aplicar_construtores_em_memoria) DEPOIS que
    `create_table_sql`/`colunas_insert` foram montados por
    gerar_populacao, então não estão neles ainda. Entram no CREATE TABLE
    como REAL (mesmo tipo que `ALTER TABLE ADD COLUMN` já usava pra elas
    no caminho antigo, via gravar_saidas_como_colunas) e no INSERT, além
    das colunas "nativas" da população."""
    conn.execute(f'DROP TABLE IF EXISTS "{tabela_distribuicao}"')
    conn.execute(f'DROP TABLE IF EXISTS "{tabela_populacao}"')
    if colunas_extra:
        colunas_extra_sql = ", ".join(f'"{c}" REAL' for c in colunas_extra)
        create_table_sql = create_table_sql.rstrip()
        assert create_table_sql.endswith(")"), "create_table_sql em formato inesperado"
        create_table_sql = create_table_sql[:-1] + f', {colunas_extra_sql})'
        colunas_insert = list(colunas_insert) + list(colunas_extra)
    conn.execute(create_table_sql)
    nomes_insert = ", ".join(f'"{c}"' for c in colunas_insert)
    marcadores = ", ".join("?" for _ in colunas_insert)
    linhas = [
        tuple(None if pd.isna(v) else v for v in linha)
        for linha in populacao[colunas_insert].itertuples(index=False, name=None)
    ]
    conn.executemany(
        f'INSERT INTO "{tabela_populacao}" ({nomes_insert}) VALUES ({marcadores})', linhas
    )
    if commit:
        conn.commit()


def gerar_populacao(
    conn: sqlite3.Connection,
    coluna_talhao_ifc: str,
    coluna_fustes_observados: str,
    idade_raleio: int, intensidade_raleio: float,
    idade_desbaste_1: int, intensidade_desbaste_1: float,
    idade_desbaste_2: int, intensidade_desbaste_2: float,
    idade_corte_raso: int,
    coluna_dap_med_observado: Optional[str] = None,
    coluna_dap_max_observado: Optional[str] = None,
    coluna_dap_min_observado: Optional[str] = None,
    coluna_ht_observado: Optional[str] = None,
    coluna_vtcc_observado: Optional[str] = None,
    coluna_cv_dap_observado: Optional[str] = None,
    coluna_data_plantio: Optional[str] = None,
    sufixo_tabela: str = "",
    contexto_lote: Optional[Dict] = None,
    persistir: bool = True,
) -> Dict:
    """Recria `simulacao_talhao_idade` e `simulacao_distribuicao_diametrica`:
    cada linha de base_ifc_talhao repetida pra cada idade de 1 até a
    idade máxima de manejo, marcando em qual idade cai cada intervenção
    (Raleio/1º Desbaste/2º Desbaste/Corte Raso), trazendo forma/escala do
    ajuste Weibull "Por Talhão" e "Por Simulação" pra cada talhão (LEFT
    JOIN, cruzado por `coluna_talhao_ifc` — talhão sem correspondência
    fica com forma/escala nulos em vez de travar a geração), aplicando a
    guarda de fustes/ha mínimo (cada manejo só é de fato aplicado se
    fustes observados/vigentes menos os removidos naquela etapa não
    caírem abaixo de `numero_minimo_arvores_ha`; senão, o manejo é
    pulado e o estado da etapa anterior é congelado — os manejos
    seguintes continuam sendo avaliados normalmente contra esse estado
    congelado), escolhendo qual estado está em vigor em cada idade e
    usando forma_atual/escala_atual pra calcular a distribuição
    diamétrica Weibull nas classes configuradas. Corte Raso não tem
    intensidade nem ajuste Weibull próprio — é só uma idade de evento;
    o estado em vigor após o 2º Desbaste permanece em vigor através dele.

    `coluna_dap_med_observado`/`coluna_dap_max_observado`/
    `coluna_dap_min_observado`/`coluna_ht_observado` são opcionais (mesmo
    papel que `coluna_fustes_observados` tem pra fustes_atual): se
    mapeadas, dap_med_atual/dap_max_atual/dap_min_atual/ht_atual vêm
    preenchidos com esse valor observado antes do primeiro manejo, em vez
    de vazios. A partir do primeiro manejo, cada uma dessas passa a vir
    de `<campo>_apos_<etapa>` (substituição direta, não acumulada).

    `coluna_vtcc_observado` também é opcional, mas `vtcc_atual` funciona
    diferente: começa no valor observado (volume/ha antes de qualquer
    manejo) e, a cada etapa que passa pela guarda de fustes/ha mínimo,
    SUBTRAI `vtcc_ha_removido_apos_<etapa>` do valor vigente — mesmo
    tratamento acumulado que `fustes_atual` já tem (removido reduz o que
    tava em pé), em vez da substituição direta usada pelos campos acima.

    `coluna_cv_dap_observado` também é opcional, mesmo tratamento de
    substituição direta que `coluna_ht_observado` tem: se mapeada,
    `cv_dap_atual` vem preenchido com esse valor antes do primeiro manejo
    em vez de ficar vazio (sem coluna mapeada, `cv_dap_atual` continua NaN
    antes do primeiro manejo, como sempre foi).

    `coluna_data_plantio` também é opcional: se mapeada, `ano_simulado`
    (ao lado de `idade_simulada`) vem preenchido com ano(data de plantio)
    + idade_simulada daquela linha — o ano-calendário em que o talhão
    tinha aquela idade. Sem coluna mapeada, ou com data que não dá pra
    interpretar, fica vazio.

    Persiste `coluna_talhao_ifc`/`coluna_fustes_observados`/as colunas de
    DAP/Ht/VTCC/CV do DAP observado/data de plantio como a escolha atual.

    `sufixo_tabela` (ex: "__cenario3"), se passado, grava em
    "simulacao_talhao_idade{sufixo_tabela}"/
    "simulacao_distribuicao_diametrica{sufixo_tabela}" em vez dos nomes
    canônicos — usado pelo modo "Múltiplos cenários" da tela Simulação
    (app/screens/simulacao.py) pra gerar cada cenário numa tabela própria,
    sem mexer no resultado "ativo" até o usuário chamar ativar_cenario.

    `persistir` (padrão True): com False, pula a gravação de
    `tabela_populacao` (e a de `tabela_distribuicao`, repassado como
    `persistir` pra calcular_distribuicao_diametrica) — o resultado sai
    com "_df_populacao" (o DataFrame pronto, com "id" atribuído) e o
    necessário pra gravar depois sem recalcular ("_tabela_populacao",
    "_create_table_sql", "_colunas_insert", "_linhas_distribuicao",
    "_tabela_distribuicao" — ver _persistir_populacao/
    _persistir_distribuicao). Usado pelo pipeline de geração em memória
    (ver app/screens/simulacao.py:_gerar_uma_simulacao)."""
    tabela_populacao = TABELA_POPULACAO + sufixo_tabela
    tabela_distribuicao = TABELA_DISTRIBUICAO + sufixo_tabela

    # Tudo que NÃO depende da idade/intensidade deste cenário específico —
    # ver _preparar_baseline_populacao. contexto_lote=None (cenário único):
    # recalculado aqui mesmo, comportamento idêntico a antes deste
    # parâmetro existir. contexto_lote fornecido (modo "Múltiplos
    # cenários"): já vem pronto de preparar_contexto_lote, calculado uma
    # vez só pro lote inteiro em vez de a cada cenário (ver
    # app/screens/simulacao.py:_ThreadGerarLote).
    baseline = (
        contexto_lote["baseline"] if contexto_lote is not None
        else _preparar_baseline_populacao(
            conn, coluna_talhao_ifc, coluna_fustes_observados,
            coluna_dap_med_observado, coluna_dap_max_observado, coluna_dap_min_observado,
            coluna_ht_observado, coluna_vtcc_observado, coluna_cv_dap_observado, coluna_data_plantio,
        )
    )
    idade_maxima = baseline["idade_maxima"]
    numero_minimo_arvores = baseline["numero_minimo_arvores"]
    disponiveis = baseline["disponiveis"]

    for rotulo, idade in (
        ("Raleio", idade_raleio), ("1º Desbaste", idade_desbaste_1),
        ("2º Desbaste", idade_desbaste_2), ("Corte Raso", idade_corte_raso),
    ):
        if idade < 1 or idade > idade_maxima:
            raise ValueError(
                f"A idade do {rotulo} ({idade}) precisa estar entre 1 e a idade máxima de manejo "
                f"({idade_maxima:g})."
            )

    if not (idade_raleio < idade_desbaste_1 < idade_desbaste_2 < idade_corte_raso):
        raise ValueError(
            "As idades precisam ser crescentes: Raleio < 1º Desbaste < 2º Desbaste < Corte Raso."
        )

    for chave_intensidade, rotulo, valor in (
        ("int_raleio", "Raleio", intensidade_raleio),
        ("int_desbaste_1", "1º Desbaste", intensidade_desbaste_1),
        ("int_desbaste_2", "2º Desbaste", intensidade_desbaste_2),
    ):
        if not any(abs(valor - v) < 1e-9 for v in disponiveis[chave_intensidade]):
            raise ValueError(
                f"A intensidade do {rotulo} ({valor:g}) não é um valor testado na Simulação de "
                "Intensidades."
            )

    # df_weibull_simulacao varia por intensidade (não por idade nem pelo
    # resto do mapeamento) — no lote, cacheado por (int_raleio,
    # int_desbaste_1, int_desbaste_2) em contexto_lote, já que várias
    # combinações de idade testadas no mesmo lote costumam repetir a mesma
    # tripla de intensidade (ver preparar_contexto_lote).
    if contexto_lote is not None:
        cache_weibull_simulacao = contexto_lote["cache_weibull_simulacao"]
        chave_cache = (intensidade_raleio, intensidade_desbaste_1, intensidade_desbaste_2)
        if chave_cache not in cache_weibull_simulacao:
            cache_weibull_simulacao[chave_cache] = obter_ajuste_weibull_por_simulacao(
                conn, intensidade_raleio, intensidade_desbaste_1, intensidade_desbaste_2
            )
        df_weibull_simulacao = cache_weibull_simulacao[chave_cache]
    else:
        df_weibull_simulacao = obter_ajuste_weibull_por_simulacao(
            conn, intensidade_raleio, intensidade_desbaste_1, intensidade_desbaste_2
        )

    # .copy() pra nunca mutar o DataFrame guardado em contexto_lote — as
    # colunas "__idade_*_final__"/etc. montadas mais abaixo são atribuídas
    # por coluna (df[...] = ...), que mutaria em lugar o objeto
    # compartilhado entre todos os cenários do lote sem essa cópia.
    df_talhao_com_weibull = baseline["df_talhao_com_weibull"].copy()
    coluna_forma = baseline["coluna_forma"]
    coluna_escala = baseline["coluna_escala"]
    coluna_fustes_observado = baseline["coluna_fustes_observado"]
    coluna_dap_med_observado_dest = baseline["coluna_dap_med_observado_dest"]
    coluna_dap_max_observado_dest = baseline["coluna_dap_max_observado_dest"]
    coluna_dap_min_observado_dest = baseline["coluna_dap_min_observado_dest"]
    coluna_ht_observado_dest = baseline["coluna_ht_observado_dest"]
    coluna_vtcc_observado_dest = baseline["coluna_vtcc_observado_dest"]
    coluna_cv_dap_observado_dest = baseline["coluna_cv_dap_observado_dest"]
    colunas_por_etapa = baseline["colunas_por_etapa"]
    coluna_idade = baseline["coluna_idade"]
    coluna_ano_simulado = baseline["coluna_ano_simulado"]
    coluna_evento = baseline["coluna_evento"]
    coluna_intensidade = baseline["coluna_intensidade"]
    coluna_remocao_absoluta = baseline["coluna_remocao_absoluta"]
    coluna_remocao_vtcc = baseline["coluna_remocao_vtcc"]
    coluna_forma_atual = baseline["coluna_forma_atual"]
    coluna_escala_atual = baseline["coluna_escala_atual"]
    coluna_dap_med_atual = baseline["coluna_dap_med_atual"]
    coluna_dap_max_atual = baseline["coluna_dap_max_atual"]
    coluna_dap_min_atual = baseline["coluna_dap_min_atual"]
    coluna_fustes_atual = baseline["coluna_fustes_atual"]
    coluna_ht_atual = baseline["coluna_ht_atual"]
    coluna_vtcc_atual = baseline["coluna_vtcc_atual"]
    coluna_cv_dap_atual = baseline["coluna_cv_dap_atual"]
    coluna_dg_atual = baseline["coluna_dg_atual"]
    coluna_truncado_esquerda_atual = baseline["coluna_truncado_esquerda_atual"]
    colunas_originais = baseline["colunas_originais"]
    talhoes_total = baseline["talhoes_total"]
    talhoes_com_weibull = baseline["talhoes_com_weibull"]
    talhoes_sem_weibull = baseline["talhoes_sem_weibull"]
    talhoes_sem_fustes_observado = baseline["talhoes_sem_fustes_observado"]

    # Ajuste de manejo (opcional, ligado em Configurações): empurra a idade
    # de Raleio/1º Desbaste/2º Desbaste (não Corte Raso, fora do escopo
    # confirmado) pra frente, por talhão, se a idade configurada cair num
    # ano-calendário anterior ao ano_referencia — como não dá pra "voltar no
    # tempo" pra fazer o manejo, a idade efetiva vira a idade mínima que cai
    # em ano_referencia ou depois (usando o ano de plantio do próprio
    # talhão, __ano_plantio__ acima). Cada evento é ajustado em cascata
    # (Desbaste 1 nunca antes do Raleio ajustado, Desbaste 2 nunca antes do
    # Desbaste 1 ajustado) pra preservar a ordem estritamente crescente que
    # a validação de idades já garante nos valores configurados — sem isso,
    # dois eventos poderiam colidir na mesma idade ajustada. Sem
    # __ano_plantio__ (talhão sem data de plantio mapeada) ou com a opção
    # desligada, a idade efetiva fica igual à configurada — mesmo
    # comportamento de sempre, igual pra todo talhão. A intensidade não
    # muda, só a idade em que o evento acontece; se a idade ajustada passar
    # da idade máxima de manejo, o evento simplesmente não cai em nenhuma
    # linha simulada (sem erro, ver `idades`/cross join mais abaixo).
    ajuste_manejo_ativo = baseline["ajuste_manejo_ativo"]
    ano_referencia = baseline["ano_referencia"]
    ano_plantio = df_talhao_com_weibull["__ano_plantio__"]

    def _idade_ajustada(idade_config: int) -> pd.Series:
        if not ajuste_manejo_ativo or ano_referencia is None:
            return pd.Series(float(idade_config), index=df_talhao_com_weibull.index)
        atraso = ano_referencia - (ano_plantio + idade_config)
        empurrao = np.where(ano_plantio.notna() & (atraso > 0), atraso, 0.0)
        return pd.Series(float(idade_config), index=df_talhao_com_weibull.index) + empurrao

    df_talhao_com_weibull["__idade_raleio_final__"] = _idade_ajustada(idade_raleio)
    df_talhao_com_weibull["__idade_desbaste_1_final__"] = np.maximum(
        _idade_ajustada(idade_desbaste_1).to_numpy(),
        df_talhao_com_weibull["__idade_raleio_final__"].to_numpy() + 1,
    )
    df_talhao_com_weibull["__idade_desbaste_2_final__"] = np.maximum(
        _idade_ajustada(idade_desbaste_2).to_numpy(),
        df_talhao_com_weibull["__idade_desbaste_1_final__"].to_numpy() + 1,
    )

    # Traz forma/escala/dap_med/dap_max/dap_min/ht_med/fustes_ha_removidos/
    # vtcc_ha_removido do ajuste "Por Simulação" pra cada etapa (Raleio,
    # 1º Desbaste, 2º Desbaste), cruzando pelo mesmo valor de talhão —
    # LEFT JOIN também, ver talhoes_com_weibull_por_etapa.
    df_weibull_simulacao_renomeado = df_weibull_simulacao.rename(
        columns={"talhao": "__chave_weibull_simulacao__"}
    )
    for sufixo, (coluna_f, coluna_e, coluna_dm, coluna_dx, coluna_dn, coluna_hm, coluna_fr, coluna_vr,
                 coluna_cv, coluna_dg, coluna_te) \
            in colunas_por_etapa.items():
        df_weibull_simulacao_renomeado = df_weibull_simulacao_renomeado.rename(
            columns={
                f"forma_{sufixo}": coluna_f, f"escala_{sufixo}": coluna_e,
                f"dap_med_{sufixo}": coluna_dm, f"dap_max_{sufixo}": coluna_dx,
                f"dap_min_{sufixo}": coluna_dn, f"ht_med_{sufixo}": coluna_hm,
                f"fustes_ha_removidos_{sufixo}": coluna_fr, f"vtcc_ha_removido_{sufixo}": coluna_vr,
                f"cv_dap_{sufixo}": coluna_cv, f"dg_{sufixo}": coluna_dg,
                f"truncado_esquerda_{sufixo}": coluna_te,
            }
        )
    df_talhao_com_weibull = (
        df_talhao_com_weibull.merge(
            df_weibull_simulacao_renomeado, left_on=coluna_talhao_ifc,
            right_on="__chave_weibull_simulacao__", how="left")
        .drop(columns=["__chave_weibull_simulacao__"])
    )

    talhoes_com_weibull_por_etapa = {
        sufixo: int(df_talhao_com_weibull[colunas[0]].notna().sum())
        for sufixo, colunas in colunas_por_etapa.items()
    }

    # ------------------------------------------------------------
    # Guarda de fustes/ha mínimo: em cada etapa (Raleio, 1º Desbaste, 2º
    # Desbaste — Corte Raso fica de fora, não tem intensidade/remoção
    # própria), a candidata a fustes_atual é o fustes vigente antes da
    # etapa menos os fustes/ha removidos nela; se cair abaixo do mínimo
    # configurado, a etapa é "pulada": forma/escala/dap_med/dap_max/
    # dap_min/fustes_atual ficam iguais aos vigentes antes dela. Cada
    # etapa é avaliada na sua vez, usando o que "sobrou" da etapa
    # anterior (pulada ou não) — não é um corte all-or-nothing. Um
    # talhão sem ajuste "Por Simulação" numa etapa, ou sem fustes
    # observado numérico, naturalmente reprova a guarda nessa etapa
    # (comparação com NaN é sempre False), sem checagem extra.
    # ------------------------------------------------------------
    forma_vigente = df_talhao_com_weibull[coluna_forma]
    escala_vigente = df_talhao_com_weibull[coluna_escala]
    dap_med_vigente = df_talhao_com_weibull[coluna_dap_med_observado_dest]
    dap_max_vigente = df_talhao_com_weibull[coluna_dap_max_observado_dest]
    dap_min_vigente = df_talhao_com_weibull[coluna_dap_min_observado_dest]
    ht_med_vigente = df_talhao_com_weibull[coluna_ht_observado_dest]
    fustes_vigente = df_talhao_com_weibull[coluna_fustes_observado]
    vtcc_vigente = df_talhao_com_weibull[coluna_vtcc_observado_dest]
    cv_dap_vigente = df_talhao_com_weibull[coluna_cv_dap_observado_dest]
    # dg não tem baseline observado (nenhuma coluna da Base IFC é mapeada
    # pra isso, ao contrário de dap_med/dap_max/dap_min/ht/vtcc/cv_dap
    # observados) — antes do primeiro manejo aprovado pela guarda, fica
    # NaN, mesmo comportamento que os campos acima já têm quando a coluna
    # observada correspondente não é mapeada (ver loop acima).
    dg_vigente = pd.Series(np.nan, index=df_talhao_com_weibull.index)
    # truncado_esquerda: mesmo raciocínio de dg acima (sem baseline
    # observado) — antes do primeiro manejo aprovado pela guarda, é
    # sempre 0/False (a Weibull "Por Talhão" nunca é truncada, ver
    # weibull_ifc.ajustar_por_chave).
    truncado_esquerda_vigente = pd.Series(0, index=df_talhao_com_weibull.index)

    talhoes_manejo_pulado_por_etapa = {}
    colunas_efetivas_por_etapa = {}
    colunas_remocao_por_etapa = {}
    intensidades_por_etapa = {
        "apos_raleio": intensidade_raleio,
        "apos_desbaste_1": intensidade_desbaste_1,
        "apos_desbaste_2": intensidade_desbaste_2,
    }

    for sufixo in ("apos_raleio", "apos_desbaste_1", "apos_desbaste_2"):
        coluna_f, coluna_e, coluna_dm, coluna_dx, coluna_dn, coluna_hm, coluna_fr, coluna_vr, \
            coluna_cv, coluna_dg, coluna_te = colunas_por_etapa[sufixo]

        fustes_candidato = fustes_vigente - df_talhao_com_weibull[coluna_fr]
        # Intensidade zero representa manejo não executado. O ajuste "Por
        # Simulação" pode conter um par Weibull próprio para essa etapa,
        # mas ele não deve substituir o estado vigente quando nenhuma
        # árvore foi removida. Trate a etapa inteira como pulada; assim
        # forma/escala e os demais atributos continuam exatamente iguais
        # aos da etapa anterior.
        manejo_executado = abs(float(intensidades_por_etapa[sufixo])) > 1e-12
        guarda_ok = (
            (fustes_candidato >= numero_minimo_arvores).to_numpy()
            if manejo_executado
            else np.zeros(len(df_talhao_com_weibull), dtype=bool)
        )

        # vtcc_atual não vem de um valor "por etapa" pronto (como ht_med
        # vem de ht_med_apos_<etapa>) — é o volume vigente MENOS o que foi
        # removido nesta etapa, mesmo raciocínio de fustes_candidato acima
        # (removido reduz o que tava em pé).
        vtcc_candidato = vtcc_vigente - df_talhao_com_weibull[coluna_vr]

        indice = df_talhao_com_weibull.index
        forma_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_f], forma_vigente), index=indice)
        escala_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_e], escala_vigente), index=indice)
        dap_med_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_dm], dap_med_vigente), index=indice)
        dap_max_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_dx], dap_max_vigente), index=indice)
        dap_min_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_dn], dap_min_vigente), index=indice)
        ht_med_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_hm], ht_med_vigente), index=indice)
        fustes_vigente = pd.Series(
            np.where(guarda_ok, fustes_candidato, fustes_vigente), index=indice)
        vtcc_vigente = pd.Series(
            np.where(guarda_ok, vtcc_candidato, vtcc_vigente), index=indice)
        cv_dap_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_cv], cv_dap_vigente), index=indice)
        dg_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_dg], dg_vigente), index=indice)
        truncado_esquerda_vigente = pd.Series(
            np.where(guarda_ok, df_talhao_com_weibull[coluna_te], truncado_esquerda_vigente),
            index=indice)

        talhoes_manejo_pulado_por_etapa[sufixo] = int((~guarda_ok).sum())

        nomes_temp = tuple(
            f"__efetivo_{campo}_{sufixo}__"
            for campo in (
                "forma", "escala", "dap_med", "dap_max", "dap_min", "ht_med", "fustes", "vtcc",
                "cv_dap", "dg", "truncado_esquerda",
            )
        )
        for nome_temp, serie in zip(
            nomes_temp,
            (forma_vigente, escala_vigente, dap_med_vigente, dap_max_vigente, dap_min_vigente,
             ht_med_vigente, fustes_vigente, vtcc_vigente, cv_dap_vigente, dg_vigente,
             truncado_esquerda_vigente),
        ):
            df_talhao_com_weibull[nome_temp] = serie
        colunas_efetivas_por_etapa[sufixo] = nomes_temp

        # Remoção REALMENTE efetivada nesta etapa (0, não o valor teórico
        # de fustes_ha_removidos_<etapa>/vtcc_ha_removido_<etapa>, quando a
        # guarda de fustes/ha mínimo pulou o manejo) — é o que vira
        # remocao_absoluta/remocao_vtcc na linha do evento correspondente
        # mais abaixo (ver populacao[coluna_remocao_absoluta]).
        nome_temp_remocao_fustes = f"__remocao_fustes_{sufixo}__"
        nome_temp_remocao_vtcc = f"__remocao_vtcc_{sufixo}__"
        df_talhao_com_weibull[nome_temp_remocao_fustes] = np.where(
            guarda_ok, df_talhao_com_weibull[coluna_fr], 0.0)
        df_talhao_com_weibull[nome_temp_remocao_vtcc] = np.where(
            guarda_ok, df_talhao_com_weibull[coluna_vr], 0.0)
        colunas_remocao_por_etapa[sufixo] = (nome_temp_remocao_fustes, nome_temp_remocao_vtcc)

    idades = list(range(1, int(idade_maxima) + 1))
    df_idades = pd.DataFrame({coluna_idade: idades})

    # Cross join: cada linha (já com forma/escala/idades finais de
    # Raleio/Desbaste 1/Desbaste 2, ver __idade_*_final__ acima) pareada com
    # cada idade — mantém a ordem natural "por talhão, idade crescente".
    populacao = df_talhao_com_weibull.merge(df_idades, how="cross")

    # condicoes_evento_manejo compara contra a idade FINAL de cada talhão
    # (igual à idade configurada quando o ajuste de manejo está desligado,
    # ou sem data de plantio mapeada) — reaproveitada tanto pra marcar o
    # evento/intensidade da linha quanto, mais abaixo, pra remocao_absoluta/
    # remocao_vtcc. Corte Raso fica de fora (idade fixa, fora do escopo do
    # ajuste de manejo).
    condicoes_evento_manejo = [
        populacao[coluna_idade] == populacao["__idade_raleio_final__"],
        populacao[coluna_idade] == populacao["__idade_desbaste_1_final__"],
        populacao[coluna_idade] == populacao["__idade_desbaste_2_final__"],
    ]
    populacao[coluna_evento] = np.select(
        condicoes_evento_manejo + [populacao[coluna_idade] == idade_corte_raso],
        [EVENTO_RALEIO, EVENTO_DESBASTE_1, EVENTO_DESBASTE_2, EVENTO_CORTE_RASO],
        default=None,
    )
    populacao[coluna_intensidade] = np.select(
        condicoes_evento_manejo,
        [float(intensidade_raleio), float(intensidade_desbaste_1), float(intensidade_desbaste_2)],
        default=np.nan,
    )

    # ano_simulado: ano-calendário em que o talhão tinha aquela
    # idade_simulada, contando a partir do ano de plantio (ano do plantio
    # + idade_simulada). Fica vazio se a data de plantio não foi mapeada
    # (ou não deu pra interpretar) pra aquele talhão.
    populacao[coluna_ano_simulado] = populacao["__ano_plantio__"] + populacao[coluna_idade]

    # custo_formacao NÃO é calculado aqui — virou um nó "Custo de Formação"
    # do Construtor de Variáveis (ver core/construtores.py, ramo
    # "custo_formacao" de avaliar_grafo), reaplicado automaticamente logo
    # depois de gerar_populacao (ver app/screens/simulacao.py:gerar) se o
    # usuário salvou um construtor com esse nó — soma (R$/ha) de todo
    # custo de formação florestal (tela Configurações, tabela
    # `custos_formacao`) cujo `ano` (idade do povoamento) bate com
    # idade_simulada da linha, mesma regra de sempre.

    # remocao_absoluta (fustes/ha) e remocao_vtcc (m³/ha): quanto foi
    # REALMENTE removido no evento daquela linha — diferente de
    # intensidade_evento (fração pretendida, igual pra todo talhão),
    # varia por talhão (cada um tem uma densidade/volume vigente
    # diferente) e já é 0 se a guarda de fustes/ha mínimo pulou o manejo
    # (ver colunas_remocao_por_etapa acima). NaN no Corte Raso e nas
    # idades sem evento, mesmo padrão de coluna_intensidade.
    coluna_remocao_fustes_r, coluna_remocao_vtcc_r = colunas_remocao_por_etapa["apos_raleio"]
    coluna_remocao_fustes_d1, coluna_remocao_vtcc_d1 = colunas_remocao_por_etapa["apos_desbaste_1"]
    coluna_remocao_fustes_d2, coluna_remocao_vtcc_d2 = colunas_remocao_por_etapa["apos_desbaste_2"]
    populacao[coluna_remocao_absoluta] = np.select(
        condicoes_evento_manejo,
        [populacao[coluna_remocao_fustes_r], populacao[coluna_remocao_fustes_d1],
         populacao[coluna_remocao_fustes_d2]],
        default=np.nan,
    )
    populacao[coluna_remocao_vtcc] = np.select(
        condicoes_evento_manejo,
        [populacao[coluna_remocao_vtcc_r], populacao[coluna_remocao_vtcc_d1],
         populacao[coluna_remocao_vtcc_d2]],
        default=np.nan,
    )

    # Estado "em vigor" em cada idade: antes do Raleio, o original ("Por
    # Talhão", fustes_atual = fustes observado, dap_*_atual = DAP
    # observado se mapeado, senão NaN); a partir de cada manejo
    # (inclusive), o valor "efetivo" daquela etapa
    # (já passou pela guarda de fustes/ha mínimo acima). Como
    # idade_corte_raso > idade_desbaste_2 é garantido pela validação, o
    # Corte Raso sempre cai no bucket default (pós-2º-desbaste) — o
    # estado permanece em vigor através dele, sem precisar de bucket
    # próprio.
    (coluna_forma_ef_r, coluna_escala_ef_r, coluna_dm_ef_r, coluna_dx_ef_r,
     coluna_dn_ef_r, coluna_hm_ef_r, coluna_fu_ef_r, coluna_vt_ef_r,
     coluna_cv_ef_r, coluna_dg_ef_r, coluna_te_ef_r) = colunas_efetivas_por_etapa["apos_raleio"]
    (coluna_forma_ef_d1, coluna_escala_ef_d1, coluna_dm_ef_d1, coluna_dx_ef_d1,
     coluna_dn_ef_d1, coluna_hm_ef_d1, coluna_fu_ef_d1, coluna_vt_ef_d1,
     coluna_cv_ef_d1, coluna_dg_ef_d1, coluna_te_ef_d1) = \
        colunas_efetivas_por_etapa["apos_desbaste_1"]
    (coluna_forma_ef_d2, coluna_escala_ef_d2, coluna_dm_ef_d2, coluna_dx_ef_d2,
     coluna_dn_ef_d2, coluna_hm_ef_d2, coluna_fu_ef_d2, coluna_vt_ef_d2,
     coluna_cv_ef_d2, coluna_dg_ef_d2, coluna_te_ef_d2) = \
        colunas_efetivas_por_etapa["apos_desbaste_2"]

    condicoes_idade = [
        populacao[coluna_idade] < populacao["__idade_raleio_final__"],
        populacao[coluna_idade] < populacao["__idade_desbaste_1_final__"],
        populacao[coluna_idade] < populacao["__idade_desbaste_2_final__"],
    ]
    populacao[coluna_forma_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_forma], populacao[coluna_forma_ef_r], populacao[coluna_forma_ef_d1]],
        default=populacao[coluna_forma_ef_d2],
    )
    populacao[coluna_escala_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_escala], populacao[coluna_escala_ef_r], populacao[coluna_escala_ef_d1]],
        default=populacao[coluna_escala_ef_d2],
    )
    populacao[coluna_dap_med_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_dap_med_observado_dest], populacao[coluna_dm_ef_r], populacao[coluna_dm_ef_d1]],
        default=populacao[coluna_dm_ef_d2],
    )
    populacao[coluna_dap_max_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_dap_max_observado_dest], populacao[coluna_dx_ef_r], populacao[coluna_dx_ef_d1]],
        default=populacao[coluna_dx_ef_d2],
    )
    populacao[coluna_dap_min_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_dap_min_observado_dest], populacao[coluna_dn_ef_r], populacao[coluna_dn_ef_d1]],
        default=populacao[coluna_dn_ef_d2],
    )
    populacao[coluna_fustes_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_fustes_observado], populacao[coluna_fu_ef_r], populacao[coluna_fu_ef_d1]],
        default=populacao[coluna_fu_ef_d2],
    )
    # ht_atual: substituição direta por etapa, mesmo padrão de dap_med_atual
    # acima (ht_med_apos_<etapa> já é a média das remanescentes daquela
    # etapa, não precisa de subtração).
    populacao[coluna_ht_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_ht_observado_dest], populacao[coluna_hm_ef_r], populacao[coluna_hm_ef_d1]],
        default=populacao[coluna_hm_ef_d2],
    )
    # vtcc_atual: mesmo padrão de fustes_atual acima (coluna_vt_ef_* já é
    # o valor vigente PÓS-subtração do removido na etapa, calculado no
    # laço da guarda de fustes/ha mínimo).
    populacao[coluna_vtcc_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_vtcc_observado_dest], populacao[coluna_vt_ef_r], populacao[coluna_vt_ef_d1]],
        default=populacao[coluna_vt_ef_d2],
    )
    # cv_dap_atual/dg_atual: substituição direta por etapa, mesmo padrão de
    # ht_atual/dap_med_atual acima. cv_dap_atual usa a baseline observada
    # (coluna_cv_dap_observado_dest, NaN se não mapeada — mesmo padrão de
    # ht_atual/dap_med_atual); dg_atual não tem baseline observado (ver
    # dg_vigente), então antes do Raleio fica NaN.
    populacao[coluna_cv_dap_atual] = np.select(
        condicoes_idade,
        [populacao[coluna_cv_dap_observado_dest], populacao[coluna_cv_ef_r], populacao[coluna_cv_ef_d1]],
        default=populacao[coluna_cv_ef_d2],
    )
    populacao[coluna_dg_atual] = np.select(
        condicoes_idade,
        [np.nan, populacao[coluna_dg_ef_r], populacao[coluna_dg_ef_d1]],
        default=populacao[coluna_dg_ef_d2],
    )
    # truncado_esquerda_atual: mesmo padrão de dg_atual acima (sem
    # baseline, 0/False antes do 1º manejo aprovado pela guarda) — diz se
    # a Weibull vigente NESSA idade (forma_atual/escala_atual) foi
    # ajustada com truncagem à esquerda, pra
    # calcular_distribuicao_diametrica/densidade_weibull saberem se
    # aplicam a correção de área/densidade truncada (ver
    # simulacao.probabilidades_por_classe, `limite_truncamento`).
    populacao[coluna_truncado_esquerda_atual] = np.select(
        condicoes_idade,
        [0, populacao[coluna_te_ef_r], populacao[coluna_te_ef_d1]],
        default=populacao[coluna_te_ef_d2],
    )

    colunas_por_etapa_flat = [c for tupla in colunas_por_etapa.values() for c in tupla]

    colunas_extras = (
        [coluna_forma, coluna_escala, coluna_fustes_observado,
         coluna_dap_med_observado_dest, coluna_dap_max_observado_dest, coluna_dap_min_observado_dest,
         coluna_ht_observado_dest, coluna_vtcc_observado_dest, coluna_cv_dap_observado_dest]
        + colunas_por_etapa_flat
        + [coluna_idade, coluna_ano_simulado, coluna_evento, coluna_intensidade,
           coluna_remocao_absoluta, coluna_remocao_vtcc,
           coluna_forma_atual, coluna_escala_atual,
           coluna_dap_med_atual, coluna_dap_max_atual, coluna_dap_min_atual, coluna_fustes_atual,
           coluna_ht_atual, coluna_vtcc_atual, coluna_cv_dap_atual, coluna_dg_atual,
           coluna_truncado_esquerda_atual]
    )
    colunas_insert = ["id"] + colunas_originais + colunas_extras

    # Ordena por talhão + idade antes de gravar: o cross join contra
    # df_idades (ver mais acima) já deixa a idade ascendente DENTRO de
    # cada talhão, mas a ordem dos talhões em si é a que veio da consulta
    # em base_ifc_talhao (não necessariamente ordenada) — como "id" é
    # INTEGER PRIMARY KEY (= rowid), a ordem de inserção decide a ordem
    # física da tabela, e um SELECT * sem ORDER BY (ex: Exportar Excel,
    # gráficos) devolve por rowid (mesmo raciocínio de
    # calcular_volume_por_sortimento, que ordena antes de gravar pelo
    # mesmo motivo).
    populacao = populacao.sort_values(
        [coluna_talhao_ifc, coluna_idade], kind="stable").reset_index(drop=True)
    # id explícito (em vez de deixar o AUTOINCREMENT decidir) pra poder
    # referenciar cada linha em simulacao_distribuicao_diametrica sem
    # precisar reconsultar a tabela pra descobrir o id gerado.
    populacao["id"] = np.arange(1, len(populacao) + 1)

    colunas_sql_originais = ", ".join(f'"{c}" TEXT' for c in colunas_originais)
    colunas_sql_por_etapa = ", ".join(f'"{c}" REAL' for c in colunas_por_etapa_flat)
    create_table_sql = (
        f'CREATE TABLE "{tabela_populacao}" ('
        f'id INTEGER PRIMARY KEY, {colunas_sql_originais}, '
        f'"{coluna_forma}" REAL, "{coluna_escala}" REAL, "{coluna_fustes_observado}" REAL, '
        f'"{coluna_dap_med_observado_dest}" REAL, "{coluna_dap_max_observado_dest}" REAL, '
        f'"{coluna_dap_min_observado_dest}" REAL, '
        f'"{coluna_ht_observado_dest}" REAL, "{coluna_vtcc_observado_dest}" REAL, '
        f'"{coluna_cv_dap_observado_dest}" REAL, '
        f'{colunas_sql_por_etapa}, '
        f'"{coluna_idade}" INTEGER, "{coluna_ano_simulado}" REAL, '
        f'"{coluna_evento}" TEXT, "{coluna_intensidade}" REAL, '
        f'"{coluna_remocao_absoluta}" REAL, "{coluna_remocao_vtcc}" REAL, '
        f'"{coluna_forma_atual}" REAL, "{coluna_escala_atual}" REAL, '
        f'"{coluna_dap_med_atual}" REAL, "{coluna_dap_max_atual}" REAL, "{coluna_dap_min_atual}" REAL, '
        f'"{coluna_fustes_atual}" REAL, "{coluna_ht_atual}" REAL, "{coluna_vtcc_atual}" REAL, '
        f'"{coluna_cv_dap_atual}" REAL, "{coluna_dg_atual}" REAL, '
        f'"{coluna_truncado_esquerda_atual}" INTEGER)'
    )

    if persistir:
        _persistir_populacao(
            conn, tabela_populacao, tabela_distribuicao, create_table_sql, populacao,
            colunas_insert, commit=False)

    salvar_coluna_talhao(conn, coluna_talhao_ifc)
    salvar_coluna_fustes_observados(conn, coluna_fustes_observados)
    salvar_coluna_dap_med_observado(conn, coluna_dap_med_observado)
    salvar_coluna_dap_max_observado(conn, coluna_dap_max_observado)
    salvar_coluna_dap_min_observado(conn, coluna_dap_min_observado)
    salvar_coluna_ht_observado(conn, coluna_ht_observado)
    salvar_coluna_vtcc_observado(conn, coluna_vtcc_observado)
    salvar_coluna_cv_dap_observado(conn, coluna_cv_dap_observado)
    salvar_coluna_data_plantio(conn, coluna_data_plantio)
    # Commita já (não só quando persistir=True) — mesmo motivo do
    # comentário em calcular_cenario_em_memoria/salvar_coluna_data_
    # medicao: com persistir=False (usado por calcular_cenario_em_
    # memoria, inclusive rodando num worker do lote paralelo, ver
    # app/screens/simulacao.py:_ThreadGerarLote), essas escritas
    # ficavam penduradas numa transação que só o processo principal (não
    # essa conexão) eventualmente commitaria — na prática nunca
    # commitava (worker não chama commit() na conexão dele), travando o
    # processo principal na hora de gravar ("database is locked").
    conn.commit()

    # Distribuição diamétrica Weibull por classe — extraída em função própria
    # (calcular_distribuicao_diametrica) pra poder ser recalculada depois,
    # fora daqui, com uma coluna de forma/escala diferente das calculadas
    # acima (ex: uma gerada por um construtor de variáveis salvo — ver
    # app/screens/simulacao.py:gerar, que reaplica construtores depois de
    # chamar gerar_populacao e só então decide se recalcula a distribuição
    # com a coluna apontada em vez de forma_atual/escala_atual).
    resultado_distribuicao = calcular_distribuicao_diametrica(
        conn, coluna_forma_atual, coluna_escala_atual, sufixo_tabela=sufixo_tabela,
        coluna_dap_min=coluna_dap_min_atual, coluna_truncado_esquerda=coluna_truncado_esquerda_atual,
        contexto_lote=contexto_lote, df_entrada=populacao, persistir=persistir)

    resultado = {
        "talhoes": talhoes_total,
        "talhoes_com_weibull": talhoes_com_weibull,
        "talhoes_sem_weibull": talhoes_sem_weibull,
        "talhoes_com_weibull_apos_raleio": talhoes_com_weibull_por_etapa["apos_raleio"],
        "talhoes_com_weibull_apos_desbaste_1": talhoes_com_weibull_por_etapa["apos_desbaste_1"],
        "talhoes_com_weibull_apos_desbaste_2": talhoes_com_weibull_por_etapa["apos_desbaste_2"],
        "talhoes_sem_fustes_observado": talhoes_sem_fustes_observado,
        "talhoes_manejo_pulado_apos_raleio": talhoes_manejo_pulado_por_etapa["apos_raleio"],
        "talhoes_manejo_pulado_apos_desbaste_1": talhoes_manejo_pulado_por_etapa["apos_desbaste_1"],
        "talhoes_manejo_pulado_apos_desbaste_2": talhoes_manejo_pulado_por_etapa["apos_desbaste_2"],
        "idades": len(idades),
        "idade_maxima_manejo": idade_maxima,
        "linhas_geradas": len(populacao),
        "coluna_forma_atual": coluna_forma_atual,
        "coluna_escala_atual": coluna_escala_atual,
        "coluna_dap_min_atual": coluna_dap_min_atual,
        "coluna_truncado_esquerda_atual": coluna_truncado_esquerda_atual,
        "classes_diametricas": resultado_distribuicao["classes_diametricas"],
        "combinacoes_sem_distribuicao": resultado_distribuicao["combinacoes_sem_distribuicao"],
        "linhas_distribuicao_geradas": resultado_distribuicao["linhas_distribuicao_geradas"],
    }
    if not persistir:
        resultado["_df_populacao"] = populacao
        resultado["_tabela_populacao"] = tabela_populacao
        resultado["_create_table_sql"] = create_table_sql
        resultado["_colunas_insert"] = colunas_insert
        resultado["_tabela_distribuicao"] = tabela_distribuicao
        resultado["_linhas_distribuicao"] = resultado_distribuicao.get("_linhas_distribuicao", ())
    return resultado


# ==========================================================
# DISTRIBUIÇÃO DIAMÉTRICA (separado de gerar_populacao pra poder recalcular
# com uma coluna de forma/escala diferente, ex: gerada por um construtor de
# variáveis salvo — ver simulacao_metadados.coluna_forma_distribuicao_ifc)
# ==========================================================

def _validar_colunas_populacao(conn, coluna_forma, coluna_escala, tabela_populacao=TABELA_POPULACAO):
    colunas_populacao = [
        d[0] for d in conn.execute(f'SELECT * FROM "{tabela_populacao}" LIMIT 0').description
    ]
    for rotulo, coluna in (("forma", coluna_forma), ("escala", coluna_escala)):
        if coluna not in colunas_populacao:
            raise ValueError(f"A coluna de {rotulo} \"{coluna}\" não existe em \"{tabela_populacao}\".")


def _calcular_matriz_distribuicao(
    df, coluna_forma, coluna_escala, classes_diametricas, tipo_normalizacao="aditiva",
    coluna_dap_min=None, coluna_truncado_esquerda=None,
):
    """A partir de um DataFrame com colunas "id"/`coluna_forma`/`coluna_escala`
    (uma linha por combinação talhão×idade), devolve (ids, probabilidades,
    densidades) só das linhas com forma/escala não nulos —
    `probabilidades` vem de `probabilidades_por_classe` (já normalizada
    por linha conforme `tipo_normalizacao` — ver TIPOS_NORMALIZACAO_WEIBULL:
    massa de probabilidade dentro da classe, largura 1,
    S(classe-0.5)-S(classe+0.5)); `densidades` é a PDF da Weibull
    (`densidade_weibull`) avaliada exatamente no valor da classe — a
    "altura instantânea" da curva ali, sem integrar numa janela, não
    normalizada linha a linha (não tem por quê somar 1 — é densidade, não
    massa).

    `coluna_dap_min`/`coluna_truncado_esquerda` (opcionais, só faz
    sentido passar quando `coluna_forma`/`coluna_escala` forem
    forma_atual/escala_atual — ver calcular_distribuicao_diametrica):
    nomes das colunas de `df` com o ponto de corte (ex: dap_min_atual) e
    o flag por linha dizendo se aquela Weibull foi ajustada truncada (ex:
    truncado_esquerda_atual). Quando os dois são passados, linhas com o
    flag ligado (e dap_min não nulo) usam a fórmula truncada em
    `probabilidades`/`densidades` (ver probabilidades_por_classe/
    densidade_weibull, `limite_truncamento`) — as demais linhas (flag
    desligado, ou um dos dois não passado) continuam com a fórmula
    comum."""
    validas = df[["id", coluna_forma, coluna_escala]].dropna()
    ids = validas["id"].to_numpy()

    if len(ids) == 0 or len(classes_diametricas) == 0:
        vazio = np.zeros((len(ids), len(classes_diametricas)))
        return ids, vazio, vazio

    formas_validas = validas[coluna_forma].to_numpy(dtype=float)
    escalas_validas = validas[coluna_escala].to_numpy(dtype=float)

    limite_truncamento = None
    if coluna_dap_min is not None and coluna_truncado_esquerda is not None:
        dap_min_validos = df.loc[validas.index, coluna_dap_min].to_numpy(dtype=float)
        truncado_validos = df.loc[validas.index, coluna_truncado_esquerda].to_numpy(dtype=float)
        limite_truncamento = np.where(
            (truncado_validos == 1) & ~np.isnan(dap_min_validos), dap_min_validos, np.nan)

    probabilidades = probabilidades_por_classe(
        formas_validas, escalas_validas, classes_diametricas, tipo_normalizacao, limite_truncamento)
    densidades = densidade_weibull(
        classes_diametricas[None, :], formas_validas[:, None], escalas_validas[:, None],
        limite_truncamento[:, None] if limite_truncamento is not None else None)

    return ids, probabilidades, densidades


def _persistir_distribuicao(
    conn: sqlite3.Connection, tabela_distribuicao: str, tabela_populacao: str,
    linhas_distribuicao_dados, commit: bool = True,
) -> None:
    """DROP+CREATE+INSERT de `tabela_distribuicao` a partir de linhas já
    calculadas (populacao_id, classe_diametrica, probabilidade, densidade)
    — extraído de calcular_distribuicao_diametrica pra poder ser chamado
    separadamente do cálculo (ver `persistir` nela, e
    app/screens/simulacao.py:_gerar_uma_simulacao, que calcula várias
    etapas em memória e só grava tudo no final).

    `linhas_distribuicao_dados`: tupla de 4 arrays/sequências PARALELAS
    (populacao_id, classe_diametrica, probabilidade, densidade), mesmo
    comprimento — não uma lista já pronta de tuplas por linha (ver
    calcular_distribuicao_diametrica). O zip() que monta cada tupla pro
    executemany() só acontece AQUI, de propósito: quando este resultado
    vem do lote paralelo (_worker_calcular_cenario/
    persistir_cenario_calculado), ele atravessa a fronteira entre
    processos ANTES de chegar aqui — arrays numpy contíguos são bem mais
    rápidos de picklar (um buffer só) do que a mesma quantidade de tuplas
    Python já prontas (cada uma um objeto próprio, com centenas de
    milhares de linhas fácil: talhões × idades × classes diamétricas —
    era o maior componente medido em "Fila/IPC" num lote de verdade)."""
    conn.execute(f'DROP TABLE IF EXISTS "{tabela_distribuicao}"')
    conn.execute(
        f'CREATE TABLE "{tabela_distribuicao}" ('
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        f'populacao_id INTEGER NOT NULL REFERENCES "{tabela_populacao}"(id), '
        "classe_diametrica REAL NOT NULL, probabilidade REAL, densidade REAL)"
    )
    if linhas_distribuicao_dados:
        # truthy check, não len(): a tupla vazia () (sem linha nenhuma pra
        # gravar, ver calcular_distribuicao_diametrica) e a tupla de 4
        # arrays (com linhas) têm tamanhos DIFERENTES (0 vs 4) — não dá
        # pra usar len() como "quantas linhas tem", só bool() como "tem
        # alguma coisa pra gravar ou não".
        ids_repetidos, classes_repetidas, probabilidades_lineares, densidades_lineares = (
            linhas_distribuicao_dados)
        linhas = list(zip(
            np.asarray(ids_repetidos).tolist(), np.asarray(classes_repetidas).tolist(),
            np.asarray(probabilidades_lineares).tolist(), np.asarray(densidades_lineares).tolist()))
        conn.executemany(
            f'INSERT INTO "{tabela_distribuicao}" '
            "(populacao_id, classe_diametrica, probabilidade, densidade) "
            "VALUES (?, ?, ?, ?)",
            linhas,
        )
    if commit:
        conn.commit()


def calcular_distribuicao_diametrica(
    conn: sqlite3.Connection, coluna_forma: str, coluna_escala: str, sufixo_tabela: str = "",
    coluna_dap_min: Optional[str] = None, coluna_truncado_esquerda: Optional[str] = None,
    contexto_lote: Optional[Dict] = None, df_entrada: Optional[pd.DataFrame] = None,
    persistir: bool = True,
) -> Dict:
    """(Re)calcula `simulacao_distribuicao_diametrica` inteira (DROP +
    CREATE), lendo forma/escala de `coluna_forma`/`coluna_escala` em
    `simulacao_talhao_idade` — podem ser forma_atual/escala_atual (o par
    calculado por gerar_populacao a partir do pipeline Weibull) ou
    qualquer outra coluna numérica ali, inclusive uma gerada por um
    construtor de variáveis salvo. A normalização por classe usa o tipo
    configurado em Configurações (ver obter_tipo_normalizacao_weibull).
    Levanta ValueError se a tabela de população ou as colunas não
    existirem.

    `coluna_dap_min`/`coluna_truncado_esquerda` (opcionais — ver
    _calcular_matriz_distribuicao): só faz sentido passar quando
    `coluna_forma`/`coluna_escala` forem forma_atual/escala_atual (ex:
    "dap_min_atual"/"truncado_esquerda_atual", os nomes que
    gerar_populacao expõe no seu retorno) — habilita a fórmula truncada
    de área/densidade por classe nas linhas cuja Weibull "Por Simulação"
    foi de fato ajustada truncada. Sem sentido (deixe None) quando
    `coluna_forma`/`coluna_escala` apontam pra uma coluna arbitrária (ex:
    override do Construtor de Variáveis), que não tem um dap_min
    correspondente.

    `sufixo_tabela`: mesmo papel que em gerar_populacao — lê/grava
    "simulacao_talhao_idade{sufixo_tabela}"/
    "simulacao_distribuicao_diametrica{sufixo_tabela}" em vez dos nomes
    canônicos.

    `contexto_lote` (opcional, mesmo objeto de gerar_populacao): quando
    fornecido, reaproveita `classes_diametricas`/`tipo_normalizacao_weibull`
    já lidos em `preparar_contexto_lote` em vez de reconsultar
    Configurações — usado pelo modo "Múltiplos cenários", já que essa
    chamada acontece de novo (uma vez por cenário) dentro de
    gerar_populacao.

    `df_entrada` (opcional): usa esse DataFrame (precisa ter "id",
    `coluna_forma`, `coluna_escala`, e `coluna_dap_min`/
    `coluna_truncado_esquerda` se passados) em vez de reler
    `tabela_populacao` do banco — usado pelo pipeline de geração em
    memória (ver app/screens/simulacao.py:_gerar_uma_simulacao), que já
    tem a população inteira em memória e não precisa reler.

    `persistir` (padrão True): com False, pula DROP/CREATE/INSERT em
    `tabela_distribuicao` — as linhas calculadas saem em
    resultado["_linhas_distribuicao"] (junto com resultado
    ["_tabela_distribuicao"]) pra quem chamou gravar depois, sem
    recalcular (ver _persistir_distribuicao)."""
    tabela_populacao = TABELA_POPULACAO + sufixo_tabela
    tabela_distribuicao = TABELA_DISTRIBUICAO + sufixo_tabela

    if df_entrada is not None:
        df = df_entrada
        if coluna_forma not in df.columns or coluna_escala not in df.columns:
            raise ValueError(
                f"A coluna de forma \"{coluna_forma}\" ou escala \"{coluna_escala}\" não existe "
                "no DataFrame em memória."
            )
    else:
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela_populacao,)
        ).fetchone()
        if existe is None:
            raise ValueError(
                "Nenhuma simulação gerada ainda. Rode \"Gerar simulação\" antes de calcular a "
                "distribuição diamétrica."
            )

        _validar_colunas_populacao(conn, coluna_forma, coluna_escala, tabela_populacao)

        colunas_extras_sql = ""
        if coluna_dap_min is not None and coluna_truncado_esquerda is not None:
            colunas_extras_sql = f', "{coluna_dap_min}", "{coluna_truncado_esquerda}"'
        df = pd.read_sql_query(
            f'SELECT id, "{coluna_forma}", "{coluna_escala}"{colunas_extras_sql} FROM "{tabela_populacao}"',
            conn,
        )
    if contexto_lote is not None:
        classes_diametricas = contexto_lote["baseline"]["classes_diametricas"]
        tipo_normalizacao = contexto_lote["baseline"]["tipo_normalizacao_weibull"]
    else:
        classes_diametricas = obter_classes_diametricas(conn)
        tipo_normalizacao = obter_tipo_normalizacao_weibull(conn)

    ids, probabilidades, densidades = _calcular_matriz_distribuicao(
        df, coluna_forma, coluna_escala, classes_diametricas, tipo_normalizacao,
        coluna_dap_min, coluna_truncado_esquerda)
    combinacoes_sem_distribuicao = len(df) - len(ids)

    # Guardado como 4 arrays numpy PARALELOS — não uma lista já "zipada"
    # de tuplas Python (era assim antes). Esse resultado pode atravessar a
    # fronteira entre processos (lote paralelo, ver
    # _worker_calcular_cenario/persistir_cenario_calculado) — arrays numpy
    # contíguos picklam bem mais rápido que a mesma quantidade de tuplas
    # Python já prontas, principal suspeito por trás do tempo medido em
    # "Fila/IPC" num lote de verdade (centenas de milhares de linhas:
    # talhões × idades × classes diamétricas). O zip() que monta cada
    # tupla pro executemany() foi movido pra dentro de
    # _persistir_distribuicao, que só roda DEPOIS dessa travessia.
    linhas_distribuicao_dados = ()
    n_linhas_distribuicao = 0
    if len(ids) and len(classes_diametricas):
        ids_repetidos = np.repeat(ids, len(classes_diametricas))
        classes_repetidas = np.tile(classes_diametricas, len(ids))
        probabilidades_lineares = probabilidades.reshape(-1)
        densidades_lineares = densidades.reshape(-1)

        linhas_distribuicao_dados = (
            ids_repetidos, classes_repetidas, probabilidades_lineares, densidades_lineares)
        n_linhas_distribuicao = len(ids_repetidos)

    resultado = {
        "classes_diametricas": len(classes_diametricas),
        "linhas_distribuicao_geradas": n_linhas_distribuicao,
        "combinacoes_sem_distribuicao": int(combinacoes_sem_distribuicao),
    }
    if persistir:
        _persistir_distribuicao(conn, tabela_distribuicao, tabela_populacao, linhas_distribuicao_dados)
    else:
        resultado["_linhas_distribuicao"] = linhas_distribuicao_dados
        resultado["_tabela_distribuicao"] = tabela_distribuicao
    return resultado


# ==========================================================
# VOLUME POR SORTIMENTO
# ==========================================================

def colunas_volume_por_classe_disponiveis(
    conn: sqlite3.Connection, tabela_populacao: str = TABELA_POPULACAO
) -> List[str]:
    """Nomes-base disponíveis pro combobox "Volume" (tela Simulação):
    detecta, entre as colunas de `tabela_populacao` (por padrão
    `simulacao_talhao_idade`, mas aceita uma tabela sufixada de cenário —
    ver colunas_kpi_cenarios_disponiveis), grupos gerados por um Modelo
    ligado ao nó Classe Diamétrica no Construtor de Variáveis (ver
    app/construtores.py:saidas_nomeadas — cada classe vira uma coluna
    "{nome_saida}_{classe:g}"). Um nome-base só entra no resultado se
    TODAS as classes configuradas em Configurações tiverem a coluna
    correspondente — parcial (ex: classes mudaram desde a última "Gerar
    simulação") fica de fora, pra não montar um volume incompleto sem
    avisar. Lista vazia se a tabela de população ou as classes
    diamétricas ainda não existirem — não é erro, só "nada disponível
    ainda"."""
    try:
        classes = obter_classes_diametricas(conn)
    except ValueError:
        return []
    if len(classes) == 0:
        return []

    try:
        colunas = [
            d[0] for d in conn.execute(f'SELECT * FROM "{tabela_populacao}" LIMIT 0').description
        ]
    except sqlite3.OperationalError:
        return []

    classes_por_base: Dict[str, set] = {}
    for coluna in colunas:
        for classe in classes:
            sufixo = f"_{classe:g}"
            if coluna.endswith(sufixo):
                base = coluna[: -len(sufixo)]
                classes_por_base.setdefault(base, set()).add(classe)
                break

    classes_completas = set(classes.tolist())
    return sorted(base for base, achadas in classes_por_base.items() if achadas == classes_completas)


def _classes_por_sortimento(
    classes: List[float], sortimentos: List[Tuple]
) -> Dict[str, List[float]]:
    """Agrupa `classes` por sortimento cujo [limite_inferior,
    limite_superior] cobre cada uma — bordas inclusivas, None num limite =
    sem limite naquele lado, MAS cada classe entra em só UM sortimento: o
    primeiro que bater, na ordem de `sortimentos` (já vem ordenado por
    limite_inferior — mesma regra/mesmo desempate de
    construtores._preco_sortimento_da_classe, que já não somava duplicado
    porque para no primeiro match). Faixas que se tocam (ex: um sortimento
    termina em 18 e o seguinte começa em 18) são comuns — sem esse
    desempate, uma classe bem na borda entraria em DOIS sortimentos e
    ficaria contada duas vezes em qualquer soma entre eles (ver
    calcular_volume_por_sortimento/dados_grafico_resultado, que dependem
    desta função pra não duplicar)."""
    resultado: Dict[str, List[float]] = {nome: [] for nome, _li, _ls in sortimentos}
    for classe in classes:
        for nome, limite_inferior, limite_superior in sortimentos:
            if (limite_inferior is None or classe >= limite_inferior) and (
                    limite_superior is None or classe <= limite_superior):
                resultado[nome].append(classe)
                break
    return resultado


def obter_idade_corte_raso(
    conn: sqlite3.Connection, tabela_populacao: str = TABELA_POPULACAO
) -> Optional[int]:
    """Idade do Corte Raso da última "Gerar simulação" — não é lembrada
    como metadado próprio (ao contrário de coluna_talhao_ifc etc.): a
    idade digitada na tela Simulação só existe em memória durante
    gerar_populacao, então lê aqui de volta da própria
    `simulacao_talhao_idade` já gerada (mesma idade pra todo talhão, já
    que "Corte Raso" não tem guarda de fustes/ha mínimo que possa pular a
    idade em alguns talhões — ver gerar_populacao). Usado pelo nó
    "vet_sortimento" (ver construtores.avaliar_grafo). None se a
    simulação ainda não foi gerada.

    `tabela_populacao` (opcional, padrão a tabela canônica): passe a
    tabela SUFIXADA ("simulacao_talhao_idade__cenario3") ao processar um
    cenário do lote de "Múltiplos cenários"/"Grade automática" — a
    canônica só reflete o cenário ativado por último (ou nem existe),
    nunca o idade_corte_raso do cenário sendo processado AGORA; ler a
    canônica ali dava VET errado pra todo cenário do lote cuja idade do
    Corte Raso não coincidisse por acaso com a da tabela canônica."""
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela_populacao,)
    ).fetchone()
    if existe is None:
        return None
    row = conn.execute(
        f'SELECT idade_simulada FROM "{tabela_populacao}" WHERE evento_manejo = ? LIMIT 1',
        (EVENTO_CORTE_RASO,),
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else None


# Colunas constantes acrescentadas em toda linha (inclusive as sintéticas
# de idade anterior à simulação) quando `metadados_cenario` é passado pra
# calcular_volume_por_sortimento — nome da coluna -> tipo SQL. Ordem aqui
# também é a ordem de gravação (ver colunas_insert/extra_metadados
# abaixo) e a ordem das colunas no Excel (ver
# app/screens/simulacao.py:_CAMPOS_METADADOS_CENARIO, mesmos nomes,
# usada por exportar_todos_cenarios pra injetar os mesmos valores direto
# de simulacao_cenarios sem depender dessa coluna já existir no banco).
COLUNAS_METADADOS_CENARIO = (
    ("cenario", "TEXT"),
    ("idade_raleio", "REAL"), ("intensidade_raleio", "REAL"),
    ("idade_desbaste_1", "REAL"), ("intensidade_desbaste_1", "REAL"),
    ("idade_desbaste_2", "REAL"), ("intensidade_desbaste_2", "REAL"),
    ("idade_corte_raso", "REAL"),
)


def _persistir_volume_sortimento(
    conn: sqlite3.Connection, tabela_volume_sortimento: str,
    create_table_sql: Optional[str], colunas_insert: Optional[list], marcadores: Optional[str],
    linhas: Optional[list], commit: bool = True,
) -> None:
    """DROP (sempre) + CREATE/INSERT (só se `create_table_sql` não for
    None) de `tabela_volume_sortimento` — extraído de
    calcular_volume_por_sortimento pra poder ser chamado separadamente do
    cálculo (ver `persistir` nela e
    app/screens/simulacao.py:_gerar_uma_simulacao). `create_table_sql=None`
    reproduz o "sempre derruba a tabela antiga, mesmo sem recriar"
    (nenhuma coluna de volume selecionada, sem sortimento cadastrado etc)."""
    conn.execute(f'DROP TABLE IF EXISTS "{tabela_volume_sortimento}"')
    if create_table_sql is not None:
        conn.execute(create_table_sql)
        colunas_insert_sql = ", ".join(f'"{c}"' for c in colunas_insert)
        conn.executemany(
            f'INSERT INTO "{tabela_volume_sortimento}" ({colunas_insert_sql}) VALUES ({marcadores})',
            linhas,
        )
    if commit:
        conn.commit()


def calcular_volume_por_sortimento(
    conn: sqlite3.Connection, colunas_base_classes: Optional[List[str]],
    tipos_agregacao: Optional[Dict[str, str]],
    sufixo_tabela: str = "", metadados_cenario: Optional[Dict] = None,
    df_entrada: Optional[pd.DataFrame] = None, persistir: bool = True,
) -> Dict:
    """(Re)calcula `simulacao_volume_sortimento` inteira (DROP + CREATE):
    uma coluna REAL por (campo, sortimento cadastrado na tela
    Configurações), somando ou tirando a média — `tipos_agregacao`,
    {campo: "Soma"/"Média"}, independente por campo (ex: soma pro volume
    total, média pro volume de biomassa); campo ausente do dict usa
    "Soma" — das colunas de classe "{campo}_{classe:g}" de
    `simulacao_talhao_idade` cujo centro de classe cai dentro da faixa
    [limite_inferior, limite_superior] daquele sortimento (bordas
    inclusivas; None num limite = sem limite naquele lado). Ao lado, traz
    a coluna de talhão mapeada, idade_simulada, ano_simulado,
    evento_manejo e custo_formacao. TODAS as idades simuladas entram
    (não só as com evento de manejo).

    `colunas_base_classes` é uma LISTA de nomes-base (campo "Volume" da
    tela Simulação, multi-seleção — ex: volume total E volume de
    biomassa). Com 1 campo só, a coluna de cada sortimento sai com o nome
    cru do sortimento (comportamento de sempre, ex: "0-10"); com 2+
    campos, cada coluna vem prefixada com o campo pra não colidir (ex:
    "vtcc_0-10", "biomassa_0-10") — mesmo `_nome_coluna_destino` de
    sempre resolve qualquer colisão remanescente. Aceita também uma
    string solta (1 campo só) por conveniência/compatibilidade.

    Cada talhão também ganha uma linha extra POR idade REALMENTE
    cadastrada em `custos_formacao` com ano < 1 (idade_simulada nunca é
    negativa nem zero em `simulacao_talhao_idade` — gerar_populacao só
    gera 1..idade_maxima_manejo — então um custo de formação incorrido
    ANTES da 1ª idade simulada nunca apareceria em lugar nenhum sem essas
    linhas sintéticas). Só entram as idades com custo cadastrado — sem
    preencher o intervalo entre elas: ex. só -10 cadastrado insere só a
    idade -10 (com o custo de verdade), sem gerar -9..0 com custo 0; a
    idade seguinte já é 1, do df normal. Nessas linhas, só
    talhão/idade_simulada/ano_simulado/custo_formacao (e "cenario", se
    houver) vêm preenchidos — sem distribuição diamétrica simulada nessa
    idade, não tem volume por sortimento pra somar (fica tudo NULL).
    ano_simulado é ano_plantio + idade_simulada, com ano_plantio derivado
    de volta (ano_simulado - idade_simulada) de qualquer linha já
    simulada daquele talhão — NULL se a data de plantio não foi mapeada.

    `metadados_cenario`, se passado (rodando dentro do lote de "Múltiplos
    cenários" — ver app/screens/simulacao.py:_ThreadGerarLote), acrescenta
    as colunas de COLUNAS_METADADOS_CENARIO ("cenario" + idade/intensidade
    de Raleio/1º Desbaste/2º Desbaste/idade do Corte Raso) com o valor
    constante de `metadados_cenario[chave]` (None se a chave não existir
    no dict) em toda linha, inclusive as sintéticas acima — não existem
    sem isso (geração única, comportamento de sempre): pensadas pra
    reconhecer de qual cenário (e com que parâmetros) veio cada linha
    depois de exportar pra Excel um cenário de cada vez e juntar as
    planilhas por fora (ver também
    app/screens/simulacao.py:exportar_todos_cenarios, que injeta as
    mesmas colunas na hora de exportar, direto de simulacao_cenarios, sem
    depender de terem sido gravadas aqui).

    Sempre derruba a tabela antiga primeiro, mesmo sem recriar (ex:
    `colunas_base_classes` vazio) — evita deixar um resultado de uma
    configuração anterior (Volume mapeado antes, depois desmarcado)
    parecendo válido. Devolve {"executado": False, "motivo": str} nesse
    caso e quando não há sortimento cadastrado; levanta ValueError se
    algum campo de `colunas_base_classes` estiver mapeado mas faltar a
    coluna de alguma classe configurada (schema desatualizado —
    reaplicar o construtor ou gerar a simulação de novo resolve).

    `sufixo_tabela`: mesmo papel que em gerar_populacao — lê
    "simulacao_talhao_idade{sufixo_tabela}" e grava
    "simulacao_volume_sortimento{sufixo_tabela}" em vez dos nomes
    canônicos.

    `df_entrada` (opcional): usa esse DataFrame (precisa ter "id",
    `coluna_talhao`, "idade_simulada", "ano_simulado", "evento_manejo"[,
    "custo_formacao"] e as colunas de classe) em vez de reler
    `tabela_populacao` do banco — usado pelo pipeline de geração em
    memória (ver app/screens/simulacao.py:_gerar_uma_simulacao).

    `persistir` (padrão True): com False, pula DROP/CREATE/INSERT em
    `tabela_volume_sortimento` — o necessário pra gravar depois sai em
    resultado["_persistir_volume"] (ver _persistir_volume_sortimento)."""
    tabela_populacao = TABELA_POPULACAO + sufixo_tabela
    tabela_volume_sortimento = TABELA_VOLUME_SORTIMENTO + sufixo_tabela

    def _sem_execucao(motivo: str) -> Dict:
        resultado = {"executado": False, "motivo": motivo}
        if persistir:
            _persistir_volume_sortimento(conn, tabela_volume_sortimento, None, None, None, None)
        else:
            resultado["_tabela_volume_sortimento"] = tabela_volume_sortimento
            resultado["_persistir_volume"] = None
        return resultado

    if isinstance(colunas_base_classes, str):
        colunas_base_classes = [colunas_base_classes] if colunas_base_classes else []
    colunas_base_classes = [c for c in (colunas_base_classes or []) if c]
    if not colunas_base_classes:
        return _sem_execucao("Nenhuma coluna de volume por classe selecionada.")

    coluna_talhao = obter_coluna_talhao(conn)
    if not coluna_talhao:
        return _sem_execucao("Coluna de talhão não configurada.")

    classes = obter_classes_diametricas(conn)
    if df_entrada is not None:
        colunas_populacao = set(df_entrada.columns)
    else:
        colunas_populacao = {
            d[0] for d in conn.execute(f'SELECT * FROM "{tabela_populacao}" LIMIT 0').description
        }
    # {campo: {classe: nome_da_coluna}} — uma entrada por campo selecionado.
    colunas_por_classe_por_campo: Dict[str, Dict[float, str]] = {}
    for coluna_base_classes_campo in colunas_base_classes:
        colunas_por_classe = {}
        for classe in classes:
            nome_coluna = f"{coluna_base_classes_campo}_{classe:g}"
            if nome_coluna not in colunas_populacao:
                raise ValueError(
                    f"A coluna \"{nome_coluna}\" (classe {classe:g}, campo "
                    f"\"{coluna_base_classes_campo}\") não existe em \"{tabela_populacao}\" — "
                    "reaplique o construtor de variáveis ou gere a simulação de novo.")
            colunas_por_classe[classe] = nome_coluna
        colunas_por_classe_por_campo[coluna_base_classes_campo] = colunas_por_classe

    sortimentos = conn.execute(
        "SELECT nome, limite_inferior, limite_superior FROM sortimentos ORDER BY limite_inferior, nome"
    ).fetchall()
    if not sortimentos:
        return _sem_execucao("Nenhum sortimento cadastrado.")

    # idade_simulada/ano_simulado/evento_manejo são os nomes "de fábrica"
    # dessas colunas (ver gerar_populacao) — só mudam se colidirem com uma
    # coluna já existente na Base IFC ByTalhao, caso raro que o resto do
    # módulo (ex: _COLUNA_IDADE_PADRAO mais abaixo) também não cobre.
    # custo_formacao NÃO vem mais de gerar_populacao — é um nó "Custo de
    # Formação" do Construtor de Variáveis (ver core/construtores.py,
    # ramo "custo_formacao" de avaliar_grafo), então só existe em
    # `tabela_populacao` se o usuário salvou um construtor com esse nó
    # aplicado; sem ele, entra como 0.0 (mesmo padrão de sempre — "0 nas
    # idades sem custo", não NaN).
    todas_colunas_classe = [
        nome for colunas_por_classe in colunas_por_classe_por_campo.values()
        for nome in colunas_por_classe.values()
    ]
    if df_entrada is not None:
        colunas_selecionar = (
            ["id", coluna_talhao, "idade_simulada", "ano_simulado", "evento_manejo"]
            + (["custo_formacao"] if "custo_formacao" in colunas_populacao else [])
            + todas_colunas_classe
        )
        df = df_entrada[colunas_selecionar].rename(columns={coluna_talhao: "talhao"})
        if "custo_formacao" not in df.columns:
            df["custo_formacao"] = 0.0
    else:
        coluna_custo_formacao_sql = (
            '"custo_formacao" AS custo_formacao' if "custo_formacao" in colunas_populacao
            else '0.0 AS custo_formacao')
        df = pd.read_sql_query(
            f'SELECT id, "{coluna_talhao}" AS talhao, "idade_simulada" AS idade_simulada, '
            f'"ano_simulado" AS ano_simulado, "evento_manejo" AS evento_manejo, '
            f'{coluna_custo_formacao_sql}, '
            + ", ".join(f'"{nome}"' for nome in todas_colunas_classe)
            + f' FROM "{tabela_populacao}"',
            conn,
        )

    reservados = {
        coluna_talhao.lower(), "idade_simulada", "ano_simulado", "evento_manejo",
        "custo_formacao", "id"} | {nome for nome, _tipo in COLUNAS_METADADOS_CENARIO}
    tipos_agregacao = tipos_agregacao or {}
    classes_por_sortimento = _classes_por_sortimento(classes, sortimentos)
    # Com 1 campo só, mantém o nome cru do sortimento (comportamento de
    # sempre); com 2+, prefixa com o campo pra distinguir na mesma tabela.
    prefixar_com_campo = len(colunas_base_classes) > 1
    colunas_resultado: Dict[str, pd.Series] = {}
    for coluna_base_classes_campo in colunas_base_classes:
        usar_soma = tipos_agregacao.get(coluna_base_classes_campo) != "Média"
        colunas_por_classe = colunas_por_classe_por_campo[coluna_base_classes_campo]
        for nome, _limite_inferior, _limite_superior in sortimentos:
            nome_bruto = f"{coluna_base_classes_campo}_{nome}" if prefixar_com_campo else nome
            coluna_destino = _nome_coluna_destino(nome_bruto, reservados)
            classes_no_sortimento = classes_por_sortimento[nome]
            if not classes_no_sortimento:
                colunas_resultado[coluna_destino] = pd.Series(np.nan, index=df.index)
                continue
            subset = df[[colunas_por_classe[classe] for classe in classes_no_sortimento]]
            colunas_resultado[coluna_destino] = (
                subset.sum(axis=1, skipna=True) if usar_soma else subset.mean(axis=1, skipna=True)
            )

    colunas_metadados_sql = "".join(
        f', "{nome}" {tipo}' for nome, tipo in COLUNAS_METADADOS_CENARIO) if metadados_cenario else ""
    colunas_sql = ", ".join(f'"{nome}" REAL' for nome in colunas_resultado)
    create_table_sql = (
        f'CREATE TABLE "{tabela_volume_sortimento}" ('
        f'id INTEGER PRIMARY KEY, "{coluna_talhao}" TEXT, idade_simulada INTEGER, '
        f'ano_simulado REAL, evento_manejo TEXT, custo_formacao REAL{colunas_metadados_sql}, {colunas_sql})'
    )

    colunas_insert = (
        ["id", coluna_talhao, "idade_simulada", "ano_simulado", "evento_manejo", "custo_formacao"]
        + ([nome for nome, _tipo in COLUNAS_METADADOS_CENARIO] if metadados_cenario else [])
        + list(colunas_resultado.keys())
    )
    marcadores = ", ".join("?" for _ in colunas_insert)
    series_listas = [
        [None if pd.isna(v) else float(v) for v in serie.tolist()] for serie in colunas_resultado.values()
    ]
    # Mesmo saneamento de NaN -> None de gerar_populacao (linha ~1193) —
    # sem isso, idade_simulada/ano_simulado/evento_manejo/custo_formacao
    # ausentes (ex: talhão sem data de plantio mapeada) virariam NaN em
    # vez de NULL.
    colunas_base = df[["id", "talhao", "idade_simulada", "ano_simulado", "evento_manejo",
                        "custo_formacao"]]
    linhas_base = [
        tuple(None if pd.isna(v) else v for v in linha)
        for linha in colunas_base.itertuples(index=False, name=None)
    ]
    extra_metadados = (
        tuple(metadados_cenario.get(nome) for nome, _tipo in COLUNAS_METADADOS_CENARIO)
        if metadados_cenario else ()
    )
    linhas = [
        base + extra_metadados + tuple(valores)
        for base, valores in zip(linhas_base, zip(*series_listas))
    ]

    # Linhas sintéticas de idade ANTERIOR à simulação (custo de formação
    # incorrido antes da 1ª idade simulada, idade_simulada=1) — ver
    # docstring. Uma linha por talhão por idade REALMENTE cadastrada em
    # custos_formacao com ano < 1 (ex: só -10 cadastrado -> só entra
    # idade -10, sem preencher -9..0 com zero — a idade "de verdade"
    # seguinte já é 1, do df normal, sem gap preenchido no meio). Cada
    # talhão sempre ganha essa linha (o custo daquele ano é o mesmo pra
    # todos — não é por talhão); colunas de sortimento ficam NULL (sem
    # distribuição diamétrica simulada nessa idade).
    #
    # FALLBACK: só entra (talhão, idade) que AINDA NÃO veio no `df` normal
    # acima — se o usuário já montou e salvou o nó "Custo de Formação" do
    # Construtor de Variáveis (ver core/construtores.py:
    # sincronizar_linhas_formacao), essas linhas já existem de verdade em
    # `tabela_populacao` e vieram no SELECT * lá em cima; duplicar aqui
    # geraria 2 linhas pro mesmo (talhão, idade). Isso também é o que
    # mantém funcionando quem cadastrou custo de formação em
    # Configurações mas ainda não criou o nó — sem essa cobertura, o
    # custo simplesmente não apareceria em lugar nenhum.
    pares_ja_existentes = {
        (talhao, idade) for talhao, idade in zip(df["talhao"], df["idade_simulada"]) if idade <= 0
    }
    custos_antes_simulacao = conn.execute(
        "SELECT ano, custo FROM custos_formacao WHERE ano < 1 AND ano IS NOT NULL "
        "AND custo IS NOT NULL"
    ).fetchall()
    custo_por_idade: Dict[int, float] = {}
    for ano_custo, custo in custos_antes_simulacao:
        idade_custo = round(float(ano_custo))
        custo_por_idade[idade_custo] = custo_por_idade.get(idade_custo, 0.0) + float(custo)

    if custo_por_idade and not df.empty:
        # ano_plantio por talhão, derivado de volta (ano_simulado -
        # idade_simulada) de qualquer linha já simulada daquele talhão —
        # mesmo valor em toda idade positiva dele, por construção (ver
        # gerar_populacao). NaN se a data de plantio não foi mapeada
        # (ano_simulado já vem NaN nesse caso, propaga NaN aqui também).
        idades_reais = df[df["idade_simulada"] >= 1]
        ano_plantio_por_talhao = (
            (idades_reais["ano_simulado"] - idades_reais["idade_simulada"])
            .groupby(idades_reais["talhao"]).first()
        )
        proximo_id = int(df["id"].max()) + 1
        n_colunas_sortimento = len(colunas_resultado)
        for talhao, ano_plantio in ano_plantio_por_talhao.items():
            for idade, custo in sorted(custo_por_idade.items()):
                if (talhao, idade) in pares_ja_existentes:
                    continue
                ano_simulado_negativo = (
                    None if pd.isna(ano_plantio) else float(ano_plantio) + idade)
                linhas.append(
                    (proximo_id, talhao, idade, ano_simulado_negativo, None, custo)
                    + extra_metadados + (None,) * n_colunas_sortimento
                )
                proximo_id += 1

    # Ordena por talhão + idade_simulada (linha[1]/linha[2] — ver
    # colunas_insert) antes de gravar: como "id" é INTEGER PRIMARY KEY
    # (= rowid), a ordem de inserção decide a ordem física da tabela, e
    # um SELECT * sem ORDER BY (ex: Exportar Excel) devolve por rowid —
    # sem isso, as linhas sintéticas de idade anterior à simulação
    # (sempre com id maior, gerado depois) ficariam todas no fim da
    # tabela em vez de intercaladas na idade certa de cada talhão.
    # Reatribui "id" sequencial na ordem final por clareza (o id antigo,
    # herdado de simulacao_talhao_idade nas linhas não-sintéticas, não é
    # referenciado em lugar nenhum fora desta tabela).
    linhas.sort(key=lambda linha: (linha[1], linha[2]))
    linhas = [(i + 1,) + linha[1:] for i, linha in enumerate(linhas)]

    resultado = {
        "executado": True,
        "linhas": len(linhas),
        "colunas_sortimento": list(colunas_resultado.keys()),
        "n_sortimentos": len(sortimentos),
        "n_campos": len(colunas_base_classes),
    }
    if persistir:
        _persistir_volume_sortimento(
            conn, tabela_volume_sortimento, create_table_sql, colunas_insert, marcadores, linhas)
    else:
        resultado["_tabela_volume_sortimento"] = tabela_volume_sortimento
        resultado["_persistir_volume"] = (create_table_sql, colunas_insert, marcadores, linhas)
    return resultado


# Campos de idade/intensidade de manejo de um cenário — mesmos nomes de
# COLUNAS_METADADOS_CENARIO (sem "cenario", tratado à parte) e de
# app/screens/simulacao.py:_CAMPOS_MANEJO_CENARIO (duplicado ali só porque
# é usado também fora de calcular_cenario_em_memoria, ex: exportar_todos_
# cenarios) — usado por calcular_cenario_em_memoria pra montar
# `metadados_cenario`.
_CAMPOS_MANEJO_CENARIO = tuple(nome for nome, _tipo in COLUNAS_METADADOS_CENARIO[1:])


def calcular_cenario_em_memoria(
    conn: sqlite3.Connection, configuracao: Dict, sufixo_tabela: str = "",
    contexto_lote: Optional[Dict] = None,
) -> Optional[Dict]:
    """Calcula um cenário INTEIRO em memória — população + construtores +
    distribuição + volume por sortimento — sem escrever nada no banco
    (`persistir=False` em cada etapa); quem chamar decide quando/como
    persistir (ver persistir_cenario_calculado). Extraído de
    app/screens/simulacao.py:_gerar_uma_simulacao (que persiste
    imediatamente depois, no mesmo processo) pra também poder rodar num
    processo worker à parte (ver _worker_calcular_cenario) — o lote de
    "Múltiplos cenários"/"Grade automática"
    (app/screens/simulacao.py:_ThreadGerarLote) usa um ProcessPoolExecutor
    pra calcular vários cenários em paralelo (um por núcleo de CPU — essa
    etapa é a que mais pesa: geração da população + Weibull + construtores
    de variáveis + volume por sortimento, tudo vetorizado em pandas/numpy,
    mas ainda assim CPU-bound e hoje limitado a 1 núcleo por rodar tudo
    sequencial numa thread só) e SÓ o processo principal persiste (SQLite
    não aceita escrita concorrente de verdade — ver journal_mode=MEMORY em
    core/db.py).

    Devolve o dict `resultado` de sempre (com "tempos_estagios" cobrindo
    populacao/construtores/distribuicao/volume_sortimento — SEM
    "gravacao_final"/"mip_continuo": essas só existem depois da
    persistência, fora desta função) — OU None se algum construtor ativo
    (pra TABELA_POPULACAO) tiver um nó "Custo de Formação" E a coluna de
    talhão (tela Configurações) ainda não estiver mapeada: sem ela não dá
    pra saber por qual coluna agrupar as linhas sintéticas de idade <= 0
    (custo de formação anterior ao plantio, ver
    construtores.aplicar_construtores_em_memoria/_sincronizar_linhas_
    formacao_em_memoria — equivalente em memória de sincronizar_linhas_
    formacao). Com a coluna mapeada, o nó "Custo de Formação" roda
    normalmente aqui, em memória, junto com o resto — só cai pro caminho
    antigo (inteiramente via banco, ver aplicar_construtores_salvos,
    usado por app/screens/simulacao.py:_gerar_uma_simulacao no seu branch
    de fallback) nesse caso raro de coluna não mapeada."""
    # Import tardio: core/construtores.py importa este módulo
    # (`from . import motor_modelos, simulacao`) — um `from . import
    # construtores` no topo do arquivo criaria um ciclo. Mesmo truque de
    # core/db.py:conectar (import tardio de `projeto`).
    from . import construtores

    tempos_estagios = {}
    _marca = time.perf_counter()

    def _medir(nome_estagio):
        nonlocal _marca
        agora = time.perf_counter()
        tempos_estagios[nome_estagio] = agora - _marca
        _marca = agora

    resultado = gerar_populacao(
        conn, configuracao["coluna_talhao_ifc"], configuracao["coluna_fustes_observados"],
        configuracao["idade_raleio"], configuracao["intensidade_raleio"],
        configuracao["idade_desbaste_1"], configuracao["intensidade_desbaste_1"],
        configuracao["idade_desbaste_2"], configuracao["intensidade_desbaste_2"],
        configuracao["idade_corte_raso"],
        coluna_dap_med_observado=configuracao["coluna_dap_med_observado"],
        coluna_dap_max_observado=configuracao["coluna_dap_max_observado"],
        coluna_dap_min_observado=configuracao["coluna_dap_min_observado"],
        coluna_ht_observado=configuracao["coluna_ht_observado"],
        coluna_vtcc_observado=configuracao["coluna_vtcc_observado"],
        coluna_cv_dap_observado=configuracao["coluna_cv_dap_observado"],
        coluna_data_plantio=configuracao["coluna_data_plantio"],
        sufixo_tabela=sufixo_tabela,
        contexto_lote=contexto_lote,
        persistir=False,
    )
    _medir("populacao")

    # Data de medição não entra em nenhuma conta de gerar_populacao (ao
    # contrário da data de plantio) — só lembrada pra próxima vez, mesmo
    # padrão de coluna_forma_distribuicao/coluna_escala_distribuicao logo
    # abaixo (persistida à parte, não por gerar_populacao). É config
    # comum (não varia por cenário no lote), sempre seguro regravar.
    salvar_coluna_data_medicao(conn, configuracao["coluna_data_medicao"])
    # Commita já — não dá pra deixar essa escrita pendurada numa
    # transação aberta: num worker do lote paralelo (ver
    # _worker_calcular_cenario), `conn` é a conexão PRÓPRIA daquele
    # processo, que nunca mais commita sozinha (quem persiste o cenário
    # em si é sempre o processo principal, ver persistir_cenario_
    # calculado) — sem isso, essa UPDATE ficava com lock aberto
    # indefinidamente, travando o processo principal na hora de gravar
    # ("database is locked", já visto na prática). Como o valor é o
    # mesmo pra todo cenário do lote (vem de `configuracao_comum`),
    # commitar aqui, de qualquer worker, é inofensivo.
    conn.commit()

    df_populacao = resultado["_df_populacao"]

    try:
        classes_diametricas = obter_classes_diametricas(conn)
    except ValueError:
        classes_diametricas = None
    sortimentos = conn.execute(
        "SELECT nome, limite_inferior, limite_superior, rendimento, preco, preco_pe "
        "FROM sortimentos ORDER BY limite_inferior, nome"
    ).fetchall()
    config_financeiro = construtores.obter_config_financeiro(conn)
    # NÃO obter_idade_corte_raso(conn) — leria a tabela CANÔNICA (só
    # reflete o cenário ativado por último, ou nem existe ainda: a tabela
    # sufixada deste cenário só é gravada lá na frente, na persistência),
    # dando VET errado pra todo cenário do lote de "Múltiplos cenários"/
    # "Grade automática" cuja idade do Corte Raso não coincidisse por
    # acaso com a da canônica. `configuracao` já tem o valor certo deste
    # cenário especificamente, sem precisar de leitura nenhuma no banco.
    idade_corte_raso = configuracao["idade_corte_raso"]
    dimensoes_tora = construtores.obter_dimensoes_tora(conn)
    custos_colheita = construtores.obter_custos_colheita(conn)
    tipo_normalizacao_weibull = obter_tipo_normalizacao_weibull(conn)
    custos_formacao = construtores.obter_custos_formacao(conn)

    em_memoria = construtores.aplicar_construtores_em_memoria(
        conn, df_populacao, TABELA_POPULACAO, classes_diametricas, sortimentos,
        config_financeiro, idade_corte_raso, dimensoes_tora, custos_colheita,
        tipo_normalizacao_weibull, custos_formacao)
    # None só no caso raro de um construtor ativo ter nó "custo_formacao"
    # SEM a coluna de talhão mapeada (ver docstring) — propaga None pra
    # quem chamou cair pro caminho antigo, via banco (ver
    # app/screens/simulacao.py:_gerar_uma_simulacao).
    if em_memoria is None:
        return None
    df_populacao, resultado["resumo_construtores"] = em_memoria
    _medir("construtores")

    coluna_forma_distribuicao = configuracao["coluna_forma_distribuicao"]
    coluna_escala_distribuicao = configuracao["coluna_escala_distribuicao"]
    salvar_coluna_forma_distribuicao(conn, coluna_forma_distribuicao)
    salvar_coluna_escala_distribuicao(conn, coluna_escala_distribuicao)
    conn.commit()  # ver comentário acima de salvar_coluna_data_medicao — mesmo motivo

    # Se forma/escala da distribuição foram apontadas pra uma coluna
    # própria (ex: gerada por um construtor, recém-reaplicado acima),
    # recalcula a distribuição com elas — gerar_populacao já calculou com
    # forma_atual/escala_atual antes dos construtores rodarem, e sem
    # refazer aqui a distribuição ficaria presa ao valor antigo. Ainda em
    # memória: sobrescreve resultado["_tabela_distribuicao"]/
    # ["_linhas_distribuicao"] (é o que persiste no final).
    resultado["aviso_distribuicao"] = None
    if coluna_forma_distribuicao or coluna_escala_distribuicao:
        coluna_forma_efetiva = coluna_forma_distribuicao or resultado["coluna_forma_atual"]
        coluna_escala_efetiva = coluna_escala_distribuicao or resultado["coluna_escala_atual"]
        try:
            resultado_distribuicao = calcular_distribuicao_diametrica(
                conn, coluna_forma_efetiva, coluna_escala_efetiva, sufixo_tabela=sufixo_tabela,
                contexto_lote=contexto_lote, df_entrada=df_populacao, persistir=False)
            resultado.update(resultado_distribuicao)
        except ValueError as e:
            resultado["aviso_distribuicao"] = (
                f"Simulação gerada, mas não foi possível recalcular a distribuição com "
                f"a coluna de forma/escala apontada:\n{e}\n\n"
                "A distribuição ficou com forma_atual/escala_atual (padrão).")
    _medir("distribuicao")

    # Volume por sortimento: precisa rodar depois dos construtores acima
    # — é lá que as colunas por classe (ex: vtcc_5, vtcc_7, ...) usadas
    # aqui são geradas. A seleção/agregação é sempre salva como está
    # (independente do checkbox "Tabela de agregação") — só a CHAMADA de
    # calcular_volume_por_sortimento abaixo é pulada com o checkbox
    # desligado, preservando a seleção pra quando ele for religado.
    salvar_colunas_base_volume_classes(conn, configuracao["colunas_volume_classes"])
    salvar_tipo_agregacao_volume(conn, configuracao["tipo_agregacao_volume"])
    salvar_usar_tabela_agregacao_volume(conn, configuracao["usar_tabela_agregacao_volume"])
    conn.commit()  # ver comentário acima de salvar_coluna_data_medicao — mesmo motivo
    resultado["aviso_volume_sortimento"] = None
    metadados_cenario = None
    if configuracao.get("nome_cenario"):
        metadados_cenario = {"cenario": configuracao["nome_cenario"]}
        metadados_cenario.update({campo: configuracao.get(campo) for campo in _CAMPOS_MANEJO_CENARIO})
    colunas_volume_efetivas = (
        configuracao["colunas_volume_classes"] if configuracao["usar_tabela_agregacao_volume"] else [])
    try:
        resultado["resultado_volume_sortimento"] = calcular_volume_por_sortimento(
            conn, colunas_volume_efetivas, configuracao["tipo_agregacao_volume"],
            sufixo_tabela=sufixo_tabela, metadados_cenario=metadados_cenario,
            df_entrada=df_populacao, persistir=False)
    except ValueError as e:
        resultado["resultado_volume_sortimento"] = {"executado": False}
        resultado["aviso_volume_sortimento"] = (
            f"Simulação gerada, mas não foi possível calcular o volume por sortimento:\n{e}")
    _medir("volume_sortimento")

    resultado["_df_populacao"] = df_populacao  # sobrescreve com a versão já com colunas de construtor
    resultado["tempos_estagios"] = tempos_estagios
    return resultado


def persistir_cenario_calculado(
    conn: sqlite3.Connection, resultado: Dict, sufixo_tabela: str = "", commit: bool = True,
    cenario_id: Optional[int] = None, proximo_id_populacao: Optional[int] = None,
) -> None:
    """Persiste (gravação única de população, distribuição e volume por
    sortimento) o resultado de calcular_cenario_em_memoria — separado dela
    de propósito: quem calculou (processo principal OU um worker do lote
    paralelo, ver _worker_calcular_cenario) nem sempre é quem persiste (só
    o processo principal grava no arquivo de trabalho, ver
    app/screens/simulacao.py:_ThreadGerarLote). Ao final, limpa de
    `resultado` as chaves internas (prefixo "_") que só faziam sentido pra
    persistir — quem chamou fica só com o resultado "público" (avisos,
    resumo_construtores etc).

    `cenario_id` (opcional): quando informado, este é UM cenário do lote
    "Múltiplos cenários"/"Grade automática" — grava nas tabelas
    UNIFICADAS simulacao_lote_* (ver persistir_cenario_no_lote) em vez de
    DROP+CREATE numa tabela "{sufixo_tabela}" própria: é essa troca que
    elimina o custo crescente de DDL que causava "database is locked" em
    lotes grandes. Nesse caso `proximo_id_populacao` é obrigatório (ver
    persistir_cenario_no_lote) e `sufixo_tabela` é ignorado — o resultado
    ganha "_proximo_id_populacao_lote" (quem chamou, no laço do lote,
    passa esse valor de volta como `proximo_id_populacao` do próximo
    cenário; ver app/screens/simulacao.py:_executar_lote_sequencial/
    _executar_lote_paralelo). Geração de cenário ÚNICO ("Gerar
    simulação") nunca passa `cenario_id` — segue exatamente como sempre
    foi, DROP+CREATE nas tabelas canônicas (sufixo_tabela="") ou numa
    tabela sufixada específica."""
    if cenario_id is not None:
        assert proximo_id_populacao is not None, (
            "persistir_cenario_calculado com cenario_id precisa de proximo_id_populacao")
        resultado["_proximo_id_populacao_lote"] = persistir_cenario_no_lote(
            conn, cenario_id, resultado, proximo_id_populacao)
    else:
        df_populacao = resultado["_df_populacao"]
        # "colunas_extra": as colunas de construtor mescladas por
        # aplicar_construtores_em_memoria não estavam em "_colunas_insert"
        # (montado por gerar_populacao ANTES dos construtores rodarem).
        _persistir_populacao(
            conn, resultado["_tabela_populacao"], resultado["_tabela_distribuicao"],
            resultado["_create_table_sql"], df_populacao, resultado["_colunas_insert"],
            colunas_extra=resultado["resumo_construtores"]["colunas_adicionadas"], commit=False)
        _persistir_distribuicao(
            conn, resultado["_tabela_distribuicao"], resultado["_tabela_populacao"],
            resultado["_linhas_distribuicao"], commit=False)
        # Nome sempre computável mesmo se calcular_volume_por_sortimento
        # levantou ValueError antes de chegar num "return" (nesse caso o
        # dict de resultado não tem "_tabela_volume_sortimento") — sem
        # isso, uma tabela de volume de uma geração ANTERIOR bem-sucedida
        # ficaria stale em vez de dropada (mesma garantia de sempre:
        # "sempre derruba a tabela antiga primeiro", ver docstring de
        # calcular_volume_por_sortimento).
        res_volume = resultado["resultado_volume_sortimento"]
        persistir_volume = res_volume.pop("_persistir_volume", None)
        tabela_volume_sortimento = res_volume.pop(
            "_tabela_volume_sortimento", TABELA_VOLUME_SORTIMENTO + sufixo_tabela)
        _persistir_volume_sortimento(
            conn, tabela_volume_sortimento, *(persistir_volume or (None, None, None, None)),
            commit=False)
    if commit:
        conn.commit()
    for chave in ("_df_populacao", "_tabela_populacao", "_create_table_sql", "_colunas_insert",
                  "_tabela_distribuicao", "_linhas_distribuicao"):
        resultado.pop(chave, None)


# ---------------- tabelas unificadas do lote (simulacao_lote_*) ----------------
# Ver TABELA_LOTE_POPULACAO etc. mais acima pro porquê: substituem uma
# tabela "__cenarioN" por cenário (DROP+CREATE, milhares de vezes num lote
# grande — cada CREATE TABLE bate o schema_version do SQLite, invalidando
# o cache de schema de TODAS as outras conexões abertas, inclusive as dos
# até 32 processos-worker lendo em paralelo; o custo disso cresce com o
# número de tabelas acumuladas, não com o tamanho de cada uma — foi o que
# causou "database is locked" em cascata num lote de 2112 cenários) por 5
# tabelas fixas, uma linha por cenário (coluna cenario_id), gravadas via
# DELETE+INSERT (idempotente — cobre "Reiniciar"/reprocessar um cenário
# com erro) em vez de DROP+CREATE.

def _create_table_sql_lote(create_table_sql_original: str, tabela_lote: str, colunas_extra_sql: str = "") -> str:
    """Deriva o CREATE TABLE de uma tabela `simulacao_lote_*` a partir do
    `create_table_sql` já montado pro cenário único/sufixado (ex:
    gerar_populacao/calcular_volume_por_sortimento) — troca só o nome da
    tabela (o que vem depois de `CREATE TABLE "..."`, seja qual for,
    canônico ou sufixado com "__cenarioN") por `tabela_lote`, liga
    `IF NOT EXISTS` (idempotente entre cenários do mesmo lote — só cria de
    verdade na primeira vez) e acrescenta `cenario_id INTEGER NOT NULL`
    (mais qualquer `colunas_extra_sql` pedida) antes do parêntese final."""
    sql = re.sub(
        r'CREATE TABLE "[^"]+"', f'CREATE TABLE IF NOT EXISTS "{tabela_lote}"',
        create_table_sql_original, count=1)
    sql = sql.rstrip()
    assert sql.endswith(")"), "create_table_sql em formato inesperado"
    return sql[:-1] + f', cenario_id INTEGER NOT NULL{colunas_extra_sql})'


def _garantir_tabelas_lote(
    conn: sqlite3.Connection, create_table_sql_populacao: str, colunas_extra_populacao: list,
    persistir_volume: Optional[tuple],
) -> None:
    """Cria (uma vez, `CREATE TABLE IF NOT EXISTS`) e mantém em dia
    (`ALTER TABLE ADD COLUMN`, mesmo padrão de app/core/db.py:
    _adicionar_colunas_faltantes/COLUNAS_NOVAS) o schema de
    simulacao_lote_populacao/simulacao_lote_distribuicao_diametrica/
    simulacao_lote_volume_sortimento — chamada a cada cenário persistido
    (ver persistir_cenario_no_lote), mas cara só na 1ª vez: depois disso é
    um `CREATE TABLE IF NOT EXISTS` (no-op) + alguns `PRAGMA table_info`,
    desprezível perto de inserir dezenas/centenas de milhares de linhas.

    As colunas variam por PROJETO (base IFC + construtores ativos), não
    por cenário dentro de um mesmo lote — todos compartilham o mesmo
    contexto (ver preparar_contexto_lote) — mas podem mudar de um lote pro
    próximo NO MESMO projeto (um construtor editado entre uma rodada e
    outra) — daí o ALTER TABLE cobrindo esse caso a cada chamada, não só
    na criação. Forward-only: nunca remove uma coluna que passou a faltar
    (mesma limitação que COLUNAS_NOVAS já tem em db.py)."""
    from .db import _adicionar_colunas_faltantes

    sql_populacao = _create_table_sql_lote(create_table_sql_populacao, TABELA_LOTE_POPULACAO)
    conn.execute(sql_populacao)
    if colunas_extra_populacao:
        _adicionar_colunas_faltantes(
            conn, TABELA_LOTE_POPULACAO, {c: "REAL" for c in colunas_extra_populacao})
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{TABELA_LOTE_DISTRIBUICAO}" ('
        "id INTEGER PRIMARY KEY AUTOINCREMENT, cenario_id INTEGER NOT NULL, "
        "populacao_id INTEGER NOT NULL, classe_diametrica REAL NOT NULL, "
        "probabilidade REAL, densidade REAL)"
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS idx_lote_distribuicao_cenario_populacao '
        f'ON "{TABELA_LOTE_DISTRIBUICAO}"(cenario_id, populacao_id)'
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS idx_lote_populacao_cenario '
        f'ON "{TABELA_LOTE_POPULACAO}"(cenario_id)'
    )

    if persistir_volume is not None:
        create_table_sql_volume, colunas_insert_volume, _marcadores, _linhas = persistir_volume
        sql_volume = _create_table_sql_lote(create_table_sql_volume, TABELA_LOTE_VOLUME_SORTIMENTO)
        conn.execute(sql_volume)
        # Colunas de sortimento variam com o que foi cadastrado em
        # Configurações — mesma lógica de drift entre lotes que a
        # população tem acima, então mesmo tratamento (ALTER TABLE ADD
        # COLUMN pras que ainda não existirem).
        colunas_existentes = {
            linha[1] for linha in conn.execute(f'PRAGMA table_info("{TABELA_LOTE_VOLUME_SORTIMENTO}")')
        }
        colunas_faltantes = [c for c in colunas_insert_volume if c not in colunas_existentes]
        if colunas_faltantes:
            _adicionar_colunas_faltantes(
                conn, TABELA_LOTE_VOLUME_SORTIMENTO, {c: "REAL" for c in colunas_faltantes})
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_lote_volume_sortimento_cenario '
            f'ON "{TABELA_LOTE_VOLUME_SORTIMENTO}"(cenario_id)'
        )


def _persistir_populacao_lote(
    conn: sqlite3.Connection, cenario_id: int, df_populacao: pd.DataFrame,
    ids_populacao_global, colunas_insert: list, colunas_extra: Optional[list] = None,
) -> None:
    """DELETE (idempotente) + INSERT de um cenário em
    simulacao_lote_populacao — mesmos dados de _persistir_populacao, só
    que numa tabela compartilhada por todo o lote (cenario_id na linha em
    vez de sufixo na tabela) e com "id" GLOBAL (`ids_populacao_global`,
    já deslocado por quem chamou — ver persistir_cenario_no_lote) em vez
    do id local 1..N que gerar_populacao atribuiu."""
    conn.execute(f'DELETE FROM "{TABELA_LOTE_POPULACAO}" WHERE cenario_id = ?', (cenario_id,))
    if colunas_extra:
        colunas_insert = list(colunas_insert) + list(colunas_extra)
    # Substitui o "id" LOCAL (1..N, atribuído por gerar_populacao) pelo
    # GLOBAL já deslocado por quem chamou (ver persistir_cenario_no_lote)
    # — `df_populacao` não é mais usada depois disto (persistir_cenario_
    # calculado descarta "_df_populacao" ao final), então sobrescrever a
    # coluna aqui é seguro.
    df_lote = df_populacao[colunas_insert].copy()
    df_lote["id"] = ids_populacao_global
    df_lote["cenario_id"] = cenario_id
    colunas_finais = colunas_insert + ["cenario_id"]
    nomes_insert = ", ".join(f'"{c}"' for c in colunas_finais)
    marcadores = ", ".join("?" for _ in colunas_finais)
    linhas = [
        tuple(None if pd.isna(v) else v for v in linha)
        for linha in df_lote[colunas_finais].itertuples(index=False, name=None)
    ]
    conn.executemany(
        f'INSERT INTO "{TABELA_LOTE_POPULACAO}" ({nomes_insert}) VALUES ({marcadores})', linhas
    )


def _persistir_distribuicao_lote(
    conn: sqlite3.Connection, cenario_id: int, linhas_distribuicao_dados, offset_populacao: int,
) -> None:
    """DELETE (idempotente) + INSERT de um cenário em
    simulacao_lote_distribuicao_diametrica — mesmo formato de dados de
    _persistir_distribuicao (tupla de 4 arrays paralelos), só que
    `populacao_id` recebe o MESMO deslocamento (`offset_populacao`) que
    _persistir_populacao_lote aplicou no "id" da população, senão a
    ligação lógica entre as duas tabelas fica incorreta."""
    conn.execute(f'DELETE FROM "{TABELA_LOTE_DISTRIBUICAO}" WHERE cenario_id = ?', (cenario_id,))
    if not linhas_distribuicao_dados:
        return
    ids_repetidos, classes_repetidas, probabilidades_lineares, densidades_lineares = (
        linhas_distribuicao_dados)
    ids_globais = (np.asarray(ids_repetidos) + offset_populacao).tolist()
    linhas = list(zip(
        [cenario_id] * len(ids_globais), ids_globais,
        np.asarray(classes_repetidas).tolist(), np.asarray(probabilidades_lineares).tolist(),
        np.asarray(densidades_lineares).tolist()))
    conn.executemany(
        f'INSERT INTO "{TABELA_LOTE_DISTRIBUICAO}" '
        "(cenario_id, populacao_id, classe_diametrica, probabilidade, densidade) "
        "VALUES (?, ?, ?, ?, ?)",
        linhas,
    )


def _persistir_volume_sortimento_lote(
    conn: sqlite3.Connection, cenario_id: int, persistir_volume: Optional[tuple],
) -> None:
    """DELETE (idempotente) + INSERT de um cenário em
    simulacao_lote_volume_sortimento. `persistir_volume` é o mesmo
    `(create_table_sql, colunas_insert, marcadores, linhas)` de
    resultado["resultado_volume_sortimento"]["_persistir_volume"] (ver
    calcular_volume_por_sortimento) — None quando esse cenário não teve
    volume por sortimento calculado (nenhuma coluna de classe selecionada,
    sem sortimento cadastrado etc), caso em que só o DELETE roda (limpa
    um resultado anterior, mesma garantia de _persistir_volume_
    sortimento: "sempre derruba, mesmo sem recriar") — e só quando a
    tabela já existe: _garantir_tabelas_lote só cria
    simulacao_lote_volume_sortimento quando ALGUM cenário do lote tem
    volume calculado; se o primeiro cenário processado não tiver (e
    nenhum antes dele também não), a tabela ainda nem existe."""
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABELA_LOTE_VOLUME_SORTIMENTO,)
    ).fetchone()
    if existe is not None:
        conn.execute(
            f'DELETE FROM "{TABELA_LOTE_VOLUME_SORTIMENTO}" WHERE cenario_id = ?', (cenario_id,))
    if persistir_volume is None:
        return
    _create_table_sql, colunas_insert, _marcadores, linhas = persistir_volume
    if not linhas:
        return
    nomes_insert = ", ".join(f'"{c}"' for c in colunas_insert) + ', "cenario_id"'
    marcadores = ", ".join("?" for _ in colunas_insert) + ", ?"
    linhas_com_cenario = [tuple(linha) + (cenario_id,) for linha in linhas]
    conn.executemany(
        f'INSERT INTO "{TABELA_LOTE_VOLUME_SORTIMENTO}" ({nomes_insert}) VALUES ({marcadores})',
        linhas_com_cenario,
    )


def proximo_id_populacao_lote(conn: sqlite3.Connection) -> int:
    """Próximo id GLOBAL livre pra gravar em simulacao_lote_populacao —
    chamado UMA VEZ no início de um lote (ver
    app/screens/simulacao.py:_executar_lote_sequencial/_executar_lote_
    paralelo), depois mantido incrementando em memória (cada
    persistir_cenario_calculado devolve o próximo via
    resultado["_proximo_id_populacao_lote"]) sem reconsultar o banco a
    cada cenário. 1 se a tabela ainda não existir (1º lote do projeto)."""
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_LOTE_POPULACAO,)
    ).fetchone()
    if existe is None:
        return 1
    maximo = conn.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{TABELA_LOTE_POPULACAO}"').fetchone()[0]
    return int(maximo) + 1


def persistir_cenario_no_lote(
    conn: sqlite3.Connection, cenario_id: int, resultado: Dict, proximo_id_populacao: int,
) -> int:
    """Persiste UM cenário do lote "Múltiplos cenários"/"Grade automática"
    nas tabelas unificadas simulacao_lote_* — DELETE (idempotente, cobre
    "Reiniciar" e reprocessar um cenário com erro via "Gerar pendentes") +
    INSERT, nunca DROP+CREATE (ver comentário da seção acima pro porquê).
    Chamada por persistir_cenario_calculado quando `cenario_id` é passado
    — não chame direto, é o `resultado` já montado por
    calcular_cenario_em_memoria que ela espera.

    Devolve o próximo id livre de população DEPOIS deste cenário
    (`proximo_id_populacao` + linhas de população gravadas aqui) — quem
    chama o lote usa isso como `proximo_id_populacao` do cenário
    seguinte."""
    df_populacao = resultado["_df_populacao"]
    res_volume = resultado["resultado_volume_sortimento"]
    persistir_volume = res_volume.get("_persistir_volume")

    _garantir_tabelas_lote(
        conn, resultado["_create_table_sql"], resultado["resumo_construtores"]["colunas_adicionadas"],
        persistir_volume)

    offset = proximo_id_populacao - 1
    ids_populacao_global = (df_populacao["id"].to_numpy() + offset)

    _persistir_populacao_lote(
        conn, cenario_id, df_populacao, ids_populacao_global, resultado["_colunas_insert"],
        colunas_extra=resultado["resumo_construtores"]["colunas_adicionadas"])
    _persistir_distribuicao_lote(conn, cenario_id, resultado["_linhas_distribuicao"], offset)
    res_volume.pop("_persistir_volume", None)
    _persistir_volume_sortimento_lote(conn, cenario_id, persistir_volume)

    return proximo_id_populacao + len(df_populacao)


# ---------------- lote paralelo (ProcessPoolExecutor) ----------------
# Usado só por app/screens/simulacao.py:_ThreadGerarLote (modo "Múltiplos
# cenários"/"Grade automática") — cada processo worker roda
# _worker_inicializar_lote UMA VEZ (initializer do ProcessPoolExecutor,
# não repete a cada cenário) e depois _worker_calcular_cenario pra cada
# cenário que esse worker pegar. Módulo-level (não método/lambda/closure)
# de propósito: o multiprocessing (`spawn`, padrão no Windows) precisa
# picklar a referência da função pra mandar pro processo novo — só
# funciona com algo importável pelo nome, no topo do módulo.
_worker_conn: Optional[sqlite3.Connection] = None
_worker_contexto_lote: Optional[Dict] = None


def _gravar_resultado_worker_parquet(resultado: Dict) -> Dict:
    """Grava o payload grande de um worker fora do pipe do multiprocessing.

    ``ProcessPoolExecutor`` serializa o valor de retorno com pickle. Um
    cenário grande contém centenas de milhares de tuplas da distribuição
    e um DataFrame largo; devolver isso diretamente cria pressão no pipe e
    na thread de result-handler do processo principal. Aqui cada worker
    materializa as duas partes grandes em Parquet e devolve apenas um
    manifesto pequeno. O restante do dict (avisos, tempos, schema e resumo
    dos construtores) continua em um pickle local pequeno.

    Os arquivos são transitórios: o processo principal os lê, persiste no
    formato atual e remove a pasta em ``carregar_resultado_worker_parquet``.
    Essa separação é intencional para permitir migrar a persistência
    permanente para Parquet sem quebrar projetos SQLite existentes.
    """
    pasta = Path(tempfile.mkdtemp(prefix="khaya_cenario_parquet_"))
    try:
        df_populacao = resultado.pop("_df_populacao")
        linhas_distribuicao = resultado.pop("_linhas_distribuicao")

        caminho_populacao = pasta / "populacao.parquet"
        caminho_distribuicao = pasta / "distribuicao.parquet"
        caminho_metadados = pasta / "metadados.pkl"

        formato_populacao = "parquet"
        try:
            df_populacao.to_parquet(
                caminho_populacao, engine="pyarrow", compression="zstd", index=False)
        except (TypeError, ValueError, OverflowError):
            # SQLite aceita tipos heterogêneos na mesma coluna; Arrow não.
            # Bases antigas podem, por exemplo, misturar texto e número em
            # uma coluna livre. O fallback continua fora do IPC (objetivo
            # principal desta camada), sem converter silenciosamente tipos.
            caminho_populacao = pasta / "populacao.pkl"
            df_populacao.to_pickle(caminho_populacao)
            formato_populacao = "pickle"
        if linhas_distribuicao:
            df_distribuicao = pd.DataFrame({
                "populacao_id": np.asarray(linhas_distribuicao[0]),
                "classe_diametrica": np.asarray(linhas_distribuicao[1]),
                "probabilidade": np.asarray(linhas_distribuicao[2]),
                "densidade": np.asarray(linhas_distribuicao[3]),
            })
        else:
            df_distribuicao = pd.DataFrame(columns=(
                "populacao_id", "classe_diametrica", "probabilidade", "densidade"))
        df_distribuicao.to_parquet(
            caminho_distribuicao, engine="pyarrow", compression="zstd", index=False)

        with open(caminho_metadados, "wb") as arquivo:
            pickle.dump(resultado, arquivo, protocol=pickle.HIGHEST_PROTOCOL)

        return {
            "_manifesto_parquet": True,
            "pasta": str(pasta),
            "populacao": str(caminho_populacao),
            "formato_populacao": formato_populacao,
            "distribuicao": str(caminho_distribuicao),
            "metadados": str(caminho_metadados),
        }
    except Exception:
        shutil.rmtree(pasta, ignore_errors=True)
        raise


def carregar_resultado_worker_parquet(manifesto: Dict, remover: bool = True) -> Dict:
    """Reconstrói um resultado transitório gravado pelo worker.

    A leitura ocorre no processo principal sem atravessar o pipe. Mantém o
    contrato de ``persistir_cenario_calculado`` enquanto o armazenamento
    permanente do lote ainda oferece compatibilidade com SQLite.
    """
    if not manifesto.get("_manifesto_parquet"):
        return manifesto
    pasta = Path(manifesto["pasta"])
    try:
        with open(manifesto["metadados"], "rb") as arquivo:
            resultado = pickle.load(arquivo)
        if manifesto.get("formato_populacao", "parquet") == "pickle":
            resultado["_df_populacao"] = pd.read_pickle(manifesto["populacao"])
        else:
            resultado["_df_populacao"] = pd.read_parquet(
                manifesto["populacao"], engine="pyarrow")
        df_distribuicao = pd.read_parquet(manifesto["distribuicao"], engine="pyarrow")
        resultado["_linhas_distribuicao"] = (
            tuple(df_distribuicao[c].to_numpy() for c in df_distribuicao.columns)
            if not df_distribuicao.empty else ())
        return resultado
    finally:
        if remover:
            shutil.rmtree(pasta, ignore_errors=True)


def remover_resultado_worker_parquet(manifesto: Dict) -> None:
    """Remove um resultado transitório que não chegou a ser consumido."""
    if manifesto and manifesto.get("_manifesto_parquet"):
        shutil.rmtree(manifesto.get("pasta", ""), ignore_errors=True)


def persistir_manifesto_parquet(
    conn: sqlite3.Connection, cenario_id: int, manifesto: Dict, remover: bool = True,
) -> Dict:
    """Guarda os arquivos produzidos pelo worker como BLOBs comprimidos.

    Retorna somente os metadados pequenos do cenário, usados pelo relatório
    do lote. Não desserializa população/distribuição nem executa milhões de
    INSERTs. A transação/commit permanece sob controle do chamador.
    """
    pasta = Path(manifesto["pasta"])
    try:
        _garantir_tabela_cenarios_parquet(conn)
        metadados_bytes = Path(manifesto["metadados"]).read_bytes()
        metadados = pickle.loads(metadados_bytes)
        conn.execute(
            f'INSERT INTO "{TABELA_CENARIOS_PARQUET}" '
            '(cenario_id, formato_populacao, populacao, distribuicao, metadados, atualizado_em) '
            "VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(cenario_id) DO UPDATE SET "
            "formato_populacao=excluded.formato_populacao, populacao=excluded.populacao, "
            "distribuicao=excluded.distribuicao, metadados=excluded.metadados, "
            "atualizado_em=excluded.atualizado_em",
            (
                cenario_id,
                manifesto.get("formato_populacao", "parquet"),
                sqlite3.Binary(Path(manifesto["populacao"]).read_bytes()),
                sqlite3.Binary(Path(manifesto["distribuicao"]).read_bytes()),
                sqlite3.Binary(metadados_bytes),
            ),
        )
        return metadados
    finally:
        if remover:
            shutil.rmtree(pasta, ignore_errors=True)


def carregar_cenario_parquet(conn: sqlite3.Connection, cenario_id: int) -> Optional[Dict]:
    """Carrega um cenário do armazenamento Parquet interno do .mogno."""
    if not _existe_tabela_cenarios_parquet(conn):
        return None
    linha = conn.execute(
        f'SELECT formato_populacao, populacao, distribuicao, metadados '
        f'FROM "{TABELA_CENARIOS_PARQUET}" WHERE cenario_id = ?', (cenario_id,)
    ).fetchone()
    if linha is None:
        return None
    formato, populacao_bytes, distribuicao_bytes, metadados_bytes = linha
    resultado = pickle.loads(metadados_bytes)
    if formato == "pickle":
        resultado["_df_populacao"] = pickle.loads(populacao_bytes)
    else:
        resultado["_df_populacao"] = pd.read_parquet(
            io.BytesIO(populacao_bytes), engine="pyarrow")
    df_distribuicao = pd.read_parquet(io.BytesIO(distribuicao_bytes), engine="pyarrow")
    resultado["_linhas_distribuicao"] = (
        tuple(df_distribuicao[c].to_numpy() for c in df_distribuicao.columns)
        if not df_distribuicao.empty else ())
    return resultado


def carregar_populacao_cenario_parquet(
    conn: sqlite3.Connection, cenario_id: int,
) -> Optional[pd.DataFrame]:
    """Lê somente a população de um cenário, sem inflar a distribuição."""
    if not _existe_tabela_cenarios_parquet(conn):
        return None
    linha = conn.execute(
        f'SELECT formato_populacao, populacao FROM "{TABELA_CENARIOS_PARQUET}" '
        "WHERE cenario_id = ?", (cenario_id,)
    ).fetchone()
    if linha is None:
        return None
    formato, dados = linha
    if formato == "pickle":
        return pickle.loads(dados)
    return pd.read_parquet(io.BytesIO(dados), engine="pyarrow")


def _preparar_resultado_parquet_canonico(resultado: Dict) -> Dict:
    """Troca os nomes sufixados do lote pelos nomes canônicos."""
    resultado["_tabela_populacao"] = TABELA_POPULACAO
    resultado["_tabela_distribuicao"] = TABELA_DISTRIBUICAO
    sql_populacao = resultado.get("_create_table_sql")
    if sql_populacao:
        resultado["_create_table_sql"] = re.sub(
            r'^(CREATE TABLE\s+)"[^"]+"',
            rf'\1"{TABELA_POPULACAO}"', sql_populacao, count=1, flags=re.IGNORECASE)
    res_volume = resultado.get("resultado_volume_sortimento", {})
    persistir_volume = res_volume.get("_persistir_volume")
    if persistir_volume:
        sql, colunas, marcadores, linhas = persistir_volume
        sql = re.sub(
            r'^(CREATE TABLE\s+)"[^"]+"',
            rf'\1"{TABELA_VOLUME_SORTIMENTO}"', sql, count=1, flags=re.IGNORECASE)
        res_volume["_persistir_volume"] = (sql, colunas, marcadores, linhas)
    res_volume["_tabela_volume_sortimento"] = TABELA_VOLUME_SORTIMENTO
    return resultado


def _worker_inicializar_lote(caminho_trabalho: str, contexto_lote: Dict) -> None:
    """Roda UMA VEZ em cada processo worker do lote paralelo, antes do
    1º cenário — abre a conexão SQLite deste processo (usada só pra
    leitura: config financeira, sortimentos, construtores salvos etc —
    quem grava é sempre o processo principal, ver persistir_cenario_
    calculado) e guarda o baseline comum (`contexto_lote`, já pronto do
    processo principal, mandado uma vez só via `initargs` do
    ProcessPoolExecutor em vez de repicklado a cada cenário) em globais
    do módulo, lidas por _worker_calcular_cenario.

    Também limita threads internas do BLAS/OpenMP (numpy/scipy) a 1 por
    processo — sem isso, cada um dos N processos workers tentaria abrir
    seu próprio pool de threads pra multiplicação de matriz/álgebra
    linear, competindo entre si pelos mesmos núcleos que o
    ProcessPoolExecutor já está usando pra paralelizar por CENÁRIO
    (oversubscription: N processos × M threads cada nos mesmos núcleos
    piora o tempo total em vez de melhorar — e satura a máquina inteira a
    ponto de a THREAD DA GUI do processo principal ficar sem CPU pra
    processar sua fila de mensagens, o Windows relata "Não está
    respondendo" mesmo sem nenhum deadlock de verdade).

    As variáveis de ambiente abaixo (`os.environ.setdefault`) só afetam
    threads nativas que ainda vão ler esse valor pela primeira vez — mas
    numpy/scipy (`import numpy as np`/`from scipy.optimize import
    curve_fit`, topo deste módulo) já foram importados, e já inicializaram
    seu backend BLAS, no momento em que o multiprocessing (`spawn`,
    padrão no Windows) precisou importar este módulo só pra localizar
    esta própria função — ANTES do corpo dela rodar. Setar a env var aqui
    é tarde demais pro backend já carregado; por isso `threadpool_limits`
    (threadpoolctl) também é chamado logo abaixo — ele ajusta o limite
    de threads em tempo de execução, direto na biblioteca nativa já
    carregada, então funciona mesmo depois do import. Chamado sem `with`
    (não como context manager) de propósito: o limite deve valer pelo
    resto da vida deste processo worker, que só serve pra calcular
    cenários — não há nada depois disso que precise dos threads de volta."""
    import os as _os

    for _var in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS"):
        _os.environ.setdefault(_var, "1")

    import threadpoolctl
    threadpoolctl.threadpool_limits(limits=1)

    global _worker_conn, _worker_contexto_lote
    from .db import conectar_caminho_com_retry
    from . import construtores as _construtores
    # Com retry (não conectar_caminho direto): uma falha aqui é FATAL pro
    # lote inteiro, não só pra este cenário — o ProcessPoolExecutor
    # derruba o pool inteiro se o initializer levantar (ver docstring de
    # conectar_caminho_com_retry pro cenário real que motivou isso: um
    # worker tentando conectar bem no meio de uma regravação do .mogno em
    # segundo plano, projeto.sincronizar, que pode legitimamente passar
    # dos 30s do busy_timeout normal num projeto grande).
    cache_afilamento = contexto_lote.pop("cache_afilamento", None)
    _construtores.instalar_cache_afilamento(cache_afilamento)
    _worker_conn = conectar_caminho_com_retry(caminho_trabalho)
    _worker_contexto_lote = contexto_lote


def _worker_calcular_cenario(configuracao: Dict, sufixo_tabela: str) -> Dict:
    """Roda num processo worker do lote paralelo — calcula UM cenário
    inteiro em memória (ver calcular_cenario_em_memoria) e devolve o
    resultado pro processo principal persistir (nunca escreve no banco;
    SQLite não aceita escrita concorrente de verdade). Só é chamada
    quando o lote já garantiu de antemão (ver
    app/screens/simulacao.py:_ThreadGerarLote.run) que nenhum construtor
    ativo tem nó "Custo de Formação" — na prática calcular_cenario_em_
    memoria não deveria devolver None aqui; se devolver mesmo assim (ex:
    um construtor foi ativado/editado NO MEIO do lote, entre a checagem e
    agora), levanta um erro claro em vez de devolver None calado, que o
    processo principal não saberia persistir.

    `_t_inicio_worker`/`_t_fim_worker` (chaves internas, removidas pelo
    processo principal antes do resultado "público" — ver
    _executar_lote_paralelo): timestamps `time.perf_counter()` batidos
    NESTE processo, logo antes/depois do cálculo — comparados no processo
    principal contra o t_submit (quando pool.submit() foi chamado) e o
    t_pronto (quando wait() reportou a future pronta), separam "tempo até
    o worker começar a rodar de verdade" (fila de despacho — pode incluir
    o processo ainda esquentando: import de numpy/scipy, _worker_
    inicializar_lote) de "tempo entre o worker terminar e o resultado
    chegar no processo principal" (serialização + IPC de volta) — a
    métrica anterior ("fila_ipc") só tinha o total dos dois somados, sem
    dar pra saber qual dos dois pesava. `time.perf_counter()` é um
    relógio monotônico do sistema (não por processo) em Windows/Linux,
    então dá pra comparar valores batidos em processos diferentes — não é
    garantia formal da stdlib, mas suficiente pra uma métrica diagnóstica
    na casa de segundos, não nanossegundos."""
    _t_inicio_worker = time.perf_counter()
    resultado = calcular_cenario_em_memoria(
        _worker_conn, configuracao, sufixo_tabela=sufixo_tabela, contexto_lote=_worker_contexto_lote)
    if resultado is None:
        raise RuntimeError(
            "Um construtor de variáveis com nó \"Custo de Formação\" foi ativado durante o lote — "
            "esse caso não roda em paralelo. Pare o lote e rode de novo.")
    resultado["_t_inicio_worker"] = _t_inicio_worker
    _marca_parquet = time.perf_counter()
    manifesto = _gravar_resultado_worker_parquet(resultado)
    manifesto["_t_fim_calculo_worker"] = _marca_parquet
    manifesto["_t_fim_worker"] = time.perf_counter()
    return manifesto


_TABELAS_LOTE_POR_CANONICA = (
    (TABELA_DISTRIBUICAO, TABELA_LOTE_DISTRIBUICAO),
    (TABELA_POPULACAO, TABELA_LOTE_POPULACAO),
    (TABELA_VOLUME_SORTIMENTO, TABELA_LOTE_VOLUME_SORTIMENTO),
    (TABELA_MIP_CONTINUO, TABELA_LOTE_MIP_CONTINUO),
    (TABELA_MIP_AJUSTE_LOGISTICO, TABELA_LOTE_MIP_AJUSTE_LOGISTICO),
)


def ativar_cenario(conn: sqlite3.Connection, cenario_id: int) -> None:
    """"Ativa" um cenário gerado pelo modo "Múltiplos cenários" (tela
    Simulação, tabela `simulacao_cenarios`): copia as linhas desse
    `cenario_id` nas tabelas unificadas do lote (`simulacao_lote_*`, ver
    persistir_cenario_no_lote) pra dentro dos nomes canônicos
    (`simulacao_talhao_idade`, `simulacao_distribuicao_diametrica`,
    `simulacao_volume_sortimento`, MIP) — depois disso, Construtor de
    Variáveis, Gráfico de Resultados e Exportar Excel (que só conhecem os
    nomes canônicos) passam a refletir esse cenário, sem precisar de
    nenhuma mudança neles.

    Nunca usa `SELECT *` daqui: a tabela unificada carrega uma coluna
    `cenario_id` que a canônica nunca teve — vazar ela pra dentro
    apareceria como uma "coluna de KPI" espúria em
    colunas_grafico_resultado_disponiveis/exportações desse cenário.
    Enumera as colunas via `PRAGMA table_info` excluindo `cenario_id`.

    Se uma das tabelas do lote não existir ou não tiver linha desse
    `cenario_id` (ex: esse cenário não tinha coluna de volume por classe
    configurada, ou MIP contínuo não foi calculado), a canônica
    correspondente só é derrubada, não recriada — mesmo comportamento de
    quando essa etapa é pulada numa geração única.

    Levanta ValueError se o cenário não tiver NENHUMA linha em NENHUMA
    tabela do lote — cenário gerado por uma versão anterior do app
    (tabelas "__cenarioN" antigas, nunca migradas) ou status
    inconsistente; não mexe nas tabelas canônicas nesse caso, pra não
    apagar o que estava ativado antes sem ter com o que substituir.

    Não mexe na tabela do lote em si (fica intacta — dá pra ativar de
    novo, ou ativar outro cenário depois, sem perder nada).

    Ordem importa: `simulacao_distribuicao_diametrica` tem uma FOREIGN KEY
    pra `simulacao_talhao_idade(id)` (ver calcular_distribuicao_diametrica)
    — com as constraints de FK ativas, o SQLite recusa derrubar a tabela
    "pai" (população) enquanto a "filha" (distribuição) ainda existir
    referenciando ela ("FOREIGN KEY constraint failed"). Por isso a
    distribuição é sempre derrubada/recriada ANTES da população, mesma
    ordem que gerar_populacao já usa pro DROP."""
    # Formato atual: materializa sob demanda o resultado colunar nas
    # tabelas canônicas. Isso mantém gráficos/construtores/exportação de
    # cenário único inalterados, mas evita manter todos os cenários
    # expandidos simultaneamente no SQLite.
    resultado_parquet = carregar_cenario_parquet(conn, cenario_id)
    if resultado_parquet is not None:
        resultado_parquet.pop("_t_inicio_worker", None)
        _preparar_resultado_parquet_canonico(resultado_parquet)
        persistir_cenario_calculado(conn, resultado_parquet, commit=True)
        return

    def _tem_linha(tabela_lote: str) -> bool:
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela_lote,)
        ).fetchone()
        if existe is None:
            return False
        return conn.execute(
            f'SELECT 1 FROM "{tabela_lote}" WHERE cenario_id = ? LIMIT 1', (cenario_id,)
        ).fetchone() is not None

    if not any(_tem_linha(tabela_lote) for _base, tabela_lote in _TABELAS_LOTE_POR_CANONICA):
        raise ValueError(
            "Esse cenário não tem dado no formato atual — se foi gerado numa versão anterior do "
            "app, rode \"Reiniciar\" pra regenerá-lo antes de ativar.")

    for base, tabela_lote in _TABELAS_LOTE_POR_CANONICA:
        conn.execute(f'DROP TABLE IF EXISTS "{base}"')
        if not _tem_linha(tabela_lote):
            continue
        colunas = [
            linha[1] for linha in conn.execute(f'PRAGMA table_info("{tabela_lote}")')
            if linha[1] != "cenario_id"
        ]
        colunas_sql = ", ".join(f'"{c}"' for c in colunas)
        conn.execute(
            f'CREATE TABLE "{base}" AS SELECT {colunas_sql} FROM "{tabela_lote}" WHERE cenario_id = ?',
            (cenario_id,))
    conn.commit()


def limpar_registro_cenarios_orfaos(conn: sqlite3.Connection) -> int:
    """Remove de `simulacao_cenarios` toda linha sem NENHUMA linha em
    `simulacao_lote_populacao` com esse `cenario_id` — normalmente porque
    a tabela unificada foi excluída na tela Configurações ("Manutenção do
    banco", ver core/db.py:excluir_tabelas). Sem isso, sobra um cenário
    com status "Gerado" sem dado nenhum por trás, que quebra ("no such
    table"/ValueError) na hora de ativar/exportar/ranquear. Não verifica
    as OUTRAS 4 tabelas do lote (distribuição/volume/mip) — a população é
    a que manda: sem ela o cenário já não é utilizável de jeito nenhum,
    tanto faz se as outras 4 ainda têm linha ou não. Devolve quantas
    linhas foram removidas; chame depois de qualquer exclusão de tabela
    que possa ter mexido num cenário.

    Mesma observação de sempre pra um cenário "Pendente" (nunca gerado,
    sem linha nenhuma ainda): já era considerado "órfão" por esta função
    antes desta tabela unificada existir (a tabela sufixada também não
    existia pra ele) — comportamento preservado, não uma regressão nova."""
    ids = [r[0] for r in conn.execute("SELECT id FROM simulacao_cenarios").fetchall()]
    existe_tabela = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_LOTE_POPULACAO,)
    ).fetchone() is not None
    cenarios_com_dado = (
        {r[0] for r in conn.execute(f'SELECT DISTINCT cenario_id FROM "{TABELA_LOTE_POPULACAO}"')}
        if existe_tabela else set()
    )
    existe_parquet = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABELA_CENARIOS_PARQUET,),
    ).fetchone() is not None
    if existe_parquet:
        cenarios_com_dado.update(
            r[0] for r in conn.execute(f'SELECT cenario_id FROM "{TABELA_CENARIOS_PARQUET}"'))
    orfaos = [cenario_id for cenario_id in ids if cenario_id not in cenarios_com_dado]
    if orfaos:
        marcadores = ", ".join("?" for _ in orfaos)
        conn.execute(f"DELETE FROM simulacao_cenarios WHERE id IN ({marcadores})", orfaos)
        conn.commit()
    return len(orfaos)


# ==========================================================
# RANKING DE CENÁRIOS POR KPI (tela Simulação)
# ==========================================================

def valor_kpi_cenario(
    conn: sqlite3.Connection, cenario_id: int, colunas: List[str]
) -> Optional[float]:
    """Soma total de `colunas` (cada uma plain OU família por classe —
    mesma distinção de colunas_grafico_resultado_disponiveis) nas linhas
    desse `cenario_id` em `simulacao_lote_populacao`, somadas entre si num
    número só — o "KPI" de um cenário do modo "Múltiplos cenários" (ver
    ranquear_cenarios). Uma família por classe soma TODAS as classes numa
    query só (`COALESCE(SUM(c), 0)` por classe, evita uma query por
    classe); uma coluna plain soma todas as linhas direto.

    None se a tabela unificada ainda não existir, ou esse cenário não
    tiver nenhuma linha nela (status inconsistente/cenário de uma versão
    anterior do app, ainda não regenerado — não deveria acontecer, mas
    não é erro de programação chamar aqui assim mesmo). Levanta ValueError
    se alguma coluna pedida não existir nem como plain nem como família —
    mensagem pronta pra messagebox."""
    df_parquet = carregar_populacao_cenario_parquet(conn, cenario_id)
    if df_parquet is not None:
        classes = None
        total = 0.0
        for coluna in colunas:
            if coluna in df_parquet.columns:
                total += float(pd.to_numeric(df_parquet[coluna], errors="coerce").sum())
                continue
            if classes is None:
                try:
                    classes = obter_classes_diametricas(conn)
                except ValueError:
                    classes = np.array([])
            nomes = [
                f"{coluna}_{classe:g}" for classe in classes
                if f"{coluna}_{classe:g}" in df_parquet.columns]
            if not nomes:
                raise ValueError(f'A coluna "{coluna}" não existe nesse cenário.')
            total += float(df_parquet[nomes].apply(pd.to_numeric, errors="coerce").sum().sum())
        return total

    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_LOTE_POPULACAO,)
    ).fetchone()
    if existe is None:
        return None
    tem_linha = conn.execute(
        f'SELECT 1 FROM "{TABELA_LOTE_POPULACAO}" WHERE cenario_id = ? LIMIT 1', (cenario_id,)
    ).fetchone()
    if tem_linha is None:
        return None

    colunas_populacao = {
        d[0] for d in conn.execute(f'SELECT * FROM "{TABELA_LOTE_POPULACAO}" LIMIT 0').description
    } - {"cenario_id"}
    classes = None
    total = 0.0
    for coluna in colunas:
        if coluna in colunas_populacao:
            valor = conn.execute(
                f'SELECT SUM("{coluna}") FROM "{TABELA_LOTE_POPULACAO}" WHERE cenario_id = ?',
                (cenario_id,)).fetchone()[0]
            total += float(valor) if valor is not None else 0.0
            continue

        if classes is None:
            try:
                classes = obter_classes_diametricas(conn)
            except ValueError:
                classes = np.array([])
        nomes_classe = [
            f"{coluna}_{classe:g}" for classe in classes
            if f"{coluna}_{classe:g}" in colunas_populacao
        ]
        if not nomes_classe:
            raise ValueError(f"A coluna \"{coluna}\" não existe nesse cenário.")
        expressao = " + ".join(f'COALESCE(SUM("{nome}"), 0)' for nome in nomes_classe)
        valor = conn.execute(
            f'SELECT {expressao} FROM "{TABELA_LOTE_POPULACAO}" WHERE cenario_id = ?',
            (cenario_id,)).fetchone()[0]
        total += float(valor) if valor is not None else 0.0

    return total


def ranquear_cenarios(
    conn: sqlite3.Connection, colunas: List[str], decrescente: bool = True
) -> List[Dict]:
    """Ranking dos cenários já gerados (`simulacao_cenarios` com
    `status = 'Gerado'`) pela soma de `colunas` (ver valor_kpi_cenario),
    lida direto de `simulacao_lote_populacao`, filtrando por cenario_id —
    não precisa "ativar" nenhum antes. `decrescente=True` (padrão) põe o
    MAIOR valor primeiro (acha o cenário que maximiza o KPI, ex: VET
    total); `False` põe o MENOR primeiro (minimizar, ex: um custo). Um
    cenário cuja coluna não existe (ValueError de valor_kpi_cenario) entra
    com `valor=None` e vai sempre pro fim da lista, em vez de derrubar o
    ranking inteiro.

    Devolve lista de dicts {id, nome, valor, posicao} (posicao 1-based,
    já na ordem final)."""
    cenarios = conn.execute(
        "SELECT id, nome FROM simulacao_cenarios WHERE status = 'Gerado' ORDER BY id"
    ).fetchall()

    resultado = []
    for cenario_id, nome in cenarios:
        try:
            valor = valor_kpi_cenario(conn, cenario_id, colunas)
        except ValueError:
            valor = None
        resultado.append({"id": cenario_id, "nome": nome, "valor": valor})

    def _chave_ordenacao(item):
        if item["valor"] is None:
            return (1, 0.0)
        sinal = -1.0 if decrescente else 1.0
        return (0, sinal * item["valor"])

    resultado.sort(key=_chave_ordenacao)
    for posicao, item in enumerate(resultado, start=1):
        item["posicao"] = posicao
    return resultado


def valores_kpi_cenario_por_chave(
    conn: sqlite3.Connection, cenario_id: int, colunas: List[str], coluna_chave: str
) -> Dict[str, float]:
    """Mesma soma de valor_kpi_cenario (cada coluna plain OU família por
    classe, somadas entre si), mas GROUP BY `coluna_chave` (ex: a coluna
    de talhão) em vez de somar todas as linhas juntas — devolve
    {valor_da_chave: soma_do_kpi_só_nesse_grupo}, o que permite comparar
    cenários DENTRO de cada talhão em vez de só no total da simulação
    inteira (ver ranquear_cenarios_por_chave).

    {} se a tabela unificada ainda não existir, ou esse cenário não tiver
    nenhuma linha nela. Levanta ValueError se `coluna_chave` ou alguma de
    `colunas` não existir nem como plain nem como família (mesmas
    condições de valor_kpi_cenario)."""
    df_parquet = carregar_populacao_cenario_parquet(conn, cenario_id)
    if df_parquet is not None:
        if coluna_chave not in df_parquet.columns:
            raise ValueError(f'A coluna "{coluna_chave}" não existe nesse cenário.')
        valores = pd.Series(0.0, index=df_parquet.index)
        classes = None
        for coluna in colunas:
            if coluna in df_parquet.columns:
                valores = valores.add(
                    pd.to_numeric(df_parquet[coluna], errors="coerce").fillna(0), fill_value=0)
                continue
            if classes is None:
                try:
                    classes = obter_classes_diametricas(conn)
                except ValueError:
                    classes = np.array([])
            nomes = [
                f"{coluna}_{classe:g}" for classe in classes
                if f"{coluna}_{classe:g}" in df_parquet.columns]
            if not nomes:
                raise ValueError(f'A coluna "{coluna}" não existe nesse cenário.')
            valores = valores.add(
                df_parquet[nomes].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1),
                fill_value=0)
        agrupado = valores.groupby(df_parquet[coluna_chave]).sum()
        return {str(chave): float(valor) for chave, valor in agrupado.items() if pd.notna(chave)}

    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_LOTE_POPULACAO,)
    ).fetchone()
    if existe is None:
        return {}
    tem_linha = conn.execute(
        f'SELECT 1 FROM "{TABELA_LOTE_POPULACAO}" WHERE cenario_id = ? LIMIT 1', (cenario_id,)
    ).fetchone()
    if tem_linha is None:
        return {}

    colunas_populacao = {
        d[0] for d in conn.execute(f'SELECT * FROM "{TABELA_LOTE_POPULACAO}" LIMIT 0').description
    } - {"cenario_id"}
    if coluna_chave not in colunas_populacao:
        raise ValueError(f"A coluna \"{coluna_chave}\" não existe nesse cenário.")

    classes = None
    resultado: Dict[str, float] = {}
    for coluna in colunas:
        if coluna in colunas_populacao:
            expressao = f'SUM("{coluna}")'
            nomes_classe = None
        else:
            if classes is None:
                try:
                    classes = obter_classes_diametricas(conn)
                except ValueError:
                    classes = np.array([])
            nomes_classe = [
                f"{coluna}_{classe:g}" for classe in classes
                if f"{coluna}_{classe:g}" in colunas_populacao
            ]
            if not nomes_classe:
                raise ValueError(f"A coluna \"{coluna}\" não existe nesse cenário.")
            expressao = " + ".join(f'COALESCE(SUM("{nome}"), 0)' for nome in nomes_classe)

        linhas = conn.execute(
            f'SELECT "{coluna_chave}", {expressao} FROM "{TABELA_LOTE_POPULACAO}" '
            f'WHERE cenario_id = ? GROUP BY "{coluna_chave}"',
            (cenario_id,)
        ).fetchall()
        for chave, valor in linhas:
            if chave is None:
                continue
            resultado[chave] = resultado.get(chave, 0.0) + (float(valor) if valor is not None else 0.0)

    return resultado


def ranquear_cenarios_por_chave(
    conn: sqlite3.Connection, coluna_chave: str, colunas: List[str], decrescente: bool = True,
    top_n: Optional[int] = None,
) -> Dict[str, List[Dict]]:
    """Ranking dos cenários já gerados (`simulacao_cenarios` com
    `status = 'Gerado'`), um ranking INDEPENDENTE por valor de
    `coluna_chave` (ex: talhão) — o KPI de cada (chave, cenário) é a soma
    de `colunas` só nas linhas daquele valor de chave (ver
    valores_kpi_cenario_por_chave), ao contrário de ranquear_cenarios, que
    soma TODOS os talhões juntos num KPI só por cenário. Útil quando
    cenários diferentes são melhores pra talhões diferentes (condição de
    sítio/crescimento não é igual em todo talhão).

    `decrescente`/`top_n`: mesmo sentido de ranquear_cenarios (maior
    primeiro por padrão) — `top_n`, se passado, corta CADA ranking (por
    valor de chave) nos N melhores; None (padrão) devolve o ranking
    completo.

    Cenário cuja tabela não existe, ou cujas `colunas` não existem nela
    (ValueError), simplesmente não entra em nenhum ranking — ao contrário
    de ranquear_cenarios (que mantém o cenário na lista com valor=None),
    não dá pra saber de quais chaves ele participaria sem essa
    informação, então ele só some, sem quebrar o resto.

    Devolve {valor_da_chave: [{id, nome, valor, posicao}, ...]}, cada
    lista já ordenada com posição 1-based — mesmo formato de item que
    ranquear_cenarios, só que agrupado por chave."""
    cenarios = conn.execute(
        "SELECT id, nome FROM simulacao_cenarios WHERE status = 'Gerado' ORDER BY id"
    ).fetchall()

    por_chave: Dict[str, List[Dict]] = {}
    for cenario_id, nome in cenarios:
        try:
            valores = valores_kpi_cenario_por_chave(conn, cenario_id, colunas, coluna_chave)
        except ValueError:
            continue
        for chave, valor in valores.items():
            por_chave.setdefault(chave, []).append({"id": cenario_id, "nome": nome, "valor": valor})

    sinal = -1.0 if decrescente else 1.0
    for chave, itens in por_chave.items():
        itens.sort(key=lambda item: sinal * item["valor"])
        for posicao, item in enumerate(itens, start=1):
            item["posicao"] = posicao
        if top_n is not None:
            por_chave[chave] = itens[:top_n]

    return por_chave


# ==========================================================
# GRÁFICO DE RESULTADOS (tela Simulação)
# ==========================================================

_COLUNAS_DIMENSAO_GRAFICO = {
    "id", "idade_simulada", "ano_simulado", "evento_manejo", "intensidade_evento",
    # "cenario_id": só existe em simulacao_lote_populacao (ver
    # colunas_kpi_cenarios_disponiveis, que passa essa tabela aqui em vez
    # da canônica) — nunca deveria aparecer como "coluna de KPI"
    # plotável/ranqueável.
    "cenario_id",
}


def colunas_grafico_resultado_disponiveis(
    conn: sqlite3.Connection, tabela_populacao: str = TABELA_POPULACAO
) -> Tuple[List[str], List[str]]:
    """Opções pro combobox "Coluna" do gráfico de resultados (tela
    Simulação). Devolve (colunas_simples, bases_por_classe):

    `bases_por_classe` é exatamente colunas_volume_por_classe_disponiveis
    (mesmo agrupamento "{base}_{classe:g}" — uma curva por sortimento
    cadastrado usa essas, ver dados_grafico_resultado).

    `colunas_simples` é toda coluna "normal" (não agrupável por classe) de
    `tabela_populacao` (por padrão `simulacao_talhao_idade`, mas aceita
    uma tabela sufixada de cenário — ver colunas_kpi_cenarios_disponiveis)
    — uma curva por tipo de evento usa essas — excluindo "id", a coluna
    de talhão, e as colunas de dimensão/controle (idade_simulada,
    ano_simulado, evento_manejo, intensidade_evento) que não fazem
    sentido como KPI plotável. Colunas que já pertencem a uma família de
    `bases_por_classe` (ex: "vtcc_rt_5", "vtcc_rt_7", ...) também ficam
    de fora daqui — já aparecem representadas pela base ("vtcc_rt") na
    outra lista, listá-las soltas também só duplicaria/confundiria a
    escolha. Listas vazias (não erro) se a tabela de população ainda não
    existir."""
    try:
        colunas = [
            d[0] for d in conn.execute(f'SELECT * FROM "{tabela_populacao}" LIMIT 0').description
        ]
    except sqlite3.OperationalError:
        return [], []

    bases_por_classe = colunas_volume_por_classe_disponiveis(conn, tabela_populacao)

    colunas_de_familia = set()
    if bases_por_classe:
        try:
            classes = obter_classes_diametricas(conn)
        except ValueError:
            classes = []
        for base in bases_por_classe:
            for classe in classes:
                colunas_de_familia.add(f"{base}_{classe:g}")

    coluna_talhao = (obter_coluna_talhao(conn) or "").lower()
    excluidas = _COLUNAS_DIMENSAO_GRAFICO | colunas_de_familia
    colunas_simples = sorted(
        c for c in colunas if c.lower() != coluna_talhao and c not in excluidas)

    return colunas_simples, bases_por_classe


def colunas_kpi_cenarios_disponiveis(conn: sqlite3.Connection) -> Tuple[List[str], List[str]]:
    """Opções pro Listbox "Ranquear cenários por KPI" (tela Simulação,
    ver _montar_secao_ranking_cenarios) — colunas_grafico_resultado_
    disponiveis lida direto de `simulacao_lote_populacao` (a tabela
    unificada do lote, ver persistir_cenario_no_lote), não a canônica.

    No modo "Múltiplos cenários" a tabela canônica só reflete o cenário
    ativado por último (ver ativar_cenario) — ou nem existe, se nenhum
    cenário nunca foi ativado. Uma coluna que um construtor de variáveis
    gerou (ver app/construtores.py:aplicar_construtores_salvos, reaplicado
    em todo cenário na hora de gerar o lote) ficava de fora do Listbox
    mesmo estando presente nas tabelas de verdade que ranquear_cenarios
    lê — daí essa função ler a tabela do lote direto em vez de depender
    da canônica. Como o schema é compartilhado por todos os cenários de um
    mesmo lote (ver _garantir_tabelas_lote), 1 leitura já basta — não
    precisa mais unir coluna a coluna de cada cenário "Gerado".

    Sem nenhum cenário gerado ainda (tabela do lote não existe), cai de
    volta pra colunas_grafico_resultado_disponiveis na canônica (modo de
    cenário único, mesmo comportamento de sempre)."""
    linha_parquet = (
        conn.execute(
            f'SELECT cenario_id FROM "{TABELA_CENARIOS_PARQUET}" ORDER BY cenario_id LIMIT 1'
        ).fetchone()
        if _existe_tabela_cenarios_parquet(conn) else None)
    if linha_parquet is not None:
        df = carregar_populacao_cenario_parquet(conn, linha_parquet[0])
        if df is not None:
            try:
                classes = obter_classes_diametricas(conn)
            except ValueError:
                classes = []
            colunas = list(df.columns)
            bases = []
            colunas_familia = set()
            for coluna in colunas:
                for classe in classes:
                    sufixo = f"_{classe:g}"
                    if coluna.endswith(sufixo):
                        base = coluna[:-len(sufixo)]
                        if base and base not in bases:
                            bases.append(base)
                        colunas_familia.add(coluna)
                        break
            coluna_talhao = (obter_coluna_talhao(conn) or "").lower()
            simples = sorted(
                c for c in colunas
                if c.lower() != coluna_talhao
                and c not in _COLUNAS_DIMENSAO_GRAFICO
                and c not in colunas_familia)
            return simples, sorted(bases)

    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_LOTE_POPULACAO,)
    ).fetchone()
    if existe is None:
        return colunas_grafico_resultado_disponiveis(conn)
    return colunas_grafico_resultado_disponiveis(conn, TABELA_LOTE_POPULACAO)


def dados_grafico_resultado(
    conn: sqlite3.Connection, coluna: str, por_classe: bool, tipo_agregacao: Optional[str],
) -> pd.DataFrame:
    """Dados prontos pro gráfico de resultados (tela Simulação), sempre no
    formato "longo" — colunas idade_simulada/serie/valor, uma linha por
    ponto de uma curva — pra desenhar igual nos dois modos (só agrupar por
    "serie" e plotar cada grupo).

    `por_classe=False`: `coluna` é uma coluna comum de
    `simulacao_talhao_idade` (ver colunas_grafico_resultado_disponiveis) —
    "serie" é o evento_manejo daquela idade ("Em pé" pras idades sem
    evento, já que a maioria das colunas tem valor em toda idade, não só
    nas de evento); "valor" agrega (SUM ou AVG, conforme `tipo_agregacao`
    — "Soma"/"Média", None equivale a "Soma") a coluna entre TODOS os
    talhões daquela idade+evento. Levanta ValueError se `coluna` não
    existir.

    `por_classe=True`: `coluna` é uma base de família por classe (ver
    colunas_grafico_resultado_disponiveis) — "serie" é o NOME de cada
    sortimento cadastrado (Configurações); "valor", por talhão/idade, soma
    (sempre soma entre classes — combinar volume de classes diferentes é
    aditivo, independente de `tipo_agregacao`) as colunas
    "{coluna}_{classe:g}" das classes cobertas pela faixa
    [limite_inferior, limite_superior] daquele sortimento (mesma regra de
    calcular_volume_por_sortimento), depois agrega SUM/AVG ENTRE talhões
    (conforme `tipo_agregacao`) pra cada idade. Levanta ValueError se
    faltar coluna de alguma classe configurada ou se não houver sortimento
    cadastrado.

    `idade_simulada <= 0` nunca entra aqui, nos dois modos — são as linhas
    de custo de formação florestal anteriores ao plantio (ver nó "Custo de
    Formação" do Construtor de Variáveis/core/construtores.py:
    avaliar_grafo), sem distribuição diamétrica nem a maioria das colunas
    preenchidas; graficar um gráfico "de resultados da simulação" com um
    ponto ali (ou, no modo por_classe, um zero espúrio — soma de colunas
    todas NaN dá 0 com skipna=True) não faz sentido. Essas idades aparecem
    normalmente nas exportações (abas "Simulação"/"Volume por Sortimento"),
    só não em gráfico/tabela agregados."""
    usar_soma = tipo_agregacao != "Média"

    if not por_classe:
        colunas_populacao = {
            d[0] for d in conn.execute(f'SELECT * FROM "{TABELA_POPULACAO}" LIMIT 0').description
        }
        if coluna not in colunas_populacao:
            raise ValueError(f"A coluna \"{coluna}\" não existe em \"{TABELA_POPULACAO}\".")

        funcao_sql = "SUM" if usar_soma else "AVG"
        return pd.read_sql_query(
            f'SELECT idade_simulada, '
            f'CASE WHEN evento_manejo IS NULL OR evento_manejo = \'\' THEN \'Em pé\' '
            f'ELSE evento_manejo END AS serie, '
            f'{funcao_sql}("{coluna}") AS valor '
            f'FROM "{TABELA_POPULACAO}" '
            f'WHERE "{coluna}" IS NOT NULL AND idade_simulada >= 1 '
            f'GROUP BY idade_simulada, serie '
            f'ORDER BY idade_simulada',
            conn,
        )

    classes = obter_classes_diametricas(conn)
    colunas_populacao = {
        d[0] for d in conn.execute(f'SELECT * FROM "{TABELA_POPULACAO}" LIMIT 0').description
    }
    colunas_por_classe = {}
    for classe in classes:
        nome_coluna = f"{coluna}_{classe:g}"
        if nome_coluna not in colunas_populacao:
            raise ValueError(
                f"A coluna \"{nome_coluna}\" (classe {classe:g}) não existe em "
                f"\"{TABELA_POPULACAO}\" — reaplique o construtor de variáveis ou gere a "
                "simulação de novo.")
        colunas_por_classe[classe] = nome_coluna

    sortimentos = conn.execute(
        "SELECT nome, limite_inferior, limite_superior FROM sortimentos ORDER BY limite_inferior, nome"
    ).fetchall()
    if not sortimentos:
        raise ValueError("Nenhum sortimento cadastrado (tela Configurações).")

    df = pd.read_sql_query(
        'SELECT idade_simulada, ' + ", ".join(f'"{nome}"' for nome in colunas_por_classe.values())
        + f' FROM "{TABELA_POPULACAO}" WHERE idade_simulada >= 1',
        conn,
    )

    classes_por_sortimento = _classes_por_sortimento(classes, sortimentos)
    linhas = []
    for nome, _limite_inferior, _limite_superior in sortimentos:
        classes_no_sortimento = classes_por_sortimento[nome]
        if not classes_no_sortimento:
            continue
        subset_cols = [colunas_por_classe[c] for c in classes_no_sortimento]
        soma_por_linha = df[subset_cols].sum(axis=1, skipna=True)
        temp = pd.DataFrame({"idade_simulada": df["idade_simulada"], "valor": soma_por_linha})
        agrupado = temp.groupby("idade_simulada")["valor"]
        agregado = agrupado.sum() if usar_soma else agrupado.mean()
        for idade, valor in agregado.items():
            linhas.append({"idade_simulada": idade, "serie": nome, "valor": valor})

    return pd.DataFrame(linhas, columns=["idade_simulada", "serie", "valor"])


def tabela_evento_sortimento(
    conn: sqlite3.Connection, coluna_base_classes: str, tipo_agregacao: Optional[str] = None,
) -> pd.DataFrame:
    """Tabela cruzada evento de manejo (linha) x sortimento cadastrado
    (coluna) pra aba "Tabela por sortimento" do Gráfico de Resultados —
    `coluna_base_classes` só aceita família por classe (sortimento só faz
    sentido pra uma coluna com dimensão de classe diamétrica).

    Agrega direto pelo evento_manejo DE CADA LINHA (mesmo padrão de
    dados_grafico_por_classe, que já faz isso certo) — NÃO por
    idade_simulada seguida de um remapeamento idade->evento global feito à
    parte. Esse remapeamento já existiu aqui e dependia de "cada evento
    acontece numa única idade, igual pra todo talhão"; com o "Ajuste de
    manejo" (Configurações — ver `obter_ajuste_manejo_padrao`/
    `gerar_populacao`) ligado, talhões plantados em anos diferentes podem
    ter Raleio/1º/2º Desbaste em idades simuladas DIFERENTES, então uma
    mesma idade_simulada pode corresponder a eventos diferentes em
    talhões diferentes — remapear por idade global misturava (e
    mislabelava) valores de talhões com eventos diferentes que só por
    coincidência caíam na mesma idade numérica.

    Idades sem evento (Em pé) ficam de fora — a tabela é "por evento de
    manejo", não por estado de idade. Devolve DataFrame "longo" (colunas
    evento/sortimento/valor) — vazio (não erro) se não houver nenhuma
    linha com evento. Levanta ValueError se faltar coluna de alguma classe
    configurada ou se não houver sortimento cadastrado (mesmas condições
    de dados_grafico_por_classe/dados_grafico_resultado)."""
    usar_soma = tipo_agregacao != "Média"
    classes = obter_classes_diametricas(conn)
    colunas_populacao = {
        d[0] for d in conn.execute(f'SELECT * FROM "{TABELA_POPULACAO}" LIMIT 0').description
    }
    colunas_por_classe = {}
    for classe in classes:
        nome_coluna = f"{coluna_base_classes}_{classe:g}"
        if nome_coluna not in colunas_populacao:
            raise ValueError(
                f"A coluna \"{nome_coluna}\" (classe {classe:g}) não existe em "
                f"\"{TABELA_POPULACAO}\" — reaplique o construtor de variáveis ou gere a "
                "simulação de novo.")
        colunas_por_classe[classe] = nome_coluna

    sortimentos = conn.execute(
        "SELECT nome, limite_inferior, limite_superior FROM sortimentos ORDER BY limite_inferior, nome"
    ).fetchall()
    if not sortimentos:
        raise ValueError("Nenhum sortimento cadastrado (tela Configurações).")

    df = pd.read_sql_query(
        'SELECT evento_manejo, ' + ", ".join(f'"{nome}"' for nome in colunas_por_classe.values())
        + f' FROM "{TABELA_POPULACAO}" WHERE evento_manejo IS NOT NULL AND evento_manejo != \'\'',
        conn,
    )
    if df.empty:
        return pd.DataFrame(columns=["evento", "sortimento", "valor"])

    classes_por_sortimento = _classes_por_sortimento(classes, sortimentos)
    linhas = []
    for nome, _limite_inferior, _limite_superior in sortimentos:
        classes_no_sortimento = classes_por_sortimento[nome]
        if not classes_no_sortimento:
            continue
        subset_cols = [colunas_por_classe[c] for c in classes_no_sortimento]
        soma_por_linha = df[subset_cols].sum(axis=1, skipna=True)
        temp = pd.DataFrame({"evento": df["evento_manejo"], "valor": soma_por_linha})
        agrupado = temp.groupby("evento")["valor"]
        agregado = agrupado.sum() if usar_soma else agrupado.mean()
        for evento, valor in agregado.items():
            linhas.append({"evento": evento, "sortimento": nome, "valor": valor})

    return pd.DataFrame(linhas, columns=["evento", "sortimento", "valor"])


def dados_grafico_por_classe(
    conn: sqlite3.Connection, coluna_base_classes: str, tipo_agregacao: Optional[str] = None,
) -> pd.DataFrame:
    """Dados pra aba "Gráfico por classe" do Gráfico de Resultados (tela
    Simulação): uma curva por evento de manejo, classe diamétrica no eixo
    x — ao contrário de dados_grafico_resultado (por_classe=True), que
    SOMA entre classes pra virar uma curva por sortimento, aqui a classe
    é preservada como dimensão (é o eixo x) e só o evento vira série.

    `coluna_base_classes` é uma família por classe (ver
    colunas_volume_por_classe_disponiveis) — cada coluna
    "{coluna_base_classes}_{classe:g}" é agregada (Soma/Média,
    `tipo_agregacao`) ENTRE TALHÕES, separado por evento_manejo (idades
    sem evento — "Em pé" — ficam de fora, mesma regra de
    tabela_evento_sortimento: aqui o eixo é classe, não faz sentido
    misturar todas as idades "em pé" numa curva só).

    Devolve DataFrame longo (colunas classe/evento/valor) — vazio (não
    erro) se não houver nenhuma idade com evento. Levanta ValueError se
    faltar a coluna de alguma classe configurada."""
    usar_soma = tipo_agregacao != "Média"
    classes = obter_classes_diametricas(conn)
    colunas_populacao = {
        d[0] for d in conn.execute(f'SELECT * FROM "{TABELA_POPULACAO}" LIMIT 0').description
    }
    colunas_por_classe = {}
    for classe in classes:
        nome_coluna = f"{coluna_base_classes}_{classe:g}"
        if nome_coluna not in colunas_populacao:
            raise ValueError(
                f"A coluna \"{nome_coluna}\" (classe {classe:g}) não existe em "
                f"\"{TABELA_POPULACAO}\" — reaplique o construtor de variáveis ou gere a "
                "simulação de novo.")
        colunas_por_classe[classe] = nome_coluna

    df = pd.read_sql_query(
        'SELECT evento_manejo, ' + ", ".join(f'"{nome}"' for nome in colunas_por_classe.values())
        + f' FROM "{TABELA_POPULACAO}" WHERE evento_manejo IS NOT NULL AND evento_manejo != \'\'',
        conn,
    )
    if df.empty:
        return pd.DataFrame(columns=["classe", "evento", "valor"])

    linhas = []
    for classe, nome_coluna in colunas_por_classe.items():
        agrupado = df.groupby("evento_manejo")[nome_coluna]
        agregado = agrupado.sum() if usar_soma else agrupado.mean()
        for evento, valor in agregado.items():
            linhas.append({"classe": classe, "evento": evento, "valor": valor})
    return pd.DataFrame(linhas, columns=["classe", "evento", "valor"])


# ==========================================================
# MIP (Diâmetro Diferenciador / Ingresso Percentual / IPM / ITD)
#
# Método dos Ingressos Percentuais (MIP), de Garcia (1999), documentado
# em Leite, Nogueira, Campos, Souza e Carvalho (2005), "Avaliação de um
# modelo de distribuição diamétrica ajustado para povoamentos de
# Eucalyptus sp. submetidos a desbaste", R. Árvore v.29 n.2. Pra cada
# talhão, compara a distribuição diamétrica de cada idade simulada contra
# a distribuição da idade IMEDIATAMENTE ANTERIOR (não uma base fixa) — por
# classe diamétrica, usando uma de duas grandezas por classe, configurável
# em Configurações (ver BASES_CALCULO_MIP/obter_base_calculo_mip):
#
# - "fdp" (padrão): DENSIDADE (PDF pontual) da Weibull (`densidade_weibull`,
#   avaliada exatamente no valor da classe — mesma grandeza gravada em
#   simulacao_distribuicao_diametrica.densidade). Segue a Figura 3 de
#   Helfenstein (2020): curvas de densidade sobrepostas por idade, DD no
#   ponto onde a curva da idade nova ultrapassa a da idade anterior.
# - "classe": PROBABILIDADE/área por classe já normalizada pra somar 1
#   (`probabilidades_por_classe`, mesma grandeza gravada em
#   simulacao_distribuicao_diametrica.probabilidade). Mais próximo do
#   texto de Leite et al. (2005), que descreve o método em cima de F(x)
#   (acumulada) por classe.
#
#     delta(classe, idade) = grandeza(classe | forma(idade), escala(idade))
#                              - grandeza(classe | forma(idade-1), escala(idade-1))
#
# - Diâmetro Diferenciador (DD): percorrendo as classes do menor pro
#   maior diâmetro, a PRIMEIRA classe em que delta muda de negativo
#   pra positivo.
# - Ingresso Percentual (IP): soma de delta (grandeza da idade atual menos
#   grandeza da idade anterior) da classe do DD em diante (inclusive), em
#   FRAÇÃO (0-1, não mais em pontos percentuais/×100).
# - Ingresso Percentual Médio (IPM) = IP / idade (idade absoluta, em
#   anos — mesma unidade de idade_simulada em todo o resto do projeto).
# - Idade Técnica de Desbaste (ITD): ajuste do modelo logístico
#   y = a/(1 + b·exp(-c·idade)) sobre (idade, 1/IP) ou (idade, 1/IPM)
#   (configurável — ver BASES_AJUSTE_LOGISTICO/obter_base_ajuste_logistico)
#   por talhão; ITD = ln(b)/c, o ponto de inflexão da curva ajustada (onde
#   1/IP cresce mais rápido, i.e. onde o ingresso IP declina mais rápido),
#   em anos.
#
# A 1ª idade de cada talhão não tem idade anterior pra comparar — DD/IP/
# IPM ficam NULL ali (estruturalmente impossível de calcular). Da 2ª
# idade em diante, IP/IPM sempre têm valor numérico: idade sem transição
# (delta não muda de sinal em nenhuma classe — ex: antes de qualquer
# manejo, quando a distribuição ainda não mudou da idade anterior) grava
# IP = IPM = 0,0 ("sem ingresso" é uma resposta válida); só o DD fica
# NULL nesse caso.
# ==========================================================

_COLUNA_IDADE_PADRAO = "idade_simulada"


def modelo_logistico(idade, a, b, c):
    """y = a / (1 + b·exp(-c·idade)) — logística de 3 parâmetros. Ajustada
    sobre (idade, 1/IP) ou (idade, 1/IPM): como IP declina e se estabiliza
    conforme o povoamento amadurece, 1/IP cresce em S até um teto (a) —
    ITD = ln(b)/c é o ponto de inflexão dessa curva (onde 1/IP cresce mais
    rápido, i.e. onde IP declina mais rápido). Pública (sem "_") — além de
    usada internamente por `ajustar_logistico`, a tela de Ingressos chama
    direto pra desenhar a curva ajustada sobre os pontos de 1/IP (ver
    app/screens/ingressos_curvas_distribuicao.py)."""
    return a / (1.0 + b * np.exp(-c * idade))


def itd_fora_do_intervalo(itd: float, idade_maxima_manejo: float) -> bool:
    """True se `itd` (ln(b)/c, devolvida por `ajustar_logistico`) cair
    fora de [0, idade_maxima_manejo] — o intervalo de idades realmente
    simulado. Ao contrário do antigo ajuste expolinear (onde ITD era um
    parâmetro explicitamente limitado a essa faixa na busca), aqui ITD é
    DERIVADA de a/b/c livres — pode sair de [0, idade_maxima_manejo] sem
    limite nenhum, o que significa que o ponto de inflexão da curva
    ajustada cai fora dos dados observados (extrapolação, não um
    "cotovelo" real dentro da simulação); nesse caso a ITD devolvida não
    deve ser interpretada como a idade técnica de desbaste real."""
    return itd < 0.0 or itd > idade_maxima_manejo


def ajustar_logistico(x: np.ndarray, y: np.ndarray, idade_maxima_manejo: float):
    """Mínimos quadrados não-lineares (scipy.optimize.curve_fit) pro
    modelo logístico sobre pontos (idade, 1/IP) ou (idade, 1/IPM) de UM
    talhão — `y` já vem invertido (1/IP ou 1/IPM) e sem zeros (1/0 não é
    definido) de quem chama (ver calcular_mip_continuo). 1/IP cresce
    monotonicamente com a idade (IP declina conforme o povoamento
    amadurece) até um teto — por isso a, b, c saem todos POSITIVOS
    (bounds refletem isso: b > 0 também é exigido pra ln(b) em ITD =
    ln(b)/c ser definido).

    p0: `a0` um pouco acima do maior 1/IP observado (teto ainda não
    necessariamente alcançado nos dados); `c0`/`x_inflexao_0` estimados
    pelo ponto de maior variação local de y (proxy pro "cotovelo" da
    curva) e pela inclinação ali (inclinação de uma logística no ponto de
    inflexão é a·c/4); `b0` derivado de `c0`/`x_inflexao_0` de forma que
    o modelo passe por y ≈ a0/2 nesse ponto (definição do ponto de
    inflexão). Levanta RuntimeError se o curve_fit não convergir. Devolve
    (a, b, c, itd, r2) — use `itd_fora_do_intervalo` pra saber se essa ITD
    é confiável (ver calcular_mip_continuo, que marca esses casos como
    ITD_NAO_IDENTIFICAVEL em vez de OK)."""
    ordem = np.argsort(x)
    x_ordenado = x[ordem]
    y_ordenado = y[ordem]

    a0 = max(float(np.max(y_ordenado)) * 1.2, 1e-6) if len(y_ordenado) else 1.0

    if len(x_ordenado) >= 2:
        idx = int(np.argmax(np.diff(y_ordenado)))
        x_inflexao_0 = float((x_ordenado[idx] + x_ordenado[idx + 1]) / 2)
        dx = x_ordenado[idx + 1] - x_ordenado[idx]
        inclinacao_0 = (y_ordenado[idx + 1] - y_ordenado[idx]) / dx if dx != 0 else 1.0
    else:
        x_inflexao_0 = float(np.median(x_ordenado)) if len(x_ordenado) else 0.0
        inclinacao_0 = 1.0
    inclinacao_0 = max(inclinacao_0, 1e-6)

    c0 = max(4.0 * inclinacao_0 / a0, 1e-6)
    b0 = max(float(np.exp(c0 * x_inflexao_0)), 1e-6)

    parametros, _ = curve_fit(
        modelo_logistico, x, y, p0=[a0, b0, c0],
        bounds=([1e-9, 1e-9, 1e-9], [np.inf, np.inf, np.inf]),
        maxfev=10000,
    )
    a, b, c = (float(v) for v in parametros)
    itd = float(np.log(b) / c)
    y_previsto = modelo_logistico(x, a, b, c)
    soma_quadrados_residuos = float(np.sum((y - y_previsto) ** 2))
    soma_quadrados_totais = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1 - soma_quadrados_residuos / soma_quadrados_totais if soma_quadrados_totais > 0 else float("nan")
    return a, b, c, itd, r2


def _calcular_mip_talhao(
    talhao, grupo: pd.DataFrame, classes: np.ndarray,
    base_calculo: str = "fdp", tipo_normalizacao: str = "aditiva",
) -> pd.DataFrame:
    """DD/IP/IPM de todas as idades de UM talhão, comparando cada idade
    contra a idade IMEDIATAMENTE anterior simulada — ver o cabeçalho da
    seção pra fórmula exata. `base_calculo` (ver BASES_CALCULO_MIP/
    obter_base_calculo_mip) escolhe qual grandeza por classe alimenta o
    delta entre idades:
    - "fdp" (padrão): densidade/PDF pontual da Weibull avaliada na
      classe (`densidade_weibull`, mesma grandeza gravada em
      simulacao_distribuicao_diametrica.densidade) — segue a Figura 3 de
      Helfenstein (2020, curvas de densidade sobrepostas por idade).
    - "classe": probabilidade/área por classe já normalizada pra somar 1
      por linha (`probabilidades_por_classe`, mesma grandeza gravada em
      simulacao_distribuicao_diametrica.probabilidade, conforme
      `tipo_normalizacao` — ver TIPOS_NORMALIZACAO_WEIBULL) — mais
      próximo do texto de Leite et al. (2005), que descreve o método em
      cima de F(x) (acumulada) por classe.
    Em ambos os casos, IP/IPM saem em fração (0-1), não em pontos
    percentuais. Linhas com forma/escala nulos são descartadas (mesmo
    padrão de _calcular_matriz_distribuicao) — não entram no resultado.

    A 1ª idade do talhão (depois do dropna/sort) não tem idade anterior
    pra comparar — DD/IP/IPM ficam NULL ali. Da 2ª idade em diante, IP/
    IPM sempre têm valor numérico: idade sem transição negativo->positivo
    (delta não muda de sinal em nenhuma classe) grava IP = IPM = 0,0; só
    o DD fica NULL nesse caso.

    Se `grupo` tiver as colunas opcionais "dap_min"/"truncado_esquerda"
    (ver calcular_mip_continuo), a grandeza de cada idade com o flag
    ligado é calculada truncada à esquerda em "dap_min" daquela idade (ver
    densidade_weibull/probabilidades_por_classe, `limite_truncamento`) —
    cada idade pode ter seu próprio ponto de corte (a Weibull "Por
    Simulação" muda de etapa em etapa)."""
    grupo = grupo.dropna(subset=["forma_idade", "escala_idade"]).sort_values("idade")
    ids = grupo["id"].to_numpy()
    idades = grupo["idade"].to_numpy(dtype=float)
    n = len(grupo)
    n_classes = len(classes)

    dd = np.full(n, np.nan)
    ip = np.full(n, np.nan)
    ipm = np.full(n, np.nan)

    if n > 1 and n_classes > 0:
        formas = grupo["forma_idade"].to_numpy(dtype=float)
        escalas = grupo["escala_idade"].to_numpy(dtype=float)
        limite_truncamento = None
        if "dap_min" in grupo.columns and "truncado_esquerda" in grupo.columns:
            dap_min = grupo["dap_min"].to_numpy(dtype=float)
            truncado = grupo["truncado_esquerda"].to_numpy(dtype=float)
            limite_truncamento = np.where((truncado == 1) & ~np.isnan(dap_min), dap_min, np.nan)
        if base_calculo == "classe":
            grandeza = probabilidades_por_classe(
                formas, escalas, classes, tipo_normalizacao, limite_truncamento)
        else:
            grandeza = densidade_weibull(
                classes[None, :], formas[:, None], escalas[:, None],
                limite_truncamento[:, None] if limite_truncamento is not None else None)

        # delta[k] = grandeza(idade[k+1]) - grandeza(idade[k]), uma linha
        # por par de idades consecutivas (idade[k+1] é a idade "atual"
        # daquela linha). IP/IPM saem em fração (0-1), sem ×100.
        delta = np.diff(grandeza, axis=0)
        transicao = (delta[:, :-1] < 0) & (delta[:, 1:] >= 0)
        tem_transicao = transicao.any(axis=1)
        primeiro_indice = np.argmax(transicao, axis=1)  # 1º True por linha, relativo a delta[:,1:]

        # soma_reversa[k, c] = soma de delta[k, c:] (IP soma o delta a
        # partir da classe do DD, inclusive).
        soma_reversa = np.cumsum(delta[:, ::-1], axis=1)[:, ::-1]

        linhas_com_transicao = np.nonzero(tem_transicao)[0]
        j_dd = primeiro_indice[linhas_com_transicao] + 1
        dd[linhas_com_transicao + 1] = classes[j_dd]
        ip[linhas_com_transicao + 1] = soma_reversa[linhas_com_transicao, j_dd]

        linhas_sem_transicao = np.nonzero(~tem_transicao)[0]
        ip[linhas_sem_transicao + 1] = 0.0

        com_idade_anterior = np.arange(1, n)
        ipm[com_idade_anterior] = ip[com_idade_anterior] / idades[com_idade_anterior]

    return pd.DataFrame({
        "id": ids, "talhao": talhao, "idade": idades,
        "diametro_diferenciador": dd, "ingresso_percentual": ip,
        "ingresso_percentual_medio": ipm,
    })


def _calcular_mip_para_colunas(
    df: pd.DataFrame, classes: np.ndarray,
    base_calculo: str = "fdp", tipo_normalizacao: str = "aditiva",
) -> pd.DataFrame:
    """Núcleo puro do MIP (sem tocar banco) — recebe um DataFrame com
    colunas id, talhao, idade, forma_idade, escala_idade (uma linha por
    combinação talhão × idade simulada) e as classes diamétricas
    configuradas. `base_calculo`/`tipo_normalizacao`: ver
    _calcular_mip_talhao. Usado por calcular_mip_continuo, que persiste o
    resultado em tabela. Devolve id, talhao, idade,
    diametro_diferenciador, ingresso_percentual, ingresso_percentual_medio
    — uma linha por linha de entrada com forma/escala não nulas (ver
    _calcular_mip_talhao)."""
    partes = [
        _calcular_mip_talhao(talhao, grupo, classes, base_calculo, tipo_normalizacao)
        for talhao, grupo in df.groupby("talhao", sort=False)
    ]
    if not partes:
        return pd.DataFrame(columns=[
            "id", "talhao", "idade", "diametro_diferenciador",
            "ingresso_percentual", "ingresso_percentual_medio"])
    return pd.concat(partes, ignore_index=True)


def _garantir_tabelas_lote_mip(conn: sqlite3.Connection, coluna_talhao: str) -> None:
    """Mesmo papel de _garantir_tabelas_lote (ver ali o porquê completo),
    só que pras 2 tabelas de MIP contínuo do lote — chamada a cada
    cenário com MIP calculado (ver calcular_mip_continuo, ramo
    `cenario_id`), cara só na 1ª vez (`CREATE TABLE IF NOT EXISTS`
    depois disso é no-op). Sem cobertura de drift de schema aqui (ao
    contrário da população/volume): `coluna_talhao` é a mesma coluna do
    projeto inteiro, não varia por construtor — mudar isso entre lotes é
    um caso tão raro que não compensa a complexidade extra."""
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{TABELA_LOTE_MIP_CONTINUO}" ('
        "id INTEGER PRIMARY KEY AUTOINCREMENT, cenario_id INTEGER NOT NULL, "
        "populacao_id INTEGER NOT NULL, "
        f'"{coluna_talhao}" TEXT, idade_simulada INTEGER, '
        "diametro_diferenciador REAL, ingresso_percentual REAL, "
        "ingresso_percentual_medio REAL)"
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS idx_lote_mip_continuo_cenario '
        f'ON "{TABELA_LOTE_MIP_CONTINUO}"(cenario_id)'
    )
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{TABELA_LOTE_MIP_AJUSTE_LOGISTICO}" ('
        "id INTEGER PRIMARY KEY AUTOINCREMENT, cenario_id INTEGER NOT NULL, "
        f'"{coluna_talhao}" TEXT NOT NULL, '
        "a REAL, b REAL, c REAL, itd REAL, r2 REAL, "
        "n_pontos INTEGER NOT NULL, status TEXT NOT NULL, mensagem TEXT)"
    )
    conn.execute(
        f'CREATE INDEX IF NOT EXISTS idx_lote_mip_ajuste_cenario '
        f'ON "{TABELA_LOTE_MIP_AJUSTE_LOGISTICO}"(cenario_id)'
    )


def calcular_mip_continuo(
    conn: sqlite3.Connection,
    coluna_forma_idade: str, coluna_escala_idade: str,
    sufixo_tabela: str = "",
    coluna_dap_min: Optional[str] = None, coluna_truncado_esquerda: Optional[str] = None,
    cenario_id: Optional[int] = None,
) -> Dict:
    """(Re)calcula `simulacao_mip_continuo` — DD/IP/IPM
    por (talhão, idade simulada) — e `simulacao_mip_ajuste_logistico` —
    a/b/c/ITD/R² do ajuste logístico por talhão (ver ajustar_logistico),
    a partir da coluna de forma/escala "por idade"
    (`coluna_forma_idade`/`coluna_escala_idade` — tipicamente
    forma_atual/escala_atual) de `simulacao_talhao_idade`, comparando
    cada idade contra a idade IMEDIATAMENTE anterior simulada (ver o
    cabeçalho da seção e _calcular_mip_talhao pro método exato).

    O ajuste logístico roda sobre (idade, 1/IP) ou (idade, 1/IPM),
    conforme `obter_base_ajuste_logistico`/Configurações (padrão: IP) —
    idades com IP (ou IPM) igual a zero saem do ajuste (1/0 não é
    definido). Como IPM = IP/idade, IPM tende a já vir decrescente desde
    a 1ª idade simulada (a divisão por uma idade sempre crescente esconde
    o platô que pode existir no IP bruto), o que empurra a ITD ajustada
    pra perto de zero mesmo quando o IP bruto só começa a declinar bem
    mais tarde; a opção "IP" (padrão) evita essa distorção.
    `simulacao_mip_continuo` sempre grava IP e IPM (a escolha só afeta
    qual dos dois alimenta o ajuste logístico, não o que é persistido por
    idade) — em fração (0-1), não em pontos percentuais.

    A 1ª idade de cada talhão fica com DD/IP/IPM NULL (sem idade anterior
    pra comparar). Da 2ª idade em diante, toda idade tem IP/IPM
    numéricos — idade sem transição negativo->positivo grava IP = IPM =
    0,0; só o Diâmetro Diferenciador fica NULL nesse caso. Só fica de
    fora quem não tem forma/escala em vigor naquela idade (ex: talhão sem
    ajuste Weibull "Por Talhão"/"Por Simulação" pra aquela etapa).

    `coluna_dap_min`/`coluna_truncado_esquerda` (opcionais — mesmo papel
    de calcular_distribuicao_diametrica: só faz sentido passar quando
    `coluna_forma_idade`/`coluna_escala_idade` forem forma_atual/
    escala_atual, ex: "dap_min_atual"/"truncado_esquerda_atual"): habilita
    a grandeza por classe truncada à esquerda (ver
    densidade_weibull/probabilidades_por_classe/_calcular_mip_talhao) nas
    idades cuja Weibull "Por Simulação" foi ajustada truncada — cada idade
    usa seu próprio dap_min (a etapa vigente muda de idade em idade).

    A grandeza por classe usada pro delta entre idades é escolhida por
    `obter_base_calculo_mip`/Configurações ("fdp", padrão, ou "classe" —
    ver BASES_CALCULO_MIP/_calcular_mip_talhao); "classe" usa a mesma
    normalização (aditiva/proporcional) de `obter_tipo_normalizacao_weibull`
    que simulacao_distribuicao_diametrica já usa — "fdp" ignora essa
    normalização (densidade pontual não é normalizada por classe).

    Talhão com menos de MINIMO_PONTOS_AJUSTE_LOGISTICO pontos (idade,
    1/IP ou 1/IPM) válidos, ou cujo curve_fit não converge, fica com
    status DADOS_INSUFICIENTES/ERRO_AJUSTE (e parâmetros NULL) em
    simulacao_mip_ajuste_logistico — não interrompe o cálculo dos outros
    talhões.

    `sufixo_tabela`: mesmo papel que em gerar_populacao — lê
    "simulacao_talhao_idade{sufixo_tabela}" e grava as duas tabelas com
    esse mesmo sufixo, em vez dos nomes canônicos. Ignorado se
    `cenario_id` for passado (ver abaixo).

    `cenario_id` (opcional): quando informado, este é UM cenário do lote
    "Múltiplos cenários"/"Grade automática" — lê de `simulacao_lote_
    populacao` (filtrando por cenario_id) em vez da tabela sufixada, e
    grava (DELETE idempotente + INSERT) nas tabelas unificadas
    `simulacao_lote_mip_continuo`/`simulacao_lote_mip_ajuste_logistico`
    em vez de DROP+CREATE numa tabela sufixada própria — mesmo motivo de
    persistir_cenario_no_lote (ver ali).

    Levanta ValueError se a simulação ainda não tiver sido gerada, a
    coluna de talhão não estiver configurada, as classes diamétricas não
    estiverem configuradas, ou a coluna de forma/escala não existir em
    simulacao_talhao_idade."""
    tabela_populacao = TABELA_LOTE_POPULACAO if cenario_id is not None else TABELA_POPULACAO + sufixo_tabela
    tabela_mip = TABELA_MIP_CONTINUO + sufixo_tabela
    tabela_ajuste = TABELA_MIP_AJUSTE_LOGISTICO + sufixo_tabela

    coluna_talhao = obter_coluna_talhao(conn)
    if not coluna_talhao:
        raise ValueError(
            "Coluna de talhão não configurada. Gere a simulação em \"Simulação\" antes de "
            "calcular o MIP contínuo."
        )

    existe_populacao = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela_populacao,)
    ).fetchone()
    if existe_populacao is None:
        raise ValueError("Nenhuma simulação gerada ainda. Rode \"Gerar simulação\" primeiro.")
    if cenario_id is not None:
        tem_linha = conn.execute(
            f'SELECT 1 FROM "{tabela_populacao}" WHERE cenario_id = ? LIMIT 1', (cenario_id,)
        ).fetchone()
        if tem_linha is None:
            raise ValueError("Nenhuma simulação gerada ainda. Rode \"Gerar simulação\" primeiro.")

    _validar_colunas_populacao(conn, coluna_forma_idade, coluna_escala_idade, tabela_populacao)
    classes = obter_classes_diametricas(conn)

    colunas_extras_sql = ""
    if coluna_dap_min is not None and coluna_truncado_esquerda is not None:
        colunas_extras_sql = (
            f', "{coluna_dap_min}" AS dap_min, "{coluna_truncado_esquerda}" AS truncado_esquerda')
    filtro_cenario_sql = " WHERE cenario_id = ?" if cenario_id is not None else ""
    parametros = (cenario_id,) if cenario_id is not None else ()
    df = pd.read_sql_query(
        f'SELECT id, "{coluna_talhao}" AS talhao, "{_COLUNA_IDADE_PADRAO}" AS idade, '
        f'"{coluna_forma_idade}" AS forma_idade, "{coluna_escala_idade}" AS escala_idade'
        f'{colunas_extras_sql} '
        f'FROM "{tabela_populacao}"{filtro_cenario_sql}',
        conn, params=parametros,
    )
    df["talhao"] = df["talhao"].astype(str)

    base_calculo_mip = obter_base_calculo_mip(conn)
    tipo_normalizacao = obter_tipo_normalizacao_weibull(conn)
    resultado_mip = _calcular_mip_para_colunas(df, classes, base_calculo_mip, tipo_normalizacao)

    if cenario_id is not None:
        _garantir_tabelas_lote_mip(conn, coluna_talhao)
        conn.execute(f'DELETE FROM "{TABELA_LOTE_MIP_CONTINUO}" WHERE cenario_id = ?', (cenario_id,))
        conn.execute(
            f'DELETE FROM "{TABELA_LOTE_MIP_AJUSTE_LOGISTICO}" WHERE cenario_id = ?', (cenario_id,))
    else:
        conn.execute(f'DROP TABLE IF EXISTS "{tabela_mip}"')
        conn.execute(
            f'CREATE TABLE "{tabela_mip}" ('
            "id INTEGER PRIMARY KEY, populacao_id INTEGER NOT NULL, "
            f'"{coluna_talhao}" TEXT, idade_simulada INTEGER, '
            "diametro_diferenciador REAL, ingresso_percentual REAL, "
            "ingresso_percentual_medio REAL)"
        )
    linhas_mip = [
        (i + 1, int(linha.id), linha.talhao, int(linha.idade),
         None if pd.isna(linha.diametro_diferenciador) else float(linha.diametro_diferenciador),
         None if pd.isna(linha.ingresso_percentual) else float(linha.ingresso_percentual),
         None if pd.isna(linha.ingresso_percentual_medio) else float(linha.ingresso_percentual_medio))
        for i, linha in enumerate(resultado_mip.itertuples(index=False))
    ]
    if cenario_id is not None:
        conn.executemany(
            f'INSERT INTO "{TABELA_LOTE_MIP_CONTINUO}" '
            f'(cenario_id, populacao_id, "{coluna_talhao}", idade_simulada, diametro_diferenciador, '
            "ingresso_percentual, ingresso_percentual_medio) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(cenario_id,) + linha[1:] for linha in linhas_mip],
        )
    else:
        conn.executemany(
            f'INSERT INTO "{tabela_mip}" '
            f'(id, populacao_id, "{coluna_talhao}", idade_simulada, diametro_diferenciador, '
            "ingresso_percentual, ingresso_percentual_medio) VALUES (?, ?, ?, ?, ?, ?, ?)",
            linhas_mip,
        )

    if cenario_id is None:
        conn.execute(f'DROP TABLE IF EXISTS "{tabela_ajuste}"')
        conn.execute(
            f'CREATE TABLE "{tabela_ajuste}" ('
            "id INTEGER PRIMARY KEY, "
            f'"{coluna_talhao}" TEXT NOT NULL, '
            "a REAL, b REAL, c REAL, itd REAL, r2 REAL, "
            "n_pontos INTEGER NOT NULL, status TEXT NOT NULL, mensagem TEXT)"
        )
    idade_maxima_manejo = obter_idade_maxima_manejo(conn)
    base_ajuste_logistico = obter_base_ajuste_logistico(conn)
    coluna_alvo_ajuste = (
        "ingresso_percentual" if base_ajuste_logistico == "ip" else "ingresso_percentual_medio")
    rotulo_alvo_ajuste = "1/IP" if base_ajuste_logistico == "ip" else "1/IPM"
    linhas_ajuste = []
    for idx, (talhao, grupo) in enumerate(resultado_mip.groupby("talhao", sort=False), start=1):
        # != 0: 1/0 não é definido — idade sem ingresso (IP ou IPM = 0,0,
        # ver _calcular_mip_talhao) não entra no ajuste.
        pontos = grupo[["idade", coluna_alvo_ajuste]].dropna()
        pontos = pontos[pontos[coluna_alvo_ajuste] != 0]
        n_pontos = len(pontos)
        if n_pontos < MINIMO_PONTOS_AJUSTE_LOGISTICO:
            linhas_ajuste.append((
                idx, talhao, None, None, None, None, None, n_pontos, "DADOS_INSUFICIENTES",
                f"Apenas {n_pontos} idade(s) com {rotulo_alvo_ajuste} válido "
                f"(mínimo: {MINIMO_PONTOS_AJUSTE_LOGISTICO}).",
            ))
            continue
        x = pontos["idade"].to_numpy(dtype=float)
        y = 1.0 / pontos[coluna_alvo_ajuste].to_numpy(dtype=float)
        try:
            a, b, c, itd, r2 = ajustar_logistico(x, y, idade_maxima_manejo)
            if itd_fora_do_intervalo(itd, idade_maxima_manejo):
                linhas_ajuste.append((
                    idx, talhao, a, b, c, itd, r2, n_pontos, "ITD_NAO_IDENTIFICAVEL",
                    f"ITD (ln(b)/c = {itd:.2f}) cai fora de [0, {idade_maxima_manejo:g}]: o "
                    f"ponto de inflexão da curva de {rotulo_alvo_ajuste} ajustada não está "
                    "dentro da idade simulada — aumente idade_maxima_manejo ou não confie "
                    "nesse valor de ITD."))
            else:
                linhas_ajuste.append((idx, talhao, a, b, c, itd, r2, n_pontos, "OK", ""))
        except RuntimeError as erro:
            linhas_ajuste.append(
                (idx, talhao, None, None, None, None, None, n_pontos, "ERRO_AJUSTE", str(erro)))
    if cenario_id is not None:
        conn.executemany(
            f'INSERT INTO "{TABELA_LOTE_MIP_AJUSTE_LOGISTICO}" '
            f'(cenario_id, "{coluna_talhao}", a, b, c, itd, r2, n_pontos, status, mensagem) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(cenario_id,) + linha[1:] for linha in linhas_ajuste],
        )
    else:
        conn.executemany(
            f'INSERT INTO "{tabela_ajuste}" '
            f'(id, "{coluna_talhao}", a, b, c, itd, r2, n_pontos, status, mensagem) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            linhas_ajuste,
        )
    conn.commit()

    talhoes_com_dd = int(
        resultado_mip.dropna(subset=["diametro_diferenciador"])["talhao"].nunique()
    ) if not resultado_mip.empty else 0
    talhoes_ajustados = sum(1 for linha in linhas_ajuste if linha[8] == "OK")

    return {
        "executado": True,
        "talhoes": int(resultado_mip["talhao"].nunique()) if not resultado_mip.empty else 0,
        "linhas_mip": len(linhas_mip),
        "talhoes_com_dd": talhoes_com_dd,
        "talhoes_ajustados": talhoes_ajustados,
        "talhoes_sem_ajuste": len(linhas_ajuste) - talhoes_ajustados,
    }


def calcular_mip_para_cenario(
    conn: sqlite3.Connection, configuracao: Dict, resultado: Dict, sufixo_tabela: str = "",
    cenario_id: Optional[int] = None,
) -> None:
    """Roda o MIP contínuo (ver calcular_mip_continuo) pra UM cenário já
    persistido, gravando em `resultado["resultado_mip_continuo"]`/
    ["aviso_mip_continuo"] — mesma coluna "por idade" que a distribuição
    diamétrica já usou (coluna_forma_distribuicao/escala_distribuicao
    apontadas em `configuracao`, senão resultado["coluna_forma_atual"]/
    ["coluna_escala_atual"]) — mantém a tela de Ingressos coerente com o
    que "Curvas de Distribuição" já mostra. Compara cada idade contra a
    idade imediatamente anterior simulada.

    A fdp truncada (dap_min_atual/truncado_esquerda_atual) só é passada
    quando NENHUMA coluna de override foi apontada — coluna_forma/
    escala_distribuicao apontam pra uma coluna arbitrária (ex: gerada no
    Construtor de Variáveis) que não tem um dap_min/truncado_esquerda
    correspondente, então não faz sentido tentar truncar ali.

    Extraída de app/screens/simulacao.py:_gerar_uma_simulacao pra também
    rodar no processo PRINCIPAL do lote paralelo, logo depois de
    persistir um cenário computado por um worker (ver
    app/screens/simulacao.py:_ThreadGerarLote) — MIP lê a tabela já
    gravada (via `conn`), não dá pra calcular em memória junto com o
    resto (ver calcular_cenario_em_memoria).

    `cenario_id`: repassado direto pra calcular_mip_continuo — quando
    informado, lê/grava nas tabelas unificadas do lote (simulacao_lote_*)
    em vez de uma tabela sufixada própria."""
    coluna_forma_distribuicao = configuracao["coluna_forma_distribuicao"]
    coluna_escala_distribuicao = configuracao["coluna_escala_distribuicao"]
    resultado["aviso_mip_continuo"] = None
    try:
        coluna_forma_mip = coluna_forma_distribuicao or resultado["coluna_forma_atual"]
        coluna_escala_mip = coluna_escala_distribuicao or resultado["coluna_escala_atual"]
        if coluna_forma_distribuicao or coluna_escala_distribuicao:
            coluna_dap_min_mip = None
            coluna_truncado_esquerda_mip = None
        else:
            coluna_dap_min_mip = resultado["coluna_dap_min_atual"]
            coluna_truncado_esquerda_mip = resultado["coluna_truncado_esquerda_atual"]
        resultado["resultado_mip_continuo"] = calcular_mip_continuo(
            conn, coluna_forma_mip, coluna_escala_mip, sufixo_tabela=sufixo_tabela,
            coluna_dap_min=coluna_dap_min_mip, coluna_truncado_esquerda=coluna_truncado_esquerda_mip,
            cenario_id=cenario_id,
        )
    except ValueError as e:
        resultado["resultado_mip_continuo"] = {"executado": False}
        resultado["aviso_mip_continuo"] = (
            f"Simulação gerada, mas não foi possível calcular o MIP contínuo:\n{e}")


def obter_mip_continuo(conn: sqlite3.Connection, sufixo_tabela: str = "") -> pd.DataFrame:
    """Lê o MIP contínuo já persistido por calcular_mip_continuo (rodado
    dentro de "Gerar simulação") — não recalcula nada. Retorna talhao,
    idade, diametro_diferenciador, ingresso_percentual,
    ingresso_percentual_medio. Levanta ValueError se a tabela ainda não
    existir."""
    coluna_talhao = obter_coluna_talhao(conn)
    if not coluna_talhao:
        raise ValueError("Coluna de talhão não configurada.")
    tabela = TABELA_MIP_CONTINUO + sufixo_tabela
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
    ).fetchone()
    if existe is None:
        raise ValueError("Nenhum MIP contínuo calculado ainda. Rode \"Gerar simulação\" primeiro.")
    df = pd.read_sql_query(
        f'SELECT "{coluna_talhao}" AS talhao, idade_simulada AS idade, '
        "diametro_diferenciador, ingresso_percentual, ingresso_percentual_medio "
        f'FROM "{tabela}"',
        conn,
    )
    df["talhao"] = df["talhao"].astype(str)
    return df


def obter_mip_ajuste_logistico(conn: sqlite3.Connection, sufixo_tabela: str = "") -> pd.DataFrame:
    """Lê os parâmetros do ajuste logístico (a, b, c, ITD = ln(b)/c, R²,
    n_pontos, status, mensagem) já persistidos por talhão. Levanta
    ValueError se a tabela ainda não existir."""
    coluna_talhao = obter_coluna_talhao(conn)
    if not coluna_talhao:
        raise ValueError("Coluna de talhão não configurada.")
    tabela = TABELA_MIP_AJUSTE_LOGISTICO + sufixo_tabela
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
    ).fetchone()
    if existe is None:
        raise ValueError(
            "Nenhum ajuste logístico calculado ainda. Rode \"Gerar simulação\" primeiro.")
    df = pd.read_sql_query(
        f'SELECT "{coluna_talhao}" AS talhao, a, b, c, itd, r2, n_pontos, status, mensagem '
        f'FROM "{tabela}"',
        conn,
    )
    df["talhao"] = df["talhao"].astype(str)
    return df


# ==========================================================
# CURVAS DE DISTRIBUIÇÃO POR IDADE (tela Curvas de Distribuição)
# ==========================================================

def obter_talhoes_disponiveis(conn: sqlite3.Connection) -> List[str]:
    """Talhões distintos já simulados (simulacao_talhao_idade), ordenados —
    usado pra escolher qual talhão visualizar. Lista vazia se a coluna de
    talhão não estiver configurada ou a simulação não tiver sido gerada."""
    coluna_talhao = obter_coluna_talhao(conn)
    if not coluna_talhao:
        return []
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_POPULACAO,)
    ).fetchone()
    if existe is None:
        return []
    linhas = conn.execute(
        f'SELECT DISTINCT "{coluna_talhao}" FROM "{TABELA_POPULACAO}" ORDER BY "{coluna_talhao}"'
    ).fetchall()
    return [str(linha[0]) for linha in linhas if linha[0] is not None]


def obter_distribuicoes_por_talhao(conn: sqlite3.Connection, talhao: str) -> pd.DataFrame:
    """Distribuição diamétrica (classe × probabilidade) de cada idade
    simulada pra um talhão — usado pra sobrepor as curvas de distribuição
    idade a idade (tela Curvas de Distribuição). Retorna colunas idade,
    classe_diametrica, probabilidade. Levanta ValueError se a simulação ou
    a distribuição diamétrica ainda não tiverem sido geradas."""
    coluna_talhao = obter_coluna_talhao(conn)
    if not coluna_talhao:
        raise ValueError(
            "Coluna de talhão não configurada. Gere a simulação em \"Simulação\" primeiro."
        )

    for tabela, mensagem in (
        (TABELA_POPULACAO, "Nenhuma simulação gerada ainda. Rode \"Gerar simulação\" primeiro."),
        (TABELA_DISTRIBUICAO, "Nenhuma distribuição diamétrica calculada ainda."),
    ):
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
        ).fetchone()
        if existe is None:
            raise ValueError(mensagem)

    return pd.read_sql_query(
        f'SELECT p."{_COLUNA_IDADE_PADRAO}" AS idade, d.classe_diametrica, d.probabilidade '
        f'FROM "{TABELA_POPULACAO}" p '
        f'JOIN "{TABELA_DISTRIBUICAO}" d ON d.populacao_id = p.id '
        f'WHERE p."{coluna_talhao}" = ? '
        f'ORDER BY p."{_COLUNA_IDADE_PADRAO}", d.classe_diametrica',
        conn, params=(talhao,),
    )
