"""Estimativa de corte paralelo em seção circular, em centímetros.

Busca determinística entre grades centradas e deslocadas, nas duas
orientações. É uma estimativa de aproveitamento, não um ótimo global.
"""
from math import floor, isfinite, pi, sqrt


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
    return dict(pecas=pecas, quantidade=quantidade, longitudinais=n, volume_tora=volume_tora,
                quantidade_prevista=quantidade_prevista, volume_previsto=volume_previsto,
                rendimento_previsto=100 * volume_previsto / volume_tora,
                volume=volume, rendimento=100 * volume / volume_tora,
                cerne=sum(f > .5 + 1e-6 for f in fracoes) * n,
                alburno=sum(f < .5 - 1e-6 for f in fracoes) * n,
                equilibradas=sum(abs(f - .5) <= 1e-6 for f in fracoes) * n)
