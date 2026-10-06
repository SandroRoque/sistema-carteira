import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import CPF_A, CPF_B, SENHA
from database import connect_sistema, execute, scalar
from fabricas import documento, negociacao
from loader import carregar

_ORIGEM = {"Origin": "http://testserver"}


def _cliente() -> TestClient:
    return TestClient(app, headers=_ORIGEM)


def _login(client: TestClient, email: str = "ana@example.com", senha: str = SENHA) -> str:
    """Log in and return the session's CSRF token."""
    resp = client.post("/entrar", data={"email": email, "senha": senha})
    assert resp.status_code == 200, resp.text
    return re.search(r'"X-CSRF-Token": "([^"]+)"', resp.text).group(1)


@pytest.fixture
def ativo_id(usuario_id):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A, negociacao("PETR4")), "1001.pdf")
        return scalar(conn, "SELECT id FROM ativos WHERE ticker = 'PETR4'")


@pytest.fixture
def client(ativo_id):
    """Logged in as ana, who owns one portfolio with one PETR4 trade."""
    c = _cliente()
    c.csrf = _login(c)
    return c


def _form(ativo_id, **kw):
    return {
        "ativo_id": ativo_id, "data": "2025-04-01", "sentido": "entrada",
        "quantidade": "10", "preco_unitario": "20", "taxas": "1",
    } | kw


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/negociacoes", "/ativos", "/conta", "/conta/exportar"])
def test_paginas_exigem_login(db, path):
    resp = _cliente().get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/entrar"


def test_htmx_sem_login_recebe_hx_redirect(db):
    resp = _cliente().get("/negociacoes", headers={"HX-Request": "true"})
    assert resp.status_code == 204
    assert resp.headers["HX-Redirect"] == "/entrar"


def test_cadastro_entra_e_mostra_sem_carteira(db):
    c = _cliente()
    resp = c.post("/cadastrar", data={
        "email": "nova@example.com", "senha": SENHA, "senha_confirmacao": SENHA, "aceite": "true",
    })
    assert resp.status_code == 200
    assert "Nenhuma carteira ainda" in resp.text


def test_cadastro_exige_aceite(db):
    resp = _cliente().post("/cadastrar", data={
        "email": "nova@example.com", "senha": SENHA, "senha_confirmacao": SENHA,
    })
    assert resp.status_code == 400


def test_login_errado_nao_revela_se_email_existe(usuario_id):
    c = _cliente()
    existe = c.post("/entrar", data={"email": "ana@example.com", "senha": "errada-errada"})
    nao_existe = c.post("/entrar", data={"email": "zz@example.com", "senha": "errada-errada"})
    assert existe.status_code == nao_existe.status_code == 400
    assert existe.text.replace("ana@example.com", "") == nao_existe.text.replace("zz@example.com", "")


def test_cookie_de_sessao_protegido(usuario_id):
    resp = _cliente().post("/entrar", data={"email": "ana@example.com", "senha": SENHA},
                           follow_redirects=False)
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_sair_encerra_sessao(client):
    client.post("/sair", data={"csrf_token": client.csrf})
    assert client.get("/", follow_redirects=False).status_code == 303
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM sessoes") == 0


# ---------------------------------------------------------------------------
# CSRF and origin
# ---------------------------------------------------------------------------

def test_escrita_sem_csrf_e_recusada(client, ativo_id):
    resp = client.post("/negociacoes", data=_form(ativo_id))
    assert resp.status_code == 403


