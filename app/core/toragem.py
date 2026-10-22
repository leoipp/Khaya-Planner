"""Perfil de taper e traçamento por ponta fina; unidades cm, m e m³."""
from math import floor, isfinite, pi
import re

import numpy as np

from .motor_modelos import avaliar_expressao_array


def _tracar_perfil(equacao, coeficientes, variaveis, dap, altura, comprimento,
                  diametro_minimo, toco=0.2, indicadores=None):
    if len(variaveis) != 3 or len(set(variaveis)) != 3:
        raise ValueError('O modelo precisa de 3 variáveis distintas, na ordem DAP, h, H.')
    valores = (dap, altura, comprimento, diametro_minimo, toco)
    if not all(isfinite(v) for v in valores) or min(dap, altura, comprimento) <= 0:
        raise ValueError('DAP, altura e comprimento devem ser positivos.')
    if not 0 <= toco < altura or diametro_minimo < 0:
        raise ValueError('Confira a altura do toco e o diâmetro mínimo.')
    if altura / comprimento > 1000:
        raise ValueError('Mais de 1.000 toretes por árvore; revise o comprimento configurado.')

    def avaliar(h):
        with np.errstate(all='ignore'):
            d = avaliar_expressao_array(equacao, coeficientes,
                {**dict(zip(variaveis, (dap, np.asarray(h), altura))), **(indicadores or {})})
        try:
            d = np.broadcast_to(d, np.shape(h)).copy()
        except ValueError:
            raise ValueError('O modelo não retornou um diâmetro por altura.')
        if not np.all(np.isfinite(d)) or np.any(d < -1e-8):
            raise ValueError('O taper gerou diâmetros negativos ou indefinidos no fuste. Revise o modelo.')
        return np.maximum(d, 0)

    # Não avalia h=H exato: alguns modelos têm singularidade na ponta.
    alturas = np.linspace(0, altura * (1 - 1e-9), 501)
    diametros = avaliar(alturas)
    toretes = []
    interrompido = False
    for i in range(floor((altura - toco + 1e-9) / comprimento)):
        inicio, fim = toco + i * comprimento, toco + (i + 1) * comprimento
        hs = np.linspace(inicio, min(fim, altura * (1 - 1e-9)), 81)
        ds = avaliar(hs)
        base, topo = float(ds[0]), float(ds[-1])
        ponta_fina = min(base, topo)
        # Um estreitamento no interior também limita tábuas retas.
        limitante = float(ds.min())
        comercial = not interrompido and limitante > 1e-8 and limitante + 1e-8 >= diametro_minimo
        interrompido = interrompido or not comercial
        volume = pi / 80000 * (base ** 2 + topo ** 2) * comprimento
        toretes.append(dict(numero=i+1, inicio=inicio, fim=fim, comprimento=comprimento,
                            diametro_base=base, diametro_topo=topo, ponta_fina=ponta_fina,
                            diametro_corte=limitante, volume=volume, comercial=comercial))
    ultimo = max((t['fim'] for t in toretes if t['comercial']), default=toco)
    return dict(alturas=alturas, diametros=diametros, toretes=toretes,
                altura=altura, toco=toco, sobra=altura-ultimo,
                perfil_nao_monotono=bool(np.any(np.diff(diametros) > 1e-6)))


def indicadores_casca(equacao, variaveis):
    """Seletores x4/x5 ou Tx1/Tx2 (também Tx_1/Tx_2)."""
    nomes = set(re.findall(r"\b[A-Za-z_]\w*\b", equacao or "")) | set(variaveis)
    encontrados = {}
    for nome in nomes:
        normal = nome.lower().replace('_', '')
        aliases = {'x4': 'tx1', 'x5': 'tx2', 'tx1': 'tx1', 'tx2': 'tx2'}
        if normal in aliases:
            encontrados[nome] = aliases[normal]
    if set(encontrados.values()) not in (set(), {'tx1', 'tx2'}):
        raise ValueError('Para três perfis, informe os dois seletores x4 e x5 (ou Tx1 e Tx2) na equação.')
    return encontrados


PAPEIS_PRODUTO = {'com_casca': 'Com casca', 'sem_casca': 'Sem casca', 'cerne': 'Cerne', 'outro': 'Outro produto'}


def configuracao_produtos_padrao(equacao, variaveis):
    """Sugestão editável; os nomes conhecidos só migram a convenção anterior."""
    try:
        seletores = indicadores_casca(equacao, variaveis)
    except ValueError:
        seletores = {}
    entradas = [v for v in variaveis if v not in seletores]
    geometricas = entradas[:3]
    dummies = list(dict.fromkeys([v for v in variaveis if v not in geometricas] + list(seletores)))
    codigos = [('com_casca', [0]*len(dummies))]
    if dummies:
        codigos.append(('sem_casca', [1] + [0]*(len(dummies)-1)))
    if len(dummies) > 1:
        codigos.append(('cerne', [1,1] + [0]*(len(dummies)-2)))
    if seletores and set(dummies) == set(seletores):
        codigos = [(papel, [0 if papel == 'com_casca' else
                    (1 if papel == 'cerne' or seletores.get(v) == 'tx1' else 0)
                    for v in dummies]) for papel, _ in codigos]
    return dict(geometricas=geometricas, dummies=dummies,
                produtos=[dict(nome=PAPEIS_PRODUTO[p], papel=p, valores=dict(zip(dummies, vs)))
                          for p,vs in codigos])


