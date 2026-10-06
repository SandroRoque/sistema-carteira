"""Fetch current market prices for portfolio positions from B3 via yfinance.

All B3 tickers (ações, FIIs, BDRs, Tesouro Direto) are looked up with the
'.SA' suffix.  Instruments with no marketable price (renda_fixa, subscricoes)
are simply omitted from the result.

Prices are cached in the shared `cotacoes` table: a ticker is downloaded at
most once per VALIDADE, whichever account asks for it. If a refresh fails,
the last cached price is returned rather than nothing.

Usage
-----
    from cotacoes import buscar_cotacoes
    with connect(usuario_id) as conn:
        precos = buscar_cotacoes(conn, ["BBAS3", "VALE3", "BTCI11"])
    # {"BBAS3": Decimal("20.5400"), ...}
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import Connection

from database import execute, fetch_all

logger = logging.getLogger(__name__)

# How long a cached price is served before it is downloaded again.
VALIDADE = timedelta(hours=1)


def buscar_cotacoes(conn: Connection, tickers: list[str]) -> dict[str, Decimal | None]:
    """Return {ticker: last_close} for each ticker.

    Parameters
    ----------
    conn:
        Any connection (tenant-scoped or sistema): the cache holds no tenant data.
    tickers:
        Canonical ticker codes without suffix, e.g. ['BBAS3', 'BTCI11'].

    Returns
    -------
    Dict mapping each input ticker to its last closing price (Decimal) or None
    if the price could not be retrieved.
    """
    if not tickers:
        return {}

    cache = {
        r["ticker"]: r
        for r in fetch_all(
            conn,
            "SELECT ticker, preco, atualizado_em > now() - :validade AS fresca "
            "FROM cotacoes WHERE ticker = ANY(:tickers)",
            tickers=list(tickers),
            validade=VALIDADE,
        )
    }
    result: dict[str, Decimal | None] = {t: cache[t]["preco"] if t in cache else None for t in tickers}

    vencidos = [t for t in dict.fromkeys(tickers) if t not in cache or not cache[t]["fresca"]]
    if not vencidos:
        return result

    try:
        baixados = _baixar_yfinance(vencidos)
    except Exception as exc:
        # Keep whatever the cache had: a stale price beats none.
        logger.warning("Erro ao buscar cotações: %s", exc)
        return result

    for ticker, preco in baixados.items():
        # A ticker yfinance does not know is cached as NULL too, so it is not
        # requested again on every page view.
        execute(
            conn,
            "INSERT INTO cotacoes (ticker, preco, atualizado_em) VALUES (:ticker, :preco, now()) "
            "ON CONFLICT (ticker) DO UPDATE "
            "SET preco = COALESCE(EXCLUDED.preco, cotacoes.preco), atualizado_em = now()",
            ticker=ticker,
            preco=preco,
        )
        if preco is not None:
            result[ticker] = preco
    return result


def _baixar_yfinance(tickers: list[str]) -> dict[str, Decimal | None]:
    """Download last closes. Raises when the download as a whole fails, so a
    network error is not mistaken for "no price" and cached as such."""
    import yfinance as yf

    # yfinance uses the .SA suffix for B3 (São Paulo Stock Exchange)
    yf_to_canonical: dict[str, str] = {f"{t}.SA": t for t in tickers}

    data = yf.download(
        list(yf_to_canonical),
        period="2d",          # 2 days so we get a price even on off-hours
        progress=False,
        auto_adjust=True,
    )
    if data.empty:
        raise RuntimeError("yfinance não retornou dados")

    # Single-ticker download returns a plain Series under 'Close'
    close = data["Close"]
    # Take the last available row
    last_row = close.iloc[-1]

    result: dict[str, Decimal | None] = {}
    for yf_sym, canonical in yf_to_canonical.items():
        try:
            if hasattr(last_row, "__getitem__"):
                val = last_row[yf_sym]
            else:
                # Single-ticker: last_row is a scalar
                val = float(last_row)
            # Guard against NaN. yfinance prices are float32-ish (55.36000061…):
            # 4 decimal places keep every real B3 price and drop the noise.
            result[canonical] = Decimal(f"{float(val):.4f}") if val == val else None
        except (KeyError, TypeError, ValueError):
            result[canonical] = None
    return result
