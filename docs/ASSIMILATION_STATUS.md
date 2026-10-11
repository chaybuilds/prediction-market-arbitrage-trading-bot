# Chay Trades — Repository Assimilation Status

Verified on October 11, 2026. All simulation, integration testing, and capability status must be evaluated independently of marketing appearance.

## What runs

- Public Synpath MIT-licensed prediction market fork preserved. The actual `src.arbitrage.Pricer` and `src.strategy.Strategy` are imported and exercised by `api/engine_fixtures.py` using six deterministic synthetic order-book pairs and assumed fees. No exchange orders. Tests pass in GitHub Actions.
- `/api/engine/fixtures` and `/api/engine/status` are read-only; interactive terminal `terminal/engine.js` can populate the scanner with the returned **synthetic** original-fork calculations. Public Vercel deployment is a demo only.
- `chaybuilds/nexus-core` PR #29 contains a **private** advisory `createWorldlineForecast` bridge and tests. No private NEXUS source is copied to this public repository. The hosted NEXUS service is not yet activated or verified; the public browser never handles its service token.

## Not running

- Synpath hosted cross-venue market discovery requires a valid server-side provider key and permitted use. `PUBLIC_SCANNER_ENABLED=false` remains the default, so `/api/opportunities` returns 503.
- The fork targets international Polymarket contracts, not the separately governed Polymarket US exchange. No cross-venue US settlement equivalence or liquidity verified.
- No AlphaPy, OddsHarvester, or other third-party repository is loaded into the running trading process. Their individual licenses, dependency versions, provenance, API terms, and model evidence need reviews.
- No production customer login, paid entitlements, automated venue execution, reconciled fills, audited performance, or authority to move capital.

## Environment boundaries

- Static `terminal/` deployment: no API keys or private NEXUS code, and the UI labels all synthetic prices.
- Python API: public **research-only** module verification endpoints, with a separate fail-closed and off-by-default vendor scanner.
- Private NEXUS project: separate repository, token-gated advisory service, not browser accessible when deployed. Enable only after independent authentication, rate limits, and runtime verification.
- Never enable live trading on a code merge or user-provided model score. All opportunities remain research-only until independently validated.

## Acceptance and next phases

1. Confirm HTTPS responses for `/api/engine/status` and `/api/engine/fixtures` from deployed URLs, not just build status.
2. Add an authenticated, externally reachable private NEXUS service and verify one synthetic end-to-end evaluation from Python to Worldline and back, with no leakage of the bearer token.
3. Review vendor credentials/data resale and venue permissions before enabling market scanning.
4. Implement market contract mapping and settlement-rule checks separately for Polymarket US. Add order-book depth, quote freshness, exact fee schedules, realistic two-leg fill/failure costs, and forward performance tests.
5. Evaluate ML and sports-odds libraries as independent **research adapters**, not automatic alpha or execution privileges.

Public repo: https://github.com/chaybuilds/prediction-market-arbitrage-trading-bot/pull/1

Private Worldline adapter: https://github.com/chaybuilds/nexus-core/pull/29
