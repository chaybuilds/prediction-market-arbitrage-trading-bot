# Chay Trades / NEXUS: Polymarket US 15-minute Bitcoin forward-observation V0.1

## Verified topology (October 11, 2026, ET)

* Polymarket US is NOT international Polymarket.com. User's local Polymarket US SDK authenticated read-only checks reported PASS for balances, positions and open orders. This is user-reported local verification, not yet independently attested server-side verification.
* Personal API credentials reside only in protected Vercel project chay-trades-polymarket-account, with encrypted production vars. NO trading credentials enter public research API, static terminal, PostgreSQL quote cron or NEXUS advisory.
* The public API exposes GET /api/poly-us/btc15m/current using the official Polymarket US SDK, event lookup + BBO. The static terminal shows a distinct BTC/15M observer, with explicit ABSTAIN and vendor-clock / depth uncertainty.
* Events use btc-updown-15m-YYYY-MM-DD-HHMMz in UTC (quarter-hour aligned). Event.market[].slug uses cpc-btc-updown-15m-YYYY-MM-DD-HHMMz. Event-to-market identity is checked on each public API call. Source: verified public Polymarket US event API.
* The /v1/markets/{slug}/book route was observed to return 404 for an OPEN 15m market while /v1/markets/{slug}/bbo responded with a live price. Therefore 15m research uses official public BBO, NOT an assumed L2 orderbook. BBO has no independent vendor transaction time, and bidDepth/askDepth are not attested fillable quantities.
* Settlement reference in official event rules is CF Benchmarks Bitcoin Real-Time Index (BRTI), with opening/closing-window averaging. Do not equate a random exchange BTC spot quote with the official settlement benchmark.

## Private Supabase forward-only data witnesses

Project: syrqomebszaotdahewnz. Schema: oracle_paper_us. All tables have RLS enabled, NO public policies and revoked anon/authenticated/service_role grants; only private admin/cron access.

| Component | Schedule | Behavior |
| --- | --- | --- |
| oracle_poly_us_enqueue_v01 | Every 15 minutes | Original long-dated BTC US public L2 quotes |
| oracle_poly_us_collect_v01 | 2 minutes after polling windows | Commit raw long-dated public quote observations |
| oracle_poly_us_btc15m_forward_v01 | Every minute | Collect previously enqueued public BTC 15m BBO, append-only, then enqueue active UTC 15m contract BBO |
| oracle_poly_us_btc15m_settlement_v01 | Minutes 05,20,35,50 hourly | Query 45-60 minute old 15m contracts' PUBLIC reported settlement (0 or 1), no key or account access |

Tables for BTC 15m:
- btc15m_requests: publicly unauthenticated HTTP queue and status, never any capital operations.
- btc15m_observations: immutable official BBO raw data, sha256, server observation timestamp, open/state/integrity flags, decision always ABSTAIN.
- btc15m_forward_session: immutable research start cutoff, excludes historical market snapshots from being misrepresented as prospective model decisions.
- btc15m_forward_quote_marks: future bid versus earlier ask quote-change diagnostics and midpoint moves for SAME market slug only, always indicates no real position, no fee or fill/realized PnL verification.
- btc15m_settlement_requests / btc15m_settlement_observations: immutable public settlement value, stored at least 25 minutes after window end, labeled VENDOR_REPORTED_NOT_INDEPENDENT_FINALITY. No cash payout or market direction mapping verified.

Implemented through Supabase migrations:
- oracle_polymarket_us_btc15m_public_bbo_forward_v01
- oracle_btc15m_forward_quote_diagnostics_v01
- oracle_btc15m_vendor_settlement_witness_v01

## Truth & risk boundaries

1. Public market data collection **does not** equal authenticated account connectivity.
2. Bid and ask and "depth" fields **do not** prove executable contracts / fills.
3. A vendor-reported settled outcome **does not** prove the user received a payout.
4. A forward quote-change comparison **is not** a profitable paper trade or realized returns.
5. NEXUS Worldline provides an explicit no-position ABSTAIN baseline until calibrated independent forecast probabilities exist.
6. Existing fail-closed risk preflight refuses every live order even when account credentials exist. In api/execution_guard.py the prototype has fixed $10 max order/exposure, $3 daily drawdown threshold, one open order limit and kill-switch TRUE. These are architectural/test caps, NOT venue-enforced yet.
7. No current live Polymarket US order-placement method is wired to Chay Trades. No account debit, no real trade, no autonomous trading permission.

## Auditing / disabling

Run privately as database administrator (do not expose this schema through the Data API):

    select jobname,schedule,active from cron.job where jobname like 'oracle_poly_us_%';
    select count(*),max(observed_at) from oracle_paper_us.btc15m_observations;
    select count(*) from oracle_paper_us.btc15m_forward_quote_marks;
    select count(*) from oracle_paper_us.btc15m_settlement_observations;
    select status,count(*) from oracle_paper_us.btc15m_requests group by status;
    select status,count(*) from oracle_paper_us.btc15m_settlement_requests group by status;

For containment: deactivate only affected jobs using authorized Supabase Cron controls; avoid purging immutable observation evidence.

## Requirements for graduating to true trading

* Read-only private Vercel authenticated account verification with no secrets in output; local PASS alone is insufficient.
* Correct BTC 15-minute settlement semantics, contract sizing, fees, best executable market depth and rate/venue limits.
* A real independent short-horizon probability model, time-split forward calibration, and comparison with market prices after fees/slippage.
* Forward paper strategy decisions recorded PRIOR to future outcomes, with rejected/stale scenarios, realistic modeled fills and fees, no lookahead leakage.
* Dedicated private broker transport with idempotency, reconciliation, cancel/replace, exception handling, broker API permissions and exchange rule checks.
* Explicit user-defined live capital limit and bounded operator authority, e-stop, and verified position-risk protections. Never infer authorization from connected keys.

Current phase: real-data observation + vendor-reported outcomes; **NO VERIFIED PROFITABLE STRATEGY OR LIVE ORDER EXECUTION**.
