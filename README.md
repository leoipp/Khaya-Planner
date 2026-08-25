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
| Modelos | Cadastrar equações, coeficientes e variáveis de entrada. |
| Sortimentos | Definir faixas diamétricas, rendimentos e preços. |
| Custos | Cadastrar custos de formação e operações de colheita. |
| Weibull | Ajustar e avaliar distribuições diamétricas. |
| Simulação | Configurar manejos, gerar cenários, ranquear e visualizar resultados. |
| Construtor de Variáveis | Montar grafos de cálculo e criar colunas derivadas. |
| Ingressos e Curvas | Analisar ingressos e curvas de distribuição. |
| Resumos de Cenários | Agrupar, filtrar e exportar resultados consolidados. |
| Configurações | Definir classes, parâmetros financeiros, comportamento da simulação e preferências. |

## Projetos `.mogno`

Um projeto `.mogno` contém o banco de dados da aplicação em formato codificado. Durante o uso, o programa cria uma cópia SQLite decodificada em uma pasta temporária e sincroniza as alterações de volta ao arquivo original.

Recomendações:

- Não edite um arquivo `.mogno` diretamente com ferramentas SQLite.
- Não mova ou sobrescreva o projeto enquanto ele estiver aberto.
- Mantenha cópias de segurança dos projetos importantes.
- Aguarde o encerramento normal da aplicação para garantir a sincronização das últimas alterações.
- Projetos grandes podem exigir espaço temporário adicional equivalente ao tamanho do arquivo.

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

Atualmente o repositório não inclui uma suíte automatizada de testes. Recomenda-se adicionar testes para os fluxos críticos de simulação e persistência.

## Licença

Este projeto é distribuído sob a licença MIT. Consulte o arquivo [`LICENSE`](LICENSE) para conhecer os termos.
