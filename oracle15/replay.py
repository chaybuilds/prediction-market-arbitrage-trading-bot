"""Strictly chronological US 15-minute quote replay, not a fill simulator.

Each signal at timestamp T fills no earlier than the NEXT recorded display quote.
Ask buys / bid sells, symmetric taker fees, slippage both legs. No order book
depth or queue position exists in historical display prices; reported profit is
conditional on receiving those displayed quotes and thus NOT realized P&L.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import sqlite3
import statistics

from .model import taker_fee_per_share


@dataclass(frozen=True)
class Quote:
    ts: int
    ask_up: float
    ask_down: float
    bid_up: float
    bid_down: float
    spread: float

    def ask(self, side):
        return self.ask_up if side == "UP" else self.ask_down

    def bid(self, side):
        return self.bid_up if side == "UP" else self.bid_down

    @property
    def mid_up(self):
        return (self.ask_up + self.bid_up) / 2


@dataclass(frozen=True)
class Event:
    start: int
    slug: str
    fee: float
    settlement: float
    quotes: tuple
    fee_regime: str


@dataclass(frozen=True)
class Spec:
    family: str
    threshold: float
    target: float
    stop: float
    hold_s: int
    mode: str = "SCALP"

    @property
    def name(self):
        return ("%s/%.2f/%s/t%.2f/s%.2f/h%d" %
                (self.family, self.threshold, self.mode,
                 self.target, self.stop, self.hold_s))


@dataclass(frozen=True)
class Cost:
    per_share_slippage: float = 0.01
    fee_multiplier: float = 1.0
    latency_quotes: int = 1
    max_stake: float = 2.50
    price_floor: float = 0.15
    price_ceiling: float = 0.85
    max_spread: float = 0.06
    max_signal_fill_move: float = 0.15
    cutoff_seconds: int = 300
    min_trade_age_seconds: int = 120


@dataclass(frozen=True)
class Trade:
    event_start: int
    slug: str
    side: str
    family: str
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    qty: float
    fees: float
    slippage: float
    pnl: float
    exit_mode: str
    yes_settlement: float


def load_events(db_path, require_current_fee=True):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT start,event_slug,fee,settlement,fee_regime
           FROM markets WHERE status='OK' AND quote_count>=8
           ORDER BY start"""
    ).fetchall()
    result = []
    for market in rows:
        if require_current_fee and market["fee_regime"] != "CURRENT_DOC":
            continue
        try:
            if (market["fee"] is None or market["settlement"] not in (0.,1.) or
                not 0 <= market["fee"] <= .2):
                continue
            history = connection.execute(
                """SELECT ts,ask_up,ask_down,bid_up,bid_down,spread
                   FROM quotes WHERE market_start=? ORDER BY ts""",
                (market["start"],)
            )
            quotes = tuple(Quote(*q) for q in history.fetchall())
            if len(quotes) >= 8:
                result.append(Event(int(market["start"]),market["event_slug"],
                                    float(market["fee"]), float(market["settlement"]),
                                    quotes, market["fee_regime"]))
        except (TypeError, ValueError):
            continue
    connection.close()
    return result


def available_side(event, spec, i):
    """Signals exclusively use q[0..i], never the fill quote q[i+1]."""
    quotes = event.quotes
    current = quotes[i]
    if i < 2:
        return None
    previous = quotes[i-1]
    older = quotes[i-2]
    delta = current.mid_up - previous.mid_up
    older_delta = previous.mid_up - older.mid_up
    k = spec.threshold
    name = spec.family
    if name == "always_up":
        return "UP"
    if name == "always_down":
        return "DOWN"
    if name == "seeded_random":
        # Stable holdout baseline, does not peek at settlement/outcome.
        seed = hashlib.sha256((event.slug+":control").encode()).digest()
        return "UP" if seed[0] % 2 else "DOWN"
    if name == "quote_momentum":
        return "UP" if delta >= k else ("DOWN" if delta <= -k else None)
    if name == "quote_reversion":
        return "DOWN" if delta >= k else ("UP" if delta <= -k else None)
    if name == "two_step_trend":
        return "UP" if min(delta, older_delta) >= k else (
            "DOWN" if max(delta, older_delta) <= -k else None)
    if name == "v_reversal":
        return "UP" if delta >= k and older_delta <= -k else (
            "DOWN" if delta <= -k and older_delta >= k else None)
    if name == "breakout":
        high = max(q.mid_up for q in quotes[max(0,i-5):i])
        low = min(q.mid_up for q in quotes[max(0,i-5):i])
        return "UP" if current.mid_up >= high+k else (
            "DOWN" if current.mid_up <= low-k else None)
    if name == "range_fade":
        # Fades price overshoot, no settlement outcome used in signal.
        return "DOWN" if current.mid_up >= 0.70+k else (
            "UP" if current.mid_up <= 0.30-k else None)
    if name == "spread_squeeze":
        if previous.spread-current.spread < k:
            return None
        return "UP" if delta>0 else ("DOWN" if delta<0 else None)
    return None


def fee(price, theta, costs):
    return taker_fee_per_share(price, max(0.0000001, min(0.2,theta))) * costs.fee_multiplier


