# -*- coding: utf-8 -*-
"""
Ajuste de distribuições Weibull 2P (Shape/Scale, Location fixado em
zero) por máxima verossimilhança, a partir das árvores remanescentes
gravadas pela simulação de intensidades (app/intensidades.py).

Adaptado do script standalone de ajuste Weibull por chave: mesma
estimação por MLE (Shape resolvido pela equação de verossimilhança via
busca de raiz com brentq; Scale pela forma fechada condicional ao Shape
já estimado, ambos em escala logarítmica pra evitar overflow). Duas
diferenças em relação ao script original:

- os DAPs já vêm como REAL da tabela gerada pela própria simulação (não
  de uma planilha externa que pode ter vírgula decimal, texto solto
  etc.), então não precisa da conversão numérica tolerante a texto;
- o agrupamento é fixo — talhão, int_raleio, int_desbaste_1,
  int_desbaste_2 e etapa —, olhando só pras árvores com
  situacao='Remanescente', em vez de colunas-chave configuráveis.
"""
import sqlite3
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .intensidades import ETAPAS, TABELA_DETALHAMENTO, TABELA_RESUMO_TALHAO

MINIMO_OBSERVACOES = 2

_INTENSIDADE_DA_ETAPA = {
    "Após raleio": "int_raleio",
    "Após 1º desbaste": "int_desbaste_1",
    "Após 2º desbaste": "int_desbaste_2",
}

# Etapa gravada em parametros_weibull_manejo.manejo (rótulo das árvores
# remanescentes, ver app/intensidades.py) -> sufixo de coluna usado em
# intensidades_resumo_talhao (app/intensidades.py, ETAPAS) — de onde
# vêm dap_med/dap_max/dap_min/fustes_ha_removidos já agregados por
# talhão, pra manter consistência numérica com o resumo em vez de
# recalcular aqui.
_SUFIXO_INTENSIDADES_POR_ETAPA = {
    "Após raleio": "raleio",
    "Após 1º desbaste": "desbaste_1",
    "Após 2º desbaste": "desbaste_2",
}


def _ou_none(valor):
    """Converte NaN/None em None (bind sqlite3); mantém o resto como float."""
    return None if valor is None or pd.isna(valor) else float(valor)


# ==========================================================
# AJUSTE WEIBULL 2P POR MÁXIMA VEROSSIMILHANÇA
# ==========================================================

def _estimar_shape_mle(x: np.ndarray) -> float:
    """Resolve a equação do MLE pro Shape diretamente:
    sum(x^k * ln(x)) / sum(x^k) - mean(ln(x)) - 1/k = 0."""
    log_x = np.log(x)
    media_log_x = np.mean(log_x)

    def equacao_shape(shape):
        # Escala logarítmica reduz risco de overflow em x^shape.
        potencias_log = shape * log_x
        maximo = np.max(potencias_log)
        pesos = np.exp(potencias_log - maximo)
        media_log_ponderada = np.sum(pesos * log_x) / np.sum(pesos)
        return media_log_ponderada - media_log_x - 1.0 / shape

    limite_inferior = 1e-6
    limite_superior = 10.0
    valor_inferior = equacao_shape(limite_inferior)
    valor_superior = equacao_shape(limite_superior)

    while np.sign(valor_inferior) == np.sign(valor_superior) and limite_superior < 1e6:
        limite_superior *= 2.0
        valor_superior = equacao_shape(limite_superior)

    if np.sign(valor_inferior) == np.sign(valor_superior):
        raise RuntimeError("Não foi possível localizar uma raiz positiva para o parâmetro Shape.")

    return float(brentq(
        equacao_shape, limite_inferior, limite_superior, xtol=1e-13, rtol=1e-13, maxiter=1000
    ))


def _calcular_scale_mle(x: np.ndarray, shape: float) -> float:
    """scale = [mean(x^shape)]^(1/shape), em escala logarítmica."""
    valores_log = shape * np.log(x)
    maximo = np.max(valores_log)
    log_media_potencias = maximo + np.log(np.mean(np.exp(valores_log - maximo)))
    return float(np.exp(log_media_potencias / shape))


