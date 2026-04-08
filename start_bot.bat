@echo off
cd /d C:\Users\JackGremse\Projects\polymarket-bot
start "Polymarket Bot" /min C:\Users\JackGremse\Projects\polymarket-bot\.venv\Scripts\python.exe deploy\main.py --exchange kalshi --strategy all --scan --top-n 20 --dry-run --dashboard
