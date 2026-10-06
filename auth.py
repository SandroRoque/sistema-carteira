"""Accounts, passwords and login sessions.

Passwords are hashed with Argon2id. Sessions are random tokens kept in a
cookie; the database stores only their SHA-256, so a leaked dump does not
contain usable sessions. Everything here runs on the system connection: the
restricted role used for request data cannot read usuarios or sessoes.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from database import execute, fetch_one, scalar

DURACAO_SESSAO = timedelta(days=30)
MAX_FALHAS_LOGIN = 5
BLOQUEIO_LOGIN = timedelta(minutes=15)
SENHA_MIN = 10
SENHA_MAX = 256  # bounds Argon2 work per request

_hasher = PasswordHasher()
# Verified when the e-mail does not exist, so response time does not reveal
# which e-mails have accounts.
_HASH_FICTICIO = _hasher.hash(secrets.token_urlsafe(16))
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErroCadastro(ValueError):
    pass


@dataclass(frozen=True)
class Sessao:
    usuario_id: int
    investidor_id: int | None
    csrf_token: str
    e_admin: bool
    email: str
    # The public demo account: read-only (app.seguranca).
    demo: bool = False


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validar_senha(senha: str) -> None:
    if len(senha) < SENHA_MIN:
        raise ErroCadastro(f"A senha precisa ter pelo menos {SENHA_MIN} caracteres.")
    if len(senha) > SENHA_MAX:
        raise ErroCadastro(f"A senha pode ter no máximo {SENHA_MAX} caracteres.")


def criar_usuario(conn: Connection, email: str, senha: str) -> int:
    email = email.strip()
    if not _EMAIL_RE.match(email):
        raise ErroCadastro("E-mail inválido.")
    validar_senha(senha)
    try:
        with conn.begin_nested():
            return scalar(
                conn,
                "INSERT INTO usuarios (email, senha_hash) VALUES (:email, :hash) RETURNING id",
                email=email,
                hash=_hasher.hash(senha),
            )
    except IntegrityError:
        raise ErroCadastro("Já existe uma conta com este e-mail.") from None


def definir_senha(conn: Connection, usuario_id: int, senha: str) -> None:
    validar_senha(senha)
    execute(
        conn,
        "UPDATE usuarios SET senha_hash = :hash, falhas_login = 0, bloqueado_ate = NULL WHERE id = :id",
        hash=_hasher.hash(senha),
        id=usuario_id,
    )
    # A password change ends every existing session.
    execute(conn, "DELETE FROM sessoes WHERE usuario_id = :id", id=usuario_id)


def verificar_senha(conn: Connection, usuario_id: int, senha: str) -> bool:
    row = fetch_one(conn, "SELECT senha_hash FROM usuarios WHERE id = :id", id=usuario_id)
    if not row or not row["senha_hash"]:
        return False
    try:
        return _hasher.verify(row["senha_hash"], senha[:SENHA_MAX])
    except (VerificationError, InvalidHashError):
        return False


def autenticar(conn: Connection, email: str, senha: str) -> int | None:
    """usuario_id when the credentials are valid and the account is not locked."""
    senha = senha[:SENHA_MAX]
    row = fetch_one(
        conn,
        """
        SELECT id, senha_hash, bloqueado_ate FROM usuarios
        WHERE lower(email) = lower(:email)
        """,
        email=email.strip(),
    )
    if row is None or row["senha_hash"] is None:
        try:
            _hasher.verify(_HASH_FICTICIO, senha)
        except VerificationError:
            pass
        return None

    if row["bloqueado_ate"]:
        if row["bloqueado_ate"] > _agora():
            return None
        # Lock expired: start counting failures from zero again.
        execute(
            conn,
            "UPDATE usuarios SET falhas_login = 0, bloqueado_ate = NULL WHERE id = :id",
            id=row["id"],
        )

    try:
        _hasher.verify(row["senha_hash"], senha)
    except (VerificationError, InvalidHashError):
        execute(
            conn,
            """
            UPDATE usuarios
            SET falhas_login = falhas_login + 1,
                bloqueado_ate = CASE WHEN falhas_login + 1 >= :max
                                     THEN now() + :bloqueio ELSE bloqueado_ate END
            WHERE id = :id
            """,
            max=MAX_FALHAS_LOGIN,
            bloqueio=BLOQUEIO_LOGIN,
            id=row["id"],
        )
        return None

    novo_hash = _hasher.hash(senha) if _hasher.check_needs_rehash(row["senha_hash"]) else row["senha_hash"]
    execute(
        conn,
        "UPDATE usuarios SET falhas_login = 0, bloqueado_ate = NULL, senha_hash = :h WHERE id = :id",
        h=novo_hash,
        id=row["id"],
    )
    return row["id"]


def criar_sessao(conn: Connection, usuario_id: int) -> str:
    """Return the cookie token for a new session.

    Expired sessions are dropped here: every demo visit creates one, and
    nothing else cleans them up."""
    execute(conn, "DELETE FROM sessoes WHERE expira_em <= now()")
    token = secrets.token_urlsafe(32)
    execute(
        conn,
        """
        INSERT INTO sessoes (token_hash, usuario_id, csrf_token, expira_em)
        VALUES (:h, :usuario_id, :csrf, :expira)
        """,
        h=_hash_token(token),
        usuario_id=usuario_id,
        csrf=secrets.token_urlsafe(32),
        expira=_agora() + DURACAO_SESSAO,
    )
    return token


def obter_sessao(conn: Connection, token: str | None) -> Sessao | None:
    if not token:
        return None
    row = fetch_one(
        conn,
        """
        SELECT s.usuario_id, s.investidor_id, s.csrf_token, u.e_admin, u.email, u.demo
        FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id
        WHERE s.token_hash = :h AND s.expira_em > now()
        """,
        h=_hash_token(token),
    )
    return Sessao(**row) if row else None


def usuario_demo(conn: Connection) -> int | None:
    """The demo account (demo.py), if this installation has one."""
    return scalar(conn, "SELECT id FROM usuarios WHERE demo")


def selecionar_investidor(conn: Connection, token: str, investidor_id: int | None) -> None:
    """Callers must have checked that investidor_id belongs to the session's usuario."""
    execute(
        conn,
        "UPDATE sessoes SET investidor_id = :inv WHERE token_hash = :h",
        inv=investidor_id,
        h=_hash_token(token),
    )


def encerrar_sessao(conn: Connection, token: str | None) -> None:
    if token:
        execute(conn, "DELETE FROM sessoes WHERE token_hash = :h", h=_hash_token(token))


def excluir_usuario(conn: Connection, usuario_id: int) -> None:
    """Delete the account and, by cascade, every investidor, nota, trade,
    B3 row and session it owns (LGPD art. 18, VI)."""
    execute(conn, "DELETE FROM usuarios WHERE id = :id", id=usuario_id)
