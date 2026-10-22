"""Estimativa de corte paralelo em seção circular, em centímetros.

Busca determinística entre grades centradas e deslocadas, nas duas
orientações. É uma estimativa de aproveitamento, não um ótimo global.
"""
from math import floor, isfinite, pi, sqrt

import numpy as np


def encaixar(diametro, largura, espessura, serra_mm=3):
    valores = (diametro, largura, espessura, serra_mm)
    if not all(isfinite(v) for v in valores) or min(valores[:3]) <= 0 or serra_mm < 0:
        raise ValueError("Dimensões devem ser positivas e a serra não negativa.")
    if diametro / min(largura, espessura) > 600:
        raise ValueError("Dimensões geram uma grade muito grande; aumente a seção da tábua.")
    raio, folga = diametro / 2, serra_mm / 10
    melhor = []
    for w, h in ((largura, espessura), (espessura, largura)):
        passo = h + folga
        for fase in range(16):
            pecas = []
            deslocamento = fase * passo / 16
            for i in range(-int(diametro / passo) - 2, int(diametro / passo) + 3):
                y = i * passo + deslocamento - h / 2
                extremo = max(abs(y), abs(y + h))
                if extremo > raio:
                    continue
                meia_corda = sqrt(max(0, raio * raio - extremo * extremo))
                n = floor((2 * meia_corda + folga + 1e-9) / (w + folga))
                inicio = -(n * w + max(0, n - 1) * folga) / 2
                pecas.extend((inicio + j * (w + folga), y, w, h) for j in range(n))
            if len(pecas) > len(melhor):
                melhor = pecas
    return melhor


def fracao_cerne(peca, raio_cerne):
    """Integração por 80 faixas da interseção retângulo/círculo."""
    x, y, w, h = peca
    area = 0.0
    for i in range(80):
        yy = y + (i + .5) * h / 80
        if abs(yy) < raio_cerne:
            corda = sqrt(raio_cerne ** 2 - yy ** 2)
            area += max(0, min(x + w, corda) - max(x, -corda)) * h / 80
    return min(1.0, max(0.0, area / (w * h)))


def fracoes_cerne(pecas, raio_cerne):
    """Mesmas 80 faixas de fracao_cerne, calculadas em lotes NumPy."""
    if not pecas:
        return []
    resultados=[]
    faixas=(np.arange(80,dtype=float)+.5)[None,:]
    for inicio in range(0,len(pecas),256):
        x,y,w,h=np.asarray(pecas[inicio:inicio+256],dtype=float).T
        yy=y[:,None]+faixas*h[:,None]/80
        cordas=np.sqrt(np.maximum(0.,raio_cerne**2-yy**2))
        larguras=np.maximum(0.,np.minimum((x+w)[:,None],cordas)-np.maximum(x[:,None],-cordas))
        areas=np.where(np.abs(yy)<raio_cerne,larguras*h[:,None]/80,0.)
        # Acumulação na mesma ordem da rotina escalar, inclusive nos limites de classe.
        fracoes=np.cumsum(areas,axis=1)[:,-1]/(w*h)
        resultados.extend(np.clip(fracoes,0.,1.).tolist())
    return resultados


