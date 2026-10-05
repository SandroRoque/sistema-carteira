"""Reconciliation report — surfaces data quality issues in the portfolio DB.

Checks performed
----------------
1. Ativos com B3 mas sem nota de corretagem
   Equity/FII/BDR ativos that have income or corporate-action events in
   b3_movimentacoes but no negociacoes entry (missing purchase PDFs).

2. Bonificações sem custo_por_cota
   Bonus-share events where the declared cost is not yet filled in;
   these skew the weighted average cost of the position.

3. Ativos não revisados
   Rows in ativos with revisado=0 (auto-created, not yet confirmed).

4. Posições com custo desconhecido
   Equity positions that arrived via custody transfer only (no purchase
   note), so the cost basis is R$0.

5. Qty divergence: negociacoes vs B3 transfers
   For ativos that appear in both negociacoes and Transferência events,
   checks that the final position makes sense (positive qty).

Usage:
    .venv/bin/python reconcilia.py
"""

from __future__ import annotations

from sqlalchemy import Connection

from contas import investidor_do_cli
from database import connect, fetch_all
from posicoes import calcular_posicoes

_SEP = "─" * 80
_OK  = "✓"
_WAR = "⚠"
_ERR = "✗"


def _check_sem_negociacoes(conn: Connection, investidor_id: int) -> list[dict]:
    """Equity ativos with income/corporate B3 events but no negociacoes."""
    rows = fetch_all(
        conn,
        """
        SELECT
            a.id,
            a.ticker,
            a.tipo,
            COUNT(DISTINCT bm.id)  AS b3_events,
            SUM(CASE WHEN bm.movimentacao IN (
                'Rendimento','Dividendo','Juros Sobre Capital Próprio',
                'PAGAMENTO DE JUROS','Bonificação em Ativos'
            ) THEN 1 ELSE 0 END)   AS income_events
        FROM ativos a
        JOIN b3_movimentacoes bm ON bm.ativo_id = a.id AND bm.investidor_id = :investidor_id
        LEFT JOIN negociacoes n   ON n.ativo_id  = a.id AND n.investidor_id  = :investidor_id
        WHERE n.id IS NULL
          AND a.tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto')
          AND bm.movimentacao NOT IN (
              'Transferência - Liquidação', 'Atualização',
              'COMPRA / VENDA', 'Compra', 'Resgate', 'VENCIMENTO'
          )
        GROUP BY a.id
        ORDER BY b3_events DESC
        """,
        investidor_id=investidor_id,
    )
    return [dict(r) for r in rows]


def _check_bonif_sem_custo(conn: Connection, investidor_id: int) -> list[dict]:
    """Bonus-share events with NULL custo_por_cota."""
    rows = fetch_all(
        conn,
        """
        SELECT
            a.ticker,
            bm.data,
            bm.quantidade,
            b.custo_por_cota
        FROM bonificacoes b
        JOIN b3_movimentacoes bm ON bm.id = b.b3_movimentacao_id
        JOIN ativos a            ON a.id  = bm.ativo_id
        WHERE bm.investidor_id = :investidor_id
          AND b.custo_por_cota IS NULL
        ORDER BY a.ticker, bm.data
        """,
        investidor_id=investidor_id,
    )
    return [dict(r) for r in rows]


def _check_nao_revisados(conn: Connection, investidor_id: int) -> list[dict]:
    """Ativos used by the investidor that are still unreviewed."""
    rows = fetch_all(
        conn,
        """
        SELECT id, ticker, tipo, nome FROM ativos
        WHERE NOT revisado
          AND id IN (
              SELECT ativo_id FROM negociacoes WHERE investidor_id = :investidor_id
              UNION
              SELECT ativo_id FROM b3_movimentacoes WHERE investidor_id = :investidor_id
          )
        ORDER BY tipo, ticker
        """,
        investidor_id=investidor_id,
    )
    return [dict(r) for r in rows]


def _check_custo_desconhecido(posicoes: list[dict]) -> list[dict]:
    """Open positions with no cost basis (custody-transfer origin)."""
    return [p for p in posicoes if p["is_open"] and p["custo_sem_origem"]]


def _check_qty_negativa(posicoes: list[dict]) -> list[dict]:
    """Positions with negative quantity — indicates data inconsistency."""
    return [p for p in posicoes if p["qty"] < -0.001]


def _section(title: str) -> None:
    print()
    print(f" {title} ".center(80, "─"))
    print()


