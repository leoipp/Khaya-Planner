"""Núcleo em lote do desdobro, com as mesmas grades e faixas do motor detalhado."""
from math import floor, sqrt, pi
from collections import OrderedDict
import json
import numpy as np
from numba import njit
from .motor_modelos import avaliar_expressao_array
from .toragem import validar_configuracao_produtos


@njit(cache=True)
def _grade(diametro,w,h,folga):
    raio=diametro/2.;melhor=-1;bw=w;bh=h;bf=0
    for orientacao in range(2):
        ww=w if orientacao==0 else h;hh=h if orientacao==0 else w
        passo=hh+folga
        for fase in range(16):
            total=0
            for i in range(-int(diametro/passo)-2,int(diametro/passo)+3):
                y=i*passo+fase*passo/16-hh/2
                extremo=max(abs(y),abs(y+hh))
                if extremo>raio:continue
                corda=sqrt(max(0.,raio*raio-extremo*extremo))
                total+=int(floor((2*corda+folga+1e-9)/(ww+folga)))
            if total>melhor:melhor=total;bw=ww;bh=hh;bf=fase
    pecas=np.empty((melhor,4));k=0;passo=bh+folga
    for i in range(-int(diametro/passo)-2,int(diametro/passo)+3):
        y=i*passo+bf*passo/16-bh/2;extremo=max(abs(y),abs(y+bh))
        if extremo>raio:continue
        corda=sqrt(max(0.,raio*raio-extremo*extremo))
        n=int(floor((2*corda+folga+1e-9)/(bw+folga)))
        inicio=-(n*bw+max(0,n-1)*folga)/2
        for j in range(n):
            pecas[k,0]=inicio+j*(bw+folga);pecas[k,1]=y;pecas[k,2]=bw;pecas[k,3]=bh;k+=1
    return pecas


@njit(cache=True)
def _corte(diametro,cerne,comprimento,bruto,restante,grupos,classes,regras,serra):
    orcamento=min(bruto,restante)
    # pool: retângulo, grupo, faixa de cerne, receita potencial.
    pool=[]
    for g in range(len(grupos)):
        pecas=_grade(diametro,grupos[g,0],grupos[g,1],serra/10)
        for p in pecas:
            x,y,w,h=p;area=0.;raio=cerne/2
            for k in range(80):
                yy=y+(k+.5)*h/80
                if abs(yy)<raio:
                    corda=sqrt(raio**2-yy**2)
                    area+=max(0.,min(x+w,corda)-max(x,-corda))*h/80
            percentual=100*min(1.,max(0.,area/(w*h)));qual=-1
            for k in range(len(regras)):
                a,b,inc_a,inc_b=regras[k]
                if (percentual>a or (inc_a and percentual==a)) and (percentual<b or (inc_b and percentual==b)):
                    qual=k;break
            valor=-1.
            for k in range(len(classes)):
                if classes[k,0]==g and classes[k,1]==qual and classes[k,3]>0:
                    valor=max(valor,classes[k,2]*(w*h/10000*comprimento))
            if valor>=0:pool.append((x,y,w,h,float(g),float(qual),valor))
    baixo=0.;alto=float('inf')
    n=len(pool);ordem=np.arange(n)
    # Mesma ordenação: receita decrescente e coordenadas arredondadas a 9 casas.
    for i in range(1,n):
        a=ordem[i];j=i-1
        while j>=0:
            b=ordem[j];antes=pool[a][6]>pool[b][6]
            if pool[a][6]==pool[b][6]:
                for k in range(4):
                    aa=round(pool[a][k],9);bb=round(pool[b][k],9)
                    if aa!=bb:antes=aa<bb;break
            if not antes:break
            ordem[j+1]=b;j-=1
        ordem[j+1]=a
    melhor=np.zeros(len(classes));melhor_r=0.;melhor_v=0.;melhor_n=0;melhor_consumo=0.
    for semente in range(-1,len(grupos)):
        aceitas=np.empty(n,np.int64);na=0;consumo=0.;volume=0.;receita=0.;totais=np.zeros(len(classes))
        for etapa in range(2):
            for ii in range(n):
                k=ordem[ii];x,y,w,h,g,qual,valor=pool[k]
                na_semente=int(g)==semente
                if (etapa==0 and not na_semente) or (etapa==1 and na_semente):continue
                separada=True
                for z in range(na):
                    xx,yy,ww,hh,_,__,___=pool[aceitas[z]]
                    if not (x+w+serra/10<=xx+1e-8 or xx+ww+serra/10<=x+1e-8 or y+h+serra/10<=yy+1e-8 or yy+hh+serra/10<=y+1e-8):
                        separada=False;break
                if not separada:continue
                unidade=w*h/10000*comprimento
                for c in range(len(classes)):
                    if classes[c,0]!=g or classes[c,1]!=qual or classes[c,3]<=0:continue
                    custo=unidade/(classes[c,3]/100)
                    requerido=consumo+custo
                    if requerido>bruto+1e-10:continue
                    if requerido>orcamento+1e-10:
                        alto=min(alto,requerido-1e-10);continue
                    baixo=max(baixo,requerido-1e-10)
                    aceitas[na]=k;na+=1;consumo+=custo;volume+=unidade;receita+=unidade*classes[c,2];totais[c]+=unidade;break
        if receita>melhor_r or (receita==melhor_r and (volume>melhor_v or (volume==melhor_v and na>melhor_n))):
            melhor=totais;melhor_r=receita;melhor_v=volume;melhor_n=na;melhor_consumo=consumo
    return melhor,melhor_consumo,baixo,alto


