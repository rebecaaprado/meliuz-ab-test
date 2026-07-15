# CLAUDE.md — Análise de Testes A/B de Cashback (Méliuz)

Este arquivo orienta uma ferramenta de IA (Claude Code, Cursor, GPT custom, etc.) sobre como analisar um teste A/B de cashback neste projeto, a partir de um pedido em linguagem natural do usuário — sem que a pessoa precise saber Python ou conhecer os scripts internos.

## Cenário de uso

Uma pessoa do time de Growth (sem background técnico) abre esta pasta numa ferramenta de IA e escreve algo como:

> "Analisa esse teste novo aqui: dados/dataset_04_parceiroD.csv"

> "Roda a análise do parceiro X e me diz se devo escalar alguma variante"

> "Registra esse resultado na planilha de acompanhamento"

A IA deve entender o pedido, rodar os scripts certos na ordem certa, e devolver a resposta em linguagem natural — não o output cru do terminal.

## O que fazer quando alguém pede para analisar um teste novo

1. **Identifique o caminho do dataset** mencionado pelo usuário (ex: `dados/dataset_04_parceiroD.csv`). Se a pessoa só mencionar o nome do parceiro ou anexar o arquivo, localize o CSV correspondente em `dados/`.

2. **Rode o pipeline completo com `registro.py`**, que já encadeia limpeza → métricas → análise → decisão → registro na planilha (CSV local sempre, e também no Google Sheets se um `--sheet-id` estiver configurado):

   ```bash
   python src/registro.py <caminho_do_dataset> [custo_troca] [--sheet-id SEU_SHEET_ID]
   ```

   - O CSV local (`planilha_acompanhamento.csv`) é sempre gravado, mesmo sem `--sheet-id` ou sem `service_account.json` configurado.
   - Se `--sheet-id` for informado mas a credencial (`service_account.json`, na raiz do projeto) não estiver configurada ou a planilha não estiver compartilhada com a service account, o script apenas avisa — não quebra a execução.

   - Se o usuário não mencionar um `custo_troca`, rode sem esse argumento (o script avalia só o filtro estatístico e sinaliza que a decisão de negócio depende desse parâmetro).
   - Se o usuário fornecer ou já tiver definido um `custo_troca` para aquele parceiro em conversas anteriores, use o mesmo valor. Não invente um número — se não houver um definido, pergunte ao usuário ou explique como calculá-lo (ver seção "Como calcular custo_troca" abaixo).

3. **Leia o output do script e os avisos de `limpeza.py`.** Preste atenção especial a:
   - Avisos de outliers e "choques comuns" (indício de evento externo afetando múltiplos grupos na mesma data).
   - Casos degenerados (variância zero em algum grupo) — a decisão nesses casos é determinística, não estatística.
   - Decisão "inconclusivo" — significa que nenhum grupo superou todos os demais com confiança suficiente; não force uma recomendação onde os dados não sustentam uma.

4. **Traduza o resultado para linguagem natural**, no formato:
   - Qual foi a decisão (escalar / manter controle / inconclusivo) e qual grupo, se houver.
   - Por que — cite a lógica dos dois filtros (estatístico + negócio), não apenas o resultado final.
   - Qualquer nota de atenção relevante (ex: outlier simultâneo, variância zero, amostra pequena).

5. **Confirme que a planilha (`planilha_acompanhamento.csv`) foi atualizada** e informe isso ao usuário. Se rodar de novo para o mesmo parceiro, a linha antiga é substituída, não duplicada.

6. **Se o usuário pedir um relatório apresentável** (não apenas o resumo em chat), gere um documento (`.docx` ou `.md`) seguindo o mesmo padrão de `Relatorio_Testes_AB_Cashback.docx`: sumário executivo, metodologia do custo_troca, uma seção por parceiro com tabela de médias, decisão final destacada, e notas de atenção.

## Como calcular custo_troca (quando não fornecido)

`custo_troca` é o ganho mínimo de margem (R$/dia) para justificar escalar uma variante. Não é derivado dos dados — é uma estimativa de negócio. A fórmula usada neste projeto:

```
custo_troca = max(custo_operacional_amortizado, 1.5 × desvio_padrão_pooled_da_margem_do_parceiro)
```

- `custo_operacional_amortizado` = (horas de trabalho estimadas × taxa/hora) ÷ dias de amortização. Sem uma estimativa melhor, use como referência ilustrativa: 6h, ~R$15/hora, amortizado em 30 dias (~R$3/dia) — mas deixe claro ao usuário que isso é uma suposição, não um dado real do negócio.
- `desvio_padrão_pooled` = desvio-padrão da margem diária, ponderado entre todos os grupos do parceiro (não apenas um grupo "controle", já que os datasets não identificam qual grupo é o baseline).

Nunca decida esse número sozinho sem avisar o usuário que é uma suposição — pergunte se ele já tem um valor definido, ou ofereça calcular com a fórmula acima deixando a arbitrariedade explícita.

## Regras gerais

- **Nunca altere o código dos scripts em `src/` para acomodar um dataset específico.** A solução deve funcionar em qualquer dataset com o mesmo schema, apenas trocando o argumento do caminho.
- **Nunca invente números de negócio** (custo_troca, piso de relevância prática, etc.) sem deixar explícito que é uma suposição e sem oferecer a lógica por trás.
- **Não esconda incerteza estatística.** Se o teste F conjunto não for significativo ou a decisão for "inconclusivo", comunique isso claramente — não force uma recomendação de "escalar" só porque um grupo teve média nominalmente mais alta.
- **Sinalize dados suspeitos** (outliers, choques comuns, variância zero) ao usuário, mesmo que a decisão final não dependa diretamente deles.

## Schema esperado dos datasets

| Coluna | Tipo | Descrição |
|---|---|---|
| Data | YYYY-MM-DD | Data da observação |
| Grupos de usuários | string | Variante do teste (Grupo 1, Grupo 2, ...) |
| Parceiro | string | Parceiro do teste |
| compradores | int | Usuários únicos que compraram no dia |
| comissão | string (R$) | Comissão paga pelo parceiro ao Méliuz no dia |
| cashback | string (R$) | Cashback distribuído aos usuários no dia |
| vendas totais | string (R$) | GMV (valor total das vendas) no dia |
