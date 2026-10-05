"""Tax / IR tracking report.

Usage:
    .venv/bin/python imposto.py               # current year
    .venv/bin/python imposto.py --ano 2025    # specific year (useful for IRPF)
    .venv/bin/python imposto.py --todos       # all years

Brazilian IR rules applied
--------------------------
Ações (acao):
  - Monthly sales ≤ R$20.000 → exempt (isento)
  - Monthly sales  > R$20.000 → 15% on gains (swing trade)
  - Day trade: always 15% (not tracked here — requires intraday data)

FIIs (fii):
  - All sales → 20% on gains (no exemption)

BDRs (bdr):
  - Monthly sales ≤ R$20.000 → exempt  (same rule as ações from 2023)
  - Monthly sales  > R$20.000 → 15% on gains

Tesouro Direto / Renda Fixa:
  - IR withheld at source by broker/bank (regressiva: 22.5%→15%)
  - Shown for IRPF declaration reference only

JCP (Juros Sobre Capital Próprio):
  - 15% withholding already applied by company
  - Must appear in IRPF as "rendimentos sujeitos à tributação exclusiva"

Dividendos:
  - Currently exempt (Brazil legislation as of 2026)
  - FII rendimentos: exempt for PF investors

Cost-basis method: weighted average (custo médio ponderado), computed
from all negociacoes with sentido='entrada' prior to each sale date.
This is the method required by Receita Federal for variable income.
"""

from __future__ import annotations

import sys
from datetime import date

from database import connect, init_db

# Tax thresholds
_LIMITE_ISENCAO_ACOES = 20_000.0   # R$ / month
_LIMITE_ISENCAO_BDRS  = 20_000.0   # same rule since 2023

# Tax rates (%)
_ALIQUOTA_ACOES = 15.0
_ALIQUOTA_FII   = 20.0
_ALIQUOTA_BDR   = 15.0


def _custo_medio_antes_da_venda(conn, ativo_id: int, data_venda: str) -> float | None:
    """Weighted average cost per share from all purchases BEFORE the sale date."""
    row = conn.execute(
        """
        SELECT
            SUM(quantidade)    AS qty_total,
            SUM(valor_liquido) AS custo_total
        FROM negociacoes
        WHERE ativo_id = ?
          AND sentido = 'entrada'
          AND data < ?
        """,
        (ativo_id, data_venda),
    ).fetchone()
    if row is None or not row["qty_total"]:
        return None
    return row["custo_total"] / row["qty_total"]


def _buscar_vendas(conn, ano: int | None = None) -> list[dict]:
    """Return all sale transactions with cost-basis and gain/loss computed."""
    where = "n.sentido = 'saida'"
    params: list = []
    if ano is not None:
        where += " AND strftime('%Y', n.data) = ?"
        params.append(str(ano))

    rows = conn.execute(
        f"""
        SELECT
            n.id,
            n.ativo_id,
            n.data,
            a.ticker,
            a.nome,
            a.tipo,
            n.quantidade,
            n.valor_bruto,
            n.valor_liquido
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE {where}
        ORDER BY n.data
        """,
        params,
    ).fetchall()

    result = []
    for r in rows:
        pm = _custo_medio_antes_da_venda(conn, r["ativo_id"], r["data"])
        custo_venda = (pm * r["quantidade"]) if pm is not None else None
        ganho = (r["valor_liquido"] - custo_venda) if custo_venda is not None else None
        result.append({
            **dict(r),
            "pm_custo": pm,
            "custo_venda": custo_venda,
            "ganho": ganho,
        })
    return result


