"""Load B3 movimentações Excel reports into b3_movimentacoes.

Usage:
    .venv/bin/python carrega_b3.py
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from database import connect, init_db
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


def _to_date(val: str) -> str:
    """Convert 'DD/MM/YYYY' to 'YYYY-MM-DD'."""
    d, m, y = str(val).strip().split("/")
    return f"{y}-{m}-{d}"


def _carregar_arquivo(conn, arquivo: Path) -> tuple[int, int]:
    """Insert rows from one XLSX for dates not yet in the DB.

    Deduplication is date-based: if any row for a given date is already
    present in b3_movimentacoes, all rows for that date are skipped.
    This is safe even when two files cover overlapping date ranges.

    Returns (inserted, skipped).
    """
    filename = arquivo.name

    df = pd.read_excel(arquivo, header=0)
    df.columns = [
        "sentido", "data", "movimentacao", "produto_raw",
        "instituicao", "quantidade", "preco_unitario", "valor",
    ]

    # Dates already present in the DB (includes rows inserted earlier in this run).
    datas_existentes = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT data FROM b3_movimentacoes"
        ).fetchall()
    }

    # Record the file for auditing (idempotent if run twice).
    conn.execute(
        "INSERT OR IGNORE INTO b3_arquivos_processados (arquivo) VALUES (?)",
        (filename,),
    )

    inserted = 0
    skipped = 0
    for _, r in df.iterrows():
        data_iso = _to_date(r["data"])
        if data_iso in datas_existentes:
            skipped += 1
            continue

        produto_raw = str(r["produto_raw"]).strip()
        ativo_id = resolve_ou_criar_ativo(conn, produto_raw, doc_type="B3")

        movimentacao = str(r["movimentacao"]).strip()
        cur = conn.execute(
            """
            INSERT INTO b3_movimentacoes
                (sentido, data, movimentacao, produto_raw, ativo_id,
                 instituicao, quantidade, preco_unitario, valor, arquivo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(r["sentido"]).strip(),
                data_iso,
                movimentacao,
                produto_raw,
                ativo_id,
                str(r["instituicao"]).strip() if pd.notna(r["instituicao"]) else None,
                _to_float(r["quantidade"]),
                _to_float(r["preco_unitario"]),
                _to_float(r["valor"]),
                filename,
            ),
        )

        # Every bonus-share event gets a placeholder row in bonificacoes so the
        # user can later fill in custo_por_cota for correct average-cost calc.
        if movimentacao == "Bonificação em Ativos":
            conn.execute(
                "INSERT OR IGNORE INTO bonificacoes (b3_movimentacao_id) VALUES (?)",
                (cur.lastrowid,),
            )

        inserted += 1

    return inserted, skipped


def main() -> None:
    init_db()
    with connect() as conn:
        arquivos = sorted(B3_REPORTS_DIR.glob("*.xlsx"))
        if not arquivos:
            print(f"Nenhum arquivo .xlsx em {B3_REPORTS_DIR}")
            return

        for arquivo in arquivos:
            inserted, skipped = _carregar_arquivo(conn, arquivo)
            if inserted == 0 and skipped > 0:
                print(f"  ignorado  : {arquivo.name}  ({skipped} linhas, datas já carregadas)")
            elif skipped > 0:
                print(f"  carregado : {arquivo.name}  ({inserted} novas linhas, {skipped} ignoradas)")
            else:
                print(f"  carregado : {arquivo.name}  ({inserted} linhas)")

        # Quick breakdown of what's now in the table
        rows = conn.execute(
            """
            SELECT movimentacao, COUNT(*) AS n, SUM(valor) AS total
            FROM b3_movimentacoes
            GROUP BY movimentacao
            ORDER BY n DESC
            """
        ).fetchall()
        print("\nMovimentações na base:")
        for r in rows:
            total = f"R$ {r['total']:,.2f}" if r["total"] else "-"
            print(f"  {r['movimentacao']:<45}  {r['n']:>4}  {total}")


if __name__ == "__main__":
    main()
