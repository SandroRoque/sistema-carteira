"""E-mail confirmation at sign-up and the new-password link."""

import logging
import re

import pytest
from fastapi.testclient import TestClient

import auth
from app.main import _SemQueryString, app
from conftest import SENHA
from database import connect_sistema, execute, scalar

_BASE = "https://testserver"
NOVA_SENHA = "outra-senha-de-teste-456"


def _cliente() -> TestClient:
    return TestClient(app, base_url=_BASE, headers={"Origin": _BASE})


def _csrf(resp) -> str:
    return re.search(r'"X-CSRF-Token": "([^"]+)"', resp.text).group(1)


def _link(caixa, indice=-1) -> str:
    """Path and query of the link in a captured e-mail."""
    texto = caixa[indice][2]
    return re.search(r"https://carteira\.example\.com(\S+)", texto).group(1)


def _cadastrar(c: TestClient, email: str = "nova@example.com"):
    return c.post("/cadastrar", data={
        "email": email, "senha": SENHA, "senha_confirmacao": SENHA, "aceite": "true",
    })


def _vencer_tokens(conn):
    execute(conn, "UPDATE tokens_email SET expira_em = now() - interval '1 second', criado_em = now() - interval '1 day'")


# ---------------------------------------------------------------------------
# Sign-up and confirmation
# ---------------------------------------------------------------------------

def test_conta_nova_so_entra_depois_de_confirmar(db, caixa_de_saida):
    c = _cliente()
    _cadastrar(c)
    para, assunto, _ = caixa_de_saida[0]
    assert para == "nova@example.com" and "Confirme" in assunto

    for caminho in ("/", "/importar", "/posicoes"):
        resp = c.get(caminho, follow_redirects=False)
        assert resp.headers["location"] == "/email/pendente", caminho
    # Nothing can be imported or created before confirming.
    csrf = _csrf(c.get("/email/pendente"))
    resp = c.post("/carteiras", data={"csrf_token": csrf, "cpf": "12345678909"}, follow_redirects=False)
    assert resp.headers["location"] == "/email/pendente"

    # The link works in any browser, without a session.
    resp = _cliente().get(_link(caixa_de_saida))
    assert resp.status_code == 200
    assert "E-mail confirmado" in resp.text
    assert c.get("/", follow_redirects=False).headers["location"] != "/email/pendente"


def test_sem_confirmar_ainda_exporta_e_exclui_a_conta(db, caixa_de_saida):
    c = _cliente()
    csrf = _csrf(_cadastrar(c))
    assert c.get("/conta").status_code == 200
    assert c.get("/conta/exportar").status_code == 200
    resp = c.post("/conta/excluir", data={"csrf_token": csrf, "senha": SENHA}, follow_redirects=False)
    assert resp.status_code == 303
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT count(*) FROM usuarios") == 0


def test_link_de_confirmacao_vale_uma_vez(db, caixa_de_saida):
    _cadastrar(_cliente())
    link = _link(caixa_de_saida)
    assert _cliente().get(link).status_code == 200
    assert _cliente().get(link).status_code == 400


def test_link_de_confirmacao_vencido(db, caixa_de_saida):
    c = _cliente()
    _cadastrar(c)
    with connect_sistema() as conn:
        _vencer_tokens(conn)
    assert _cliente().get(_link(caixa_de_saida)).status_code == 400
    assert c.get("/", follow_redirects=False).headers["location"] == "/email/pendente"


def test_reenvio_espera_um_minuto_e_substitui_o_link(db, caixa_de_saida):
    c = _cliente()
    csrf = _csrf(_cadastrar(c))
    assert c.post("/email/reenviar", data={"csrf_token": csrf}).status_code == 429
    assert len(caixa_de_saida) == 1

    with connect_sistema() as conn:
        execute(conn, "UPDATE tokens_email SET criado_em = now() - interval '2 minutes'")
    assert c.post("/email/reenviar", data={"csrf_token": csrf}).status_code == 200
    assert len(caixa_de_saida) == 2
    assert _cliente().get(_link(caixa_de_saida, 0)).status_code == 400
    assert _cliente().get(_link(caixa_de_saida, 1)).status_code == 200


