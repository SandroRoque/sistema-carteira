"""Pending items: data no document carries, which the user must supply for
average cost, results and the tax return to be complete.

Pendências (counted in the header badge, each fixable inline):
  bonificacao   bonus shares whose cost per share is not informed
  sem_custo     shares that arrived without a trade note and with no cost
                (the whole position, or part of it when later bought too).
                Often an incorporação or conversão: the user can say which
                ativo they came from and its cost carries over (conversoes)
  vendido_mais  more sold than bought: a purchase note is missing
  sem_nota      a trade settled at B3 with no note: it counts in the
                position at the B3 gross value, fees missing, until the note
                (or a manual entry) arrives
  divergente    a B3 settlement whose quantity disagrees with the notes

Avisos (informational, not counted): a stale B3 statement, uploads that
could not be read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Connection

import cobertura
from custo_medio import Saldo, saldos
from database import execute, fetch_all, fetch_one, scalar
from posicoes import calcular_posicoes

_ZERO = Decimal("0.000001")

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
    # sem_custo: only part of the position (other shares were bought).
    parcial: bool = False
    # sem_custo: the B3 credit, and the ativos held that day it may come
    # from, as (ativo_id, rótulo, why it is likely or None), likeliest first.
    credito_id: int | None = None
    origens: tuple[tuple[int, str, str | None], ...] = ()
    # sem_nota / divergente: the settlement.
    liquidacao: cobertura.Liquidacao | None = None


@dataclass(frozen=True)
class Aviso:
    tipo: str
    texto: str
    data: date | None = None


def _rotulo(r) -> str:
    return r["ticker"] or r["nome"] or ""


def _rotulo_de(conn: Connection, ativo_id: int) -> str:
    r = fetch_one(conn, "SELECT ticker, nome FROM ativos WHERE id = :id", id=ativo_id)
    return _rotulo(r) if r else ""


def _qtd_em(saldo: Saldo, dia: date) -> Decimal:
    """Position at the end of `dia`."""
    qty = Decimal(0)
    for p in saldo.passos:
        if p.evento.data > dia:
            break
        qty = p.qty
    return qty


def _zerou_em(saldo: Saldo, depois_de: date, ate: date) -> date | None:
    """First day in (depois_de, ate] the position reached zero."""
    for p in saldo.passos:
        if depois_de < p.evento.data <= ate and p.qty <= _ZERO:
            return p.evento.data
    return None


def _origens(replays: dict[int, Saldo], rotulos: dict[int, str], destino: int, dia: date,
             movimentados: set[int]) -> tuple[tuple[int, str, str | None], ...]:
    """Ativos held on `dia` that a credit of `destino` may have replaced.

    Evidence, strongest first: B3 moved it the same day (an incorporação
    shows as Atualização of both sides), same company (same 4-letter ticker
    root), and, only as support, its position closed within a month. It is
    marked likely with one of the first two; the third alone is too common."""
    raiz = rotulos.get(destino, "")[:4]
    candidatos = []
    for ativo_id, s in replays.items():
        if ativo_id == destino or _qtd_em(s, dia) <= _ZERO:
            continue
        rotulo = rotulos[ativo_id]
        motivos, forte = [], False
        if ativo_id in movimentados:
            motivos.append(f"a B3 também movimentou {rotulo} em {dia:%d/%m/%Y}")
            forte = True
        if rotulo[:4] == raiz:
            motivos.append(f"mesmo código de empresa ({raiz})")
            forte = True
        zerou = _zerou_em(s, dia, dia + timedelta(days=31))
        if zerou:
            motivos.append(f"{rotulo} saiu da carteira em {zerou:%d/%m/%Y}")
        pontos = 2 * len(motivos) - (zerou is not None)
        candidatos.append((-pontos, rotulo, ativo_id, "; ".join(motivos) if forte else None))
    candidatos.sort()
    return tuple((a, r, m) for _, r, a, m in candidatos)


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
    replays = saldos(conn, investidor_id)
    rotulos = {
        r["id"]: _rotulo(r)
        for r in fetch_all(conn, "SELECT id, ticker, nome FROM ativos WHERE id = ANY(:ids)", ids=list(replays))
    }
    for p in calcular_posicoes(conn, investidor_id):
        if p["is_open"] and (p["custo_sem_origem"] or p["custo_incompleto"]):
            creditos = [
                passo.evento for passo in replays[p["ativo_id"]].passos
                if passo.evento.tipo in ("atualizacao", "transferencia_entrada", "conversao_entrada")
                and not passo.ignorado and passo.evento.custo is None
            ]
            primeiro = creditos[0] if creditos else None
            origens = ()
            if primeiro and primeiro.b3_id:
                movimentados = {
                    r["ativo_id"] for r in fetch_all(
                        conn,
                        """
                        SELECT DISTINCT ativo_id FROM b3_movimentacoes
                        WHERE investidor_id = :i AND data = :d AND ativo_id IS NOT NULL
                          AND movimentacao IN ('Atualização', 'Resgate', 'Transferência')
                        """,
                        i=investidor_id, d=primeiro.data,
                    )
                }
                origens = _origens(replays, rotulos, p["ativo_id"], primeiro.data, movimentados)
            pendencias.append(Pendencia(
                "sem_custo", p["ativo_id"], p["ativo_id"], p["ticker"] or p["nome"],
                primeiro.data if primeiro else None,
                sum((e.quantidade for e in creditos), Decimal(0)) if creditos else p["qty"],
                parcial=not p["custo_sem_origem"],
                credito_id=primeiro.b3_id if primeiro else None,
                origens=origens,
            ))
        elif p["qty"] < Decimal("-0.001"):
            pendencias.append(Pendencia(
                "vendido_mais", p["ativo_id"], p["ativo_id"], p["ticker"] or p["nome"], None, -p["qty"],
            ))
    sem_nota, divergentes = cobertura.carregar(conn, investidor_id)
    for tipo, liquidacoes in (("sem_nota", sem_nota), ("divergente", divergentes)):
        for l in liquidacoes:
            pendencias.append(Pendencia(
                tipo, l.ativo_id, l.ativo_id, rotulos.get(l.ativo_id) or _rotulo_de(conn, l.ativo_id),
                l.data, l.quantidade, liquidacao=l,
            ))
    return pendencias


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


def informar_conversao(conn: Connection, investidor_id: int, mov_id: int, origem_id: int) -> None:
    """The B3 credit `mov_id` replaced the position in `origem_id`."""
    credito = fetch_one(
        conn,
        """
        SELECT ativo_id, data FROM b3_movimentacoes
        WHERE id = :id AND investidor_id = :i AND sentido = 'Credito'
          AND movimentacao IN ('Atualização', 'Transferência')
        """,
        id=mov_id,
        i=investidor_id,
    )
    if credito is None:
        raise PendenciaInvalida("Crédito não encontrado nesta carteira.")
    if origem_id == credito["ativo_id"]:
        raise PendenciaInvalida("Escolha outro ativo como origem.")
    origem = saldos(conn, investidor_id, ate=credito["data"], tipos=None).get(origem_id)
    if origem is None or origem.qty <= Decimal("0.000001"):
        raise PendenciaInvalida("Você não tinha esse ativo nessa data.")
    execute(
        conn,
        """
        INSERT INTO conversoes (b3_movimentacao_id, investidor_id, ativo_origem_id)
        VALUES (:id, :i, :origem)
        ON CONFLICT (b3_movimentacao_id)
        DO UPDATE SET ativo_origem_id = EXCLUDED.ativo_origem_id, informado_em = now()
        """,
        id=mov_id,
        i=investidor_id,
        origem=origem_id,
    )


def desfazer_conversao(conn: Connection, investidor_id: int, mov_id: int) -> int | None:
    """Remove a conversion; returns the ativo it was credited to."""
    return scalar(
        conn,
        """
        DELETE FROM conversoes c USING b3_movimentacoes b
        WHERE c.b3_movimentacao_id = :id AND c.investidor_id = :i AND b.id = c.b3_movimentacao_id
        RETURNING b.ativo_id
        """,
        id=mov_id,
        i=investidor_id,
    )
