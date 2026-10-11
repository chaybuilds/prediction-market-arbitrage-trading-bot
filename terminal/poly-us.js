/* Official Polymarket US - unauthenticated public data and manual forward paper observations. */
(function(){
"use strict";
const API="https://chay-trades-arb-api.vercel.app";
const STORE="chaytrades_poly_us_forward_paper_v1";
const $=id=>document.getElementById(id);
if(!$("polyUSPanel"))return;
let current=null;
let records=[];
try{const x=JSON.parse(localStorage.getItem(STORE)||"[]");if(Array.isArray(x))records=x.slice(-80)}catch{}
function info(s){$("polyUSMessage").textContent=s}
function cents(v){return typeof v==="number"&&Number.isFinite(v)?(v*100).toFixed(2)+"c":"NO BID / ASK"}
function fmt(t){try{return new Date(t).toLocaleString()}catch{return "unknown"}}
async function json(path){
  const r=await fetch(API+path,{method:"GET",mode:"cors",cache:"no-store",signal:AbortSignal.timeout(12500)});
  if(!r.ok)throw Error("read-only US research API HTTP "+r.status);
  return r.json();
}
function renderRecords(){
  $("polyWitList").replaceChildren();
  const selected=$("polyUSMarkets").value;
  const filtered=records.filter(r=>r.market_slug===selected).slice(-4).reverse();
  if(!filtered.length)return;
  for(const item of filtered){
    const el=document.createElement("div");
    el.className="poly-us-witness";
    const text="FORWARD WITNESS / "+fmt(item.observed_at)+" / ASK "+cents(item.ask)+
      " / BID "+cents(item.bid)+" / "+item.decision+
      (item.forward_mark_cents===null?"":" / NEXT-QUOTE MOVE "+Number(item.forward_mark_cents).toFixed(2)+"c");
    el.textContent=text+" / NO FILL VERIFIED, NO REALIZED PNL";
    $("polyWitList").appendChild(el);
  }
}
async function loadStatus(){
  try{
    const s=await json("/api/poly-us/status");
    if(s.execution_enabled!==false||s.execution?.auto_trading!==false)throw Error("unsafe order declaration");
    $("polyAccountState").textContent=s.account_key_id_configured&&s.account_secret_configured?
      "KEY METADATA CONFIGURED / ACCOUNT NOT VERIFIED":"ACCOUNT KEYS NOT CONNECTED";
    $("polyUSStatus").textContent=s.public_research_configured?"US PUBLIC FEED / GATED":"US FEED NOT ACTIVATED";
  }catch(err){
    $("polyUSStatus").textContent="PUBLIC API NOT VERIFIED";
    info("US research service unreachable: "+String(err.message).slice(0,120));
  }
}
async function loadMarkets(){
  $("polyLoad").disabled=true;
  info("Loading official Polymarket US public markets. No personal API credentials are used.");
  try{
    const x=await json("/api/poly-us/markets?limit=8");
    if(x.source!=="POLYMARKET_US_OFFICIAL_PUBLIC_SDK"||x.execution_enabled!==false||!Array.isArray(x.markets))throw Error("unrecognized public data source");
    const select=$("polyUSMarkets");
    const previous=select.value;
    select.replaceChildren();
    if(!x.markets.length)throw Error("Official source returned no usable market slugs");
    for(const m of x.markets){
      if(typeof m.slug!=="string"||!/^[a-z0-9][a-z0-9-]{3,139}$/.test(m.slug))continue;
      const opt=document.createElement("option");
      opt.value=m.slug;
      opt.textContent=String(m.title||m.slug).slice(0,85);
      select.appendChild(opt);
    }
    if(!select.options.length)throw Error("No supported market entries");
    if([...select.options].some(x=>x.value===previous))select.value=previous;
    $("polyUSStatus").textContent="OFFICIAL US / PUBLIC";
    info("Received "+select.options.length+" official US market listings. Prices are not predictions or trading signals.");
    renderRecords();
  }catch(e){info("Polymarket US public catalog not available: "+String(e.message).slice(0,120));}
  finally{$("polyLoad").disabled=false}
}
async function inspect(){
  const slug=$("polyUSMarkets").value;
  if(!/^[a-z0-9][a-z0-9-]{3,139}$/.test(slug)){info("Select a current official US market first.");return}
  $("polyInspect").disabled=true;
  info("Reading public L2 order book from official Polymarket US SDK...");
  try{
    const q=await json("/api/poly-us/quote/"+encodeURIComponent(slug));
    if(q.schema!=="chaytrades.poly_us.public_quote.v1"||q.source!=="POLYMARKET_US_OFFICIAL_PUBLIC_SDK"||q.execution_enabled!==false||q.market_slug!==slug)throw Error("invalid market quote provenance");
    current=q;
    $("polyUSQuote").hidden=false;
    $("polyUSQuoteTitle").textContent=q.title+" / "+q.market_slug;
    $("polyUSBid").textContent=cents(q.best_yes_bid);
    $("polyUSAsk").textContent=cents(q.best_yes_ask);
    $("polyUSSpread").textContent=cents(q.spread);
    $("polyUSAge").textContent=typeof q.exchange_age_seconds==="number"?q.exchange_age_seconds.toFixed(1)+" seconds":"NOT ATTESTED";
    $("polyUSQuoteState").textContent=q.usable_for_research?"PUBLIC ORDER BOOK / UNVERIFIED EXECUTION":"UNUSABLE / STALE / CLOSED";
    $("polyUSStatus").textContent=q.usable_for_research?"OFFICIAL US / READ-ONLY":"QUOTE NOT ACTIONABLE";
    info("Observed "+fmt(q.observed_at)+". "+(q.usable_for_research?"Research quote validated for age and visible book spread.":"Freshness/depth/market state gate failed.")+" Zero orders placed.");
    renderRecords();
  }catch(e){current=null;$("polyUSQuote").hidden=true;info("No verified public quote: "+String(e.message).slice(0,155));}
  finally{$("polyInspect").disabled=false}
}
async function witness(){
  if(!current||!current.usable_for_research){info("First load a usable, fresh public quote.");return}
  $("polyWitness").disabled=true;
  try{
    const data=await json("/api/poly-us/witness/"+encodeURIComponent(current.market_slug));
    const o=data.observation,decision=data.decision;
    if(!o||o.event_type!=="QUOTE_OBSERVED"||o.source!=="POLYMARKET_US_OFFICIAL_PUBLIC_SDK"||data.execution_enabled!==false||
       !decision||decision.execution_enabled!==false)throw Error("forward witness provenance mismatch");
    if(!o.research_usable){info("Current exchange quote stale; no paper observation recorded.");return}
    if(records.some(r=>r.event_id===o.event_id)){info("Already saved this exact observation.");return}
    const earlier=records.filter(r=>r.market_slug===o.market_slug&&Date.parse(r.observed_at)<Date.parse(o.observed_at)).slice(-1)[0];
    const lastAsk=earlier?.ask,lastBid=o.bid;
    const delta=typeof lastAsk==="number"&&typeof lastBid==="number"?(lastBid-lastAsk)*100:null;
    records.push({event_id:o.event_id,market_slug:o.market_slug,observed_at:o.observed_at,
      ask:o.ask,bid:o.bid,source:o.source,decision:o.decision,forward_mark_cents:delta});
    records=records.slice(-80);
    try{localStorage.setItem(STORE,JSON.stringify(records))}
    catch{info("Browser storage unavailable; observation not persisted.");return}
    renderRecords();
    info("Saved forward-only public quote in THIS BROWSER. Decision: ABSTAIN (no verified signal). "+
      (delta===null?"Await a later quote to calculate a conservative observation-to-observation mark.":"Later bid-to-earlier ask change recorded; NOT a fill or realized profit.")+
      " Server-side ledger is not yet active.");
  }catch(e){info("Forward paper witness failed closed: "+String(e.message).slice(0,140));}
  finally{$("polyWitness").disabled=false;}
}
$("polyLoad").addEventListener("click",loadMarkets);
$("polyInspect").addEventListener("click",inspect);
$("polyWitness").addEventListener("click",witness);
$("polyUSMarkets").addEventListener("change",()=>{current=null;$("polyUSQuote").hidden=true;renderRecords();});
loadStatus();loadMarkets();
})();
