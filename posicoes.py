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
    custo = custo médio ponderado, replayed in date order (custo_medio.py):
            sales leave the average unchanged, a closed position starts
            afresh, desdobramento adds qty at zero cost (diluting the avg)

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
Recibo de Subscrição
    → closed when its shares are credited (subscricoes.py); B3 has no debit
Direitos de Subscrição - Não Exercido
    → B3 writes it with quantity 0: it closes whatever was left of the right
Cessão de Direitos / Cessão de Direitos - Solicitada
    → same-day Crédito and Débito of the same quantity at the same broker,
      no value: a procedural pair that nets to zero, not a sale (a sale of
      rights comes on a trade note). Ignored

Every query is scoped to a single investidor_id.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from sqlalchemy import Connection

import subscricoes as subscricoes_
from custo_medio import saldos
from database import fetch_all

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EQUITY_TIPOS = {"acao", "fii", "bdr", "etf", "fundo", "tesouro_direto"}
_SUBSCRICAO_TIPOS = {"direito_subscricao", "recibo_subscricao"}


# ---------------------------------------------------------------------------
# Per-source aggregations
# ---------------------------------------------------------------------------


def _negocios_rf(conn: Connection, investidor_id: int, ate: date | None = None) -> dict[int, dict]:
    """Principal invested and redeemed for renda_fixa ativos, up to `ate`."""
    rows = fetch_all(
        conn,
        """
        SELECT
            n.ativo_id,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.valor_bruto ELSE 0 END) AS principal_investido,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.valor_bruto ELSE 0 END) AS valor_resgatado,
            SUM(CASE WHEN n.sentido = 'entrada' THEN n.quantidade  ELSE 0 END) AS qty_entrada,
            SUM(CASE WHEN n.sentido = 'saida'   THEN n.quantidade  ELSE 0 END) AS qty_saida
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE n.investidor_id = :investidor_id
          AND a.tipo = 'renda_fixa'
          AND (CAST(:ate AS date) IS NULL OR n.data <= :ate)
        GROUP BY n.ativo_id
        """,
        investidor_id=investidor_id,
        ate=ate,
    )
    return {r["ativo_id"]: dict(r) for r in rows}


def _rf_b3_vencimentos(conn: Connection, investidor_id: int, ate: date | None = None) -> dict[int, Decimal]:
    """Qty returned via B3 VENCIMENTO/RESGATE events (no brokerage note issued).

    For renda_fixa instruments that matured and were redeemed without a
    corresponding negociacoes entry (e.g. CDB matured while cash stayed at
    the bank — broker doesn't issue a nota de corretagem for that).

    Using the max of negociacoes.qty_saida vs this value avoids
    double-counting when both a nota AND a B3 event exist.
    """
    rows = fetch_all(
        conn,
        """
        SELECT ativo_id, SUM(COALESCE(quantidade, 0)) AS qty_vencida
        FROM b3_movimentacoes
        WHERE investidor_id = :investidor_id
          AND movimentacao IN ('VENCIMENTO/RESGATE SALDO EM CONTA', 'VENCIMENTO')
          AND sentido = 'Debito'
          AND ativo_id IS NOT NULL
          AND (CAST(:ate AS date) IS NULL OR data <= :ate)
        GROUP BY ativo_id
        """,
        investidor_id=investidor_id,
        ate=ate,
    )
    return {r["ativo_id"]: r["qty_vencida"] for r in rows}


def saldos_renda_fixa(
    conn: Connection, investidor_id: int, ate: date | None = None
) -> dict[int, tuple[Decimal, Decimal]]:
    """{ativo_id: (remaining units, principal still applied)} of renda_fixa
    ativos on `ate`. Redemptions count from the trade notes or, when larger,
    from B3's maturity events: CDBs that matured without a note, without
    double-counting when both record the same redemption."""
    vencidos = _rf_b3_vencimentos(conn, investidor_id, ate)
    saida = {}
    for ativo_id, rf in _negocios_rf(conn, investidor_id, ate).items():
        entrada = rf["qty_entrada"] or 0
        restante = max(0, entrada - max(rf["qty_saida"] or 0, vencidos.get(ativo_id) or 0))
        fracao = (restante / entrada) if entrada > 0 else 0
        saida[ativo_id] = (Decimal(restante), Decimal((rf["principal_investido"] or 0) * fracao))
    return saida


