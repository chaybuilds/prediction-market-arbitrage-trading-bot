"""Multi-year BTC spot/futures lead-lag experiment, separate from Polymarket P&L.

Features known only after each 15m bar completes; prospective trade is the
NEXT bar's open->close, priced with two-sided conservative spot friction.
No news sentiment, order-book queues, liquidation events or market-maker
intent is invented from OHLCV candles.
"""
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from statistics import mean, stdev
import argparse
import hashlib
import json
import math
import random
import sqlite3


def joined_history(path):
    db=sqlite3.connect(path)
    rows=db.execute("""
        SELECT s.open_ts,s.open,s.high,s.low,s.close,s.quote_volume,s.buy_quote_volume,
               f.close,f.quote_volume,f.buy_quote_volume
        FROM spot_bars s
        INNER JOIN spot_bars f ON s.open_ts=f.open_ts
        WHERE s.venue='spot' AND f.venue='futures_um'
        ORDER BY s.open_ts
    """).fetchall()
    sources=db.execute("""SELECT venue,month,source_sha256 FROM source_files
                      WHERE status='VERIFIED' ORDER BY venue,month""").fetchall()
    db.close()
    return rows,sources


def factors(rows):
    """O(n) rolling features known at each completed bar, not future labels."""
    n=len(rows)
    if n<100:
        return []
    vols=[max(float(r[5]),0) for r in rows]
    closes=[float(r[4]) for r in rows]
    ret=[0.0]*n
    vtotal=[0.0]*(n+1)
    vtotal2=[0.0]*(n+1)
    ret_sq=[0.0]*(n+1)
    gap_cum=[0]*(n+1)
    for i in range(n):
        if i and closes[i]>0 and closes[i-1]>0:
            ret[i]=math.log(closes[i]/closes[i-1])
        vtotal[i+1]=vtotal[i]+vols[i]
        vtotal2[i+1]=vtotal2[i]+vols[i]*vols[i]
        ret_sq[i+1]=ret_sq[i]+ret[i]*ret[i]
        gap_cum[i+1]=gap_cum[i]+int(i>0 and rows[i][0]-rows[i-1][0]!=900)
    result=[]
    for i in range(96,n-1):
        r=rows[i];nextbar=rows[i+1]
        if (r[0]+900!=nextbar[0] or
            gap_cum[i+1]-gap_cum[i-15]!=0):
            continue
        p=r[4]
        if p<=0 or rows[i-4][4]<=0 or nextbar[1]<=0:
            continue
        ret1=1e4*ret[i]
        ret4=1e4*math.log(p/rows[i-4][4])
        vol=1e4*math.sqrt(max(0.0,(ret_sq[i+1]-ret_sq[i-15])/16))
        prior_n=32
        mean_vol=max(1e-9,(vtotal[i]-vtotal[i-prior_n])/prior_n)
        variance=max(0.0,(vtotal2[i]-vtotal2[i-prior_n])/prior_n-mean_vol**2)
        vol_z=(r[5]-mean_vol)/(math.sqrt(variance)+1e-8)
        sf=2*(r[6]/r[5])-1 if r[5]>0 else 0
        ff=2*(r[9]/r[8])-1 if r[8]>0 else 0
        basis=1e4*(r[7]/p-1)
        oldbasis=1e4*(rows[i-4][7]/rows[i-4][4]-1)
        future_bps=1e4*math.log(nextbar[4]/nextbar[1])
        stamp=datetime.fromtimestamp(r[0],timezone.utc)
        result.append({
            "signal_available_at":int(r[0]+900),
            "entry_bar_open":int(nextbar[0]),
            "month":stamp.strftime("%Y-%m"),
            "ret1":ret1,"ret4":ret4,"volatility_bps":vol,
            "volume_z":vol_z,"spot_flow":sf,"futures_flow":ff,
            "basis_bps":basis,"basis_change4":basis-oldbasis,
            "regime":"WEEKEND" if stamp.weekday()>=5 else "WEEKDAY",
            "next_spot_return_bps":future_bps,
        })
    return result


