"""Official Polymarket US public read-only market adapter.

The US exchange is distinct from international Polymarket and Synpath/Kalshi
matching. This module NEVER authenticates, creates orders or calculates arb.
No API keys are loaded or passed to the read-only SDK client.
"""
from __future__ import annotations
import math
import re
from datetime import datetime, timezone
from typing import Any

MARKET_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{3,139}$")
SOURCE = "POLYMARKET_US_OFFICIAL_PUBLIC_SDK"
MAX_AGE_SECONDS = 90

class PublicQuoteUnavailable(Exception):
    """Cannot establish a usable public quote."""


def require_slug(slug: str) -> str:
    if not isinstance(slug, str) or MARKET_SLUG.fullmatch(slug) is None:
        raise ValueError("invalid market slug")
    return slug


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        if value.get("currency") not in (None, "USD"):
            return None
        value = value.get("value")
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        obj = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if obj.tzinfo is None:
        return None
    return obj.astimezone(timezone.utc)


def _top(levels: Any, side: str) -> tuple[float | None, float | None]:
    if not isinstance(levels, list):
        return None, None
    normalized = []
    for level in levels[:100]:
        if not isinstance(level, dict):
            continue
        p = _number(level.get("px"))
        qty = _number(level.get("qty"))
        if p is not None and qty is not None and 0 < p < 1 and qty > 0:
            normalized.append((p, qty))
    if not normalized:
        return None, None
    return (max(normalized, key=lambda x: x[0]) if side == "bid"
            else min(normalized, key=lambda x: x[0]))


def public_quote(slug: str, *, client: Any = None, clock: datetime | None = None) -> dict[str, Any]:
    slug = require_slug(slug)
    observed = clock or datetime.now(timezone.utc)
    created = client is None
    if created:
        from polymarket_us import PolymarketUS
        client = PolymarketUS(timeout=6.0, max_retries=0)
    try:
        info = client.markets.retrieve_by_slug(slug)
        book = client.markets.book(slug)
    except Exception as exc:
        # API errors might contain request IDs and upstream details; never publish full exceptions.
        raise PublicQuoteUnavailable("official public market source unavailable") from exc
    finally:
        if created:
            client.close()
    if not isinstance(book, dict) or not isinstance(book.get("marketData"), dict):
        raise PublicQuoteUnavailable("missing official orderbook marketData")
    b = book["marketData"]
    if b.get("marketSlug") != slug:
        raise PublicQuoteUnavailable("mismatched market slug from vendor")
    record = info.get("market", {}) if isinstance(info, dict) else {}
    if not isinstance(record, dict):
        record = {}
    bid, bid_qty = _top(b.get("bids"), "bid")
    ask, ask_qty = _top(b.get("offers"), "ask")
    exchange = _timestamp(b.get("transactTime"))
    age_s = (observed - exchange).total_seconds() if exchange else None
    exchange_clock_ok = (age_s is not None and -5 <= age_s <= MAX_AGE_SECONDS)
    is_open = b.get("state") == "MARKET_STATE_OPEN"
    valid_spread = bid is not None and ask is not None and bid < ask
    quote_valid = bool(is_open and valid_spread and exchange_clock_ok)
    title = next((v for v in [record.get("question"), record.get("title"), record.get("name")]
                  if isinstance(v, str) and v.strip()), slug)
    return {
        "schema": "chaytrades.poly_us.public_quote.v1",
        "venue": "POLYMARKET_US",
        "source": SOURCE,
        "source_class": "OFFICIAL_PUBLIC_UNAUTHENTICATED",
        "market_slug": slug,
        "title": title[:200],
        "observed_at": observed.isoformat(),
        "exchange_transact_at": exchange.isoformat() if exchange else None,
        "exchange_age_seconds": round(age_s, 3) if age_s is not None else None,
        "market_state": str(b.get("state") or "UNKNOWN"),
        "best_yes_bid": bid, "best_yes_ask": ask,
        "best_bid_quantity": bid_qty, "best_ask_quantity": ask_qty,
        "spread": round(ask - bid, 6) if bid is not None and ask is not None else None,
        "quote_freshness_valid": exchange_clock_ok,
        "usable_for_research": quote_valid,
        "filled_order_verified": False,
        "expected_profit_verified": False,
        "cross_venue_arb_evaluated": False,
        "execution_enabled": False,
        "note": "Public quote only; exchange clock and executable fill cannot be independently attested.",
    }


def public_catalog(*, limit: int = 8, client: Any = None) -> dict[str, Any]:
    limit = max(1, min(12, int(limit)))
    created = client is None
    if created:
        from polymarket_us import PolymarketUS
        client = PolymarketUS(timeout=6.0, max_retries=0)
    try:
        payload = client.markets.list({"limit": limit})
    except Exception as exc:
        raise PublicQuoteUnavailable("official public catalog unavailable") from exc
    finally:
        if created:
            client.close()
    rows = payload.get("markets", []) if isinstance(payload, dict) else []
    result = []
    if isinstance(rows, list):
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            slug = row.get("slug")
            if not isinstance(slug, str) or MARKET_SLUG.fullmatch(slug) is None:
                continue
            title = next((v for v in (row.get("question"), row.get("title"), row.get("name"))
                          if isinstance(v, str) and v.strip()), slug)
            result.append({"slug": slug, "title": title[:180],
                           "state": str(row.get("status") or row.get("state") or "UNVERIFIED"),
                           "venue": "POLYMARKET_US"})
    return {"schema": "chaytrades.poly_us.public_catalog.v1",
            "source": SOURCE, "as_of": datetime.now(timezone.utc).isoformat(),
            "markets": result, "public_only": True, "execution_enabled": False}


def connection_status() -> dict[str, Any]:
    import os
    return {"schema": "chaytrades.poly_us.connection.v1",
            "official_sdk_integrated": True,
            "public_data_requires_account": False,
            "account_key_id_configured": bool(os.getenv("POLYMARKET_KEY_ID")),
            "account_secret_configured": bool(os.getenv("POLYMARKET_SECRET_KEY")),
            "account_verified": False,
            "nexus_live_verified": False,
            "paper_forward_witness": "IMPLEMENTED_NOT_SCHEDULED",
            "execution_enabled": False,
            "mode": "READ_ONLY_RESEARCH",
            "operator_action": "Create account API keys at https://polymarket.us/developer and configure server-only secrets once private authenticated runtime is ready"}
