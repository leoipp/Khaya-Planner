"""Planejamento hierárquico via executável local do CPLEX (LP/SOL)."""
import math
import json
import queue
import threading
from contextlib import closing
import re
import sqlite3
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from .temporarios import pasta_temporaria


class ModeloInviavel(RuntimeError):
    def __init__(self, mensagem, conflitos):
        super().__init__(mensagem)
        self.conflitos = conflitos


def publicar(caminho, frames):
    """Publica todas as tabelas numa transação, sem substituir tabelas existentes."""
    with tempfile.TemporaryDirectory(prefix='khaya_resultado_', dir=pasta_temporaria()) as pasta:
        temporario = str(Path(pasta, 'resultado.sqlite'))
        with closing(sqlite3.connect(temporario)) as conn:
            for nome, frame in frames.items():
                frame.to_sql(nome, conn, index=False)
        with closing(sqlite3.connect(caminho, timeout=30)) as conn:
            conn.execute('ATTACH DATABASE ? AS resultado', (temporario,))
            conn.execute('BEGIN IMMEDIATE')
            for nome in frames:
                conn.execute(f'CREATE TABLE {identificador(nome)} AS SELECT * FROM resultado.{identificador(nome)}')
            conn.commit()


def identificador(nome):
    return '"' + str(nome).replace('"', '""') + '"'


def validar_solver(caminho):
    arquivo = Path(caminho).expanduser()
    if not arquivo.is_file():
        raise ValueError("Selecione o executável cplex.exe da instalação do CPLEX.")
    return str(arquivo.resolve())


def inspecionar(caminho):
    with closing(sqlite3.connect(f'{Path(caminho).resolve().as_uri()}?mode=ro', uri=True)) as conn:
        tabelas = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: [r[1] for r in conn.execute(f'PRAGMA table_info({identificador(t)})')]
                for t in tabelas}


def preparar(caminho, cfg):
    inicio, fim, regulacao = (int(cfg[k]) for k in ('inicio', 'fim', 'regulacao'))
    if not inicio <= regulacao <= fim:
        raise ValueError("O início da regulação deve estar dentro do horizonte.")
    for chave in ('demanda', 'tempo_alpha', 'tempo_vpl'):
        if not math.isfinite(cfg[chave]) or cfg[chave] <= 0:
            raise ValueError(f'{chave}: informe um número positivo e finito.')
    for chave in ('alpha_max', 'folga', 'tolerancia', 'gap_alpha', 'gap_vpl'):
        if not math.isfinite(cfg[chave]) or cfg[chave] < 0:
            raise ValueError(f'{chave}: informe um número não negativo e finito.')
    if cfg['gap_alpha'] > 1 or cfg['gap_vpl'] > 1 or cfg['threads'] < 0:
        raise ValueError("Gap deve estar entre 0 e 100%; threads não pode ser negativo.")
    anos = list(range(inicio, fim + 1))
    campos = list(dict.fromkeys([cfg[k] for k in ('talhao', 'cenario', 'area', 'idade', 'vpl')]
                              + [str(a) for a in anos]
                              + [r['coluna'] for r in cfg.get('restricoes', [])]))
    if len({cfg[k] for k in ('talhao', 'cenario', 'area', 'idade', 'vpl')}) != 5:
        raise ValueError('Selecione colunas distintas para talhão, cenário, área, idade e VPL.')
    esquema = inspecionar(caminho)
    if cfg['tabela'] not in esquema:
        raise ValueError("Tabela de origem não encontrada.")
    faltantes = set(campos) - set(esquema[cfg['tabela']])
    if faltantes:
        raise ValueError(f'Colunas ausentes: {", ".join(sorted(faltantes))}')
    with closing(sqlite3.connect(f'{Path(caminho).resolve().as_uri()}?mode=ro', uri=True)) as conn:
        df = pd.read_sql_query(f'SELECT {", ".join(map(identificador, campos))} '
                               f'FROM {identificador(cfg["tabela"])}', conn)
    if df.empty:
        raise ValueError("A tabela de origem está vazia.")
    for c in (cfg['talhao'], cfg['cenario']):
        if df[c].isna().any():
            raise ValueError(f'Identificadores ausentes em {c}.')
        df[c] = df[c].astype(str).str.strip()
        if df[c].eq('').any():
            raise ValueError(f'Identificadores vazios em {c}.')
    if df.duplicated([cfg['talhao'], cfg['cenario']]).any():
        raise ValueError("Existem combinações talhão × cenário duplicadas.")
    numericas = set(campos) - {cfg['talhao'], cfg['cenario']}
    for c in numericas:
        df[c] = pd.to_numeric(df[c], errors='coerce')
        if not np.isfinite(df[c].to_numpy(dtype=float)).all():
            raise ValueError(f'Valores inválidos na coluna {c}.')
    if (df[cfg['area']] <= 0).any():
        raise ValueError("As áreas devem ser positivas.")
    if (df[[str(a) for a in anos]] < 0).any().any():
        raise ValueError("Volumes anuais não podem ser negativos.")
    for r in cfg.get('restricoes', []):
        if r['operador'] not in ('>=', '<=', '=') or not math.isfinite(r['limite']):
            raise ValueError("Restrição adicional inválida.")
    grupos = list(df.groupby(cfg['talhao'], sort=False).indices.values())
    area = df[cfg['area']].to_numpy(dtype=float)
    vpl = df[cfg['vpl']].to_numpy(dtype=float) * (area if cfg['vpl_ha'] else 1)
    volumes = {a: df[str(a)].to_numpy(dtype=float) * (area if cfg['volume_ha'] else 1)
               for a in anos}
    return df, grupos, vpl, volumes


