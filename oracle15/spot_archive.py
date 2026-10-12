"""Reproducible Binance BTCUSDT 15m SPOT + USD-M FUTURES historical archive.

This is a CROSS-VENUE RESEARCH COVARIATE, never the BRTI settlement source
and never a Polymarket quote or executable US market order. Downloaded files
are verified against Binance's matching SHA-256 CHECKSUM, then retained.
Official source: https://github.com/binance/binance-public-data
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import argparse
import csv
import hashlib
import io
import json
import sqlite3
import time
import zipfile

from .archive import RateGate

ORIGIN = "https://data.binance.vision/data"
VENUES = {"spot":"spot", "futures_um":"futures/um"}
MAX_ZIP_BYTES = 8*1024*1024
MAX_UNZIP_BYTES = 10*1024*1024


def months(start, end):
    year,month = [int(x) for x in start.split("-")]
    end_year,end_month = [int(x) for x in end.split("-")]
    while (year,month) <= (end_year,end_month):
        yield "%04d-%02d" %(year,month)
        month+=1
        if month==13:
            year+=1;month=1


def base_url(venue,month):
    base="%s/%s/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-%s.zip" % (
        ORIGIN,VENUES[venue],month)
    return base


class DownloadFailure(RuntimeError):
    pass


def download(url,gate,max_bytes):
    for retry in range(3):
        gate.wait()
        try:
            req=Request(url,headers={"User-Agent":"ORACLE15-HistoricalResearch/0.2"})
            with urlopen(req,timeout=18) as r:
                data=r.read(max_bytes+1)
            if len(data)>max_bytes:
                raise DownloadFailure("oversized_download")
            return data
        except HTTPError as exc:
            if exc.code==404:
                raise DownloadFailure("http_404") from exc
            if retry==2:
                raise DownloadFailure("http_"+str(exc.code)) from exc
        except (URLError,OSError) as exc:
            if retry==2:
                raise DownloadFailure(type(exc).__name__) from exc
        time.sleep(retry+1)
    raise DownloadFailure("retries_exhausted")


def parse_zip(content,month):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        members=z.infolist()
        if len(members)!=1 or members[0].is_dir() or members[0].file_size>MAX_UNZIP_BYTES:
            raise DownloadFailure("invalid_zip_members")
        expected_name="BTCUSDT-15m-"+month+".csv"
        if members[0].filename != expected_name:
            raise DownloadFailure("unexpected_zip_filename")
        text=z.read(expected_name).decode("utf-8")
    rows=[]
    for raw in csv.reader(io.StringIO(text)):
        if not raw or raw[0].strip().lower().replace("_"," ") == "open time":
            continue
        if len(raw)<11:
            raise DownloadFailure("invalid_kline_fields")
        try:
            raw_epoch=int(raw[0])
            # Binance SPOT data uses MICROSECONDS from Jan 2025, old data
            # and some derivatives use milliseconds. Normalize explicitly.
            start = raw_epoch//1000000 if raw_epoch >= 10**14 else raw_epoch//1000
            when=time.gmtime(start)
            if "%04d-%02d"%(when.tm_year,when.tm_mon)!=month or start%900:
                raise DownloadFailure("invalid_timestamp_units_or_month")
            o,h,l,c = [float(x) for x in raw[1:5]]
            vol=float(raw[5]); quote_vol=float(raw[7])
            trades=int(raw[8]); buy_quote=float(raw[10])
            if not (min(o,h,l,c)>0 and h>=max(o,c,l) and
                    l<=min(o,c,h) and vol>=0 and quote_vol>=0 and
                    0<=buy_quote<=quote_vol*1.01 and trades>=0):
                raise DownloadFailure("invalid_candle_values")
        except (ValueError,OverflowError,IndexError) as exc:
            raise DownloadFailure("malformed_candle") from exc
        rows.append((start,o,h,l,c,vol,quote_vol,trades,buy_quote))
    if not rows or any(b[0]<=a[0] for a,b in zip(rows,rows[1:])):
        raise DownloadFailure("missing_or_unsorted_rows")
    return rows


def fetch_month(gate,venue,month):
    url=base_url(venue,month)
    try:
        content=download(url,gate,MAX_ZIP_BYTES)
        checksum=download(url+".CHECKSUM",gate,1000).decode("utf-8","replace").strip()
        expected=checksum.split()[0].lower()
        sha=hashlib.sha256(content).hexdigest()
        if len(expected)!=64 or sha!=expected:
            raise DownloadFailure("official_sha256_mismatch")
        rows=parse_zip(content,month)
        return {"venue":venue,"month":month,"zip":content,"sha":sha,
                "checksum":checksum,"rows":rows,"status":"VERIFIED"}
    except (DownloadFailure,zipfile.BadZipFile,UnicodeError) as exc:
        return {"venue":venue,"month":month,"status":"ERROR","reason":str(exc)[:120]}


def open_spot_db(path):
    p=Path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(str(p),timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
      CREATE TABLE IF NOT EXISTS source_files (
        venue TEXT NOT NULL, month TEXT NOT NULL, source_sha256 TEXT,
        row_count INTEGER, status TEXT NOT NULL, fetched_at INTEGER NOT NULL,
        reason TEXT, PRIMARY KEY (venue,month));
      CREATE TABLE IF NOT EXISTS spot_bars (
        venue TEXT NOT NULL,open_ts INTEGER NOT NULL,
        open REAL,high REAL,low REAL,close REAL,
        base_volume REAL,quote_volume REAL,trades INTEGER,
        buy_quote_volume REAL,source_sha256 TEXT NOT NULL,
        PRIMARY KEY (venue,open_ts));
      CREATE INDEX IF NOT EXISTS ix_spot_bars_ts ON spot_bars(open_ts);
      """)
    c.commit()
    return c


