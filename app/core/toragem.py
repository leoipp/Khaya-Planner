"""Perfil de taper e traçamento por ponta fina; unidades cm, m e m³."""
from math import floor, isfinite, pi

import numpy as np

from .motor_modelos import avaliar_expressao_array


def tracar_arvore(equacao, coeficientes, variaveis, dap, altura, comprimento,
                  diametro_minimo, toco=0.2):
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
                dict(zip(variaveis, (dap, np.asarray(h), altura))))
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