def specifications():
    specs=[]
    for fam,thresholds in {
        "spot_momentum_1":(3,8,16),
        "spot_momentum_4":(8,16,35),
        "spot_reversion_1":(3,8,16),
        "spot_reversion_4":(8,16,35),
        "future_basis_level":(3,8,16),
        "future_basis_shift":(3,8,16),
        "spot_flow_follow":(.10,.20,.35),
        "perp_flow_follow":(.10,.20,.35),
        "perp_flow_fade":(.10,.20,.35),
        "volume_spike_trend":(1,2,3),
        "vol_high_reversion":(8,16,24),
        "vol_low_momentum":(3,8,16),
    }.items():
        for threshold in thresholds:
            specs.append((fam,float(threshold)))
    return specs


def signal(x,fam,t):
    if fam=="spot_momentum_1":return 1 if x["ret1"]>t else (-1 if x["ret1"]<-t else 0)
    if fam=="spot_momentum_4":return 1 if x["ret4"]>t else (-1 if x["ret4"]<-t else 0)
    if fam=="spot_reversion_1":return -1 if x["ret1"]>t else (1 if x["ret1"]<-t else 0)
    if fam=="spot_reversion_4":return -1 if x["ret4"]>t else (1 if x["ret4"]<-t else 0)
    if fam=="future_basis_level":return -1 if x["basis_bps"]>t else (1 if x["basis_bps"]<-t else 0)
    if fam=="future_basis_shift":return 1 if x["basis_change4"]>t else (-1 if x["basis_change4"]<-t else 0)
    if fam=="spot_flow_follow":return 1 if x["spot_flow"]>t else (-1 if x["spot_flow"]<-t else 0)
    if fam=="perp_flow_follow":return 1 if x["futures_flow"]>t else (-1 if x["futures_flow"]<-t else 0)
    if fam=="perp_flow_fade":return -1 if x["futures_flow"]>t else (1 if x["futures_flow"]<-t else 0)
    if fam=="volume_spike_trend":
        return (1 if x["ret1"]>0 else -1) if x["volume_z"]>t else 0
    if fam=="vol_high_reversion":
        return (-1 if x["ret1"]>0 else 1) if x["volatility_bps"]>35 and abs(x["ret1"])>t else 0
    if fam=="vol_low_momentum":
        return (1 if x["ret1"]>t else (-1 if x["ret1"]<-t else 0)) if x["volatility_bps"]<20 else 0
    return 0


def evaluate(features, spec, round_trip_friction_bps=12.0):
    fam,threshold=spec
    realized=[]
    by_month=defaultdict(list)
    correct=0
    positive=0
    for f in features:
        side=signal(f,fam,threshold)
        if side==0:
            continue
        gross=side*f["next_spot_return_bps"]
        net=gross-round_trip_friction_bps
        realized.append(net)
        positive+=int(net>0)
        correct+=int(gross>0)
        key=f["month"]
        by_month[key].append(net)
    n=len(realized)
    return {
        "samples":len(features),"signals":n,
        "coverage":round(n/max(1,len(features)),5),
        "mean_net_spot_bps":round(sum(realized)/n,5) if n else None,
        "sum_net_spot_bps":round(sum(realized),5),
        "hit_before_friction":round(correct/n,4) if n else None,
        "positive_after_friction":round(positive/n,4) if n else None,
        "monthly":{k:round(sum(v)/len(v),4) for k,v in by_month.items()},
        "not_prediction_market_pnl":True,
    }


def score_train(rows,stat,specs_count):
    if stat["signals"]<200 or len(stat["monthly"])<6:
        return None
    # Month-block penalty accounts better for serial correlations than
    # falsely treating tens of thousands of adjacent bars as independent.
    vals=list(stat["monthly"].values())
    if len(vals)<6:
        return None
    se=stdev(vals)/math.sqrt(len(vals))
    multiple_search_penalty=math.sqrt(2*math.log(specs_count))*se
    return stat["mean_net_spot_bps"] - multiple_search_penalty


