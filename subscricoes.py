"""Exercised subscription rights and the shares they turn into.

The B3 statement records a subscription in steps, on different tickers:

    Direito de Subscrição        ABCD1  Credito  (right granted)
    Direitos de Subscrição - Exercido
                                 ABCD1  Debito   qty × price paid
    Recibo de Subscrição         ABCD9  Credito  (receipt, until approval)
    Atualização                  ABCD3  Credito  the new shares

It never records the receipt leaving, nor ties the new shares to the
amount paid. This module does: an exercise is matched with the first later
credit of the same company (same 4-letter ticker root) with the same
quantity, within JANELA. The amount paid is the acquisition cost of the
new shares (custo_medio adds it to that credit), and the receipt of that
company and quantity closes on the credit date (posicoes).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Connection

from database import fetch_all

# From the exercise to the shares being credited (approval of the capital
# increase): usually weeks, sometimes a few months.
JANELA = timedelta(days=180)

_EXERCICIOS = ("Direitos de Subscrição - Exercido", "Direito Sobras de Subscrição - Exercido")


@dataclass(frozen=True)
class Exercicio:
    raiz: str
    data: date
    quantidade: Decimal
    valor: Decimal


@dataclass(frozen=True)
class Credito:
    b3_id: int
    ativo_id: int
    raiz: str
    data: date
    quantidade: Decimal


@dataclass(frozen=True)
class Recibo:
    ativo_id: int
    raiz: str
    data: date
    quantidade: Decimal


@dataclass(frozen=True)
class Subscricao:
    """An exercise matched with the shares it became."""

    credito: Credito
    custo: Decimal
    recibo: Recibo | None


def casar(exercicios: list[Exercicio], creditos: list[Credito], recibos: list[Recibo]) -> list[Subscricao]:
    usados: set[int] = set()
    recibos_usados: set[int] = set()
    resultado = []
    for e in sorted(exercicios, key=lambda e: e.data):
        credito = next(
            (c for c in sorted(creditos, key=lambda c: c.data)
             if c.b3_id not in usados and c.raiz == e.raiz and c.quantidade == e.quantidade
             and e.data < c.data <= e.data + JANELA),
            None,
        )
        if credito is None:
            continue
        usados.add(credito.b3_id)
        i = next(
            (i for i, r in enumerate(recibos)
             if i not in recibos_usados and r.raiz == e.raiz and r.quantidade == e.quantidade
             and e.data <= r.data <= credito.data),
            None,
        )
        if i is not None:
            recibos_usados.add(i)
        resultado.append(Subscricao(credito, e.valor, recibos[i] if i is not None else None))
    return resultado


def carregar(conn: Connection, investidor_id: int, ate: date | None = None) -> list[Subscricao]:
    rows = fetch_all(
        conn,
        """
        SELECT b.id, b.ativo_id, b.data, b.movimentacao, b.quantidade, b.valor, a.tipo,
               LEFT(COALESCE(a.ticker, split_part(b.produto_raw, ' ', 1)), 4) AS raiz
        FROM b3_movimentacoes b JOIN ativos a ON a.id = b.ativo_id
        WHERE b.investidor_id = :i AND b.quantidade > 0
          AND (CAST(:ate AS date) IS NULL OR b.data <= :ate)
          AND (b.movimentacao = ANY(:exercicios) OR b.movimentacao = 'Recibo de Subscrição'
               OR (b.movimentacao = 'Atualização' AND b.sentido = 'Credito'
                   AND a.tipo IN ('acao', 'fii', 'bdr', 'etf', 'fundo')))
        """,
        i=investidor_id, ate=ate, exercicios=list(_EXERCICIOS),
    )
    if not any(r["movimentacao"] in _EXERCICIOS for r in rows):
        return []
    exercicios = [
        Exercicio(r["raiz"], r["data"], r["quantidade"], r["valor"])
        for r in rows if r["movimentacao"] in _EXERCICIOS and r["valor"] is not None
    ]
    creditos = [
        Credito(r["id"], r["ativo_id"], r["raiz"], r["data"], r["quantidade"])
        for r in rows if r["movimentacao"] == "Atualização"
    ]
    recibos = [
        Recibo(r["ativo_id"], r["raiz"], r["data"], r["quantidade"])
        for r in rows if r["movimentacao"] == "Recibo de Subscrição"
    ]
    return casar(exercicios, creditos, recibos)
