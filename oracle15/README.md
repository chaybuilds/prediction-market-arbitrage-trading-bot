# ORACLE 15M: Polymarket US BTC Up/Down

A **read-only public-data, automatic forward PAPER trading system** for recurring
15-minute BTC Up/Down events on **Polymarket US**. It lives alongside the
existing Kalshi/Polymarket cross-market arbitrage proof-of-concept; it does
**not** overwrite or activate that separate bot.

**No private credentials, orders, deposits, withdrawals or signed trading
requests are used by this code.** The `--live` flag explicitly fails closed.

## What the system does

1. Derives the active 15m BTC event slug from UTC quarter-hour boundaries.
2. Loads official public **US** event metadata and rejects any instrument
   other than BTC, index BRTI, horizon 15m and the exact active quarter-hour.
3. Reads the actual US book, and recent completed 1m BRTI candles.
4. Estimates a conservative, **uncalibrated** probability for closing above
   the published price-to-beat, using historical return volatility and a
   40% shrinkage-style uncertainty penalty.
5. Computes **taker** fees from the active market coefficient, effective
   spreads, a slippage reserve, and a further model-risk buffer.
6. If it clears the net edge threshold, records an imaginary entry with
   at most 20% of best-level liquidity, a maximum $2.50 stake, a maximum
   $7.50 open stake, a daily $5 stop and six simulated entries per UTC day.
7. Uses UNIQUE(event_slug) and SQLite transactions to prevent duplicates,
   even after a restart or overlapping scans.
8. Checks previously simulated positions for the venue's official 0/1
   settlement; when available, books final paper P&L. If no settlement is
   available, the position stays unresolved and counts toward exposure.
9. Logs all skipped windows and action reasons into SQLite for audits.

**The model has no established out-of-sample edge.** Its "expected edge"
is only its *own*, as-yet-uncalibrated probabilistic estimate minus
estimated execution costs. A positive signal is not proven profitability.
A profitable demo/backtest is not an independent live result.

## Run on macOS/Linux

From repo root, using Python >=3.9:

```bash
python3 -m unittest discover -s tests -p test_oracle15.py -v
python3 -m oracle15 --once
python3 -m oracle15 --status
python3 -m oracle15 --loop --interval 30
```

The loop continuously **observes and paper trades** while your Mac is awake,
network-connected, and the process is running. It does **not** send orders,
and it is not a server-backed 24/7 deployment.

Control all state with `ORACLE15_STATE_DIR`, or `--state-dir DIR`.
By default: `state/oracle15/paper.sqlite3` and `runner.lock`.
Do not delete the ledger just because you want fresh experiment results;
versioned experiments need immutable lineage.

**Emergency stop for new signals/paper entries**:

```bash
mkdir -p state/oracle15
touch state/oracle15/KILL
```

Remove the file to restart paper entries. Existing simulated trades still
attempt official result reconciliation after stopping new entries. To stop
the entire loop, Ctrl+C or terminate its process. The loop only holds a
single lock and will reject a parallel copy.

## Critical exchange details

- **Polymarket US != Polymarket international**; they use different venue
  API/auth and BTC settlement references. US uses CF Benchmarks **BRTI**,
  not a random Binance/Coinbase/Chainlink spot feed.
- The event window is under `assetPriceTerms.windowStart/windowEnd`.
  **Do not trade off `market.endDate`**: it may be much later.
- Settlement uses a 60-second average of BRTI one-second prices preceding
  the fixed window boundary; even a correct BTC spot view can disagree.
- UP is long YES; DOWN is the complementary short-YES position in US.
  The public book is indexed by the YES instrument; DOWN executable cost
  is conservatively `1 - best_bid`.
- A single venue observation is not guaranteed executable: feed/latency,
  partial fills, tick-size rounding, and price movement need real
  preflight validation before enabling a real order adapter.

## Mandatory gates before a real-money deployment

**None of these gates are certified by this repository change:**

1. Verify account eligibility, custody, and official supported venue/API.
2. Create credentialed **read-only** account status on the intended host and
   configure secure secret storage **outside git/chat**.
3. Build an isolated order adapter with tested buy-long and buy-short
   semantics, post-only/IOC behavior, order preview and minimum lots.
4. Implement order-creation idempotency, persisted submit/ACK/unknown states,
   exactly-once reconciliation of fills, cancel, open risk and settlements.
5. Fail closed for stale BRTI/book/clock, exception, downtime, halt or lack
   of buying power. Real order cutoffs must tolerate worst-case latency.
6. Calibrate the probability model using forward-only data and proper
   realized fills/fees, comparing to a no-trade baseline. Measure net
   expectation, confidence intervals, drawdown and live-vs-paper slippage.
7. Start only with explicitly authorized, tiny capped live capital and
   rollback controls once independent review verifies all checks.

Do not grant a model permission to bypass the risk engine. NEXUS can
propose candidates, but execution authority must remain deterministic,
auditable and contract-limited.

## Optional background PAPER mode on the Mac

A user-scoped, reversible macOS LaunchAgent can keep the research loop
running at login with `KeepAlive`. It has no access to exchange credentials
and can never send an order:

```bash
python3 -m oracle15.macos_agent install
python3 -m oracle15.macos_agent status
tail -n 3 state/oracle15/paper-stdout.log
python3 -m oracle15.macos_agent uninstall
```

The service can run only while the laptop is powered, awake, connected,
and permitted to run background tasks. It does not constitute always-on
server infrastructure.
