"""
gen_options_site.py — static page for the options tool.

Writes options.html, matching the visual language of the existing sentiment
pages (serif body, Arial headings, navy rules, 1100px column) so the two sit
together on galavgay.com without looking like different sites.

Everything is embedded: the page fetches nothing at runtime, which is what
makes it safe to serve from S3 behind CloudFront with no backend. The position
calculator runs client-side in JavaScript against the embedded snapshot — it is
pure arithmetic on already-fetched data, so it needs no server.

The four spec caveats are reproduced verbatim in the page text. They are not
decoration: a page that ranks options without them reads as a trade-finder,
which is exactly what this tool must not be mistaken for.

Python 3.9 compatible.
"""
import datetime as dt
import html
import json
import os
import sys

import options_config as cfg
import sim_config as scfg

try:
    import settle_positions
except ImportError:  # pragma: no cover
    settle_positions = None


def _log(msg):
    print(msg)
    sys.stdout.flush()


def _esc(text):
    return html.escape(str(text if text is not None else ""))


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except ValueError:
        return default


def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


CSS = """
  :root{
    --bg:#fff;--ink:#333;--body:#696969;--faint:#8a8a8a;--navy:#00008b;--link:#597ccd;
    --link-hover:#00008b;--hair:#e6e6e6;--hair2:#f0f0f0;--panel:#fafafb;
    --pos:#1a7f37;--neg:#c0392b;--neut:#9a7a12;--warn:#b7791f;--warnbg:#fffbf0;
    --serif:"Times New Roman",Times,Georgia,serif;--head:Arial,Helvetica,sans-serif;
    --mono:"Cascadia Mono",ui-monospace,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--serif);
       font-size:16px;line-height:1.5;padding:3px;}
  .wrap{max-width:1100px;margin:0 auto;padding:0 24px 80px;}
  a{color:var(--link);text-decoration:none;} a:hover{text-decoration:underline;}
  .topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:16px 0 0;
          font-size:14px;font-family:var(--head);}
  .topbar a{font-weight:bold;}
  h1{font-family:var(--head);color:#000;font-size:30px;font-weight:bold;
     border-bottom:3px solid var(--navy);padding:0 0 6px;margin:18px 0 14px;text-align:center;}
  h2{font-family:var(--head);font-size:20px;color:#000;margin:34px 0 10px;
     border-bottom:1px solid var(--hair);padding-bottom:5px;}
  h3{font-family:var(--head);font-size:16px;color:#000;margin:20px 0 8px;}
  .sub{color:var(--body);font-family:var(--head);font-size:14px;margin:0 0 14px;text-align:center;}
  .intro{font-size:16px;color:var(--body);margin:0 auto 12px;line-height:1.6;text-align:justify;}
  .intro b{color:var(--ink);}
  .panel{background:var(--panel);border:1px solid var(--hair);border-left:3px solid var(--navy);
         border-radius:6px;padding:12px 16px;font-size:14.5px;margin:14px 0;color:var(--body);}
  .warnpanel{background:var(--warnbg);border:1px solid #f0e0c0;border-left:3px solid var(--warn);
             border-radius:6px;padding:12px 16px;font-size:14.5px;margin:14px 0;color:#7a5a1a;}
  .stale{background:#fff4f4;border-left-color:var(--neg);color:#8a2b2b;}
  table{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0;}
  thead th{background:#f4f5f8;font-family:var(--head);font-weight:bold;text-align:left;
    padding:7px 9px;border-bottom:2px solid var(--hair);white-space:nowrap;cursor:pointer;
    user-select:none;}
  td{padding:6px 9px;border-bottom:1px solid var(--hair2);}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);
                white-space:nowrap;}
  tbody tr:hover{background:#fafbfd;}
  .pos{color:var(--pos);} .neg{color:var(--neg);} .neut{color:var(--neut);}
  .mono{font-family:var(--mono);font-size:12.5px;}
  .faint{color:var(--faint);font-size:13px;}
  .tabs{display:flex;gap:6px;margin:18px 0 0;font-family:var(--head);font-size:14px;
        border-bottom:2px solid var(--hair);flex-wrap:wrap;}
  .tab{padding:8px 16px;cursor:pointer;border:1px solid transparent;border-bottom:none;
       border-radius:6px 6px 0 0;color:var(--body);}
  .tab.on{background:#fff;border-color:var(--hair);color:#000;font-weight:bold;
          margin-bottom:-2px;border-bottom:2px solid #fff;}
  .tabpane{display:none;} .tabpane.on{display:block;}
  .grid{overflow-x:auto;}
  .grid table{font-size:12.5px;}
  .grid td.c{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;
             white-space:nowrap;padding:4px 7px;}
  .calc{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end;margin:12px 0;
        font-family:var(--head);font-size:14px;}
  .calc label{display:flex;flex-direction:column;gap:3px;color:var(--body);font-size:13px;}
  .calc select,.calc input{font-family:var(--head);font-size:14px;padding:6px 9px;
        border:1px solid var(--hair);border-radius:6px;}
  .calc select{min-width:280px;}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 18px;font-size:14px;margin:8px 0;}
  .kv .k{color:var(--body);} .kv .v{font-family:var(--mono);text-align:right;}
  .empty{color:var(--faint);font-style:italic;padding:14px 0;}
  .cav{font-size:13.5px;color:var(--body);}
  .cav li{margin:7px 0;}
  .gate{font-family:var(--mono);font-size:12.5px;}
  .badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11.5px;
         font-family:var(--head);border:1px solid var(--hair);color:var(--body);}
  .badge.ok{background:#eefaf0;border-color:#bfe3c8;color:var(--pos);}
  .badge.no{background:#fdf0ee;border-color:#f0c8c0;color:var(--neg);}
"""


