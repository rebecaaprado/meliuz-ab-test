"""
Módulo de decisão final do teste A/B.

Aplica dois filtros em sequência para cada comparação par-a-par:

  FILTRO 1 (estatístico) — a diferença é confiável?
      IC95% não cruza zero  E  |diferença| >= effect size mínimo detectável (MDE)
      (abaixo do MDE, o teste não tem poder estatístico suficiente pra confiar
      no resultado, independente do p-valor.)

  FILTRO 2 (negócio) — mesmo no cenário mais conservador, ainda compensa agir?
      limite inferior do IC95% (na direção do grupo favorecido) > custo_troca
      (custo_troca é uma estimativa de negócio de quanto a mudança precisa
      valer a pena para justificar o esforço/risco de escalar uma variante --
      não é derivado dos dados, é definido por quem interpreta o resultado.)

Só recomenda escalar uma variante se ela passar nos dois filtros contra
TODOS os outros grupos.
"""

import numpy as np


class CustoTrocaNaoDefinidoError(Exception):
    """Levantado quando custo_troca não foi definido -- decisão de negócio
    não pode ser tomada automaticamente sem esse parâmetro."""
    pass


def _pares_favorecidos(pares: list) -> list:
    """
    Reorienta cada par para (grupo_favorecido, grupo_desfavorecido, diferenca_abs,
    ic_inferior_na_direcao_favoravel), já que 'diferenca_estimada' é sempre
    grupo_b - grupo_a e pode ser negativa.
    """
    reorientados = []
    for p in pares:
        if p["diferenca_estimada"] >= 0:
            favorecido, desfavorecido = p["grupo_b"], p["grupo_a"]
            ic_inf = p["ic95_inferior"]
        else:
            favorecido, desfavorecido = p["grupo_a"], p["grupo_b"]
            ic_inf = -p["ic95_superior"]  # inverte o IC junto com o sinal
        reorientados.append({
            **p,
            "favorecido": favorecido,
            "desfavorecido": desfavorecido,
            "diferenca_abs": abs(p["diferenca_estimada"]),
            "ic_inferior_direcao_favoravel": ic_inf,
        })
    return reorientados


