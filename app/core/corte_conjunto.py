"""Encaixe conjunto determinístico: uma peça física pertence a uma única classe.

Compara grades candidatas e preenche sobras sem sobreposição. É uma heurística
por receita, não uma prova de ótimo global de serraria.
"""
from math import pi

import numpy as np
from .desdobro import encaixar, fracoes_cerne, classificar_percentual
from .motor_modelos import avaliar_expressao_array
from .toragem import tracar_arvore


def validar_vinculos(classes, regras):
    nomes = {r['nome'] for r in regras}
    for c in classes:
        vinculo = c.get('classe_cerne')
        if vinculo not in nomes:
            raise ValueError(f"Vincule a classe {c['nome']} a uma faixa de cerne válida no nó Tábuas.")
        if c['nome'] in nomes and vinculo != c['nome']:
            raise ValueError(f"A classe {c['nome']} exige também a classe de cerne {c['nome']}.")


def _separadas(a,b,folga):
    x,y,w,h=a;xx,yy,ww,hh=b
    return (x+w+folga <= xx+1e-8 or xx+ww+folga <= x+1e-8 or
            y+h+folga <= yy+1e-8 or yy+hh+folga <= y+1e-8)


def planejar_tora(diametro, diametro_cerne, comprimento, volume_madeira, classes, regras, serra_mm=3):
    """Retorna peças inteiras, classe, cerne, volume e receita de uma tora."""
    validar_vinculos(classes,regras)
    classes = sorted(classes,key=lambda c:(-c['preco'],c['id']))
    grupos = {}
    for c in classes:
        dimensao = tuple(sorted((c['largura'],c['espessura'])))
        grupos.setdefault(dimensao,[]).append(c)
    layouts=[];pool={}
    for dimensao,opcoes in grupos.items():
        layout=[]
        pecas=encaixar(diametro,*dimensao,serra_mm)
        for ret,fracao in zip(pecas,fracoes_cerne(pecas,diametro_cerne/2)):
            qualidade=classificar_percentual(100*fracao,regras)['nome']
            elegiveis=[c for c in opcoes if c['classe_cerne']==qualidade and c['rendimento']>0]
            if not elegiveis:continue
            chave=tuple(round(v,9) for v in ret)
            item=dict(retangulo=ret,fracao_cerne=fracao,classe_cerne=qualidade,opcoes=elegiveis)
            pool[chave]=item;layout.append(chave)
        layouts.append(layout)
    def valor(item):
        area=item['retangulo'][2]*item['retangulo'][3]/10000*comprimento
        return max(c['preco']*area for c in item['opcoes'])
    ordem=sorted(pool,key=lambda k:(-valor(pool[k]),k))
    melhor=dict(pecas=[],volume=0.,receita=0.,consumo_madeira=0.)
    for semente in [[]]+layouts:
        aceitas=[];receita=volume=consumo=0.
        # A semente preserva uma grade viável; o restante tenta preencher as sobras.
        chaves_semente=set(semente)
        candidatos=sorted(semente,key=lambda k:(-valor(pool[k]),k)) + [k for k in ordem if k not in chaves_semente]
        for chave in candidatos:
            item=pool[chave];ret=item['retangulo']
            if any(not _separadas(ret,p['retangulo'],serra_mm/10) for p in aceitas):continue
            unidade=ret[2]*ret[3]/10000*comprimento
            for classe in item['opcoes']:
                custo=unidade/(classe['rendimento']/100)
                if consumo+custo > volume_madeira+1e-10:continue
                aceitas.append(dict(retangulo=ret,id_classe=classe['id'],classe=classe['nome'],
                    classe_cerne=item['classe_cerne'],fracao_cerne=item['fracao_cerne'],
                    volume=unidade,receita=unidade*classe['preco']))
                consumo+=custo;volume+=unidade;receita+=unidade*classe['preco']
                break
        if (receita,volume,len(aceitas)) > (melhor['receita'],melhor['volume'],len(melhor['pecas'])):
            melhor=dict(pecas=aceitas,volume=volume,receita=receita,consumo_madeira=consumo)
    return melhor


