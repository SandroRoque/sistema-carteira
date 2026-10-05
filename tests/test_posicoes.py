from datetime import date

import pytest

from conftest import CPF_A
from database import connect
from fabricas import documento, negociacao
from loader import carregar
from posicoes import calcular_posicoes


def _carregar(usuario_id, *negs, nota_id="1001"):
    with connect() as conn:
        carregar(conn, usuario_id, documento(CPF_A, *negs, nota_id=nota_id), f"{nota_id}.pdf")


def _posicao(investidor_id, ticker):
    with connect() as conn:
        posicoes = calcular_posicoes(conn, investidor_id)
    return next(p for p in posicoes if p["ticker"] == ticker)


def test_compra_simples(usuario_id, investidor_a):
    _carregar(usuario_id, negociacao("PETR4", quantidade=100, preco=10.0))

    pos = _posicao(investidor_a, "PETR4")

    assert pos["qty"] == 100
    assert pos["preco_medio"] == pytest.approx(10.0)
    assert pos["custo_total"] == pytest.approx(1000.0)
    assert pos["is_open"]


def test_posicoes_isoladas_por_investidor(usuario_id, investidor_a, investidor_b):
    _carregar(usuario_id, negociacao("PETR4", quantidade=100, preco=10.0))

    with connect() as conn:
        assert calcular_posicoes(conn, investidor_b) == []


@pytest.mark.xfail(
    strict=True,
    reason="Custo médio soma todas as compras sem respeitar a ordem cronológica "
           "das vendas; a correção é o próximo passo.",
)
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