def main() -> None:
    with connect() as conn:
        investidor_id = investidor_do_cli(conn)
        sem_neg    = _check_sem_negociacoes(conn, investidor_id)
        bonif_pend = _check_bonif_sem_custo(conn, investidor_id)
        nao_rev    = _check_nao_revisados(conn, investidor_id)
        posicoes   = calcular_posicoes(conn, investidor_id)

    custo_desc = _check_custo_desconhecido(posicoes)
    qty_neg    = _check_qty_negativa(posicoes)

    # Check #1 is a superset of check #4: if an ativo has no purchase note AND no
    # cost basis, check #4 already surfaces it — no need to double-flag it here.
    custo_desc_tickers = {p["ticker"] for p in custo_desc if p["ticker"]}
    sem_neg = [r for r in sem_neg if r["ticker"] not in custo_desc_tickers]

    total_issues = len(sem_neg) + len(bonif_pend) + len(nao_rev) + len(custo_desc) + len(qty_neg)

    print()
    print(" RECONCILIAÇÃO DA CARTEIRA ".center(80, "═"))
    print()

    # ── 1. Ativos sem nota de corretagem ──────────────────────────────────
    _section("1. Ativos com eventos B3 mas sem nota de corretagem")
    if not sem_neg:
        print(f"  {_OK}  Nenhum.")
    else:
        print(f"  {_WAR}  {len(sem_neg)} ativo(s) encontrado(s):")
        print()
        print(f"  {'Ticker':<12}  {'Tipo':<14}  {'Eventos B3':>10}  {'c/ Renda':>8}")
        print("  " + "─" * 48)
        for r in sem_neg:
            print(
                f"  {r['ticker'] or '—':<12}  {r['tipo']:<14}  "
                f"{r['b3_events']:>10}  {r['income_events']:>8}"
            )
        print()
        print("  → Verifique se as notas de corretagem desses ativos estão")
        print("    no diretório NOTAS_DIR e rode: python carrega_notas.py")

    # ── 2. Bonificações sem custo ─────────────────────────────────────────
    _section("2. Bonificações pendentes de custo_por_cota")
    if not bonif_pend:
        print(f"  {_OK}  Nenhuma.")
    else:
        print(f"  {_WAR}  {len(bonif_pend)} bonificação(ões) sem custo informado:")
        print()
        print(f"  {'Ticker':<12}  {'Data':>12}  {'Qtd Bonif':>10}")
        print("  " + "─" * 38)
        for b in bonif_pend:
            print(f"  {b['ticker']:<12}  {b['data'].isoformat():>12}  {b['quantidade']:>10.4f}")
        print()
        print("  → Execute: python revisa_bonificacoes.py")

    # ── 3. Ativos não revisados ───────────────────────────────────────────
    _section("3. Ativos não revisados")
    if not nao_rev:
        print(f"  {_OK}  Todos os ativos revisados.")
    else:
        print(f"  {_WAR}  {len(nao_rev)} ativo(s) pendente(s):")
        print()
        for a in nao_rev:
            print(f"  id={a['id']}  {a['ticker'] or '—':<12}  {a['tipo']:<14}  {a['nome'] or ''}")
        print()
        print("  → Execute: python revisa_ativos.py")

    # ── 4. Posições com custo desconhecido ────────────────────────────────
    _section("4. Posições abertas com custo desconhecido (transferência)")
    if not custo_desc:
        print(f"  {_OK}  Nenhuma.")
    else:
        print(f"  {_WAR}  {len(custo_desc)} posição(ões) sem custo de aquisição:")
        print()
        for p in custo_desc:
            ticker = p["ticker"] or p["nome"][:20]
            print(f"  {ticker:<20}  qty={p['qty']:.4f}  (transferência sem nota)")
        print()
        print("  → Adicione manualmente uma negociacoes entrada com o custo correto.")

    # ── 5. Quantidade negativa ────────────────────────────────────────────
    _section("5. Posições com quantidade negativa")
    if not qty_neg:
        print(f"  {_OK}  Nenhuma.")
    else:
        print(f"  {_ERR}  {len(qty_neg)} posição(ões) com qty < 0 — inconsistência de dados:")
        print()
        for p in qty_neg:
            ticker = p["ticker"] or p["nome"][:20]
            print(f"  {ticker:<20}  qty={p['qty']:.4f}")

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("═" * 80)
    if total_issues == 0:
        print(f"  {_OK}  Tudo OK — nenhum problema encontrado.")
    else:
        print(f"  {_WAR}  {total_issues} problema(s) encontrado(s). Ver detalhes acima.")
    print()


if __name__ == "__main__":
    main()
