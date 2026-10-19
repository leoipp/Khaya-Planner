# Desempenho da navegação

Medição em 17/10/2026, Windows, Python 3.10 e Qt offscreen, sem projeto
carregado. Cronômetro `time.perf_counter()` envolvendo cada chamada e um
`QApplication.processEvents()` após mostrar a janela ou trocar a tela.
Ordem: janela inicial, Configurações, Simulação, Planejamento, Simulação.
Tempos de uma execução antes e outra depois; variam com cache de disco,
antivírus e carga da máquina. Não representam abertura de projetos grandes.

| Operação | Antes | Depois |
| --- | ---: | ---: |
| Importação inicial de Qt e janela | 0,755 s | 0,565 s |
| Construção e exibição da janela | 0,306 s | 0,162 s |
| Primeiro acesso a Configurações | 2,434 s | 0,862 s |
| Primeiro acesso a Simulação | 2,406 s | 0,065 s |
| Primeiro acesso a Planejamento | 0,034 s | 0,027 s |
| Retorno a Simulação | 0,014 s | 0,009 s |

As alterações miram os primeiros acessos a Configurações e Simulação;
diferenças nas outras linhas podem decorrer da variação entre execuções.

## Alterações

- SciPy Optimize e Gamma são importados dentro dos cálculos que os usam.
  Abrir os formulários não inicializa mais essas bibliotecas.
- Os dois gráficos da Simulação usam um contêiner leve. Matplotlib e seus
  canvases só são criados quando o gráfico fica visível. Enquanto oculto,
  o contêiner guarda apenas a última atualização solicitada.
- As consultas para gráficos e tabela dinâmica dos resultados só executam
  com a janela de resultados aberta. Ao abrir, são atualizados com o projeto
  e a seleção atuais. Não há cache de resultados do banco.
- A construção sob demanda das telas e o controle de revisão existentes
  continuam evitando reconstruções e recargas ao voltar a telas atuais.

A primeira abertura de um gráfico ainda paga o custo de importar Matplotlib;
o primeiro cálculo que usa SciPy paga o custo correspondente. O trabalho foi
adiado até seu uso, sem mudar fórmulas ou resultados numéricos.

## Verificação

`python -m unittest discover -s tests -q`

Há testes para criação única do gráfico, atualização enquanto oculto,
reabertura com o último estado e ausência de consultas de gráficos ocultos.
Para novas medições, usar um processo Python novo por execução; importar os
módulos de teste antes do cronômetro aquece as bibliotecas e invalida a
comparação dos primeiros acessos. Em testes com a janela principal, substituir
`limpar_arquivos_orfaos` por um mock para preservar sessões reais em execução.
