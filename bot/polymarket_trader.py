"""
Polymarket trader — wraps py-clob-client.
Limit orders only. Prices are 0.0 – 1.0 (probability).
"""

import logging
import os
from typing import List, Optional

import pandas as pd
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.constants import POLYGON

from strategies.base_strategy import Signal, Side
from bot.base_trader import BaseTrader
from bot.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class PolymarketTrader(BaseTrader):
    """
    Environment variables required
    -------------------------------
    POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE
    POLY_PRIVATE_KEY, POLY_FUNDER_ADDRESS
    """

    @property
    def exchange_name(self) -> str:
        return "Polymarket"

    def __init__(self, risk_manager: RiskManager, dry_run: bool = False):
        super().__init__(risk_manager, dry_run)
        self._client = self._build_client()

    # ── Order management ─────────────────────────────────────────────────

    def place_order(self, signal: Signal, market_id: str) -> Optional[str]:
        if self.dry_run:
            logger.info("[DRY RUN] Would place %s limit @ %.4f x %.2f on %s",
                        signal.side, signal.price, signal.size, market_id)
            return "dry-run-order-id"

        try:
            order_args = OrderArgs(
                token_id=market_id,
                price=signal.price,
                size=signal.size,
                side=signal.side.value,
            )
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

    def get_open_orders(self, market_id: str) -> List[dict]:
        try:
            return self._client.get_orders(market_id) or []
        except Exception as exc:
            logger.error("Failed to fetch open orders: %s", exc)
            return []

    # ── Price feed ───────────────────────────────────────────────────────

    def fetch_prices(self, market_id: str, lookback: int) -> pd.DataFrame:
        try:
            trades = self._client.get_trades({"market": market_id}) or []
            if trades:
                df = pd.DataFrame(trades)
                df = df.rename(columns={"size": "volume"})
                df["price"] = df["price"].astype(float)
                df["volume"] = df["volume"].astype(float)
                df = df.sort_values("timestamp").tail(lookback).reset_index(drop=True)

                book = self._client.get_order_book(market_id)
                if book:
                    df["bid"] = float(book.bids[0].price) if book.bids else df["price"].iloc[-1]
                    df["ask"] = float(book.asks[0].price) if book.asks else df["price"].iloc[-1]

                return df
        except Exception as exc:
            logger.error("Failed to fetch prices: %s", exc)

        return self._empty_df()

    # ── Client setup ─────────────────────────────────────────────────────

    def _build_client(self) -> ClobClient:
        creds = ApiCreds(
            api_key=os.environ["POLY_API_KEY"],
            api_secret=os.environ["POLY_API_SECRET"],
            api_passphrase=os.environ["POLY_API_PASSPHRASE"],
        )
        return ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=os.environ["POLY_PRIVATE_KEY"],
            creds=creds,
            signature_type=2,
            funder=os.environ["POLY_FUNDER_ADDRESS"],
        )
