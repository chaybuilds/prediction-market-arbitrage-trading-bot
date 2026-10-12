"""Crash-safe local paper ledger; no brokerage account access."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time


def utc_day(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


class Ledger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
          id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, event_slug TEXT NOT NULL,
          label TEXT NOT NULL, details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_positions (
          id INTEGER PRIMARY KEY, event_slug TEXT NOT NULL UNIQUE,
          market_slug TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL,
          price REAL NOT NULL, fee REAL NOT NULL, slippage REAL NOT NULL,
          stake REAL NOT NULL, opened_at INTEGER NOT NULL, window_end INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'OPEN', outcome REAL, pnl REAL,
          verified_at INTEGER);
        CREATE INDEX IF NOT EXISTS ix_paper_open
          ON paper_positions(state, window_end);
        """)
        self.db.execute("""CREATE TABLE IF NOT EXISTS paper_entry_intents (
            event_slug TEXT PRIMARY KEY, market_slug TEXT NOT NULL,
            side TEXT NOT NULL, signaled_at INTEGER NOT NULL,
            signal_ask REAL NOT NULL, model_version TEXT NOT NULL,
            signal_edge REAL NOT NULL, state TEXT NOT NULL,
            rejection_reason TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS paper_exit_intents (
             position_id INTEGER PRIMARY KEY, signaled_at INTEGER NOT NULL,
             signal_bid REAL NOT NULL, signal_net_share REAL NOT NULL,
             reason TEXT NOT NULL,
             FOREIGN KEY (position_id) REFERENCES paper_positions(id))""")
        # Forward-compatible migration: keep all old entries and losses intact.
        columns = {x[1] for x in self.db.execute("PRAGMA table_info(paper_positions)")}
        for column, kind in (
            ("exit_bid", "REAL"), ("exit_at", "INTEGER"),
            ("exit_fee", "REAL"), ("exit_slippage", "REAL"),
            ("exit_reason", "TEXT")):
            if column not in columns:
                self.db.execute("ALTER TABLE paper_positions ADD COLUMN %s %s" % (column, kind))
        self.db.execute("""CREATE TABLE IF NOT EXISTS forecast_registry (
            event_slug TEXT NOT NULL, market_slug TEXT NOT NULL,
            horizon_label TEXT NOT NULL, issued_at INTEGER NOT NULL,
            window_end INTEGER NOT NULL, p_up REAL NOT NULL,
            ask_up REAL NOT NULL, bid_up REAL NOT NULL,
            brti_price REAL NOT NULL, price_to_beat REAL NOT NULL,
            model_version TEXT NOT NULL, source_digest TEXT NOT NULL,
            PRIMARY KEY (event_slug,horizon_label))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS forecast_outcomes (
            event_slug TEXT PRIMARY KEY, market_slug TEXT NOT NULL,
            yes_settlement REAL NOT NULL, verified_at INTEGER NOT NULL,
            evidence_url TEXT NOT NULL)""")
        self.db.commit()

    def decision(self, ts, slug, label, details=""):
        self.db.execute(
            "INSERT INTO decisions(ts,event_slug,label,details) VALUES (?,?,?,?)",
            (int(ts), slug, label, details[:600]))
        self.db.commit()

    def exists(self, event_slug):
        return self.db.execute(
            "SELECT 1 FROM paper_positions WHERE event_slug=?", (event_slug,)
        ).fetchone() is not None

    def stats(self, settings, now=None):
        now = int(time.time() if now is None else now)
        realized = self.db.execute(
            "SELECT COALESCE(SUM(pnl),0) AS v FROM paper_positions WHERE state IN ('SETTLED','EARLY_EXIT')"
        ).fetchone()["v"]
        exposure = self.db.execute(
            "SELECT COALESCE(SUM(stake),0) AS v FROM paper_positions WHERE state='OPEN'"
        ).fetchone()["v"]
        day = utc_day(now)
        today = self.db.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS pnl
               FROM paper_positions WHERE substr(datetime(opened_at,'unixepoch'),1,10)=?""",
            (day,)
        ).fetchone()
        return {
            "bankroll": round(settings.paper_bankroll + realized, 4),
            "available": round(settings.paper_bankroll + realized - exposure, 4),
            "open_exposure": round(exposure, 4),
            "trades_today": today["n"],
            "realized_today": round(today["pnl"], 4),
            "realized_total": round(realized, 4),
            "pnl_truth": "PAPER_MODELED_OUTCOMES_ONLY",
        }

    def enter(self, window, candidate, qty, settings, now):
        """Atomic one-trade-per-window rule with a second risk check."""
        if qty <= 0 or not 0 < candidate.ask_equivalent < 1:
            return False, "bad_quantity"
        fee = qty * window.fee_coeff * candidate.ask_equivalent * (1 - candidate.ask_equivalent)
        slippage = qty * settings.slippage_usd_per_share
        stake = qty * candidate.ask_equivalent + fee + slippage
        self.db.execute("BEGIN IMMEDIATE")
        try:
            realized = self.db.execute(
                "SELECT COALESCE(SUM(pnl),0) FROM paper_positions WHERE state IN ('SETTLED','EARLY_EXIT')"
            ).fetchone()[0]
            exposure = self.db.execute(
                "SELECT COALESCE(SUM(stake),0) FROM paper_positions WHERE state='OPEN'"
            ).fetchone()[0]
            day = utc_day(now)
            count, day_pnl = self.db.execute(
                """SELECT COUNT(*),COALESCE(SUM(pnl),0) FROM paper_positions
                   WHERE substr(datetime(opened_at,'unixepoch'),1,10)=?""",
                (day,)
            ).fetchone()
            exists = self.db.execute(
                "SELECT 1 FROM paper_positions WHERE event_slug=?", (window.event_slug,)
            ).fetchone()
            if exists:
                reason = "duplicate_window"
            elif int(now) < window.start + settings.min_seconds_after_start:
                reason = "too_early"
            elif int(now) > window.end - settings.min_seconds_before_end:
                reason = "expiry_too_near"
            elif day_pnl <= -settings.max_daily_loss_usd:
                reason = "daily_loss_stop"
            elif count >= settings.max_trades_per_day:
                reason = "daily_trade_limit"
            elif stake > settings.max_ticket_usd + 1e-7:
                reason = "ticket_limit"
            elif max(0.0, -day_pnl) + exposure + stake > settings.max_daily_loss_usd + 1e-7:
                # Worst-case binary loss: reserve the entire cost of open entries.
                # A realized stop by itself cannot cap losses on simultaneous opens.
                reason = "daily_worst_case_loss_cap"
            elif exposure + stake > settings.max_open_exposure_usd + 1e-7:
                reason = "exposure_limit"
            elif stake > settings.paper_bankroll + realized - exposure + 1e-7:
                reason = "insufficient_paper_cash"
            else:
                reason = None
            if reason:
                self.db.rollback()
                return False, reason
            self.db.execute(
                """INSERT INTO paper_positions(event_slug,market_slug,side,qty,price,fee,
                  slippage,stake,opened_at,window_end)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (window.event_slug, window.market_slug, candidate.side, qty,
                 candidate.ask_equivalent, fee, slippage, stake, int(now), window.end)
            )
            self.db.commit()
            return True, "paper_entered"
        except Exception:
            self.db.rollback()
            raise

    def entry_intent(self,event_slug):
        return self.db.execute("SELECT * FROM paper_entry_intents WHERE event_slug=?",
            (event_slug,)).fetchone()

    def signal_entry(self,window,choice,forecast,now):
        """Pre-register entry before a later, independently fetched book can fill it."""
        if not (0<choice.ask_equivalent<1 and choice.side in ("UP","DOWN") and
                window.start<=now<window.end):
            return False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM paper_positions WHERE event_slug=?",
                               (window.event_slug,)).fetchone():
                self.db.rollback();return False
            cur=self.db.execute("""INSERT OR IGNORE INTO paper_entry_intents
                 VALUES (?,?,?,?,?,?,?,? ,?)""",
                 (window.event_slug,window.market_slug,choice.side,int(now),
                  choice.ask_equivalent,forecast.model_version,
                  choice.expected_net_edge,"SIGNALLED",None))
            ok=cur.rowcount==1
            self.db.commit();return ok
        except Exception:
            self.db.rollback();raise

    def finish_entry_intent(self,event_slug,state,reason=None):
        if state not in ("PAPER_FILLED","REJECTED"):
            raise ValueError("invalid_entry_terminal_state")
        cur=self.db.execute("""UPDATE paper_entry_intents SET state=?,rejection_reason=?
             WHERE event_slug=? AND state='SIGNALLED'""",(state,reason,event_slug))
        self.db.commit()
        return cur.rowcount==1

    def record_forecast(self,window,forecast,book,now):
        # Choose fixed forward time horizons. Never retroactively change a
        # forecast, and never treat later event settlement as a feature.
        age=int(now)-window.start
        bucket=next((tag for at,tag in ((120,"2m"),(300,"5m"),(480,"8m"))
                     if at<=age<at+90),None)
        if bucket is None or not all(map(math.isfinite,
            (forecast.p_up,forecast.latest_index,window.target,book.ask,book.bid))):
            return False
        if not 0<forecast.p_up<1 or not 0<book.bid<book.ask<1:
            return False
        payload=[window.event_slug,window.market_slug,bucket,int(now),
                 round(forecast.p_up,10),book.ask,book.bid,
                 round(forecast.latest_index,4),window.target,forecast.model_version]
        digest=hashlib.sha256(json.dumps(payload,separators=(",",":")).encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            q=self.db.execute("""INSERT OR IGNORE INTO forecast_registry VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (window.event_slug,window.market_slug,bucket,int(now),window.end,
                 forecast.p_up,book.ask,book.bid,forecast.latest_index,
                 window.target,forecast.model_version,digest))
            added=q.rowcount==1
            self.db.commit()
            return added
        except Exception:
            self.db.rollback();raise

    def forecast_outcome_backlog(self,now,limit=4):
        return list(self.db.execute("""SELECT f.event_slug,f.market_slug
             FROM forecast_registry f LEFT JOIN forecast_outcomes o
             ON f.event_slug=o.event_slug
             WHERE o.event_slug IS NULL AND f.window_end+90<=?
             GROUP BY f.event_slug,f.market_slug ORDER BY MIN(f.window_end)
             LIMIT ?""",(int(now),limit)).fetchall())

    def record_forecast_outcome(self,event_slug,market_slug,yes_settlement,now):
        if yes_settlement not in (0.,1.):
            return False
        if not self.db.execute("SELECT 1 FROM forecast_registry WHERE event_slug=? AND market_slug=?",
                               (event_slug,market_slug)).fetchone():
            return False
        url="https://gateway.polymarket.us/v1/markets/"+market_slug+"/settlement"
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing=self.db.execute("SELECT yes_settlement FROM forecast_outcomes WHERE event_slug=?",
                                    (event_slug,)).fetchone()
            if existing:
                if existing[0]!=yes_settlement:
                    raise RuntimeError("immutable_forecast_outcome_conflict")
                self.db.rollback();return False
            self.db.execute("INSERT INTO forecast_outcomes VALUES (?,?,?,?,?)",
                (event_slug,market_slug,yes_settlement,int(now),url))
            self.db.commit();return True
        except Exception:
            self.db.rollback();raise

    def calibration(self):
        rows=self.db.execute("""SELECT f.horizon_label,f.p_up,o.yes_settlement,
              (f.bid_up+f.ask_up)/2 AS market_mid,
              f.event_slug FROM forecast_registry f
              JOIN forecast_outcomes o USING(event_slug)
              ORDER BY f.issued_at""").fetchall()
        groups={}
        for row in rows:
            k=row["horizon_label"]
            group=groups.setdefault(k,{"n":0,"brier_sum":0.,"market_brier_sum":0.,
                                       "sum_p":0.,"up":0})
            p=row["p_up"];truth=row["yes_settlement"]
            group["n"]+=1
            group["brier_sum"]+=(p-truth)**2
            group["market_brier_sum"]+=(row["market_mid"]-truth)**2
            group["sum_p"]+=p
            group["up"]+=truth
        return {"mode":"FORWARD_PRE_REGISTERED_FORECAST_ONLY",
          "calibrations":{k:{"sample_count":v["n"],
            "brier":round(v["brier_sum"]/v["n"],5),
            "market_mid_baseline_brier":round(v["market_brier_sum"]/v["n"],5),
            "mean_predicted_up":round(v["sum_p"]/v["n"],5),
            "observed_up_rate":round(v["up"]/v["n"],5),
            "maturity":"INSUFFICIENT_FOR_PROMOTION" if v["n"]<100 else "REVIEW_REQUIRED"}
                     for k,v in sorted(groups.items())},
          "registered_forecasts":self.db.execute("SELECT COUNT(*) FROM forecast_registry").fetchone()[0],
          "resolved_events":self.db.execute("SELECT COUNT(*) FROM forecast_outcomes").fetchone()[0]}

    def active_positions(self):
        return list(self.db.execute("SELECT * FROM paper_positions WHERE state='OPEN'").fetchall())

    def exit_intent(self, position_id):
        return self.db.execute("SELECT * FROM paper_exit_intents WHERE position_id=?",
                               (position_id,)).fetchone()

    def signal_exit(self, position_id, now, bid, net_share, reason):
        if not 0 < bid < 1:
            return False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row=self.db.execute("SELECT state FROM paper_positions WHERE id=?",
                                (position_id,)).fetchone()
            if row is None or row["state"]!="OPEN":
                self.db.rollback();return False
            cur=self.db.execute("""INSERT OR IGNORE INTO paper_exit_intents
                (position_id,signaled_at,signal_bid,signal_net_share,reason)
                VALUES (?,?,?,?,?)""",(position_id,int(now),bid,net_share,reason))
            inserted=cur.rowcount==1
            self.db.commit()
            return inserted
        except Exception:
            self.db.rollback();raise

    def paper_exit(self, position_id, now, bid, fee_coefficient, slippage_per_share,
                   min_confirmation_s=20, min_buffer_s=90):
        # Simulate a bid-side taker exit ONLY after an earlier quote signal.
        if not 0 < bid < 1 or not 0 < fee_coefficient <= .2:
            return False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            pos=self.db.execute("SELECT * FROM paper_positions WHERE id=? AND state='OPEN'",
                                (position_id,)).fetchone()
            intent=self.db.execute("SELECT * FROM paper_exit_intents WHERE position_id=?",
                                   (position_id,)).fetchone()
            if pos is None or intent is None or not (
                int(now)-intent["signaled_at"]>=min_confirmation_s and
                int(now)<pos["window_end"]-min_buffer_s):
                self.db.rollback();return False
            qty=pos["qty"]
            exit_fee=qty*fee_coefficient*bid*(1-bid)
            exit_slip=qty*slippage_per_share
            pnl=qty*(bid-pos["price"])-pos["fee"]-pos["slippage"]-exit_fee-exit_slip
            updated=self.db.execute("""UPDATE paper_positions SET
                 state='EARLY_EXIT',exit_bid=?,exit_fee=?,exit_slippage=?,
                 exit_at=?,exit_reason=?,pnl=? WHERE id=? AND state='OPEN'""",
                (bid,exit_fee,exit_slip,int(now),intent["reason"],pnl,position_id)).rowcount
            self.db.commit()
            return updated==1
        except Exception:
            self.db.rollback();raise

    def pending(self, now=None):
        now = int(time.time() if now is None else now)
        return list(self.db.execute(
            "SELECT * FROM paper_positions WHERE state='OPEN' AND window_end + 90 <= ?",
            (now,)
        ).fetchall())

    def settle(self, position_id, yes_settlement, now=None):
        if yes_settlement not in (0.0, 1.0):
            return False
        now = int(time.time() if now is None else now)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM paper_positions WHERE id=? AND state='OPEN'",
                (position_id,)
            ).fetchone()
            if row is None:
                self.db.rollback()
                return False
            wins = (row["side"] == "UP" and yes_settlement == 1.0) or (
                row["side"] == "DOWN" and yes_settlement == 0.0)
            pnl = row["qty"] * ((1.0 if wins else 0.0) - row["price"]) - row["fee"] - row["slippage"]
            self.db.execute(
                """UPDATE paper_positions SET state='SETTLED', outcome=?, pnl=?,
                   verified_at=? WHERE id=? AND state='OPEN'""",
                (yes_settlement, pnl, now, position_id)
            )
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()
