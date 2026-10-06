import sqlite3
from datetime import date
from pathlib import Path

from conftest import CPF_A
from database import connect, fetch_one, scalar
from migra_sqlite import migrar

_LEGACY_SCHEMA = (Path(__file__).parent / "legacy_sqlite_schema.sql").read_text()


def _sqlite_legado() -> sqlite3.Connection:
    src = sqlite3.connect(":memory:")
    src.row_factory = sqlite3.Row
    src.executescript(_LEGACY_SCHEMA)
    src.executescript(f"""
        INSERT INTO ativos (id, tipo, ticker, nome, vencimento, revisado)
            VALUES (7, 'acao', 'PETR4', 'PETR4F PN N2', NULL, 1),
                   (9, 'renda_fixa', NULL, 'CDB X', '2029-02-01', 0);
        INSERT INTO ticker_aliases VALUES ('PETR4F PN N2', 7), ('CDB X', 9);
        INSERT INTO notas (nota_id, corretora_id, doc_type, data_pregao, cpf_cliente,
                           codigo_cliente, filename)
            VALUES ('1', 'nu_invest', 'NotaCorretagem', '2025-01-10', '123.456.789-09', 'c', '1.pdf');
        INSERT INTO negociacoes (nota_id, corretora_id, doc_type, linha_na_nota, ativo_id, data,
                                 sentido, tipo, quantidade, preco_unitario, valor_liquido)
            VALUES ('1', 'nu_invest', 'NotaCorretagem', 1, 7, '2025-01-10',
                    'entrada', 'compra', 100, 10.5, 1050.0);
        INSERT INTO b3_arquivos_processados (arquivo) VALUES ('mov.xlsx');
        INSERT INTO b3_movimentacoes (id, sentido, data, movimentacao, produto_raw, ativo_id,
                                      quantidade, arquivo)
            VALUES (40, 'Credito', '2025-05-20', 'Bonificação em Ativos', 'PETR4', 7, 10, 'mov.xlsx');
        INSERT INTO bonificacoes VALUES (40, 12.34);
        -- References to an ativo that was deleted (legacy FKs were not enforced).
        PRAGMA foreign_keys=OFF;
        INSERT INTO ticker_aliases VALUES ('ZZZZ33', 88);
        INSERT INTO b3_movimentacoes (id, sentido, data, movimentacao, produto_raw, ativo_id,
                                      quantidade, arquivo)
            VALUES (41, 'Credito', '2025-06-01', 'Transferência', 'ZZZZ33', 88, 5, 'mov.xlsx');
    """)
    return src


def test_migra_banco_legado(db):
    src = _sqlite_legado()
    with connect() as conn:
        investidor_id = migrar(src, conn, "ana@example.com", CPF_A)

        assert scalar(
            conn, "SELECT cpf_mascarado FROM investidores WHERE id = :i", i=investidor_id
        ) == "***.456.789-**"
        assert "123.456.789-09" not in scalar(conn, "SELECT row_to_json(n)::text FROM notas n")

        neg = fetch_one(conn, "SELECT * FROM negociacoes")
        assert neg["investidor_id"] == investidor_id
        assert neg["data"] == date(2025, 1, 10)
        assert neg["valor_liquido"] == 1050.0

        rf = fetch_one(conn, "SELECT * FROM ativos WHERE id = 9")
        assert rf["vencimento"] == date(2029, 2, 1)
        assert rf["revisado"] is False

        assert scalar(conn, "SELECT custo_por_cota FROM bonificacoes WHERE b3_movimentacao_id = 40") == 12.34

        # The legacy database did not always enforce foreign keys.
        assert scalar(conn, "SELECT COUNT(*) FROM ticker_aliases WHERE raw_text = 'ZZZZ33'") == 0
        orfa = fetch_one(conn, "SELECT ativo_id, produto_raw FROM b3_movimentacoes WHERE id = 41")
        assert (orfa["ativo_id"], orfa["produto_raw"]) == (None, "ZZZZ33")

        # Sequences continue after the migrated ids.
        novo = scalar(conn, "INSERT INTO ativos (tipo, ticker) VALUES ('fii', 'XPML11') RETURNING id")
        assert novo == 10
