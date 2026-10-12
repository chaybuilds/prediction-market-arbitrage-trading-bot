"""Walk-forward research tournament. Never auto-promote or enable real orders.

Only PRIOR UTC days select the strategy for each future test day.
Every strategy, threshold, TP, stop and hold duration is registered
before historical outcomes are inspected. OOS P&L is a conditional
display-quote simulation (not evidence of executable historical fills).
"""
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import math
import statistics
import time

from .replay import (Cost, Spec, bootstrap_mean_ci, load_events,
                     performance, simulate)

FAMILIES = (
    "quote_momentum", "quote_reversion", "two_step_trend",
    "v_reversal", "breakout", "range_fade", "spread_squeeze"
)
CONTROL_FAMILIES = ("always_up", "always_down", "seeded_random")


def catalog():
    specs = [
        Spec(name,0,0,0,900,"SETTLE")
        for name in CONTROL_FAMILIES
    ]
    for family in FAMILIES:
        for threshold in (0.025,0.055,0.085):
            for target in (0.05,0.10):
                for stop in (0.08,0.16):
                    for hold in (180,360):
                        specs.append(Spec(family,threshold,target,stop,hold,"SCALP"))
            # Also test non-scalping settlement; a different hypothesis.
            specs.append(Spec(family,threshold,0,0,900,"SETTLE"))
    return tuple(specs)


def day_at(unix):
    return datetime.fromtimestamp(unix,timezone.utc).strftime("%Y-%m-%d")


def score_prior_only(trades, events_count, family_count):
    """Heuristic conservative score. NOT a multiple-testing-corrected p-value."""
    n=len(trades)
    if n < max(12, math.ceil(events_count * .06)):
        return None
    days={day_at(t.event_start) for t in trades}
    if len(days)<2:
        return None
    profits=[t.pnl for t in trades]
    mu=statistics.mean(profits)
    sd=statistics.stdev(profits) if n>1 else 0
    # Extensive hypothesis search should be penalized rather than crowned.
    search_penalty=math.sqrt(2*math.log(max(2,family_count)))*sd/math.sqrt(n)
    drawdown=performance(trades,events_count)["max_drawdown"]
    return mu - search_penalty - .05*drawdown/max(1,n)


def tune(days, events_by_day, candidates, costs):
    history = [e for d in days for e in events_by_day[d]]
    scored=[]
    for spec in candidates:
        trades=simulate(history,spec,costs)
        value=score_prior_only(trades,len(history),len(candidates))
        if value is not None:
            scored.append((value,spec,len(trades),
                           sum(t.pnl for t in trades)))
    scored.sort(key=lambda x:(-x[0],x[1].name))
    return scored


