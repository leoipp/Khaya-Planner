# Construtor Tábuas V4

O novo nó Tábuas recebe três entradas fixas:

1. **Perfil taper**: saída Modelo de um Taper com produtos Com casca, Sem casca e Cerne configurados pelo botão direito.
2. **Vol. disponível**: volume aproveitável corrigido do MDD, por diâmetro.
3. **Árvores**: quantidade de árvores da mesma intervenção, por diâmetro e na mesma base de área do volume.

As saídas de cada classe são **volume (m³), receita bruta (R$) e quantidade estimada (unidades)** por diâmetro. O volume de cada classe equivale à quantidade multiplicada pelo volume geométrico da tábua; a receita equivale ao volume multiplicado pelo preço cadastrado. As peças são inteiras por árvore representativa. O total estimado pode ser fracionário porque o número de árvores vem da distribuição diamétrica.

O volume disponível é dividido pelo número de árvores para limitar o consumo de madeira por árvore. O consumo de cada peça é seu volume dividido pelo rendimento da classe. Todas as classes disputam o mesmo orçamento. Os cortes ficam dentro dos trechos comerciais do Taper, definidos pelo comprimento global e diâmetro mínimo; a sobra superior não produz tábuas. Os comprimentos das tábuas competem dentro dessa região disponível. O toco do nó também é respeitado.

Quantidade zero com volume positivo é uma inconsistência e gera erro. Dados ausentes permanecem ausentes. Volume e árvores precisam conter os diâmetros presentes no perfil.

As famílias de colunas por diâmetro podem ser selecionadas na distribuição por sortimento existente. O nó também pode alimentar somas de todas as classes diamétricas. A receita bruta não desconta custos, tributos ou juros.

## Projeto entregue

`Teste_v04.mogno` contém **Weibull Otimizado V4** ativo, derivado do V3 (que tinha o mesmo grafo do V2). V2 e V3 estão preservados e inativos nessa cópia. O arquivo original não foi alterado.

O Taper do V4 usa o modelo cadastrado **Taper Multiproduto** (Garay Y), inclusive no cálculo da relação de aproveitamento sobre volume total. As dummies têm combinações 0/0 para com casca, 1/0 para sem casca e 1/1 para cerne. O nó Tábuas recebe Volume Final Taper e a contagem por diâmetro para corte raso ou desbaste, multiplicada pela área. São usadas as três classes, preços, rendimentos e faixas de cerne existentes no projeto.

O grafo possui saídas por diâmetro e nove totalizadores: quantidade, volume e receita para cada classe de tábua. O layout foi organizado em camadas com ligações curvas. `Weibull Otimizado V4.json` é uma cópia legível do grafo.

Os resultados históricos permanecem os da execução anterior. **Execute novamente a simulação para produzir as saídas V4.** O editor atualizado aparecerá na próxima abertura do aplicativo.

## Compatibilidade com nós anteriores

Nós antigos sem a propriedade `versao_producao: 4` mantêm duas saídas por classe e o cálculo por árvore descrito abaixo. Novos nós têm três entradas e três saídas por classe. A configuração de classes continua no botão direito.

# Taper parametrizado e nó Tábuas

## Ligações

1. Ligue **Classe Diamétrica** à entrada DAP do modelo hipsométrico e forneça as demais entradas desse modelo.
2. Ligue a altura resultante à entrada **Ht (m)** do **Taper**.
3. No Taper, botão direito → **Configurar dummies e produtos…**. Mapeie DAP, h, H e as variáveis dummy; cadastre as combinações dos produtos. A quantidade e os nomes das dummies são livres.
4. Em **Nós Especiais → Tábuas**, crie o nó. Ligue a saída **Modelo** do Taper à entrada **Modelo** do nó Tábuas. Essa saída transmite o modelo, não um volume.
5. No nó Tábuas, botão direito → **Configurar classes de tábua e saídas…**. Escolha os cadastros, vínculo de cerne, prefixo das colunas, espessura da serra e altura do toco.

O nó Taper continua com as saídas numéricas **Aproveitável** e **Biomassa**, nas mesmas posições dos grafos antigos. Elas usam o produto com casca cadastrado; se não houver, o sem casca ou o primeiro produto. A saída adicional Modelo permite ao nó Tábuas calcular os demais perfis sem transformar o taper em uma equação genérica com cinco fios.

## Classes, unidades e preços

As dimensões, rendimento e preço são lidos do cadastro atual em **Sortimentos → Classes de tábua**, inclusive ao executar construtores salvos na simulação e em seus processos de cálculo. Cada cadastro selecionado ganha duas saídas, ambas por classe diamétrica:

- Volume previsto de tábuas: **m³ por árvore**.
- Receita bruta: volume previsto × preço cadastrado, em **R$ por árvore**.

Para totais do povoamento ou de uma intervenção, multiplique pela quantidade de árvores de cada classe no próprio grafo. Não há multiplicador de população implícito. A receita não deduz tributos, custos ou desconto financeiro.

