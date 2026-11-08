#!/usr/bin/env bash
cd "$(dirname "$0")"
# wait for any running build_review.py to finish
while pgrep -f build_review.py >/dev/null; do sleep 10; done
echo "[refetch] build_review done; review.csv rows: $(wc -l < data/review.csv)" > data/refetch.log
echo "[refetch] fetching Lynch fundamentals..." >> data/refetch.log
rm -f data/lynch.csv data/lynch_processed.txt
python3 fetch_lynch.py >> data/refetch.log 2>&1
echo "[refetch] DONE; lynch.csv rows: $(wc -l < data/lynch.csv)" >> data/refetch.log
