"""Load B3 movimentações Excel reports into b3_movimentacoes.

B3 reports carry no CPF, so the target investidor comes from
CARTEIRA_INVESTIDOR_ID (or the only investidor in the database).

Usage:
    uv run python carrega_b3.py
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection

from contas import investidor_do_cli
from database import connect_sistema, execute, fetch_all, scalar
from loader import resolve_ou_criar_ativo

B3_REPORTS_DIR = Path(__file__).resolve().parent / "b3-reports"


def _to_float(val) -> float | None:
    """Return float or None for NaN / '-' / blank cells."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(val: str) -> date:
    """Parse a 'DD/MM/YYYY' cell."""
    return datetime.strptime(str(val).strip(), "%d/%m/%Y").date()


def carregar_arquivo(
    conn: Connection, investidor_id: int, df: pd.DataFrame, filename: str
) -> tuple[int, int]:
    """Insert rows from one B3 report for dates not yet loaded for this investidor.

    Deduplication is date-based: B3 reports are final for every date they
    cover, so if any row for a given date is already present for the
    investidor, all rows for that date are skipped. This is safe even when
    two files cover overlapping date ranges.

    Returns (inserted, skipped).
    """
    df = df.copy()
    df.columns = [
        "sentido", "data", "movimentacao", "produto_raw",
        "instituicao", "quantidade", "preco_unitario", "valor",
    ]

    datas_existentes = {
        r["data"]
        for r in fetch_all(
            conn,
            "SELECT DISTINCT data FROM b3_movimentacoes WHERE investidor_id = :investidor_id",
            investidor_id=investidor_id,
        )
    }

    # Record the file for auditing (idempotent if run twice).
    execute(
        conn,
        """
        INSERT INTO b3_arquivos_processados (investidor_id, arquivo)
        VALUES (:investidor_id, :arquivo)
        ON CONFLICT (investidor_id, arquivo) DO NOTHING
        """,
        investidor_id=investidor_id,
        arquivo=filename,
    )
    arquivo_id = scalar(
        conn,
        "SELECT id FROM b3_arquivos_processados WHERE investidor_id = :investidor_id AND arquivo = :arquivo",
        investidor_id=investidor_id,
        arquivo=filename,
    )

    inserted = 0
    skipped = 0
    for _, r in df.iterrows():
        data = _to_date(r["data"])
        if data in datas_existentes:
            skipped += 1
            continue

        produto_raw = str(r["produto_raw"]).strip()
        ativo_id = resolve_ou_criar_ativo(conn, produto_raw, doc_type="B3")

        movimentacao = str(r["movimentacao"]).strip()
        mov_id = scalar(
            conn,
            """
            INSERT INTO b3_movimentacoes
                (investidor_id, arquivo_id, sentido, data, movimentacao, produto_raw,
                 ativo_id, instituicao, quantidade, preco_unitario, valor)
            VALUES
                (:investidor_id, :arquivo_id, :sentido, :data, :movimentacao, :produto_raw,
                 :ativo_id, :instituicao, :quantidade, :preco_unitario, :valor)
            RETURNING id
            """,
            investidor_id=investidor_id,
            arquivo_id=arquivo_id,
            sentido=str(r["sentido"]).strip(),
            data=data,
            movimentacao=movimentacao,
            produto_raw=produto_raw,
            ativo_id=ativo_id,
            instituicao=str(r["instituicao"]).strip() if pd.notna(r["instituicao"]) else None,
            quantidade=_to_float(r["quantidade"]),
            preco_unitario=_to_float(r["preco_unitario"]),
            valor=_to_float(r["valor"]),
        )

        # Every bonus-share event gets a placeholder row in bonificacoes so the
        # user can later fill in custo_por_cota for correct average-cost calc.
        if movimentacao == "Bonificação em Ativos":
            execute(
                conn,
                "INSERT INTO bonificacoes (b3_movimentacao_id) VALUES (:id) ON CONFLICT DO NOTHING",
                id=mov_id,
            )

        inserted += 1

    return inserted, skipped


def main() -> None:
    with connect_sistema() as conn:
        investidor_id = investidor_do_cli(conn)

        arquivos = sorted(B3_REPORTS_DIR.glob("*.xlsx"))
        if not arquivos:
            print(f"Nenhum arquivo .xlsx em {B3_REPORTS_DIR}")
            return

        for arquivo in arquivos:
            df = pd.read_excel(arquivo, header=0)
            inserted, skipped = carregar_arquivo(conn, investidor_id, df, arquivo.name)
            if inserted == 0 and skipped > 0:
                print(f"  ignorado  : {arquivo.name}  ({skipped} linhas, datas já carregadas)")
            elif skipped > 0:
                print(f"  carregado : {arquivo.name}  ({inserted} novas linhas, {skipped} ignoradas)")
            else:
                print(f"  carregado : {arquivo.name}  ({inserted} linhas)")

        # Quick breakdown of what's now in the table
        rows = fetch_all(
            conn,
            """
            SELECT movimentacao, COUNT(*) AS n, SUM(valor) AS total
            FROM b3_movimentacoes
            WHERE investidor_id = :investidor_id
            GROUP BY movimentacao
            ORDER BY n DESC
            """,
            investidor_id=investidor_id,
        )
        print("\nMovimentações na base:")
        for r in rows:
            total = f"R$ {r['total']:,.2f}" if r["total"] else "-"
            print(f"  {r['movimentacao']:<45}  {r['n']:>4}  {total}")


if __name__ == "__main__":
    main()
