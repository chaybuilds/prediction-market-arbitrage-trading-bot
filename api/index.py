"""Chay Trades / NEXUS read-only REST bridge for the MIT-licensed Synpath research bot.

No account endpoints, no order endpoints, no trading credentials, and no capital authority.
The scanner deliberately fails closed until explicitly enabled by the operator.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Chay Trades Read-only Market Research", version="0.1.0", docs_url=None, redoc_url=None)
allowed_origins = [x.strip() for x in os.getenv("TERMINAL_ALLOWED_ORIGINS", "").split(",") if x.strip()]
# Explicit origin allowlist; never allow all origins with a vendor-paid API key.
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["GET"], allow_headers=["content-type"])
_cached: dict[str, Any] = {"at": 0.0, "data": None}
_lock = Lock()


def is_ready() -> bool:
    return os.getenv("PUBLIC_SCANNER_ENABLED", "").lower() == "true" and bool(os.getenv("SYNPATH_API_KEY", "")) and bool(allowed_origins)


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "engine": "nexus-arbitrage-readonly-bridge",
        "scanner_ready": is_ready(),
        "execution_enabled": False,
        "mode": "vendor-readonly" if is_ready() else "not-configured",
        "reason": None if is_ready() else "Configure SYNPATH_API_KEY, PUBLIC_SCANNER_ENABLED=true, and TERMINAL_ALLOWED_ORIGINS on the server.",
    }


def _scan() -> dict[str, Any]:
    """Run the original repo's market matcher and fee-aware pricer, never its order methods."""
    import httpx
    import synpath
    from src.discover import event_pairs, read_books, search

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=12.0) as http:
        events = search(http, query=None, domain=None, events=12)
        for event in events:
            if time.monotonic() - started > 22:
                break
            try:
                matches = event_pairs(http, str(event["event_id"]))
            except (ValueError, KeyError, LookupError, httpx.HTTPError):
                continue
            for candidate in matches:
                if len(results) >= 14 or time.monotonic() - started > 22:
                    break
                pair = candidate.pair
                if pair.rules != "same":
                    continue
                try:
                    quoted = read_books(synpath.Client(), candidate)
                except Exception:
                    continue
                if quoted.kalshi is None or quoted.poly is None:
                    continue
                k = quoted.kalshi
                p = quoted.poly
                direction_yes = p.ask is not None and k.bid is not None
                direction_kalshi = k.ask is not None and p.bid is not None
                if not direction_yes and not direction_kalshi:
                    continue
                # The calculation is sourced from the existing repo, including its fee schedules.
                from src.arbitrage import Pricer
                client = synpath.Client()
                try:
                    pricer = Pricer(client.fetch_fee_schedule(pair.kalshi_id), client.fetch_fee_schedule(pair.poly_id), contracts=100)
                    opportunities = pricer.opportunities(k, p)
                except Exception:
                    continue
                if not opportunities:
                    continue
                best = opportunities[0]
                # The browser applies an additional slippage stress test; no fill guarantee is made.
                if not (0 < best.yes.price < 1 and 0 < best.no.price < 1):
                    continue
                results.append({
                    "id": pair.kalshi_id + "::" + pair.poly_id,
                    "title": pair.name,
                    "short": pair.event,
                    "sector": "Macro",  # conservative display category; market-specific taxonomy not inferred
                    "yes_venue": best.yes.venue.title(),
                    "no_venue": best.no.venue.title(),
                    "yes_price": round(best.yes.price, 6),
                    "no_price": round(best.no.price, 6),
                    "fees": round(best.fees, 6),
                    "vendor_rules": pair.rules,
                    "liquidity_verified": False,
                })
    return {"as_of": datetime.now(timezone.utc).isoformat(), "source": "Synpath price snapshots (unverified fillability)", "opportunities": results, "execution_enabled": False}


@app.get("/api/opportunities")
def opportunities(request: Request) -> dict[str, Any]:
    if not is_ready():
        raise HTTPException(status_code=503, detail="Read-only scanner is not configured.")
    # CORS alone is not authentication. This endpoint must sit behind edge rate-limits or a
    # customer-authenticated proxy before it can be made public as a paid product.
    origin = request.headers.get("origin")
    if origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="Origin not allowed; customer identity gateway required for public launch.")
    with _lock:
        if _cached["data"] is not None and time.monotonic() - _cached["at"] < 60:
            return _cached["data"]
        try:
            data = _scan()
        except Exception as exc:
            # Never expose vendor keys, request headers, tracebacks, or subscription data.
            raise HTTPException(status_code=502, detail="Upstream market data unavailable. Retrying automatically is disabled.") from exc
        _cached.update(at=time.monotonic(), data=data)
        return data