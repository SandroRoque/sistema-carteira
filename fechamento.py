"""Point-in-time portfolio position and annual activity report.

Usage:
    python fechamento.py 2025               # position at 31/12/2025 + 2025 activity
    python fechamento.py 2024               # position at 31/12/2024 + 2024 activity
    python fechamento.py --data 2025-06-30  # position at an arbitrary date

Columns
-------
Ticker       : asset identifier
Qtd          : quantity held at the closing date
Preço Médio  : weighted average cost per share as of closing date
Custo Total  : Preço Médio × Qtd  (cost of remaining position)
Compras      : R$ spent on purchases within the calendar year
Vendas       : R$ received from sales within the calendar year
Δ Qtd        : net change in qty through the year (from all sources)
"""
from __future__ import annotations

import sys
from datetime import date

from database import connect, init_db

_EQUITY_TIPOS = {"acao", "fii", "bdr", "tesouro_direto"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _brl(v: float | None, width: int = 14) -> str:
    if v is None or v == 0.0:
        return f"{'—':>{width}}"
    return f"R$ {v:>{width - 4},.2f}"


def _brl_signed(v: float, width: int = 10) -> str:
    if v == 0.0:
        return f"{'—':>{width}}"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:>{width - 1},.2f}"


def _qty_str(v: float, width: int = 8) -> str:
    if v == 0.0:
        return f"{'—':>{width}}"
    if v == int(v):
        return f"{int(v):>{width}}"
    # Show up to 4 decimal places but strip trailing zeros
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{s:>{width}}"


# ---------------------------------------------------------------------------
# Atualização Crédito — genuine credits only, date-capped
# ---------------------------------------------------------------------------


def _atualizacoes_credito_ate(conn, data_fim: str) -> dict[int, float]:
    """Atualização Crédito genuine share credits up to data_fim.

    Same algorithm as posicoes._atualizacoes_credito but capped at data_fim
    so that post-date events don't pollute historical snapshots.
    """
    events = conn.execute(
        """
        SELECT b.ativo_id, b.data, b.quantidade
        FROM b3_movimentacoes b
        JOIN ativos a ON a.id = b.ativo_id
        WHERE b.movimentacao = 'Atualização'
          AND b.sentido = 'Credito'
          AND b.ativo_id IS NOT NULL
          AND b.data <= ?
          AND a.tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto')
        ORDER BY b.ativo_id, b.data
        """,
        (data_fim,),
    ).fetchall()

    result: dict[int, float] = {}

    for ev in events:
        ativo_id = ev["ativo_id"]
        data     = ev["data"]
        qty_ev   = ev["quantidade"] or 0.0

        row = conn.execute(
            """SELECT COALESCE(SUM(
                CASE WHEN sentido = 'entrada' THEN quantidade ELSE -quantidade END
            ), 0.0) AS qty FROM negociacoes WHERE ativo_id = ? AND data < ?""",
            (ativo_id, data),
        ).fetchone()
        qty_negocios = row["qty"] if row else 0.0

        row = conn.execute(
            """SELECT
                COALESCE((SELECT SUM(quantidade) FROM b3_movimentacoes
                          WHERE ativo_id = ? AND movimentacao = 'Bonificação em Ativos'
                            AND data < ?), 0.0)
                - COALESCE((SELECT SUM(quantidade) FROM b3_movimentacoes
                            WHERE ativo_id = ? AND movimentacao = 'Leilão de Fração'
                              AND data < ?), 0.0) AS qty_bonif_net""",
            (ativo_id, data, ativo_id, data),
        ).fetchone()
        qty_bonif = row["qty_bonif_net"] if row else 0.0

        row = conn.execute(
            """SELECT COALESCE(SUM(quantidade), 0.0) AS qty FROM b3_movimentacoes
               WHERE ativo_id = ? AND movimentacao = 'Desdobro'
                 AND sentido = 'Credito' AND data < ?""",
            (ativo_id, data),
        ).fetchone()
        qty_desdobro = row["qty"] if row else 0.0

        qty_prev = result.get(ativo_id, 0.0)
        derived  = qty_negocios + qty_bonif + qty_desdobro + qty_prev

        if abs(qty_ev - derived) < 0.001:
            continue  # periodic snapshot — skip

        result[ativo_id] = qty_prev + qty_ev

    return result


