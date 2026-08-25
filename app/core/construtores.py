# -*- coding: utf-8 -*-
"""
Execução dos "construtores de variáveis" salvos — grafos (nós + ligações)
montados na tela Construtor de Variáveis (app/screens/construtor_variaveis.py)
e guardados em `construtores_variaveis`. Fica num módulo à parte da tela pra
poder rodar tanto sob clique do usuário (Salvar/Prévia) quanto automaticamente
depois de qualquer nova geração da tabela de simulação (ver
app/screens/simulacao.py:gerar), sem a tela do Construtor precisar estar
aberta — sem isso, as colunas que um construtor gera sumiriam a cada "Gerar
simulação" (simulacao_talhao_idade é recriada do zero, DROP + CREATE, a cada
execução).
"""
import json
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import gamma as _funcao_gamma

from . import motor_modelos, simulacao
from .numerico import converter_numero

TABELA_CONSTRUTORES = "construtores_variaveis"


# ==========================================================
# CRUD dos construtores salvos
# ==========================================================

def listar_construtores(conn: sqlite3.Connection) -> List[Dict]:
    linhas = conn.execute(
        f'SELECT id, nome, tabela_origem, grafo, ativo FROM "{TABELA_CONSTRUTORES}" ORDER BY nome'
    ).fetchall()
    return [_desserializar_linha(linha) for linha in linhas]


def obter_construtor(conn: sqlite3.Connection, construtor_id: int) -> Dict:
    linha = conn.execute(
        f'SELECT id, nome, tabela_origem, grafo, ativo FROM "{TABELA_CONSTRUTORES}" WHERE id = ?',
        (construtor_id,),
    ).fetchone()
    if linha is None:
        raise ValueError("Construtor não encontrado (pode ter sido excluído).")
    return _desserializar_linha(linha)


def _desserializar_linha(linha) -> Dict:
    id_, nome, tabela_origem, grafo_json, ativo = linha
    grafo = json.loads(grafo_json)
    grafo["nos"] = {int(k): v for k, v in grafo["nos"].items()}
    return {"id": id_, "nome": nome, "tabela_origem": tabela_origem, "grafo": grafo, "ativo": bool(ativo)}


def definir_ativo(conn: sqlite3.Connection, construtor_id: int, ativo: bool) -> None:
    """Liga/desliga a reaplicação automática desse construtor (ver
    aplicar_construtores_salvos) — continua salvo, só para de rodar sozinho
    a cada geração da tabela de origem até ser reativado."""
    conn.execute(
        f'UPDATE "{TABELA_CONSTRUTORES}" SET ativo = ? WHERE id = ?', (1 if ativo else 0, construtor_id)
    )
    conn.commit()


def salvar_construtor(
    conn: sqlite3.Connection, nome: str, tabela_origem: str, grafo: dict, construtor_id=None
) -> int:
    """`grafo` = {"nos": {no_id: {...}}, "conexoes": [...]}. Salva (insere ou
    atualiza, conforme `construtor_id`) e retorna o id."""
    grafo_json = json.dumps(grafo, ensure_ascii=False)
    if construtor_id is None:
        cursor = conn.execute(
            f'INSERT INTO "{TABELA_CONSTRUTORES}" (nome, tabela_origem, grafo) VALUES (?, ?, ?)',
            (nome, tabela_origem, grafo_json),
        )
        novo_id = cursor.lastrowid
    else:
        conn.execute(
            f'UPDATE "{TABELA_CONSTRUTORES}" SET nome=?, tabela_origem=?, grafo=?, '
            "atualizado_em=datetime('now','localtime') WHERE id=?",
            (nome, tabela_origem, grafo_json, construtor_id),
        )
        novo_id = construtor_id
    conn.commit()
    return novo_id


def excluir_construtor(conn: sqlite3.Connection, construtor_id: int) -> None:
    conn.execute(f'DELETE FROM "{TABELA_CONSTRUTORES}" WHERE id = ?', (construtor_id,))
    conn.commit()


# ==========================================================
# AVALIAÇÃO DO GRAFO
# ==========================================================

# Só reporta o tempo de um construtor em aplicar_construtores_salvos (ver
# essa função) se ele passar disso — evita poluir o resumo de "Gerar
# simulação" com construtores rápidos, que não são o problema.
_LIMIAR_TEMPO_RELATADO = 1.0

_TIPOS_COM_DEPENDENCIA = (
    "modelo", "saida", "calculo", "distribuicao", "acumulado", "receita_sortimento",
    "rendimento_sortimento", "vpl_sortimento", "vet_sortimento", "afilamento",
    "recuperacao_weibull", "custo_colheita", "custo_formacao",
)

def _somar_com_vazio_zero(a, b):
    """"+" tolerante a NaN: uma linha vazia de UM lado (ex: um nó
    "Saída"/financeiro mascarado fora do seu evento configurado, ver
    _mascara_eventos_no) conta como 0 em vez de derrubar a soma inteira
    em NaN — só fica NaN se os DOIS lados estiverem vazios na mesma
    linha (nada pra somar mesmo). Sem isso, encadear várias contas
    parciais (ex: "CCF TOTAL" = CCF Colheita + CCF Baldeio + CCF Colheita
    Raleio, cada uma só preenchida no SEU evento) só dava valor nas
    linhas em que TODAS calhassem de estar preenchidas ao mesmo tempo —
    quase nunca, já que cada uma vale num evento diferente. `Series.add`
    com `fill_value` já tem exatamente essa semântica (0 de um lado só,
    NaN se os dois) — só cai pra "+" comum quando um dos dois é escalar
    (a constante digitada de um passo de "Cálculo", nunca "vazia" nesse
    sentido)."""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return a.add(b, fill_value=0.0)
    return a + b


def _subtrair_com_vazio_zero(a, b):
    """Mesma ideia de _somar_com_vazio_zero, pra "-"."""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return a.sub(b, fill_value=0.0)
    return a - b


_OPERADORES = {
    "+": _somar_com_vazio_zero,
    "-": _subtrair_com_vazio_zero,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "^": lambda a, b: a ** b,
    # Mescla (não é aritmético) — usa o valor de `a`, e só cai pro de `b`
    # onde `a` for NaN. Pensado pra juntar, na mesma coluna por classe,
    # duas saídas de nós financeiros (receita/rendimento/vpl/vet) que
    # nunca têm valor na mesma linha ao mesmo tempo por causa da
    # configuração de eventos de cada um (ver _mascara_eventos_no) — ex:
    # um VET configurado só pra "Corte Raso" + outro só pra "Raleio"/"1º
    # Desbaste"/"2º Desbaste", ligados nas 2 entradas de uma "Saída" com
    # esse operador na 2ª, dão um "vet_classe" só, com o valor certo em
    # cada idade. Se as duas entradas tiverem valor na mesma linha (config
    # com eventos sobrepostos), o valor de `a` (a mais à esquerda) vence,
    # sem avisar — não é o caso de uso pensado, mas não trava o grafo.
    "ou": lambda a, b: a.where(a.notna(), b),
}

# Passos de redução do nó "Cálculo" (ver _aplicar_passo_calculo): ao
# contrário dos aritméticos (+-*/^ com uma constante digitada), estes não
# têm "valor" — colapsam um dict {classe: Series} (resultado por classe
# diamétrica, ex: VTCC de cada classe numa coluna) numa Series só, somando/
# tirando a média das classes LINHA A LINHA (ex: VTCC total da linha,
# somando as classes 1 a 100 de cada árvore/talhão).
_ROTULO_REDUCAO_CLASSE = {
    "soma_classes": "Somar todas as classes",
    "media_classes": "Média de todas as classes",
}


def _combinar_entradas_saida(entradas, valores_entrada, classe, rotulo, erros):
    """Combina os valores já resolvidos das entradas de um nó "saida" (ver
    ramo "saida" de avaliar_grafo), aplicando 1/x e o operador de cada
    entrada em sequência. `classe`: se não for None, extrai o valor
    daquela classe de qualquer entrada que seja um dict por classe
    diamétrica (as demais, Series única, valem igual em toda classe) — é
    o que permite encadear a saída de um "modelo" por classe numa Saída
    pra continuar a conta. Devolve (valor, ok); ok=False já deixou a
    mensagem em `erros`."""
    acumulador = None
    for i, entrada in enumerate(entradas):
        valor_bruto = valores_entrada[i]
        v = valor_bruto[classe] if (classe is not None and isinstance(valor_bruto, dict)) else valor_bruto
        if entrada.get("inverso"):
            v = 1.0 / v
        if i == 0:
            acumulador = v
            continue
        operador = entrada.get("operador", "+")
        try:
            acumulador = _OPERADORES[operador](acumulador, v)
        except ZeroDivisionError:
            acumulador = float("nan")
        except KeyError:
            erros.append(f"\"{rotulo}\": operador desconhecido \"{operador}\"")
            return None, False
    return acumulador, True


def _aplicar_passo_calculo(valor, passo, rotulo, erros):
    """Aplica um único passo de um nó "calculo" sobre `valor`, que pode ser
    uma Series (um valor por linha) ou um dict {classe: Series} (resultado
    por classe diamétrica, vindo de um Modelo/Distribuição por classe).
    Devolve (novo_valor, ok); ok=False já deixou a mensagem em `erros`.

    Um passo de redução ("soma_classes"/"media_classes", ver
    _ROTULO_REDUCAO_CLASSE) exige `valor` como dict e devolve sempre uma
    Series só (colapsa a dimensão de classe) — depois dele, os passos
    seguintes (se houver) já operam sobre essa Series normalmente. Um passo
    aritmético comum (operador +-*/^ com "valor" constante) preserva o
    formato de entrada: aplica a mesma conta em cada classe separadamente
    se `valor` ainda for um dict, ou direto se já for uma Series só."""
    operador = passo.get("operador", "+")

    if operador in _ROTULO_REDUCAO_CLASSE:
        if not isinstance(valor, dict):
            erros.append(
                f"\"{rotulo}\": \"{_ROTULO_REDUCAO_CLASSE[operador]}\" só funciona sobre uma "
                "entrada por classe diamétrica (vinda de um Modelo/Distribuição ligado em Classe "
                "Diamétrica)")
            return None, False
        combinado = pd.concat(list(valor.values()), axis=1)
        resultado = combinado.sum(axis=1) if operador == "soma_classes" else combinado.mean(axis=1)
        return resultado, True

    if isinstance(valor, dict):
        novo = {}
        for classe, serie in valor.items():
            resultado, ok = _aplicar_operador_constante(serie, operador, passo.get("valor"), rotulo, erros)
            if not ok:
                return None, False
            novo[classe] = resultado
        return novo, True
    return _aplicar_operador_constante(valor, operador, passo.get("valor"), rotulo, erros)


def _aplicar_operador_constante(valor, operador, constante, rotulo, erros):
    try:
        resultado = _OPERADORES[operador](valor, constante)
    except ZeroDivisionError:
        resultado = float("nan")
    except KeyError:
        erros.append(f"\"{rotulo}\": operador desconhecido \"{operador}\"")
        return None, False
    return resultado, True


def _aplicar_passos_calculo(valor, passos, rotulo, erros):
    """Aplica os passos de um nó "calculo" em sequência sobre `valor` (ver
    ramo "calculo" de avaliar_grafo e _aplicar_passo_calculo pro que cada
    passo faz). Devolve (valor, ok); ok=False já deixou a mensagem em
    `erros`."""
    for passo in passos:
        valor, ok = _aplicar_passo_calculo(valor, passo, rotulo, erros)
        if not ok:
            return None, False
    return valor, True


def _acumular_por_grupo(valor: pd.Series, grupo: pd.Series, ordem: pd.Series) -> pd.Series:
    """Soma acumulada de `valor` dentro de cada grupo (ex: talhão),
    respeitando a ordem de `ordem` (ex: idade simulada) — NÃO a ordem das
    linhas na tabela. Usado pelo nó "acumulado" (ver ramo correspondente
    em avaliar_grafo) pra somar, por ex., todo VTCC removido nos eventos
    de manejo anteriores do mesmo talhão (volume líquido = VTCC em pé +
    esse acumulado).

    NaN em `valor` conta como 0 na soma — uma linha sem evento de manejo
    não reseta nem interrompe o acumulado, só não contribui nada a mais
    (é o que faz o valor "carregar" sem mudar entre eventos). Linhas com
    grupo ou ordem indefinidos (NaN) ficam de fora da conta e saem como
    NaN no resultado — não tem como saber a que grupo/posição pertencem."""
    valido = grupo.notna() & ordem.notna()
    resultado = pd.Series(np.nan, index=valor.index)
    if not valido.any():
        return resultado

    base = pd.DataFrame({"valor": valor.fillna(0.0), "grupo": grupo, "ordem": ordem})
    base_valida = base[valido].sort_values("ordem", kind="stable")
    acumulado = base_valida.groupby("grupo")["valor"].cumsum()
    resultado.loc[acumulado.index] = acumulado
    return resultado


def _preco_sortimento_da_classe(sortimentos: List[Tuple], classe: float, tipo_preco: str = "serrada"):
    """Preço (tela Configurações) do sortimento cujo [limite_inferior,
    limite_superior] cobre `classe` (bordas inclusivas; None num limite =
    sem limite naquele lado — mesma regra de
    simulacao.calcular_volume_por_sortimento). `sortimentos` já vem
    ordenado por limite_inferior; a 1ª faixa que bater vale. Devolve None
    se nenhuma faixa cobrir a classe, ou se o sortimento que bateu não tem
    o preço pedido cadastrado. `sortimentos`: lista de tuplas (nome,
    limite_inferior, limite_superior, rendimento, preco_serrada, preco_pe)
    — ver consulta em aplicar_construtores_salvos /
    construtor_variaveis.py:_avaliar_grafo. `tipo_preco`: "serrada"
    (padrão — Madeira Serrada, R$/m³ de produto já desdobrado) ou "pe"
    (Madeira em Pé, R$/m³ da árvore em pé antes da colheita/desdobro) —
    escolhido no nó "receita_sortimento" (botão direito, ver
    construtor_variaveis.py:_montar_menu_no)."""
    for _nome, limite_inferior, limite_superior, _rendimento, preco_serrada, preco_pe in sortimentos:
        if (limite_inferior is None or classe >= limite_inferior) and (
                limite_superior is None or classe <= limite_superior):
            return preco_pe if tipo_preco == "pe" else preco_serrada
    return None


def _rendimento_sortimento_da_classe(sortimentos: List[Tuple], classe: float):
    """Rendimento (tela Configurações, percentual — ex: 30 cadastrado quer
    dizer 30%) do sortimento cujo [limite_inferior, limite_superior] cobre
    `classe`, já convertido pra fração (30 -> 0.3) — mesma regra de
    casamento de faixa que _preco_sortimento_da_classe (mesmo `sortimentos`,
    mesma ordem de tupla). Devolve None se nenhuma faixa cobrir a classe, ou
    se o sortimento que bateu não tem rendimento cadastrado."""
    for _nome, limite_inferior, limite_superior, rendimento, _preco_serrada, _preco_pe in sortimentos:
        if (limite_inferior is None or classe >= limite_inferior) and (
                limite_superior is None or classe <= limite_superior):
            return rendimento / 100.0 if rendimento is not None else None
    return None


def _custo_efetivo_colheita_da_classe(custo: dict, classe: float) -> Optional[float]:
    """Custo efetivo (R$/m³) de UM custo de colheita (linha de
    `obter_custos_colheita`, com a produtividade por classe já aninhada) numa
    classe diamétrica específica — mesma fórmula clássica de custo de
    colheita florestal usada em app/screens/configuracoes.py:
    _custo_efetivo_colheita: Custo Hora Máquina / (Produtividade da classe ×
    Disponibilidade Mecânica × Eficiência Operacional). Disponibilidade/
    eficiência são cadastradas em % (ex: 85 -> 85%), divididas por 100 aqui
    antes de entrar na conta. Devolve None se a classe não tiver
    produtividade cadastrada, se faltar custo_hora_maquina/disponibilidade/
    eficiência, ou se o denominador der zero."""
    produtividade = custo["produtividade"].get(classe)
    custo_hora_maquina = custo["custo_hora_maquina"]
    disponibilidade = custo["disponibilidade_mecanica"]
    eficiencia = custo["eficiencia_operacional"]
    if None in (produtividade, custo_hora_maquina, disponibilidade, eficiencia):
        return None
    denominador = produtividade * (disponibilidade / 100.0) * (eficiencia / 100.0)
    if denominador == 0:
        return None
    return custo_hora_maquina / denominador


def _mascarar_sem_evento_manejo(resultado, mascara_evento_valido):
    """Zera pra NaN as linhas fora de `mascara_evento_valido` (ver
    _mascara_eventos_no) — usado por "receita_sortimento"/
    "rendimento_sortimento"/"vpl_sortimento"/"vet_sortimento" pra não gerar
    valor em idades fora dos eventos configurados nesse nó. `resultado`
    pode ser um dict {classe: Series} (entrada por classe diamétrica,
    caso comum) ou uma Series só (VPL/VET aceitam "rt"/"vpl" tanto por
    classe quanto já agregado — ver ramos correspondentes); os dois
    formatos são mascarados igual. `mascara_evento_valido` None (tabela
    sem "evento_manejo") deixa passar tudo, sem mascarar nada."""
    return _aplicar_mascara_valor(resultado, mascara_evento_valido)


# Eventos que um nó financeiro (receita_sortimento/rendimento_sortimento/
# vpl_sortimento/vet_sortimento) pode restringir via "eventos_manejo" (ver
# _mascara_eventos_no) — mesmos 4 valores que evento_manejo assume em
# simulacao_talhao_idade (ver simulacao.EVENTO_*).
EVENTOS_MANEJO_CONFIGURAVEIS = (
    simulacao.EVENTO_RALEIO, simulacao.EVENTO_DESBASTE_1, simulacao.EVENTO_DESBASTE_2,
    simulacao.EVENTO_CORTE_RASO,
)

# Subconjunto de EVENTOS_MANEJO_CONFIGURAVEIS que tem uma intensidade de
# verdade (fração de remoção, coluna "intensidade_evento" — ver
# simulacao.gerar_populacao) — Corte Raso fica de fora: é sempre colheita
# total, sem percentual, e "intensidade_evento" já vem NaN nessas linhas
# por definição (não está no dict `intensidades_evento` de lá), então não
# faria sentido exigir ">0" numa coluna que é sempre vazia ali.
_EVENTOS_COM_INTENSIDADE = (
    simulacao.EVENTO_RALEIO, simulacao.EVENTO_DESBASTE_1, simulacao.EVENTO_DESBASTE_2,
)


