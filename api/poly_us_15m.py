"""Official Polymarket US BTC 15-minute observer (BBO, not L2 orderbook).

The vendor's 15m event links to a cpc-* market slug. The full /book
endpoint can return 404 for this category even when /bbo is live.
The BBO does not attest exchange transaction time or executable quantity.
No account keys, orders, or profitability claims are used in this module.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

class BTC15mUnavailable(Exception):
    """Official 15m market or public BBO cannot currently be established."""


def current_window(clock: datetime | None = None) -> dict[str, str]:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    now = now.astimezone(timezone.utc)
    start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    end = start + timedelta(minutes=15)
    suffix = start.strftime("%Y-%m-%d-%H%M") + "z"
    return {
        "event_slug": "btc-updown-15m-" + suffix,
        "market_slug": "cpc-btc-updown-15m-" + suffix,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
    }


def _amount(value: Any) -> float | None:
    if not isinstance(value, dict) or value.get("currency") != "USD":
        return None
    try:
        x = Decimal(str(value.get("value")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not x.is_finite() or x <= 0 or x >= 1:
        return None
    return float(x)


def _depth(value: Any) -> int | None:
    if type(value) is not int or value < 0 or value > 1_000_000_000:
        return None
    return value


def public_btc15m_bbo(*, client: Any = None, clock: datetime | None = None) -> dict[str, Any]:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    now = now.astimezone(timezone.utc)
    window = current_window(now)
    owns_client = client is None
    if owns_client:
        from polymarket_us import PolymarketUS
        client = PolymarketUS(timeout=7.0, max_retries=0)
    try:
        event_response = client.events.retrieve_by_slug(window["event_slug"])
        quote_response = client.markets.bbo(window["market_slug"])
    except Exception as exc:
        raise BTC15mUnavailable("current official Polymarket US BTC 15m BBO unavailable") from exc
    finally:
        if owns_client:
            client.close()
    event = event_response.get("event") if isinstance(event_response, dict) else None
    book = quote_response.get("marketData") if isinstance(quote_response, dict) else None
    if not isinstance(event, dict) or not isinstance(book, dict):
        raise BTC15mUnavailable("missing official event or BBO shape")
    if event.get("slug") != window["event_slug"] or book.get("marketSlug") != window["market_slug"]:
        raise BTC15mUnavailable("official event or market identity mismatch")
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise BTC15mUnavailable("cannot verify event-to-market identity")
    matched = next((x for x in markets
                    if isinstance(x, dict) and x.get("slug") == window["market_slug"]), None)
    if matched is None:
        raise BTC15mUnavailable("no matching US market in official event")
    bid = _amount(book.get("bestBid"))
    ask = _amount(book.get("bestAsk"))
    bid_depth = _depth(book.get("bidDepth"))
    ask_depth = _depth(book.get("askDepth"))
    integrity = bool(
        event.get("active") is True and event.get("closed") is False
        and matched.get("active") is True and matched.get("closed") is False
        and book.get("state") == "MARKET_STATE_OPEN"
        and bid is not None and ask is not None and bid < ask
        and bid_depth is not None and ask_depth is not None
        and bid_depth > 0 and ask_depth > 0
    )
    return {
        "schema": "chaytrades.poly_us.btc15m.bbo.v1",
        "venue": "POLYMARKET_US",
        "source": "POLYMARKET_US_OFFICIAL_PUBLIC_BBO_SDK",
        "source_class": "PUBLIC_UNAUTHENTICATED_BBO_NOT_LEVEL2",
        **window,
        "observed_at": now.isoformat(),
        "vendor_exchange_transact_at": None,
        "vendor_exchange_time_verified": False,
        "market_state": str(book.get("state") or "UNKNOWN"),
        "event_active": event.get("active") is True,
        "market_open": matched.get("closed") is False,
        "best_bid": bid,
        "best_ask": ask,
        "spread": round(ask - bid, 6) if bid is not None and ask is not None else None,
        "vendor_bid_depth_field": bid_depth,
        "vendor_ask_depth_field": ask_depth,
        "depth_units_verified_as_executable_contracts": False,
        "quote_integrity_valid": integrity,
        "research_observation_only": True,
        "independent_probability_estimate": None,
        "directional_edge_verified": False,
        "fill_verified": False,
        "realized_profit_verified": False,
        "decision": "ABSTAIN_NO_CALIBRATED_SIGNAL",
        "execution_enabled": False,
        "orders_submitted": 0,
        "settlement_reference": "CF Benchmarks BRTI; verify exact event rules before any research claim",
        "warning": "BBO lacks an exchange timestamp and verified executable contract quantity. Prices are observations, not fills or profits.",
    }
