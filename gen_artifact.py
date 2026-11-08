"""Generate the self-contained Lynch report HTML from lynch_lists.json + curated theses/flags
   + sentiment.json (Reddit + mainstream-media sentiment per ticker).

   The 'last updated' date is read from data/last_updated.txt if present (written by
   update_lynch.sh at run time), else falls back to an env var, else a static default."""
import json, html, os

d = json.load(open("data/lynch_lists.json"))
SENT = json.load(open("data/sentiment.json")) if os.path.exists("data/sentiment.json") else {}

if os.path.exists("data/last_updated.txt"):
    UPDATED = open("data/last_updated.txt").read().strip()
else:
    UPDATED = os.environ.get("LYNCH_UPDATED", "July 9, 2026")

# Curated one-line Lynch-voiced thesis + honest risk flags per ticker.
THESIS = {
 "NEON": ("Licenses touch/optical-sensing IP (royalty model, 43% ROE). The kind of asset-light, high-return niche Lynch prized.",
          ["nano-cap ~$16M — thinly traded, high risk", "sub-$1 stock; negative forward EPS — verify turnaround"]),
 "PATH": ("Enterprise robotic-process-automation leader growing ~17% at a PEG of 0.42 — a beaten-down software name priced for pessimism.",
          ["stock-based comp dilutes; profitability still maturing"]),
 "FRSH": ("Freshworks undercuts Salesforce/Zendesk on price for SMBs; 16% growth, 21% margins, cheap vs growth.",
          ["competes with giants; needs to keep winning SMB share"]),
 "PRDO": ("For-profit online education; 31% earnings growth, PEG 0.74, buys back stock. Boring, cash-generative, cheap.",
          ["for-profit education faces regulatory/enrollment cycles"]),
 "DAC": ("Container-ship lessor on long charters; PEG 0.12 and 50% margins. A textbook Lynch CYCLICAL — buy low in the cycle, don't marry it.",
         ["cyclical — earnings tied to shipping rates; time the cycle", "not a compounder; a value/cyclical play"]),
 "MSA": ("Makes safety equipment (gas detection, breathing apparatus) — a 'dull' product with pricing power and 23% ROE. Very Lynch.",
         ["mature end-markets; steady not explosive"]),
 "GNTX": ("Auto-dimming mirrors — dominant niche, fortress balance sheet (D/E 0.43), 16% ROE. Classic 'know the product' pick.",
          ["cyclical — tied to auto production volumes"]),
 "BBW": ("Build-A-Bear: a brand you know, 36% ROE, strong-buy consensus, PEG 0.43. Retail turnaround executing well.",
         ["small-cap retail ~$383M; consumer-spending sensitive", "flat revenue — thesis rests on margins/buybacks"]),
 "EME": ("Mechanical/electrical construction — riding data-center & electrification capex; 30% earnings growth, 39% ROE, PEG 0.41.",
         ["cyclical construction backlog; high share price/share"]),
 "UI": ("Ubiquiti: networking gear beloved by prosumers/IT (you may know UniFi). 30% growth, absurd 115% ROE, founder-led.",
        ["PEG ~1.1 (fairly, not cheaply, priced)", "founder controls; low float, volatile"]),
 "KVHI": ("Maritime connectivity turnaround; revenue growing 27%, PEG 0.47. Small, early-innings recovery.",
          ["micro-cap ~$172M; still unprofitable (turnaround bet)"]),
 "EPAM": ("Elite software-engineering outsourcer; forward P/E 6 vs trailing 12 — earnings inflecting, PEG 0.46.",
          ["exposure to IT-spending cycles; historically Eastern-Europe delivery"]),
 "MNDY": ("monday.com work-OS; 24% growth, PEG 0.28 — a genuine fast grower at a reasonable price.",
          ["SaaS competition; valuation still premium on absolute P/E"]),
 "AMSC": ("Grid & wind power-electronics; 30% growth, 45% margins, earnings exploding. Rides electrification.",
          ["lumpy order-driven revenue; forward P/E richer than trailing"]),
 "PCTY": ("Paylocity: cloud payroll/HR; earnings inflecting (fwd P/E 13 vs 24), 37% analyst upside, strong-buy, clean books.",
          ["HR-software competition (Workday, ADP)"]),
 "ADSK": ("Autodesk: you know the product (AutoCAD/Fusion). Earnings inflecting hard, 59% analyst upside, strong-buy.",
          ["elevated debt (D/E 85); mature growth"]),
 "GLOB": ("Globant digital-engineering; forward P/E 4.7, 95% analyst upside — deeply out of favor, big re-rating potential if it delivers.",
          ["revenue flat this year; 'catch a falling knife' risk"]),
 "WAY": ("Waystar: healthcare-payments software; 22% growth, fwd P/E 12, strong-buy, clean balance sheet.",
         ["recent IPO; healthcare-IT execution risk"]),
 "TRMB": ("Trimble: GPS/positioning hardware+software; earnings up 56%, 60% analyst upside, strong-buy.",
          ["PEG ~1.5 on trailing growth; transition to subscription"]),
 "BSX": ("Boston Scientific: med-device compounder, earnings doubling, 73% analyst upside, strong-buy, PEG 0.55.",
         ["large-cap; upside depends on continued device launches"]),
 "CRM": ("Salesforce: forward P/E 10.5 vs trailing 19 — market underpricing an AI-driven earnings ramp; 51% analyst upside.",
         ["high debt (D/E 124); mega-cap, needs AI monetization"]),
 "CHWY": ("Chewy: online pet retail (you know it); earnings up 53%, fwd P/E 11, 64% ROE. Recurring auto-ship revenue.",
          ["thin margins (~2%); high leverage (D/E 123)"]),
 "HLLY": ("Holley: performance auto parts; PEG 0.23, 90% analyst upside — deep-value cyclical rebound.",
          ["micro-cap $295M, high debt (D/E 128), $2.45 stock — speculative"]),
 "CYD": ("China Yuchai: diesel/engine maker; earnings up 109%, fwd P/E 12, clean books.",
         ["China ADR — geopolitical/regulatory & disclosure risk", "cyclical engine demand"]),
 "ONON": ("On Holding: the Swiss running shoe you've seen everywhere; 81% earnings growth, brand momentum, 46% upside.",
          ["footwear is fashion-cyclical; premium absolute multiple"]),
 "VISN": ("Vistance Networks — the rebranded CommScope (renamed Jan 2026) after asset sales (RUCKUS→Belden, connectivity→Amphenol) and a $10 special dividend. Networking-infrastructure turnaround.",
          ["screen metrics distorted by one-off asset-sale gains — margins/growth not representative of the ongoing business", "post-restructuring; thin analyst coverage (Hold, ~$15 target)"]),
 "ALGN": ("Align Technology (Invisalign — a product you know); earnings up 24%, fwd P/E 15, clean balance sheet.",
          ["consumer-discretionary dental spend; China exposure"]),
 "EDU": ("New Oriental: Chinese education rebuilding post-crackdown; 60% earnings growth, fwd P/E 11.",
         ["China ADR — regulatory risk (survived the 2021 tutoring ban)"]),
 "ACM": ("AECOM infrastructure engineering; strong-buy, 51% upside, earnings up 28% — rides public infra spending.",
         ["high debt (D/E 135); slow top-line growth"]),
}

