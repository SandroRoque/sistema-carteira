"""Calculate portfolio positions from the two data sources.

Sources of truth
----------------
negociacoes      : buys, sells, aquisições, resgates (source of truth for qty and cost)
b3_movimentacoes : corporate events — bonificações, subscription events, income

Rules per tipo
--------------
acao / fii / bdr / tesouro_direto
    qty   = (entrada − saída via negociacoes)
            + bonificações + desdobramentos (splits)
            − leilão_de_fração
            + custody transfers (Transferência Crédito − Débito)
              only for ativos with NO negociacoes entry (e.g. a BDR received by custody transfer)
    custo = custo médio ponderado × qty_atual
            (desdobramento adds qty with zero marginal cost, diluting avg)

renda_fixa
    qty       = qty_entrada_negociacoes − qty_saida_negociacoes
                − qty_vencida_b3 (VENCIMENTO/RESGATE SALDO EM CONTA)
                (uses the max of negociacoes or B3 to avoid double-counting)
    custo     = principal investido ainda em aberto (proporção não resgatada)

direito_subscricao / recibo_subscricao
    qty   = crédito B3 − débito B3  (negociacoes não registra esses instrumentos)
    custo = valor pago no exercício (Débito events that have a valor)

Income always comes from b3_movimentacoes:
    Rendimento, Dividendo (net of Dividendo - Cancelado), JCP, PAGAMENTO DE JUROS

Events explicitly skipped
--------------------------
Transferência - Liquidação, COMPRA / VENDA, Compra, Resgate, VENCIMENTO
    → already in negociacoes, would double-count
Atualização
    → administrative (ticker rename), no financial impact
Fração em Ativos (Débito)
    → paired with Leilão de Fração (Crédito); only the latter is processed
"""

from __future__ import annotations

from database import connect

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EQUITY_TIPOS = {"acao", "fii", "bdr", "tesouro_direto"}
_SUBSCRICAO_TIPOS = {"direito_subscricao", "recibo_subscricao"}


# ---------------------------------------------------------------------------
# Per-source aggregations
# ---------------------------------------------------------------------------


def _negocios_equity(conn) -> dict[int, dict]:
    """Qty and cost from negociacoes for equity/fii/bdr/tesouro_direto ativos."""
    rows = conn.execute(
        """
        SELECT
            n.ativo_id,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.quantidade   ELSE 0 END) AS qty_entrada,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.quantidade   ELSE 0 END) AS qty_saida,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.valor_liquido ELSE 0 END) AS custo_compras,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.valor_liquido ELSE 0 END) AS receita_vendas
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE a.tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto')
        GROUP BY n.ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: dict(r) for r in rows}


def _negocios_rf(conn) -> dict[int, dict]:
    """Principal invested and redeemed for renda_fixa ativos."""
    rows = conn.execute(
        """
        SELECT
            n.ativo_id,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.valor_bruto ELSE 0 END) AS principal_investido,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.valor_bruto ELSE 0 END) AS valor_resgatado,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.quantidade  ELSE 0 END) AS qty_entrada,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.quantidade  ELSE 0 END) AS qty_saida
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE a.tipo = 'renda_fixa'
        GROUP BY n.ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: dict(r) for r in rows}


