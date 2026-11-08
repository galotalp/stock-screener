"""
Apply the project's category exclusions to the screener results.
Produces data/filtered.csv (survivors) and data/excluded.csv (with reasons).

This screen is GENERAL-PURPOSE (not tied to any personal portfolio). It excludes
exactly five stated categories, and nothing else:

    EXCLUDED_CATEGORIES = Finance · Defense · Oil & Gas · Crypto · Meme/Shell

The EXCLUSIONS_DISPLAY string below is the single source of truth that the
website page also shows, so the page and the code can never drift apart.
"""
import csv
import re

DATA = "data"

# Human-readable exclusion statement — surfaced verbatim on the site.
EXCLUSIONS_DISPLAY = ["Finance", "Defense", "Oil & Gas", "Crypto", "Meme stocks"]

# --- Sector-level exclusion ---
EXCLUDE_SECTORS = {"Financial Services"}

# --- Industry-level exclusions (within otherwise-allowed sectors) ---
# Finance (banks/insurers/lenders that Yahoo tags outside Financial Services),
# defense/weapons, and oil & gas across the value chain.
EXCLUDE_INDUSTRY_PAT = re.compile(
    r"(Aerospace & Defense|Defense|Weapon|Firearm|Munition|"
    r"Oil & Gas (Drilling|E&P|Integrated|Midstream|Refining|Equipment|Exploration)|"
    r"\bOil\b|Petroleum|"
    r"Banks?|Insurance|Capital Markets|Credit Services|Asset Management|Mortgage|"
    r"Financial (Data|Conglomerates)|Savings & Cooperative)",
    re.IGNORECASE,
)

# --- Crypto detection (name / business summary) ---
CRYPTO_PAT = re.compile(
    r"\b(bitcoin|ethereum|crypto|blockchain|digital asset|web3|"
    r"stablecoin|nft|mining rig|token treasury)\b",
    re.IGNORECASE,
)

# --- Meme / shell detection: SPACs, blank-check, obvious meme vehicles ---
MEME_SHELL_PAT = re.compile(
    r"\b(acquisition corp|blank check|special purpose acquisition|SPAC|"
    r"shell company)\b",
    re.IGNORECASE,
)
# Well-known meme tickers to hard-exclude regardless of fundamentals.
MEME_TICKERS = {"GME", "AMC", "BBBY", "BB", "KOSS", "EXPR", "NAKD", "CLOV", "MULN", "DJT"}


def norm(t):
    return t.strip().upper()


def classify_exclusion(r):
    ticker = norm(r["ticker"])
    base = ticker.replace(".TO", "")
    sector = r.get("sector", "")
    industry = r.get("industry", "")
    blob = f"{r.get('name','')} {r.get('summary','')}"

    if base in MEME_TICKERS or ticker in MEME_TICKERS:
        return "meme"
    if sector in EXCLUDE_SECTORS:
        return "finance"
    if industry and EXCLUDE_INDUSTRY_PAT.search(industry):
        # Bucket the reason into one of the five stated categories
        il = industry.lower()
        if "defense" in il or "weapon" in il or "aerospace" in il or "firearm" in il or "munition" in il:
            return "defense"
        if "oil" in il or "petroleum" in il or "gas" in il:
            return "oil-and-gas"
        return "finance"
    if CRYPTO_PAT.search(blob):
        return "crypto"
    if MEME_SHELL_PAT.search(blob):
        return "meme"
    return None


def main():
    rows = list(csv.DictReader(open(f"{DATA}/results.csv")))
    survivors, excluded = [], []

    for r in rows:
        reason = classify_exclusion(r)
        if reason:
            excluded.append({**r, "exclude_reason": reason})
        else:
            survivors.append(r)

    fields = list(rows[0].keys())
    with open(f"{DATA}/filtered.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(survivors)
    with open(f"{DATA}/excluded.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["exclude_reason"])
        w.writeheader()
        w.writerows(excluded)

    from collections import Counter
    print(f"Survivors: {len(survivors)}   Excluded: {len(excluded)}")
    print("\n=== Exclusion reasons ===")
    for reason, n in Counter(e["exclude_reason"] for e in excluded).most_common():
        print(f"  {reason}: {n}")


if __name__ == "__main__":
    main()
