"""
Discovers and ranks Kalshi financial markets by recent activity.
Markets closest to 50¢ have the most uncertainty and price movement.
Re-scans every SCAN_INTERVAL seconds; results are cached between scans.
"""

import json
import logging
import time

logger = logging.getLogger(__name__)

# Known financial series to scan across
FINANCIAL_SERIES = [
    "KXBTCD",        # Bitcoin daily
    "KXETHD",        # Ethereum daily
    "KXSOLD",        # Gold daily
]

# Maps series prefix -> spot feed function
_SPOT_FEED = {
    "KXBTCD": ("BTC", "bot.spot_feed", "fetch_btc_spot"),
    "KXETHD": ("ETH", "bot.spot_feed", "fetch_eth_spot"),
    "KXSOLD": ("Gold", "bot.spot_feed", "fetch_gold_spot"),
}

SCAN_INTERVAL = 300  # seconds between market re-scans (5 min)


class KalshiMarketScanner:
    """
    Fetches open markets across financial series and ranks them by
    how close their price is to 50¢ — markets nearest 50¢ are the
    most actively contested and tend to have the most price movement.
    """

    def __init__(self, markets_api, top_n: int = 5):
        self._api = markets_api
        self.top_n = top_n
        self._cache: list = []
        self._last_scan: float = 0.0

    def get_markets(self) -> list:
        """Return cached top markets, refreshing every SCAN_INTERVAL seconds."""
        if not self._cache or (time.time() - self._last_scan) > SCAN_INTERVAL:
            self._cache = self._scan()
            self._last_scan = time.time()
        return self._cache

    def _scan(self) -> list:
        logger.info("Scanning Kalshi financial markets...")
        all_tickers = []

        for series in FINANCIAL_SERIES:
            try:
                resp = self._api.get_markets(limit=100, series_ticker=series, status="open")
                tickers = [m.ticker for m in (resp.markets or [])]
                # For BTC daily contracts, keep all available so we can filter by proximity
                if series == "KXBTCD":
                    all_tickers.extend(tickers)
                else:
                    all_tickers.extend(tickers[:20])
                time.sleep(0.2)
            except Exception as exc:
                logger.debug("Scanner skipped series %s: %s", series, exc)
                time.sleep(0.5)

        if not all_tickers:
            logger.warning("Scanner found no financial markets — using known fallbacks")
            return ["KXFED-27APR-T4.25", "KXCPI-26MAY-T1.0"]

        def _strike(ticker):
            try:
                return float(ticker.rsplit("-T", 1)[1])
            except Exception:
                return float("inf")

        # For each spot-feed series, sort by proximity to current spot price
        import importlib
        result = []
        for prefix, (label, module, func) in _SPOT_FEED.items():
            tickers = [t for t in all_tickers if t.startswith(prefix)]
            if not tickers:
                continue
            try:
                feed_fn = getattr(importlib.import_module(module), func)
                kwargs = {"lookback": 1}
                if prefix != "KXSOLD":
                    kwargs["granularity"] = 3600
                spot_df = feed_fn(**kwargs)
                if not spot_df.empty:
                    spot_price = spot_df["price"].iloc[-1]
                    tickers.sort(key=lambda t: abs(_strike(t) - spot_price))
                    logger.info(
                        "%s spot @ $%.2f — nearest strike: %s",
                        label, spot_price, tickers[0],
                    )
            except Exception as exc:
                logger.warning("Spot feed failed for %s: %s", prefix, exc)
            result.extend(tickers[: self.top_n])

        if not result:
            result = ["KXFED-27APR-T4.25", "KXCPI-26MAY-T1.0"]

        logger.info("Selected %d markets: %s", len(result), result)
        return result
