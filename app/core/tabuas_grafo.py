"""Produção de tábuas por árvore e classe diamétrica para o construtor."""
from functools import lru_cache
import json
import re

import numpy as np
import pandas as pd

from .desdobro import validar_classes
from .corte_conjunto import produzir_arvore, validar_vinculos


def saidas_tabuas(no):
    saidas = []
    prefixo = no.get('nome_saida') or 'tabuas'
    for classe in no.get('classes_tabuas', []):
        slug = re.sub(r'[^\w]+','_',classe['nome']).strip('_') or 'classe'
        for tipo,rotulo in ([('volume','Vol.'),('receita','Receita'),('quantidade','Qtd.')] if no.get('versao_producao') == 4 else [('volume','Vol.'),('receita','Receita')]):
            saidas.append(dict(chave=f"{tipo}_{classe['id']}",
                nome_saida=f"{prefixo}_{tipo}_{slug}_{classe['id']}",
                rotulo=f"{rotulo} {classe['nome']}", id_classe=classe['id']))
    return saidas


@lru_cache(maxsize=131072)
def _producao_arvore(equacao, coef_json, variaveis_json, config_json, classes_json,
                    regras_json, diametro, altura, minimo, serra, toco, limite=None, comprimento_comercial=None):
    resultado=produzir_arvore(equacao,json.loads(coef_json),json.loads(variaveis_json),
        json.loads(config_json),diametro,altura,minimo,serra,toco,json.loads(classes_json),json.loads(regras_json),limite,comprimento_comercial)
    return tuple(resultado['volumes'].items())


def avaliar_tabuas(df, no, contexto, variantes, dimensoes, volume_disponivel=None, arvores=None):
    v4 = no.get('versao_producao') == 4
    if v4 and (not isinstance(volume_disponivel,dict) or not isinstance(arvores,dict)):
        raise ValueError('Ligue volume aproveitável e árvores, ambos por diâmetro e na mesma base de área.')
    selecionadas = no.get('classes_tabuas', [])
    if not selecionadas:
        raise ValueError('Selecione as classes de tábua pelo botão direito no nó.')
    if dimensoes.get('diametro_minimo_tora') is None:
        raise ValueError('Configure o diâmetro mínimo dos toretes em Sortimentos.')
    cadastro = {c['id']:c for c in dimensoes.get('classes_tabuas', [])}
    regras = dimensoes.get('classificacao_cerne', [])
    validar_classes(regras)
    if any(c['id'] not in cadastro for c in selecionadas):
        raise ValueError('Uma classe de tábua foi excluída; atualize a configuração do nó.')
    classes=[dict(cadastro[c['id']],classe_cerne=c.get('classe_cerne')) for c in selecionadas]
    validar_vinculos(classes,regras)
    serra = float(no.get('serra_mm',3))
    toco = float(no.get('toco',0))
    if not np.isfinite(serra) or not 0 <= serra <= 20 or not np.isfinite(toco) or toco < 0:
        raise ValueError('Confira a serra e a altura do toco no nó Tábuas.')
    resultados = {s['chave']:{} for s in saidas_tabuas(no)}
    modelo = contexto['no']
    j = lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False)
    especificacoes=[]
    for variante,mascara in variantes:
        especificacoes.append((variante,mascara,j(variante['coeficientes'])))
    variaveis_json=j(modelo['variaveis']);config_json=j(modelo.get('configuracao_produtos'))
    classes_json=j(classes);regras_json=j(regras)
    for diametro, alturas in contexto['alturas'].items():
        hs=alturas.to_numpy(dtype=float)
        dados=np.full((len(df),len(classes)),np.nan)
        preenchidas=np.zeros(len(df),dtype=bool)
        ns=np.ones(len(df));limites=None
        if v4:
            if float(diametro) not in volume_disponivel or float(diametro) not in arvores:
                raise ValueError('Volume e árvores devem conter os mesmos diâmetros do Taper.')
            ns=arvores[float(diametro)].to_numpy(dtype=float)
            disponiveis=volume_disponivel[float(diametro)].to_numpy(dtype=float)
            validos=np.isfinite(hs)&(hs>toco)&np.isfinite(ns)&np.isfinite(disponiveis)
            if np.any(validos & ((ns<0)|(disponiveis < -1e-9))):
                raise ValueError('Volume e número de árvores não podem ser negativos.')
            if np.any(validos & (ns==0)&(disponiveis>1e-9)):
                raise ValueError('Há volume disponível sem árvores na classe diamétrica.')
            limites=np.divide(np.maximum(0.,disponiveis),ns,out=np.zeros(len(df)),where=ns!=0)
        else:validos=np.isfinite(hs)&(hs>toco)
        for variante,mascara,coefs in especificacoes:
            pendentes=validos & ~preenchidas
            if mascara is not None:pendentes &= mascara.to_numpy(dtype=bool)
            posicoes=np.flatnonzero(pendentes)
            if not len(posicoes):continue
            comprimento=float(dimensoes['comprimento_tora']) if v4 else None
            if (v4 and toco==0 and all(c['comprimento']==comprimento for c in classes)):
                from .tabuas_lote import produzir_lote
                for inicio in range(0,len(posicoes),128):
                    lote=posicoes[inicio:inicio+128]
                    producao=produzir_lote(variante['equacao'],variante['coeficientes'],modelo['variaveis'],
                        modelo.get('configuracao_produtos'),float(diametro),hs[lote],limites[lote],
                        comprimento,float(dimensoes['diametro_minimo_tora']),serra,classes,regras)
                    dados[lote,:]=producao*ns[lote,None]
            else:
                for pos in posicoes:
                    producao=dict(_producao_arvore(variante['equacao'],coefs,
                        variaveis_json,config_json,classes_json,regras_json,float(diametro),float(hs[pos]),
                        float(dimensoes['diametro_minimo_tora']),serra,toco,
                        float(limites[pos]) if v4 else None,comprimento))
                    dados[pos,:]=[producao[c['id']]*ns[pos] for c in classes]
            preenchidas[posicoes]=True
        for i,c in enumerate(classes):
            volume=pd.Series(dados[:,i],index=df.index)
            resultados[f"volume_{c['id']}"][float(diametro)]=volume
            if v4:
                unidade=c['comprimento']*c['espessura']*c['largura']/10000
                resultados[f"quantidade_{c['id']}"][float(diametro)]=volume/unidade
            resultados[f"receita_{c['id']}"][float(diametro)]=volume*c['preco']
    return resultados
