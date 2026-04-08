"""
Shared in-memory state — written to by the bot, read by the dashboard.
Thread-safe via a single lock.
"""

import threading
from collections import deque
from datetime import datetime


class DashboardState:
    MAX_PRICES = 500
    MAX_SIGNALS = 200
    MAX_FILLS = 500

    def __init__(self):
        self._lock = threading.Lock()
        self.market_id = ""
        self.strategy_name = ""
        self.capital = 1000.0
        self.prices = deque(maxlen=self.MAX_PRICES)
        self.signals = deque(maxlen=self.MAX_SIGNALS)
        self.fills = deque(maxlen=self.MAX_FILLS)
        self.open_orders = {}
        self.equity = deque(maxlen=self.MAX_PRICES)

    def add_price(self, timestamp, price, volume, bid=None, ask=None):
        with self._lock:
            self.prices.append({
                "timestamp": str(timestamp),
                "price": float(price),
                "volume": float(volume),
                "bid": float(bid) if bid else float(price),
                "ask": float(ask) if ask else float(price),
            })
            # Update equity mark-to-market (simplified)
            self.equity.append({
                "timestamp": str(timestamp),
                "value": round(self.capital, 4),
            })

    def add_signal(self, strategy, side, price, size, confidence, reason):
        with self._lock:
            self.signals.appendleft({
                "timestamp": str(datetime.now().strftime("%H:%M:%S")),
                "strategy": strategy,
                "side": side,
                "price": price,
                "size": size,
                "confidence": round(confidence * 100, 1),
                "reason": reason,
            })

    def add_fill(self, side, price, size, pnl=0.0, strategy=""):
        with self._lock:
            self.fills.appendleft({
                "timestamp": str(datetime.now().strftime("%H:%M:%S")),
                "side": side,
                "price": price,
                "size": size,
                "pnl": round(pnl, 4),
                "strategy": strategy,
            })
            self.capital += pnl

    def set_open_order(self, order_id, side, price, size, market_id):
        with self._lock:
            self.open_orders[order_id] = {
                "order_id": order_id[:8] + "...",
                "side": side,
                "price": price,
                "size": size,
                "market_id": market_id,
                "timestamp": str(datetime.now().strftime("%H:%M:%S")),
            }

    def remove_order(self, order_id):
        with self._lock:
            self.open_orders.pop(order_id, None)

    def snapshot(self):
        with self._lock:
            return {
                "market_id": self.market_id,
                "strategy_name": self.strategy_name,
                "capital": self.capital,
                "prices": list(self.prices),
                "signals": list(self.signals),
                "fills": list(self.fills),
                "open_orders": list(self.open_orders.values()),
                "equity": list(self.equity),
            }


# Global singleton — imported by both bot and dashboard
state = DashboardState()
