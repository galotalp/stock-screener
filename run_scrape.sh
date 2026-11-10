#!/usr/bin/env bash
#
# run_scrape.sh — portable, EC2-ready one-shot run of the sentiment pipeline.
#
# Designed to be called inside the ScratcherCatcher boot sequence, BEFORE shutdown:
#     boot -> scratchercatcher scrape -> ./run_scrape.sh -> shutdown
#
# Unlike update_sentiment.sh (which staged into a local galavgay.com/ repo on the
# Mac), this publishes DIRECTLY to S3 — no local site repo needed on the server.
#
# Portable: uses the script's own directory, no hardcoded /Users paths.
# A lock file prevents overlapping runs. Safe to run headless (no browser).
#
# Env overrides (optional):
#   S3_BUCKET   (required)
#   S3_PREFIX   (default: sentiment)
#   OPT_PREFIX  (default: options)   prefix for the options tool's page
#   CF_DISTS    (optional, space-separated)
#   NO_PUBLISH=1  do everything except S3 upload/invalidation
#
# Requirements on the host:
#   - python3 with: yfinance, curl_cffi, feedparser  (pip install --user … )
#   - AWS credentials with s3:PutObject on the bucket + cloudfront:CreateInvalidation
#     (an IAM role on the EC2 instance is cleanest — no keys on disk)

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

S3_BUCKET="${S3_BUCKET:?set S3_BUCKET}"
S3_PREFIX="${S3_PREFIX:-sentiment}"
OPT_PREFIX="${OPT_PREFIX:-options}"
CF_DISTS="${CF_DISTS:-}"
NO_PUBLISH="${NO_PUBLISH:-0}"

LOCK="$DIR/.scrape.lock"
# don't overlap with another run (or the previous one that hung)
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "another run is active (pid $(cat "$LOCK")); exiting." >&2
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

STAMP="$(date -u '+%Y-%m-%dT%H:%MZ')"
echo "==> sentiment scrape  ($STAMP)  dir=$DIR"

echo "==> [1/8] universe"
python3 build_universe_dynamic.py | tail -2
# Refresh company-name cache (data/names.json) BEFORE collecting mentions, so the
# news entity-scoping filter can match articles by company name (e.g. "Apple"),
# not just ticker symbol. New/trending tickers get named here first. Non-fatal.
echo "==> [2/8] company names (entity scope)"
python3 entity_scope.py --refresh-mentions 2>&1 | tail -1 || echo "  (entity_scope refresh failed — news filter falls back to symbol-only)"
echo "==> [3/8] mentions (deduped, entity-scoped news)"
python3 collect_mentions.py --from-universe | tail -1
echo "==> [4/8] prices"
python3 collect_prices.py | tail -1
# earnings dates per ticker (data/earnings/<T>.json) — feeds the earnings regime
# split in analyze_sessions and the E-markers in the dashboard. Non-fatal.
echo "==> [4b/8] earnings dates"
python3 collect_earnings.py --from-mentions 2>&1 | tail -1 || echo "  (collect_earnings failed — earnings UI may be stale)"
echo "==> [5/8] analyze + generate (dashboard only)"
python3 analyze.py | head -1
# session lead/lag analysis -> data/session_analysis.json, which gen_analysis.py
# embeds for the third plot ("was the move priced in after hours?"). Must run
# AFTER analyze.py and BEFORE gen_analysis.py. Non-fatal if it fails.
python3 analyze_sessions.py > /dev/null 2>&1 || echo "  (analyze_sessions failed — third plot may be stale)"
# label new news items with Claude Haiku 5.5 (Message Batches) -> data/news_llm.jsonl.
# Needs ANTHROPIC_API_KEY or ~/.anthropic_api_key and `pip install --user anthropic`;
# without them it prints a note and the study uses word-list scores. Waits up to 20 min
# for tonight's batch, otherwise it is collected on the next run. Never fails the run.
python3 news_llm.py --wait 20 2>&1 | tail -3
# v2 study (sentiment_v2 stance, NYSE-clock windows, market-adjusted returns,
# pooled tests with earnings controls) -> data/analysis_v2.json, rendered by
# gen_analysis.py. Read-only over the scraped data. Non-fatal.
python3 analyze_v2.py 2>&1 | tail -1 || echo "  (analyze_v2 failed — study results may be stale)"
python3 gen_analysis.py
# per-ticker raw-mention JSONs for the "View mentions" validation table
# (mentions.html). Writes mentions_export/<TICKER>.json. Non-fatal.
python3 export_mentions.py "$STAMP" || echo "  (export_mentions failed — mentions tables may be stale)"
# NOTE: the loop regenerates ONLY the dashboard (analysis.html -> index.html).
# proposal.html and scenarios.html are STATIC pages published manually from the
# Mac; the loop must never overwrite them. Do NOT add gen_proposal.py here.

