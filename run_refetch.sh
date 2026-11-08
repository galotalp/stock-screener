#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "[refetch] building review.csv from new filtered.csv (Real Estate excluded)..."
python3 build_review.py > data/refetch.log 2>&1
echo "[refetch] fetching Lynch fundamentals for survivors..."
rm -f data/lynch.csv data/lynch_processed.txt
python3 fetch_lynch.py >> data/refetch.log 2>&1
echo "[refetch] DONE" >> data/refetch.log
