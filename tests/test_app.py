from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app, investidor_atual
from conftest import CPF_A
from database import connect, scalar
from fabricas import documento, negociacao
from loader import carregar


@pytest.fixture
def client(investidor_a):
    app.dependency_overrides[investidor_atual] = lambda: investidor_a
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ativo_id(usuario_id):
    with connect() as conn:
        carregar(conn, usuario_id, documento(CPF_A, negociacao("PETR4")), "1001.pdf")
        return scalar(conn, "SELECT id FROM ativos WHERE ticker = 'PETR4'")


def _form(ativo_id, **kw):
    return {
        "ativo_id": ativo_id, "data": "2025-04-01", "sentido": "entrada",
        "quantidade": "10", "preco_unitario": "20", "taxas": "1",
    } | kw


def test_paginas_renderizam(client, ativo_id):
    for path in ("/", "/negociacoes", "/ativos"):
        resp = client.get(path)
        assert resp.status_code == 200, path
    assert "PETR4" in client.get("/").text


def test_cria_negociacao_manual(client, ativo_id, investidor_a):
    resp = client.post("/negociacoes", data=_form(ativo_id))

    assert resp.status_code == 200
    assert "R$ 201.00" in resp.text
    with connect() as conn:
        assert scalar(
            conn,
            "SELECT data FROM negociacoes WHERE corretora_id = 'manual' AND investidor_id = :i",
            i=investidor_a,
        ) == date(2025, 4, 1)


@pytest.mark.parametrize("campo, valor", [
    ("sentido", "talvez"),
    ("quantidade", "0"),
    ("quantidade", "-5"),
    ("preco_unitario", "-1"),
    ("data", "2025-13-45"),
])
def test_rejeita_negociacao_invalida(client, ativo_id, campo, valor):
    resp = client.post("/negociacoes", data=_form(ativo_id, **{campo: valor}))
    assert resp.status_code == 422


def test_nao_apaga_negociacao_de_outro_investidor(client, ativo_id, investidor_b):
    with connect() as conn:
        alheia = scalar(conn, "SELECT id FROM negociacoes")  # belongs to investidor_a

    app.dependency_overrides[investidor_atual] = lambda: investidor_b
    resp = client.delete(f"/negociacoes/{alheia}")

    assert resp.status_code == 404
    with connect() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes WHERE id = :id", id=alheia) == 1


def test_apaga_propria_negociacao(client, ativo_id):
    with connect() as conn:
        propria = scalar(conn, "SELECT id FROM negociacoes")

    assert client.delete(f"/negociacoes/{propria}").status_code == 200
    with connect() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes") == 0


def test_ativo_update_valida_tipo(client, ativo_id):
    resp = client.patch(f"/ativos/{ativo_id}", data={"tipo": "cripto"})
    assert resp.status_code == 422

    resp = client.patch(f"/ativos/{ativo_id}", data={"tipo": "acao", "revisado": "1"})
    assert resp.status_code == 200
