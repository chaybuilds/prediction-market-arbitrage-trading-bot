"""Deterministic sandbox backed by the unmodified Synpath fork's actual pricing modules.

Inputs are synthetic. Fees are assumed. This is a *code integration test*, never
a market quote, tradeable arbitrage, fill claim, or evidence of profitability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.arbitrage import Book, Pricer
from src.strategy import Strategy

# All examples are deliberately fictional, including names, books and sizes.
CASES = (
    ("FX-BTC-001", "Synthetic BTC threshold A", "BTC scenario", "Crypto", (.39, .40, 750, 800), (.46, .47, 540, 900)),
    ("FX-FED-002", "Synthetic rate decision B", "Fed scenario", "Macro", (.59, .60, 650, 750), (.66, .67, 420, 800)),
    ("FX-NFL-003", "Synthetic sports outcome C", "Sports scenario", "Sports", (.44, .45, 800, 900), (.43, .44, 450, 550)),
    ("FX-ETH-004", "Synthetic ETH threshold D", "ETH scenario", "Crypto", (.31, .32, 400, 650), (.38, .39, 600, 700)),
    ("FX-CPI-005", "Synthetic inflation report E", "CPI scenario", "Macro", (.55, .56, 600, 700), (.53, .54, 240, 480)),
    ("FX-SOL-006", "Synthetic SOL threshold F", "SOL scenario", "Crypto", (.72, .73, 420, 520), (.80, .81, 310, 460)),
)

class AssumedFee:
    """Fake fees provided ONLY to exercise the real pricer's fee-schedule interface."""
    def __init__(self, per_contract: float = 0.0045):
        self.per_contract = per_contract

    def estimate(self, price: float, contracts: float) -> float:
        return self.per_contract * contracts

def evaluate_fixtures() -> dict[str, Any]:
    pricing = Pricer(AssumedFee(), AssumedFee(), contracts=100)
    strategy = Strategy(entry_edge=0.02, take_profit=0.02)
    results = []
    for id_, title, short, sector, k, p in CASES:
        kalshi = Book(k[0], k[1], k[2], k[3])
        polymarket = Book(p[0], p[1], p[2], p[3])
        opportunities = pricing.opportunities(kalshi, polymarket)
        if not opportunities:
            continue
        best = opportunities[0]
        size = min(best.size or 0, 100)
        results.append({
            "id": id_,
            "title": title,
            "short": short,
            "sector": sector,
            "yes_venue": best.yes.venue.title(),
            "no_venue": best.no.venue.title(),
            "yes_price": best.yes.price,
            "no_price": best.no.price,
            "fees": round(best.fees, 6),
            "edge": best.edge,
            "depth": size,
            "engine_entry": strategy.entry(opportunities) is not None,
            "vendor_rules": "SYNTHETIC_MATCH_ASSUMED",
            "liquidity_verified": False,
            "source": "FORK_FIXTURE",
            "source_code": ["src/arbitrage.py:Pricer", "src/strategy.py:Strategy"],
            "evidence_status": "SIMULATED_UNVERIFIED",
        })
    return {
        "schema": "chaytrades.fork-fixtures.v1",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "FORK_ENGINE_WITH_SYNTHETIC_INPUTS",
        "source_provenance": "Synpath MIT fork, original Pricer and Strategy classes",
        "fee_assumption": "$0.0045 per contract per leg (illustrative only)",
        "execution_enabled": False,
        "synthetic": True,
        "opportunities": results,
    }
