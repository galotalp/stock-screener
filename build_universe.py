"""
Build a clean universe of common-stock tickers from NASDAQ, NYSE/other, and TSX,
formatted for yfinance. Filters out ETFs, funds, warrants, notes, preferred shares,
rights, units, and test issues.
"""
import csv
import json
import re

DATA = "data"

# Words in a security name that signal it's NOT a plain common share.
EXCLUDE_NAME_PAT = re.compile(
    r"\b("
    r"ETF|ETN|Fund|Trust|Warrant|Warrants|Right|Rights|Unit|Units|"
    r"Preferred|Preferential|Depositary|Depository|Notes?|Debenture|"
    r"Bond|Index|Portfolio|SPDR|iShares|ProShares|Series [A-Z]|"
    r"% |Redeemable|Convertible"
    r")\b",
    re.IGNORECASE,
)

# Symbols with these suffix chars are typically warrants/units/preferred/when-issued.
BAD_SYMBOL_CHARS = re.compile(r"[.$]")  # e.g. BRK.A stays but most .XX are share classes/warrants


def clean_us_symbol(sym: str) -> str | None:
    sym = sym.strip().upper()
    if not sym or " " in sym:
        return None
    # yfinance uses '-' for share classes (BRK.A -> BRK-A). Keep letters/dash only.
    if not re.fullmatch(r"[A-Z]{1,6}", sym):
        return None
    return sym


def load_nasdaq():
    out = []
    with open(f"{DATA}/nasdaqlisted.txt") as f:
        rows = f.read().splitlines()
    header = rows[0].split("|")
    for line in rows[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < len(header):
            continue
        rec = dict(zip(header, parts))
        if rec.get("ETF", "N") == "Y":
            continue
        if rec.get("Test Issue", "N") == "Y":
            continue
        name = rec.get("Security Name", "")
        if EXCLUDE_NAME_PAT.search(name):
            continue
        sym = clean_us_symbol(rec.get("Symbol", ""))
        if not sym:
            continue
        out.append((sym, name, "NASDAQ"))
    return out


def load_other():
    out = []
    with open(f"{DATA}/otherlisted.txt") as f:
        rows = f.read().splitlines()
    header = rows[0].split("|")
    for line in rows[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < len(header):
            continue
        rec = dict(zip(header, parts))
        if rec.get("ETF", "N") == "Y":
            continue
        if rec.get("Test Issue", "N") == "Y":
            continue
        name = rec.get("Security Name", "")
        if EXCLUDE_NAME_PAT.search(name):
            continue
        # Prefer NASDAQ Symbol col if present, else ACT Symbol
        sym = clean_us_symbol(rec.get("NASDAQ Symbol") or rec.get("ACT Symbol", ""))
        if not sym:
            continue
        exch = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "BATS"}.get(
            rec.get("Exchange", ""), rec.get("Exchange", "")
        )
        out.append((sym, name, exch))
    return out


def load_tsx():
    out = []
    with open(f"{DATA}/tsx_raw.json") as f:
        d = json.load(f)
    for r in d.get("results", []):
        sym = r.get("symbol", "").strip().upper()
        name = r.get("name", "")
        if not sym:
            continue
        # Skip obvious ETFs/funds by name
        if EXCLUDE_NAME_PAT.search(name):
            continue
        # TSX share-class / special suffixes: skip .UN (trusts), .PR (pref), .WT (warrant)
        if re.search(r"\.(UN|PR|WT|RT|DB|NT)\b", sym):
            continue
        # yfinance format: dots become dashes, add .TO
        yf_sym = sym.replace(".", "-") + ".TO"
        out.append((yf_sym, name, "TSX"))
    return out


def main():
    seen = set()
    universe = []
    for loader in (load_nasdaq, load_other, load_tsx):
        for sym, name, exch in loader():
            if sym in seen:
                continue
            seen.add(sym)
            universe.append({"ticker": sym, "name": name, "exchange": exch})

    with open(f"{DATA}/universe.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "exchange"])
        w.writeheader()
        w.writerows(universe)

    from collections import Counter
    by_exch = Counter(u["exchange"] for u in universe)
    print(f"Total universe: {len(universe)} tickers")
    for exch, n in sorted(by_exch.items(), key=lambda x: -x[1]):
        print(f"  {exch}: {n}")


if __name__ == "__main__":
    main()
