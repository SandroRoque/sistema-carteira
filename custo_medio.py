"""Average cost (custo médio ponderado) by replaying events in date order.

The Receita Federal method: every acquisition raises the total cost; every
disposal removes shares at the average cost of that moment, leaving the
average unchanged. When a position reaches zero the next purchase starts a
fresh average — buy 100 @ 10, sell all, buy 100 @ 20 gives 20, not 15.

Events per ativo
----------------
compra                 + qty, + valor líquido (negociacoes entrada)
venda                  − qty at the current average (negociacoes saída)
bonificacao            + qty, + qty × custo_por_cota (0 while unknown)
desdobro               + qty at zero cost (dilutes the average)
atualizacao            + qty at the cost the user informed for shares received
                         without a note (custos_informados), else zero
fracao, resgate        − qty at the current average (Leilão de Fração;
                         B3 Resgate closing a fund absorbed in a merger)
transferencia_entrada  + qty at the current average (custody transfer); with
                         nothing held, at the informed cost if there is one
transferencia_saida    − qty at the current average

'Atualização' Crédito is ambiguous in B3 reports: usually it is a periodic
confirmation of the whole position (qty equal to what is held), sometimes a
genuine credit (e.g. a conversion). A confirmation is recognized by its qty
matching the position held before that day, and ignored.

Same-day order: atualizacao is compared against the previous day's position,
then entries, then exits — so a same-day buy and sell never dips below zero.

Day trade (same ativo, same day, same broker, buys and sells matched in
order — PR-IRPF-2026 q.705) is a separate regime that does not touch the
position: the matched quantity is taken out of those trades before the
replay and recorded in Saldo.day_trades. It is not taxed by this app.

The replay itself (`replay`) is pure; `carregar_eventos` reads the events
of one investidor from the database.
"""

from __future__ import annotations

from decimal import Decimal
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from sqlalchemy import Connection

from database import fetch_all

# Below this a quantity is treated as zero (position closed).
_ZERO = Decimal("0.000001")
# Tolerance for recognizing an 'Atualização' that just confirms the position.
_TOLERANCIA_ATUALIZACAO = Decimal("0.001")

_ENTRADAS = {"compra", "bonificacao", "desdobro", "transferencia_entrada"}
_SAIDAS = {"venda", "fracao", "resgate", "transferencia_saida"}
_ORDEM_NO_DIA = {"atualizacao": 0, **{t: 1 for t in _ENTRADAS}, **{t: 2 for t in _SAIDAS}}

EQUITY_TIPOS = ("acao", "fii", "bdr", "tesouro_direto")


@dataclass(frozen=True)
class Evento:
    data: date
    tipo: str
    quantidade: Decimal
    # compra / venda: valor líquido; fracao: proceeds of the auction;
    # bonificacao: qty × custo_por_cota, None while unknown;
    # atualizacao / transferencia_entrada: qty × informed cost, or None.
    custo: Decimal | None = None
    # negociacoes.id for compra / venda.
    negociacao_id: int | None = None
    # Where it came from: (corretora_id, nota_id) of a trade note; None for B3.
    nota: tuple[str, str] | None = None


@dataclass(frozen=True)
class Baixa:
    """Shares leaving the position, with the cost basis they carried out."""

    evento: Evento
    # Average cost at that moment; None when nothing was held (cost unknown).
    preco_medio: Decimal | None
    custo: Decimal | None
    # Some bonus shares in the average still lack their acquisition cost.
    tem_bonif_sem_custo: bool
    # Some shares in the average came in with unknown cost (counted as zero).
    custo_incompleto: bool = False


@dataclass(frozen=True)
class Passo:
    """The position right after one event (the ativo's history, step by step)."""

    evento: Evento
    qty: Decimal
    preco_medio: Decimal | None
    # An 'Atualização' recognized as a mere confirmation of the position.
    ignorado: bool = False


@dataclass(frozen=True)
class DayTrade:
    data: date
    corretora: str | None
    qtd: Decimal
    custo_compra: Decimal  # cost of the matched buys
    valor_venda: Decimal  # proceeds of the matched sales

    @property
    def resultado(self) -> Decimal:
        return self.valor_venda - self.custo_compra


@dataclass
class Saldo:
    qty: Decimal = 0
    custo: Decimal = 0
    # Unknown-cost bonus shares are part of the current position.
    tem_bonif_sem_custo: bool = False
    tem_negociacoes: bool = False
    # Some shares came in without a note and their cost was informed.
    tem_custo_informado: bool = False
    # Shares arrived into an empty position with no known cost. They count at
    # zero cost (as a bonus would) and the position is flagged until the cost
    # is informed or the position closes. When nothing else was ever paid,
    # sales have no cost basis at all (Baixa.custo None).
    custo_desconhecido: bool = False
    baixas: list[Baixa] = field(default_factory=list)
    passos: list[Passo] = field(default_factory=list)
    day_trades: list[DayTrade] = field(default_factory=list)

    @property
    def preco_medio(self) -> Decimal | None:
        return self.custo / self.qty if self.qty > _ZERO else None