def save_result(db,root,result):
    venue=result["venue"];month=result["month"]
    if result["status"]=="VERIFIED":
        p=Path(root)/venue/(month+".zip")
        p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_suffix(".download")
        tmp.write_bytes(result["zip"])
        tmp.replace(p)
        (p.parent/(month+".sha256")).write_text(result["sha"]+"\n")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""INSERT OR REPLACE INTO source_files VALUES (?,?,?,?,?,?,?)""",
                   (venue,month,result.get("sha"),len(result.get("rows",[])),
                    result["status"],int(time.time()),result.get("reason")))
        if result["status"]=="VERIFIED":
            db.executemany("INSERT OR REPLACE INTO spot_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(venue,*row,result["sha"]) for row in result["rows"]])
        db.commit()
    except Exception:
        db.rollback()
        raise


def main():
    p=argparse.ArgumentParser(description="Verify and archive BTCUSDT 15m SPOT/PERP klines; no trading")
    p.add_argument("--start",default="2020-01")
    p.add_argument("--end",default="2026-09")
    p.add_argument("--venues",default="spot,futures_um")
    p.add_argument("--workers",type=int,default=4)
    p.add_argument("--max-months",type=int,default=0)
    p.add_argument("--db",default="state/oracle15/research/spot.sqlite3")
    p.add_argument("--refresh",action="store_true")
    args=p.parse_args()
    requested=args.venues.split(",")
    if not 1<=args.workers<=5 or any(v not in VENUES for v in requested):
        p.error("unsupported venue or workers")
    candidates=[(v,m) for m in months(args.start,args.end) for v in requested]
    db=open_spot_db(args.db)
    done=set() if args.refresh else set(db.execute(
        "SELECT venue,month FROM source_files WHERE status='VERIFIED'").fetchall())
    candidates=[x for x in candidates if x not in done]
    if args.max_months>0:
        candidates=candidates[:args.max_months]
    print(json.dumps({"source":"BINANCE_PUBLIC_SPOT_AND_PERPETUALS_NOT_BRTI",
                      "requested_months":len(candidates),"cached":len(done)}),flush=True)
    gate=RateGate(0.18)
    destination=Path(args.db).parent/"binance-raw"
    counts={}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(fetch_month,gate,v,m):(v,m) for v,m in candidates}
        for i,fut in enumerate(as_completed(futures),1):
            result=fut.result()
            save_result(db,destination,result)
            status=result["status"]
            counts[status]=counts.get(status,0)+1
            if i%10==0 or i==len(candidates):
                print(json.dumps({"done":i,"total":len(candidates),"status":counts,
                                  "bars":db.execute("SELECT COUNT(*) FROM spot_bars").fetchone()[0]}),flush=True)
    db.close()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
