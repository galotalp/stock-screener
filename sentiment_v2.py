"""
Mention stance for the v2 analysis (read-only: never rewrites the mention store).

Why this exists (see analyze_v2.py for the evidence):
  * The stored `score` (lm_sentiment.py) counts price-move words ("fell",
    "surged", "rally") as sentiment, so a headline saying a stock fell scores
    negative *because* it fell. That builds reverse causality into any
    sentiment -> price test.
  * It treats uncertainty words ("risk", "volatile") as negative, and posts with
    no hits score exactly 0 and are averaged in, pulling every level toward 0.
  * StockTwits posters often tag their own post Bullish / Bearish. That label is
    the best stance signal available and was being ignored.

stance(m) -> (value, method)
  value   +1 / -1 for a tagged StockTwits post; for other text, the share of
          bullish vs bearish lexicon hits in [-1, 1]; None when the mention has
          no usable stance (it still counts toward attention / mention volume).
  method  "tag" | "lex" | "move" (news that only reports a past price move:
          no stance, so it can't feed back into a price test) | "none"

flags(m) -> {"earn": bool, "move": bool}
"""
import re

# Loughran-McDonald-style finance polarity words, minus price-move verbs,
# uncertainty words and neutral filler. Trading-action words (buy/sell) are a
# stance in a social post but usually reported analyst action in news, so they
# only count for social text.
NEG = set("""
loss losses adverse adversely deficit deficits weak weakness weakened weaker
downturn litigation lawsuit lawsuits sue sued fraud fraudulent breach default
defaults defaulted bankruptcy bankrupt insolvent insolvency impairment impaired
writedown writeoff writeoffs layoff layoffs shortfall miss missed misses warn
warning warned downgrade downgraded downgrades underperform underperformed
underperforming concern concerns concerned concerning doubt doubts doubtful
trouble troubled troubling struggle struggled struggling struggles fail failed
failing failure failures delay delayed delays halt halted suspend suspended
suspension recall recalled probe probes investigation investigations subpoena
penalty penalties fined sanctions pessimistic overvalued dilution diluted
dilutive bubble recession slowdown headwind headwinds disappointing disappointed
disappoint bad worse worst terrible awful poor scam overpriced dead dying
dumpster bearish
""".split())
POS = set("""
profit profits profitable profitability growth grow grew growing strong stronger
strongest strength strengthen beat beats beating exceed exceeded exceeds
outperform outperformed outperforming upgrade upgraded upgrades optimistic
optimism opportunity opportunities advantage advantages advantageous efficient
efficiency improve improved improving improvement improvements innovation
innovative leading leader leadership success successful successfully win wins
winning won achieve achieved achievement expand expanded expanding expansion
boost boosted boosting robust solid healthy favorable favourable attractive
undervalued tailwind tailwinds accelerate accelerated accelerating buyback
buybacks upside great excellent superb bullish
""".split())
SOCIAL_POS = {"buy", "buying", "bought", "long", "calls", "moon", "rocket", "cheap",
              "breakout", "diamond", "gem", "multibagger", "tenbagger", "yolo", "strongbuy"}
SOCIAL_NEG = {"sell", "selling", "short", "shorting", "puts", "dump", "trap", "avoid",
              "rip", "bagholder", "bagholders"}
EMO_POS = {"🚀", "🌙", "💎", "🙌", "📈", "🟢"}
EMO_NEG = {"📉", "🔴", "🩸", "🐻"}
NEGATORS = {"not", "no", "never", "isn't", "isnt", "wasn't", "wasnt", "aren't", "arent",
            "don't", "dont", "didn't", "didnt", "won't", "wont", "cannot", "can't", "cant",
            "without", "lack", "lacks", "lacking", "hardly", "barely", "nothing"}
TOKEN_RE = re.compile(r"[a-z']+|[🚀🌙💎🙌📈🔴🟢📉🩸🐻]")