def _bonificacoes(conn) -> dict[int, dict]:
    """Bonificação qty and cost from B3, joined with bonificacoes cost table."""
    rows = conn.execute(
        """
        SELECT
            b.ativo_id,
            SUM(b.quantidade)                                                         AS qty_bonif,
            SUM(CASE WHEN bc.custo_por_cota IS NOT NULL
                     THEN b.quantidade * bc.custo_por_cota ELSE 0 END)                AS custo_bonif,
            MAX(CASE WHEN bc.custo_por_cota IS NULL THEN 1 ELSE 0 END)                AS tem_bonif_sem_custo
        FROM b3_movimentacoes b
        LEFT JOIN bonificacoes bc ON bc.b3_movimentacao_id = b.id
        WHERE b.movimentacao = 'Bonificação em Ativos'
          AND b.ativo_id IS NOT NULL
        GROUP BY b.ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: dict(r) for r in rows}


def _atualizacoes_credito(conn) -> dict[int, float]:
    """Atualização Crédito events that are genuine share credits (not position snapshots).

    B3 uses 'Atualização' for two distinct purposes:
    a) Periodic position confirmations — B3 records the current position size
       (qty == total position at that point in time); these must be ignored.
    b) Genuine share credits — corporate action adjustments, conversion credits
       (qty ≠ total position at that point in time); these must be counted.

    We distinguish them per event: compute the independently-derived position
    at each event's date (from negociacoes + bonifs + desdobros + prior genuine
    Atualização credits); if the event qty equals that derived position it is a
    snapshot and is skipped, otherwise it is a genuine credit and is included.
    """
    events = conn.execute(
        """
        SELECT b.ativo_id, b.data, b.quantidade
        FROM b3_movimentacoes b
        JOIN ativos a ON a.id = b.ativo_id
        WHERE b.movimentacao = 'Atualização'
          AND b.sentido = 'Credito'
          AND b.ativo_id IS NOT NULL
          AND a.tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto')
        ORDER BY b.ativo_id, b.data
        """
    ).fetchall()

    result: dict[int, float] = {}

    for ev in events:
        ativo_id = ev["ativo_id"]
        data     = ev["data"]
        qty_ev   = ev["quantidade"] or 0.0

        # Negociacoes-derived qty strictly before this event date
        row = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN sentido = 'entrada' THEN quantidade ELSE -quantidade END
            ), 0.0) AS qty
            FROM negociacoes WHERE ativo_id = ? AND data < ?
            """,
            (ativo_id, data),
        ).fetchone()
        qty_negocios = row["qty"] if row else 0.0

        # Bonificações net of leilão de fração strictly before this date
        row = conn.execute(
            """
            SELECT
                COALESCE((SELECT SUM(quantidade) FROM b3_movimentacoes
                          WHERE ativo_id = ? AND movimentacao = 'Bonificação em Ativos'
                            AND data < ?), 0.0)
                - COALESCE((SELECT SUM(quantidade) FROM b3_movimentacoes
                            WHERE ativo_id = ? AND movimentacao = 'Leilão de Fração'
                              AND data < ?), 0.0) AS qty_bonif_net
            """,
            (ativo_id, data, ativo_id, data),
        ).fetchone()
        qty_bonif = row["qty_bonif_net"] if row else 0.0

        # Desdobros strictly before this date
        row = conn.execute(
            """
            SELECT COALESCE(SUM(quantidade), 0.0) AS qty
            FROM b3_movimentacoes
            WHERE ativo_id = ? AND movimentacao = 'Desdobro'
              AND sentido = 'Credito' AND data < ?
            """,
            (ativo_id, data),
        ).fetchone()
        qty_desdobro = row["qty"] if row else 0.0

        # Prior genuine Atualização credits already confirmed for this ativo
        qty_prev = result.get(ativo_id, 0.0)

        derived = qty_negocios + qty_bonif + qty_desdobro + qty_prev

        # If event qty matches derived position → periodic snapshot → skip
        if abs(qty_ev - derived) < 0.001:
            continue

        # Otherwise it is a genuine credit
        result[ativo_id] = qty_prev + qty_ev

    return result


def _fracoes(conn) -> dict[int, float]:
    """Leilão de Fração: fractional shares sold after bonificação (reduce qty)."""
    rows = conn.execute(
        """
        SELECT ativo_id, SUM(quantidade) AS qty_fracao
        FROM b3_movimentacoes
        WHERE movimentacao = 'Leilão de Fração'
          AND ativo_id IS NOT NULL
        GROUP BY ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: r["qty_fracao"] for r in rows}


def _desdobramentos(conn) -> dict[int, float]:
    """Desdobro (stock split): free shares credited, zero marginal cost.

    The total cost basis stays the same but is spread over more shares,
    so avg_cost per share decreases proportionally.
    """
    rows = conn.execute(
        """
        SELECT ativo_id, SUM(quantidade) AS qty_desdobro
        FROM b3_movimentacoes
        WHERE movimentacao = 'Desdobro'
          AND sentido = 'Credito'
          AND ativo_id IS NOT NULL
        GROUP BY ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: r["qty_desdobro"] for r in rows}


def _transferencias_custody(conn) -> dict[int, float]:
    """Net qty from custody transfers ('Transferência') per ativo.

    Used only for ativos that have no negociacoes entry — e.g. BDRs
    received via broker-to-broker transfer rather than a trade note.
    Cost is unknown (set to zero in the position).
    """
    rows = conn.execute(
        """
        SELECT
            ativo_id,
            SUM(CASE WHEN sentido = 'Credito' THEN COALESCE(quantidade, 0) ELSE 0 END)
            - SUM(CASE WHEN sentido = 'Debito'  THEN COALESCE(quantidade, 0) ELSE 0 END)
              AS qty_net
        FROM b3_movimentacoes
        WHERE movimentacao = 'Transferência'
          AND ativo_id IS NOT NULL
        GROUP BY ativo_id
        HAVING qty_net != 0
        """
    ).fetchall()
    return {r["ativo_id"]: r["qty_net"] for r in rows}


