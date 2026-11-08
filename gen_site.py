"""
Generate the two-screen Peter Lynch Portfolio site page (self-contained HTML).

Design:
  Screen 1 (Rankings):
    - view switcher GROUPED into Lynch vs Sentiment (top-level toggle -> sub-view pills)
    - COMPACT rows (ticker, category, score, one-line sentiment); click to expand
      full metrics + sentiment summaries + Lynch (Fable) verdict
  Screen 2 (Backtest): list + $/stock + scrape-date -> P&L simulation.
  Minimal copy; keeps Rankings/Backtest tabs + site links.

Data (views.json + backtest history) is embedded as JSON so the page is fully
static and hostable on S3 with no backend.
"""
import json
import os

DATA = "data"


def load_history():
    hist = {}
    hdir = f"{DATA}/history"
    if os.path.isdir(hdir):
        for fn in sorted(os.listdir(hdir)):
            if fn.endswith(".json"):
                hist[fn[:-5]] = json.load(open(f"{hdir}/{fn}"))
    return hist


def main():
    views = json.load(open(f"{DATA}/views.json"))
    history = load_history()
    prices = {}
    for v in views["views"].values():
        for it in v["items"]:
            if it.get("price") is not None:
                prices[it["ticker"]] = it["price"]

    payload = {
        "views": views["views"], "view_order": views["view_order"],
        "generated": views["generated"], "exclusions": views["exclusions"],
        "universe_size": views["universe_size"], "history": history,
        "current_prices": prices,
    }
    data_js = json.dumps(payload, separators=(",", ":"))
    html = TEMPLATE.replace("/*DATA*/", data_js)
    out = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Peter Lynch Portfolio — Gal Av-Gay</title>
