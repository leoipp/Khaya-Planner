# Otimização configurável

A formulação padrão permite modelos lineares e inteiros mistos expressos por uma matriz de coeficientes. Cada linha da origem corresponde a uma variável de decisão. O objetivo é a soma do indicador de cada linha multiplicado pela decisão.

1. Escolha uma tabela SQLite do projeto ou de um arquivo externo.
2. Selecione maximização ou minimização e a coluna do objetivo. Informe a unidade; marque por hectare somente quando for necessária a conversão pela área.
3. Configure variáveis binárias, inteiras ou contínuas. Uma coluna opcional com B/I/C permite misturá-las no mesmo problema. Limites por coluna são opcionais; binárias ficam no intervalo [0,1], respeitando os limites específicos cadastrados.
4. Selecione uma alternativa por grupo, no máximo uma ou variáveis independentes sem agrupamento. A primeira opção representa o problema florestal de escolher um cenário por talhão. As demais permitem outras formulações.
5. Cadastre as restrições em **Restrições e solver**: coluna dos coeficientes, relação (`<=`, `>=` ou `=`), limite e conversão por hectare, quando pertinente. Não há demanda obrigatória nem regulação automática no modelo configurável.
6. Confira as decisões, objetivo, status, bound/gap e a tabela de restrições com limite, atingido e folga. Os resultados são publicados no banco sem substituir tabelas anteriores; escolha outro prefixo para uma nova execução.

## Capacidade máxima da serraria

Ative a opção somente se o problema tiver essa restrição. O teto usa as colunas nomeadas por ano, do início ao fim do horizonte, como coeficientes do volume que entra no processamento. A relação é **volume anual <= capacidade anual**. Produzir abaixo desse teto é permitido: ele não se transforma em demanda mínima ou em quantidade exata.

Para capacidades diferentes por ano ou produto, deixe essa opção desmarcada e cadastre uma linha de restrição para cada coluna correspondente. O mesmo cadastro serve para demanda mínima (`>=`), atendimento exato (`=`), orçamento e outros limites.

## Outros problemas

Produção, mistura, transporte e seleção de projetos podem ser representados quando a tabela contém as variáveis em linhas e os coeficientes das restrições em colunas. Por exemplo, transporte usa uma variável por ligação origem–destino, custo como objetivo de minimização e colunas de coeficientes para cada origem e destino. Não há geração automática dessas matrizes: a tabela deve conter os coeficientes corretos.

O modelo legado de minimizar alfa e depois maximizar VPL continua disponível em uma opção separada. Modelos JSON antigos são carregados nessa formulação. O modelo configurável não executa a etapa de alfa.

## Correções de persistência e controles

O Resumo de cenários lembra a configuração escolhida, os filtros, grupos, métricas, pivô e nome da tabela. Referências temporariamente indisponíveis são mantidas para revisão em vez de substituídas pela primeira coluna.

Comboboxes recebem altura mínima calculada pela fonte, opções mais altas e ajuste das linhas de tabela que os contêm, em todas as telas e diálogos.

O salvamento verifica espaço para backup e codificação, mantém o original até a substituição atômica e informa falhas na interface. O banco de trabalho fica preservado quando o salvamento não termina; o fechamento é bloqueado enquanto a tentativa de salvar continuar falhando. Um marcador `.recovery.json` registra o caminho do projeto e do banco quando a falha ocorre em segundo plano. Arquivos de sessões interrompidas não são apagados ao iniciar.
