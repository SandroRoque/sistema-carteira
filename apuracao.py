"""Monthly income-tax calculation (apuração) for stock-exchange operations.

Rules and their sources are in regras_fiscais.py; this module applies them.

Per month, in date order:
1. Every sale's result is its proceeds minus the average cost at that
   moment (custo_medio). Fraction auctions (leilão de fração) are sales too.
2. Stocks: when the month's stock sales stay within R$ 20.000 a net gain is
   exempt; a net loss still joins the loss pool.
3. Regular operations (stocks, BDRs, units, rights) share one result and
   one loss pool, taxed at 15%; FIIs have their own, taxed at 20%.
4. The 0,005% withheld at source on sales is deducted from the tax.
5. Below R$ 10,00 no DARF is issued; the amount carries to later months.

Not computed: day trade (flagged when a same-day buy and sale is seen).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Connection

import regras_fiscais as R
from custo_medio import saldos
from database import fetch_all

ZERO = Decimal(0)
CENTAVO = Decimal("0.01")

# Ativo tipos that count as regular stock-exchange operations.
_TIPOS_BOLSA = ("acao", "bdr", "fii", "direito_subscricao", "recibo_subscricao")


def categoria(tipo: str, ticker: str | None) -> str:
    """'acao' (exemption-eligible stock), 'comum' (BDR, unit, right) or 'fii'."""
    if tipo == "fii":
        return "fii"
    if tipo == "acao" and not (ticker or "").endswith("11"):
        return "acao"
    # Units (ticker ending in 11) and everything else regular: no exemption.
    return "comum"


@dataclass(frozen=True)
class Venda:
    data: date
    ativo_id: int
    rotulo: str
    categoria: str
    valor: Decimal  # proceeds, net of fees
    custo: Decimal | None  # average-cost basis; None when unknown
    # Part of the cost is missing (bonus or credited shares counted at zero).
    incompleto: bool = False
    tipo: str = "venda"  # 'venda' | 'fracao' (leilão de fração)
    quantidade: Decimal | None = None
    # Taken from the B3 statement: no note, proceeds without fees.
    sem_nota: bool = False

    @property
    def resultado(self) -> Decimal | None:
        return None if self.custo is None else self.valor - self.custo


@dataclass
class Mes:
    mes: date  # first day
    vendas: list[Venda] = field(default_factory=list)
    irrf: Decimal = ZERO
    day_trade: list[str] = field(default_factory=list)

    vendas_acoes: Decimal = ZERO
    resultado_acoes: Decimal = ZERO
    isento: bool = False
    vendas_comuns: Decimal = ZERO  # BDRs, units, rights
    resultado_comuns: Decimal = ZERO
    vendas_fii: Decimal = ZERO
    resultado_fii: Decimal = ZERO

    prejuizo_comum_usado: Decimal = ZERO
    prejuizo_fii_usado: Decimal = ZERO
    prejuizo_comum_saldo: Decimal = ZERO  # after this month
    prejuizo_fii_saldo: Decimal = ZERO
    base_comum: Decimal = ZERO
    base_fii: Decimal = ZERO
    ir_bruto: Decimal = ZERO
    irrf_usado: Decimal = ZERO
    acumulado_anterior: Decimal = ZERO  # below-minimum tax brought in
    darf: Decimal = ZERO  # amount to pay for this month (0: nothing due)
    acumulado: Decimal = ZERO  # carried to the next month (below minimum)
    vencimento: date | None = None
    valor_pago: Decimal | None = None
    pago_em: date | None = None

    @property
    def periodo_apuracao(self) -> date:
        """Last day of the month, as the DARF asks for it."""
        return date(self.mes.year + (self.mes.month == 12), self.mes.month % 12 + 1, 1) - timedelta(days=1)

    @property
    def resultado_tributavel(self) -> Decimal:
        return self.base_comum + self.base_fii

    @property
    def ganho_isento(self) -> Decimal:
        return self.resultado_acoes if self.isento and self.resultado_acoes > 0 else ZERO

    @property
    def sem_custo(self) -> list[Venda]:
        return [v for v in self.vendas if v.custo is None]

    def sem_custo_em(self, categoria: str) -> bool:
        """Every sale of this category lacks a cost (its result is unknown)."""
        vendas = [v for v in self.vendas if v.categoria == categoria]
        return bool(vendas) and all(v.custo is None for v in vendas)

    @property
    def custo_incompleto(self) -> list[Venda]:
        return [v for v in self.vendas if v.incompleto]

    @property
    def sem_nota(self) -> list[Venda]:
        return [v for v in self.vendas if v.sem_nota]

    @property
    def confiavel(self) -> bool:
        """No sale with missing cost or note and no day trade left out."""
        return not (self.sem_custo or self.custo_incompleto or self.day_trade or self.sem_nota)

    @property
    def tem_vendas(self) -> bool:
        return bool(self.vendas)


def _arredondar(v: Decimal) -> Decimal:
    return v.quantize(CENTAVO, ROUND_HALF_UP)


def apurar(vendas: list[Venda], irrf: dict[date, Decimal] | None = None,
           day_trade: dict[date, list[str]] | None = None) -> list[Mes]:
    """Monthly calculation over the whole history (losses and below-minimum
    amounts carry across months and years)."""
    irrf = irrf or {}
    day_trade = day_trade or {}
    meses: dict[date, Mes] = {}
    for v in vendas:
        meses.setdefault(date(v.data.year, v.data.month, 1), Mes(date(v.data.year, v.data.month, 1))).vendas.append(v)
    for m, valor in irrf.items():
        meses.setdefault(m, Mes(m)).irrf = valor
    for m, ativos in day_trade.items():
        meses.setdefault(m, Mes(m)).day_trade = ativos

    prejuizo_comum = prejuizo_fii = ZERO
    irrf_saldo = ZERO
    acumulado = ZERO
    ano_irrf = None
    resultado = []
    for chave in sorted(meses):
        m = meses[chave]
        conhecidas = [v for v in m.vendas if v.custo is not None]
        for v in m.vendas:
            if v.categoria == "acao":
                m.vendas_acoes += v.valor
            elif v.categoria == "fii":
                m.vendas_fii += v.valor
            else:
                m.vendas_comuns += v.valor
        m.resultado_acoes = sum((v.valor - v.custo for v in conhecidas if v.categoria == "acao"), ZERO)
        m.resultado_comuns = sum((v.valor - v.custo for v in conhecidas if v.categoria == "comum"), ZERO)
        m.resultado_fii = sum((v.valor - v.custo for v in conhecidas if v.categoria == "fii"), ZERO)
        m.isento = m.vendas_acoes <= R.LIMITE_ISENCAO_ACOES.valor

        # Regular operations: the exempt stock gain stays out; a loss goes in.
        resultado_comum = m.resultado_comuns + (
            m.resultado_acoes if not m.isento or m.resultado_acoes < 0 else ZERO
        )
        m.base_comum, prejuizo_comum, m.prejuizo_comum_usado = _compensar(resultado_comum, prejuizo_comum)
        m.base_fii, prejuizo_fii, m.prejuizo_fii_usado = _compensar(m.resultado_fii, prejuizo_fii)
        m.prejuizo_comum_saldo, m.prejuizo_fii_saldo = prejuizo_comum, prejuizo_fii

        m.ir_bruto = (m.base_comum * R.ALIQUOTA_OPERACOES_COMUNS.valor
                      + m.base_fii * R.ALIQUOTA_FII.valor)

        # Withholding offsets the tax of the month and, if left over, of
        # later months of the same year.
        if ano_irrf != chave.year:
            irrf_saldo, ano_irrf = ZERO, chave.year
        irrf_saldo += m.irrf
        m.irrf_usado = min(irrf_saldo, m.ir_bruto)
        irrf_saldo -= m.irrf_usado

        m.acumulado_anterior = acumulado
        devido = _arredondar(m.ir_bruto - m.irrf_usado) + acumulado
        if devido < R.DARF_MINIMO.valor:
            m.darf, acumulado = ZERO, devido
        else:
            m.darf, acumulado = devido, ZERO
            m.vencimento = vencimento(chave)
        m.acumulado = acumulado
        resultado.append(m)
    return resultado


def _compensar(resultado: Decimal, prejuizo: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """(taxable base, loss pool after, loss used)."""
    if resultado <= 0:
        return ZERO, prejuizo - resultado, ZERO
    usado = min(prejuizo, resultado)
    return resultado - usado, prejuizo - usado, usado


# ---------------------------------------------------------------------------
# Due dates: last business day of the following month
# ---------------------------------------------------------------------------


def _pascoa(ano: int) -> date:
    """Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def feriados_bancarios(ano: int) -> set[date]:
    """National holidays plus the bank-closed days (Carnaval, Corpus Christi,
    31/12) that move a payment deadline."""
    pascoa = _pascoa(ano)
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25), (12, 31)]
    if ano >= 2024:
        fixos.append((11, 20))  # Consciência Negra, national since Lei 14.759/2023
    return {date(ano, m, d) for m, d in fixos} | {
        pascoa - timedelta(days=48),  # Carnaval (Monday)
        pascoa - timedelta(days=47),  # Carnaval (Tuesday)
        pascoa - timedelta(days=2),  # Sexta-feira Santa
        pascoa + timedelta(days=60),  # Corpus Christi
    }


