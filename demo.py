"""Public demo account: an invented portfolio, rebuilt from scratch.

    uv run python admin.py recriar-demo

The data goes through the real loaders (loader.carregar for the notas,
carrega_b3.carregar_linhas for the B3 statement), so the demo exercises the
same paths as an upload. Everything is invented (see AGENTS.md): the tickers
are real, but every trade, price and event is generated from a fixed seed and
dated relative to today, so rebuilding it on each deploy keeps it current.

What it shows, besides positions and income:
- a sale month under R$ 20 mil (exempt), a taxable one with its DARF paid,
  a loss carried forward and, last month, a FII sale with the DARF still open;
- a bonus-share event without a cost, listed in Pendências.

The account is read-only (app.seguranca) and has no password: visitors enter
through the "Ver demonstração" button.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import apuracao
import auth
import loader
from apuracao import feriados_bancarios, vencimento
from carrega_b3 import LinhaB3, carregar_linhas
from database import connect_sistema, execute, fetch_all, scalar
from transformer import DocumentoTransformado, NegociacaoRecord, NotaRecord

EMAIL = "demo@carteira.invalid"
# Fake CPF with valid check digits; stored only as HMAC + mask like any other.
CPF = "52998224725"
APELIDO = "Carteira de demonstração"
INSTITUICAO = "NU INVEST CORRETORA DE VALORES S.A."
ARQUIVO_B3 = "movimentacao-demonstracao.xlsx"
MESES = 24
SEMENTE = 20261006


@dataclass(frozen=True)
class Papel:
    especificacao: str  # as printed on a nota
    produto: str  # as printed on the B3 statement
    preco: float  # invented starting price


PAPEIS = {
    "ABEV3": Papel("ABEV3 ON", "ABEV3 - AMBEV S.A.", 13.5),
    "RADL3": Papel("RADL3 ON NM", "RADL3 - RAIA DROGASIL S.A.", 26.0),
    "SUZB3": Papel("SUZB3 ON NM", "SUZB3 - SUZANO S.A.", 56.0),
    "BBDC4": Papel("BBDC4 PN N1", "BBDC4 - BANCO BRADESCO S.A.", 16.0),
    "CMIG4": Papel("CMIG4 PN N1", "CMIG4 - CIA ENERGETICA DE MINAS GERAIS - CEMIG", 11.5),
    "XPML11": Papel("XPML11 CI", "XPML11 - FII XPML", 104.0),
    "BTLG11": Papel("BTLG11 CI", "BTLG11 - FII BTLG", 101.0),
    "HGRU11": Papel("HGRU11 CI", "HGRU11 - FII HGRU", 126.0),
}
ACOES = ["ABEV3", "RADL3", "SUZB3", "BBDC4", "CMIG4"]
FIIS = ["XPML11", "BTLG11", "HGRU11"]
COMPRA_INICIAL = {
    "ABEV3": 800, "RADL3": 350, "SUZB3": 250, "BBDC4": 600, "CMIG4": 700,
    "XPML11": 80, "BTLG11": 80, "HGRU11": 60,
}

# Income per share (invented): (movimentação, months paid or None for every month, R$ per share).
PROVENTOS = {
    "ABEV3": [("Dividendo", (3, 12), 0.40), ("Juros Sobre Capital Próprio", (12,), 0.15)],
    "RADL3": [("Juros Sobre Capital Próprio", (3, 6, 9, 12), 0.12)],
    "SUZB3": [("Dividendo", (12,), 1.80)],
    "BBDC4": [("Juros Sobre Capital Próprio", None, 0.02), ("Dividendo", (3, 9), 0.30)],
    "CMIG4": [("Dividendo", (6, 12), 0.55)],
    "XPML11": [("Rendimento", None, 0.92)],
    "BTLG11": [("Rendimento", None, 0.78)],
    "HGRU11": [("Rendimento", None, 0.95)],
}

# Sales by month index (0 = first month): ticker, share of the position, price / average cost.
VENDA_ISENTA = 9  # under R$ 20 mil, with gain
VENDA_TRIBUTADA = 14  # over R$ 20 mil, with gain; DARF marked paid
VENDA_COM_PREJUIZO = 18
MES_BONIFICACAO = 12


@dataclass
class Posicao:
    quantidade: int = 0
    custo: float = 0.0

    @property
    def medio(self) -> float:
        return self.custo / self.quantidade if self.quantidade else 0.0


@dataclass
class Gerador:
    hoje: date
    rng: random.Random = field(default_factory=lambda: random.Random(SEMENTE))
    posicoes: dict[str, Posicao] = field(default_factory=dict)
    notas: list[DocumentoTransformado] = field(default_factory=list)
    b3: list[LinhaB3] = field(default_factory=list)
    _proxima_nota: int = 90001

    @property
    def inicio(self) -> date:
        ano, mes = divmod(self.hoje.year * 12 + self.hoje.month - 1 - MESES, 12)
        return date(ano, mes + 1, 1)

    def mes(self, indice: int) -> date:
        ano, mes = divmod(self.inicio.year * 12 + self.inicio.month - 1 + indice, 12)
        return date(ano, mes + 1, 1)

    # --- calendar --------------------------------------------------------

    @staticmethod
    def dia_util(dia: date) -> date:
        while dia.weekday() >= 5 or dia in feriados_bancarios(dia.year):
            dia += timedelta(days=1)
        return dia

    def liquidacao(self, pregao: date) -> date:
        dia = pregao
        for _ in range(2):
            dia = self.dia_util(dia + timedelta(days=1))
        return dia

    def preco(self, ticker: str, indice: int) -> float:
        tendencia = 1 + 0.004 * indice
        return round(PAPEIS[ticker].preco * tendencia * (1 + self.rng.uniform(-0.03, 0.03)), 2)

    # --- events ----------------------------------------------------------

    def negociar(self, pregao: date, ordens: list[tuple[str, str, int, float]]) -> None:
        """One nota with these orders: (ticker, 'compra' | 'venda', quantity, price)."""
        if pregao >= self.hoje or not ordens:
            return
        nota_id = str(self._proxima_nota)
        self._proxima_nota += 1
        negociacoes = []
        total_taxas = 0.0
        compras = vendas = 0.0
        for linha, (ticker, tipo, quantidade, preco) in enumerate(ordens):
            bruto = round(quantidade * preco, 2)
            taxa = round(bruto * 0.0003, 2)
            total_taxas += taxa
            compra = tipo == "compra"
            liquido = round(bruto + taxa if compra else bruto - taxa, 2)
            if compra:
                compras += bruto
            else:
                vendas += bruto
            negociacoes.append(_negociacao(nota_id, linha, pregao, ticker, tipo, quantidade, preco, bruto, taxa, liquido))

            p = self.posicoes.setdefault(ticker, Posicao())
            if compra:
                p.quantidade += quantidade
                p.custo += liquido
            else:
                p.custo -= p.medio * quantidade
                p.quantidade -= quantidade

            dia = self.liquidacao(pregao)
            if dia < self.hoje:
                self.b3.append(LinhaB3(
                    "Credito" if compra else "Debito", dia, "Transferência - Liquidação",
                    PAPEIS[ticker].produto, INSTITUICAO, Decimal(quantidade),
                    Decimal(str(preco)), Decimal(str(bruto)),
                ))
        self.notas.append(DocumentoTransformado(
            nota=_nota(nota_id, pregao, self.liquidacao(pregao), compras, vendas, round(total_taxas, 2)),
            negociacoes=negociacoes,
        ))

    def vender(self, pregao: date, indice: int, ticker: str, fracao: float, fator: float) -> float:
        """Sell a fraction of the position at fator × average cost; returns the gross value."""
        p = self.posicoes[ticker]
        quantidade = max(1, int(p.quantidade * fracao))
        preco = round(p.medio * fator, 2)
        self.negociar(pregao, [(ticker, "venda", quantidade, preco)])
        return quantidade * preco

    def provento(self, dia: date, ticker: str, movimentacao: str, por_cota: float) -> None:
        p = self.posicoes.get(ticker)
        if dia >= self.hoje or not p or not p.quantidade:
            return
        por_cota *= 1 + self.rng.uniform(-0.05, 0.05)
        valor = (Decimal(str(por_cota)) * p.quantidade).quantize(Decimal("0.01"))
        self.b3.append(LinhaB3(
            "Credito", dia, movimentacao, PAPEIS[ticker].produto, INSTITUICAO,
            Decimal(p.quantidade), (valor / p.quantidade).quantize(Decimal("0.01")), valor,
        ))

    def bonificar(self, dia: date, ticker: str, fracao: float) -> None:
        p = self.posicoes[ticker]
        quantidade = int(p.quantidade * fracao)
        if dia >= self.hoje or not quantidade:
            return
        # The cost of bonus shares is not on the statement: left for Pendências.
        p.quantidade += quantidade
        self.b3.append(LinhaB3(
            "Credito", dia, "Bonificação em Ativos", PAPEIS[ticker].produto, INSTITUICAO,
            Decimal(quantidade), None, None,
        ))

    # --- the two years ---------------------------------------------------

    def gerar(self) -> None:
        for indice in range(MESES + 1):
            primeiro = self.mes(indice)
            dia = lambda d: self.dia_util(primeiro.replace(day=d))  # noqa: E731

            for ticker, eventos in PROVENTOS.items():
                for movimentacao, meses, por_cota in eventos:
                    if movimentacao == "Juros Sobre Capital Próprio" and meses is None:
                        self.provento(dia(2), ticker, movimentacao, por_cota)

            if indice == 0:
                ordens = [(t, "compra", q, self.preco(t, 0)) for t, q in COMPRA_INICIAL.items()]
            else:
                escolhidas = [ACOES[indice % len(ACOES)], ACOES[(indice + 2) % len(ACOES)], FIIS[indice % len(FIIS)]]
                ordens = []
                for ticker in escolhidas:
                    preco = self.preco(ticker, indice)
                    ordens.append((ticker, "compra", max(1, round(self.rng.uniform(900, 1600) / preco)), preco))
            self.negociar(dia(5), ordens)

            venda = dia(12)
            if indice == VENDA_ISENTA:
                self.vender(venda, indice, "ABEV3", 0.4, 1.18)
            elif indice == VENDA_TRIBUTADA:
                total = self.vender(venda, indice, "SUZB3", 1.0, 1.22)
                if total < 25_000:
                    self.vender(venda, indice, "CMIG4", 0.6, 1.15)
            elif indice == VENDA_COM_PREJUIZO:
                self.vender(venda, indice, "RADL3", 0.7, 0.84)
            elif indice == MESES - 1:
                self.vender(venda, indice, "BTLG11", 0.3, 1.12)

            if indice == MES_BONIFICACAO:
                self.bonificar(dia(18), "BBDC4", 0.10)

            for ticker, eventos in PROVENTOS.items():
                for movimentacao, meses, por_cota in eventos:
                    if meses is None and movimentacao == "Rendimento":
                        self.provento(dia(15), ticker, movimentacao, por_cota)
                    elif meses and primeiro.month in meses:
                        self.provento(dia(22), ticker, movimentacao, por_cota)


def _nota(nota_id: str, pregao: date, liquidacao: date, compras: float, vendas: float, taxas: float) -> NotaRecord:
    campos = {f: None for f in NotaRecord.__dataclass_fields__}
    return NotaRecord(**campos | {
        "nota_id": nota_id,
        "corretora_id": "nu_invest",
        "doc_type": "NotaCorretagem",
        "data_pregao": pregao,
        "data_de_liquidacao": liquidacao,
        "cpf_cliente": CPF,
        "codigo_cliente": "000000",
        "nome_cliente": "Investidor de Demonstração",
        "compras_a_vista": round(compras, 2),
        "vendas_a_vista": round(vendas, 2),
        "taxa_de_liquidacao": taxas,
        "valor_liquido_das_operacoes": round(vendas - compras, 2),
    })


def _negociacao(nota_id, linha, pregao, ticker, tipo, quantidade, preco, bruto, taxa, liquido) -> NegociacaoRecord:
    campos = {f: None for f in NegociacaoRecord.__dataclass_fields__}
    return NegociacaoRecord(**campos | {
        "nota_id": nota_id,
        "corretora_id": "nu_invest",
        "doc_type": "NotaCorretagem",
        "linha_na_nota": linha,
        "raw_ticker": PAPEIS[ticker].especificacao,
        "data": pregao,
        "sentido": "entrada" if tipo == "compra" else "saida",
        "tipo": tipo,
        "debito_credito": "D" if tipo == "compra" else "C",
        "quantidade": float(quantidade),
        "preco_unitario": preco,
        "valor_bruto": bruto,
        "taxas_proporcionais": taxa,
        "valor_liquido": liquido,
        "mercado": "BOVESPA",
        "tipo_de_mercado": "VISTA",
    })


def recriar(hoje: date | None = None) -> int:
    """Drop the demo account (if any) and build it again. Returns its usuario_id."""
    hoje = hoje or date.today()
    gerador = Gerador(hoje)
    gerador.gerar()
    with connect_sistema() as conn:
        antigo = auth.usuario_demo(conn)
        if antigo is not None:
            auth.excluir_usuario(conn, antigo)
        usuario_id = scalar(
            conn,
            "INSERT INTO usuarios (email, nome, demo) VALUES (:email, :nome, true) RETURNING id",
            email=EMAIL, nome="Demonstração",
        )
        for doc in gerador.notas:
            loader.carregar(conn, usuario_id, doc, f"nota-{doc.nota.nota_id}.pdf")
        investidor_id = scalar(conn, "SELECT id FROM investidores WHERE usuario_id = :u", u=usuario_id)
        execute(conn, "UPDATE investidores SET apelido = :a WHERE id = :i", a=APELIDO, i=investidor_id)
        carregar_linhas(conn, investidor_id, gerador.b3, ARQUIVO_B3)

        pago = gerador.mes(VENDA_TRIBUTADA)
        for m in apuracao.apuracao(conn, investidor_id):
            if m.mes == pago and m.darf > 0:
                execute(
                    conn,
                    "INSERT INTO darfs_pagos (investidor_id, mes, valor_pago, pago_em) VALUES (:i, :mes, :v, :em)",
                    i=investidor_id, mes=m.mes, v=m.darf, em=vencimento(m.mes) - timedelta(days=3),
                )
    return usuario_id


def resumo(usuario_id: int) -> dict[str, int]:
    """Row counts of the demo account, for the command-line report."""
    with connect_sistema() as conn:
        linhas = fetch_all(
            conn,
            """
            SELECT
                (SELECT count(*) FROM notas n JOIN investidores i ON i.id = n.investidor_id WHERE i.usuario_id = :u) AS notas,
                (SELECT count(*) FROM negociacoes n JOIN investidores i ON i.id = n.investidor_id WHERE i.usuario_id = :u) AS negociacoes,
                (SELECT count(*) FROM b3_movimentacoes b JOIN investidores i ON i.id = b.investidor_id WHERE i.usuario_id = :u) AS movimentacoes_b3
            """,
            u=usuario_id,
        )
    return dict(linhas[0])
