"""
Módulo de limpeza e validação dos dados de teste A/B de cashback.

Responsável por:
- Ler o CSV e detectar parceiro e grupos automaticamente (nada hardcoded)
- Converter valores monetários "R$ 2.911" -> float
- Validar consistência (duplicatas, negativos, grupos com poucos dias)
- Flagar (não remover) outliers e anomalias de dados
"""

import pandas as pd
import numpy as np
import re
import warnings


def _parse_valor_brl(valor: str) -> float:
    """Converte string 'R$ 2.911' ou 'R$ 1.234,56' para float.
    Formato BR: ponto = milhar, vírgula = decimal.
    """
    if pd.isna(valor):
        return np.nan
    s = str(valor).strip()
    s = re.sub(r"[Rr]\$\s*", "", s)
    # remove separador de milhar (ponto) e troca vírgula decimal por ponto
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def carregar_dados(caminho_csv: str) -> dict:
    """
    Lê o CSV, limpa e retorna um dict com:
        - df: DataFrame limpo
        - parceiro: nome do parceiro detectado
        - grupos: lista de grupos detectados
        - avisos: lista de strings com problemas encontrados nos dados
    """
    avisos = []

    df = pd.read_csv(caminho_csv)

    # normaliza nomes de coluna (tira acento/espaço de forma defensiva)
    df.columns = [c.strip() for c in df.columns]
    colunas_esperadas = {
        "Data", "Grupos de usuários", "Parceiro",
        "compradores", "comissão", "cashback", "vendas totais"
    }
    faltando = colunas_esperadas - set(df.columns)
    if faltando:
        raise ValueError(f"Colunas faltando no CSV: {faltando}")

    # tipos
    df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
    if df["Data"].isna().any():
        n = df["Data"].isna().sum()
        avisos.append(f"{n} linha(s) com Data inválida foram encontradas.")

    for col in ["comissão", "cashback", "vendas totais"]:
        df[col] = df[col].apply(_parse_valor_brl)

    df["compradores"] = pd.to_numeric(df["compradores"], errors="coerce")

    # detecta parceiro e grupos
    parceiros = df["Parceiro"].dropna().unique().tolist()
    if len(parceiros) != 1:
        avisos.append(f"Esperado 1 parceiro por arquivo, encontrado(s): {parceiros}")
    parceiro = parceiros[0] if parceiros else "desconhecido"

    grupos = sorted(df["Grupos de usuários"].dropna().unique().tolist())

    # duplicatas (mesma data + grupo)
    dup = df.duplicated(subset=["Data", "Grupos de usuários"], keep=False)
    if dup.any():
        avisos.append(f"{dup.sum()} linha(s) duplicadas (mesma Data + Grupo).")

    # valores negativos ou nulos onde não deveriam existir
    for col in ["compradores", "comissão", "cashback", "vendas totais"]:
        neg = (df[col] < 0).sum()
        if neg > 0:
            avisos.append(f"{neg} valor(es) negativo(s) em '{col}'.")
        nulos = df[col].isna().sum()
        if nulos > 0:
            avisos.append(f"{nulos} valor(es) nulo(s)/não convertido(s) em '{col}'.")

    # cobertura de dias por grupo
    for g in grupos:
        n_dias = df[df["Grupos de usuários"] == g]["Data"].nunique()
        if n_dias < 14:
            avisos.append(
                f"Grupo '{g}' tem apenas {n_dias} dias de dados "
                f"(amostra pequena, resultado deve ser lido com cautela)."
            )

    # flag: cashback igual a comissão (possível regra de negócio distinta ou dado duplicado)
    for g in grupos:
        sub = df[df["Grupos de usuários"] == g]
        if len(sub) > 0 and (sub["cashback"] == sub["comissão"]).all():
            avisos.append(
                f"Grupo '{g}': cashback é idêntico a comissão em 100% das linhas — "
                f"verificar se é regra de negócio (ex: repasse total) ou erro de exportação. "
                f"Não foi corrigido automaticamente."
            )

    # flag de outliers por z-score (por grupo, em 'compradores') — não remove, só marca
    df["outlier_compradores"] = False
    for g in grupos:
        mask = df["Grupos de usuários"] == g
        serie = df.loc[mask, "compradores"]
        if serie.std() > 0:
            z = (serie - serie.mean()) / serie.std()
            df.loc[mask, "outlier_compradores"] = (z.abs() > 3)

    n_outliers = df["outlier_compradores"].sum()
    if n_outliers > 0:
        datas_out = df.loc[df["outlier_compradores"], "Data"].dt.date.unique()
        avisos.append(
            f"{n_outliers} observação(ões) com 'compradores' fora de 3 desvios-padrão "
            f"do grupo. Datas: {sorted(datas_out)}."
        )
        # choque comum: mesma data aparece como outlier em mais de 1 grupo?
        datas_por_grupo = df[df["outlier_compradores"]].groupby(
            df["Data"].dt.date
        )["Grupos de usuários"].nunique()
        datas_comuns = datas_por_grupo[datas_por_grupo > 1].index.tolist()
        if datas_comuns:
            avisos.append(
                f"Datas {datas_comuns} têm outlier em MAIS DE UM grupo simultaneamente "
                f"-> indício de choque comum (evento externo), não ruído de um grupo só. "
                f"Recomenda-se tratar via dummy na regressão, não excluir."
            )
            df["dia_atipico"] = df["Data"].dt.date.isin(datas_comuns)
        else:
            df["dia_atipico"] = False
    else:
        df["dia_atipico"] = False

    df["dia_semana"] = df["Data"].dt.dayofweek  # 0=segunda

    return {
        "df": df,
        "parceiro": parceiro,
        "grupos": grupos,
        "avisos": avisos,
    }


if __name__ == "__main__":
    import sys
    resultado = carregar_dados(sys.argv[1] if len(sys.argv) > 1 else "dados/dataset_02_parceiroB.csv")
    print(f"Parceiro: {resultado['parceiro']}")
    print(f"Grupos: {resultado['grupos']}")
    print(f"\nAvisos ({len(resultado['avisos'])}):")
    for a in resultado["avisos"]:
        print(f"  - {a}")
    print(f"\nShape final: {resultado['df'].shape}")
