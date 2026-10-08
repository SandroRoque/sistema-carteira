"""What the annual IRPF declaration needs from a portfolio, by sheet.

For a calendar year: each listed asset held on 31/12 of the year or of the
year before (Bens e Direitos, at cost), the income received (Rendimentos
Isentos, Tributação Exclusiva) and the exempt stock gains. Every code comes
from regras_fiscais, with its source; CNPJs come from the catalog or from
CVM's data (cnpjs.py), never typed in.

Fixed income and Tesouro Direto appear as assets (at the amount applied);
their income is not computed, because the tax withheld at source is not in
the documents: the brokers' informes de rendimentos give it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection

import apuracao
import cnpjs
import formato
import painel
import regras_fiscais as R
from custo_medio import saldos
from database import fetch_all
from posicoes import saldos_renda_fixa

ZERO = Decimal(0)
TIPOS = ("acao", "bdr", "fii", "etf", "fundo", "tesouro_direto", "direito_subscricao", "recibo_subscricao")

# First word of a private bond's name (the nota's title) → taxed or exempt.
_TITULOS_TRIBUTAVEIS = {"CDB", "RDB", "LC", "LF"}
_TITULOS_ISENTOS = {"LCI", "LCA", "LCD", "CRI", "CRA", "LIG"}


def codigo_do_bem(tipo: str, subtipo: str | None, nome: str | None = None) -> R.Regra | None:
    """Bens e Direitos (grupo, código) of an asset, or None when the app
    cannot tell (FIP, FIDC and unrecognized funds have more than one; a
    debenture is exempt only when incentivized)."""
    if tipo == "tesouro_direto":
        return R.BEM_TITULOS_TRIBUTAVEIS
    if tipo == "renda_fixa":
        especie = (nome or "").split()[0].upper() if (nome or "").strip() else ""
        if especie in _TITULOS_TRIBUTAVEIS:
            return R.BEM_TITULOS_TRIBUTAVEIS
        if especie in _TITULOS_ISENTOS:
            return R.BEM_TITULOS_ISENTOS
        return None
    if tipo == "acao":
        return R.BEM_UNITS if subtipo == "unit" else R.BEM_ACOES
    if tipo == "bdr":
        return R.BEM_BDR
    if tipo in ("direito_subscricao", "recibo_subscricao"):
        return R.BEM_DIREITOS
    if tipo == "fii":
        return R.BEM_FIAGRO if subtipo == "fiagro" else R.BEM_FII
    if tipo == "etf":
        return R.BEM_ETF_RENDA_FIXA if subtipo == "renda_fixa" else R.BEM_ETF
    if tipo == "fundo" and subtipo == "infra":
        return R.BEM_FUNDO_INFRA
    return None


_UNIDADE = {
    "acao": ("ação", "ações"), "bdr": ("BDR", "BDRs"), "tesouro_direto": ("título", "títulos"),
    "direito_subscricao": ("direito de subscrição", "direitos de subscrição"),
    "recibo_subscricao": ("recibo de subscrição", "recibos de subscrição"),
}


def _unidade(tipo: str, subtipo: str | None, qtd: Decimal) -> str:
    if subtipo == "unit":
        return "unit" if qtd == 1 else "units"
    um, varios = _UNIDADE.get(tipo, ("cota", "cotas"))
    return um if qtd == 1 else varios


@dataclass
class Bem:
    ativo_id: int
    ticker: str
    nome: str  # legal name of the issuer or fund, when known
    tipo: str
    subtipo: str | None
    codigo: R.Regra | None
    cnpj: str | None
    qtd_anterior: Decimal
    custo_anterior: Decimal
    qtd: Decimal
    custo: Decimal
    custodia: list[str]
    # Part of the cost is unknown (bonus or credited shares counted at zero).
    incompleto: bool = False
    # A bond's description ("CDB Banco X, 110% do CDI, vencimento 15/03/2027"):
    # it replaces the quantity and average price, which say little for bonds.
    titulo: str | None = None

    @property
    def rotulo(self) -> str:
        """How the page names it: the ticker, or a bond's short description."""
        return self.titulo.split(",")[0] if self.titulo else self.ticker

    @property
    def renda_fixa(self) -> bool:
        return self.tipo in ("renda_fixa", "tesouro_direto")

    @property
    def discriminacao(self) -> str:
        nome = f" ({self.nome})" if self.nome else ""
        if self.titulo:
            texto = f"{self.titulo}. " + ("Valor aplicado." if self.qtd > 0 else "Resgatado no ano.")
        elif self.qtd <= 0:
            texto = f"{self.ticker}{nome}: posição encerrada no ano."
        else:
            preco = formato.brl(self.custo / self.qtd)
            unidade = _unidade(self.tipo, self.subtipo, self.qtd)
            texto = f"{formato.qtd(self.qtd)} {unidade} {self.ticker}{nome}. Preço médio {preco}."
        if self.custodia:
            texto += f" Custódia: {', '.join(self.custodia)}"
            texto += "" if texto.endswith(".") else "."
        return texto