def _mascara_eventos_no(no: dict, evento: Optional[pd.Series], intensidade: Optional[pd.Series]):
    """Máscara de evento_manejo válido pra ESTE nó — quais linhas
    (idades) ele calcula. `no.get("eventos_manejo")`: lista de eventos
    escolhidos pelo usuário (subconjunto de EVENTOS_MANEJO_CONFIGURAVEIS,
    configurado por botão direito no nó — ver
    construtor_variaveis.py:_configurar_eventos_no). Lista vazia/ausente
    (padrão — também cobre grafos salvos antes dessa opção existir) cai na
    regra antiga: qualquer evento preenchido (não nulo/não vazio) vale.
    `evento` None (tabela sem "evento_manejo") sempre deixa passar tudo,
    sem mascarar nada — não há o que restringir.

    Além do evento em si, uma linha de Raleio/1º Desbaste/2º Desbaste só
    vale se `intensidade` (coluna "intensidade_evento") for maior que
    zero ali — idade marcada com o evento mas intensidade 0% (cenário
    configurado assim, ou a guarda de fustes/ha mínimo pulou o manejo
    nesse talhão) não é uma colheita de verdade, então não gera receita/
    rendimento/VPL/VET, mesmo com o evento certo selecionado. Corte Raso
    nunca entra nessa exigência (ver _EVENTOS_COM_INTENSIDADE). `intensidade`
    None (tabela sem "intensidade_evento") não restringe nada além do
    evento em si."""
    if evento is None:
        return None
    eventos_escolhidos = no.get("eventos_manejo")
    if eventos_escolhidos:
        mascara = evento.isin(eventos_escolhidos)
    else:
        mascara = evento.notna() & (evento.astype(str).str.strip() != "")

    if intensidade is not None:
        exige_intensidade = evento.isin(_EVENTOS_COM_INTENSIDADE)
        intensidade_valida = intensidade.fillna(0) > 0
        mascara = mascara & (~exige_intensidade | intensidade_valida)

    return mascara


def _variantes_do_no(no: dict) -> List[Dict]:
    """Lista de variantes (uma por estrato) de um nó "modelo". Normaliza
    tanto o formato atual — chave "variantes", usado quando o Construtor
    agrupa num nó só todas as linhas de Modelos que têm o mesmo nome (ver
    app/screens/construtor_variaveis.py:_atualizar_modelos_disponiveis) —
    quanto o formato salvo antes desse agrupamento existir, com
    equacao/coeficientes/estrato_coluna/estrato soltos direto no nó (uma
    variante só)."""
    variantes = no.get("variantes")
    if variantes is not None:
        return variantes
    return [{
        "estrato_coluna": no.get("estrato_coluna"),
        "estrato": no.get("estrato"),
        "equacao": no.get("equacao", ""),
        "coeficientes": no.get("coeficientes", {}),
    }]


def _mascaras_variantes(df: pd.DataFrame, variantes: List[Dict], rotulo: str, erros: List[str]) -> List[Tuple[Dict, object]]:
    """Resolve a máscara (Series booleana, ou None pra variante "Todos"
    sem estrato) de cada variante — devolve só as pares (variante,
    máscara) válidos; uma variante cuja coluna do estrato não existe na
    base já entra em `erros` e é descartada daqui."""
    resolvidas = []
    for variante in variantes:
        estrato_coluna = variante.get("estrato_coluna")
        if not estrato_coluna:
            resolvidas.append((variante, None))
            continue
        if estrato_coluna not in df.columns:
            erros.append(
                f"\"{rotulo}\": coluna do estrato \"{estrato_coluna}\" não existe na base")
            continue
        mascara = df[estrato_coluna].astype(str) == str(variante.get("estrato"))
        resolvidas.append((variante, mascara))
    return resolvidas


def _resolver_valor_saida(no_origem: Optional[dict], valor_no, saida_idx: int):
    """Um nó comum tem uma saída só — `valor_no` (o que já está em
    `valores[no_id]`) já É o valor a usar, passthrough. Os nós "afilamento"
    e "recuperacao_weibull" (ver ramos abaixo) têm duas — um dict com
    chaves nomeadas ("aproveitavel"/"biomassa", ou "forma"/"escala") — e
    `saida_idx` (vindo de conexao["saida_idx"], o pino de onde o fio foi
    puxado no Construtor de Variáveis) escolhe qual delas. Devolve None se
    a saída pedida não existir (nó ainda não calculado, ou formato
    inesperado) — quem chama trata como "entrada não ligada"."""
    if no_origem is not None and no_origem.get("tipo") == "afilamento":
        if not isinstance(valor_no, dict):
            return None
        return valor_no.get("biomassa" if saida_idx == 1 else "aproveitavel")
    if no_origem is not None and no_origem.get("tipo") == "recuperacao_weibull":
        if not isinstance(valor_no, dict):
            return None
        return valor_no.get("escala" if saida_idx == 1 else "forma")
    return valor_no


def _aplicar_mascara_valor(valor, mascara):
    """Aplica `mascara` (bool Series, True = mantém o valor) a `valor` —
    Series, dict {classe: Series} (saída por classe), ou dict aninhado
    (afilamento: {"aproveitavel": {classe: Series}, "biomassa": {...}}).
    `mascara` None ou `valor` None devolve `valor` sem tocar (passthrough)
    — usado só quando existe um nó "custo_formacao" com
    excluir_outras_contas=True no grafo (ver avaliar_grafo)."""
    if mascara is None or valor is None:
        return valor
    if isinstance(valor, dict):
        return {chave: _aplicar_mascara_valor(v, mascara) for chave, v in valor.items()}
    if isinstance(valor, pd.Series):
        return valor.where(mascara)
    return valor


# ==========================================================
# RECUPERAÇÃO DE PARÂMETROS WEIBULL POR MOMENTOS (nó "recuperacao_weibull")
# ==========================================================
#
# Alternativa a regredir forma/escala cada um por si (Parameter Prediction
# Method) contra alguma variável de talhão: aqui forma/escala são
# RECUPERADOS casando os dois primeiros momentos da Weibull 2P com uma
# média e um CV já previstos/localizados por fora (ex: dap_med_atual e
# cv_dap_atual, ver app/simulacao.py) —
#     média = escala * Gamma(1 + 1/forma)
#     cv²   = Gamma(1 + 2/forma) / Gamma(1 + 1/forma)² - 1
# O CV não depende da escala (cancela na razão), só da forma — dá pra achar
# a forma por busca de raiz (função estritamente decrescente: forma baixa
# = distribuição mais espalhada/CV alto; forma alta = mais concentrada/CV
# baixo) e só depois a escala sai em forma fechada da média. Isso garante
# que a Weibull recuperada bate EXATAMENTE com a média prevista (ao
# contrário de duas regressões independentes, que não têm por que serem
# consistentes entre si).
#  forma mínima calibrada pra não estourar o Gamma (overflow de float64
# acima de ~171): 1+2/forma < 171 exige forma > ~0.0118 — 0.05 sobra de
# margem (Gamma(41) ~ 8e47, bem abaixo do limite) e já corresponde a um CV
# absurdamente alto pra qualquer distribuição diamétrica real.
_CV_FORMA_MINIMA = 0.05
_CV_FORMA_MAXIMA = 50.0


def _forma_a_partir_do_cv(cv: float) -> float:
    """Raiz de Gamma(1+2/forma)/Gamma(1+1/forma)² - 1 - cv² = 0 em forma —
    única no intervalo de busca porque essa função é estritamente
    decrescente em forma (ver comentário acima). Deixa propagar
    ValueError/RuntimeError do brentq se os dois extremos não trocarem de
    sinal (cv fora do que uma Weibull 2P consegue representar)."""
    def diferenca(forma):
        return (
            _funcao_gamma(1.0 + 2.0 / forma) / _funcao_gamma(1.0 + 1.0 / forma) ** 2 - 1.0 - cv ** 2
        )
    return float(brentq(
        diferenca, _CV_FORMA_MINIMA, _CV_FORMA_MAXIMA, xtol=1e-10, rtol=1e-10, maxiter=200))


