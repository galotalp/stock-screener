"""
Generate scenarios.html: a reference page showing what BOTH lead/lag plots look like
in the three archetypal cases, so a reader can interpret the real dashboard (and
understand what it means when the two plots seem to disagree).

Scenarios:
  A. Leading sentiment  (predictive)  — correlation peaks at a POSITIVE offset
  B. Lagging sentiment  (reactive)    — correlation peaks at a NEGATIVE offset
  C. No relationship                  — correlation is flat/near-zero everywhere

Each scenario shows:
  - the daily lead/lag bar chart (offsets in days)
  - the session dual-line chart (full path vs regular-only, offsets in hours),
    where the "conflict" case is illustrated (leads regular, coincides with full)

All curves are synthetic and clearly labelled as illustrative.
"""
import math

# ---- synthetic curves ----
def gauss(center, amp, width, k):
    return amp * math.exp(-((k - center) ** 2) / (2 * width ** 2))

def daily_curve(kind):
    # offsets -7..+7 days
    out = {}
    for k in range(-7, 8):
        if kind == "lead":
            out[k] = round(gauss(3, 0.7, 2.2, k), 3)      # peak at +3 (leads, POSITIVE=green)
        elif kind == "lag":
            out[k] = round(gauss(-3, -0.65, 2.2, k), 3)    # trough at -3 (reacts, NEGATIVE=red)
        elif kind == "contrarian":
            out[k] = round(gauss(3, -0.68, 2.2, k), 3)     # leads (+3) but NEGATIVE (red): contrarian
        elif kind == "chase":
            out[k] = round(gauss(-3, 0.66, 2.2, k), 3)     # reacts (-3) and POSITIVE (green): chases
        else:  # none
            out[k] = round(0.06 * math.sin(k) , 3)         # flat noise near 0
    return out

def session_curves(kind):
    # offsets -24..+24 hours; return (full, regular)
    full, reg = {}, {}
    for k in range(-24, 25):
        if kind == "lead":
            full[k] = round(gauss(4, 0.6, 6, k), 3)        # both lead
            reg[k]  = round(gauss(5, 0.7, 6, k), 3)
        elif kind == "lag":
            full[k] = round(gauss(-5, -0.6, 6, k), 3)      # both lag
            reg[k]  = round(gauss(-4, -0.65, 6, k), 3)
        elif kind == "conflict":
            full[k] = round(gauss(0, 0.62, 5, k), 3)       # full: coincident (peak ~0)
            reg[k]  = round(gauss(7, 0.7, 6, k), 3)        # regular: leads by ~7h
        else:  # none
            full[k] = round(0.05 * math.sin(k/3), 3)
            reg[k]  = round(0.05 * math.cos(k/3), 3)
    return full, reg


def dict_js(d):
    return "{" + ",".join(f'"{k}":{v}' for k, v in d.items()) + "}"


SCEN = {
    "lead":   {"daily": daily_curve("lead"),  "full": session_curves("lead")[0],  "reg": session_curves("lead")[1]},
    "lag":    {"daily": daily_curve("lag"),   "full": session_curves("lag")[0],   "reg": session_curves("lag")[1]},
    "contrarian": {"daily": daily_curve("contrarian"), "full": session_curves("lead")[0], "reg": session_curves("lead")[1]},
    "none":   {"daily": daily_curve("none"),  "full": session_curves("none")[0],  "reg": session_curves("none")[1]},
    "conflict": {"daily": daily_curve("lead"), "full": session_curves("conflict")[0], "reg": session_curves("conflict")[1]},
}


def main():
    import json
    data_js = json.dumps({k: {"daily": v["daily"], "full": v["full"], "reg": v["reg"]}
                          for k, v in SCEN.items()}, separators=(",", ":"))
    html = TEMPLATE.replace("/*DATA*/", data_js)
    out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>How to read the lead/lag plots · Sentiment vs Price</title>
