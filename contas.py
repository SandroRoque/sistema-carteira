"""Usuarios and investidores — the tenancy boundary.

An usuario (account) owns everything it uploads. Inside an account, data is
split into investidores: one per CPF found on the uploaded notas, because
positions and taxes are computed per person. The CPF is only a partition key;
the system never verifies identity and never stores the CPF itself — only a
keyed HMAC (to recognize the same CPF on later uploads) and a masked form
(to tell portfolios apart on screen).

Every portfolio query is scoped to one investidor_id. The web app resolves it
from the logged-in session; command-line scripts resolve it from environment
variables (or pick the only one that exists, for single-user local setups).
"""

from __future__ import annotations

import hashlib
import hmac
import os

from sqlalchemy import Connection

from database import fetch_all, fetch_one, scalar
from parsers import cpf_parser
from settings import cpf_hmac_key


def pseudonimizar_cpf(cpf: str) -> tuple[str, str]:
    """Return (hmac_hex, masked) for a CPF; the CPF itself is not kept."""
    cpf = cpf_parser(cpf)
    digest = hmac.new(cpf_hmac_key(), cpf.encode(), hashlib.sha256).hexdigest()
    return digest, f"***.{cpf[3:6]}.{cpf[6:9]}-**"


def get_or_create_usuario(conn: Connection, email: str, nome: str | None = None) -> int:
    email = email.strip()
    row = fetch_one(conn, "SELECT id FROM usuarios WHERE lower(email) = lower(:email)", email=email)
    if row:
        return row["id"]
    return scalar(
        conn,
        "INSERT INTO usuarios (email, nome) VALUES (:email, :nome) RETURNING id",
        email=email,
        nome=nome,
    )


def get_or_create_investidor(
    conn: Connection, usuario_id: int, cpf: str, apelido: str | None = None
) -> int:
    cpf_hash, cpf_mascarado = pseudonimizar_cpf(cpf)
    row = fetch_one(
        conn,
        "SELECT id FROM investidores WHERE usuario_id = :usuario_id AND cpf_hash = :cpf_hash",
        usuario_id=usuario_id,
        cpf_hash=cpf_hash,
    )
    if row:
        return row["id"]
    return scalar(
        conn,
        """
        INSERT INTO investidores (usuario_id, cpf_hash, cpf_mascarado, apelido)
        VALUES (:usuario_id, :cpf_hash, :cpf_mascarado, :apelido)
        RETURNING id
        """,
        usuario_id=usuario_id,
        cpf_hash=cpf_hash,
        cpf_mascarado=cpf_mascarado,
        apelido=apelido,
    )


class ContextoNaoResolvido(RuntimeError):
    pass


def _unico_ou_erro(rows, entidade: str, variavel: str) -> int:
    if len(rows) == 1:
        return rows[0]["id"]
    if not rows:
        raise ContextoNaoResolvido(f"Nenhum {entidade} cadastrado.")
    opcoes = ", ".join(f"{r['id']} ({r['label']})" for r in rows)
    raise ContextoNaoResolvido(f"Há mais de um {entidade}; defina {variavel}. Opções: {opcoes}")


def usuario_do_cli(conn: Connection) -> int:
    """CARTEIRA_USUARIO_EMAIL, or the only usuario in the database."""
    email = os.environ.get("CARTEIRA_USUARIO_EMAIL")
    if email:
        return get_or_create_usuario(conn, email)
    rows = fetch_all(conn, "SELECT id, email AS label FROM usuarios ORDER BY id")
    return _unico_ou_erro(rows, "usuário", "CARTEIRA_USUARIO_EMAIL")


def investidor_do_cli(conn: Connection) -> int:
    """CARTEIRA_INVESTIDOR_ID, or the only investidor in the database."""
    raw = os.environ.get("CARTEIRA_INVESTIDOR_ID")
    if raw:
        investidor_id = int(raw)
        if not fetch_one(conn, "SELECT 1 FROM investidores WHERE id = :id", id=investidor_id):
            raise ContextoNaoResolvido(f"Investidor {investidor_id} não existe.")
        return investidor_id
    rows = fetch_all(
        conn,
        "SELECT id, COALESCE(apelido, cpf_mascarado) AS label FROM investidores ORDER BY id",
    )
    return _unico_ou_erro(rows, "investidor", "CARTEIRA_INVESTIDOR_ID")
