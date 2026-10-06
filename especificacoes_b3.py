"""Ticker of a security described the way market-standard notas print it.

Notas from XP, Brasil Plural, Safra and other Sinacor brokers identify a
security by B3's short trading name and its specification ("PETROBRAS PN N2",
"MAGNESITA SA ON EJ NM"), not by its ticker. B3's historical quotes files
(COTAHIST, https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista/series-historicas/)
print the same two fields (NOMRES, ESPECI) next to the ticker (CODNEG) for
every trading day, which also covers renamed and delisted companies.

    uv run python especificacoes_b3.py 2015 2026    # rebuild dados/especificacoes_b3.csv

keeps, for each (name, share class, ticker), the first and last trading day
it was seen; `resolver` picks the ticker valid on a trade's date.
"""

from __future__ import annotations

import csv
import io
import re
import sys
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent / "dados" / "especificacoes_b3.csv"
URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ano}.ZIP"

# Share classes as they open the specification (ESPECI).
CLASSES = {
    "ON", "PN", "PNA", "PNB", "PNC", "PND", "PNE", "PNF", "PNG", "PNH", "UNT", "CI",
    "DR1", "DR2", "DR3", "DRN", "DRE", "BDR", "REC", "DIR", "BNS", "CPA", "PCD",
    "ONP", "PNP", "ONR", "PNR", "OR", "PR", "ON*", "PN*",
}
# Markets kept: lot-standard cash trading (TPMERC 010). Fractional codes
# (ending in F) describe the same securities; options are resolved by code.
_MERCADO_A_VISTA = "010"


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", sem_acento.upper())


@dataclass(frozen=True)
class Especificacao:
    nome: str
    classe: str
    ticker: str
    primeiro: date
    ultimo: date


# ---------------------------------------------------------------------------
# Building the table from COTAHIST
# ---------------------------------------------------------------------------

def baixar(ano: int) -> bytes:
    """One year of COTAHIST, zipped."""
    print(f"COTAHIST {ano}...", file=sys.stderr)
    # B3 refuses urllib's default User-Agent.
    pedido = urllib.request.Request(URL.format(ano=ano), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(pedido, timeout=600) as resposta:
        return resposta.read()


def _linhas_cotahist(conteudo: bytes):
    """(date, ticker, name, specification, market group) of each cash-market quote.

    The market group (CODBDI) is B3's own grouping: 02 lot-standard
    stocks and units, 12 FIIs, 14 ETFs and other funds."""
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        nome = z.namelist()[0]
        with z.open(nome) as f:
            for bruta in io.TextIOWrapper(f, encoding="latin-1"):
                if not bruta.startswith("01") or bruta[24:27] != _MERCADO_A_VISTA:
                    continue
                dia = date(int(bruta[2:6]), int(bruta[6:8]), int(bruta[8:10]))
                yield dia, bruta[12:24].strip(), bruta[27:39].strip(), bruta[39:49].split(), bruta[10:12]


def gerar(anos: list[int], destino: Path = ARQUIVO) -> int:
    """Download COTAHIST for these years and write the table. Returns its row count."""
    vistos: dict[tuple[str, str, str], list[date]] = {}
    for ano in anos:
        for dia, ticker, nome, especi, _ in _linhas_cotahist(baixar(ano)):
            if not especi or especi[0] not in CLASSES:
                continue
            chave = (nome, especi[0], ticker)
            datas = vistos.setdefault(chave, [dia, dia])
            datas[0] = min(datas[0], dia)
            datas[1] = max(datas[1], dia)
    destino.parent.mkdir(exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nome", "classe", "ticker", "primeiro", "ultimo"])
        for (nome, classe, ticker), (primeiro, ultimo) in sorted(vistos.items()):
            w.writerow([nome, classe, ticker, primeiro.isoformat(), ultimo.isoformat()])
    return len(vistos)


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------

@cache
def _tabela() -> tuple[dict[tuple[str, str], list[Especificacao]], dict[tuple[str, str], list[Especificacao]]]:
    """(by normalized name and class, by ticker root and class)."""
    por_nome: dict[tuple[str, str], list[Especificacao]] = {}
    por_raiz: dict[tuple[str, str], list[Especificacao]] = {}
    if not ARQUIVO.exists():
        return por_nome, por_raiz
    with ARQUIVO.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            e = Especificacao(r["nome"], r["classe"], r["ticker"],
                              date.fromisoformat(r["primeiro"]), date.fromisoformat(r["ultimo"]))
            por_nome.setdefault((_norm(e.nome), e.classe), []).append(e)
            por_raiz.setdefault((e.ticker[:4], e.classe), []).append(e)
    return por_nome, por_raiz


def _mais_proximo(candidatos: list[Especificacao], dia: date | None) -> str | None:
    tickers = {e.ticker for e in candidatos}
    if len(tickers) == 1:
        return tickers.pop()
    if dia is None:
        return max(candidatos, key=lambda e: e.ultimo).ticker
    def distancia(e: Especificacao) -> int:
        if e.primeiro <= dia <= e.ultimo:
            return 0
        return min(abs((e.primeiro - dia).days), abs((dia - e.ultimo).days))
    return min(candidatos, key=lambda e: (distancia(e), -e.ultimo.toordinal())).ticker


def resolver(especificacao: str, dia: date | None = None) -> str | None:
    """The ticker of 'NAME CLASS [flags] [governance]' on the trade date, or None.

    The name is everything before the first share-class word. Some notas
    print the ticker root instead of the name ("PETR PN N2"): that is tried
    when the name is unknown.
    """
    palavras = especificacao.upper().split()
    posicao = next((i for i, p in enumerate(palavras) if p in CLASSES and i > 0), None)
    if posicao is None:
        return None
    nome, classe = " ".join(palavras[:posicao]), palavras[posicao]
    por_nome, por_raiz = _tabela()
    candidatos = por_nome.get((_norm(nome), classe))
    if not candidatos and re.fullmatch(r"[A-Z]{4}", nome):
        candidatos = por_raiz.get((nome, classe))
    if not candidatos:
        return None
    return _mais_proximo(candidatos, dia)


# An option code: ticker root, series letter (calls A-L, puts M-X by month),
# strike number, optional weekly suffix; exercise rows add an E.
_OPCAO = re.compile(r"^([A-Z]{4})[A-X]\d{1,3}(?:W\d)?E?$")


def subjacente(especificacao: str, dia: date | None = None) -> str | None:
    """Ticker of the shares behind an option ("PETRA375E PN 36,27" -> PETR4).

    An option exercise on a nota is a trade of the underlying shares at the
    strike price, so its row belongs to the stock, not to the option."""
    palavras = especificacao.upper().split()
    m = _OPCAO.match(palavras[0]) if palavras else None
    classe = next((p for p in palavras[1:] if p in CLASSES), None)
    if not m or not classe:
        return None
    return resolver(f"{m.group(1)} {classe}", dia)


if __name__ == "__main__":
    inicio, fim = int(sys.argv[1]), int(sys.argv[2])
    print(gerar(list(range(inicio, fim + 1))), "linhas em", ARQUIVO)
