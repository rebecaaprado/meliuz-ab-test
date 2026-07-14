"""
Módulo de métricas derivadas para análise de teste A/B de cashback.

Recebe o DataFrame já limpo (saída de limpeza.carregar_dados) e adiciona:
    - ticket_medio   = vendas_totais / compradores
    - margem         = comissão - cashback
    - roi_cashback   = margem / cashback
    - taxa_cashback  = cashback / vendas_totais
"""

import pandas as pd
import numpy as np


def calcular_metricas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # evita divisão por zero -> vira NaN (será tratado como aviso, não erro)
    df["ticket_medio"] = np.where(
        df["compradores"] > 0,
        df["vendas totais"] / df["compradores"],
        np.nan,
    )

    df["margem"] = df["comissão"] - df["cashback"]

    df["roi_cashback"] = np.where(
        df["cashback"] > 0,
        df["margem"] / df["cashback"],
        np.nan,
    )

    df["taxa_cashback"] = np.where(
        df["vendas totais"] > 0,
        df["cashback"] / df["vendas totais"],
        np.nan,
    )

    return df


def resumo_por_grupo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agregação descritiva por grupo (média, desvio-padrão, N).
    Serve como sanity check inicial antes de qualquer teste estatístico.
    """
    metricas = [
        "compradores", "vendas totais", "margem",
        "roi_cashback", "taxa_cashback", "ticket_medio",
    ]
    agg = df.groupby("Grupos de usuários")[metricas].agg(["mean", "std", "count"])
    agg.columns = ["_".join(c) for c in agg.columns]
    return agg.reset_index()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "src")
    from limpeza import carregar_dados

    caminho = sys.argv[1] if len(sys.argv) > 1 else "dados/dataset_01_parceiroA.csv"
    resultado = carregar_dados(caminho)
    df = calcular_metricas(resultado["df"])

    print(f"Parceiro: {resultado['parceiro']}\n")
    resumo = resumo_por_grupo(df)
    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
    pd.set_option("display.width", 160)
    print(resumo.to_string(index=False))

    # sanity check: quantas linhas geraram NaN nas métricas derivadas?
    for col in ["ticket_medio", "roi_cashback", "taxa_cashback"]:
        n_nan = df[col].isna().sum()
        if n_nan > 0:
            print(f"\nAviso: {n_nan} linha(s) com '{col}' = NaN (divisão por zero).")
