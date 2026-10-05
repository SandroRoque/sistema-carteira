"""Usuarios and investidores — the tenancy boundary.

Every portfolio query is scoped to one investidor_id. The web app resolves it
from the logged-in session; command-line scripts resolve it from environment
variables (or pick the only one that exists, for single-user local setups).
"""

from __future__ import annotations

import os

from sqlalchemy import Connection

from database import fetch_all, fetch_one, scalar
from parsers import cpf_parser


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
    conn: Connection, usuario_id: int, cpf: str, nome: str | None = None
) -> int:
    cpf = cpf_parser(cpf)
    row = fetch_one(
        conn,
        "SELECT id FROM investidores WHERE usuario_id = :usuario_id AND cpf = :cpf",
        usuario_id=usuario_id,
        cpf=cpf,
    )
    if row:
        return row["id"]
    return scalar(
        conn,
        """
        INSERT INTO investidores (usuario_id, cpf, nome)
        VALUES (:usuario_id, :cpf, :nome)
        RETURNING id
        """,
        usuario_id=usuario_id,
        cpf=cpf,
        nome=nome,
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
        conn, "SELECT id, COALESCE(nome, 'sem nome') AS label FROM investidores ORDER BY id"
    )
    return _unico_ou_erro(rows, "investidor", "CARTEIRA_INVESTIDOR_ID")
