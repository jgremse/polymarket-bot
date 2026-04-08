from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy, Signal, Side


class CVDStrategy(BaseStrategy):
    """
    Cumulative Volume Delta (CVD) strategy for Polymarket binary markets.

    CVD measures net buying/selling pressure:
        - Trades at ask price  → buy volume  (+)
        - Trades at bid price  → sell volume (-)
        CVD = cumulative sum of (buy_vol - sell_vol)

    Expected DataFrame columns:
        price     : float  — trade price
        volume    : float  — trade size
        bid       : float  — best bid at time of trade
        ask       : float  — best ask at time of trade

    Signal logic:
        Buy  : CVD diverges positively (rising CVD while price flat/dipping)
        Sell : CVD diverges negatively (falling CVD while price flat/rising)
    """

    def __init__(
        self,
        lookback: int = 20,
        cvd_threshold: float = 50.0,
        divergence_threshold: float = 0.02,  # 2% relative move allowed while CVD diverges
        price_offset: float = 0.01,
    ):
        super().__init__("CVD")
        self.lookback = lookback
        self.cvd_threshold = cvd_threshold          # min absolute CVD move
        self.divergence_threshold = divergence_threshold  # price must not confirm
        self.price_offset = price_offset

    def _compute_cvd(self, df: pd.DataFrame) -> pd.Series:
        mid = (df["bid"] + df["ask"]) / 2
        delta = df["volume"].where(df["price"] >= mid, -df["volume"])
        return delta.cumsum()

    def generate_signal(self, prices: pd.DataFrame) -> Optional[Signal]:
        required_cols = {"price", "volume", "bid", "ask"}
        if not required_cols.issubset(prices.columns):
            return None
        if len(prices) < self.lookback:
            return None

        window = prices.iloc[-self.lookback:]
        cvd = self._compute_cvd(window)

        cvd_change = cvd.iloc[-1] - cvd.iloc[0]
        price_change = window["price"].iloc[-1] - window["price"].iloc[0]
        last_price = window["price"].iloc[-1]

        # Normalise price change to a relative value so the threshold works for both
        # 0-1 probability markets and raw spot prices (BTC/ETH in dollars)
        relative_price_change = price_change / max(abs(last_price), 1e-9)

        # Bullish divergence: CVD rising while price flat or lower
        if cvd_change > self.cvd_threshold and relative_price_change < self.divergence_threshold:
            limit_price = round(min(last_price + self.price_offset, 0.99), 4)
            confidence = min(cvd_change / (self.cvd_threshold * 3), 1.0)
            return Signal(
                side=Side.BUY,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"CVD bullish divergence | cvd_chg={cvd_change:.1f} price_chg={price_change:.4f}",
            )

        # Bearish divergence: CVD falling while price flat or higher
        if cvd_change < -self.cvd_threshold and relative_price_change > -self.divergence_threshold:
            limit_price = round(max(last_price - self.price_offset, 0.01), 4)
            confidence = min(abs(cvd_change) / (self.cvd_threshold * 3), 1.0)
            return Signal(
                side=Side.SELL,
                price=limit_price,
                size=0,
                confidence=confidence,
                reason=f"CVD bearish divergence | cvd_chg={cvd_change:.1f} price_chg={price_change:.4f}",
            )

        return None
