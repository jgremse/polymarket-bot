from typing import List
import numpy as np
import pandas as pd


class BacktestMetrics:
    @staticmethod
    def compute(equity: pd.Series, fills) -> dict:
        if len(equity) < 2:
            return {}

        returns = equity.pct_change().dropna()
        total_return = (equity.iloc[-1] / equity.iloc[0]) - 1
        max_dd = BacktestMetrics._max_drawdown(equity)
        sharpe = BacktestMetrics._sharpe(returns)

        winning = [f for f in fills if f.pnl > 0]
        losing  = [f for f in fills if f.pnl < 0]
        trades  = [f for f in fills if f.pnl != 0]

        win_rate = len(winning) / len(trades) if trades else 0.0
        avg_win  = np.mean([f.pnl for f in winning]) if winning else 0.0
        avg_loss = np.mean([f.pnl for f in losing])  if losing  else 0.0
        profit_factor = (
            sum(f.pnl for f in winning) / abs(sum(f.pnl for f in losing))
            if losing else float("inf")
        )

        return {
            "total_return_pct": round(total_return * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "sharpe_ratio": round(sharpe, 3),
            "total_trades": len(trades),
            "win_rate_pct": round(win_rate * 100, 2),
            "avg_win_usd": round(avg_win, 4),
            "avg_loss_usd": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 3),
            "final_equity": round(equity.iloc[-1], 2),
        }

    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        peak = equity.cummax()
        drawdown = (equity - peak) / peak
        return float(drawdown.min())

    @staticmethod
    def _sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
        if returns.std() == 0:
            return 0.0
        return float((returns.mean() / returns.std()) * np.sqrt(periods_per_year))

    @staticmethod
    def print_summary(metrics: dict) -> None:
        print("\n── Backtest Results ─────────────────────────")
        for k, v in metrics.items():
            print(f"  {k:<25} {v}")
        print("─────────────────────────────────────────────\n")
