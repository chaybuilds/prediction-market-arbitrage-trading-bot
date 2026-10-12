"""Polymarket US 15m point-in-time display quote archive. Public GET only.

Price-history is ~minute sampled and is NOT a full historical order book.
The official outcome label is obtained independently after the market ends.
Historical availability varies. No settlement or future quote may be used
as a predictor at an earlier timestamp.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import argparse
import gzip
import hashlib
import json
import math
import sqlite3
import threading
import time

BASE = "https://gateway.polymarket.us"
LAUNCH_UTC = 1790092800   # Sep 22 2026, 16:00 UTC
FEE_CHANGE_UTC = 1791381600  # Oct 7 2026, 14:00 UTC: documented fee schedule


def slug_at(start):
    return "btc-updown-15m-" + datetime.fromtimestamp(
        start, timezone.utc).strftime("%Y-%m-%d-%H%Mz").lower()


class RateGate:
    def __init__(self, interval=0.17):
        self.next = 0.0
        self.interval = interval
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            delay = self.next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next = time.monotonic() + self.interval


class DataUnavailable(RuntimeError):
    pass


class USClient:
    """Read-only, non-authenticated US endpoints, explicit request pacing."""

    def __init__(self, gate=None):
        self.gate = gate or RateGate()

    def get(self, path, params=None):
        uri = BASE + path + ("?" + urlencode(params) if params else "")
        for attempt in range(4):
            self.gate.wait()
            try:
                req = Request(uri, headers={"User-Agent": "ORACLE15-HistoricalResearch/0.2"})
                with urlopen(req, timeout=10) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code == 404:
                    raise DataUnavailable("http_404") from exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise DataUnavailable("http_" + str(exc.code)) from exc
                time.sleep(min(2**attempt, 8))
            except (URLError, OSError, ValueError) as exc:
                if attempt == 3:
                    raise DataUnavailable(type(exc).__name__) from exc
                time.sleep(min(2**attempt, 8))
        raise DataUnavailable("retries_exhausted")

    def window(self, start):
        event_slug = slug_at(start)
        market_slug = "cpc-" + event_slug
        raw_event = self.get("/v1/events/slug/" + quote(event_slug, safe=""))
        event = raw_event.get("event") or {}
        markets = event.get("markets") or []
        if event.get("slug") != event_slug or len(markets) != 1:
            raise DataUnavailable("missing_or_ambiguous_event")
        m = markets[0]
        terms = m.get("assetPriceTerms") or {}
        asset = terms.get("asset") or {}
        actual_start = int(datetime.fromisoformat(terms["windowStart"].replace("Z", "+00:00")).timestamp())
        actual_end = int(datetime.fromisoformat(terms["windowEnd"].replace("Z", "+00:00")).timestamp())
        if not (
            m.get("slug") == market_slug and actual_start == start and actual_end == start + 900
            and terms.get("marketType") == "ASSET_PRICE_MARKET_TYPE_UP_DOWN"
            and terms.get("indexSymbol") == "BRTI"
            and asset.get("symbol", "").lower() == "btc"
        ):
            raise DataUnavailable("wrong_market_or_settlement_spec")
        target = float((terms.get("priceToBeat") or {}).get("value") or 0)
        fee = float(m.get("feeCoefficient") or 0)
        if not (0 < target and 0 <= fee <= 0.2):
            raise DataUnavailable("invalid_target_or_fee")
        hist = self.get("/v1/price-history", {
            "symbol": market_slug, "timestamp.startTimestamp": start,
            "timestamp.endTimestamp": start + 900, "fidelity": 1,
        })
        settlement = self.get("/v1/markets/" + quote(market_slug, safe="") + "/settlement")
        outcome = settlement.get("settlement")
        if outcome is None or float(outcome) not in (0.0, 1.0):
            raise DataUnavailable("unresolved_outcome")
        cleaned = {}
        for item in hist.get("history") or []:
            try:
                ts = int(item["timestamp"])
                long_ask = float(item["longPrice"])
                short_ask = float(item["shortPrice"])
            except (KeyError, TypeError, ValueError):
                continue
            if not start <= ts < start + 900:
                continue  # crucial: exclude closing / post-settlement $1 repricing
            if not (0 < long_ask < 1 and 0 < short_ask < 1):
                continue
            bid_up = 1 - short_ask
            bid_down = 1 - long_ask
            spread = long_ask + short_ask - 1
            if not (0 < bid_up <= long_ask and 0 < bid_down <= short_ask and
                    -0.000001 <= spread <= 0.25 and
                    all(math.isfinite(v) for v in [long_ask,short_ask,bid_up,bid_down])):
                continue
            cleaned[ts] = (ts, long_ask, short_ask, bid_up, bid_down, max(0,spread))
        provenance = {"event": raw_event, "price_history": hist, "settlement": settlement,
                      "retrieved_at": int(time.time()), "venue": "POLYMARKET_US",
                      "query_window": [start, start + 900]}
        data = json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()
        return {
            "start": start, "event_slug": event_slug, "market_slug": market_slug,
            "target": target, "settlement": float(outcome), "fee": fee,
            "fee_regime": "CURRENT_DOC" if start >= FEE_CHANGE_UTC else "PRE_OCT7_UNVERIFIED",
            "quotes": sorted(cleaned.values()), "raw": data,
            "digest": hashlib.sha256(data).hexdigest(),
        }


def open_db(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS markets (
      start INTEGER PRIMARY KEY,
      event_slug TEXT NOT NULL UNIQUE, market_slug TEXT,
      target REAL, settlement REAL, fee REAL, fee_regime TEXT,
      quote_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
      source_digest TEXT, retrieved_at INTEGER, reason TEXT);
    CREATE TABLE IF NOT EXISTS quotes (
      market_start INTEGER NOT NULL, ts INTEGER NOT NULL,
      ask_up REAL NOT NULL, ask_down REAL NOT NULL,
      bid_up REAL NOT NULL, bid_down REAL NOT NULL, spread REAL NOT NULL,
      PRIMARY KEY (market_start, ts),
      FOREIGN KEY (market_start) REFERENCES markets(start));
    CREATE INDEX IF NOT EXISTS ix_quotes_ts ON quotes(ts);
    """)
    connection.commit()
    return connection