def test_sem_email_configurado_o_cadastro_fica_fechado(db, monkeypatch):
    monkeypatch.delenv("URL_PUBLICA", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    c = _cliente()
    assert _cadastrar(c).status_code == 403
    assert 'href="/cadastrar"' not in c.get("/entrar").text


def test_contas_do_administrador_e_existentes_ja_estao_confirmadas(usuario_id):
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT email_verificado_em IS NOT NULL FROM usuarios WHERE id = :id", id=usuario_id)


# ---------------------------------------------------------------------------
# New password
# ---------------------------------------------------------------------------

def _pedir(c: TestClient, email: str = "ana@example.com"):
    return c.post("/senha/esqueci", data={"email": email})


def _redefinir(c: TestClient, link: str, senha: str = NOVA_SENHA, confirmacao: str | None = None):
    token = link.split("token=", 1)[1]
    return c.post("/senha/redefinir", data={
        "token": token, "senha": senha, "senha_confirmacao": confirmacao or senha,
    })


def test_nova_senha_pelo_link(usuario_id, caixa_de_saida):
    antigo = _cliente()
    antigo.post("/entrar", data={"email": "ana@example.com", "senha": SENHA})
    assert antigo.get("/conta").status_code == 200

    c = _cliente()
    resp = _pedir(c, "ANA@example.com")
    assert resp.status_code == 200
    assert [para for para, _, _ in caixa_de_saida] == ["ana@example.com"]
    link = _link(caixa_de_saida)

    # Opening the link (or a mail scanner opening it) does not spend it.
    assert c.get(link).status_code == 200
    assert c.get(link).status_code == 200
    resp = _redefinir(c, link)
    assert resp.status_code == 200
    assert c.get("/conta").status_code == 200  # logged in

    # Other sessions ended; only the new password works; the link is spent.
    assert antigo.get("/conta", follow_redirects=False).headers["location"] == "/entrar"
    with connect_sistema() as conn:
        assert auth.autenticar(conn, "ana@example.com", SENHA) is None
        assert auth.autenticar(conn, "ana@example.com", NOVA_SENHA) == usuario_id
    assert _redefinir(_cliente(), link).status_code == 400
    assert c.get(link).status_code == 400


def test_pedido_responde_igual_para_email_desconhecido(usuario_id, caixa_de_saida):
    conhecido = _pedir(_cliente())
    desconhecido = _pedir(_cliente(), "ninguem@example.com")
    assert conhecido.status_code == desconhecido.status_code == 200
    assert conhecido.text == desconhecido.text
    assert len(caixa_de_saida) == 1


def test_senha_invalida_ou_diferente_mantem_o_link(usuario_id, caixa_de_saida):
    _pedir(_cliente())
    link = _link(caixa_de_saida)
    c = _cliente()
    assert _redefinir(c, link, "curta").status_code == 400
    assert _redefinir(c, link, NOVA_SENHA, "outra-coisa-qualquer").status_code == 400
    assert _redefinir(c, link).status_code == 200


def test_link_de_nova_senha_vencido(usuario_id, caixa_de_saida):
    _pedir(_cliente())
    with connect_sistema() as conn:
        _vencer_tokens(conn)
    assert _redefinir(_cliente(), _link(caixa_de_saida)).status_code == 400


def test_so_o_ultimo_link_vale(usuario_id, caixa_de_saida):
    _pedir(_cliente())
    _pedir(_cliente())  # within the minute: no second e-mail
    assert len(caixa_de_saida) == 1
    with connect_sistema() as conn:
        execute(conn, "UPDATE tokens_email SET criado_em = now() - interval '2 minutes'")
    _pedir(_cliente())
    assert len(caixa_de_saida) == 2
    assert _redefinir(_cliente(), _link(caixa_de_saida, 0)).status_code == 400
    assert _redefinir(_cliente(), _link(caixa_de_saida, 1)).status_code == 200


def test_nova_senha_libera_conta_em_espera_e_confirma_email(db, caixa_de_saida):
    c = _cliente()
    _cadastrar(c)
    with connect_sistema() as conn:
        execute(conn, "UPDATE usuarios SET falhas_login = 9, bloqueado_ate = now() + interval '10 minutes'")
        execute(conn, "UPDATE tokens_email SET criado_em = now() - interval '2 minutes'")
    _pedir(_cliente(), "nova@example.com")
    novo = _cliente()
    assert _redefinir(novo, _link(caixa_de_saida)).status_code == 200
    assert novo.get("/", follow_redirects=False).headers.get("location") != "/email/pendente"
    with connect_sistema() as conn:
        assert auth.autenticar(conn, "nova@example.com", NOVA_SENHA) is not None


def test_pedidos_contam_no_limite_por_ip(usuario_id, caixa_de_saida):
    for _ in range(auth.MAX_FALHAS_IP):
        _pedir(_cliente(), "ninguem@example.com")
    assert _pedir(_cliente()).status_code == 429
    assert caixa_de_saida == []


def test_demo_nao_recebe_nova_senha(db, caixa_de_saida):
    with connect_sistema() as conn:
        execute(conn, "INSERT INTO usuarios (email, demo) VALUES ('demo@example.com', true)")
    _pedir(_cliente(), "demo@example.com")
    assert caixa_de_saida == []


def test_sem_email_configurado_a_pagina_avisa(usuario_id, monkeypatch):
    monkeypatch.delenv("URL_PUBLICA", raising=False)
    resp = _pedir(_cliente())
    assert resp.status_code == 503
    assert "administrador" in _cliente().get("/senha/esqueci").text


def test_login_tem_link_para_esqueci_a_senha(db):
    assert 'href="/senha/esqueci"' in _cliente().get("/entrar").text


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def test_log_de_acesso_nao_guarda_o_token():
    registro = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:1234", "GET", "/senha/redefinir?token=segredo", "1.1", 200), None,
    )
    assert _SemQueryString().filter(registro)
    assert "segredo" not in registro.getMessage()
    assert "/senha/redefinir" in registro.getMessage()