def fmtpe(v): return f"{v:.1f}" if isinstance(v,(int,float)) and v==v else "—"
def fmtpeg(v): return f"{v:.2f}" if isinstance(v,(int,float)) and v==v and v>0 else "—"
def fmtpct(v): return f"{v*100:.0f}%" if isinstance(v,(int,float)) and v==v else "—"
def fmtcap(v):
    if not isinstance(v,(int,float)) or v!=v: return "—"
    if v>=1e12: return f"${v/1e12:.1f}T"
    if v>=1e9: return f"${v/1e9:.1f}B"
    if v>=1e6: return f"${v/1e6:.0f}M"
    return f"${v:.0f}"
def upside(r):
    p,t=r.get("price"),r.get("target")
    if isinstance(p,(int,float)) and isinstance(t,(int,float)) and p:
        return f"+{(t-p)/p*100:.0f}%"
    return "—"

CAT_CLASS={"Fast Grower":"fast","Stalwart":"stalwart","Turnaround":"turn","Cyclical":"cyc",
           "Moderate Grower":"mod","Slow Grower":"slow"}

def badge_class(label):
    l=(label or "").lower()
    if "bull" in l: return "bull"
    if "bear" in l: return "bear"
    if "no discussion" in l or "little" in l: return "none"
    return "neut"

def meter_class(score, label):
    if badge_class(label)=="none": return "none"
    if score>=67: return "bull"
    if score<=33: return "bear"
    return "neut"

