"""What the uploaded documents cover, and where they disagree.

Two sources describe the same trades: the brokerage notes (price, fees) and
the B3 statement ('Transferência - Liquidação' rows: quantity and gross
value, settled about two business days after the trade). Each fills the
other's gaps:

- a settlement with no note of that ativo nearby is a trade whose note was
  never uploaded. Its shares still count (custo_medio adds it at the B3
  gross value, without fees) and it is listed so the note can be sent;
- a settlement whose quantity differs from the notes nearby is listed as a
  divergence and left out (adding it could count the same shares twice).

The B3 statement itself says nothing about the period it was exported for,
so the coverage of each file is the span between its first and last row.
Gaps between files are what the user still has to download.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Connection

from database import fetch_all, scalar

# A settlement is matched with notes of the same ativo from these many days
# before it (D+2 in business days, plus weekends and holidays).
JANELA_LIQUIDACAO = timedelta(days=7)
# Periods between files closer than this are treated as continuous: B3
# lists only days with movements, so short silences are normal.
LACUNA_MINIMA = timedelta(days=10)

_TIPOS_NEGOCIADOS = ("acao", "fii", "bdr", "etf", "fundo")


@dataclass(frozen=True)
class Liquidacao:
    ativo_id: int
    data: date  # settlement date
    sentido: str  # 'entrada' | 'saida'
    quantidade: Decimal
    valor: Decimal | None

    @property
    def data_pregao(self) -> date:
        """Estimated trade date: two business days earlier (holidays ignored)."""
        d, n = self.data, 0
        while n < 2:
            d -= timedelta(days=1)
            n += d.weekday() < 5
        return d


@dataclass(frozen=True)
class Negocio:
    ativo_id: int
    data: date
    sentido: str
    quantidade: Decimal


def conciliar(
    liquidacoes: list[Liquidacao], negocios: list[Negocio]
) -> tuple[list[Liquidacao], list[Liquidacao]]:
    """(settlements with no note, settlements disagreeing with the notes).

    Settlements and notes are summed per ativo, direction and day first, so
    one order filled in parts on a note matches its single settlement."""
    notas: dict[tuple, Decimal] = defaultdict(Decimal)
    for n in negocios:
        notas[(n.ativo_id, n.sentido, n.data)] += n.quantidade
    grupos: dict[tuple, list[Liquidacao]] = defaultdict(list)
    for l in liquidacoes:
        grupos[(l.ativo_id, l.sentido, l.data)].append(l)

    usadas: set[tuple] = set()
    sem_nota, divergentes = [], []
    for (ativo_id, sentido, dia), ls in sorted(grupos.items(), key=lambda kv: kv[0][2]):
        qtd = sum((l.quantidade for l in ls), Decimal(0))
        valores = [l.valor for l in ls]
        junta = Liquidacao(
            ativo_id, dia, sentido, qtd,
            None if None in valores else sum(valores, Decimal(0)),
        )
        candidatas = [
            k for k in notas
            if k[0] == ativo_id and k[1] == sentido and dia - JANELA_LIQUIDACAO <= k[2] <= dia and k not in usadas
        ]
        iguais = [k for k in candidatas if notas[k] == qtd]
        if iguais:
            usadas.add(max(iguais, key=lambda k: k[2]))
        elif candidatas:
            divergentes.append(junta)
        else:
            sem_nota.append(junta)
    return sem_nota, divergentes


def carregar(conn: Connection, investidor_id: int, ate: date | None = None) -> tuple[list[Liquidacao], list[Liquidacao]]:
    """conciliar() over the investidor's documents of traded ativos."""
    liquidacoes = [
        Liquidacao(r["ativo_id"], r["data"], "entrada" if r["sentido"] == "Credito" else "saida",
                   r["quantidade"], r["valor"])
        for r in fetch_all(
            conn,
            """
            SELECT b.ativo_id, b.data, b.sentido, b.quantidade, b.valor
            FROM b3_movimentacoes b JOIN ativos a ON a.id = b.ativo_id
            WHERE b.investidor_id = :i AND b.movimentacao = 'Transferência - Liquidação'
              AND b.quantidade > 0 AND a.tipo = ANY(:tipos)
              AND (CAST(:ate AS date) IS NULL OR b.data <= :ate)
            """,
            i=investidor_id, tipos=list(_TIPOS_NEGOCIADOS), ate=ate,
        )
    ]
    if not liquidacoes:
        return [], []
    negocios = [
        Negocio(r["ativo_id"], r["data"], r["sentido"], r["quantidade"])
        for r in fetch_all(
            conn,
            """
            SELECT n.ativo_id, n.data, n.sentido, n.quantidade
            FROM negociacoes n JOIN ativos a ON a.id = n.ativo_id
            WHERE n.investidor_id = :i AND n.quantidade > 0 AND a.tipo = ANY(:tipos)
            """,
            i=investidor_id, tipos=list(_TIPOS_NEGOCIADOS),
        )
    ]
    return conciliar(liquidacoes, negocios)


