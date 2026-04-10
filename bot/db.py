"""
SQLite persistence layer.
Logs signals, fills, and orders to trading.db so history survives restarts.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "trading.db"


class TradingDB:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup()
        logger.info("Database ready at %s", path)

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _setup(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market_id TEXT NOT NULL,
                strategy  TEXT NOT NULL,
                side      TEXT NOT NULL,
                price     REAL NOT NULL,
                size      REAL NOT NULL,
                confidence REAL NOT NULL,
                reason    TEXT
            );

            CREATE TABLE IF NOT EXISTS fills (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market_id TEXT NOT NULL,
                strategy  TEXT NOT NULL,
                side      TEXT NOT NULL,
                price     REAL NOT NULL,
                size      REAL NOT NULL,
                pnl       REAL NOT NULL DEFAULT 0,
                order_id  TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market_id TEXT NOT NULL,
                order_id  TEXT NOT NULL,
                side      TEXT NOT NULL,
                price     REAL NOT NULL,
                size      REAL NOT NULL,
                status    TEXT NOT NULL DEFAULT 'open'
            );
        """)
        self._conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def log_signal(self, market_id: str, strategy: str, side: str,
                   price: float, size: float, confidence: float, reason: str):
        self._conn.execute(
            """INSERT INTO signals
               (timestamp, market_id, strategy, side, price, size, confidence, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (self._now(), market_id, strategy, side,
             price, size, confidence, reason),
        )
        self._conn.commit()

    def log_fill(self, market_id: str, strategy: str, side: str,
                 price: float, size: float, pnl: float, order_id: str = ""):
        self._conn.execute(
            """INSERT INTO fills
               (timestamp, market_id, strategy, side, price, size, pnl, order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (self._now(), market_id, strategy, side,
             price, size, pnl, order_id),
        )
        self._conn.commit()

    def log_order(self, market_id: str, order_id: str, side: str,
                  price: float, size: float):
        self._conn.execute(
            """INSERT INTO orders
               (timestamp, market_id, order_id, side, price, size)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (self._now(), market_id, order_id, side, price, size),
        )
        self._conn.commit()

    def close_order(self, order_id: str, status: str = "cancelled"):
        self._conn.execute(
            "UPDATE orders SET status = ? WHERE order_id = ?",
            (status, order_id),
        )
        self._conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_signals(self, limit: int = 200) -> list:
        rows = self._conn.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fills(self, limit: int = 500) -> list:
        rows = self._conn.execute(
            "SELECT * FROM fills ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_open_orders(self) -> list:
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE status = 'open' ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_performance_summary(self) -> dict:
        fills = self.get_fills()
        closed = [f for f in fills if f["pnl"] != 0
                  or str(f.get("order_id", "")).endswith("-close")]
        if not closed:
            return {"total_trades": 0, "total_pnl": 0.0, "win_rate": 0.0}
        wins = [f for f in closed if f["pnl"] > 0]
        total_pnl = round(sum(f["pnl"] for f in closed), 4)
        win_rate = round(len(wins) / len(closed) * 100, 1)
        return {
            "total_trades": len(closed),
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "wins": len(wins),
            "losses": len(closed) - len(wins),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def close(self):
        self._conn.close()
