"""What the web pages show: the portfolio valued at market, income, an
ativo's history. Pure reads, scoped to one investidor; the routes in app/
turn these structures into pages.

Valuation rules
---------------
acao, fii, bdr         quantity × cached price (cotacoes.ler_cache). Without a
                       price, the position is shown at cost and flagged.
tesouro_direto,        at the amount invested (valor aplicado): no market
renda_fixa             pricing or accrued interest.
subscrição             at the amount paid for exercising.

The unrealized result only covers positions with both a known cost and a
price; anything else is left out of it rather than guessed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Connection

import cotacoes
import formato
from custo_medio import Saldo, saldos
from database import fetch_all, fetch_one, scalar
from posicoes import calcular_posicoes

ZERO = Decimal(0)

# Display order and labels of the asset classes.
CLASSES = {
    "acao": "Ações",
    "fii": "FIIs",
    "bdr": "BDRs",
    "tesouro_direto": "Tesouro Direto",
    "renda_fixa": "Renda fixa",
    "subscricao": "Direitos e recibos de subscrição",
}
# Allocation groups the fixed-income classes; series = chart color slot.
ALOCACAO = (
    ("Ações", ("acao",), "s-1"),
    ("FIIs", ("fii",), "s-2"),
    ("Renda fixa", ("renda_fixa", "tesouro_direto"), "s-3"),
    ("BDRs", ("bdr",), "s-4"),
    ("Outros", ("subscricao",), "s-outros"),
)
_COM_COTACAO = {"acao", "fii", "bdr"}
_SUBSCRICAO = {"direito_subscricao", "recibo_subscricao"}

CORRETORAS = {
    "nu_invest": "Nu Invest",
    "xp": "XP",
    "safra": "Safra",
    "brasil_plural": "Brasil Plural",
    "manual": "lançamento manual",
}

# B3 movimentações that are income, by category.
_PROVENTOS = {
    "Dividendo": "dividendos",
    "Dividendo - Cancelado": "dividendos",
    "Juros Sobre Capital Próprio": "jcp",
    "Rendimento": "rendimentos",
    "PAGAMENTO DE JUROS": "juros",
}
TIPOS_PROVENTO = {
    "dividendos": ("Dividendos", "s-1"),
    "jcp": ("JCP", "s-2"),
    "rendimentos": ("Rendimentos", "s-3"),
    "juros": ("Juros de renda fixa", "s-4"),
}


def nome_exibicao(nome: str | None, ticker: str | None) -> str:
    """The catalog name, unless it is just the raw text from a nota
    ('PETR4F PN EDJ N2'): that adds nothing next to the ticker."""
    nome = (nome or "").strip()
    if ticker and nome.upper().startswith(ticker[:4].upper()):
        return ""
    return nome


def classe(tipo: str) -> str:
    return "subscricao" if tipo in _SUBSCRICAO else tipo


def razao(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return a / b if a is not None and b else None


# ---------------------------------------------------------------------------
# Positions at market
# ---------------------------------------------------------------------------


@dataclass
class Linha:
    ativo_id: int
    ticker: str
    nome: str
    tipo: str
    qtd: Decimal
    preco_medio: Decimal | None
    custo: Decimal | None  # None: unknown (custody transfer without a note)
    cotacao: Decimal | None
    valor: Decimal
    a_custo: bool  # valor is the cost, not a market price
    resultado: Decimal | None
    proventos_12m: Decimal
    bonif_sem_custo: bool = False
    vencimento: date | None = None
    indexador: str | None = None
    peso: Decimal = ZERO

    @property
    def resultado_pct(self) -> Decimal | None:
        return razao(self.resultado, self.custo)

    @property
    def rotulo(self) -> str:
        return self.ticker or self.nome


@dataclass
class Grupo:
    classe: str
    rotulo: str
    linhas: list[Linha]
    valor: Decimal = ZERO
    resultado: Decimal | None = None
    custo_com_resultado: Decimal = ZERO
    proventos_12m: Decimal = ZERO
    peso: Decimal = ZERO

    @property
    def resultado_pct(self) -> Decimal | None:
        return razao(self.resultado, self.custo_com_resultado)

    @property
    def a_mercado(self) -> bool:
        return self.classe in _COM_COTACAO


@dataclass
class Fatia:
    rotulo: str
    serie: str
    valor: Decimal
    peso: Decimal


@dataclass
class Carteira:
    grupos: list[Grupo]
    patrimonio: Decimal
    custo_conhecido: Decimal
    resultado: Decimal | None
    custo_com_resultado: Decimal
    proventos_12m: Decimal
    cotacoes_em: datetime | None
    atualizando_cotacoes: bool
    sem_custo: list[Linha] = field(default_factory=list)

    @property
    def resultado_pct(self) -> Decimal | None:
        return razao(self.resultado, self.custo_com_resultado)

    @property
    def linhas(self) -> list[Linha]:
        return [l for g in self.grupos for l in g.linhas]

    @property
    def vazia(self) -> bool:
        return not self.grupos

    @property
    def custo_renda_variavel(self) -> Decimal:
        return sum(
            (l.custo for l in self.linhas if l.tipo in _COM_COTACAO and l.custo is not None), ZERO
        )

    def alocacao(self) -> list[Fatia]:
        fatias = []
        for rotulo, classes, serie in ALOCACAO:
            valor = sum((g.valor for g in self.grupos if g.classe in classes), ZERO)
            if valor > 0:
                fatias.append(Fatia(rotulo, serie, valor, valor / self.patrimonio))
        return fatias

    def maiores(self, n: int = 5) -> list[Linha]:
        return sorted(self.linhas, key=lambda l: l.valor, reverse=True)[:n]


def descricao_indexador(ativo: dict) -> str | None:
    """'113% do CDI', 'IPCA + 6,35%', 'Prefixado 12,50%'."""
    indexador = (ativo.get("indexador") or "").upper().replace("IPC-A", "IPCA")
    taxa = ativo.get("taxa_prefixada") or ZERO
    percentual = ativo.get("percentual_do_indexador")
    if not indexador:
        return None
    if indexador in ("PRE", "PRÉ", "PREFIXADO"):
        return f"Prefixado {formato.numero(taxa)}%"
    if taxa:
        return f"{indexador} + {formato.numero(taxa)}%"
    if percentual and percentual != 100:
        return f"{formato.numero(percentual, 1).removesuffix(',0')}% do {indexador}"
    return indexador


def janela_12m(hoje: date | None = None) -> tuple[date, date]:
    """The current month and the 11 before it."""
    hoje = hoje or date.today()
    ano, mes = hoje.year, hoje.month - 11
    if mes <= 0:
        ano, mes = ano - 1, mes + 12
    return date(ano, mes, 1), hoje


def carteira(conn: Connection, investidor_id: int, hoje: date | None = None) -> Carteira:
    posicoes = [p for p in calcular_posicoes(conn, investidor_id) if p["is_open"]]
    catalogo = _catalogo(conn, [p["ativo_id"] for p in posicoes])
    inicio, fim = janela_12m(hoje)
    prov_12m = {a: v["total"] for a, v in proventos_por_ativo(conn, investidor_id, inicio, fim).items()}

    tickers = [p["ticker"] for p in posicoes if p["tipo"] in _COM_COTACAO and p["ticker"]]
    cache = cotacoes.ler_cache(conn, tickers)
    atualizando = cotacoes.atualizar_em_segundo_plano(cache.vencidos)

    por_classe: dict[str, list[Linha]] = defaultdict(list)
    for p in posicoes:
        tipo = p["tipo"]
        qtd = p["qty"]
        custo = None if p["custo_sem_origem"] else p["custo_total"]
        cotacao = cache.precos.get(p["ticker"]) if tipo in _COM_COTACAO else None
        if cotacao is not None:
            valor, a_custo = qtd * cotacao, False
        else:
            valor, a_custo = (custo or ZERO), True
        resultado = valor - custo if not a_custo and custo is not None else None
        ativo = catalogo.get(p["ativo_id"], {})
        por_classe[classe(tipo)].append(Linha(
            ativo_id=p["ativo_id"],
            ticker=p["ticker"],
            nome=nome_exibicao(p["nome"], p["ticker"]) if p["ticker"] else p["nome"],
            tipo=tipo,
            qtd=qtd,
            preco_medio=p["preco_medio"] if custo is not None else None,
            custo=custo,
            cotacao=cotacao,
            valor=valor,
            a_custo=a_custo,
            resultado=resultado,
            proventos_12m=prov_12m.get(p["ativo_id"], ZERO),
            bonif_sem_custo=p["tem_bonif_sem_custo"],
            vencimento=p["vencimento"],
            indexador=descricao_indexador(ativo),
        ))

    grupos = []
    for chave, rotulo in CLASSES.items():
        linhas = sorted(por_classe.get(chave, []), key=lambda l: l.rotulo)
        if not linhas:
            continue
        g = Grupo(chave, rotulo, linhas)
        g.valor = sum((l.valor for l in linhas), ZERO)
        com_resultado = [l for l in linhas if l.resultado is not None]
        if com_resultado:
            g.resultado = sum((l.resultado for l in com_resultado), ZERO)
            g.custo_com_resultado = sum((l.custo for l in com_resultado), ZERO)
        g.proventos_12m = sum((l.proventos_12m for l in linhas), ZERO)
        grupos.append(g)

    patrimonio = sum((g.valor for g in grupos), ZERO)
    for g in grupos:
        g.peso = g.valor / patrimonio if patrimonio else ZERO
        for l in g.linhas:
            l.peso = l.valor / patrimonio if patrimonio else ZERO

    com_resultado = [g for g in grupos if g.resultado is not None]
    linhas = [l for g in grupos for l in g.linhas]
    return Carteira(
        grupos=grupos,
        patrimonio=patrimonio,
        custo_conhecido=sum((l.custo for l in linhas if l.custo is not None), ZERO),
        resultado=sum((g.resultado for g in com_resultado), ZERO) if com_resultado else None,
        custo_com_resultado=sum((g.custo_com_resultado for g in com_resultado), ZERO),
        proventos_12m=sum(prov_12m.values(), ZERO),
        cotacoes_em=cache.atualizado_em,
        atualizando_cotacoes=atualizando,
        sem_custo=[l for l in linhas if l.custo is None],
    )


def _catalogo(conn: Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = fetch_all(
        conn,
        "SELECT id, ticker, nome, tipo, indexador, taxa_prefixada, percentual_do_indexador, vencimento "
        "FROM ativos WHERE id = ANY(:ids)",
        ids=ids,
    )
    return {r["id"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# Closed positions
# ---------------------------------------------------------------------------


@dataclass
class Encerrada:
    ativo_id: int
    rotulo: str
    nome: str
    encerrada_em: date
    resultado: Decimal | None  # realized on sales; None when it left otherwise
    proventos: Decimal


def encerradas(conn: Connection, investidor_id: int) -> list[Encerrada]:
    fechadas = {
        ativo_id: s for ativo_id, s in saldos(conn, investidor_id).items()
        if s.tem_negociacoes and s.qty <= Decimal("0.001") and s.passos
    }
    if not fechadas:
        return []
    catalogo = _catalogo(conn, list(fechadas))
    proventos = proventos_por_ativo(conn, investidor_id, date(1900, 1, 1), date(2999, 12, 31))
    resultado = []
    for ativo_id, s in fechadas.items():
        vendas = [b for b in s.baixas if b.evento.tipo == "venda" and b.custo is not None]
        a = catalogo.get(ativo_id, {})
        resultado.append(Encerrada(
            ativo_id=ativo_id,
            rotulo=a.get("ticker") or a.get("nome") or "",
            nome=a.get("nome") or "",
            encerrada_em=s.passos[-1].evento.data,
            resultado=sum(((b.evento.custo or ZERO) - b.custo for b in vendas), ZERO) if vendas else None,
            proventos=proventos.get(ativo_id, {}).get("total", ZERO),
        ))
    return sorted(resultado, key=lambda e: e.encerrada_em, reverse=True)


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------


def _linhas_proventos(conn: Connection, investidor_id: int, inicio: date, fim: date) -> list[dict]:
    return fetch_all(
        conn,
        """
        SELECT ativo_id, data, movimentacao, COALESCE(valor, 0) AS valor
        FROM b3_movimentacoes
        WHERE investidor_id = :investidor_id
          AND ativo_id IS NOT NULL
          AND movimentacao = ANY(:movs)
          AND data BETWEEN :inicio AND :fim
        ORDER BY data
        """,
        investidor_id=investidor_id,
        movs=list(_PROVENTOS),
        inicio=inicio,
        fim=fim,
    )


def _valor_provento(row: dict) -> Decimal:
    # A cancelled dividend comes as a positive debit: it subtracts.
    return -row["valor"] if row["movimentacao"] == "Dividendo - Cancelado" else row["valor"]


def proventos_por_ativo(
    conn: Connection, investidor_id: int, inicio: date, fim: date
) -> dict[int, dict[str, Decimal]]:
    """{ativo_id: {categoria: valor, 'total': valor}} received in [inicio, fim]."""
    por_ativo: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for r in _linhas_proventos(conn, investidor_id, inicio, fim):
        v = _valor_provento(r)
        por_ativo[r["ativo_id"]][_PROVENTOS[r["movimentacao"]]] += v
        por_ativo[r["ativo_id"]]["total"] += v
    return por_ativo


@dataclass
class Mes:
    inicio: date
    valores: dict[str, Decimal]

    @property
    def total(self) -> Decimal:
        return sum(self.valores.values(), ZERO)


@dataclass
class LinhaProvento:
    ativo_id: int
    rotulo: str
    valores: dict[str, Decimal]
    total: Decimal
    retorno: Decimal | None  # total / cost of the open position


@dataclass
class Proventos:
    inicio: date
    fim: date
    meses: list[Mes]
    por_tipo: dict[str, Decimal]
    por_ativo: list[LinhaProvento]
    total: Decimal
    atualizado_ate: date | None

    @property
    def tipos(self) -> list[str]:
        return [t for t in TIPOS_PROVENTO if self.por_tipo.get(t)]

    @property
    def total_renda_variavel(self) -> Decimal:
        """Income from stocks, FIIs and BDRs: what return on their cost measures
        (fixed-income interest is already part of that investment's yield)."""
        return self.total - self.por_tipo.get("juros", ZERO)

    @property
    def media_mensal(self) -> Decimal:
        return self.total / len(self.meses) if self.meses else ZERO

    @property
    def maior_mes(self) -> Mes | None:
        return max(self.meses, key=lambda m: m.total) if self.total else None


def _meses_entre(inicio: date, fim: date) -> list[date]:
    meses, atual = [], date(inicio.year, inicio.month, 1)
    while atual <= fim:
        meses.append(atual)
        atual = date(atual.year + (atual.month == 12), atual.month % 12 + 1, 1)
    return meses


def proventos(
    conn: Connection, investidor_id: int, inicio: date, fim: date, custos: dict[int, Decimal] | None = None
) -> Proventos:
    """Income in [inicio, fim], by month, type and ativo. `custos` (ativo_id →
    cost of the open position) gives each ativo's return on cost."""
    meses = {m: defaultdict(lambda: ZERO) for m in _meses_entre(inicio, fim)}
    por_tipo: dict[str, Decimal] = defaultdict(lambda: ZERO)
    por_ativo: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for r in _linhas_proventos(conn, investidor_id, inicio, fim):
        v, tipo = _valor_provento(r), _PROVENTOS[r["movimentacao"]]
        meses[date(r["data"].year, r["data"].month, 1)][tipo] += v
        por_tipo[tipo] += v
        por_ativo[r["ativo_id"]][tipo] += v

    catalogo = _catalogo(conn, list(por_ativo))
    custos = custos or {}
    linhas = []
    for ativo_id, valores in por_ativo.items():
        total = sum(valores.values(), ZERO)
        a = catalogo.get(ativo_id, {})
        linhas.append(LinhaProvento(
            ativo_id, a.get("ticker") or a.get("nome") or "", dict(valores), total,
            razao(total, custos.get(ativo_id)),
        ))
    linhas.sort(key=lambda l: l.total, reverse=True)

    return Proventos(
        inicio=inicio,
        fim=fim,
        meses=[Mes(m, dict(v)) for m, v in meses.items()],
        por_tipo=dict(por_tipo),
        por_ativo=linhas,
        total=sum(por_tipo.values(), ZERO),
        atualizado_ate=scalar(
            conn, "SELECT MAX(data) FROM b3_movimentacoes WHERE investidor_id = :i", i=investidor_id
        ),
    )


def anos_com_proventos(conn: Connection, investidor_id: int) -> list[int]:
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT EXTRACT(YEAR FROM data)::int AS ano FROM b3_movimentacoes
        WHERE investidor_id = :i AND movimentacao = ANY(:movs) ORDER BY ano DESC
        """,
        i=investidor_id,
        movs=list(_PROVENTOS),
    )
    return [r["ano"] for r in rows]


# ---------------------------------------------------------------------------
# One ativo
# ---------------------------------------------------------------------------

_EVENTO_ROTULO = {
    "compra": "Compra",
    "venda": "Venda",
    "bonificacao": "Bonificação",
    "desdobro": "Desdobramento",
    "atualizacao": "Crédito B3",
    "fracao": "Leilão de fração",
    "resgate": "Resgate",
    "transferencia_entrada": "Transferência recebida",
    "transferencia_saida": "Transferência enviada",
    "conversao_entrada": "Recebido de outro ativo",
    "conversao_saida": "Convertido em outro ativo",
}
_PROVENTO_ROTULO = {
    "Dividendo": "Dividendo",
    "Dividendo - Cancelado": "Dividendo cancelado",
    "Juros Sobre Capital Próprio": "JCP",
    "Rendimento": "Rendimento",
    "PAGAMENTO DE JUROS": "Juros",
}


@dataclass
class ItemHistorico:
    data: date
    rotulo: str
    categoria: str  # 'compra' | 'venda' | 'evento' | 'provento'
    qtd: Decimal | None
    valor: Decimal | None
    qtd_apos: Decimal | None
    preco_medio_apos: Decimal | None
    detalhe: str | None = None
    # conversao_entrada: the B3 credit, so the conversion can be undone.
    conversao_id: int | None = None


@dataclass
class Ativo:
    ativo_id: int
    ticker: str
    nome: str
    tipo: str
    linha: Linha | None  # None: no open position
    custodia: list[str]
    historico: list[ItemHistorico]
    proventos_por_ano: list[tuple[int, Decimal]]
    proventos_total: Decimal

    @property
    def rotulo(self) -> str:
        return self.ticker or self.nome


def ativo(conn: Connection, investidor_id: int, ativo_id: int, hoje: date | None = None) -> Ativo | None:
    a = fetch_one(conn, "SELECT id, ticker, nome, tipo FROM ativos WHERE id = :id", id=ativo_id)
    if a is None:
        return None
    saldo: Saldo | None = saldos(conn, investidor_id, tipos=None).get(ativo_id)
    linhas_prov = [
        r for r in _linhas_proventos(conn, investidor_id, date(1900, 1, 1), date(2999, 12, 31))
        if r["ativo_id"] == ativo_id
    ]
    if saldo is None and not linhas_prov:
        return None  # nothing of this investidor's

    linha = next((l for l in carteira(conn, investidor_id, hoje).linhas if l.ativo_id == ativo_id), None)

    historico = []
    for p in (saldo.passos if saldo else []):
        if p.ignorado:
            continue
        ev = p.evento
        categoria = ev.tipo if ev.tipo in ("compra", "venda") else "evento"
        detalhe = None
        if ev.nota:
            corretora, nota_id = ev.nota
            detalhe = f"Nota {nota_id} · {CORRETORAS.get(corretora, corretora)}"
        elif ev.sem_nota:
            detalhe = "Extrato B3, sem nota: valor sem taxas"
        elif ev.contraparte:
            outro = fetch_one(conn, "SELECT ticker, nome FROM ativos WHERE id = :id", id=ev.contraparte)
            nome_outro = (outro["ticker"] or outro["nome"]) if outro else "?"
            detalhe = (f"De {nome_outro}, com o custo dele (incorporação ou conversão)"
                       if ev.tipo == "conversao_entrada" else f"Para {nome_outro}, levando o custo")
        elif ev.tipo != "compra":
            detalhe = "Extrato B3"
        if ev.tipo == "venda":
            baixa = next((b for b in saldo.baixas if b.evento is ev), None)
            if baixa and baixa.custo is not None and ev.custo is not None:
                lucro = ev.custo - baixa.custo
                detalhe = f"{'Lucro' if lucro >= 0 else 'Prejuízo'} de {formato.brl(abs(lucro))}" + (
                    f" · {detalhe}" if detalhe else ""
                )
        historico.append(ItemHistorico(
            data=ev.data,
            rotulo=_EVENTO_ROTULO[ev.tipo],
            categoria=categoria,
            qtd=ev.quantidade,
            valor=ev.custo if ev.tipo in ("compra", "venda") else None,
            qtd_apos=p.qty,
            preco_medio_apos=p.preco_medio,
            detalhe=detalhe,
            conversao_id=ev.b3_id if ev.tipo == "conversao_entrada" else None,
        ))
    for r in linhas_prov:
        historico.append(ItemHistorico(
            data=r["data"],
            rotulo=_PROVENTO_ROTULO[r["movimentacao"]],
            categoria="provento",
            qtd=None,
            valor=_valor_provento(r),
            qtd_apos=None,
            preco_medio_apos=None,
            detalhe="Extrato B3" + (" · valor líquido" if r["movimentacao"] == "Juros Sobre Capital Próprio" else ""),
        ))
    # Newest first; within a day, the order the replay applied them.
    historico.sort(key=lambda h: h.data, reverse=True)

    por_ano: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for r in linhas_prov:
        por_ano[r["data"].year] += _valor_provento(r)

    custodia = [
        CORRETORAS.get(r["corretora_id"], r["corretora_id"])
        for r in fetch_all(
            conn,
            "SELECT DISTINCT corretora_id FROM negociacoes WHERE investidor_id = :i AND ativo_id = :a",
            i=investidor_id,
            a=ativo_id,
        )
    ]
    return Ativo(
        ativo_id=ativo_id,
        ticker=a["ticker"] or "",
        nome=nome_exibicao(a["nome"], a["ticker"]) if a["ticker"] else (a["nome"] or ""),
        tipo=a["tipo"],
        linha=linha,
        custodia=sorted(custodia),
        historico=historico,
        proventos_por_ano=sorted(por_ano.items()),
        proventos_total=sum(por_ano.values(), ZERO),
    )
