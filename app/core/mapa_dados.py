"""Consultas filtradas, join por atributos e estado persistente do mapa."""
import json
import math
import uuid
from decimal import Decimal, InvalidOperation

from . import projeto, simulacao
from .camadas import identificador
from .db import conectar

OPERADORES = ('=', '!=', '>', '>=', '<', '<=', 'contém', 'começa com',
              'em lista', 'fora da lista', 'é nulo', 'não é nulo')


def colunas(conn, tabela):
    existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
    if not existe:
        raise ValueError('Tabela indisponível. Disponibilize um resumo no mapa ou gere uma simulação primeiro.')
    return [(r[1], r[2]) for r in conn.execute(f'PRAGMA table_info({identificador(tabela)})')]


def fontes_simulacao(conn):
    nomes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    bases = (simulacao.TABELA_POPULACAO, simulacao.TABELA_LOTE_POPULACAO)
    resumos = []
    if 'mapa_fontes_resumos' in nomes:
        resumos = [r[0] for r in conn.execute('SELECT tabela FROM mapa_fontes_resumos ORDER BY nome') if r[0] in nomes]
    return resumos + sorted(n for n in nomes if n in bases or n.startswith(simulacao.TABELA_POPULACAO + '__cenario'))


def obter_estado(conn, chave):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='mapa_estado' AND type='table'").fetchone():
        return {}
    linha = conn.execute('SELECT valor FROM mapa_estado WHERE chave=?', (chave,)).fetchone()
    return json.loads(linha[0]) if linha else {}


def gravar_estado(conn, chave, valor):
    conn.execute('CREATE TABLE IF NOT EXISTS mapa_estado (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)')
    conn.execute('INSERT INTO mapa_estado VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor',
                 (chave, json.dumps(valor, ensure_ascii=False, allow_nan=False)))


def salvar_estado(chave, valor):
    conn = conectar()
    try:
        with conn:
            gravar_estado(conn, chave, valor)
    finally:
        conn.close()
    projeto.sincronizar()


def clausula_filtros(conn, tabela, filtros=None, combinacao='E'):
    tipos = dict(colunas(conn, tabela))
    if combinacao not in ('E', 'OU'):
        raise ValueError('Combinação de filtros inválida.')
    partes, parametros = [], []
    for filtro in filtros or []:
        nome, op = filtro['coluna'], filtro['operador']
        if nome not in tipos or op not in OPERADORES:
            raise ValueError(f'Filtro inválido: {nome} {op}. Revise a consulta da simulação.')
        campo = identificador(nome)
        texto = str(filtro.get('valor', ''))
        if op in ('é nulo', 'não é nulo'):
            partes.append(f'{campo} IS {"NOT " if op == "não é nulo" else ""}NULL')
            continue
        def converter(valor):
            if any(t in tipos[nome].upper() for t in ('INT', 'REAL', 'FLOAT', 'DOUBLE', 'NUM', 'DEC')):
                try:
                    numero = float(valor.strip().replace(',', '.'))
                except ValueError as e:
                    raise ValueError(f'Informe um número para {nome}.') from e
                if not math.isfinite(numero):
                    raise ValueError(f'Número inválido para {nome}.')
                return numero
            return valor
        if op in ('em lista', 'fora da lista'):
            valores = [v.strip() for v in texto.split(';') if v.strip()]
            if not valores:
                raise ValueError('Separe os valores da lista com ponto e vírgula.')
            partes.append(f'{campo} {"NOT " if op == "fora da lista" else ""}IN ({",".join("?" for _ in valores)})')
            parametros.extend(converter(v) for v in valores)
        elif op in ('contém', 'começa com'):
            literal = texto.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            partes.append(f"CAST({campo} AS TEXT) LIKE ? ESCAPE '\\'")
            parametros.append(('%' if op == 'contém' else '') + literal + '%')
        else:
            partes.append(f'{campo} {op} ?')
            parametros.append(converter(texto))
    return (' WHERE ' + (' AND ' if combinacao == 'E' else ' OR ').join(f'({p})' for p in partes)
            if partes else ''), parametros


def consultar(conn, tabela, filtros=None, combinacao='E', limite=200, pagina=0):
    where, params = clausula_filtros(conn, tabela, filtros, combinacao)
    origem = identificador(tabela)
    total = conn.execute(f'SELECT COUNT(*) FROM {origem}{where}', params).fetchone()[0]
    cursor = conn.execute(f'SELECT * FROM {origem}{where} ORDER BY rowid LIMIT ? OFFSET ?',
                          [*params, limite, pagina * limite])
    return [c[0] for c in cursor.description], cursor.fetchall(), total


