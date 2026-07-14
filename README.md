# Análise de Testes A/B — Cashback (Méliuz)

Solução reutilizável para analisar testes A/B de variação de % de cashback e decidir, de forma consistente e reprodutível, qual variante escalar para 100% do tráfego.

Roda em qualquer um dos datasets fornecidos (Parceiro A, B ou C) **sem alterar nenhuma linha de código** — só trocando o caminho do arquivo passado como argumento.

## Pergunta que a solução responde

> Dado esse teste A/B, qual variante de cashback devemos escalar pra 100% do tráfego?

## Estrutura do projeto

```
meliuz-ab-test/
├── dados/
│   ├── dataset_01_parceiroA.csv
│   ├── dataset_02_parceiroB.csv
│   └── dataset_03_parceiroC.csv
├── src/
│   ├── limpeza.py      # carrega e limpa os dados, detecta outliers e choques comuns
│   ├── metricas.py      # calcula métricas derivadas (margem = comissão - cashback, etc.)
│   ├── analise.py       # teste F conjunto, comparações pairwise, IC95%, MDE
│   ├── decisao.py       # aplica os dois filtros (estatístico + negócio) e decide
│   └── registro.py      # roda o pipeline completo e registra o resultado na planilha
├── planilha_acompanhamento.csv   # gerada/atualizada pelo registro.py
├── Relatorio_Testes_AB_Cashback.docx  # relatório consolidado, apresentável para gestor
└── README.md
```

## Requisitos

- Python 3.10+ (testado com 3.14)
- Bibliotecas: `pandas`, `numpy`, `statsmodels`

## Instalação

Na raiz do projeto:

```bash
pip install -r requirements.txt
```

## Como rodar

Todos os comandos abaixo são executados **a partir da raiz do projeto** (`meliuz-ab-test/`).

### 1. Só a limpeza (conferir avisos de qualidade de dado)

```bash
python src/limpeza.py dados/dataset_01_parceiroA.csv
```

Mostra os grupos identificados, avisos de outliers/choques comuns detectados, e o shape final dos dados limpos.

### 2. Decisão de negócio (sem registrar na planilha)

```bash
python src/decisao.py dados/dataset_01_parceiroA.csv
```

Roda o pipeline completo e imprime no terminal a decisão, com ou sem `custo_troca`:

```bash
# sem custo_troca -- só o filtro estatístico é avaliado
python src/decisao.py dados/dataset_01_parceiroA.csv

# com custo_troca -- avalia também o filtro de negócio (R$/dia)
python src/decisao.py dados/dataset_01_parceiroA.csv 3041.68
```

### 3. Rodar e registrar na planilha de acompanhamento (fluxo recomendado)

```bash
python src/registro.py dados/dataset_01_parceiroA.csv 3041.68
python src/registro.py dados/dataset_02_parceiroB.csv 1922.82
python src/registro.py dados/dataset_03_parceiroC.csv
```

Isso roda o pipeline completo e grava/atualiza uma linha em `planilha_acompanhamento.csv`, com: nome do teste, parceiro, descrição, período, número de grupos, resumo do resultado estatístico, decisão, grupo recomendado, custo_troca usado e data do registro. Rodar de novo para o mesmo parceiro **atualiza** a linha existente em vez de duplicar.

Parâmetro opcional de destino da planilha:

```bash
python src/registro.py dados/dataset_01_parceiroA.csv 3041.68 --planilha caminho/custom.csv
```

## Metodologia (resumo)

A decisão de escalar uma variante passa por dois filtros aplicados em sequência a cada comparação par-a-par entre grupos:

1. **Filtro estatístico** — a diferença é confiável? `IC95% não cruza zero` **e** `|diferença| ≥ MDE` (effect size mínimo detectável, calculado a partir da amostra). Abaixo do MDE, o teste não tem poder estatístico suficiente para confiar no resultado, independente do p-valor.

2. **Filtro de negócio** — mesmo no cenário mais conservador, ainda compensa agir? `limite inferior do IC95% > custo_troca`.

`custo_troca` (R$/dia) é o ganho mínimo necessário para justificar o esforço/risco de escalar uma variante. Não é derivado dos dados — é uma estimativa de negócio, calculada aqui como:

```
custo_troca = max(custo_operacional_amortizado, 1.5 × desvio_padrão_pooled_da_margem)
```

- **Custo operacional amortizado**: estimativa ilustrativa de horas de trabalho para reconfigurar a variante ativa, convertida em R$/dia. Usamos 6h a ~R$15,15/hora (referência: salário médio de estágio em Growth/IA no Brasil, ~R$2.000/mês), amortizado em 30 dias.
- **1,5× desvio-padrão pooled**: piso de "ruído natural do negócio" — como os datasets não identificam qual grupo era o controle, usamos o desvio-padrão ponderado (pooled) entre todos os grupos do parceiro, em vez de supor qual grupo era o baseline.

O uso do **máximo** (em vez de soma ou média) garante que a mudança precisa, ao mesmo tempo, cobrir o custo de implementação **e** ser grande o suficiente para não se perder na variação natural do negócio.

Um teste de sensibilidade (variando o custo operacional de R$3 a R$21/dia) mostrou que o desvio-padrão domina o cálculo em ambos os parceiros com incerteza estatística — ou seja, a decisão final é pouco sensível à premissa mais arbitrária (custo operacional) e depende principalmente da variação real do negócio.

Detalhes completos, incluindo o resultado de cada parceiro, estão no relatório: `Relatorio_Testes_AB_Cashback.docx`.

## Resultado consolidado

| Parceiro | custo_troca (R$/dia) | Decisão | Grupo recomendado |
|---|---|---|---|
| A | 3.041,68 | inconclusivo | — |
| B | 1.922,82 | escalar | Grupo 1 |
| C | não se aplica (caso determinístico) | escalar | Grupo 1 |

## Limitações conhecidas

- O `custo_troca` usado é uma estimativa ilustrativa; recomenda-se substituí-lo por um número validado com o time de growth/engenharia antes de uso definitivo em produção.
- Parceiro A requer re-teste ou aumento de amostra para permitir uma decisão conclusiva.
- Parceiro C: o Grupo 2 tem cashback idêntico à comissão em 100% das linhas (margem zero). Vale confirmar com o time se isso é uma regra de negócio intencional (repasse total) ou um erro de exportação de dados antes de tratar a decisão como definitiva.
- A limpeza de dados identifica choques comuns (outliers simultâneos em múltiplos grupos na mesma data) em datas específicas de cada parceiro — recomenda-se tratá-los via variável dummy em uma regressão futura, não excluí-los.