O comprimento de cada cadastro define uma opção de comprimento do torete e da tábua no plano conjunto. A tora global não define esse comprimento; o diâmetro mínimo de Sortimentos continua sendo usado no traçamento. As tábuas são encaixadas no perfil sem casca quando disponível, com a espessura de serra configurada. O rendimento limita a quantidade prevista de acordo com o volume disponível.

## Produção conjunta e cerne obrigatório

Cada tora é cortada em um plano conjunto. A mesma peça física pertence a uma única classe; os volumes das classes podem ser somados porque não há sobreposição ou repetição de peças.

Uma classe só é válida quando a tábua atende às **dimensões e ao cerne**. C1 exige cerne C1, C2 exige cerne C2 etc., quando os nomes correspondem às faixas cadastradas. Esse vínculo fica fixo no diálogo. Cadastros com outros nomes precisam de uma faixa de cerne explícita. Não há opção de ignorar o cerne. O Taper deve fornecer os perfis **Sem casca** e **Cerne**.

O percentual continua sendo a interseção da área da tábua com o círculo do cerne na seção de corte; não é uma integração ao longo de todo seu comprimento. As quantidades são peças inteiras, não frações proporcionais de um resultado teórico. Uma tora com quatro peças pode resultar em quatro C1, três C1 e uma C2, ou outras combinações conforme as áreas de cerne reais.

O planejador compara grades candidatas e preenche sobras com peças de outras dimensões, priorizando a receita dos planos válidos. As peças respeitam o disco de madeira e a folga da serra. É uma **heurística determinística**, sem garantia de ótimo global.

Comprimentos diferentes competem pelos mesmos trechos do fuste: em cada início de tora, compara-se a receita por metro dos planos viáveis e avança-se apenas pelo comprimento escolhido. Não são calculadas árvores independentes para cada comprimento. Dentro de uma tora, as tábuas têm o comprimento desse torete.

O rendimento é um limite **compartilhado** da tora. Para uma peça de volume V e rendimento r%, seu consumo de madeira é V/(r/100); a soma desses consumos não pode superar o volume sem casca da tora. Com todas as classes a 35%, o volume total de tábuas não ultrapassa 35% da tora. Classes com rendimento zero não produzem peças.

A receita usa somente o volume das peças efetivamente atribuídas a cada classe, multiplicado pelo respectivo preço. Não se atribui preço alto a uma tábua que falhou na regra de cerne correspondente.

## Saídas e persistência

Os pinos de saída seguem a ordem volume/receita de cada cadastro. É possível ligá-los a cálculos, saídas e nós financeiros numéricos. Ao salvar o construtor, suas colunas são gravadas quando **Gravar na tabela** estiver marcado.

Exemplo de coluna: `tabuas_4_volume_C1_1_40` identifica prefixo `tabuas_4`, volume, classe C1 de id 1 e diâmetro 40 cm. O id evita colisões entre cadastros com o mesmo nome e dimensões diferentes. Atualizar as classes do nó preserva os fios por id e tipo de saída; saídas removidas deixam de ter fios.

O mapeamento das dummies e a escolha de classes/filtros ficam no grafo salvo. Excluir um cadastro ou uma faixa de cerne usados pelo nó exige revisar essa configuração. Altura ausente continua ausente nas saídas; árvore válida sem tábuas aproveitáveis produz zero.


## Execução em lote e desempenho

O caminho V4 com toco zero e todas as classes no comprimento global da tora usa `tabuas_lote.py`: perfis vetorizados e encaixe compilado pelo Numba. O cálculo mantém float64, as 32 grades candidatas, as 80 faixas de integração do cerne, as regras de classe e os orçamentos de madeira. Classes com comprimentos diferentes ou toco diferente de zero continuam no planejador detalhado.

O cache de produção guarda intervalos de orçamento nos quais todas as decisões de corte são iguais. A chave inclui equação, coeficientes, configuração de produtos, altura real, DAP, dimensões, preços, rendimentos e regras. Não arredonda alturas nem diâmetros. O cache é limitado a 65.536 geometrias e oito intervalos por geometria, por processo. O taper numérico também reutiliza resultados em alturas reais, com limites por curva e por número de curvas.

Numba está declarado em `requirements.txt`; a primeira compilação pode demorar mais que as chamadas seguintes. Workers novos carregam o código compilado do cache em disco e constroem seus caches de resultados conforme processam o lote.

Verificação local de 20/10/2026: três cenários reais, calculados consecutivamente em uma conexão isolada em memória, leram os dados do projeto em modo somente leitura. Os tempos das etapas de cálculo foram **15,576 s, 6,243 s e 6,282 s**, sem falhas nos construtores. Essa medição inclui população, construtores e distribuição, mas não inclui persistência, sincronização do `.mogno` nem MIP. Não é uma previsão de duração para toda a grade.

A equivalência foi verificada contra o planejador detalhado em casos sintéticos, limites de orçamento, misturas de classes e 100 árvores reais com as mesmas entradas. A suíte completa passou com 138 testes.
