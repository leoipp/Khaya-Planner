"""Cadastro de dimensões e preço de tábuas; comprimento em m e seção em cm."""
from math import isfinite


def listar(conn):
    return [dict(zip(('id','nome','comprimento','espessura','largura','preco','rendimento'), row)) for row in
        conn.execute('SELECT id,nome,comprimento,espessura,largura,preco,rendimento FROM classes_tabuas ORDER BY nome,id')]


def salvar(conn, nome, comprimento, espessura, largura, preco, id_classe=None, rendimento=35):
    nome = nome.strip()
    if not nome:
        raise ValueError('Informe a classe de tábua.')
    if not all(isfinite(v) for v in (comprimento,espessura,largura,preco,rendimento)):
        raise ValueError('Informe dimensões e preço finitos.')
    if min(comprimento,espessura,largura) <= 0 or preco < 0:
        raise ValueError('As dimensões devem ser positivas e o preço não negativo.')
    if not 0 <= rendimento <= 100:
        raise ValueError('O rendimento deve estar entre 0 e 100%.')
    dados = (nome,comprimento,espessura,largura,preco,rendimento)
    if id_classe is None:
        return conn.execute('INSERT INTO classes_tabuas(nome,comprimento,espessura,largura,preco,rendimento) VALUES(?,?,?,?,?,?)',dados).lastrowid
    if conn.execute('UPDATE classes_tabuas SET nome=?,comprimento=?,espessura=?,largura=?,preco=?,rendimento=? WHERE id=?',dados+(id_classe,)).rowcount != 1:
        raise ValueError('Classe não encontrada; atualize a lista.')
    return id_classe



def carregar_classificacao_cerne(conn):
    import json
    from copy import deepcopy
    from .desdobro import CLASSES_MADEIRA_PADRAO
    row = conn.execute('SELECT dados FROM classificacao_cerne_estado WHERE id=1').fetchone()
    if row:
        return json.loads(row[0])
    # Reaproveita a configuração de estudos feitos antes do cadastro em Sortimentos.
    row = conn.execute('SELECT dados FROM toragem_estado WHERE id=1').fetchone()
    if row:
        regras = json.loads(row[0]).get('classes_madeira')
        if regras:
            return regras
    return deepcopy(CLASSES_MADEIRA_PADRAO)


def salvar_classificacao_cerne(conn, classes):
    import json
    from .desdobro import validar_classes
    validar_classes(classes)
    conn.execute('INSERT INTO classificacao_cerne_estado(id,dados) VALUES(1,?) '
                 'ON CONFLICT(id) DO UPDATE SET dados=excluded.dados',
                 (json.dumps(classes,ensure_ascii=False),))