def _rank_table(contracts, limit=25):
    """The least-friction ranking table."""
    ranked = sorted([c for c in contracts if c.get("drag_7d_pct") is not None],
                    key=lambda c: c["drag_7d_pct"])[:limit]
    if not ranked:
        return '<p class="empty">No contracts survived the filters this run.</p>'

    rows = []
    for c in ranked:
        rows.append(
            "<tr>"
            "<td>{t}</td><td>{ty}</td><td class='mono'>{e}</td>"
            "<td class='num'>{k:.2f}</td><td class='num'>{d}</td>"
            "<td class='num'>{a:.2f}</td><td class='num'>{rt:.2f}</td>"
            "<td class='num'>{th:.2f}</td><td class='num'><b>{dr:.2f}</b></td>"
            "<td class='num'>{p:.3f}</td><td class='num'>{sd:.2f}</td>"
            "</tr>".format(
                t=_esc(c["ticker"]), ty=_esc(c["option_type"]), e=_esc(c["expiry"]),
                k=c["strike"], d=c["dte"], a=c["ask"],
                rt=100.0 * c["round_trip_cost_pct"],
                th=c["theta_pct_of_premium_per_day"], dr=c["drag_7d_pct"],
                p=c["p_itm"] if c["p_itm"] == c["p_itm"] else 0.0,
                sd=c["breakeven_in_sds"] if c["breakeven_in_sds"] is not None else 0.0))

    return (
        "<table><thead><tr>"
        "<th>Ticker</th><th>Type</th><th>Expiry</th><th class='num'>Strike</th>"
        "<th class='num'>DTE</th><th class='num'>Ask</th>"
        "<th class='num'>Round&nbsp;trip&nbsp;%</th><th class='num'>Theta&nbsp;%/day</th>"
        "<th class='num'>Drag&nbsp;7d&nbsp;%</th><th class='num'>P(ITM)</th>"
        "<th class='num'>BE&nbsp;in&nbsp;SDs</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _summary_html(meta):
    """Universe medians vs the cheap-contract cohort."""
    summary = meta.get("summary") or {}
    universe = summary.get("universe")
    cheap = summary.get("cheap")

    if not universe:
        return ""

    def fmt(value, places=2):
        if value is None:
            return "&mdash;"
        return "{0:.{1}f}".format(value, places)

    rows = [
        ("Median round-trip spread cost", "round_trip_cost_pct", 2, "%"),
        ("Median theta burn per day", "theta_pct_per_day", 2, "% of premium"),
        ("Median implied P(ITM)", "p_itm", 3, ""),
        ("Median breakeven move", "breakeven_move_pct", 2, "%"),
        ("Median breakeven in SDs", "breakeven_in_sds", 2, ""),
    ]

    body = []
    for label, key, places, unit in rows:
        u = fmt(universe.get(key), places)
        c = fmt(cheap.get(key), places) if cheap else "&mdash;"
        body.append(
            "<tr><td>{0}</td><td class='num'>{1} {2}</td>"
            "<td class='num'>{3} {2}</td></tr>".format(label, u, unit, c))

    cheap_n = cheap.get("n") if cheap else 0
    note = ""
    if not cheap_n:
        note = ("<p class='faint'>No contract priced at or below ${0:.2f} survived the "
                "liquidity filters this run. That is itself the finding: at those prices "
                "the spread is usually a large fraction of the premium, and the open "
                "interest, volume, or nonzero-bid tests remove them."
                .format(cfg.CHEAP_CONTRACT_MAX_ASK))

    return (
        "<table><thead><tr><th>Measure</th>"
        "<th class='num'>All screened (n={0})</th>"
        "<th class='num'>&le; ${1:.2f} (n={2})</th></tr></thead><tbody>{3}</tbody></table>{4}"
        .format(universe.get("n", 0), cfg.CHEAP_CONTRACT_MAX_ASK, cheap_n,
                "".join(body), note))


def _term_structure_html(meta):
    term = meta.get("term_structure") or {}
    if not term:
        return '<p class="empty">No near-the-money contracts this run.</p>'

    all_dtes = sorted(set(int(d) for per in term.values() for d in per))[:12]
    head = "".join("<th class='num'>{0}d</th>".format(d) for d in all_dtes)

    rows = []
    for ticker in sorted(term):
        cells = []
        for d in all_dtes:
            iv = term[ticker].get(str(d))
            cells.append("<td class='num'>{0}</td>".format(
                "{0:.3f}".format(iv) if iv else "&mdash;"))
        rows.append("<tr><td><b>{0}</b></td>{1}</tr>".format(
            _esc(ticker), "".join(cells)))

    return ("<table><thead><tr><th>Ticker</th>{0}</tr></thead><tbody>{1}</tbody></table>"
            .format(head, "".join(rows)))


def _ledger_html(ledger, positions):
    """Simulation ledger: decisions, open positions, settled results."""
    parts = []

    settled = [p for p in positions if p.get("status") == "settled"]
    open_pos = [p for p in positions if p.get("status") == "open"]

    # Cumulative performance
    if settled:
        total_pl = sum(p.get("pl_dollars", 0.0) for p in settled)
        deployed = sum(p.get("entry_outlay", 0.0) for p in settled)
        wins = len([p for p in settled if p.get("pl_dollars", 0) > 0])
        correct = len([p for p in settled if p.get("direction_correct")])
        right_lost = len([p for p in settled
                          if p.get("direction_correct") and p.get("pl_dollars", 0) <= 0])

        cls = "pos" if total_pl > 0 else "neg"
        parts.append(
            "<div class='kv'>"
            "<div class='k'>Settled positions</div><div class='v'>{0}</div>"
            "<div class='k'>Capital deployed</div><div class='v'>${1:,.2f}</div>"
            "<div class='k'>Total P/L</div><div class='v {2}'>${3:+,.2f}</div>"
            "<div class='k'>Win rate</div><div class='v'>{4:.1f}%</div>"
            "<div class='k'>Direction accuracy</div><div class='v'>{5:.1f}%</div>"
            "</div>".format(len(settled), deployed, cls, total_pl,
                            100.0 * wins / len(settled),
                            100.0 * correct / len(settled)))

        if right_lost:
            parts.append(
                "<div class='panel'><b>{0} position(s) were directionally right and "
                "still lost money.</b> That gap is the premium: the underlying cleared "
                "the strike but not the cost of entry. It is the most informative number "
                "on this page &mdash; it separates <i>the signal is wrong</i> from "
                "<i>the signal is too weak to overcome friction</i>, which have entirely "
                "different remedies.</div>".format(right_lost))

        if len(settled) < 20:
            parts.append(
                "<div class='warnpanel'>Only {0} position(s) have settled. That is far too "
                "few to support any conclusion about whether the selection rule works; "
                "results at this sample size are dominated by variance.</div>"
                .format(len(settled)))

        rows = []
        for p in sorted(settled, key=lambda x: x.get("settle_date", ""), reverse=True):
            pl = p.get("pl_dollars", 0.0)
            rows.append(
                "<tr><td class='mono'>{0}</td><td>{1}</td><td>{2}</td>"
                "<td class='num'>{3:.2f}</td><td class='num'>{4:.2f}</td>"
                "<td class='num {5}'>${6:+,.2f}</td><td class='num'>{7:+.2f}%</td>"
                "<td>{8}</td></tr>".format(
                    _esc(p.get("settle_date")), _esc(p["ticker"]),
                    _esc(p["option_type"]), p["strike"], p.get("settle_close", 0.0),
                    "pos" if pl > 0 else "neg", pl,
                    p.get("underlying_move_pct", 0.0),
                    "<span class='badge ok'>right</span>" if p.get("direction_correct")
                    else "<span class='badge no'>wrong</span>"))
        parts.append(
            "<h3>Settled</h3><table><thead><tr><th>Settled</th><th>Ticker</th>"
            "<th>Type</th><th class='num'>Strike</th><th class='num'>Close</th>"
            "<th class='num'>P/L</th><th class='num'>Underlying</th>"
            "<th>Direction</th></tr></thead><tbody>{0}</tbody></table>"
            .format("".join(rows)))
    else:
        parts.append(
            "<div class='panel'>No positions have settled yet. Performance attribution "
            "becomes meaningful after roughly 20&ndash;30 settled positions.</div>")

    if open_pos:
        rows = []
        for p in open_pos:
            unreal = p.get("unrealised_pl")
            if unreal is None:
                mark = "<td class='num faint'>no quote</td>"
            else:
                mark = "<td class='num {0}'>${1:+,.2f}</td>".format(
                    "pos" if unreal > 0 else "neg", unreal)
            rows.append(
                "<tr><td class='mono'>{0}</td><td>{1}</td><td>{2}</td>"
                "<td class='num'>{3:.2f}</td><td class='mono'>{4}</td>{5}</tr>".format(
                    _esc(p.get("entry_date")), _esc(p["ticker"]),
                    _esc(p["option_type"]), p["strike"], _esc(p["expiry"]), mark))
        parts.append(
            "<h3>Open</h3><table><thead><tr><th>Entered</th><th>Ticker</th><th>Type</th>"
            "<th class='num'>Strike</th><th>Expiry</th><th class='num'>Unrealised</th>"
            "</tr></thead><tbody>{0}</tbody></table>"
            "<p class='faint'>Marks are at the bid and are indicative &mdash; nothing has "
            "been closed, and the exit spread has not been paid.</p>".format("".join(rows)))

    # Decision log, including no-trade days.
    recent = list(reversed(ledger))[:40]
    if recent:
        rows = []
        for d in recent:
            detail = d.get("detail") or {}
            outcome = d.get("outcome")
            if outcome == "opened":
                what = "<span class='badge ok'>opened</span> " + _esc(
                    ", ".join(detail.get("positions", [])))
            else:
                reason = detail.get("reason", "")
                stage = detail.get("stage")
                what = "<span class='badge'>no trade</span> <span class='gate'>{0}{1}</span>".format(
                    _esc(reason), " (stage {0})".format(stage) if stage else "")
            rows.append("<tr><td class='mono'>{0}</td><td>{1}</td><td class='faint'>{2}</td></tr>"
                        .format(_esc(d.get("run_date")), what,
                                _esc(d.get("quote_session") or "")))
        parts.append(
            "<h3>Decision log</h3>"
            "<p class='faint'>Every run is recorded, including days with no trade. "
            "No-trade days are the rule working, not a failure &mdash; the gate that "
            "stopped each one is shown.</p>"
            "<table><thead><tr><th>Date</th><th>Outcome</th><th>Quote session</th>"
            "</tr></thead><tbody>{0}</tbody></table>".format("".join(rows)))

    return "".join(parts)


def _cascade_explainer():
    return """
  <div class='panel'>
  <b>The selection rule.</b> Four gates, in order. A ticker must clear every one
  before a position is simulated.
  <ol>
    <li><b>Intersect</b> &mdash; the ticker is in the options universe <i>and</i>
        has sentiment coverage.</li>
    <li><b>Predictiveness</b> &mdash; news historically <i>leads</i>
        regular-session price for this name, on enough observations for the
        correlation to mean something.</li>
    <li><b>Unreflected</b> &mdash; sentiment has moved recently and price has
        <i>not yet</i> followed. If price already moved, the information is
        priced in and there is nothing left to capture.</li>
    <li><b>Vehicle</b> &mdash; a contract exists that expresses the view without
        excessive friction, and is affordable at the simulated budget.</li>
  </ol>
  Most days nothing passes. That is intended: a rule that always finds a trade
  is not a rule.
  </div>
  """


def _gate_thresholds():
    return (
        "<div class='kv'>"
        "<div class='k'>Min sentiment-hours (stage 2)</div><div class='v'>{0}</div>"
        "<div class='k'>Min |lead correlation|</div><div class='v'>{1}</div>"
        "<div class='k'>Min sentiment change (stage 3)</div><div class='v'>{2}</div>"
        "<div class='k'>Max price move already made</div><div class='v'>{3}%</div>"
        "<div class='k'>Max breakeven (stage 4)</div><div class='v'>{4} SDs</div>"
        "<div class='k'>Simulated budget per position</div><div class='v'>${5:,.0f}</div>"
        "</div>".format(scfg.MIN_SENT_HOURS, scfg.MIN_LEAD_CORR,
                        scfg.MIN_SENT_CHANGE, scfg.MAX_PRICE_MOVE_PCT,
                        scfg.MAX_BREAKEVEN_SDS, scfg.SIM_BUDGET))


# NOTE: this template uses @@NAME@@ placeholders and plain string replacement
# rather than %-formatting. The JavaScript is full of literal '%' characters
# (percentage labels), and %-formatting would try to interpret every one of them
# as a conversion specifier. Escaping them individually works until the next
# edit forgets one; this avoids the class of bug entirely.
CALC_JS = """
(function(){
  var C = window.__CONTRACTS__ || [];
  var COMM = @@COMM@@, REGFEE = @@REG@@, MULT = @@MULT@@;
  var sel = document.getElementById('ctr'),
      bud = document.getElementById('bud'),
      ivd = document.getElementById('ivd'),
      out = document.getElementById('calcout');
  if(!sel) return;

  C.sort(function(a,b){
    return a.ticker.localeCompare(b.ticker) || a.expiry.localeCompare(b.expiry)
        || a.option_type.localeCompare(b.option_type) || a.strike-b.strike; });
  C.forEach(function(c,i){
    var o=document.createElement('option'); o.value=i;
    o.textContent = c.ticker+'  '+c.option_type.toUpperCase()+'  $'+c.strike.toFixed(2)
        +'  exp '+c.expiry+'  ('+c.dte+'d)  ask $'+c.ask.toFixed(2);
    sel.appendChild(o); });

  function ncdf(x){
    // Abramowitz-Stegun 7.1.26 approximation to erf; ~1e-7 absolute error,
    // which is far below the precision of the quotes this operates on.
    var s=x<0?-1:1, ax=Math.abs(x)/Math.SQRT2;
    var t=1/(1+0.3275911*ax);
    var y=1-(((((1.061405429*t-1.453152027)*t)+1.421413741)*t-0.284496736)*t
            +0.254829592)*t*Math.exp(-ax*ax);
    return 0.5*(1+s*y);
  }
  function bsPrice(S,K,T,r,q,sig,isCall){
    if(!(S>0&&K>0&&sig>0&&T>0)) return NaN;
    var vt=sig*Math.sqrt(T);
    var d1=(Math.log(S/K)+(r-q+0.5*sig*sig)*T)/vt, d2=d1-vt;
    var dq=Math.exp(-q*T), dr=Math.exp(-r*T);
    return isCall ? S*dq*ncdf(d1)-K*dr*ncdf(d2)
                  : K*dr*ncdf(-d2)-S*dq*ncdf(-d1);
  }
  function money(v){
    var s=(v<0?'-':'')+'$'+Math.abs(v).toLocaleString('en-US',
      {minimumFractionDigits:2,maximumFractionDigits:2});
    return s;
  }

  function render(){
    var c=C[parseInt(sel.value,10)]; if(!c) return;
    var budget=parseFloat(bud.value)||0;
    var drop=(parseFloat(ivd.value)||0)/100;
    var cost=c.ask*MULT;
    var n=Math.floor(budget/cost);
    var deployed=n*cost, residual=budget-deployed;
    var oneWay=COMM+REGFEE, entryFees=n*oneWay, exitFees=n*oneWay;
    var outlay=deployed+entryFees;
    var spreadCost=(c.ask-c.bid)*n*MULT;
    var roundTrip=spreadCost+entryFees+exitFees;

    var h='';
    if(n<1){
      h='<div class="warnpanel">A budget of '+money(budget)+' does not cover a single '
       +'contract at '+money(cost)+'. Contracts are integers carrying a '+MULT+'x '
       +'multiplier; there is no partial position.</div>';
      out.innerHTML=h; return;
    }

    h+='<div class="kv">'
      +'<div class="k">Contracts</div><div class="v">'+n+'</div>'
      +'<div class="k">Capital deployed</div><div class="v">'+money(deployed)+'</div>'
      +'<div class="k">Residual (idle cash)</div><div class="v">'+money(residual)
      +'  ('+(budget?(100*residual/budget).toFixed(1):0)+'% of budget)</div>'
      +'<div class="k">Entry fees</div><div class="v">'+money(entryFees)+'</div>'
      +'<div class="k">Total entry outlay</div><div class="v">'+money(outlay)+'</div>'
      +'<div class="k">Guaranteed round-trip cost</div><div class="v">'+money(roundTrip)
      +'  ('+(deployed?(100*roundTrip/deployed).toFixed(2):0)+'% of deployed)</div>'
      +'<div class="k">Breakeven underlying</div><div class="v">$'
      +c.breakeven_price.toFixed(2)+'</div>'
      +'</div>';

    if(residual/budget > 0.15){
      h+='<div class="warnpanel">'+money(residual)+' of a '+money(budget)+' budget '
       +'sits idle &mdash; '+(100*residual/budget).toFixed(0)+'% of the stated amount '
       +'doing nothing. Returns quoted on deployed capital and on total budget are '
       +'materially different numbers here.</div>';
    }

    // Payoff grid, Black-Scholes re-priced, calibrated so entry at the quoted
    // ask does not book an instant loss at day 0.
    var isCall=c.option_type==='call';
    var r=@@RATE@@, q=@@DIV@@;
    function grid(iv,title){
      var theoEntry=bsPrice(c.spot,c.strike,Math.max(c.dte,0.5)/365,r,q,iv,isCall);
      var cal=(theoEntry>1e-9&&c.ask>0)?(c.ask/theoEntry):1;
      var sd=c.sd_move_pct||0.1;
      var lo=Math.max(c.spot*(1-2*sd),0.01), hi=c.spot*(1+2*sd);
      var cols=[],nc=Math.min(9,Math.max(2,c.dte+1));
      for(var i=0;i<nc;i++) cols.push(Math.round(c.dte*i/(nc-1)));
      cols=cols.filter(function(v,i,a){return a.indexOf(v)===i;});
      var th='<table><thead><tr><th class="num">Underlying</th>';
      cols.forEach(function(d){ th+='<th class="num">d+'+d+'</th>'; });
      th+='</tr></thead><tbody>';
      for(var k=0;k<13;k++){
        var S=lo+(hi-lo)*k/12;
        th+='<tr><td class="c">'+S.toFixed(2)+'</td>';
        cols.forEach(function(d){
          var rem=c.dte-d, val;
          if(rem<=0){ val=isCall?Math.max(S-c.strike,0):Math.max(c.strike-S,0); }
          else { val=cal*bsPrice(S,c.strike,rem/365,r,q,iv,isCall); }
          var pl=n*val*MULT-outlay-exitFees;
          var cls=pl>0?'pos':(pl<0?'neg':'');
          th+='<td class="c '+cls+'">'+Math.round(pl).toLocaleString('en-US')+'</td>';
        });
        th+='</tr>';
      }
      th+='</tbody></table>';
      return '<h3>'+title+'</h3><div class="grid">'+th+'</div>';
    }

    h+=grid(c.iv,'Payoff grid &mdash; IV unchanged at '+(100*c.iv).toFixed(1)+'%');
    if(drop>0){
      h+=grid(c.iv*(1-drop),'Payoff grid &mdash; IV crushed to '
        +(100*c.iv*(1-drop)).toFixed(1)+'% (&minus;'+(100*drop).toFixed(0)+'%)');
      h+='<p class="faint">Holding IV constant is the optimistic case. The difference '
       +'between the two grids at any cell is what a volatility crush costs at that '
       +'price level &mdash; independent of being directionally right. Post-earnings '
       +'crush routinely costs more than the directional move gains.</p>';
    }
    h+='<p class="faint">Cells are dollar P/L on the full position, net of entry and '
     +'exit fees, Black-Scholes re-priced. Only the final column is intrinsic value at '
     +'expiry.</p>';
    out.innerHTML=h;
  }
  sel.addEventListener('change',render);
  bud.addEventListener('input',render);
  ivd.addEventListener('input',render);
  render();
})();
"""


TABS_JS = """
(function(){
  var tabs=document.querySelectorAll('.tab');
  tabs.forEach(function(t){
    t.addEventListener('click',function(){
      document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on');});
      document.querySelectorAll('.tabpane').forEach(function(x){x.classList.remove('on');});
      t.classList.add('on');
      var p=document.getElementById(t.dataset.pane);
      if(p) p.classList.add('on');
    });
  });
})();
"""


def build(screen_payload=None, ledger=None, positions=None):
    """Render the full page to a string."""
    if screen_payload is None:
        screen_payload = _load_json(cfg.LATEST_JSON, {"meta": {}, "contracts": []})
    if ledger is None:
        ledger = _load_jsonl(cfg.SIM_LEDGER)
    if positions is None:
        positions = _load_jsonl(cfg.SIM_POSITIONS)

    meta = screen_payload.get("meta", {})
    contracts = screen_payload.get("contracts", [])

    fetched = meta.get("fetched_at", "never")
    session = meta.get("quote_session", "unknown")
    generated = dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")

    # Quote-freshness banner. This is not cosmetic: every figure on the page is
    # derived from these quotes, and outside the regular session the spreads
    # that drive the cost metrics are unrepresentative.
    if session == "regular":
        fresh = ("<div class='panel'>Quotes pulled during the <b>regular session</b> "
                 "({0}). Spreads are representative.</div>".format(_esc(fetched)))
    else:
        fresh = ("<div class='warnpanel stale'><b>Quotes were pulled outside the regular "
                 "session</b> (&lsquo;{0}&rsquo;, {1}). Spreads outside regular hours are "
                 "unrepresentatively wide and last prices are stale, so every cost figure "
                 "below is indicative only. The simulation does not open positions on "
                 "these quotes.</div>".format(_esc(session), _esc(fetched)))

    # Slim the embedded contract data to only what the calculator needs, so the
    # page stays small enough to serve comfortably.
    slim = [{
        "ticker": c["ticker"], "option_type": c["option_type"], "strike": c["strike"],
        "expiry": c["expiry"], "dte": c["dte"], "bid": c["bid"], "ask": c["ask"],
        "spot": c["spot"], "iv": c["iv"],
        "sd_move_pct": c.get("sd_move_pct") or 0.1,
        "breakeven_price": c["breakeven_price"],
    } for c in sorted(contracts, key=lambda x: x.get("drag_7d_pct") or 9e9)[:600]]

    calc_js = CALC_JS
    for placeholder, value in (
            ("@@COMM@@", repr(float(cfg.COMMISSION_PER_CONTRACT))),
            ("@@REG@@", repr(float(cfg.REG_FEE_PER_CONTRACT))),
            ("@@MULT@@", str(int(cfg.CONTRACT_MULTIPLIER))),
            ("@@RATE@@", repr(float(cfg.RISK_FREE_RATE))),
            ("@@DIV@@", repr(float(cfg.DIVIDEND_YIELD)))):
        calc_js = calc_js.replace(placeholder, value)

    tickers_ok = ", ".join(meta.get("tickers_ok", []))
    failed = meta.get("tickers_failed") or []

    page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Options Cost Screener</title>
<style>%(css)s</style>
</head><body><div class="wrap">

<nav class="topbar">
  <a class="home" href="../index.html">&larr; galavgay.com</a>
  <a class="home" href="../projects.html">Projects</a>
  <a class="home" href="../sentiment/index.html">Sentiment vs Price</a>
</nav>

<h1>Options Cost Screener</h1>
<p class="sub">Structural cost of holding listed equity options &mdash; and a
simulated ledger driven by the sentiment study</p>

<p class="intro"><b>This is not a trade-finder.</b> It does not identify mispriced or
underpriced options, and nothing here should be read as saying it does. Implied
volatility is the market's own volatility estimate, and the probability of finishing in
the money is derived from that same IV &mdash; so comparing an option's price against its
own implied probability is circular, and would show every contract as fairly priced.
What this measures is <b>cost</b>: the guaranteed, quantifiable drag paid on entry and
over the holding period. Its use is to find the least-costly vehicle for a directional
view already held on other grounds. It does not generate the view.</p>

%(fresh)s

<div class="tabs">
  <div class="tab on" data-pane="p-screen">Screener</div>
  <div class="tab" data-pane="p-calc">Position calculator</div>
  <div class="tab" data-pane="p-sim">Simulation</div>
  <div class="tab" data-pane="p-about">Method &amp; caveats</div>
</div>

<div class="tabpane on" id="p-screen">
  <h2>Lowest-drag contracts</h2>
  <p class="intro">Ranked ascending by <b>drag over %(horizon)d days</b> = round-trip
  spread cost + theta, as a percentage of premium paid. This is a
  <b>least-friction ranking, not an expected-return ranking</b>. A contract at the top
  is not a good trade; it is the least expensive way to hold a position.</p>
  %(rank)s

  <h2>Universe medians, and the cheap-contract cohort</h2>
  <p class="intro">Contracts that look cheapest in dollar terms are typically the worst
  on every measure of cost. The comparison is shown rather than left as folklore.</p>
  %(summary)s

  <h2>Term structure &mdash; mean ATM implied volatility by DTE</h2>
  <p class="intro">An <b>inverted</b> term structure &mdash; near-dated IV above
  far-dated &mdash; usually means an event is priced into the front month. That is the
  classic decay setup: the elevated premium collapses once the event passes, and the
  holder pays for that collapse even when directionally right.</p>
  %(term)s
</div>

<div class="tabpane" id="p-calc">
  <h2>Dollar-denominated position calculator</h2>
  <p class="intro">Percentages obscure how small the absolute stakes can be relative to
  fixed costs. Pick a contract and a budget; everything below is in dollars, including
  the cash left idle because contracts are integers, and commissions charged on both
  entry and exit.</p>
  <div class="calc">
    <label>Contract<select id="ctr"></select></label>
    <label>Budget ($)<input id="bud" type="number" value="1000" min="0" step="100"></label>
    <label>IV crush scenario (%%)<input id="ivd" type="number" value="20" min="0"
      max="90" step="5"></label>
  </div>
  <div id="calcout"></div>
</div>

<div class="tabpane" id="p-sim">
  <h2>Sentiment-driven simulation</h2>
  <p class="intro">Positions are simulated, never real. The point is not the P/L &mdash;
  it is learning <b>which signals predict</b>. Each entry records the sentiment features
  that triggered it, so outcomes can later be grouped by signal strength, mention volume,
  and earnings contamination rather than judged in aggregate.</p>
  %(cascade)s
  <h3>Gate thresholds currently in force</h3>
  %(gates)s
  %(ledger)s
</div>

<div class="tabpane" id="p-about">
  <h2>Caveats</h2>
  <ol class="cav">
    <li><b>This is not a mispricing detector.</b> Nothing in a chain snapshot identifies
    underpriced options; the quotes are the market's estimate, set by participants with
    faster data and better execution.</li>
    <li><b>P(ITM) is risk-neutral, not predictive.</b> It is the market's implied
    probability, backed out of the contract's own quoted IV. Its use is calibration: if
    the market implies 22%% and you believe 45%%, the gap is the thesis. If you cannot
    articulate why your estimate differs, that indicates no edge on that contract.</li>
    <li><b>SD scaling assumes lognormal returns.</b> Real equity returns have fat tails,
    so genuine 2-SD moves occur somewhat more often than a normal distribution implies
    &mdash; especially to the downside and especially in single names around catalysts.
    <i>Breakeven in SDs</i> is a comparability ruler, not a probability.</li>
    <li><b>Free-feed quotes are delayed and sometimes crossed.</b> Any figure derived
    from them is indicative, not executable.</li>
  </ol>

  <h2>Known limits of the simulation</h2>
  <p class="intro">Source-level attribution &mdash; <i>which publisher</i> predicted
  best &mdash; is <b>not available</b>. The sentiment collector records only the feed
  (<span class="mono">stocktwits</span> or <span class="mono">news:yahoo</span>) and
  discards the article URL and publisher, so Reuters and a blog post arriving through
  the same RSS aggregator are indistinguishable. Keyword-level attribution is likewise
  unavailable: the matched sentiment words are computed at ingest and not retained.
  Both would require a change to the collector, and would only apply to mentions
  gathered after that change.</p>

  <h2>This run</h2>
  <div class="kv">
    <div class="k">Quotes fetched</div><div class="v">%(fetched)s</div>
    <div class="k">Market session</div><div class="v">%(session)s</div>
    <div class="k">Raw contracts</div><div class="v">%(raw)s</div>
    <div class="k">Survived filters</div><div class="v">%(screened)s</div>
    <div class="k">Page generated</div><div class="v">%(generated)s</div>
  </div>
  <p class="faint">Tickers covered: %(tickers)s</p>
  %(failednote)s
</div>

<script>window.__CONTRACTS__=%(slim)s;</script>
<script>%(tabsjs)s</script>
<script>%(calcjs)s</script>
</div></body></html>
""" % {
        "css": CSS,
        "fresh": fresh,
        "horizon": cfg.DRAG_HORIZON_DAYS,
        "rank": _rank_table(contracts),
        "summary": _summary_html(meta),
        "term": _term_structure_html(meta),
        "cascade": _cascade_explainer(),
        "gates": _gate_thresholds(),
        "ledger": _ledger_html(ledger, positions),
        "fetched": _esc(fetched),
        "session": _esc(session),
        "raw": meta.get("raw_row_count", 0),
        "screened": meta.get("screened_count", len(contracts)),
        "generated": _esc(generated),
        "tickers": _esc(tickers_ok) if tickers_ok else "(none)",
        "failednote": ("<p class='faint'>No usable data for: {0}</p>".format(
            _esc(", ".join(failed))) if failed else ""),
        "slim": json.dumps(slim),
        "tabsjs": TABS_JS,
        "calcjs": calc_js,
    }
    return page


def run(out_path=None):
    if out_path is None:
        out_path = cfg.SITE_HTML
    page = build()
    with open(out_path, "w") as fh:
        fh.write(page)
    _log("wrote {0} ({1:,} bytes)".format(os.path.relpath(out_path), len(page)))
    return out_path


if __name__ == "__main__":
    run()
