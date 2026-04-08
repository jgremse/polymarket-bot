from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy, Signal, Side


class RSIStrategy(BaseStrategy):
    """
    RSI mean-reversion strategy for Polymarket binary markets.

    Buy signal  : RSI crosses back above `oversold` threshold (dip recovery).
    Sell signal : RSI crosses back below `overbought` threshold (fade the rally).

    Limit price is set at the last trade price offset by `price_offset` to
    stay passive in the order book.
    """

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        price_offset: float = 0.01,
    ):
        super().__init__("RSI")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.price_offset = price_offset

    def _compute_rsi(self, prices: pd.Series) -> pd.Series:
        delta = prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=self.period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("inf"))
        return 100 - (100 / (1 + rs))

    def generate_signal(self, prices: pd.DataFrame) -> Optional[Signal]:
        if len(prices) < self.period + 1:
            return None

        rsi = self._compute_rsi(prices["price"])
        prev_rsi = rsi.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        last_price = prices["price"].iloc[-1]

        # Recovery from oversold
        if prev_rsi < self.oversold and curr_rsi >= self.oversold:
            limit_price = round(min(last_price + self.price_offset, 0.99), 4)
            confidence = (self.oversold - prev_rsi) / self.oversold
            return Signal(
                side=Side.BUY,
                price=limit_price,
                size=0,
                confidence=min(confidence, 1.0),
                reason=f"RSI oversold recovery | rsi={curr_rsi:.1f}",
            )

        # Rejection from overbought
        if prev_rsi > self.overbought and curr_rsi <= self.overbought:
            limit_price = round(max(last_price - self.price_offset, 0.01), 4)
            confidence = (prev_rsi - self.overbought) / (100 - self.overbought)
            return Signal(
                side=Side.SELL,
                price=limit_price,
                size=0,
                confidence=min(confidence, 1.0),
                reason=f"RSI overbought rejection | rsi={curr_rsi:.1f}",
            )

        return None