def run_tournament(events, minimum_train_days=2, holdout_fraction=0.30,
                   costs=Cost(), allow_pre_fee=False):
    events=sorted(events,key=lambda e:e.start)
    by_day=defaultdict(list)
    for event in events:
        if not allow_pre_fee and event.fee_regime!="CURRENT_DOC":
            continue
        by_day[day_at(event.start)].append(event)
    all_days=sorted(by_day)
    specs=catalog()
    candidate_names=[s.name for s in specs]
    manifest={
        "research_schema":"oracle15.research.tournament.v2",
        "venue":"POLYMARKET_US",
        "market":"BTC_UP_DOWN_15M_BRTI",
        "data_quality":"HISTORICAL_DISPLAY_QUOTES_ONLY",
        "asof_model":"SIGNAL_T_FILL_T_PLUS_AT_LEAST_1_QUOTE",
        "fee": "VERIFIED_CURRENT_OCT7_US_THETA_BY_EVENT",
        "data_window_start":min((e.start for e in events),default=None),
        "data_window_end":max((e.start+900 for e in events),default=None),
        "source_digests_required":True,
        "candidate_count":len(specs),
        "candidate_names":candidate_names,
        "costs":asdict(costs),
        "minimum_train_days":minimum_train_days,
        "holdout_fraction":holdout_fraction,
        "scan_fraction_pre_fee_disabled":not allow_pre_fee,
    }
    manifest["manifest_sha256"]=hashlib.sha256(
        json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    report={"manifest":manifest, "generated_at":datetime.now(timezone.utc).isoformat(),
            "markets_seen":len(events),"usable_days":len(all_days),
            "venue_status":"READ_ONLY_HISTORICAL_RESEARCH",
            "capital_authority":"NONE","approved_for_live_trading":False,
            "oos":None,"walk_forward":None,"controls":{},
            "guardrail":"Cannot infer live profitability without real fill and depth data."}
    if len(all_days)<=minimum_train_days:
        report["status"]="INSUFFICIENT_CHRONOLOGICAL_DAYS"
        return report
    # First experiment: final fraction of entire calendar-day series remains
    # held out from *all* tuning. No parameter may be selected on that period.
    cut=max(minimum_train_days, int(len(all_days)*(1-holdout_fraction)))
    cut=min(len(all_days)-1,cut)
    train_days=all_days[:cut]
    test_days=all_days[cut:]
    ranking=tune(train_days,by_day,specs,costs)
    chosen=ranking[0] if ranking and ranking[0][0]>0 else None
    held_out=[e for d in test_days for e in by_day[d]]
    train=[e for d in train_days for e in by_day[d]]
    selected_spec=chosen[1] if chosen else None
    trades=simulate(held_out,selected_spec,costs) if chosen else []
    report["oos"]={
        "split":"ANCHORED_CALENDAR_DAY_FINAL_BLOCK",
        "train_dates":[train_days[0],train_days[-1]],
        "test_dates":[test_days[0],test_days[-1]],
        "train_markets":len(train),
        "test_markets":len(held_out),
        "selected_by_training_only":selected_spec.name if selected_spec else "ABSTAIN",
        "training_lower_bound_heuristic":round(chosen[0],6) if chosen else None,
        "top_prior_candidates":[
            {"spec":s.name,"train_lower_bound":round(v,6),
             "train_trades":count,"train_conditional_pnl":round(pnl,4)}
            for v,s,count,pnl in ranking[:10]
        ],
        "result":performance(trades,len(held_out)),
        "bootstrap_mean_pnl_95ci_by_day":bootstrap_mean_ci(trades),
        "illustrative_trades":[asdict(t) for t in trades[:15]],
    }
    if chosen:
        report["oos"]["stress_tests"]={}
        for stress_name,condition in {
            "2x_taker_fees":Cost(**{**asdict(costs),"fee_multiplier":2.0}),
            "2x_slippage":Cost(**{**asdict(costs),"per_share_slippage":costs.per_share_slippage*2}),
            "2_quote_execution_lag":Cost(**{**asdict(costs),"latency_quotes":2}),
        }.items():
            samples=simulate(held_out,selected_spec,condition)
            report["oos"]["stress_tests"][stress_name]=performance(samples,len(held_out))
    # Sequential daily tournament, distinct from the fixed held-out test.
    prior=[]; forward_trades=[]; checkpoints=[]
    for i,day in enumerate(all_days):
        if i<minimum_train_days:
            prior.append(day)
            continue
        rank=tune(prior,by_day,specs,costs)
        champion=rank[0] if rank and rank[0][0]>0 else None
        daily=simulate(by_day[day],champion[1],costs) if champion else []
        checkpoints.append({
            "test_day":day,"prior_through":prior[-1],
            "prior_markets":sum(len(by_day[d]) for d in prior),
            "strategy":champion[1].name if champion else "ABSTAIN",
            "prior_score":round(champion[0],6) if champion else None,
            "trades":len(daily),"conditional_pnl":round(sum(t.pnl for t in daily),4)
        })
        forward_trades+=daily
        prior.append(day)
    report["walk_forward"]={
        "selection":"EXPANDING_PRIOR_DAYS_ONLY",
        "daily_checkpoints":checkpoints,
        "result":performance(forward_trades,sum(len(by_day[d]) for d in all_days[minimum_train_days:])),
        "bootstrap_mean_pnl_95ci_by_day":bootstrap_mean_ci(forward_trades),
    }
    for control in specs[:3]:
        control_trades=simulate(held_out,control,costs)
        report["controls"][control.family]=performance(control_trades,len(held_out))
    report["status"]="RESEARCH_COMPLETE_NOT_VALIDATED_FOR_EXECUTION"
    return report


def main():
    p=argparse.ArgumentParser(description="ORACLE 15M fee-aware chronological replay")
    p.add_argument("--db",default="state/oracle15/research/us_quotes.sqlite3")
    p.add_argument("--train-days",type=int,default=2)
    p.add_argument("--max-events",type=int,default=0)
    p.add_argument("--include-pre-fee-uncertain",action="store_true")
    p.add_argument("--out",default="")
    args=p.parse_args()
    if args.train_days<2:
        p.error("at least two prior calendar days required")
    events=load_events(args.db,require_current_fee=not args.include_pre_fee_uncertain)
    if args.max_events>0:
        events=events[:args.max_events]
    report=run_tournament(events,args.train_days,
        allow_pre_fee=args.include_pre_fee_uncertain)
    if not args.out:
        folder=Path(args.db).parent/"reports"
        folder.mkdir(parents=True,exist_ok=True)
        now=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        digest=report["manifest"]["manifest_sha256"][:10]
        target=folder/(now+"_"+digest+".json")
    else:
        target=Path(args.out)
        target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    summary={
        "report":str(target),"status":report["status"],
        "markets":report["markets_seen"],"days":report["usable_days"],
        "candidate_count":report["manifest"]["candidate_count"],
        "heldout": report["oos"]["result"] if report["oos"] else None,
        "chosen":report["oos"]["selected_by_training_only"] if report["oos"] else None,
        "walk_forward":report["walk_forward"]["result"] if report["walk_forward"] else None,
        "capital_authority":"NONE",
    }
    print(json.dumps(summary,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
