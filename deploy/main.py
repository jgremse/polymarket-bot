"""
Entry point for the prediction market trading bot.

Usage
-----
# Kalshi live trading (US-friendly, no VPN needed)
python deploy/main.py --exchange kalshi --strategy macd --market KXBTCD-24DEC31-T50000

# Polymarket live trading (requires VPN outside US)
python deploy/main.py --exchange polymarket --strategy rsi --market 0xYOUR_TOKEN_ID

# Dry run — logs orders but never sends them
python deploy/main.py --exchange kalshi --strategy cvd --market KXBTCD-24DEC31-T50000 --dry-run

# Backtest (no credentials needed, works for both exchanges)
python deploy/main.py --strategy macd --backtest --data path/to/trades.csv
"""

import argparse
import logging
import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd

from strategies import MACDStrategy, RSIStrategy, CVDStrategy
from bot.risk_manager import RiskManager, RiskConfig
from bot.polymarket_trader import PolymarketTrader
from bot.kalshi_trader import KalshiTrader
from bot.market_scanner import KalshiMarketScanner
from bot.db import TradingDB
from backtesting.engine import BacktestEngine
from backtesting.metrics import BacktestMetrics
import dashboard.app as dash_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("deploy.main")

STRATEGIES = {
    "macd": MACDStrategy,
    "rsi": RSIStrategy,
    "cvd": CVDStrategy,
    "all": None,  # special: runs all three simultaneously
}

EXCHANGES = {
    "polymarket": PolymarketTrader,
    "kalshi": KalshiTrader,
}


def build_strategy(name: str):
    if name.lower() == "all":
        return [MACDStrategy(), RSIStrategy(), CVDStrategy()]
    cls = STRATEGIES.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(STRATEGIES)}")
    return cls()


def build_trader(exchange: str, risk_manager: RiskManager, dry_run: bool, db=None):
    cls = EXCHANGES.get(exchange.lower())
    if cls is None:
        raise ValueError(f"Unknown exchange '{exchange}'. Choose from: {list(EXCHANGES)}")
    return cls(risk_manager=risk_manager, dry_run=dry_run, db=db)


def run_backtest(strategy, data_path: str, capital: float) -> None:
    logger.info("Loading data from %s", data_path)
    data = pd.read_csv(data_path, parse_dates=["timestamp"])

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=capital,
        fee_rate=0.002,
        order_ttl_bars=5,
        max_position=0.20,
    )
    result = engine.run(data)
    BacktestMetrics.print_summary(result.metrics)

    out_path = f"backtest_{strategy.name.lower()}_results.csv"
    result.equity_curve.to_csv(out_path, header=["equity"])
    logger.info("Equity curve saved to %s", out_path)


def run_scan(exchange: str, strategy, capital: float, dry_run: bool, dashboard: bool, top_n: int) -> None:
    risk_config = RiskConfig(
        max_position_pct=0.10,
        max_total_exposure_pct=0.50,
        max_daily_loss_pct=0.05,
    )
    rm = RiskManager(capital=capital, config=risk_config)
    db = TradingDB()
    trader = build_trader(exchange, rm, dry_run, db=db)

    # Scanner only works with Kalshi (Polymarket has different market structure)
    if exchange.lower() != "kalshi":
        raise ValueError("--scan is only supported for --exchange kalshi")

    scanner = KalshiMarketScanner(trader._client._markets_api, top_n=top_n)

    if dashboard:
        bot_thread = threading.Thread(
            target=trader.run_scan,
            kwargs={"strategies": strategy, "scanner": scanner},
            daemon=True,
        )
        bot_thread.start()
        print(f"\n  Dashboard -> http://127.0.0.1:5000\n")
        dash_app.run(host="127.0.0.1", port=5000)
    else:
        trader.run_scan(strategies=strategy, scanner=scanner)


def run_live(exchange: str, strategy, market_id: str, capital: float, dry_run: bool, dashboard: bool) -> None:
    risk_config = RiskConfig(
        max_position_pct=0.10,
        max_total_exposure_pct=0.50,
        max_daily_loss_pct=0.05,
    )
    rm = RiskManager(capital=capital, config=risk_config)
    trader = build_trader(exchange, rm, dry_run)

    if dashboard:
        # Run bot in background thread, Flask in main thread so it prints startup info
        bot_thread = threading.Thread(
            target=trader.run,
            kwargs={"strategy": strategy, "market_id": market_id},
            daemon=True,
        )
        bot_thread.start()
        print("\n  Dashboard -> http://127.0.0.1:5000\n")
        dash_app.run(host="127.0.0.1", port=5000)
    else:
        trader.run(strategy=strategy, market_id=market_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prediction Market Limit-Order Trading Bot")
    parser.add_argument("--exchange", default="kalshi", choices=list(EXCHANGES),
                        help="Exchange to trade on: kalshi (US, no VPN) | polymarket (VPN required)")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES),
                        help="Strategy to run: macd | rsi | cvd | all")
    parser.add_argument("--market", default=None,
                        help="Market ID (required for live trading)")
    parser.add_argument("--capital", type=float, default=1000.0,
                        help="Starting capital in USD (default: 1000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log orders but do not send them to the exchange")
    parser.add_argument("--backtest", action="store_true",
                        help="Run in backtest mode (no exchange credentials needed)")
    parser.add_argument("--data", default=None,
                        help="Path to CSV data file for backtesting")
    parser.add_argument("--dashboard", action="store_true",
                        help="Launch live dashboard at http://localhost:5000")
    parser.add_argument("--scan", action="store_true",
                        help="Auto-scan all major Kalshi financial markets (ignores --market)")
    parser.add_argument("--top-n", type=int, default=100,
                        help="Number of top markets to trade in scan mode (default: 100)")
    args = parser.parse_args()

    strategy = build_strategy(args.strategy)

    if args.backtest:
        if not args.data:
            parser.error("--data is required for backtesting")
        run_backtest(strategy, args.data, args.capital)
    elif args.scan:
        run_scan(args.exchange, strategy, args.capital, args.dry_run, args.dashboard, args.top_n)
    else:
        if not args.market:
            parser.error("--market is required for live trading (or use --scan)")
        run_live(args.exchange, strategy, args.market, args.capital, args.dry_run, args.dashboard)


if __name__ == "__main__":
    main()
