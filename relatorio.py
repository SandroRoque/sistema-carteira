"""Generate a Markdown portfolio report with all data in formatted tables.

Usage
-----
    .venv/bin/python relatorio.py              # open positions only
    .venv/bin/python relatorio.py --cotacoes   # include live prices + P&L
    .venv/bin/python relatorio.py --todos      # include closed positions

Output
------
    reports/YYYY-MM-DD.md  (directory auto-created; overwritten if run again same day)
"""

from __future__ import annotations

from decimal import Decimal
import sys
from datetime import date, datetime
from pathlib import Path

from cotacoes import buscar_cotacoes
from sqlalchemy import Connection

from contas import investidor_do_cli
from database import connect_sistema, fetch_all, fetch_one
from posicoes import calcular_posicoes

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_TIPO_ORDER = [
    "acao", "fii", "bdr", "etf", "fundo", "tesouro_direto",
    "renda_fixa", "recibo_subscricao", "direito_subscricao",
]

_TIPO_LABEL = {
    "acao":               "Ações",
    "fii":                "FIIs",
    "bdr":                "BDRs",
    "etf":                "ETFs",
    "fundo":              "Outros fundos",
    "tesouro_direto":     "Tesouro Direto",
    "renda_fixa":         "Renda Fixa",
    "recibo_subscricao":  "Recibos de Subscrição",
    "direito_subscricao": "Direitos de Subscrição",
}


def _brl(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}"


def _brl_signed(v: Decimal | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"R$ {sign}{v:,.2f}"


def _pct(v: Decimal | None, sign: bool = False) -> str:
    if v is None:
        return "—"
    prefix = "+" if sign and v >= 0 else ""
    return f"{prefix}{v:.1f}%"


def _qty(v: Decimal) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.4f}"


def _display_name(pos: dict) -> str:
    if pos["ticker"]:
        return pos["ticker"]
    nome = pos["nome"]
    return nome[:30] if nome else f"id={pos['ativo_id']}"


def _yoc(pos: dict) -> str:
    cost = pos["custo_total"]
    if cost <= 0:
        return "—"
    return f"{pos['total_income'] / cost * 100:.1f}%"


def _alertas(pos: dict) -> str:
    parts = []
    if not pos["is_open"]:
        parts.append("fechada")
    if pos.get("tem_bonif_sem_custo"):
        parts.append("⚠ bonif sem custo")
    if pos.get("custo_sem_origem"):
        parts.append("⚠ custo desconhecido")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Markdown table builder
# ---------------------------------------------------------------------------