def normalizar_chave(valor, modo='texto'):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if modo == 'texto':
        return texto
    if modo != 'numero':
        raise ValueError('Modo de chave inválido.')
    try:
        numero = Decimal(texto.replace(',', '.'))
    except InvalidOperation:
        return None
    return format(numero.normalize(), 'f') if numero.is_finite() else None


def aplicar_join(conn, camada, fonte, chave_shape, chave_simulacao, selecionadas,
                 filtros=None, combinacao='E', repeticoes='unico', modo_chave='texto'):
    """Left join materializado; nunca duplica geometrias nem modifica a simulação."""
    from . import camadas
    if camada not in {r[0] for r in camadas.listar(conn, incluir_geometrias=False)}:
        raise ValueError('Selecione uma camada importada.')
    if fonte not in fontes_simulacao(conn):
        raise ValueError('Selecione uma tabela de resumos de cenários ou simulação.')
    shape_cols = dict(colunas(conn, camada))
    sim_cols = dict(colunas(conn, fonte))
    if chave_shape not in shape_cols or chave_simulacao not in sim_cols:
        raise ValueError('Selecione as duas colunas de ligação.')
    selecionadas = list(dict.fromkeys(selecionadas))
    if not selecionadas or any(c not in sim_cols for c in selecionadas):
        raise ValueError('Selecione as colunas da simulação que deseja trazer.')
    if repeticoes not in ('unico', 'primeiro', 'ultimo', 'soma', 'media', 'min', 'max'):
        raise ValueError('Tratamento de repetições inválido.')
    if modo_chave not in ('texto', 'numero'):
        raise ValueError('Modo de chave inválido.')
    conn.create_function('mapa_chave', 1, lambda v: normalizar_chave(v, modo_chave), deterministic=True)
    where, params = clausula_filtros(conn, fonte, filtros, combinacao)
    src = identificador(fonte)
    chave = f'mapa_chave({identificador(chave_simulacao)})'
    filtrada = f'SELECT * FROM {src}{where}'
    # Nomes de trabalho são independentes dos campos do usuário.
    chave_alias = '__mapa_' + uuid.uuid4().hex
    validos = f'SELECT *, {chave} AS {identificador(chave_alias)} FROM ({filtrada}) WHERE {chave} IS NOT NULL'
    key = identificador(chave_alias)
    if repeticoes == 'unico':
        duplicadas = conn.execute(f'SELECT {key}, COUNT(*) FROM ({validos}) GROUP BY {key} HAVING COUNT(*) > 1 LIMIT 5', params).fetchall()
        if duplicadas:
            exemplos = ', '.join(f'{k} ({n} linhas)' for k, n in duplicadas)
            raise ValueError('Há várias linhas por chave: ' + exemplos +
                             '. Filtre idade/cenário ou escolha como tratar as repetições.')
    agregacoes = {'soma': 'SUM', 'media': 'AVG', 'min': 'MIN', 'max': 'MAX'}
    if repeticoes in agregacoes:
        for c in selecionadas:
            campo = identificador(c)
            if conn.execute(f"SELECT 1 FROM ({validos}) WHERE {campo} IS NOT NULL AND typeof({campo}) NOT IN ('integer', 'real') LIMIT 1", params).fetchone():
                raise ValueError(f'A agregação exige colunas numéricas. Desmarque "{c}" ou escolha primeira/última linha.')
    antigas = obter_estado(conn, 'join:' + camada)
    destinos = {}
    usados = {c.casefold() for c in shape_cols}
    for c in selecionadas:
        anterior = antigas.get('colunas', {}).get(c)
        if anterior in shape_cols:
            destino = anterior
        else:
            destino = 'sim_' + c
            while destino.casefold() in usados:
                destino += '_join'
        usados.add(destino.casefold())
        destinos[c] = destino
    aliases = {c: 'v' + str(i) for i, c in enumerate(selecionadas)}
    if repeticoes in agregacoes:
        campos = ', '.join(f'{agregacoes[repeticoes]}({identificador(c)}) AS {identificador(aliases[c])}' for c in selecionadas)
        consulta = f'SELECT {key} AS k, {campos} FROM ({validos}) GROUP BY {key}'
    else:
        campos = ', '.join(f'{identificador(c)} AS {identificador(aliases[c])}' for c in selecionadas)
        if repeticoes in ('primeiro', 'ultimo'):
            ordem = identificador('id') if 'id' in sim_cols else 'rowid'
            # rowid deve ser projetado explicitamente quando a fonte não tem id.
            base = f'SELECT rowid AS "{chave_alias}_ordem", * FROM {src}{where}' if 'id' not in sim_cols else filtrada
            ordem = identificador('id') if 'id' in sim_cols else identificador(chave_alias + '_ordem')
            ranked = f'SELECT *, {chave} AS {key}, ROW_NUMBER() OVER (PARTITION BY {chave} ORDER BY {ordem} {"DESC" if repeticoes == "ultimo" else "ASC"}) AS "{chave_alias}_rn" FROM ({base}) WHERE {chave} IS NOT NULL'
            consulta = f'SELECT {key} AS k, {campos} FROM ({ranked}) WHERE "{chave_alias}_rn"=1'
        else:
            consulta = f'SELECT {key} AS k, {campos} FROM ({validos})'
    temp = '__join_' + uuid.uuid4().hex
    qtemp, qshape = identificador(temp), identificador(camada)
    total_fonte = conn.execute(f'SELECT COUNT(*) FROM ({filtrada})', params).fetchone()[0]
    if not total_fonte:
        raise ValueError('O recorte não contém linhas. Revise os filtros antes de aplicar o join.')
    conn.execute('SAVEPOINT join_mapa')
    try:
        conn.execute(f'CREATE TEMP TABLE {qtemp} AS {consulta}', params)
        conn.execute(f'CREATE UNIQUE INDEX {identificador(temp + "_idx")} ON {qtemp}(k)')
        correspondencias = conn.execute(f'SELECT COUNT(*) FROM {qshape} s WHERE EXISTS (SELECT 1 FROM {qtemp} j WHERE j.k=mapa_chave(s.{identificador(chave_shape)}))').fetchone()[0]
        if not correspondencias:
            raise ValueError('Nenhuma chave do shapefile corresponde à simulação. Confira as colunas e o modo texto/número.')
        for c, destino in destinos.items():
            if destino not in shape_cols:
                tipo = 'REAL' if repeticoes in agregacoes else ('INTEGER' if 'INT' in sim_cols[c].upper() else 'REAL' if any(t in sim_cols[c].upper() for t in ('REAL', 'FLOAT', 'NUM', 'DOUBLE')) else 'TEXT')
                conn.execute(f'ALTER TABLE {qshape} ADD COLUMN {identificador(destino)} {tipo}')
        # Colunas de um join anterior ficam vazias se não forem trazidas novamente.
        # Mantê-las preserva os grafos já salvos que as referenciam.
        anteriores = set(antigas.get('colunas', {}).values()) - set(destinos.values())
        for c in anteriores & set(shape_cols):
            conn.execute(f'UPDATE {qshape} SET {identificador(c)}=NULL')
        saidas = ', '.join(identificador(c) for c in destinos.values())
        valores = ', '.join(identificador(aliases[c]) for c in selecionadas)
        conn.execute(f'UPDATE {qshape} AS s SET ({saidas})=(SELECT {valores} FROM {qtemp} j WHERE j.k=mapa_chave(s.{identificador(chave_shape)}))')
        total_shape = conn.execute(f'SELECT COUNT(*) FROM {qshape}').fetchone()[0]
        estado = dict(fonte=fonte, chave_shape=chave_shape, chave_simulacao=chave_simulacao,
                      colunas=destinos, filtros=filtros or [], combinacao=combinacao,
                      repeticoes=repeticoes, modo_chave=modo_chave, linhas_fonte=total_fonte,
                      correspondencias=correspondencias, sem_correspondencia=total_shape-correspondencias)
        gravar_estado(conn, 'join:' + camada, estado)
        conn.execute(f'DROP TABLE {qtemp}')
        conn.execute('RELEASE join_mapa')
    except Exception:
        conn.execute('ROLLBACK TO join_mapa')
        conn.execute('RELEASE join_mapa')
        raise
    return estado


def chave_categoria(valor):
    if valor is None or isinstance(valor, float) and not math.isfinite(valor):
        return 'null'
    if isinstance(valor, (int, float)):
        return 'numero:' + format(Decimal(str(valor)).normalize(), 'f')
    return 'texto:' + str(valor)


def categorias(valores):
    resultado = {}
    for valor in valores:
        chave = chave_categoria(valor)
        if chave not in resultado:
            rotulo = 'Sem valor' if chave == 'null' else str(valor)
            resultado[chave] = {'valor': rotulo, 'quantidade': 0}
        resultado[chave]['quantidade'] += 1
    return resultado