# ==========================================================
# AJUSTE WEIBULL 2P TRUNCADA À ESQUERDA (opcional, ver ajustar_grupo)
# ==========================================================
#
# Quando a amostra vem de uma população que sofreu remoção por baixo (ex:
# desbaste por área basal, que corta as árvores menores primeiro), o MLE
# comum acima (_estimar_shape_mle/_calcular_scale_mle) trata as árvores
# remanescentes como se fossem a população inteira — sem saber que a
# cauda esquerda foi cortada, ele "explica" a variância menor da amostra
# encolhendo o Shape... na verdade inflando (Weibull com Shape maior tem
# MENOS variância/mais concentrada), o que empurra a distribuição pra
# menos assimétrica-à-direita do que a população de origem realmente é.
#
# Densidade condicional a x > tau (tau = ponto de corte da truncagem):
#     f_trunc(x) = f(x) / S(tau),   S(tau) = exp(-(tau/scale)^shape)
#
# Isso soma um termo à log-verossimilhança: + n*(tau/scale)^shape.
# Derivando de novo (mesmo raciocínio de _calcular_scale_mle/
# _estimar_shape_mle), o Scale continua em forma fechada, só que
# corrigido:
#     scale^shape = mean(x^shape) - tau^shape
# e a equação do Shape ganha o termo de tau:
#     [sum(x^k*ln x) - n*tau^k*ln(tau)] / [sum(x^k) - n*tau^k]
#         - mean(ln x) - 1/k = 0
# (tau=0 recupera exatamente as fórmulas não-truncadas acima — é só
# conferir substituindo.)
#
# tau é conhecido, não precisa ser estimado: como a remoção é por área
# basal a partir dos menores, tau É o DAP mínimo remanescente do grupo
# (quem chama passa esse valor — ver ajustar_a_partir_da_simulacao).


