#!/usr/bin/env bash
cd "$(dirname "$0")"
# wait for mention collection to finish
while pgrep -f "collect_mentions.py" >/dev/null; do sleep 8; done
echo "[pipeline] mentions done; collecting prices for full universe..." > data/pipeline.log
python3 collect_prices.py >> data/pipeline.log 2>&1
echo "[pipeline] prices done; running analysis..." >> data/pipeline.log
python3 analyze.py >> data/pipeline.log 2>&1
echo "[pipeline] DONE" >> data/pipeline.log