@njit(cache=True,nogil=True)
def _produzir(diametros,cernes,brutos,comerciais,limites,comprimento,grupos,classes,regras,serra):
    resultado=np.zeros((len(limites),len(classes)))
    baixos=np.zeros(len(limites));altos=np.full(len(limites),np.inf)
    menor=float('inf')
    for c in range(len(classes)):
        if classes[c,3]>0:menor=min(menor,classes[c,4]/(classes[c,3]/100))
    for i in range(len(limites)):
        restante=limites[i]
        for t in range(diametros.shape[1]):
            if not comerciais[i,t]:break
            usado=limites[i]-restante
            if restante<=1e-12 or restante+1e-10<menor:
                altos[i]=min(altos[i],usado+max(1e-12,menor-1e-10));break
            baixos[i]=max(baixos[i],usado+max(1e-12,menor-1e-10))
            if diametros[i,t]<=1e-8:break
            v,consumo,baixo,alto=_corte(diametros[i,t],cernes[i,t],comprimento,brutos[i,t],restante,grupos,classes,regras,serra)
            baixos[i]=max(baixos[i],usado+baixo);altos[i]=min(altos[i],usado+alto)
            resultado[i]+=v;restante-=consumo
    return resultado,baixos,altos


# Intervalos exatos do orçamento que mantêm todas as decisões de corte.
# Mudar coeficientes, classes, regras ou dimensões cria outra chave.
_CACHE_PRODUCAO=OrderedDict()
_LIMITE_CACHE=65536

