"""NEXUS oracle experiment-factory interoperability on long-term BTC spot bars.

The NEXUS experiment-factory is run unchanged as an advisory-only local
dependency. We do NOT merge Binance spot P&L with Polymarket US executions.
Price observations are from Binance, and only *completed* bars are exposed.
"""
from datetime import datetime,timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess


def fetch_completed_bars(db_path, max_bars=0):
    db=sqlite3.connect(str(db_path))
    bars=db.execute(
        """SELECT open_ts,open,high,low,close FROM spot_bars
           WHERE venue='spot' ORDER BY open_ts"""
    ).fetchall()
    digests=db.execute(
        """SELECT month,source_sha256 FROM source_files WHERE venue='spot'
           AND status='VERIFIED' ORDER BY month""").fetchall()
    db.close()
    if max_bars>0:
        bars=bars[-max_bars:]
    series=[]
    for ts,o,h,l,c in bars:
        # The signal may use bar CLOSE only at bar ending timestamp, never
        # at opening timestamp. Fail on gaps to avoid accidental forward joins.
        if series and ts<=series[-1]["time"]-900:
            raise RuntimeError("unsorted bar history")
        series.append({"time":int(ts+900),"open":o,"high":h,"low":l,"close":c})
    return series,digests


def evaluate(db_path,nexus_root,out_path,max_bars=0):
    bars,digests=fetch_completed_bars(db_path,max_bars)
    if len(bars)<120:
        raise RuntimeError("need 120 completed spot bars")
    file=Path(nexus_root).resolve()/"research/kraken/experiment-factory.mjs"
    if not file.is_file():
        raise RuntimeError("NEXUS experiment factory unavailable")
    node=shutil.which("node")
    if not node:
        raise RuntimeError("Node 20+ required")
    source='''import fs from "node:fs";
import {pathToFileURL} from "node:url";
const {runFactory}=await import(pathToFileURL(process.argv[1]).href);
const bars=JSON.parse(fs.readFileSync(0,"utf8"));
const report=runFactory({bars,symbol:"BINANCE_SPOT_BTCUSDT_15M"});
process.stdout.write(JSON.stringify(report));
'''
    proc=subprocess.run(
        [node,"--input-type=module","-e",source,str(file)],
        input=json.dumps(bars,separators=(",",":")),text=True,
        capture_output=True,timeout=90,check=False,
        env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),
             "HOME":str(Path.home())},
    )
    if proc.returncode:
        raise RuntimeError("NEXUS experiment rejected: "+proc.stderr[:450])
    result=json.loads(proc.stdout)
    if result.get("tradePermission") is not False or result.get("modelPromotion") is not False:
        raise RuntimeError("NEXUS authority invariant violated")
    envelope={
        "schema":"oracle15.nexus.research-bridge.v1",
        "venue":"BINANCE_SPOT_GLOBAL",
        "cross_venue_transfer_unvalidated":True,
        "settlement_index":"NOT_BRTI",
        "live_order_authority":False,
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "completed_bar_count":len(bars),
        "bars_start_end":[bars[0]["time"],bars[-1]["time"]],
        "nexus_factory_path":str(file),
        "nexus_factory_sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
        "source_month_sha256":digests,
        "nexus_advisory_experiment":result,
    }
    target=Path(out_path)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(envelope,indent=2,sort_keys=True)+"\n")
    return {"report":str(target),"venue":envelope["venue"],
            "completed_bars":len(bars),"nexus_candidate":result.get("candidateLeader"),
            "nexus_test":result.get("test"),"nexus_validation":result.get("validation"),
            "research_only":True}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="state/oracle15/research/spot.sqlite3")
    p.add_argument("--nexus-root",default="../nexus-core")
    p.add_argument("--out",default="state/oracle15/research/nexus-spot-replay.json")
    p.add_argument("--max-bars",type=int,default=0)
    args=p.parse_args()
    print(json.dumps(evaluate(args.db,args.nexus_root,args.out,args.max_bars),indent=2))


if __name__=="__main__":
    main()
