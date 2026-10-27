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


def test_custo_absurdo_e_recusado(com_bonificacao, cliente):
    [p] = _listar(com_bonificacao)

    resp = cliente.post(f"/pendencias/bonificacoes/{p.chave}",
                        data={"csrf_token": cliente.csrf, "custo_por_cota": "1E+99999"})

    assert resp.status_code == 422
    assert "grande demais" in resp.text
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


def test_parte_da_posicao_sem_custo_vira_pendencia_e_aceita_zero(usuario_id, investidor_a, cliente):
    _b3(investidor_a, ("Credito", "10/05/2023", "Atualização", "EFGH34 - EFGH INC", "NU", 1, "-", "-"))
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("EFGH34", "entrada", 7, 12.0, date(2025, 9, 15))), "1.pdf")

    [p] = _listar(investidor_a)
    assert (p.tipo, p.parcial, p.qtd, p.data) == ("sem_custo", True, 1, date(2023, 5, 10))

    cliente.post(f"/pendencias/custos/{p.chave}", data={"csrf_token": cliente.csrf, "custo_por_cota": "0"})

    assert _listar(investidor_a) == []
    assert _posicao(investidor_a, "EFGH34")["preco_medio"] == D("10.5")


@pytest.fixture
def com_incorporacao(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("AAAA11", "entrada", 3, 50.0, date(2023, 6, 20))), "1.pdf")
    _b3(investidor_a,
        ("Credito", "12/03/2024", "Atualização", "AAAA11 - FII ALFA", "NU", 3, "-", "-"),
        ("Credito", "12/03/2024", "Atualização", "BBBB11 - FII BETA", "NU", 12.34, "-", "-"),
        ("Credito", "18/03/2024", "Resgate", "AAAA11 - FII ALFA", "NU", 3, 2.00, 6.00))
    return investidor_a


def test_sugere_e_aplica_a_origem_de_uma_incorporacao(com_incorporacao, cliente):
    [p] = _listar(com_incorporacao)
    assert (p.tipo, p.rotulo) == ("sem_custo", "BBBB11")
    assert p.origens[0][1] == "AAAA11"
    assert p.origens[0][2] == ("a B3 também movimentou AAAA11 em 12/03/2024; "
                               "AAAA11 saiu da carteira em 18/03/2024")

    pagina = cliente.get("/pendencias").text
    assert "Veio de outro ativo" in pagina and "AAAA11 (provável)" in pagina
    assert "Por que AAAA11:" in pagina

    resp = cliente.post(f"/pendencias/conversoes/{p.credito_id}",
                        data={"csrf_token": cliente.csrf, "ativo_origem_id": p.origens[0][0]})
    assert resp.status_code == 200 and "Salvo." in resp.text
    pos = _posicao(com_incorporacao, "BBBB11")
    assert (pos["custo_sem_origem"], pos["custo_total"]) == (False, D("150.00"))
    assert _listar(com_incorporacao) == []
    historico = cliente.get(f"/posicoes/{p.ativo_id}").text
    assert "De AAAA11, com o custo dele" in historico

    cliente.post(f"/pendencias/conversoes/{p.credito_id}/desfazer", data={"csrf_token": cliente.csrf})
    assert [x.rotulo for x in _listar(com_incorporacao)] == ["BBBB11"]


def test_origem_precisa_estar_na_carteira_na_data(com_incorporacao, cliente, usuario_id):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("PETR4", "entrada", 10, 30.0, date(2025, 1, 10)), nota_id="2"), "2.pdf")
        petr = scalar(conn, "SELECT id FROM ativos WHERE ticker = 'PETR4'")
    [p] = _listar(com_incorporacao)

    resp = cliente.post(f"/pendencias/conversoes/{p.credito_id}",
                        data={"csrf_token": cliente.csrf, "ativo_origem_id": petr})
    assert resp.status_code == 422 and "não tinha esse ativo" in resp.text


def test_nao_converte_credito_de_outra_conta(com_incorporacao, investidor_b):
    [p] = _listar(com_incorporacao)
    bruno = _cliente()
    bruno.csrf = _login(bruno, "bruno@example.com")

    resp = bruno.post(f"/pendencias/conversoes/{p.credito_id}",
                      data={"csrf_token": bruno.csrf, "ativo_origem_id": p.origens[0][0]})
    assert resp.status_code == 422
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM conversoes") == 0


def test_custo_zero_por_botao(com_transferencia, cliente):
    [p] = _listar(com_transferencia)
    assert "Recebi de graça" in cliente.get("/pendencias").text

    resp = cliente.post(f"/pendencias/custos/{p.chave}", data={"csrf_token": cliente.csrf, "custo_por_cota": "0"})

    assert resp.status_code == 200 and "Salvo." in resp.text
    pos = _posicao(com_transferencia, "BBSE3")
    assert (pos["custo_sem_origem"], pos["custo_total"]) == (False, 0)
    assert _listar(com_transferencia) == []