def _b3_resgates_equity(conn) -> dict[int, float]:
    """B3 'Resgate' Crédito events that close equity/FII positions.

    Fund incorporações (mergers) generate a Resgate event in B3 as the
    only record of position exit — no brokerage note is issued for these.
    The qty in this event reduces the open position to zero.

    Regular sales appear in negociacoes (not as B3 Resgate), so there is
    no double-counting risk in practice.
    """
    rows = conn.execute(
        """
        SELECT b.ativo_id, SUM(COALESCE(b.quantidade, 0)) AS qty_resgatada
        FROM b3_movimentacoes b
        JOIN ativos a ON a.id = b.ativo_id
        WHERE b.movimentacao = 'Resgate'
          AND b.sentido = 'Credito'
          AND a.tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto')
          AND b.ativo_id IS NOT NULL
        GROUP BY b.ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: r["qty_resgatada"] for r in rows}


def _rf_b3_vencimentos(conn) -> dict[int, float]:
    """Qty returned via B3 VENCIMENTO/RESGATE events (no brokerage note issued).

    For renda_fixa instruments that matured and were redeemed without a
    corresponding negociacoes entry (e.g. CDB matured while cash stayed at
    the bank — broker doesn't issue a nota de corretagem for that).

    Using the max of negociacoes.qty_saida vs this value avoids
    double-counting when both a nota AND a B3 event exist.
    """
    rows = conn.execute(
        """
        SELECT ativo_id, SUM(COALESCE(quantidade, 0)) AS qty_vencida
        FROM b3_movimentacoes
        WHERE movimentacao IN ('VENCIMENTO/RESGATE SALDO EM CONTA', 'VENCIMENTO')
          AND sentido = 'Debito'
          AND ativo_id IS NOT NULL
        GROUP BY ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: r["qty_vencida"] for r in rows}


def _subscricao_posicoes(conn) -> dict[int, dict]:
    """Qty for direito/recibo_subscricao ativos from B3 events.

    Position-opening events (Crédito):
        Direito de Subscrição, Direito Sobras de Subscrição, Recibo de Subscrição

    Position-closing events (Débito, affect qty):
        Direitos de Subscrição - Exercido         (right was exercised)
        Direitos de Subscrição - Não Exercido     (right expired)
        Direito Sobras de Subscrição - Não Exercido (leftover right expired)

    Cost-only events (Débito, affect cost but NOT qty):
        Solicitação de Subscrição  ← this is the payment request step that
        precedes "Exercido"; both record the same exercise, so only "Exercido"
        should reduce qty to avoid double-counting.
    """
    rows = conn.execute(
        """
        SELECT
            b.ativo_id,
            SUM(CASE WHEN b.movimentacao IN (
                    'Direito de Subscrição',
                    'Direito Sobras de Subscrição',
                    'Recibo de Subscrição'
                ) THEN COALESCE(b.quantidade, 0) ELSE 0 END)                    AS qty_entrada,
            SUM(CASE WHEN b.movimentacao IN (
                    'Direitos de Subscrição - Exercido',
                    'Direitos de Subscrição - Não Exercido',
                    'Direito Sobras de Subscrição - Não Exercido'
                ) THEN COALESCE(b.quantidade, 0) ELSE 0 END)                    AS qty_saida,
            SUM(CASE WHEN b.movimentacao IN (
                    'Direitos de Subscrição - Exercido',
                    'Solicitação de Subscrição'
                ) AND b.valor IS NOT NULL
                THEN b.valor ELSE 0 END)                                         AS custo_exercicio
        FROM b3_movimentacoes b
        JOIN ativos a ON a.id = b.ativo_id
        WHERE a.tipo IN ('direito_subscricao', 'recibo_subscricao')
          AND b.ativo_id IS NOT NULL
        GROUP BY b.ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: dict(r) for r in rows}


def _rendimentos_por_ativo(conn) -> dict[int, dict]:
    """Income received per ativo from B3.

    Covers:
    - Rendimento        : FII / renda_fixa distributions
    - Dividendo         : dividends (net of Dividendo - Cancelado reversals)
    - JCP               : juros sobre capital próprio
    - PAGAMENTO DE JUROS: interest paid at maturity for renda_fixa
    """
    rows = conn.execute(
        """
        SELECT
            ativo_id,
            SUM(CASE WHEN movimentacao = 'Rendimento'
                     THEN COALESCE(valor, 0) ELSE 0 END)                            AS rendimentos,
            SUM(CASE WHEN movimentacao = 'Dividendo'
                     THEN  COALESCE(valor, 0)
                     WHEN movimentacao = 'Dividendo - Cancelado'
                     THEN -COALESCE(valor, 0)
                     ELSE 0 END)                                                     AS dividendos,
            SUM(CASE WHEN movimentacao = 'Juros Sobre Capital Próprio'
                     THEN COALESCE(valor, 0) ELSE 0 END)                            AS jcp,
            SUM(CASE WHEN movimentacao = 'PAGAMENTO DE JUROS'
                     THEN COALESCE(valor, 0) ELSE 0 END)                            AS pagamento_juros,
            SUM(CASE
                WHEN movimentacao IN (
                    'Rendimento', 'Dividendo', 'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
                ) THEN  COALESCE(valor, 0)
                WHEN movimentacao = 'Dividendo - Cancelado'
                THEN -COALESCE(valor, 0)
                ELSE 0
            END)                                                                     AS total_income
        FROM b3_movimentacoes
        WHERE movimentacao IN (
            'Rendimento', 'Dividendo', 'Dividendo - Cancelado',
            'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
        )
          AND ativo_id IS NOT NULL
        GROUP BY ativo_id
        """
    ).fetchall()
    return {r["ativo_id"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def calcular_posicoes(conn) -> list[dict]:
    """Return one position dict per ativo that has any recorded activity.

    Fields in each dict
    -------------------
    ativo_id, ticker, nome, tipo, subtipo, vencimento
    qty            – current quantity (units; for renda_fixa: remaining face units)
    custo_total    – cost of current holding (R$)
    preco_medio    – avg cost per unit (None for renda_fixa)
    rendimentos       – total 'Rendimento' income received (R$)
    dividendos        – total 'Dividendo' income (net of Cancelado) (R$)
    jcp               – total JCP received (R$)
    pagamento_juros   – interest paid at maturity (renda_fixa) (R$)
    total_income      – sum of all income types above (R$)
    is_open           – True if qty > 0
    tem_bonif_sem_custo – True if any bonus-share event lacks custo_por_cota
    custo_sem_origem  – True if position from custody transfer (cost unknown)
    """
    ativos = {
        r["id"]: dict(r)
        for r in conn.execute(
            "SELECT id, tipo, subtipo, ticker, nome, vencimento FROM ativos ORDER BY tipo, ticker"
        ).fetchall()
    }

    neg_equity    = _negocios_equity(conn)
    neg_rf        = _negocios_rf(conn)
    bonifs        = _bonificacoes(conn)
    fracoes       = _fracoes(conn)
    desdobros     = _desdobramentos(conn)
    transferencias = _transferencias_custody(conn)
    atualizacoes  = _atualizacoes_credito(conn)
    rf_vencidos   = _rf_b3_vencimentos(conn)
    b3_resgates   = _b3_resgates_equity(conn)
    subscricoes   = _subscricao_posicoes(conn)
    rendimentos   = _rendimentos_por_ativo(conn)

    posicoes: list[dict] = []

    for ativo_id, ativo in ativos.items():
        tipo = ativo["tipo"]

        pos: dict = {
            "ativo_id":            ativo_id,
            "ticker":              ativo["ticker"] or "",
            "nome":                ativo["nome"] or "",
            "tipo":                tipo,
            "subtipo":             ativo["subtipo"] or "",
            "vencimento":          ativo["vencimento"] or "",
            "qty":                 0.0,
            "custo_total":         0.0,
            "preco_medio":         None,
            "rendimentos":         0.0,
            "dividendos":          0.0,
            "jcp":                 0.0,
            "pagamento_juros":     0.0,
            "total_income":        0.0,
            "is_open":             False,
            "tem_bonif_sem_custo": False,
            "custo_sem_origem":    False,
        }

        if tipo in _EQUITY_TIPOS:
            neq = neg_equity.get(ativo_id)

            # Ativos received via custody transfer with no purchase note
            qty_transfer    = transferencias.get(ativo_id) or 0.0
            qty_atualizacao = atualizacoes.get(ativo_id)   or 0.0

            if neq is None:
                # No negociacoes — position comes from custody transfer and/or
                # genuine Atualização credits (e.g. corporate-action conversions)
                qty_pos = qty_transfer + qty_atualizacao
                if qty_pos <= 0:
                    continue
                pos["qty"]              = qty_pos
                pos["custo_total"]      = 0.0
                pos["preco_medio"]      = None
                pos["custo_sem_origem"] = True
                pos["is_open"]          = True
            else:
                qty_comprada   = neq["qty_entrada"]   or 0.0
                qty_vendida    = neq["qty_saida"]     or 0.0
                custo_compras  = neq["custo_compras"] or 0.0

                b              = bonifs.get(ativo_id, {})
                qty_bonif      = b.get("qty_bonif")  or 0.0
                custo_bonif    = b.get("custo_bonif") or 0.0
                tem_bonif_sem  = bool(b.get("tem_bonif_sem_custo", False))

                qty_fracao     = fracoes.get(ativo_id) or 0.0

                # Desdobro (split): free shares, zero marginal cost → dilutes avg
                qty_desdobro     = desdobros.get(ativo_id) or 0.0

                # Fund incorporação / absorption: B3 Resgate Crédito closes position
                qty_resgatada_b3 = b3_resgates.get(ativo_id) or 0.0

                qty_atual = (qty_comprada - qty_vendida
                             + qty_bonif + qty_desdobro
                             - qty_fracao + qty_transfer
                             + qty_atualizacao
                             - qty_resgatada_b3)

                # Weighted average cost per share
                # Desdobro and Atualização credits add to denominator but NOT
                # numerator (zero marginal cost), diluting avg cost per share.
                total_qty_entradas = qty_comprada + qty_bonif + qty_desdobro + qty_atualizacao
                total_custo        = custo_compras + custo_bonif
                if total_qty_entradas > 0:
                    avg_cost = total_custo / total_qty_entradas
                else:
                    avg_cost = 0.0

                pos["qty"]                 = qty_atual
                pos["custo_total"]         = avg_cost * qty_atual if qty_atual > 0 else 0.0
                pos["preco_medio"]         = avg_cost if qty_atual > 0 else None
                pos["tem_bonif_sem_custo"] = tem_bonif_sem
                pos["is_open"]             = qty_atual > 0.001

        elif tipo == "renda_fixa":
            rf = neg_rf.get(ativo_id)
            if rf is None:
                continue

            qty_entrada         = rf["qty_entrada"]         or 0.0
            qty_saida_neg       = rf["qty_saida"]           or 0.0
            principal_investido = rf["principal_investido"] or 0.0

            # Use the larger of: negociacoes resgate qty vs B3 vencimento qty.
            # This covers CDBs that matured without a brokerage note, without
            # double-counting when both sources record the same redemption.
            qty_vencida_b3 = rf_vencidos.get(ativo_id) or 0.0
            qty_saida      = max(qty_saida_neg, qty_vencida_b3)

            remaining        = max(0.0, qty_entrada - qty_saida)
            frac_outstanding = (remaining / qty_entrada) if qty_entrada > 0 else 0.0

            pos["qty"]         = remaining
            pos["custo_total"] = principal_investido * frac_outstanding
            pos["is_open"]     = remaining > 0.001

        elif tipo in _SUBSCRICAO_TIPOS:
            sub            = subscricoes.get(ativo_id, {})
            qty_entrada    = sub.get("qty_entrada")    or 0.0
            qty_saida      = sub.get("qty_saida")      or 0.0
            custo_exerc    = sub.get("custo_exercicio") or 0.0

            qty_atual      = qty_entrada - qty_saida
            pos["qty"]       = qty_atual
            pos["custo_total"] = custo_exerc
            pos["is_open"]   = qty_atual > 0.001

        else:
            continue  # desconhecido or unhandled tipo

        # Income from B3 applies to all tipos
        rend = rendimentos.get(ativo_id, {})
        pos["rendimentos"]    = rend.get("rendimentos")    or 0.0
        pos["dividendos"]     = rend.get("dividendos")     or 0.0
        pos["jcp"]            = rend.get("jcp")            or 0.0
        pos["pagamento_juros"] = rend.get("pagamento_juros") or 0.0
        pos["total_income"]   = rend.get("total_income")   or 0.0

        posicoes.append(pos)

    return posicoes
