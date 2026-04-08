from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy, Signal, Side


class MACDStrategy(BaseStrategy):
    """
    MACD crossover strategy adapted for Polymarket binary markets.

    Buy signal  : MACD line crosses above the signal line (bullish momentum).
    Sell signal : MACD line crosses below the signal line (bearish momentum).

    Because Polymarket prices are bounded [0, 1], the limit price is clamped
    to that range with a configurable offset to improve fill probability.
    """

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        price_offset: float = 0.01,
        min_histogram: float = 0.001,
    ):
        super().__init__("MACD")
        self.fast = fast
        self.slow = slow
        self.signal_period = signal
        self.price_offset = price_offset      # tighten/widen limit vs mid
        self.min_histogram = min_histogram    # ignore weak crossovers

    def _compute_macd(self, prices: pd.Series) -> pd.DataFrame:
        ema_fast = prices.ewm(span=self.fast, adjust=False).mean()
        ema_slow = prices.ewm(span=self.slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": histogram})

    def generate_signal(self, prices: pd.DataFrame) -> Optional[Signal]:
        if len(prices) < self.slow + self.signal_period:
            return None

        macd_df = self._compute_macd(prices["price"])
        prev = macd_df.iloc[-2]
        curr = macd_df.iloc[-1]
        last_price = prices["price"].iloc[-1]

        # Bullish crossover
        if prev["macd"] < prev["signal"] and curr["macd"] > curr["signal"]:
            if curr["hist"] < self.min_histogram:
                return None
            limit_price = round(min(last_price + self.price_offset, 0.99), 4)
            confidence = min(curr["hist"] / 0.05, 1.0)
            return Signal(
                side=Side.BUY,
                price=limit_price,
                size=0,  # sized by RiskManager
                confidence=confidence,
                reason=f"MACD bullish crossover | hist={curr['hist']:.4f}",
            )

        # Bearish crossover
        if prev["macd"] > prev["signal"] and curr["macd"] < curr["signal"]:
            if abs(curr["hist"]) < self.min_histogram:
                return None
            limit_price = round(max(last_price - self.price_offset, 0.01), 4)
            confidence = min(abs(curr["hist"]) / 0.05, 1.0)
            return Signal(
                side=Side.SELL,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"MACD bearish crossover | hist={curr['hist']:.4f}",
            )

        return None
