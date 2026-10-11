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


@app.get("/api/engine/status")
def engine_status() -> dict[str, Any]:
    """Capability truth: source is present, but finance/data services have separate gates."""
    try:
        from api.engine_fixtures import evaluate_fixtures
        fixture_count = len(evaluate_fixtures()["opportunities"])
        fork_ok = fixture_count > 0
    except Exception:
        fixture_count, fork_ok = 0, False
    return {
        "schema": "chaytrades.capabilities.v1",
        "engine": "Synpath fork + Chay Trades research adapter",
        "fork_pricing": {"integrated": fork_ok, "module": "src.arbitrage.Pricer",
                         "test_cases": fixture_count, "input_type": "synthetic"},
        "fork_strategy": {"integrated": fork_ok, "module": "src.strategy.Strategy"},
        "cross_venue_market_matching": {"integrated": False, "vendor_key_required": True,
                                        "status": "SYNC_PENDING"},
        "nexus_worldline": {"runtime_connected": False, "configured": bool(os.getenv("NEXUS_ADVISORY_URL") and os.getenv("NEXUS_ADVISORY_SECRET")), "status": "CONFIGURED_NOT_PROVEN" if os.getenv("NEXUS_ADVISORY_URL") and os.getenv("NEXUS_ADVISORY_SECRET") else "PRIVATE_NOT_CONNECTED"},
        "alphapy": {"runtime_connected": False, "status": "NOT_IMPORTED_LEGACY_DEPENDENCIES"},
        "oddsharvester": {"runtime_connected": False, "status": "OPTIONAL_SPORTS_RESEARCH_NOT_IMPORTED"},
        "kalshi": {"live_quotes_connected": False, "status": "SEPARATE_VENUE_DATA_GATE"},
        "polymarket_us": {"live_quotes_connected": False, "status": "SEPARATE_VENUE_DATA_GATE"},
        "execution_enabled": False,
        "realized_profit_verified": False,
    }


@app.get("/api/engine/fixtures")
def engine_fixtures() -> dict[str, Any]:
    """Actual fork pricing + strategy on deterministic imaginary data, no external IO."""
    from api.engine_fixtures import evaluate_fixtures
    return evaluate_fixtures()


@app.get("/api/engine/intelligence/{case_id}")
def engine_intelligence(case_id: str, request: Request) -> dict[str, Any]:
    """Server-to-server integration: original fork fixture -> private NEXUS Worldline.

    No user secrets, account data, orders, or browser-facing NEXUS bearer tokens.
    Fail closed if NEXUS runtime, permissions or returned authority cannot be verified.
    """
    origin = request.headers.get("origin")
    if origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="Origin not authorized for public research preview.")
    nexus_url = os.getenv("NEXUS_ADVISORY_URL", "").rstrip("/")
    token = os.getenv("NEXUS_ADVISORY_SECRET", "")
    if not (nexus_url.startswith("https://") and token and len(token) >= 32):
        raise HTTPException(status_code=503, detail="Private NEXUS advisory runtime not configured.")
    from api.engine_fixtures import evaluate_fixtures
    fixture = evaluate_fixtures()
    candidate = next((r for r in fixture["opportunities"] if r["id"] == case_id), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Fixture not found.")
    payload = {
        "id": candidate["id"], "asOf": fixture["as_of"], "quoteTime": fixture["as_of"],
        "source": "FORK_FIXTURE", "yesPrice": candidate["yes_price"],
        "noPrice": candidate["no_price"], "fees": candidate["fees"],
        "rules": "synthetic_assumed", "liquidityVerified": False, "feesVerified": False,
        "hypotheticalPairs": 100, "assumedBothLegFillProbability": 0.8,
        "assumedSlippagePerPair": 0.01, "assumedHedgeFailureLossPerPair": 0.1,
        "evidenceRefs": ["repo:chaybuilds/prediction-market-arbitrage-trading-bot:src/arbitrage.py"],
    }
    try:
        import httpx
        response = httpx.post(
            nexus_url + "/api/arbitrage-intelligence",
            json=payload,
            headers={"Authorization": "Bearer " + token, **({"x-vercel-protection-bypass": os.environ["NEXUS_DEPLOYMENT_BYPASS"]} if os.getenv("NEXUS_DEPLOYMENT_BYPASS") else {})},
            timeout=9.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        advisory = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Private NEXUS advisory service unavailable.") from exc
    if not isinstance(advisory, dict) or advisory.get("schema") != "nexus.oracle.prediction-arbitrage.v1" or advisory.get("authority") != "ADVISORY_ONLY" or advisory.get("executionEnabled") is not False or advisory.get("worldline", {}).get("schema") != "nexus.worldline.forecast.v1":
        raise HTTPException(status_code=502, detail="NEXUS authority or schema mismatch.")
    return {"source": "PRIVATE_NEXUS_WORLDLINE", "candidate_source": "SYNTHETIC_FORK_FIXTURE",
            "execution_enabled": False, "advisory": advisory}



# ===== Official Polymarket US (different exchange from intl Polymarket) =====
# Public market information is available without account secrets.
# This optional integration is guarded separately from Synpath cross-venue data.
def _public_us_enabled() -> bool:
    return os.getenv("POLY_US_PUBLIC_RESEARCH_ENABLED", "false").lower() == "true"

@app.get("/api/poly-us/status")
def poly_us_status() -> dict[str, Any]:
    from api.poly_us import connection_status
    from api.execution_guard import execution_status
    status = connection_status()
    status["public_research_configured"] = _public_us_enabled()
    status["execution"] = execution_status()
    status["forward_ledger_persistent"] = False
    status["note"] = "Credentials not read by public adapter. No orders or personal account requests."
    return status

@app.get("/api/poly-us/markets")
def poly_us_markets(limit: int = 8) -> dict[str, Any]:
    if not _public_us_enabled():
        raise HTTPException(status_code=503, detail="Polymarket US public feed feature gate disabled.")
    from api.poly_us import public_catalog, PublicQuoteUnavailable
    try:
        return public_catalog(limit=max(1,min(limit,8)))
    except PublicQuoteUnavailable as exc:
        raise HTTPException(status_code=502, detail="Official US market catalog temporarily unavailable.") from exc

@app.get("/api/poly-us/quote/{slug}")
def poly_us_quote(slug: str) -> dict[str, Any]:
    if not _public_us_enabled():
        raise HTTPException(status_code=503, detail="Polymarket US public feed feature gate disabled.")
    from api.poly_us import public_quote, PublicQuoteUnavailable
    try:
        return public_quote(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Polymarket US slug.") from exc
    except PublicQuoteUnavailable as exc:
        raise HTTPException(status_code=502, detail="Official US market quote unavailable.") from exc

@app.get("/api/poly-us/witness/{slug}")
def poly_us_witness(slug: str) -> dict[str, Any]:
    """Read the public quote and form a forward-only abstain record.
    
    This endpoint does NOT pretend to persist; no customer/user auth exists yet.
    """
    quote=poly_us_quote(slug)
    from api.paper_forward import observation_event, assess_paper_intent
    event=observation_event(quote)
    decision=assess_paper_intent(quote)
    return {"schema":"chaytrades.poly_us.paper_witness_response.v1",
            "observation":event,"decision":decision,
            "stored_server_side":False,
            "never_assume_fill":True,"execution_enabled":False}

@app.get("/api/poly-us/execution/status")
def poly_us_execution_status() -> dict[str, Any]:
    from api.execution_guard import execution_status
    return execution_status()
