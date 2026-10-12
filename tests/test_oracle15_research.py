"""Adversarial offline replay checks: chronology, fill uncertainty, risk limits."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from oracle15.market import Book, Candle, Window, event_slug
from oracle15.model import Candidate, Settings, Forecast
from oracle15.runner import Runner
from oracle15.ledger import Ledger
from oracle15.replay import Quote, Event, Spec, Cost, evaluate_trade, performance
from oracle15.archive import USClient, DataUnavailable, FEE_CHANGE_UTC
from oracle15.tournament import catalog

START=1791768600  # UTC quarter-hour, sample deterministic interval
WIN=Window(event_slug(START),"cpc-"+event_slug(START),START,START+900,83500,0.01,0.01,0.0695)


class LocalUS:
    def __init__(self, bid=.61, ask=.62):
        self.bid=bid;self.ask=ask

    def window(self,now):
        return WIN

    def book(self,window):
        return Book(self.bid,self.ask,10000,10000)

    def candles(self,now):
        return [Candle(int(now)-(60-i)*60,83500+i*.3,60) for i in range(60)]

    def settlement(self, market_slug):
        return None


class PaperScalpSafety(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.runner=Runner(Path(self.tmp.name),client=LocalUS(),settings=Settings())
        original=Candidate("UP",.45,.45,.20,100)
        self.assertTrue(self.runner.ledger.enter(
            WIN,original,4,self.runner.settings,START+200)[0])

    def tearDown(self):
        self.runner.close()
        self.tmp.cleanup()

    def row(self):
        return self.runner.ledger.active_positions()

    def test_signal_fills_only_on_later_observation(self):
        res=self.runner.tick(START+410)
        self.assertEqual(res["paper_scalp_actions"][0]["type"],"PAPER_EXIT_SIGNAL")
        self.assertEqual(len(self.row()),1)
        pos=self.row()[0]
        self.assertFalse(self.runner.ledger.paper_exit(
            pos["id"],START+415,.61,.0695,.01))
        self.runner.client.bid=.59
        res=self.runner.tick(START+440)
        self.assertEqual(res["paper_scalp_actions"][0]["type"],"PAPER_SCALP_EXIT")
        self.assertEqual(len(self.row()),0)
        row=self.runner.ledger.db.execute("SELECT * FROM paper_positions").fetchone()
        self.assertEqual(row["state"],"EARLY_EXIT")
        self.assertEqual(row["exit_bid"],.59)
        self.assertGreater(row["pnl"],0)
        self.assertTrue(self.runner.ledger.stats(self.runner.settings,START+441)["pnl_truth"].startswith("PAPER"))
        self.assertEqual(self.runner.tick(START+470)["paper_scalp_actions"],[])

    def test_stop_loss_is_not_guaranteed_fill(self):
        self.runner.client.bid=.25;self.runner.client.ask=.26
        self.assertEqual(self.runner.tick(START+410)["paper_scalp_actions"][0]["type"],"PAPER_EXIT_SIGNAL")
        self.runner.client.bid=.16;self.runner.client.ask=.17
        result=self.runner.tick(START+442)
        self.assertEqual(result["paper_scalp_actions"][0]["type"],"PAPER_SCALP_EXIT")
        state=self.runner.ledger.db.execute("SELECT exit_reason,pnl FROM paper_positions").fetchone()
        self.assertEqual(state["exit_reason"],"STOP")
        self.assertLess(state["pnl"],0)

    def test_kill_file_blocks_entries_not_existing_exit(self):
        (Path(self.tmp.name)/"KILL").touch()
        self.runner.tick(START+411)
        self.runner.client.bid=.60
        self.runner.tick(START+440)
        row=self.runner.ledger.db.execute("SELECT state FROM paper_positions").fetchone()
        self.assertEqual(row["state"],"EARLY_EXIT")
        self.assertEqual(self.runner.tick(START+441)["status"],"kill_switch")

    def test_window_buffer_prevents_last_second_quote_fill(self):
        result=self.runner.tick(START+900-70)
        self.assertEqual(result["paper_scalp_actions"],[])
        self.assertEqual(len(self.row()),1)

    def test_migration_preserves_prior_ledger(self):
        p=Path(self.tmp.name)/"paper.sqlite3"
        self.runner.close()
        db=Ledger(p)
        try:
            cols={x[1] for x in db.db.execute("PRAGMA table_info(paper_positions)")}
            self.assertTrue({"exit_bid","exit_at","exit_fee","exit_slippage"}.issubset(cols))
            self.assertEqual(db.db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0],1)
        finally:
            db.close()
        self.runner=Runner(Path(self.tmp.name),client=LocalUS())


def mock_event_quotes():
    # At 120s the model sees a breakout but at 180s the price worsens.
    # It must pay that next snapshot, not the 120s signal price.
    mids=[.50,.50,.45,.50,.55,.65,.75,.64,.61,.55,.50,.54,.56,.52]
    quotes=[]
    for i,x in enumerate(mids):
        quotes.append(Quote(START+i*60,x+.01,1-x+.01,x-.01,1-x-.01,.02))
    return Event(START,"btc-updown-15m-unit",.0695,0.0,tuple(quotes),"CURRENT_DOC")


class PaperEntryConfirmation(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.client=LocalUS(bid=.45,ask=.46)
        self.runner=Runner(Path(self.tmp.name),client=self.client,settings=Settings())
        self.pick=Candidate("UP",.46,.46,.20,10000)
        self.forecast=Forecast(.69,.001,83500,59,"entry-fixture")
        self.choice_patch=patch("oracle15.runner.candidate",return_value=(self.pick,"candidate"))
        self.model_patch=patch("oracle15.runner.forecast_up",return_value=self.forecast)
        self.choice_patch.start();self.model_patch.start()

    def tearDown(self):
        self.choice_patch.stop();self.model_patch.stop()
        self.runner.close();self.tmp.cleanup()

    def test_no_same_quote_fill_even_when_edge_positive(self):
        first=self.runner.tick(START+250)
        self.assertEqual(first["status"],"PAPER_ENTRY_SIGNAL")
        self.assertEqual(len(self.runner.ledger.active_positions()),0)
        intent=self.runner.ledger.entry_intent(WIN.event_slug)
        self.assertEqual(intent["state"],"SIGNALLED")
        self.assertEqual(self.runner.tick(START+260)["status"],
                         "skip: entry_pending_independent_quote")
        self.client.ask=.47
        second=self.runner.tick(START+282)
        self.assertEqual(second["status"],"paper_entered")
        positions=self.runner.ledger.active_positions()
        self.assertEqual(len(positions),1)
        self.assertAlmostEqual(positions[0]["price"],.46)
        self.assertEqual(self.runner.ledger.entry_intent(WIN.event_slug)["state"],"PAPER_FILLED")

    def test_signal_declines_if_adverse_price_moves_too_far(self):
        self.assertEqual(self.runner.tick(START+250)["status"],"PAPER_ENTRY_SIGNAL")
        moved=Candidate("UP",.52,.52,.20,10000)
        with patch("oracle15.runner.candidate",return_value=(moved,"candidate")):
            result=self.runner.tick(START+282)
        self.assertEqual(result["status"],"skip: entry_rejected_on_next_quote")
        self.assertEqual(self.runner.ledger.entry_intent(WIN.event_slug)["state"],"REJECTED")
        self.assertEqual(len(self.runner.ledger.active_positions()),0)

    def test_pending_entry_never_spends_after_emergency_stop(self):
        self.assertEqual(self.runner.tick(START+250)["status"],"PAPER_ENTRY_SIGNAL")
        (Path(self.tmp.name)/"KILL").touch()
        self.assertEqual(self.runner.tick(START+285)["status"],"kill_switch")
        self.assertEqual(len(self.runner.ledger.active_positions()),0)


class ReplayIntegrity(unittest.TestCase):
    def test_one_quote_lag_and_bid_side_exit(self):
        event=mock_event_quotes()
        spec=Spec("quote_momentum",.03,.05,.12,180,"SCALP")
        result=evaluate_trade(event,spec,Cost())
        self.assertIsNotNone(result)
        self.assertGreater(result.entry_ts,event.start+120)
        self.assertGreater(result.exit_ts,result.entry_ts)
        self.assertEqual(result.family,"quote_momentum")
        self.assertNotEqual(result.exit_px,result.entry_px)

    def test_rejects_unaffordable_late_quote(self):
        event=mock_event_quotes()
        cost=Cost(price_floor=.60,price_ceiling=.60)
        result=evaluate_trade(event,Spec("always_up",0,0,0,900,"SETTLE"),cost)
        self.assertIsNone(result)

    def test_settlement_only_if_position_survives(self):
        event=mock_event_quotes()
        result=evaluate_trade(event,Spec("always_down",0,0,0,900,"SETTLE"),Cost())
        self.assertIsNotNone(result)
        self.assertEqual(result.exit_mode,"SETTLEMENT_FALLBACK")
        self.assertEqual(result.exit_ts,event.start+900)
        self.assertEqual(result.yes_settlement,0.0)

    def test_192_precommitted_candidates_and_declared_costs(self):
        universe=catalog()
        self.assertEqual(len(universe),192)
        self.assertEqual(len({x.name for x in universe}),192)
        self.assertEqual(Cost().latency_quotes,1)
        self.assertEqual(Cost().fee_multiplier,1)

    def test_always_record_quote_simulated_not_live(self):
        result=performance([],10)
        self.assertEqual(result["label"],"MODELED_DISPLAY_QUOTE_PNL_NOT_EXECUTED")


class ForwardTruthIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.ledger=Ledger(Path(self.tmp.name)/"empirical.sqlite3")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_forecast_is_immutable_and_later_settlement_calibrates(self):
        forecast=Forecast(.70,.002,83510,59,"preregistered-test")
        book=Book(.48,.50,100,100)
        self.assertTrue(self.ledger.record_forecast(WIN,forecast,book,START+145))
        self.assertFalse(self.ledger.record_forecast(
            WIN,Forecast(.99,.001,83600,59,"posthoc-bias"),book,START+155))
        status=self.ledger.calibration()
        self.assertEqual(status["registered_forecasts"],1)
        self.assertEqual(status["resolved_events"],0)
        self.assertEqual(status["calibrations"],{})
        self.assertEqual(len(self.ledger.forecast_outcome_backlog(START+800)),0)
        backlog=self.ledger.forecast_outcome_backlog(START+1100)
        self.assertEqual(len(backlog),1)
        self.assertTrue(self.ledger.record_forecast_outcome(
            WIN.event_slug,WIN.market_slug,1.0,START+1100))
        self.assertFalse(self.ledger.record_forecast_outcome(
            WIN.event_slug,WIN.market_slug,1.0,START+1101))
        with self.assertRaises(RuntimeError):
            self.ledger.record_forecast_outcome(
                WIN.event_slug,WIN.market_slug,0.0,START+1102)
        status=self.ledger.calibration()
        self.assertEqual(status["resolved_events"],1)
        self.assertAlmostEqual(status["calibrations"]["2m"]["brier"],.09)
        self.assertGreater(status["calibrations"]["2m"]["market_mid_baseline_brier"],.25)
        self.assertEqual(status["calibrations"]["2m"]["maturity"],"INSUFFICIENT_FOR_PROMOTION")

    def test_separate_2m_5m_8m_horizons(self):
        f=Forecast(.6,.002,83510,59,"horizon-test")
        book=Book(.48,.50,100,100)
        for sec in (145,330,510):
            self.assertTrue(self.ledger.record_forecast(WIN,f,book,START+sec))
        self.assertEqual(self.ledger.calibration()["registered_forecasts"],3)
        self.assertEqual({x[0] for x in self.ledger.db.execute(
            "SELECT horizon_label FROM forecast_registry")},{"2m","5m","8m"})

    def test_no_unsafe_quotes_can_be_preregistered(self):
        f=Forecast(.6,.002,83510,59,"bad-quote-test")
        self.assertFalse(self.ledger.record_forecast(WIN,f,Book(.5,.5,100,100),START+145))
        self.assertFalse(self.ledger.record_forecast(WIN,f,Book(.6,.4,100,100),START+145))
        self.assertEqual(self.ledger.calibration()["registered_forecasts"],0)


class ArchiveTimeIntegrity(unittest.TestCase):
    def test_archive_discards_post_expiration_quote(self):
        from oracle15.archive import USClient
        from oracle15.archive import slug_at
        class FixtureUS(USClient):
            def __init__(self):
                pass
            def get(self,path,params=None):
                if path.startswith("/v1/events/slug/"):
                    return {"event":{"slug":slug_at(START),"markets":[{
                        "slug":"cpc-"+slug_at(START),
                        "feeCoefficient":.0695,
                        "assetPriceTerms":{
                            "windowStart":"2026-10-12T01:30:00Z",
                            "windowEnd":"2026-10-12T01:45:00Z",
                            "indexSymbol":"BRTI",
                            "asset":{"symbol":"btc"},
                            "marketType":"ASSET_PRICE_MARKET_TYPE_UP_DOWN",
                            "priceToBeat":{"value":"83500"}}
                    }]}}
                if path == "/v1/price-history":
                    return {"history":[
                        {"timestamp":START+120,"longPrice":.49,"shortPrice":.52},
                        {"timestamp":START+540,"longPrice":.58,"shortPrice":.43},
                        {"timestamp":START+900,"longPrice":1.0,"shortPrice":0.0},
                        {"timestamp":START+960,"longPrice":1.0,"shortPrice":0.0}]}
                if path.endswith("/settlement"):
                    return {"settlement":1}
                raise RuntimeError("unexpected request")
        # Fixture uses 2026-10-12T01:30:00Z as START.
        from oracle15.market import timestamp
        self.assertEqual(START,timestamp("2026-10-12T01:30:00Z"))
        event=FixtureUS().window(START)
        self.assertEqual(len(event["quotes"]),2)
        self.assertTrue(all(q[0] < START+900 for q in event["quotes"]))
        self.assertEqual(event["settlement"],1.0)


class ResearchScoreboardSafety(unittest.TestCase):
    def test_no_data_is_not_invented_or_promoted(self):
        from oracle15.report import make
        with TemporaryDirectory() as root:
            result=make(Path(root))
            self.assertFalse(result["polymarket_us_archive"]["available"])
            self.assertFalse(result["binance_btc_archive"]["available"])
            self.assertFalse(result["forward_paper"]["available"])
            self.assertEqual(result["promotion_gate"]["status"],"LOCKED")
            self.assertFalse(result["promotion_gate"]["live_trading_allowed"])
            self.assertEqual(result["execution_mode"],"PAPER_ONLY_NO_BROKER_CREDENTIALS")

    def test_paper_loss_reported_as_paper_only(self):
        from oracle15.report import make
        with TemporaryDirectory() as root:
            target=Path(root)
            db=Ledger(target/"paper.sqlite3")
            try:
                c=Candidate("UP",.45,.45,.16,100)
                self.assertTrue(db.enter(WIN,c,4,Settings(),START+250)[0])
                pos=db.pending(START+1500)[0]
                self.assertTrue(db.settle(pos["id"],0.0,START+1600))
            finally:
                db.close()
            result=make(target)
            self.assertLess(result["forward_paper"]["paper_modeled_total_pnl"],0)
            self.assertEqual(result["forward_paper"]["capital_proof"],"NONE")
            self.assertFalse(result["promotion_gate"]["live_trading_allowed"])


class ArchiveIntegrity(unittest.TestCase):
    def test_documented_fee_start(self):
        self.assertEqual(FEE_CHANGE_UTC,1791381600)

    def test_us_archive_excludes_closing_quote(self):
        start=START
        raw={"event":{"slug":"btc-updown-15m-2026-10-12-0130z"}}
        self.assertEqual(Cost().max_stake,2.5)


if __name__=="__main__":
    unittest.main()
