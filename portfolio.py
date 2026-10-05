"""Portfolio overview CLI.

Usage
-----
    .venv/bin/python portfolio.py              # open positions only
    .venv/bin/python portfolio.py --todos      # include closed positions
    .venv/bin/python portfolio.py --renda      # show income breakdown per ativo
    .venv/bin/python portfolio.py --cotacoes   # fetch live prices + unrealized P&L
    .venv/bin/python portfolio.py --historico  # monthly income timeline
"""

from __future__ import annotations

import sys
from datetime import date

from cotacoes import buscar_cotacoes
from database import connect, init_db
from posicoes import calcular_posicoes

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_SEP = "─"
_TIPO_LABEL = {
    "acao":               "AÇÕES",
    "fii":                "FIIs",
    "bdr":                "BDRs",
    "tesouro_direto":     "TESOURO DIRETO",
    "renda_fixa":         "RENDA FIXA",
    "recibo_subscricao":  "RECIBOS DE SUBSCRIÇÃO",
    "direito_subscricao": "DIREITOS DE SUBSCRIÇÃO",
}

_TIPO_ORDER = [
    "acao", "fii", "bdr", "tesouro_direto",
    "renda_fixa", "recibo_subscricao", "direito_subscricao",
]


def _brl(value: float | None) -> str:
    if value is None:
        return "—"
    return f"R$ {value:>12,.2f}"


def _yoc(pos: dict) -> str:
    """Yield on cost: total income received as % of current cost basis."""
    cost = pos["custo_total"]
    if cost <= 0:
        return f"{'—':>7}"
    return f"{pos['total_income'] / cost * 100:>6.1f}%"


def _pct(value: float | None) -> str:
    if value is None:
        return f"{'—':>8}"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:>6.1f}%"


def _pl(value: float | None) -> str:
    """Formatted unrealized P&L — green-ish sign prefix."""
    if value is None:
        return f"{'—':>15}"
    sign = "+" if value >= 0 else ""
    return f"R$ {sign}{value:>11,.2f}"


def _qty(value: float) -> str:
    if value == int(value):
        return f"{int(value):>8}"
    return f"{value:>8.4f}"


def _display_name(pos: dict) -> str:
    """Prefer ticker; fall back to nome for instruments without a ticker."""
    if pos["ticker"]:
        return pos["ticker"]
    nome = pos["nome"]
    # Truncate long names
    return nome[:30] if nome else f"id={pos['ativo_id']}"


# ---------------------------------------------------------------------------
# Table printers
# ---------------------------------------------------------------------------

_HDR_EQUITY = (
    f"  {'Ticker':<12}  {'Qtd':>8}  {'Preço Médio':>15}  "
    f"{'Custo Posição':>15}  {'Rendimentos':>15}  {'YoC%':>7}  Alertas"
)
_HDR_EQUITY_COTACOES = (
    f"  {'Ticker':<12}  {'Qtd':>8}  {'PM Custo':>15}  {'Cotação':>15}  "
    f"{'Valor Merc.':>15}  {'P&L Lat.':>16}  {'Ret.Total':>9}  "
    f"{'Rendimentos':>15}  {'YoC%':>7}  Alertas"
)
_HDR_RF = (
    f"  {'Nome':<30}  {'Vencimento':>12}  "
    f"{'Principal':>15}  {'Rendimentos':>15}  {'YoC%':>7}"
)
_HDR_SUBSCRICAO = (
    f"  {'Ticker':<12}  {'Qtd':>8}  {'Custo Exerc.':>15}"
)


