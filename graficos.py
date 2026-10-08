"""Geometry for the server-rendered SVG charts.

The CSP forbids inline styles, so charts are plain SVG: rect positions in
percent of the chart width (the chart stretches with the page) and heights
in px. Colors come from series classes (.s-1 … in style.css), never from
attributes. Every mark carries a <title> for hover, and every chart sits
next to a legend or table with the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import formato

ZERO = Decimal(0)


@dataclass(frozen=True)
class Retangulo:
    x: str  # percent
    largura: str  # percent
    y: float
    altura: float
    serie: str
    titulo: str


@dataclass(frozen=True)
class Rotulo:
    x: str  # percent, center
    y: float
    texto: str


@dataclass(frozen=True)
class Colunas:
    altura: int
    base: float
    retangulos: list[Retangulo]
    valores: list[Rotulo]
    eixo: list[Rotulo]


def _pct(v: float) -> str:
    return f"{v:.3f}"


def faixa(fatias: list[tuple[Decimal, str, str]]) -> list[Retangulo]:
    """One horizontal stacked bar: (fraction, series, title) per slice.
    Slices are separated by a 2px surface stroke (see .faixa in the CSS)."""
    retangulos, x = [], 0.0
    for fracao, serie, titulo in fatias:
        largura = float(fracao) * 100
        retangulos.append(Retangulo(_pct(x), _pct(largura), 0, 40, serie, titulo))
        x += largura
    return retangulos


def colunas(
    grupos: list[tuple[str, list[tuple[Decimal, str, str]]]],
    altura: int = 220,
    mostrar_valores: bool = True,
) -> Colunas:
    """Vertical (stacked) columns. grupos: (axis label, [(value, series, title)])
    bottom to top. Totals are written above each column."""
    topo, rodape = 18, 22
    area = altura - topo - rodape
    base = altura - rodape
    maximo = max((sum((v for v, _, _ in segs), ZERO) for _, segs in grupos), default=ZERO)
    n = len(grupos) or 1
    passo = 100 / n
    largura = passo * 0.62
    retangulos, valores, eixo = [], [], []
    for i, (rotulo, segmentos) in enumerate(grupos):
        x = i * passo + (passo - largura) / 2
        centro = _pct(i * passo + passo / 2)
        y = base
        total = ZERO
        for valor, serie, titulo in segmentos:
            if valor <= 0 or not maximo:
                continue
            h = max(float(valor / maximo) * area, 2.0)
            y -= h
            retangulos.append(Retangulo(_pct(x), _pct(largura), round(y, 1), round(h, 1), serie, titulo))
            y -= 2  # surface gap between stacked segments
            total += valor
        if mostrar_valores and total > 0:
            valores.append(Rotulo(centro, round(y - 4 + 2, 1), formato.numero(total, 0)))
        eixo.append(Rotulo(centro, altura - 6, rotulo))
    return Colunas(altura, base, retangulos, valores, eixo)
