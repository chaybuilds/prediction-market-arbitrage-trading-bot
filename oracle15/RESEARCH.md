# ORACLE 15M Research Lab v0.2 — reproducible evidence contract

## Status and authority

This branch adds historical data collection, walk-forward research, NEXUS
integration and forward-calibrated **paper-only** risk management. It **does
not** provide or enable authenticated Polymarket account access, order
submission, withdrawals or live trading.

A model or strategy cannot promote itself. Research summaries must not be
confused with fills, executable prices, realized cash, or guaranteed profits.
The CLI `python3 -m oracle15 --live` remains intentionally blocked.

### Sources and settlement

**Polymarket US market-native history:** `https://gateway.polymarket.us`
public event, settlement, and price-history endpoints. Custom-range market
history at `fidelity=1` preserves irregular, approximately minute-sampled
`longPrice` (Yes display ask) and `shortPrice` (No display ask).
Implied bids are `1 - opposite ask`, subject to the venue's symmetry.
Those snapshots do **not** contain historical best-level depth, trades,
queue position or a guarantee of price execution. Snapshots timestamped at
or after `assetPriceTerms.windowEnd` are **excluded** from backtesting.
Settlement labels are retrieved independently and used **only after** a
position is held to expiry or to score earlier registered forecasts.

US BTC settlement uses the CF Benchmarks **BRTI** reference and 60 prices
averaged before each time boundary. The live paper model reads only
completed US reference-index candles, not Binance spot or Chainlink.

The fixed **current** public US fee schedule is documented effective
**October 7, 2026 at 10 AM ET** (14:00 UTC): fee coefficient 0.0695,
applied as theta × contracts × p × (1-p). Historical contracts before
that timestamp are marked `PRE_OCT7_UNVERIFIED` and are not mixed into
the primary current-fee P&L test. For fee schedule or fee-tier changes,
refresh exchange documentation. Cross-venue historical performance cannot
be substituted for US market results.

**2020–September 2026 market context:** official public Binance spot and
USD-M perpetual BTC/USDT **15-minute candles**, fetched from
`https://data.binance.vision`, verified against matching official
`.CHECKSUM` SHA256 files. These are **not** historical Polymarket
quotes, BRTI settlement, liquidation tapes, depth or news events.
The 2020–2026 archive contains 81 calendar months × two venues =
162 verified monthly source files, totaling 473,128 venue-specific bars.

The repository retains only data collector code and tests. All downloaded
data, manifests, local logs and trades stay under gitignored `state/`.
Do not commit credentials or raw private-account information.

## Reproducible commands from the repository root

```bash
# Full offline safety and data-integrity suite, Python >= 3.9
python3 -m unittest discover -s tests -p 'test_oracle15*.py' -v

# All available post-fee US 15m markets, one request-paced backfill.
python3 -m oracle15.archive --start 2026-10-07T14:00:00Z --workers 5

# Pre-fee US launch-history: observation research only; commission
# assumptions aren't established for the earlier regime.
python3 -m oracle15.archive --start 2026-09-22T16:00:00Z \
  --end 2026-10-07T14:00:00Z --workers 5

# Six-plus years of official Binance BTC spot and perpetual regime evidence.
python3 -m oracle15.spot_archive --start 2020-01 --end 2026-09

# Training-only US quote-scalping tournament and anchored held-out block.
python3 -m oracle15.tournament

# EXTRA: 5c–95c exploratory high-cost tail entries, motivated by the
# user's 6c-to-15c scalp example. This NEVER adjusts the live paper gate.
python3 -m oracle15.tournament --tail-experiment

# 36 paired spot/perpetual lead-lag hypotheses, 24h purged time splits.
python3 -m oracle15.leadlag

# Import and call the local NEXUS experiment factory (advisory only).
python3 -m oracle15.nexus_bridge --max-bars 30000

# One-command local evidence scoreboard (all profit labels remain qualified).
python3 -m oracle15.report

# Independent live US public feed, local ledger and learning calibration.
python3 -m oracle15 --once
python3 -m oracle15 --status

# Mac login-resident PAPER research loop only while computer stays awake.
python3 -m oracle15.macos_agent status
```

### Current experimental tracks

| Track | Hypotheses | Historical unit | Evaluation |
| --- | ---: | --- | --- |
| US quote-scalping | 189 variants + 3 controls = 192 | Verified market-display Yes/No ask timestamps and final result | Prior-day training only, final untouched calendar-day block, expanding walk-forward, fee/slippage/latency stress |
| BTC spot–futures leads/lags | 36 | Paired Binance 15m closes and NEXT bar open/close | 50% chronological train, 25% validation, 25% test with 24-hour purge |
| NEXUS shared experiment factory | Four predefined spot rules | Binance spot OHLC, times marked on completed bars | Existing NEXUS factory, strictly advisory |
| Forward paper and learning | Up/Down probability + scalp exits | Live US book and completed BRTI candles | Fixed-time forecast preregistration, later official outcome and paper fills |

