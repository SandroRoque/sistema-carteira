"""Smoke tests: every report query runs on PostgreSQL and respects tenancy."""

from datetime import date

import pandas as pd
import pytest

import fechamento
import imposto
import reconcilia
import relatorio
from carrega_b3 import carregar_arquivo
from conftest import CPF_A
from database import connect_sistema
from fabricas import documento, negociacao
from loader import carregar
from test_carrega_b3 import _COLUNAS


@pytest.fixture
def carteira(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("PETR4", "entrada", 100, 10.0, date(2025, 1, 10), linha=1),
            negociacao("PETR4", "saida", 50, 15.0, date(2025, 6, 10), linha=2),
        ), "1001.pdf")
        carregar_arquivo(conn, investidor_a, pd.DataFrame([
            ("Credito", "15/03/2025", "Dividendo", "PETR4 - PETROBRAS", "NU", 100, 1.5, 150.0),
            ("Credito", "16/04/2025", "Juros Sobre Capital Próprio", "PETR4 - PETROBRAS", "NU", 100, 0.5, 50.0),
            ("Credito", "20/05/2025", "Bonificação em Ativos", "PETR4 - PETROBRAS", "NU", 10, "-", "-"),
        ], columns=_COLUNAS), "mov.xlsx")
    return investidor_a


def test_imposto(carteira, investidor_b):
    with connect_sistema() as conn:
        vendas = imposto._buscar_vendas(conn, carteira, ano=2025)
        assert len(vendas) == 1
        assert vendas[0]["ganho"] == pytest.approx(50 * 15.0 - 50 * 10.0)
        assert imposto._buscar_vendas(conn, carteira, ano=2024) == []
        assert len(imposto._buscar_vendas(conn, carteira)) == 1
        assert imposto._buscar_vendas(conn, investidor_b) == []

        mensais = imposto._buscar_vendas_mensais(conn, carteira, ano=2025)
        assert [(m["mes"], m["tipo"]) for m in mensais] == [("2025-06", "acao")]

        anuais = imposto._buscar_rendimentos_anuais(conn, carteira)
        assert anuais == [{"ano": "2025", "dividendo": 150.0, "jcp": 50.0}]

        detalhe = imposto._buscar_rendimentos_detalhe(conn, carteira, 2025)
        assert {d["categoria"] for d in detalhe} == {"dividendo", "jcp"}


def test_fechamento(carteira, investidor_b):
    with connect_sistema() as conn:
        pos = fechamento.calcular_posicao_em(conn, carteira, date(2025, 12, 31))
        assert [p["qty"] for p in pos.values()] == [60]  # 100 - 50 + 10 bonificadas

        antes = fechamento.calcular_posicao_em(conn, carteira, date(2025, 5, 19))
        assert [p["qty"] for p in antes.values()] == [100]

        atividade = fechamento.calcular_atividade_ano(
            conn, carteira, date(2025, 1, 1), date(2025, 12, 31)
        )
        assert list(atividade.values())[0]["qty_vendas"] == 50

        assert fechamento.calcular_posicao_em(conn, investidor_b, date(2025, 12, 31)) == {}


def test_relatorio_secoes(carteira):
    with connect_sistema() as conn:
        assert "2025-03" in relatorio._section_historico(conn, carteira)
        assert "PETR4" in relatorio._section_ir(conn, carteira)


def test_reconcilia(carteira, investidor_b):
    with connect_sistema() as conn:
        assert len(reconcilia._check_bonif_sem_custo(conn, carteira)) == 1
        assert reconcilia._check_bonif_sem_custo(conn, investidor_b) == []
        assert len(reconcilia._check_nao_revisados(conn, carteira)) == 1
        assert reconcilia._check_sem_negociacoes(conn, carteira) == []