# text that describes a price move that already happened
MOVE_RE = re.compile(
    r"\b(shares?|stocks?|price)\b[^.]{0,40}?\b(rose|rise[sn]?|rising|fell|fall(s|ing)?|jump\w*|surg\w*|"
    r"soar\w*|plung\w*|plummet\w*|tumbl\w*|slid(es|ing)?|slump\w*|climb\w*|sank|sink\w*|drop\w*|"
    r"gain\w*|declin\w*|rall\w*|spik\w*|skyrocket\w*|crash\w*|rebound\w*|retreat\w*|slip\w*|dip\w*)\b"
    r"|\b(up|down|higher|lower)\s+\d+(\.\d+)?\s?%"
    r"|\d+(\.\d+)?\s?%\s+(higher|lower|gain|drop|jump|rise|fall|decline)"
    r"|\b(hits?|hitting|reach\w*)\s+(a\s+)?(new\s+)?(record|all[- ]time|52[- ]week)\s+(high|low)"
    r"|\bwhy\b[^.]{0,60}\b(stock|shares)\b[^.]{0,20}\b(is|are)\s+(up|down|soaring|sinking|falling|rising|jumping|plunging)"
    r"|\b(stock|shares)\s+(is|are)\s+(up|down|soaring|sinking|falling|rising|jumping|plunging)\b"
    r"|\b(biggest|top)\s+(gainers?|losers?|movers?)\b|\bpre-?market\s+movers?\b|\bstocks?\s+making\s+the\s+biggest\s+moves\b",
    re.IGNORECASE)
EARN_RE = re.compile(
    r"\b(earnings|eps|quarterly|quarter|q[1-4]|fy\d{2,4}|guidance|outlook|revenue|revenues|"
    r"results|reports?|reported|beats?|miss(es|ed)?|estimates?|consensus|forecast)\b", re.IGNORECASE)


def _family(src):
    return "news" if (src or "").lower().startswith("news") else "social"


def lex_score(text, social):
    toks = TOKEN_RE.findall((text or "").lower())
    pos = neg = 0
    for i, t in enumerate(toks):
        if t in POS or t in EMO_POS or (social and t in SOCIAL_POS):
            pol = 1
        elif t in NEG or t in EMO_NEG or (social and t in SOCIAL_NEG):
            pol = -1
        else:
            continue
        if any(w in NEGATORS for w in toks[max(0, i - 2):i]):
            pol = -pol
        if pol > 0:
            pos += 1
        else:
            neg += 1
    return pos, neg


def flags(m):
    text = m.get("blurb") or ""
    return {"earn": bool(EARN_RE.search(text)), "move": bool(MOVE_RE.search(text))}


def stance(m, fl=None):
    tag = m.get("st_tag")
    if tag == "Bullish":
        return 1.0, "tag"
    if tag == "Bearish":
        return -1.0, "tag"
    social = _family(m.get("source")) == "social"
    if fl is None:
        fl = flags(m)
    if not social and fl["move"]:
        return None, "move"
    pos, neg = lex_score(m.get("blurb") or "", social)
    if pos + neg == 0:
        return None, "none"
    return (pos - neg) / (pos + neg), "lex"


if __name__ == "__main__":
    tests = [
        {"source": "news:yahoo", "blurb": "Nvidia stock falls 4% as chip rally fades"},
        {"source": "news:yahoo", "blurb": "Apple beats estimates on strong iPhone demand, raises guidance"},
        {"source": "news:yahoo", "blurb": "Microsoft faces antitrust probe over cloud licensing"},
        {"source": "stocktwits", "blurb": "loading calls here 🚀", "st_tag": None},
        {"source": "stocktwits", "blurb": "whatever", "st_tag": "Bearish"},
        {"source": "stocktwits", "blurb": "see you tomorrow", "st_tag": None},
    ]
    for t in tests:
        print(stance(t), flags(t), t["blurb"])