@pytest.mark.parametrize("porta, ssl_direto", [(465, True), (587, False)])
def test_envio_por_smtp(monkeypatch, porta, ssl_direto):
    import smtplib

    import correio

    chamadas = []

    class Servidor:
        def __init__(self, host, port, **kw):
            chamadas.append(("conectar", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, **kw):
            chamadas.append(("starttls",))

        def login(self, usuario, senha):
            chamadas.append(("login", usuario))

        def send_message(self, msg):
            chamadas.append(("enviar", msg["To"], msg["From"], msg["Subject"]))

    monkeypatch.setattr(smtplib, "SMTP", Servidor)
    monkeypatch.setattr(smtplib, "SMTP_SSL", Servidor)
    monkeypatch.delenv("EMAIL_NO_TERMINAL", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORTA", str(porta))
    monkeypatch.setenv("SMTP_USUARIO", "usuario")
    monkeypatch.setenv("SMTP_SENHA", "senha")
    monkeypatch.setenv("EMAIL_REMETENTE", "Carteira <nao-responda@example.com>")
    correio.enviar("ana@example.com", "Assunto", "Texto")
    esperado = [("conectar", "smtp.example.com", porta)]
    if not ssl_direto:
        esperado.append(("starttls",))
    esperado += [
        ("login", "usuario"),
        ("enviar", "ana@example.com", "Carteira <nao-responda@example.com>", "Assunto"),
    ]
    assert chamadas == esperado


def test_falha_no_envio_so_registra_o_tipo(monkeypatch, caplog):
    import smtplib

    import correio

    def recusa(*a, **kw):
        raise smtplib.SMTPConnectError(421, "token=segredo")

    monkeypatch.setattr(smtplib, "SMTP", recusa)
    monkeypatch.delenv("EMAIL_NO_TERMINAL", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_REMETENTE", "nao-responda@example.com")
    correio.enviar("ana@example.com", "Assunto", "Texto com token=segredo")
    assert "SMTPConnectError" in caplog.text
    assert "segredo" not in caplog.text