**US quote replay families:** quote momentum, quote reversion,
two-step trend, V-reversal, breakout, extreme range fading, and
spread contraction. Also always-Up, always-Down, seeded-direction
controls. Stop/target/hold parameters are enumerated explicitly in
`oracle15/tournament.py`, not secretly adjusted on the final block.

**Spot/futures families:** 1-bar/4-bar spot momentum and reversal,
futures-basis premium and changes, taker-buy imbalance from spot and
perpetual venues, volume spikes, high-volatility fade and
low-volatility momentum. The source is candlesticks and aggregated
volume, **not direct evidence** of manipulation, hidden makers,
liquidations, insider activity or politician/news causality. Those
proposed predictors require separately sourced, point-in-time data
and an explicit incremental-outcome test before any model uses them.

### Known limitations and falsification

1. **Quote availability is not a fill.** Entry signals at T fill on
   T+1 available snapshot in the historical experiment. Exits use
   opposite bid with another observation delay. Both legs pay taker
   fees and a configurable slippage reserve. Real book depth and maker
   queue cannot be inferred from one-minute display prices. A
   profitable historical quote replay may be unexecutable.
2. **Historical sample selection:** Missing market windows remain
   `UNAVAILABLE`; sparse histories are not treated as profitable
   complete windows. Archive checksums are saved. No synthetic data
   is inserted to fill missing quotes.
3. **Data regimes:** Pre-Oct7 fee conditions are not documented by the
   present fee schedule. Training there is for signal exploration
   only, not historical after-cost profit certification.
4. **Avoid leakage:** Strategy parameters are fixed in code before
   scoring. Champion selection uses training days only. Test-day
   labels must never influence test-day signals or training-based
   selection. A manifest hash documents inputs/parameters but is
   **not** external proof that hypotheses were preregistered in the
   past.
5. **False discovery:** A broad grid of 192 parameters may overfit.
   Training heuristic penalizes searched candidates; this is **not
   a corrected statistical p-value**. Bootstrap confidence intervals
   require enough independent market-days. If insufficient, report
   `null` and abstain.
6. **Risk:** Broker-side order-state reconciliation, fills, account
   permissions, order preview, official instrument semantics, tick
   precision, retry/idempotency, halts, stale markets and emergency
   liquidation cannot be certified in paper-only code.
7. **Empirical learning, not hindsight:** Forward 2m, 5m and 8m
   forecasts are inserted once per (window,horizon). They are immutable
   and scored only after authenticated public **settlement** labels
   arrive, with the market mid-price Brier score as the benchmark.
   They do not self-promote based on a single apparent win.
8. **Scalp paper execution:** Two independently fetched quotes are
   required to enter (signal → later simulated fill) and to exit
   (exit signal → later modeled bid). A no-trade decision is valid.
   A paper stop is **not** a guaranteed stop execution.

### Observed checkpoints on October 11, 2026 US Eastern time

- 418 full current-fee US 15-minute windows (October 7–11, plus part
  of October 12 UTC), 5,355 valid historical quotes; eight missing,
  one sparse. Chronological tournament on these 418 windows:
  final 99-window block selected **ABSTAIN**. Expanding prior-day
  walk-forward showed 24 model trades and approximately +$3.39
  conditional historical quote P&L, heavily concentrated in one
  day. Not reliable positive economic proof.
- A completed, bounded launch-to-present Polymarket US historical scan
  requested 1,859 quarter-hour windows. It retained **1,791** with
  adequate market display prices, **20** sparse, **48** unavailable,
  and **23,194** valid quote snapshots; 1,373 adequate windows are
  pre-October 7 and therefore fee-uncertain, while 418 use the
  currently documented fee regime. Missing windows are not backfilled
  with synthetic guesses.
- After the launch-to-present archive finished, the exploratory
  **1,791 usable US window** / **21 calendar day** replay of the
  192-strategy grid reserved a final **570 market held-out block**.
  Training-only selection returned **ABSTAIN**; its historical
  pre-October 7 fee assumptions are explicitly **unverified**, so
  this does not establish a reliable negative or positive cash P&L.
- Exploratory 5c–95c tail-scalp research at 2c/share of modeled
  execution slippage, expanded spread tolerance and current-fee events
  selected **ABSTAIN** on the final held-out block; the live paper bot
  still rejects 6c contracts until independent forward proof exists.
- Full paired 2020–September 2026 spot/futures history tested 36
  predeclared rules on 236,136 usable aligned 15m observations,
  after assumed 12 basis points total spot-market friction.
  Training lower-bound selection: **ABSTAIN**. These cannot be
  counted as Polymarket returns.
- The autonomous paper runner recorded a real-world-time
  **simulated** $2.50 loss on its first Up position, verified against
  US settlement. The ledger is retained rather than overwritten.

**Graduation criteria:** sufficient independent forward outcomes,
positive and well-calibrated expected value against market baseline
after executable costs, stable performance across day/regime and
latency scenarios, limited drawdown, audited code/CI, safe authenticated
order preview/ack/fill/cancel/reconciliation, and explicit written
risk-budget authority before any account trading.

This project records *observation → preregistered forecast →
hypothetical action → verified external outcome → recalibration*.
No result in this document is an earnings prediction or promise.
