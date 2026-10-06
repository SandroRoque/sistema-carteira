"""Account administration from the command line.

Usage:
    uv run python admin.py criar-usuario EMAIL [--admin]
    uv run python admin.py definir-senha EMAIL
    uv run python admin.py promover-admin EMAIL

Passwords are read interactively, never from arguments (shell history).
"""

from __future__ import annotations

import argparse
import getpass
import sys

import auth
from database import connect_sistema, execute, scalar


def _ler_senha() -> str:
    senha = getpass.getpass("Senha: ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    return senha


def _usuario_id(conn, email: str) -> int:
    usuario_id = scalar(conn, "SELECT id FROM usuarios WHERE lower(email) = lower(:e)", e=email)
    if usuario_id is None:
        sys.exit(f"Usuário {email} não encontrado.")
    return usuario_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Administração de contas.")
    sub = parser.add_subparsers(dest="comando", required=True)
    criar = sub.add_parser("criar-usuario")
    criar.add_argument("email")
    criar.add_argument("--admin", action="store_true")
    sub.add_parser("definir-senha").add_argument("email")
    sub.add_parser("promover-admin").add_argument("email")
    args = parser.parse_args()

    with connect_sistema() as conn:
        try:
            if args.comando == "criar-usuario":
                usuario_id = auth.criar_usuario(conn, args.email, _ler_senha())
                if args.admin:
                    execute(conn, "UPDATE usuarios SET e_admin = true WHERE id = :id", id=usuario_id)
            elif args.comando == "definir-senha":
                auth.definir_senha(conn, _usuario_id(conn, args.email), _ler_senha())
            elif args.comando == "promover-admin":
                execute(
                    conn,
                    "UPDATE usuarios SET e_admin = true WHERE id = :id",
                    id=_usuario_id(conn, args.email),
                )
        except auth.ErroCadastro as exc:
            sys.exit(str(exc))
    print("ok")


if __name__ == "__main__":
    main()
