# Direção de layout — Khaya Planner

Pesquisa de referências: 17 de outubro de 2026.

## Diagnóstico do layout atual

A padronização anterior resolveu diferenças de cores e componentes, mas repetiu
a mesma composição em tarefas diferentes. O excesso de cartões, descrições e
formulários largos deixa a interface genérica. Dados importantes disputam
espaço com elementos que apenas explicam a própria tela.

A próxima revisão precisa partir da tarefa: comparar cenários, editar um
modelo, configurar uma execução ou inspecionar um corte têm composições distintas.

## Referências selecionadas

### AlignUI: referência principal de acabamento e estrutura

- [Apresentação e previews do autor](https://figma.alignui.com/)
- [Tabelas](https://figma.alignui.com/components/table)
- [Prévia pública no Figma](https://www.figma.com/design/G2Jsv2KKziOWB9yjFk00pR/)

Aplicação proposta: cabeçalho compacto, ações contextuais, filtros e navegação.
Usar a hierarquia como referência; o conteúdo e a composição devem ser do Khaya.

### Carbon: referência para interfaces com muitos dados

- [Recursos de Figma do autor](https://www.carbondesignsystem.com/getting-started/designing/design-resources)
- [Biblioteca no Figma Community](https://www.figma.com/community/file/1157761560874207208)
- [Diretrizes de tabelas](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)

Aplicação proposta: tabelas como área principal de Simulação, Resumos e
Planejamento; filtros associados à tabela; detalhes sob demanda.

### Calcite / Esri: referência para a área de trabalho técnica

- [UI Kit e exemplos do autor](https://developers.arcgis.com/calcite-design-system/resources/ui-kit/)
- [Kit no Figma Community](https://www.figma.com/community/file/1360396942288069891)

Aplicação proposta: mapa e desenhos centrais, ferramentas próximas da área
visual e painel de propriedades recolhível para Mapa, Toragem e Construtor.

### Untitled UI: referência complementar de tabelas e filtros

- [Guia e kit de tabelas](https://www.untitledui.com/blog/create-tables-in-figma)
- [Kit de tabelas no Figma Community](https://www.figma.com/community/file/1332203037536263387)

Aplicação proposta: revisão de cabeçalhos, estados e organização dos filtros.
Não adotar sua estrutura de dashboard como padrão para todas as telas.

Os links de Figma foram identificados nas páginas dos autores. Os arquivos
bloquearam leitura automatizada; esta pesquisa usa a documentação pública,
sem afirmar que os frames internos foram inspecionados. Não é necessário
comprar bibliotecas para avaliar esta direção.

## Proposta de composição por tarefa

| Tela | Composição proposta |
| --- | --- |
| Simulação | Lista de cenários central; edição do manejo em painel lateral; execução e seleção em barra contextual. |
| Otimização | Metas e restrições à esquerda; produção anual e resultados à direita; opções do solver recolhidas. |
| Mapa | Mapa predominante; camadas à esquerda; atributos em painel inferior sob demanda. |
| Modelos | Lista com busca; edição do modelo selecionado ao lado; equação com largura suficiente. |
| Toragem e Desdobro | Desenhos com escala e cotas; parâmetros em inspetor lateral; tabela associada à seleção. |
| Visão geral | Resumo curto e continuidade do trabalho; reduzir a repetição de cartões de orientação. |
| Configurações | Navegação por categoria; formulários com largura de leitura controlada; ações de salvar previsíveis. |

## Regras propostas

- Escolher uma referência principal por tipo de tela e manter a mesma linguagem de componentes.
- Trocar cartões aninhados por seções, alinhamento e separadores discretos.
- Reservar o verde para ações, seleção e estados relevantes.
- Usar rótulos curtos; instruções extensas ficam em ajuda contextual.
- Evitar campos numéricos estendidos por toda a largura disponível.
- Abrir espaço real para tabelas e gráficos; dimensionar desenhos técnicos pelo conteúdo.
- Testar a composição com dados e nomes longos, incluindo seleção, execução e erro.

## Ordem de execução

1. Desenhar Simulação como tela piloto com cenários e edição do manejo.
2. Conferir o desenho em estado vazio, com dados e durante uma execução.
3. Implementar o piloto em Qt e comparar visualmente com a referência escolhida.
4. Aplicar os padrões aprovados às outras famílias de tela.

Esta direção substitui ajustes sucessivos de cores por uma revisão da composição.
Não altera regras de cálculo nem autoriza substituir o aplicativo desktop por web.


## Piloto implementado: Simulação

A lista de cenários ocupa a área principal e recebe busca por nome e filtro de
situação. A partir de 1120 px, o formulário manual fica na lateral, com 350 px;
em janelas menores ele volta para cima da lista. A grade automática utiliza a
largura disponível para preservar os campos de intervalos.

Exportações e regeração ficam em “Mais ações”. A seleção habilita os comandos
para usar e excluir cenários. O rodapé resume a preparação e abre as pendências
completas em uma janela. Os modos e cálculos existentes são preservados.

Verificação: testes dos modos, cadastro, filtros, seleção, temas e larguras de
760, 980, 1300 e 1440 px; prévias com dados de demonstração em ambos os temas.
