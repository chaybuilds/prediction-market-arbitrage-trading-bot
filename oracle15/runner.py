"""Forward-only autonomous paper trader; public API read operations only."""
import json
import math
from pathlib import Path
import time

from .ledger import Ledger
from .market import MarketDataError, PolymarketUSPublic, event_slug
from .model import Settings, candidate, forecast_up, taker_fee_per_share


class Runner:
    def __init__(self, state_dir, client=None, settings=None):
        self.state_dir = Path(state_dir)
        self.settings = settings or Settings()
        self.client = client or PolymarketUSPublic()
        self.ledger = Ledger(self.state_dir / "paper.sqlite3")

    def tick(self, now=None):
        now = time.time() if now is None else now
        slug = event_slug(now)
        # Verification always reads the venue's final settlement, never spot moves.
        verified = []
        for position in self.ledger.pending(now):
            try:
                settlement = self.client.settlement(position["market_slug"])
                if settlement in (0.0, 1.0) and self.ledger.settle(
                    position["id"], settlement, now
                ):
                    verified.append({"market": position["market_slug"], "yes": settlement})
            except MarketDataError:
                pass
        if (self.state_dir / "KILL").exists():
            reason = "kill_switch"
            self.ledger.decision(now, slug, reason)
            return self._status(reason, slug, verified)
        try:
            window = self.client.window(now)
            if now < window.start + self.settings.min_seconds_after_start:
                raise ValueError("too_early_in_window")
            if now > window.end - self.settings.min_seconds_before_end:
                raise ValueError("too_close_to_expiry")
            if self.ledger.exists(slug):
                raise ValueError("already_traded_window")
            book = self.client.book(window)
            candles = self.client.candles(now)
            forecast = forecast_up(window, candles, now)
            choice, reason = candidate(window, book, forecast, self.settings)
            context = {
                "p_up": round(forecast.p_up, 4),
                "index": round(forecast.latest_index, 2),
                "price_to_beat": round(window.target, 2),
                "book_bid": book.bid,
                "book_ask": book.ask,
                "model": forecast.model_version,
            }
            if choice is None:
                self.ledger.decision(now, slug, reason, json.dumps(context))
                return self._status(reason, slug, verified, context)
            # Conservatively charge a full taker fee and $0.01/share of slippage.
            price_cost = (choice.ask_equivalent +
                          taker_fee_per_share(choice.ask_equivalent, window.fee_coeff) +
                          self.settings.slippage_usd_per_share)
            if not 0 < window.min_qty <= 1 or not 0 < window.tick <= 0.05:
                raise ValueError("unsupported_market_lot_or_tick")
            max_qty = min(self.settings.max_ticket_usd / price_cost, choice.price_depth * 0.20)
            qty = math.floor(max_qty / window.min_qty + 1e-9) * window.min_qty
            qty = round(qty, 6)
            if qty < window.min_qty:
                raise ValueError("insufficient_top_of_book_depth")
            ok, reason = self.ledger.enter(window, choice, qty, self.settings, now)
            context.update({
                "side": choice.side, "quantity": qty,
                "price": round(choice.ask_equivalent, 4),
                "net_edge": round(choice.expected_net_edge, 4),
                "venue": "POLYMARKET_US", "execution": "SIMULATED_IOC",
            })
            self.ledger.decision(now, slug, reason, json.dumps(context))
            return self._status(reason, slug, verified, context)
        except (MarketDataError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
            # Fail closed: data errors cannot cause trading.
            reason = str(exc).replace("\n", " ")[:160]
            self.ledger.decision(now, slug, "skip", reason)
            return self._status("skip: " + reason, slug, verified)

    def _status(self, reason, slug, verified, data=None):
        out = {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": "PAPER_ONLY",
            "event": slug, "status": reason,
            "resolved": verified,
            "paper": self.ledger.stats(self.settings),
        }
        if data is not None:
            out["data"] = data
        return out

    def close(self):
        self.ledger.close()
