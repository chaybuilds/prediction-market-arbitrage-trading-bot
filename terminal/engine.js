/* Real fork Python and NEXUS Worldline connector; no order routes. */
(function(){
"use strict";
const API="https://chay-trades-arb-api.vercel.app",dock=document.getElementById("engineDock");
if(!dock)return;
dock.innerHTML='<div class="panel-kicker">ASSIMILATED SOURCE / ENGINE CORE V0.2</div><h2>Original code. Real integration gates.</h2><p class="engine-notice">Run original Synpath fork pricing code with deterministic example books. Private NEXUS Worldline analyzes risk separately. Data is synthetic until market feeds, settlement rules and entitlements are verified.</p><div class="engine-grid"><div class="engine-status" id="forkState"><small>ORIGINAL PRICER / STRATEGY</small><strong>CHECKING</strong></div><div class="engine-status" id="marketState"><small>CROSS-VENUE MATCHING</small><strong>NOT CONNECTED</strong></div><div class="engine-status" id="nexusState"><small>PRIVATE NEXUS WORLDLINE</small><strong>NOT CONNECTED</strong></div></div><div class="engine-controls"><button type="button" class="button primary" id="runFork">RUN FORK PRICER ↗</button><button type="button" class="button secondary" id="runWorldline">CHALLENGE WITH NEXUS →</button></div><p class="engine-notice" id="engineMessage" role="status" aria-live="polite">Checking read-only Python API.</p><div class="engine-results" id="engineResults"></div><p class="engine-notice">NOT a backtest, not live quotes, not trading signals and not economic proof. No orders or funds can be moved.</p>';
const $=id=>document.getElementById(id);
let cases=[];
function msg(t){$("engineMessage").textContent=t;}
function stamp(id,text){$(id).querySelector("strong").textContent=text;}
async function read(path){const r=await fetch(API+path,{cache:"no-store",mode:"cors",signal:AbortSignal.timeout(14000)});if(!r.ok)throw new Error("read-only API HTTP "+r.status);return r.json();}
async function status(){
try{const x=await read("/api/engine/status");if(x.execution_enabled!==false)throw Error("unsafe execution flag");
stamp("forkState",x.fork_pricing?.integrated?"REAL CODE ONLINE":"UNVERIFIED");
stamp("marketState",x.cross_venue_market_matching?.integrated?"VERIFIED":"VENDOR KEY REQUIRED");
stamp("nexusState",x.nexus_worldline?.configured?"CONFIGURED / UNVERIFIED":"PRIVATE / NOT LINKED");
msg("Engine status checked. Actual original pricing module "+(x.fork_pricing?.integrated?"loaded and exercised":"not verified")+". No exchange account or trade connection.");}
catch(e){stamp("forkState","API UNREACHABLE");msg("No verified engine connection: "+String(e.message).slice(0,110));}
}
async function fork(){
$("runFork").disabled=true;msg("Running original fork Pricer and Strategy on 6 synthetic order-book cases...");
try{const x=await read("/api/engine/fixtures");if(x.synthetic!==true||x.execution_enabled!==false||x.source!=="FORK_ENGINE_WITH_SYNTHETIC_INPUTS"||!Array.isArray(x.opportunities))throw Error("unexpected engine contract");
cases=x.opportunities.filter(c=>c.source==="FORK_FIXTURE"&&c.evidence_status==="SIMULATED_UNVERIFIED");
if(!cases.length)throw Error("no original module calculations");
$("engineResults").replaceChildren(...cases.map(c=>{const n=document.createElement("span");n.className="engine-chip";n.textContent=c.short+" / "+(Number(c.edge)*100).toFixed(2)+" cents SIMULATED";return n;}));
document.dispatchEvent(new CustomEvent("nexus-engine-fixtures",{detail:x}));
stamp("forkState","ORIGINAL MODULE VERIFIED");msg("Original Python fork executed "+cases.length+" synthetic cases. The Scanner now displays only labeled fork fixtures.");}
catch(e){msg("Could not verify the original engine: "+String(e.message).slice(0,160));}
finally{$("runFork").disabled=false;}
}
async function nexus(){
$("runWorldline").disabled=true;
try{if(!cases.length)await fork();if(!cases.length)throw Error("run fork fixture first");
const x=await read("/api/engine/intelligence/"+encodeURIComponent(cases[0].id)),a=x.advisory;
if(x.execution_enabled!==false||a?.authority!=="ADVISORY_ONLY"||a.executionEnabled!==false||a.worldline?.schema!=="nexus.worldline.forecast.v1")throw Error("invalid private Worldline authority");
stamp("nexusState","WORLDLINE VERIFIED");msg("Private NEXUS Worldline: "+a.decision+" | "+a.blockers.join(", ")+" | forecast "+a.worldline.forecastHash.slice(0,16)+"... Hypothetical only.");}
catch(e){msg("NEXUS runtime not verified: "+String(e.message).slice(0,150)+". No trade or capital permission.");}
finally{$("runWorldline").disabled=false;}
}
$("runFork").addEventListener("click",fork);$("runWorldline").addEventListener("click",nexus);status();
})();