def produzir_arvore(equacao, coeficientes, variaveis, config, dap, altura, minimo, serra, toco, classes, regras, limite_volume=None, comprimento_comercial=None):
    """Planeja sequencialmente os toretes; classes de comprimentos iguais disputam a mesma seção."""
    validar_vinculos(classes,regras)
    if not config:
        raise ValueError('Configure os produtos Sem casca e Cerne nas dummies do nó Taper para classificar as tábuas.')
    comprimentos=sorted({c['comprimento'] for c in classes})
    referencia=tracar_arvore(equacao,coeficientes,variaveis,dap,altura,min(comprimentos),minimo,toco,config)
    if not {'sem_casca','cerne'} <= set(referencia.get('perfis',{})):
        raise ValueError('Cadastre os produtos Sem casca e Cerne nas dummies do nó Taper.')
    totais={c['id']:0. for c in classes};toretes=[];inicio=toco
    limite_altura=altura
    if comprimento_comercial is not None:
        comercial=(referencia if toco==0 and comprimento_comercial==min(comprimentos) else
            tracar_arvore(equacao,coeficientes,variaveis,dap,altura,comprimento_comercial,minimo,0.,config))
        limite_altura=max((t['fim'] for t in comercial['toretes'] if t['comercial']),default=0.)
    restante=float('inf') if limite_volume is None else max(0.,limite_volume)
    menor_consumo=min((c['comprimento']*c['largura']*c['espessura']/10000/(c['rendimento']/100)
                       for c in classes if c['rendimento']>0),default=float('inf'))
    while inicio < limite_altura and restante > 1e-12:
        if restante+1e-10 < menor_consumo or menor_consumo==float('inf'):break
        planos=[]
        for comp in comprimentos:
            if inicio+comp > limite_altura+1e-9:continue
            hs=np.linspace(inicio,min(inicio+comp,altura*(1-1e-9)),81)
            ds={}
            for produto in config['produtos']:
                if produto['papel'] not in ('com_casca','sem_casca','cerne'):continue
                contexto=dict(zip(config['geometricas'],(dap,hs,altura)))
                contexto.update(produto['valores'])
                d=np.broadcast_to(avaliar_expressao_array(equacao,coeficientes,contexto),hs.shape)
                if not np.all(np.isfinite(d)) or np.any(d < -1e-8):raise ValueError('Taper inválido na seção de corte.')
                ds[produto['papel']]=np.maximum(d,0)
            if np.any(ds['cerne'] > ds['sem_casca']+1e-6):raise ValueError('Diâmetro do cerne maior que o diâmetro sem casca.')
            base=ds.get('com_casca',ds['sem_casca'])
            if 'com_casca' in ds and np.any(ds['sem_casca']>base+1e-6):raise ValueError('Diâmetro sem casca maior que o diâmetro com casca.')
            if base.min()+1e-8 < minimo or ds['sem_casca'].min()<=1e-8:continue
            idx=int(np.argmin(ds['sem_casca']))
            bruto=pi/80000*(ds['sem_casca'][0]**2+ds['sem_casca'][-1]**2)*comp
            candidatas=[c for c in classes if c['comprimento']==comp]
            plano=planejar_tora(float(ds['sem_casca'][idx]),float(ds['cerne'][idx]),comp,min(float(bruto),restante),candidatas,regras,serra)
            plano.update(inicio=inicio,fim=inicio+comp,comprimento=comp,volume_madeira=bruto)
            planos.append(plano)
        if not planos:break
        # Compara receita por metro; comprimentos diferentes competem pelo mesmo trecho do fuste.
        plano=max(planos,key=lambda p:(p['receita']/p['comprimento'],p['volume']/p['comprimento'],-p['comprimento']))
        for p in plano['pecas']:totais[p['id_classe']]+=p['volume']
        restante-=plano['consumo_madeira']
        toretes.append(plano);inicio=plano['fim']
    return dict(volumes=totais,toretes=toretes)
