# -*- coding: utf-8 -*-
"""
Converte o texto livre de uma equação de "Modelos" (app/screens/modelos.py,
mesma gramática avaliada por core/motor_modelos.py — "^" como potência,
EXP/LN/LOG/SQRT/ABS/MIN/MAX, nomes de variável/coeficiente livres) numa
string mathtext (subconjunto de LaTeX que o matplotlib sabe desenhar sem
precisar de uma instalação de LaTeX de verdade — ver
core/motor_modelos.py:_FUNCOES_PERMITIDAS pra a mesma lista de funções do
lado da AVALIAÇÃO) — usada só pra pré-visualizar a equação enquanto o
usuário digita (app/screens/modelos.py:_atualizar_preview_equacao), nunca
pra avaliar nada.

Não reimplementa um parser — reaproveita `ast.parse` (a equação, com "^"
trocado por "**", já é uma expressão Python válida, mesma premissa de
motor_modelos._avaliar_expressao_bruta) e percorre a árvore convertendo
cada nó pro mathtext equivalente. `ast.parse` aqui NUNCA executa nada
(diferente de `eval`) — é só a fase de análise sintática, então não herda
o risco de segurança que faria `_FUNCOES_PERMITIDAS`/namespace restrito
serem necessários.
"""
import ast

# Nível de precedência de cada operador — usado só pra decidir quando um
# sub-nó precisa de parênteses ao virar texto (ex: "(a+b)*c" precisa,
# "a+b*c" não). Divisão fica de fora de propósito: vira \frac{}{} (a
# própria fração já delimita visualmente, sem precisar de parênteses).
_PREC_SOMA = 1
_PREC_MULT = 2
_PREC_UNARIO = 3
_PREC_POTENCIA = 4
_PREC_ATOMO = 5

# Mesmos nomes (maiúsculo/minúsculo) de core/motor_modelos.py:_FUNCOES_PERMITIDAS
# — funções cujo nome mathtext tem um macro upright pronto (\exp, \ln, \min,
# \max — mathtext desenha essas retas, não em itálico, convenção matemática
# padrão pra nome de função, ao contrário de uma variável comum).
_FUNCOES_UPRIGHT = {
    "exp": r"\exp", "EXP": r"\exp",
    "ln": r"\ln", "LN": r"\ln",
    "min": r"\min", "MIN": r"\min",
    "max": r"\max", "MAX": r"\max",
}
# log/LOG são base 10 nessa gramática (ver _FUNCOES_PERMITIDAS) — diferente
# da convenção matemática comum (log = base 10 só em alguns contextos), por
# isso a base entra explícita no render, pra não confundir com log natural.
_FUNCOES_LOG_BASE = {
    "log": "10", "LOG": "10", "log10": "10", "LOG10": "10", "log2": "2", "LOG2": "2",
}
_FUNCOES_SQRT = {"sqrt", "SQRT"}
_FUNCOES_ABS = {"abs", "ABS"}
_CONSTANTES = {"pi": r"\pi", "PI": r"\pi"}


def _escapar_identificador(nome: str) -> str:
    """"dap_med_atual" -> "dap\\_med\\_atual" — mathtext usa "_" pra
    subscrito (e só aceita UM por "base", "x_a_b" dá erro de "double
    subscript"), então qualquer "_" de nome de variável/coeficiente real
    precisa vir escapado como caractere literal, não como marcador de
    subscrito."""
    return nome.replace("_", r"\_")


def _envolver_se_preciso(latex: str, prec_no: int, prec_minima: int, forcar_no_empate: bool = False) -> str:
    if prec_no < prec_minima or (forcar_no_empate and prec_no == prec_minima):
        return rf"\left({latex}\right)"
    return latex


