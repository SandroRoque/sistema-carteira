from datetime import date
from decimal import Decimal

from conftest import CPF_A, CPF_B
from contas import get_or_create_usuario
from database import connect_sistema, execute, fetch_all, scalar
from fabricas import documento, negociacao
from loader import carregar


def test_carregar_cria_investidor_pelo_cpf_da_nota(usuario_id):
    with connect_sistema() as conn:
        assert carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        investidores = fetch_all(conn, "SELECT usuario_id, cpf_mascarado FROM investidores")

    assert [dict(r) for r in investidores] == [
        {"usuario_id": usuario_id, "cpf_mascarado": "***.456.789-**"}
    ]


def test_mesmo_cpf_reaproveita_investidor(usuario_id):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A, nota_id="1"), "1.pdf")
        carregar(conn, usuario_id, documento("123.456.789-09", nota_id="2"), "2.pdf")
        assert scalar(conn, "SELECT COUNT(*) FROM investidores") == 1


def test_dados_de_identidade_nao_sao_persistidos(usuario_id):
    """LGPD minimization: no column anywhere holds the CPF or the client's name."""
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        linhas = [
            r["linha"]
            for tabela in ("investidores", "notas", "negociacoes", "ativos", "ticker_aliases")
            for r in fetch_all(conn, f"SELECT row_to_json(t)::text AS linha FROM {tabela} t")
        ]

    texto = " ".join(linhas)
    assert texto  # the load actually wrote something
    assert CPF_A not in texto
    assert "123.456.789-09" not in texto
    assert "Cliente Teste" not in texto


def test_carregar_e_idempotente(usuario_id):
    with connect_sistema() as conn:
        assert carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        assert not carregar(conn, usuario_id, documento(CPF_A), "1001.pdf")
        assert scalar(conn, "SELECT COUNT(*) FROM negociacoes") == 1


def test_mesma_nota_de_investidores_diferentes_nao_conflita(usuario_id):
    """Nota numbers are per broker, not global: two people can share one."""
    with connect_sistema() as conn:
        outro_usuario = get_or_create_usuario(conn, "bruno@example.com")
        assert carregar(conn, usuario_id, documento(CPF_A, nota_id="777"), "a.pdf")
        assert carregar(conn, outro_usuario, documento(CPF_B, nota_id="777"), "b.pdf")
        assert scalar(conn, "SELECT COUNT(*) FROM notas") == 2


def test_ativo_e_compartilhado_entre_investidores(usuario_id):
    with connect_sistema() as conn:
        outro_usuario = get_or_create_usuario(conn, "bruno@example.com")
        carregar(conn, usuario_id, documento(CPF_A, negociacao("PETR4F PN N2")), "a.pdf")
        carregar(conn, outro_usuario, documento(CPF_B, negociacao("PETR4 PN EDJ N2")), "b.pdf")

        ativos = fetch_all(conn, "SELECT ticker, tipo, revisado FROM ativos")
        aliases = scalar(conn, "SELECT COUNT(*) FROM ticker_aliases")

    assert [dict(a) for a in ativos] == [{"ticker": "PETR4", "tipo": "acao", "revisado": False}]
    assert aliases == 2


def test_valores_e_datas_preservam_tipos(usuario_id):
    doc = documento(CPF_A, negociacao(quantidade=3, preco=12.345, data=date(2024, 12, 31)))
    with connect_sistema() as conn:
        carregar(conn, usuario_id, doc, "1001.pdf")
        row = fetch_all(conn, "SELECT data, quantidade, preco_unitario FROM negociacoes")[0]

    assert row["data"] == date(2024, 12, 31)
    assert row["quantidade"] == 3
    assert row["preco_unitario"] == Decimal("12.345")


# ---------------------------------------------------------------------------
# Notas without a CPF (some XP notas leave the field blank)
# ---------------------------------------------------------------------------

def _sem_cpf(nota_id="9001"):
    import dataclasses

    doc = documento(CPF_A, nota_id=nota_id)
    return dataclasses.replace(doc, nota=dataclasses.replace(doc.nota, cpf_cliente=None))


def _investidor_da_nota(conn, nota_id):
    return scalar(conn, "SELECT investidor_id FROM notas WHERE nota_id = :n", n=nota_id)


def test_nota_sem_cpf_vai_para_a_unica_carteira(usuario_id, investidor_a):
    with connect_sistema() as conn:
        assert carregar(conn, usuario_id, _sem_cpf(), "sem-cpf.pdf")
        assert _investidor_da_nota(conn, "9001") == investidor_a


