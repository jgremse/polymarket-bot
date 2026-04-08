from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy, Signal, Side


class VWAPStrategy(BaseStrategy):
    """
    VWAP (Volume Weighted Average Price) deviation strategy.

    VWAP represents the average price weighted by volume — where most money
    actually traded. When price is significantly below VWAP and buy volume
    is picking up (positive delta), it's likely to mean-revert upward.

    Buy signal  : price is `threshold` % below VWAP and last bar's volume
                  delta is positive (more buys than sells).
    Sell signal : price is `threshold` % above VWAP and last bar's volume
                  delta is negative.

    Expected DataFrame columns: price, volume, bid, ask
    """

    def __init__(
        self,
        threshold: float = 0.005,   # 0.5% deviation from VWAP to signal
        lookback: int = 20,          # bars used to compute rolling VWAP
        price_offset: float = 0.01,
    ):
        super().__init__("VWAP")
        self.threshold = threshold
        self.lookback = lookback
        self.price_offset = price_offset

    def _compute_vwap(self, prices: pd.DataFrame) -> pd.Series:
        pv = prices["price"] * prices["volume"]
        return pv.rolling(self.lookback).sum() / prices["volume"].rolling(self.lookback).sum()

    def _volume_delta(self, prices: pd.DataFrame) -> float:
        """Positive = more buy volume, negative = more sell volume on last bar."""
        row = prices.iloc[-1]
        mid = (row.get("bid", row["price"]) + row.get("ask", row["price"])) / 2
        return row["volume"] if row["price"] >= mid else -row["volume"]

    def generate_signal(self, prices: pd.DataFrame) -> Optional[Signal]:
        if len(prices) < self.lookback + 1:
            return None

        vwap = self._compute_vwap(prices)
        curr_vwap = vwap.iloc[-1]
        if pd.isna(curr_vwap) or curr_vwap == 0:
            return None

        curr_price = prices["price"].iloc[-1]
        deviation = (curr_price - curr_vwap) / curr_vwap
        delta = self._volume_delta(prices)

        # Price below VWAP + buying pressure → mean reversion BUY
        if deviation < -self.threshold and delta > 0:
            limit_price = round(min(curr_price + self.price_offset, 0.99), 4)
            confidence = min(abs(deviation) / (self.threshold * 3), 1.0)
            return Signal(
                side=Side.BUY,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"VWAP deviation BUY | price={curr_price:.2f} vwap={curr_vwap:.2f} dev={deviation:.3f}",
            )

        # Price above VWAP + selling pressure → mean reversion SELL
        if deviation > self.threshold and delta < 0:
            limit_price = round(max(curr_price - self.price_offset, 0.01), 4)
            confidence = min(abs(deviation) / (self.threshold * 3), 1.0)
            return Signal(
                side=Side.SELL,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"VWAP deviation SELL | price={curr_price:.2f} vwap={curr_vwap:.2f} dev={deviation:.3f}",
            )

        return None
