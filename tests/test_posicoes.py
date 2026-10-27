from datetime import date
from decimal import Decimal

import pytest

from conftest import CPF_A
from database import connect_sistema
from fabricas import documento, negociacao
from loader import carregar
from posicoes import calcular_posicoes


def _carregar(usuario_id, *negs, nota_id="1001"):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A, *negs, nota_id=nota_id), f"{nota_id}.pdf")


def _posicao(investidor_id, ticker):
    with connect_sistema() as conn:
        posicoes = calcular_posicoes(conn, investidor_id)
    return next(p for p in posicoes if p["ticker"] == ticker)


def test_compra_simples(usuario_id, investidor_a):
    _carregar(usuario_id, negociacao("PETR4", quantidade=100, preco=10.0))

    pos = _posicao(investidor_a, "PETR4")

    assert pos["qty"] == 100
    assert pos["preco_medio"] == 10
    assert pos["custo_total"] == 1000
    assert pos["is_open"]
    # Exact arithmetic end to end: NUMERIC is loaded as Decimal, never float.
    assert {type(pos[k]) for k in ("qty", "preco_medio", "custo_total")} == {Decimal}


def test_posicoes_isoladas_por_investidor(usuario_id, investidor_a, investidor_b):
    _carregar(usuario_id, negociacao("PETR4", quantidade=100, preco=10.0))

    with connect_sistema() as conn:
        assert calcular_posicoes(conn, investidor_b) == []


def test_custo_medio_reinicia_apos_zerar_posicao(usuario_id, investidor_a):
    _carregar(
        usuario_id,
        negociacao("PETR4", "entrada", 100, 10.0, date(2025, 1, 10), linha=1),
        negociacao("PETR4", "saida", 100, 12.0, date(2025, 2, 10), linha=2),
        negociacao("PETR4", "entrada", 100, 20.0, date(2025, 3, 10), linha=3),
    )

    pos = _posicao(investidor_a, "PETR4")

    assert pos["qty"] == 100
    assert pos["preco_medio"] == pytest.approx(20.0)
    assert pos["custo_total"] == pytest.approx(2000.0)


def test_direito_nao_exercido_fecha_mesmo_com_quantidade_zero(investidor_a):
    import pandas as pd

    from carrega_b3 import carregar_arquivo
    from test_carrega_b3 import _COLUNAS

    linhas = [
        ("Credito", "12/02/2025", "Direito de Subscrição", "WXYZ1 - WXYZ S/A", "NU", 7, "-", "-"),
        # Procedural pair the day before expiry: nets to zero, no value.
        ("Credito", "10/03/2025", "Cessão de Direitos", "WXYZ1 - WXYZ S/A", "NU", 7, "-", "-"),
        ("Debito", "10/03/2025", "Cessão de Direitos - Solicitada", "WXYZ1 - WXYZ S/A", "NU", 7, "-", "-"),
        ("Debito", "11/03/2025", "Direitos de Subscrição - Não Exercido", "WXYZ1 - WXYZ S/A", "NU", 0, "-", "-"),
    ]
    with connect_sistema() as conn:
        carregar_arquivo(conn, investidor_a, pd.DataFrame(linhas, columns=_COLUNAS), "mov.xlsx")
        [p] = [p for p in calcular_posicoes(conn, investidor_a) if p["ticker"] == "WXYZ1"]
    assert (p["qty"], p["is_open"]) == (0, False)