def test_nota_sem_cpf_usa_a_carteira_escolhida(usuario_id, investidor_a):
    from contas import get_or_create_investidor

    with connect_sistema() as conn:
        outra = get_or_create_investidor(conn, usuario_id, CPF_B, "Outra")
        assert carregar(conn, usuario_id, _sem_cpf(), "sem-cpf.pdf", carteira=outra)
        assert _investidor_da_nota(conn, "9001") == outra


def test_nota_sem_cpf_sem_como_decidir_a_carteira(usuario_id, investidor_a, investidor_b):
    import pytest

    from contas import get_or_create_investidor
    from loader import NotaSemCpf

    with connect_sistema() as conn:
        get_or_create_investidor(conn, usuario_id, CPF_B, "Outra")
        # Several portfolios, and the one offered belongs to another account.
        with pytest.raises(NotaSemCpf, match="Escolha a carteira"):
            carregar(conn, usuario_id, _sem_cpf(), "sem-cpf.pdf", carteira=investidor_b)


def test_nota_sem_cpf_numa_conta_sem_carteira(usuario_id):
    import pytest

    from loader import NotaSemCpf

    with connect_sistema() as conn:
        with pytest.raises(NotaSemCpf, match="nota com CPF"):
            carregar(conn, usuario_id, _sem_cpf(), "sem-cpf.pdf")


def _cdb(titulo: str, nota_id: str, taxa: str = "14.30", cnpj: str = "11.222.333/0001-81"):
    """A private bond purchase (TituloPrivado) with invented terms."""
    import dataclasses

    doc = documento(CPF_A, negociacao(
        titulo, quantidade=1, preco=1000.0, doc_type="TituloPrivado", tipo="aquisicao",
        indexador="PRE", taxa_cupom_percentual=Decimal(taxa),
        emissao=date(2025, 3, 10), vencimento=date(2028, 3, 10),
    ), nota_id=nota_id)
    nota = dataclasses.replace(doc.nota, doc_type="TituloPrivado", cnpj_emissor=cnpj, emissor="BANCO FICTICIO")
    return dataclasses.replace(doc, nota=nota)


def test_mesmo_titulo_com_texto_diferente_e_um_ativo_so(usuario_id):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, _cdb("CDB PREFIXADO 14,30% AA", "1"), "1.pdf")
        carregar(conn, usuario_id, _cdb("CDB 14.30% PREFIXADO AA", "2"), "2.pdf")
        # Different terms are a different bond.
        carregar(conn, usuario_id, _cdb("CDB PREFIXADO 13,90% AA", "3", taxa="13.90"), "3.pdf")

        ativos = scalar(conn, "SELECT COUNT(*) FROM ativos WHERE tipo = 'renda_fixa'")
        por_ativo = fetch_all(conn, "SELECT ativo_id, COUNT(*) AS n FROM negociacoes GROUP BY 1 ORDER BY 1")

    assert ativos == 2
    assert [r["n"] for r in por_ativo] == [2, 1]


def test_mescla_renda_fixa_carregada_em_duplicidade(usuario_id):
    from loader import mesclar_renda_fixa_duplicada

    with connect_sistema() as conn:
        carregar(conn, usuario_id, _cdb("CDB PREFIXADO 14,30% AA", "1"), "1.pdf")
        # As loaded before titles were matched by terms: a second ativo, same terms.
        primeiro = scalar(conn, "SELECT id FROM ativos WHERE tipo = 'renda_fixa'")
        segundo = scalar(conn, """
            INSERT INTO ativos (tipo, nome, cnpj_emissor, indexador, taxa_prefixada, emissao, vencimento, revisado)
            SELECT tipo, 'CDB 14.30% PREFIXADO AA', '11222333000181', indexador, 14.3, emissao, vencimento, false
            FROM ativos WHERE id = :id RETURNING id""", id=primeiro)
        execute(conn, "INSERT INTO ticker_aliases (raw_text, ativo_id) VALUES ('CDB 14.30% PREFIXADO AA', :id)",
                id=segundo)
        execute(conn, "UPDATE negociacoes SET ativo_id = :id", id=segundo)

        assert mesclar_renda_fixa_duplicada(conn) == [(segundo, primeiro)]
        assert scalar(conn, "SELECT COUNT(*) FROM ativos WHERE tipo = 'renda_fixa'") == 1
        assert scalar(conn, "SELECT DISTINCT ativo_id FROM negociacoes") == primeiro
        assert scalar(conn, "SELECT ativo_id FROM ticker_aliases WHERE raw_text = 'CDB 14.30% PREFIXADO AA'") == primeiro
        assert mesclar_renda_fixa_duplicada(conn) == []
