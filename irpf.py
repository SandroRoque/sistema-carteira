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
TIPOS = ("acao", "bdr", "fii", "etf", "fundo", "tesouro_direto")

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
    if tipo == "fii":
        return R.BEM_FIAGRO if subtipo == "fiagro" else R.BEM_FII
    if tipo == "etf":
        return R.BEM_ETF_RENDA_FIXA if subtipo == "renda_fixa" else R.BEM_ETF
    if tipo == "fundo" and subtipo == "infra":
        return R.BEM_FUNDO_INFRA
    return None


_UNIDADE = {"acao": ("ação", "ações"), "bdr": ("BDR", "BDRs"), "tesouro_direto": ("título", "títulos")}


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
class Declaracao:
    ano: int
    bens: list[Bem]
    isentos: list[Rendimento]
    exclusivos: list[Rendimento]
    # Income the app does not place in a sheet: BDR dividends (taxable),
    # distributions of ETFs and other funds. {ticker: valor}
    fora: dict[str, Decimal]
    meses_com_darf: list[apuracao.Mes]
    prejuizo_comum: Decimal
    prejuizo_fii: Decimal

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


def rendimentos(
    conn: Connection, investidor_id: int, ano: int
) -> tuple[list[Rendimento], list[Rendimento], dict[str, Decimal]]:
    """(isentos, exclusivos, fora) for the year, one line per paying CNPJ."""
    por_ativo = painel.proventos_por_ativo(conn, investidor_id, date(ano, 1, 1), date(ano, 12, 31))
    if not por_ativo:
        return [], [], {}
    catalogo = {r["id"]: dict(r) for r in fetch_all(
        conn, "SELECT id, tipo, subtipo, ticker, nome, cnpj_emissor, emissor FROM ativos WHERE id = ANY(:ids)",
        ids=list(por_ativo),
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
            elif categoria != "juros":  # fixed-income interest: not covered yet
                fora[ticker] += valor

    isentos = [r for r in linhas.values() if r.linha is not R.EXCLUSIVO_JCP]
    exclusivos = [r for r in linhas.values() if r.linha is R.EXCLUSIVO_JCP]
    isentos.sort(key=lambda r: (r.linha.valor, -r.valor))
    exclusivos.sort(key=lambda r: -r.valor)
    return isentos, exclusivos, dict(fora)


def declaracao(conn: Connection, investidor_id: int, ano: int) -> Declaracao:
    isentos, exclusivos, fora = rendimentos(conn, investidor_id, ano)
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
