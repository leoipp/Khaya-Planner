"""Geometrias e atributos incorporados ao banco do projeto."""
import codecs
import json
import re
import unicodedata
import uuid
from pathlib import Path

from .db import conectar
from . import projeto


def identificador(nome):
    return '"' + nome.replace('"', '""') + '"'


def listar(conn, incluir_geometrias=True):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='camadas_mapa'").fetchone():
        return []
    geometria = 'geometrias' if incluir_geometrias else 'NULL'
    return conn.execute(f"SELECT tabela, nome, {geometria}, projecao FROM camadas_mapa ORDER BY rowid").fetchall()


def importar(caminho, encoding=None):
    import shapefile

    caminho = Path(caminho)
    arquivos = {p.suffix.lower(): p for p in caminho.parent.iterdir()
                if p.stem.lower() == caminho.stem.lower()}
    for extensao in ('.shp', '.shx', '.dbf'):
        if extensao not in arquivos:
            raise ValueError(f'Arquivo {extensao} ausente. Mantenha .shp, .shx e .dbf na mesma pasta.')
    if encoding is None:
        encoding = arquivos['.cpg'].read_text(encoding='utf-8-sig').strip() if '.cpg' in arquivos else 'utf-8'
        encoding = {'65001': 'utf-8', '1252': 'cp1252', 'ANSI 1252': 'cp1252'}.get(encoding.upper(), encoding)
    codecs.lookup(encoding)
    projecao = arquivos['.prj'].read_text(encoding='utf-8-sig') if '.prj' in arquivos else ''
    with shapefile.Reader(shp=str(arquivos['.shp']), shx=str(arquivos['.shx']),
                          dbf=str(arquivos['.dbf']), encoding=encoding) as reader:
        campos = reader.fields[1:]
        nomes = [c[0] for c in campos]
        if not nomes or len({n.casefold() for n in nomes}) != len(nomes):
            raise ValueError('A tabela deve conter campos com nomes únicos.')
        # O motor de variáveis usa id como chave interna; preserva um ID
        # do arquivo sob outro nome, mesmo quando repetido ou textual.
        for i, nome in enumerate(nomes):
            if nome.casefold() == 'id':
                novo = 'atributo_id'
                while novo.casefold() in {n.casefold() for n in nomes}:
                    novo += '_origem'
                nomes[i] = novo
        registros, geometrias = [], []
        for item in reader.iterShapeRecords():
            registros.append(tuple(v.isoformat() if hasattr(v, 'isoformat') else v for v in item.record))
            geometrias.append(item.shape.__geo_interface__ if item.shape.shapeType else None)
    slug = unicodedata.normalize('NFKD', caminho.stem).encode('ascii', 'ignore').decode()
    slug = re.sub(r'[^a-zA-Z0-9_]', '_', slug)[:40]
    tabela = 'shape_' + slug + '_' + uuid.uuid4().hex[:8]
    tipos = [('INTEGER' if c[1] in ('N', 'L') and not c[3] else
              'REAL' if c[1] in ('N', 'F') else 'TEXT') for c in campos]
    conn = conectar()
    try:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS camadas_mapa (tabela TEXT PRIMARY KEY, nome TEXT NOT NULL, geometrias TEXT NOT NULL, projecao TEXT NOT NULL)')
            definicoes = ', '.join(f'{identificador(n)} {t}' for n, t in zip(nomes, tipos))
            conn.execute(f'CREATE TABLE {identificador(tabela)} (id INTEGER PRIMARY KEY, {definicoes})')
            conn.executemany(f'INSERT INTO {identificador(tabela)} VALUES ({",".join("?" for _ in range(len(nomes) + 1))})',
                             ((i, *registro) for i, registro in enumerate(registros, 1)))
            conn.execute('INSERT INTO camadas_mapa VALUES (?, ?, ?, ?)',
                         (tabela, caminho.stem, json.dumps(geometrias), projecao))
    finally:
        conn.close()
    projeto.sincronizar()
    return tabela