</head>
<body>
{html}
</body>
</html>
'''
    open("lynch_site.html", "w", encoding="utf-8").write(out)
    print("wrote lynch_site.html", len(out), "bytes;", len(prices), "priced tickers;",
          len(history), "history snapshots")


TEMPLATE = r"""
<style>
  :root{
    --bg:#ffffff; --ink:#333; --body:#696969; --faint:#8a8a8a;
    --navy:#00008b; --link:#597ccd; --link-hover:#00008b;
    --hair:#e6e6e6; --hair2:#f0f0f0; --panel:#fafafb;
    --bull:#1a7f37; --bull-bg:#e8f4ea; --neut:#9a7a12; --neut-bg:#f6f0dc;
    --bear:#c0392b; --bear-bg:#f7e7e4; --none:#8a8a8a; --none-bg:#f0f0f0;
    --gain:#1a7f37; --loss:#c0392b;
    --sans:"Trebuchet MS",Verdana,Geneva,sans-serif; --head:Arial,Helvetica,sans-serif;
    --mono:"Cascadia Mono","SF Mono",ui-monospace,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--body);font-family:var(--sans);font-size:16px;line-height:1.5;padding:3px;}
  .wrap{max-width:880px;margin:0 auto;padding:0 15px 70px;}
  a{color:var(--link);text-decoration:none;} a:hover{color:var(--link-hover);text-decoration:underline;}

  .topbar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:14px 0 0;font-size:13px;}
  .topbar .home{font-weight:bold;}
  .topbar .updated{margin-left:auto;color:var(--faint);font-family:var(--mono);font-size:12px;}
  .topbar .updated b{color:var(--ink);}

  h1{font-family:var(--head);color:#000;font-size:26px;font-weight:bold;border-bottom:3px solid var(--navy);
     padding:0 0 5px;margin:16px 0 12px;}
  .intro{font-size:14.5px;color:var(--body);margin:0 0 4px;line-height:1.5;}
  .intro b{color:var(--ink);}
  .excl{display:inline-flex;flex-wrap:wrap;gap:5px;margin:8px 0 0;}
  .excl .x{font-family:var(--mono);font-size:11px;color:var(--bear);background:var(--bear-bg);border-radius:3px;padding:2px 8px;}

  /* screen tabs */
  .tabs{display:flex;gap:8px;margin:20px 0 16px;border-bottom:1px solid var(--hair);}
  .tab{font-family:var(--head);font-size:14px;font-weight:bold;padding:8px 14px;cursor:pointer;color:var(--faint);
       border-bottom:3px solid transparent;margin-bottom:-1px;background:none;border-top:none;border-left:none;border-right:none;}
  .tab.active{color:#000;border-bottom-color:var(--navy);}

  /* GROUPED view switcher */
  .groups{display:flex;gap:6px;margin:0 0 10px;}
  .grp{font-family:var(--head);font-size:13px;font-weight:bold;padding:7px 16px;cursor:pointer;border-radius:5px 5px 0 0;
       border:1px solid var(--hair);border-bottom:none;background:var(--panel);color:var(--faint);}
  .grp.active{background:#fff;color:var(--navy);border-color:var(--navy);border-bottom:2px solid #fff;position:relative;top:1px;}
  .subviews{display:flex;flex-wrap:wrap;gap:6px;padding:0 0 14px;border-bottom:1px solid var(--hair);margin-bottom:14px;}
  .pill-v{font-family:var(--sans);font-size:13px;padding:6px 13px;border-radius:20px;cursor:pointer;
          border:1px solid var(--hair);background:#fff;color:var(--body);}
  .pill-v:hover{border-color:var(--link);color:var(--link);}
  .pill-v.active{background:var(--navy);border-color:var(--navy);color:#fff;}
  .viewhead{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:0 0 12px;}
  .viewhead .t{font-family:var(--head);font-weight:bold;font-size:16px;color:#000;}
  .viewhead .c{font-family:var(--mono);font-size:12px;color:var(--faint);}

  /* COMPACT rows (collapsed) */
  .row{border:1px solid var(--hair);border-radius:5px;margin-bottom:7px;background:#fff;overflow:hidden;}
  .rowhead{display:grid;grid-template-columns:28px 1fr auto auto;gap:0 12px;align-items:center;padding:11px 14px;cursor:pointer;}
  .row:hover{border-color:#d3d9e6;}
  .row.open{border-color:var(--navy);}
  .rk{font-family:var(--mono);font-size:13px;color:#b7bdc7;}
  .idc{display:flex;align-items:center;gap:9px;flex-wrap:wrap;min-width:0;}
  .tk{font-family:var(--head);font-size:17px;font-weight:bold;color:#000;}
  .cat{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.03em;padding:1px 7px;border-radius:3px;color:#fff;white-space:nowrap;}
  .cat.fast{background:#1a7f37;} .cat.stalwart{background:#2c6fb0;} .cat.turn{background:#b8860b;}
  .cat.cyc{background:#b0563e;} .cat.mod{background:#5a6b7a;} .cat.slow{background:#8a8a8a;}
  .senti-mini{font-family:var(--mono);font-size:11.5px;color:var(--faint);display:flex;gap:8px;align-items:center;white-space:nowrap;}
  .senti-mini .d{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:3px;}
  .d.reddit{background:#ff4500;} .d.msm{background:#2c6fb0;}
  .mini.bull{color:var(--bull);} .mini.bear{color:var(--bear);} .mini.neut{color:var(--neut);} .mini.none{color:var(--none);}
  .score{font-family:var(--mono);font-weight:bold;font-size:18px;color:var(--navy);text-align:right;min-width:44px;}
  .caret{font-family:var(--mono);color:#b7bdc7;font-size:12px;transition:transform .15s;}
  .row.open .caret{transform:rotate(90deg);}

  /* expanded detail */
  .detail{display:none;padding:0 14px 14px 40px;border-top:1px solid var(--hair2);}
  .row.open .detail{display:block;}
  .metivals{display:flex;flex-wrap:wrap;gap:4px 16px;margin:12px 0 0;font-family:var(--mono);font-size:12.5px;color:var(--faint);}
  .metivals b{color:var(--ink);}
  .nmfull{font-size:13px;color:var(--body);margin-top:8px;}
  .sent2{display:flex;flex-direction:column;gap:6px;margin-top:12px;}
  .sblk{font-size:12.5px;color:var(--body);line-height:1.4;}
  .sblk .h{font-family:var(--head);font-weight:bold;font-size:11px;color:var(--ink);display:inline-flex;align-items:center;gap:5px;}
  .sblk .b{padding:1px 7px;border-radius:20px;font-family:var(--mono);font-size:11px;margin-left:4px;}
  .b.bull{color:var(--bull);background:var(--bull-bg);} .b.bear{color:var(--bear);background:var(--bear-bg);}
  .b.neut{color:var(--neut);background:var(--neut-bg);} .b.none{color:var(--none);background:var(--none-bg);}
  .fresh{font-family:var(--mono);font-size:10px;margin-left:6px;padding:1px 6px;border-radius:3px;letter-spacing:.02em;}
  .fresh.fresh{color:#1a7f37;background:#e8f4ea;} .fresh.recent{color:#2c6fb0;background:#eaf1f8;}
  .fresh.aging{color:#9a7a12;background:#f6f0dc;} .fresh.stale{color:#8a8a8a;background:#f0f0f0;}
  .fable{margin-top:12px;font-size:13px;color:var(--ink);border-left:3px solid var(--navy);padding-left:10px;line-height:1.45;}
  .fable .lbl{font-family:var(--head);font-weight:bold;font-size:11px;color:var(--navy);text-transform:uppercase;letter-spacing:.04em;display:block;margin-bottom:2px;}
  .lnk{margin-top:11px;display:flex;gap:15px;font-family:var(--mono);font-size:11.5px;}

  /* backtest */
  .bt-controls{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end;margin-bottom:18px;background:var(--panel);border:1px solid var(--hair);border-radius:6px;padding:14px 16px;}
  .fld{display:flex;flex-direction:column;gap:4px;}
  .fld label{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-family:var(--head);font-weight:bold;}
  .fld input,.fld select{font-family:var(--mono);font-size:15px;border:1px solid var(--hair);border-radius:5px;padding:8px 10px;}
  .fld input{width:130px;}
  .bt-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:18px;}
  .stat{border:1px solid var(--hair);border-radius:6px;padding:12px 14px;background:#fff;}
  .stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-family:var(--head);font-weight:bold;}
  .stat .val{font-family:var(--mono);font-size:22px;font-weight:bold;color:var(--ink);margin-top:3px;}
  .val.gain{color:var(--gain);} .val.loss{color:var(--loss);}
  .bt-row{display:grid;grid-template-columns:30px 1fr auto auto auto;gap:0 14px;align-items:center;border-bottom:1px solid var(--hair2);padding:9px 4px;font-family:var(--mono);font-size:13px;}
  .bt-row.head{font-family:var(--head);font-weight:bold;font-size:11px;text-transform:uppercase;color:var(--faint);letter-spacing:.04em;border-bottom:1px solid var(--hair);}
  .bt-row .r{text-align:right;} .g{color:var(--gain);} .r-loss{color:var(--loss);}
  .empty{color:var(--faint);text-align:center;padding:30px;font-size:14px;}
  select:focus,input:focus{outline:2px solid var(--link);border-color:var(--link);}
  footer{margin-top:44px;border-top:1px solid var(--hair);padding-top:16px;color:var(--faint);font-size:12px;line-height:1.5;}
  .mono{font-family:var(--mono);}
  @media(max-width:560px){
    .rowhead{grid-template-columns:24px 1fr auto;} .senti-mini{display:none;}
    .detail{padding-left:14px;} .bt-row{grid-template-columns:24px 1fr auto;} .bt-row .hide-sm{display:none;}
  }
</style>

<div class="wrap">
  <nav class="topbar">
    <a class="home" href="../index.html">← galavgay.com</a>
    <a class="home" href="../projects.html">Projects</a>
    <a class="home" href="analysis.html">Sentiment vs Price</a>
    <a class="home" href="proposal.html">Research proposal</a>
    <span class="updated">Updated <b id="genDate">—</b></span>
  </nav>

  <h1>Peter Lynch Portfolio</h1>
  <p class="intro"><b>What Peter Lynch would buy</b> — ranked from a market-wide scan, with <b>Reddit</b>
     and <b>mainstream-media</b> sentiment. Excluded:</p>
  <div class="excl" id="exclList"></div>

  <div class="tabs">
    <button class="tab active" data-screen="rankings">Rankings</button>
    <button class="tab" data-screen="backtest">Backtest</button>
  </div>

  <!-- SCREEN 1 -->
  <div id="screen-rankings">
    <div class="groups" id="groups"></div>
    <div class="subviews" id="subviews"></div>
    <div class="viewhead"><span class="t" id="viewTitle"></span><span class="c" id="viewCount"></span></div>
    <div id="rows"></div>
  </div>

  <!-- SCREEN 2 -->
  <div id="screen-backtest" style="display:none;">
    <div class="bt-controls">
      <div class="fld"><label for="btList">List</label><select id="btList"></select></div>
      <div class="fld"><label for="btAmount">$ per stock</label><input id="btAmount" type="number" value="1000" min="1" step="100"></div>
      <div class="fld"><label for="btDate">Scrape date</label><select id="btDate"></select></div>
    </div>
    <div class="bt-summary" id="btSummary"></div>
    <div id="btTable"></div>
    <p class="intro" style="margin-top:14px;font-size:13px;color:var(--faint);">
      Simulates buying each stock at its price on the scrape date, held to the latest scan. History
      begins with the first scrape under this system and grows each run.</p>
  </div>

  <footer>Not financial advice. Fundamentals via Yahoo Finance; sentiment from live web search.
     Point-in-time data can be noisy or stale. A research starting point, not a buy list.</footer>
</div>

<script id="payload" type="application/json">/*DATA*/</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);
const CATCLASS={"Fast Grower":"fast","Stalwart":"stalwart","Turnaround":"turn","Cyclical":"cyc","Moderate Grower":"mod","Slow Grower":"slow"};

// group the six views
const GROUPS=[
  {key:"lynch", label:"Lynch picks", views:["lynch_long","lynch_short"]},
  {key:"sentiment", label:"Sentiment", views:["reddit_mentions","reddit_sentiment","msm_sentiment","overall_sentiment"]},
];
const SHORT_TITLE={lynch_long:"Long-term (5 yr)",lynch_short:"Short-term (1 yr)",
  reddit_mentions:"Most Reddit mentions",reddit_sentiment:"Reddit sentiment",
  msm_sentiment:"MSM sentiment",overall_sentiment:"Overall sentiment"};

function fmtNum(v,d=1){return (typeof v==='number'&&isFinite(v))?v.toFixed(d):'—';}
function fmtPct(v){return (typeof v==='number'&&isFinite(v))?(v*100).toFixed(0)+'%':'—';}
function fmtCap(v){if(typeof v!=='number'||!isFinite(v))return '—';
  if(v>=1e12)return '$'+(v/1e12).toFixed(1)+'T'; if(v>=1e9)return '$'+(v/1e9).toFixed(1)+'B';
  if(v>=1e6)return '$'+(v/1e6).toFixed(0)+'M'; return '$'+v.toFixed(0);}
function fmtMoney(v){return (v<0?'-$':'$')+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0});}
function sclass(label){const l=(label||'').toLowerCase();
  if(l.includes('bull'))return 'bull'; if(l.includes('bear'))return 'bear';
  if(l.includes('no discussion')||l.includes('little'))return 'none'; return 'neut';}
function freshTag(fresh,age){
  if(!fresh)return '';
  const lbl={fresh:'fresh',recent:'recent',aging:'aging',stale:'stale'}[fresh]||fresh;
  const days=(typeof age==='number')?` (~${Math.round(age)}d)`:'';
  return `<span class="fresh ${fresh}" title="weighted evidence age${days}; 14-day half-life">${lbl}${days}</span>`;}
function primaryLabel(k){return ({lynch_long:'LT',lynch_short:'ST',reddit_mentions:'mentions',
  reddit_sentiment:'Reddit',msm_sentiment:'MSM',overall_sentiment:'overall'})[k]||'';}
function primaryValue(it,k){
  if(k==='reddit_mentions')return it.reddit_mentions??'—';
  if(k==='lynch_long')return fmtNum(it.lt_score,0);
  if(k==='lynch_short')return fmtNum(it.st_score,0);
  if(k==='reddit_sentiment')return it.reddit_score??'—';
  if(k==='msm_sentiment')return it.msm_score??'—';
  if(k==='overall_sentiment')return fmtNum(it.overall_sentiment,0);
  return '—';}

let curGroup=GROUPS[0].key, curView="lynch_long";

function renderGroups(){
  const g=document.getElementById('groups'); g.innerHTML='';
  GROUPS.forEach(gr=>{
    const b=document.createElement('button'); b.className='grp'+(gr.key===curGroup?' active':'');
    b.textContent=gr.label; b.onclick=()=>{curGroup=gr.key; curView=gr.views[0]; renderGroups(); renderSubviews(); renderRows();};
    g.appendChild(b);
  });
}
function renderSubviews(){
  const s=document.getElementById('subviews'); s.innerHTML='';
  const gr=GROUPS.find(x=>x.key===curGroup);
  gr.views.forEach(vk=>{
    const p=document.createElement('button'); p.className='pill-v'+(vk===curView?' active':'');
    p.textContent=SHORT_TITLE[vk]; p.onclick=()=>{curView=vk; renderSubviews(); renderRows();};
    s.appendChild(p);
  });
}
function miniSent(it){
  const rc=sclass(it.reddit_label), mc=sclass(it.msm_label);
  const rtxt=rc==='none'?'no Reddit':(it.reddit_label||'—');
  return `<span class="senti-mini"><span class="mini ${rc}"><span class="d reddit"></span>${rtxt}</span>`
       + `<span class="mini ${mc}"><span class="d msm"></span>${it.msm_label||'—'}</span></span>`;
}
function renderRows(){
  const view=D.views[curView];
  document.getElementById('viewTitle').textContent=view.title;
  document.getElementById('viewCount').textContent=view.items.length+' stocks';
  const rows=document.getElementById('rows'); rows.innerHTML='';
  view.items.forEach(it=>{
    const cat=CATCLASS[it.category]||'mod';
    const rc=sclass(it.reddit_label), mc=sclass(it.msm_label);
    const fable=it.fable&&(it.fable.verdict||it.fable.summary)
      ?`<div class="fable"><span class="lbl">Lynch verdict${it.fable.conviction?' · '+it.fable.conviction:''}</span>${it.fable.verdict||it.fable.summary}</div>`:'';
    const el=document.createElement('div'); el.className='row';
    el.innerHTML=`
      <div class="rowhead">
        <div class="rk">${String(it.rank).padStart(2,'0')}</div>
        <div class="idc"><span class="tk">${it.ticker}</span><span class="cat ${cat}">${it.category}</span></div>
        ${miniSent(it)}
        <div style="display:flex;align-items:center;gap:8px;"><span class="score">${primaryValue(it,curView)}</span><span class="caret">▸</span></div>
      </div>
      <div class="detail">
        <div class="nmfull">${(it.name||'').split(' - ')[0].split(' Common')[0].split(' Class')[0]}</div>
        <div class="metivals">
          <span>P/E <b>${fmtNum(it.pe)}</b></span><span>Fwd P/E <b>${fmtNum(it.fpe)}</b></span>
          <span>PEG <b>${fmtNum(it.peg,2)}</b></span><span>Growth <b>${fmtPct(it.growth)}</b></span>
          <span>Margin <b>${fmtPct(it.margin)}</b></span><span>ROE <b>${fmtPct(it.roe)}</b></span>
          <span>Mkt cap <b>${fmtCap(it.mcap)}</b></span>
          ${typeof it.upside==='number'?`<span>Upside <b>${it.upside>0?'+':''}${it.upside.toFixed(0)}%</b></span>`:''}
        </div>
        <div class="sent2">
          <div class="sblk"><span class="h"><span class="d reddit" style="width:8px;height:8px;border-radius:50%;display:inline-block;"></span>Reddit</span>
            <span class="b ${rc}">${it.reddit_label||'—'}${typeof it.reddit_score==='number'&&rc!=='none'?' · '+it.reddit_score:''}${typeof it.reddit_mentions==='number'?' · '+it.reddit_mentions+' mentions':''}</span>${freshTag(it.reddit_freshness,it.reddit_age_days)}
            <div style="margin-top:3px;">${it.reddit_summary||''}</div></div>
          <div class="sblk"><span class="h"><span class="d msm" style="width:8px;height:8px;border-radius:50%;display:inline-block;"></span>Mainstream media</span>
            <span class="b ${mc}">${it.msm_label||'—'}${typeof it.msm_score==='number'?' · '+it.msm_score:''}</span>${freshTag(it.msm_freshness,it.msm_age_days)}
            <div style="margin-top:3px;">${it.msm_summary||''}</div></div>
        </div>
        ${fable}
        <div class="lnk">${it.website?`<a href="${it.website}" target="_blank" rel="noopener">↗ company</a>`:''}
          <a href="${it.yahoo||'#'}" target="_blank" rel="noopener">↗ financials</a></div>
      </div>`;
    el.querySelector('.rowhead').addEventListener('click',()=>el.classList.toggle('open'));
    rows.appendChild(el);
  });
}

// tabs
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active')); t.classList.add('active');
  const s=t.dataset.screen;
  document.getElementById('screen-rankings').style.display=s==='rankings'?'':'none';
  document.getElementById('screen-backtest').style.display=s==='backtest'?'':'none';
}));

// header bits
document.getElementById('genDate').textContent=D.generated||'—';
const el=document.getElementById('exclList');
(D.exclusions||[]).forEach(x=>{const s=document.createElement('span');s.className='x';s.textContent=x;el.appendChild(s);});

// backtest
const btList=document.getElementById('btList'),btDate=document.getElementById('btDate'),btAmount=document.getElementById('btAmount');
D.view_order.forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=D.views[k].title;btList.appendChild(o);});
const dates=Object.keys(D.history).sort();
dates.forEach(d=>{const o=document.createElement('option');o.value=d;o.textContent=d;btDate.appendChild(o);});
if(dates.length)btDate.value=dates[0];
function runBacktest(){
  const listKey=btList.value,date=btDate.value,amt=parseFloat(btAmount.value)||0;
  const table=document.getElementById('btTable'),summary=document.getElementById('btSummary');
  const snap=D.history[date];
  if(!snap||!snap.lists[listKey]){table.innerHTML='<div class="empty">No history for this selection.</div>';summary.innerHTML='';return;}
  const holdings=snap.lists[listKey]; let invested=0,current=0,rows='',counted=0;
  const detail=holdings.map(h=>{const entry=h.entry_price,now=D.current_prices[h.ticker];
    if(typeof entry!=='number'||!isFinite(entry)||entry<=0||typeof now!=='number')return {t:h.ticker,entry,now,ret:null};
    const shares=amt/entry,val=shares*now,ret=(now-entry)/entry; invested+=amt;current+=val;counted++;
    return {t:h.ticker,entry,now,ret,val};
  }).sort((a,b)=>(b.ret??-9)-(a.ret??-9));
  detail.forEach(d=>{const rc=d.ret==null?'':(d.ret>=0?'g':'r-loss');
    rows+=`<div class="bt-row"><div>${d.t}</div>
      <div class="hide-sm">${d.entry?'$'+d.entry.toFixed(2):'—'} → ${typeof d.now==='number'?'$'+d.now.toFixed(2):'—'}</div>
      <div class="r ${rc}">${d.ret==null?'n/a':((d.ret>=0?'+':'')+(d.ret*100).toFixed(1)+'%')}</div>
      <div class="r hide-sm">${d.val!=null?fmtMoney(d.val):'—'}</div>
      <div class="r ${rc}">${d.ret!=null?(d.ret>=0?'+':'')+fmtMoney(d.val-amt):'—'}</div></div>`;});
  const pnl=current-invested,pct=invested>0?(pnl/invested*100):0,cls=pnl>=0?'gain':'loss';
  summary.innerHTML=`
    <div class="stat"><div class="k">Invested</div><div class="val">${fmtMoney(invested)}</div></div>
    <div class="stat"><div class="k">Current value</div><div class="val">${fmtMoney(current)}</div></div>
    <div class="stat"><div class="k">Profit / loss</div><div class="val ${cls}">${pnl>=0?'+':''}${fmtMoney(pnl)}</div></div>
    <div class="stat"><div class="k">Return</div><div class="val ${cls}">${pct>=0?'+':''}${pct.toFixed(1)}%</div></div>`;
  table.innerHTML=`<div class="bt-row head"><div>#</div><div class="hide-sm">Entry → Now</div><div class="r">Return</div><div class="r hide-sm">Value</div><div class="r">P/L</div></div>`
    +rows+`<p class="intro" style="font-size:12px;color:var(--faint);margin-top:8px;">${counted} of ${holdings.length} priced. Held ${amt?('$'+amt+'/stock'):''} from ${date} to latest scan.</p>`;
}
[btList,btDate,btAmount].forEach(e=>e.addEventListener('input',runBacktest));

// init
renderGroups(); renderSubviews(); renderRows();
if(dates.length)runBacktest();
</script>
"""

if __name__ == "__main__":
    main()
