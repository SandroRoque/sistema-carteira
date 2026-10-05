"""PostgreSQL access layer.

The schema lives in tabelas.py and is managed by Alembic (`alembic upgrade head`).
Queries are plain SQL with named parameters, run through the helpers below:

    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM ativos WHERE tipo = :tipo", tipo="fii")
        rows[0]["ticker"]

`connect()` commits when the block exits normally and rolls back on error.

NUMERIC values are loaded as Python floats for now: the calculation modules
(posicoes, fechamento, imposto) still use float arithmetic. Storage is exact;
moving the calculations to Decimal is a separate step.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg.types.numeric import FloatLoader
from sqlalchemy import Connection, Engine, RowMapping, create_engine, event, text

from settings import database_url

_engine: Engine | None = None


def _register_numeric_as_float(dbapi_connection, _connection_record) -> None:
    dbapi_connection.adapters.register_loader("numeric", FloatLoader)


def configure(url: str | None = None) -> Engine:
    """(Re)create the engine. Without a url, reads DATABASE_URL (after loading .env)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url or database_url(), pool_pre_ping=True)
    event.listen(_engine, "connect", _register_numeric_as_float)
    return _engine


def get_engine() -> Engine:
    return _engine or configure()


@contextmanager
def connect() -> Iterator[Connection]:
    with get_engine().connect() as conn:
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def fetch_all(conn: Connection, sql: str, **params: Any) -> list[RowMapping]:
    return list(conn.execute(text(sql), params).mappings())


def fetch_one(conn: Connection, sql: str, **params: Any) -> RowMapping | None:
    return conn.execute(text(sql), params).mappings().first()


def scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()


def execute(conn: Connection, sql: str, **params: Any) -> None:
    conn.execute(text(sql), params)