def _estimar_shape_mle_truncado(x: np.ndarray, tau: float) -> float:
    """Igual a _estimar_shape_mle, mas com a correção de truncagem à
    esquerda em tau (ver comentário acima). `tau` deve ser <= min(x) —
    quem chama garante isso (ver ajustar_grupo).

    Ao contrário da equação sem truncagem (monotônica, só cruza zero uma
    vez — por isso o MLE comum só olha o sinal nas duas pontas), a
    truncada pode ter MAIS de uma raiz quando há observações empatadas
    exatamente (ou bem perto) de `tau` — nesse caso os dois EXTREMOS de
    uma busca ingênua podem ter o mesmo sinal mesmo havendo raiz(es) no
    meio (a função mergulha de sinal e volta). Por isso a busca aqui
    varre um grid log-espaçado, acha TODAS as trocas de sinal, refina
    cada uma com brentq, e — se houver mais de uma raiz candidata —
    escolhe a de maior log-verossimilhança: é isso que a torna o MLE de
    verdade, não só "uma" solução qualquer da equação de score."""
    log_x = np.log(x)
    log_tau = np.log(tau)
    media_log_x = np.mean(log_x)
    soma_log_x = np.sum(log_x)
    n = len(x)

    def equacao_shape(shape):
        # Mesmo truque de escala logarítmica do MLE comum, incluindo o
        # termo de tau na mesma base (maximo) pra continuar estável.
        potencias_log = shape * log_x
        potencia_log_tau = shape * log_tau
        maximo = max(np.max(potencias_log), potencia_log_tau)
        pesos = np.exp(potencias_log - maximo)
        peso_tau = np.exp(potencia_log_tau - maximo)
        denominador = np.sum(pesos) - n * peso_tau
        numerador = np.sum(pesos * log_x) - n * peso_tau * log_tau
        media_log_ponderada = numerador / denominador
        return media_log_ponderada - media_log_x - 1.0 / shape

    def log_m(shape):
        # ln(M), M = mean(x^shape) - tau^shape (mesmo cálculo estável de
        # _calcular_scale_mle_truncado, só devolvendo ln(M) direto).
        valores_log = shape * log_x
        log_tau_shape = shape * log_tau
        maximo = max(np.max(valores_log), log_tau_shape)
        soma_pesos = np.sum(np.exp(valores_log - maximo))
        peso_tau = np.exp(log_tau_shape - maximo)
        media_menos_tau = (soma_pesos - n * peso_tau) / n
        return maximo + np.log(media_menos_tau)

    def log_verossimilhanca(shape):
        # ℓ_trunc(shape) = n*ln(shape) - n*ln(M) + (shape-1)*sum(ln x) - n
        # — forma fechada depois de substituir scale = M^(1/shape) (ver
        # comentário no topo do arquivo).
        return n * np.log(shape) - n * log_m(shape) + (shape - 1.0) * soma_log_x - n

    limite_inferior = 1e-6
    limite_superior = 10.0

    # Caminho rápido: a grande maioria dos grupos tem a mesma equação
    # bem-comportada (só uma raiz, igual o MLE comum) — testar só as duas
    # pontas primeiro (mesmo custo de _estimar_shape_mle) resolve quase
    # tudo num instante. Só cai pro grid varredura completa (mais caro —
    # ver comentário abaixo) quando as pontas empatam de sinal, que é
    # justamente o sinal de que pode haver raízes escondidas no meio
    # (grupos pequenos/muito próximos de tau).
    valor_inferior = equacao_shape(limite_inferior)
    valor_superior = equacao_shape(limite_superior)
    if np.sign(valor_inferior) != np.sign(valor_superior):
        return float(brentq(
            equacao_shape, limite_inferior, limite_superior, xtol=1e-13, rtol=1e-13, maxiter=1000))

    pontos_grid = 200
    candidatos: List[float] = []
    while not candidatos and limite_superior < 1e6:
        grid = np.geomspace(limite_inferior, limite_superior, pontos_grid)
        valores = np.array([equacao_shape(s) for s in grid])
        sinais = np.sign(valores)
        for i in range(len(grid) - 1):
            if sinais[i] == 0:
                candidatos.append(float(grid[i]))
            elif sinais[i] != sinais[i + 1]:
                candidatos.append(float(brentq(
                    equacao_shape, grid[i], grid[i + 1], xtol=1e-13, rtol=1e-13, maxiter=1000)))
        if not candidatos:
            limite_superior *= 4.0

    # Descarta raiz(es) cujo scale implícito (exp(log_m(shape)/shape))
    # desabou perto de zero — degenerescência real da equação truncada
    # perto de shape->0 (M = mean(x^k) - tau^k tende a
    # shape*(mean(ln x) - ln(tau)) nesse limite, expansão de Taylor, então
    # scale = M^(1/shape) desaba pra 0 matematicamente, não por erro de
    # ponto flutuante — mesma patologia clássica de MLE degenerado em
    # fronteira, tipo variância zero num componente de mistura gaussiana:
    # tecnicamente pode ter log-verossimilhança competitiva, mas é um
    # ajuste sem sentido prático, "a população inteira encolhida pro
    # zero"). `tau` é a ordem de grandeza certa pra comparar — um scale
    # mil vezes menor que tau não representa nenhuma distribuição de
    # diâmetro plausível.
    candidatos_validos = [s for s in candidatos if np.exp(log_m(s) / s) >= tau * 1e-3]

    if not candidatos_validos:
        raise RuntimeError(
            "Não foi possível localizar uma raiz positiva para o parâmetro Shape (truncado).")

    if len(candidatos_validos) == 1:
        return candidatos_validos[0]

    return max(candidatos_validos, key=log_verossimilhanca)


def _calcular_scale_mle_truncado(x: np.ndarray, shape: float, tau: float) -> float:
    """scale = [mean(x^shape) - tau^shape]^(1/shape), em escala
    logarítmica (mesmo truque de _calcular_scale_mle) — tau^shape é
    computado na MESMA base (maximo) que os x^shape antes de subtrair,
    pra não perder precisão quando os dois têm ordens de grandeza
    parecidas."""
    n = len(x)
    valores_log = shape * np.log(x)
    log_tau = shape * np.log(tau)
    maximo = max(np.max(valores_log), log_tau)
    soma_pesos = np.sum(np.exp(valores_log - maximo))
    peso_tau = np.exp(log_tau - maximo)
    media_menos_tau = (soma_pesos - n * peso_tau) / n
    log_media_menos_tau = maximo + np.log(media_menos_tau)
    return float(np.exp(log_media_menos_tau / shape))


def _calcular_cv_e_dg(x: np.ndarray) -> Tuple[float, float]:
    """CV(dap) = desvio padrão amostral / média, e dg (diâmetro quadrático
    médio) = sqrt(média(dap²)) — descritivos do grupo remanescente,
    pensados pra alimentar o nó de recuperação de parâmetros (média+CV ->
    forma/escala) no Construtor de Variáveis. `x` deve vir sem NaN e sem
    valores <= 0 (mesmo filtro aplicado antes do ajuste Weibull do grupo),
    pra ficar consistente com o `n_valido` do ajuste."""
    media = float(np.mean(x))
    cv_dap = float(np.std(x, ddof=1) / media) if media > 0 else float("nan")
    dg = float(np.sqrt(np.mean(x ** 2)))
    return cv_dap, dg


