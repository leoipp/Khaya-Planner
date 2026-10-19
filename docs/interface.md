# Padrão visual do Khaya Planner

As telas compartilham os componentes Qt existentes; nenhuma regra de cálculo
depende de estilos ou da posição dos controles.

## Estrutura das telas

- Margens externas de 20 px; distância de 12–16 px entre blocos.
- `CabecalhoTela` contém título, descrição curta e ações contextuais. As ações
  passam para uma segunda linha quando falta largura.
- `Cartao` contém título e corpo, com descrição opcional. A superfície acompanha
  o tema e dispensa sombra. `PainelOperacao` usa esse mesmo componente.
- Abas separam tarefas relacionadas. Salvamento e execução de operações longas
  devem permanecer acessíveis fora da área rolável.
- Use `LayoutFluxo` para barras de ferramentas e `GradeCampos` para campos com
  rótulos acima. Evite uma linha fixa com muitos campos ou botões.
- Tabelas mantêm cabeçalhos, seleção e ordenação; tabelas largas podem rolar
  horizontalmente em vez de comprimir suas colunas até ficarem ilegíveis.

## Navegação

A barra lateral agrupa telas em Dados e cadastros, Modelagem, Simulação e
desdobro e Planejamento. Configurações permanece fixa na parte inferior.
O conteúdo da navegação rola em janelas baixas; o botão de recolher permanece
fora da barra. As chaves de navegação e o carregamento sob demanda não mudam.

A abertura do aplicativo mostra a Visão geral. Com projeto aberto, ela consulta
cadastros e presença de resultados sem carregar inventários ou resultados
detalhados. Os indicadores não certificam a validade dos dados. A orientação
de inventário abre Configurações diretamente na aba Dados e manutenção.

Ctrl+K abre a busca por telas e assuntos, com ou sem acentos. As teclas de seta
escolhem o destino, Enter abre a tela e Esc fecha a busca. A busca, o menu e os
atalhos da Visão geral respeitam a trava de navegação durante operações.

## Aparência e estados

As cores e a tipografia estão em `app/theme/tokens.py`, e os estilos em
`app/theme/qss.py`. Os temas claro e escuro devem usar as mesmas proporções.
Seleções e foco usam o verde da marca; ações destrutivas usam a cor de perigo.
Use a variante `salvar` ou `accent` para a ação principal e botões neutros para
ações secundárias. Nunca dependa apenas de um ícone para explicar uma ação
central do fluxo.

Tabelas e áreas de desenho vazias explicam como começar. Informações técnicas
extensas ficam em dicas ou detalhes recolhíveis, sem retirar os parâmetros
necessários para tomar decisões.

## Verificação

Verifique as telas em ambos os temas, com área de conteúdo de 760 × 680 px e
em uma janela maior. Observe textos longos, navegação com rolagem, controles
habilitados/desabilitados, tabelas vazias e o acesso aos botões principais.
Os testes de interface cobrem a abertura das telas, o tamanho mínimo, a
navegação e a preservação dos parâmetros de simulação e planejamento.
