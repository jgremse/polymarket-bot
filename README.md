# Polymarket Trading Bot

A Python limit-order trading bot for [Polymarket](https://polymarket.com) using the official [`py-clob-client`](https://github.com/Polymarket/py-clob-client).

## Structure

```
polymarket-bot/
├── strategies/
│   ├── base_strategy.py     # Abstract base class + Signal dataclass
│   ├── macd_strategy.py     # MACD crossover
│   ├── rsi_strategy.py      # RSI mean-reversion
│   └── cvd_strategy.py      # Cumulative Volume Delta divergence
├── backtesting/
│   ├── engine.py            # Limit-order simulation engine
│   └── metrics.py           # Sharpe, drawdown, win rate, profit factor
├── bot/
│   ├── trader.py            # Polymarket API integration (limit orders only)
│   └── risk_manager.py      # Position sizing, exposure limits, daily loss halt
├── deploy/
│   └── main.py              # CLI entry point (live + backtest modes)
├── .env.example
├── requirements.txt
└── .gitignore
```

## Setup

```bash
# 1. Clone and enter the project
cd polymarket-bot

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp .env.example .env
# Edit .env and fill in your Polymarket API keys and wallet details
```

## Usage

### Live Trading

```bash
# MACD strategy on a market (replace token ID with your target market)
python deploy/main.py --strategy macd --market 0xYOUR_MARKET_TOKEN_ID

# Dry run — logs orders but never sends them
python deploy/main.py --strategy rsi --market 0xYOUR_MARKET_TOKEN_ID --dry-run

# Custom capital
python deploy/main.py --strategy cvd --market 0xYOUR_MARKET_TOKEN_ID --capital 500
```

### Backtesting

Your CSV must have at minimum: `timestamp`, `price`, `volume`. CVD strategy also requires `bid` and `ask` columns.

```bash
python deploy/main.py --strategy macd --backtest --data path/to/trades.csv
python deploy/main.py --strategy rsi  --backtest --data path/to/trades.csv --capital 500
```

Backtest results are printed to the console and the equity curve is saved as `backtest_<strategy>_results.csv`.

## Strategies

| Strategy | Signal Logic |
|---|---|
| **MACD** | Buys on bullish MACD/signal crossover, sells on bearish crossover |
| **RSI** | Buys when RSI recovers from oversold (<30), sells when it rejects from overbought (>70) |
| **CVD** | Buys on bullish CVD divergence (rising buy pressure, flat price), sells on bearish divergence |

All strategies return `Signal(side, price, size, confidence)`. Size is set to `0` by the strategy and filled in by the `RiskManager` before order placement.

## Risk Management

`RiskManager` enforces:
- **Max position per market** — default 10% of capital
- **Max total exposure** — default 50% of capital across all markets
- **Daily loss halt** — stops new orders if daily PnL drops below -5%
- **Confidence scaling** — position size scales with signal confidence (0–1)
- **Order size bounds** — min 1 share, max 100 shares (configurable)

## Important Notes

- **Limit orders only** — no market orders are ever sent.
- **Polymarket prices are between 0 and 1** (probability). All limit prices are clamped to [0.01, 0.99].
- **Credentials** — never commit your `.env` file. Your private key signs on-chain transactions.
- **Paper trade first** — use `--dry-run` until you've validated the strategy on real market data.

## Disclaimer

This software is for educational purposes. Trading prediction markets carries significant financial risk. Past backtest performance does not guarantee future results.
