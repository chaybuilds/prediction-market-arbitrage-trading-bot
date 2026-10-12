"""Crash-safe local paper ledger; no brokerage account access."""
from datetime import datetime, timezone
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
            "SELECT COALESCE(SUM(pnl),0) AS v FROM paper_positions WHERE state='SETTLED'"
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
                "SELECT COALESCE(SUM(pnl),0) FROM paper_positions WHERE state='SETTLED'"
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
