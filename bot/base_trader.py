"""
Abstract base class for exchange traders.
Both PolymarketTrader and KalshiTrader implement this interface,
allowing deploy/main.py to swap exchanges with a single flag.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import pandas as pd

from strategies.base_strategy import Signal, Side
from bot.risk_manager import RiskManager
from dashboard.state import state as dashboard_state

logger = logging.getLogger(__name__)


class BaseTrader(ABC):
    POLL_INTERVAL = 60  # seconds between price-feed polls

    def __init__(self, risk_manager: RiskManager, dry_run: bool = False, db=None):
        self.rm = risk_manager
        self.dry_run = dry_run
        self.db = db
        self._open_orders: Dict[str, str] = {}  # market_id -> order_id

    # ── Main loop ────────────────────────────────────────────────────────

    def run(self, strategy, market_id: str, lookback: int = 100) -> None:
        """Main loop: fetch prices → generate signal → risk check → place order."""
        logger.info("Starting trader | exchange=%s | market=%s | strategy=%s | dry_run=%s",
                    self.exchange_name, market_id, strategy.name, self.dry_run)

        dashboard_state.market_id = market_id
        dashboard_state.strategy_name = strategy.name
        dashboard_state.capital = self.rm.capital

        while True:
            try:
                self._poll_market(strategy, market_id, lookback)
            except KeyboardInterrupt:
                logger.info("Shutting down trader.")
                self.cancel_all()
                break
            except Exception as exc:
                logger.error("Error in trading loop: %s", exc, exc_info=True)
            time.sleep(self.POLL_INTERVAL)

    def run_scan(self, strategies, scanner, lookback: int = 100) -> None:
        """
        Scanner loop: auto-discover top markets and run all strategies against each.
        `strategies` can be a single strategy or a list.
        Prices are fetched once per market and evaluated by every strategy.
        """
        if not isinstance(strategies, list):
            strategies = [strategies]

        names = ", ".join(s.name for s in strategies)
        logger.info("Starting market scanner | exchange=%s | strategies=[%s] | dry_run=%s",
                    self.exchange_name, names, self.dry_run)

        dashboard_state.strategy_name = names
        dashboard_state.capital = self.rm.capital

        # Pre-load signal/fill history from DB so dashboard shows past activity
        if self.db:
            for sig in reversed(self.db.get_signals(limit=50)):
                dashboard_state.add_signal(
                    sig["strategy"], sig["side"], sig["price"],
                    sig["size"], sig["confidence"], sig["reason"],
                )
            for fill in reversed(self.db.get_fills(limit=100)):
                dashboard_state.add_fill(
                    fill["side"], fill["price"], fill["size"],
                    fill["pnl"], fill["strategy"],
                )
            logger.info("Loaded history from database.")

        while True:
            try:
                markets = scanner.get_markets()
                logger.info("Scanning %d markets with [%s]: %s", len(markets), names, markets)
                for market_id in markets:
                    dashboard_state.market_id = market_id
                    self._poll_market_multi(strategies, market_id, lookback)
                    time.sleep(1)
                # Check paper positions for settlement after each full scan
                if self.dry_run and hasattr(self, "settle_paper_positions"):
                    self.settle_paper_positions()
            except KeyboardInterrupt:
                logger.info("Shutting down scanner.")
                self.cancel_all()
                break
            except Exception as exc:
                logger.error("Error in scanner loop: %s", exc, exc_info=True)
            time.sleep(self.POLL_INTERVAL)

    def _poll_market(self, strategy, market_id: str, lookback: int) -> None:
        """Single-strategy poll: fetch prices and evaluate one strategy."""
        self._poll_market_multi([strategy], market_id, lookback)

    def _poll_market_multi(self, strategies: list, market_id: str, lookback: int) -> None:
        """
        Multi-strategy poll: fetch prices once, evaluate all strategies.
        Requires at least 2 strategies to agree on direction before placing an order.
        """
        prices = self.fetch_prices(market_id, lookback)

        if not prices.empty:
            push_rows = prices if len(dashboard_state.prices) == 0 else prices.tail(2)
            for _, row in push_rows.iterrows():
                dashboard_state.add_price(
                    row.get("timestamp", ""),
                    row["price"],
                    row.get("volume", 0),
                    row.get("bid"),
                    row.get("ask"),
                )

        # Collect all signals from every strategy
        all_signals = []
        for strategy in strategies:
            signal = strategy.generate_signal(prices)
            if signal:
                signal.strategy = strategy.name
                logger.info("[%s][%s] Signal: %s | %s",
                            market_id, strategy.name, signal.side.value, signal.reason)
                all_signals.append(signal)

        if not all_signals:
            return

        # Consensus rules:
        #   - CVD + any one other strategy agreeing on same side → trade
        #   - MACD + RSI agreeing (no CVD) → trade
        #   - CVD alone → blocked
        from collections import Counter
        side_counts = Counter(s.side for s in all_signals)
        best_side, best_count = side_counts.most_common(1)[0]
        agreeing_signals = [s for s in all_signals if s.side == best_side]
        agreeing_names = {s.strategy for s in agreeing_signals}

        has_cvd = "CVD" in agreeing_names
        confirming = agreeing_names - {"CVD"}

        # CVD alone is not enough; need at least one confirming strategy
        if has_cvd and not confirming:
            logger.info("[%s] CVD-only signal blocked — waiting for MACD, RSI, Bollinger or VWAP confirmation",
                        market_id)
            return

        # Without CVD, need at least 2 other strategies to agree
        if not has_cvd and len(agreeing_names) < 2:
            logger.info("[%s] No consensus — signals: %s",
                        market_id, list(agreeing_names))
            return

        strategy_names = "+".join(s.strategy for s in agreeing_signals)
        logger.info("[%s] Consensus %s from [%s]", market_id, best_side.value, strategy_names)

        # Build a combined signal: average confidence, get contract price once
        import statistics
        combined = agreeing_signals[0]
        combined.strategy = strategy_names
        combined.confidence = round(statistics.mean(s.confidence for s in agreeing_signals), 4)
        combined.reason = " | ".join(s.reason for s in agreeing_signals)

        contract_price = self.get_contract_price(market_id)
        if contract_price is not None:
            combined.price = contract_price

        # Don't place if price is the 0.50 fallback — means we have no real price data yet
        if combined.price == 0.50:
            logger.info("[%s] Skipping order — contract price unavailable (0.50 fallback)", market_id)
            return

        # Don't place if we already have an open position on this contract
        if market_id in self._open_orders and self._open_orders[market_id]:
            logger.info("[%s] Skipping order — already have an open position on this contract", market_id)
            return

        sized = self.rm.evaluate(combined, market_id)
        if sized:
            dashboard_state.add_signal(
                sized.strategy, sized.side.value,
                sized.price, sized.size,
                sized.confidence, sized.reason,
            )
            if self.db:
                self.db.log_signal(
                    market_id, sized.strategy, sized.side.value,
                    sized.price, sized.size, sized.confidence, sized.reason,
                )
            order_id = self.place_order(sized, market_id)
            if order_id:
                dashboard_state.set_open_order(
                    order_id, sized.side.value,
                    sized.price, sized.size, market_id,
                )
                if self.db:
                    self.db.log_order(
                        market_id, order_id, sized.side.value,
                        sized.price, sized.size,
                    )

    # ── Abstract interface ───────────────────────────────────────────────

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """Human-readable exchange name for logging."""

    @abstractmethod
    def place_order(self, signal: Signal, market_id: str) -> Optional[str]:
        """Place a limit order. Returns order_id or None on failure."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""

    @abstractmethod
    def get_open_orders(self, market_id: str) -> List[dict]:
        """Return list of open orders for a market."""

    @abstractmethod
    def fetch_prices(self, market_id: str, lookback: int) -> pd.DataFrame:
        """
        Return a DataFrame with columns: timestamp, price, volume, bid, ask.
        Most-recent row last.
        """

    # ── Shared helpers ───────────────────────────────────────────────────

    def cancel_all(self) -> None:
        for market_id, order_ids in list(self._open_orders.items()):
            ids = order_ids if isinstance(order_ids, list) else [order_ids]
            for order_id in ids:
                self.cancel_order(order_id)
            del self._open_orders[market_id]

    def _cancel_stale(self, market_id: str) -> None:
        if market_id in self._open_orders:
            order_ids = self._open_orders.pop(market_id)
            ids = order_ids if isinstance(order_ids, list) else [order_ids]
            for order_id in ids:
                self.cancel_order(order_id)

    def get_contract_price(self, market_id: str) -> Optional[float]:
        """Return current market price (0.0-1.0) for a contract. Override in subclasses."""
        return None

    def _empty_df(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "price", "volume", "bid", "ask"])