def _print_equity_row(
    pos: dict,
    fechadas: bool = False,
    precos: dict[str, float | None] | None = None,
) -> None:
    status = ""
    if not pos["is_open"]:
        status = "[fechada] "
    if pos["tem_bonif_sem_custo"]:
        status += "⚠ bonif sem custo"
    if pos["custo_sem_origem"]:
        status += "⚠ custo desconhecido (transferência)"
    preco_medio = pos["preco_medio"]
    pm_str = f"R$ {preco_medio:>10,.2f}" if preco_medio is not None else f"{'—':>13}"

    if precos is not None:
        ticker = pos["ticker"]
        cotacao = precos.get(ticker) if ticker else None
        qty = pos["qty"]
        custo = pos["custo_total"]
        income = pos["total_income"]
        if cotacao is not None and qty > 0:
            valor_merc = cotacao * qty
            pl_lat = valor_merc - custo
            ret_total = ((valor_merc + income - custo) / custo * 100) if custo > 0 else None
        else:
            valor_merc = None
            pl_lat = None
            ret_total = None
        cotacao_str = f"R$ {cotacao:>10,.2f}" if cotacao is not None else f"{'—':>13}"
        print(
            f"  {_display_name(pos):<12}  {_qty(pos['qty'])}  {pm_str}  "
            f"{cotacao_str}  {_brl(valor_merc)}  {_pl(pl_lat)}  {_pct(ret_total)}  "
            f"{_brl(pos['total_income'])}  {_yoc(pos)}  {status}"
        )
    else:
        print(
            f"  {_display_name(pos):<12}  {_qty(pos['qty'])}  {pm_str}  "
            f"{_brl(pos['custo_total'])}  {_brl(pos['total_income'])}  {_yoc(pos)}  {status}"
        )


def _print_rf_row(pos: dict) -> None:
    status = "" if pos["is_open"] else " [resgatado]"
    venc = pos["vencimento"] or "—"
    print(
        f"  {_display_name(pos):<30}  {venc:>12}  "
        f"{_brl(pos['custo_total'])}  {_brl(pos['total_income'])}  {_yoc(pos)}{status}"
    )


def _print_subscricao_row(pos: dict) -> None:
    status = "" if pos["is_open"] else " [encerrado]"
    custo = _brl(pos["custo_total"]) if pos["custo_total"] else "—"
    print(
        f"  {_display_name(pos):<12}  {_qty(pos['qty'])}  {custo}{status}"
    )


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _totals(group: list[dict], precos: dict[str, float | None] | None = None) -> dict:
    custo  = sum(p["custo_total"]  for p in group if p["is_open"])
    income = sum(p["total_income"] for p in group)
    valor_merc = None
    if precos is not None:
        vm = 0.0
        for p in group:
            if not p["is_open"]:
                continue
            ticker = p["ticker"]
            cotacao = precos.get(ticker) if ticker else None
            if cotacao is not None:
                vm += cotacao * p["qty"]
            else:
                vm += p["custo_total"]  # fall back to cost for missing prices
        valor_merc = vm
    return {"custo": custo, "income": income, "valor_merc": valor_merc}


# ---------------------------------------------------------------------------
# Main display logic
# ---------------------------------------------------------------------------


