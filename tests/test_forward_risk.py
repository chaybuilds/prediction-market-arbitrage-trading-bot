from datetime import datetime,timezone,timedelta
import pytest
from api.paper_forward import observation_event,assess_paper_intent,forward_mark
from api.execution_guard import construct_order_preflight, execution_status, POLICY

TIME=datetime(2026,10,11,16,40,tzinfo=timezone.utc).isoformat()
Q={"source":"POLYMARKET_US_OFFICIAL_PUBLIC_SDK","venue":"POLYMARKET_US",
   "market_slug":"cpc-btc-150k-12-31-2026","observed_at":TIME,
   "exchange_transact_at":TIME,"best_yes_bid":.04,"best_yes_ask":.05,
   "best_bid_quantity":120,"best_ask_quantity":80,"market_state":"MARKET_STATE_OPEN",
   "quote_freshness_valid":True,"usable_for_research":True}
def test_forward_quote_witness_is_deterministic_and_abstains():
    a,b=observation_event(Q),observation_event(Q)
    assert a["event_sha256"]==b["event_sha256"]
    assert a["decision"]=="ABSTAIN_NO_VALIDATED_SIGNAL"
    assert a["real_fills_observed"] is False
def test_forward_paper_abstains_without_calibrated_model():
    x=assess_paper_intent(Q,contracts=1)
    assert x["decision"]=="ABSTAIN"
    assert "NO_INDEPENDENT_PROBABILITY_ESTIMATE" in x["blockers"]
    assert "RISK_LEDGER_UNAVAILABLE" in x["blockers"]
def test_paper_hypothesis_does_not_mean_real_execution():
    x=assess_paper_intent(Q,assumed_probability=.35,contracts=1,
         loss_today=0,gross_exposure=0)
    assert x["decision"]=="PAPER_HYPOTHESIS_ELIGIBLE"
    assert x["forecast_type"]=="USER_ASSUMED_NOT_CALIBRATED"
    assert x["execution_enabled"] is False
    assert x["hypothetical_fill_status"]=="NOT_OBSERVED"
def test_exposure_and_hypothetical_daily_loss_limits():
    assert "DAILY_LOSS_LIMIT" in assess_paper_intent(
       Q,assumed_probability=.5,loss_today=3,gross_exposure=0)["blockers"]
    assert "GROSS_EXPOSURE_LIMIT" in assess_paper_intent(
       Q,assumed_probability=.5,loss_today=0,gross_exposure=9.99)["blockers"]
def test_forward_mark_requires_later_quote():
    event=observation_event(Q)
    with pytest.raises(ValueError,match="non_forward_mark"):forward_mark(event,Q)
    future={**Q,"observed_at":(datetime.fromisoformat(TIME)+timedelta(hours=1)).isoformat(),
           "best_yes_bid":.06}
    out=forward_mark(event,future)
    assert out["modeled_bid_to_ask_move_usd"]==pytest.approx(.01)
    assert out["fill_verified"] is False
    assert out["position_opened"] is False
def test_live_execution_stays_disabled_even_if_self_asserted_all_green():
    risk={"loss_today":0,"gross_exposure":0,"open_orders":0,"account_authenticated":True,
          "positions_reconciled":True,"balances_reconciled":True,
          "operator_approved":True,"calibration_proof":True,
          "market_rules_independently_verified":True}
    plan=construct_order_preflight(Q,forecast_probability=.9,risk_state=risk)
    assert plan["final_decision"]=="REJECT_LIVE_ORDER"
    assert "GLOBAL_KILL_SWITCH_ACTIVE" in plan["blockers"]
    assert plan["execution_enabled"] is False
    assert plan["orders_sent"]==0
    assert plan["exchange_order_id"] is None
    assert execution_status()["auto_trading"] is False
