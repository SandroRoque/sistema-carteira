"""Row-level security: the database itself keeps accounts apart."""

import pytest
from sqlalchemy.exc import DBAPIError

from conftest import CPF_A, CPF_B
from database import connect, connect_sistema, execute, fetch_all, scalar
from fabricas import documento
from loader import carregar

_TABELAS = ("investidores", "notas", "negociacoes")


@pytest.fixture
def duas_contas(usuario_id, investidor_b):
    """ana (usuario_id) and bruno each own one portfolio with one trade."""
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A, nota_id="1"), "a.pdf")
        bruno = scalar(conn, "SELECT usuario_id FROM investidores WHERE id = :id", id=investidor_b)
        carregar(conn, bruno, documento(CPF_B, nota_id="2"), "b.pdf")
    return usuario_id, bruno


def test_cada_conta_ve_so_o_que_e_seu(duas_contas):
    ana, bruno = duas_contas
    for usuario in (ana, bruno):
        with connect(usuario) as conn:
            for tabela in _TABELAS:
                # Deliberately no WHERE clause: the policy does the filtering.
                assert scalar(conn, f"SELECT COUNT(*) FROM {tabela}") == 1, tabela


def test_sem_usuario_nao_ve_nada(duas_contas):
    with connect(None) as conn:
        for tabela in _TABELAS:
            assert scalar(conn, f"SELECT COUNT(*) FROM {tabela}") == 0


def test_catalogo_e_visivel_para_todos(duas_contas):
    with connect(None) as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM ativos") == 1


def test_nao_escreve_em_carteira_alheia(duas_contas, investidor_b):
    ana, _ = duas_contas
    with pytest.raises(DBAPIError, match="row-level security"):
        with connect(ana) as conn:
            execute(
                conn,
                """
                INSERT INTO notas (investidor_id, corretora_id, doc_type, nota_id, data_pregao, filename)
                VALUES (:inv, 'manual', 'Manual', 'x', '2025-01-01', 'x')
                """,
                inv=investidor_b,
            )


def test_nao_altera_nem_apaga_dados_alheios(duas_contas):
    ana, _ = duas_contas
    with connect(ana) as conn:
        conn.exec_driver_sql("UPDATE negociacoes SET quantidade = 0")
        conn.exec_driver_sql("DELETE FROM notas")
    with connect_sistema() as conn:
        quantidades = [r["quantidade"] for r in fetch_all(conn, "SELECT quantidade FROM negociacoes")]
        # Ana's own nota (and its trade, by cascade) is gone; Bruno's is untouched.
        assert quantidades == [100]
        assert scalar(conn, "SELECT COUNT(*) FROM notas") == 1


def test_sem_acesso_a_usuarios_e_sessoes(duas_contas):
    ana, _ = duas_contas
    for tabela in ("usuarios", "sessoes"):
        with pytest.raises(DBAPIError, match="permission denied"):
            with connect(ana) as conn:
                scalar(conn, f"SELECT COUNT(*) FROM {tabela}")


def test_commit_no_meio_do_bloco_continua_restrito(duas_contas):
    ana, _ = duas_contas
    with connect(ana) as conn:
        conn.commit()
        assert scalar(conn, "SELECT current_user") == "carteira_app"
        assert scalar(conn, "SELECT COUNT(*) FROM investidores") == 1


def test_conexao_devolvida_ao_pool_nao_carrega_contexto(duas_contas):
    ana, _ = duas_contas
    with connect(ana) as conn:
        conn.commit()  # leaves the session-level role in place
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT current_user") != "carteira_app"
        assert scalar(conn, "SELECT current_setting('app.usuario_id', true)") == ""
        assert scalar(conn, "SELECT COUNT(*) FROM investidores") == 2