def exibir_portfolio(
    mostrar_fechadas: bool = False,
    mostrar_renda: bool = False,
    com_cotacoes: bool = False,
) -> None:
    init_db()
    with connect() as conn:
        posicoes = calcular_posicoes(conn)

    precos: dict[str, float | None] | None = None
    if com_cotacoes:
        equity_tipos = {"acao", "fii", "bdr", "tesouro_direto"}
        tickers = [
            p["ticker"]
            for p in posicoes
            if p["tipo"] in equity_tipos and p["ticker"] and p["is_open"]
        ]
        if tickers:
            print(f"  Buscando cotações para {len(tickers)} ativos...", flush=True)
            precos = buscar_cotacoes(tickers)
            n_ok = sum(1 for v in precos.values() if v is not None)
            print(f"  {n_ok}/{len(tickers)} preços obtidos.\n")

    hoje = date.today().isoformat()
    width = 130 if precos is not None else 80
    print()
    print(f" PORTFÓLIO DE INVESTIMENTOS — {hoje} ".center(width, "═"))
    print()

    # Group by tipo
    by_tipo: dict[str, list[dict]] = {t: [] for t in _TIPO_ORDER}
    for p in posicoes:
        t = p["tipo"]
        if t in by_tipo:
            by_tipo[t].append(p)

    grand_custo     = 0.0
    grand_income    = 0.0
    grand_valor_merc: float | None = 0.0 if precos is not None else None

    for tipo in _TIPO_ORDER:
        group = by_tipo[tipo]
        if not group:
            continue

        to_show = group if mostrar_fechadas else [p for p in group if p["is_open"]]

        # Skip grupos with nothing to show
        if not to_show and not mostrar_fechadas:
            # Still count closed positions in totals
            t = _totals(group, precos)
            grand_income += t["income"]
            continue

        n_open   = sum(1 for p in group if p["is_open"])
        n_closed = len(group) - n_open

        label = _TIPO_LABEL.get(tipo, tipo.upper())
        suffix = f"  ({n_open} abertas" + (f", {n_closed} fechadas" if n_closed else "") + ")"
        header_line = f" {label}{suffix} "
        print(header_line)
        print(_SEP * width)

        if tipo in ("acao", "fii", "bdr", "tesouro_direto"):
            hdr = _HDR_EQUITY_COTACOES if precos is not None else _HDR_EQUITY
            print(hdr)
            print(_SEP * width)
            for p in sorted(to_show, key=lambda x: _display_name(x)):
                _print_equity_row(p, fechadas=not p["is_open"], precos=precos)
        elif tipo == "renda_fixa":
            print(_HDR_RF)
            print(_SEP * width)
            for p in sorted(to_show, key=lambda x: _display_name(x)):
                _print_rf_row(p)
        elif tipo in ("direito_subscricao", "recibo_subscricao"):
            print(_HDR_SUBSCRICAO)
            print(_SEP * width)
            for p in sorted(to_show, key=lambda x: _display_name(x)):
                _print_subscricao_row(p)

        t = _totals(group, precos)
        grand_custo  += t["custo"]
        grand_income += t["income"]
        if grand_valor_merc is not None and t["valor_merc"] is not None:
            grand_valor_merc += t["valor_merc"]

        print()
        if precos is not None and t["valor_merc"] is not None:
            pl = t["valor_merc"] - t["custo"]
            print(
                f"  {'Custo total aberto:':>35}  {_brl(t['custo'])}  "
                f"Valor mercado: {_brl(t['valor_merc'])}  P&L: {_pl(pl)}  "
                f"Rendimentos: {_brl(t['income'])}"
            )
        else:
            print(
                f"  {'Custo total aberto:':>35}  {_brl(t['custo'])}   "
                f"Rendimentos: {_brl(t['income'])}"
            )
        print()

    # Grand totals
    print("═" * width)
    if grand_valor_merc is not None:
        grand_pl = grand_valor_merc - grand_custo
        grand_ret = (
            (grand_valor_merc + grand_income - grand_custo) / grand_custo * 100
            if grand_custo > 0 else None
        )
        print(
            f"  {'TOTAL INVESTIDO (posições abertas):':>35}  {_brl(grand_custo)}  "
            f"Valor mercado: {_brl(grand_valor_merc)}  P&L Lat.: {_pl(grand_pl)}  "
            f"Ret.Total: {_pct(grand_ret)}  Rendimentos: {_brl(grand_income)}"
        )
    else:
        print(
            f"  {'TOTAL INVESTIDO (posições abertas):':>35}  {_brl(grand_custo)}   "
            f"Rendimentos: {_brl(grand_income)}"
        )
    print()

    if mostrar_renda:
        _exibir_renda_detalhada(posicoes)


def _exibir_renda_detalhada(posicoes: list[dict]) -> None:
    """Print a breakdown of income by ativo, sorted descending by total."""
    com_renda = [p for p in posicoes if p["total_income"] > 0]
    if not com_renda:
        print("Nenhum rendimento registrado.")
        return

    com_renda.sort(key=lambda p: p["total_income"], reverse=True)

    width = 80
    print(f" RENDIMENTOS RECEBIDOS ".center(width, "═"))
    print()
    hdr = f"  {'Ativo':<14}  {'Tipo':<14}  {'Rendimento':>14}  {'Dividendo':>14}  {'JCP':>14}  {'Jrs RF':>14}  {'Total':>14}"
    print(hdr)
    print(_SEP * width)
    for p in com_renda:
        nome = _display_name(p)
        print(
            f"  {nome:<14}  {p['tipo']:<14}  "
            f"{_brl(p['rendimentos'])}  {_brl(p['dividendos'])}  "
            f"{_brl(p['jcp'])}  {_brl(p['pagamento_juros'])}  {_brl(p['total_income'])}"
        )

    total = sum(p["total_income"] for p in com_renda)
    print(_SEP * width)
    print(f"  {'TOTAL':>42}  {_brl(total)}")
    print()