def produzir_lote(equacao,coeficientes,variaveis,config,dap,alturas,limites,comprimento,minimo,serra,classes,regras):
    """Caminho exato para classes com o mesmo comprimento da tora e toco zero."""
    if not config:raise ValueError('Configure os produtos Sem casca e Cerne nas dummies do nó Taper.')
    validar_configuracao_produtos(config,variaveis)
    papeis={p['papel'] for p in config['produtos']}
    if not {'sem_casca','cerne'}<=papeis:raise ValueError('Cadastre os produtos Sem casca e Cerne nas dummies do nó Taper.')
    alturas=np.asarray(alturas,dtype=float);limites=np.asarray(limites,dtype=float)
    if np.any(alturas/comprimento>1000):raise ValueError('Mais de 1.000 toretes por árvore; revise o comprimento configurado.')
    ordenadas=sorted(classes,key=lambda c:(-c['preco'],c['id']))
    grupos=list(dict.fromkeys(tuple(sorted((c['largura'],c['espessura']))) for c in ordenadas))
    if any(dap/min(g)>600 for g in grupos):raise ValueError('Dimensões geram uma grade muito grande; aumente a seção da tábua.')
    ordenadas=sorted(classes,key=lambda c:(-c['preco'],c['id']))
    nomes=[r['nome'] for r in regras]
    matriz=np.array([[grupos.index(tuple(sorted((c['largura'],c['espessura'])))),nomes.index(c['classe_cerne']),c['preco'],c['rendimento'],c['comprimento']*c['largura']*c['espessura']/10000] for c in ordenadas],dtype=float)
    regras_array=np.array([[r['minimo'],r['maximo'],r['inclui_minimo'],r['inclui_maximo']] for r in regras],dtype=float)
    chave=json.dumps([equacao,coeficientes,variaveis,config,dap,comprimento,minimo,serra,classes,regras],sort_keys=True)
    saida=np.empty((len(alturas),len(classes)));pendentes=[]
    for i,(h,limite) in enumerate(zip(alturas,limites)):
        k=(chave,float(h));encontrado=False
        for baixo,alto,volumes in _CACHE_PRODUCAO.get(k,()):
            if baixo<limite<alto:
                saida[i]=volumes;_CACHE_PRODUCAO.move_to_end(k);encontrado=True;break
        if not encontrado:pendentes.append(i)
    if not pendentes:return saida
    posicoes=np.array(pendentes);originais=alturas
    alturas=alturas[posicoes];limites=limites[posicoes]
    nt=max(1,int(np.floor((alturas.max()+1e-9)/comprimento)))
    inicio=np.arange(nt)*comprimento
    fim=np.minimum((inicio+comprimento)[None,:],alturas[:,None]*(1-1e-9))
    secoes=inicio[None,:,None]+np.arange(81)[None,None,:]*((fim-inicio[None,:])/80)[:,:,None]
    secoes=np.minimum(secoes,alturas[:,None,None]*(1-1e-9))
    perfil=np.linspace(0.,1-1e-9,501)[None,:]*alturas[:,None]
    def camadas(h,H):
        ds={}
        for p in config['produtos']:
            contexto=dict(zip(config['geometricas'],(dap,h,H)));contexto.update(p['valores'])
            d=np.broadcast_to(avaliar_expressao_array(equacao,coeficientes,contexto),h.shape)
            if np.any(~np.isfinite(d)) or np.any(d < -1e-8):raise ValueError('Taper inválido na seção de corte.')
            ds[p['papel']]=np.maximum(d,0.)
        for a,b in [('cerne','sem_casca'),('sem_casca','com_casca'),('cerne','com_casca')]:
            if a in ds and b in ds and np.any(ds[a]>ds[b]+1e-6):raise ValueError('Modelo inconsistente: exige cerne ≤ sem casca ≤ com casca.')
        return ds
    # Valida a mesma grade de 501 pontos usada pelo traçamento detalhado.
    camadas(perfil,alturas[:,None])
    ds=camadas(secoes,alturas[:,None,None]);sc=ds['sem_casca'];cc=ds.get('com_casca',sc)
    min_base=cc.min(axis=2)
    comerciais=np.logical_and.accumulate(((inicio+comprimento)[None,:]<=alturas[:,None]+1e-9)&(min_base>1e-8)&(min_base+1e-8>=minimo),axis=1)
    idx=sc.argmin(axis=2)[:,:,None]
    diametros=np.take_along_axis(sc,idx,axis=2)[:,:,0]
    cernes=np.take_along_axis(ds['cerne'],idx,axis=2)[:,:,0]
    brutos=pi/80000*(sc[:,:,0]**2+sc[:,:,-1]**2)*comprimento
    valores,baixos,altos=_produzir(diametros,cernes,brutos,comerciais,limites,comprimento,np.array(grupos,dtype=float),matriz,regras_array,serra)
    ordem=[ordenadas.index(c) for c in classes]
    valores=valores[:,ordem];saida[posicoes]=valores
    for j,i in enumerate(posicoes):
        k=(chave,float(originais[i]));entradas=_CACHE_PRODUCAO.setdefault(k,[])
        entradas.append((baixos[j],altos[j],tuple(valores[j])));entradas[:]=entradas[-8:]
        _CACHE_PRODUCAO.move_to_end(k)
        if len(_CACHE_PRODUCAO)>_LIMITE_CACHE:_CACHE_PRODUCAO.popitem(last=False)
    return saida
