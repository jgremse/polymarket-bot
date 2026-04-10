"""
Flask dashboard server.
Run standalone or as a background thread from deploy/main.py.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template
from flask_cors import CORS

from dashboard.state import state
import dashboard.metrics as m
from bot.db import TradingDB, DB_PATH

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    snap = state.snapshot()
    prices = snap["prices"]
    fills = snap["fills"]
    signals = snap["signals"]

    # Read performance from DB so it's always accurate across restarts
    import sqlite3 as _sqlite3
    _conn = _sqlite3.connect(str(DB_PATH))
    _conn.row_factory = _sqlite3.Row
    _fills_from_db = [dict(r) for r in _conn.execute("SELECT * FROM fills ORDER BY id DESC").fetchall()]
    _conn.close()
    perf = m.compute_performance(_fills_from_db)
    macd = m.compute_macd(prices)
    cvd = m.compute_cvd(prices)

    snap["metrics"] = {
        "vwap": m.compute_vwap(prices),
        "volatility": m.compute_volatility(prices),
        "rsi": m.compute_rsi(prices),
        "rsi_series": m.compute_rsi_series(prices),
        "macd": macd,
        "cvd": cvd,
        "bollinger": m.compute_bollinger(prices),
        "vwap_deviation": m.compute_vwap_deviation_series(prices),
        "implied_probability": m.compute_implied_probability(prices),
        "signal_strength": m.compute_signal_strength(signals),
        "performance": perf,
        "daily_pnl": m.compute_daily_pnl(_fills_from_db),
    }

    snap["last_price"] = round(prices[-1]["price"], 4) if prices else 0
    snap["last_volume"] = round(prices[-1]["volume"], 2) if prices else 0
    snap["total_pnl"] = perf["total_pnl"]
    snap["win_rate"] = perf["win_rate"]
    snap["capital"] = round(snap.get("initial_capital", 1000.0) + perf["total_pnl"], 4)
    _conn2 = _sqlite3.connect(str(DB_PATH))
    _conn2.row_factory = _sqlite3.Row
    snap["open_orders"] = [dict(r) for r in _conn2.execute("SELECT * FROM orders WHERE status='open' ORDER BY id ASC").fetchall()]
    _conn2.close()

    return jsonify(snap)


def run(host="127.0.0.1", port=5000, debug=False):
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run(debug=True)
