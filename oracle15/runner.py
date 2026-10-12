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
        # Reconcile *all* pre-registered 2m/5m/8m predictions, including
        # windows where the risk engine deliberately made no trade.
        for old in self.ledger.forecast_outcome_backlog(now):
            try:
                outcome=self.client.settlement(old["market_slug"])
                if outcome in (0.,1.):
                    if self.ledger.record_forecast_outcome(old["event_slug"],
                                                         old["market_slug"],outcome,now):
                        self.ledger.decision(now,old["event_slug"],"FORECAST_OUTCOME",
                            json.dumps({"yes_settlement":outcome,
                                       "source":"POLYMARKET_US_OFFICIAL_SETTLEMENT"}))
            except MarketDataError:
                continue  # Unsettled markets remain pending; never guess.
        # Scalp research closes are separately labeled simulated fill-to-bid.
        # A stop file prevents new entries, not risk-reducing paper exits.
        scalp_actions = self._check_open_scalps(now)
        if (self.state_dir / "KILL").exists():
            reason = "kill_switch"
            self.ledger.decision(now, slug, reason)
            return self._status(reason, slug, verified, scalp_actions=scalp_actions)
        try:
            window = self.client.window(now)
            if now < window.start + self.settings.min_seconds_after_start:
                raise ValueError("too_early_in_window")
            if now > window.end - self.settings.min_seconds_before_end:
                raise ValueError("too_close_to_expiry")
            book = self.client.book(window)
            candles = self.client.candles(now)
            forecast = forecast_up(window, candles, now)
            if self.ledger.record_forecast(window,forecast,book,now):
                self.ledger.decision(now,slug,"FORECAST_PRE_REGISTERED",
                    json.dumps({"p_up":forecast.p_up,"model":forecast.model_version,
                                "asof":"FORWARD_ONLY"}))
            if self.ledger.exists(slug):
                raise ValueError("already_traded_window")
            stats=self.ledger.stats(self.settings,now)
            remaining=max(0.0,self.settings.max_daily_loss_usd-
                      max(0.0,-stats["realized_today"])-stats["open_exposure"])
            paper_budget=min(self.settings.max_ticket_usd,stats["available"],
                        self.settings.max_open_exposure_usd-stats["open_exposure"],remaining)
            if (stats["trades_today"]>=self.settings.max_trades_per_day or
                paper_budget<self.settings.min_paper_risk_budget):
                prior_intent=self.ledger.entry_intent(slug)
                if prior_intent and prior_intent["state"]=="SIGNALLED":
                    self.ledger.finish_entry_intent(slug,"REJECTED","paper_risk_budget_exhausted")
                raise ValueError("paper_risk_budget_exhausted")
            choice, reason = candidate(window, book, forecast, self.settings)
            context = {
                "p_up": round(forecast.p_up, 4),
                "index": round(forecast.latest_index, 2),
                "price_to_beat": round(window.target, 2),
                "book_bid": book.bid,
                "book_ask": book.ask,
                "model": forecast.model_version,
            }
            intent=self.ledger.entry_intent(slug)
            if intent is not None:
                if intent["state"]!="SIGNALLED":
                    raise ValueError("entry_intent_terminal_"+intent["state"])
                elapsed=int(now)-intent["signaled_at"]
                if elapsed < self.settings.paper_entry_confirmation_s:
                    raise ValueError("entry_pending_independent_quote")
                if elapsed > self.settings.paper_entry_max_delay_s:
                    self.ledger.finish_entry_intent(slug,"REJECTED","signal_quote_expired")
                    raise ValueError("entry_quote_confirmation_expired")
                if (choice is None or choice.side!=intent["side"] or
                    choice.ask_equivalent-intent["signal_ask"] >
                    self.settings.paper_entry_max_adverse_move):
                    self.ledger.finish_entry_intent(slug,"REJECTED",
                                                    "signal_no_longer_valid")
                    raise ValueError("entry_rejected_on_next_quote")
                context["intent_signal_at"]=intent["signaled_at"]
                context["signal_ask"]=intent["signal_ask"]
            elif choice is None:
                self.ledger.decision(now, slug, reason, json.dumps(context))
                return self._status(reason, slug, verified, context, scalp_actions=scalp_actions)
            else:
                if self.ledger.signal_entry(window,choice,forecast,now):
                    context.update({"side":choice.side,
                                    "signal_ask":choice.ask_equivalent,
                                    "mode":"PAPER_ENTRY_INTENT_NOT_FILLED"})
                    self.ledger.decision(now,slug,"PAPER_ENTRY_SIGNAL",json.dumps(context))
                    return self._status("PAPER_ENTRY_SIGNAL",slug,verified,context,
                                        scalp_actions=scalp_actions)
                raise ValueError("duplicate_entry_intent")
            # The second, independently fetched quote validates a paper-only entry.
            # Conservatively charge a full taker fee and $0.01/share of slippage.
            price_cost = (choice.ask_equivalent +
                          taker_fee_per_share(choice.ask_equivalent, window.fee_coeff) +
                          self.settings.slippage_usd_per_share)
            if not 0 < window.min_qty <= 1 or not 0 < window.tick <= 0.05:
                raise ValueError("unsupported_market_lot_or_tick")
            max_qty = min(paper_budget / price_cost, choice.price_depth * 0.20)
            qty = math.floor(max_qty / window.min_qty + 1e-9) * window.min_qty
            qty = round(qty, 6)
            if qty < window.min_qty:
                raise ValueError("insufficient_top_of_book_depth")
            ok, reason = self.ledger.enter(window, choice, qty, self.settings, now)
            self.ledger.finish_entry_intent(slug,"PAPER_FILLED" if ok else "REJECTED",
                                             None if ok else reason)
            context.update({
                "side": choice.side, "quantity": qty,
                "price": round(choice.ask_equivalent, 4),
                "net_edge": round(choice.expected_net_edge, 4),
                "venue": "POLYMARKET_US", "execution": "SIMULATED_DELAYED_DISPLAY_QUOTE",
            })
            self.ledger.decision(now, slug, reason, json.dumps(context))
            return self._status(reason, slug, verified, context, scalp_actions=scalp_actions)
        except (MarketDataError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
            # Fail closed: data errors cannot cause trading.
            reason = str(exc).replace("\n", " ")[:160]
            self.ledger.decision(now, slug, "skip", reason)
            return self._status("skip: " + reason, slug, verified, scalp_actions=scalp_actions)

    def _check_open_scalps(self, now):
        if not self.settings.paper_scalp_enabled:
            return []
        open_positions=self.ledger.active_positions()
        if not open_positions:
            return []
        # Never use a different window's quotes to mark/exit an old position.
        try:
            current=self.client.window(now)
            book=self.client.book(current)
        except (MarketDataError, KeyError, TypeError, ValueError):
            return []
        actions=[]
        for pos in open_positions:
            if pos["market_slug"]!=current.market_slug:
                continue
            if now>=current.end-self.settings.paper_scalp_min_exit_buffer_s:
                continue
            bid=book.bid if pos["side"]=="UP" else 1.0-book.ask
            available_depth=book.bid_qty if pos["side"]=="UP" else book.ask_qty
            if not 0<bid<1 or pos["qty"]>available_depth*.20:
                continue  # Quote exists; available size is not proven executable.
            fee_exit=taker_fee_per_share(bid,current.fee_coeff)
            net_share=(bid-pos["price"]-pos["fee"]/pos["qty"]-
                       pos["slippage"]/pos["qty"]-fee_exit-
                       self.settings.slippage_usd_per_share)
            prior=self.ledger.exit_intent(pos["id"])
            if prior is not None:
                ok=self.ledger.paper_exit(pos["id"],now,bid,current.fee_coeff,
                    self.settings.slippage_usd_per_share,
                    self.settings.paper_scalp_quote_lag_s,
                    self.settings.paper_scalp_min_exit_buffer_s)
                if ok:
                    self.ledger.decision(now,pos["event_slug"],"PAPER_SCALP_EXIT",
                                         json.dumps({"bid":bid,"side":pos["side"],
                                           "reason":prior["reason"],"execution":"MODELED_BID_ONLY"}))
                    actions.append({"event":pos["event_slug"],
                                    "modeled_exit_bid":bid,"type":"PAPER_SCALP_EXIT"})
                continue
            reason=None
            if net_share>=self.settings.paper_scalp_profit_per_share:
                reason="TARGET"
            elif net_share<=-self.settings.paper_scalp_stop_per_share:
                reason="STOP"
            elif now-pos["opened_at"]>=self.settings.paper_scalp_max_hold_s:
                reason="MAX_HOLD"
            elif now>=current.end-180:
                reason="EXPIRY_EXIT"
            if reason and now<current.end-(self.settings.paper_scalp_min_exit_buffer_s+
                                            self.settings.paper_scalp_quote_lag_s):
                if self.ledger.signal_exit(pos["id"],now,bid,net_share,reason):
                    self.ledger.decision(now,pos["event_slug"],"PAPER_EXIT_SIGNAL",
                                         json.dumps({"bid":bid,"net_share":round(net_share,4),
                                                     "reason":reason}))
                    actions.append({"event":pos["event_slug"],"type":"PAPER_EXIT_SIGNAL"})
        return actions

    def _status(self, reason, slug, verified, data=None, scalp_actions=None):
        out = {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": "PAPER_ONLY",
            "event": slug, "status": reason,
            "resolved": verified,
            "paper_scalp_actions": scalp_actions or [],
            "paper": self.ledger.stats(self.settings),
            "learning": self.ledger.calibration(),
        }
        if data is not None:
            out["data"] = data
        return out

    def close(self):
        self.ledger.close()
