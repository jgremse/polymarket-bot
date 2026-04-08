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

    perf = m.compute_performance(fills)
    macd = m.compute_macd(prices)
    cvd = m.compute_cvd(prices)

    snap["metrics"] = {
        "vwap": m.compute_vwap(prices),
        "volatility": m.compute_volatility(prices),
        "rsi": m.compute_rsi(prices),
        "rsi_series": m.compute_rsi_series(prices),
        "macd": macd,
        "cvd": cvd,
        "implied_probability": m.compute_implied_probability(prices),
        "signal_strength": m.compute_signal_strength(signals),
        "performance": perf,
    }

    snap["last_price"] = round(prices[-1]["price"], 4) if prices else 0
    snap["last_volume"] = round(prices[-1]["volume"], 2) if prices else 0
    snap["total_pnl"] = perf["total_pnl"]
    snap["win_rate"] = perf["win_rate"]

    return jsonify(snap)


def run(host="127.0.0.1", port=5000, debug=False):
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run(debug=True)