def ajustar_grupo(
    valores: np.ndarray,
    minimo_observacoes: int = MINIMO_OBSERVACOES,
    remover_nao_positivos: bool = True,
    truncar_esquerda: bool = False,
    tau: Optional[float] = None,
) -> Dict:
    """Ajusta a Weibull 2P (Location=0) pra um grupo de valores (NaN é
    tratado como ausente e descartado antes de tudo). `shape` e `scale`
    vêm None quando não é possível ajustar — `status` explica o motivo
    (DADOS_INSUFICIENTES, VALOR_NAO_POSITIVO, SEM_VARIACAO, ERRO_AJUSTE
    ou OK). Quando `remover_nao_positivos` é True (padrão), valores <= 0
    são descartados como os NaN; quando False, a presença de qualquer
    valor <= 0 reprova o grupo inteiro.

    `truncar_esquerda=True` usa o MLE truncado à esquerda em `tau` (ver
    _estimar_shape_mle_truncado/_calcular_scale_mle_truncado) em vez do
    MLE comum — pensado pra amostras que sofreram remoção por baixo (ex:
    desbaste por área basal, que corta os menores primeiro), onde o MLE
    comum infla o Shape por não saber que a cauda esquerda foi cortada.
    `tau` é o ponto de corte — quem chama passa o DAP mínimo remanescente
    do grupo (ver ajustar_a_partir_da_simulacao); é ajustado (`min(tau,
    min(x))`) pra nunca ficar acima do menor valor da amostra, senão a
    correção `mean(x^shape) - tau^shape` poderia sair negativa. Ignorado
    (sem efeito) se `truncar_esquerda=False` ou `tau` não for passado —
    nesse caso cai no MLE comum, mesmo comportamento de sempre.

    O dict devolvido inclui `"truncado": bool` — True só quando o MLE
    truncado foi de fato usado (ajuste OK, com `truncar_esquerda=True` e
    `tau` informado); False em qualquer status de falha (mesmo que
    `truncar_esquerda=True` tivesse sido pedido) e no MLE comum. É esse
    flag que fica gravado por ajuste em parametros_weibull_manejo (ver
    ajustar_a_partir_da_simulacao) — quem for calcular a área/densidade
    da Weibull por classe (simulacao.probabilidades_por_classe/
    densidade_weibull, `limite_truncamento`) sabe, por linha, se PRECISA
    aplicar a correção de truncagem (dividir por S(tau)) sem depender do
    valor atual do checkbox em Configurações, que pode ter mudado desde
    que esse ajuste específico rodou."""
    x = np.asarray(valores, dtype=float)
    n_original = len(x)

    x = x[~np.isnan(x)]
    n_removido = n_original - len(x)

    n_nao_positivo = int(np.sum(x <= 0))
    if n_nao_positivo > 0:
        if remover_nao_positivos:
            x = x[x > 0]
        else:
            return {
                "n_original": n_original, "n_valido": len(x), "n_removido": n_removido,
                "n_nao_positivo": n_nao_positivo, "shape": None, "scale": None,
                "status": "VALOR_NAO_POSITIVO", "truncado": False,
                "mensagem": "O grupo contém valores menores ou iguais a zero.",
            }

    n_valido = len(x)

    if n_valido < minimo_observacoes:
        return {
            "n_original": n_original, "n_valido": n_valido, "n_removido": n_removido,
            "n_nao_positivo": n_nao_positivo, "shape": None, "scale": None,
            "status": "DADOS_INSUFICIENTES", "truncado": False,
            "mensagem": f"Apenas {n_valido} valor(es) válido(s) (mínimo: {minimo_observacoes}).",
        }

    if np.allclose(x, x[0], rtol=0.0, atol=0.0):
        return {
            "n_original": n_original, "n_valido": n_valido, "n_removido": n_removido,
            "n_nao_positivo": n_nao_positivo, "shape": None, "scale": None,
            "status": "SEM_VARIACAO", "truncado": False,
            "mensagem": "Todos os valores válidos do grupo são iguais.",
        }

    try:
        usar_truncado = truncar_esquerda and tau is not None
        if usar_truncado:
            tau_efetivo = min(float(tau), float(np.min(x)))
            shape = _estimar_shape_mle_truncado(x, tau_efetivo)
            scale = _calcular_scale_mle_truncado(x, shape, tau_efetivo)
        else:
            shape = _estimar_shape_mle(x)
            scale = _calcular_scale_mle(x, shape)
        return {
            "n_original": n_original, "n_valido": n_valido, "n_removido": n_removido,
            "n_nao_positivo": n_nao_positivo, "shape": shape, "scale": scale,
            "status": "OK", "truncado": usar_truncado, "mensagem": "",
        }
    except Exception as erro:
        return {
            "n_original": n_original, "n_valido": n_valido, "n_removido": n_removido,
            "n_nao_positivo": n_nao_positivo, "shape": None, "scale": None,
            "status": "ERRO_AJUSTE", "truncado": False, "mensagem": str(erro),
        }


