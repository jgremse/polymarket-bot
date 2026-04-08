"""
Trader — wraps py-clob-client and handles all order lifecycle operations.
Only limit orders are used (no market orders).
"""

import logging
import os
import time
from typing import Dict, List, Optional

import pandas as pd
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    OrderArgs,
    OrderType,
)
from py_clob_client.constants import POLYGON

from strategies.base_strategy import Signal, Side
from bot.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class Trader:
    """
    Parameters
    ----------
    risk_manager : RiskManager instance — must approve every signal before placement.
    dry_run      : If True, log orders but never send them to the exchange.

    Environment variables required (see .env.example)
    --------------------------------------------------
    POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE
    POLY_PRIVATE_KEY, POLY_FUNDER_ADDRESS
    """

    POLL_INTERVAL = 60  # seconds between price-feed polls

    def __init__(self, risk_manager: RiskManager, dry_run: bool = False):
        self.rm = risk_manager
        self.dry_run = dry_run
        self._client = self._build_client()
        self._open_orders: Dict[str, str] = {}   # market_id -> order_id

    # ── Lifecycle ────────────────────────────────────────────────────────

    def run(self, strategy, market_id: str, lookback: int = 100) -> None:
        """Main loop: fetch prices → generate signal → risk check → place order."""
        logger.info("Starting trader | market=%s | strategy=%s | dry_run=%s",
                    market_id, strategy.name, self.dry_run)

        while True:
            try:
                prices = self._fetch_prices(market_id, lookback)
                signal = strategy.generate_signal(prices)

                if signal:
                    logger.info("Signal: %s @ %.4f | %s", signal.side, signal.price, signal.reason)
                    sized = self.rm.evaluate(signal, market_id)
                    if sized:
                        self._cancel_stale(market_id)
                        self.place_order(sized, market_id)

            except KeyboardInterrupt:
                logger.info("Shutting down trader.")
                break
            except Exception as exc:
                logger.error("Error in trading loop: %s", exc, exc_info=True)

            time.sleep(self.POLL_INTERVAL)

    # ── Order management ─────────────────────────────────────────────────

    def place_order(self, signal: Signal, market_id: str) -> Optional[str]:
        """Place a limit order. Returns order_id or None on failure."""
        order_args = OrderArgs(
            token_id=market_id,
            price=signal.price,
            size=signal.size,
            side=signal.side.value,
        )

        if self.dry_run:
            logger.info("[DRY RUN] Would place %s limit @ %.4f x %.2f on %s",
                        signal.side, signal.price, signal.size, market_id)
            return "dry-run-order-id"

        try:
            resp = self._client.create_and_post_order(order_args, OrderType.GTC)
            order_id = resp.get("orderID")
            if order_id:
                self._open_orders[market_id] = order_id
                logger.info("Order placed | id=%s | %s %.2f @ %.4f",
                            order_id, signal.side, signal.size, signal.price)
            return order_id
        except Exception as exc:
            logger.error("Failed to place order: %s", exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        if self.dry_run:
            logger.info("[DRY RUN] Would cancel order %s", order_id)
            return True
        try:
            self._client.cancel(order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    def cancel_all(self) -> None:
        for market_id, order_id in list(self._open_orders.items()):
            if self.cancel_order(order_id):
                del self._open_orders[market_id]

    def get_open_orders(self, market_id: str) -> List[dict]:
        try:
            return self._client.get_orders(market_id) or []
        except Exception as exc:
            logger.error("Failed to fetch open orders: %s", exc)
            return []

    # ── Price feed ───────────────────────────────────────────────────────

    def _fetch_prices(self, market_id: str, lookback: int) -> pd.DataFrame:
        """
        Fetch recent trades from the CLOB and return a price DataFrame.
        Falls back to the orderbook midpoint if trade history is sparse.
        """
        try:
            trades = self._client.get_trades({"market": market_id}) or []
            if trades:
                df = pd.DataFrame(trades)
                df = df.rename(columns={"price": "price", "size": "volume",
                                        "timestamp": "timestamp"})
                df["price"] = df["price"].astype(float)
                df["volume"] = df["volume"].astype(float)
                df = df.sort_values("timestamp").tail(lookback).reset_index(drop=True)

                # Attach best bid/ask for CVD strategy
                book = self._client.get_order_book(market_id)
                if book:
                    best_bid = float(book.bids[0].price) if book.bids else df["price"].iloc[-1]
                    best_ask = float(book.asks[0].price) if book.asks else df["price"].iloc[-1]
                    df["bid"] = best_bid
                    df["ask"] = best_ask

                return df

        except Exception as exc:
            logger.error("Failed to fetch prices: %s", exc)

        return pd.DataFrame(columns=["timestamp", "price", "volume", "bid", "ask"])

    # ── Helpers ──────────────────────────────────────────────────────────

    def _cancel_stale(self, market_id: str) -> None:
        if market_id in self._open_orders:
            self.cancel_order(self._open_orders.pop(market_id))

    def _build_client(self) -> ClobClient:
        creds = ApiCreds(
            api_key=os.environ["POLY_API_KEY"],
            api_secret=os.environ["POLY_API_SECRET"],
            api_passphrase=os.environ["POLY_API_PASSPHRASE"],
        )
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=os.environ["POLY_PRIVATE_KEY"],
            creds=creds,
            signature_type=2,
            funder=os.environ["POLY_FUNDER_ADDRESS"],
        )
        return client