def validar_configuracao_produtos(config, variaveis):
    geometricas = config.get('geometricas', [])
    dummies = config.get('dummies', [])
    nomes = geometricas + dummies
    if len(geometricas) != 3 or len(set(nomes)) != len(nomes):
        raise ValueError('Selecione variáveis distintas para DAP, altura da seção e altura total; as demais são dummies.')
    if set(nomes) != set(variaveis):
        raise ValueError('Mapeie todas as variáveis do modelo: DAP, h, H ou dummy. O cadastro do modelo pode ter mudado.')
    produtos = config.get('produtos', [])
    if not produtos or len({p['nome'].strip() for p in produtos}) != len(produtos):
        raise ValueError('Cadastre ao menos um produto, com nomes distintos.')
    papeis = [p['papel'] for p in produtos if p['papel'] != 'outro']
    if len(set(papeis)) != len(papeis):
        raise ValueError('Use cada papel (com casca, sem casca ou cerne) em apenas um produto.')
    combinacoes = set()
    for p in produtos:
        if not p['nome'].strip() or p['papel'] not in PAPEIS_PRODUTO:
            raise ValueError('Informe o nome e o papel de cada produto.')
        valores = p.get('valores', {})
        if set(valores) != set(dummies) or not all(isfinite(v) for v in valores.values()):
            raise ValueError('Preencha um valor numérico finito para cada dummy de cada produto.')
        combinacao = tuple(valores[v] for v in dummies)
        if combinacao in combinacoes:
            raise ValueError('Cada produto deve ter uma combinação distinta de dummies.')
        combinacoes.add(combinacao)


def tracar_arvore(equacao, coeficientes, variaveis, dap, altura, comprimento,
                  diametro_minimo, toco=0.2, configuracao_produtos=None):
    # Ausência de configuração mantém estudos antigos. Configuração explícita
    # define os papéis independentemente dos nomes e da ordem das variáveis.
    if configuracao_produtos is None:
        seletores = indicadores_casca(equacao, variaveis)
        if not seletores:
            return _tracar_perfil(equacao, coeficientes, variaveis, dap, altura,
                                  comprimento, diametro_minimo, toco)
        if len([v for v in variaveis if v not in seletores]) != 3:
            raise ValueError('Configure as variáveis dummy e as combinações de produtos deste modelo.')
        config = configuracao_produtos_padrao(equacao, variaveis)
        disponiveis = list(dict.fromkeys(variaveis + list(seletores)))
    else:
        config = configuracao_produtos
        disponiveis = variaveis
    validar_configuracao_produtos(config, disponiveis)
    entradas = config['geometricas']
    perfis = {}
    metadados = {}
    for produto in config['produtos']:
        chave = produto['papel'] if produto['papel'] != 'outro' else 'produto:' + produto['nome']
        perfis[chave] = _tracar_perfil(equacao, coeficientes, entradas, dap, altura,
                                     comprimento, diametro_minimo, toco, produto['valores'])
        metadados[chave] = produto
    base = next((k for k in ('com_casca','sem_casca') if k in perfis), next(iter(perfis)))
    madeira = 'sem_casca' if 'sem_casca' in perfis else base
    principal = perfis[base]

    def validar_camadas(camadas):
        for menor,maior in [('cerne','sem_casca'),('sem_casca','com_casca'),('cerne','com_casca')]:
            if menor in camadas and maior in camadas and np.any(camadas[menor] > camadas[maior]+1e-6):
                raise ValueError('Modelo inconsistente: exige cerne ≤ sem casca ≤ com casca.')

    camadas = {k: p['diametros'] for k,p in perfis.items()}
    validar_camadas(camadas)
    principal['perfil_nao_monotono'] = any(p['perfil_nao_monotono'] for p in perfis.values())
    principal['perfis'] = camadas
    principal['produtos'] = {k: dict(nome=p['nome'], papel=p['papel']) for k,p in metadados.items()}
    principal['produto_base'] = base
    principal['produto_madeira'] = madeira
    for i, t in enumerate(principal['toretes']):
        hs = np.linspace(t['inicio'], min(t['fim'], altura * (1-1e-9)), 81)
        secoes = {}
        for chave, produto in metadados.items():
            contexto = dict(zip(entradas, (dap, hs, altura)))
            contexto.update(produto['valores'])
            secoes[chave] = np.maximum(np.broadcast_to(avaliar_expressao_array(equacao, coeficientes, contexto), hs.shape),0)
        validar_camadas(secoes)
        indice = int(np.argmin(secoes[madeira]))
        t['altura_corte'] = float(hs[indice])
        t['diametros_secao'] = {k: float(v[indice]) for k,v in secoes.items()}
        t['volumes'] = {k: v['toretes'][i]['volume'] for k,v in perfis.items()}
        t['diametro_madeira'] = t['diametros_secao'][madeira]
        if madeira == 'cerne':
            t['cerne_percentual'] = 100.
        elif 'cerne' in secoes:
            t['cerne_percentual'] = (min(100., 100*(t['diametros_secao']['cerne']/t['diametro_madeira'])**2)
                                      if t['diametro_madeira'] > 0 else 0)
    return principal
