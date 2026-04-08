from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Signal:
    side: Side
    price: float        # limit price (0.0 – 1.0 on Polymarket)
    size: float         # number of shares
    confidence: float   # 0.0 – 1.0, used by risk manager for sizing
    reason: str = ""
    strategy: str = ""  # set by base_trader before order placement


class BaseStrategy(ABC):
    """
    All strategies must inherit from this class and implement `generate_signal`.

    Data contract
    -------------
    `prices` DataFrame columns expected (at minimum):
        timestamp : datetime64[ns]
        price     : float  — last trade price
        volume    : float  — trade volume
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signal(self, prices: pd.DataFrame) -> Optional[Signal]:
        """
        Analyse the latest price data and return a Signal or None.

        Parameters
        ----------
        prices : pd.DataFrame
            Historical OHLCV-style data, most-recent row last.

        Returns
        -------
        Signal | None
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
