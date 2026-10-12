"""Read-only market data adapter for Polymarket US."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BASE = "https://gateway.polymarket.us"


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Window:
    event_slug: str
    market_slug: str
    start: int
    end: int
    target: float
    tick: float
    min_qty: float
    fee_coeff: float


@dataclass(frozen=True)
class Book:
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float


@dataclass(frozen=True)
class Candle:
    timestamp: int
    close: float
    ticks: int


def timestamp(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def event_slug(now):
    start = int(now) // 900 * 900
    return "btc-updown-15m-" + datetime.fromtimestamp(
        start, timezone.utc
    ).strftime("%Y-%m-%d-%H%Mz").lower()


def money(value):
    if isinstance(value, dict):
        value = value.get("value")
    v = float(value)
    if not math.isfinite(v):
        raise MarketDataError("invalid price")
    return v


class PolymarketUSPublic:
    def __init__(self, timeout=7):
        self.timeout = timeout

    def _get(self, path, params=None):
        url = BASE + path + ("?" + urlencode(params) if params else "")
        req = Request(url, headers={"User-Agent": "ORACLE15-PublicResearch/0.1"})
        try:
            with urlopen(req, timeout=self.timeout) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            raise MarketDataError("public feed unavailable: " + type(exc).__name__) from exc

    def window(self, now=None):
        now = time.time() if now is None else now
        slug = event_slug(now)
        raw = self._get("/v1/events/slug/" + quote(slug, safe=""))
        event = raw.get("event") or {}
        if event.get("slug") != slug or not event.get("active") or event.get("closed"):
            raise MarketDataError("current event not active")
        markets = event.get("markets") or []
        if len(markets) != 1:
            raise MarketDataError("ambiguous US market")
        m = markets[0]
        terms = m.get("assetPriceTerms") or {}
        asset = terms.get("asset") or {}
        try:
            start = timestamp(terms["windowStart"])
            end = timestamp(terms["windowEnd"])
            target = money(terms["priceToBeat"])
        except (ValueError, TypeError, KeyError) as exc:
            raise MarketDataError("missing window/target") from exc
        if not (start == int(now) // 900 * 900 and end-start == 900 and
                terms.get("marketType") == "ASSET_PRICE_MARKET_TYPE_UP_DOWN" and
                terms.get("horizon") == "15m" and
                terms.get("indexSymbol") == "BRTI" and
                asset.get("symbol", "").lower() == "btc" and
                target > 0 and m.get("status") == "MARKET_STATUS_OPEN" and
                m.get("active") and not m.get("closed")):
            raise MarketDataError("unexpected exchange/market identity or rules")
        name = m.get("slug", "")
        sides = m.get("marketSides") or []
        if name != "cpc-" + slug or len(sides) != 2 or not {s.get("long") for s in sides} == {True, False}:
            raise MarketDataError("unexpected YES/NO mapping")
        return Window(slug, name, start, end, target,
                      float(m.get("orderPriceMinTickSize") or 0),
                      float(m.get("minimumTradeQty") or 0),
                      float(m.get("feeCoefficient") or 0))

    def book(self, window):
        raw = self._get("/v1/markets/" + quote(window.market_slug, safe="") + "/book")
        data = raw.get("marketData") or {}
        if data.get("marketSlug") != window.market_slug or data.get("state") != "MARKET_STATE_OPEN":
            raise MarketDataError("book closed or wrong instrument")
        bids = data.get("bids") or []
        asks = data.get("offers") or []
        if not bids or not asks:
            raise MarketDataError("empty book")
        best_bid = max(bids, key=lambda x: money(x["px"]))
        best_ask = min(asks, key=lambda x: money(x["px"]))
        bid = money(best_bid["px"])
        ask = money(best_ask["px"])
        bid_qty = float(best_bid["qty"])
        ask_qty = float(best_ask["qty"])
        if not (0 < bid < ask < 1 and math.isfinite(bid_qty) and
                math.isfinite(ask_qty) and min(bid_qty, ask_qty) > 0):
            raise MarketDataError("crossed/invalid book")
        return Book(bid, ask, bid_qty, ask_qty)

    def candles(self, now=None):
        now = int(time.time() if now is None else now)
        raw = self._get("/v1/asset-prices/history", {
            "assets": "crypto:btc",
            "from": now - 7200,
            "to": now,
            "bucket": "ASSET_PRICE_BUCKET_1M",
        })
        series = [
            s for s in raw.get("series") or []
            if (s.get("asset") or {}).get("symbol", "").lower() == "btc"
            and s.get("bucket") == "ASSET_PRICE_BUCKET_1M"
        ]
        if len(series) != 1:
            raise MarketDataError("missing/ambiguous BTC reference series")
        candles = []
        for item in series[0].get("candles") or []:
            c = Candle(int(item["timestamp"]), float(item["close"]), int(item["tickCount"]))
            if c.timestamp + 60 <= now and c.ticks >= 55 and math.isfinite(c.close) and c.close > 0:
                candles.append(c)
        if len(candles) < 45:
            raise MarketDataError("insufficient complete index history")
        if now - (candles[-1].timestamp + 60) > 115:
            raise MarketDataError("reference index feed stale")
        return candles

    def settlement(self, market_slug):
        result = self._get("/v1/markets/" + quote(market_slug, safe="") + "/settlement")
        val = result.get("settlement")
        if val is None:
            return None
        val = money(val)
        return val if val in (0.0, 1.0) else None