def _subscricao_posicoes(conn: Connection, investidor_id: int) -> dict[int, dict]:
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
    rows = fetch_all(
        conn,
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
                THEN b.valor ELSE 0 END)                                         AS custo_exercicio,
            -- The period ended after the last grant: whatever was not
            -- exercised expired (B3 writes these rows with quantity 0).
            COALESCE(MAX(CASE WHEN b.movimentacao IN (
                    'Direitos de Subscrição - Não Exercido',
                    'Direito Sobras de Subscrição - Não Exercido'
                ) THEN b.data END)
                >= MAX(CASE WHEN b.movimentacao IN (
                    'Direito de Subscrição', 'Direito Sobras de Subscrição'
                ) THEN b.data END), false)                                       AS expirou
        FROM b3_movimentacoes b
        JOIN ativos a ON a.id = b.ativo_id
        WHERE b.investidor_id = :investidor_id
          AND a.tipo IN ('direito_subscricao', 'recibo_subscricao')
          AND b.ativo_id IS NOT NULL
        GROUP BY b.ativo_id
        """,
        investidor_id=investidor_id,
    )
    return {r["ativo_id"]: dict(r) for r in rows}


def _rendimentos_por_ativo(conn: Connection, investidor_id: int) -> dict[int, dict]:
    """Income received per ativo from B3.

    Covers:
    - Rendimento        : FII / renda_fixa distributions
    - Dividendo         : dividends (net of Dividendo - Cancelado reversals)
    - JCP               : juros sobre capital próprio
    - PAGAMENTO DE JUROS: interest paid at maturity for renda_fixa
    """
    rows = fetch_all(
        conn,
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
        WHERE investidor_id = :investidor_id
          AND movimentacao IN (
            'Rendimento', 'Dividendo', 'Dividendo - Cancelado',
            'Juros Sobre Capital Próprio', 'PAGAMENTO DE JUROS'
          )
          AND ativo_id IS NOT NULL
        GROUP BY ativo_id
        """,
        investidor_id=investidor_id,
    )
    return {r["ativo_id"]: dict(r) for r in rows}


