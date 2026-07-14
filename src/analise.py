"""
Módulo de análise estatística do teste A/B.

Implementa as 5 decisões metodológicas definidas:
  1. Erros-padrão HAC (Newey-West) — corrige heterocedasticidade E autocorrelação
     serial, adequado para dado em série temporal diária (HC0 não corrige autocorrelação).
  2. Comparação pairwise entre grupos, sem assumir qual é o "controle" —
     nenhum dataset indica hierarquia, então reportamos todos os pares.
  3. Teste F conjunto primeiro ("existe alguma diferença entre os grupos?");
     comparações par-a-par só são decompostas se o F for significativo,
     reduzindo o risco de falso positivo por múltiplas comparações.
  4. Piso mínimo de relevância prática (diferença de margem em %), combinado
     com significância estatística — p<0.05 sozinho não basta pra decisão de negócio.
  5. Dummy de dia atípico (choque comum, quando outlier aparece em >1 grupo no
     mesmo dia) incluída na regressão, em vez de excluir as linhas.
"""

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
import statsmodels.api as sm


def _num_lags_newey_west(t: int) -> int:
    """Regra de bolso comum para número de lags do HAC: floor(4*(T/100)^(2/9))."""
    return max(1, int(np.floor(4 * (t / 100) ** (2 / 9))))


def rodar_regressao(df: pd.DataFrame, metrica: str = "margem"):
    """
    Roda OLS de `metrica` em função de grupo + dia da semana + tendência linear
    + dummy de dia atípico, com erros-padrão HAC (Newey-West).

    Retorna o objeto de resultados (statsmodels) já com cov HAC aplicada.
    """
    d = df.copy()
    d["t"] = (d["Data"] - d["Data"].min()).dt.days  # tendência linear
    d["grupo_cat"] = d["Grupos de usuários"].astype("category")
    # grupo de referência = primeiro em ordem alfabética (ex: "Grupo 1")
    grupos_ordenados = sorted(d["grupo_cat"].unique().tolist())
    d["grupo_cat"] = d["grupo_cat"].cat.reorder_categories(grupos_ordenados)

    formula = (
        f"{metrica} ~ C(grupo_cat, Treatment(reference='{grupos_ordenados[0]}')) "
        f"+ C(dia_semana) + t + C(dia_atipico)"
    )
    modelo = smf.ols(formula, data=d)

    n_lags = _num_lags_newey_west(len(d))
    resultado = modelo.fit(cov_type="HAC", cov_kwds={"maxlags": n_lags})
    return resultado, grupos_ordenados, n_lags


def teste_f_conjunto(resultado, grupos_ordenados):
    """
    Testa H0: todos os coeficientes de grupo (exceto referência) = 0.
    Se p < 0.05, há evidência de diferença entre PELO MENOS um par de grupos.
    """
    nomes_coef = [n for n in resultado.params.index if n.startswith("C(grupo_cat")]
    if not nomes_coef:
        return None
    hipotese = " = ".join(nomes_coef) + " = 0"
    f_test = resultado.f_test(hipotese)
    return {
        "estatistica_f": float(f_test.fvalue),
        "p_valor": float(f_test.pvalue),
        "significativo": float(f_test.pvalue) < 0.05,
    }


def comparacoes_pairwise(resultado, grupos_ordenados):
    """
    Compara cada par de grupos (incluindo pares que não envolvem a referência,
    via contraste linear). Retorna lista de dicts com diferença estimada, IC95%,
    e p-valor — usando a MESMA matriz de covariância HAC do modelo.
    """
    ref = grupos_ordenados[0]
    pares = []
    for i in range(len(grupos_ordenados)):
        for j in range(i + 1, len(grupos_ordenados)):
            g1, g2 = grupos_ordenados[i], grupos_ordenados[j]
            nome1 = f"C(grupo_cat, Treatment(reference='{ref}'))[T.{g1}]"
            nome2 = f"C(grupo_cat, Treatment(reference='{ref}'))[T.{g2}]"

            if g1 == ref:
                # comparação direto contra a referência: usa o coeficiente puro
                contraste = f"{nome2} = 0"
            else:
                # diferença entre dois grupos não-referência
                contraste = f"{nome2} - {nome1} = 0"

            teste = resultado.t_test(contraste)
            pares.append({
                "grupo_a": g1,
                "grupo_b": g2,
                "diferenca_estimada": float(teste.effect[0]),
                "ic95_inferior": float(teste.conf_int()[0][0]),
                "ic95_superior": float(teste.conf_int()[0][1]),
                "p_valor": float(teste.pvalue),
                "significativo": float(teste.pvalue) < 0.05,
            })
    return pares


