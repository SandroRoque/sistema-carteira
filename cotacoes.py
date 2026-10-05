"""Fetch current market prices for portfolio positions from B3 via yfinance.

All B3 tickers (ações, FIIs, BDRs, Tesouro Direto) are looked up with the
'.SA' suffix.  Instruments with no marketable price (renda_fixa, subscricoes)
are simply omitted from the result.

Usage
-----
    from cotacoes import buscar_cotacoes
    precos = buscar_cotacoes(["BBAS3", "VALE3", "BTCI11"])
    # {"BBAS3": 20.54, "VALE3": 83.19, "BTCI11": 9.24}
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import yfinance as yf

    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


def buscar_cotacoes(tickers: list[str]) -> dict[str, float | None]:
    """Return {ticker: last_close} for each ticker.

    Parameters
    ----------
    tickers:
        Canonical ticker codes without suffix, e.g. ['BBAS3', 'BTCI11'].

    Returns
    -------
    Dict mapping each input ticker to its last closing price (float) or None
    if the price could not be retrieved.
    """
    if not tickers:
        return {}

    if not _YF_AVAILABLE:
        logger.warning("yfinance não instalado — cotações indisponíveis")
        return {t: None for t in tickers}

    # yfinance uses the .SA suffix for B3 (São Paulo Stock Exchange)
    yf_to_canonical: dict[str, str] = {f"{t}.SA": t for t in tickers}
    yf_symbols = list(yf_to_canonical.keys())

    try:
        data = yf.download(
            yf_symbols,
            period="2d",          # 2 days so we get a price even on off-hours
            progress=False,
            auto_adjust=True,
        )
        if data.empty:
            return {t: None for t in tickers}

        # Single-ticker download returns a plain Series under 'Close'
        close = data["Close"]
        # Take the last available row
        last_row = close.iloc[-1]
    except Exception as exc:
        logger.warning("Erro ao buscar cotações: %s", exc)
        return {t: None for t in tickers}

    result: dict[str, float | None] = {}
    for yf_sym, canonical in yf_to_canonical.items():
        try:
            if hasattr(last_row, "__getitem__"):
                val = last_row[yf_sym]
            else:
                # Single-ticker: last_row is a scalar
                val = float(last_row)
            # Guard against NaN
            price = float(val) if val == val else None
            result[canonical] = price
        except (KeyError, TypeError, ValueError):
            result[canonical] = None

    return result
