# -*- coding: utf-8 -*-
import ctypes
import shutil
import sqlite3
import sys
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS modelos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL,
    estrato_coluna TEXT,
    estrato TEXT,
    variavel_x TEXT,
    equacao TEXT,
    coeficientes TEXT,
    observacoes TEXT,
    criado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Sortimentos (classificação comercial de toras por faixa de diâmetro,
-- ex: "Fino" 8-15cm, "Serraria" 15-35cm, ...), cadastrados na tela
-- Configurações (app/screens/configuracoes.py). Dois preços por
-- sortimento — `preco` (Madeira Serrada, R$/m³ de produto já
-- desdobrado) e `preco_pe` (Madeira em Pé, R$/m³ da árvore em pé antes
-- da colheita/desdobro) — o nó "Receita Total" do Construtor de
-- Variáveis escolhe qual dos dois usar (botão direito no nó, padrão
-- Madeira Serrada — ver core/construtores.py:_preco_sortimento_da_classe).
CREATE TABLE IF NOT EXISTS sortimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    limite_inferior REAL,
    limite_superior REAL,
    rendimento REAL,
    preco REAL,
    preco_pe REAL,
    criado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Custo de formação florestal (preparo de solo, plantio, manutenção etc.),
-- cadastrado na tela Configurações (app/screens/configuracoes.py) —
-- mesmo CRUD de sortimentos acima, mas casado por idade (`ano`, contado
-- desde o plantio) em vez de faixa de diâmetro: ver
-- core/construtores.py:avaliar_grafo/obter_custos_formacao — o nó "Custo
-- de Formação" do Construtor de Variáveis soma `custo` de toda linha cujo
-- `ano` bate com idade_simulada numa coluna da população (não calculado
-- automaticamente — o usuário monta e salva esse nó lá).
CREATE TABLE IF NOT EXISTS custos_formacao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    ano REAL,
    custo REAL,
    criado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Custo de colheita (corte, baldeio, transporte...), cadastrado na tela
