"""
Spot price feeds for BTC, ETH, and Gold.
All public APIs — no auth required.

Used as signal sources for Kalshi daily contracts (KXBTCD, KXETHD, KXSOLD),
which have no intraday price history of their own since each contract is
freshly issued each day.
"""

import datetime
import json
import logging
import urllib.request

import pandas as pd

logger = logging.getLogger(__name__)

_COINBASE_URL = "https://api.exchange.coinbase.com/products/{}/candles"
_YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{}"
    "?interval=1h&range=7d"
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "price", "volume", "bid", "ask"])


def _fetch_coinbase(product: str, lookback: int, granularity: int) -> pd.DataFrame:
    """Fetch OHLCV from Coinbase Exchange public API."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(seconds=lookback * granularity)
        url = (
            f"{_COINBASE_URL.format(product)}"
            f"?granularity={granularity}"
            f"&start={start.isoformat()}"
            f"&end={now.isoformat()}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "polymarket-bot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            candles = json.loads(resp.read())

        if not candles:
            return _empty()

        # Coinbase: [time, low, high, open, close, volume], newest first
        rows = []
        for c in candles:
            ts, low, high, open_, close, volume = c
            close = float(close)
            rows.append({
                "timestamp": datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc),
                "price": close,
                "volume": float(volume),
                "bid": round(close * 0.9995, 2),
                "ask": round(close * 1.0005, 2),
            })

        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        return df.tail(lookback).reset_index(drop=True)

    except Exception as exc:
        logger.error("Coinbase feed failed for %s: %s", product, exc)
        return _empty()


def _fetch_yahoo(symbol: str, lookback: int) -> pd.DataFrame:
    """Fetch hourly OHLCV from Yahoo Finance public API (up to 7 days)."""
    try:
        url = _YAHOO_URL.format(symbol)
        req = urllib.request.Request(url, headers={"User-Agent": "polymarket-bot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        volumes = result["indicators"]["quote"][0].get("volume", [0] * len(closes))

        rows = []
        for ts, close, volume in zip(timestamps, closes, volumes):
            if close is None:
                continue
            close = float(close)
            rows.append({
                "timestamp": datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc),
                "price": close,
                "volume": float(volume or 0),
                "bid": round(close * 0.9995, 2),
                "ask": round(close * 1.0005, 2),
            })

        if not rows:
            return _empty()

        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        return df.tail(lookback).reset_index(drop=True)

    except Exception as exc:
        logger.error("Yahoo feed failed for %s: %s", symbol, exc)
        return _empty()


# ── Public feed functions ─────────────────────────────────────────────────────

def fetch_btc_spot(lookback: int = 100, granularity: int = 3600) -> pd.DataFrame:
    df = _fetch_coinbase("BTC-USD", lookback, granularity)
    if not df.empty:
        logger.info("BTC spot feed: %d candles, latest $%.2f", len(df), df["price"].iloc[-1])
    return df


def fetch_eth_spot(lookback: int = 100, granularity: int = 3600) -> pd.DataFrame:
    df = _fetch_coinbase("ETH-USD", lookback, granularity)
    if not df.empty:
        logger.info("ETH spot feed: %d candles, latest $%.2f", len(df), df["price"].iloc[-1])
    return df


def fetch_gold_spot(lookback: int = 100) -> pd.DataFrame:
    """Fetch gold spot price (GC=F futures) from Yahoo Finance."""
    df = _fetch_yahoo("GC=F", lookback)
    if not df.empty:
        logger.info("Gold spot feed: %d candles, latest $%.2f", len(df), df["price"].iloc[-1])
    return df
