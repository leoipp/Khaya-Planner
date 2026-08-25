# -*- coding: utf-8 -*-
"""
Catálogo de ícones do app — nomes semânticos (usados pelas telas) mapeados
pra ícones vetoriais do conjunto Phosphor (via qtawesome, prefixo `ph.`),
um dos grandes pacotes de ícones livres/abertos que também alimentam a
maioria dos pacotes "free UI icons" — como o pedido não dava pra puxar
direto de um arquivo Figma (canvas renderizado, sem acesso programático
sem token da API), esta é a alternativa combinada com o usuário: mesmo
espírito (ícones de linha, minimalistas, um conjunto grande e consistente),
só que já embutido no app, sem depender de arquivo externo.

`QIcon` não é alcançado pela stylesheet Qt (ao contrário de cor/fonte de
widget) — por isso `aplicar_icone` existe: registra o widget pra ter o
ícone regenerado (na cor certa) a cada troca de tema, em vez de precisar
de um ouvinte de tema manual em cada tela que usa ícone.
"""
import qtawesome as qta

from . import manager as tema

# ---- navegação (barra lateral) --------------------------------------------
_NAVEGACAO = {
    "modelos": "cube",
    "sortimentos": "tree-evergreen",
    "custos": "currency-dollar",
    "weibull_ifc": "chart-bar",
    "simulacao": "chart-line-up",
    "construtor_variaveis": "flow-arrow",
    "ingressos_curvas": "trend-up",
    "resumos_cenarios": "database",
    "configuracoes": "gear",
}

# ---- ações recorrentes nas telas -------------------------------------------
_ACOES = {
    "novo_projeto": "file-plus",
    "abrir_projeto": "folder-open",
    "salvar": "floppy-disk",
    "adicionar": "plus",
    "excluir": "trash",
    "limpar": "eraser",
    "importar": "upload-simple",
    "exportar": "download-simple",
    "zoom_mais": "magnifying-glass-plus",
    "zoom_menos": "magnifying-glass-minus",
    "redefinir": "arrow-counter-clockwise",
    "atualizar": "arrow-clockwise",
    "maximizar": "arrows-out",
    "restaurar": "arrows-in",
    "abrir": "folder-open",
    "novo": "file-plus",
    "duplicar": "copy",
    # "remover" (tira algo de uma lista em edição, ex: uma variável ainda
    # não salva) é visualmente mais leve que "excluir" (apaga um registro
    # de verdade do banco) — mesmo ícone (lixeira), cor neutra em vez de
    # vermelha (ver _ACOES_PERIGO abaixo, só "excluir" entra nela).
    "remover": "trash",
    "ativar_desativar": "toggle-left",
    "configurar": "sliders",
    "ajustar": "magic-wand",
    "previa": "eye",
    "gerar": "play",
    "parar": "stop-circle",
    "compactar": "database",
    "buscar": "magnifying-glass",
    "afilamento": "tree-evergreen",
    "fechar": "x",
    "nos_especiais": "squares-four",
    "colunas": "columns",
    "modelos": "cube",
    "cor_borda": "palette",
    # Botão fora do cartão da barra lateral (BotaoAlternarBarraLateral,
    # ver app/widgets/barra_lateral.py) — navicon/hamburger (3 linhas),
    # mesmo ícone nos dois estados (não indica direção/efeito como as
    # setas antigas — é só o símbolo universal de "menu"). "fa.navicon"
    # (nome literal pedido) não existe nesta versão do qtawesome — o
    # prefixo "fa" (Font Awesome 4, onde "navicon" era um alias de
    # "bars") foi descontinuado, e nem "fa5.navicon" existe (renomeado
    # "bars" na v5). "list" é o mesmo desenho (3 linhas) dentro do
    # Phosphor, consistente com o resto do catálogo.
    "recolher_barra_lateral": "list",
    "expandir_barra_lateral": "list",
}

_CATALOGO = {**_NAVEGACAO, **_ACOES}

# Ícones de ação destrutiva (excluir um registro, remover um item de uma
# lista/entrada) — vermelho por padrão (tokens.PALETA_STATUS["perigo"]),
# convenção comum de UI pra destacar "isso apaga algo". Só o ícone; o
# fundo/borda do botão continuam neutros (ver QPushButton normal em
# app/theme/qss.py) — não precisa de outra variante de botão só pra isso.
_ACOES_PERIGO = {"excluir"}


def _nome_phosphor(nome):
    if nome not in _CATALOGO:
        raise KeyError(f'Ícone "{nome}" não cadastrado em app/theme/icones.py')
    return f"ph.{_CATALOGO[nome]}"


def icone(nome, cor=None):
    """QIcon pro ícone semântico `nome`, na cor `cor` (hex) ou, se omitida,
    na cor de primeiro-plano do tema em vigor (vermelho de perigo, pros
    ícones de ação destrutiva em `_ACOES_PERIGO`)."""
    if cor is None:
        cor = tema.obter().cor_status("perigo") if nome in _ACOES_PERIGO else tema.obter().cor_ui("fg")
    return qta.icon(_nome_phosphor(nome), color=cor)


def aplicar_icone(botao, nome, cor=None):
    """Define o ícone de um QPushButton/QToolButton e mantém ele recolorido
    a cada troca de tema. `cor` fixa (ex: branco, pra um botão de destaque
    com fundo colorido) pula a lógica de recolorir — nesse caso o ícone já
    nasce na cor certa e não muda com o tema."""
    def _atualizar(_modo=None):
        botao.setIcon(icone(nome, cor=cor))
    _atualizar()
    if cor is None:
        tema.obter().themeChanged.connect(_atualizar)
