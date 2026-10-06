"""One-off import of the legacy single-user SQLite database into PostgreSQL.

The whole legacy portfolio is filed under one investidor (one CPF) owned by
one usuario (identified by e-mail). Identity data of the legacy notas is not
carried over. Run after `alembic upgrade head`,
against an empty catalog:

    uv run python migra_sqlite.py --email voce@exemplo.com [--cpf 12345678909] [--sqlite carteira.db]

Without --cpf, the CPF printed on most notas is used.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import Connection

import tabelas
from contas import get_or_create_investidor, get_or_create_usuario
from database import connect_sistema, execute, scalar
from parsers import cpf_parser

_DATE_COLUMNS = {"data", "data_pregao", "data_de_liquidacao", "emissao", "vencimento"}


def _converter(row: sqlite3.Row, tabela, **extra) -> dict:
    colunas = set(tabela.c.keys())
    valores = {}
    for k in row.keys():
        if k not in colunas:
            continue
        v = row[k]
        if k in _DATE_COLUMNS and v:
            v = date.fromisoformat(v)
        valores[k] = v
    valores.update(extra)
    return valores


def _inserir(conn: Connection, tabela, linhas: list[dict]) -> None:
    if linhas:
        conn.execute(tabela.insert(), linhas)


def _resetar_sequencia(conn: Connection, tabela: str) -> None:
    execute(
        conn,
        f"SELECT setval(pg_get_serial_sequence('{tabela}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {tabela}), 0) + 1, false)",
    )


def _cpf_predominante(src: sqlite3.Connection) -> str:
    validos: Counter[str] = Counter()
    for (cpf,) in src.execute("SELECT cpf_cliente FROM notas"):
        try:
            validos[cpf_parser(cpf)] += 1
        except ValueError:
            pass
    if not validos:
        raise SystemExit("Nenhum CPF válido nas notas; informe --cpf.")
    return validos.most_common(1)[0][0]


def migrar(src: sqlite3.Connection, conn: Connection, email: str, cpf: str) -> int:
    if scalar(conn, "SELECT COUNT(*) FROM ativos"):
        raise SystemExit("O catálogo de ativos no Postgres não está vazio; migração abortada.")

    usuario_id = get_or_create_usuario(conn, email)
    investidor_id = get_or_create_investidor(conn, usuario_id, cpf)

    ativos = []
    for r in src.execute("SELECT * FROM ativos"):
        linha = _converter(r, tabelas.ativos)
        linha["revisado"] = bool(linha["revisado"])
        ativos.append(linha)
    _inserir(conn, tabelas.ativos, ativos)
    _resetar_sequencia(conn, "ativos")

    # The legacy database did not always enforce foreign keys, so it can hold
    # references to deleted ativos. Those aliases are dropped and B3 rows are
    # kept as "unresolved product" (ativo_id NULL), which reproduces what the
    # legacy reports showed: rows pointing at a deleted ativo were invisible.
    ativo_ids = {a["id"] for a in ativos}
    aliases = [_converter(r, tabelas.ticker_aliases) for r in src.execute("SELECT * FROM ticker_aliases")]
    for a in aliases:
        if a["ativo_id"] not in ativo_ids:
            print(f"  alias órfão descartado: {a['raw_text']!r} → ativo {a['ativo_id']} (inexistente)")
    _inserir(conn, tabelas.ticker_aliases, [a for a in aliases if a["ativo_id"] in ativo_ids])

    # _converter keeps only columns that exist in Postgres, so the identity
    # data of the legacy notas (CPF, name, address...) is left behind.
    notas = []
    for r in src.execute("SELECT * FROM notas"):
        linha = _converter(r, tabelas.notas, investidor_id=investidor_id)
        linha.pop("processado_em", None)  # legacy value is a naive local string
        notas.append(linha)
    _inserir(conn, tabelas.notas, notas)

    _inserir(conn, tabelas.negociacoes, [
        _converter(r, tabelas.negociacoes, investidor_id=investidor_id)
        for r in src.execute("SELECT * FROM negociacoes")
    ])
    _resetar_sequencia(conn, "negociacoes")

    arquivo_ids: dict[str, int] = {}
    for (arquivo,) in src.execute("SELECT arquivo FROM b3_arquivos_processados"):
        arquivo_ids[arquivo] = scalar(
            conn,
            """
            INSERT INTO b3_arquivos_processados (investidor_id, arquivo)
            VALUES (:investidor_id, :arquivo) RETURNING id
            """,
            investidor_id=investidor_id,
            arquivo=arquivo,
        )

    movimentacoes = [
        _converter(
            r,
            tabelas.b3_movimentacoes,
            investidor_id=investidor_id,
            arquivo_id=arquivo_ids[r["arquivo"]],
        )
        for r in src.execute("SELECT * FROM b3_movimentacoes")
    ]
    for m in movimentacoes:
        if m["ativo_id"] is not None and m["ativo_id"] not in ativo_ids:
            print(f"  movimentação {m['id']} ({m['produto_raw']!r}): ativo {m['ativo_id']} "
                  "inexistente → ativo_id NULL")
            m["ativo_id"] = None
    _inserir(conn, tabelas.b3_movimentacoes, movimentacoes)
    _resetar_sequencia(conn, "b3_movimentacoes")

    _inserir(conn, tabelas.bonificacoes, [
        _converter(r, tabelas.bonificacoes) for r in src.execute("SELECT * FROM bonificacoes")
    ])

    return investidor_id


def _contagens(src: sqlite3.Connection, conn: Connection, investidor_id: int) -> None:
    pares = [
        ("ativos", "SELECT COUNT(*) FROM ativos", "SELECT COUNT(*) FROM ativos"),
        ("ticker_aliases", "SELECT COUNT(*) FROM ticker_aliases", "SELECT COUNT(*) FROM ticker_aliases"),
        ("notas", "SELECT COUNT(*) FROM notas",
         "SELECT COUNT(*) FROM notas WHERE investidor_id = :i"),
        ("negociacoes", "SELECT COUNT(*) FROM negociacoes",
         "SELECT COUNT(*) FROM negociacoes WHERE investidor_id = :i"),
        ("b3_movimentacoes", "SELECT COUNT(*) FROM b3_movimentacoes",
         "SELECT COUNT(*) FROM b3_movimentacoes WHERE investidor_id = :i"),
        ("bonificacoes", "SELECT COUNT(*) FROM bonificacoes", "SELECT COUNT(*) FROM bonificacoes"),
    ]
    for nome, sql_src, sql_pg in pares:
        origem = src.execute(sql_src).fetchone()[0]
        destino = scalar(conn, sql_pg, i=investidor_id)
        status = "ok" if origem == destino else "DIVERGENTE"
        print(f"  {nome:<18} sqlite={origem:<5} postgres={destino:<5} {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--cpf")
    parser.add_argument("--sqlite", type=Path, default=Path("carteira.db"))
    args = parser.parse_args()

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row
    cpf = cpf_parser(args.cpf) if args.cpf else _cpf_predominante(src)

    with connect_sistema() as conn:
        investidor_id = migrar(src, conn, args.email, cpf)
        print(f"Migrado para investidor_id={investidor_id}")
        _contagens(src, conn, investidor_id)

    divergentes = []
    for nota_id, cpf_nota in src.execute("SELECT nota_id, cpf_cliente FROM notas"):
        try:
            if cpf_parser(cpf_nota) != cpf:
                divergentes.append(nota_id)
        except ValueError:
            divergentes.append(nota_id)
    if divergentes:
        print(f"  atenção: nota(s) {', '.join(divergentes)} com CPF diferente do investidor "
              "(provável erro de extração); foram migradas para o mesmo investidor.")


if __name__ == "__main__":
    main()
