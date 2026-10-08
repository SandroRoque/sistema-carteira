"""Option trades: stored under their own ativos, left out of positions and taxes."""

from datetime import date

import apuracao
import painel
from conftest import CPF_A
from database import connect, connect_sistema, fetch_all
from fabricas import documento, negociacao
from loader import carregar
from posicoes import calcular_posicoes
from test_app import _cliente, _login


def _opcao(raw, sentido, prazo, **kw):
    return negociacao(raw, sentido=sentido, tipo_de_mercado="OPCAO DE COMPRA", prazo=prazo, **kw)


def _carregar(usuario_id, *docs):
    with connect_sistema() as conn:
        for doc in docs:
            carregar(conn, usuario_id, doc, "nota.pdf")


def test_opcao_vira_ativo_proprio_sem_ticker(usuario_id):
    _carregar(usuario_id, documento(
        CPF_A,
        _opcao("ABCDK35 PN ABCD", "saida", "11/25", quantidade=100, preco=0.5),
        _opcao("ABCDK35 PN ABCD", "entrada", "11/25", quantidade=100, preco=0.2, linha=2),
        # The same code reused by B3 for another expiry is another option.
        _opcao("ABCDK35 PN ABCD", "entrada", "11/26", quantidade=100, preco=0.3, linha=3),
        # Some notas omit the code: underlying, class and strike only.
        _opcao("ABCD PN 35,00", "entrada", "02/26", quantidade=100, preco=0.1, linha=4),
    ))
    with connect_sistema() as conn:
        ativos = fetch_all(conn, "SELECT tipo, ticker, nome FROM ativos ORDER BY id")
    assert [(a["tipo"], a["ticker"]) for a in ativos] == [("opcao", None)] * 3
    assert [a["nome"] for a in ativos] == [
        "ABCDK35 PN ABCD · opção de compra · venc. 11/25",
        "ABCDK35 PN ABCD · opção de compra · venc. 11/26",
        "ABCD PN 35,00 · opção de compra · venc. 02/26",
    ]


def test_opcoes_ficam_fora_das_posicoes_e_do_imposto(usuario_id, investidor_a):
    _carregar(usuario_id, documento(
        CPF_A,
        negociacao("PETR4", quantidade=100, preco=30.0, data=date(2025, 3, 10)),
        # A profitable option round trip in the same month.
        _opcao("PETRC35 PN PETR", "saida", "03/25", quantidade=1000, preco=1.0, data=date(2025, 3, 10), linha=2),
        _opcao("PETRC35 PN PETR", "entrada", "03/25", quantidade=1000, preco=0.2, data=date(2025, 3, 14), linha=3),
    ))
    with connect(usuario_id) as conn:
        posicoes = [p["ticker"] or p["nome"] for p in calcular_posicoes(conn, investidor_a) if p["is_open"]]
        meses = apuracao.apuracao(conn, investidor_a)
        assert painel.negocios_com_opcoes(conn, investidor_a) == 2
    assert posicoes == ["PETR4"]
    assert all(m.darf == 0 for m in meses)


def test_paginas_avisam_que_opcoes_nao_sao_calculadas(usuario_id, investidor_a):
    _carregar(usuario_id, documento(
        CPF_A, _opcao("PETRC35 PN PETR", "entrada", "03/25", quantidade=100, preco=0.2)
    ))
    c = _cliente()
    _login(c)
    assert "1 negócio com opções não aparece nas posições" in c.get("/posicoes").text
    assert "1 negócio com opções não entra nesta apuração" in c.get("/impostos").text
