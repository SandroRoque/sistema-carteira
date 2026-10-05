from datetime import date

from conftest import CPF_A, CPF_B
from contas import get_or_create_usuario
from database import connect, fetch_all, scalar
from fabricas import documento, negociacao
from loader import carregar


def test_carregar_cria_investidor_pelo_cpf_da_nota(usuario_id):
    with connect() as conn:
        assert carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        investidores = fetch_all(conn, "SELECT usuario_id, cpf FROM investidores")

    assert [dict(r) for r in investidores] == [{"usuario_id": usuario_id, "cpf": CPF_A}]


def test_carregar_e_idempotente(usuario_id):
    with connect() as conn:
        assert carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        assert not carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes") == 1


def test_mesma_nota_de_investidores_diferentes_nao_conflita(usuario_id):
    """Nota numbers are per broker, not global: two people can share one."""
    with connect() as conn:
        outro_usuario = get_or_create_usuario(conn, "bruno@example.com")
        assert carregar(conn, usuario_id, documento(CPF_A, nota_id="777"), "a.pdf")
        assert carregar(conn, outro_usuario, documento(CPF_B, nota_id="777"), "b.pdf")
        assert scalar(conn, "SELECT COUNT(*) FROM notas") == 2


def test_ativo_e_compartilhado_entre_investidores(usuario_id):
    with connect() as conn:
        outro_usuario = get_or_create_usuario(conn, "bruno@example.com")
        carregar(conn, usuario_id, documento(CPF_A, negociacao("PETR4F PN N2")), "a.pdf")
        carregar(conn, outro_usuario, documento(CPF_B, negociacao("PETR4 PN EDJ N2")), "b.pdf")

        ativos = fetch_all(conn, "SELECT ticker, tipo, revisado FROM ativos")
        aliases = scalar(conn, "SELECT COUNT(*) FROM ticker_aliases")

    assert [dict(a) for a in ativos] == [{"ticker": "PETR4", "tipo": "acao", "revisado": False}]
    assert aliases == 2


def test_valores_e_datas_preservam_tipos(usuario_id):
    doc = documento(CPF_A, negociacao(quantidade=3, preco=12.345, data=date(2024, 12, 31)))
    with connect() as conn:
        carregar(conn, usuario_id, doc, "1001.pdf")
        row = fetch_all(conn, "SELECT data, quantidade, preco_unitario FROM negociacoes")[0]

    assert row["data"] == date(2024, 12, 31)
    assert row["quantidade"] == 3
    assert row["preco_unitario"] == 12.345
