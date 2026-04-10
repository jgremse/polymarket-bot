"""
Risk manager — enforces position limits and sizes orders before they reach the trader.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from strategies.base_strategy import Signal, Side

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_position_pct: float = 0.10    # max % of capital per market
    max_total_exposure_pct: float = 0.50  # max % of capital across all open positions
    max_daily_loss_pct: float = 0.05  # halt trading if daily loss exceeds this
    min_order_size: float = 1.0       # minimum order in shares
    max_order_size: float = 100.0     # hard cap per order
    confidence_scale: bool = True     # scale size by signal confidence
    take_profit: float = 0.25         # close position when price moves +25c above entry
    stop_loss: float = 0.15           # close position when price moves -15c below entry
    max_open_positions: int = 5       # hard cap on simultaneous open positions


class RiskManager:
    """
    Usage
    -----
    rm = RiskManager(capital=1000.0, config=RiskConfig())
    sized_signal = rm.evaluate(signal, market_id="0xabc...")
    if sized_signal:
        trader.place_order(sized_signal, market_id)
    """

    def __init__(self, capital: float, config: Optional[RiskConfig] = None):
        self.capital = capital
        self.cfg = config or RiskConfig()
        self._positions: Dict[str, float] = {}   # market_id -> dollar value held (shares × price)
        self._daily_pnl: float = 0.0

    # ── Public API ───────────────────────────────────────────────────────

    def evaluate(self, signal: Signal, market_id: str) -> Optional[Signal]:
        """Apply risk rules and return a sized signal, or None if blocked."""

        if self._is_daily_loss_breached():
            logger.warning("Daily loss limit breached — no new orders.")
            return None

        if len(self._positions) >= self.cfg.max_open_positions:
            logger.warning("Max open positions (%d) reached — order rejected.", self.cfg.max_open_positions)
            return None

        if self._is_exposure_limit_breached(signal, market_id):
            logger.warning("Exposure limit would be breached — order rejected.")
            return None

        size = self._compute_size(signal, market_id)
        if size < self.cfg.min_order_size:
            logger.debug("Computed size %.2f below minimum — skipped.", size)
            return None

        signal.size = size
        return signal

    def record_fill(self, market_id: str, side: Side, size: float, price: float) -> None:
        """Call after a confirmed fill to update internal state."""
        current = self._positions.get(market_id, 0.0)
        dollar_value = size * price
        if side == Side.BUY:
            self._positions[market_id] = current + dollar_value
        else:
            # Bug 4 fix: remove key entirely when position is closed so max_open_positions
            # cap doesn't count positions with zero exposure
            new_val = max(current - dollar_value, 0.0)
            if new_val == 0.0:
                self._positions.pop(market_id, None)
            else:
                self._positions[market_id] = new_val

    def record_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0

    def update_capital(self, capital: float) -> None:
        self.capital = capital

    # ── Internal helpers ─────────────────────────────────────────────────

    def _compute_size(self, signal: Signal, market_id: str) -> float:
        alloc = self.capital * self.cfg.max_position_pct
        if self.cfg.confidence_scale:
            alloc *= signal.confidence
        size = alloc / signal.price if signal.price > 0 else 0.0

        if signal.side == Side.SELL:
            # For sells, cap at shares held (stored as dollar value, convert back)
            held_dollars = self._positions.get(market_id, 0.0)
            held_shares = held_dollars / signal.price if signal.price > 0 else 0.0
            size = min(size, held_shares)

        return round(min(size, self.cfg.max_order_size), 2)

    def _total_exposure(self, exclude_market: Optional[str] = None) -> float:
        return sum(
            v for k, v in self._positions.items()
            if k != exclude_market and v > 0
        )

    def _is_exposure_limit_breached(self, signal: Signal, market_id: str) -> bool:
        if signal.side == Side.SELL:
            return False
        current_exposure = self._total_exposure(exclude_market=market_id)
        # Bug 6 fix: include the proposed order's dollar cost in the exposure check
        proposed_alloc = self.capital * self.cfg.max_position_pct
        if self.cfg.confidence_scale:
            proposed_alloc *= signal.confidence
        max_allowed = self.capital * self.cfg.max_total_exposure_pct
        return current_exposure + proposed_alloc > max_allowed

    def _is_daily_loss_breached(self) -> bool:
        return self._daily_pnl < -(self.capital * self.cfg.max_daily_loss_pct)
