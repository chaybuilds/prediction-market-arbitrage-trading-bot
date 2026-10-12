/* Official US 15-minute BTC observer; browser NEVER sees account keys. */
(() => {
  "use strict";
  const API="https://chay-trades-arb-api.vercel.app";
  const $=id=>document.getElementById(id);
  if (!$("btc15Panel")) return;
  const cents=v=>typeof v==="number"&&Number.isFinite(v)?(v*100).toFixed(2)+"c":"NO QUOTE";
  const time=v=>typeof v==="string"?new Date(v).toLocaleString():"unknown";
  async function observe() {
    $("btc15Refresh").disabled=true;
    $("btc15Message").textContent="Reading current official US BTC 15-minute BBO; no personal account connection is used...";
    try {
      const response=await fetch(API+"/api/poly-us/btc15m/current",{
        method:"GET",cache:"no-store",mode:"cors",signal:AbortSignal.timeout(14000)
      });
      if(!response.ok)throw Error("official US feed HTTP "+response.status);
      const item=await response.json();
      if(item.schema!=="chaytrades.poly_us.btc15m.bbo.v1"||
         item.venue!=="POLYMARKET_US"||item.source!=="POLYMARKET_US_OFFICIAL_PUBLIC_BBO_SDK"||
         item.execution_enabled!==false||item.orders_submitted!==0||
         item.vendor_exchange_time_verified!==false||
         item.depth_units_verified_as_executable_contracts!==false){
        throw Error("unrecognized or unsafe market payload");
      }
      const slug=item.event_slug;
      if(typeof slug!=="string"||!/^[a-z0-9-]+$/.test(slug))throw Error("bad vendor event slug");
      $("btc15EventLink").href="https://polymarket.us/event/"+slug;
      $("btc15Snapshot").hidden=false;
      $("btc15Bid").textContent=cents(item.best_bid);
      $("btc15Ask").textContent=cents(item.best_ask);
      $("btc15Spread").textContent=cents(item.spread);
      $("btc15Depth").textContent=String(item.vendor_bid_depth_field??"--")+" / "+String(item.vendor_ask_depth_field??"--");
      $("btc15Window").textContent=time(item.start_utc)+" → "+time(item.end_utc);
      $("btc15Slug").textContent=item.market_slug;
      $("btc15Validity").textContent=item.quote_integrity_valid?"PUBLIC BBO INTEGRITY PASS":"BOOK / STATE NOT VERIFIED";
      $("btc15State").textContent=item.quote_integrity_valid?"PUBLIC 15M BBO LIVE":"15M MARKET NOT ACTIONABLE";
      $("btc15Message").textContent="Observed at "+time(item.observed_at)+". Decision: "+item.decision+". BBO source timestamp is unavailable; depth fields do not establish fillable quantity.";
    } catch(e) {
      $("btc15Snapshot").hidden=true;
      $("btc15State").textContent="PUBLIC FEED UNVERIFIED";
      $("btc15Message").textContent="No confirmed 15-minute public quote: "+String(e.message||e).slice(0,140)+". No order sent.";
    } finally {$("btc15Refresh").disabled=false;}
  }
  $("btc15Refresh").addEventListener("click",observe);
  observe();
})();
