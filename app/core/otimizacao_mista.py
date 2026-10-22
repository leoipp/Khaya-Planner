"""Formulação linear/mista por variáveis em linhas e coeficientes em colunas."""
from contextlib import closing
import json
import math
import sqlite3
import time
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from .temporarios import pasta_temporaria


def preparar(caminho,cfg):
    from .planejamento import identificador
    with closing(sqlite3.connect(Path(caminho).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        df=pd.read_sql_query('SELECT * FROM '+identificador(cfg['tabela']),conn)
    if df.empty:raise ValueError('A tabela de origem está vazia.')
    def numeros(coluna):
        if coluna not in df:raise ValueError(f'Coluna ausente: {coluna}')
        v=pd.to_numeric(df[coluna],errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(v).all():raise ValueError(f'Valores não numéricos ou ausentes: {coluna}')
        return v
    por_area=cfg.get('vpl_ha') or (cfg.get('capacidade_ativa') and cfg.get('volume_ha')) or any(r.get('por_ha') for r in cfg.get('restricoes',[]))
    area=numeros(cfg['area']) if por_area else np.ones(len(df))
    if (area<=0).any():raise ValueError('A área deve ser positiva para conversões por hectare.')
    objetivo=numeros(cfg['vpl'])*(area if cfg.get('vpl_ha') else 1.)
    if cfg.get('sentido','max') not in ('min','max'):raise ValueError('Selecione maximizar ou minimizar.')
    tipos=np.full(len(df),cfg.get('tipo_variavel','B'),dtype=object)
    if cfg.get('coluna_tipo'):
        if cfg['coluna_tipo'] not in df:raise ValueError('Coluna de tipos não encontrada.')
        tipos=df[cfg['coluna_tipo']].astype(str).str.strip().str.upper().to_numpy()
    if not np.isin(tipos,['B','I','C']).all():raise ValueError('Tipos de variável devem ser B (binária), I (inteira) ou C (contínua).')
    inferior=float(cfg.get('limite_inferior',0));superior=float(cfg.get('limite_superior',1e9))
    if not math.isfinite(inferior) or not math.isfinite(superior) or inferior>superior:raise ValueError('Limites de variável inválidos.')
    baixos=numeros(cfg['coluna_min']) if cfg.get('coluna_min') else np.full(len(df),inferior)
    altos=numeros(cfg['coluna_max']) if cfg.get('coluna_max') else np.full(len(df),superior)
    baixos=np.where(tipos=='B',np.maximum(0.,baixos) if cfg.get('coluna_min') else 0.,baixos)
    altos=np.where(tipos=='B',np.minimum(1.,altos) if cfg.get('coluna_max') else 1.,altos)
    if np.any(baixos>altos):raise ValueError('Há limites inferiores maiores que os superiores.')
    restricoes=[]
    grupo=cfg.get('selecao_grupo','exatamente_um')
    if grupo not in ('livre','exatamente_um','no_maximo_um'):raise ValueError('Regra de agrupamento inválida.')
    if grupo!='livre':
        coluna=cfg.get('talhao')
        if coluna not in df or df[coluna].isna().any():raise ValueError('Selecione uma coluna de grupo sem identificadores ausentes.')
        for nome,indices in df.groupby(coluna,sort=False).indices.items():
            v=np.zeros(len(df));v[indices]=1.
            restricoes.append(dict(nome=f'Seleção do grupo {nome}',valores=v,operador='=' if grupo=='exatamente_um' else '<=',limite=1.))
    for i,r in enumerate(cfg.get('restricoes',[])):
        op=r['operador'];lim=float(r['limite'])
        if op not in ('<=','>=','=') or not math.isfinite(lim):raise ValueError('Restrição cadastrada inválida.')
        restricoes.append(dict(nome=r.get('nome') or f'{r["coluna"]} ({i+1})',valores=numeros(r['coluna'])*(area if r.get('por_ha') else 1.),operador=op,limite=lim))
    anuais={}
    if cfg.get('capacidade_ativa',False):
        inicio,fim=int(cfg['inicio']),int(cfg['fim'])
        limite=float(cfg['demanda'])
        if inicio>fim or not math.isfinite(limite) or limite<0:raise ValueError('Horizonte ou capacidade máxima inválidos.')
        for ano in range(inicio,fim+1):
            v=numeros(str(ano))*(area if cfg.get('volume_ha') else 1.)
            if (v<0).any():raise ValueError('Volume destinado à serraria não pode ser negativo.')
            anuais[ano]=v
            restricoes.append(dict(nome=f'Capacidade máxima da serraria em {ano}',valores=v,operador='<=',limite=limite,ano=ano))
    return df,objetivo,tipos,baixos,altos,restricoes,anuais


def escrever_lp(destino,cfg,dados):
    from .planejamento import expressao
    df,obj,tipos,baixos,altos,restricoes,_=dados
    with open(destino,'w',encoding='ascii') as f:
        f.write(('Maximize' if cfg.get('sentido','max')=='max' else 'Minimize')+'\n obj: '+expressao(obj)+'\nSubject To\n')
        if not restricoes:f.write(' dominio: + x0 >= '+format(baixos[0],'.17g')+'\n')
        for i,r in enumerate(restricoes):f.write(f' restricao_{i}: {expressao(r["valores"])} {r["operador"]} {r["limite"]:.17g}\n')
        f.write('Bounds\n')
        for i,(lo,hi) in enumerate(zip(baixos,altos)):f.write(f' {lo:.17g} <= x{i} <= {hi:.17g}\n')
        for nome,tipo in [('Binary','B'),('General','I')]:
            ids=np.flatnonzero(tipos==tipo)
            if len(ids):f.write(nome+'\n'+'\n'.join(f' x{i}' for i in ids)+'\n')
        f.write('End\n')


def conferir(valores,dados):
    df,obj,tipos,baixos,altos,restricoes,_=dados
    x=np.array([valores.get(f'x{i}',0.) for i in range(len(df))])
    if not np.isfinite(x).all() or np.any(x<baixos-1e-6) or np.any(x>altos+1e-6):raise RuntimeError('Solução viola limites das variáveis.')
    inteiras=tipos!='C'
    if np.any(np.abs(x[inteiras]-np.round(x[inteiras]))>1e-5):raise RuntimeError('Solução viola integralidade.')
    x[inteiras]=np.round(x[inteiras]);avaliadas=[]
    for i,r in enumerate(restricoes):
        valor=float(r['valores']@x);lim=r['limite'];op=r['operador'];tol=max(1e-5,abs(lim)*1e-7)
        folga=lim-valor if op=='<=' else valor-lim if op=='>=' else -abs(valor-lim)
        if folga < -tol:raise RuntimeError(f'A solução viola {r["nome"]}.')
        avaliadas.append(dict(RESTRICAO=r['nome'],RELACAO=op,LIMITE=lim,ATINGIDO=valor,FOLGA=folga,ATENDIDA=1))
    return x,avaliadas


def otimizar(caminho,exe,cfg,progresso):
    from . import planejamento as p
    inicio=time.monotonic();exe=p.validar_solver(exe)
    if not math.isfinite(cfg['tempo_vpl']) or cfg['tempo_vpl']<=0 or not 0<=cfg['gap_vpl']<=1 or cfg['threads']<0:raise ValueError('Tempo, gap ou quantidade de threads inválidos.')
    prefixo=cfg.get('prefixo','modelo_misto')
    if not p.re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,60}',prefixo):raise ValueError('Prefixo inválido: use letras, números e sublinhado, começando por letra.')
    nomes=[f'{n}_{prefixo}' for n in ['resultado_otimizacao','cenarios_escolhidos','restricoes_otimizacao','resumo_otimizacao','etapas_otimizacao','producao_anual']]
    esquema=p.inspecionar(caminho)
    if any(n in esquema for n in nomes+[f'conflitos_{prefixo}']):raise ValueError('O prefixo já possui resultados. Escolha outro para preservar os anteriores.')
    progresso('Validando objetivo, domínios e restrições cadastradas…');dados=preparar(caminho,cfg)
    with tempfile.TemporaryDirectory(prefix='khaya_milp_',dir=pasta_temporaria()) as pasta:
        escrever_lp(Path(pasta,'etapa1.lp'),cfg,dados)
        parametros=dict(cfg,tempo_alpha=cfg['tempo_vpl'],gap_alpha=cfg['gap_vpl'])
        try:valores,etapa=p.executar_solver(exe,pasta,1,parametros,progresso)
        except p.ModeloInviavel as erro:
            p.publicar(caminho,{f'conflitos_{prefixo}':pd.DataFrame(erro.conflitos)})
            raise
        x,avaliadas=conferir(valores,dados)
    df,obj,tipos,lo,hi,restricoes,anuais=dados
    df=df.copy();df['DECISAO_OTIMA']=x;df['TIPO_VARIAVEL']=tipos
    etapa['OBJETIVO']=('Maximizar ' if cfg.get('sentido','max')=='max' else 'Minimizar ')+cfg['vpl']
    etapa['UNIDADE']=cfg.get('unidade_objetivo','')
    producao=[dict(ANO=a,VOLUME_TAPER=float(v@x),DEMANDA=cfg['demanda'],LIMITE_MIN=None,LIMITE_MAX=cfg['demanda'],REGULADO=1,DESVIO_PERCENTUAL=(float(v@x)/cfg['demanda']-1)*100 if cfg['demanda'] else 0.) for a,v in anuais.items()]
    resumo=dict(OBJETIVO=etapa['OBJETIVO'],VALOR_OBJETIVO=float(obj@x),NUM_VARIAVEIS=len(x),NUM_RESTRICOES=len(restricoes),TEMPO_TOTAL_S=time.monotonic()-inicio,CONFIGURACAO_JSON=json.dumps(cfg,ensure_ascii=False))
    frames=[df,df.loc[np.abs(x)>1e-8],pd.DataFrame(avaliadas,columns=['RESTRICAO','RELACAO','LIMITE','ATINGIDO','FOLGA','ATENDIDA']),pd.DataFrame([resumo]),pd.DataFrame([etapa]),pd.DataFrame(producao,columns=['ANO','VOLUME_TAPER','DEMANDA','LIMITE_MIN','LIMITE_MAX','REGULADO','DESVIO_PERCENTUAL'])]
    progresso('Gravando decisões e conferência das restrições…');p.publicar(caminho,dict(zip(nomes,frames)))
    return dict(resumo=resumo,tabelas=nomes,producao=producao,etapas=[etapa],restricoes=avaliadas)
