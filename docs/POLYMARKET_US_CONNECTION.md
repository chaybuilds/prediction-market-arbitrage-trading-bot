# ORACLE / CHAY TRADES — Polymarket US account connection & forward witness

**October 11, 2026.** US Polymarket is a separate venue from the international
Polymarket contract in the Synpath/Kalshi fork. Do not mix markets.

## What is online

* The official Python SDK \`polymarket-us==2.4.1\` backs an **unauthenticated, read-only** US public catalog and L2 book API. \`POLY_US_PUBLIC_RESEARCH_ENABLED\` guards public calls independently of Synpath.
* Routes: \`/api/poly-us/status\`, \`/api/poly-us/markets\`, \`/api/poly-us/quote/{slug}\`, \`/api/poly-us/witness/{slug}\`, \`/api/poly-us/execution/status\`.
* The terminal has a US Research Bridge with explicit market selection, official public quotes, and manual browser-saved forward quote witnesses. Any later observed bid-to-earlier ask comparison is a **modeled quote change**; no order fill or realized P&L is asserted.
* The existing OO Supabase project \`syrqomebszaotdahewnz\` has an isolated private schema \`oracle_paper_us\`. Migration \`oracle_polymarket_us_private_forward_witness_v01\` created a watchlist, request queue, append-only market observations, RLS with no public policies, and no public grants.
* Initial two real Bitcoin public orderbook snapshots were stored at approx 2026-10-11T16:35:38Z, with SHA-256 fingerprints, exchange timestamps, and decision \`ABSTAIN_NO_VALIDATED_SIGNAL\`.
* \`pg_cron\` schedules two **public-data-only** jobs: \`oracle_poly_us_enqueue_v01\` (id 10, every 15 min) and \`oracle_poly_us_collect_v01\` (id 11, 2 min after each polling window). Each collects at most 5 fixed US market slugs. No trades; no account credentials. Check job failures, public API terms and cost before expanding.
* Current watched slugs: \`cpc-btc-150k-12-31-2026\`, \`cpc-btc-150k-11-30-2026\`. They are observational anchors, not trade recommendations.

## Connect *your* Polymarket US account safely

1. Complete Polymarket US app access and required KYC at \`https://polymarket.us\`.
2. Go to \`https://polymarket.us/developer\` and sign in using exactly the same Apple/Google/email method as the app.
3. Create a key ID and secret. The secret is shown once. Keep both in a password manager; **never paste them in chat, browser JavaScript, GitHub files, PRs, or logs.**
4. When the private execution environment has been reviewed, add separate encrypted server-only configuration \`POLYMARKET_KEY_ID\` and \`POLYMARKET_SECRET_KEY\` in Vercel project Settings → Environment Variables, with no \`NEXT_PUBLIC_\` prefix and no client exposure.
5. The first authenticated call will only read \`account.balances\`, \`orders.list\`, and \`portfolio.positions\`; never call order creation as a connectivity test.
6. Do not enable real execution until production auth isolation, idempotent order ledger, venue compliance, order preview semantics, kill switch, reconciliation, human approval, and prospective calibrated performance prove safe.

## Evidence boundaries

**Public feed:** real exchange quotes at observed times, with freshness and depth checks, not expected-value alpha.
**Paper:** provisional hypothetical decisions, no real fill verification, not broker statements.
**NEXUS:** private Worldline scoring adapter in \`chaybuilds/nexus-core\` PR #29; code tested, remote runtime linkage not yet independently verified.
**Execution:** explicit hard-coded kill switch in \`api/execution_guard.py\`. No submit/cancel account endpoints, no capital authority, no real-money order transport.

## Resume / incident response

* To stop the database watcher, disable Supabase Cron jobs named \`oracle_poly_us_enqueue_v01\` and \`oracle_poly_us_collect_v01\`. Do not delete observation history.
* Check \`select status,count(*) from oracle_paper_us.poll_requests group by status;\`
* Check \`select count(*),max(received_at) from oracle_paper_us.market_observations;\`
* Verify both jobs via \`select jobname,schedule,active from cron.job where jobname like 'oracle_poly_us_%';\`
* Investigate stale vendor timestamps, invalid snapshots and quota/rate limits. No retries should fabricate market data.