# ---------------------------------------------------------------------------
# Position at a given date
# ---------------------------------------------------------------------------


def calcular_posicao_em(conn, data_fim: str) -> dict[int, dict]:
    """Return {ativo_id: position_dict} for all equity ativos as of data_fim."""

    ativos = {
        r["id"]: dict(r)
        for r in conn.execute(
            "SELECT id, tipo, subtipo, ticker, nome, vencimento FROM ativos"
        ).fetchall()
    }

    # ── Negociacoes (equity) ──────────────────────────────────────────────
    neg_rows = conn.execute(
        """
        SELECT
            n.ativo_id,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.quantidade    ELSE 0 END) AS qty_entrada,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.quantidade    ELSE 0 END) AS qty_saida,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.valor_liquido ELSE 0 END) AS custo_compras
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE a.tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto')
          AND n.data <= ?
        GROUP BY n.ativo_id
        """,
        (data_fim,),
    ).fetchall()
    neg_equity = {r["ativo_id"]: dict(r) for r in neg_rows}

    # ── Negociacoes (renda_fixa) ──────────────────────────────────────────
    rf_rows = conn.execute(
        """
        SELECT
            n.ativo_id,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.valor_bruto  ELSE 0 END) AS principal_investido,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.valor_bruto  ELSE 0 END) AS valor_resgatado,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.quantidade   ELSE 0 END) AS qty_entrada,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.quantidade   ELSE 0 END) AS qty_saida
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE a.tipo = 'renda_fixa'
          AND n.data <= ?
        GROUP BY n.ativo_id
        """,
        (data_fim,),
    ).fetchall()
    neg_rf = {r["ativo_id"]: dict(r) for r in rf_rows}

    # ── Corporate events (equity) ─────────────────────────────────────────
    bonif_rows = conn.execute(
        """
        SELECT
            b.ativo_id,
            SUM(b.quantidade) AS qty_bonif,
            SUM(CASE WHEN bc.custo_por_cota IS NOT NULL
                     THEN b.quantidade * bc.custo_por_cota ELSE 0 END) AS custo_bonif
        FROM b3_movimentacoes b
        LEFT JOIN bonificacoes bc ON bc.b3_movimentacao_id = b.id
        WHERE b.movimentacao = 'Bonificação em Ativos'
          AND b.ativo_id IS NOT NULL
          AND b.data <= ?
        GROUP BY b.ativo_id
        """,
        (data_fim,),
    ).fetchall()
    bonifs = {r["ativo_id"]: dict(r) for r in bonif_rows}

    desdobro_rows = conn.execute(
        """
        SELECT ativo_id, SUM(quantidade) AS qty_desdobro
        FROM b3_movimentacoes
        WHERE movimentacao = 'Desdobro' AND sentido = 'Credito'
          AND ativo_id IS NOT NULL AND data <= ?
        GROUP BY ativo_id
        """,
        (data_fim,),
    ).fetchall()
    desdobros = {r["ativo_id"]: r["qty_desdobro"] for r in desdobro_rows}

    fracao_rows = conn.execute(
        """
        SELECT ativo_id, SUM(quantidade) AS qty_fracao
        FROM b3_movimentacoes
        WHERE movimentacao = 'Leilão de Fração'
          AND ativo_id IS NOT NULL AND data <= ?
        GROUP BY ativo_id
        """,
        (data_fim,),
    ).fetchall()
    fracoes = {r["ativo_id"]: r["qty_fracao"] for r in fracao_rows}

    rf_venc_rows = conn.execute(
        """
        SELECT ativo_id, SUM(COALESCE(quantidade, 0)) AS qty_vencida
        FROM b3_movimentacoes
        WHERE movimentacao IN ('VENCIMENTO/RESGATE SALDO EM CONTA', 'VENCIMENTO')
          AND sentido = 'Debito' AND ativo_id IS NOT NULL AND data <= ?
        GROUP BY ativo_id
        """,
        (data_fim,),
    ).fetchall()
    rf_vencidos = {r["ativo_id"]: r["qty_vencida"] for r in rf_venc_rows}

    atualizacoes = _atualizacoes_credito_ate(conn, data_fim)

    result: dict[int, dict] = {}

    for ativo_id, ativo in ativos.items():
        tipo = ativo["tipo"]

        if tipo in _EQUITY_TIPOS:
            neq             = neg_equity.get(ativo_id)
            qty_atualizacao = atualizacoes.get(ativo_id) or 0.0

            if neq is None:
                if qty_atualizacao <= 0:
                    continue
                result[ativo_id] = {
                    "ativo_id": ativo_id,
                    "ticker":   ativo["ticker"] or ativo["nome"],
                    "nome":     ativo["nome"] or "",
                    "tipo":     tipo,
                    "qty":      qty_atualizacao,
                    "custo_total": 0.0,
                    "preco_medio": None,
                }
                continue

            qty_comprada  = neq["qty_entrada"]   or 0.0
            qty_vendida   = neq["qty_saida"]     or 0.0
            custo_compras = neq["custo_compras"] or 0.0

            b           = bonifs.get(ativo_id, {})
            qty_bonif   = b.get("qty_bonif")  or 0.0
            custo_bonif = b.get("custo_bonif") or 0.0

            qty_fracao   = fracoes.get(ativo_id)  or 0.0
            qty_desdobro = desdobros.get(ativo_id) or 0.0

            qty_atual = (qty_comprada - qty_vendida + qty_bonif + qty_desdobro
                         - qty_fracao + qty_atualizacao)

            total_qty_entradas = qty_comprada + qty_bonif + qty_desdobro + qty_atualizacao
            total_custo        = custo_compras + custo_bonif
            avg_cost = total_custo / total_qty_entradas if total_qty_entradas > 0 else 0.0

            if qty_atual <= -0.001 or (qty_atual < 0.001 and qty_comprada == 0):
                continue

            result[ativo_id] = {
                "ativo_id":   ativo_id,
                "ticker":     ativo["ticker"] or ativo["nome"],
                "nome":       ativo["nome"] or "",
                "tipo":       tipo,
                "qty":        qty_atual,
                "custo_total": avg_cost * qty_atual if qty_atual > 0.001 else 0.0,
                "preco_medio": avg_cost if qty_atual > 0.001 else None,
            }

        elif tipo == "renda_fixa":
            rf = neg_rf.get(ativo_id)
            if rf is None:
                continue
            qty_entrada         = rf["qty_entrada"]         or 0.0
            qty_saida_neg       = rf["qty_saida"]           or 0.0
            principal_investido = rf["principal_investido"] or 0.0
            qty_vencida_b3      = rf_vencidos.get(ativo_id) or 0.0
            qty_saida           = max(qty_saida_neg, qty_vencida_b3)
            remaining           = max(0.0, qty_entrada - qty_saida)
            frac_out            = (remaining / qty_entrada) if qty_entrada > 0 else 0.0
            result[ativo_id] = {
                "ativo_id":    ativo_id,
                "ticker":      ativo["ticker"] or "",
                "nome":        ativo["nome"] or "",
                "tipo":        tipo,
                "subtipo":     ativo.get("subtipo") or "",
                "vencimento":  ativo.get("vencimento") or "",
                "qty":         remaining,
                "custo_total": principal_investido * frac_out,
                "preco_medio": None,
            }

    return result