def expressao(valores):
    termos = [f'{float(v):+.17g} x{i}' for i, v in enumerate(valores) if v != 0]
    return '\n  '.join(' '.join(termos[i:i+8]) for i in range(0, len(termos), 8)) or '0 x0'


def validar_faixa_demanda(cfg, grupos, volumes):
    """Detecta incompatibilidades anuais certas; não garante viabilidade conjunta."""
    demanda = cfg['demanda']
    for ano, valores in volumes.items():
        if ano < cfg['regulacao']:
            continue
        minimo = float(sum(valores[g].min() for g in grupos))
        maximo = float(sum(valores[g].max() for g in grupos))
        necessario = max(0., 1 - maximo / demanda, minimo / demanda - 1)
        if necessario > cfg['alpha_max'] + 1e-7:
            raise ValueError(
                f'Restrição de demanda inviável em {ano}: demanda de {demanda:,.2f} m³/ano, '
                f'produção possível entre {minimo:,.2f} e {maximo:,.2f} m³. '
                f'O limite de alfa informado é {cfg["alpha_max"]*100:g}%, '
                f'mas esse ano exige pelo menos {necessario*100:.4f}%.\n'
                'Confira os volumes, as unidades, o horizonte regulado e o limite de alfa. '
                'O ALPHA_MAX = 5.0 do script original corresponde a 500% na tela, não a 5%. '
                'Esse limite necessário por ano não garante a viabilidade conjunta das restrições.')


def escrever_lp(destino, cfg, df, grupos, vpl, volumes, faixa=None):
    with open(destino, 'w', encoding='ascii') as f:
        f.write('Minimize\n obj: alpha\n' if faixa is None else
                'Maximize\n obj: ' + expressao(vpl) + '\n')
        f.write('Subject To\n')
        for i, ids in enumerate(grupos):
            termos = [f'+ x{j}' for j in ids]
            soma = '\n  '.join(' '.join(termos[j:j+8]) for j in range(0, len(termos), 8))
            f.write(f' prescricao_{i}: {soma} = 1\n')
        for ano, v in volumes.items():
            if ano < cfg['regulacao']:
                continue
            e = expressao(v)
            if faixa is None:
                f.write(f' min_{ano}: {e} + {cfg["demanda"]:.17g} alpha >= {cfg["demanda"]:.17g}\n')
                f.write(f' max_{ano}: {e} - {cfg["demanda"]:.17g} alpha <= {cfg["demanda"]:.17g}\n')
            else:
                f.write(f' min_{ano}: {e} >= {faixa[0]:.17g}\n max_{ano}: {e} <= {faixa[1]:.17g}\n')
        for i, r in enumerate(cfg.get('restricoes', [])):
            v = df[r['coluna']].to_numpy(dtype=float)
            if r.get('por_ha'):
                v = v * df[cfg['area']].to_numpy(dtype=float)
            f.write(f' adicional_{i}: {expressao(v)} {r["operador"]} {r["limite"]:.17g}\n')
        if faixa is None:
            f.write(f'Bounds\n 0 <= alpha <= {cfg["alpha_max"]:.17g}\n')
        f.write('Binary\n' + '\n'.join(f' x{i}' for i in range(len(df))) + '\nEnd\n')


