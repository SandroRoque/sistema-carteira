"""Pending items: detection, inline fixes and their effect on the numbers."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

import pendencias
from carrega_b3 import carregar_arquivo
from conftest import CPF_A
from database import connect_sistema, scalar
from fabricas import documento, negociacao
from loader import carregar
from posicoes import calcular_posicoes
from test_app import _cliente, _login
from test_carrega_b3 import _COLUNAS

D = Decimal


def _b3(investidor_id, *linhas):
    with connect_sistema() as conn:
        carregar_arquivo(conn, investidor_id, pd.DataFrame(list(linhas), columns=_COLUNAS), "mov.xlsx")


def _posicao(investidor_id, ticker):
    with connect_sistema() as conn:
        return next(p for p in calcular_posicoes(conn, investidor_id) if p["ticker"] == ticker)


def _listar(investidor_id):
    with connect_sistema() as conn:
        return pendencias.listar(conn, investidor_id)


@pytest.fixture
def com_bonificacao(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("ITSA4", "entrada", 100, 10.0, date(2025, 1, 10))), "1.pdf")
    _b3(investidor_a, ("Credito", "22/12/2025", "Bonificação em Ativos", "ITSA4 - ITAUSA", "NU", 10, "-", "-"))
    return investidor_a


@pytest.fixture
def com_transferencia(usuario_id, investidor_a):
    _b3(investidor_a, ("Credito", "14/02/2024", "Transferência", "BBSE3 - BB SEGURIDADE", "NU", 50, "-", "-"))
    return investidor_a


@pytest.fixture
def cliente(investidor_a):
    c = _cliente()
    c.csrf = _login(c)
    return c


def test_detecta_bonificacao_sem_custo(com_bonificacao):
    [p] = _listar(com_bonificacao)
    assert (p.tipo, p.rotulo, p.data, p.qtd) == ("bonificacao", "ITSA4", date(2025, 12, 22), 10)


def test_informar_custo_da_bonificacao_recalcula_preco_medio(com_bonificacao, cliente):
    assert _posicao(com_bonificacao, "ITSA4")["preco_medio"] == D(1000) / 110  # bonus at zero cost
    [p] = _listar(com_bonificacao)

    resp = cliente.post(f"/pendencias/bonificacoes/{p.chave}",
                        data={"csrf_token": cliente.csrf, "custo_por_cota": "18,04"})

    assert resp.status_code == 200 and "Salvo." in resp.text
    pos = _posicao(com_bonificacao, "ITSA4")
    assert pos["custo_total"] == D("1180.40")
    assert not pos["tem_bonif_sem_custo"]
    assert _listar(com_bonificacao) == []


def test_detecta_e_resolve_posicao_sem_custo(com_transferencia, cliente):
    [p] = _listar(com_transferencia)
    assert (p.tipo, p.rotulo, p.qtd, p.data) == ("sem_custo", "BBSE3", 50, date(2024, 2, 14))

    cliente.post(f"/pendencias/custos/{p.chave}", data={"csrf_token": cliente.csrf, "custo_por_cota": "30,00"})

    pos = _posicao(com_transferencia, "BBSE3")
    assert (pos["custo_sem_origem"], pos["custo_total"], pos["preco_medio"]) == (False, D("1500.00"), D("30.00"))
    assert _listar(com_transferencia) == []


def test_custo_informado_pode_ser_corrigido(com_transferencia, cliente):
    [p] = _listar(com_transferencia)
    for valor in ("30,00", "32,50"):
        cliente.post(f"/pendencias/custos/{p.chave}", data={"csrf_token": cliente.csrf, "custo_por_cota": valor})

    assert _posicao(com_transferencia, "BBSE3")["preco_medio"] == D("32.50")


def test_valor_invalido_mostra_erro_no_item(com_bonificacao, cliente):
    [p] = _listar(com_bonificacao)

    resp = cliente.post(f"/pendencias/bonificacoes/{p.chave}",
                        data={"csrf_token": cliente.csrf, "custo_por_cota": "abc"})

    assert resp.status_code == 422
    assert "Informe um valor" in resp.text
    assert len(_listar(com_bonificacao)) == 1


def test_nao_resolve_pendencia_de_outra_conta(com_bonificacao, investidor_b):
    [p] = _listar(com_bonificacao)
    bruno = _cliente()
    bruno.csrf = _login(bruno, "bruno@example.com")

    resp = bruno.post(f"/pendencias/bonificacoes/{p.chave}",
                      data={"csrf_token": bruno.csrf, "custo_por_cota": "1,00"})

    assert resp.status_code == 422
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT custo_por_cota FROM bonificacoes") is None


def test_detecta_venda_maior_que_compra(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A, negociacao("VALE3", "saida", 30, 60.0)), "1.pdf")

    [p] = _listar(investidor_a)
    assert (p.tipo, p.rotulo, p.qtd) == ("vendido_mais", "VALE3", 30)


def test_aviso_de_extrato_antigo(com_bonificacao):
    with connect_sistema() as conn:
        [a] = pendencias.avisos(conn, com_bonificacao, hoje=date(2026, 10, 6))
        assert pendencias.avisos(conn, com_bonificacao, hoje=date(2026, 1, 15)) == []
    assert (a.tipo, a.data) == ("extrato_antigo", date(2025, 12, 22))


def test_contador_no_cabecalho(com_bonificacao, cliente):
    assert "1 pendência" in cliente.get("/").text
    assert cliente.get("/pendencias").status_code == 200