</head>
<body>
{html}
</body>
</html>
"""
    open("scenarios.html", "w", encoding="utf-8").write(out)
    print("wrote scenarios.html", len(out), "bytes")


TEMPLATE = r"""
<style>
  :root{
    --bg:#fff;--ink:#333;--body:#696969;--faint:#8a8a8a;--navy:#00008b;--link:#597ccd;--link-hover:#00008b;
    --hair:#e6e6e6;--hair2:#f0f0f0;--panel:#fafafb;--price:#3b5bdb;--senti:#0ca678;--pos:#1a7f37;--neg:#c0392b;--neut:#9a7a12;
    --serif:"Times New Roman",Times,Georgia,serif;--head:Arial,Helvetica,sans-serif;--mono:"Cascadia Mono",monospace;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--serif);font-size:17px;line-height:1.6;padding:3px;}
  .wrap{max-width:1000px;margin:0 auto;padding:0 28px 80px;}
  a{color:var(--link);text-decoration:none;} a:hover{text-decoration:underline;}
  .topbar{display:flex;gap:18px;flex-wrap:wrap;padding:16px 0 0;font-size:14px;font-family:var(--head);}
  .topbar a{font-weight:bold;}
  h1{font-family:var(--head);color:#000;font-size:28px;font-weight:bold;border-bottom:3px solid var(--navy);padding:0 0 6px;margin:18px 0 14px;text-align:center;}
  .intro{font-size:16px;text-align:justify;margin:0 0 18px;}
  h2{font-family:var(--head);font-size:20px;margin:30px 0 4px;}
  h2 .tag{font-size:14px;font-weight:normal;padding:2px 10px;border-radius:20px;margin-left:8px;vertical-align:middle;}
  .tag.lead{color:var(--pos);background:#e8f4ea;} .tag.lag{color:var(--neg);background:#f7e7e4;} .tag.none{color:var(--neut);background:#f6f0dc;}
  .scen-desc{font-size:15.5px;text-align:justify;margin:4px 0 12px;color:var(--body);}
  .scen-desc b{color:var(--ink);}
  .charts{display:grid;grid-template-columns:1fr 1fr;gap:18px;}
  @media(max-width:760px){.charts{grid-template-columns:1fr;}}
  .card{border:1px solid var(--hair);border-radius:8px;padding:10px 8px 6px;background:#fff;}
  .card .ct{font-family:var(--head);font-size:13.5px;font-weight:bold;text-align:center;color:#000;margin:2px 0 2px;}
  svg{display:block;width:100%;height:auto;font-family:var(--mono);}
  .axislbl{fill:var(--faint);font-size:12px;} .axistitle{fill:var(--ink);font-size:12.5px;font-family:var(--head);font-weight:bold;}
  .gridln{stroke:var(--hair2);stroke-width:1;} .zeroln{stroke:var(--hair);stroke-width:1;stroke-dasharray:3 3;}
  .lgd{display:flex;gap:20px;justify-content:center;font-size:14px;margin:2px 0 2px;}
  .lgd span{display:inline-flex;align-items:center;gap:8px;} .lgd i{width:24px;height:6px;border-radius:3px;}
  .callout{background:var(--panel);border:1px solid var(--hair);border-left:3px solid var(--navy);border-radius:6px;padding:12px 16px;font-size:15.5px;margin:14px 0 6px;text-align:justify;}
  /* two-axes explainer */
  .axes2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:10px 0 6px;}
  @media(max-width:680px){.axes2{grid-template-columns:1fr;}}
  .axbox{border:1px solid var(--hair);border-radius:8px;padding:12px 15px;background:#fff;font-size:15px;}
  .axbox h3{font-family:var(--head);font-size:16px;margin:0 0 6px;color:#000;}
  .axbox .sw{display:inline-block;width:22px;height:10px;border-radius:2px;vertical-align:middle;margin:0 3px;}
  /* quadrant table */
  .quad{width:100%;border-collapse:collapse;margin:12px 0 4px;font-size:14.5px;}
  .quad th,.quad td{border:1px solid var(--hair);padding:9px 12px;text-align:left;vertical-align:top;}
  .quad th{background:var(--panel);font-family:var(--head);color:#000;}
  .quad td b{color:#000;} .quad .g{color:var(--pos);font-weight:bold;} .quad .r{color:var(--neg);font-weight:bold;}
  .quad caption{font-size:14px;color:var(--faint);text-align:left;margin-bottom:4px;}
  footer{margin-top:40px;border-top:1px solid var(--hair);padding-top:16px;color:var(--faint);font-size:13px;text-align:center;}
</style>
<div class="wrap">
  <nav class="topbar">
    <a href="../index.html">← galavgay.com</a>
    <a href="index.html">Sentiment vs Price</a>
    <a href="proposal.html">Research proposal</a>
  </nav>
  <h1>How to read the lead / lag plots</h1>
  <p class="intro">The dashboard shows two related plots for each stock. The <b>daily lead/lag chart</b> uses bar
     height to show the correlation between sentiment and price returns at each day-offset. The <b>session
     chart</b> shows the same idea at hourly resolution, comparing the full price path (which includes
     pre-market and after-hours moves) against the regular-hours-only path. A positive offset means sentiment
     <b>leads</b> price; a negative offset means sentiment <b>reacts to</b> price. Below is what each plot looks
     like in the basic cases, plus the important case where the two plots seem to disagree.</p>

  <h2>Each bar carries two separate signals</h2>
  <p class="scen-desc">A common point of confusion: on the daily chart a bar's <b>horizontal position</b> and its
     <b>color</b> mean two different things. Reading them together is the whole skill.</p>
  <div class="axes2">
    <div class="axbox">
      <h3>Position = timing</h3>
      Where the tallest bars sit left-to-right. Bars to the <b>right of zero</b> mean sentiment <b>leads</b>
      (it moved first, price followed later). Bars to the <b>left of zero</b> mean sentiment <b>reacts</b>
      (price moved first, the crowd responded after). This answers <i>“which came first?”</i>
    </div>
    <div class="axbox">
      <h3>Color = direction</h3>
      Whether sentiment and price move the <b>same way</b> or <b>opposite ways</b>.
      <span class="sw" style="background:var(--pos)"></span> <b class="q g">Green</b> = positive correlation
      (bullish sentiment goes with a price <i>rise</i>).
      <span class="sw" style="background:var(--neg)"></span> <b class="q r">Red</b> = negative correlation
      (bullish sentiment goes with a price <i>fall</i>). This answers <i>“same direction, or opposite?”</i>
    </div>
  </div>
  <p class="scen-desc">Because these are independent, the same chart can show green bars on one side and red on the
     other. That is not a glitch: it means the relationship <b>changes sign</b> depending on the timing. The four
     combinations each tell a different story:</p>
  <table class="quad">
    <caption>How to read any bar, by its position and color</caption>
    <tr><th></th><th><span class="sw" style="background:var(--pos)"></span> Green (same direction)</th>
        <th><span class="sw" style="background:var(--neg)"></span> Red (opposite direction)</th></tr>
    <tr><th>Right of 0 <br><span style="font-weight:normal;color:var(--faint)">sentiment leads</span></th>
        <td><b>Leads &amp; predicts.</b> Bullish sentiment today precedes a price <b>rise</b> later. The
            hoped-for “sentiment is an early signal” case.</td>
        <td><b>Leads, but contrarian.</b> Bullish sentiment today precedes a price <b>drop</b> later. The crowd’s
            forward bets are wrong-signed — a fade-the-hype pattern.</td></tr>
    <tr><th>Left of 0 <br><span style="font-weight:normal;color:var(--faint)">sentiment reacts</span></th>
        <td><b>Reacts, chases.</b> Price rose first, then sentiment turned bullish. The crowd is <b>chasing</b>
            a move that already happened.</td>
        <td><b>Reacts, fades.</b> Price rose first, then sentiment turned <b>bearish</b>. The crowd fades the
            move after the fact.</td></tr>
  </table>
  <p class="scen-desc">So the “green on the left, red on the right” pattern (or its mirror) simply means the crowd
     behaves one way <i>before</i> a move and the opposite way <i>after</i> it. On <b>thin data</b> these patterns are
     mostly noise, and a single earnings gap can flip the whole picture — which is exactly why the dashboard lets you
     <b>exclude earnings windows</b> (see below).</p>

  <div id="scenarios"></div>

  <footer>Illustrative synthetic curves for interpretation only; not real data.</footer>
</div>

<script id="payload" type="application/json">/*DATA*/</script>
<script>
const S=JSON.parse(document.getElementById('payload').textContent);
const NS="http://www.w3.org/2000/svg";
function el(t,a){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function tx(x,y,s,cls,anchor,extra){const e=el('text',Object.assign({x,y,class:cls||'axislbl','text-anchor':anchor||'start'},extra||{}));e.textContent=s;return e;}

function dailyChart(curve){
  const W=460,H=220,mL=44,mR=12,mT=16,mB=40,LAG=7;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,role:'img'});
  const cx=k=>mL+(W-mL-mR)*((k+LAG)/(2*LAG));
  const y0=(H-mB+mT)/2, yS=v=>y0-(H/2-mT-6)*v;
  svg.appendChild(el('line',{x1:mL,y1:y0,x2:W-mR,y2:y0,stroke:'var(--hair)','stroke-width':1}));
  [-1,-0.5,0.5,1].forEach(v=>{const yy=yS(v);svg.appendChild(el('line',{x1:mL,y1:yy,x2:W-mR,y2:yy,class:'gridln'}));
    svg.appendChild(tx(mL-4,yy+3,v.toFixed(1),'axislbl','end'));});
  const bw=(W-mL-mR)/(2*LAG)*0.55;
  for(let k=-LAG;k<=LAG;k++){const c=curve[k]||0;const yy=yS(c);const h=Math.abs(y0-yy);
    svg.appendChild(el('rect',{x:cx(k)-bw/2,y:Math.min(y0,yy),width:bw,height:h||0.6,rx:2,fill:c>=0?'var(--pos)':'var(--neg)'}));
    if(k%2===0) svg.appendChild(tx(cx(k),H-22,(k>0?'+':'')+k,'axislbl','middle'));}
  svg.appendChild(tx(W/2,H-6,'Offset in days','axistitle','middle'));
  svg.appendChild(tx(mL,12,'← reacts','axislbl','start'));
  svg.appendChild(tx(W-mR,12,'leads →','axislbl','end'));
  return svg;
}
function sessionChart(full,reg){
  const W=460,H=220,mL=44,mR=12,mT=22,mB=40,LAG=24;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,role:'img'});
  const cx=k=>mL+(W-mL-mR)*((k+LAG)/(2*LAG));
  const y0=(H-mB+mT)/2, yS=v=>y0-(H/2-mT-8)*v;
  svg.appendChild(el('line',{x1:mL,y1:y0,x2:W-mR,y2:y0,stroke:'var(--hair)','stroke-width':1}));
  [-1,-0.5,0.5,1].forEach(v=>{const yy=yS(v);svg.appendChild(el('line',{x1:mL,y1:yy,x2:W-mR,y2:yy,class:'gridln'}));
    svg.appendChild(tx(mL-4,yy+3,v.toFixed(1),'axislbl','end'));});
  svg.appendChild(el('line',{x1:cx(0),y1:mT,x2:cx(0),y2:H-mB,class:'zeroln'}));
  const curve=(c,color)=>{let d='',st=false;for(let k=-LAG;k<=LAG;k++){if(c[k]===undefined)continue;
    d+=(st?'L':'M')+cx(k)+' '+yS(c[k])+' ';st=true;}svg.appendChild(el('path',{d,fill:'none',stroke:color,'stroke-width':2.4}));};
  curve(full,'var(--price)'); curve(reg,'var(--senti)');
  [-24,-12,0,12,24].forEach(k=>svg.appendChild(tx(cx(k),H-22,(k>0?'+':'')+k+'h','axislbl','middle')));
  svg.appendChild(tx(W/2,H-6,'Offset in hours','axistitle','middle'));
  svg.appendChild(tx(mL,14,'← reacts','axislbl','start'));
  svg.appendChild(tx(W-mR,14,'leads →','axislbl','end'));
  return svg;
}

const SCENARIOS=[
  {key:'lead', title:'Sentiment leads price', tag:'lead', label:'predictive',
   desc:'The correlation is <b>strongest at a positive offset</b>. On the daily chart the tallest bars are to the right of zero; on the session chart both curves peak to the right. Sentiment moves first and price follows. This is the pattern of a potentially <b>actionable</b> signal, because the move had not yet happened when the sentiment appeared.'},
  {key:'lag', title:'Sentiment lags price', tag:'lag', label:'reactive',
   desc:'The correlation is <b>strongest at a negative offset</b>. The largest bars are to the left of zero and the session curves dip to the left. Price moves first and sentiment reacts to it afterward. This is not a tradable signal; the crowd is <b>responding</b> to a move that already occurred.'},
  {key:'contrarian', title:'Sentiment leads, but the wrong way', tag:'lag', label:'contrarian',
   desc:'The tallest bars are again <b>to the right of zero</b> (sentiment leads), but now they point <b>downward and are red</b> — a <b>negative</b> correlation. Bullish sentiment today precedes a price <b>fall</b>, not a rise. This is the classic "fade the crowd" pattern: the sentiment is early, but consistently on the wrong side of the move. Note this is a different message from the reactive case even though both use red — here the red bars are on the <b>right</b> (leading), not the left.'},
  {key:'none', title:'No relationship', tag:'none', label:'noise',
   desc:'The correlation is <b>flat and near zero at every offset</b>. Neither chart shows a clear peak in either direction. Sentiment and price are essentially unrelated for this stock, at least over the window observed.'},
];

const host=document.getElementById('scenarios');
SCENARIOS.forEach(s=>{
  const sec=document.createElement('div');
  sec.innerHTML=`<h2>${s.title} <span class="tag ${s.tag}">${s.label}</span></h2><p class="scen-desc">${s.desc}</p>`;
  const grid=document.createElement('div'); grid.className='charts';
  const c1=document.createElement('div'); c1.className='card'; c1.innerHTML='<div class="ct">Daily lead/lag chart</div>';
  c1.appendChild(dailyChart(S[s.key].daily)); grid.appendChild(c1);
  const c2=document.createElement('div'); c2.className='card'; c2.innerHTML='<div class="ct">Session chart (full vs regular hours)</div>';
  c2.appendChild(sessionChart(S[s.key].full,S[s.key].reg));
  const lg=document.createElement('div'); lg.className='lgd';
  lg.innerHTML='<span><i style="background:var(--price)"></i>full path</span><span><i style="background:var(--senti)"></i>regular hours only</span>';
  c2.appendChild(lg); grid.appendChild(c2);
  sec.appendChild(grid); host.appendChild(sec);
});

// the conflict case
const cf=document.createElement('div');
cf.innerHTML=`<h2>When the two plots disagree <span class="tag none">read carefully</span></h2>
  <p class="scen-desc">Sometimes the daily chart suggests sentiment <b>leads</b> price, but the session chart tells a subtler story:
  sentiment leads the <b style="color:var(--senti)">regular-hours-only path</b> yet only <b>coincides with</b> the
  <b style="color:var(--price)">full path</b> that includes extended hours. This is the key case the study is built to detect.
  It means the price had <b>already moved in pre-market or after-hours trading</b> before the regular session opened, so although
  sentiment appears to lead the price a regular-hours trader sees, the information was in fact already impounded. The apparent
  signal was <b>not actionable</b> for someone trading only during regular hours.</p>`;
const grid=document.createElement('div'); grid.className='charts';
const cc1=document.createElement('div'); cc1.className='card'; cc1.innerHTML='<div class="ct">Daily chart: looks like sentiment leads</div>';
cc1.appendChild(dailyChart(S.conflict.daily)); grid.appendChild(cc1);
const cc2=document.createElement('div'); cc2.className='card'; cc2.innerHTML='<div class="ct">Session chart: leads regular, coincides with full</div>';
cc2.appendChild(sessionChart(S.conflict.full,S.conflict.reg));
const lg=document.createElement('div'); lg.className='lgd';
lg.innerHTML='<span><i style="background:var(--price)"></i>full path (peaks near 0h)</span><span><i style="background:var(--senti)"></i>regular hours only (peaks right)</span>';
cc2.appendChild(lg); grid.appendChild(cc2);
cf.appendChild(grid);
const callout=document.createElement('div'); callout.className='callout';
callout.innerHTML='<b>Rule of thumb:</b> trust the <b>session chart</b> when it disagrees with the daily one. If sentiment leads the regular path but the full path already peaks at or before zero, the extended-hours market got there first.';
cf.appendChild(callout);
host.appendChild(cf);

// earnings regime explainer
const ern=document.createElement('div');
ern.innerHTML=`<h2>Controlling for earnings <span class="tag none">why it matters</span></h2>
  <p class="scen-desc">Earnings reports are an <b>outside shock</b>: the price jumps because of the fundamental
  news in the report, not because of sentiment. Most reports land in <b>extended hours</b> (after the close or
  before the open), so they gap the price precisely where this study is looking. If we left them in, a stock
  could look like sentiment "predicts" price when really both were reacting to the same earnings release.
  This study is <b>not an analysis of earnings</b> — it simply <b>notes when each report occurs</b> and holds
  those windows aside so the sentiment-price relationship can be read cleanly.</p>
  <p class="scen-desc">On the dashboard each earnings date is a <b style="color:#b58900">gold dashed line</b> on
  the price chart (hover it for whether the company beat or missed). The <b>Earnings</b> control then re-computes
  the session chart three ways:</p>`;
const et=document.createElement('table'); et.className='quad';
et.innerHTML=`<caption>What the Earnings control does</caption>
  <tr><th>Setting</th><th>What it shows</th></tr>
  <tr><td><b>Show all</b></td><td>Every hour, earnings windows included. The default.</td></tr>
  <tr><td><b>Exclude earnings</b></td><td>Earnings windows removed. This is the <b>clean</b> sentiment-price
      signal. If it looks much weaker than "Show all," the apparent relationship was largely earnings driving
      both series.</td></tr>
  <tr><td><b>Earnings only</b></td><td>Only the earnings windows. Isolates how sentiment and price behave
      around reports specifically. Often "accumulating" until enough reports fall inside the intraday history.</td></tr>`;
ern.appendChild(et);
const ecall=document.createElement('div'); ecall.className='callout';
ecall.innerHTML='<b>How to use it:</b> compare <b>Show all</b> against <b>Exclude earnings</b>. If the correlation barely changes, sentiment tracks price on its own. If it drops a lot, the signal was mostly earnings — an important thing to know before trusting it.';
ern.appendChild(ecall);
host.appendChild(ern);
</script>
"""

if __name__ == "__main__":
    main()