def run(rows,sources,round_trip_friction_bps=12.0):
    obs=factors(rows)
    specs=specifications()
    n=len(obs)
    cut1=int(n*.50);cut2=int(n*.75)
    purge=96  # purge 24h around both time-split boundaries
    train=obs[:max(0,cut1-purge)]
    validation=obs[cut1+purge:max(0,cut2-purge)]
    test=obs[cut2+purge:]
    ranked=[]
    for spec in specs:
        res=evaluate(train,spec,round_trip_friction_bps)
        sc=score_train(train,res,len(specs))
        if sc is not None:
            ranked.append((sc,spec,res))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    champion=ranked[0] if ranked and ranked[0][0]>0 else None
    choose=champion[1] if champion else None
    report={
        "schema":"oracle15.leadlag.historical.v1",
        "source":"BINANCE_GLOBAL_SPOT_FUTURES_15M_CSV_SHA256_VERIFIED",
        "outcome":"NEXT_BINANCE_SPOT_BAR_RETURN_NOT_BRTI_SETTLEMENT",
        "news_and_liquidations_not_observed":True,
        "trading_authority":"NONE",
        "unvalidated_transfer_to_polymarket_us":True,
        "features_asof":"completed_bar_closing_time",
        "entry_asof":"next_bar_open_after_signal",
        "round_trip_spot_friction_bps":round_trip_friction_bps,
        "candidate_count":len(specs),
        "selection":"TRAIN_ONLY_PURGED_24H_SEPARATE_VALIDATION_AND_TEST",
        "segments":{"train":len(train),"validation":len(validation),"test":len(test)},
        "available_bars":len(rows),"usable_lagged_windows":len(obs),
        "source_manifest_sha256":hashlib.sha256(
            json.dumps(sources,sort_keys=True).encode()).hexdigest(),
        "champion":str(choose) if choose else "ABSTAIN",
        "leader_train_score":round(champion[0],5) if champion else None,
        "leader_train":champion[2] if champion else None,
        "validation":evaluate(validation,choose,round_trip_friction_bps) if choose else None,
        "test":evaluate(test,choose,round_trip_friction_bps) if choose else None,
        "controls": {
            "always_long": {
                "mean_net_spot_bps":round(mean([f["next_spot_return_bps"]-round_trip_friction_bps for f in test]),5)
                if test else None,
            },
            "always_short": {
                "mean_net_spot_bps":round(mean([-f["next_spot_return_bps"]-round_trip_friction_bps for f in test]),5)
                if test else None,
            },
        },
        "ranking_train":[{"spec":str(spec),"lower_bound_heuristic":round(sc,5),
                          "signals":stat["signals"],"mean_net_bps":stat["mean_net_spot_bps"]}
                         for sc,spec,stat in ranked[:12]],
        "limitations":[
            "Binance spot/perpetual prices are not Polymarket US book quotes or BRTI",
            "The OHLCV dataset cannot identify market manipulation or liquidation events",
            "Market-impact, maker queue/fills, and order latency are not measured",
            "Historical price correlations are not proof of causal predictability",
            "No news or political timestamps ingested, so no event attribution tested",
            "Multiple candidate selection is penalized heuristically, not proven significant",
        ],
    }
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="state/oracle15/research/spot.sqlite3")
    p.add_argument("--friction-bps",type=float,default=12)
    p.add_argument("--out",default="state/oracle15/research/leadlag-report.json")
    args=p.parse_args()
    if args.friction_bps<0:
        p.error("friction must be >=0")
    rows,sources=joined_history(args.db)
    report=run(rows,sources,args.friction_bps)
    target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({
        "report":str(target),"candidate_count":report["candidate_count"],
        "usable_lagged_windows":report["usable_lagged_windows"],
        "selected":report["champion"],
        "validation_mean_net_bps":report["validation"]["mean_net_spot_bps"] if report["validation"] else None,
        "test_mean_net_bps":report["test"]["mean_net_spot_bps"] if report["test"] else None,
        "capital_authority":"NONE"
    },indent=2))


if __name__=="__main__":
    main()
