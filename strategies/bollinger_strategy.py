from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy, Signal, Side


class BollingerStrategy(BaseStrategy):
    """
    Bollinger Bands mean-reversion strategy.

    Plots a moving average with upper/lower bands at `num_std` standard
    deviations. Signals when price touches a band and then reverses back
    inside — confirming the extreme was rejected rather than just touched.

    Buy signal  : price was below lower band last bar, back inside this bar.
    Sell signal : price was above upper band last bar, back inside this bar.

    Expected DataFrame columns: price, volume
    """

    def __init__(
        self,
        period: int = 20,
        num_std: float = 2.0,
        price_offset: float = 0.01,
    ):
        super().__init__("Bollinger")
        self.period = period
        self.num_std = num_std
        self.price_offset = price_offset

    def _compute_bands(self, prices: pd.Series):
        ma = prices.rolling(self.period).mean()
        std = prices.rolling(self.period).std()
        return ma, ma + self.num_std * std, ma - self.num_std * std

    def generate_signal(self, prices: pd.DataFrame) -> Optional[Signal]:
        if len(prices) < self.period + 1:
            return None

        close = prices["price"]
        _, upper, lower = self._compute_bands(close)

        prev_price = close.iloc[-2]
        curr_price = close.iloc[-1]
        prev_lower = lower.iloc[-2]
        curr_lower = lower.iloc[-1]
        prev_upper = upper.iloc[-2]
        curr_upper = upper.iloc[-1]

        # Recovery: was below lower band, now back inside
        if prev_price < prev_lower and curr_price >= curr_lower:
            limit_price = round(min(curr_price + self.price_offset, 0.99), 4)
            # Confidence based on how far below the band the price was
            deviation = (prev_lower - prev_price) / max(prev_lower, 1e-9)
            confidence = min(deviation * 10, 1.0)
            return Signal(
                side=Side.BUY,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"Bollinger lower band recovery | price={curr_price:.2f} lower={curr_lower:.2f}",
            )

        # Rejection: was above upper band, now back inside
        if prev_price > prev_upper and curr_price <= curr_upper:
            limit_price = round(max(curr_price - self.price_offset, 0.01), 4)
            deviation = (prev_price - prev_upper) / max(prev_upper, 1e-9)
            confidence = min(deviation * 10, 1.0)
            return Signal(
                side=Side.SELL,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"Bollinger upper band rejection | price={curr_price:.2f} upper={curr_upper:.2f}",
            )

        return None
