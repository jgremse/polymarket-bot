"""
Kalshi trader — uses kalshi-python KalshiClient.
Limit orders only. Prices are in cents (1–99).

Kalshi is CFTC-regulated and available to US traders — no VPN required.

Environment variables required (see .env.example)
--------------------------------------------------
KALSHI_API_KEY_ID       — UUID from kalshi.com > Settings > API Keys
KALSHI_PRIVATE_KEY_PATH — path to your RSA private key .pem file

Market tickers on Kalshi look like: KXBTCD-24DEC31-T50000
"""

import datetime
import json
import logging
import os
import uuid
from typing import List, Optional

import pandas as pd
from kalshi_python import KalshiClient, Configuration, CreateOrderRequest

from strategies.base_strategy import Signal, Side
from bot.base_trader import BaseTrader
from bot.risk_manager import RiskManager

logger = logging.getLogger(__name__)


def _to_cents(price: float) -> int:
    """Convert 0.0–1.0 probability to Kalshi cents (1–99)."""
    return max(1, min(99, round(price * 100)))


def _from_cents(cents) -> float:
    """Convert Kalshi cents to 0.0–1.0 probability."""
    return round(int(cents) / 100, 4)


class KalshiTrader(BaseTrader):
    """
    Wraps kalshi-python's KalshiClient.
    Prices are kept as 0.0–1.0 internally (matching strategy output)
    and converted to cents only at the API boundary.
    """

    @property
    def exchange_name(self) -> str:
        return "Kalshi"

    def __init__(self, risk_manager: RiskManager, dry_run: bool = False, db=None):
        super().__init__(risk_manager, dry_run, db=db)
        self._client = self._build_client()
        # Paper trading: track open positions for settlement {order_id -> position dict}
        self._paper_positions: dict = {}

    # ── Order management ─────────────────────────────────────────────────

    def place_order(self, signal: Signal, market_id: str) -> Optional[str]:
        cents = _to_cents(signal.price)

        if self.dry_run:
            order_id = f"paper-{uuid.uuid4().hex[:8]}"
            logger.info("[PAPER] Fill %s %d @ %.4f (%dc) on %s",
                        signal.side, int(signal.size), signal.price, cents, market_id)

            # Record fill in dashboard and DB (PnL=0 at entry; settled later)
            from dashboard.state import state as dashboard_state
            dashboard_state.add_fill(
                signal.side.value, signal.price, signal.size,
                pnl=0.0, strategy=signal.strategy,
            )
            if self.db:
                self.db.log_fill(
                    market_id, signal.strategy, signal.side.value,
                    signal.price, signal.size, pnl=0.0, order_id=order_id,
                )

            # Track position for settlement PnL
            self._paper_positions[order_id] = {
                "market_id": market_id,
                "side": signal.side,
                "entry_price": signal.price,
                "size": signal.size,
                "strategy": signal.strategy,
            }
            self._open_orders[market_id] = order_id
            self.rm.record_fill(market_id, signal.side, signal.size, signal.price)
            return order_id

        try:
            # Kalshi uses "yes"/"no" sides with buy/sell actions
            if signal.side == Side.BUY:
                side, action = "yes", "buy"
                yes_price, no_price = cents, None
            else:
                side, action = "no", "buy"
                yes_price, no_price = None, 100 - cents

            req = CreateOrderRequest(
                ticker=market_id,
                client_order_id=str(uuid.uuid4()),
                side=side,
                action=action,
                count=max(1, int(signal.size)),
                type="limit",
                yes_price=yes_price,
                no_price=no_price,
            )
            resp = self._client._portfolio_api.create_order(create_order_request=req)
            order_id = resp.order.order_id if resp and resp.order else None

            if order_id:
                self._open_orders[market_id] = order_id
                logger.info("Order placed | id=%s | %s %d @ %dc",
                            order_id, signal.side, int(signal.size), cents)
            return order_id

        except Exception as exc:
            logger.error("Failed to place order: %s", exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        if self.dry_run:
            logger.info("[DRY RUN] Would cancel order %s", order_id)
            return True
        try:
            self._client._portfolio_api.cancel_order(order_id=order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    def get_open_orders(self, market_id: str) -> List[dict]:
        try:
            resp = self._client._portfolio_api.get_orders(ticker=market_id, status="resting")
            return [o.model_dump() for o in resp.orders] if resp and resp.orders else []
        except Exception as exc:
            logger.error("Failed to fetch open orders: %s", exc)
            return []

    # ── Contract price & paper settlement ────────────────────────────────

    def get_contract_price(self, market_id: str) -> Optional[float]:
        """
        Fetch the current price for a Kalshi contract (0.0-1.0).
        Tries candlesticks (5-min), then most recent trade, falls back to 0.50.
        """
        import json as _json
        import time as _time

        event_ticker = market_id.rsplit("-T", 1)[0]
        now = int(_time.time())

        # 1) Try 5-min candlesticks over the last 2 hours
        try:
            start = now - 7200
            raw = self._client._markets_api.get_market_candlesticks_without_preload_content(
                ticker=event_ticker,
                market_ticker=market_id,
                start_ts=start,
                end_ts=now,
                period_interval="5m",
            )
            candles = _json.loads(raw.data).get("candlesticks", [])
            logger.info("Contract price candles for %s: %d candles", market_id, len(candles))
            for candle in reversed(candles):
                p = candle.get("price", {})
                price = p.get("close_dollars") or p.get("previous_dollars")
                if price is not None:
                    logger.debug("Contract price from candlestick: %s -> %.4f", market_id, float(price))
                    return float(price)
        except Exception as exc:
            logger.warning("Candlestick price fetch failed for %s: %s", market_id, exc)

        # 2) Fall back to most recent trade
        try:
            resp = self._client._markets_api.get_trades(ticker=market_id, limit=1)
            trades = resp.trades if resp and resp.trades else []
            if trades:
                t = trades[0]
                # Try yes_price first (cents), then price attr
                raw_price = getattr(t, "yes_price", None) or getattr(t, "price", None)
                if raw_price is not None:
                    price = round(int(raw_price) / 100, 4)
                    logger.info("Contract price from trade: %s -> %.4f", market_id, price)
                    return price
                logger.warning("Trade found for %s but no price attr: %s", market_id, vars(t) if hasattr(t, '__dict__') else t)
            else:
                logger.debug("No trades found for %s", market_id)
        except Exception as exc:
            logger.warning("Trade price fetch failed for %s: %s", market_id, exc)

        # 3) Try get_market for last traded price / bid-ask midpoint
        try:
            resp = self._client._markets_api.get_market(ticker=market_id)
            mkt = resp.market if resp else None
            if mkt:
                # Try last_price, then midpoint of bid/ask
                last = getattr(mkt, "last_price", None)
                if last is not None:
                    price = round(int(last) / 100, 4)
                    logger.info("Contract price from market last_price: %s -> %.4f", market_id, price)
                    return price
                bid = getattr(mkt, "yes_bid", None)
                ask = getattr(mkt, "yes_ask", None)
                if bid is not None and ask is not None:
                    price = round((int(bid) + int(ask)) / 200, 4)
                    logger.info("Contract price from market bid/ask: %s -> %.4f", market_id, price)
                    return price
                if bid is not None:
                    price = round(int(bid) / 100, 4)
                    logger.info("Contract price from market bid: %s -> %.4f", market_id, price)
                    return price
                logger.warning("Market found for %s but no price fields; attrs: %s",
                               market_id, [a for a in dir(mkt) if not a.startswith('_')])
        except Exception as exc:
            logger.warning("Market price fetch failed for %s: %s", market_id, exc)

        # 4) For spot-based markets (KXBTCD/KXETHD/KXSOLD): compute probability
        #    from current spot price vs contract strike using a sigmoid function.
        #    This gives meaningful non-0.50 prices when the API has no data.
        prefix_feed = {
            "KXBTCD": ("bot.spot_feed", "fetch_btc_spot", {"lookback": 1, "granularity": 60}),
            "KXETHD": ("bot.spot_feed", "fetch_eth_spot", {"lookback": 1, "granularity": 60}),
            "KXSOLD": ("bot.spot_feed", "fetch_gold_spot", {"lookback": 1}),
        }
        for prefix, (module, func, kwargs) in prefix_feed.items():
            if market_id.startswith(prefix):
                try:
                    import importlib, math
                    strike = float(market_id.rsplit("-T", 1)[1])
                    feed_fn = getattr(importlib.import_module(module), func)
                    df = feed_fn(**kwargs)
                    if not df.empty:
                        spot = df["price"].iloc[-1]
                        pct_diff = (spot - strike) / max(strike, 1e-9)
                        # Sigmoid with scale=50: 1% ITM → 0.62, 5% ITM → 0.92
                        prob = round(1 / (1 + math.exp(-pct_diff * 50)), 4)
                        prob = max(0.01, min(0.99, prob))
                        logger.info("Contract price from spot/strike: %s spot=%.2f strike=%.2f -> %.4f",
                                    market_id, spot, strike, prob)
                        return prob
                except Exception as exc:
                    logger.warning("Spot/strike price calc failed for %s: %s", market_id, exc)
                break

        logger.warning("All contract price sources failed for %s — using 0.50 fallback", market_id)
        return 0.50

    def settle_paper_positions(self) -> None:
        """
        Check open paper positions each scan cycle:
        - Close at take-profit (+25c) or stop-loss (-15c) if hit
        - Close at final settlement price when contract expires
        """
        if not self._paper_positions:
            return

        from dashboard.state import state as dashboard_state

        tp = self.rm.cfg.take_profit
        sl = self.rm.cfg.stop_loss
        settled = []

        for order_id, pos in self._paper_positions.items():
            market_id = pos["market_id"]
            try:
                current = self.get_contract_price(market_id)
                if current is None:
                    continue

                entry = pos["entry_price"]
                close_price = None
                close_reason = None

                if pos["side"] == Side.BUY:
                    if current >= entry + tp:
                        close_price = current
                        close_reason = f"take-profit (entry={entry:.2f} current={current:.2f})"
                    elif current <= entry - sl:
                        close_price = current
                        close_reason = f"stop-loss (entry={entry:.2f} current={current:.2f})"
                    elif current <= 0.01 or current >= 0.99:
                        close_price = current
                        close_reason = f"settlement at {current:.2f}"
                else:  # SELL
                    if current <= entry - tp:
                        close_price = current
                        close_reason = f"take-profit (entry={entry:.2f} current={current:.2f})"
                    elif current >= entry + sl:
                        close_price = current
                        close_reason = f"stop-loss (entry={entry:.2f} current={current:.2f})"
                    elif current <= 0.01 or current >= 0.99:
                        close_price = current
                        close_reason = f"settlement at {current:.2f}"

                if close_price is None:
                    continue

                if pos["side"] == Side.BUY:
                    pnl = (close_price - entry) * pos["size"]
                else:
                    pnl = (entry - close_price) * pos["size"]
                pnl = round(pnl, 4)

                logger.info("[PAPER CLOSE] %s | %s | entry=%.2f exit=%.2f size=%d pnl=$%.2f",
                            market_id, close_reason, entry, close_price, int(pos["size"]), pnl)

                dashboard_state.add_fill(
                    pos["side"].value, close_price, pos["size"],
                    pnl=pnl, strategy=pos["strategy"],
                )
                if self.db:
                    self.db.log_fill(
                        market_id, pos["strategy"], pos["side"].value,
                        close_price, pos["size"], pnl=pnl, order_id=order_id + "-close",
                    )
                self.rm.record_pnl(pnl)
                self.rm.record_fill(market_id, Side.SELL if pos["side"] == Side.BUY else Side.BUY,
                                    pos["size"], close_price)
                settled.append(order_id)

            except Exception as exc:
                logger.debug("Position check failed for %s: %s", market_id, exc)

        for order_id in settled:
            del self._paper_positions[order_id]

    # ── Price feed ───────────────────────────────────────────────────────

    def fetch_prices(self, market_id: str, lookback: int) -> pd.DataFrame:
        """
        Fetch OHLCV candles via the candlesticks endpoint.
        For KXBTCD markets, uses BTC/USD spot price from Coinbase instead —
        daily contracts have no intraday history of their own.
        Falls back to the trades endpoint if candlesticks fail.
        """
        if market_id.startswith("KXBTCD"):
            from bot.spot_feed import fetch_btc_spot
            return fetch_btc_spot(lookback=lookback, granularity=900)  # 15-min candles

        if market_id.startswith("KXETHD"):
            from bot.spot_feed import fetch_eth_spot
            return fetch_eth_spot(lookback=lookback, granularity=900)  # 15-min candles

        if market_id.startswith("KXSOLD"):
            from bot.spot_feed import fetch_gold_spot
            return fetch_gold_spot(lookback=lookback)

        try:
            # Derive event ticker: "KXFED-27APR-T4.25" -> "KXFED-27APR"
            event_ticker = market_id.rsplit("-T", 1)[0]

            # Request enough history to cover `lookback` 60-min bars
            now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            start_ts = now_ts - lookback * 3600

            raw = self._client._markets_api.get_market_candlesticks_without_preload_content(
                ticker=event_ticker,
                market_ticker=market_id,
                start_ts=start_ts,
                end_ts=now_ts,
                period_interval="1h",
            )
            candles = json.loads(raw.data).get("candlesticks", [])

            if candles:
                rows = []
                for c in candles:
                    price_info = c.get("price", {})
                    # Use close price if traded, otherwise carry previous close
                    close = price_info.get("close_dollars") or price_info.get("previous_dollars")
                    if close is None:
                        continue
                    bid_info = c.get("yes_bid", {})
                    ask_info = c.get("yes_ask", {})
                    rows.append({
                        "timestamp": datetime.datetime.fromtimestamp(
                            c["end_period_ts"], tz=datetime.timezone.utc
                        ),
                        "price": float(close),
                        "volume": float(c.get("volume_fp") or 0),
                        "bid": float(bid_info.get("close_dollars") or close),
                        "ask": float(ask_info.get("close_dollars") or close),
                    })

                if rows:
                    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
                    return df.tail(lookback).reset_index(drop=True)

        except Exception as exc:
            logger.warning("Candlestick fetch failed (%s), falling back to trades", exc)

        # Fallback: individual trades
        try:
            resp = self._client._markets_api.get_trades(ticker=market_id, limit=lookback)
            trades = resp.trades if resp and resp.trades else []
            if trades:
                rows = [{
                    "timestamp": t.created_time,
                    "price": _from_cents(t.yes_price),
                    "volume": float(t.count),
                    "bid": _from_cents(t.yes_price),
                    "ask": _from_cents(t.yes_price),
                } for t in trades]
                df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
                return df
        except Exception as exc:
            logger.error("Failed to fetch prices: %s", exc)

        return self._empty_df()

    # ── Client setup ─────────────────────────────────────────────────────

    def _build_client(self) -> KalshiClient:
        key_id = os.environ["KALSHI_API_KEY_ID"]
        key_path = os.environ["KALSHI_PRIVATE_KEY_PATH"]

        with open(key_path, "r") as f:
            private_key_pem = f.read()

        config = Configuration()
        config.host = "https://api.elections.kalshi.com/trade-api/v2"
        config.api_key_id = key_id
        config.private_key_pem = private_key_pem

        return KalshiClient(configuration=config)
