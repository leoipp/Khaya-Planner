"""Consultas somente de leitura, paginação e exportação em fluxo."""
from contextlib import closing
from pathlib import Path
import sqlite3
import uuid
import xlsxwriter


def identificador(nome):return '"'+str(nome).replace('"','""')+'"'


def conectar(caminho):
    c=sqlite3.connect(Path(caminho).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    c.execute('PRAGMA query_only=ON')
    def autorizar(acao,a,b,banco,origem):
        permitidos=(sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE)
        if acao not in permitidos or (acao==sqlite3.SQLITE_FUNCTION and str(b).lower()=='load_extension'):return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    c.set_authorizer(autorizar)
    return c


def tabelas(caminho):
    with closing(conectar(caminho)) as c:
        return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def normalizar(sql):
    texto=sql.strip().rstrip(';').strip()
    if not texto:raise ValueError('Selecione uma tabela ou escreva uma consulta SELECT.')
    return texto


def consulta_tabela(caminho,tabela):
    if tabela not in tabelas(caminho):raise ValueError('Tabela não encontrada.')
    return 'SELECT * FROM '+identificador(tabela)


def preparar(c,sql):
    sql=normalizar(sql)
    cursor=c.execute(f'SELECT * FROM ({sql}) AS q LIMIT 0')
    nomes=[d[0] for d in cursor.description]
    # BLOBs podem conter cenários Parquet de vários GB; não os transfere para a interface.
    campos=[f"CASE WHEN typeof(q.{identificador(n)})='blob' THEN '[BLOB '||length(q.{identificador(n)})||' bytes]' ELSE q.{identificador(n)} END AS {identificador(n)}" for n in nomes]
    return nomes,f'SELECT {", ".join(campos)} FROM ({sql}) AS q'


def pagina(caminho,sql,numero=1,tamanho=500):
    if numero<1 or not 1<=tamanho<=10000:raise ValueError('Página ou tamanho inválido.')
    with closing(conectar(caminho)) as c:
        nomes,consulta=preparar(c,sql)
        linhas=c.execute(consulta+' LIMIT ? OFFSET ?',(tamanho+1,(numero-1)*tamanho)).fetchall()
    return dict(colunas=nomes,linhas=linhas[:tamanho],mais=len(linhas)>tamanho,pagina=numero,tamanho=tamanho)


def exportar_excel(caminho,sql,destino,progresso=lambda n:None,linhas_por_aba=1048575):
    destino=Path(destino)
    if destino.suffix.lower()!='.xlsx':raise ValueError('Escolha um arquivo .xlsx.')
    if destino.resolve()==Path(caminho).resolve():raise ValueError('O destino deve ser diferente do banco.')
    if not 1<=linhas_por_aba<=1048575:raise ValueError('Quantidade de linhas por aba inválida.')
    temporario=destino.with_name(destino.stem+'.'+uuid.uuid4().hex+'.tmp.xlsx');total=0;aba=0;linha=0
    try:
        with closing(conectar(caminho)) as c:
            nomes,consulta=preparar(c,sql)
            if len(nomes)>16384:raise ValueError('Excel permite no máximo 16.384 colunas por aba.')
            with xlsxwriter.Workbook(str(temporario),{'constant_memory':True,'strings_to_formulas':False,'strings_to_urls':False,'nan_inf_to_errors':True}) as livro:
                cab=livro.add_format({'bold':True,'bg_color':'#DDEEEF'})
                folha=None
                cursor=c.execute(consulta)
                while True:
                    lote=cursor.fetchmany(max(1,min(2000,50000//max(1,len(nomes)))))
                    if not lote and folha is not None:break
                    for valores in lote or [None]:
                        if folha is None or linha>linhas_por_aba:
                            aba+=1;folha=livro.add_worksheet(f'Dados_{aba}');folha.write_row(0,0,nomes,cab);folha.freeze_panes(1,0);linha=1
                            folha.set_column(0,len(nomes)-1,20)
                        if valores is not None:
                            if any(isinstance(v,str) and len(v)>32767 for v in valores):raise ValueError('Uma célula ultrapassa 32.767 caracteres, limite do Excel. Ajuste a consulta SQL.')
                            folha.write_row(linha,0,valores);linha+=1;total+=1
                    progresso(total)
                    if not lote:break
        temporario.replace(destino)
    finally:temporario.unlink(missing_ok=True)
    return dict(linhas=total,abas=aba,destino=str(destino))


def dados_grafico(caminho,sql,x,y,agregacao='sum',limite=10000):
    if agregacao not in ('sum','avg','count','raw'):raise ValueError('Agregação inválida.')
    with closing(conectar(caminho)) as c:
        nomes,consulta=preparar(c,sql)
        if x not in nomes or (agregacao!='count' and y not in nomes):raise ValueError('Selecione as colunas X e Y.')
        xx=identificador(x);yy=identificador(y)
        if agregacao!='count':
            invalido=c.execute(f"SELECT 1 FROM ({consulta}) WHERE {yy} IS NOT NULL AND typeof({yy}) NOT IN ('integer','real') LIMIT 1").fetchone()
            if invalido:raise ValueError('A coluna Y precisa ser numérica. Converta-a na consulta SQL ou escolha outra coluna.')
        if agregacao=='raw':q=f'SELECT {xx},{yy} FROM ({consulta}) WHERE {yy} IS NOT NULL'
        else:
            funcao='COUNT(*)' if agregacao=='count' else f'{agregacao.upper()}({yy})'
            q=f'SELECT {xx},{funcao} FROM ({consulta}) GROUP BY {xx} ORDER BY {xx}'
        linhas=c.execute(q+' LIMIT ?',(limite+1,)).fetchall()
        if len(linhas)>limite:raise ValueError(f'O gráfico tem mais de {limite:,} pontos. Agrupe ou filtre a consulta SQL.')
    return linhas
