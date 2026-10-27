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


def _errar(conn, vezes, dispositivo=None):
    for _ in range(vezes):
        assert auth.autenticar(conn, "ana@example.com", "senha-errada-123", dispositivo) is None


def _vencer_espera(conn, usuario_id, ha=timedelta(seconds=1)):
    execute(conn, "UPDATE usuarios SET bloqueado_ate = :t WHERE id = :id",
            t=datetime.now(timezone.utc) - ha, id=usuario_id)


def _espera(conn, usuario_id):
    ate = scalar(conn, "SELECT bloqueado_ate FROM usuarios WHERE id = :id", id=usuario_id)
    return ate - datetime.now(timezone.utc)


def test_espera_cresce_a_cada_falha():
    assert auth.espera(auth.MAX_FALHAS_LOGIN - 1) == timedelta(0)
    assert auth.espera(auth.MAX_FALHAS_LOGIN) == timedelta(seconds=30)
    assert auth.espera(auth.MAX_FALHAS_LOGIN + 1) == timedelta(minutes=1)
    assert auth.espera(auth.MAX_FALHAS_LOGIN + 3) == timedelta(minutes=4)
    assert auth.espera(auth.MAX_FALHAS_LOGIN + 20) == auth.BLOQUEIO_LOGIN


def test_espera_apos_falhas_seguidas(usuario_id):
    with connect_sistema() as conn:
        _errar(conn, auth.MAX_FALHAS_LOGIN)
        # Waiting: even the right password is refused.
        assert auth.autenticar(conn, "ana@example.com", SENHA) is None
        assert timedelta(seconds=25) < _espera(conn, usuario_id) <= timedelta(seconds=30)

        # After the wait, one more failure waits twice as long.
        _vencer_espera(conn, usuario_id)
        _errar(conn, 1)
        assert timedelta(seconds=55) < _espera(conn, usuario_id) <= timedelta(minutes=1)

        # The right password after the wait works and the counter resets.
        _vencer_espera(conn, usuario_id)
        assert auth.autenticar(conn, "ana@example.com", SENHA) == usuario_id
        assert scalar(conn, "SELECT falhas_login FROM usuarios WHERE id = :id", id=usuario_id) == 0


def test_falhas_de_dias_atras_sao_esquecidas(usuario_id):
    with connect_sistema() as conn:
        _errar(conn, auth.MAX_FALHAS_LOGIN + 3)
        _vencer_espera(conn, usuario_id, auth.ESQUECER_FALHAS + timedelta(minutes=1))
        _errar(conn, 1)
        assert scalar(conn, "SELECT falhas_login FROM usuarios WHERE id = :id", id=usuario_id) == 1


def test_aparelho_conhecido_nao_espera(usuario_id):
    with connect_sistema() as conn:
        _errar(conn, auth.MAX_FALHAS_LOGIN)
        dispositivo = auth.dispositivo_de(auth.token_dispositivo(usuario_id))
        assert dispositivo == usuario_id
        assert auth.autenticar(conn, "ana@example.com", SENHA, dispositivo) == usuario_id


def test_cookie_de_aparelho_falso_ou_de_outra_conta_nao_vale(usuario_id):
    token = auth.token_dispositivo(usuario_id)
    assert auth.dispositivo_de(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert auth.dispositivo_de(f"{usuario_id + 1}.{token.partition('.')[2]}") is None
    assert auth.dispositivo_de("") is None and auth.dispositivo_de(None) is None
    with connect_sistema() as conn:
        _errar(conn, auth.MAX_FALHAS_LOGIN)
        # A genuine cookie of another account does not skip this one's wait.
        assert auth.autenticar(conn, "ana@example.com", SENHA, usuario_id + 1) is None


def test_login_pela_web_lembra_o_aparelho(usuario_id):
    from test_app import _cliente

    cliente = _cliente()
    r = cliente.post("/entrar", data={"email": "ana@example.com", "senha": SENHA}, follow_redirects=False)
    assert r.status_code == 303 and "dispositivo" in r.cookies
    cliente.post("/sair")
    with connect_sistema() as conn:
        _errar(conn, auth.MAX_FALHAS_LOGIN)
    # Someone else made the account wait; this browser logged in before.
    r = cliente.post("/entrar", data={"email": "ana@example.com", "senha": SENHA}, follow_redirects=False)
    assert r.status_code == 303


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
