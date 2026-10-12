"""Offline deterministic checks for the Polymarket US read-only paper lane."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from oracle15.market import (
    Book, Candle, MarketDataError, PolymarketUSPublic, Window, event_slug, timestamp
)
from oracle15.model import (
    Candidate, Forecast, Settings, candidate, forecast_up, taker_fee_per_share
)
from oracle15.ledger import Ledger
from oracle15.runner import Runner

START = timestamp("2026-10-12T01:30:00Z")
SLUG = "btc-updown-15m-2026-10-12-0130z"
MSLUG = "cpc-" + SLUG


def fixture_event(index="BRTI", market_end="2026-10-12T02:15:00Z"):
    return {"event": {
        "slug": SLUG, "active": True, "closed": False,
        "markets": [{
            "slug": MSLUG, "active": True, "closed": False,
            "status": "MARKET_STATUS_OPEN", "endDate": market_end,
            "orderPriceMinTickSize": 0.01, "minimumTradeQty": 0.01,
            "feeCoefficient": 0.0695,
            "marketSides": [{"long": True}, {"long": False}],
            "assetPriceTerms": {
                "marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
                "asset": {"symbol": "btc"}, "indexSymbol": index,
                "horizon": "15m", "windowStart": "2026-10-12T01:30:00Z",
                "windowEnd": "2026-10-12T01:45:00Z",
                "priceToBeat": {"value": "83695.59"}
            }
        }]
    }}


def fixture_candles(now, drifting=1):
    return [Candle(now - 61*60 + i*60, 83695 + drifting * i * 3, 60)
            for i in range(60)]


class FakeUS(PolymarketUSPublic):
    def __init__(self, event=None, bid=0.42, ask=0.44, stale=False):
        self.event = fixture_event() if event is None else event
        self.bid = bid
        self.ask = ask
        self.stale = stale
        self.calls = []

    def _get(self, path, params=None):
        self.calls.append(path)
        if path.startswith("/v1/events/slug/"):
            return self.event
        if path.endswith("/book"):
            return {"marketData": {
                "marketSlug": MSLUG, "state": "MARKET_STATE_OPEN",
                "bids": [{"px": {"value": str(self.bid)}, "qty": "100"}],
                "offers": [{"px": {"value": str(self.ask)}, "qty": "100"}]
            }}
        if path == "/v1/asset-prices/history":
            now = int(params["to"])
            drift = 500 if self.stale else 0
            return {"series": [{"asset": {"symbol": "btc"},
                                "bucket": "ASSET_PRICE_BUCKET_1M",
                                "candles": [
                                    {"timestamp": now - 61*60 + i*60 - drift,
                                     "close": str(83695 + 0.3*i),
                                     "tickCount": 60} for i in range(60)
                                ]}]}
        if path.endswith("/settlement"):
            return {"settlement": 1.0}
        raise RuntimeError("unexpected fixture path: " + path)


class TestUSFeed(unittest.TestCase):
    def test_slug_alignment(self):
        self.assertEqual(event_slug(START+899), SLUG)
        self.assertNotEqual(event_slug(START+900), SLUG)

    def test_window_uses_asset_window_end_not_instrument_expiration(self):
        win = FakeUS().window(START+190)
        self.assertEqual(win.end, START+900)
        self.assertEqual(win.target, 83695.59)
        self.assertEqual(win.fee_coeff, 0.0695)
        self.assertEqual(win.market_slug, MSLUG)

    def test_rejects_other_index_and_wrong_window(self):
        with self.assertRaises(MarketDataError):
            FakeUS(fixture_event(index="Chainlink")).window(START+190)
        with self.assertRaises(MarketDataError):
            FakeUS().window(START+901)

    def test_book_prices_and_crossed_book(self):
        win = FakeUS().window(START+190)
        book = FakeUS().book(win)
        self.assertEqual((book.bid, book.ask), (0.42, 0.44))
        with self.assertRaises(MarketDataError):
            FakeUS(bid=0.60, ask=0.50).book(win)

    def test_rejects_stale_reference_history(self):
        client = FakeUS(stale=True)
        with self.assertRaises(MarketDataError):
            client.candles(START+420)


class TestModel(unittest.TestCase):
    def setUp(self):
        self.window = Window(SLUG, MSLUG, START, START+900, 83695.59, 0.01, 0.01, 0.0695)

    def test_fee_exactness(self):
        self.assertAlmostEqual(taker_fee_per_share(0.5, 0.0695), 0.017375)

    def test_forecast_is_bounded_and_forward_only(self):
        candles = fixture_candles(START+600)
        result = forecast_up(self.window, candles, START+600)
        self.assertGreater(result.p_up, 0.5)
        self.assertLess(result.p_up, 1.0)
        with self.assertRaises(ValueError):
            forecast_up(self.window, candles, START+901)

    def test_candidate_both_directions(self):
        book = Book(0.42, 0.44, 100, 100)
        settings = Settings(min_edge=0.065)
        choice, reason = candidate(self.window, book, Forecast(0.82, 0.001, 84000, 59), settings)
        self.assertEqual((choice.side, reason), ("UP", "candidate"))
        choice, _ = candidate(self.window, book, Forecast(0.04, 0.001, 83500, 59), settings)
        self.assertEqual(choice.side, "DOWN")
        self.assertAlmostEqual(choice.ask_equivalent, 0.58)

    def test_skip_spread_and_no_edge(self):
        f = Forecast(0.5, 0.001, 83695, 59)
        self.assertIsNone(candidate(self.window, Book(0.2, 0.8, 100, 100), f, Settings())[0])
        self.assertIsNone(candidate(self.window, Book(0.48, 0.50, 100, 100), f, Settings())[0])


class TestLedgerAndRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.window = Window(SLUG, MSLUG, START, START+900, 83695.59, 0.01, 0.01, 0.0695)
        self.choice = Candidate("UP", 0.45, 0.45, 0.2, 100)
        self.settings = Settings()
        self.ledger = Ledger(self.path / "ledger.sqlite3")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_atomic_one_trade_per_window_and_exact_settlement(self):
        ok, reason = self.ledger.enter(self.window, self.choice, 4, self.settings, START+200)
        self.assertTrue(ok, reason)
        ok, reason = self.ledger.enter(self.window, self.choice, 4, self.settings, START+220)
        self.assertFalse(ok)
        self.assertEqual(reason, "duplicate_window")
        pending = self.ledger.pending(START+1000)
        self.assertEqual(len(pending), 1)
        self.assertTrue(self.ledger.settle(pending[0]["id"], 1.0, START+1100))
        self.assertFalse(self.ledger.settle(pending[0]["id"], 1.0, START+1101))
        expected = 4*(1-0.45) - 4*0.0695*.45*.55 - 4*.01
        self.assertAlmostEqual(self.ledger.stats(self.settings, START+1200)["realized_total"], round(expected, 4), places=4)

    def test_fails_on_small_bankroll_and_expiry(self):
        tiny = Settings(paper_bankroll=0.30)
        ok, reason = self.ledger.enter(self.window, self.choice, 2, tiny, START+200)
        self.assertFalse(ok)
        self.assertEqual(reason, "insufficient_paper_cash")
        ok, reason = self.ledger.enter(self.window, self.choice, 2, self.settings, START+800)
        self.assertFalse(ok)
        self.assertEqual(reason, "expiry_too_near")

    def test_daily_loss_budget_reserves_open_downside(self):
        one = self.ledger.enter(self.window, self.choice, 5, self.settings, START+200)
        self.assertTrue(one[0], one)
        next_window = Window("btc-updown-15m-next", "cpc-next", START+900,
                             START+1800, 83690, 0.01, 0.01, 0.0695)
        middle = self.ledger.enter(next_window, self.choice, 5, self.settings, START+1100)
        self.assertTrue(middle[0], middle)
        third_window = Window("btc-updown-15m-third", "cpc-third", START+1800,
                              START+2700, 83690, 0.01, 0.01, 0.0695)
        result = self.ledger.enter(third_window, self.choice, 5, self.settings, START+2000)
        self.assertEqual(result, (False, "daily_worst_case_loss_cap"))

    def test_kill_switch_disables_entries(self):
        runner = Runner(self.path / "runtime", client=FakeUS(), settings=self.settings)
        try:
            (self.path / "runtime" / "KILL").touch()
            status = runner.tick(START+400)
            self.assertEqual(status["status"], "kill_switch")
            self.assertEqual(status["paper"]["trades_today"], 0)
            self.assertFalse(runner.ledger.exists(SLUG))
        finally:
            runner.close()

    def test_ledger_reopens_without_duplicate_trade(self):
        ok, _ = self.ledger.enter(self.window, self.choice, 4, self.settings, START+200)
        self.assertTrue(ok)
        second = Ledger(self.path / "ledger.sqlite3")
        try:
            self.assertTrue(second.exists(SLUG))
            ok, reason = second.enter(self.window, self.choice, 4, self.settings, START+210)
            self.assertEqual((ok, reason), (False, "duplicate_window"))
        finally:
            second.close()


if __name__ == "__main__":
    unittest.main()