def senti_block(src_class, src_label, dot_class, data):
    if not data:
        data={"label":"No data","score":50,"summary":"Sentiment not yet gathered."}
    label=data.get("label","—"); score=data.get("score",50); summ=data.get("summary","")
    bc=badge_class(label); mc=meter_class(score,label)
    width = 0 if bc=="none" else max(4,min(100,int(score)))
    scoretxt = "n/a" if bc=="none" else f"{int(score)}/100"
    return f'''<div class="sblock">
        <div class="shead">
          <span class="ssrc"><span class="dot {dot_class}"></span>{src_label}</span>
          <span class="sbadge {bc}">{html.escape(label)} · {scoretxt}</span>
        </div>
        <div class="meter"><i class="{mc}" style="width:{width}%"></i></div>
        <div class="ssum">{html.escape(summ)}</div>
      </div>'''

def card(r, rank, mode):
    tk=r["ticker"]; base=tk.replace(".TO","")
    thesis,flags=THESIS.get(base,("",[]))
    cat=r["category"]; catc=CAT_CLASS.get(cat,"mod")
    website=r.get("website","")
    flaghtml="".join(f'<li>{html.escape(fl)}</li>' for fl in flags)
    div = '<span class="chip div">diversifier</span>' if r.get("diversification_note") else ''
    if mode=="long":
        headline=f'<div class="headline"><span class="hlnum">{fmtpeg(r.get("peg"))}</span><span class="hllab">PEG</span></div>'
    else:
        headline=f'<div class="headline"><span class="hlnum">{upside(r)}</span><span class="hllab">analyst upside</span></div>'
    rec=r.get("rec","") or "none"
    recmap={"strong_buy":"Strong Buy","buy":"Buy","hold":"Hold","none":"—","underperform":"Underperform","sell":"Sell"}
    sent=SENT.get(base,{})
    reddit_html=senti_block("reddit","Reddit","reddit",sent.get("reddit"))
    msm_html=senti_block("msm","Mainstream Media","msm",sent.get("msm"))
    return f'''
    <article class="card">
      <div class="cardtop">
        <div class="rank">{rank:02d}</div>
        <div class="idwrap">
          <a class="tk" href="{r.get("yahoo","#")}" target="_blank" rel="noopener">{tk}</a>
          <span class="nm">{html.escape(r["name"].split(" - ")[0].split(" Common")[0].split(" Class")[0][:44])}</span>
        </div>
        {headline}
      </div>
      <div class="chips">
        <span class="chip cat {catc}">{cat}</span>{div}
        <span class="chip sec">{html.escape(r["sector"])}</span>
      </div>
      <p class="thesis">{html.escape(thesis)}</p>
      <div class="metrics">
        <div><span>P/E</span><b>{fmtpe(r.get("pe"))}</b></div>
        <div><span>Fwd P/E</span><b>{fmtpe(r.get("fpe"))}</b></div>
        <div><span>PEG</span><b>{fmtpeg(r.get("peg"))}</b></div>
        <div><span>Rev grw</span><b>{fmtpct(r.get("growth"))}</b></div>
        <div><span>Margin</span><b>{fmtpct(r.get("margin"))}</b></div>
        <div><span>ROE</span><b>{fmtpct(r.get("roe"))}</b></div>
        <div><span>Mkt cap</span><b>{fmtcap(r.get("mcap"))}</b></div>
        <div><span>Analyst</span><b>{recmap.get(rec,rec)}</b></div>
      </div>
      <div class="senti">{reddit_html}{msm_html}</div>
      {'<ul class="flags">'+flaghtml+'</ul>' if flaghtml else ''}
      <div class="links">{'<a href="'+website+'" target="_blank" rel="noopener">↗ company</a>' if website else ''}
        <a href="{r.get("yahoo","#")}" target="_blank" rel="noopener">↗ financials</a></div>
    </article>'''

long_cards="".join(card(r,i+1,"long") for i,r in enumerate(d["long_term"]))
short_cards="".join(card(r,i+1,"short") for i,r in enumerate(d["short_term"]))

TEMPLATE = open("artifact_template.html", encoding="utf-8").read()
body = (TEMPLATE
       .replace("{{UNIVERSE}}", f"{d['universe_size']:,}")
       .replace("{{UPDATED}}", html.escape(UPDATED))
       .replace("{{LONG_CARDS}}", long_cards)
       .replace("{{SHORT_CARDS}}", short_cards))

# The template is a bare <style>+body fragment so it stays Artifact-compatible
# (the Artifact harness injects <head>). For standalone hosting on the website we
# must supply a full document with an explicit UTF-8 charset, or the browser
# mis-decodes em-dashes, arrows, middots, ≤ etc.
out = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Peter Lynch Portfolio — Gal Av-Gay</title>
</head>
<body>
{body}
</body>
</html>
'''
open("lynch_report.html", "w", encoding="utf-8").write(out)
print("wrote lynch_report.html", len(out), "bytes; sentiment tickers:", len(SENT))
