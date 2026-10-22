# Taper, toragem e classes de madeira

Na tela **Modelos**, o botão **Exemplo Garay CC / SC / cerne** preenche um novo formulário com a equação e os coeficientes fornecidos pelo usuário. Salve o cadastro para disponibilizá-lo na toragem. O exemplo não é cadastrado automaticamente no projeto aberto.

A equação deve retornar **diâmetro em centímetros**, não a razão d/DAP. Na tela **Desdobro**, selecione o modelo e abra **Variáveis dummy e produtos…**.

## Variáveis e combinações

Na configuração do modelo selecionado:

1. Indique exatamente uma variável para DAP, uma para altura da seção e uma para altura total. Os nomes e a posição dessas variáveis são livres.
2. Marque as demais variáveis como **Dummy do produto**. O modelo pode ter nenhuma, uma, duas, três ou mais dummies.
3. Cadastre uma linha por produto, com nome, papel no perfil e um valor numérico para cada dummy. Os valores não precisam ser binários.
4. Os papéis **Com casca**, **Sem casca** e **Cerne** identificam a geometria usada no perfil/disco e no desdobro. Use **Outro produto** para perfis adicionais. Não é obrigatório cadastrar os três papéis.

Exemplo do Garay fornecido, depois de indicar x1=DAP, x2=h, x3=H e x4/x5 como dummies:

| Produto | Papel | x4 | x5 |
|---|---|---:|---:|
| Com casca | Com casca | 0 | 0 |
| Sem casca | Sem casca | 1 | 0 |
| Cerne | Cerne | 1 | 1 |

Esses valores são apenas um exemplo de codificação: cada equação deve receber as combinações previstas em seu ajuste. O diálogo sugere linhas iniciais editáveis. Combinações duplicadas, valores ausentes e papéis geométricos repetidos são recusados.

Cada modelo mantém sua própria configuração, salva no projeto com **Salvar estudo**. Trocar o modelo recupera seu mapeamento. Se suas variáveis cadastradas forem alteradas, revise o mapeamento.

Estudos anteriores que usam x4/x5 ou Tx1/Tx2 preservam a convenção antiga até o usuário definir uma configuração explícita. A partir da configuração explícita, as funções são determinadas pelo mapeamento, não pelo nome da variável. Outros modelos com entradas adicionais exigem configuração antes de calcular. Os demais usos do modelo no Construtor de Variáveis continuam exigindo o mapeamento de suas entradas.

## Volumes e desenhos

Os volumes CC, SC e cerne são calculados por Smalian nos mesmos limites de cada torete. O resumo soma os toretes comerciais; a tabela também mostra os não comerciais. O diâmetro mínimo de sortimento é aplicado ao perfil CC, quando cadastrado; caso contrário, ao SC ou ao primeiro produto. O encaixe das tábuas usa o menor diâmetro SC amostrado no torete, e o rendimento referência é aplicado ao volume SC. Sem SC, usa-se o perfil base disponível. O rendimento global mostrado no resumo tem como denominador o volume do perfil base, identificado no texto.

O perfil mostra as camadas cadastradas; produtos adicionais aparecem como contornos tracejados, também disponíveis no disco e em colunas de volume na tabela. O disco mostra os produtos na mesma altura, escolhida pelo menor diâmetro do perfil de corte (80 intervalos por torete). A casca é o anel entre CC e SC; o alburno está entre SC e cerne. Modelos que produzam cerne maior que SC ou SC maior que CC são rejeitados.

Modelos simples continuam disponíveis com um único perfil, e o cerne pode ser informado manualmente em percentual da área. Os volumes dos produtos não cadastrados ficam indisponíveis; não são inferidos do percentual manual.

## Classes

Em **Sortimentos → Classes de tábua → Classificação por cerne**, configure nome, mínimo, máximo, inclusão de cada limite e cor (duplo clique na célula de cor). É possível adicionar ou remover classes. As faixas devem cobrir 0–100% sem sobreposição ou lacunas, inclusive nos limites.

Padrão: C1 >50%; C2 de 20% a 50%, inclusive; C3 <20%. O percentual é a interseção da área da tábua com o círculo do cerne, dividida pela área da tábua, na seção de corte exibida. As tábuas recebem a cor da classe. A classificação não integra cerne ao longo do comprimento.

A quantidade teórica por classe vem do encaixe geométrico. A quantidade prevista e seu volume são distribuídos proporcionalmente entre as classes, respeitando o total limitado pelo rendimento. Por isso, as quantidades previstas por classe são **tábuas equivalentes**, podendo ter casas decimais, e não uma seleção individual das tábuas que serão produzidas.

As faixas e cores ficam salvas no projeto com **Salvar classificação** e são usadas pelo Desdobro para todas as dimensões de tábua. Configurações anteriores salvas no estudo são reaproveitadas até existir um cadastro nessa aba.


## Classes de tábua em Sortimentos

Em **Sortimentos → Classes de tábua**, cadastre classe, comprimento do torete (m), espessura (cm), largura (cm), preço (R$/m³) e rendimento (%). Os botões Adicionar, Salvar e Excluir persistem o cadastro no projeto. O comprimento representa tanto a tábua quanto o torete.

Na tela **Desdobro**, escolha uma classe para preencher as dimensões e usar seu comprimento no traçamento dos toretes. A seleção da classe fica salva com o estudo. O rendimento da classe é aplicado automaticamente à previsão; para usar um rendimento manual, selecione Dimensões manuais. Classes anteriores a esse campo recebem inicialmente 35%, que pode ser editado no cadastro. O preço fica disponível no cadastro; esta mudança não altera os cálculos financeiros dos cenários existentes.

A tabela de classes comerciais de toras continua com preço de madeira em pé. As colunas e os campos de preço de madeira serrada e rendimento foram retirados das classes comerciais de toras; valores históricos são preservados no banco para manter estudos existentes.