# ---------------------------------------------------------------------------
# Year activity
# ---------------------------------------------------------------------------


def calcular_atividade_ano(conn, data_inicio: str, data_fim: str) -> dict[int, dict]:
    """Per-ativo buy/sell activity between data_inicio and data_fim (inclusive)."""
    rows = conn.execute(
        """
        SELECT
            n.ativo_id,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.quantidade    ELSE 0 END) AS qty_compras,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.quantidade    ELSE 0 END) AS qty_vendas,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.valor_liquido ELSE 0 END) AS valor_compras,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.valor_liquido ELSE 0 END) AS valor_vendas
        FROM negociacoes n
        WHERE n.data BETWEEN ? AND ?
        GROUP BY n.ativo_id
        """,
        (data_inicio, data_fim),
    ).fetchall()
    return {r["ativo_id"]: dict(r) for r in rows}


def calcular_bonifs_ano(conn, data_inicio: str, data_fim: str) -> dict[int, float]:
    """Bonificação qty per ativo within the period."""
    rows = conn.execute(
        """
        SELECT ativo_id, SUM(quantidade) AS qty_bonif
        FROM b3_movimentacoes
        WHERE movimentacao = 'Bonificação em Ativos'
          AND ativo_id IS NOT NULL
          AND data BETWEEN ? AND ?
        GROUP BY ativo_id
        """,
        (data_inicio, data_fim),
    ).fetchall()
    return {r["ativo_id"]: r["qty_bonif"] for r in rows}


