# Khaya Planner

Aplicação desktop para modelagem, simulação e análise de cenários de manejo florestal. O Khaya Planner reúne cadastro de modelos, ajuste de distribuições Weibull, construção visual de variáveis, simulação de povoamentos, cálculo de receitas e custos e comparação de cenários.

A interface é desenvolvida em Python com PySide6. Os dados de cada trabalho ficam concentrados em um arquivo de projeto com extensão `.mogno`.

## Funcionalidades

- Cadastro e teste de modelos matemáticos florestais.
- Cadastro de sortimentos, rendimentos e preços de madeira serrada ou em pé.
- Cadastro de custos de formação e colheita.
- Ajuste da distribuição diamétrica Weibull.
- Importação e preparação de dados de inventário florestal.
- Construtor visual de variáveis com nós, conexões, cálculos e saídas personalizadas.
- Nós para volume, afilamento, receita, custos, VPL e VET.
- Dedução opcional de PIS, COFINS e FUNRURAL no nó Receita Total.
- Configuração de eventos de manejo: raleio, desbastes e corte raso.
- Geração individual ou em lote de cenários de simulação.
- Ranking de cenários por KPI e por talhão.
- Gráficos de resultados, ingressos e curvas de distribuição.
- Resumos configuráveis e exportação de resultados para Excel ou SQLite.
- Mapas com importação de shapefiles, join de resultados e cores por atributo.
- Otimização do planejamento florestal em duas etapas com CPLEX.
- Cálculos de saídas com base anterior ao manejo, mantendo a remoção da idade atual.
- Temas claro e escuro.

## Requisitos

- Python 3.10 ou mais recente.
- Windows, Linux ou macOS com suporte ao Qt 6.
- Memória e espaço em disco compatíveis com o tamanho dos projetos processados.

As principais bibliotecas utilizadas são PySide6, pandas, NumPy, SciPy, Matplotlib, PyArrow e OpenPyXL. As versões adotadas estão em `requirements.txt`.

## Instalação

Clone ou copie o projeto e, no diretório raiz, crie um ambiente virtual:

### Windows — PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Linux ou macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Execução

Com o ambiente virtual ativado, execute:

```bash
python main.py
```

Na primeira abertura, use a barra lateral para criar um projeto novo ou abrir um arquivo `.mogno` existente.

## Fluxo de uso sugerido

1. Crie ou abra um projeto.
2. Importe a base de dados e revise o mapeamento das colunas.
3. Cadastre e teste os modelos necessários.
4. Configure sortimentos, preços, custos, tributos e parâmetros gerais.
5. Ajuste a distribuição Weibull, quando aplicável.
6. Monte os cálculos no Construtor de Variáveis e defina quais saídas devem ser gravadas.
7. Configure os parâmetros e eventos de manejo na tela Simulação.
8. Gere uma simulação ou um conjunto de cenários.
9. Compare rankings, gráficos e resumos e exporte os resultados necessários.

## Telas principais

| Tela | Finalidade |
| --- | --- |
| Mapa | Importar shapefiles, unir atributos da simulação e colorir por valores únicos. |
| Modelos | Cadastrar equações, coeficientes e variáveis de entrada. |
| Sortimentos | Definir faixas diamétricas, rendimentos e preços. |
| Custos | Cadastrar custos de formação e operações de colheita. |
| Weibull | Ajustar e avaliar distribuições diamétricas. |
| Simulação | Configurar manejos, gerar cenários, ranquear e visualizar resultados. |
| Construtor de Variáveis | Montar grafos de cálculo e criar colunas derivadas. |
| Ingressos e Curvas | Analisar ingressos e curvas de distribuição. |
| Resumos de Cenários | Agrupar, filtrar e exportar resultados consolidados. |
| Otimização de Planejamento | Escolher cenários por talhão, regular produção e maximizar VPL com CPLEX. |
| Configurações | Definir classes, parâmetros financeiros, comportamento da simulação e preferências. |

## Organização da tela Simulação

A tela separa o trabalho em três abas:

