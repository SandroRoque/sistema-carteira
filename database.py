"""PostgreSQL access layer.

The schema lives in tabelas.py and is managed by Alembic (`alembic upgrade head`).
Queries are plain SQL with named parameters, run through the helpers below:

    with connect(usuario_id) as conn:
        rows = fetch_all(conn, "SELECT * FROM ativos WHERE tipo = :tipo", tipo="fii")
        rows[0]["ticker"]

connect(usuario_id) is tenant-scoped (row-level security, see migration 0003);
connect_sistema() is unrestricted and reserved for trusted code. Both commit
when the block exits normally and roll back on error.

NUMERIC values are loaded as decimal.Decimal, so money and quantities stay
exact from storage through every calculation. Never mix them with float
literals in arithmetic (Decimal + 0.0 raises TypeError): use 0 or Decimal.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, RowMapping, create_engine, event, text

from settings import database_url

_engine: Engine | None = None


def _limpar_contexto(dbapi_connection, _connection_record, _connection_proxy) -> None:
    """Every connection leaves the pool as the owner role with no tenant set.

    Tenant context is applied at session level (see _transacao), so this reset
    on checkout is what keeps it from leaking to the next borrower.
    """
    with dbapi_connection.cursor() as cur:
        cur.execute("RESET ROLE")
        cur.execute("SELECT set_config('app.usuario_id', '', false)")
    dbapi_connection.commit()


def configure(url: str | None = None) -> Engine:
    """(Re)create the engine. Without a url, reads DATABASE_URL (after loading .env)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url or database_url(), pool_pre_ping=True)
    event.listen(_engine, "checkout", _limpar_contexto)
    return _engine


def get_engine() -> Engine:
    return _engine or configure()


@contextmanager
def _transacao(usuario_id: int | None, *, restrita: bool) -> Iterator[Connection]:
    with get_engine().connect() as conn:
        try:
            if restrita:
                # Session-level (not SET LOCAL): if code commits mid-block, the
                # connection stays restricted instead of silently becoming the
                # owner. The pool checkout hook resets it for the next borrower.
                conn.exec_driver_sql("SET ROLE carteira_app")
                conn.execute(
                    text("SELECT set_config('app.usuario_id', :id, false)"),
                    {"id": "" if usuario_id is None else str(usuario_id)},
                )
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def connect(usuario_id: int | None) -> AbstractContextManager[Connection]:
    """Tenant-scoped transaction: row-level security limits every query to
    the data of `usuario_id` (None = no account: tenant tables look empty).

    This is what web requests use.
    """
    return _transacao(usuario_id, restrita=True)


def connect_sistema() -> AbstractContextManager[Connection]:
    """Unrestricted transaction for trusted code: CLI tools, migrations,
    authentication and background jobs. Never use it to serve a request's data."""
    return _transacao(None, restrita=False)


def fetch_all(conn: Connection, sql: str, **params: Any) -> list[RowMapping]:
    return list(conn.execute(text(sql), params).mappings())


def fetch_one(conn: Connection, sql: str, **params: Any) -> RowMapping | None:
    return conn.execute(text(sql), params).mappings().first()


def scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()


def execute(conn: Connection, sql: str, **params: Any) -> None:
    conn.execute(text(sql), params)
