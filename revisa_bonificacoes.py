"""Interactive review loop for bonus-share events lacking a cost-per-share.

When a company issues bonus shares (Bonificação em Ativos), each new share has
a cost that the company declares.  B3 records the event but not the declared
cost; the user must supply it.  This script walks through each pending entry
and prompts for the custo_por_cota value.

Usage:
    .venv/bin/python revisa_bonificacoes.py
"""

from __future__ import annotations

import os

from contas import investidor_do_cli
from database import connect_sistema, execute, fetch_all

_SEP = "─" * 60


def _clear() -> None:
    os.system("clear")


def _fetch_pending(conn, investidor_id: int) -> list:
    return fetch_all(
        conn,
        """
        SELECT
            b.b3_movimentacao_id,
            b.custo_por_cota,
            bm.data,
            bm.quantidade,
            a.ticker,
            a.nome
        FROM bonificacoes b
        JOIN b3_movimentacoes bm ON bm.id = b.b3_movimentacao_id
        JOIN ativos a ON a.id = bm.ativo_id
        WHERE bm.investidor_id = :investidor_id
          AND b.custo_por_cota IS NULL
        ORDER BY a.ticker, bm.data
        """,
        investidor_id=investidor_id,
    )


def _show(row, idx: int, total: int) -> None:
    _clear()
    bar = "═" * 54
    print(f"╔{bar}╗")
    print(f"║  Bonificação {idx}/{total}{' ' * (40 - len(str(idx)) - len(str(total)))}║")
    print(f"╚{bar}╝")
    print()
    print(f"  Ativo         {row['ticker'] or row['nome']}")
    print(f"  Data          {row['data']}")
    print(f"  Quantidade    {row['quantidade']}")
    print()
    print(_SEP)
    print("  Informe o custo declarado por cota (R$).")
    print("  Geralmente publicado no comunicado de bonificação da RI.")
    print("  [Enter] pular   [q] sair")
    print(_SEP)


def main() -> None:
    with connect_sistema() as conn:
        rows = _fetch_pending(conn, investidor_do_cli(conn))
        total = len(rows)

        if not total:
            print("Nenhuma bonificação pendente de custo.")
            return

        print(f"{total} bonificações aguardando custo_por_cota.")
        idx = 0

        while idx < len(rows):
            row = rows[idx]
            _show(row, idx + 1, total)

            try:
                raw = input("  custo_por_cota R$ ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nInterrompido.")
                break

            if raw.lower() == "q":
                break

            if raw == "":
                idx += 1
                continue

            # Accept both comma and period as decimal separator
            raw = raw.replace(",", ".")
            try:
                custo = float(raw)
            except ValueError:
                print(f"  Valor inválido: {raw!r}. Tente novamente.")
                input("  [Enter] continuar")
                continue

            if custo < 0:
                print("  Custo não pode ser negativo. Tente novamente.")
                input("  [Enter] continuar")
                continue

            execute(
                conn,
                "UPDATE bonificacoes SET custo_por_cota = :custo WHERE b3_movimentacao_id = :id",
                custo=custo,
                id=row["b3_movimentacao_id"],
            )
            conn.commit()
            print(f"  Salvo: R$ {custo:.4f} / cota")
            idx += 1

    print("Revisão concluída.")


if __name__ == "__main__":
    main()
