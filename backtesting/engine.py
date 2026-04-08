"""
Backtest engine — simulates limit-order execution on historical trade data.

Execution model
---------------
A limit BUY  at price P fills if a subsequent candle's low  <= P.
A limit SELL at price P fills if a subsequent candle's high >= P.
Unfilled orders expire after `order_ttl_bars` bars.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, Side
from backtesting.metrics import BacktestMetrics


@dataclass
class Fill:
    bar_index: int
    side: Side
    price: float
    size: float
    pnl: float = 0.0


@dataclass
class BacktestResult:
    fills: List[Fill]
    equity_curve: pd.Series
    metrics: dict


@dataclass
class _PendingOrder:
    signal: Signal
    placed_at: int
    ttl: int
    size: float


class BacktestEngine:
    """
    Parameters
    ----------
    strategy       : BaseStrategy instance to test.
    initial_capital: Starting portfolio value in USD.
    fee_rate       : Per-side taker fee (default 0.2 %).
    order_ttl_bars : Bars before an unfilled limit order is cancelled.
    max_position   : Maximum fraction of capital in one position (0–1).

    Expected DataFrame columns
    --------------------------
    timestamp, price, volume, bid, ask  (bid/ask optional for non-CVD strategies)
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 1_000.0,
        fee_rate: float = 0.002,
        order_ttl_bars: int = 5,
        max_position: float = 0.20,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.order_ttl_bars = order_ttl_bars
        self.max_position = max_position

    def run(self, data: pd.DataFrame) -> BacktestResult:
        data = data.reset_index(drop=True)
        capital = self.initial_capital
        position = 0.0          # shares held (positive = long)
        avg_entry = 0.0
        fills: List[Fill] = []
        equity: List[float] = []
        pending: Optional[_PendingOrder] = None

        for i in range(len(data)):
            row = data.iloc[i]
            current_price = row["price"]

            # ── Try to fill pending limit order ──────────────────────────
            if pending is not None:
                filled = False
                if pending.signal.side == Side.BUY and current_price <= pending.signal.price:
                    cost = pending.size * pending.signal.price
                    fee = cost * self.fee_rate
                    if capital >= cost + fee:
                        capital -= cost + fee
                        position += pending.size
                        avg_entry = pending.signal.price
                        fills.append(Fill(i, Side.BUY, pending.signal.price, pending.size))
                        filled = True

                elif pending.signal.side == Side.SELL and current_price >= pending.signal.price:
                    if position > 0:
                        proceeds = pending.size * pending.signal.price
                        fee = proceeds * self.fee_rate
                        pnl = (pending.signal.price - avg_entry) * pending.size - fee
                        capital += proceeds - fee
                        position = max(position - pending.size, 0)
                        fills.append(Fill(i, Side.SELL, pending.signal.price, pending.size, pnl))
                        filled = True

                if filled or (i - pending.placed_at) >= pending.ttl:
                    pending = None

            # ── Generate signal from strategy ─────────────────────────────
            if pending is None and i >= 1:
                window = data.iloc[: i + 1]
                signal = self.strategy.generate_signal(window)

                if signal is not None:
                    max_spend = capital * self.max_position * signal.confidence
                    size = round(max_spend / signal.price, 2) if signal.price > 0 else 0

                    if signal.side == Side.SELL:
                        size = min(size, position)

                    if size > 0:
                        signal.size = size
                        pending = _PendingOrder(signal, i, self.order_ttl_bars, size)

            # ── Mark-to-market equity ─────────────────────────────────────
            equity.append(capital + position * current_price)

        equity_series = pd.Series(equity, index=data["timestamp"] if "timestamp" in data.columns else None)
        metrics = BacktestMetrics.compute(equity_series, fills)
        return BacktestResult(fills=fills, equity_curve=equity_series, metrics=metrics)