def vencimento(mes_apuracao: date) -> date:
    """Last business day of the month after mes_apuracao."""
    ano, mes = mes_apuracao.year + (mes_apuracao.month == 12), mes_apuracao.month % 12 + 1
    dia = date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)
    feriados = feriados_bancarios(ano)
    while dia.weekday() >= 5 or dia in feriados:
        dia -= timedelta(days=1)
    return dia


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def apuracao(conn: Connection, investidor_id: int) -> list[Mes]:
    catalogo = {
        r["id"]: r for r in fetch_all(
            conn, "SELECT id, ticker, nome, tipo FROM ativos WHERE tipo = ANY(:t)", t=list(_TIPOS_BOLSA)
        )
    }
    vendas, day_trade = [], defaultdict(list)
    for ativo_id, s in saldos(conn, investidor_id, tipos=_TIPOS_BOLSA).items():
        a = catalogo[ativo_id]
        rotulo = a["ticker"] or a["nome"] or ""
        cat = categoria(a["tipo"], a["ticker"])
        for b in s.baixas:
            if b.evento.tipo not in ("venda", "fracao") or b.evento.custo is None or not b.evento.quantidade:
                continue  # a sale fully matched as day trade is not a regular sale
            vendas.append(Venda(
                b.evento.data, ativo_id, rotulo, cat, b.evento.custo, b.custo,
                incompleto=b.custo is not None and (b.tem_bonif_sem_custo or b.custo_incompleto),
                tipo=b.evento.tipo, quantidade=b.evento.quantidade, sem_nota=b.evento.sem_nota,
            ))
        for dt in s.day_trades:
            m = date(dt.data.year, dt.data.month, 1)
            if rotulo not in day_trade[m]:
                day_trade[m].append(rotulo)

    irrf = {
        r["mes"]: r["irrf"] for r in fetch_all(
            conn,
            """
            SELECT date_trunc('month', data_pregao)::date AS mes, SUM(irrf_sobre_operacoes) AS irrf
            FROM notas
            WHERE investidor_id = :i AND irrf_sobre_operacoes > :dispensa
            GROUP BY 1
            """,
            i=investidor_id,
            dispensa=R.IRRF_DISPENSA_ATE.valor,
        )
    }
    meses = apurar(vendas, irrf, dict(day_trade))
    pagos = {
        r["mes"]: r for r in fetch_all(
            conn, "SELECT mes, valor_pago, pago_em FROM darfs_pagos WHERE investidor_id = :i", i=investidor_id
        )
    }
    for m in meses:
        if m.mes in pagos:
            m.valor_pago, m.pago_em = pagos[m.mes]["valor_pago"], pagos[m.mes]["pago_em"]
    return meses


def origem_prejuizo(meses: list[Mes], fii: bool = False) -> list[Mes]:
    """Months whose losses make up the current loss pool: those that added
    to it since it was last empty."""
    origem: list[Mes] = []
    anterior = ZERO
    for m in meses:
        saldo = m.prejuizo_fii_saldo if fii else m.prejuizo_comum_saldo
        if saldo <= 0:
            origem = []
        elif saldo > anterior - (m.prejuizo_fii_usado if fii else m.prejuizo_comum_usado):
            origem.append(m)
        anterior = saldo
    return origem


def origem_acumulado(meses: list[Mes]) -> list[Mes]:
    """Months whose below-minimum tax is still carried forward."""
    origem: list[Mes] = []
    for m in meses:
        if m.acumulado <= 0:
            origem = []
        elif m.acumulado > m.acumulado_anterior:
            origem.append(m)
    return origem


def em_aberto(meses: list[Mes]) -> list[Mes]:
    """DARFs due and not marked paid, oldest first."""
    return [m for m in meses if m.darf > 0 and m.valor_pago is None]
