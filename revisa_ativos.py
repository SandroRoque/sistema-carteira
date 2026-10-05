"""Interactive review loop for unconfirmed ativos.

Usage:
    .venv/bin/python revisa_ativos.py
"""

from __future__ import annotations

import os
import sys

from database import connect

# Fields the user can edit (in display order)
_EDIT_FIELDS = [
    "tipo",
    "subtipo",
    "ticker",
    "nome",
    "cnpj_emissor",
    "emissor",
    "indexador",
    "taxa_prefixada",
    "percentual_do_indexador",
    "emissao",
    "vencimento",
]

_TIPOS = ["acao", "fii", "bdr", "renda_fixa", "tesouro_direto", "desconhecido"]


def _clear() -> None:
    os.system("clear")


def _fetch_unreviewed(conn) -> list:
    return conn.execute(
        "SELECT * FROM ativos WHERE revisado = 0 ORDER BY tipo, nome"
    ).fetchall()


def _fetch_aliases(conn, ativo_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT raw_text FROM ticker_aliases WHERE ativo_id = ? ORDER BY raw_text",
        (ativo_id,),
    ).fetchall()
    return [r["raw_text"] for r in rows]


def _fetch_usage(conn, ativo_id: int) -> tuple[int, int]:
    neg = conn.execute(
        "SELECT COUNT(*) FROM negociacoes WHERE ativo_id = ?", (ativo_id,)
    ).fetchone()[0]
    b3 = conn.execute(
        "SELECT COUNT(*) FROM b3_movimentacoes WHERE ativo_id = ?", (ativo_id,)
    ).fetchone()[0]
    return neg, b3


def _show(ativo, aliases: list[str], usage: tuple[int, int], idx: int, total: int) -> None:
    _clear()
    neg, b3 = usage
    bar = "═" * 54
    print(f"╔{bar}╗")
    print(f"║  Ativo {idx}/{total}{' ' * (46 - len(str(idx)) - len(str(total)))}║")
    print(f"╚{bar}╝")

    def row(label: str, value) -> None:
        if value is not None and value != "":
            print(f"  {label:<28} {value}")

    row("id", ativo["id"])
    row("tipo", ativo["tipo"])
    row("subtipo", ativo["subtipo"])
    row("ticker", ativo["ticker"])
    row("nome", ativo["nome"])
    row("emissor", ativo["emissor"])
    row("cnpj_emissor", ativo["cnpj_emissor"])
    row("indexador", ativo["indexador"])
    row("taxa_prefixada", ativo["taxa_prefixada"])
    row("percentual_do_indexador", ativo["percentual_do_indexador"])
    row("emissao", ativo["emissao"])
    row("vencimento", ativo["vencimento"])

    print()
    print(f"  negociacoes: {neg}   b3_movimentacoes: {b3}")
    print()

    if aliases:
        print("  Aliases:")
        for alias in aliases:
            print(f"    · {alias}")
        print()

    print("─" * 56)
    print("  [Enter/c] confirmar   [e] editar   [s] pular   [q] sair")


def _edit(conn, ativo) -> bool:
    """Prompt the user to edit fields.  Returns True if any changes were saved."""
    print("\n  Editar (Enter para manter, '-' para apagar, Ctrl+C para cancelar)\n")
    updates: dict[str, object] = {}

    try:
        for field in _EDIT_FIELDS:
            current = ativo[field]
            hint = f"  {field} [{current!r}]: "
            val = input(hint).strip()
            if val == "":
                continue  # keep current
            new_val: object = None if val == "-" else val
            if new_val != current:
                updates[field] = new_val
    except KeyboardInterrupt:
        print("\n  (cancelado — nenhuma alteração salva)")
        return False

    if not updates:
        print("  (sem alterações)")
        return False

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [ativo["id"]]
    conn.execute(f"UPDATE ativos SET {set_clause} WHERE id = ?", params)
    conn.commit()

    print(f"\n  Salvo: {updates}")
    return True


def main() -> None:
    with connect() as conn:
        ativos = _fetch_unreviewed(conn)
        total = len(ativos)

        if not total:
            print("Nenhum ativo pendente de revisão.")
            return

        print(f"{total} ativos pendentes de revisão.")
        idx = 0

        while idx < len(ativos):
            ativo = ativos[idx]
            aliases = _fetch_aliases(conn, ativo["id"])
            usage = _fetch_usage(conn, ativo["id"])

            _show(ativo, aliases, usage, idx + 1, total)

            try:
                choice = input("  → ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nInterrompido.")
                break

            if choice in ("", "c"):
                conn.execute(
                    "UPDATE ativos SET revisado = 1 WHERE id = ?", (ativo["id"],)
                )
                conn.commit()
                idx += 1

            elif choice == "e":
                changed = _edit(conn, ativo)
                if changed:
                    # reload the ativo so the next display reflects edits
                    updated = conn.execute(
                        "SELECT * FROM ativos WHERE id = ?", (ativo["id"],)
                    ).fetchone()
                    ativos[idx] = updated
                # ask once more whether to confirm or skip
                try:
                    print("\n  [Enter/c] confirmar   [s] pular")
                    confirm = input("  → ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    break
                if confirm in ("", "c"):
                    conn.execute(
                        "UPDATE ativos SET revisado = 1 WHERE id = ?", (ativo["id"],)
                    )
                    conn.commit()
                    idx += 1
                # else: stay on same ativo (loop redisplays)

            elif choice == "s":
                idx += 1

            elif choice == "q":
                break

        remaining = conn.execute(
            "SELECT COUNT(*) FROM ativos WHERE revisado = 0"
        ).fetchone()[0]
        _clear()
        print(f"Sessão encerrada.  Ativos pendentes restantes: {remaining}")


if __name__ == "__main__":
    main()
