"""Contract tests use official SDK wire response shapes with no network or keys."""
from datetime import datetime,timezone,timedelta
import pytest
from api.poly_us import public_quote, public_catalog, connection_status, require_slug, PublicQuoteUnavailable

AT=datetime(2026,10,11,16,35,tzinfo=timezone.utc)
SLUG="cpc-btc-150k-12-31-2026"
class Markets:
    def __init__(self):
        self.info={"market":{"slug":SLUG,"question":"Synthetic wire market"}}
        self.bookdata={"marketData":{"marketSlug":SLUG,"bids":[
            {"px":{"value":"0.04","currency":"USD"},"qty":"120"},
            {"px":{"value":"0.03","currency":"USD"},"qty":"90"}],
            "offers":[{"px":{"value":"0.05","currency":"USD"},"qty":"80"}],
            "state":"MARKET_STATE_OPEN","transactTime":"2026-10-11T16:34:55Z"}}
    def retrieve_by_slug(self,slug):
        assert slug == SLUG
        return self.info
    def book(self,slug):
        assert slug==SLUG
        return self.bookdata
    def list(self,params):
        return {"markets":[{"slug":SLUG,"question":"Synthetic wire market","active":True,"closed":False}]}
class Client:
    def __init__(self):self.markets=Markets()
    def close(self):raise AssertionError("should not close injected test SDK")
def test_market_quote_official_book_wire_shape():
    out=public_quote(SLUG,client=Client(),clock=AT)
    assert out["source"]=="POLYMARKET_US_OFFICIAL_PUBLIC_SDK"
    assert out["best_yes_bid"]==.04
    assert out["best_yes_ask"]==.05
    assert out["best_ask_quantity"]==80
    assert out["quote_freshness_valid"] is True
    assert out["usable_for_research"] is True
    assert out["execution_enabled"] is False
    assert out["cross_venue_arb_evaluated"] is False
def test_empty_or_stale_quotes_not_actionable():
    c=Client()
    c.markets.bookdata["marketData"]["transactTime"]=None
    q=public_quote(SLUG,client=c,clock=AT)
    assert q["usable_for_research"] is False
    assert q["quote_freshness_valid"] is False
    c.markets.bookdata["marketData"]["transactTime"]=(AT-timedelta(minutes=6)).isoformat()
    assert public_quote(SLUG,client=c,clock=AT)["usable_for_research"] is False
def test_nonexistent_depth_or_locked_book_fail_closed():
    c=Client()
    c.markets.bookdata["marketData"]["state"]="MARKET_STATE_CLOSED"
    assert public_quote(SLUG,client=c,clock=AT)["usable_for_research"] is False
    c.markets.bookdata["marketData"]["offers"]=[]
    assert public_quote(SLUG,client=c,clock=AT)["best_yes_ask"] is None
def test_bbo_rejects_nonfinite_and_wrong_currency():
    c=Client()
    c.markets.bookdata["marketData"]["bids"]=[{"px":{"value":"NaN","currency":"USD"},"qty":"5"}]
    assert public_quote(SLUG,client=c,clock=AT)["best_yes_bid"] is None
    c.markets.bookdata["marketData"]["bids"]=[{"px":{"value":"0.04","currency":"EUR"},"qty":"5"}]
    assert public_quote(SLUG,client=c,clock=AT)["best_yes_bid"] is None
def test_slug_is_bounded_and_cannot_redirect_to_external_host():
    for bad in ("../secret", "https://example.com", "ABC/CDE", "a"*200, "%2e%2e"):
        with pytest.raises(ValueError):require_slug(bad)
def test_catalog_does_not_authenticate():
    c=Client()
    got=public_catalog(limit=8,client=c)
    assert len(got["markets"])==1
    assert got["public_only"] is True
    assert got["execution_enabled"] is False
def test_account_key_presence_never_grants_trade_permission(monkeypatch):
    monkeypatch.setenv("POLYMARKET_KEY_ID","fake-uuid")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY","fake-secret")
    conn=connection_status()
    assert conn["account_key_id_configured"] and conn["account_secret_configured"]
    assert conn["account_verified"] is False
    assert conn["execution_enabled"] is False

def test_catalog_excludes_inactive_resolved_markets():
    c=Client()
    def combined(params):
        assert params["active"] is True
        assert params["closed"] is False
        assert params["categories"]==["crypto"]
        return {"markets":[
            {"slug":"aec-nfl-lac-ten-2025-11-02","active":False,"closed":True,"title":"Old game"},
            {"slug":SLUG,"active":True,"closed":False,"title":"Before January 2027"}]}
    c.markets.list=combined
    rows=public_catalog(limit=8,client=c)["markets"]
    assert len(rows)==1
    assert rows[0]["slug"]==SLUG
    assert SLUG in rows[0]["title"]