- **Manejo e cenários**: escolha **Cenário único**, **Múltiplos cenários — manual** ou **Múltiplos cenários — grade automática**. O formulário mostra apenas os campos usados pelo modo escolhido. No modo manual, preencha o manejo, informe um nome e clique em **Adicionar cenário**; no automático, defina os intervalos de idades/intensidades e gere a grade.
- **Dados e parâmetros**: mapeie as colunas da base, configure distribuição/agregação e salve os parâmetros de manejo. Os cartões se ajustam à largura da janela.
- **Comparação**: selecione indicadores e a direção para ranquear cenários no conjunto ou por talhão.

Nos múltiplos cenários, cadastro, seleção, execução do lote e exportação ficam em grupos distintos. **Gerar pendentes** processa apenas o que falta; **Regerar todos** refaz o lote; **Parar** mantém a parada entre cenários. A tabela informa a situação de cada cenário, e o cabeçalho mostra quantos estão cadastrados e gerados. Os controles de resultados e o estado da simulação permanecem no rodapé ao trocar de aba.

```bash
python -m unittest discover -s tests -p "test_simulacao_ui.py" -v
```

### Cenários sem manejo

Na Simulação, cada intervenção possui uma caixa de seleção: Raleio, 1º Desbaste,
2º Desbaste e Corte Raso. Desmarque as intervenções que não serão realizadas,
no cenário único, no cadastro manual ou na grade automática. Desmarque todas
para criar um cenário sem manejo. Os campos desativados preservam seus valores.
A sequência de idades considera somente as intervenções selecionadas.
Sem corte raso, a simulação acompanha o horizonte definido pela idade máxima
nas Configurações. O indicador VET exige uma idade de corte raso e não se aplica
a esse cenário.

## Construtor: base anterior ao manejo

Clique com o botão direito em um nó **Saída** e marque **Usar base anterior ao manejo**.
Salve o construtor para manter a opção. O título do nó passa a indicar **base anterior**.

Nas linhas de intervenção válida, essa saída recalcula todos os nós que a alimentam
usando os campos `_atual` da idade anterior positiva do mesmo talhão e cenário.
A idade simulada, a remoção absoluta e as demais colunas permanecem na linha atual.
Linhas sem intervenção ou com intensidade zero mantêm a base atual. A opção exige
o mapeamento da coluna de talhão e as colunas `idade_simulada` e `evento_manejo`.
Sem uma linha anterior, os campos da base anterior ficam vazios; idades duplicadas
no mesmo talhão e cenário geram erro por ambiguidade.

Por exemplo, habilite a opção em **MDD DESBASTE CORRIGIDO** para projetar o cálculo
até a idade do desbaste usando a base anterior e a remoção registrada nessa idade.
Mantenha a opção desabilitada em **MDD REMANESCENTE CORRIGIDO**, que continua usando
a base atual, mesmo compartilhando nós com o desbaste. Basta habilitar a saída final:
o comportamento alcança todo o ramo e não altera os dados da tabela.

## Mapas

### Mapa e shapefiles

