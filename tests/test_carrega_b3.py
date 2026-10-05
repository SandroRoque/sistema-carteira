import pandas as pd

from carrega_b3 import carregar_arquivo
from database import connect, fetch_all, scalar

_COLUNAS = [
    "Entrada/Saída", "Data", "Movimentação", "Produto",
    "Instituição", "Quantidade", "Preço unitário", "Valor da Operação",
]


def _relatorio(*linhas) -> pd.DataFrame:
    return pd.DataFrame(list(linhas), columns=_COLUNAS)


_DIVIDENDO = ("Credito", "15/03/2025", "Dividendo", "PETR4 - PETROBRAS", "NU INVEST", 100, 1.5, 150.0)
_BONIFICACAO = ("Credito", "20/03/2025", "Bonificação em Ativos", "ITSA4 - ITAUSA", "NU INVEST", 10, "-", "-")


def test_carrega_e_deduplica_por_data(investidor_a):
    df = _relatorio(_DIVIDENDO, _BONIFICACAO)
    with connect() as conn:
        assert carregar_arquivo(conn, investidor_a, df, "mov-1.xlsx") == (2, 0)
        assert carregar_arquivo(conn, investidor_a, df, "mov-2.xlsx") == (0, 2)
        assert scalar(conn, "SELECT COUNT(*) FROM b3_movimentacoes") == 2
        assert scalar(conn, "SELECT COUNT(*) FROM b3_arquivos_processados") == 2


def test_deduplicacao_e_por_investidor(investidor_a, investidor_b):
    """A date already loaded for one investidor must not block another."""
    df = _relatorio(_DIVIDENDO)
    with connect() as conn:
        assert carregar_arquivo(conn, investidor_a, df, "mov.xlsx") == (1, 0)
        assert carregar_arquivo(conn, investidor_b, df, "mov.xlsx") == (1, 0)


def test_bonificacao_gera_placeholder_de_custo(investidor_a):
    with connect() as conn:
        carregar_arquivo(conn, investidor_a, _relatorio(_BONIFICACAO), "mov.xlsx")
        rows = fetch_all(conn, "SELECT custo_por_cota FROM bonificacoes")

    assert [r["custo_por_cota"] for r in rows] == [None]


def test_celulas_traco_viram_null(investidor_a):
    with connect() as conn:
        carregar_arquivo(conn, investidor_a, _relatorio(_BONIFICACAO), "mov.xlsx")
        row = fetch_all(conn, "SELECT quantidade, preco_unitario, valor FROM b3_movimentacoes")[0]

    assert (row["quantidade"], row["preco_unitario"], row["valor"]) == (10, None, None)
