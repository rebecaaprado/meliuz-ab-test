"""
Registra o resultado de um teste A/B analisado na planilha de acompanhamento.
Roda o pipeline completo (limpeza -> metricas -> analise -> decisao) e
grava/atualiza uma linha por parceiro:

  - Sempre no CSV local (planilha_acompanhamento.csv) -- garantido, nunca falha
    por causa de rede/credencial.
  - No Google Sheets também, se um SHEET_ID for informado (via --sheet-id ou
    variável de ambiente MELIUZ_SHEET_ID) e a credencial (service_account.json)
    estiver configurada. Se a escrita no Sheets falhar por qualquer motivo
    (sem internet, credencial ausente, planilha não compartilhada), o script
    AVISA mas não quebra -- o CSV local já está salvo de qualquer forma.

Uso:
    python src/registro.py dados/dataset_01_parceiroA.csv
    python src/registro.py dados/dataset_01_parceiroA.csv 3041.68
    python src/registro.py dados/dataset_01_parceiroA.csv 3041.68 --sheet-id SEU_SHEET_ID
    python src/registro.py dados/dataset_01_parceiroA.csv 3041.68 --planilha caminho/custom.csv

Se o parceiro já tiver uma linha na planilha (CSV ou Sheets), ela é
atualizada (não duplicada).
"""

import sys
import os
import csv
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limpeza import carregar_dados
from metricas import calcular_metricas
from analise import analisar
from decisao import tomar_decisao


COLUNAS = [
    "nome_teste",
    "parceiro",
    "descricao",
    "periodo",
    "n_grupos",
    "resultado",
    "decisao",
    "grupo_recomendado",
    "custo_troca_usado",
]


def _medias_por_grupo(df, metrica: str, coluna_grupo: str = "Grupos de usuários") -> dict:
    """Calcula a média da métrica por grupo diretamente do DataFrame, sem depender
    de uma chave específica no dicionário de análise (que nem sempre existe)."""
    return df.groupby(coluna_grupo)[metrica].mean().to_dict()


def _resumo_resultado(analise: dict, df, metrica: str = "margem") -> str:
    """Monta um resumo textual curto do resultado estatístico para a coluna 'resultado'."""
    medias = _medias_por_grupo(df, metrica)
    partes = [f"{g}: R$ {v:.2f}/dia" for g, v in medias.items()]
    resumo_medias = "; ".join(partes)

    if analise.get("caso_degenerado"):
        return f"Caso determinístico (variância zero). Médias -- {resumo_medias}."

    f_conjunto = analise.get("teste_f_conjunto")
    if not f_conjunto or not f_conjunto.get("significativo"):
        p = f_conjunto["p_valor"] if f_conjunto else None
        p_str = f"{p:.4f}" if p is not None else "n/d"
        return (
            f"Teste F conjunto não significativo (p={p_str}). Médias -- {resumo_medias}. "
            f"Sem evidência de diferença entre grupos."
        )

    mde = analise.get("effect_size_minimo_detectavel")
    mde_str = f"R$ {mde['mde_absoluto']:.2f}/dia" if mde else "n/d"
    return (
        f"Teste F conjunto significativo (p={f_conjunto['p_valor']:.4f}). "
        f"Médias -- {resumo_medias}. MDE = {mde_str}."
    )


def _ler_planilha(caminho: str) -> list:
    if not os.path.exists(caminho):
        return []
    with open(caminho, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _escrever_planilha(caminho: str, linhas: list):
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        writer.writeheader()
        for linha in linhas:
            writer.writerow(linha)


def registrar(
    caminho_dataset: str,
    custo_troca: float = None,
    caminho_planilha: str = None,
    sheet_id: str = None,
    caminho_credencial: str = "service_account.json",
) -> dict:
    if caminho_planilha is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        caminho_planilha = os.path.join(base_dir, "planilha_acompanhamento.csv")

    r = carregar_dados(caminho_dataset)
    df = calcular_metricas(r["df"])
    resultado_analise = analisar(df, metrica="margem")
    decisao = tomar_decisao(resultado_analise, custo_troca=custo_troca)

    parceiro = r["parceiro"]
    grupos = resultado_analise["grupos"]
    data_min = df["Data"].min()
    data_max = df["Data"].max()
    # datas podem vir como Timestamp (com hora zerada) ou já como date -- normaliza pra dd/mm/aaaa
    fmt_data = lambda d: d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)

    linha = {
        "nome_teste": f"Teste A/B cashback -- {parceiro}",
        "parceiro": parceiro,
        "descricao": (
            f"Teste de variação de % de cashback em {len(grupos)} grupos "
            f"({', '.join(grupos)}), métrica avaliada: margem (comissão - cashback)."
        ),
        "periodo": f"{fmt_data(data_min)} a {fmt_data(data_max)}",
        "n_grupos": len(grupos),
        "resultado": _resumo_resultado(resultado_analise, df),
        "decisao": decisao["decisao"],
        "grupo_recomendado": decisao["grupo_recomendado"] or "-",
        "custo_troca_usado": f"R$ {custo_troca:.2f}" if custo_troca is not None else "não definido",
    }

    linhas = _ler_planilha(caminho_planilha)
    linhas = [l for l in linhas if l["parceiro"] != parceiro]  # remove versão antiga do mesmo parceiro
    linhas.append(linha)
    linhas.sort(key=lambda l: l["parceiro"])
    _escrever_planilha(caminho_planilha, linhas)

    print(f"Registrado: {linha['nome_teste']}")
    print(f"  Decisão: {linha['decisao']}")
    print(f"  Planilha (CSV) atualizada em: {caminho_planilha}")

    sheet_id = sheet_id or os.environ.get("MELIUZ_SHEET_ID")
    if sheet_id:
        try:
            from sheets import registrar_no_sheets
            registrar_no_sheets(linha, sheet_id, caminho_credencial)
            print(f"  Google Sheets atualizado (sheet_id={sheet_id}).")
        except Exception as e:
            print(f"  Aviso: não consegui atualizar o Google Sheets ({e}). "
                  f"O CSV local já está salvo normalmente.")

    return linha


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Registra o resultado de um teste A/B na planilha de acompanhamento.")
    parser.add_argument("dataset", help="Caminho do CSV do teste A/B")
    parser.add_argument("custo_troca", nargs="?", type=float, default=None, help="Custo mínimo de troca (R$/dia), opcional")
    parser.add_argument("--planilha", default=None, help="Caminho da planilha CSV de acompanhamento (default: planilha_acompanhamento.csv na raiz do projeto)")
    parser.add_argument("--sheet-id", default=None, help="ID da planilha do Google Sheets (também pode ser definido via variável de ambiente MELIUZ_SHEET_ID)")
    parser.add_argument("--credencial", default="service_account.json", help="Caminho do JSON da service account (default: service_account.json na raiz do projeto)")
    args = parser.parse_args()

    registrar(
        args.dataset,
        custo_troca=args.custo_troca,
        caminho_planilha=args.planilha,
        sheet_id=args.sheet_id,
        caminho_credencial=args.credencial,
    )