@dataclass
class Rendimento:
    linha: R.Regra
    descricao: str
    fonte: str
    cnpj: str | None
    valor: Decimal
    tickers: list[str] = field(default_factory=list)


@dataclass
class ConversaoDeAcoes:
    """Shares of one company received for shares of another (conversoes): a
    possible ganho de capital event (regras_fiscais INCORPORACAO_DE_ACOES_GCAP)."""
    data: date
    origem: str
    destino: str
    destino_id: int
    quantidade: Decimal
    custo: Decimal | None  # cost carried from the old shares


def conversoes_de_acoes(conn: Connection, investidor_id: int, ano: int) -> list[ConversaoDeAcoes]:
    entradas = [
        (ativo_id, p.evento)
        for ativo_id, s in saldos(conn, investidor_id, date(ano, 12, 31), ("acao",)).items()
        for p in s.passos
        if p.evento.tipo == "conversao_entrada" and p.evento.data.year == ano
    ]
    if not entradas:
        return []
    ids = {a for a, _ in entradas} | {e.contraparte for _, e in entradas}
    catalogo = {r["id"]: r for r in fetch_all(
        conn, "SELECT id, tipo, ticker, nome FROM ativos WHERE id = ANY(:ids)", ids=list(ids))}
    saida = []
    for ativo_id, e in entradas:
        origem = catalogo.get(e.contraparte)
        if origem is None or origem["tipo"] != "acao":
            continue
        destino = catalogo[ativo_id]
        saida.append(ConversaoDeAcoes(
            e.data, origem["ticker"] or origem["nome"] or "", destino["ticker"] or destino["nome"] or "",
            ativo_id, e.quantidade, None if e.custo is None else _centavos(e.custo),
        ))
    return sorted(saida, key=lambda c: c.data)


@dataclass
class MesRendaVariavel:
    """One month of the Renda Variável sheets, field by field as the program
    asks for them (regras_fiscais RV_*). Amounts are in reais; losses negative."""
    mes: date
    # Operações comuns e day trade
    comum_resultado: Decimal
    comum_prejuizo_anterior: Decimal  # "Resultado negativo até o mês anterior"
    comum_base: Decimal
    comum_prejuizo: Decimal  # "Prejuízo a compensar" after the month
    comum_imposto: Decimal
    dt_resultado: Decimal
    dt_prejuizo_anterior: Decimal
    dt_base: Decimal
    dt_prejuizo: Decimal
    dt_imposto: Decimal
    irrf_day_trade: Decimal
    irrf_day_trade_estimado: bool
    irrf_lei_11033: Decimal  # the 0,005% withheld on sales
    # Operações em FII ou Fiagro
    fii_resultado: Decimal
    fii_prejuizo_anterior: Decimal
    fii_base: Decimal
    fii_prejuizo: Decimal
    fii_imposto: Decimal
    darf: Decimal  # what the monthly calculation says to pay, both sheets
    pago: Decimal | None
    outros_a_vista: list[str]  # BDRs, ETFs, units, rights in the stocks' line
    confiavel: bool

    @property
    def total_imposto(self) -> Decimal:
        return self.comum_imposto + self.dt_imposto