def _ordenar(eventos: Iterable[Evento]) -> list[Evento]:
    return sorted(eventos, key=lambda e: (e.data, _ORDEM_NO_DIA[e.tipo], e.negociacao_id or 0))


def _parcela(ev: Evento, qtd: Decimal) -> Evento:
    """The same trade for `qtd` of its quantity (cost/proceeds pro rata)."""
    fracao = qtd / ev.quantidade if ev.quantidade else 0
    custo = None if ev.custo is None else ev.custo * fracao
    return Evento(ev.data, ev.tipo, qtd, custo, ev.negociacao_id, ev.nota)


def _separar_day_trade(eventos: list[Evento]) -> tuple[list[Evento], list[DayTrade]]:
    """Match same-day buys and sells at the same broker, first with first."""
    grupos: dict[tuple, list[Evento]] = {}
    for ev in eventos:
        if ev.tipo in ("compra", "venda"):
            grupos.setdefault((ev.data, ev.nota[0] if ev.nota else None), []).append(ev)
    trocados: dict[int, Evento] = {}
    day_trades = []
    for (dia, corretora), evs in grupos.items():
        compras = sorted((e for e in evs if e.tipo == "compra"), key=lambda e: e.negociacao_id or 0)
        vendas = sorted((e for e in evs if e.tipo == "venda"), key=lambda e: e.negociacao_id or 0)
        if not compras or not vendas:
            continue
        restante = {id(e): e.quantidade for e in compras + vendas}
        qtd = custo = valor = Decimal(0)
        i = j = 0
        while i < len(compras) and j < len(vendas):
            c, v = compras[i], vendas[j]
            q = min(restante[id(c)], restante[id(v)])
            custo += _parcela(c, q).custo or 0
            valor += _parcela(v, q).custo or 0
            qtd += q
            restante[id(c)] -= q
            restante[id(v)] -= q
            i += restante[id(c)] == 0
            j += restante[id(v)] == 0
        day_trades.append(DayTrade(dia, corretora, qtd, custo, valor))
        for e in compras + vendas:
            if restante[id(e)] != e.quantidade:
                trocados[id(e)] = _parcela(e, restante[id(e)])
    return [trocados.get(id(e), e) for e in eventos], day_trades


def replay(eventos: Iterable[Evento]) -> Saldo:
    """Apply one ativo's events in date order and return the resulting position."""
    saldo = Saldo()
    eventos, saldo.day_trades = _separar_day_trade(list(eventos))
    eventos = _ordenar(eventos)
    qty_no_inicio_do_dia = 0
    dia = None

    for ev in eventos:
        if ev.data != dia:
            dia, qty_no_inicio_do_dia = ev.data, saldo.qty
        q = ev.quantidade or 0

        if ev.tipo in ("compra", "venda"):
            saldo.tem_negociacoes = True

        if ev.tipo == "atualizacao":
            if abs(q - qty_no_inicio_do_dia) < _TOLERANCIA_ATUALIZACAO:
                # Confirmation of the position, not a credit.
                saldo.passos.append(Passo(ev, saldo.qty, saldo.preco_medio, ignorado=True))
                continue
            vazia = saldo.qty <= _ZERO
            saldo.qty += q
            if ev.custo is not None:
                saldo.custo += ev.custo
                saldo.tem_custo_informado = True
            elif vazia:
                # A credit on top of a position dilutes it at zero cost (e.g. a
                # conversion bonus); into nothing, its cost is simply unknown.
                saldo.custo_desconhecido = True
        elif ev.tipo == "compra":
            saldo.qty += q
            saldo.custo += ev.custo or 0
        elif ev.tipo == "bonificacao":
            saldo.qty += q
            if ev.custo is None:
                saldo.tem_bonif_sem_custo = True
            else:
                saldo.custo += ev.custo
        elif ev.tipo == "desdobro":
            saldo.qty += q
        elif ev.tipo == "transferencia_entrada":
            pm = saldo.preco_medio
            saldo.qty += q
            if pm is not None:
                saldo.custo += pm * q
            elif ev.custo is not None:
                saldo.custo += ev.custo
                saldo.tem_custo_informado = True
            else:
                saldo.custo_desconhecido = True
        elif ev.tipo in _SAIDAS:
            _baixar(saldo, ev, q)
        else:
            raise ValueError(f"tipo de evento desconhecido: {ev.tipo}")
        saldo.passos.append(Passo(ev, saldo.qty, saldo.preco_medio))

    return saldo