def _renderizar_no(no) -> "tuple[str, int]":
    """Devolve (latex, precedência) — a precedência sobe pra quem chamou
    decidir se precisa envolver este pedaço em parênteses."""
    if isinstance(no, ast.BinOp):
        return _renderizar_binop(no)
    if isinstance(no, ast.UnaryOp):
        return _renderizar_unaryop(no)
    if isinstance(no, ast.Call):
        return _renderizar_chamada(no), _PREC_ATOMO
    if isinstance(no, ast.Name):
        if no.id in _CONSTANTES:
            return _CONSTANTES[no.id], _PREC_ATOMO
        return rf"\mathrm{{{_escapar_identificador(no.id)}}}", _PREC_ATOMO
    if isinstance(no, ast.Constant) and isinstance(no.value, (int, float)):
        return _formatar_numero(no.value), _PREC_ATOMO
    if isinstance(no, ast.Tuple):
        partes = [_renderizar_no(elemento)[0] for elemento in no.elts]
        return ", ".join(partes), _PREC_ATOMO
    # Nó sem tratamento específico (ex: comparação, algo fora da gramática
    # de motor_modelos.py) — cai pro texto Python cru via ast.unparse
    # (stdlib, Python 3.9+) em vez de levantar erro: o preview some/atrasa
    # graciosamente numa equação incomum, em vez de quebrar a tela.
    return rf"\mathrm{{{_escapar_identificador(ast.unparse(no))}}}", _PREC_ATOMO


def _formatar_numero(valor) -> str:
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def _renderizar_binop(no: ast.BinOp) -> "tuple[str, int]":
    esquerda, prec_esq = _renderizar_no(no.left)
    direita, prec_dir = _renderizar_no(no.right)

    if isinstance(no.op, ast.Add):
        e = _envolver_se_preciso(esquerda, prec_esq, _PREC_SOMA)
        d = _envolver_se_preciso(direita, prec_dir, _PREC_SOMA)
        return f"{e} + {d}", _PREC_SOMA
    if isinstance(no.op, ast.Sub):
        e = _envolver_se_preciso(esquerda, prec_esq, _PREC_SOMA)
        # Lado direito de "-" SEMPRE precisa de parênteses se for outra
        # soma/subtração — "a-(b-c)" != "a-b-c" (diferente de "+", onde
        # "a+(b-c)" == "a+b-c") — daí o empate forçado aqui.
        d = _envolver_se_preciso(direita, prec_dir, _PREC_SOMA, forcar_no_empate=True)
        return f"{e} - {d}", _PREC_SOMA
    if isinstance(no.op, ast.Mult):
        e = _envolver_se_preciso(esquerda, prec_esq, _PREC_MULT)
        d = _envolver_se_preciso(direita, prec_dir, _PREC_MULT)
        return rf"{e} \cdot {d}", _PREC_MULT
    if isinstance(no.op, ast.Div):
        # \frac{}{} já delimita visualmente — os dois lados renderizam
        # "soltos" (sem herdar parênteses de precedência), a fração inteira
        # sempre vira um átomo pra quem a envolve (ex: base de potência).
        return rf"\frac{{{esquerda}}}{{{direita}}}", _PREC_ATOMO
    if isinstance(no.op, ast.Mod):
        e = _envolver_se_preciso(esquerda, prec_esq, _PREC_MULT)
        d = _envolver_se_preciso(direita, prec_dir, _PREC_MULT, forcar_no_empate=True)
        return rf"{e} \bmod {d}", _PREC_MULT
    if isinstance(no.op, ast.Pow):
        # Base precisa de parênteses se não for um átomo (ex: "(a+b)^2"),
        # inclusive no empate ("(a^b)^c" != "a^(b^c)", potência a
        # direita-associativa — só o EXPOENTE dispensa parênteses de novo).
        # Divisão é caso à parte: vira \frac{}{} (prec_esq já ATOMO, não
        # cairia no empate acima), mas "\frac{a}{b}^{c}" sem parênteses
        # fica visualmente ambíguo (o expoente parece grudado só no "b")
        # mesmo sendo matematicamente um átomo — força parênteses aqui.
        eh_divisao = isinstance(no.left, ast.BinOp) and isinstance(no.left.op, ast.Div)
        if eh_divisao:
            base = rf"\left({esquerda}\right)"
        else:
            base = _envolver_se_preciso(esquerda, prec_esq, _PREC_POTENCIA, forcar_no_empate=True)
        return f"{base}^{{{direita}}}", _PREC_POTENCIA
    # Operador binário fora da gramática de motor_modelos.py (não deveria
    # acontecer — "^" já virou Pow antes de chegar aqui) — mesmo fallback
    # gracioso do átomo genérico acima.
    return rf"\mathrm{{{_escapar_identificador(ast.unparse(no))}}}", _PREC_ATOMO


