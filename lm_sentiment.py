"""
Deterministic finance sentiment scorer — Loughran-McDonald lexicon + light NLP.

No LLM, no training, no external files: the L-M positive/negative word stems are
embedded below (the canonical finance sentiment dictionary derived from SEC
filings). We add negation handling and a few social-media cues (StockTwits/Reddit
slang) so informal text scores sensibly.

score_text(text) -> {
  "score": float in [-1, 1],   # (pos - neg) / (pos + neg), 0 if none
  "pos": int, "neg": int,      # term counts
  "hits": [words...]           # matched sentiment words (for transparency)
}
"""
import re

# --- Loughran-McDonald NEGATIVE (finance-context negative). Core high-frequency set. ---
LM_NEG = set("""
loss losses adverse adversely against decline declines declined declining decrease
decreased decreases decreasing negative negatively deficit deficits weak weakness
weakened weaker downturn litigation lawsuit lawsuits sue sued fraud fraudulent breach
default defaults defaulted bankruptcy bankrupt insolvent insolvency impairment impaired
writedown writeoff writeoffs restructuring layoff layoffs cut cuts cutting shortfall
miss missed misses missing warn warning warned downgrade downgraded downgrades sell
selling sold plunge plunged plunges plummet plummeted crash crashed crashing tumble
tumbled slump slumped drop dropped dropping fell fall falling sink sinks sinking
underperform underperformed underperforming risk risks risky risked concern concerns
concerned concerning doubt doubts doubtful trouble troubled troubling struggle
struggled struggling struggles fail failed failing failure failures delay delayed
delays halt halted suspend suspended suspension recall recalled probe probes
investigation investigations subpoena penalty penalties fine fined sanctions volatile
volatility uncertain uncertainty pessimistic bearish bear overvalued dilution diluted
dilutive bubble selloff correction recession slowdown slowing headwind headwinds
disappointing disappointed disappoint bad worse worst terrible awful poor scam pump
dump trap avoid overpriced dead dying dumpster
""".split())

# --- Loughran-McDonald POSITIVE (finance-context positive). Core high-frequency set. ---
LM_POS = set("""
gain gains gained gaining profit profits profitable profitability growth grow grew
growing increase increased increases increasing rise rises rising rose surge surged
surges soar soared jump jumped jumps rally rallied strong stronger strongest strength
strengthen record records beat beats beating exceed exceeded exceeds outperform
outperformed outperforming upgrade upgraded upgrades buy buying bought bullish bull
optimistic optimism opportunity opportunities opportunistic advantage advantages
advantageous efficient efficiency efficiencies improve improved improving improvement
improvements innovation innovative innovate leading leader leadership success
successful successfully win wins winning won achieve achieved achievement achievements
expand expanded expanding expansion boost boosted boosting momentum robust solid
healthy positive positively favorable favourable attractive undervalued cheap value
breakout moon rocket tailwind tailwinds accelerate accelerated accelerating dividend
buyback buybacks upside outperformer highflyer great excellent good better best superb
strongbuy longterm hold diamond gem multibagger tenbagger
""".split())

# words that flip the following sentiment word's polarity
NEGATORS = {"not", "no", "never", "isn't", "isnt", "wasn't", "wasnt", "aren't",
            "arent", "don't", "dont", "didn't", "didnt", "won't", "wont",
            "cannot", "can't", "cant", "without", "lack", "lacks", "lacking",
            "hardly", "barely", "nothing"}

# social emphasis (StockTwits/Reddit)
BULL_CUES = {"🚀", "🌙", "💎", "🙌", "📈", "🟢", "�up", "bullish", "calls", "yolo"}
BEAR_CUES = {"📉", "🔴", "🩸", "puts", "bearish", "rip", "🐻"}

TOKEN_RE = re.compile(r"[a-z']+|[🚀🌙💎🙌📈🔴🟢📉🩸🐻]")


def _tokens(text):
    return TOKEN_RE.findall((text or "").lower())


def score_text(text):
    toks = _tokens(text)
    pos = neg = 0
    hits = []
    for i, t in enumerate(toks):
        polarity = 0
        if t in LM_POS or t in BULL_CUES:
            polarity = 1
        elif t in LM_NEG or t in BEAR_CUES:
            polarity = -1
        if polarity == 0:
            continue
        # check preceding 1-2 tokens for negation
        window = toks[max(0, i - 2):i]
        if any(w in NEGATORS for w in window):
            polarity = -polarity
        if polarity > 0:
            pos += 1
        else:
            neg += 1
        hits.append(t)
    total = pos + neg
    score = 0.0 if total == 0 else (pos - neg) / total
    return {"score": round(score, 4), "pos": pos, "neg": neg, "hits": hits}


def score_to_100(score):
    """Map [-1,1] lexicon score to the 0-100 scale used elsewhere (50 = neutral)."""
    return round((score + 1) * 50, 1)


if __name__ == "__main__":
    tests = [
        "NVDA earnings beat expectations, stock surged to record highs 🚀",
        "$MU is a value trap, huge losses and weak guidance, avoid",
        "not bullish on this, the growth is not sustainable",
        "TSLA to the moon 🚀🚀 diamond hands",
        "mixed quarter, some gains but rising costs and a lawsuit",
    ]
    for t in tests:
        r = score_text(t)
        print(f"{r['score']:+.2f} (100-scale {score_to_100(r['score'])}) pos={r['pos']} neg={r['neg']} :: {t[:50]}")
