"""
Quant metrics computed on-demand from state snapshots.
All functions accept list-of-dicts (the state price/fill format).
"""

import math
from typing import List


# ── Market metrics ────────────────────────────────────────────────────────────

def compute_vwap(prices: list) -> float:
    if not prices:
        return 0.0
    total_val = sum(p["price"] * p["volume"] for p in prices)
    total_vol = sum(p["volume"] for p in prices)
    return round(total_val / total_vol, 4) if total_vol > 0 else 0.0


def compute_volatility(prices: list, window: int = 20) -> float:
    """Annualised volatility as a percentage."""
    recent = prices[-window:]
    if len(recent) < 2:
        return 0.0
    ps = [p["price"] for p in recent]
    returns = [ps[i] / ps[i - 1] - 1 for i in range(1, len(ps))]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(variance) * math.sqrt(252) * 100, 2)


def compute_implied_probability(prices: list, window: int = 50) -> list:
    """Returns last `window` prices as implied probability series."""
    recent = prices[-window:]
    return [{"timestamp": p["timestamp"], "prob": round(p["price"] * 100, 2)} for p in recent]


# ── Signal & Analysis metrics ─────────────────────────────────────────────────

def compute_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    ps = [p["price"] for p in prices[-(period + 1):]]
    deltas = [ps[i] - ps[i - 1] for i in range(1, len(ps))]
    avg_gain = sum(max(d, 0) for d in deltas) / period
    avg_loss = sum(max(-d, 0) for d in deltas) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def compute_rsi_series(prices: list, period: int = 14, points: int = 50) -> list:
    result = []
    for i in range(period + 1, len(prices) + 1):
        window = prices[max(0, i - period - 1):i]
        rsi = compute_rsi(window, period)
        result.append({"timestamp": prices[i - 1]["timestamp"], "rsi": rsi})
    return result[-points:]


def compute_macd(prices: list, fast: int = 12, slow: int = 26, signal_p: int = 9) -> dict:
    if len(prices) < slow + signal_p:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0, "series": []}

    def ema(data, span):
        k = 2 / (span + 1)
        out = [data[0]]
        for v in data[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    ps = [p["price"] for p in prices]
    ef = ema(ps, fast)
    es = ema(ps, slow)
    macd_line = [f - s for f, s in zip(ef, es)]
    sig_line = ema(macd_line[slow - 1:], signal_p)

    # Build series for chart
    series = []
    offset = slow - 1
    for i, (m, s) in enumerate(zip(macd_line[offset:], sig_line)):
        idx = offset + i
        series.append({
            "timestamp": prices[idx]["timestamp"],
            "macd": round(m, 6),
            "signal": round(s, 6),
            "histogram": round(m - s, 6),
        })

    last = series[-1] if series else {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    return {
        "macd": last["macd"],
        "signal": last["signal"],
        "histogram": last["histogram"],
        "series": series[-50:],
    }


def compute_cvd(prices: list) -> dict:
    """Cumulative Volume Delta series and latest value."""
    cvd = 0.0
    series = []
    for p in prices:
        mid = (p["bid"] + p["ask"]) / 2
        delta = p["volume"] if p["price"] >= mid else -p["volume"]
        cvd += delta
        series.append({"timestamp": p["timestamp"], "cvd": round(cvd, 2)})
    return {"value": round(cvd, 2), "series": series[-50:]}


def compute_signal_strength(signals: list) -> float:
    """Average confidence of the last 3 signals."""
    recent = [s for s in signals[:3]]
    if not recent:
        return 0.0
    return round(sum(s["confidence"] for s in recent) / len(recent), 1)


# ── Performance metrics ───────────────────────────────────────────────────────

def compute_performance(fills: list) -> dict:
    closed = [f for f in fills if f.get("pnl", 0) != 0]
    if not closed:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "total_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
            "by_strategy": {},
        }

    wins = [f for f in closed if f["pnl"] > 0]
    losses = [f for f in closed if f["pnl"] < 0]
    gross_wins = sum(f["pnl"] for f in wins)
    gross_losses = abs(sum(f["pnl"] for f in losses))

    # Per-strategy breakdown
    strategies = {}
    for f in closed:
        s = f.get("strategy", "unknown")
        if s not in strategies:
            strategies[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
        strategies[s]["trades"] += 1
        strategies[s]["pnl"] = round(strategies[s]["pnl"] + f["pnl"], 4)
        if f["pnl"] > 0:
            strategies[s]["wins"] += 1
    for s in strategies:
        t = strategies[s]["trades"]
        strategies[s]["win_rate"] = round(strategies[s]["wins"] / t * 100, 1) if t else 0

    return {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0.0,
        "avg_win": round(gross_wins / len(wins), 4) if wins else 0.0,
        "avg_loss": round(-gross_losses / len(losses), 4) if losses else 0.0,
        "total_pnl": round(sum(f["pnl"] for f in closed), 4),
        "best_trade": round(max(f["pnl"] for f in closed), 4),
        "worst_trade": round(min(f["pnl"] for f in closed), 4),
        "by_strategy": strategies,
    }