def tomar_decisao(resultado_analise: dict, custo_troca: float = None) -> dict:
    """
    resultado_analise: saída de analise.analisar()
    custo_troca: ganho mínimo de margem (R$/dia) para justificar escalar uma
                 variante. Deve ser definido por quem interpreta o resultado --
                 não é calculado a partir dos dados.

    Retorna dict com: decisao, grupo_recomendado (ou None), justificativa,
    detalhes de cada filtro.
    """
    grupos = resultado_analise["grupos"]

    # --- caso degenerado (variância zero em algum grupo) ---
    if resultado_analise.get("caso_degenerado"):
        medias = resultado_analise["medias_por_grupo"]
        melhor_grupo = max(medias, key=medias.get)
        pior_grupo = min(medias, key=medias.get)
        diff = medias[melhor_grupo] - medias[pior_grupo]
        return {
            "decisao": "escalar" if diff > 0 else "manter controle",
            "grupo_recomendado": melhor_grupo if diff > 0 else None,
            "justificativa": (
                f"Diferença determinística (variância zero em pelo menos um grupo). "
                f"{melhor_grupo} tem média R$ {medias[melhor_grupo]:.2f} vs "
                f"{pior_grupo} R$ {medias[pior_grupo]:.2f} -- não requer teste "
                f"estatístico, a diferença é certa, não estimada."
            ),
            "filtro_estatistico": "não aplicável (caso degenerado)",
            "filtro_negocio": "não aplicável (caso degenerado)",
        }

    # --- teste F conjunto ausente ou não significativo: para tudo aqui ---
    f_conjunto = resultado_analise["teste_f_conjunto"]
    if not f_conjunto or not f_conjunto["significativo"]:
        if f_conjunto is None:
            justificativa = (
                "Não foi possível calcular o teste F conjunto (nenhum coeficiente de "
                "grupo disponível -- provavelmente há um único grupo no dataset). "
                "Sem evidência de diferença entre grupos a avaliar."
            )
        else:
            justificativa = (
                f"Teste F conjunto não significativo (p={f_conjunto['p_valor']:.4f}). "
                f"Sem evidência de diferença entre os grupos -- não decompomos em "
                f"pares (evita inflar falso positivo)."
            )
        return {
            "decisao": "manter controle",
            "grupo_recomendado": None,
            "justificativa": justificativa,
            "filtro_estatistico": "reprovado no teste F conjunto" if f_conjunto else "não aplicável (teste F indisponível)",
            "filtro_negocio": "não avaliado",
        }

    pares = resultado_analise["comparacoes_pairwise"]
    mde_global = resultado_analise["effect_size_minimo_detectavel"]
    mde_absoluto_fallback = mde_global["mde_absoluto"] if mde_global else 0

    pares_reorientados = _pares_favorecidos(pares)

    # FILTRO 1: estatístico (IC não cruza zero + diferença >= MDE do próprio par)
    # Usa 'significativo_ajustado' (Holm-Bonferroni, corrige múltiplas comparações
    # quando há 3+ grupos) e 'mde_par_absoluto' (calculado a partir do erro-padrão
    # daquele contraste específico, não uma média global) -- ambos calculados em
    # analise.comparacoes_pairwise(). O fallback existe só por segurança, caso
    # algum chamador externo passe pares sem essas chaves.
    for p in pares_reorientados:
        significativo = p.get("significativo_ajustado", p["significativo"])
        mde_par = p.get("mde_par_absoluto", mde_absoluto_fallback)
        p["passa_filtro_estatistico"] = (
            significativo and p["diferenca_abs"] >= mde_par
        )

    # FILTRO 2: negócio (só avaliado se custo_troca foi definido)
    if custo_troca is not None:
        for p in pares_reorientados:
            p["passa_filtro_negocio"] = (
                p["passa_filtro_estatistico"]
                and p["ic_inferior_direcao_favoravel"] > custo_troca
            )
    else:
        for p in pares_reorientados:
            p["passa_filtro_negocio"] = None  # não avaliado

    # candidato a "vencedor": grupo que vence TODAS as comparações que participa
    # (mesma lógica simétrica usada no filtro de negócio: só é vencedor quem é
    # o favorecido E passa no filtro em TODAS as comparações em que aparece --
    # nenhuma comparação é descartada da checagem)
    candidatos_vitoriosos = {}
    for g in grupos:
        comparacoes_do_grupo = [p for p in pares_reorientados if g in (p["grupo_a"], p["grupo_b"])]
        venceu_todas_estatistico = all(
            p["favorecido"] == g and p["passa_filtro_estatistico"]
            for p in comparacoes_do_grupo
        )
        candidatos_vitoriosos[g] = venceu_todas_estatistico

    vencedores_estatisticos = [g for g, venceu in candidatos_vitoriosos.items() if venceu]

    if custo_troca is None:
        if len(vencedores_estatisticos) == 1:
            decisao = "significativo -- defina custo_troca para avaliar se compensa"
        else:
            decisao = "inconclusivo (estatístico)"
        return {
            "decisao": decisao,
            "grupo_recomendado": vencedores_estatisticos[0] if len(vencedores_estatisticos) == 1 else None,
            "justificativa": (
                "Filtro estatístico aplicado (IC95% + MDE). Filtro de negócio (custo_troca) "
                "ainda não definido -- decisão final de 'escalar' requer esse parâmetro."
            ),
            "filtro_estatistico": pares_reorientados,
            "filtro_negocio": "custo_troca não definido",
        }

    # com custo_troca definido: repete a lógica de vitória usando filtro de negócio
    candidatos_negocio = {}
    for g in grupos:
        comparacoes_do_grupo = [p for p in pares_reorientados if g in (p["grupo_a"], p["grupo_b"])]
        venceu_todas_negocio = all(
            p["favorecido"] == g and p["passa_filtro_negocio"]
            for p in comparacoes_do_grupo
        )
        candidatos_negocio[g] = venceu_todas_negocio

    vencedores_negocio = [g for g, venceu in candidatos_negocio.items() if venceu]

    if len(vencedores_negocio) == 1:
        return {
            "decisao": "escalar",
            "grupo_recomendado": vencedores_negocio[0],
            "justificativa": (
                f"{vencedores_negocio[0]} supera todos os demais grupos com diferença "
                f"estatisticamente confiável (IC95% + MDE) E economicamente relevante "
                f"(limite inferior do IC > custo de troca de R$ {custo_troca:.2f})."
            ),
            "filtro_estatistico": pares_reorientados,
            "filtro_negocio": f"custo_troca = R$ {custo_troca:.2f}",
        }
    elif len(vencedores_estatisticos) >= 1:
        return {
            "decisao": "significativo, mas não compensa o risco de trocar",
            "grupo_recomendado": None,
            "justificativa": (
                "Há diferença estatisticamente confiável entre grupos, mas nenhum supera "
                "o custo mínimo de troca definido -- manter controle é a decisão mais segura."
            ),
            "filtro_estatistico": pares_reorientados,
            "filtro_negocio": f"custo_troca = R$ {custo_troca:.2f}",
        }
    else:
        return {
            "decisao": "inconclusivo",
            "grupo_recomendado": None,
            "justificativa": (
                "Resultados dos pares são inconsistentes (nenhum grupo vence todos os "
                "demais de forma confiável) -- recomenda-se re-teste ou investigação "
                "adicional antes de escalar qualquer variante."
            ),
            "filtro_estatistico": pares_reorientados,
            "filtro_negocio": f"custo_troca = R$ {custo_troca:.2f}",
        }


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from limpeza import carregar_dados
    from metricas import calcular_metricas
    from analise import analisar

    caminho = sys.argv[1] if len(sys.argv) > 1 else "dados/dataset_01_parceiroA.csv"
    # custo_troca de exemplo -- SUBSTITUIR pelo valor definido com calma (ver conversa)
    custo_troca_exemplo = float(sys.argv[2]) if len(sys.argv) > 2 else None

    r = carregar_dados(caminho)
    df = calcular_metricas(r["df"])
    analise = analisar(df, metrica="margem")
    decisao = tomar_decisao(analise, custo_troca=custo_troca_exemplo)

    print(f"Parceiro: {r['parceiro']}\n")
    print(f"DECISÃO: {decisao['decisao']}")
    if decisao["grupo_recomendado"]:
        print(f"GRUPO RECOMENDADO: {decisao['grupo_recomendado']}")
    print(f"\nJustificativa: {decisao['justificativa']}")
