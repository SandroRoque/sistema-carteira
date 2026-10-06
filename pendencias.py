"""Pending items: data no document carries, which the user must supply for
average cost, results and the tax return to be complete.

Pendências (counted in the header badge, each fixable inline):
  bonificacao   bonus shares whose cost per share is not informed
  sem_custo     an open position that arrived without a trade note
  vendido_mais  more sold than bought: a purchase note is missing

Avisos (informational, not counted): a stale B3 statement, uploads that
could not be read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Connection

from database import execute, fetch_all, fetch_one, scalar
from posicoes import calcular_posicoes

# A B3 statement older than this is worth refreshing.
EXTRATO_ANTIGO = timedelta(days=40)


@dataclass(frozen=True)
class Pendencia:
    tipo: str
    chave: int  # b3_movimentacao_id (bonificacao) or ativo_id
    ativo_id: int
    rotulo: str
    data: date | None
    qtd: Decimal | None


@dataclass(frozen=True)
class Aviso:
    tipo: str
    texto: str
    data: date | None = None


def _rotulo(r) -> str:
    return r["ticker"] or r["nome"] or ""


def listar(conn: Connection, investidor_id: int) -> list[Pendencia]:
    pendencias = [
        Pendencia("bonificacao", r["id"], r["ativo_id"], _rotulo(r), r["data"], r["quantidade"])
        for r in fetch_all(
            conn,
            """
            SELECT b.id, b.ativo_id, b.data, b.quantidade, a.ticker, a.nome
            FROM b3_movimentacoes b
            JOIN bonificacoes bc ON bc.b3_movimentacao_id = b.id
            JOIN ativos a ON a.id = b.ativo_id
            WHERE b.investidor_id = :i AND bc.custo_por_cota IS NULL
            ORDER BY b.data DESC
            """,
            i=investidor_id,
        )
    ]
    for p in calcular_posicoes(conn, investidor_id):
        if p["is_open"] and p["custo_sem_origem"]:
            pendencias.append(Pendencia(
                "sem_custo", p["ativo_id"], p["ativo_id"], p["ticker"] or p["nome"],
                _primeiro_credito(conn, investidor_id, p["ativo_id"]), p["qty"],
            ))
        elif p["qty"] < Decimal("-0.001"):
            pendencias.append(Pendencia(
                "vendido_mais", p["ativo_id"], p["ativo_id"], p["ticker"] or p["nome"], None, -p["qty"],
            ))
    return pendencias


def _primeiro_credito(conn: Connection, investidor_id: int, ativo_id: int) -> date | None:
    return scalar(
        conn,
        """
        SELECT MIN(data) FROM b3_movimentacoes
        WHERE investidor_id = :i AND ativo_id = :a AND sentido = 'Credito'
          AND movimentacao IN ('Transferência', 'Atualização')
        """,
        i=investidor_id,
        a=ativo_id,
    )


def avisos(conn: Connection, investidor_id: int, hoje: date | None = None) -> list[Aviso]:
    hoje = hoje or date.today()
    resultado = []
    ultimo = scalar(conn, "SELECT MAX(data) FROM b3_movimentacoes WHERE investidor_id = :i", i=investidor_id)
    if ultimo is not None and hoje - ultimo > EXTRATO_ANTIGO:
        resultado.append(Aviso("extrato_antigo", "", ultimo))
    for r in fetch_all(
        conn,
        """
        SELECT nome_arquivo, mensagem, criado_em FROM uploads
        WHERE status = 'erro' AND criado_em > now() - interval '30 days'
          AND (investidor_id IS NULL OR investidor_id = :i)
        ORDER BY id DESC LIMIT 5
        """,
        i=investidor_id,
    ):
        resultado.append(Aviso("upload_erro", f"{r['nome_arquivo']}: {r['mensagem']}", r["criado_em"].date()))
    return resultado


def contar(conn: Connection, investidor_id: int) -> int:
    return len(listar(conn, investidor_id))


# ---------------------------------------------------------------------------
# Fixes (tenant-scoped connection: RLS checks ownership)
# ---------------------------------------------------------------------------


class PendenciaInvalida(ValueError):
    pass


def informar_custo_bonificacao(conn: Connection, investidor_id: int, mov_id: int, custo: Decimal) -> None:
    if custo < 0:
        raise PendenciaInvalida("O custo não pode ser negativo.")
    alterada = scalar(
        conn,
        """
        UPDATE bonificacoes SET custo_por_cota = :custo
        WHERE b3_movimentacao_id = :id
          AND b3_movimentacao_id IN (SELECT id FROM b3_movimentacoes WHERE investidor_id = :i)
        RETURNING b3_movimentacao_id
        """,
        custo=custo,
        id=mov_id,
        i=investidor_id,
    )
    if alterada is None:
        raise PendenciaInvalida("Bonificação não encontrada.")


def informar_custo_sem_nota(conn: Connection, investidor_id: int, ativo_id: int, custo: Decimal) -> None:
    if custo < 0:
        raise PendenciaInvalida("O custo não pode ser negativo.")
    if not fetch_one(
        conn,
        "SELECT 1 FROM b3_movimentacoes WHERE investidor_id = :i AND ativo_id = :a LIMIT 1",
        i=investidor_id,
        a=ativo_id,
    ):
        raise PendenciaInvalida("Ativo não encontrado nesta carteira.")
    execute(
        conn,
        """
        INSERT INTO custos_informados (investidor_id, ativo_id, custo_por_cota)
        VALUES (:i, :a, :custo)
        ON CONFLICT (investidor_id, ativo_id)
        DO UPDATE SET custo_por_cota = EXCLUDED.custo_por_cota, informado_em = now()
        """,
        i=investidor_id,
        a=ativo_id,
        custo=custo,
    )
