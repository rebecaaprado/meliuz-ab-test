"""
Módulo de integração com Google Sheets.

Escreve/atualiza uma linha por parceiro na planilha de acompanhamento, usando
uma service account (credencial em service_account.json, nunca commitada --
veja .gitignore).

Se a credencial ou o ID da planilha não estiverem configurados, as funções
deste módulo falham de forma explícita (não silenciosa) -- quem chama decide
se quer tratar isso como erro fatal ou como fallback para o CSV local
(ver registro.py).
"""

import os
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# nomes das colunas, na ordem em que aparecem na planilha (mesmo schema do CSV)
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


def _abrir_planilha(caminho_credencial: str, sheet_id: str, aba: str = None):
    """
    aba: nome da worksheet (aba) dentro da planilha. Se None (default), usa a
    primeira aba por posição (.sheet1) -- mesmo comportamento de antes. Passar
    um nome explícito evita quebrar silenciosamente se a planilha ganhar mais
    de uma aba ou a ordem delas mudar.
    """
    if not os.path.exists(caminho_credencial):
        raise FileNotFoundError(
            f"Credencial não encontrada em '{caminho_credencial}'. "
            f"Baixe a chave JSON da service account e salve nesse caminho "
            f"(veja README, seção Google Sheets)."
        )
    creds = Credentials.from_service_account_file(caminho_credencial, scopes=SCOPES)
    cliente = gspread.authorize(creds)
    planilha = cliente.open_by_key(sheet_id)
    if aba:
        return planilha.worksheet(aba)
    return planilha.sheet1


def _garantir_cabecalho(planilha):
    primeira_linha = planilha.row_values(1)
    if primeira_linha != COLUNAS:
        planilha.update(range_name="A1", values=[COLUNAS])


def registrar_no_sheets(linha: dict, sheet_id: str, caminho_credencial: str = "service_account.json", aba: str = None) -> None:
    """
    Grava/atualiza a linha do parceiro na planilha do Google Sheets.
    `linha` é o mesmo dict que registro.py já monta para o CSV local --
    ambos os destinos (Sheets e CSV) ficam sempre em sincronia.

    Se já existir uma linha para o mesmo parceiro (coluna B), ela é
    sobrescrita em vez de duplicada -- mesmo comportamento do CSV.

    `aba`: nome da worksheet a usar (ver _abrir_planilha). Default None usa a
    primeira aba por posição.
    """
    planilha = _abrir_planilha(caminho_credencial, sheet_id, aba)
    _garantir_cabecalho(planilha)

    valores = [str(linha.get(c, "")) for c in COLUNAS]

    # procura se o parceiro já tem uma linha (coluna "parceiro" = índice 2, 1-based)
    todas_linhas = planilha.get_all_values()
    linha_existente = None
    for i, row in enumerate(todas_linhas[1:], start=2):  # pula cabeçalho
        if len(row) > 1 and row[1] == linha["parceiro"]:
            linha_existente = i
            break

    if linha_existente:
        planilha.update(range_name=f"A{linha_existente}", values=[valores])
    else:
        planilha.append_row(valores)


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Uso: python src/sheets.py <SHEET_ID> [caminho_credencial]")
        sys.exit(1)

    sheet_id_teste = sys.argv[1]
    cred_teste = sys.argv[2] if len(sys.argv) > 2 else "service_account.json"

    linha_teste = {
        "nome_teste": "Teste de conexão -- sheets.py",
        "parceiro": "TESTE_CONEXAO",
        "descricao": "Linha de teste gerada por 'python src/sheets.py' para validar a integração.",
        "periodo": "-",
        "n_grupos": "-",
        "resultado": "-",
        "decisao": "-",
        "grupo_recomendado": "-",
        "custo_troca_usado": "-",
    }

    try:
        registrar_no_sheets(linha_teste, sheet_id_teste, cred_teste)
        print("Conexão OK -- linha de teste gravada na planilha. Pode apagar essa linha manualmente.")
    except Exception as e:
        print(f"Falhou: {e}")
        sys.exit(1)