# ==========================================================
# AJUSTE EM LOTE A PARTIR DA SIMULAÇÃO DE INTENSIDADES
# ==========================================================

def ajustar_a_partir_da_simulacao(
    conn: sqlite3.Connection,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    truncar_esquerda: bool = False,
) -> Dict:
    """Ajusta uma Weibull 2P por (talhão, int_raleio, int_desbaste_1,
    int_desbaste_2, etapa), usando o DAP das árvores com
    situacao='Remanescente' no detalhamento da simulação de
    intensidades, e substitui totalmente o conteúdo de
    `parametros_weibull_manejo` pelos grupos ajustados com sucesso.

    `truncar_esquerda=True` (ver ajustar_grupo/_estimar_shape_mle_truncado)
    usa o DAP mínimo remanescente do próprio grupo (`dap_min_apos_<etapa>`,
    já buscado do resumo por talhão) como ponto de corte `tau` — é o valor
    certo aqui porque a remoção da simulação de intensidades é por área
    basal a partir dos menores, então o DAP mínimo que sobrou É o ponto
    onde a população foi cortada. Padrão False = MLE comum, mesmo
    comportamento de sempre."""
    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABELA_DETALHAMENTO,)
    ).fetchone()
    if existe is None:
        raise ValueError(
            "Nenhuma simulação de intensidades encontrada. Rode a simulação em "
            "\"Simulação de Intensidades\" antes de ajustar a Weibull."
        )

    # Filtra situacao='Remanescente' no próprio SQL, não em pandas depois de
    # carregar tudo — a tabela de detalhamento tem uma linha por árvore x
    # etapa x combinação de intensidades, e trazer também as árvores
    # "Removida" (que seriam descartadas no passo seguinte) pra memória só
    # pra jogar fora dobra o consumo de RAM à toa numa base grande, podendo
    # estourar memória antes mesmo de chegar no ajuste.
    df_remanescente = pd.read_sql_query(
        f'SELECT talhao, int_raleio, int_desbaste_1, int_desbaste_2, etapa, dap '
        f'FROM "{TABELA_DETALHAMENTO}" WHERE situacao = ?',
        conn, params=("Remanescente",),
    )

    if df_remanescente.empty:
        raise ValueError("Não há árvores remanescentes na simulação de intensidades.")

    colunas_chave = ["talhao", "int_raleio", "int_desbaste_1", "int_desbaste_2", "etapa"]

    # dap_med/dap_max/dap_min/ht_med/fustes_ha_removidos/vtcc_ha_removido
    # por etapa vêm direto do resumo por talhão da simulação de
    # intensidades (já calculado com a mesma chave), em vez de
    # recalculados aqui — garante consistência numérica com
    # intensidades_resumo_talhao.
    colunas_resumo = ["talhao", "int_raleio", "int_desbaste_1", "int_desbaste_2"]
    for sufixo in ETAPAS:
        colunas_resumo += [
            f"dap_med_apos_{sufixo}", f"dap_max_apos_{sufixo}",
            f"dap_min_apos_{sufixo}", f"ht_med_apos_{sufixo}",
            f"fustes_ha_removidos_{sufixo}", f"vtcc_ha_removido_{sufixo}",
        ]
    colunas_resumo_sql = ", ".join(f'"{c}"' for c in colunas_resumo)
    df_resumo = pd.read_sql_query(
        f'SELECT {colunas_resumo_sql} FROM "{TABELA_RESUMO_TALHAO}"', conn
    ).set_index(["talhao", "int_raleio", "int_desbaste_1", "int_desbaste_2"])

    grupos = df_remanescente.groupby(colunas_chave, sort=False, dropna=False)
    total_grupos = grupos.ngroups

    linhas: List[tuple] = []
    falhas: List[Dict] = []

    for numero, (chave, grupo) in enumerate(grupos, start=1):
        talhao, int_raleio, int_desbaste_1, int_desbaste_2, etapa = chave
        valores_dap = grupo["dap"].to_numpy()

        # Busca o resumo (dap_med/dap_max/dap_min/...) ANTES do ajuste —
        # com truncar_esquerda=True, precisa do dap_min de lá como `tau`
        # (ver docstring desta função) pra passar pro ajuste; sem
        # truncagem, só usaria depois mesmo (pra gravar na linha).
        sufixo_intensidades = _SUFIXO_INTENSIDADES_POR_ETAPA[etapa]
        chave_resumo = (talhao, int_raleio, int_desbaste_1, int_desbaste_2)
        if chave_resumo in df_resumo.index:
            linha_resumo = df_resumo.loc[chave_resumo]
            dap_med = linha_resumo[f"dap_med_apos_{sufixo_intensidades}"]
            dap_max = linha_resumo[f"dap_max_apos_{sufixo_intensidades}"]
            dap_min = linha_resumo[f"dap_min_apos_{sufixo_intensidades}"]
            ht_med = linha_resumo[f"ht_med_apos_{sufixo_intensidades}"]
            fustes_ha_removidos = linha_resumo[f"fustes_ha_removidos_{sufixo_intensidades}"]
            vtcc_ha_removido = linha_resumo[f"vtcc_ha_removido_{sufixo_intensidades}"]
        else:
            dap_med = dap_max = dap_min = ht_med = fustes_ha_removidos = vtcc_ha_removido = None

        tau = float(dap_min) if (truncar_esquerda and dap_min is not None and not pd.isna(dap_min)) else None
        ajuste = ajustar_grupo(valores_dap, truncar_esquerda=truncar_esquerda, tau=tau)

        if ajuste["status"] == "OK":
            chave_intensidade = _INTENSIDADE_DA_ETAPA.get(etapa, "int_desbaste_2")
            intensidade_etapa = {
                "int_raleio": int_raleio, "int_desbaste_1": int_desbaste_1, "int_desbaste_2": int_desbaste_2,
            }[chave_intensidade]

            # Mesmo filtro (sem NaN, sem <=0) que ajustar_grupo aplicou por
            # dentro pra chegar no n_valido do ajuste — refeito aqui pra CV/dg
            # saírem sobre exatamente a mesma amostra do Weibull do grupo.
            x_valido = valores_dap[~np.isnan(valores_dap)]
            x_valido = x_valido[x_valido > 0]
            cv_dap, dg = _calcular_cv_e_dg(x_valido)

            linhas.append((
                talhao, etapa, float(intensidade_etapa),
                float(ajuste["scale"]), float(ajuste["shape"]),
                _ou_none(dap_med), _ou_none(dap_max), _ou_none(dap_min), _ou_none(fustes_ha_removidos),
                _ou_none(ht_med), _ou_none(vtcc_ha_removido), _ou_none(cv_dap), _ou_none(dg),
                float(int_raleio), float(int_desbaste_1), float(int_desbaste_2),
                f"Ajustado a partir da simulação de intensidades (N={ajuste['n_valido']}).",
                int(ajuste["truncado"]),
            ))
        else:
            falhas.append({
                "talhao": talhao, "etapa": etapa,
                "int_raleio": int_raleio, "int_desbaste_1": int_desbaste_1, "int_desbaste_2": int_desbaste_2,
                "status": ajuste["status"], "mensagem": ajuste["mensagem"],
            })

        if progress_callback is not None:
            progress_callback(numero, total_grupos)

    conn.execute("DELETE FROM parametros_weibull_manejo")
    conn.executemany(
        "INSERT INTO parametros_weibull_manejo "
        "(talhao, manejo, intensidade, escala, forma, dap_med, dap_max, dap_min, fustes_ha_removidos, "
        "ht_med, vtcc_ha_removido, cv_dap, dg, "
        "int_raleio, int_desbaste_1, int_desbaste_2, observacoes, truncado_esquerda) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        linhas,
    )
    conn.commit()

    return {
        "total_grupos": total_grupos,
        "ajustados": len(linhas),
        "sem_ajuste": len(falhas),
        "falhas": falhas,
    }