def simular(diametro, largura, espessura, comprimento, comprimento_tora, cerne, serra_mm,
            rendimento_serraria=None, volume_tora_m3=None):
    if rendimento_serraria is not None and (
            not isfinite(rendimento_serraria) or not 0 <= rendimento_serraria <= 100):
        raise ValueError("Rendimento da serraria deve estar entre 0 e 100%.")
    if not all(isfinite(v) for v in (comprimento, comprimento_tora, cerne)):
        raise ValueError("Valores inválidos.")
    if comprimento <= 0 or comprimento_tora <= 0 or not 0 <= cerne <= 100:
        raise ValueError("Informe comprimentos positivos e cerne entre 0 e 100%.")
    pecas = encaixar(diametro, largura, espessura, serra_mm)
    # Reserva corte transversal somente entre peças, sem exigir sobra final.
    n = floor((comprimento_tora + serra_mm / 1000 + 1e-10) /
              (comprimento + serra_mm / 1000))
    if n == 0:
        pecas = []
    raio_cerne = diametro / 2 * sqrt(cerne / 100)
    fracoes = [fracao_cerne(p, raio_cerne) for p in pecas]
    quantidade = len(pecas) * n
    volume = quantidade * largura * espessura / 10000 * comprimento
    volume_tora = pi * (diametro / 200) ** 2 * comprimento_tora
    if volume_tora_m3 is not None:
        if not isfinite(volume_tora_m3) or volume_tora_m3 <= 0:
            raise ValueError("Volume do torete deve ser positivo.")
        volume_tora = volume_tora_m3
    # Referência volumétrica do processo, e não um desconto percentual
    # sobre o rendimento geométrico (35% de 71% não significa 35% da tora).
    volume_unitario = largura * espessura / 10000 * comprimento
    quantidade_prevista = quantidade if rendimento_serraria is None else min(
        quantidade, floor(volume_tora * rendimento_serraria / 100 / volume_unitario + 1e-9))
    volume_previsto = quantidade_prevista * volume_unitario
    return dict(fracoes_cerne=fracoes, pecas=pecas, quantidade=quantidade, longitudinais=n, volume_tora=volume_tora,
                quantidade_prevista=quantidade_prevista, volume_previsto=volume_previsto,
                rendimento_previsto=100 * volume_previsto / volume_tora,
                volume=volume, rendimento=100 * volume / volume_tora,
                cerne=sum(f > .5 + 1e-6 for f in fracoes) * n,
                alburno=sum(f < .5 - 1e-6 for f in fracoes) * n,
                equilibradas=sum(abs(f - .5) <= 1e-6 for f in fracoes) * n)


CLASSES_MADEIRA_PADRAO = [
    dict(nome='C1', minimo=50., maximo=100., inclui_minimo=False, inclui_maximo=True, cor='#22a06b'),
    dict(nome='C2', minimo=20., maximo=50., inclui_minimo=True, inclui_maximo=True, cor='#e2b52b'),
    dict(nome='C3', minimo=0., maximo=20., inclui_minimo=True, inclui_maximo=False, cor='#df6262'),
]


def classificar_percentual(percentual, classes):
    encontradas = [c for c in classes if
        (percentual > c['minimo'] or (c['inclui_minimo'] and percentual == c['minimo'])) and
        (percentual < c['maximo'] or (c['inclui_maximo'] and percentual == c['maximo']))]
    if len(encontradas) != 1:
        raise ValueError('Classes devem cobrir 0 a 100% sem lacunas ou sobreposição.')
    return encontradas[0]


def validar_classes(classes):
    if not classes or len({c['nome'] for c in classes}) != len(classes):
        raise ValueError('Informe classes com nomes distintos.')
    for c in classes:
        if not c['nome'].strip() or not 0 <= c['minimo'] < c['maximo'] <= 100:
            raise ValueError('Classes: informe nomes e limites válidos entre 0 e 100%.')
    pontos = sorted({0., 100.} | {c[k] for c in classes for k in ('minimo', 'maximo')})
    for v in pontos + [(a+b)/2 for a,b in zip(pontos,pontos[1:])]:
        classificar_percentual(v, classes)


def classificar_tabuas(resultado, classes, volume_unitario):
    """Classes do encaixe na seção; previsão proporcional, sem prometer seleção de tábuas."""
    validar_classes(classes)
    classificacoes = [classificar_percentual(100*f, classes) for f in resultado['fracoes_cerne']]
    resumo = []
    total = resultado['quantidade']
    for c in classes:
        teorica = sum(v['nome'] == c['nome'] for v in classificacoes) * resultado['longitudinais']
        prevista = teorica * resultado['quantidade_prevista'] / total if total else 0
        resumo.append(dict(nome=c['nome'], cor=c['cor'], quantidade_teorica=teorica,
                           quantidade_prevista=prevista, volume_previsto=prevista*volume_unitario))
    resultado['cores_pecas'] = [c['cor'] for c in classificacoes]
    resultado['classes'] = resumo
    return resumo