def renda_variavel(meses: list[apuracao.Mes], ano: int) -> list[MesRendaVariavel]:
    """The months of `ano` with anything to fill, from the whole history
    (the loss pools come from earlier years)."""
    saida = []
    anterior = None
    for m in meses:
        if m.mes.year == ano:
            ant_comum = anterior.prejuizo_comum_saldo if anterior else ZERO
            ant_dt = anterior.prejuizo_day_trade_saldo if anterior else ZERO
            ant_fii = anterior.prejuizo_fii_saldo if anterior else ZERO
            comum = m.resultado_comuns + (m.resultado_acoes if not m.isento or m.resultado_acoes < 0 else ZERO)
            tem_algo = (m.tem_vendas or m.resultado_day_trade or m.irrf or m.irrf_day_trade)
            if tem_algo:
                saida.append(MesRendaVariavel(
                    mes=m.mes,
                    comum_resultado=comum,
                    comum_prejuizo_anterior=ant_comum,
                    comum_base=m.base_comum,
                    comum_prejuizo=m.prejuizo_comum_saldo,
                    comum_imposto=_centavos(m.base_comum * R.ALIQUOTA_OPERACOES_COMUNS.valor),
                    dt_resultado=m.resultado_day_trade,
                    dt_prejuizo_anterior=ant_dt,
                    dt_base=m.base_day_trade,
                    dt_prejuizo=m.prejuizo_day_trade_saldo,
                    dt_imposto=_centavos(m.base_day_trade * R.ALIQUOTA_DAY_TRADE.valor),
                    irrf_day_trade=m.irrf_day_trade,
                    irrf_day_trade_estimado=m.irrf_day_trade_estimado,
                    irrf_lei_11033=m.irrf,
                    fii_resultado=m.resultado_fii,
                    fii_prejuizo_anterior=ant_fii,
                    fii_base=m.base_fii,
                    fii_prejuizo=m.prejuizo_fii_saldo,
                    fii_imposto=_centavos(m.base_fii * R.ALIQUOTA_FII.valor),
                    darf=m.darf,
                    pago=m.valor_pago,
                    outros_a_vista=m.rotulos("comum"),
                    confiavel=m.confiavel,
                ))
        if m.mes.year > ano:
            break
        anterior = m
    return saida


@dataclass
class Declaracao:
    ano: int
    bens: list[Bem]
    isentos: list[Rendimento]
    exclusivos: list[Rendimento]
    # Income the app does not place in a sheet: distributions of ETFs and
    # other funds. {ticker: valor}
    fora: dict[str, Decimal]
    meses_com_darf: list[apuracao.Mes]
    prejuizo_comum: Decimal
    prejuizo_fii: Decimal
    # BDR dividends by month (carnê-leão is monthly): [(month, {ticker: valor})]
    dividendos_bdr: list[tuple[date, dict[str, Decimal]]] = field(default_factory=list)
    # Bonus shares received in the year whose cost per share is not known yet.
    bonificacoes_sem_custo: list[str] = field(default_factory=list)
    renda_variavel: list[MesRendaVariavel] = field(default_factory=list)
    conversoes_de_acoes: list[ConversaoDeAcoes] = field(default_factory=list)

    @property
    def total_dividendos_bdr(self) -> Decimal:
        return sum((v for _, por in self.dividendos_bdr for v in por.values()), ZERO)

    @property
    def tem_fii_na_renda_variavel(self) -> bool:
        return any(m.fii_resultado or m.fii_prejuizo_anterior for m in self.renda_variavel)

    @property
    def incompletos(self) -> list[Bem]:
        return [b for b in self.bens if b.incompleto]

    @property
    def sem_cnpj(self) -> list[Bem]:
        """Bens whose code requires a CNPJ the app could not find."""
        return [b for b in self.bens if not b.cnpj and b.codigo is not None and b.codigo.valor != ("04", "04")]

    @property
    def tem_renda_fixa(self) -> bool:
        return any(b.renda_fixa for b in self.bens)