def effect_size_minimo_detectavel(resultado, metrica_media: float, alpha=0.05, power=0.8):
    """
    Effect size mínimo detectável (MDE) aproximado, dado o erro-padrão HAC do
    modelo e o N disponível. Ajuda a distinguir "sem diferença real" de
    "teste sem poder estatístico suficiente pra detectar diferença que existe".
    Aproximação: MDE ≈ (z_alpha/2 + z_power) * erro_padrao_medio_dos_coefs_de_grupo
    """
    from scipy.stats import norm

    nomes_coef = [n for n in resultado.params.index if n.startswith("C(grupo_cat")]
    if not nomes_coef:
        return None
    erro_padrao_medio = float(np.mean([resultado.bse[n] for n in nomes_coef]))

    z_alpha = norm.ppf(1 - alpha / 2)
    z_power = norm.ppf(power)
    mde_absoluto = (z_alpha + z_power) * erro_padrao_medio
    mde_percentual = (mde_absoluto / metrica_media * 100) if metrica_media else np.nan

    return {
        "mde_absoluto": mde_absoluto,
        "mde_percentual": mde_percentual,
    }


def grupos_com_variancia_zero(df: pd.DataFrame, metrica: str) -> list:
    """
    Detecta grupos cuja métrica não varia (std=0 ou NaN por constância).
    Regressão HAC fica degenerada nesses casos (covariância sem rank completo)
    porque não há erro a estimar -- o resultado é determinístico, não estatístico.
    """
    stats = df.groupby("Grupos de usuários")[metrica].std()
    return stats[(stats == 0) | (stats.isna())].index.tolist()


def analisar(df: pd.DataFrame, metrica: str = "margem"):
    """
    Pipeline completo: regressão -> teste F -> pairwise -> effect size.
    Retorna um dict com tudo pronto para a camada de decisão (decisao.py).

    Nota: a "relevância prática" de uma diferença é decidida pelo MDE (effect
    size mínimo detectável), não por um piso de % arbitrário -- por isso não
    há parâmetro de piso de relevância aqui. `diferenca_percentual` é mantida
    só como leitura auxiliar (dá contexto de magnitude ao lado do R$), sem
    influenciar a decisão em decisao.py.
    """
    grupos_degenerados = grupos_com_variancia_zero(df, metrica)
    if grupos_degenerados:
        medias = df.groupby("Grupos de usuários")[metrica].mean()
        return {
            "metrica": metrica,
            "grupos": sorted(df["Grupos de usuários"].unique().tolist()),
            "caso_degenerado": True,
            "grupos_variancia_zero": grupos_degenerados,
            "medias_por_grupo": medias.to_dict(),
            "observacao": (
                f"Grupo(s) {grupos_degenerados} têm '{metrica}' constante (variância zero). "
                f"Regressão HAC não é aplicável -- diferença é determinística, não estatística. "
                f"Comparar médias diretamente."
            ),
        }

    resultado, grupos_ordenados, n_lags = rodar_regressao(df, metrica)
    f_conjunto = teste_f_conjunto(resultado, grupos_ordenados)
    pares = comparacoes_pairwise(resultado, grupos_ordenados) if f_conjunto and f_conjunto["significativo"] else []

    media_geral = df[metrica].mean()
    mde = effect_size_minimo_detectavel(resultado, media_geral)

    # diferença em % da média geral -- só para leitura, não usada na decisão
    for p in pares:
        p["diferenca_percentual"] = (p["diferenca_estimada"] / media_geral * 100) if media_geral else np.nan

    return {
        "metrica": metrica,
        "grupos": grupos_ordenados,
        "n_lags_hac": n_lags,
        "media_geral": media_geral,
        "teste_f_conjunto": f_conjunto,
        "comparacoes_pairwise": pares,
        "effect_size_minimo_detectavel": mde,
        "resumo_modelo": resultado.summary().as_text(),
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "src")
    from limpeza import carregar_dados
    from metricas import calcular_metricas

    caminho = sys.argv[1] if len(sys.argv) > 1 else "dados/dataset_02_parceiroB.csv"
    r = carregar_dados(caminho)
    df = calcular_metricas(r["df"])

    print(f"Parceiro: {r['parceiro']} | Grupos: {r['grupos']}\n")

    analise = analisar(df, metrica="margem")

    if analise.get("caso_degenerado"):
        print(f"CASO DEGENERADO: {analise['observacao']}\n")
        for g, m in analise["medias_por_grupo"].items():
            print(f"  {g}: média = R$ {m:.2f}")
        sys.exit(0)

    print(f"Lags HAC usados: {analise['n_lags_hac']}")
    print(f"Média geral de margem: R$ {analise['media_geral']:.2f}\n")

    ft = analise["teste_f_conjunto"]
    print(f"Teste F conjunto: F={ft['estatistica_f']:.3f}, p={ft['p_valor']:.4f} "
          f"-> {'SIGNIFICATIVO' if ft['significativo'] else 'não significativo'}")

    if analise["comparacoes_pairwise"]:
        print("\nComparações par-a-par (margem):")
        for p in analise["comparacoes_pairwise"]:
            print(
                f"  {p['grupo_a']} vs {p['grupo_b']}: "
                f"dif=R$ {p['diferenca_estimada']:.2f} ({p['diferenca_percentual']:.1f}%) "
                f"p={p['p_valor']:.4f} [{'sig' if p['significativo'] else 'ns'}]"
            )
    else:
        print("\nSem decomposição pairwise (teste F não significativo).")

    mde = analise["effect_size_minimo_detectavel"]
    if mde:
        print(f"\nEffect size mínimo detectável: R$ {mde['mde_absoluto']:.2f} "
              f"({mde['mde_percentual']:.1f}% da média)")