def save(db, result, raw_root):
    start = result["start"]
    slug = result["event_slug"]
    directory = Path(raw_root) / datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d")
    directory.mkdir(parents=True, exist_ok=True)
    if result.get("raw") is not None:
        dest = directory / (slug + ".json.gz")
        tmp = dest.with_suffix(".tmp")
        with gzip.open(tmp, "wb") as fp:
            fp.write(result["raw"])
        tmp.replace(dest)
    quotes = result.get("quotes") or []
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""INSERT OR REPLACE INTO markets
          (start,event_slug,market_slug,target,settlement,fee,fee_regime,quote_count,
           status,source_digest,retrieved_at,reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            start,slug,result.get("market_slug"),result.get("target"),
            result.get("settlement"),result.get("fee"),result.get("fee_regime"),
            len(quotes),result["status"],result.get("digest"),int(time.time()),
            result.get("reason"),
        ))
        db.execute("DELETE FROM quotes WHERE market_start=?", (start,))
        db.executemany("INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                       [(start, *q) for q in quotes])
        db.commit()
    except Exception:
        db.rollback()
        raise


def retrieve_one(client, start):
    try:
        result = client.window(start)
        result["status"] = "OK" if len(result["quotes"]) >= 8 else "SPARSE_QUOTES"
        return result
    except (DataUnavailable, KeyError, ValueError, TypeError) as exc:
        return {"start":start, "event_slug":slug_at(start), "status":"UNAVAILABLE",
                "reason":str(exc)[:180]}


def main():
    parser = argparse.ArgumentParser(description="Backfill POLYMARKET_US 15m only; no trade access")
    parser.add_argument("--start", default="2026-10-07T14:00:00Z",
                        help="Inclusive ISO-UTC; launch 2026-09-22T16:00:00Z")
    parser.add_argument("--end", help="Exclusive ISO-UTC; defaults to last fully settled hour")
    parser.add_argument("--limit", type=int, default=0, help="Take N earliest missing windows (0 all)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--db", default="state/oracle15/research/us_quotes.sqlite3")
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("workers max 6 to protect public API limits")
    iso = lambda s: int(datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
    start = iso(args.start)
    max_end = int(time.time() // 900 * 900) - 3600
    end = min(iso(args.end), max_end) if args.end else max_end
    if start < LAUNCH_UTC or start % 900 or end <= start:
        parser.error("UTC quarter hour range invalid or before US launch")
    connection = open_db(args.db)
    recorded = set() if args.refresh else {
        r[0] for r in connection.execute(
            "SELECT start FROM markets WHERE status IN ('OK','SPARSE_QUOTES')")}
    windows = [ts for ts in range(start,end,900) if ts not in recorded]
    if args.limit > 0:
        windows = windows[:args.limit]
    print(json.dumps({"venue":"POLYMARKET_US","start":start,"end":end,
                      "total_candidates":len(range(start,end,900)),
                      "missing":len(windows),"already_recorded":len(recorded),
                      "strategy":"historical_data_only"}), flush=True)
    if not windows:
        return 0
    client = USClient()
    counts = {}
    raw_root = Path(args.db).parent / "raw"
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(retrieve_one, client, ts):ts for ts in windows}
        for i, fut in enumerate(as_completed(futures),1):
            result = fut.result()
            save(connection, result, raw_root)
            status = result["status"]
            counts[status] = counts.get(status,0)+1
            if i % 25 == 0 or i == len(windows):
                print(json.dumps({"done":i,"total":len(windows),"counts":counts,
                                  "latest_saved":result["event_slug"],
                                  "quoted_rows":connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]}),
                      flush=True)
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