def evaluate_trade(event, spec, costs=Cost()):
    """One and only one position per window; no intrabar fills or hidden volume."""
    q = event.quotes
    n = len(q)
    for i in range(2, n):
        signal = q[i]
        age = signal.ts - event.start
        if age < costs.min_trade_age_seconds or age > 900-costs.cutoff_seconds:
            continue
        if signal.spread > costs.max_spread:
            continue
        side = available_side(event,spec,i)
        if not side:
            continue
        entry_i = i + costs.latency_quotes
        if entry_i >= n:
            break
        entry = q[entry_i]
        if entry.ts - signal.ts > 180 or entry.ts >= event.start+900-costs.cutoff_seconds:
            continue
        px = entry.ask(side)
        if (entry.spread > costs.max_spread or
            not costs.price_floor <= px <= costs.price_ceiling or
            abs(px-signal.ask(side)) > costs.max_signal_fill_move):
            continue
        entry_fee = fee(px, event.fee, costs)
        # Entry cannot cost more than the stake cap including fees and slippage.
        qty = math.floor((costs.max_stake/(px+entry_fee+costs.per_share_slippage))*100)/100
        if qty <= 0:
            continue
        final_i = None
        why = "SETTLEMENT_FALLBACK"
        if spec.mode == "SCALP":
            for j in range(entry_i+1, n):
                observed = q[j]
                if observed.ts >= event.start+900:
                    break
                sell = observed.bid(side)
                potential = sell-px-entry_fee-fee(sell,event.fee,costs)-2*costs.per_share_slippage
                holding = observed.ts-entry.ts
                # Signal at observed quote; the earliest fill is a later quote.
                if (potential >= spec.target or potential <= -spec.stop or
                    holding >= spec.hold_s or
                    observed.ts >= event.start+900-180):
                    candidate_exit = j+costs.latency_quotes
                    if (candidate_exit < n and
                        q[candidate_exit].ts < event.start+900 and
                        q[candidate_exit].ts-observed.ts <= 180):
                        final_i = candidate_exit
                        why = ("TARGET" if potential >= spec.target else
                               "STOP" if potential <= -spec.stop else "TIME")
                    break
        if final_i is None:
            exit_px = 1.0 if (side == "UP") == (event.settlement == 1.0) else 0.0
            exit_ts = event.start+900
            exit_fee = 0.0
            # No fictitious terminal quote, zero extra slippage on cash settlement.
            slide = costs.per_share_slippage
        else:
            exit_px = q[final_i].bid(side)
            exit_ts = q[final_i].ts
            exit_fee = fee(exit_px,event.fee,costs)
            slide = 2*costs.per_share_slippage
        fees = qty*(entry_fee+exit_fee)
        slippage = qty*slide
        pnl = qty*(exit_px-px)-fees-slippage
        return Trade(event.start,event.slug,side,spec.family,entry.ts,exit_ts,
                     px,exit_px,qty,fees,slippage,pnl,why,event.settlement)
    return None


def simulate(events, spec, costs=Cost(), max_daily_loss=5.0):
    trades=[]
    day = None
    day_pnl = 0.0
    for event in sorted(events, key=lambda e:e.start):
        event_day = datetime.fromtimestamp(event.start,timezone.utc).strftime("%Y-%m-%d")
        if event_day != day:
            day = event_day
            day_pnl = 0.0
        if day_pnl <= -max_daily_loss:
            continue
        trade = evaluate_trade(event,spec,costs)
        if trade is None:
            continue
        trades.append(trade)
        day_pnl += trade.pnl
    return trades


def performance(trades, events_total):
    profits = [t.pnl for t in trades]
    realized = sum(profits)
    equity=0.0
    peak=0.0
    drawdown=0.0
    for profit in profits:
        equity += profit
        peak = max(peak,equity)
        drawdown = max(drawdown,peak-equity)
    count=len(trades)
    win=sum(p > 0 for p in profits)
    daily = {}
    for tr in trades:
        date = datetime.fromtimestamp(tr.event_start,timezone.utc).strftime("%Y-%m-%d")
        daily[date] = daily.get(date,0.0)+tr.pnl
    return {
        "trades":count, "markets":events_total,
        "coverage":round(count/events_total,4) if events_total else 0,
        "conditional_quote_pnl":round(realized,4),
        "mean_pnl":round(realized/count,6) if count else None,
        "win_fraction":round(win/count,4) if count else None,
        "max_drawdown":round(drawdown,4),
        "settlement_fallbacks":sum(t.exit_mode=="SETTLEMENT_FALLBACK" for t in trades),
        "exits_before_end":sum(t.exit_ts<t.event_start+900 for t in trades),
        "daily_pnl":daily,
        "label":"MODELED_DISPLAY_QUOTE_PNL_NOT_EXECUTED",
    }


def bootstrap_mean_ci(trades, draws=1500, seed=18015):
    """Resamples whole UTC market-days, not correlated within-day trades."""
    if not trades:
        return None
    days={}
    for t in trades:
        key=datetime.fromtimestamp(t.event_start,timezone.utc).strftime("%Y-%m-%d")
        days.setdefault(key,[]).append(t.pnl)
    groups=list(days.values())
    if len(groups)<3:
        return None
    rng=random.Random(seed)
    sims=[]
    for _ in range(draws):
        sample=[groups[rng.randrange(len(groups))] for _ in groups]
        p=[x for day in sample for x in day]
        sims.append(sum(p)/len(p))
    sims.sort()
    return [round(sims[int(.025*draws)],6),round(sims[int(.975*draws)],6)]
