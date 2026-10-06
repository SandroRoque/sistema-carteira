import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["CPF_HMAC_KEY"] = "chave-de-teste-" + "x" * 32

# Fake CPFs with valid check digits. Never use real ones in tests.
CPF_A = "12345678909"
CPF_B = "98765432100"


@pytest.fixture(scope="session")
def database_url():
    """A throwaway PostgreSQL with the schema built by the real Alembic migrations."""
    from alembic import command
    from alembic.config import Config
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17", driver="psycopg") as pg:
        url = pg.get_connection_url()

        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        cfg.set_main_option("sqlalchemy.url", url)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")

        yield url


@pytest.fixture
def db(database_url):
    """Point the app at the test database and wipe all data after each test."""
    import database

    engine = database.configure(database_url)
    yield engine
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "TRUNCATE usuarios, investidores, ativos, ticker_aliases, notas, negociacoes, "
            "b3_arquivos_processados, b3_movimentacoes, bonificacoes RESTART IDENTITY CASCADE"
        )


@pytest.fixture
def usuario_id(db):
    from contas import get_or_create_usuario
    from database import connect

    with connect() as conn:
        return get_or_create_usuario(conn, "ana@example.com")


@pytest.fixture
def investidor_a(db, usuario_id):
    from contas import get_or_create_investidor
    from database import connect

    with connect() as conn:
        return get_or_create_investidor(conn, usuario_id, CPF_A, "Ana")


@pytest.fixture
def investidor_b(db):
    """An investidor owned by a different usuario."""
    from contas import get_or_create_investidor, get_or_create_usuario
    from database import connect

    with connect() as conn:
        outro = get_or_create_usuario(conn, "bruno@example.com")
        return get_or_create_investidor(conn, outro, CPF_B, "Bruno")