# ---------------------------------------------------------------------------
# [6/8] OPTIONS STAGE — runs LAST, after every sentiment step above has finished.
#
# This is a SEPARATE project that happens to share the host. It scrapes option
# chains (the only new network work) and reads the sentiment data collected
# above; it never writes to data/mentions/, data/prices/, or data/earnings/.
#
# Placed BEFORE the NO_PUBLISH early-exit on purpose, so a NO_PUBLISH=1 test run
# still exercises the options stage locally.
#
# run_options.py always exits 0 by design — this stage must never be able to
# fail the sentiment pipeline it is appended to. The `|| echo` is belt and
# braces for an interpreter-level failure (e.g. a missing module).
#
# TIMING CAVEAT: the spec wants chain quotes pulled during the regular US
# session; outside it, spreads are unrepresentatively wide. This script is
# invoked by the ScratcherCatcher boot sequence, whose timing it does not
# control, so the stage DETECTS the session at runtime, labels the output, and
# refuses to open simulated positions on non-regular quotes. If the boot lands
# outside market hours the screener still logs IV history (which is the part
# that compounds), but the simulation will correctly sit idle. To get live
# quotes, schedule a boot for mid-afternoon ET.
# ---------------------------------------------------------------------------
echo "==> [6/8] options chains + simulation"
python3 run_options.py 2>&1 | tail -12 || echo "  (options stage failed — sentiment results above are unaffected)"

if [ "$NO_PUBLISH" = "1" ]; then
  echo "==> NO_PUBLISH set; local files only (analysis.html, options.html)"
  exit 0
fi

echo "==> [7/8] publish dashboard + mentions to s3://$S3_BUCKET/$S3_PREFIX/ + invalidate"
# per-stock chart data the v2 dashboard fetches on demand (series/<TICKER>.json).
# Upload BEFORE index.html so a freshly published page never asks for a missing file.
aws s3 sync series/ "s3://$S3_BUCKET/$S3_PREFIX/series/" --delete --content-type "application/json" >/dev/null
aws s3 cp analysis.html "s3://$S3_BUCKET/$S3_PREFIX/index.html"    --content-type "text/html; charset=utf-8" >/dev/null
# the mentions validation page (static; safe to re-upload each run) + the
# per-ticker mention JSONs the page fetches on demand.
aws s3 cp mentions.html "s3://$S3_BUCKET/$S3_PREFIX/mentions.html"  --content-type "text/html; charset=utf-8" >/dev/null
aws s3 sync mentions_export/ "s3://$S3_BUCKET/$S3_PREFIX/mentions/" --delete --content-type "application/json" >/dev/null

# The options tool publishes to its OWN prefix (default: options) so it can never
# overwrite a sentiment page. Non-fatal: a failure here leaves the sentiment
# publish above already done.
if [ -f options.html ]; then
  echo "==> [8/8] publish options page to s3://$S3_BUCKET/$OPT_PREFIX/"
  aws s3 cp options.html "s3://$S3_BUCKET/$OPT_PREFIX/index.html" \
    --content-type "text/html; charset=utf-8" >/dev/null \
    || echo "  (options publish failed)"
fi

for D in $CF_DISTS; do
  aws cloudfront create-invalidation --distribution-id "$D" \
    --paths "/$S3_PREFIX/*" "/$OPT_PREFIX/*" --query "Invalidation.Status" --output text
done
echo "==> done ($STAMP)  live: https://$S3_BUCKET/$S3_PREFIX  and  https://$S3_BUCKET/$OPT_PREFIX"