# ---------------------------------------------------------------------------
# Periods covered
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Periodo:
    inicio: date
    fim: date


def juntar(periodos: list[Periodo]) -> list[Periodo]:
    """Merge overlapping or nearly adjacent periods (see LACUNA_MINIMA)."""
    juntos: list[Periodo] = []
    for p in sorted(periodos, key=lambda p: p.inicio):
        if juntos and p.inicio - juntos[-1].fim <= LACUNA_MINIMA:
            juntos[-1] = Periodo(juntos[-1].inicio, max(juntos[-1].fim, p.fim))
        else:
            juntos.append(p)
    return juntos


@dataclass
class Cobertura:
    periodos: list[Periodo]  # merged periods covered by B3 statements
    primeira_nota: date | None
    ultima_nota: date | None
    hoje: date
    sem_nota: list[Liquidacao] = field(default_factory=list)
    divergentes: list[Liquidacao] = field(default_factory=list)

    @property
    def lacunas(self) -> list[Periodo]:
        """Periods to download between the covered ones."""
        return [Periodo(a.fim + timedelta(days=1), b.inicio - timedelta(days=1))
                for a, b in zip(self.periodos, self.periodos[1:])]

    @property
    def antes(self) -> Periodo | None:
        """Notes older than any statement: the statement before them is missing.
        (A note a few days before it settles inside the statement.)"""
        if (self.primeira_nota and self.periodos
                and self.primeira_nota < self.periodos[0].inicio - JANELA_LIQUIDACAO):
            return Periodo(self.primeira_nota, self.periodos[0].inicio - timedelta(days=1))
        return None

    @property
    def proximo(self) -> Periodo | None:
        """From the day after the last statement to today."""
        if not self.periodos:
            return Periodo(self.primeira_nota, self.hoje) if self.primeira_nota else None
        inicio = self.periodos[-1].fim + timedelta(days=1)
        return Periodo(inicio, self.hoje) if inicio <= self.hoje else None

    @property
    def a_baixar(self) -> list[Periodo]:
        return [p for p in [self.antes, *self.lacunas, self.proximo] if p]

    @property
    def notas_depois_do_extrato(self) -> bool:
        """Notes after the last statement: their settlements are not checked yet."""
        return bool(self.periodos and self.ultima_nota and self.ultima_nota > self.periodos[-1].fim)


def cobertura(conn: Connection, investidor_id: int, hoje: date | None = None) -> Cobertura:
    periodos = juntar([
        Periodo(r["periodo_inicio"], r["periodo_fim"])
        for r in fetch_all(
            conn,
            """
            SELECT periodo_inicio, periodo_fim FROM b3_arquivos_processados
            WHERE investidor_id = :i AND periodo_inicio IS NOT NULL
            """,
            i=investidor_id,
        )
    ])
    sem_nota, divergentes = carregar(conn, investidor_id)
    return Cobertura(
        periodos=periodos,
        primeira_nota=scalar(conn, "SELECT MIN(data_pregao) FROM notas WHERE investidor_id = :i", i=investidor_id),
        ultima_nota=scalar(conn, "SELECT MAX(data_pregao) FROM notas WHERE investidor_id = :i", i=investidor_id),
        hoje=hoje or date.today(),
        sem_nota=sem_nota,
        divergentes=divergentes,
    )
