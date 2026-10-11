# Chay Trades NEXUS — Commercial launch contract (v0.1)

## Product

An interactive prediction-market *research* terminal built as a separate visual wrapper on the MIT-licensed Synpath-ai/prediction-market-arbitrage-trading-bot.

The upstream paper bot scans matched Kalshi/Polymarket markets, estimates opportunities from displayed prices after fee schedules, paper-tracks signals, and backtests. It **does not send orders**. All original copyright and license notices must remain.

## What's implemented in this version

- Responsive high-contrast, game-inspired mission-control interface in `terminal/` (plain HTML/CSS/JS; no package-install prerequisite).
- Functional command deck, candidate scanner, browser-local paper arena, deterministic sensitivity calculator, local event notebook, architecture roadmap, and proposed pricing view.
- Explicit **synthetic data** treatment on initial launch and offline fallback.
- Optional read-only FastAPI gateway `api/index.py` that invokes existing repo matching/pricing modules when licensed market data access is provisioned and opted in.
- Security defaults: no secrets in browser; origin allowlist; scanner disabled by default; no order/withdrawal endpoints.

## What is NOT implemented or verified

Customer sign-up, payment acceptance, entitlement checks, revenue, customer isolation, market data rights for commercial resale, independent fill proof, real trade execution, real profit, robust historical replay, durable audit, and regulatory review. Do **not** advertise these as present.

## Launch sequencing

1. Preview terminal: static host from `terminal/` (no keys needed, synthetic example data).
2. Hosted read-only API: deploy `api/index.py` from repository root as a separate Python/FastAPI service with `SYNPATH_API_KEY` held server-side, `PUBLIC_SCANNER_ENABLED=true`, and comma-separated `TERMINAL_ALLOWED_ORIGINS` list. The root `requirements.txt` should include FastAPI and ASGI runtime when deploying.
3. In Settings > Data link, enter its HTTPS origin. API status must indicate `execution_enabled=false`, `scanner_ready=true`; timestamp must be fresh (<=3 min), otherwise terminal falls back to DEMO.
4. Before any customer billing: protect API with authenticated tenant sessions and per-tenant quotas, test permitted commercial data redistribution, verify claims, privacy/security controls, support and refunds, price approval, and payment webhooks.
5. Launch research-only paid SaaS after end-to-end beta verification. Future live orders require a **separate** independently risk-tested service and explicit brokerage-specific authority, and must not be implied by UI visuals.

## Important assumptions and debt

- `src.discover.read_books` uses top-of-book prices and first matching contract only. No guaranteed depth or fill.
- Market rule-match results are supplied by Synpath; we have not independently certified proposition or settlement equivalence.
- Historical Polymarket prices in upstream `backtest/data.py` have unrealistic bid/ask assumptions: **do not use original displayed performance as verified returns**.
- The gateway's origin check is defense-in-depth, *not* user authentication. It must not be exposed publicly with valuable rate-limited data before adding server-side customer auth and rate limiting.
- The sample arena ledger is localStorage, not signed or durable.
- Hosting source code alone does not grant permission to resell a third-party feed's raw or derived data; review Synpath and venue terms.

## Pricing hypothesis (NOT active payment products)

- Explorer: $0, demo and education.
- Researcher: $39/month **proposed** for connected read-only research after validation.
- Operator: $99/month **proposed** for advanced analytics and evidence export after implementation.

Pricing can change after customer discovery. No checkout endpoints are connected and no subscription charges are authorized here.

## Launch copy (factually supportable now)

> Chay Trades NEXUS is an interactive prediction-market research terminal built around open-source fee-aware matching and paper-trading technology. Explore hypothetical arbitrage scenarios, study fee and hedge sensitivity, and understand why apparent price gaps do not always turn into realized profit. Read-only live data is planned/available only when an approved data gateway is connected. No real order entry, no earnings claims, and no exchange affiliation.

## MIT attribution

Original project: https://github.com/Synpath-ai/prediction-market-arbitrage-trading-bot

Copyright (c) 2026 Synpath. MIT license. Preserve original `LICENSE` in the fork. Vendor SDK licenses, fonts, APIs and market-data contracts require separate checks.