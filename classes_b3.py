"""Asset class of tickers ending in 11, from B3's public data.

A ticker ending in 11 can be a FII, a Fiagro, a unit (TAEE11), an ETF
(BOVA11) or another listed fund (infrastructure, FIDC, FIP), and each is
taxed differently. Two public B3 sources tell them apart:

- the instruments register (Cadastro de Instrumentos, "InstrumentsConsolidatedFile"
  at https://arquivos.b3.com.br/), which gives each listed ticker a security
  category (SHARES, UNIT, ETF EQUITIES, ETF FOREIGN INDEX, FUNDS...) and the
  fund's full name. It only covers what is listed on that day;
- the historical quotes (COTAHIST, see especificacoes_b3), whose market group
  (CODBDI 12 = FIIs) and specification (UNT = unit) also cover delisted tickers.

    uv run python classes_b3.py 2015 2026    # rebuild dados/classes_b3.csv

The table is committed (public data). `classe` returns (tipo, subtipo) for
a ticker in it, or None so the caller falls back to the ticker suffix.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import especificacoes_b3

ARQUIVO = Path(__file__).resolve().parent / "dados" / "classes_b3.csv"
_CADASTRO = "https://arquivos.b3.com.br/api/download/requestname?fileName=InstrumentsConsolidatedFile&date={dia}"
_DOWNLOAD = "https://arquivos.b3.com.br/api/download/?token={token}"

Classe = tuple[str, str | None]


def _palavras(nome: str) -> set[str]:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return set(re.findall(r"[A-Z0-9]+", sem_acento.upper()))


def fundo(nome: str, grupo: str | None) -> Classe:
    """Class of a listed fund from its name and COTAHIST market group.

    Fiagros pay FII tax, so they are FIIs with a subtipo. Infrastructure
    funds, FIP-IE, FIDCs and anything unrecognized are 'fundo': their tax
    is not the monthly DARF's, so they stay out of it."""
    p = _palavras(nome)
    if grupo == "12":
        return "fii", None
    if p & {"FIAGRO", "FIAG"}:
        return "fii", "fiagro"
    if p & {"FIDC", "CREDITORIOS"}:
        return "fundo", "fidc"
    if "FIP" in p:
        return "fundo", "fip"
    if p & {"INFRA", "IE", "INFRAESTRUTURA"}:
        return "fundo", "infra"
    if p & {"IMOB", "FII", "IMOBILIARIO", "IMOBILIARIA"}:
        return "fii", None
    return "fundo", None


def do_cadastro(categoria: str, nome: str, grupo: str | None, etf_renda_fixa: bool) -> Classe | None:
    """Class from the instruments register's security category."""
    if etf_renda_fixa:
        return "etf", "renda_fixa"
    return {
        "SHARES": ("acao", None),
        "UNIT": ("acao", "unit"),
        "BDR": ("bdr", None),
        "ETF EQUITIES": ("etf", "acoes"),
        "ETF FOREIGN INDEX": ("etf", "exterior"),
    }.get(categoria) or (fundo(nome, grupo) if categoria == "FUNDS" else None)


def do_historico(nome: str, especi: str, grupo: str) -> Classe | None:
    """Class of a ticker seen only in COTAHIST (no longer listed)."""
    if especi == "UNT":
        return "acao", "unit"
    if especi != "CI":
        return None
    return fundo(nome, grupo)


# ---------------------------------------------------------------------------
# Building the table
# ---------------------------------------------------------------------------

def _baixar_cadastro() -> tuple[date, str]:
    """The latest instruments register B3 still serves (it keeps a few days)."""
    dia = date.today()
    for _ in range(10):
        dia -= timedelta(days=1)
        if dia.weekday() >= 5:
            continue
        pedido = urllib.request.Request(_CADASTRO.format(dia=dia.isoformat()), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(pedido, timeout=60) as resposta:
            token = json.load(resposta).get("token")
        if not token:
            continue
        pedido = urllib.request.Request(_DOWNLOAD.format(token=token), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(pedido, timeout=600) as resposta:
            texto = resposta.read().decode("latin-1")
        if "TckrSymb" in texto[:2000]:
            return dia, texto
    raise RuntimeError("B3 não serviu o cadastro de instrumentos dos últimos dias")


def ler_cadastro(texto: str) -> dict[str, tuple[str, str]]:
    """{ticker: (category, name)} of cash-market tickers ending in 11, with the
    fixed-income ETFs (listed in the fixed-income market, with a primary-market
    ETF instrument on the same asset) marked as 'ETF RENDA FIXA'."""
    linhas = texto.splitlines()
    inicio = next(i for i, linha in enumerate(linhas) if linha.startswith("RptDt;"))
    registros = list(csv.DictReader(linhas[inicio:], delimiter=";"))
    com_etf = {r["Asst"] for r in registros if r["SctyCtgyNm"].startswith("ETF PRIMARY MARKET")}
    saida = {}
    for r in registros:
        ticker = r["TckrSymb"]
        if not ticker.endswith("11"):
            continue
        if r["SgmtNm"] == "CASH":
            saida[ticker] = (r["SctyCtgyNm"], r["CrpnNm"])
        elif r["MktNm"] == "FIXED INCOME" and r["Asst"] in com_etf:
            saida.setdefault(ticker, ("ETF RENDA FIXA", r["CrpnNm"]))
    return saida


def gerar(anos: list[int], destino: Path = ARQUIVO) -> int:
    historico: dict[str, tuple[date, str, str, str]] = {}  # ticker -> (last day, name, spec, group)
    for ano in anos:
        for dia, ticker, nome, especi, grupo in especificacoes_b3._linhas_cotahist(especificacoes_b3.baixar(ano)):
            if ticker.endswith("11") and especi and (ticker not in historico or dia >= historico[ticker][0]):
                historico[ticker] = (dia, nome, especi[0], grupo)
    dia_cadastro, texto = _baixar_cadastro()
    print(f"cadastro de instrumentos de {dia_cadastro}", file=sys.stderr)
    cadastro = ler_cadastro(texto)

    tabela: dict[str, Classe] = {}
    for ticker in sorted(set(historico) | set(cadastro)):
        grupo = historico[ticker][3] if ticker in historico else None
        if ticker in cadastro:
            categoria, nome = cadastro[ticker]
            classe = do_cadastro(categoria, nome, grupo, categoria == "ETF RENDA FIXA")
        else:
            _, nome, especi, grupo = historico[ticker]
            classe = do_historico(nome, especi, grupo)
        if classe:
            tabela[ticker] = classe
    destino.parent.mkdir(exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "tipo", "subtipo"])
        for ticker, (tipo, subtipo) in tabela.items():
            w.writerow([ticker, tipo, subtipo or ""])
    return len(tabela)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

@cache
def _tabela() -> dict[str, Classe]:
    if not ARQUIVO.exists():
        return {}
    with ARQUIVO.open(encoding="utf-8") as f:
        return {r["ticker"]: (r["tipo"], r["subtipo"] or None) for r in csv.DictReader(f)}


def classe(ticker: str) -> Classe | None:
    """(tipo, subtipo) of a ticker in the table, or None."""
    return _tabela().get(ticker)


if __name__ == "__main__":
    inicio, fim = int(sys.argv[1]), int(sys.argv[2])
    print(gerar(list(range(inicio, fim + 1))), "linhas em", ARQUIVO)