def _recuperar_forma_escala(media: pd.Series, cv: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Vetoriza `_forma_a_partir_do_cv` sobre as linhas de `media`/`cv`,
    devolvendo (forma, escala) como pd.Series no mesmo índice. Linha com
    média/cv ausente, não-positivo, ou cujo cv não tem raiz de forma no
    intervalo de busca (`_forma_a_partir_do_cv` falhou) sai como NaN em vez
    de derrubar o nó inteiro — mesmo espírito de "distribuicao"/"modelo",
    que deixam buraco NaN em vez de exigir dado completo em toda linha.
    Cv iguais (comum: mesmo talhão/etapa repetido em várias idades)
    resolvem a raiz uma vez só e reaproveitam — evita repetir a busca por
    brentq (não é grátis) por nada em tabelas com muita linha repetida."""
    media_np = media.to_numpy(dtype=float)
    cv_np = cv.to_numpy(dtype=float)
    valido = np.isfinite(media_np) & np.isfinite(cv_np) & (media_np > 0) & (cv_np > 0)

    forma_np = np.full(media_np.shape, np.nan)
    formas_por_cv: Dict[float, Optional[float]] = {}
    for i in np.flatnonzero(valido):
        chave = round(float(cv_np[i]), 9)
        if chave not in formas_por_cv:
            try:
                formas_por_cv[chave] = _forma_a_partir_do_cv(chave)
            except (ValueError, RuntimeError):
                formas_por_cv[chave] = None
        forma_encontrada = formas_por_cv[chave]
        if forma_encontrada is not None:
            forma_np[i] = forma_encontrada

    escala_np = np.full(media_np.shape, np.nan)
    com_forma = np.isfinite(forma_np)
    escala_np[com_forma] = media_np[com_forma] / _funcao_gamma(1.0 + 1.0 / forma_np[com_forma])

    return pd.Series(forma_np, index=media.index), pd.Series(escala_np, index=media.index)


_PASSO_ALTURA_AFILAMENTO = 0.1


def _perfil_area_seccional(equacao, coeficientes, nomes_variaveis, dap, h_grid, H_grid):
    """Avalia a equação de afilamento (3 "Variáveis (x)" posicionais —
    DAP, h, H, ver ramo "afilamento" de avaliar_grafo) numa grade de
    alturas `h_grid` (mesmo shape de `H_grid`, broadcastável com `dap`
    escalar) e devolve `(area_seccional, diametro)`, ambos na mesma grade.
    Área em m² a partir de um diâmetro em cm — mesma conversão
    diâmetro²·π/40000 já documentada no nó "Cálculo" (DAP -> ² -> ×π ->
    ÷40000 vira área seccional). `np.errstate` silencia os avisos do numpy
    pra combinações inválidas fora do domínio da equação (ex: log de
    número negativo quando h > H) — essas entradas são descartadas depois
    pelas máscaras de validade, não usadas no resultado.

    Tentativa registrada e descartada: rodar essa grade em float32 (metade
    da banda de memória, potencialmente ~1,3-2x mais rápido) parecia segura
    à primeira vista, mas o teste de sanidade (cone com diâmetro caindo
    EXATO em cima do diâmetro mínimo configurado) pegou uma tora inteira
    sendo excluída indevidamente — o arredondamento de float32 empurrou a
    comparação ">= diâmetro mínimo" pro lado errado bem na borda. Como
    esse tipo de "quase empate" pode acontecer de verdade (equação real +
    diâmetro mínimo configurado coincidindo), o risco de mudar silenciosamente
    quanto volume vira tora não compensa o ganho de velocidade — fica em
    float64."""
    nome_dap, nome_h, nome_H = nomes_variaveis
    with np.errstate(all="ignore"):
        d_grid = motor_modelos.avaliar_expressao_array(
            equacao, coeficientes, {nome_dap: dap, nome_h: h_grid, nome_H: H_grid})
        g_grid = np.pi / 40000.0 * d_grid ** 2
    return g_grid, d_grid


def _calcular_volumes_afilamento(
    equacao, coeficientes, nomes_variaveis, dap: float, serie_H: pd.Series,
    comprimento_tora: float, diametro_minimo_tora: float,
) -> Tuple[pd.Series, pd.Series]:
    """Volumes (Smalian) pra uma classe diamétrica (`dap` fixo) e uma
    pd.Series de Ht (`serie_H`, uma por linha) — ver "Núcleo numérico" no
    plano do nó "afilamento". Devolve (serie_aproveitavel, serie_biomassa),
    indexadas igual `serie_H`; linha com H desconhecido (NaN) sai NaN nas
    duas.

    Volume total do fuste: grade fina (passo `_PASSO_ALTURA_AFILAMENTO`,
    0,1 m) de 0 até Ht, seções somadas por Smalian. Volume aproveitável:
    grade grossa própria, em múltiplos de `comprimento_tora` (avaliada
    direto pela equação nesses pontos exatos, não reaproveita a grade fina
    — evita erro de arredondamento entre os dois passos), soma só as toras
    INTEIRAS (altura cabe e diâmetro na ponta fina >= mínimo),
    sequencialmente a partir da base — para no primeiro corte que não
    fechar (`np.cumprod` da validade ao longo das toras)."""
    index = serie_H.index
    H = serie_H.to_numpy(dtype=float)
    n_linhas = len(H)
    H_valido = ~np.isnan(H)
    h_maximo = float(np.nanmax(H)) if H_valido.any() else 0.0

    n_passos = max(int(np.ceil(h_maximo / _PASSO_ALTURA_AFILAMENTO)), 1)
    j = np.arange(n_passos + 1).reshape(-1, 1)
    h_fino = j * _PASSO_ALTURA_AFILAMENTO
    H_fino = np.broadcast_to(H, (n_passos + 1, n_linhas))
    g_fino, _ = _perfil_area_seccional(equacao, coeficientes, nomes_variaveis, dap, h_fino, H_fino)
    valido_fino = h_fino <= H_fino
    v_secao = np.where(
        valido_fino[1:], (g_fino[:-1] + g_fino[1:]) / 2.0 * _PASSO_ALTURA_AFILAMENTO, 0.0)
    volume_total = np.nansum(v_secao, axis=0)

    n_toras = max(int(np.ceil(h_maximo / comprimento_tora)), 1)
    k = np.arange(n_toras + 1).reshape(-1, 1)
    h_grosso = k * comprimento_tora
    H_grosso = np.broadcast_to(H, (n_toras + 1, n_linhas))
    g_grosso, d_grosso = _perfil_area_seccional(
        equacao, coeficientes, nomes_variaveis, dap, h_grosso, H_grosso)
    cabe_altura = h_grosso[1:] <= H_grosso[1:]
    with np.errstate(invalid="ignore"):
        diametro_ok = d_grosso[1:] >= diametro_minimo_tora
    valido_acumulado = np.cumprod(cabe_altura & diametro_ok, axis=0).astype(bool)
    v_tora = np.where(
        valido_acumulado, (g_grosso[:-1] + g_grosso[1:]) / 2.0 * comprimento_tora, 0.0)
    volume_aproveitavel = np.nansum(v_tora, axis=0)
    volume_aproveitavel = np.minimum(volume_aproveitavel, volume_total)

    volume_total = np.where(H_valido, volume_total, np.nan)
    volume_aproveitavel = np.where(H_valido, volume_aproveitavel, np.nan)
    volume_biomassa = volume_total - volume_aproveitavel

    return (
        pd.Series(volume_aproveitavel, index=index),
        pd.Series(volume_biomassa, index=index),
    )


# Altura máxima coberta pela tabela pré-calculada de afilamento (ver
# _obter_tabela_afilamento) — bem acima de qualquer altura real esperada
# (eucalipto/pinus raramente passam de 45-50m), folga generosa sem custo
# perceptível (a tabela é ~800 pontos, calculada uma vez por classe
# diamétrica).
_ALTURA_MAXIMA_TABELA_AFILAMENTO = 80.0

# {chave -> (tabela_aproveitavel, tabela_biomassa)} — ver _obter_tabela_
# afilamento. Cache module-level: sobrevive entre chamadas de avaliar_grafo
# (inclusive entre cenários de um lote de "Múltiplos cenários", já que
# todos rodam no mesmo processo/thread) — é isso que faz a tabela valer a
# pena: calculada 1x por classe diamétrica, reaproveitada por todo
# cenário/linha que passar por aquela classe dali em diante. A chave
# inclui TODOS os parâmetros que definem a curva (equação, coeficientes,
# variáveis, DAP, comprimento/diâmetro mínimo de tora) — qualquer mudança
# neles (ex: editar um coeficiente em Modelos) gera uma chave nova
# automaticamente, nunca serve uma tabela desatualizada.
_CACHE_TABELAS_AFILAMENTO: Dict[tuple, tuple] = {}


def instalar_cache_afilamento(cache: Optional[Dict[tuple, tuple]]) -> None:
    """Instala tabelas já calculadas no processo atual.

    Usado pelo initializer dos workers no Windows, onde ``spawn`` não
    herda o cache do processo coordenador. ``update`` preserva entradas
    locais válidas e a própria chave completa garante invalidação por
    conteúdo, sem depender do nome ou da posição de nenhum nó.
    """
    if cache:
        _CACHE_TABELAS_AFILAMENTO.update(cache)


def preaquecer_cache_afilamento(
    grafos: List[dict], classes_diametricas, dimensoes_tora: Optional[dict],
) -> Dict[tuple, tuple]:
    """Precalcula tabelas exigidas por quaisquer nós de afilamento.

    Não interpreta a topologia nem nomes definidos pelo usuário: percorre
    todos os nós desse tipo e todas as variantes cadastradas. Se a opção
    de tabela estiver desligada, devolve vazio e mantém o cálculo exato.
    """
    if not dimensoes_tora or not dimensoes_tora.get("usar_tabela_afilamento"):
        return {}
    comprimento = dimensoes_tora.get("comprimento_tora")
    diametro_minimo = dimensoes_tora.get("diametro_minimo_tora")
    if comprimento is None or diametro_minimo is None or float(comprimento) <= 0:
        return {}
    if classes_diametricas is None:
        return {}

    cache_lote = {}
    for grafo in grafos:
        for no in grafo.get("nos", {}).values():
            if no.get("tipo") != "afilamento":
                continue
            nomes_variaveis = no.get("variaveis", [])
            if len(nomes_variaveis) != 3:
                continue
            for variante in _variantes_do_no(no):
                for classe in classes_diametricas:
                    tabela = _obter_tabela_afilamento(
                        variante["equacao"], variante["coeficientes"], nomes_variaveis,
                        float(classe), float(comprimento), float(diametro_minimo))
                    chave = _chave_tabela_afilamento(
                        variante["equacao"], variante["coeficientes"], nomes_variaveis,
                        float(classe), float(comprimento), float(diametro_minimo))
                    cache_lote[chave] = tabela
    return cache_lote


def _chave_tabela_afilamento(
    equacao, coeficientes: dict, nomes_variaveis: list, dap: float,
    comprimento_tora: float, diametro_minimo_tora: float,
) -> tuple:
    return (
        equacao, tuple(sorted(coeficientes.items())), tuple(nomes_variaveis), float(dap),
        float(comprimento_tora), float(diametro_minimo_tora),
    )


def _obter_tabela_afilamento(
    equacao, coeficientes: dict, nomes_variaveis: list, dap: float,
    comprimento_tora: float, diametro_minimo_tora: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Tabela pré-calculada de (volume_aproveitavel, volume_biomassa) por
    altura candidata (0; 0,1; 0,2; ...; _ALTURA_MAXIMA_TABELA_AFILAMENTO),
    pra uso do nó "afilamento" quando "usar_tabela_afilamento" (tela
    Configurações, ver obter_dimensoes_tora) está ligado — ver
    _calcular_volumes_afilamento_tabela, que faz a busca.

    Calculada chamando _calcular_volumes_afilamento normalmente, só que
    com `serie_H` = a grade de alturas CANDIDATAS em vez de alturas reais
    de árvore — mesmíssima conta de sempre, então cada candidato sai
    exatamente como sairia se fosse uma árvore de verdade com aquela
    altura. A aproximação entra depois, na busca (app/core/construtores.py:
    _calcular_volumes_afilamento_tabela): a altura REAL de cada árvore é
    arredondada pro candidato mais próximo antes de buscar — como a
    equação usa H como variável (não só como limite de integração, ver
    docstring de obter_dimensoes_tora), essa é uma aproximação pequena
    mas real, não uma reprodução exata.

    Resultado cacheado em `_CACHE_TABELAS_AFILAMENTO` (ver lá) — chamadas
    seguintes com os mesmos parâmetros devolvem a tabela já pronta."""
    chave = _chave_tabela_afilamento(
        equacao, coeficientes, nomes_variaveis, dap, comprimento_tora, diametro_minimo_tora)
    tabela = _CACHE_TABELAS_AFILAMENTO.get(chave)
    if tabela is not None:
        return tabela

    n_candidatos = int(np.ceil(_ALTURA_MAXIMA_TABELA_AFILAMENTO / _PASSO_ALTURA_AFILAMENTO)) + 1
    alturas_candidatas = np.arange(n_candidatos) * _PASSO_ALTURA_AFILAMENTO
    aproveitavel, biomassa = _calcular_volumes_afilamento(
        equacao, coeficientes, nomes_variaveis, dap, pd.Series(alturas_candidatas),
        comprimento_tora, diametro_minimo_tora)
    tabela = (aproveitavel.to_numpy(), biomassa.to_numpy())
    _CACHE_TABELAS_AFILAMENTO[chave] = tabela
    return tabela


def _calcular_volumes_afilamento_tabela(
    tabela_aproveitavel: np.ndarray, tabela_biomassa: np.ndarray, serie_H: pd.Series,
) -> Tuple[pd.Series, pd.Series]:
    """Busca (volume_aproveitavel, volume_biomassa) na tabela pré-calculada
    (ver _obter_tabela_afilamento) pra cada altura de `serie_H` — arredonda
    cada altura pro candidato mais próximo da tabela (passo
    _PASSO_ALTURA_AFILAMENTO) e indexa direto, em vez de reintegrar a
    grade. Linha com H desconhecido (NaN) sai NaN nas duas, mesmo
    tratamento de _calcular_volumes_afilamento."""
    index = serie_H.index
    H = serie_H.to_numpy(dtype=float)
    H_valido = ~np.isnan(H)
    n_candidatos = len(tabela_aproveitavel)
    indices = np.zeros(len(H), dtype=int)
    indices[H_valido] = np.clip(
        np.round(H[H_valido] / _PASSO_ALTURA_AFILAMENTO).astype(int), 0, n_candidatos - 1)
    aproveitavel = np.where(H_valido, tabela_aproveitavel[indices], np.nan)
    biomassa = np.where(H_valido, tabela_biomassa[indices], np.nan)
    return pd.Series(aproveitavel, index=index), pd.Series(biomassa, index=index)


def _ordem_execucao(nos: dict, conexoes: list) -> list:
    """Ordena os nós tipo "modelo"/"saida" pra que cada um seja avaliado só
    depois de quem alimenta suas entradas (permite encadear saída de um nó
    na entrada de outro, inclusive saída dentro de saída). Levanta
    ValueError se houver ciclo."""
    dependencias = {
        no_id: set() for no_id, no in nos.items() if no["tipo"] in _TIPOS_COM_DEPENDENCIA
    }
    for con in conexoes:
        if nos[con["destino"]]["tipo"] in _TIPOS_COM_DEPENDENCIA:
            dependencias[con["destino"]].add(con["origem"])

    # "classe_diametrica" é fonte sem dependência, igual "coluna" — nunca
    # entra em `dependencias` (não está em _TIPOS_COM_DEPENDENCIA), mas
    # precisa estar "resolvido" desde o início pra não travar falsamente
    # um "modelo" que ligue nele (ver avaliar_grafo) como se fosse ciclo.
    resolvidos = {no_id for no_id, no in nos.items() if no["tipo"] in ("coluna", "classe_diametrica")}
    pendentes = set(dependencias.keys())
    ordem = []
    progresso = True
    while pendentes and progresso:
        progresso = False
        for no_id in list(pendentes):
            if dependencias[no_id] <= resolvidos:
                ordem.append(no_id)
                resolvidos.add(no_id)
                pendentes.discard(no_id)
                progresso = True
    if pendentes:
        raise ValueError("Ciclo detectado entre nós ligados uns nos outros — desfaça alguma ligação.")
    return ordem


def avaliar_grafo(
    df: pd.DataFrame, nos: dict, conexoes: list, classes_diametricas=None, sortimentos=None,
    config_financeiro=None, idade_corte_raso=None, dimensoes_tora=None, custos_colheita=None,
    tipo_normalizacao_weibull="aditiva", custos_formacao=None,
    debug_tempos: Optional[Dict[int, float]] = None,
) -> Tuple[Dict, List[str]]:
    """Avalia o grafo sobre um DataFrame já lido (precisa ter coluna "id").
    Devolve (valores, erros): `valores` mapeia no_id -> pd.Series pros nós
    "modelo"/"saida"/"calculo" calculados com sucesso — EXCETO um "modelo"
    com uma entrada ligada a um nó "classe_diametrica" (ver bloco "modelo"
    abaixo) ou um "distribuicao" (ver bloco "distribuicao" — sempre por
    classe, não tem versão "de uma vez só"), que mapeiam pra um dict
    {classe: pd.Series}, um valor por classe diamétrica em vez de uma
    Series só; `erros` é uma lista de mensagens legíveis pros que não
    puderam (entrada solta, equação inválida, operador inválido).

    `classes_diametricas`, se passado (array de centros de classe — ver
    simulacao.obter_classes_diametricas), alimenta qualquer "modelo" com
    entrada ligada num nó "classe_diametrica". Sem isso, esses nós viram
    erro (mensagem própria), mas o resto do grafo continua avaliado
    normalmente.

    `sortimentos`, se passado (lista de tuplas (nome, limite_inferior,
    limite_superior, rendimento, preco), ordenada por limite_inferior — ver
    tela Configurações), alimenta qualquer nó "receita_sortimento"/
    "rendimento_sortimento". Sem isso, esses nós viram erro (mensagem
    própria), igual `classes_diametricas` faltando.

    `config_financeiro`, se passado (dict com "taxa_desconto",
    "ano_referencia", "pis", "cofins", "funrural" — valores crus da tela
    Configurações, percentuais como 8.0 pra 8%), alimenta qualquer nó
    "vpl_sortimento"/"vet_sortimento". Mesmo tratamento de ausência que
    `sortimentos`.

    `idade_corte_raso`, se passado (int — ver
    simulacao.obter_idade_corte_raso), alimenta qualquer nó
    "vet_sortimento". Mesmo tratamento de ausência que `sortimentos`.

    `dimensoes_tora`, se passado (dict com "comprimento_tora"/
    "diametro_minimo_tora"/"usar_tabela_afilamento" — ver
    obter_dimensoes_tora), alimenta qualquer nó "afilamento" — a última
    chave (checkbox em Configurações) troca o cálculo exato pela busca em
    tabela pré-calculada (ver _obter_tabela_afilamento/
    _calcular_volumes_afilamento_tabela). Mesmo tratamento de ausência que
    `sortimentos`.

    `custos_colheita`, se passado (dict id -> {"nome", "custo_hora_maquina",
    "disponibilidade_mecanica", "eficiencia_operacional", "produtividade":
    {classe: valor}} — ver obter_custos_colheita), alimenta qualquer nó
    "custo_colheita" que tenha um `custo_colheita_id` escolhido (botão
    direito no nó, na tela Construtor de Variáveis). Sem isso, ou se o id
    escolhido não existir mais em `custos_colheita`, esse nó vira erro
    (mensagem própria) — mesmo tratamento de ausência que `sortimentos`.

    `tipo_normalizacao_weibull` ("aditiva", padrão, ou "proporcional" —
    ver simulacao.TIPOS_NORMALIZACAO_WEIBULL/obter_tipo_normalizacao_weibull)
    alimenta qualquer nó "distribuicao" (mesma normalização por classe
    usada em simulacao.calcular_distribuicao_diametrica).

    `custos_formacao`, se passado ({idade: custo_total_no_ano} — ver
    obter_custos_formacao), alimenta qualquer nó "custo_formacao". Sem
    isso, esses nós devolvem 0.0 em toda linha (mesmo comportamento de
    "nada cadastrado ainda" que a coluna tinha quando era calculada
    direto em gerar_populacao) — ao contrário de `sortimentos`/
    `custos_colheita`, ausência aqui NÃO vira erro, porque é normal um
    projeto não ter nenhum custo de formação cadastrado.

    `debug_tempos`, se passado (dict vazio, mutado aqui), recebe
    no_id -> segundos gastos avaliando aquele nó — diagnóstico de
    performance pra grafos grandes/lentos (ver
    construtor_variaveis.py:testar/salvar_construtor, que mostram os mais
    lentos). Não afeta o resultado, só mede; None (padrão) não mede nada,
    sem custo extra além de um `time.perf_counter()` por nó.

    Nós "receita_sortimento"/"rendimento_sortimento"/"custo_colheita" só
    calculam nas linhas com "evento_manejo" preenchido (se essa coluna
    existir em `df`), mesmo sem nada configurado em "Configurar
    eventos..." (botão direito no nó) — idade sem evento vira NaN nesses,
    mesma regra de simulacao.calcular_volume_por_sortimento (ver
    _mascarar_sem_evento_manejo). "vpl_sortimento"/"vet_sortimento" são
    EXCEÇÃO (mesmo espírito do nó "saida"): sem nada configurado em
    "Configurar eventos...", calculam em TODA idade, sem restrição — só
    passam a restringir se o usuário marcar eventos específicos ali."""
    ordem = _ordem_execucao(nos, conexoes)

    # Índice imutável das entradas do grafo. O construtor continua aceitando
    # qualquer topologia válida; apenas trocamos centenas de buscas lineares
    # em ``conexoes`` por lookup O(1) durante esta avaliação.
    conexoes_por_entrada = {
        (conexao["destino"], conexao["entrada_idx"]): conexao
        for conexao in conexoes
    }

    def _conexao_entrada(no_destino, entrada_idx):
        return conexoes_por_entrada.get((no_destino, entrada_idx))

    # Linhas fora dos eventos de manejo configurados em CADA nó (padrão:
    # qualquer evento preenchido), ou com intensidade 0% num evento que
    # tem intensidade de verdade (ver _mascara_eventos_no, chamada nos
    # ramos "receita_sortimento"/"rendimento_sortimento"/"vpl_sortimento"/
    # "vet_sortimento" abaixo) não têm receita/rendimento/VPL/VET, viram
    # NaN em vez de calculadas. None quando a coluna nem existe em `df`
    # (construtor rodando sobre uma tabela que não é a população
    # simulada) — nesse caso não há o que mascarar naquele aspecto.
    evento = df["evento_manejo"] if "evento_manejo" in df.columns else None
    intensidade_evento = df["intensidade_evento"] if "intensidade_evento" in df.columns else None

    # Linhas de custo de formação florestal ANTERIORES ao plantio
    # (idade_simulada <= 0, inseridas por sincronizar_linhas_formacao
    # antes de chamar avaliar_grafo — ver core/construtores.py) não têm
    # nenhum outro dado de verdade (DAP, distribuição, volume por
    # classe...), só talhão/idade_simulada/ano_simulado/custo_formacao —
    # qualquer OUTRO nó do grafo que tentasse calcular algo ali receberia
    # lixo (na prática NaN na maioria dos casos, já que os outros campos
    # são nulos ali, mas "acumulado" (fillna(0) na entrada) e "ou"
    # (mescla com a 2ª entrada onde a 1ª é NaN) podem vazar valor de
    # OUTRAS linhas pra essas). Nó "custo_formacao" com
    # excluir_outras_contas=True (padrão) liga essa máscara — todo OUTRO
    # nó (nunca ele mesmo) vira NaN nessas linhas, inclusive em cadeia (um
    # nó que lê a saída de outro já mascarado sai mascarado também, já
    # que a máscara é aplicada progressivamente, ANTES de cada nó
    # downstream ler valores[no_id]). None (sem máscara) se não houver
    # "idade_simulada" em `df` ou nenhum nó "custo_formacao" com a opção
    # ligada.
    mascara_linhas_formacao = None
    if "idade_simulada" in df.columns and any(
            no.get("tipo") == "custo_formacao" and no.get("excluir_outras_contas", True)
            for no in nos.values()):
        mascara_linhas_formacao = df["idade_simulada"] > 0

    # Nó que RECEBE (direto ou em cadeia) a saída de um "custo_formacao" —
    # ex: um "Cálculo"/"Saída" que soma custo_formacao a outra conta antes
    # de alimentar um "vpl_sortimento"/"vet_sortimento". Esses nós têm
    # SIM um valor de verdade nessas linhas (o próprio custo de formação,
    # ou uma combinação dele — "ou"/"acumulado" existem exatamente pra
    # deixar passar isso, ver comentário acima) e NÃO podem ser
    # remascarados no final de cada iteração como os demais: um VPL/VET
    # que dependa de custo_formacao TEM que enxergar essas idades, senão o
    # custo de formação nunca entra na conta (o motivo de existir a linha
    # em primeiro lugar). Achado por alcançabilidade no grafo (BFS a
    # partir de todo nó "custo_formacao", seguindo `conexoes` adiante) —
    # não depende de calcular nada ainda, só da topologia.
    nos_dependentes_formacao = set()
    if mascara_linhas_formacao is not None:
        fila = [no_id for no_id, no in nos.items() if no.get("tipo") == "custo_formacao"]
        nos_dependentes_formacao = set(fila)
        while fila:
            atual = fila.pop()
            for con in conexoes:
                if con["origem"] == atual and con["destino"] not in nos_dependentes_formacao:
                    nos_dependentes_formacao.add(con["destino"])
                    fila.append(con["destino"])

    # Nó que ALIMENTA (direto ou em cadeia) a entrada de um "custo_formacao"
    # — ex: um "coluna" lendo "área" do talhão, ligado no pino de entrada
    # de um "custo_formacao" pra multiplicar o custo/ha pela área (ver nó
    # "custo_formacao" abaixo). Sem essa exceção, esse "coluna" seria
    # mascarado pra NaN nas linhas de formação (idade_simulada <= 0) pelo
    # mesmo motivo de qualquer outro "coluna" (ver bloco "coluna" acima) —
    # mesmo a área do talhão estando corretamente preenchida ali por
    # sincronizar_linhas_formacao (é uma coluna original da Base IFC
    # ByTalhao, repetida por talhão, não "lixo vazado" de outra idade).
    # Achado por alcançabilidade REVERSA (BFS a partir de todo nó
    # "custo_formacao", seguindo `conexoes` pra TRÁS) — mesmo raciocínio de
    # `nos_dependentes_formacao`, só que rio acima em vez de rio abaixo.
    nos_upstream_formacao = set()
    if mascara_linhas_formacao is not None:
        fila = [no_id for no_id, no in nos.items() if no.get("tipo") == "custo_formacao"]
        nos_upstream_formacao = set()
        while fila:
            atual = fila.pop()
            for con in conexoes:
                if con["destino"] == atual and con["origem"] not in nos_upstream_formacao:
                    nos_upstream_formacao.add(con["origem"])
                    fila.append(con["origem"])

    def _mascarar_evento_com_excecao_formacao(no_id, resultado, mascara_evento_valido):
        """Mesmo `_mascarar_sem_evento_manejo`, mas "resgata" as linhas de
        formação (idade_simulada <= 0) de um nó em `nos_dependentes_formacao`
        antes de mascarar por evento — evento_manejo é sempre NULL nessas
        linhas (não existe Raleio/Desbaste antes do plantio), então a
        restrição por evento (ligada por padrão em receita/rendimento/custo
        de colheita, ou ligada manualmente num "saida"/"vpl_sortimento"/
        "vet_sortimento") as excluiria de novo mesmo já isentas da máscara
        geral acima. Faz
        diferença de verdade pra "vpl_sortimento"/"vet_sortimento"/"saida"
        (podem legitimamente ter valor ali, vindo de custo_formacao); pros
        outros tipos (receita/rendimento/custo de colheita) é inofensivo —
        a entrada deles já é NaN ali de qualquer jeito (depende de uma
        cadeia de Classe Diamétrica, que não existe antes do plantio)."""
        if mascara_evento_valido is not None and mascara_linhas_formacao is not None \
                and no_id in nos_dependentes_formacao:
            mascara_evento_valido = mascara_evento_valido | ~mascara_linhas_formacao
        return _mascarar_sem_evento_manejo(resultado, mascara_evento_valido)

    valores = {}
    colunas_numericas = {}
    for no_id, no in nos.items():
        if no["tipo"] == "coluna":
            if no["coluna"] not in df.columns:
                continue
            # Colunas originais da Base IFC (e as que atravessam pra
            # simulacao_talhao_idade) são gravadas como TEXT — sem essa
            # conversão, uma equação quebra em tempo de execução em vez de
            # calcular o número.
            # Vários nós podem apontar para a mesma coluna. A conversão de
            # texto/locale para número é pura e idêntica, então é feita uma
            # única vez por nome de coluna em cada execução do grafo.
            if no["coluna"] not in colunas_numericas:
                colunas_numericas[no["coluna"]] = converter_numero(df[no["coluna"]])
            valor_coluna = colunas_numericas[no["coluna"]]
            # idade_simulada/ano_simulado são as únicas colunas (fora
            # custo_formacao) que sincronizar_linhas_formacao preenche com
            # dado "de idade" nessas linhas — nunca "lixo vazado", são o
            # dado real da própria linha. Ficam sempre sem máscara aqui (um
            # nó "coluna" nunca é alcançado por outro nó, então não
            # entraria em nos_dependentes_formacao de outro jeito) porque
            # um "vpl_sortimento" downstream precisa do idade_simulada de
            # verdade dessas linhas como "período" — sem isso, o VPL
            # calcularia com período NaN e viraria NaN de qualquer jeito,
            # mesmo já exempto da remascagem final abaixo. Um nó NÃO
            # dependente de custo_formacao que leia idade_simulada/
            # ano_simulado (ex: alguma conta de idade em meses) continua
            # sendo pego pela remascagem final normalmente — só o valor
            # "de passagem" aqui fica exposto, não o resultado final.
            #
            # Um "coluna" em `nos_upstream_formacao` (ligado no pino de
            # entrada de um "custo_formacao" — ver bloco "custo_formacao"
            # abaixo) também fica sem máscara: colunas ORIGINAIS da Base
            # IFC ByTalhao (ex: área do talhão) são repetidas por talhão
            # nessas linhas por sincronizar_linhas_formacao, são dado real,
            # não "lixo vazado" — mascarar isso deixaria o multiplicador
            # de custo_formacao sempre NaN nas linhas de formação.
            if no["coluna"] not in ("idade_simulada", "ano_simulado") \
                    and no_id not in nos_upstream_formacao:
                valor_coluna = _aplicar_mascara_valor(valor_coluna, mascara_linhas_formacao)
            valores[no_id] = valor_coluna
        # "classe_diametrica" não entra em `valores` — não é um valor por
        # linha, é uma fonte que só faz sentido dentro da avaliação de um
        # "modelo" ligado nele (ver abaixo), então não tem Series própria.

    erros = []
    # Mede o tempo de cada nó sem precisar envolver o corpo gigante do
    # if/elif abaixo num try/finally: no INÍCIO de cada iteração, atribui
    # o tempo decorrido desde a última marca ao nó ANTERIOR (que acabou de
    # terminar, seja caindo num "continue" no meio de um ramo ou chegando
    # ao fim do if/elif) — funciona porque um "continue" só pula pro topo
    # do próximo "for", exatamente onde a marca seguinte é tirada.
    _debug_no_anterior = None
    _debug_marca = time.perf_counter() if debug_tempos is not None else None
    for no_id in ordem:
        if debug_tempos is not None:
            _debug_agora = time.perf_counter()
            if _debug_no_anterior is not None:
                debug_tempos[_debug_no_anterior] = _debug_agora - _debug_marca
            _debug_marca = _debug_agora
            _debug_no_anterior = no_id

        no = nos[no_id]

        if no["tipo"] == "modelo":
            # Uma entrada ligada num nó "classe_diametrica" (em vez de
            # "coluna"/outro "modelo") faz esse nó inteiro ser avaliado
            # uma vez por classe diamétrica (primeira até a última,
            # configuradas em Configurações), com a variável daquela
            # entrada recebendo o valor escalar da classe em vez de uma
            # Series ligada por fio — as outras entradas continuam
            # normais. É o mesmo mecanismo que a distribuição Weibull já
            # usa pra calcular probabilidade por classe (ver
            # simulacao._calcular_matriz_distribuicao), só que aqui vale
            # pra QUALQUER equação cadastrada em Modelos, não só a fórmula
            # de sobrevivência fixa. Só é permitido ligar "classe_diametrica"
            # numa entrada por modelo — combinar duas geraria um produto
            # cartesiano de classes que este mecanismo não cobre.
            mapeamento_variaveis = {}
            variavel_classe = None
            faltando = []
            for i, nome_var in enumerate(no["variaveis"]):
                conexao = _conexao_entrada(no_id, i)
                if conexao is None:
                    faltando.append(nome_var)
                    continue
                no_origem = nos.get(conexao["origem"])
                if no_origem is not None and no_origem["tipo"] == "classe_diametrica":
                    if variavel_classe is not None:
                        erros.append(
                            f"\"{no['rotulo']}\": só é permitido ligar Classe Diamétrica numa "
                            "única entrada do mesmo modelo")
                        variavel_classe = False  # marca erro já reportado, sem tentar de novo
                        break
                    variavel_classe = nome_var
                    continue
                if conexao["origem"] not in valores:
                    faltando.append(nome_var)
                    continue
                valor_resolvido = _resolver_valor_saida(
                    no_origem, valores[conexao["origem"]], conexao.get("saida_idx", 0))
                if valor_resolvido is None:
                    faltando.append(nome_var)
                    continue
                mapeamento_variaveis[nome_var] = valor_resolvido
            if variavel_classe is False:
                continue
            if faltando:
                erros.append(f"\"{no['rotulo']}\": falta ligar {', '.join(faltando)}")
                continue

            # Modelo estratificado (Coluna do estrato/Valor do estrato
            # cadastrados em Modelos, ver app/screens/modelos.py): quando
            # várias linhas de Modelos têm o mesmo nome (uma por estrato),
            # o Construtor as agrupa num nó só (ver
            # app/screens/construtor_variaveis.py:_atualizar_modelos_disponiveis),
            # e aqui cada variante só é avaliada nas linhas cuja Coluna do
            # estrato bate com o Valor do estrato dela — o resultado final
            # do nó já sai combinado (primeira variante que "bater" preenche
            # cada linha), sem precisar de um nó Saída pra juntar.
            variantes = _variantes_do_no(no)
            if not variantes:
                erros.append(f"\"{no['rotulo']}\": nenhuma variante de modelo cadastrada")
                continue
            variantes_com_mascara = _mascaras_variantes(df, variantes, no["rotulo"], erros)
            if not variantes_com_mascara:
                continue

            if variavel_classe is None:
                # Uma entrada "normal" (não ligada em Classe Diamétrica)
                # que recebeu um dict {classe: Series} — porque vem de
                # outro "modelo"/"saida"/"calculo" avaliado por classe
                # diamétrica — não tem como ser usada aqui: sem uma
                # entrada própria em Classe Diamétrica, este nó não tem
                # "a classe da vez" pra escolher o valor certo dentro do
                # dict. Sem esta checagem, o dict cai direto no `eval` da
                # equação e quebra com um erro de numpy sem explicação
                # (ex: "argument 0 of type dict").
                entradas_por_classe = [
                    nome_var for nome_var, valor in mapeamento_variaveis.items()
                    if isinstance(valor, dict)
                ]
                if entradas_por_classe:
                    erros.append(
                        f"\"{no['rotulo']}\": a entrada {', '.join(entradas_por_classe)} vem de um "
                        "nó calculado por classe diamétrica — só pode alimentar outro modelo que "
                        "também tenha uma entrada ligada em Classe Diamétrica")
                    continue
                resultado_total = pd.Series(np.nan, index=df.index)
                algum_ok = False
                for variante, mascara in variantes_com_mascara:
                    try:
                        resultado = motor_modelos.avaliar_modelo(
                            variante["equacao"], variante["coeficientes"], mapeamento_variaveis,
                            index_padrao=df.index)
                    except ValueError as e:
                        erros.append(f"\"{no['rotulo']}\": {e}")
                        continue
                    if mascara is not None:
                        resultado = resultado.where(mascara)
                    resultado_total = resultado_total.where(resultado_total.notna(), resultado)
                    algum_ok = True
                if algum_ok:
                    valores[no_id] = resultado_total
            else:
                if classes_diametricas is None or len(classes_diametricas) == 0:
                    erros.append(
                        f"\"{no['rotulo']}\": configure a primeira classe, a última classe e o "
                        "intervalo de classe diamétrica em Configurações.")
                    continue
                try:
                    # Avalia TODAS as classes numa matriz (linhas ×
                    # classes) em uma chamada por variante. O contrato
                    # externo continua sendo {classe: Series}; portanto
                    # qualquer grafo/encadeamento existente permanece
                    # válido, mas evitamos 90 evals e 90 pipelines pandas
                    # para cada modelo ligado em Classe Diamétrica.
                    classes_np = np.asarray(classes_diametricas, dtype=float)
                    shape = (len(df), len(classes_np))
                    variaveis_matriz = {}
                    for nome_var, valor in mapeamento_variaveis.items():
                        if isinstance(valor, dict):
                            variaveis_matriz[nome_var] = np.column_stack([
                                valor[float(classe)].to_numpy(dtype=float)
                                for classe in classes_np])
                        elif isinstance(valor, pd.Series):
                            variaveis_matriz[nome_var] = valor.to_numpy(dtype=float)[:, None]
                        else:
                            variaveis_matriz[nome_var] = valor
                    variaveis_matriz[variavel_classe] = classes_np[None, :]

                    resultado_matriz = np.full(shape, np.nan, dtype=float)
                    for variante, mascara in variantes_com_mascara:
                        bruto = motor_modelos.avaliar_expressao_array(
                            variante["equacao"], variante["coeficientes"], variaveis_matriz)
                        try:
                            bruto = np.broadcast_to(bruto, shape)
                        except ValueError as e:
                            raise ValueError(
                                f"resultado com dimensões incompatíveis {bruto.shape}; esperado {shape}: {e}")
                        preencher = np.isnan(resultado_matriz)
                        if mascara is not None:
                            preencher &= mascara.to_numpy(dtype=bool)[:, None]
                        resultado_matriz[preencher] = bruto[preencher]

                    resultado_por_classe = {
                        float(classe): pd.Series(resultado_matriz[:, j], index=df.index)
                        for j, classe in enumerate(classes_np)
                    }
                    valores[no_id] = resultado_por_classe
                except ValueError as e:
                    erros.append(f"\"{no['rotulo']}\": {e}")
                except KeyError as e:
                    erros.append(
                        f"\"{no['rotulo']}\": uma entrada por classe diamétrica não tem a classe "
                        f"{e} — confira se as classes diamétricas em Configurações não mudaram "
                        "desde que aquele nó foi calculado")

        elif no["tipo"] == "distribuicao":
            # Probabilidade por classe diamétrica (mesma fórmula da FDP
            # usada na tela Simulação — ver simulacao.probabilidades_por_classe/
            # sobrevivencia_weibull: P(classe-0.5 < diâmetro <= classe+0.5)
            # segundo a Weibull de forma/escala daquela linha), pra usar
            # como entrada de outro nó (ex: multiplicar pela contagem de
            # árvores da classe). Duas entradas fixas (não editáveis, ao
            # contrário de "modelo"): "forma" e "escala". Sempre por
            # classe — não existe uma versão "de uma vez só", já que o
            # resultado só faz sentido classe a classe.
            faltando = []
            entradas_resolvidas = {}
            for i, nome_var in enumerate(("forma", "escala")):
                conexao = _conexao_entrada(no_id, i)
                if conexao is None or conexao["origem"] not in valores:
                    faltando.append(nome_var)
                    continue
                valor_resolvido = _resolver_valor_saida(
                    nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
                if valor_resolvido is None:
                    faltando.append(nome_var)
                    continue
                entradas_resolvidas[nome_var] = valor_resolvido
            if faltando:
                erros.append(f"\"{no['rotulo']}\": falta ligar {', '.join(faltando)}")
                continue

            entradas_por_classe = [
                nome_var for nome_var, valor in entradas_resolvidas.items() if isinstance(valor, dict)
            ]
            if entradas_por_classe:
                erros.append(
                    f"\"{no['rotulo']}\": a entrada {', '.join(entradas_por_classe)} vem de um nó "
                    "calculado por classe diamétrica — forma/escala aqui precisam ser um valor só "
                    "por linha")
                continue

            if classes_diametricas is None or len(classes_diametricas) == 0:
                erros.append(
                    f"\"{no['rotulo']}\": configure a primeira classe, a última classe e o "
                    "intervalo de classe diamétrica em Configurações.")
                continue

            serie_forma = entradas_resolvidas["forma"]
            serie_escala = entradas_resolvidas["escala"]
            probabilidades = simulacao.probabilidades_por_classe(
                serie_forma.to_numpy(dtype=float), serie_escala.to_numpy(dtype=float),
                classes_diametricas, tipo_normalizacao_weibull)

            valores[no_id] = {
                float(classe): pd.Series(probabilidades[:, j], index=serie_forma.index)
                for j, classe in enumerate(classes_diametricas)
            }

        elif no["tipo"] == "recuperacao_weibull":
            # Recupera forma/escala da Weibull 2P casando média+CV (ver
            # comentário/funções _recuperar_forma_escala acima) em vez de
            # regredir forma/escala cada um por si — a técnica de projetar
            # média/CV entre idades/intensidades (ex: razão-guia f(i2)/f(i1))
            # entra ANTES deste nó, nas próprias entradas ligadas aqui, não
            # depois em cima de forma/escala. Duas entradas fixas (não
            # editáveis, mesmo esquema de "distribuicao"): "media" (ex:
            # dap_med_atual) e "cv" (ex: cv_dap_atual). Sempre "de uma vez
            # só" — média/cv são um valor por linha, não por classe
            # diamétrica (ao contrário de "distribuicao"). Duas saídas
            # nomeadas, ver _resolver_valor_saida/saidas_nomeadas.
            faltando = []
            entradas_resolvidas = {}
            for i, nome_var in enumerate(("media", "cv")):
                conexao = _conexao_entrada(no_id, i)
                if conexao is None or conexao["origem"] not in valores:
                    faltando.append(nome_var)
                    continue
                valor_resolvido = _resolver_valor_saida(
                    nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
                if valor_resolvido is None:
                    faltando.append(nome_var)
                    continue
                entradas_resolvidas[nome_var] = valor_resolvido
            if faltando:
                erros.append(f"\"{no['rotulo']}\": falta ligar {', '.join(faltando)}")
                continue

            entradas_por_classe = [
                nome_var for nome_var, valor in entradas_resolvidas.items() if isinstance(valor, dict)
            ]
            if entradas_por_classe:
                erros.append(
                    f"\"{no['rotulo']}\": a entrada {', '.join(entradas_por_classe)} vem de um nó "
                    "calculado por classe diamétrica — média/CV aqui precisam ser um valor só por "
                    "linha")
                continue

            serie_media = entradas_resolvidas["media"]
            serie_cv = entradas_resolvidas["cv"]
            serie_forma, serie_escala = _recuperar_forma_escala(serie_media, serie_cv)
            valores[no_id] = {"forma": serie_forma, "escala": serie_escala}

        elif no["tipo"] == "saida":
            # Combina as entradas ligadas em ordem (a 1ª é só a base; a
            # partir da 2ª, aplica o operador escolhido no fio contra o
            # acumulado até ali — soma/subtrai/multiplica/divide, sem
            # precedência, só da esquerda pra direita). Cada entrada
            # (inclusive a 1ª) pode ter "inverso": True — usa 1/valor no
            # lugar do valor antes de combinar, o suficiente pra uma Saída
            # de entrada única virar só "1/variável".
            #
            # Se alguma entrada vier de um "modelo" por classe diamétrica
            # (dict {classe: Series}, ver ramo "modelo"), a Saída inteira
            # também passa a ser "por classe" — combina classe a classe,
            # com as entradas "normais" (Series única) valendo igual em
            # toda classe. É o que permite continuar a conta depois de um
            # modelo por classe (ex: multiplicar o resultado por uma área).
            entradas = no.get("entradas", [])
            if not entradas:
                erros.append(f"\"{no['rotulo']}\": nenhuma entrada ligada")
                continue
            valores_entrada = []
            ok = True
            for i in range(len(entradas)):
                conexao = _conexao_entrada(no_id, i)
                if conexao is None or conexao["origem"] not in valores:
                    erros.append(f"\"{no['rotulo']}\": entrada {i + 1} sem ligação válida")
                    ok = False
                    break
                valor_resolvido = _resolver_valor_saida(
                    nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
                if valor_resolvido is None:
                    erros.append(f"\"{no['rotulo']}\": entrada {i + 1} sem ligação válida")
                    ok = False
                    break
                valores_entrada.append(valor_resolvido)
            if not ok:
                continue

            classes_por_classe = None
            for valor in valores_entrada:
                if isinstance(valor, dict):
                    chaves = frozenset(valor.keys())
                    if classes_por_classe is not None and chaves != classes_por_classe:
                        erros.append(
                            f"\"{no['rotulo']}\": entradas por classe diamétrica com conjuntos "
                            "de classe diferentes — não dá pra combinar")
                        ok = False
                        break
                    classes_por_classe = chaves
            if not ok:
                continue

            if classes_por_classe is not None:
                resultado_por_classe = {}
                for classe in classes_por_classe:
                    valor, ok = _combinar_entradas_saida(
                        entradas, valores_entrada, classe, no["rotulo"], erros)
                    if not ok:
                        break
                    resultado_por_classe[classe] = valor
                if ok:
                    valores[no_id] = resultado_por_classe
            else:
                valor, ok = _combinar_entradas_saida(
                    entradas, valores_entrada, None, no["rotulo"], erros)
                if ok:
                    valores[no_id] = valor

            # Restrição de eventos é OPCIONAL num nó "saida" genérico (ao
            # contrário dos financeiros, que sempre restringem — ver
            # _mascara_eventos_no) — só mascara se o usuário marcou algo
            # em "Configurar eventos..." (botão direito no nó, ver
            # construtor_variaveis.py:_ao_botao_direito). Sem isso, um
            # "saida" usado numa conta que precisa valer em toda idade
            # (ex: "DAP MED SIMULADO") continuaria funcionando igual a
            # antes dessa opção existir.
            if ok and no.get("eventos_manejo"):
                mascara = _mascara_eventos_no(no, evento, intensidade_evento)
                if mascara is not None:
                    if no_id in nos_dependentes_formacao and mascara_linhas_formacao is not None:
                        mascara = mascara | ~mascara_linhas_formacao
                    if isinstance(valores[no_id], dict):
                        valores[no_id] = _mascarar_sem_evento_manejo(valores[no_id], mascara)
                    else:
                        valores[no_id] = valores[no_id].where(mascara)

        elif no["tipo"] == "calculo":
            # Entrada única — aplica cada passo em sequência sobre o valor
            # de entrada (ver _aplicar_passo_calculo pro que cada tipo de
            # passo faz). Ex: DAP -> "^2" -> "*3.14159" -> "/40000" vira
            # área seccional. Um passo de redução ("Somar"/"Média de todas
            # as classes") no meio da sequência colapsa um resultado por
            # classe diamétrica (dict {classe: Series}, ex: VTCC de cada
            # classe numa coluna) numa Series só por linha (ex: VTCC total
            # da linha) — os passos depois dele operam nessa Series normal.
            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            valor_bruto = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if valor_bruto is None:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            passos = no.get("passos", [])

            resultado, ok = _aplicar_passos_calculo(valor_bruto, passos, no["rotulo"], erros)
            if ok:
                valores[no_id] = resultado

        elif no["tipo"] == "acumulado":
            # Soma acumulada de uma entrada, agrupada por uma coluna da
            # base (ex: talhão) e ordenada por outra (ex: idade simulada)
            # — não pelas colunas ligadas por fio, e sim escolhidas direto
            # da tabela de origem (botão direito no nó), porque uma coluna
            # de agrupamento/ordem passando por um nó "coluna" comum sairia
            # convertida por converter_numero (quebra talhão alfanumérico
            # tipo "T01") ou perderia o propósito de ordenar por idade
            # ainda que a idade venha como texto. Ver _acumular_por_grupo
            # pro que "acumulado" quer dizer (ex: volume líquido = VTCC em
            # pé + acumulado do VTCC removido nos eventos anteriores do
            # mesmo talhão). Se a entrada vier por classe diamétrica (dict
            # {classe: Series}), acumula cada classe separadamente.
            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            valor_bruto = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if valor_bruto is None:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue

            coluna_grupo = no.get("coluna_grupo")
            coluna_ordem = no.get("coluna_ordem")
            if not coluna_grupo or not coluna_ordem:
                erros.append(
                    f"\"{no['rotulo']}\": configure a coluna de grupo e a coluna de ordem "
                    "(botão direito no nó)")
                continue
            if coluna_grupo not in df.columns:
                erros.append(f"\"{no['rotulo']}\": coluna de grupo \"{coluna_grupo}\" não existe na base")
                continue
            if coluna_ordem not in df.columns:
                erros.append(f"\"{no['rotulo']}\": coluna de ordem \"{coluna_ordem}\" não existe na base")
                continue

            grupo = df[coluna_grupo]
            ordem = converter_numero(df[coluna_ordem])

            if isinstance(valor_bruto, dict):
                valores[no_id] = {
                    classe: _acumular_por_grupo(serie, grupo, ordem)
                    for classe, serie in valor_bruto.items()
                }
            else:
                valores[no_id] = _acumular_por_grupo(valor_bruto, grupo, ordem)

        elif no["tipo"] == "receita_sortimento":
            # Multiplica uma entrada por classe diamétrica (dict {classe:
            # Series}, ex: VTCC de cada classe vindo de um Modelo ligado
            # em Classe Diamétrica) pelo preço (tela Configurações) do
            # sortimento cuja faixa [limite_inferior, limite_superior]
            # cobre aquela classe — mesma regra de
            # simulacao.calcular_volume_por_sortimento, só que aqui o
            # resultado continua por classe (uma coluna de receita por
            # classe, ex: "vtcc_rt_5".."vtcc_rt_100" — ver
            # saidas_nomeadas), não agregado por sortimento.
            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            valor_bruto = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if not isinstance(valor_bruto, dict):
                erros.append(
                    f"\"{no['rotulo']}\": a entrada precisa vir de um nó calculado por classe "
                    "diamétrica (ex: um Modelo ligado em Classe Diamétrica)")
                continue
            if not sortimentos:
                erros.append(f"\"{no['rotulo']}\": nenhum sortimento cadastrado (Configurações)")
                continue

            tipo_preco = no.get("tipo_preco", "serrada")
            fracao_liquida = 1.0
            if no.get("deduzir_tributos", False):
                if config_financeiro is None:
                    erros.append(
                        f"\"{no['rotulo']}\": configure PIS, COFINS e FUNRURAL na tela Configurações")
                    continue
                aliquota_total = sum(
                    float(config_financeiro.get(tributo) or 0.0)
                    for tributo in ("pis", "cofins", "funrural"))
                fracao_liquida = 1.0 - aliquota_total / 100.0
            resultado_por_classe = {}
            for classe, serie in valor_bruto.items():
                preco = _preco_sortimento_da_classe(sortimentos, classe, tipo_preco)
                resultado_por_classe[classe] = (
                    serie * preco * fracao_liquida
                    if preco is not None else pd.Series(np.nan, index=serie.index))
            valores[no_id] = _mascarar_evento_com_excecao_formacao(
                no_id, resultado_por_classe, _mascara_eventos_no(no, evento, intensidade_evento))

        elif no["tipo"] == "rendimento_sortimento":
            # Mesmo mecanismo de "receita_sortimento" (mesma tabela
            # `sortimentos`, mesmo casamento de faixa [limite_inferior,
            # limite_superior]), só que multiplica pelo RENDIMENTO em vez
            # do preço — cadastrado em Configurações como percentual (ex:
            # 30 quer dizer 30%), já convertido pra fração (0.3) por
            # _rendimento_sortimento_da_classe antes de multiplicar. Ex de
            # uso: volume aproveitável (saída do nó Afilamento) x
            # rendimento de serraria da classe = volume de produto.
            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            valor_bruto = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if not isinstance(valor_bruto, dict):
                erros.append(
                    f"\"{no['rotulo']}\": a entrada precisa vir de um nó calculado por classe "
                    "diamétrica (ex: a saída Aproveitável de um nó Afilamento)")
                continue
            if not sortimentos:
                erros.append(f"\"{no['rotulo']}\": nenhum sortimento cadastrado (Configurações)")
                continue

            resultado_por_classe = {}
            for classe, serie in valor_bruto.items():
                rendimento = _rendimento_sortimento_da_classe(sortimentos, classe)
                resultado_por_classe[classe] = (
                    serie * rendimento if rendimento is not None
                    else pd.Series(np.nan, index=serie.index))
            valores[no_id] = _mascarar_evento_com_excecao_formacao(
                no_id, resultado_por_classe, _mascara_eventos_no(no, evento, intensidade_evento))

        elif no["tipo"] == "custo_colheita":
            # Multiplica uma entrada por classe diamétrica (dict {classe:
            # Series}, ex: volume de alguma operação vindo de um Modelo
            # ligado em Classe Diamétrica) pelo custo efetivo (ver
            # _custo_efetivo_colheita_da_classe) do custo de colheita
            # ESCOLHIDO NESSE NÓ (botão direito — "nome" cadastrado na tela
            # Configurações: harvester, motosserra etc, o que estiver
            # cadastrado) naquela classe — produtividade varia por classe
            # (árvore mais grossa demora mais pra processar), os outros 3
            # fatores da fórmula (custo_hora_maquina, disponibilidade_mecanica,
            # eficiencia_operacional) são fixos pro custo inteiro. Mesmo
            # mascaramento por evento de manejo que receita_sortimento/
            # rendimento_sortimento (custo de colheita só faz sentido nas
            # idades de um evento de fato).
            custo_colheita_id = no.get("custo_colheita_id")
            if custo_colheita_id is None:
                erros.append(
                    f"\"{no['rotulo']}\": nenhum custo de colheita selecionado (botão direito no nó)")
                continue
            custo_selecionado = (custos_colheita or {}).get(custo_colheita_id)
            if custo_selecionado is None:
                erros.append(
                    f"\"{no['rotulo']}\": o custo de colheita selecionado não existe mais "
                    "(pode ter sido excluído na tela Configurações)")
                continue

            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            valor_bruto = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if not isinstance(valor_bruto, dict):
                erros.append(
                    f"\"{no['rotulo']}\": a entrada precisa vir de um nó calculado por classe "
                    "diamétrica (ex: um Modelo ligado em Classe Diamétrica)")
                continue

            resultado_por_classe = {}
            for classe, serie in valor_bruto.items():
                custo_efetivo = _custo_efetivo_colheita_da_classe(custo_selecionado, classe)
                resultado_por_classe[classe] = (
                    serie * custo_efetivo if custo_efetivo is not None
                    else pd.Series(np.nan, index=serie.index))
            valores[no_id] = _mascarar_evento_com_excecao_formacao(
                no_id, resultado_por_classe, _mascara_eventos_no(no, evento, intensidade_evento))

        elif no["tipo"] == "custo_formacao":
            # Custo de formação florestal (tela Configurações, tabela
            # `custos_formacao`) por idade — soma (R$/ha) de todo custo
            # cujo `ano` (idade do povoamento) bate com idade_simulada da
            # linha, 0 nas idades sem custo cadastrado (não NaN, pra somar/
            # exportar direto sem tratamento especial — mesma regra de
            # quando isso era calculado direto em gerar_populacao, antes
            # de virar nó). "idade_simulada" sempre lida direto da tabela
            # de origem — igual "acumulado" — nunca por fio (só faz
            # sentido rodando sobre simulacao_talhao_idade, a única tabela
            # com essa coluna); sem ela, vira erro. Não mascara por evento
            # de manejo — formação acontece pela IDADE, não por um evento
            # de manejo específico.
            if "idade_simulada" not in df.columns:
                erros.append(
                    f"\"{no['rotulo']}\": esta tabela não tem \"idade_simulada\" — só funciona "
                    "sobre a população simulada (simulacao_talhao_idade)")
                continue
            custo_por_idade = df["idade_simulada"].round().map(custos_formacao or {}).fillna(0.0)

            # Pino de entrada OPCIONAL — se ligado, multiplica o custo/ha
            # de cada idade pelo valor de entrada daquela linha (ex: um
            # "coluna" de área do talhão, pra converter R$/ha em R$ do
            # talhão inteiro; ou qualquer outro multiplicador por linha).
            # Sem nada ligado, comportamento igual a antes (só o custo/ha).
            # A entrada precisa ser uma Series simples (uma linha da
            # tabela = um valor), não um dict por classe diamétrica — não
            # faz sentido multiplicar custo de formação (que não varia por
            # classe) por algo que varia por classe.
            conexao = _conexao_entrada(no_id, 0)
            if conexao is not None and conexao["origem"] in valores:
                valor_entrada = _resolver_valor_saida(
                    nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
                if isinstance(valor_entrada, dict):
                    erros.append(
                        f"\"{no['rotulo']}\": a entrada precisa ser um valor por linha (ex: um "
                        "\"Coluna\"), não um valor por classe diamétrica")
                    continue
                if valor_entrada is not None:
                    custo_por_idade = custo_por_idade * valor_entrada

            valores[no_id] = custo_por_idade

        elif no["tipo"] == "vpl_sortimento":
            # VPL = RT/(1+taxa)^n, com "n" conforme "Base do período do
            # VPL" (tela Configurações — ver simulacao.BASES_PERIODO_VPL):
            # - "ano_referencia" (padrão): n = ano_simulado - ano_referencia
            #   (SEM abs — negativo quando a receita é ANTERIOR ao ano de
            #   referência, o que COMPÕE ela pra frente até lá em vez de
            #   descontar; positivo quando é posterior, descontando normal).
            #   A 2ª entrada do nó é lida como ano-calendário (ano_simulado).
            #   AQUI, e só aqui, ainda desconta a fração de PIS+COFINS —
            #   RT/(1+taxa)^n * (1-pis-cofins) — porque esse modo pressupõe
            #   "rt" como receita de venda tributável de verdade.
            # - "ano_zero": n = a própria 2ª entrada, direto — VPL =
            #   RT/(1+taxa)^idade_simulada, SEM PIS/COFINS (esse modo é de
            #   uso mais geral, não pressupõe receita tributável — ver
            #   fracao_liquida abaixo). Nesse modo a 2ª entrada deve vir
            #   ligada em idade_simulada (não ano_simulado): desconta
            #   contra a idade 0 (plantio) do próprio talhão, não um
            #   ano-calendário fixo igual pra todos, então não precisa de
            #   ano_referencia nem PIS/COFINS configurados.
            #
            # "rt" aceita os dois formatos: por classe diamétrica (dict
            # {classe: Series}, ex: saída de um nó Receita Total) —
            # resultado também por classe — OU já agregado (Series só) —
            # resultado também uma Series só; a fórmula é a mesma nos dois
            # casos, só muda se ela roda 1x ou 1x por classe (restrição de
            # eventos OPCIONAL — ver vet_sortimento logo abaixo, que segue o
            # mesmo padrão). A 2ª entrada é sempre um valor só por linha
            # (vale igual em toda classe quando "rt" é por classe — mesmo
            # tratamento que "acumulado" dá pro grupo/ordem). taxa_desconto/
            # pis/cofins (tela Configurações) são percentuais (ex: 8 -> 8%)
            # — divididos por 100 aqui antes de entrar na conta.
            faltando = []
            entradas_resolvidas = {}
            for i, nome_var in enumerate(("rt", "periodo")):
                conexao = _conexao_entrada(no_id, i)
                if conexao is None or conexao["origem"] not in valores:
                    faltando.append(nome_var)
                    continue
                valor_resolvido = _resolver_valor_saida(
                    nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
                if valor_resolvido is None:
                    faltando.append(nome_var)
                    continue
                entradas_resolvidas[nome_var] = valor_resolvido
            if faltando:
                erros.append(f"\"{no['rotulo']}\": falta ligar {', '.join(faltando)}")
                continue

            valor_rt = entradas_resolvidas["rt"]
            valor_periodo = entradas_resolvidas["periodo"]
            if isinstance(valor_periodo, dict):
                erros.append(
                    f"\"{no['rotulo']}\": a 2ª entrada vem de um nó calculado por classe "
                    "diamétrica — precisa ser um valor só por linha")
                continue

            base_periodo = (config_financeiro or {}).get("base_periodo_vpl") or "ano_referencia"
            # PIS/COFINS só entra no modo "ano_referencia" (VPL sobre
            # receita tributável de verdade, RT/(1+taxa)^n * (1-pis-cofins))
            # — no modo "ano_zero", vira só o desconto puro,
            # RT/(1+taxa)^idade_simulada, sem dedução nenhuma (esse modo
            # não pressupõe que "rt" seja receita de venda sujeita a
            # PIS/COFINS — é de uso mais geral, ver a opção "Ano Zero" na
            # tela Configurações).
            campos_obrigatorios = [("taxa_desconto", "Taxa de desconto")]
            if base_periodo == "ano_referencia":
                campos_obrigatorios += [
                    ("ano_referencia", "Ano de referência"), ("pis", "PIS"), ("cofins", "COFINS")]
            campos_faltando = [
                rotulo for chave, rotulo in campos_obrigatorios
                if not config_financeiro or config_financeiro.get(chave) is None
            ]
            if campos_faltando:
                erros.append(
                    f"\"{no['rotulo']}\": configure {', '.join(campos_faltando)} na tela "
                    "Configurações")
                continue

            taxa = float(config_financeiro["taxa_desconto"]) / 100.0

            if base_periodo == "ano_zero":
                n_periodos = converter_numero(valor_periodo)
                fracao_liquida = 1.0
            else:
                ano_referencia = float(config_financeiro["ano_referencia"])
                n_periodos = converter_numero(valor_periodo) - ano_referencia
                pis = float(config_financeiro["pis"]) / 100.0
                cofins = float(config_financeiro["cofins"]) / 100.0
                fracao_liquida = 1.0 - pis - cofins
            fator = (1.0 + taxa) ** n_periodos

            if isinstance(valor_rt, dict):
                resultado = {
                    classe: (serie_rt / fator) * fracao_liquida
                    for classe, serie_rt in valor_rt.items()
                }
            else:
                resultado = (valor_rt / fator) * fracao_liquida
            # Restrição de eventos é OPCIONAL aqui (ao contrário de
            # receita_sortimento/rendimento_sortimento/custo_colheita, que
            # sempre restringem por padrão a "qualquer evento preenchido"
            # mesmo sem nada configurado — ver _mascara_eventos_no) — mesmo
            # espírito do nó "saida" (e de vet_sortimento logo abaixo): sem
            # "Configurar eventos..." marcado (botão direito), VPL calcula
            # em TODA idade, não só nas de evento. Faz sentido pra VPL
            # especificamente porque, desde que passou a aceitar "rt"
            # agregado (não só por classe) e o modo "Ano Zero", ele virou
            # de uso mais geral — não é mais só "valor presente da receita
            # de um evento de manejo".
            mascara_evento = (
                _mascara_eventos_no(no, evento, intensidade_evento)
                if no.get("eventos_manejo") else None)
            valores[no_id] = _mascarar_evento_com_excecao_formacao(no_id, resultado, mascara_evento)

        elif no["tipo"] == "vet_sortimento":
            # VET = VPL / (1 - (1+taxa)^(-idade_corte_raso)) — "vpl" aceita
            # os dois formatos, igual "rt" em "vpl_sortimento": por classe
            # diamétrica (dict {classe: Series}, ex: saída de um nó VPL
            # ligado numa Receita por classe) ou já agregado (Series só,
            # ex: um VPL calculado sobre um "rt" que também não é por
            # classe) — resultado no mesmo formato da entrada. taxa_desconto
            # (tela Configurações, percentual — dividido por 100 aqui) e
            # idade_corte_raso (idade do Corte Raso da última "Gerar
            # simulação", ver simulacao.obter_idade_corte_raso) são os dois
            # escalares constantes em toda linha/classe.
            #
            # Restrição de eventos OPCIONAL (mesmo padrão do vpl_sortimento
            # logo acima, ver o comentário lá): sem "Configurar eventos..."
            # marcado, VET calcula em TODA idade, não só nas de evento —
            # faz sentido aqui pelo mesmo motivo do VPL, já que "vpl" aceita
            # entrada agregada (não só por classe), então VET também é de
            # uso mais geral, não só "valor da terra a partir de uma
            # receita de evento de manejo".
            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue
            valor_vpl = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if valor_vpl is None:
                erros.append(f"\"{no['rotulo']}\": entrada não ligada")
                continue

            if not config_financeiro or config_financeiro.get("taxa_desconto") is None:
                erros.append(
                    f"\"{no['rotulo']}\": configure a Taxa de desconto na tela Configurações")
                continue
            if idade_corte_raso is None:
                erros.append(
                    f"\"{no['rotulo']}\": idade do Corte Raso desconhecida — gere a simulação "
                    "(tela Simulação) antes")
                continue

            taxa = float(config_financeiro["taxa_desconto"]) / 100.0
            fator = 1.0 - (1.0 + taxa) ** (-float(idade_corte_raso))
            if fator == 0:
                erros.append(
                    f"\"{no['rotulo']}\": taxa de desconto e idade do Corte Raso resultam em "
                    "divisão por zero")
                continue

            if isinstance(valor_vpl, dict):
                resultado = {classe: serie / fator for classe, serie in valor_vpl.items()}
            else:
                resultado = valor_vpl / fator
            # Restrição de eventos OPCIONAL, mesmo padrão do VPL logo acima
            # (ver o comentário lá) — sem "Configurar eventos..." marcado,
            # VET calcula em TODA idade, não só nas de evento. Faz sentido
            # pelo mesmo motivo do VPL: "vpl" aqui aceita entrada agregada
            # (não só por classe), então VET também é de uso mais geral, não
            # só "valor da terra a partir de uma receita de evento".
            mascara_evento = (
                _mascara_eventos_no(no, evento, intensidade_evento)
                if no.get("eventos_manejo") else None)
            valores[no_id] = _mascarar_evento_com_excecao_formacao(no_id, resultado, mascara_evento)

        elif no["tipo"] == "afilamento":
            # Sempre por classe (como "distribuicao") — nunca "de uma vez
            # só". Pino único (entrada_idx 0): H (Ht), precisa vir de um nó
            # calculado por classe diamétrica (ex: um Modelo hipsométrico
            # ligado em Classe Diamétrica). DAP vem da própria classe
            # (implícito, sem pino) e h é varrido internamente de 0 até Ht
            # em passos de 0,1 m (ver _calcular_volumes_afilamento) — só H
            # precisa de fio. Gera duas saídas por classe (ver
            # _resolver_valor_saida): "aproveitavel" (toras inteiras,
            # segundo comprimento/diâmetro mínimo de Configurações) e "biomassa"
            # (resíduo = total do fuste menos aproveitável).
            conexao = _conexao_entrada(no_id, 0)
            if conexao is None or conexao["origem"] not in valores:
                erros.append(f"\"{no['rotulo']}\": entrada \"H\" não ligada")
                continue
            valor_H = _resolver_valor_saida(
                nos.get(conexao["origem"]), valores[conexao["origem"]], conexao.get("saida_idx", 0))
            if not isinstance(valor_H, dict):
                erros.append(
                    f"\"{no['rotulo']}\": a entrada \"H\" precisa vir de um nó calculado por classe "
                    "diamétrica (ex: um Modelo hipsométrico ligado em Classe Diamétrica)")
                continue

            if (dimensoes_tora is None or dimensoes_tora.get("comprimento_tora") is None
                    or dimensoes_tora.get("diametro_minimo_tora") is None):
                erros.append(
                    f"\"{no['rotulo']}\": configure Comprimento da tora e Diâmetro mínimo na tela "
                    "Configurações")
                continue
            comprimento_tora = float(dimensoes_tora["comprimento_tora"])
            diametro_minimo_tora = float(dimensoes_tora["diametro_minimo_tora"])
            if comprimento_tora <= 0:
                erros.append(f"\"{no['rotulo']}\": o comprimento da tora precisa ser maior que zero")
                continue
            usar_tabela_afilamento = bool(dimensoes_tora.get("usar_tabela_afilamento"))

            nomes_variaveis = no.get("variaveis", [])
            if len(nomes_variaveis) != 3:
                erros.append(
                    f"\"{no['rotulo']}\": o modelo de afilamento precisa ter exatamente 3 "
                    "variáveis cadastradas (DAP, h, H, nessa ordem) — confira em Modelos")
                continue

            variantes = _variantes_do_no(no)
            if not variantes:
                erros.append(f"\"{no['rotulo']}\": nenhuma variante de modelo cadastrada")
                continue
            variantes_com_mascara = _mascaras_variantes(df, variantes, no["rotulo"], erros)
            if not variantes_com_mascara:
                continue

            try:
                resultado_aproveitavel = {}
                resultado_biomassa = {}
                for classe, serie_H in valor_H.items():
                    dap = float(classe)
                    aproveitavel_classe = pd.Series(np.nan, index=df.index)
                    biomassa_classe = pd.Series(np.nan, index=df.index)
                    for variante, mascara in variantes_com_mascara:
                        # Só manda pra _calcular_volumes_afilamento (grade
                        # fina/grossa de altura, o núcleo caro deste nó) as
                        # linhas que essa variante de fato vai PREENCHER:
                        # H válido, dentro da máscara da variante (se
                        # houver — None = "Todos", toda linha bate) e ainda
                        # sem valor de uma variante anterior (mesma
                        # precedência de sempre — a 1ª variante a
                        # preencher uma linha vale, uma variante "Todos"
                        # antes de variantes por estrato não é mais
                        # sobrescrita por elas). Com N variantes de
                        # estrato cobrindo partições disjuntas de `df`
                        # (caso comum — cada variante bate SÓ no estrato
                        # dela), isso evita rodar a grade inteira N vezes
                        # sobre TODAS as linhas só pra descartar (mascarar
                        # pra NaN) as (N-1)/N que não eram daquela
                        # variante — antes o custo desse nó escalava com
                        # nº de classes × nº de variantes × nº de linhas
                        # de `df`; agora escala com nº de classes × nº de
                        # linhas de `df` (cada linha computada uma vez só,
                        # pela variante que realmente a preenche).
                        linhas_pendentes = serie_H.notna() & aproveitavel_classe.isna()
                        if mascara is not None:
                            linhas_pendentes &= mascara
                        if not linhas_pendentes.any():
                            continue
                        if usar_tabela_afilamento:
                            tabela_aproveitavel, tabela_biomassa = _obter_tabela_afilamento(
                                variante["equacao"], variante["coeficientes"], nomes_variaveis, dap,
                                comprimento_tora, diametro_minimo_tora)
                            aprov, bioma = _calcular_volumes_afilamento_tabela(
                                tabela_aproveitavel, tabela_biomassa, serie_H[linhas_pendentes])
                        else:
                            aprov, bioma = _calcular_volumes_afilamento(
                                variante["equacao"], variante["coeficientes"], nomes_variaveis, dap,
                                serie_H[linhas_pendentes], comprimento_tora, diametro_minimo_tora)
                        aproveitavel_classe.loc[linhas_pendentes] = aprov
                        biomassa_classe.loc[linhas_pendentes] = bioma
                    resultado_aproveitavel[float(classe)] = aproveitavel_classe
                    resultado_biomassa[float(classe)] = biomassa_classe
                valores[no_id] = {
                    "aproveitavel": resultado_aproveitavel, "biomassa": resultado_biomassa}
            except ValueError as e:
                erros.append(f"\"{no['rotulo']}\": {e}")

        # Aplica a máscara de linhas de formação (ver mascara_linhas_formacao
        # acima) no valor que ACABOU de ser calculado nesta iteração —
        # progressivamente, nó a nó, não só no final: um nó B que leia a
        # saída de A (já mascarado aqui) via _resolver_valor_saida também
        # sai mascarado, sem precisar mascarar B de novo explicitamente.
        # Nunca mascara o próprio nó "custo_formacao" (é ele quem tem
        # valor de verdade nessas linhas) nem um nó em
        # nos_dependentes_formacao (ver acima — ex: um "vpl_sortimento"
        # ligado a custo_formacao PRECISA enxergar essas linhas, senão o
        # custo de formação nunca chega no VPL) nem um nó em
        # nos_upstream_formacao (ver acima — ex: um "Cálculo" entre um
        # "coluna" de área e o pino de entrada de custo_formacao PRECISA
        # repassar o valor de verdade, senão o multiplicador de
        # custo_formacao vira NaN nessas linhas).
        if no_id in valores and no["tipo"] != "custo_formacao" \
                and no_id not in nos_dependentes_formacao and no_id not in nos_upstream_formacao:
            valores[no_id] = _aplicar_mascara_valor(valores[no_id], mascara_linhas_formacao)

    if debug_tempos is not None and _debug_no_anterior is not None:
        debug_tempos[_debug_no_anterior] = time.perf_counter() - _debug_marca

    return valores, erros


def saidas_nomeadas(nos: dict, valores: dict) -> Dict[str, pd.Series]:
    """Vira coluna de saída todo nó com "nome_saida" preenchido, já
    calculado, E marcado com "gravar" (não importa o tipo — cobre tanto os
    nós "saida" dedicados quanto construtores salvos antes deles
    existirem, quando o nome de saída ficava direto no nó "modelo"). Nome e
    "gravar" são independentes: dá pra nomear um nó só pra organização do
    grafo (ex: uma Saída intermediária numa cadeia) sem que isso jogue ele
    na tabela. "gravar" ausente (grafos salvos antes dessa separação
    existir) equivale a True — mantém o comportamento antigo, em que só
    ter nome já bastava.

    Um "modelo" com entrada ligada num nó "classe_diametrica" é o único
    caso (fora "afilamento", ver abaixo) em que um nó vira mais de uma
    coluna: valores[no_id] ali é {classe: Series} (ver avaliar_grafo), não
    uma Series só — cada classe expande pra uma coluna "{nome_saida}_
    {classe}" (ex: "prob_5", "prob_7", ... pra classes 5, 7, ...).

    "afilamento" tem duas saídas independentes (ver avaliar_grafo, ramo
    "afilamento") — cada uma com seu próprio nome
    ("nome_saida_aproveitavel"/"nome_saida_biomassa") e ambas sempre por
    classe; cada nome preenchido (independente do outro) expande do mesmo
    jeito que "modelo" por classe, só que lendo de valores[no_id]
    ["aproveitavel"]/["biomassa"] em vez de valores[no_id] direto.

    "recuperacao_weibull" também tem duas saídas independentes
    ("nome_saida_forma"/"nome_saida_escala"), mas nunca por classe (média/cv
    são sempre um valor por linha) — cada nome preenchido vira uma coluna
    só, sem o expandir "_{classe}" que afilamento tem."""
    saidas = {}
    for no_id, no in nos.items():
        if no_id not in valores or not no.get("gravar", True):
            continue
        if no["tipo"] == "afilamento":
            valor = valores[no_id]
            for chave, campo_nome in (
                    ("aproveitavel", "nome_saida_aproveitavel"), ("biomassa", "nome_saida_biomassa")):
                nome_saida = no.get(campo_nome)
                if not nome_saida:
                    continue
                for classe, serie in valor.get(chave, {}).items():
                    saidas[f"{nome_saida}_{classe:g}"] = serie
            continue
        if no["tipo"] == "recuperacao_weibull":
            valor = valores[no_id]
            for chave, campo_nome in (("forma", "nome_saida_forma"), ("escala", "nome_saida_escala")):
                nome_saida = no.get(campo_nome)
                if not nome_saida:
                    continue
                saidas[nome_saida] = valor.get(chave)
            continue
        if not no.get("nome_saida"):
            continue
        valor = valores[no_id]
        if isinstance(valor, dict):
            for classe, serie in valor.items():
                saidas[f"{no['nome_saida']}_{classe:g}"] = serie
        else:
            saidas[no["nome_saida"]] = valor
    return saidas


# ==========================================================
# GRAVAÇÃO DO RESULTADO
# ==========================================================

_TABELA_TEMP_SAIDAS = "temp_gravar_saidas_construtor"


_LOTE_COLUNAS_GRAVACAO = 200


def verificar_colisao_saidas(saidas: dict) -> None:
    """SQLite não diferencia maiúsculas de minúsculas em nome de coluna
    (ALTER TABLE ADD COLUMN recusa uma 2ª coluna que só difere no case,
    "duplicate column name") — duas saídas do grafo que só diferem em
    maiúscula/minúscula (ex: dois nós renomeados "D2"/"d2") tentariam
    gravar na MESMA coluna física. Levanta ValueError (mensagem já pronta
    pra mostrar) se houver alguma colisão — chamado tanto por
    gravar_saidas_como_colunas (defensivo, protege qualquer chamador,
    inclusive aplicar_construtores_salvos) quanto por
    screens/construtor_variaveis.py:salvar_construtor (antes de persistir
    o grafo, pra não salvar um construtor que nunca vai conseguir gravar
    as colunas)."""
    por_minusculo: Dict[str, List[str]] = {}
    for nome in saidas:
        por_minusculo.setdefault(nome.lower(), []).append(nome)
    colisoes = [nomes for nomes in por_minusculo.values() if len(nomes) > 1]
    if colisoes:
        detalhes = "; ".join(" e ".join(f'"{n}"' for n in nomes) for nomes in colisoes)
        raise ValueError(
            "Duas saídas do grafo geram a mesma coluna no banco (o SQLite não diferencia "
            f"maiúsculas de minúsculas): {detalhes}. Renomeie uma delas (botão direito no nó)."
        )


def gravar_saidas_como_colunas(conn: sqlite3.Connection, tabela: str, df: pd.DataFrame, saidas: dict) -> None:
    """Grava as colunas calculadas de volta em `tabela`, casando por id.

    Em vez de um UPDATE por linha por coluna (inviável em tabelas com
    milhões de linhas — cada UPDATE cruza a fronteira Python/SQLite, e isso
    domina o tempo bem antes do custo da busca por id em si, que já é
    rápida por `id` ser o rowid), sobe os valores numa tabela TEMP (vive só
    na sessão da conexão — não é persistida no arquivo de trabalho, não
    entra no backup() de projeto.sincronizar) com um INSERT em lote só, e
    faz UM UPDATE por LOTE de `_LOTE_COLUNAS_GRAVACAO` colunas (não mais um
    por coluna) casando pelo id — "SET (c1, c2, ...) = (SELECT c1, c2, ...
    FROM temp WHERE temp.id = tabela.id)" (row value / atribuição
    multi-coluna, suportado desde SQLite 3.15 — bem mais antigo que a
    versão mínima já considerada aqui pro UPDATE...FROM, então seguro sem
    checar versão) deixa o SQLite casar por id UMA vez por linha e trazer
    todas as colunas do lote de uma vez, em vez de uma subquery + busca
    própria POR COLUNA. Um grafo com muitas saídas por classe diamétrica
    (uma coluna por classe, multiplicada por vários nós — facilmente
    centenas) tinha aqui um UPDATE por coluna que dominava o tempo de
    "Gerar simulação" mesmo com o cálculo dos nós em si sendo rápido —
    ver construtor_variaveis.py:_resumo_tempos/
    aplicar_construtores_salvos, que agora medem "gravação no banco" à
    parte do cálculo justamente pra expor isso. Lotes de tamanho fixo (em
    vez de uma instrução só com todas as colunas de uma vez) evitam testar
    limites do SQLite (nº de colunas/tamanho de SQL) em produção sem
    necessidade — poucas centenas de colunas por instrução já entrega
    quase todo o ganho de agrupar."""
    if not saidas:
        return

    verificar_colisao_saidas(saidas)

    # Comparação por minúsculas (não só o nome exato) — mesmo motivo de
    # verificar_colisao_saidas acima: se a tabela já tem uma coluna com
    # case diferente do nome atual (ex: coluna gravada como "d2" numa
    # versão anterior do grafo, saída renomeada pra "D2" depois), SQLite
    # já entende como a MESMA coluna — tentar um ALTER TABLE ADD COLUMN
    # aqui quebraria com "duplicate column name", mesmo sem colisão
    # nenhuma dentro do `saidas` atual.
    existentes_lower = {d[0].lower() for d in conn.execute(f'SELECT * FROM "{tabela}" LIMIT 0').description}
    for nome in saidas:
        if nome.lower() not in existentes_lower:
            conn.execute(f'ALTER TABLE "{tabela}" ADD COLUMN "{nome}" REAL')
            existentes_lower.add(nome.lower())

    nomes_saida = list(saidas.keys())
    conn.execute(f'DROP TABLE IF EXISTS "{_TABELA_TEMP_SAIDAS}"')
    colunas_temp_sql = ", ".join(f'"{nome}" REAL' for nome in nomes_saida)
    conn.execute(
        f'CREATE TEMP TABLE "{_TABELA_TEMP_SAIDAS}" (id INTEGER PRIMARY KEY, {colunas_temp_sql})'
    )

    ids = df["id"].tolist()
    series_listas = [
        [None if pd.isna(v) else float(v) for v in saidas[nome].tolist()] for nome in nomes_saida
    ]
    linhas = list(zip(ids, *series_listas))
    colunas_join = ", ".join(f'"{n}"' for n in nomes_saida)
    marcadores = ", ".join("?" for _ in range(len(nomes_saida) + 1))
    conn.executemany(
        f'INSERT INTO "{_TABELA_TEMP_SAIDAS}" (id, {colunas_join}) VALUES ({marcadores})', linhas)

    for inicio in range(0, len(nomes_saida), _LOTE_COLUNAS_GRAVACAO):
        lote = nomes_saida[inicio:inicio + _LOTE_COLUNAS_GRAVACAO]
        colunas_lote = ", ".join(f'"{n}"' for n in lote)
        conn.execute(
            f'UPDATE "{tabela}" SET ({colunas_lote}) = ('
            f'  SELECT {colunas_lote} FROM "{_TABELA_TEMP_SAIDAS}" '
            f'  WHERE "{_TABELA_TEMP_SAIDAS}".id = "{tabela}".id'
            f') WHERE id IN (SELECT id FROM "{_TABELA_TEMP_SAIDAS}")'
        )

    conn.execute(f'DROP TABLE IF EXISTS "{_TABELA_TEMP_SAIDAS}"')
    conn.commit()


def gravar_saidas_como_tabela_nova(
    conn: sqlite3.Connection, nome_tabela: str, df: pd.DataFrame, saidas: dict
) -> None:
    verificar_colisao_saidas(saidas)
    conn.execute(f'DROP TABLE IF EXISTS "{nome_tabela}"')
    colunas_sql = ", ".join(f'"{nome}" REAL' for nome in saidas)
    conn.execute(f'CREATE TABLE "{nome_tabela}" (id INTEGER PRIMARY KEY, {colunas_sql})')

    colunas_nomes = ", ".join(["id"] + [f'"{n}"' for n in saidas])
    marcadores = ", ".join("?" for _ in range(len(saidas) + 1))
    series_listas = [
        [None if pd.isna(v) else float(v) for v in serie.tolist()] for serie in saidas.values()
    ]
    linhas = list(zip(df["id"].tolist(), *series_listas))
    conn.executemany(f'INSERT INTO "{nome_tabela}" ({colunas_nomes}) VALUES ({marcadores})', linhas)
    conn.commit()


def obter_dimensoes_tora(conn: sqlite3.Connection) -> Optional[Dict]:
    """Comprimento/diâmetro mínimo da tora (tela Configurações), crus como
    salvos no banco — consumidos pelo nó "afilamento" (ver avaliar_grafo).
    None se a linha de configurações nem existir ainda (projeto novo, sem
    "Salvar" nunca rodado em Configurações) — mesmo tratamento de
    obter_config_financeiro logo abaixo.

    "usar_tabela_afilamento" (checkbox em Configurações, desligado por
    padrão): troca o cálculo exato do nó "afilamento" (grade fina/grossa
    reintegrada do zero pra cada árvore, ver _calcular_volumes_afilamento)
    por uma busca numa tabela pré-calculada por altura arredondada pro
    passo de 0,1m mais próximo (ver _obter_tabela_afilamento) — bem mais
    rápido em lotes com muitos cenários (a tabela é calculada uma vez só
    por classe diamétrica e reaproveitada por todo cenário/linha), à
    custa de uma pequena aproximação: a altura de cada árvore entra na
    equação arredondada pro grid, não exata (a equação usa H como
    variável, não só como limite de integração — arredondar muda um
    pouco o formato da curva calculada, não só onde ela para)."""
    linha = conn.execute(
        "SELECT comprimento_tora, diametro_minimo_tora, usar_tabela_afilamento "
        "FROM configuracoes WHERE id = 1"
    ).fetchone()
    if linha is None:
        return None
    comprimento_tora, diametro_minimo_tora, usar_tabela_afilamento = linha
    return {
        "comprimento_tora": comprimento_tora, "diametro_minimo_tora": diametro_minimo_tora,
        "usar_tabela_afilamento": bool(usar_tabela_afilamento),
    }


def obter_config_financeiro(conn: sqlite3.Connection) -> Optional[Dict]:
    """Taxa de desconto/Ano de referência/Base do período/PIS/COFINS/FUNRURAL (tela
    Configurações), crus como salvos no banco (percentuais como 8.0 pra
    8%) — consumidos pelo nó "vpl_sortimento" (ver avaliar_grafo).
    "base_periodo_vpl" ("ano_referencia", padrão, ou "ano_zero" — ver
    BASES_PERIODO_VPL) escolhe contra o que "n" (o expoente do desconto)
    é contado ali. None se a linha de configurações nem existir ainda
    (projeto novo, sem "Salvar" nunca rodado em Configurações)."""
    linha = conn.execute(
        "SELECT taxa_desconto, ano_referencia, base_periodo_vpl, pis, cofins, funrural "
        "FROM configuracoes WHERE id = 1"
    ).fetchone()
    if linha is None:
        return None
    taxa_desconto, ano_referencia, base_periodo_vpl, pis, cofins, funrural = linha
    return {
        "taxa_desconto": taxa_desconto, "ano_referencia": ano_referencia,
        "base_periodo_vpl": base_periodo_vpl or "ano_referencia", "pis": pis, "cofins": cofins,
        "funrural": funrural,
    }


def obter_custos_colheita(conn: sqlite3.Connection) -> Dict[int, Dict]:
    """Custos de colheita cadastrados (tela Configurações — nome + Custo
    Hora Máquina/Disponibilidade Mecânica/Eficiência Operacional, mais a
    produtividade por classe diamétrica cadastrada à parte), já aninhados
    num único dict — consumidos pelo nó "custo_colheita" (ver avaliar_grafo/
    _custo_efetivo_colheita_da_classe). id -> {"nome", "custo_hora_maquina",
    "disponibilidade_mecanica", "eficiencia_operacional",
    "produtividade": {classe: valor}}."""
    custos = {}
    for id_, nome, custo_hora_maquina, disponibilidade_mecanica, eficiencia_operacional in conn.execute(
        "SELECT id, nome, custo_hora_maquina, disponibilidade_mecanica, eficiencia_operacional "
        "FROM custos_colheita ORDER BY nome"
    ).fetchall():
        custos[id_] = {
            "nome": nome, "custo_hora_maquina": custo_hora_maquina,
            "disponibilidade_mecanica": disponibilidade_mecanica,
            "eficiencia_operacional": eficiencia_operacional, "produtividade": {},
        }
    for custo_colheita_id, classe, produtividade in conn.execute(
        "SELECT custo_colheita_id, classe, produtividade FROM custo_colheita_produtividade"
    ).fetchall():
        if custo_colheita_id in custos:
            custos[custo_colheita_id]["produtividade"][classe] = produtividade
    return custos


def obter_custos_formacao(conn: sqlite3.Connection) -> Dict[int, float]:
    """Custos de formação florestal cadastrados (tela Configurações —
    nome + ano [idade do povoamento, não ano-calendário] + custo R$/ha),
    somados por idade (2+ custos no mesmo ano somam) — consumidos pelo nó
    "custo_formacao" (ver avaliar_grafo). {idade: custo_total_no_ano}."""
    custos_por_idade: Dict[int, float] = {}
    for ano, custo in conn.execute(
        "SELECT ano, custo FROM custos_formacao WHERE ano IS NOT NULL AND custo IS NOT NULL"
    ):
        idade = round(float(ano))
        custos_por_idade[idade] = custos_por_idade.get(idade, 0.0) + float(custo)
    return custos_por_idade


def grafo_tem_no_custo_formacao(grafos: List[dict]) -> bool:
    """True se algum dos grafos (cada um {"nos": {...}, "conexoes": [...]},
    ver salvar_construtor/obter_construtor) tiver pelo menos 1 nó
    "custo_formacao" — usado por quem chama avaliar_grafo pra decidir se
    vale a pena rodar sincronizar_linhas_formacao antes (custa 1+ SELECT/
    DELETE/INSERT em simulacao_talhao_idade; sem nó nenhum desse tipo, a
    idade <= 0 não tem por que ser sincronizada)."""
    return any(
        no.get("tipo") == "custo_formacao"
        for grafo in grafos for no in grafo.get("nos", {}).values()
    )


def sincronizar_linhas_formacao(
    conn: sqlite3.Connection, tabela: str, coluna_talhao: Optional[str],
    custos_formacao: Dict[int, float],
) -> int:
    """Garante que `tabela` (sempre simulacao_talhao_idade, com ou sem
    sufixo de cenário) tenha uma linha por (talhão, idade) pra cada idade
    <= 0 REALMENTE cadastrada em `custos_formacao` (ver
    obter_custos_formacao) — custo de formação florestal incorrido ANTES
    da 1ª idade simulada (idade_simulada=1; gerar_populacao só gera
    1..idade_maxima_manejo). Chamada ANTES de avaliar_grafo sempre que o
    grafo tiver um nó "custo_formacao" (ver grafo_tem_no_custo_formacao)
    — depois disso, o próprio nó calcula o valor de custo_formacao dessas
    linhas do jeito normal (mesma conta que já faz pras linhas reais,
    casando idade_simulada com `custos_formacao`); esta função só garante
    que as LINHAS existem, não escreve custo nenhum nelas.

    Idempotente: sempre apaga as linhas de idade_simulada <= 0 já
    existentes e reinsere do zero a cada chamada — evita duplicar a cada
    reaplicação (aplicar_construtores_salvos roda de novo toda "Gerar
    simulação", e "Salvar construtor" também sincroniza antes de avaliar
    — ver screens/construtor_variaveis.py). talhão/idade_simulada/
    ano_simulado E toda coluna ORIGINAL da Base IFC ByTalhao (mesmas que
    gerar_populacao copia sem alteração pra cada linha real — ex: área do
    talhão, região; qualquer coluna que ainda exista em `tabela`, exceto a
    própria coluna de talhão, já tratada à parte) vêm preenchidos nas
    linhas novas, repetindo o valor daquele talhão — é informação do
    TALHÃO, não da idade, então não tem por que ficar em branco só porque
    não existe simulação de verdade nessas idades. O resto (DAP,
    distribuição, volume por classe, evento_manejo... — tudo que É
    calculado por idade simulada) continua NULL. As outras partes do app
    que leem simulacao_talhao_idade pra gráfico/distribuição/MIP já
    ignoram essas linhas sozinhas (forma/escala e evento_manejo nulos
    nelas) — só dados_grafico_resultado precisou de um filtro explícito de
    idade_simulada >= 1 (ver lá).

    Não faz nada (devolve 0) se `tabela` não existir ainda, não tiver
    coluna "idade_simulada" (rodando sobre uma tabela que não é a
    população simulada — só faz sentido nela), `coluna_talhao` não
    estiver configurada, ou não houver custo de formação com idade <= 0
    cadastrado. Devolve quantas linhas foram inseridas.

    Reordena a tabela inteira por (talhão, idade_simulada) — com o mesmo
    id sequencial 1..N que gerar_populacao já deixa nesse formato — DEPOIS
    de inserir as linhas novas, não só elas: inserir só as novas no fim
    (INSERT com id = MAX(id)+1) as deixava fisicamente depois de TODAS as
    idades reais de TODOS os talhões, quebrando essa ordem (que várias
    outras partes do app dependem pra exportar/exibir em ordem sem um
    ORDER BY explícito — ver gerar_populacao/calcular_volume_por_
    sortimento, mesmo raciocínio "id é rowid, decide a ordem física").
    Reconstrói a tabela inteira (DROP + CREATE com o MESMO schema, lido de
    sqlite_master — preserva colunas que outros construtores já tenham
    adicionado via ALTER TABLE) em vez de tentar trocar id linha a linha
    (colidiria com outro id já em uso no meio do caminho)."""
    if not coluna_talhao:
        return 0
    linha_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
    ).fetchone()
    if linha_schema is None:
        return 0
    colunas = {d[0] for d in conn.execute(f'SELECT * FROM "{tabela}" LIMIT 0').description}
    if "idade_simulada" not in colunas or coluna_talhao not in colunas:
        return 0

    # A distribuição referencia a população por FK. Ela precisa ser
    # invalidada ANTES até mesmo do DELETE das linhas de formação; fazer
    # isso apenas antes do DROP da população era tarde demais quando uma
    # distribuição existente também continha essas idades <= 0.
    # A sincronização pode ainda reordenar e reatribuir todos os ids da
    # população, portanto a distribuição antiga não poderia ser mantida.
    if tabela.startswith(simulacao.TABELA_POPULACAO):
        sufixo = tabela[len(simulacao.TABELA_POPULACAO):]
        conn.execute(f'DROP TABLE IF EXISTS "{simulacao.TABELA_DISTRIBUICAO}{sufixo}"')

    conn.execute(f'DELETE FROM "{tabela}" WHERE idade_simulada <= 0')

    idades_negativas = sorted(idade for idade in custos_formacao if idade <= 0)
    if not idades_negativas:
        conn.commit()
        return 0

    # Colunas originais da Base IFC ByTalhao (mesmas que gerar_populacao
    # copia sem alteração pra cada linha real — ver colunas_originais em
    # core/simulacao.py:_preparar_baseline_populacao) que ainda existem em
    # `tabela` — repetidas nas linhas sintéticas também (ver docstring).
    # `coluna_talhao` sai da lista pra não duplicar (já é a 1ª coluna do
    # INSERT abaixo). sqlite3.OperationalError (base_ifc_talhao não existe
    # mais) degrada pra lista vazia — mesmo efeito de antes desta função
    # existir, só talhão/idade_simulada/ano_simulado preenchidos.
    try:
        colunas_base_ifc = [
            c for c in simulacao.colunas_base_ifc_talhao(conn)
            if c in colunas and c != coluna_talhao
        ]
    except sqlite3.OperationalError:
        colunas_base_ifc = []

    # ano_plantio (derivado de volta: ano_simulado - idade_simulada) e o
    # valor de cada coluna de `colunas_base_ifc`, de qualquer linha real
    # restante de cada talhão — mesmo valor em toda idade positiva dele,
    # por construção (ver gerar_populacao). ano_plantio None se a data de
    # plantio não foi mapeada (linha nova entra com ano_simulado NULL).
    colunas_consulta = [coluna_talhao, "ano_simulado", "idade_simulada"] + colunas_base_ifc
    colunas_consulta_sql = ", ".join(f'"{c}"' for c in colunas_consulta)
    dados_por_talhao: Dict[str, Tuple[Optional[float], tuple]] = {}
    for linha in conn.execute(
        f'SELECT {colunas_consulta_sql} FROM "{tabela}" WHERE "{coluna_talhao}" IS NOT NULL'
    ):
        talhao, ano_simulado, idade_simulada = linha[0], linha[1], linha[2]
        if talhao in dados_por_talhao:
            continue
        ano_plantio = (
            None if ano_simulado is None or idade_simulada is None
            else float(ano_simulado) - float(idade_simulada))
        dados_por_talhao[talhao] = (ano_plantio, linha[3:])

    if not dados_por_talhao:
        conn.commit()
        return 0

    linhas_inserir = [
        (talhao, idade, None if ano_plantio is None else ano_plantio + idade, *valores_base_ifc)
        for talhao, (ano_plantio, valores_base_ifc) in dados_por_talhao.items()
        for idade in idades_negativas
    ]
    colunas_insert_sql = ", ".join(
        f'"{c}"' for c in [coluna_talhao, "idade_simulada", "ano_simulado"] + colunas_base_ifc)
    marcadores_insert = ", ".join("?" for _ in range(3 + len(colunas_base_ifc)))
    conn.executemany(
        f'INSERT INTO "{tabela}" ({colunas_insert_sql}) VALUES ({marcadores_insert})',
        linhas_inserir,
    )

    # Reordena a tabela inteira (real + sintéticas recém-inseridas) por
    # (talhão, idade_simulada), com id 1..N sequencial nessa ordem — ver
    # docstring. DROP + CREATE com o schema exato de agora (colunas de
    # outros construtores incluídas) evita ter que declarar tipo por
    # coluna igual gerar_populacao faz na criação original.
    df_completo = pd.read_sql_query(f'SELECT * FROM "{tabela}"', conn)
    df_completo = df_completo.sort_values(
        [coluna_talhao, "idade_simulada"], kind="stable").reset_index(drop=True)
    df_completo["id"] = np.arange(1, len(df_completo) + 1)

    conn.execute(f'DROP TABLE "{tabela}"')
    conn.execute(linha_schema[0])
    nomes_coluna = list(df_completo.columns)
    colunas_sql = ", ".join(f'"{c}"' for c in nomes_coluna)
    marcadores = ", ".join("?" for _ in nomes_coluna)
    linhas_completas = [
        tuple(None if pd.isna(v) else v for v in linha)
        for linha in df_completo.itertuples(index=False, name=None)
    ]
    conn.executemany(
        f'INSERT INTO "{tabela}" ({colunas_sql}) VALUES ({marcadores})', linhas_completas)

    conn.commit()
    return len(linhas_inserir)


def _sincronizar_linhas_formacao_em_memoria(
    df: pd.DataFrame, coluna_talhao: str, custos_formacao: Dict[int, float], colunas_base_ifc: List[str],
) -> pd.DataFrame:
    """Equivalente em memória de sincronizar_linhas_formacao (ver lá pra
    explicação completa) — usada por aplicar_construtores_em_memoria
    quando algum construtor ativo tem nó "custo_formacao", pra também
    poder rodar no pipeline em memória/paralelo (ver core/simulacao.py:
    calcular_cenario_em_memoria/_ThreadGerarLote) em vez de cair pro
    caminho antigo, via banco.

    Mesma ideia, sobre o DataFrame em vez da tabela: acrescenta, por
    talhão, uma linha sintética pra cada idade <= 0 REALMENTE cadastrada
    em `custos_formacao` — talhão/idade_simulada/ano_simulado + cada
    coluna de `colunas_base_ifc` (as que existirem em `df`) repetidos do
    talhão (1ª linha dele em `df`, mesmo critério de "qualquer linha
    real restante" da versão via banco); resto fica NaN (não seta essas
    colunas nas linhas novas — pandas preenche sozinho ao concatenar).
    Reordena por (talhão, idade_simulada) e reatribui "id" sequencial
    1..N no final, igual a versão via banco (outras partes do app
    dependem dessa ordem física, ver docstring de sincronizar_linhas_
    formacao)."""
    idades_negativas = sorted(idade for idade in custos_formacao if idade <= 0)
    if not idades_negativas or df.empty or coluna_talhao not in df.columns:
        return df

    colunas_base_presentes = [c for c in colunas_base_ifc if c in df.columns and c != coluna_talhao]
    primeira_por_talhao = df.drop_duplicates(subset=[coluna_talhao], keep="first")

    linhas_sinteticas = []
    for _, linha in primeira_por_talhao.iterrows():
        talhao = linha[coluna_talhao]
        if pd.isna(talhao):
            continue
        ano_simulado_ref = linha.get("ano_simulado")
        idade_simulada_ref = linha.get("idade_simulada")
        ano_plantio = None
        if pd.notna(ano_simulado_ref) and pd.notna(idade_simulada_ref):
            ano_plantio = float(ano_simulado_ref) - float(idade_simulada_ref)
        for idade in idades_negativas:
            nova = {coluna_talhao: talhao, "idade_simulada": idade}
            nova["ano_simulado"] = None if ano_plantio is None else ano_plantio + idade
            for coluna in colunas_base_presentes:
                nova[coluna] = linha[coluna]
            linhas_sinteticas.append(nova)

    if not linhas_sinteticas:
        return df

    df_completo = pd.concat([df, pd.DataFrame(linhas_sinteticas)], ignore_index=True, sort=False)
    df_completo = df_completo.sort_values(
        [coluna_talhao, "idade_simulada"], kind="stable").reset_index(drop=True)
    df_completo["id"] = np.arange(1, len(df_completo) + 1)
    return df_completo


# ==========================================================
# REAPLICAÇÃO AUTOMÁTICA (chamada depois de regenerar uma tabela)
# ==========================================================

def aplicar_construtores_salvos(
    conn: sqlite3.Connection, tabela: str, tabela_origem: Optional[str] = None,
    idade_corte_raso: Optional[int] = None,
) -> Dict:
    """Reaplica (como colunas, direto em `tabela`) todos os construtores
    salvos e ativos (`ativo`, ver definir_ativo) cujo `tabela_origem`
    (campo salvo no construtor, sempre um nome canônico — ver
    TABELAS_ORIGEM em construtor_variaveis.py, não existe conceito de
    "por cenário" lá) bate com `tabela_origem` — um construtor desativado
    continua salvo, só para de rodar sozinho.

    `idade_corte_raso` (opcional, alimenta o nó "vet_sortimento"): se
    omitido, cai pra `simulacao.obter_idade_corte_raso(conn, tabela)` —
    passe explicitamente ao reaplicar construtores pra um cenário do lote
    de "Múltiplos cenários"/"Grade automática" (ver
    app/screens/simulacao.py:_gerar_uma_simulacao), onde `tabela` (a
    SUFIXADA) pode nem existir ainda no banco no momento desta chamada —
    o valor certo (configuracao["idade_corte_raso"]) já está em memória,
    não precisa de leitura nenhuma.

    `tabela_origem` (o nome pra COMPARAR contra o construtor salvo) é
    opcional e, se omitido, usa o próprio `tabela` — cobre o caso normal
    (cenário único, sem sufixo, onde "onde ler/gravar" e "com o que
    comparar" são o mesmo nome). Já o modo "Múltiplos cenários" (ver
    app/screens/simulacao.py:_gerar_uma_simulacao) lê/grava numa tabela
    SUFIXADA ("simulacao_talhao_idade__cenario3") mas o construtor foi
    salvo apontando pro nome canônico sem sufixo — sem separar os dois,
    a comparação `tabela_origem == tabela` nunca batia pra nenhum
    cenário do lote, e nenhum construtor era reaplicado neles (só no
    modo de cenário único, onde os dois nomes coincidem por acaso).

    Nunca levanta erro por um construtor individual falhar — só reporta
    em "falhas", pra não travar quem chamou (ex: geração da simulação)
    por causa de um construtor com problema. Retorna {"executados": int,
    "falhas": [str, ...], "tempos": [str, ...]} — "tempos" só lista
    construtores que passaram de 1s (ver _LIMIAR_TEMPO_RELATADO), com os
    nós mais lentos daquele construtor (mesmo diagnóstico do Construtor
    de Variáveis, "Prévia"/"Salvar construtor") — diagnóstico pra "Gerar
    simulação" lenta."""
    if tabela_origem is None:
        tabela_origem = tabela
    construtores = [
        c for c in listar_construtores(conn) if c["tabela_origem"] == tabela_origem and c["ativo"]
    ]
    if not construtores:
        return {"executados": 0, "falhas": []}

    existe = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
    ).fetchone()
    if existe is None:
        return {"executados": 0, "falhas": []}

    custos_formacao = obter_custos_formacao(conn)
    # Antes de ler `df": se algum construtor ativo tiver um nó "custo_formacao",
    # garante que as linhas de idade <= 0 (custo de formação anterior ao
    # plantio) existem em `tabela` — ver sincronizar_linhas_formacao. Sem
    # isso, o nó só calcularia custo pras idades 1..idade_maxima_manejo já
    # simuladas, nunca pras anteriores ao plantio.
    if grafo_tem_no_custo_formacao([c["grafo"] for c in construtores]):
        sincronizar_linhas_formacao(
            conn, tabela, simulacao.obter_coluna_talhao(conn), custos_formacao)

    df = pd.read_sql_query(f'SELECT * FROM "{tabela}"', conn)
    if "id" not in df.columns or df.empty:
        return {"executados": 0, "falhas": []}

    try:
        classes_diametricas = simulacao.obter_classes_diametricas(conn)
    except ValueError:
        # Não configurado ainda — só um "modelo" com entrada ligada num nó
        # "Classe Diamétrica" (se houver algum construtor usando isso)
        # reporta erro individualmente; o resto do grafo continua sendo
        # avaliado normalmente.
        classes_diametricas = None

    # Mesmo tratamento de ausência que classes_diametricas acima — só afeta
    # um nó "receita_sortimento"/"rendimento_sortimento"/"vpl_sortimento"/
    # "vet_sortimento" (se houver algum construtor usando isso).
    sortimentos = conn.execute(
        "SELECT nome, limite_inferior, limite_superior, rendimento, preco, preco_pe "
        "FROM sortimentos ORDER BY limite_inferior, nome"
    ).fetchall()
    config_financeiro = obter_config_financeiro(conn)
    if idade_corte_raso is None:
        idade_corte_raso = simulacao.obter_idade_corte_raso(conn, tabela)
    dimensoes_tora = obter_dimensoes_tora(conn)
    custos_colheita = obter_custos_colheita(conn)
    tipo_normalizacao_weibull = simulacao.obter_tipo_normalizacao_weibull(conn)

    falhas = []
    tempos = []
    executados = 0
    for construtor in construtores:
        debug_tempos = {}
        inicio = time.perf_counter()
        duracao_gravacao = None
        try:
            valores, erros = avaliar_grafo(
                df, construtor["grafo"]["nos"], construtor["grafo"]["conexoes"], classes_diametricas,
                sortimentos, config_financeiro, idade_corte_raso, dimensoes_tora, custos_colheita,
                tipo_normalizacao_weibull=tipo_normalizacao_weibull, custos_formacao=custos_formacao,
                debug_tempos=debug_tempos)
            saidas = saidas_nomeadas(construtor["grafo"]["nos"], valores)
            if not saidas:
                if erros:
                    falhas.append(f"\"{construtor['nome']}\": {'; '.join(erros)}")
                continue
            inicio_gravacao = time.perf_counter()
            gravar_saidas_como_colunas(conn, tabela, df, saidas)
            duracao_gravacao = time.perf_counter() - inicio_gravacao
            executados += 1
            if erros:
                falhas.append(f"\"{construtor['nome']}\" (parcial, alguns nós não calculados): "
                               f"{'; '.join(erros)}")
        except Exception as e:
            falhas.append(f"\"{construtor['nome']}\": {e}")
        finally:
            duracao = time.perf_counter() - inicio
            if duracao >= _LIMIAR_TEMPO_RELATADO:
                tempos.append(_resumo_tempo_construtor(construtor, duracao, debug_tempos, duracao_gravacao))

    return {"executados": executados, "falhas": falhas, "tempos": tempos}


def aplicar_construtores_em_memoria(
    conn: sqlite3.Connection, df: pd.DataFrame, tabela_origem: str,
    classes_diametricas, sortimentos, config_financeiro, idade_corte_raso,
    dimensoes_tora, custos_colheita, tipo_normalizacao_weibull, custos_formacao,
) -> Optional[Tuple[pd.DataFrame, Dict]]:
    """Equivalente de aplicar_construtores_salvos, mas sem tocar o banco:
    mescla a saída de todos os construtores ativos no DataFrame num só
    `pd.concat(axis=1)` (em vez de um `df_saida[nome] = serie` por
    coluna — fragmenta o DataFrame e degrada bastante com muitas colunas,
    ex: uma saída por classe diamétrica), e em vez de
    gravar_saidas_como_colunas (ALTER+tabela TEMP+UPDATE) — usado pelo
    pipeline de geração em memória (ver
    app/screens/simulacao.py:_gerar_uma_simulacao), que só grava tudo no
    banco uma vez no final, depois de todas as etapas calculadas.

    `avaliar_grafo` sempre vê `df` (a população recém-gerada, sem nenhuma
    saída de construtor ainda) em TODAS as iterações — igual
    aplicar_construtores_salvos, que também lê a tabela uma vez só antes
    do laço e nunca a atualiza no meio dele; um construtor B não enxerga a
    saída de um construtor A rodado antes dele na mesma chamada, mesmo
    que ambos estejam ativos pra mesma `tabela_origem`. As saídas se
    acumulam à parte, em `df_saida` (cópia de `df`), que é o que volta no
    final.

    Se algum construtor ativo tiver um nó "custo_formacao", sincroniza as
    linhas sintéticas de idade <= 0 (custo de formação anterior ao
    plantio) direto no DataFrame primeiro (ver
    _sincronizar_linhas_formacao_em_memoria — equivalente em memória de
    sincronizar_linhas_formacao, usada por aplicar_construtores_salvos
    pelo caminho via banco) — sem isso o nó só calcularia custo pras
    idades 1..idade_maxima_manejo já simuladas, nunca pras anteriores ao
    plantio. Só devolve None se `coluna_talhao` (tela Configurações,
    "Coluna de talhão") ainda não estiver configurada — sincronizar_
    linhas_formacao também não faria nada nesse caso, mas aqui precisa
    saber ANTES de decidir se cai pro caminho antigo.

    Devolve (df_saida, {"executados", "falhas", "tempos",
    "colunas_adicionadas"}) — mesmo formato de aplicar_construtores_salvos
    mais "colunas_adicionadas" (lista de nomes de coluna mesclados em
    `df_saida` ALÉM das já presentes em `df` — comparação por minúsculas,
    ver abaixo), exceto que "tempos" só reporta a fase de cálculo (a fase
    "gravação no banco" por construtor deixou de existir aqui — a gravação
    agora é uma vez só, de tudo, feita por quem chamou depois que o
    pipeline inteiro termina). "colunas_adicionadas" é o que quem chamou
    precisa pra saber que colunas incluir no CREATE TABLE/ALTER TABLE na
    hora de persistir (ver app/core/simulacao.py:_persistir_populacao/
    _garantir_tabelas_lote, parâmetro `colunas_extra`) — `df` pode ter
    colunas auxiliares (ex: "__idade_raleio_final__") que não são saída de
    nenhum construtor nem fazem parte do schema "nativo" da população;
    usar `set(df_saida.columns) - set(df.columns)` no lugar seria
    impreciso por isso.

    Uma saída de construtor cujo nome só difere no CASE de uma coluna já
    presente em `df` (ex: um nó "VOLUME" quando a Base IFC já tem
    "Volume") é tratada como a MESMA coluna, não uma nova — mesma regra
    de gravar_saidas_como_colunas (SQLite não diferencia maiúsculas de
    minúsculas em nome de coluna): sobrescreve o valor da coluna
    existente (mantendo o case ORIGINAL dela) em vez de entrar em
    "colunas_adicionadas". Sem isso, o DataFrame ganharia duas colunas
    com o mesmo nome (pandas aceita rótulos duplicados) e o CREATE
    TABLE/ALTER TABLE na persistência quebraria com "duplicate column
    name"."""
    construtores = [
        c for c in listar_construtores(conn) if c["tabela_origem"] == tabela_origem and c["ativo"]
    ]
    if not construtores:
        return df, {"executados": 0, "falhas": [], "tempos": [], "colunas_adicionadas": []}

    if grafo_tem_no_custo_formacao([c["grafo"] for c in construtores]):
        coluna_talhao = simulacao.obter_coluna_talhao(conn)
        if not coluna_talhao:
            return None
        try:
            colunas_base_ifc = simulacao.colunas_base_ifc_talhao(conn)
        except sqlite3.OperationalError:
            colunas_base_ifc = []
        df = _sincronizar_linhas_formacao_em_memoria(
            df, coluna_talhao, custos_formacao, colunas_base_ifc)

    if "id" not in df.columns or df.empty:
        return df, {"executados": 0, "falhas": [], "tempos": [], "colunas_adicionadas": []}

    # Acumula as saídas de TODOS os construtores num dict só (nome ->
    # Series) e mescla via UM `pd.concat(axis=1)` no final, em vez de um
    # `df_saida[nome] = serie` por coluna — um grafo com saída por classe
    # diamétrica (ex: Afilamento, 2 saídas × 100 classes) insere colunas
    # uma a uma de outra forma, o que fragmenta o DataFrame e degrada pra
    # O(colunas²) (pandas avisa "DataFrame is highly fragmented" — testado
    # na prática, ver diagnóstico desta otimização). Nome repetido entre
    # construtores: o de um construtor MAIS TARDE na lista sobrescreve o
    # de um mais cedo (mesmo critério do caminho antigo, gravar_saidas_
    # como_colunas/ALTER TABLE — a última gravação vence).
    todas_saidas: Dict[str, pd.Series] = {}
    falhas = []
    tempos = []
    executados = 0
    for construtor in construtores:
        debug_tempos = {}
        inicio = time.perf_counter()
        try:
            valores, erros = avaliar_grafo(
                df, construtor["grafo"]["nos"], construtor["grafo"]["conexoes"], classes_diametricas,
                sortimentos, config_financeiro, idade_corte_raso, dimensoes_tora, custos_colheita,
                tipo_normalizacao_weibull=tipo_normalizacao_weibull, custos_formacao=custos_formacao,
                debug_tempos=debug_tempos)
            saidas = saidas_nomeadas(construtor["grafo"]["nos"], valores)
            if not saidas:
                if erros:
                    falhas.append(f"\"{construtor['nome']}\": {'; '.join(erros)}")
                continue
            verificar_colisao_saidas(saidas)
            todas_saidas.update(saidas)
            executados += 1
            if erros:
                falhas.append(f"\"{construtor['nome']}\" (parcial, alguns nós não calculados): "
                               f"{'; '.join(erros)}")
        except Exception as e:
            falhas.append(f"\"{construtor['nome']}\": {e}")
        finally:
            duracao = time.perf_counter() - inicio
            if duracao >= _LIMIAR_TEMPO_RELATADO:
                tempos.append(_resumo_tempo_construtor(construtor, duracao, debug_tempos, None))

    # SQLite não diferencia maiúsculas de minúsculas em nome de coluna
    # (mesmo motivo de verificar_colisao_saidas/gravar_saidas_como_colunas,
    # ver ali) — uma saída "VOLUME" que só difere no case de uma coluna já
    # existente em `df` (ex: "Volume" vindo da Base IFC) É a mesma coluna
    # pro banco, não uma nova: tratada como new sem essa checagem, um
    # `pd.concat` deixaria as DUAS coexistirem no DataFrame com o MESMO
    # nome (pandas aceita rótulos de coluna duplicados), e a persistência
    # (CREATE TABLE/ALTER TABLE ADD COLUMN, ver core/simulacao.py:
    # _persistir_populacao/_garantir_tabelas_lote) quebraria com
    # "duplicate column name" na hora de gravar — a mesma proteção que
    # gravar_saidas_como_colunas já tem pro caminho via banco, replicada
    # aqui pro caminho em memória. Sobrescreve a coluna existente (mesmo
    # nome, case ORIGINAL de `df`) em vez de criar uma segunda.
    colunas_por_minusculo = {c.lower(): c for c in df.columns}
    novas_saidas = {}
    saidas_sobrescritas = {}
    for nome, serie in todas_saidas.items():
        nome_existente = colunas_por_minusculo.get(nome.lower())
        if nome_existente is not None:
            saidas_sobrescritas[nome_existente] = serie
        else:
            novas_saidas[nome] = serie

    if novas_saidas:
        df_saida = pd.concat([df, pd.DataFrame(novas_saidas, index=df.index)], axis=1)
    else:
        df_saida = df.copy()
    for nome_existente, serie in saidas_sobrescritas.items():
        df_saida[nome_existente] = serie

    return df_saida, {
        "executados": executados, "falhas": falhas, "tempos": tempos,
        "colunas_adicionadas": list(novas_saidas.keys()),
    }


def _resumo_tempo_construtor(
    construtor: dict, duracao_total: float, debug_tempos: Dict[int, float],
    duracao_gravacao: Optional[float],
) -> str:
    """Monta a linha de diagnóstico de UM construtor pro relatório de
    aplicar_construtores_salvos ("Gerar simulação" lenta) — diferente de
    só listar os nós mais lentos (ver construtor_variaveis.py:
    _resumo_tempos, mesma ideia pra Prévia/Salvar construtor), separa
    explicitamente 3 fases: cálculo dos nós (soma de `debug_tempos`),
    gravação das colunas no banco (`duracao_gravacao` — ALTER/INSERT/
    UPDATE em `gravar_saidas_como_colunas`, potencialmente MUITAS
    instruções UPDATE se o grafo tiver várias saídas por classe
    diamétrica com muitas classes configuradas) e "outros" (o que sobra
    de `duracao_total` sem cair em nenhuma das duas — ordenação do grafo,
    resolução das colunas-fonte). Sem essa separação, um grafo com
    dezenas de saídas por classe podia parecer "rápido" pelos nós (cada
    um individualmente barato) escondendo que gravar centenas de colunas
    de volta na tabela é o que realmente pesa."""
    nos_do_construtor = construtor["grafo"]["nos"]
    soma_nos = sum(debug_tempos.values())
    itens_ordenados = sorted(debug_tempos.items(), key=lambda item: item[1], reverse=True)
    mais_lentos = itens_ordenados[:3]
    partes_no = [
        f"{nos_do_construtor[no_id]['rotulo']} ({tempo:.2f}s)"
        for no_id, tempo in mais_lentos if tempo >= 0.05]
    restantes = itens_ordenados[3:]
    tempo_restante_nos = sum(tempo for _, tempo in restantes)
    if restantes and tempo_restante_nos >= 0.05:
        partes_no.append(f"outros {len(restantes)} nó(s) ({tempo_restante_nos:.2f}s)")

    partes_fase = [f"cálculo: {soma_nos:.2f}s"]
    if duracao_gravacao is not None:
        partes_fase.append(f"gravação no banco: {duracao_gravacao:.2f}s")
    outros = duracao_total - soma_nos - (duracao_gravacao or 0.0)
    if outros >= 0.05:
        partes_fase.append(f"outros: {outros:.2f}s")

    texto = f"\"{construtor['nome']}\": {duracao_total:.2f}s total (" + ", ".join(partes_fase) + ")"
    if partes_no:
        texto += " — nós mais lentos: " + "; ".join(partes_no)
    return texto