def _baixar(saldo: Saldo, ev: Evento, q: Decimal) -> None:
    pm = saldo.preco_medio
    if pm is None or (saldo.custo_desconhecido and saldo.custo == 0):
        custo = None  # nothing held, or nothing held was ever paid for
    elif q >= saldo.qty:
        custo = saldo.custo  # whole position: no rounding residue left behind
    else:
        custo = pm * q
    saldo.baixas.append(Baixa(ev, pm, custo, saldo.tem_bonif_sem_custo, saldo.custo_desconhecido))

    saldo.qty -= q
    if saldo.qty <= _ZERO:
        # Closed (or sold more than recorded): the next purchase starts afresh.
        saldo.custo = 0
        saldo.tem_bonif_sem_custo = False
        saldo.custo_desconhecido = False
    elif custo is not None:
        saldo.custo -= custo


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def carregar_eventos(
    conn: Connection,
    investidor_id: int,
    ate: date | None = None,
    tipos: Iterable[str] | None = EQUITY_TIPOS,
) -> dict[int, list[Evento]]:
    """{ativo_id: events} for the investidor's ativos of the given tipos
    (None = all), optionally only up to (and including) `ate`."""
    rows = fetch_all(
        conn,
        """
        SELECT n.ativo_id, n.data, n.id AS negociacao_id, n.corretora_id, n.nota_id,
               CASE WHEN n.sentido = 'entrada' THEN 'compra' ELSE 'venda' END AS tipo,
               COALESCE(n.quantidade, 0) AS quantidade,
               n.valor_liquido AS custo
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE n.investidor_id = :investidor_id
          AND (CAST(:tipos AS text[]) IS NULL OR a.tipo = ANY(:tipos))
          AND (CAST(:ate AS date) IS NULL OR n.data <= :ate)

        UNION ALL

        SELECT b.ativo_id, b.data, NULL, NULL, NULL,
               CASE
                   WHEN b.movimentacao = 'Bonificação em Ativos' THEN 'bonificacao'
                   WHEN b.movimentacao = 'Desdobro'              THEN 'desdobro'
                   WHEN b.movimentacao = 'Atualização'           THEN 'atualizacao'
                   WHEN b.movimentacao = 'Leilão de Fração'      THEN 'fracao'
                   WHEN b.movimentacao = 'Resgate'               THEN 'resgate'
                   WHEN b.sentido = 'Credito'                    THEN 'transferencia_entrada'
                   ELSE 'transferencia_saida'
               END,
               COALESCE(b.quantidade, 0),
               CASE
                   WHEN b.movimentacao = 'Bonificação em Ativos' THEN b.quantidade * bc.custo_por_cota
                   -- A fraction auction is a sale: its custo is the proceeds.
                   WHEN b.movimentacao = 'Leilão de Fração' THEN b.valor
                   WHEN b.sentido = 'Credito' AND b.movimentacao IN ('Atualização', 'Transferência')
                       THEN b.quantidade * ci.custo_por_cota
               END
        FROM b3_movimentacoes b
        JOIN ativos a ON a.id = b.ativo_id
        LEFT JOIN bonificacoes bc ON bc.b3_movimentacao_id = b.id
        LEFT JOIN custos_informados ci ON ci.investidor_id = b.investidor_id AND ci.ativo_id = b.ativo_id
        WHERE b.investidor_id = :investidor_id
          AND (CAST(:tipos AS text[]) IS NULL OR a.tipo = ANY(:tipos))
          AND (CAST(:ate AS date) IS NULL OR b.data <= :ate)
          AND (
              b.movimentacao IN ('Bonificação em Ativos', 'Leilão de Fração', 'Transferência')
              OR (b.movimentacao IN ('Desdobro', 'Atualização', 'Resgate') AND b.sentido = 'Credito')
          )
        """,
        investidor_id=investidor_id,
        tipos=None if tipos is None else list(tipos),
        ate=ate,
    )
    eventos: dict[int, list[Evento]] = defaultdict(list)
    for r in rows:
        eventos[r["ativo_id"]].append(
            Evento(
                r["data"], r["tipo"], r["quantidade"], r["custo"], r["negociacao_id"],
                (r["corretora_id"], r["nota_id"]) if r["corretora_id"] else None,
            )
        )
    return dict(eventos)


def saldos(
    conn: Connection,
    investidor_id: int,
    ate: date | None = None,
    tipos: Iterable[str] | None = EQUITY_TIPOS,
) -> dict[int, Saldo]:
    """{ativo_id: position} replayed from every event up to `ate`."""
    return {
        ativo_id: replay(evs)
        for ativo_id, evs in carregar_eventos(conn, investidor_id, ate, tipos).items()
    }