def _emissor(ativo: dict) -> tuple[str | None, str]:
    """(CNPJ, legal name): the catalog's, else CVM's."""
    cvm = cnpjs.emissor(ativo["ticker"])
    cnpj = cnpjs.formatar(ativo.get("cnpj_emissor") or "") or (cvm[0] if cvm else None)
    nome = ativo.get("emissor") or (cvm[1] if cvm else "")
    return cnpj, nome


def _custodia(conn: Connection, investidor_id: int, ate: date) -> dict[int, list[str]]:
    """{ativo_id: where it is held}, from the B3 statement, else from the notes."""
    por_ativo: dict[int, set[str]] = defaultdict(set)
    for r in fetch_all(
        conn,
        "SELECT DISTINCT ativo_id, instituicao FROM b3_movimentacoes "
        "WHERE investidor_id = :i AND data <= :ate AND ativo_id IS NOT NULL AND instituicao IS NOT NULL",
        i=investidor_id, ate=ate,
    ):
        por_ativo[r["ativo_id"]].add(r["instituicao"].strip())
    for r in fetch_all(
        conn,
        "SELECT DISTINCT ativo_id, corretora_id FROM negociacoes WHERE investidor_id = :i AND data <= :ate",
        i=investidor_id, ate=ate,
    ):
        if r["ativo_id"] not in por_ativo and r["corretora_id"] != "manual":
            por_ativo.setdefault(r["ativo_id"], set())
            por_ativo[r["ativo_id"]].add(painel.CORRETORAS.get(r["corretora_id"], r["corretora_id"]))
    return {k: sorted(v) for k, v in por_ativo.items()}


def _titulo(a: dict) -> str:
    """'CDB Banco X, 110% do CDI, vencimento 15/03/2027' from the catalog."""
    especie = (a["nome"] or "").split()[0] if (a["nome"] or "").strip() else "Título"
    partes = [f"{especie} {a['emissor']}" if a["emissor"] else especie]
    if indexador := painel.descricao_indexador(a):
        partes.append(indexador)
    if a["vencimento"]:
        partes.append(f"vencimento {formato.data(a['vencimento'])}")
    return ", ".join(partes)


def bens(conn: Connection, investidor_id: int, ano: int) -> list[Bem]:
    fim_anterior, fim = date(ano - 1, 12, 31), date(ano, 12, 31)
    # {ativo_id: (qty, cost, cost incomplete)} on each date.
    posicoes: list[dict[int, tuple[Decimal, Decimal, bool]]] = []
    for dia in (fim_anterior, fim):
        abertas = {i: (s.qty, s.custo, s.tem_bonif_sem_custo or s.custo_desconhecido)
                   for i, s in saldos(conn, investidor_id, dia, TIPOS).items() if s.qty > 0}
        abertas.update({i: (q, c, False) for i, (q, c) in saldos_renda_fixa(conn, investidor_id, dia).items() if q > 0})
        posicoes.append(abertas)
    antes, agora = posicoes
    ids = sorted(set(antes) | set(agora))
    if not ids:
        return []
    catalogo = {r["id"]: dict(r) for r in fetch_all(
        conn,
        "SELECT id, tipo, subtipo, ticker, nome, cnpj_emissor, emissor, indexador, taxa_prefixada, "
        "percentual_do_indexador, vencimento FROM ativos WHERE id = ANY(:ids)",
        ids=ids,
    )}
    custodia = _custodia(conn, investidor_id, fim)
    lista = []
    for i in ids:
        a = catalogo[i]
        q0, c0, _ = antes.get(i, (ZERO, ZERO, False))
        q1, c1, incompleto = agora.get(i, (ZERO, ZERO, False))
        cnpj, nome = _emissor(a)
        lista.append(Bem(
            ativo_id=i,
            ticker=a["ticker"] or a["nome"] or "",
            nome="" if a["tipo"] == "renda_fixa" else nome,
            tipo=a["tipo"],
            subtipo=a["subtipo"],
            codigo=codigo_do_bem(a["tipo"], a["subtipo"], a["nome"]),
            cnpj=cnpj,
            qtd_anterior=q0,
            custo_anterior=_centavos(c0),
            qtd=q1,
            custo=_centavos(c1),
            custodia=custodia.get(i, []),
            incompleto=incompleto,
            titulo=_titulo(a) if a["tipo"] == "renda_fixa" else None,
        ))
    ordem = {"03": 0, "04": 1, "07": 2}
    lista.sort(key=lambda b: (ordem.get(b.codigo.valor[0], 9) if b.codigo else 9,
                              b.codigo.valor if b.codigo else ("", ""), b.rotulo))
    return lista