def _md_table(
    headers: list[str],
    rows: list[list],
    aligns: list[str] | None = None,
) -> str:
    """Return a GitHub-flavored markdown table string.

    aligns: list of 'l' (left), 'r' (right), 'c' (center) per column.
    """
    if not rows:
        return "_Nenhum dado._\n"

    if aligns is None:
        aligns = ["l"] * len(headers)

    sep_map = {"l": ":---", "r": "---:", "c": ":---:"}
    seps = [sep_map.get(a, "---") for a in aligns]

    def _cell(v: object) -> str:
        return str(v).replace("|", "\\|")

    lines = [
        "| " + " | ".join(_cell(h) for h in headers) + " |",
        "| " + " | ".join(seps) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Section: Executive summary
# ---------------------------------------------------------------------------

def _section_resumo(
    posicoes: list[dict],
    precos: dict[str, Decimal | None] | None,
) -> str:
    with_prices = precos is not None

    group_data: dict[str, dict] = {}
    for pos in posicoes:
        tipo = pos["tipo"]
        if tipo not in _TIPO_ORDER:
            continue
        if tipo not in group_data:
            group_data[tipo] = {
                "custo": 0, "valor_merc": 0,
                "income": 0, "n_open": 0, "n_closed": 0,
            }
        g = group_data[tipo]
        if pos["is_open"]:
            g["n_open"] += 1
            g["custo"] += pos["custo_total"]
            if with_prices:
                cotacao = precos.get(pos["ticker"]) if pos["ticker"] else None
                g["valor_merc"] += (cotacao * pos["qty"]) if cotacao else pos["custo_total"]
            else:
                g["valor_merc"] += pos["custo_total"]
        else:
            g["n_closed"] += 1
        g["income"] += pos["total_income"]

    if with_prices:
        headers = ["Categoria", "Abertas", "Custo Investido", "Valor Mercado",
                   "P&L Latente", "Rendimentos", "YoC%"]
        aligns  = ["l", "r", "r", "r", "r", "r", "r"]
    else:
        headers = ["Categoria", "Abertas", "Custo Investido", "Rendimentos", "YoC%"]
        aligns  = ["l", "r", "r", "r", "r"]

    rows = []
    grand_custo = 0
    grand_vm    = 0
    grand_inc   = 0

    for tipo in _TIPO_ORDER:
        g = group_data.get(tipo)
        if not g:
            continue
        custo = g["custo"]
        vm    = g["valor_merc"]
        inc   = g["income"]
        yoc   = f"{inc / custo * 100:.1f}%" if custo > 0 else "—"
        grand_custo += custo
        grand_vm    += vm
        grand_inc   += inc

        if with_prices:
            pl = vm - custo
            rows.append([
                _TIPO_LABEL[tipo], str(g["n_open"]),
                _brl(custo), _brl(vm), _brl_signed(pl), _brl(inc), yoc,
            ])
        else:
            rows.append([
                _TIPO_LABEL[tipo], str(g["n_open"]),
                _brl(custo), _brl(inc), yoc,
            ])

    grand_yoc = f"{grand_inc / grand_custo * 100:.1f}%" if grand_custo > 0 else "—"
    if with_prices:
        grand_pl  = grand_vm - grand_custo
        grand_ret = (
            f"{(grand_vm + grand_inc - grand_custo) / grand_custo * 100:.1f}%"
            if grand_custo > 0 else "—"
        )
        rows.append([
            "**TOTAL**", "—",
            f"**{_brl(grand_custo)}**",
            f"**{_brl(grand_vm)}**",
            f"**{_brl_signed(grand_pl)}**",
            f"**{_brl(grand_inc)}**",
            f"**{grand_yoc}**",
        ])
    else:
        rows.append([
            "**TOTAL**", "—",
            f"**{_brl(grand_custo)}**",
            f"**{_brl(grand_inc)}**",
            f"**{grand_yoc}**",
        ])

    return "## Resumo Executivo\n\n" + _md_table(headers, rows, aligns)


# ---------------------------------------------------------------------------
# Section: Positions by tipo
# ---------------------------------------------------------------------------

def _section_posicoes(
    posicoes: list[dict],
    precos: dict[str, Decimal | None] | None,
    todos: bool,
) -> str:
    with_prices = precos is not None
    lines = ["## Posições"]

    by_tipo: dict[str, list[dict]] = {t: [] for t in _TIPO_ORDER}
    for p in posicoes:
        if p["tipo"] in by_tipo:
            by_tipo[p["tipo"]].append(p)

    for tipo in _TIPO_ORDER:
        group = by_tipo[tipo]
        if not group:
            continue

        to_show = group if todos else [p for p in group if p["is_open"]]
        if not to_show:
            continue

        n_open   = sum(1 for p in group if p["is_open"])
        n_closed = len(group) - n_open
        label    = _TIPO_LABEL[tipo]
        suffix   = f" ({n_open} abertas" + (f", {n_closed} fechadas" if n_closed else "") + ")"
        lines.append(f"\n### {label}{suffix}\n")

        sorted_pos = sorted(to_show, key=_display_name)

        if tipo in ("acao", "fii", "bdr", "etf", "fundo", "tesouro_direto"):
            if with_prices:
                headers = ["Ticker", "Qtd", "PM Custo", "Cotação", "Valor Merc.",
                           "P&L Lat.", "Ret. Total", "Rendimentos", "YoC%", "Alertas"]
                aligns  = ["l", "r", "r", "r", "r", "r", "r", "r", "r", "l"]
            else:
                headers = ["Ticker", "Qtd", "Preço Médio", "Custo Posição",
                           "Rendimentos", "YoC%", "Alertas"]
                aligns  = ["l", "r", "r", "r", "r", "r", "l"]

            rows = []
            for p in sorted_pos:
                pm_str = _brl(p["preco_medio"]) if p["preco_medio"] is not None else "—"
                if with_prices:
                    cotacao = precos.get(p["ticker"]) if p["ticker"] else None
                    qty     = p["qty"]
                    custo   = p["custo_total"]
                    inc     = p["total_income"]
                    if cotacao is not None and qty > 0:
                        vm  = cotacao * qty
                        pl  = vm - custo
                        ret = ((vm + inc - custo) / custo * 100) if custo > 0 else None
                    else:
                        vm = pl = ret = None
                    rows.append([
                        _display_name(p), _qty(p["qty"]),
                        pm_str, _brl(cotacao), _brl(vm),
                        _brl_signed(pl) if pl is not None else "—",
                        _pct(ret, sign=True),
                        _brl(p["total_income"]), _yoc(p), _alertas(p),
                    ])
                else:
                    rows.append([
                        _display_name(p), _qty(p["qty"]),
                        pm_str, _brl(p["custo_total"]),
                        _brl(p["total_income"]), _yoc(p), _alertas(p),
                    ])
            lines.append(_md_table(headers, rows, aligns))

        elif tipo == "renda_fixa":
            headers = ["Nome", "Vencimento", "Principal", "Rendimentos", "YoC%", "Status"]
            aligns  = ["l", "l", "r", "r", "r", "l"]
            rows = []
            for p in sorted_pos:
                rows.append([
                    _display_name(p),
                    p["vencimento"] or "—",
                    _brl(p["custo_total"]),
                    _brl(p["total_income"]),
                    _yoc(p),
                    "resgatado" if not p["is_open"] else "aberto",
                ])
            lines.append(_md_table(headers, rows, aligns))

        elif tipo in ("direito_subscricao", "recibo_subscricao"):
            headers = ["Ticker", "Qtd", "Custo Exerc.", "Status"]
            aligns  = ["l", "r", "r", "l"]
            rows = []
            for p in sorted_pos:
                rows.append([
                    _display_name(p), _qty(p["qty"]),
                    _brl(p["custo_total"]) if p["custo_total"] else "—",
                    "encerrado" if not p["is_open"] else "aberto",
                ])
            lines.append(_md_table(headers, rows, aligns))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section: Income breakdown per ativo
# ---------------------------------------------------------------------------

def _section_renda_por_ativo(posicoes: list[dict]) -> str:
    com_renda = sorted(
        [p for p in posicoes if p["total_income"] > 0],
        key=lambda p: p["total_income"],
        reverse=True,
    )
    if not com_renda:
        return "## Renda por Ativo\n\n_Nenhum rendimento registrado._\n"

    headers = ["Ativo", "Tipo", "Rendimento", "Dividendo", "JCP", "Jrs RF", "**Total**"]
    aligns  = ["l", "l", "r", "r", "r", "r", "r"]

    def _opt(v: Decimal) -> str:
        return _brl(v) if v else "—"

    rows = []
    for p in com_renda:
        rows.append([
            _display_name(p),
            _TIPO_LABEL.get(p["tipo"], p["tipo"]),
            _opt(p["rendimentos"]),
            _opt(p["dividendos"]),
            _opt(p["jcp"]),
            _opt(p["pagamento_juros"]),
            f"**{_brl(p['total_income'])}**",
        ])

    grand = sum(p["total_income"] for p in com_renda)
    rows.append(["**TOTAL**", "", "", "", "", "", f"**{_brl(grand)}**"])

    return "## Renda por Ativo\n\n" + _md_table(headers, rows, aligns)


# ---------------------------------------------------------------------------
# Section: Monthly income history
# ---------------------------------------------------------------------------

def _section_historico(conn: Connection, investidor_id: int) -> str:
    db_rows = fetch_all(
        conn,
        """
        SELECT
            to_char(data, 'YYYY-MM')                                           AS mes,
            SUM(CASE WHEN movimentacao = 'Rendimento'
                     THEN COALESCE(valor, 0) ELSE 0 END)                      AS rendimento,
            SUM(CASE WHEN movimentacao = 'Dividendo'
                     THEN  COALESCE(valor, 0)
                     WHEN movimentacao = 'Dividendo - Cancelado'
                     THEN -COALESCE(valor, 0)
                     ELSE 0 END)                                               AS dividendo,
            SUM(CASE WHEN movimentacao = 'Juros Sobre Capital Próprio'
                     THEN COALESCE(valor, 0) ELSE 0 END)                      AS jcp,
            SUM(CASE WHEN movimentacao = 'PAGAMENTO DE JUROS'
                     THEN COALESCE(valor, 0) ELSE 0 END)                      AS jrs_rf,
            SUM(CASE
                WHEN movimentacao IN (
                    'Rendimento', 'Dividendo',
                    'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
                ) THEN  COALESCE(valor, 0)
                WHEN movimentacao = 'Dividendo - Cancelado'
                THEN -COALESCE(valor, 0)
                ELSE 0
            END)                                                               AS total
        FROM b3_movimentacoes
        WHERE investidor_id = :investidor_id
          AND movimentacao IN (
            'Rendimento', 'Dividendo', 'Dividendo - Cancelado',
            'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
          )
          AND ativo_id IS NOT NULL
        GROUP BY mes
        ORDER BY mes
        """,
        investidor_id=investidor_id,
    )

    if not db_rows:
        return "## Histórico de Renda Mensal\n\n_Nenhum rendimento registrado._\n"

    headers = ["Mês", "Rendimento", "Dividendo", "JCP", "Jrs RF", "Total"]
    aligns  = ["l", "r", "r", "r", "r", "r"]

    def _opt(v: Decimal) -> str:
        return _brl(v) if v else "—"

    rows         = []
    current_year = None
    year_total   = 0
    grand_total  = 0
    n_months     = 0

    for r in db_rows:
        mes  = r["mes"]
        year = mes[:4]
        tot  = r["total"]

        if current_year is not None and year != current_year:
            rows.append([f"**{current_year} subtotal**", "", "", "", "", f"**{_brl(year_total)}**"])
            year_total = 0

        current_year  = year
        year_total   += tot
        grand_total  += tot
        n_months     += 1

        rows.append([
            mes,
            _opt(r["rendimento"]),
            _opt(r["dividendo"]),
            _opt(r["jcp"]),
            _opt(r["jrs_rf"]),
            _brl(tot),
        ])

    if current_year:
        rows.append([f"**{current_year} subtotal**", "", "", "", "", f"**{_brl(year_total)}**"])

    media = grand_total / n_months if n_months else 0
    rows.append(["**Total acumulado**", "", "", "", "", f"**{_brl(grand_total)}**"])
    rows.append([f"**Média/mês** ({n_months} meses)", "", "", "", "", f"**{_brl(media)}**"])

    return "## Histórico de Renda Mensal\n\n" + _md_table(headers, rows, aligns)


# ---------------------------------------------------------------------------
# Section: IR / tax summary
# ---------------------------------------------------------------------------

def _section_ir(conn: Connection, investidor_id: int) -> str:
    jcp_rows = fetch_all(
        conn,
        """
        SELECT
            to_char(data, 'YYYY') AS ano,
            SUM(valor)            AS total_jcp
        FROM b3_movimentacoes
        WHERE investidor_id = :investidor_id
          AND movimentacao = 'Juros Sobre Capital Próprio'
          AND ativo_id IS NOT NULL
        GROUP BY ano
        ORDER BY ano
        """,
        investidor_id=investidor_id,
    )

    vendas_rows = fetch_all(
        conn,
        """
        SELECT n.data, n.ativo_id, a.ticker, a.nome, a.tipo,
               n.quantidade, n.valor_liquido
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE n.investidor_id = :investidor_id
          AND n.sentido = 'saida'
          AND a.tipo <> 'opcao'  -- options are not calculated yet
        ORDER BY n.data
        """,
        investidor_id=investidor_id,
    )

    lines = ["## Resumo IR / Impostos"]

    # JCP table
    lines.append("\n### JCP Recebido (15% retido na fonte)\n")
    if jcp_rows:
        jcp_headers = ["Ano", "Total Bruto", "IR Retido (15%)", "Declaração IRPF"]
        jcp_aligns  = ["l", "r", "r", "l"]
        jcp_data    = []
        for j in jcp_rows:
            bruto = j["total_jcp"]
            jcp_data.append([
                j["ano"], _brl(bruto), _brl(bruto * Decimal("0.15")),
                "Rendimentos sujeitos à tributação exclusiva",
            ])
        lines.append(_md_table(jcp_headers, jcp_data, jcp_aligns))
    else:
        lines.append("_Nenhum JCP registrado._\n")

    # Sales table
    lines.append("\n### Vendas de Renda Variável\n")
    if vendas_rows:
        _LIMITE = Decimal(20_000)
        v_headers = ["Data", "Ativo", "Tipo", "Qtd", "Receita", "Custo Base", "Ganho/Perda", "Situação"]
        v_aligns  = ["l", "l", "l", "r", "r", "r", "r", "l"]
        v_data    = []
        for v in vendas_rows:
            row_pm = fetch_one(
                conn,
                """
                SELECT SUM(quantidade) AS qty_total, SUM(valor_liquido) AS custo_total
                FROM negociacoes
                WHERE investidor_id = :investidor_id AND ativo_id = :ativo_id
                  AND sentido = 'entrada' AND data < :data
                """,
                investidor_id=investidor_id,
                ativo_id=v["ativo_id"],
                data=v["data"],
            )
            pm         = (row_pm["custo_total"] / row_pm["qty_total"]) if row_pm and row_pm["qty_total"] else None
            custo_base = (pm * v["quantidade"]) if pm is not None else None
            receita    = v["valor_liquido"]
            ganho      = (receita - custo_base) if custo_base is not None else None
            tipo       = v["tipo"]
            ticker     = v["ticker"] or (v["nome"] or "")[:20]

            if tipo in ("acao", "bdr") and receita <= _LIMITE:
                situacao = "Isento"
            elif tipo == "renda_fixa":
                situacao = "IR retido na fonte"
            elif ganho is not None and ganho <= 0:
                situacao = "Sem ganho"
            elif tipo in ("acao", "bdr", "etf"):
                situacao = "DARF 15%"
            elif tipo == "fii":
                situacao = "DARF 20%"
            else:
                situacao = "Verificar"

            v_data.append([
                v["data"], ticker, tipo, _qty(v["quantidade"]),
                _brl(receita), _brl(custo_base),
                _brl_signed(ganho) if ganho is not None else "—",
                situacao,
            ])
        lines.append(_md_table(v_headers, v_data, v_aligns))
    else:
        lines.append("_Nenhuma venda de renda variável registrada._\n")

    # Quick reference
    lines.append("""\n### Regras de Referência\n
| Tipo | Regra | Observação |
| :--- | :--- | :--- |
| Ações | Vendas ≤ R$20k/mês → isento; acima → DARF 15% | Compensar perdas acumuladas |
| FIIs | Sempre DARF 20% sobre ganho | Sem isenção de valor |
| BDRs | Vendas ≤ R$20k/mês → isento; acima → DARF 15% | Vigente desde 2023 |
| JCP | 15% retido na fonte | Declarar no IRPF |
| Dividendos | Isentos (PF) | Legislação atual |
| Rend. FII | Isentos (PF com menos de 10% das cotas, fundo com 100+ cotistas; Lei 11.033 art. 3º, §1º) | Verificar cada fundo |
| Renda Fixa | IR retido na fonte (22,5% → 15% regressivo) | Declarar no IRPF |
""")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def gerar_relatorio(todos: bool = False, com_cotacoes: bool = False) -> Path:
    """Build the markdown report and return the output path."""
    with connect_sistema() as conn:
        investidor_id = investidor_do_cli(conn)
        posicoes = calcular_posicoes(conn, investidor_id)

        precos: dict[str, Decimal | None] | None = None
        if com_cotacoes:
            equity_tipos = {"acao", "fii", "bdr", "etf", "fundo", "tesouro_direto"}
            tickers = [
                p["ticker"]
                for p in posicoes
                if p["tipo"] in equity_tipos and p["ticker"] and p["is_open"]
            ]
            if tickers:
                print(f"  Buscando cotações para {len(tickers)} ativos...", flush=True)
                precos = buscar_cotacoes(conn, tickers)
                n_ok = sum(1 for v in precos.values() if v is not None)
                print(f"  {n_ok}/{len(tickers)} preços obtidos.")

        hoje  = date.today().isoformat()
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        price_note = " · com cotações de mercado" if com_cotacoes else ""

        sections = [
            f"# Portfólio de Investimentos — {hoje}\n\n_Gerado em {agora}{price_note}_",
            _section_resumo(posicoes, precos),
            _section_posicoes(posicoes, precos, todos),
            _section_renda_por_ativo(posicoes),
            _section_historico(conn, investidor_id),
            _section_ir(conn, investidor_id),
        ]

    content = "\n\n---\n\n".join(sections) + "\n"

    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    out_path = reports_dir / f"{hoje}.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main() -> None:
    todos        = "--todos"    in sys.argv
    com_cotacoes = "--cotacoes" in sys.argv
    path = gerar_relatorio(todos=todos, com_cotacoes=com_cotacoes)
    print(f"  Relatório: {path}")


if __name__ == "__main__":
    main()
