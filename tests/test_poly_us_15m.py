"""Wire-shape and fail-closed tests for official US BTC 15m public BBO."""
from datetime import datetime, timezone
from api.poly_us_15m import current_window,public_btc15m_bbo,BTC15mUnavailable
import pytest
AT=datetime(2026,10,12,2,16,0,tzinfo=timezone.utc)
SLUG="cpc-btc-updown-15m-2026-10-12-0215z"
EVENT="btc-updown-15m-2026-10-12-0215z"
class Events:
    def __init__(self):
        self.obj={"event":{"slug":EVENT,"active":True,"closed":False,
            "markets":[{"slug":SLUG,"active":True,"closed":False}]}}
    def retrieve_by_slug(self,s):
        assert s==EVENT
        return self.obj
class Markets:
    def __init__(self):
        self.obj={"marketData":{"marketSlug":SLUG,"state":"MARKET_STATE_OPEN",
            "bestBid":{"value":"0.69","currency":"USD"},
            "bestAsk":{"value":"0.70","currency":"USD"},
            "bidDepth":40,"askDepth":30}}
    def bbo(self,s):
        assert s==SLUG
        return self.obj
class Client:
    def __init__(self):
        self.events=Events()
        self.markets=Markets()
    def close(self):raise AssertionError("external SDK instance should not be closed")
def test_utc_bucket_across_dates():
    w=current_window(AT)
    assert w["market_slug"]==SLUG
    assert w["end_utc"]=="2026-10-12T02:30:00+00:00"
    w=current_window(datetime(2026,10,11,23,59,59,tzinfo=timezone.utc))
    assert w["market_slug"]=="cpc-btc-updown-15m-2026-10-11-2345z"
def test_live_15m_bbo_is_real_public_but_not_executable():
    result=public_btc15m_bbo(client=Client(),clock=AT)
    assert result["best_bid"]==.69 and result["best_ask"]==.70
    assert result["quote_integrity_valid"] is True
    assert result["vendor_exchange_time_verified"] is False
    assert result["depth_units_verified_as_executable_contracts"] is False
    assert result["independent_probability_estimate"] is None
    assert result["execution_enabled"] is False
    assert result["orders_submitted"]==0
def test_event_mismatch_fail_closed():
    client=Client()
    client.events.obj["event"]["slug"]="btc-updown-15m-other"
    with pytest.raises(BTC15mUnavailable,match="identity"):
        public_btc15m_bbo(client=client,clock=AT)
def test_missing_bbo_blocks_market():
    client=Client()
    client.markets.obj["marketData"]["bestAsk"]=None
    out=public_btc15m_bbo(client=client,clock=AT)
    assert out["quote_integrity_valid"] is False
    assert out["decision"].startswith("ABSTAIN")
def test_crossed_book_blocks_market():
    client=Client()
    client.markets.obj["marketData"]["bestAsk"]={"value":"0.30","currency":"USD"}
    assert public_btc15m_bbo(client=client,clock=AT)["quote_integrity_valid"] is False
def test_closed_market_blocks_market():
    client=Client()
    client.events.obj["event"]["closed"]=True
    out=public_btc15m_bbo(client=client,clock=AT)
    assert out["quote_integrity_valid"] is False
    assert out["execution_enabled"] is False
def test_depth_field_is_not_assumed_tradable_qty():
    client=Client()
    client.markets.obj["marketData"]["bidDepth"]=-1
    result=public_btc15m_bbo(client=client,clock=AT)
    assert result["quote_integrity_valid"] is False
    assert result["depth_units_verified_as_executable_contracts"] is False
def test_naive_clock_rejected():
    with pytest.raises(ValueError,match="timezone"):
        public_btc15m_bbo(client=Client(),clock=datetime(2026,10,12,2,16))