def _exibir_historico_renda() -> None:
    """Monthly income timeline from b3_movimentacoes."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m', data)                                           AS mes,
                SUM(CASE WHEN movimentacao = 'Rendimento'
                         THEN COALESCE(valor, 0) ELSE 0 END)                     AS rendimento,
                SUM(CASE WHEN movimentacao = 'Dividendo'
                         THEN  COALESCE(valor, 0)
                         WHEN movimentacao = 'Dividendo - Cancelado'
                         THEN -COALESCE(valor, 0)
                         ELSE 0 END)                                              AS dividendo,
                SUM(CASE WHEN movimentacao = 'Juros Sobre Capital Próprio'
                         THEN COALESCE(valor, 0) ELSE 0 END)                     AS jcp,
                SUM(CASE WHEN movimentacao = 'PAGAMENTO DE JUROS'
                         THEN COALESCE(valor, 0) ELSE 0 END)                     AS jrs_rf,
                SUM(CASE
                    WHEN movimentacao IN (
                        'Rendimento', 'Dividendo',
                        'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
                    ) THEN  COALESCE(valor, 0)
                    WHEN movimentacao = 'Dividendo - Cancelado'
                    THEN -COALESCE(valor, 0)
                    ELSE 0
                END)                                                              AS total
            FROM b3_movimentacoes
            WHERE movimentacao IN (
                'Rendimento', 'Dividendo', 'Dividendo - Cancelado',
                'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
            )
              AND ativo_id IS NOT NULL
            GROUP BY mes
            ORDER BY mes
            """
        ).fetchall()

    if not rows:
        print("Nenhum rendimento registrado.")
        return

    width = 100
    print()
    print(" HISTÓRICO DE RENDA MENSAL ".center(width, "═"))
    print()

    hdr = (
        f"  {'Mês':>7}  {'Rendimento':>14}  {'Dividendo':>14}  "
        f"{'JCP':>14}  {'Jrs RF':>14}  {'Total':>14}  Gráfico"
    )
    print(hdr)
    print(_SEP * width)

    max_total = max(r["total"] for r in rows) or 1.0
    bar_width = 20
    current_year = None
    year_total = 0.0
    grand_total = 0.0

    def _print_year_subtotal(year: str, yt: float) -> None:
        print(
            f"  {'─'*7}  {'─'*14}  {'─'*14}  {'─'*14}  {'─'*14}  {'─'*14}"
        )
        print(
            f"  {year + ' total':>7}  {'':>14}  {'':>14}  "
            f"{'':>14}  {'':>14}  R$ {yt:>11,.2f}"
        )
        print()

    for r in rows:
        mes = r["mes"]
        year = mes[:4]

        if current_year is not None and year != current_year:
            _print_year_subtotal(current_year, year_total)
            year_total = 0.0

        current_year = year
        total = r["total"]
        year_total += total
        grand_total += total

        bar_len = int(round(total / max_total * bar_width))
        bar = "█" * bar_len

        def _mc(v: float) -> str:
            return f"R$ {v:>8,.2f}" if v else f"{'—':>14}"

        print(
            f"  {mes:>7}  {_mc(r['rendimento']):>14}  {_mc(r['dividendo']):>14}  "
            f"{_mc(r['jcp']):>14}  {_mc(r['jrs_rf']):>14}  "
            f"R$ {total:>11,.2f}  {bar}"
        )

    if current_year:
        _print_year_subtotal(current_year, year_total)

    n_months = len(rows)
    media = grand_total / n_months if n_months else 0.0
    print(_SEP * width)
    print(
        f"  {'TOTAL ACUMULADO':>7}  {'':>14}  {'':>14}  {'':>14}  {'':>14}  "
        f"R$ {grand_total:>11,.2f}"
    )
    print(
        f"  {'MÉDIA/MÊS':>7}  {'':>14}  {'':>14}  {'':>14}  {'':>14}  "
        f"R$ {media:>11,.2f}"
    )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mostrar_fechadas = "--todos"     in sys.argv
    mostrar_renda    = "--renda"     in sys.argv
    com_cotacoes     = "--cotacoes"  in sys.argv
    mostrar_hist     = "--historico" in sys.argv

    if mostrar_hist:
        _exibir_historico_renda()
        return

    exibir_portfolio(
        mostrar_fechadas=mostrar_fechadas,
        mostrar_renda=mostrar_renda,
        com_cotacoes=com_cotacoes,
    )


if __name__ == "__main__":
    main()
