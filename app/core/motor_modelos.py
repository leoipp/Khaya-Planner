# -*- coding: utf-8 -*-
"""
Motor de avaliação das equações cadastradas em "Modelos" (app/screens/modelos.py).

Até a criação do Construtor de Variáveis (app/screens/construtor_variaveis.py), a
equação/coeficientes de um modelo eram só texto/JSON guardados pra documentação —
nada no código os interpretava. Este módulo é o primeiro consumidor real: avalia a
equação vetorizadamente (uma Series pandas por variável, index alinhado), substituindo
os nomes das "Variáveis (x)" pelas colunas ligadas a elas no grafo e os coeficientes
pelos valores cadastrados no modelo.

Não é um parser de fórmulas de verdade — é um `eval` com namespace restrito (sem
builtins, só funções matemáticas + coeficientes + variáveis). Suficiente pras fórmulas
de manejo florestal (Weibull, Chapman-Richards, Schumacher etc.), que são expressões
algébricas simples, mas não tenta ser uma linguagem de fórmulas robusta a entrada
adversária — quem edita a equação é o próprio usuário do app, não um terceiro.
"""
import numpy as np
import pandas as pd
from functools import lru_cache

_FUNCOES_PERMITIDAS = {
    "exp": np.exp, "EXP": np.exp,
    "ln": np.log, "LN": np.log,
    "log": np.log10, "LOG": np.log10,
    "log10": np.log10, "LOG10": np.log10,
    "log2": np.log2, "LOG2": np.log2,
    "sqrt": np.sqrt, "SQRT": np.sqrt,
    "abs": np.abs, "ABS": np.abs,
    "min": np.minimum, "MIN": np.minimum,
    "max": np.maximum, "MAX": np.maximum,
    "pi": np.pi, "PI": np.pi,
    "e": np.e, "E": np.e,
}


def extrair_expressao(equacao: str) -> str:
    """"f(dap_med_atual) = (forma/escala)*..." -> "(forma/escala)*...".
    Se não tiver "=" (usuário só escreveu a fórmula pura), usa o texto inteiro."""
    texto = (equacao or "").strip()
    if "=" in texto:
        return texto.split("=", 1)[1].strip()
    return texto


@lru_cache(maxsize=1024)
def _compilar_expressao(equacao: str):
    """Normaliza e compila uma equação uma única vez por processo.

    A chave é o próprio texto integral cadastrado pelo usuário; qualquer
    edição produz automaticamente outra entrada. O cache não conhece nós,
    modelos ou topologias e portanto não reduz a flexibilidade do grafo.
    """
    expressao = extrair_expressao(equacao)
    if not expressao:
        raise ValueError("Equação vazia.")
    expressao = expressao.replace("^", "**")
    try:
        return compile(expressao, "<modelo>", "eval"), expressao
    except (SyntaxError, ValueError) as e:
        raise ValueError(f'Erro ao compilar "{expressao}": {e}')


def _avaliar_expressao_bruta(equacao: str, coeficientes: dict, variaveis: dict):
    """Núcleo comum de avaliação — monta o namespace (funções permitidas +
    coeficientes + variáveis) e roda o `eval`, sem se importar com o tipo
    de cada variável (pd.Series, np.ndarray, escalar...). "^" é tratado
    como potência (o app usa esse símbolo nos exemplos de equação, mas em
    Python "^" é XOR bit a bit — sem essa troca, qualquer fórmula com "^"
    daria resultado errado silenciosamente em vez de calcular a potência
    esperada). Levanta ValueError com mensagem legível se a equação
    estiver vazia ou referenciar algo fora do namespace (nome de variável
    não ligada, erro de digitação). Devolve o resultado cru do `eval`
    (Series, ndarray ou escalar) — quem chama decide como empacotar."""
    codigo, expressao = _compilar_expressao(equacao)

    namespace = {"__builtins__": {}}
    namespace.update(_FUNCOES_PERMITIDAS)
    namespace.update(coeficientes or {})
    namespace.update(variaveis or {})

    try:
        return eval(codigo, namespace)
    except Exception as e:
        raise ValueError(f"Erro ao avaliar \"{expressao}\": {e}")


def avaliar_modelo(equacao: str, coeficientes: dict, variaveis: dict, index_padrao=None):
    """Avalia a equação de um modelo. `variaveis` mapeia nome_da_variavel (igual ao
    cadastrado em "Variáveis (x)") -> pd.Series; `coeficientes` mapeia nome -> float
    (igual ao cadastrado em "Coeficientes").

    Levanta ValueError com mensagem legível se a equação estiver vazia, referenciar
    algo fora do namespace (nome de variável não ligada, erro de digitação) ou o
    resultado não puder ser convertido em número.

    Retorna sempre um pd.Series; se a expressão não referenciar nenhuma variável
    (equação só com coeficientes/constantes), o escalar resultante é expandido pro
    índice de uma das variáveis recebidas (ou `index_padrao`, se nenhuma variável
    foi passada)."""
    resultado = _avaliar_expressao_bruta(equacao, coeficientes, variaveis)

    if isinstance(resultado, pd.Series):
        return resultado

    indice = None
    if variaveis:
        primeira = next(iter(variaveis.values()))
        if isinstance(primeira, pd.Series):
            indice = primeira.index
    if indice is None:
        indice = index_padrao

    try:
        valor = float(resultado)
    except (TypeError, ValueError):
        raise ValueError(f"O resultado da equação não é numérico: {resultado!r}")

    if indice is None:
        return pd.Series([valor])
    return pd.Series(valor, index=indice)


def avaliar_expressao_array(equacao: str, coeficientes: dict, variaveis: dict) -> np.ndarray:
    """Mesmo núcleo de avaliação de `avaliar_modelo`, mas pra variáveis que são
    np.ndarray (inclusive grades 2D, ex: altura x linha) em vez de pd.Series —
    usado pelo nó "Afilamento" do Construtor de Variáveis pra avaliar a equação
    de afilamento numa grade inteira de alturas de uma vez só (muito mais rápido
    que um `eval` por passo de altura). Sempre devolve um np.ndarray (broadcast
    conforme o shape das variáveis/coeficientes envolvidos na expressão)."""
    resultado = _avaliar_expressao_bruta(equacao, coeficientes, variaveis)
    try:
        return np.asarray(resultado, dtype=float)
    except (TypeError, ValueError):
        raise ValueError(f"O resultado da equação não é numérico: {resultado!r}")
