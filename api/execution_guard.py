"""Institutional-grade fail-closed boundary for future Polymarket US execution.

Only constructs *unsubmitted* plans.  Does not import exchange order SDK routes.
No credentials or environment flags can change execution_enabled to True.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from api.paper_forward import assess_paper_intent

@dataclass(frozen=True)
class ExecutionPolicy:
    max_order_usd: float = 10.0
    max_daily_loss_usd: float = 3.0
    max_gross_exposure_usd: float = 10.0
    max_open_orders: int = 1
    require_human_approval: bool = True
    force_kill_switch: bool = True
    execution_available: bool = False

POLICY = ExecutionPolicy()

def construct_order_preflight(quote: dict[str,Any], *, forecast_probability: Any=None,
                              risk_state: dict[str,Any]|None=None, contracts:int=1) -> dict[str,Any]:
    risk_state=risk_state if isinstance(risk_state,dict) else {}
    proposal=assess_paper_intent(
        quote,assumed_probability=forecast_probability,contracts=contracts,
        loss_today=risk_state.get("loss_today"),
        gross_exposure=risk_state.get("gross_exposure"))
    blockers=list(proposal["blockers"])
    if not risk_state.get("account_authenticated",False):
        blockers.append("ACCOUNT_NOT_AUTHENTICATED")
    if not risk_state.get("positions_reconciled",False):
        blockers.append("POSITIONS_NOT_RECONCILED")
    if not risk_state.get("balances_reconciled",False):
        blockers.append("BALANCES_NOT_RECONCILED")
    if not risk_state.get("market_rules_independently_verified",False):
        blockers.append("MARKET_RULES_NOT_VERIFIED")
    if not risk_state.get("calibration_proof",False):
        blockers.append("MODEL_UNCALIBRATED")
    if not risk_state.get("operator_approved",False):
        blockers.append("OPERATOR_APPROVAL_MISSING")
    if (not isinstance(risk_state.get("open_orders"),int) or
          risk_state["open_orders"]>=POLICY.max_open_orders):
        blockers.append("OPEN_ORDER_LIMIT_OR_UNKNOWN")
    if proposal["assumed_price"] is None or proposal["assumed_price"]*contracts>POLICY.max_order_usd:
        blockers.append("MAX_ORDER_NOTIONAL")
    blockers += ["GLOBAL_KILL_SWITCH_ACTIVE","LIVE_EXECUTOR_NOT_IMPLEMENTED"]
    canonical={"venue":"POLYMARKET_US","slug":quote.get("market_slug"),
               "intent":"BUY_YES","contracts":contracts,
               "reference_at":quote.get("observed_at"),"estimated_ask":proposal.get("assumed_price")}
    token=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
    return {"schema":"chaytrades.poly_us.execution_preflight.v1",
            "order_idempotency_sha256":token,"unsubmitted_order":canonical,
            "paper_review":proposal,"blockers":sorted(set(blockers)),
            "exchange_order_id":None,"execution_enabled":False,
            "orders_sent":0,"account_debit_usd":0.0,
            "final_decision":"REJECT_LIVE_ORDER",
            "kill_switch":True,"human_authority_required":True}

def execution_status()->dict[str,Any]:
    return {"schema":"chaytrades.poly_us.execution_status.v1",
            "policy":{"max_order_usd":POLICY.max_order_usd,
                      "max_daily_loss_usd":POLICY.max_daily_loss_usd,
                      "max_gross_exposure_usd":POLICY.max_gross_exposure_usd,
                      "max_open_orders":POLICY.max_open_orders},
            "execution_available":False,"kill_switch":True,"auto_trading":False,
            "reason":"No production order adapter; account authentication and forward calibration outstanding"}