def calcular_desdobros_ano(conn, data_inicio: str, data_fim: str) -> dict[int, float]:
    """Desdobro qty per ativo within the period."""
    rows = conn.execute(
        """
        SELECT ativo_id, SUM(quantidade) AS qty_desdobro
        FROM b3_movimentacoes
        WHERE movimentacao = 'Desdobro' AND sentido = 'Credito'
          AND ativo_id IS NOT NULL
          AND data BETWEEN ? AND ?
        GROUP BY ativo_id
        """,
        (data_inicio, data_fim),
    ).fetchall()
    return {r["ativo_id"]: r["qty_desdobro"] for r in rows}


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _parse_data(arg: str) -> tuple[str, str, str, int | None]:
    """Parse arg into (data_fim, data_inicio_ano, label, ano).

    Returns (data_fim, ano_inicio, label, ano_int).
    """
    if len(arg) == 4 and arg.isdigit():
        ano = int(arg)
        return f"{ano}-12-31", f"{ano}-01-01", str(ano), ano
    # YYYY-MM-DD
    data_fim = arg
    ano = int(arg[:4])
    return data_fim, f"{ano}-01-01", data_fim, ano


def exibir_fechamento(data_fim: str, data_inicio_ano: str, label: str, ano: int | None) -> None:
    init_db()
    with connect() as conn:
        # Opening position (start of year, i.e. end of previous year)
        if ano is not None:
            data_abertura = f"{ano - 1}-12-31"
        else:
            data_abertura = f"{int(data_fim[:4]) - 1}-12-31"

        pos_abertura = calcular_posicao_em(conn, data_abertura)
        pos_fechamento = calcular_posicao_em(conn, data_fim)
        atividade     = calcular_atividade_ano(conn, data_inicio_ano, data_fim)
        bonifs_ano    = calcular_bonifs_ano(conn, data_inicio_ano, data_fim)
        desdobros_ano = calcular_desdobros_ano(conn, data_inicio_ano, data_fim)

        # Fetch ativo metadata for lookup
        ativos_meta = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT id, tipo, subtipo, ticker, nome, vencimento FROM ativos"
            ).fetchall()
        }

    width = 120

    titulo = f" FECHAMENTO DE POSIÇÃO — {label} "
    print()
    print(titulo.center(width, "═"))
    print()

    # ── Equity sections ───────────────────────────────────────────────────
    tipo_labels = [
        ("acao",           "AÇÕES"),
        ("fii",            "FIIs"),
        ("bdr",            "BDRs"),
        ("tesouro_direto", "TESOURO DIRETO"),
    ]

    grand_custo_fim    = 0.0
    grand_compras      = 0.0
    grand_vendas       = 0.0

    for tipo_key, tipo_label in tipo_labels:
        # Collect all ativo_ids for this tipo that appear in any data source
        ids_tipo = {
            aid for aid, a in ativos_meta.items() if a["tipo"] == tipo_key
        }
        ids_relevantes = ids_tipo & (
            set(pos_fechamento.keys())
            | set(pos_abertura.keys())
            | set(atividade.keys())
        )

        if not ids_relevantes:
            continue

        # Sort by ticker
        ids_sorted = sorted(
            ids_relevantes,
            key=lambda aid: ativos_meta[aid].get("ticker") or ativos_meta[aid].get("nome") or "",
        )

        print(f" {tipo_label} ".center(width, "─"))
        print()

        col_ticker = 18
        col_qty    = 9
        col_pm     = 14
        col_custo  = 15
        col_comp   = 15
        col_vend   = 15
        col_dqty   = 9

        hdr = (
            f"  {'Ticker':<{col_ticker}}  {'Qtd':>{col_qty}}  {'Preço Médio':>{col_pm}}"
            f"  {'Custo Total':>{col_custo}}  {'Compras no Ano':>{col_comp}}"
            f"  {'Vendas no Ano':>{col_vend}}  {'Δ Qtd':>{col_dqty}}"
        )
        print(hdr)
        print("─" * len(hdr))

        sec_custo   = 0.0
        sec_compras = 0.0
        sec_vendas  = 0.0

        for aid in ids_sorted:
            meta    = ativos_meta[aid]
            raw_ticker = meta.get("ticker") or ""
            ticker = (raw_ticker if raw_ticker else (meta.get("nome") or ""))[:col_ticker]

            pfim    = pos_fechamento.get(aid, {})
            paber   = pos_abertura.get(aid, {})
            ativ    = atividade.get(aid, {})

            qty_fim     = pfim.get("qty")       or 0.0
            pm_fim      = pfim.get("preco_medio")
            custo_fim   = pfim.get("custo_total") or 0.0

            qty_aber    = paber.get("qty")      or 0.0

            # Activity in year (from negociacoes + bonifs + desdobros)
            qty_comp    = ativ.get("qty_compras", 0.0) or 0.0
            qty_vend    = ativ.get("qty_vendas",  0.0) or 0.0
            val_comp    = ativ.get("valor_compras", 0.0) or 0.0
            val_vend    = ativ.get("valor_vendas",  0.0) or 0.0
            qty_bonif   = bonifs_ano.get(aid, 0.0) or 0.0
            qty_desdob  = desdobros_ano.get(aid, 0.0) or 0.0

            # Net qty change from all sources
            delta_qty   = qty_fim - qty_aber

            # Skip if truly nothing happened and no position at any point
            if qty_fim == 0 and qty_aber == 0 and not atividade.get(aid):
                continue

            sec_custo   += custo_fim
            sec_compras += val_comp
            sec_vendas  += val_vend

            # Format delta_qty with sign and note if bonif/desdobro contributed
            extra = ""
            if qty_bonif:
                extra += f" +{qty_bonif:.0f}b"
            if qty_desdob:
                extra += f" +{qty_desdob:.0f}d"

            delta_str = _brl_signed(delta_qty, col_dqty)
            if extra:
                delta_str = delta_str.rstrip() + extra

            pm_str    = _brl(pm_fim, col_pm)
            custo_str = _brl(custo_fim, col_custo)
            comp_str  = _brl(val_comp, col_comp) if val_comp else f"{'—':>{col_comp}}"
            vend_str  = _brl(val_vend, col_vend) if val_vend else f"{'—':>{col_vend}}"

            qty_str   = _qty_str(qty_fim, col_qty)

            print(
                f"  {ticker:<{col_ticker}}  {qty_str}  {pm_str}"
                f"  {custo_str}  {comp_str}  {vend_str}  {delta_str}"
            )

            # If there were compras or vendas, show sub-line with qty detail
            if qty_comp or qty_vend:
                sub_parts = []
                if qty_comp:
                    sub_parts.append(f"comprou {int(qty_comp) if qty_comp == int(qty_comp) else qty_comp}")
                if qty_vend:
                    sub_parts.append(f"vendeu {int(qty_vend) if qty_vend == int(qty_vend) else qty_vend}")
                sub_parts.append(f"(início do ano: {_qty_str(qty_aber, 0).strip()})")
                print(f"  {'':>{col_ticker}}  {' | '.join(sub_parts)}")

        print("─" * len(hdr))
        print(
            f"  {'Totais':<{col_ticker}}  {'':>{col_qty}}  {'':>{col_pm}}"
            f"  {_brl(sec_custo, col_custo)}"
            f"  {_brl(sec_compras, col_comp) if sec_compras else f'{chr(8212):>{col_comp}}'}"
            f"  {_brl(sec_vendas, col_vend)  if sec_vendas  else f'{chr(8212):>{col_vend}}'}"
        )
        print()

        grand_custo_fim += sec_custo
        grand_compras   += sec_compras
        grand_vendas    += sec_vendas

    # ── Renda Fixa ────────────────────────────────────────────────────────
    ids_rf = {aid for aid, a in ativos_meta.items() if a["tipo"] == "renda_fixa"}
    ids_rf_rel = ids_rf & (set(pos_fechamento.keys()) | set(pos_abertura.keys()))

    if ids_rf_rel:
        print(" RENDA FIXA ".center(width, "─"))
        print()
        hdr_rf = f"  {'Nome':<36}  {'Vencimento':>12}  {'Principal (fim)':>17}  Situação"
        print(hdr_rf)
        print("─" * len(hdr_rf))
        sec_rf = 0.0
        for aid in sorted(ids_rf_rel, key=lambda a: ativos_meta[a].get("nome") or ""):
            meta    = ativos_meta[aid]
            nome    = (meta.get("nome") or "")[:36]
            venc    = meta.get("vencimento") or "—"
            pfim    = pos_fechamento.get(aid, {})
            custo_rf = pfim.get("custo_total") or 0.0
            qty_rf   = pfim.get("qty") or 0.0
            situacao = "aberta" if qty_rf > 0 else "resgatada/vencida"
            sec_rf  += custo_rf
            print(f"  {nome:<36}  {venc:>12}  {_brl(custo_rf, 17)}  {situacao}")
        print("─" * len(hdr_rf))
        print(f"  {'Total':<36}  {'':>12}  {_brl(sec_rf, 17)}")
        grand_custo_fim += sec_rf
        print()

    # ── Grand total ───────────────────────────────────────────────────────
    print("═" * width)
    delta_caixa = grand_vendas - grand_compras
    print(
        f"  CUSTO TOTAL (posições abertas em {label}):  {_brl(grand_custo_fim, 16)}"
        f"     Compras no período: {_brl(grand_compras, 14)}"
        f"     Vendas: {_brl(grand_vendas, 14)}"
    )
    delta_sign = "+" if delta_caixa >= 0 else ""
    print(
        f"  Δ Caixa no período (vendas − compras): "
        f"R$ {delta_sign}{delta_caixa:,.2f}"
    )
    print()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flag_data = "--data" in sys.argv

    if flag_data:
        idx = sys.argv.index("--data")
        if idx + 1 >= len(sys.argv):
            print("Uso: python fechamento.py --data YYYY-MM-DD")
            sys.exit(1)
        raw = sys.argv[idx + 1]
    elif args:
        raw = args[0]
    else:
        # Default: previous year
        raw = str(date.today().year - 1)

    data_fim, data_inicio_ano, label, ano = _parse_data(raw)
    exibir_fechamento(data_fim, data_inicio_ano, label, ano)


if __name__ == "__main__":
    main()
