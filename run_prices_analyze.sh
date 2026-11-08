#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "[run] collecting prices..." > data/pa.log
python3 collect_prices.py >> data/pa.log 2>&1
echo "[run] prices done ($(ls data/prices/*.json | wc -l | tr -d ' ') tickers); analyzing..." >> data/pa.log
python3 analyze.py >> data/pa.log 2>&1
echo "[run] DONE" >> data/pa.log
