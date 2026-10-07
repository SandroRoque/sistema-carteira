"""CNPJ and legal name behind each listed ticker, from CVM's open data.

The IRPF declaration asks for the CNPJ of the company behind a stock and of
the fund behind a quota (and of whoever paid each dividend). Three public
CVM datasets (https://dados.cvm.gov.br/) carry it:

- FCA (Formulário Cadastral) of listed companies: each security's trading
  code (Codigo_Negociacao) next to the company's CNPJ. Covers shares and units.
- the monthly report of FIIs (inf_mensal_fii) and of Fiagros
  (inf_mensal_fiagro): each fund's ISIN next to its CNPJ. B3's instruments
  register (see classes_b3) gives the ISIN of each listed ticker; a fund no
  longer listed is matched by the issuer code inside the ISIN (BRXXXXCTF... → XXXX11).

    uv run python cnpjs.py    # rebuild dados/cnpjs.csv

ETFs, infrastructure funds and FIDCs are not covered: their CVM reports do
not carry the ISIN. Those rows stay without a CNPJ and the IRPF page says so.
"""

from __future__ import annotations

import csv
import io
import re
import sys
import urllib.request
import zipfile
from datetime import date
from functools import cache
from pathlib import Path

import classes_b3

ARQUIVO = Path(__file__).resolve().parent / "dados" / "cnpjs.csv"
_CVM = "https://dados.cvm.gov.br/dados"
_FCA = _CVM + "/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{ano}.zip"
_FII = _CVM + "/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{ano}.zip"
_FIAGRO = _CVM + "/FIAGRO/DOC/INF_MENSAL/DADOS/inf_mensal_fiagro_{ano}{mes:02d}.zip"
_ACOES = {"Ações Ordinárias", "Ações Preferenciais", "Units"}


def formatar(cnpj: str) -> str | None:
    """'11222333000181' or '11.222.333/0001-81' → '11.222.333/0001-81'."""
    d = re.sub(r"\D", "", cnpj or "")
    if len(d) != 14:
        return None
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _baixar(url: str) -> zipfile.ZipFile | None:
    print(url, file=sys.stderr)
    try:
        # CVM, like B3, refuses urllib's default User-Agent.
        pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(pedido, timeout=300) as resposta:
            return zipfile.ZipFile(io.BytesIO(resposta.read()))
    except Exception as exc:  # a year or month not published yet
        print(f"  ignorado: {exc}", file=sys.stderr)
        return None


def _csv(z: zipfile.ZipFile, contem: str) -> list[dict]:
    nome = next(n for n in z.namelist() if contem in n)
    with z.open(nome) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";"))


def ler_fca(linhas: list[dict]) -> dict[str, tuple[str, str]]:
    """{ticker: (cnpj, name)} of shares and units in an FCA securities file."""
    saida = {}
    for r in linhas:
        ticker = (r.get("Codigo_Negociacao") or "").strip().upper()
        cnpj = formatar(r.get("CNPJ_Companhia", ""))
        if ticker and cnpj and r.get("Valor_Mobiliario") in _ACOES:
            saida[ticker] = (cnpj, r["Nome_Empresarial"].strip())
    return saida


def ler_fundos(linhas: list[dict], coluna_cnpj: str, coluna_nome: str) -> dict[str, tuple[str, str]]:
    """{ISIN: (cnpj, name)} from a CVM monthly fund report."""
    saida = {}
    for r in linhas:
        isin = (r.get("Codigo_ISIN") or "").strip().upper()
        cnpj = formatar(r.get(coluna_cnpj, ""))
        if len(isin) == 12 and cnpj:
            saida[isin] = (cnpj, r[coluna_nome].strip())
    return saida


def ticker_do_isin(isin: str) -> str | None:
    """A fund quota ISIN (BR + issuer code + CTF...) → its usual ticker, XXXX11."""
    return isin[2:6] + "11" if isin.startswith("BR") and isin[6:9] == "CTF" else None


def gerar(destino: Path = ARQUIVO, hoje: date | None = None) -> int:
    hoje = hoje or date.today()
    tabela: dict[str, tuple[str, str]] = {}

    for ano in range(hoje.year - 4, hoje.year + 1):  # oldest first: newer data wins
        if z := _baixar(_FCA.format(ano=ano)):
            tabela.update(ler_fca(_csv(z, "valor_mobiliario")))

    fundos: dict[str, tuple[str, str]] = {}
    for ano in range(hoje.year - 2, hoje.year + 1):
        if z := _baixar(_FII.format(ano=ano)):
            fundos.update(ler_fundos(_csv(z, "geral"), "CNPJ_Fundo_Classe", "Nome_Fundo_Classe"))
    for meses_atras in range(12, -1, -1):
        m = hoje.year * 12 + hoje.month - 1 - meses_atras
        if z := _baixar(_FIAGRO.format(ano=m // 12, mes=m % 12 + 1)):
            fundos.update(ler_fundos(_csv(z, "fiagro_2"), "CNPJ_Classe", "Nome_Classe"))

    _, texto = classes_b3._baixar_cadastro()
    isin_do_ticker = classes_b3.isins(texto)
    ticker_listado = {isin: t for t, isin in isin_do_ticker.items()}
    for isin, dados in fundos.items():
        ticker = ticker_listado.get(isin) or ticker_do_isin(isin)
        if ticker and ticker not in tabela:
            tabela[ticker] = dados

    destino.parent.mkdir(exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "cnpj", "nome"])
        for ticker in sorted(tabela):
            w.writerow([ticker, *tabela[ticker]])
    return len(tabela)


@cache
def _tabela() -> dict[str, tuple[str, str]]:
    if not ARQUIVO.exists():
        return {}
    with ARQUIVO.open(encoding="utf-8") as f:
        return {r["ticker"]: (r["cnpj"], r["nome"]) for r in csv.DictReader(f)}


def emissor(ticker: str | None) -> tuple[str, str] | None:
    """(CNPJ, legal name) behind a ticker. A share class missing from the
    FCA (EMPX3 when only EMPX4 is listed there) takes its company's."""
    if not ticker:
        return None
    tabela = _tabela()
    if ticker in tabela:
        return tabela[ticker]
    if re.fullmatch(r"[A-Z0-9]{4}\d{1,2}", ticker) and not ticker.endswith("11"):
        raiz = ticker[:4]
        mesma_empresa = {v for t, v in tabela.items() if t[:4] == raiz and not t.endswith("11")}
        if len({c for c, _ in mesma_empresa}) == 1:
            return mesma_empresa.pop()
    return None


if __name__ == "__main__":
    print(gerar(), "linhas em", ARQUIVO)
