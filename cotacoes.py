"""Fetch current market prices for portfolio positions from B3 via yfinance.

All B3 tickers (ações, FIIs, BDRs, Tesouro Direto) are looked up with the
'.SA' suffix.  Instruments with no marketable price (renda_fixa, subscricoes)
are simply omitted from the result.

Prices are cached in the shared `cotacoes` table: a ticker is downloaded at
most once per VALIDADE, whichever account asks for it. If a refresh fails,
the last cached price is returned rather than nothing.

Web pages never wait on the download: they read the cache (`ler_cache`)
and, when something is stale, ask for a refresh in a background thread
(`atualizar_em_segundo_plano`); the next page view shows the new prices.
Command-line reports call `buscar_cotacoes`, which downloads inline.

Usage
-----
    from cotacoes import buscar_cotacoes
    with connect(usuario_id) as conn:
        precos = buscar_cotacoes(conn, ["BBAS3", "VALE3", "BTCI11"])
    # {"BBAS3": Decimal("20.5400"), ...}
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Connection

from database import connect_sistema, execute, fetch_all

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


@dataclass(frozen=True)
class Cache:
    precos: dict[str, Decimal | None]
    # Oldest refresh among the prices shown (None when there is no price).
    atualizado_em: datetime | None
    vencidos: list[str]


def ler_cache(conn: Connection, tickers: list[str]) -> Cache:
    """Cached prices only, never a download. Lists what is missing or stale."""
    if not tickers:
        return Cache({}, None, [])
    rows = {
        r["ticker"]: r
        for r in fetch_all(
            conn,
            "SELECT ticker, preco, atualizado_em, atualizado_em > now() - :validade AS fresca "
            "FROM cotacoes WHERE ticker = ANY(:tickers)",
            tickers=list(tickers),
            validade=VALIDADE,
        )
    }
    precos = {t: rows[t]["preco"] if t in rows else None for t in tickers}
    com_preco = [rows[t]["atualizado_em"] for t in tickers if t in rows and rows[t]["preco"] is not None]
    vencidos = [t for t in dict.fromkeys(tickers) if t not in rows or not rows[t]["fresca"]]
    return Cache(precos, min(com_preco) if com_preco else None, vencidos)


_em_andamento: set[str] = set()
_trava = threading.Lock()


def atualizacao_automatica() -> bool:
    """CARTEIRA_COTACOES=false turns background downloads off (tests, offline)."""
    return os.environ.get("CARTEIRA_COTACOES", "true").lower() not in ("0", "false", "no")


def atualizar_em_segundo_plano(tickers: list[str]) -> bool:
    """Refresh these tickers in a background thread. Tickers already being
    refreshed are skipped. Returns whether a refresh was started."""
    if not tickers or not atualizacao_automatica():
        return False
    with _trava:
        novos = [t for t in tickers if t not in _em_andamento]
        if not novos:
            return False
        _em_andamento.update(novos)

    def tarefa() -> None:
        try:
            with connect_sistema() as conn:
                buscar_cotacoes(conn, novos)
        except Exception as exc:
            logger.warning("Atualização de cotações falhou: %s", type(exc).__name__)
        finally:
            with _trava:
                _em_andamento.difference_update(novos)

    threading.Thread(target=tarefa, name="cotacoes", daemon=True).start()
    return True


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