def _executar_com_log(comando, pasta, timeout, progresso):
    """Drena a saída continuamente, mantendo o prazo e liberando o processo em erros."""
    linhas = queue.Queue()
    with subprocess.Popen(comando, cwd=pasta, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as proc:
        def ler():
            try:
                for linha in proc.stdout:
                    linhas.put(linha)
            finally:
                linhas.put(None)

        leitor = threading.Thread(target=ler, daemon=True)
        leitor.start()
        inicio = time.monotonic()
        saida = []
        try:
            while True:
                if time.monotonic() - inicio > timeout:
                    raise subprocess.TimeoutExpired(comando, timeout)
                try:
                    linha = linhas.get(timeout=.2)
                except queue.Empty:
                    continue
                if linha is None:
                    break
                saida.append(linha)
                if linha.strip():
                    progresso(linha.rstrip())
            codigo = proc.wait(timeout=max(.1, timeout - (time.monotonic() - inicio)))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            leitor.join()
            proc.stdout.close()
    return codigo, ''.join(saida)


def executar_solver(exe, pasta, etapa, cfg, progresso):
    comandos = [f'read etapa{etapa}.lp', f'set threads {cfg["threads"]}',
                f'set timelimit {cfg["tempo_alpha" if etapa == 1 else "tempo_vpl"]}',
                f'set mip tolerances mipgap {cfg["gap_alpha" if etapa == 1 else "gap_vpl"]}']
    if etapa == 2:
        comandos.append('read inicio.mst')
    comandos += ['optimize', 'display solution quality', f'write etapa{etapa}.sol', 'quit']
    progresso(f'Etapa {etapa}: executando CPLEX…')
    inicio = time.monotonic()
    codigo, log = _executar_com_log(
        [exe, '-c', *comandos], pasta,
        cfg['tempo_alpha' if etapa == 1 else 'tempo_vpl'] + 120, progresso)
    Path(pasta, f'etapa{etapa}.log').write_text(log, encoding='utf-8')
    sol = Path(pasta, f'etapa{etapa}.sol')
    if codigo or not sol.exists():
        if 'infeasible' in log.lower():
            conflitos = []
            try:
                refino = subprocess.run([exe, '-c', f'read etapa{etapa}.lp',
                                         'set timelimit 30', 'tools conflict', 'write conflito.clp', 'quit'],
                                        cwd=pasta, capture_output=True, text=True, errors='replace', timeout=60,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                arquivo = Path(pasta, 'conflito.clp')
                texto = arquivo.read_text(errors='replace') if arquivo.exists() else refino.stdout + refino.stderr
                for nome in re.findall(r'^\s*((?:prescricao|min|max|adicional|restricao)_\d+)\s*:', texto, re.MULTILINE):
                    conflitos.append(dict(ETAPA=etapa, RESTRICAO=nome, STATUS='Member', DETALHAMENTO=texto))
                if not conflitos:
                    conflitos.append(dict(ETAPA=etapa, RESTRICAO=None, STATUS='Diagnóstico', DETALHAMENTO=texto))
            except Exception as erro:
                conflitos.append(dict(ETAPA=etapa, RESTRICAO=None, STATUS='Refino não concluído', DETALHAMENTO=str(erro)))
            raise ModeloInviavel(f'Etapa {etapa}: modelo inviável.\n{log[-6000:]}', conflitos)
        raise RuntimeError(f'Etapa {etapa} sem solução. Verifique licença, instalação e '
                           f'viabilidade das restrições.\n{log[-6000:]}')
    raiz = ET.parse(sol).getroot()
    cab = raiz.find('header')
    if cab is None or cab.get('primalFeasible') != '1':
        raise RuntimeError(f'Etapa {etapa}: CPLEX não retornou solução factível.\n{log[-6000:]}')
    valores = {v.get('name'): float(v.get('value')) for v in raiz.findall('./variables/variable')}
    qualidade = raiz.find('quality')
    meta = dict(cab.attrib)
    if qualidade is not None:
        meta.update(qualidade.attrib)
    objetivo = float(cab.get('objectiveValue'))
    bound = float(meta['bestBound']) if 'bestBound' in meta else None
    if bound is None and cab.get('solutionStatusValue') == '101':
        bound = objetivo
    if bound is None:
        encontrados = re.findall(r'(?:Current MIP best bound|Best bound)\s*=\s*([-+\d.eE]+)', log)
        if encontrados:
            bound = float(encontrados[-1])
    gap = float(meta['MIPRelativeGap']) if 'MIPRelativeGap' in meta else None
    if gap is None and bound is not None:
        gap = abs(objetivo-bound) / (abs(objetivo) + 1e-10)
    return valores, {'ETAPA': etapa, 'OBJETIVO': ('Objetivo configurado' if cfg.get('modelo')=='configuravel' else 'Minimizar Alpha Taper' if etapa == 1 else 'Maximizar VPL'),
                     'RESULTADO': objetivo, 'STATUS': cab.get('solutionStatusString'),
                     'UNIDADE': 'proporção' if etapa == 1 else 'R$',
                     'BOUND': bound, 'GAP': gap,
                     'TEMPO_S': time.monotonic() - inicio, 'LOG': log}


def conferir(valores, cfg, grupos, df, volumes, faixa):
    bruto = np.array([valores.get(f'x{i}', 0) for i in range(len(df))])
    if not np.isfinite(bruto).all() or (np.abs(bruto - np.round(bruto)) > 1e-5).any():
        raise RuntimeError("O solver retornou valores binários inválidos.")
    x = np.round(bruto).astype(np.int8)
    if not np.isin(x, [0, 1]).all() or any(x[g].sum() != 1 for g in grupos):
        raise RuntimeError("A solução não escolhe exatamente um cenário por talhão.")
    tol = max(1e-5, cfg['demanda'] * 1e-7)
    for a, v in volumes.items():
        if a >= cfg['regulacao'] and not faixa[0] - tol <= v @ x <= faixa[1] + tol:
            raise RuntimeError(f'A produção de {a} viola a faixa de volume.')
    for r in cfg.get('restricoes', []):
        v = df[r['coluna']].to_numpy(dtype=float)
        if r.get('por_ha'):
            v = v * df[cfg['area']].to_numpy(dtype=float)
        delta = v @ x - r['limite']
        tolerancia = max(1e-5, abs(r['limite']) * 1e-7)
        if ((r['operador'] == '>=' and delta < -tolerancia) or
            (r['operador'] == '<=' and delta > tolerancia) or
            (r['operador'] == '=' and abs(delta) > tolerancia)):
            raise RuntimeError(f'A solução viola a restrição da coluna {r["coluna"]}.')
    return x


def otimizar(caminho, exe, cfg, progresso=lambda msg: None):
    if cfg.get('modelo')=='configuravel':
        from .otimizacao_mista import otimizar as otimizar_misto
        return otimizar_misto(caminho,exe,cfg,progresso)
    inicio = time.monotonic()
    exe = validar_solver(exe)
    prefixo = cfg.get('prefixo', 'vpl_taper_2etapas')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,60}', prefixo):
        raise ValueError("Prefixo: use letras, números e sublinhado, começando com letra (até 61 caracteres).")
    nomes = {k: f'{k}_{prefixo}' for k in ('resultado_otimizacao', 'cenarios_escolhidos',
             'producao_anual', 'resumo_otimizacao', 'etapas_otimizacao', 'diagnostico_volume')}
    esquema = inspecionar(caminho)
    if any(n in esquema for n in [*nomes.values(), f'conflitos_{prefixo}']):
        raise ValueError("Já existem tabelas com esse prefixo. Informe outro nome para preservar os resultados anteriores.")
    progresso('Validando dados e montando o modelo…')
    df, grupos, vpl, volumes = preparar(caminho, cfg)
    validar_faixa_demanda(cfg, grupos, volumes)
    with tempfile.TemporaryDirectory(prefix='khaya_cplex_', dir=pasta_temporaria()) as pasta:
        escrever_lp(Path(pasta, 'etapa1.lp'), cfg, df, grupos, vpl, volumes)
        try:
            v1, m1 = executar_solver(exe, pasta, 1, cfg, progresso)
        except ModeloInviavel as erro:
            publicar(caminho, {f'conflitos_{prefixo}': pd.DataFrame(erro.conflitos)})
            raise ModeloInviavel(f'{erro}\nDiagnóstico salvo em conflitos_{prefixo}.', erro.conflitos) from erro
        alpha = v1.get('alpha', 0.0)
        if not math.isfinite(alpha) or not -1e-7 <= alpha <= cfg['alpha_max'] + 1e-7:
            raise RuntimeError('Alpha retornado pelo solver é inválido.')
        conferir(v1, cfg, grupos, df, volumes,
                 (max(0, cfg['demanda'] * (1-alpha)), cfg['demanda'] * (1+alpha)))
        limite = min(cfg['alpha_max'], alpha + cfg['folga'] + cfg['tolerancia'])
        faixa = max(0, cfg['demanda'] * (1-limite)), cfg['demanda'] * (1+limite)
        progresso(f'Etapa 1: {m1.get("STATUS", "")} | alpha = {alpha*100:.4f}% | '
                  f'bound = {m1["BOUND"]} | gap = {m1["GAP"]}')
        progresso(f'Etapa 2: preservar {faixa[0]:,.2f} ≤ produção anual ≤ {faixa[1]:,.2f} m³; '
                  'maximizar VPL usando a solução da etapa 1 como MIP start.')
        raiz = ET.Element('CPLEXSolution', version='1.2')
        ET.SubElement(raiz, 'header', problemName='etapa2.lp')
        vars_ = ET.SubElement(raiz, 'variables')
        for i in range(len(df)):
            ET.SubElement(vars_, 'variable', name=f'x{i}', value=str(round(v1.get(f'x{i}', 0))))
        ET.ElementTree(raiz).write(Path(pasta, 'inicio.mst'), encoding='utf-8', xml_declaration=True)
        escrever_lp(Path(pasta, 'etapa2.lp'), cfg, df, grupos, vpl, volumes, faixa)
        try:
            v2, m2 = executar_solver(exe, pasta, 2, cfg, progresso)
        except ModeloInviavel as erro:
            publicar(caminho, {f'conflitos_{prefixo}': pd.DataFrame(erro.conflitos)})
            raise ModeloInviavel(f'{erro}\nDiagnóstico salvo em conflitos_{prefixo}.', erro.conflitos) from erro
        x = conferir(v2, cfg, grupos, df, volumes, faixa)
    df['ID_ALT'] = np.arange(len(df))
    df['Xij_OTIMO'] = x
    producao, diagnostico = [], []
    for a, v in volumes.items():
        p = float(v @ x)
        regulado = int(a >= cfg['regulacao'])
        producao.append(dict(ANO=a, VOLUME_TAPER=p, DEMANDA=cfg['demanda'], REGULADO=regulado,
                             DESVIO=p-cfg['demanda'], DESVIO_ABS=abs(p-cfg['demanda']),
                             DESVIO_PERCENTUAL=(p/cfg['demanda']-1)*100,
                             LIMITE_MIN=faixa[0] if regulado else None,
                             LIMITE_MAX=faixa[1] if regulado else None))
        diagnostico.append(dict(ANO=a, MINIMO_INDIVIDUAL=sum(v[g].min() for g in grupos),
                                MAXIMO_INDIVIDUAL=sum(v[g].max() for g in grupos)))
    resumo = dict(NUM_TALHOES=len(grupos), NUM_ALTERNATIVAS=len(df), DEMANDA=cfg['demanda'],
                  ALPHA_ENCONTRADO=alpha, ALPHA_LIMITE=limite, VOLUME_MIN=faixa[0], VOLUME_MAX=faixa[1],
                  VPL_FINAL=float(vpl @ x), ALPHA_BOUND=m1['BOUND'], VPL_BOUND=m2['BOUND'],
                  GAP_ALPHA=m1['GAP'], GAP_VPL=m2['GAP'], TEMPO_ALPHA_S=m1['TEMPO_S'],
                  TEMPO_VPL_S=m2['TEMPO_S'], TEMPO_TOTAL_S=time.monotonic()-inicio,
                  CONFIGURACAO_JSON=json.dumps(cfg, ensure_ascii=False))
    frames = [df, df.loc[x == 1], pd.DataFrame(producao), pd.DataFrame([resumo]),
              pd.DataFrame([m1, m2]), pd.DataFrame(diagnostico)]
    progresso('Gravando as seis tabelas de resultado…')
    publicar(caminho, dict(zip(nomes.values(), frames)))
    return {'resumo': resumo, 'tabelas': list(nomes.values()), 'producao': producao,
            'etapas': [m1, m2]}
