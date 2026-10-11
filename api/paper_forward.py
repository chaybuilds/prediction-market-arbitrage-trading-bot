"""Forward-only paper research: modeled prices, NOT fills or execution.

All predictions, unobserved costs, and model marks remain epistemically separate.
No persistent filesystem writes or exchange actions.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA = "chaytrades.poly_us.forward_paper.v1"
DEFAULT_POLICY = {
    "max_contracts_per_idea": 2,
    "max_gross_exposure_usd": 10.0,
    "max_daily_hypothetical_loss_usd": 3.0,
    "max_quote_age_seconds": 90,
    "minimum_assumed_margin": 0.03,
    "max_spread": 0.06,
    "assumed_fee_per_contract": 0.01,
    "assumed_slippage_per_contract": 0.01,
}
def _utc(value: Any) -> datetime:
    if not isinstance(value,str):
        raise ValueError("missing utc timestamp")
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)

def _dec(value: Any) -> Decimal:
    if isinstance(value,bool):
        raise ValueError("bad decimal")
    try:
        d=Decimal(str(value))
    except (TypeError,ValueError,InvalidOperation) as exc:
        raise ValueError("bad decimal") from exc
    if not d.is_finite():
        raise ValueError("nonfinite decimal")
    return d

def observation_event(snapshot: dict[str,Any]) -> dict[str,Any]:
    if snapshot.get("source")!="POLYMARKET_US_OFFICIAL_PUBLIC_SDK" or snapshot.get("venue")!="POLYMARKET_US":
        raise ValueError("not a vetted public US quote")
    slug=snapshot.get("market_slug")
    if not isinstance(slug,str) or not slug:
        raise ValueError("missing slug")
    observed=_utc(snapshot["observed_at"])
    base={
        "schema":SCHEMA,"event_type":"QUOTE_OBSERVED",
        "venue":"POLYMARKET_US","market_slug":slug,
        "observed_at":observed.isoformat(),"exchange_transact_at":snapshot.get("exchange_transact_at"),
        "bid":snapshot.get("best_yes_bid"),"ask":snapshot.get("best_yes_ask"),
        "bid_qty":snapshot.get("best_bid_quantity"),"ask_qty":snapshot.get("best_ask_quantity"),
        "quote_freshness_valid":snapshot.get("quote_freshness_valid") is True,
        "research_usable":snapshot.get("usable_for_research") is True,
        "source":snapshot["source"],"decision":"ABSTAIN_NO_VALIDATED_SIGNAL",
        "execution_enabled":False,"real_fills_observed":False,
    }
    digest=hashlib.sha256(json.dumps(base,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return {**base,"event_id":"obs-"+digest[:28],"event_sha256":digest}


def assess_paper_intent(quote: dict[str,Any], *,
                        assumed_probability: Any=None, contracts:int=1,
                        loss_today: Any=None, gross_exposure: Any=None) -> dict[str,Any]:
    """No signal/model assumption => abstain, even if quote has positive-looking prices."""
    blockers=[]
    if quote.get("source")!="POLYMARKET_US_OFFICIAL_PUBLIC_SDK" or quote.get("venue")!="POLYMARKET_US":
        blockers.append("WRONG_VENUE_OR_SOURCE")
    if quote.get("usable_for_research") is not True or quote.get("quote_freshness_valid") is not True:
        blockers.append("NO_FRESH_LIVE_QUOTE")
    if quote.get("market_state")!="MARKET_STATE_OPEN":
        blockers.append("MARKET_NOT_OPEN")
    try:
        observed=_utc(quote["observed_at"])
        exchange=_utc(quote["exchange_transact_at"])
        age=(observed-exchange).total_seconds()
        if age < -5 or age > DEFAULT_POLICY["max_quote_age_seconds"]:
            blockers.append("QUOTE_STALE")
    except (KeyError,TypeError,ValueError):
        blockers.append("QUOTE_TIME_UNVERIFIED")
    ask=quote.get("best_yes_ask")
    bid=quote.get("best_yes_bid")
    try:
        p_ask=_dec(ask)
        p_bid=_dec(bid)
        if not (0<p_bid<p_ask<1):
            blockers.append("BAD_ORDER_BOOK")
        if p_ask-p_bid > _dec(DEFAULT_POLICY["max_spread"]):
            blockers.append("WIDE_SPREAD")
        available=_dec(quote.get("best_ask_quantity"))
        if available < contracts:
            blockers.append("INSUFFICIENT_DISPLAYED_DEPTH")
    except ValueError:
        p_ask=p_bid=None
        blockers.append("MISSING_PRICE_OR_DEPTH")
    if not isinstance(contracts,int) or isinstance(contracts,bool) or contracts<1 or contracts>DEFAULT_POLICY["max_contracts_per_idea"]:
        blockers.append("CONTRACT_LIMIT")
    if assumed_probability is None:
        blockers.append("NO_INDEPENDENT_PROBABILITY_ESTIMATE")
        probability=None
    else:
        try:
            probability=_dec(assumed_probability)
            if not 0<=probability<=1:
                blockers.append("BAD_PROBABILITY")
        except ValueError:
            probability=None
            blockers.append("BAD_PROBABILITY")
    if loss_today is None or gross_exposure is None:
        blockers.append("RISK_LEDGER_UNAVAILABLE")
    else:
        try:
            if _dec(loss_today)<0 or _dec(gross_exposure)<0:
                blockers.append("INVALID_RISK_LEDGER")
            if _dec(loss_today)>=_dec(DEFAULT_POLICY["max_daily_hypothetical_loss_usd"]):
                blockers.append("DAILY_LOSS_LIMIT")
            if p_ask is not None and _dec(gross_exposure)+p_ask*contracts>_dec(DEFAULT_POLICY["max_gross_exposure_usd"]):
                blockers.append("GROSS_EXPOSURE_LIMIT")
        except ValueError:
            blockers.append("INVALID_RISK_LEDGER")
    fee=_dec(DEFAULT_POLICY["assumed_fee_per_contract"])
    slip=_dec(DEFAULT_POLICY["assumed_slippage_per_contract"])
    cost=p_ask+fee+slip if p_ask is not None else None
    assumed_ev=(probability-cost)*contracts if cost is not None and probability is not None else None
    if assumed_ev is None or assumed_ev<=_dec(DEFAULT_POLICY["minimum_assumed_margin"])*contracts:
        blockers.append("ASSUMED_EDGE_BELOW_HURDLE")
    return {
        "schema":"chaytrades.poly_us.paper_intent.v1",
        "market_slug":quote.get("market_slug"),"side":"BUY_YES_ONLY",
        "contracts":contracts,"assumed_probability":float(probability) if probability is not None else None,
        "assumed_price":float(p_ask) if p_ask is not None else None,
        "assumed_all_in_per_contract":float(cost) if cost is not None else None,
        "assumed_expected_value_usd":float(assumed_ev) if assumed_ev is not None else None,
        "blockers":sorted(set(blockers)),
        "decision":"ABSTAIN" if blockers else "PAPER_HYPOTHESIS_ELIGIBLE",
        "forecast_type":"USER_ASSUMED_NOT_CALIBRATED",
        "hypothetical_fill_status":"NOT_OBSERVED",
        "real_trading_authorized":False,"execution_enabled":False,
    }


def forward_mark(entry: dict[str,Any], new_quote: dict[str,Any]) -> dict[str,Any]:
    """Prospective bid-side mark, no hindsight re-entry and no assumed fill."""
    if entry.get("market_slug")!=new_quote.get("market_slug"):
        raise ValueError("market_mismatch")
    entry_at=_utc(entry["observed_at"])
    mark_at=_utc(new_quote["observed_at"])
    if mark_at<=entry_at:
        raise ValueError("non_forward_mark")
    if not new_quote.get("usable_for_research"):
        raise ValueError("new_quote_unusable")
    entry_ask=_dec(entry.get("ask"))
    later_bid=_dec(new_quote.get("best_yes_bid"))
    if not 0<entry_ask<1 or not 0<later_bid<1:
        raise ValueError("invalid_price")
    return {
        "schema":"chaytrades.poly_us.forward_mark.v1",
        "market_slug":entry["market_slug"],
        "observed_at":mark_at.isoformat(),
        "entry_observed_at":entry_at.isoformat(),
        "entry_ask":float(entry_ask),"later_bid":float(later_bid),
        "modeled_bid_to_ask_move_usd":float(later_bid-entry_ask),
        "fees_or_slippage_not_included":True,
        "position_opened":False,"fill_verified":False,"realized_pnl_verified":False,
        "execution_enabled":False,"status":"FORWARD_MARK_UNVERIFIED_EXECUTION",
    }