def test_escrita_de_outra_origem_e_recusada(client, ativo_id):
    resp = client.post(
        "/negociacoes",
        data=_form(ativo_id),
        headers={"X-CSRF-Token": client.csrf, "Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


def test_headers_de_seguranca(client):
    resp = client.get("/")
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "DENY"


# ---------------------------------------------------------------------------
# Portfolio pages
# ---------------------------------------------------------------------------

def test_paginas_renderizam(client):
    for path in ("/", "/negociacoes", "/ativos", "/conta", "/privacidade"):
        assert client.get(path).status_code == 200, path
    assert "PETR4" in client.get("/").text


def test_cria_negociacao_manual(client, ativo_id, investidor_a):
    resp = client.post("/negociacoes", data=_form(ativo_id), headers={"X-CSRF-Token": client.csrf})

    assert resp.status_code == 200
    assert "R$ 201.00" in resp.text
    with connect_sistema() as conn:
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
    resp = client.post(
        "/negociacoes", data=_form(ativo_id, **{campo: valor}), headers={"X-CSRF-Token": client.csrf}
    )
    assert resp.status_code == 422


def test_apaga_propria_negociacao(client):
    with connect_sistema() as conn:
        propria = scalar(conn, "SELECT id FROM negociacoes")

    resp = client.delete(f"/negociacoes/{propria}", headers={"X-CSRF-Token": client.csrf})

    assert resp.status_code == 200
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes") == 0


def test_nao_ve_nem_apaga_dados_de_outra_conta(client, investidor_b):
    with connect_sistema() as conn:
        bruno = scalar(conn, "SELECT usuario_id FROM investidores WHERE id = :id", id=investidor_b)
        carregar(conn, bruno, documento(CPF_B, negociacao("VALE3"), nota_id="9"), "b.pdf")
        alheia = scalar(conn, "SELECT id FROM negociacoes WHERE investidor_id = :i", i=investidor_b)

    # VALE3 itself is in the shared catalog (the "add trade" dropdown); the
    # trade row must not be.
    assert f'id="neg-{alheia}"' not in client.get("/negociacoes").text
    assert "VALE3" not in client.get("/").text
    resp = client.delete(f"/negociacoes/{alheia}", headers={"X-CSRF-Token": client.csrf})
    assert resp.status_code == 404
    resp = client.post(f"/carteiras/{investidor_b}/selecionar", data={"csrf_token": client.csrf})
    assert resp.status_code == 404
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes WHERE id = :id", id=alheia) == 1


def test_troca_de_carteira(client, usuario_id):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_B, negociacao("VALE3"), nota_id="9"), "c.pdf")
        segunda = scalar(conn, "SELECT MAX(id) FROM investidores")

    assert "VALE3" not in client.get("/").text
    client.post(f"/carteiras/{segunda}/selecionar", data={"csrf_token": client.csrf})
    pagina = client.get("/").text
    assert "VALE3" in pagina and "PETR4" not in pagina


# ---------------------------------------------------------------------------
# Catalog administration
# ---------------------------------------------------------------------------

def test_catalogo_so_admin_edita(client, ativo_id, usuario_id):
    headers = {"X-CSRF-Token": client.csrf}
    assert "Editar" not in client.get("/ativos").text
    assert client.get(f"/ativos/{ativo_id}/edit").status_code == 403
    assert client.patch(f"/ativos/{ativo_id}", data={"tipo": "fii"}, headers=headers).status_code == 403

    with connect_sistema() as conn:
        execute(conn, "UPDATE usuarios SET e_admin = true WHERE id = :id", id=usuario_id)

    assert "Editar" in client.get("/ativos").text
    assert client.patch(f"/ativos/{ativo_id}", data={"tipo": "cripto"}, headers=headers).status_code == 422
    resp = client.patch(f"/ativos/{ativo_id}", data={"tipo": "acao", "revisado": "1"}, headers=headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# LGPD: export and deletion
# ---------------------------------------------------------------------------

def test_exporta_somente_os_proprios_dados(client, investidor_b):
    with connect_sistema() as conn:
        bruno = scalar(conn, "SELECT usuario_id FROM investidores WHERE id = :id", id=investidor_b)
        carregar(conn, bruno, documento(CPF_B, negociacao("VALE3"), nota_id="9"), "b.pdf")

    resp = client.get("/conta/exportar")

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    dados = json.loads(resp.text)
    assert dados["conta"]["email"] == "ana@example.com"
    assert [c["cpf_mascarado"] for c in dados["carteiras"]] == ["***.456.789-**"]
    assert len(dados["negociacoes"]) == 1
    assert "senha_hash" not in resp.text


def test_exclusao_exige_senha_e_apaga_tudo(client, usuario_id, investidor_b):
    resp = client.post("/conta/excluir", data={"csrf_token": client.csrf, "senha": "errada-errada"})
    assert resp.status_code == 400

    resp = client.post("/conta/excluir", data={"csrf_token": client.csrf, "senha": SENHA})
    assert resp.status_code == 200
    assert "foram excluídos" in resp.text

    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM usuarios WHERE id = :u", u=usuario_id) == 0
        assert scalar(conn, "SELECT COUNT(*) FROM sessoes") == 0
        # Ana's only nota and trade went with her portfolio (bruno has none here).
        assert scalar(conn, "SELECT COUNT(*) FROM notas") == 0
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes") == 0
        # The other account is untouched.
        assert scalar(conn, "SELECT id FROM investidores") == investidor_b