def _renderizar_unaryop(no: ast.UnaryOp) -> "tuple[str, int]":
    operando, prec_operando = _renderizar_no(no.operand)
    envolvido = _envolver_se_preciso(operando, prec_operando, _PREC_UNARIO)
    if isinstance(no.op, ast.USub):
        return f"-{envolvido}", _PREC_UNARIO
    if isinstance(no.op, ast.UAdd):
        return f"+{envolvido}", _PREC_UNARIO
    return rf"\mathrm{{{_escapar_identificador(ast.unparse(no))}}}", _PREC_ATOMO


def _renderizar_chamada(no: ast.Call) -> str:
    if not isinstance(no.func, ast.Name):
        # Chamada "indireta" (ex: resultado de outra expressão chamado
        # como função) — fora da gramática de motor_modelos.py, mas não
        # trava o preview por causa disso.
        return rf"\mathrm{{{_escapar_identificador(ast.unparse(no))}}}"

    nome = no.func.id
    argumentos = [_renderizar_no(arg)[0] for arg in no.args]
    argumentos_str = ", ".join(argumentos)

    if nome in _FUNCOES_SQRT:
        return rf"\sqrt{{{argumentos_str}}}"
    if nome in _FUNCOES_ABS:
        return rf"\left|{argumentos_str}\right|"
    if nome in _FUNCOES_LOG_BASE:
        return rf"\log_{{{_FUNCOES_LOG_BASE[nome]}}}\left({argumentos_str}\right)"
    if nome in _FUNCOES_UPRIGHT:
        return rf"{_FUNCOES_UPRIGHT[nome]}\left({argumentos_str}\right)"
    # Nome de função desconhecido (não cadastrado em _FUNCOES_PERMITIDAS —
    # ia virar erro na hora de AVALIAR, mas o preview ainda desenha algo
    # razoável: nome upright + argumentos entre parênteses, mesmo padrão
    # visual das funções conhecidas).
    return rf"\mathrm{{{_escapar_identificador(nome)}}}\left({argumentos_str}\right)"


def equacao_para_mathtext(equacao: str) -> "str | None":
    """Converte o texto de uma equação (ver DICA_EQUACAO em
    app/screens/modelos.py) pra uma string mathtext pronta pro matplotlib
    desenhar (já entre "$...$" — ver matplotlib.mathtext.math_to_image).
    "f(x) = expressão" vira "f(x) = <expressão renderizada>"; sem "=", só a
    expressão. Devolve None se o texto estiver vazio ou não for uma
    expressão Python válida depois de trocar "^" por "**" (equação
    incompleta/inválida no meio da digitação — quem chama mantém o último
    preview válido em vez de piscar algo quebrado a cada tecla)."""
    texto = (equacao or "").strip()
    if not texto:
        return None

    if "=" in texto:
        lado_esquerdo, lado_direito = texto.split("=", 1)
    else:
        lado_esquerdo, lado_direito = None, texto

    lado_direito = lado_direito.strip().replace("^", "**")
    if not lado_direito:
        return None

    try:
        arvore = ast.parse(lado_direito, mode="eval")
        latex_direito, _ = _renderizar_no(arvore.body)
    except (SyntaxError, ValueError):
        return None

    if lado_esquerdo is not None and lado_esquerdo.strip():
        # Lado esquerdo (ex: "f(x)", "f(x1, x2)") é só um rótulo visual —
        # tentar convertê-lo pela mesma AST se possível (fica com o mesmo
        # estilo \mathrm das variáveis), mas sem travar o preview se não
        # for uma expressão Python válida sozinha (ex: "f(x, y) " com
        # espaço estranho) — nesse caso cai pro texto cru envolto em
        # \mathrm{}.
        bruto_esquerdo = lado_esquerdo.strip()
        try:
            arvore_esquerda = ast.parse(bruto_esquerdo.replace("^", "**"), mode="eval")
            latex_esquerdo, _ = _renderizar_no(arvore_esquerda.body)
        except (SyntaxError, ValueError):
            latex_esquerdo = rf"\mathrm{{{_escapar_identificador(bruto_esquerdo)}}}"
        return f"${latex_esquerdo} = {latex_direito}$"

    return f"${latex_direito}$"