def _buscar_jcp_por_ano(conn) -> list[dict]:
    """JCP received per year (15% already withheld at source)."""
    return conn.execute(
        """
        SELECT
            strftime('%Y', data) AS ano,
            SUM(valor)           AS total_jcp
        FROM b3_movimentacoes
        WHERE movimentacao = 'Juros Sobre Capital Próprio'
          AND ativo_id IS NOT NULL
        GROUP BY ano
        ORDER BY ano
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Rendimentos recebidos (para Ficha de Rendimentos do IRPF)
# ---------------------------------------------------------------------------

_MOVS_RENDIMENTO = (
    "Dividendo",
    "Juros Sobre Capital Próprio",
    "Rendimento",
    "PAGAMENTO DE JUROS",
    "Leilão de Fração",
)

_CATEGORIA_INFO = {
    # categoria → (label, ficha IRPF, aliquota_retida)
    "dividendo":         ("Dividendos",          "Rend. Isentos cód. 09",                    None),
    "jcp":               ("JCP",                 "Rend. Tributação Exclusiva cód. 10 (15%)",  0.15),
    "rendimento_fii":    ("Rendimentos FII",      "Rend. Isentos cód. 26",                    None),
    "rendimento_outros": ("Outros rendimentos",   "Rend. Isentos / verificar",                None),
    "pagamento_juros":   ("Juros renda fixa",     "Rend. Tributado na Fonte (regressivo)",     None),
    "leilao_fracao":     ("Leilão de fração",     "Ganho de capital (renda variável)",         None),
}


def _cat(mov: str, tipo_ativo: str) -> str:
    if mov == "Dividendo":
        return "dividendo"
    if mov == "Juros Sobre Capital Próprio":
        return "jcp"
    if mov == "Rendimento":
        return "rendimento_fii" if tipo_ativo == "fii" else "rendimento_outros"
    if mov == "PAGAMENTO DE JUROS":
        return "pagamento_juros"
    if mov == "Leilão de Fração":
        return "leilao_fracao"
    return "rendimento_outros"


def _buscar_rendimentos_anuais(conn) -> list[dict]:
    """All income events grouped by year and category, including per-category totals."""
    rows = conn.execute(
        f"""
        SELECT
            strftime('%Y', m.data) AS ano,
            m.movimentacao,
            a.tipo                 AS tipo_ativo,
            SUM(m.valor)           AS total
        FROM b3_movimentacoes m
        JOIN ativos a ON a.id = m.ativo_id
        WHERE m.sentido = 'Credito'
          AND m.movimentacao IN ({','.join('?' for _ in _MOVS_RENDIMENTO)})
        GROUP BY ano, m.movimentacao, a.tipo
        ORDER BY ano, m.movimentacao
        """,
        _MOVS_RENDIMENTO,
    ).fetchall()

    # Aggregate into {ano: {categoria: total}}
    from collections import defaultdict
    by_year: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        c = _cat(r["movimentacao"], r["tipo_ativo"])
        by_year[r["ano"]][c] += r["total"] or 0.0

    return [{"ano": ano, **totals} for ano, totals in sorted(by_year.items())]


def _buscar_rendimentos_detalhe(conn, ano: int) -> list[dict]:
    """Per-ativo income breakdown for a given year, grouped by category."""
    rows = conn.execute(
        f"""
        SELECT
            COALESCE(a.ticker, a.nome) AS ativo,
            a.tipo                     AS tipo_ativo,
            m.movimentacao,
            SUM(m.valor)               AS total
        FROM b3_movimentacoes m
        JOIN ativos a ON a.id = m.ativo_id
        WHERE m.sentido = 'Credito'
          AND strftime('%Y', m.data) = ?
          AND m.movimentacao IN ({','.join('?' for _ in _MOVS_RENDIMENTO)})
        GROUP BY a.id, m.movimentacao
        ORDER BY m.movimentacao, total DESC
        """,
        (str(ano), *_MOVS_RENDIMENTO),
    ).fetchall()

    result = []
    for r in rows:
        result.append({
            "ativo":    r["ativo"],
            "categoria": _cat(r["movimentacao"], r["tipo_ativo"]),
            "total":    r["total"] or 0.0,
        })
    return result


def _buscar_vendas_mensais(conn, ano: int | None = None) -> list[dict]:
    """Monthly sales totals by tipo for exemption threshold analysis."""
    where = "n.sentido = 'saida' AND a.tipo IN ('acao', 'fii', 'bdr')"
    params: list = []
    if ano is not None:
        where += " AND strftime('%Y', n.data) = ?"
        params.append(str(ano))
    return conn.execute(
        f"""
        SELECT
            strftime('%Y-%m', n.data) AS mes,
            a.tipo,
            SUM(n.valor_liquido)      AS total_vendas,
            SUM(n.quantidade)         AS qty_total
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE {where}
        GROUP BY mes, a.tipo
        ORDER BY mes
        """,
        params,
    ).fetchall()


def _brl(v: float | None) -> str:
    if v is None:
        return f"{'—':>15}"
    return f"R$ {v:>12,.2f}"


def _pct(v: float | None) -> str:
    if v is None:
        return f"{'—':>7}"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:>6.1f}%"


def _aliquota(tipo: str) -> float | None:
    return {"acao": _ALIQUOTA_ACOES, "fii": _ALIQUOTA_FII, "bdr": _ALIQUOTA_BDR}.get(tipo)


def _exibir_rendimentos_secao(
    width: int,
    anuais: list[dict],
    detalhe_por_ano: dict[str, list[dict]],
) -> None:
    # ── Annual summary table ───────────────────────────────────────────────
    print(" RENDIMENTOS RECEBIDOS — RESUMO ANUAL ".center(width, "─"))
    print()
    cat_order = ["dividendo", "jcp", "rendimento_fii", "rendimento_outros",
                 "pagamento_juros", "leilao_fracao"]
    anos_disponiveis = [r["ano"] for r in anuais]

    # Header
    col_w = 16
    header = f"  {'Categoria':<26}" + "".join(f"  {a:>{col_w}}" for a in anos_disponiveis)
    print(header)
    print("─" * len(header))

    for cat in cat_order:
        label, _, _ = _CATEGORIA_INFO[cat]
        row_vals = []
        has_data = False
        for r in anuais:
            v = r.get(cat, 0.0)
            if v:
                has_data = True
            row_vals.append(v)
        if not has_data:
            continue
        row = f"  {label:<26}" + "".join(
            f"  {_brl(v):>{col_w}}" if v else f"  {'—':>{col_w}}" for v in row_vals
        )
        print(row)
        # JCP: show IR retido sub-row
        _, _, aliq = _CATEGORIA_INFO[cat]
        if aliq is not None:
            sub = f"  {'  └─ IR retido (15%)':<26}" + "".join(
                f"  {_brl(v * aliq):>{col_w}}" if v else f"  {'—':>{col_w}}"
                for v in row_vals
            )
            print(sub)

    # Totals row
    print("─" * len(header))
    totals_by_year = []
    for r in anuais:
        totals_by_year.append(sum(r.get(c, 0.0) for c in cat_order))
    total_row = f"  {'TOTAL':<26}" + "".join(
        f"  {_brl(t):>{col_w}}" for t in totals_by_year
    )
    print(total_row)
    print()

    # ── Per-year per-ativo detail ──────────────────────────────────────────
    for ano_str, detalhe in detalhe_por_ano.items():
        if not detalhe:
            continue

        print(f" DETALHE POR ATIVO — {ano_str} ".center(width, "─"))
        print()

        # Group by category
        from collections import defaultdict
        por_cat: dict[str, list[dict]] = defaultdict(list)
        for d in detalhe:
            por_cat[d["categoria"]].append(d)

        for cat in cat_order:
            items = por_cat.get(cat)
            if not items:
                continue
            label, ficha, aliq = _CATEGORIA_INFO[cat]
            print(f"  {label}  —  {ficha}")
            print(f"  {'Ativo':<16}  {'Total':>14}  {'IR retido':>14}")
            print("  " + "─" * 50)
            cat_total = 0.0
            for item in items:
                v = item["total"]
                cat_total += v
                ir_str = _brl(v * aliq) if aliq else f"{'—':>14}"
                print(f"  {item['ativo']:<16}  {_brl(v):>14}  {ir_str:>14}")
            ir_total = _brl(cat_total * aliq) if aliq else f"{'—':>14}"
            print("  " + "─" * 50)
            print(f"  {'Total':<16}  {_brl(cat_total):>14}  {ir_total:>14}")
            print()


def exibir_relatorio_ir(todos: bool = False, ano_override: int | None = None) -> None:
    ano_atual = date.today().year
    init_db()
    with connect() as conn:
        ano_filtro   = None if todos else (ano_override or ano_atual)
        vendas       = _buscar_vendas(conn, ano=ano_filtro)
        mens_vends   = _buscar_vendas_mensais(conn, ano=ano_filtro)
        rend_anuais  = _buscar_rendimentos_anuais(conn)
        if todos:
            anos_detalhe = [r["ano"] for r in rend_anuais]
        elif ano_override:
            anos_detalhe = [str(ano_override)]
        else:
            anos_detalhe = [str(ano_atual)]
        rend_detalhe = {
            a: _buscar_rendimentos_detalhe(conn, int(a))
            for a in anos_detalhe
        }

    ano_label = "todos os anos" if todos else str(ano_override or ano_atual)

    width = 110
    print()
    titulo = f" RELATÓRIO IR — {ano_label} "
    print(titulo.center(width, "═"))
    print()

    # ── Section 1: Sales ──────────────────────────────────────────────────
    print(" VENDAS (renda variável) ".center(width, "─"))
    print()
    if not vendas:
        print("  Nenhuma venda registrada no período.")
    else:
        hdr = (
            f"  {'Data':>10}  {'Ativo':>8}  {'Tipo':>10}  {'Qtd':>8}  "
            f"{'Receita':>15}  {'Custo Base':>15}  {'Ganho/Perda':>15}  "
            f"{'Alíquota':>9}  {'IR Estimado':>15}  Situação"
        )
        print(hdr)
        print("─" * width)

        # Group by month for exemption check
        from collections import defaultdict
        vendas_por_mes_tipo: dict[tuple, float] = defaultdict(float)
        for v in vendas:
            mes = v["data"][:7]
            vendas_por_mes_tipo[(mes, v["tipo"])] += v["valor_liquido"] or 0.0

        for v in vendas:
            tipo = v["tipo"]
            mes  = v["data"][:7]
            receita = v["valor_liquido"] or 0.0
            ganho   = v["ganho"]
            aliq    = _aliquota(tipo)

            # Determine exemption
            total_mes = vendas_por_mes_tipo.get((mes, tipo), 0.0)
            isento = False
            if tipo in ("acao", "bdr") and total_mes <= _LIMITE_ISENCAO_ACOES:
                isento = True

            if ganho is not None and aliq is not None and not isento and ganho > 0:
                ir_est = ganho * aliq / 100
            else:
                ir_est = None

            if isento:
                situacao = "ISENTO"
            elif ganho is not None and ganho <= 0:
                situacao = "sem ganho"
            elif ganho is None:
                situacao = "sem custo base"
            elif aliq is None:
                situacao = "verificar"
            else:
                situacao = f"DARF {aliq:.0f}%"

            ir_str = _brl(ir_est) if ir_est else f"{'—':>15}"
            ticker = v["ticker"] or (v["nome"] or "")[:8]
            print(
                f"  {v['data']:>10}  {ticker:>8}  {tipo:>10}  {v['quantidade']:>8.2f}  "
                f"{_brl(receita)}  {_brl(v['custo_venda'])}  {_brl(ganho)}  "
                f"{'—':>9}  {ir_str}  {situacao}"
            )

    print()

    # ── Section 2: Monthly sale totals vs R$20k threshold ─────────────────
    if mens_vends:
        print(" VENDAS MENSAIS × LIMITE DE ISENÇÃO ".center(width, "─"))
        print()
        print(f"  {'Mês':>7}  {'Tipo':>10}  {'Total Vendas':>15}  {'Limite':>10}  Situação")
        print("─" * 60)
        for m in mens_vends:
            tipo  = m["tipo"]
            total = m["total_vendas"]
            limite = _LIMITE_ISENCAO_ACOES if tipo in ("acao", "bdr") else None
            if limite is not None:
                situacao = "ISENTO" if total <= limite else "⚠ TRIBUTÁVEL"
            else:
                situacao = "TRIBUTÁVEL (FII)"
            lim_str = f"R$ {limite:>10,.2f}" if limite else f"{'n/a':>13}"
            print(f"  {m['mes']:>7}  {tipo:>10}  {_brl(total)}  {lim_str}  {situacao}")
        print()

    # ── Section 3: Rendimentos recebidos ─────────────────────────────────
    _exibir_rendimentos_secao(width, rend_anuais, rend_detalhe)

    # ── Section 4: Quick reference ────────────────────────────────────────
    print(" REFERÊNCIA RÁPIDA ".center(width, "─"))
    print()
    print("  Tipo            Regra                                          Obs")
    print("─" * width)
    rules = [
        ("Ações",       "Vendas ≤ R$20k/mês → ISENTO; acima → DARF 15%",    "compensar perdas acumuladas"),
        ("FIIs",        "Sempre DARF 20% sobre ganho",                       "sem isenção"),
        ("BDRs",        "Vendas ≤ R$20k/mês → ISENTO; acima → DARF 15%",    "vigente desde 2023"),
        ("JCP",         "15% retido na fonte",                               "declarar IRPF"),
        ("Dividendos",  "Isentos (PF)",                                      "legislação atual"),
        ("Rend. FII",   "Isentos (PF cota ≥ 10% fundo com 50+ cotistas)",    "verificar cada fundo"),
        ("Renda Fixa",  "IR retido na fonte (tabela regressiva 22.5%→15%)",  "declarar IRPF"),
    ]
    for tipo, regra, obs in rules:
        print(f"  {tipo:<16}  {regra:<52}  {obs}")
    print()


def main() -> None:
    todos = "--todos" in sys.argv
    # --ano YYYY  →  show detail for that year only
    ano_override: int | None = None
    if "--ano" in sys.argv:
        idx = sys.argv.index("--ano")
        if idx + 1 < len(sys.argv):
            try:
                ano_override = int(sys.argv[idx + 1])
            except ValueError:
                pass
    exibir_relatorio_ir(todos=todos, ano_override=ano_override)


if __name__ == "__main__":
    main()
