"""Consulta leve do conteúdo do projeto, sem carregar bases ou resultados."""


def consultar_projeto(conn):
    tabelas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def tem_dados(nome):
        # Os nomes recebidos vêm das constantes abaixo ou do próprio catálogo SQLite.
        seguro = '"' + nome.replace('"', '""') + '"'
        return nome in tabelas and conn.execute(f'SELECT 1 FROM {seguro} LIMIT 1').fetchone() is not None

    def contar(nome):
        return conn.execute(f'SELECT COUNT(*) FROM "{nome}"').fetchone()[0] if nome in tabelas else 0

    modelos = contar('modelos')
    sortimentos = contar('sortimentos')
    cenarios = gerados = erros = 0
    if 'simulacao_cenarios' in tabelas:
        cenarios, gerados, erros = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status='Gerado'),0), "
            "COALESCE(SUM(status LIKE 'Erro:%'),0) FROM simulacao_cenarios").fetchone()
    dimensoes = None
    if 'configuracoes' in tabelas:
        dimensoes = conn.execute('SELECT comprimento_tora,diametro_minimo_tora '
                                 'FROM configuracoes WHERE id=1').fetchone()
    dimensoes_ok = bool(dimensoes and dimensoes[0] is not None and dimensoes[0] > 0
                       and dimensoes[1] is not None and dimensoes[1] >= 0)
    bases = [nome for nome in ('base_ifc_talhao', 'base_ifc_arvore') if tem_dados(nome)]
    weibull = any(tem_dados(nome) for nome in (
        'weibull_ifc_talhao', 'weibull_ifc_plot', 'parametros_weibull_manejo'))
    simulacao = gerados > 0 or tem_dados('simulacao_talhao_idade')
    resultados_planejamento = [nome for nome in sorted(tabelas)
                              if nome.startswith('resultado_otimizacao_') and tem_dados(nome)]
    etapas = [
        dict(chave='inventario', titulo='Inventário', tela='configuracoes',
             presente=bool(bases), detalhe='Base de inventário encontrada.' if bases
             else 'Importe a base por talhão ou por árvore em Dados e manutenção.'),
        dict(chave='modelos', titulo='Modelos', tela='modelos', presente=modelos > 0,
             detalhe=f'{modelos} modelo(s) cadastrado(s).' if modelos
             else 'Cadastre as equações e os coeficientes utilizados no projeto.'),
        dict(chave='sortimentos', titulo='Sortimentos e toretes', tela='sortimentos',
             presente=sortimentos > 0 and dimensoes_ok,
             detalhe=f'{sortimentos} classe(s) comercial(is); '
                     + ('dimensões informadas.' if dimensoes_ok else 'revise as dimensões dos toretes.')),
        dict(chave='weibull', titulo='Distribuição diamétrica', tela='weibull_ifc', presente=weibull,
             detalhe='Parâmetros de Weibull encontrados.' if weibull
             else 'Ajuste as distribuições por talhão, parcela ou simulação.'),
        dict(chave='simulacao', titulo='Simulação', tela='simulacao', presente=simulacao,
             detalhe=f'{gerados} de {cenarios} cenário(s) marcado(s) como gerado(s).'
             if cenarios else ('População simulada encontrada.' if simulacao
                              else 'Defina o manejo e gere a população ou um conjunto de cenários.')),
        dict(chave='planejamento', titulo='Planejamento', tela='planejamento',
             presente=bool(resultados_planejamento),
             detalhe=f'{len(resultados_planejamento)} resultado(s) encontrado(s) neste projeto.'
             if resultados_planejamento else 'Selecione os cenários e configure as metas de produção.'),
    ]
    return dict(modelos=modelos, sortimentos=sortimentos, cenarios=cenarios, gerados=gerados,
                erros=erros, etapas=etapas)