-- Configurações — mesmo CRUD de custos_formacao acima, mas sem casamento
-- por idade; parâmetros da fórmula clássica de custo de colheita
-- florestal (Custo Hora Máquina / (Produtividade × Disponibilidade
-- Mecânica × Eficiência Operacional) = custo por m³), não o custo por m³
-- já calculado — só cadastro por enquanto, sem cálculo nem aplicação
-- automática na simulação. Produtividade não é um valor único (varia por
-- classe diamétrica — árvore mais grossa demora mais pra processar) —
-- ver custo_colheita_produtividade abaixo.
CREATE TABLE IF NOT EXISTS custos_colheita (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    custo_hora_maquina REAL,
    disponibilidade_mecanica REAL,
    eficiencia_operacional REAL,
    criado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Produtividade (m³/HM) por classe diamétrica de um custo de colheita —
-- editada na tela Configurações via diálogo "+" (uma linha por classe,
-- da primeira à última configurada). ON DELETE CASCADE porque
-- conectar_caminho já liga PRAGMA foreign_keys = ON: excluir um custo de
-- colheita já limpa as linhas dele sozinho.
CREATE TABLE IF NOT EXISTS custo_colheita_produtividade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    custo_colheita_id INTEGER NOT NULL,
    classe REAL NOT NULL,
    produtividade REAL,
    FOREIGN KEY (custo_colheita_id) REFERENCES custos_colheita(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS parametros_weibull_manejo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    talhao TEXT NOT NULL,
    manejo TEXT NOT NULL,
    intensidade REAL NOT NULL,
    escala REAL NOT NULL,
    forma REAL NOT NULL,
    dap_med REAL,
    dap_max REAL,
    dap_min REAL,
    fustes_ha_removidos REAL,
    ht_med REAL,
    vtcc_ha_removido REAL,
    cv_dap REAL,
    dg REAL,
    int_raleio REAL,
    int_desbaste_1 REAL,
    int_desbaste_2 REAL,
    observacoes TEXT,
    truncado_esquerda INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS weibull_ifc_metadados (
    tabela TEXT PRIMARY KEY,
    colunas_chave_origem TEXT NOT NULL,
    colunas_chave_destino TEXT NOT NULL,
    coluna_valores TEXT NOT NULL,
    minimo_observacoes INTEGER NOT NULL,
    remover_nao_positivos INTEGER NOT NULL,
    atualizado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS simulacao_metadados (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    coluna_talhao_ifc TEXT,
    coluna_fustes_observados_ifc TEXT,
    coluna_ht_observado_ifc TEXT,
    coluna_vtcc_observado_ifc TEXT
);

-- Cenários do modo "Múltiplos cenários" da tela Simulação
-- (app/screens/simulacao.py): cada linha é uma combinação completa de
-- idade/intensidade por evento. Gerar um cenário grava o resultado em
-- tabelas próprias, sufixadas "__cenario{id}" (ver
-- app/simulacao.py:gerar_populacao/ativar_cenario) — não mexe nas tabelas
-- canônicas (simulacao_talhao_idade etc.) até o usuário "ativar" esse
-- cenário, que copia as tabelas sufixadas por cima das canônicas.
CREATE TABLE IF NOT EXISTS simulacao_cenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    idade_raleio INTEGER,
    intensidade_raleio REAL,
    idade_desbaste_1 INTEGER,
    intensidade_desbaste_1 REAL,
    idade_desbaste_2 INTEGER,
    intensidade_desbaste_2 REAL,
    idade_corte_raso INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pendente',
    gerado_em TEXT,
    criado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Resultado detalhado de cenários em formato colunar comprimido. Cada
-- cenário ocupa uma única linha/BLOB por componente em vez de milhões de
-- linhas nas antigas simulacao_lote_*. Mantê-lo dentro do SQLite preserva
-- o .mogno como arquivo único e portátil.
CREATE TABLE IF NOT EXISTS simulacao_cenarios_parquet (
    cenario_id INTEGER PRIMARY KEY,
    formato_populacao TEXT NOT NULL DEFAULT 'parquet',
    populacao BLOB NOT NULL,
    distribuicao BLOB NOT NULL,
    metadados BLOB NOT NULL,
    atualizado_em TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (cenario_id) REFERENCES simulacao_cenarios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS resumos_cenarios_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE,
    configuracao_json TEXT NOT NULL,
    atualizado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Grafos salvos na tela "Construtor de Variáveis" (app/screens/construtor_variaveis.py):
-- `grafo` guarda nós+ligações como JSON. Reaplicados automaticamente pra
-- `tabela_origem` sempre que ela é regerada do zero (ver
-- app/construtores.py:aplicar_construtores_salvos, chamado por
-- app/screens/simulacao.py depois de "Gerar simulação") — sem isso, as
-- colunas geradas por um construtor sumiam a cada nova simulação, já que
-- simulacao_talhao_idade é recriada (DROP + CREATE) a cada execução.
CREATE TABLE IF NOT EXISTS construtores_variaveis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    tabela_origem TEXT NOT NULL,
    grafo TEXT NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT DEFAULT (datetime('now', 'localtime')),
    atualizado_em TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS configuracoes (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    primeira_classe_diametrica REAL,
    ultima_classe_diametrica REAL,
    intervalo_classe REAL NOT NULL DEFAULT 1,
    corrigir_mnp INTEGER NOT NULL DEFAULT 0,
    idade_maxima_manejo REAL,
    taxa_desconto REAL,
    ano_referencia INTEGER,
    base_periodo_vpl TEXT NOT NULL DEFAULT 'ano_referencia',
    pis REAL,
    cofins REAL,
    funrural REAL,
    rendimento_serraria REAL,
    passo_intensidade REAL,
    intensidade_minima REAL,
    intensidade_maxima REAL,
    numero_minimo_arvores_ha REAL,
    comprimento_tora REAL,
    diametro_minimo_tora REAL,
    usar_tabela_afilamento INTEGER NOT NULL DEFAULT 0,
    truncar_esquerda_padrao INTEGER NOT NULL DEFAULT 0,
    tipo_normalizacao_weibull TEXT NOT NULL DEFAULT 'aditiva',
    ajuste_manejo_padrao INTEGER NOT NULL DEFAULT 0,
    base_ajuste_logistico TEXT NOT NULL DEFAULT 'ip',
    base_calculo_mip TEXT NOT NULL DEFAULT 'fdp',
    dias_trabalho REAL,
    horas_trabalho REAL
);
"""

# Colunas adicionadas depois da criação original das tabelas abaixo.
# CREATE TABLE IF NOT EXISTS não altera uma tabela que já existe, então
# projetos (.mogno) criados antes dessas colunas existirem precisam do
# ALTER TABLE em _adicionar_colunas_faltantes pra não quebrar ao abrir.
COLUNAS_NOVAS = {
    "configuracoes": {
        "passo_intensidade": "REAL",
        "intensidade_minima": "REAL",
        "intensidade_maxima": "REAL",
        "idade_maxima_manejo": "REAL",
        "numero_minimo_arvores_ha": "REAL",
        "comprimento_tora": "REAL",
        "diametro_minimo_tora": "REAL",
        "usar_tabela_afilamento": "INTEGER NOT NULL DEFAULT 0",
        "truncar_esquerda_padrao": "INTEGER NOT NULL DEFAULT 0",
        "tipo_normalizacao_weibull": "TEXT NOT NULL DEFAULT 'aditiva'",
        "ajuste_manejo_padrao": "INTEGER NOT NULL DEFAULT 0",
        "base_ajuste_logistico": "TEXT NOT NULL DEFAULT 'ip'",
        "base_calculo_mip": "TEXT NOT NULL DEFAULT 'fdp'",
        "base_periodo_vpl": "TEXT NOT NULL DEFAULT 'ano_referencia'",
        "dias_trabalho": "REAL",
        "horas_trabalho": "REAL",
    },
    "parametros_weibull_manejo": {
        "int_raleio": "REAL",
        "int_desbaste_1": "REAL",
        "int_desbaste_2": "REAL",
        "dap_med": "REAL",
        "dap_max": "REAL",
        "dap_min": "REAL",
        "fustes_ha_removidos": "REAL",
        "ht_med": "REAL",
        "vtcc_ha_removido": "REAL",
        "cv_dap": "REAL",
        "dg": "REAL",
        "truncado_esquerda": "INTEGER NOT NULL DEFAULT 0",
    },
    "simulacao_metadados": {
        "coluna_fustes_observados_ifc": "TEXT",
        "coluna_dap_med_observado_ifc": "TEXT",
        "coluna_dap_max_observado_ifc": "TEXT",
        "coluna_dap_min_observado_ifc": "TEXT",
        "coluna_forma_distribuicao_ifc": "TEXT",
        "coluna_escala_distribuicao_ifc": "TEXT",
        "coluna_ht_observado_ifc": "TEXT",
        "coluna_vtcc_observado_ifc": "TEXT",
        "coluna_cv_dap_observado_ifc": "TEXT",
        "coluna_data_plantio_ifc": "TEXT",
        "coluna_data_medicao_ifc": "TEXT",
        "coluna_base_volume_classes": "TEXT",
        "tipo_agregacao_volume": "TEXT",
        "usar_tabela_agregacao_volume": "INTEGER NOT NULL DEFAULT 0",
    },
    "modelos": {
        "estrato_coluna": "TEXT",
        "variavel_x": "TEXT",
        "variaveis_x": "TEXT",
    },
    "construtores_variaveis": {
        "ativo": "INTEGER NOT NULL DEFAULT 1",
    },
    "sortimentos": {
        "rendimento": "REAL",
        "preco": "REAL",
        "preco_pe": "REAL",
    },
}

# Índices em tabelas fixas do schema (as dinâmicas, criadas em runtime por
# app/intensidades.py, indexam a si mesmas depois de popular os dados —
# ver _criar_indices_resultado lá). parametros_weibull_manejo pode chegar
# a centenas de milhares de linhas (um grupo por talhão/intensidades/
# etapa testados na Simulação de Intensidades) e é consultada por uma
# combinação exata de intensidades toda vez que "Gerar simulação" roda
# (app/simulacao.py:obter_ajuste_weibull_por_simulacao) — sem índice, isso
# é um full scan da tabela inteira a cada clique.
INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_pwm_intensidades ON parametros_weibull_manejo "
    "(int_raleio, int_desbaste_1, int_desbaste_2)",
]


def _ram_disponivel_bytes():
    """RAM fisicamente livre/reclamável AGORA (não o total instalado) —
    usada por `_cache_size_kib` pra dimensionar o cache do SQLite conforme
    a máquina. None se não for possível detectar (SO sem suporte, erro de
    API) — quem chama cai pro piso fixo de sempre (`_CACHE_MB_PADRAO`)
    nesse caso, mesmo comportamento de antes desta função existir.

    Só Windows (`GlobalMemoryStatusEx`, kernel32) e Linux (`MemAvailable`
    de /proc/meminfo) têm implementação — os dois ambientes reais de uso
    do app; qualquer outro SO cai no fallback."""
    if sys.platform == "win32":
        try:
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullAvailPhys)
        except Exception:
            return None

    try:
        with open("/proc/meminfo") as arquivo:
            for linha in arquivo:
                if linha.startswith("MemAvailable:"):
                    return int(linha.split()[1]) * 1024  # linha vem em KiB
    except Exception:
        pass
    return None


# Piso: mesmo valor fixo de sempre (também o fallback quando a RAM
# disponível não pode ser detectada). Teto: 8 GB — mesmo numa máquina com
# centenas de GB livres, nada aqui costuma se beneficiar de mais que isso
# de cache (o dado por execução tem o tamanho que tem, não cresce junto
# com a RAM da máquina — ver fração abaixo), e travar um pedaço grande
# demais da RAM disponível no cache do SQLite tiraria RAM do resto do
# processo (os próprios DataFrames pandas da simulação) e do SO à toa.
_CACHE_MB_PADRAO = 64
_CACHE_MB_TETO = 8192
# Fração conservadora da RAM LIVRE (não da instalada) dedicada ao cache —
# o resto fica pro SO, outros programas, e o processo Python em si
# (pd.read_sql_query carrega tabelas inteiras em DataFrame, à parte desse
# cache). Em 16 GB livres dá ~512 MB de cache; em 256 GB livres bate no
# teto de 8 GB.
_FRACAO_RAM_LIVRE_PARA_CACHE = 1 / 32


def _cache_size_kib():
    disponivel = _ram_disponivel_bytes()
    if disponivel is None:
        return _CACHE_MB_PADRAO * 1024
    cache_mb = (disponivel / (1024 * 1024)) * _FRACAO_RAM_LIVRE_PARA_CACHE
    cache_mb = max(_CACHE_MB_PADRAO, min(_CACHE_MB_TETO, cache_mb))
    return int(cache_mb * 1024)


def _aplicar_pragmas_performance(conn):
    """O banco de trabalho é uma cópia temporária descartável (recriada do
    zero a partir do .mogno a cada `projeto.abrir_projeto`, e regravada
    inteira nele por `projeto.sincronizar` — nunca é o arquivo final).
    Não precisa da durabilidade padrão do SQLite (fsync a cada commit):
    - synchronous=OFF evita o fsync, que domina o tempo de commits de
      simulações grandes (centenas de milhares de linhas);
    - temp_store/cache maiores evitam spill em disco nos GROUP BY/ORDER BY
      das agregações (ex: resumo por talhão) e nas leituras grandes
      (pd.read_sql_query sobre intensidades_detalhamento). cache_size é
      dimensionado conforme a RAM livre da máquina (ver _cache_size_kib) —
      num projeto grande, uma máquina com mais RAM livre consegue manter
      mais páginas do banco em cache entre operações (menos releitura de
      disco); numa máquina sem RAM de sobra (ou onde a detecção falhar),
      cai pro mesmo piso fixo de ~64 MB de sempre.
    Journal WAL foi cogitado e descartado: `projeto.sincronizar()` lê os
    bytes do arquivo de trabalho direto do disco (sem passar por uma
    conexão SQLite) — em WAL, commits recentes ficam num arquivo -wal
    separado até um checkpoint, e sincronizar() os perderia silenciosamente
    (perda de dados). journal_mode padrão (DELETE) sempre deixa o estado
    completo no arquivo principal assim que a transação comita.

    busy_timeout evita "database is locked": o arquivo de trabalho é
    acessado por várias conexões concorrentes (a UI, threads de fundo das
    telas de simulação/ajuste, e a conexão de leitura que
    projeto._sincronizar_agora abre pra fazer o backup() pro .mogno) — sem
    isso, o padrão do sqlite3 é falhar na hora (timeout 0) assim que
    alguma dessas conexões encontra a outra segurando o lock, em vez de
    esperar ela liberar."""
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute(f"PRAGMA cache_size = -{_cache_size_kib()}")


def inicializar_schema(caminho):
    conn = sqlite3.connect(caminho)
    try:
        _aplicar_pragmas_performance(conn)
        conn.executescript(SCHEMA)
        for tabela, colunas in COLUNAS_NOVAS.items():
            _adicionar_colunas_faltantes(conn, tabela, colunas)
        for indice_sql in INDICES:
            conn.execute(indice_sql)
        conn.commit()
    finally:
        conn.close()


def _adicionar_colunas_faltantes(conn, tabela, colunas):
    """`colunas`: {nome: tipo_sql}. Comparação por minúsculas (não só o
    nome exato) — SQLite não diferencia maiúsculas de minúsculas em nome
    de coluna, então uma tentativa de ADD COLUMN que só difere no case de
    uma já existente quebra com "duplicate column name" mesmo sem colisão
    nenhuma "de verdade" (mesmo motivo de core/construtores.py:
    verificar_colisao_saidas/gravar_saidas_como_colunas). `nome`/`tipo`
    sempre entre aspas duplas: os únicos usos originais (COLUNAS_NOVAS,
    aqui perto) são identificadores simples que nunca precisariam, mas
    core/simulacao.py:_garantir_tabelas_lote reusa esta função pra nomes
    de coluna arbitrários (saída de construtor de variáveis, cabeçalho da
    planilha importada) que podem ter espaço/acento/parênteses — sem
    aspas, isso quebraria a sintaxe do ALTER TABLE."""
    existentes_lower = {
        linha[1].lower() for linha in conn.execute(f'PRAGMA table_info("{tabela}")')
    }
    for nome, tipo in colunas.items():
        if nome.lower() not in existentes_lower:
            conn.execute(f'ALTER TABLE "{tabela}" ADD COLUMN "{nome}" {tipo}')
            existentes_lower.add(nome.lower())


# Nomes de tabela "de sempre", exatos (sem sufixo) — o schema estático
# acima (SCHEMA) mais as recriadas em runtime (DROP + CREATE dinâmico,
# colunas dependem da planilha importada) por core/simulacao.py,
# core/weibull_ifc.py, core/intensidades.py e core/importador.py —
# replicadas aqui à mão (dá pra importar essas constantes direto sem
# risco de import circular já que são elas que importam db.py, não o
# contrário, mas manter só os nomes crus evita esse acoplamento por uma
# lista que quase nunca muda). Uma tabela sufixada "__cenarioN" (modo
# "Múltiplos cenários" da tela Simulação — ver simulacao.ativar_cenario)
# NÃO entra aqui de propósito: mesmo sendo gerada normalmente pelo app,
# ainda é dado de simulação descartável/regenerável (o só a versão SEM
# sufixo, a "ativa", é essencial de verdade) — ver
# listar_tabelas_nao_essenciais, que por isso a lista junto de tabela
# órfã/desconhecida. Usado só por listar_tabelas_nao_essenciais/
# excluir_tabelas (tela Configurações, "Manutenção do banco") — nada mais
# lê isto.
TABELAS_ESSENCIAIS = frozenset({
    # schema estático (ver SCHEMA acima)
    "modelos", "sortimentos", "custos_formacao", "custos_colheita",
    "custo_colheita_produtividade", "parametros_weibull_manejo",
    "weibull_ifc_metadados", "simulacao_metadados", "simulacao_cenarios",
    "construtores_variaveis", "configuracoes",
    # recriadas em runtime pela importação/geração da simulação
    "base_ifc_talhao", "base_ifc_arvore", "weibull_ifc_talhao", "weibull_ifc_plot",
    "intensidades_resumo_talhao", "intensidades_resumo_parcela", "intensidades_detalhamento",
    "simulacao_talhao_idade", "simulacao_distribuicao_diametrica", "simulacao_volume_sortimento",
    "simulacao_mip_continuo", "simulacao_mip_ajuste_logistico",
    # tabelas unificadas do lote "Múltiplos cenários" (uma linha por
    # cenário, coluna cenario_id — ver core/simulacao.py:persistir_
    # cenario_no_lote) — protegidas aqui pelo MESMO motivo que as
    # canônicas acima: ao contrário das antigas tabelas "__cenarioN"
    # (uma por cenário, exclusão granular), estas guardam TODOS os
    # cenários do projeto juntos — sem essa entrada, "Manutenção do
    # banco" deixaria excluir o lote inteiro num clique só.
    "simulacao_lote_populacao", "simulacao_lote_distribuicao_diametrica",
    "simulacao_lote_volume_sortimento", "simulacao_lote_mip_continuo",
    "simulacao_lote_mip_ajuste_logistico",
})


def listar_tabelas_nao_essenciais(conn):
    """Toda tabela do arquivo de trabalho que NÃO é uma das tabelas
    "de sempre" do app, pelo nome EXATO (ver TABELAS_ESSENCIAIS — sem
    tirar sufixo "__cenarioN"). Isso inclui de propósito as tabelas de
    cada cenário do modo "Múltiplos cenários" (ex:
    "simulacao_talhao_idade__cenario5") — são geradas normalmente pelo
    app, mas continuam sendo dado de simulação descartável/regenerável
    (não a versão "ativa", sem sufixo, que é essencial de verdade),
    então valem aparecer aqui pra quem quiser liberar espaço sem passar
    pela tela Simulação — mesma tabela `simulacao_cenarios` que registra
    esses cenários é varrida à parte por
    simulacao.limpar_registro_cenarios_orfaos depois de excluir_tabelas,
    pra não sobrar um cenário "Gerado" sem tabela nenhuma por trás.
    Cobre também a tabela de um recurso removido do app (ex: um antigo
    ajuste "expolinear" de ITD, descontinuado — ver core/simulacao.py) e
    uma tabela criada manualmente fora do app. Só usada pela tela
    Configurações ("Manutenção do banco") pra listar candidatas a exclusão
    manual — nenhum fluxo automático chama isto. Devolve [(nome, nº de
    linhas)], ordenado por nome; tabelas internas do SQLite (sqlite_*)
    nunca entram."""
    nomes = [
        linha[0] for linha in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
        ).fetchall()
    ]
    nao_essenciais = [nome for nome in nomes if nome not in TABELAS_ESSENCIAIS]
    resultado = [
        (nome, conn.execute(f'SELECT COUNT(*) FROM "{nome}"').fetchone()[0])
        for nome in nao_essenciais
    ]
    resultado.sort(key=lambda item: item[0].lower())
    return resultado


def excluir_tabelas(conn, nomes) -> None:
    """DROP TABLE de cada nome em `nomes` (tela Configurações, "Manutenção
    do banco" — ver listar_tabelas_nao_essenciais, de onde os nomes
    mostrados na tela vêm). Reconfirma contra TABELAS_ESSENCIAIS aqui
    também, pelo nome EXATO (ignora silenciosamente um nome essencial em
    vez de excluir) — rede de segurança barata contra excluir sem querer
    uma tabela que o app espera existir, mesmo que quem chamou já tenha
    filtrado antes. Não mexe em `simulacao_cenarios` (ver
    simulacao.limpar_registro_cenarios_orfaos, chamada à parte por quem
    chama isto — mantém esta função só sobre tabelas de verdade, sem
    precisar saber o que uma tabela "__cenarioN" representa). Commita no
    final."""
    for nome in nomes:
        if nome in TABELAS_ESSENCIAIS:
            continue
        conn.execute(f'DROP TABLE IF EXISTS "{nome}"')
    conn.commit()


def conectar():
    """Conecta no banco de trabalho do projeto atualmente aberto.
    Levanta RuntimeError se nenhum projeto estiver aberto."""
    from . import projeto  # import tardio: evita ciclo com projeto.py

    return conectar_caminho(projeto.caminho_trabalho())


def conectar_caminho(caminho):
    """Conecta direto num arquivo de trabalho por caminho (usado nas
    threads de fundo das telas de simulação/ajuste, que abrem sua própria
    conexão em vez de reusar `conectar()` pra não compartilhar objeto
    sqlite3 entre threads), já com os pragmas de performance aplicados."""
    conn = sqlite3.connect(caminho)
    conn.execute("PRAGMA foreign_keys = ON")
    _aplicar_pragmas_performance(conn)
    return conn


# Tentativas/espera de conectar_caminho_com_retry abaixo — mesmo padrão de
# app/screens/simulacao.py:_commitar_com_retry (6 tentativas, 5s→160s de
# espera exponencial), reaproveitado aqui pro caso descrito no docstring
# da função.
_TENTATIVAS_CONECTAR_COM_RETRY = 6
_ESPERA_INICIAL_CONECTAR_COM_RETRY = 5.0


def conectar_caminho_com_retry(
    caminho, tentativas=_TENTATIVAS_CONECTAR_COM_RETRY,
    espera_inicial=_ESPERA_INICIAL_CONECTAR_COM_RETRY,
):
    """Mesma conexão de `conectar_caminho`, mas tolerando "database is
    locked" na abertura com retry/espera exponencial, em vez de propagar
    na 1ª falha — usada só por _worker_inicializar_lote (app/core/
    simulacao.py): ali, uma falha na conexão é FATAL pro lote inteiro (o
    ProcessPoolExecutor derruba o pool inteiro se o `initializer` levanta
    uma exceção — "a child process terminated abruptly, the process pool
    is not usable anymore" — não é só aquele cenário que falha, como uma
    escrita comum do processo principal via _commitar_com_retry).

    A `PRAGMA busy_timeout = 30000` que `_aplicar_pragmas_performance` já
    aplica cobre uma espera de até 30s DENTRO de uma única tentativa de
    conexão — mas isso é só UMA tentativa. Num projeto grande, um worker
    pode tentar conectar bem no meio de uma regravação do .mogno em
    segundo plano (`projeto.sincronizar`/`_sincronizar_agora`, que usa
    `Connection.backup()` — pode legitimamente levar mais que 30s num
    arquivo de trabalho grande) — essa colisão pontual não deveria matar
    o lote inteiro, então tenta de novo, com o mesmo backoff que
    _commitar_com_retry já usa pra escritas."""
    espera = espera_inicial
    for tentativa in range(tentativas):
        try:
            return conectar_caminho(caminho)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or tentativa == tentativas - 1:
                raise
            time.sleep(espera)
            espera *= 2


def _formatar_tamanho_bytes(bytes_):
    tamanho = float(bytes_)
    for unidade in ("B", "KB", "MB", "GB"):
        if tamanho < 1024 or unidade == "GB":
            return f"{int(tamanho)} B" if unidade == "B" else f"{tamanho:.1f} {unidade}"
        tamanho /= 1024


def espaco_livre_suficiente_para_vacuum(caminho):
    """VACUUM copia o banco inteiro pra um arquivo temporário antes de
    substituir o original — a documentação do SQLite recomenda contar com
    até 2x o tamanho atual do arquivo em espaço livre no disco (não é só
    o tamanho final compactado: o pico de uso é original + cópia nova
    coexistindo). Checado ANTES de chamar VACUUM pra falhar rápido com uma
    mensagem legível, em vez de deixar o SQLite abortar no meio do
    caminho com "database or disk is full" sem dizer quanto falta.
    Retorna (suficiente: bool, necessario: int, livre: int) em bytes."""
    tamanho_atual = caminho.stat().st_size
    necessario = tamanho_atual * 2
    livre = shutil.disk_usage(caminho.parent).free
    return livre >= necessario, necessario, livre


def executar_vacuum(caminho):
    """Compacta o arquivo de trabalho (VACUUM): reescreve o banco inteiro
    sem as páginas livres deixadas por DROP/DELETE em massa (ex: cada
    "Gerar simulação"/"Rodar simulação de intensidades" faz DROP TABLE +
    CREATE TABLE do zero — o espaço da tabela antiga fica reservado no
    arquivo, mas livre, até algo compactar). Conexão própria, dedicada só
    a isso (chamado de uma thread de fundo, ver
    screens/configuracoes.py:_executar_compactacao_em_thread) —
    isolation_level=None (autocommit) porque VACUUM não pode rodar dentro
    de uma transação. Retorna (tamanho_antes, tamanho_depois) em bytes.
    Levanta ValueError (não OperationalError) se não houver espaço em
    disco suficiente — nem chega a tentar, ver
    espaco_livre_suficiente_para_vacuum."""
    tamanho_antes = caminho.stat().st_size

    suficiente, necessario, livre = espaco_livre_suficiente_para_vacuum(caminho)
    if not suficiente:
        raise ValueError(
            "Espaço em disco insuficiente para compactar: o SQLite precisa de até 2x o tamanho "
            f"atual do arquivo livre durante a operação (~{_formatar_tamanho_bytes(necessario)}), "
            f"mas só há {_formatar_tamanho_bytes(livre)} livres em "
            f"\"{caminho.drive or caminho.anchor}\". Libere espaço em disco e tente novamente."
        )

    conn = sqlite3.connect(caminho, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.execute("VACUUM")
        except sqlite3.OperationalError as e:
            if "disk" in str(e).lower() or "full" in str(e).lower():
                raise ValueError(
                    "Espaço em disco insuficiente para compactar — o SQLite abortou a operação a "
                    f"meio caminho por falta de espaço em \"{caminho.drive or caminho.anchor}\". "
                    "Libere espaço em disco e tente novamente."
                ) from e
            raise
    finally:
        conn.close()
    tamanho_depois = caminho.stat().st_size
    return tamanho_antes, tamanho_depois
