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
