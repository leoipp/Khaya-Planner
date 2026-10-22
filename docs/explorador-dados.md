# Explorador de Dados

Acesse **Explorador de Dados** na barra lateral, ou **Ver tabela, Excel e gráficos** na aba Resultados da otimização.

## Resultados da otimização

Selecione o banco do projeto aberto ou um arquivo SQLite externo. As tabelas são identificadas pelo sufixo escolhido na execução:

- `cenarios_escolhidos_<sufixo>`: cenários/variáveis com decisão diferente de zero.
- `resultado_otimizacao_<sufixo>`: todas as alternativas e o valor da decisão.
- `resumo_otimizacao_<sufixo>`: objetivo e resumo da execução.
- `restricoes_otimizacao_<sufixo>`: limites, valores atingidos e folgas do modelo configurável.
- `producao_anual_<sufixo>`: produção e limites anuais, quando aplicáveis.
- `etapas_otimizacao_<sufixo>`: status, objetivo, bound e gap.

Escolher uma tabela e clicar em **Ver tabela** preenche um SELECT. A navegação por páginas permite acessar todas as linhas sem carregar o banco inteiro na memória. Use Anterior, Próxima ou o número da página. Clicar no cabeçalho ordena a consulta inteira, não apenas a página visível.

## SQL

O editor aceita consultas SELECT e CTEs WITH, incluindo filtros, junções, agrupamento e ordenação. Ctrl+Enter executa. O explorador abre o banco somente para leitura e não executa comandos de alteração.

Exemplo:

```sql
SELECT ANO, VOLUME_TAPER, LIMITE_MAX
FROM "producao_anual_otimizacao"
ORDER BY ANO
```

Alterar o texto não muda o resultado até executar a consulta. A exportação e o gráfico usam a consulta que produziu o resultado exibido.

## Excel

**Exportar consulta inteira para Excel** exporta todas as linhas do resultado, mesmo que somente uma página esteja visível. O arquivo é escrito em fluxo e dividido em abas quando ultrapassa 1.048.575 linhas de dados por aba, além do cabeçalho. Textos são gravados literalmente, inclusive os que começam com `=`.

Campos BLOB aparecem como indicação de tamanho; seu conteúdo binário não é exportado. Uma célula de texto maior que o limite de 32.767 caracteres do Excel exige ajustar a consulta. Em caso de erro, um arquivo anterior no destino é preservado.

## Gráficos

Na aba **Gráficos**, escolha X, Y, tipo e agregação: soma, média, contagem por X ou sem agregação. O gráfico consulta o resultado completo, não só a página da tabela. Y deve ser numérico, exceto na contagem. Há barras, linhas e dispersão, com exportação em PNG.

Para produção anual, use X = ANO e Y = VOLUME_TAPER. Para folgas, abra a tabela de restrições e use X = RESTRICAO e Y = FOLGA. Resultados acima de 10.000 pontos precisam ser filtrados ou agrupados na consulta SQL antes de desenhar.