def _centavos(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def _bonificacoes(conn: Connection, investidor_id: int, ano: int) -> list[dict]:
    """Bonus shares credited in the year, with the cost per share once known."""
    return fetch_all(
        conn,
        """
        SELECT b.ativo_id, b.quantidade, bc.custo_por_cota
        FROM b3_movimentacoes b
        LEFT JOIN bonificacoes bc ON bc.b3_movimentacao_id = b.id
        WHERE b.investidor_id = :i AND b.ativo_id IS NOT NULL
          AND b.movimentacao = 'Bonificação em Ativos'
          AND b.data BETWEEN :inicio AND :fim
        """,
        i=investidor_id, inicio=date(ano, 1, 1), fim=date(ano, 12, 31),
    )


def dividendos_bdr(conn: Connection, investidor_id: int, ano: int) -> list[tuple[date, dict[str, Decimal]]]:
    """BDR dividends by month of payment: [(first day of month, {ticker: valor})]."""
    meses: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for r in painel.linhas_proventos(conn, investidor_id, date(ano, 1, 1), date(ano, 12, 31), tipos=("bdr",)):
        meses[date(r["data"].year, r["data"].month, 1)][r["ticker"]] += painel.valor_provento(r)
    return [(m, dict(v)) for m, v in sorted(meses.items()) if any(v.values())]


def rendimentos(
    conn: Connection, investidor_id: int, ano: int
) -> tuple[list[Rendimento], list[Rendimento], dict[str, Decimal], list[str]]:
    """(isentos, exclusivos, fora, bonificações sem custo) for the year, one
    line per paying CNPJ."""
    por_ativo = painel.proventos_por_ativo(conn, investidor_id, date(ano, 1, 1), date(ano, 12, 31))
    bonificacoes = _bonificacoes(conn, investidor_id, ano)
    if not por_ativo and not bonificacoes:
        return [], [], {}, []
    catalogo = {r["id"]: dict(r) for r in fetch_all(
        conn, "SELECT id, tipo, subtipo, ticker, nome, cnpj_emissor, emissor FROM ativos WHERE id = ANY(:ids)",
        ids=list(por_ativo) + [b["ativo_id"] for b in bonificacoes],
    )}
    linhas: dict[tuple, Rendimento] = {}
    fora: dict[str, Decimal] = defaultdict(lambda: ZERO)

    def somar(regra: R.Regra, descricao: str, a: dict, valor: Decimal) -> None:
        cnpj, nome = _emissor(a)
        ticker = a["ticker"] or a["nome"] or ""
        chave = (regra.valor, cnpj or ticker)
        r = linhas.setdefault(chave, Rendimento(regra, descricao, nome or ticker, cnpj, ZERO))
        r.valor += valor
        if ticker not in r.tickers:
            r.tickers.append(ticker)

    for ativo_id, valores in por_ativo.items():
        a = catalogo.get(ativo_id)
        if a is None:
            continue
        ticker = a["ticker"] or a["nome"] or ""
        for categoria, valor in valores.items():
            if categoria == "total" or not valor:
                continue
            if categoria == "dividendos" and a["tipo"] == "acao":
                somar(R.ISENTO_DIVIDENDOS, "Lucros e dividendos recebidos", a, valor)
            elif categoria == "jcp" and a["tipo"] == "acao":
                somar(R.EXCLUSIVO_JCP, "Juros sobre capital próprio", a, valor)
            elif categoria == "rendimentos" and a["tipo"] == "fii":
                somar(R.ISENTO_RENDIMENTOS_FII, "Rendimentos de FII" if a["subtipo"] != "fiagro"
                      else "Rendimentos de Fiagro", a, valor)
            elif categoria == "dividendos" and a["tipo"] == "bdr":
                continue  # taxable: shown month by month (dividendos_bdr)
            elif categoria != "juros":  # fixed-income interest: not covered yet
                fora[ticker] += valor

    sem_custo = []
    for b in bonificacoes:
        a = catalogo[b["ativo_id"]]
        if a["tipo"] != "acao":
            continue  # line 18 is for shares; FII quotas are not bonus-issued this way
        if b["custo_por_cota"] is None:
            if (a["ticker"] or "") not in sem_custo:
                sem_custo.append(a["ticker"] or a["nome"] or "")
            continue
        somar(R.ISENTO_BONIFICACOES, "Bonificações em ações", a, _centavos(b["quantidade"] * b["custo_por_cota"]))

    isentos = [r for r in linhas.values() if r.linha is not R.EXCLUSIVO_JCP]
    exclusivos = [r for r in linhas.values() if r.linha is R.EXCLUSIVO_JCP]
    isentos.sort(key=lambda r: (r.linha.valor, -r.valor))
    exclusivos.sort(key=lambda r: -r.valor)
    return isentos, exclusivos, dict(fora), sem_custo


def declaracao(conn: Connection, investidor_id: int, ano: int) -> Declaracao:
    isentos, exclusivos, fora, bonificacoes_sem_custo = rendimentos(conn, investidor_id, ano)
    meses = [m for m in apuracao.apuracao(conn, investidor_id) if m.mes.year <= ano]
    do_ano = [m for m in meses if m.mes.year == ano]
    ganho_isento = sum((m.ganho_isento for m in do_ano), ZERO)
    if ganho_isento:
        isentos.append(Rendimento(
            R.ISENTO_ACOES_ATE_20_MIL, "Ganhos com ações em meses de vendas até R$ 20 mil",
            "Operações em bolsa do próprio titular", None, ganho_isento,
            sorted({v.rotulo for m in do_ano if m.isento for v in m.vendas if v.categoria == "acao"}),
        ))
    ultimo = meses[-1] if meses else None
    return Declaracao(
        ano=ano,
        bens=bens(conn, investidor_id, ano),
        isentos=isentos,
        exclusivos=exclusivos,
        fora=fora,
        meses_com_darf=[m for m in do_ano if m.darf > 0],
        prejuizo_comum=ultimo.prejuizo_comum_saldo if ultimo else ZERO,
        prejuizo_fii=ultimo.prejuizo_fii_saldo if ultimo else ZERO,
        dividendos_bdr=dividendos_bdr(conn, investidor_id, ano),
        bonificacoes_sem_custo=bonificacoes_sem_custo,
        renda_variavel=renda_variavel(meses, ano),
        conversoes_de_acoes=conversoes_de_acoes(conn, investidor_id, ano),
    )


def anos(conn: Connection, investidor_id: int, hoje: date | None = None) -> list[int]:
    """Calendar years with any trade or B3 event, newest first, up to last year."""
    hoje = hoje or date.today()
    rows = fetch_all(
        conn,
        "SELECT DISTINCT EXTRACT(YEAR FROM data)::int AS ano FROM negociacoes WHERE investidor_id = :i "
        "UNION SELECT DISTINCT EXTRACT(YEAR FROM data)::int FROM b3_movimentacoes WHERE investidor_id = :i",
        i=investidor_id,
    )
    primeiro = min((r["ano"] for r in rows), default=hoje.year - 1)
    return list(range(hoje.year - 1, primeiro - 1, -1)) or [hoje.year - 1]
