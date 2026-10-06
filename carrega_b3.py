"""Load B3 movimentações Excel reports into b3_movimentacoes.

B3 reports carry no CPF, so the target investidor comes from
CARTEIRA_INVESTIDOR_ID (or the only investidor in the database).

Usage:
    uv run python carrega_b3.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Connection

from contas import investidor_do_cli
from database import connect_sistema, execute, fetch_all, scalar
from loader import resolve_ou_criar_ativo

if TYPE_CHECKING:
    import pandas as pd

B3_REPORTS_DIR = Path(__file__).resolve().parent / "b3-reports"

_N_COLUNAS = 8


class RelatorioB3Invalido(ValueError):
    """The spreadsheet does not have the layout of a B3 movimentação report."""


@dataclass(frozen=True)
class LinhaB3:
    sentido: str
    data: date
    movimentacao: str
    produto_raw: str
    instituicao: str | None
    quantidade: Decimal | None
    preco_unitario: Decimal | None
    valor: Decimal | None


def _to_decimal(val) -> Decimal | None:
    """Return Decimal or None for NaN / '-' / blank cells."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    # str() of a float is its shortest exact repr: 1.5 → "1.5", not 1.4999…
    s = str(val).strip()
    if s in ("", "-"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _to_date(val) -> date:
    """Parse a 'DD/MM/YYYY' cell."""
    if isinstance(val, datetime):
        return val.date()
    return datetime.strptime(str(val).strip(), "%d/%m/%Y").date()


def _texto(val) -> str | None:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return str(val).strip()


def linhas_do_relatorio(df: pd.DataFrame) -> list[LinhaB3]:
    """Rows of a B3 movimentação report, typed. Raises RelatorioB3Invalido."""
    if len(df.columns) != _N_COLUNAS:
        raise RelatorioB3Invalido(f"esperadas {_N_COLUNAS} colunas, encontradas {len(df.columns)}")
    linhas = []
    for valores in df.itertuples(index=False, name=None):
        sentido, data, movimentacao, produto, instituicao, quantidade, preco, valor = valores
        try:
            data = _to_date(data)
        except (TypeError, ValueError):
            raise RelatorioB3Invalido("data em formato inesperado") from None
        linhas.append(LinhaB3(
            sentido=str(sentido).strip(),
            data=data,
            movimentacao=str(movimentacao).strip(),
            produto_raw=str(produto).strip(),
            instituicao=_texto(instituicao),
            quantidade=_to_decimal(quantidade),
            preco_unitario=_to_decimal(preco),
            valor=_to_decimal(valor),
        ))
    return linhas


def ler_relatorio(conteudo: bytes) -> list[LinhaB3]:
    """Parse the bytes of a B3 .xlsx report (run it isolated: see importacao)."""
    import io

    import pandas as pd

    return linhas_do_relatorio(pd.read_excel(io.BytesIO(conteudo), header=0))


def carregar_arquivo(
    conn: Connection, investidor_id: int, df: pd.DataFrame, filename: str
) -> tuple[int, int]:
    """Load a B3 report given as a DataFrame. See carregar_linhas."""
    return carregar_linhas(conn, investidor_id, linhas_do_relatorio(df), filename)


def carregar_linhas(
    conn: Connection, investidor_id: int, linhas: list[LinhaB3], filename: str
) -> tuple[int, int]:
    """Insert rows from one B3 report for dates not yet loaded for this investidor.

    Deduplication is date-based: B3 reports are final for every date they
    cover, so if any row for a given date is already present for the
    investidor, all rows for that date are skipped. This is safe even when
    two files cover overlapping date ranges.

    Returns (inserted, skipped).
    """
    datas_existentes = {
        r["data"]
        for r in fetch_all(
            conn,
            "SELECT DISTINCT data FROM b3_movimentacoes WHERE investidor_id = :investidor_id",
            investidor_id=investidor_id,
        )
    }

    # Record the file and the period it covers (idempotent if run twice).
    datas = [r.data for r in linhas]
    execute(
        conn,
        """
        INSERT INTO b3_arquivos_processados (investidor_id, arquivo, periodo_inicio, periodo_fim)
        VALUES (:investidor_id, :arquivo, :inicio, :fim)
        ON CONFLICT (investidor_id, arquivo) DO UPDATE SET
            periodo_inicio = LEAST(b3_arquivos_processados.periodo_inicio, EXCLUDED.periodo_inicio),
            periodo_fim = GREATEST(b3_arquivos_processados.periodo_fim, EXCLUDED.periodo_fim)
        """,
        investidor_id=investidor_id,
        arquivo=filename,
        inicio=min(datas, default=None),
        fim=max(datas, default=None),
    )
    arquivo_id = scalar(
        conn,
        "SELECT id FROM b3_arquivos_processados WHERE investidor_id = :investidor_id AND arquivo = :arquivo",
        investidor_id=investidor_id,
        arquivo=filename,
    )

    inserted = 0
    skipped = 0
    for r in linhas:
        if r.data in datas_existentes:
            skipped += 1
            continue

        ativo_id = resolve_ou_criar_ativo(conn, r.produto_raw, doc_type="B3")

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
            sentido=r.sentido,
            data=r.data,
            movimentacao=r.movimentacao,
            produto_raw=r.produto_raw,
            ativo_id=ativo_id,
            instituicao=r.instituicao,
            quantidade=r.quantidade,
            preco_unitario=r.preco_unitario,
            valor=r.valor,
        )

        # Every bonus-share event gets a placeholder row in bonificacoes so the
        # user can later fill in custo_por_cota for correct average-cost calc.
        if r.movimentacao == "Bonificação em Ativos":
            execute(
                conn,
                "INSERT INTO bonificacoes (b3_movimentacao_id) VALUES (:id) ON CONFLICT DO NOTHING",
                id=mov_id,
            )

        inserted += 1

    return inserted, skipped


def main() -> None:
    import pandas as pd

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