def ativos_do_investidor(conn: Connection, investidor_id: int) -> dict[int, dict]:
    """Catalog rows for every ativo the investidor has any activity in."""
    rows = fetch_all(
        conn,
        """
        SELECT id, tipo, subtipo, ticker, nome, vencimento
        FROM ativos
        WHERE id IN (
            SELECT ativo_id FROM negociacoes WHERE investidor_id = :investidor_id
            UNION
            SELECT ativo_id FROM b3_movimentacoes
            WHERE investidor_id = :investidor_id AND ativo_id IS NOT NULL
        )
        ORDER BY tipo, ticker
        """,
        investidor_id=investidor_id,
    )
    return {r["id"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def calcular_posicoes(conn: Connection, investidor_id: int) -> list[dict]:
    """Return one position dict per ativo that has any recorded activity.

    Fields in each dict
    -------------------
    ativo_id, ticker, nome, tipo, subtipo, vencimento (date | None)
    qty            – current quantity (units; for renda_fixa: remaining face units)
    custo_total    – cost of current holding (R$)
    preco_medio    – avg cost per unit (None for renda_fixa)
    rendimentos       – total 'Rendimento' income received (R$)
    dividendos        – total 'Dividendo' income (net of Cancelado) (R$)
    jcp               – total JCP received (R$)
    pagamento_juros   – interest paid at maturity (renda_fixa) (R$)
    total_income      – sum of all income types above (R$)
    is_open           – True if qty > 0
    tem_bonif_sem_custo – True if bonus shares in the position lack custo_por_cota
    custo_sem_origem  – True if position from custody transfer (cost unknown)
    custo_incompleto  – True if some shares came in without cost (counted as zero)
    """
    ativos = ativos_do_investidor(conn, investidor_id)

    saldos_equity  = saldos(conn, investidor_id)
    saldos_rf      = saldos_renda_fixa(conn, investidor_id)
    subscricoes    = _subscricao_posicoes(conn, investidor_id)
    # Receipts already turned into shares (B3 never debits them).
    recibos_convertidos: dict[int, Decimal] = {}
    for s in subscricoes_.carregar(conn, investidor_id):
        if s.recibo:
            recibos_convertidos[s.recibo.ativo_id] = (
                recibos_convertidos.get(s.recibo.ativo_id, Decimal(0)) + s.recibo.quantidade
            )
    rendimentos    = _rendimentos_por_ativo(conn, investidor_id)

    posicoes: list[dict] = []

    for ativo_id, ativo in ativos.items():
        tipo = ativo["tipo"]

        pos: dict = {
            "ativo_id":            ativo_id,
            "ticker":              ativo["ticker"] or "",
            "nome":                ativo["nome"] or "",
            "tipo":                tipo,
            "subtipo":             ativo["subtipo"] or "",
            "vencimento":          ativo["vencimento"],
            "qty":                 0,
            "custo_total":         0,
            "preco_medio":         None,
            "rendimentos":         0,
            "dividendos":          0,
            "jcp":                 0,
            "pagamento_juros":     0,
            "total_income":        0,
            "is_open":             False,
            "tem_bonif_sem_custo": False,
            "custo_sem_origem":    False,
            "custo_incompleto":    False,
        }

        if tipo in _EQUITY_TIPOS:
            saldo = saldos_equity.get(ativo_id)
            if saldo is None:
                continue

            if not saldo.tem_negociacoes and not saldo.tem_custo_informado:
                # No trade notes: the position comes from custody transfers
                # and/or genuine Atualização credits (e.g. corporate-action
                # conversions). Its cost is unknown.
                if saldo.qty <= 0:
                    continue
                pos["qty"]              = saldo.qty
                pos["custo_sem_origem"] = True
                pos["is_open"]          = True
            else:
                aberta = saldo.qty > 0
                pos["qty"]                 = saldo.qty
                pos["custo_total"]         = saldo.custo if aberta else 0
                pos["preco_medio"]         = saldo.preco_medio if aberta else None
                pos["tem_bonif_sem_custo"] = saldo.tem_bonif_sem_custo
                pos["custo_incompleto"]    = saldo.custo_desconhecido
                pos["is_open"]             = saldo.qty > 0.001

        elif tipo == "renda_fixa":
            if ativo_id not in saldos_rf:
                continue
            remaining, custo = saldos_rf[ativo_id]
            pos["qty"]         = remaining
            pos["custo_total"] = custo
            pos["is_open"]     = remaining > 0.001

        elif tipo in _SUBSCRICAO_TIPOS:
            sub            = subscricoes.get(ativo_id, {})
            qty_entrada    = sub.get("qty_entrada")    or 0
            qty_saida      = sub.get("qty_saida")      or 0
            custo_exerc    = sub.get("custo_exercicio") or 0

            qty_atual      = qty_entrada - qty_saida - recibos_convertidos.get(ativo_id, 0)
            if sub.get("expirou"):
                qty_atual = 0
            pos["qty"]       = qty_atual
            pos["custo_total"] = custo_exerc
            pos["is_open"]   = qty_atual > 0.001

        else:
            continue  # desconhecido or unhandled tipo

        # Income from B3 applies to all tipos
        rend = rendimentos.get(ativo_id, {})
        pos["rendimentos"]    = rend.get("rendimentos")    or 0
        pos["dividendos"]     = rend.get("dividendos")     or 0
        pos["jcp"]            = rend.get("jcp")            or 0
        pos["pagamento_juros"] = rend.get("pagamento_juros") or 0
        pos["total_income"]   = rend.get("total_income")   or 0

        posicoes.append(pos)

    return posicoes
