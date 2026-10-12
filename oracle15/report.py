"""Local decision-grade research scoreboard. No network or trading actions.

Combines separate evidence streams WITHOUT converting conditional historical
replay or simulated paper P&L into verified capital returns.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sqlite3
from collections import Counter


def scalar(db, sql, default=None):
    try:
        row=db.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.DatabaseError:
        return default


def query_summary(file):
    p=Path(file)
    if not p.exists():
        return {"available":False}
    connection=sqlite3.connect(str(p))
    try:
        markets = dict(connection.execute(
            "SELECT status,COUNT(*) FROM markets GROUP BY status").fetchall())
        current=scalar(connection,"SELECT COUNT(*) FROM markets WHERE fee_regime='CURRENT_DOC' AND status='OK'",0)
        uncertain=scalar(connection,"SELECT COUNT(*) FROM markets WHERE fee_regime='PRE_OCT7_UNVERIFIED' AND status='OK'",0)
        return {"available":True,"markets_by_status":markets,
                "valid_quote_snapshots":scalar(connection,"SELECT COUNT(*) FROM quotes",0),
                "usable_current_fee_markets":current,
                "pre_oct7_fee_uncertain_markets":uncertain}
    finally:
        connection.close()


def spot_summary(file):
    p=Path(file)
    if not p.exists():
        return {"available":False}
    db=sqlite3.connect(str(p))
    try:
        venues={r[0]:int(r[1]) for r in db.execute(
            "SELECT venue,COUNT(*) FROM spot_bars GROUP BY venue")}
        files=Counter(dict(db.execute(
            "SELECT status,COUNT(*) FROM source_files GROUP BY status")))
        return {"available":True,"bars_by_venue":venues,
                "source_files":dict(files),
                "provenance":"Binance global BTCUSDT, NOT Polymarket US/BRTI"}
    finally:
        db.close()


def paper_summary(file):
    p=Path(file)
    if not p.exists():
        return {"available":False}
    db=sqlite3.connect(str(p))
    try:
        positions=dict(db.execute(
            "SELECT state,COUNT(*) FROM paper_positions GROUP BY state"))
        pnl=scalar(db,"SELECT SUM(pnl) FROM paper_positions WHERE state IN ('SETTLED','EARLY_EXIT')",0.0)
        trades=scalar(db,"SELECT COUNT(*) FROM paper_positions",0)
        forecasts=scalar(db,"SELECT COUNT(*) FROM forecast_registry",0)
        resolved=scalar(db,"SELECT COUNT(*) FROM forecast_outcomes",0)
        intents=dict(db.execute(
            "SELECT state,COUNT(*) FROM paper_entry_intents GROUP BY state"))
        # Comparable after-the-fact Brier scores from immutable forward predictions.
        calibration={}
        for horizon,n,brier,market_brier in db.execute("""SELECT
            f.horizon_label,COUNT(*),
            AVG((f.p_up-o.yes_settlement)*(f.p_up-o.yes_settlement)),
            AVG(((f.bid_up+f.ask_up)/2-o.yes_settlement)*
                ((f.bid_up+f.ask_up)/2-o.yes_settlement))
            FROM forecast_registry f JOIN forecast_outcomes o USING(event_slug)
            GROUP BY f.horizon_label"""):
            calibration[horizon]={"n":n,"model_brier":round(brier,5),
                                  "market_baseline_brier":round(market_brier,5),
                                  "promotion_status":"LOCKED"}
        return {"available":True,"paper_positions_by_state":positions,
                "paper_entries_total":trades,
                "paper_modeled_total_pnl":round(pnl,4),
                "forecast_checkpoints":forecasts,
                "forward_forecast_events_resolved":resolved,
                "entry_intent_states":intents,
                "forward_calibration":calibration,
                "capital_proof":"NONE"}
    finally:
        db.close()


def latest_tournament(root):
    folder=Path(root)/"reports"
    if not folder.exists():
        return {"available":False}
    for path in sorted(folder.glob("*.json"),reverse=True):
        try:
            with path.open() as f:
                report=json.load(f)
            manifest=report.get("manifest",{})
            if (manifest.get("research_schema")=="oracle15.research.tournament.v2"
                and manifest.get("costs",{}).get("price_floor")==0.15
                and report.get("study_scope","CORE_CURRENT_FEE_EXPERIMENT")=="CORE_CURRENT_FEE_EXPERIMENT"):
                held=report.get("oos") or {}
                return {"available":True,"path":str(path),
                        "tested_us_events":report["markets_seen"],
                        "candidate_grid":report["manifest"]["candidate_count"],
                        "heldout_strategy":held.get("selected_by_training_only","UNKNOWN"),
                        "heldout_result":held.get("result"),
                        "walk_forward_result":(report.get("walk_forward") or {}).get("result"),
                        "capital_authority":"NONE"}
        except (OSError,ValueError,KeyError):
            continue
    return {"available":False}


def get_report(file, keys=None):
    p=Path(file)
    if not p.exists():
        return {"available":False}
    try:
        with p.open() as f:
            data=json.load(f)
        return {"available":True,"source_path":str(p),
                **({key:data.get(key) for key in keys} if keys else data)}
    except (OSError,ValueError):
        return {"available":False}


def make(root):
    root=Path(root)
    research=root/"research"
    return {
        "schema":"oracle15.local_research_scoreboard.v1",
        "updated_at":datetime.now(timezone.utc).isoformat(),
        "execution_mode":"PAPER_ONLY_NO_BROKER_CREDENTIALS",
        "polymarket_us_archive":query_summary(research/"us_quotes.sqlite3"),
        "binance_btc_archive":spot_summary(research/"spot.sqlite3"),
        "us_strategy_tournament":latest_tournament(research),
        "spot_futures_leadlag":get_report(research/"leadlag-report.json",
                 ("available_bars","usable_lagged_windows","candidate_count","champion",
                  "validation","test","trading_authority")),
        "nexus_experiment":get_report(research/"nexus-spot-replay.json",
                 ("venue","completed_bar_count","cross_venue_transfer_unvalidated","live_order_authority")),
        "forward_paper":paper_summary(root/"paper.sqlite3"),
        "promotion_gate":{
            "status":"LOCKED",
            "reason":"Unverified real fills, uncalibrated forecast edge, order adapter and account permissions",
            "live_trading_allowed":False,
        },
        "interpretation":[
            "Historical display-quote P&L is conditional, not executed profit",
            "Binance OHLC features cannot establish Polymarket US fills or BRTI settlement",
            "Paper P&L is simulated and not account funds",
            "Research may suggest testing; no strategy automatically advances to real orders",
        ],
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state-dir",default="state/oracle15")
    p.add_argument("--output",default="")
    args=p.parse_args()
    source=Path(args.state_dir)
    report=make(source)
    dest=Path(args.output) if args.output else source/"research/scoreboard.json"
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"saved":str(dest),
                      "us_archive":report["polymarket_us_archive"],
                      "spot_archive":report["binance_btc_archive"],
                      "historical_heldout":report["us_strategy_tournament"].get("heldout_strategy"),
                      "leadlag_champion":report["spot_futures_leadlag"].get("champion"),
                      "paper":report["forward_paper"],
                      "live_order_authority":False},indent=2))


if __name__=="__main__":
    main()
