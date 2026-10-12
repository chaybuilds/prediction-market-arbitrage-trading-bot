"""Official US web routes are real market research only; no account permission grants."""
from fastapi.testclient import TestClient
from api.index import app
from api import poly_us

def test_us_http_feature_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("POLY_US_PUBLIC_RESEARCH_ENABLED",raising=False)
    test=TestClient(app)
    assert test.get("/api/poly-us/markets").status_code==503
    assert test.get("/api/poly-us/quote/cpc-btc-150k-12-31-2026").status_code==503
    assert test.get("/api/poly-us/witness/cpc-btc-150k-12-31-2026").status_code==503
    status=test.get("/api/poly-us/status").json()
    assert status["public_research_configured"] is False
    assert status["execution"]["kill_switch"] is True
    assert status["account_verified"] is False
def test_us_http_public_quote_and_witness_remain_readonly(monkeypatch):
    from datetime import datetime,timezone
    time=datetime(2026,10,11,17,0,tzinfo=timezone.utc).isoformat()
    slug="cpc-btc-150k-12-31-2026"
    record={"schema":"chaytrades.poly_us.public_quote.v1","venue":"POLYMARKET_US",
      "market_slug":slug,"source":"POLYMARKET_US_OFFICIAL_PUBLIC_SDK",
      "title":"Test market","observed_at":time,"exchange_transact_at":time,
      "market_state":"MARKET_STATE_OPEN","best_yes_bid":.04,"best_yes_ask":.05,
      "best_bid_quantity":200,"best_ask_quantity":100,"quote_freshness_valid":True,
      "usable_for_research":True,"execution_enabled":False}
    monkeypatch.setenv("POLY_US_PUBLIC_RESEARCH_ENABLED","true")
    monkeypatch.setattr(poly_us,"public_quote",lambda s: record)
    monkeypatch.setattr(poly_us,"public_catalog",lambda **args:{"markets":[{"slug":slug}],
      "source":"POLYMARKET_US_OFFICIAL_PUBLIC_SDK","execution_enabled":False})
    client=TestClient(app)
    assert client.get("/api/poly-us/markets").status_code==200
    assert client.get("/api/poly-us/quote/"+slug).json()["best_yes_bid"]==.04
    out=client.get("/api/poly-us/witness/"+slug)
    assert out.status_code==200
    body=out.json()
    assert body["decision"]["decision"]=="ABSTAIN"
    assert body["observation"]["event_type"]=="QUOTE_OBSERVED"
    assert body["stored_server_side"] is False
    assert body["execution_enabled"] is False
    assert client.get("/api/poly-us/execution/status").json()["auto_trading"] is False


def test_official_us_to_private_nexus_is_advisory_only(monkeypatch):
    import api.index as route
    import httpx
    slug="cpc-btc-150k-12-31-2026"
    origin="https://chay-trades-arb-terminal.vercel.app"
    monkeypatch.setattr(route,"allowed_origins",[origin])
    monkeypatch.setenv("POLY_US_PUBLIC_RESEARCH_ENABLED","true")
    monkeypatch.setenv("NEXUS_ADVISORY_URL","https://private-nexus.example.com")
    monkeypatch.setenv("NEXUS_ADVISORY_SECRET","test-nexus-secret-xxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setenv("NEXUS_DEPLOYMENT_BYPASS","test-protected-xxxxxxxxxxxxxxxxxxxx")
    fake={"venue":"POLYMARKET_US","source":"POLYMARKET_US_OFFICIAL_PUBLIC_SDK",
          "market_slug":slug,"observed_at":"2026-10-11T17:00:00Z",
          "exchange_transact_at":"2026-10-11T16:59:40Z","best_yes_bid":.03,
          "best_yes_ask":.04,"market_state":"MARKET_STATE_OPEN",
          "quote_freshness_valid":True,"usable_for_research":True}
    monkeypatch.setattr(route,"poly_us_quote",lambda s:fake)
    seen={}
    def fake_post(url,headers,json,timeout,follow_redirects):
        seen.update({"url":url,"headers":headers,"payload":json})
        assert follow_redirects is False
        return type("Response",(),{"raise_for_status":lambda self:None,
          "json":lambda self:{"schema":"nexus.oracle.poly_us_observation.v1",
            "market_slug":slug,"source":"POLYMARKET_US_OFFICIAL_PUBLIC_SDK",
            "authority":"ADVISORY_ONLY","executionEnabled":False,
            "decision":"ABSTAIN_NO_CALIBRATED_MODEL",
            "worldline":{"schema":"nexus.worldline.forecast.v1"}}})()
    monkeypatch.setattr(httpx,"post",fake_post)
    response=TestClient(app).get("/api/poly-us/nexus/"+slug,headers={"origin":origin})
    assert response.status_code==200
    out=response.json()
    assert out["private_intelligence_connected"] is True
    assert out["execution_enabled"] is False
    assert "test-protected" not in str(out)
    assert seen["payload"]["source"]=="POLYMARKET_US_OFFICIAL_PUBLIC_SDK"
    assert seen["headers"]["Authorization"].startswith("Bearer ")
    assert "x-vercel-protection-bypass" in seen["headers"]

def test_us_to_nexus_rejects_spoofed_origin_and_stale_quote(monkeypatch):
    import api.index as route
    slug="cpc-btc-150k-12-31-2026"
    origin="https://chay-trades-arb-terminal.vercel.app"
    monkeypatch.setattr(route,"allowed_origins",[origin])
    monkeypatch.setenv("POLY_US_PUBLIC_RESEARCH_ENABLED","true")
    monkeypatch.setenv("NEXUS_ADVISORY_URL","https://private-nexus.example.com")
    monkeypatch.setenv("NEXUS_ADVISORY_SECRET","test-nexus-secret-xxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setenv("NEXUS_DEPLOYMENT_BYPASS","test-protected-xxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setattr(route,"poly_us_quote",lambda s:{"usable_for_research":False})
    client=TestClient(app)
    assert client.get("/api/poly-us/nexus/"+slug,headers={"origin":"https://badhost.com"}).status_code==403
    assert client.get("/api/poly-us/nexus/"+slug,headers={"origin":origin}).status_code==409


def test_current_btc15m_route_is_public_but_permanently_readonly(monkeypatch):
    import api.index as route
    import api.poly_us_15m as observer
    client=TestClient(app)
    monkeypatch.delenv("POLY_US_PUBLIC_RESEARCH_ENABLED",raising=False)
    assert client.get("/api/poly-us/btc15m/current").status_code==503
    monkeypatch.setenv("POLY_US_PUBLIC_RESEARCH_ENABLED","true")
    monkeypatch.setattr(observer,"public_btc15m_bbo",lambda:{
      "schema":"chaytrades.poly_us.btc15m.bbo.v1",
      "venue":"POLYMARKET_US","source":"POLYMARKET_US_OFFICIAL_PUBLIC_BBO_SDK",
      "best_bid":0.50,"best_ask":0.52,
      "vendor_exchange_time_verified":False,
      "depth_units_verified_as_executable_contracts":False,
      "decision":"ABSTAIN_NO_CALIBRATED_SIGNAL","orders_submitted":0,
      "execution_enabled":False})
    result=client.get("/api/poly-us/btc15m/current")
    assert result.status_code==200
    assert result.json()["orders_submitted"]==0
    assert result.json()["vendor_exchange_time_verified"] is False
