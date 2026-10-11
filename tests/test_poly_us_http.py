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