A tela inicial **Mapa** exibe a camada selecionada ao lado da navegação. Crie ou abra um projeto e clique em **Importar shapefile**. Mantenha os arquivos `.shp`, `.shx` e `.dbf` na mesma pasta; `.prj` e `.cpg` são opcionais. A leitura usa [PyShp](https://github.com/GeospatialPython/pyshp).

Arraste o canvas para mover o mapa, use a roda do mouse para zoom e **Enquadrar** para visualizar toda a camada. **Tabela de atributos** abre e fecha o painel inferior, cuja altura pode ser ajustada arrastando o separador. O seletor de camadas permite alternar entre os shapefiles importados.

As geometrias e os atributos são incorporados ao `.mogno`, sem depender dos arquivos originais após a importação. No **Construtor de Variáveis**, selecione a tabela `shape_<nome>_<identificador>` e use **Colunas disponíveis** para adicionar atributos ao grafo. As colunas calculadas são gravadas nessa tabela e aparecem no mapa ao retornar à tela.

O campo `id` é reservado à chave interna do motor. Se o shapefile tiver um campo `ID`, seu conteúdo é preservado em `atributo_id` (com sufixo adicional em caso de conflito). A camada é exibida nas coordenadas originais; esta versão não faz reprojeção nem inclui mapa de fundo.

### Join com a simulação e valores únicos

Para usar a mesma tabela de **Resumos de Cenários** no mapa, configure as fontes, filtros por chaves, agrupamentos, indicadores e pivô nessa tela e clique em **Disponibilizar tabela no mapa**. O resultado completo de todos os cenários gerados é salvo no projeto com o nome indicado em **Tabela no banco**. A amostra de 100 linhas e o limite de dois cenários da prévia não limitam essa tabela. O botão também atualiza a prévia com uma amostra da tabela disponibilizada.

Em **Mapa → Join com cenários...**, essa tabela aparece como origem, com os mesmos valores e colunas do resumo. **Tabela de cenários e filtros...** permite consultar esse resultado e adicionar filtros antes do join. Depois de alterar as definições do resumo, clique novamente em **Disponibilizar tabela no mapa** e reaplique o join.

Na tela **Simulação**, **Tabela e filtros...** abre uma consulta paginada dos resultados. Escolha a tabela e adicione condições por coluna, combinadas por **E** ou **OU**. Os operadores incluem comparação, texto, listas (separadas por `;`) e valores nulos. **Consultar** mostra o recorte; **Aplicar filtros para o join** salva as condições no projeto. A paginação de 200 linhas é apenas para visualização: o join consulta todas as linhas que atendem aos filtros.

No **Mapa**, clique em **Join com cenários...**, escolha **Tabela inteira** ou **Recorte filtrado da tabela**, selecione a coluna de ligação de cada tabela e marque os atributos a trazer. Os filtros também podem ser definidos dentro desse diálogo. No modo **Texto**, zeros à esquerda são preservados; no modo **Número**, `01`, `1` e `1,0` são equivalentes.

Se houver várias linhas por chave (por exemplo, várias idades ou cenários por talhão), filtre a consulta ou escolha **Primeira linha**, **Última linha**, **Soma**, **Média**, **Mínimo** ou **Máximo**. As quatro agregações exigem atributos numéricos. O padrão exige uma linha por chave para evitar uma escolha implícita.

O join mantém todas as feições e grava os atributos em colunas `sim_*`; feições sem correspondência recebem valores nulos. Colunas originais são preservadas, mesmo quando seus nomes coincidem com os atributos trazidos. O resultado é uma cópia dos dados no momento da aplicação: após gerar outra simulação ou mudar o recorte, aplique o join novamente. Colunas trazidas anteriormente e desmarcadas no novo join ficam vazias, preservando as referências dos construtores salvos.

Em **Valores únicos...**, escolha uma coluna original ou trazida pelo join. Ajuste as cores de cada valor, desmarque valores para usar a cor dos demais ou gere outra paleta. O mapa mostra a legenda e a quantidade de feições em cada categoria. Valores nulos aparecem como **Sem valor**. A coluna, as cores e os filtros ficam salvos dentro do `.mogno`. **Restaurar cor única** limpa a classificação da camada selecionada.

## Projetos `.mogno`

Um projeto `.mogno` contém o banco de dados da aplicação em formato codificado. Durante o uso, o programa cria uma cópia SQLite decodificada em uma pasta temporária e sincroniza as alterações de volta ao arquivo original.

Recomendações:

- Não edite um arquivo `.mogno` diretamente com ferramentas SQLite.
- Não mova ou sobrescreva o projeto enquanto ele estiver aberto.
- Mantenha cópias de segurança dos projetos importantes.
- Aguarde o encerramento normal da aplicação para garantir a sincronização das últimas alterações.
- O salvamento usa uma cópia consistente e um arquivo codificado temporário: reserve até duas vezes o tamanho do banco de trabalho em espaço livre adicional. O aplicativo verifica espaço antes de começar e preserva o original em caso de erro. Bancos de sessões interrompidas não são apagados automaticamente.

## Estrutura do código

```text
.
├── main.py                    # Inicialização da aplicação
├── requirements.txt          # Dependências Python
├── assets/                    # Ícones e imagens da interface
└── app/
    ├── window.py              # Janela principal e navegação
    ├── core/                  # Banco, modelos, simulação e regras de negócio
    ├── screens/               # Telas da aplicação
    ├── theme/                 # Temas, estilos e tokens visuais
    └── widgets/               # Componentes reutilizáveis e gráficos
```

Alguns módulos centrais:

- `app/core/db.py`: criação, migração e conexão com o banco de dados.
- `app/core/projeto.py`: abertura, cópia temporária e sincronização de projetos.
- `app/core/simulacao.py`: motor de simulação, cenários, rankings e resultados.
- `app/core/construtores.py`: avaliação dos grafos do Construtor de Variáveis.
- `app/core/motor_modelos.py`: compilação e avaliação das equações cadastradas.
- `app/core/importador.py`: leitura e importação de planilhas e arquivos CSV.

## Dados e desempenho

A aplicação processa tabelas com pandas e NumPy e utiliza SQLite para persistência. Resultados grandes de cenários podem ser intermediados por arquivos Parquet. A geração em lote usa processamento paralelo e pode consumir bastante CPU, memória e espaço em disco, dependendo da quantidade de talhões, idades, classes diamétricas e cenários.

Antes de uma execução extensa:

- valide um cenário individual;
- confira os modelos e mapeamentos de colunas;
- verifique o espaço livre em disco;
- mantenha uma cópia do projeto original.

## Desenvolvimento

O projeto separa interface e regras de negócio: componentes Qt ficam em `app/screens` e `app/widgets`, enquanto cálculos e persistência ficam em `app/core`. Para uma verificação rápida de sintaxe após alterações, execute:

```bash
python -m compileall main.py app
```

Para executar toda a suíte de testes:

```bash
python -m unittest discover -s tests -v
```

Os testes incluem simulação, manejos opcionais, saídas com base anterior,
resumos, mapas e planejamento. Testes que dependem do solver real são pulados
quando o executável CPLEX não está disponível no PATH.

Os testes de integração do mapa verificam importação, atributos no construtor, gravação de colunas calculadas, reabertura do projeto e troca de projeto:

```bash
python -m unittest discover -s tests -p "test_mapa*.py" -v
```

## Otimização de Planejamento (CPLEX)

O padrão agora é **Modelo linear / inteiro misto configurável**. Escolha o indicador e maximização/minimização, o domínio das variáveis (B/I/C, inclusive por coluna), os limites e a regra opcional de seleção por grupo. As restrições são cadastradas por coluna de coeficientes, relação e limite. A capacidade máxima da serraria é um teto anual opcional e não impõe demanda mínima. A aba Resultados mostra valores atingidos e folgas. Veja [o guia do modelo configurável](docs/otimizacao-configuravel.md).

### Regulação legada em duas etapas

As instruções abaixo se referem somente à formulação legada, preservada para abrir modelos anteriores.


Em **Configurações > Parâmetros > CPLEX**, selecione o `cplex.exe` da instalação IBM ILOG CPLEX (pasta `cplex/bin/<plataforma>`) e clique em **Salvar caminho do CPLEX**. Esse caminho é uma preferência do usuário/máquina e não é transportado com o projeto. A integração usa o executável local, sem exigir `docplex` ou o módulo Python `cplex` no ambiente do aplicativo.

Na tela **Otimização de Planejamento**:

1. Escolha o SQLite exportado pelos Resumos de Cenários ou use o projeto aberto, quando a tabela já estiver no projeto.
2. Selecione a tabela e mapeie talhão, cenário, área, idade e VPL. Os volumes anuais devem estar em colunas com o nome do ano, como `2026` e `2027`.
3. Defina horizonte, primeiro ano regulado, demanda, desvio máximo, folga, tolerância, gaps e tempos das duas etapas. Os campos de desvio/gap estão em **percentual**: o `ALPHA_MAX = 5.0` do script equivale a **500%**, e `TOL_ALPHA_NUMERICA = 1e-5` equivale a **0,001 ponto percentual**.
4. Marque a multiplicação pela área somente se os volumes/VPL estiverem informados por hectare.
5. Adicione limites `>=`, `<=` ou `=` para a soma de uma coluna nos cenários escolhidos. Para limitar um ano, escolha a coluna daquele ano; para limitar outro indicador, escolha sua coluna numérica. A opção por hectare multiplica esse indicador pela área. Essas restrições adicionais valem nas duas etapas; a faixa em torno da demanda vale apenas nos anos regulados.
6. Defina um sufixo livre e clique em **Otimizar em duas etapas**. Modelos podem ser salvos/carregados em JSON pelo formulário.

O modelo impõe um cenário por talhão, minimiza o maior desvio proporcional da demanda e depois maximiza o VPL na faixa encontrada, com a solução da etapa 1 como MIP start. Uma solução factível encontrada dentro do tempo limite pode ser usada mesmo sem prova de otimalidade; confira o status/gap da etapa 1 antes de interpretar o alpha como mínimo comprovado.

São geradas `resultado_otimizacao_<sufixo>`, `cenarios_escolhidos_<sufixo>`, `producao_anual_<sufixo>`, `resumo_otimizacao_<sufixo>`, `etapas_otimizacao_<sufixo>` e `diagnostico_volume_<sufixo>`. A tabela das etapas guarda status, objetivo, bound/gap quando disponíveis e o log completo do solver; o resumo guarda também a configuração utilizada. Informações não fornecidas pelo solver ficam nulas. O diagnóstico de volume usa as mesmas unidades do modelo, inclusive conversão por hectare.

Os resultados são publicados juntos em uma transação e não sobrescrevem tabelas existentes: use outro sufixo para repetir uma execução. A origem permanece intacta. Em caso de inviabilidade, o refino grava `conflitos_<sufixo>` com as restrições e o detalhamento do conflito, sem publicar uma solução. A execução ocorre em segundo plano; trocar de projeto e fechar o aplicativo ficam bloqueados até o término.

O intercâmbio de soluções usa o [formato SOL documentado pela IBM](https://www.ibm.com/docs/en/icos/22.1.2?topic=cplex-sol-file-format-solution-files).

```bash
python -m unittest discover -s tests -p "test_planejamento.py" -v
```

Os testes com solver real são executados quando `cplex` está no PATH; os demais validam dados, restrições, conversão de unidades e publicação atômica independentemente da instalação.

## Arquivos temporários

Os temporários de execução ficam em `temp/` dentro da pasta do simulador:
cópias de trabalho dos projetos, intercâmbio Parquet entre processos,
arquivos LP/SOL/MST e publicação dos resultados do CPLEX. A inicialização
também direciona `tempfile`, `TEMP`, `TMP`, `TMPDIR` e `SQLITE_TMPDIR`
para essa pasta, incluindo os processos filhos.

O caminho centralizado em `app/core/temporarios.py` usa a pasta do código
durante o desenvolvimento e `__compiled__.containing_dir` no Nuitka,
inclusive em onefile, mantendo os temporários da aplicação ao lado do executável.
A pasta precisa permitir escrita. Ao preparar o build onefile, configurar
também o diretório de extração do bootstrap do Nuitka: essa extração ocorre
antes da inicialização Python e não é controlada por este módulo.
Arquivos de substituição atômica (`.tmp`) permanecem junto ao arquivo
definitivo para garantir a substituição no mesmo volume.

## Licença

Este projeto é distribuído sob a GNU General Public License v3.0 (GPL-3.0). Consulte o arquivo [`LICENSE`](LICENSE) para conhecer os termos.


## Explorador de Dados

A tela **Explorador de Dados** permite consultar tabelas do projeto ou de outro SQLite, executar SELECT/CTE, navegar por todas as páginas, exportar a consulta inteira para Excel e gerar gráficos. A otimização tem um acesso direto às tabelas de resultado. Veja [o guia](docs/explorador-dados.md).
