from datetime import datetime, timedelta, timezone

import pytest

import auth
from conftest import SENHA
from database import connect_sistema, execute, scalar


def test_cria_e_autentica(db):
    with connect_sistema() as conn:
        usuario_id = auth.criar_usuario(conn, "Ana@Example.com", SENHA)
        assert auth.autenticar(conn, "ana@example.com", SENHA) == usuario_id
        assert auth.autenticar(conn, "ana@example.com", "senha-errada-123") is None
        assert auth.autenticar(conn, "ninguem@example.com", SENHA) is None


def test_senha_guardada_so_como_hash_argon2(usuario_id):
    with connect_sistema() as conn:
        guardado = scalar(conn, "SELECT senha_hash FROM usuarios WHERE id = :id", id=usuario_id)
    assert guardado.startswith("$argon2id$")
    assert SENHA not in guardado


@pytest.mark.parametrize("email, senha", [
    ("nao-e-email", SENHA),
    ("ana@example.com", "curta"),
    ("ana@example.com", "x" * 300),
])
def test_cadastro_invalido(db, email, senha):
    with connect_sistema() as conn, pytest.raises(auth.ErroCadastro):
        auth.criar_usuario(conn, email, senha)


def test_email_duplicado_ignora_maiusculas(usuario_id):
    with connect_sistema() as conn, pytest.raises(auth.ErroCadastro):
        auth.criar_usuario(conn, "ANA@example.com", SENHA)


def test_bloqueia_apos_falhas_seguidas(usuario_id):
    with connect_sistema() as conn:
        for _ in range(auth.MAX_FALHAS_LOGIN):
            assert auth.autenticar(conn, "ana@example.com", "senha-errada-123") is None
        # Locked: even the right password is refused.
        assert auth.autenticar(conn, "ana@example.com", SENHA) is None

        # Once the lock expires, the right password works and the counter resets.
        execute(
            conn,
            "UPDATE usuarios SET bloqueado_ate = :t WHERE id = :id",
            t=datetime.now(timezone.utc) - timedelta(seconds=1),
            id=usuario_id,
        )
        assert auth.autenticar(conn, "ana@example.com", SENHA) == usuario_id
        assert scalar(conn, "SELECT falhas_login FROM usuarios WHERE id = :id", id=usuario_id) == 0


def test_sessao_guarda_so_hash_do_token(usuario_id):
    with connect_sistema() as conn:
        token = auth.criar_sessao(conn, usuario_id)
        assert scalar(conn, "SELECT COUNT(*) FROM sessoes WHERE token_hash = :t", t=token) == 0
        sessao = auth.obter_sessao(conn, token)
    assert sessao.usuario_id == usuario_id
    assert sessao.email == "ana@example.com"


def test_sessao_expirada_ou_encerrada_nao_vale(usuario_id):
    with connect_sistema() as conn:
        token = auth.criar_sessao(conn, usuario_id)
        execute(conn, "UPDATE sessoes SET expira_em = now() - interval '1 second'")
        assert auth.obter_sessao(conn, token) is None

        token = auth.criar_sessao(conn, usuario_id)
        auth.encerrar_sessao(conn, token)
        assert auth.obter_sessao(conn, token) is None
        assert auth.obter_sessao(conn, None) is None


def test_troca_de_senha_encerra_sessoes(usuario_id):
    with connect_sistema() as conn:
        token = auth.criar_sessao(conn, usuario_id)
        auth.definir_senha(conn, usuario_id, "outra-senha-456")
        assert auth.obter_sessao(conn, token) is None
        assert auth.autenticar(conn, "ana@example.com", "outra-senha-456") == usuario_id


# ---------------------------------------------------------------------------
# Per-IP throttling
# ---------------------------------------------------------------------------


def _tentar(client, email, ip="203.0.113.7"):
    return client.post("/entrar", data={"email": email, "senha": "senha-errada-123"},
                       headers={"Fly-Client-IP": ip})


def test_bloqueia_ip_que_tenta_muitos_emails(usuario_id, monkeypatch):
    from test_app import _cliente

    monkeypatch.setenv("FLY_APP_NAME", "teste")
    monkeypatch.setattr(auth, "MAX_FALHAS_IP", 3)
    client = _cliente()
    # Different e-mails, so no account lock is involved: only the IP counter.
    for i in range(3):
        assert _tentar(client, f"pessoa{i}@example.com").status_code == 400
    resp = client.post("/entrar", data={"email": "ana@example.com", "senha": SENHA},
                       headers={"Fly-Client-IP": "203.0.113.7"})
    assert resp.status_code == 429 and "Muitas tentativas" in resp.text

    # Another address is not affected.
    resp = client.post("/entrar", data={"email": "ana@example.com", "senha": SENHA},
                       headers={"Fly-Client-IP": "198.51.100.9"})
    assert resp.status_code == 200


def test_falhas_antigas_saem_da_janela(usuario_id, monkeypatch):
    monkeypatch.setattr(auth, "MAX_FALHAS_IP", 2)
    with connect_sistema() as conn:
        auth.registrar_falha_ip(conn, "203.0.113.7")
        auth.registrar_falha_ip(conn, "203.0.113.7")
        assert auth.ip_bloqueado(conn, "203.0.113.7")
        execute(conn, "UPDATE falhas_login_ip SET em = now() - :j", j=auth.JANELA_FALHAS_IP)
        assert not auth.ip_bloqueado(conn, "203.0.113.7")
        # The IP itself is not stored.
        assert scalar(conn, "SELECT COUNT(*) FROM falhas_login_ip WHERE ip_hash LIKE '%203%'") == 0


def test_fly_client_ip_so_vale_no_fly(usuario_id, monkeypatch):
    from test_app import _cliente

    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(auth, "MAX_FALHAS_IP", 2)
    client = _cliente()
    # Off Fly the header is client-controlled: rotating it does not escape the limit.
    for i in range(2):
        _tentar(client, f"pessoa{i}@example.com", ip=f"203.0.113.{i}")
    assert _tentar(client, "outra@example.com", ip="203.0.113.99").status_code == 429
