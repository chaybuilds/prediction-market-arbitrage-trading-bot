# ORACLE 15M: hypothesis frontier and fork audit

**Operating objective:** positive *measured* after-cost profit from legal,
authoritative market data, with minimal capital and nonnegotiable downside
limits. More hypotheses increase the false-discovery burden; their number
is never treated as evidence of an edge.

## Reusable fork assessment

| Local project | Decision | Evidence/limitation |
| --- | --- | --- |
| `chaybuilds/prediction-market-arbitrage-trading-bot` | **Integrated**: native ORACLE15 research package alongside the existing cross-venue paper arbitrage program | Preexisting Kalshi/international Polymarket matching does not establish BTC US 15m direction or scalp edge. |
| `chaybuilds/nexus-core` | **Actually integrated**: `nexus_bridge.py` invokes the existing `research/kraken/experiment-factory.mjs` under Node, validating advisory-only authority | Output from Binance spot cannot become US market settlement proof or cross-venue alpha without new validation. |
| `chaybuilds/AlphaPy` | **Studied, not vendored**: old ML optimizer/pipeline concepts | Dependency and license review required before adding libraries. Current 192/36-rule tests remain stdlib and auditable. |
| `chaybuilds/awesome-systematic-trading` | **Research catalog, not executable edge** | Many citations/strategies, but not a priced, order-executable US BTC market strategy. |
| `chaybuilds/OddsHarvester` | **Excluded from this lane** | Sports betting odds/scraping architecture is not BTC index/book evidence. |
| `chaygray89/Krypto-trading-bot` | **Reference only pending adapter/license audit** | Older C++ venue/market assumptions and market-making strategies are not safe to import directly into authenticated US execution. |

No unreviewed fork receives account keys or a means of submitting orders.
System calls to NEXUS are confined to local research with explicit
`tradePermission=false`. Original fork directories are unchanged.

## Candidate data/causal frontiers

| Frontier | Evidence required | Precise falsifiable hypothesis | Backtest guardrail |
| --- | --- | --- | --- |
| Index basis and spot leadership | Official time-aligned BRTI 1s samples plus exchange trade/book timestamps | Short-lived reference-market lead predicts BRTI changes before contracts update | Account for actual index construction, delays, and market-book timestamp |
| Perp leverage and liquidation cascades | Exchange event-by-event liquidation, open interest, funding and perpetual book/flows | Forced position unwinds predict transitory contract repricing, conditional on volatility | Use observed public liquidations, not guesses from large candles |
| Extreme-spread mean reversion | Timestamped **actual** Polymarket US L2 books and fills | Extreme contract spread dislocations revert with enough accessible depth | Bid/ask executable fill simulator, queue-risk and opportunity decay |
| Market-maker response latency | Both markets' synchronized L2 books with clock drift measurement | BTC public-reference shock precedes repricing by sufficient milliseconds | Model message/order latency, cancel-replace and rate limits |
| Regime switching | Completed trailing vol/volume/funding/spot–perp basis | Momentum works only in some independently defined regimes | Regime specified from past data, rolling prospective validation |
| Macro policy/culture/news | Licensed timestamped *publication* times, event calendar, announcement surprise | CPI/FOMC/regulation/ETF information adds incremental after-cost predictive value | Publication-latency as-of joins; no retrospective summary leaks |
| Whale/ETF/on-chain flow | Timestamped verified transfer/flow source with known reporting lag | Large flow shocks change short-window volatility distribution | Distinguish reported flow from truly available live observation |
| Maker vs taker route | Verified official US order type rules, account eligibility and fill/execution reports | Passive order queue yields better expected value than crossing | Model cancellations, queue priority, nonfill selection bias |
| Exit optimization | Per-second quotes/book/trades + user-visible fills and transaction costs | Target+stop+time exit dominates holding to settlement across independent weeks | No stop-fill guarantee; compare conditional distributions and downside |
| Portfolio of independent markets | Contract settlement rule graph plus risk/funding constraints | Diverse strategies improve capital efficiency without overexposure | Verify correlation, shared market risk and simultaneous window losses |

**Unproven claims prohibited:** "I detected manipulation", "liquidations
caused this candle", "political news caused BTC to rise", and "the bot
found a guaranteed arbitrage" require contemporaneous independent
evidence. OHLC alone does not prove motive or causality.

## Experiment priority and promotion decisions

1. **Price/fill truth first.** Obtain point-in-time US book depth,
   latency and independent observable fills in the paper engine.
   Historical display-price replays are not fill-calibrated.
2. **Predictive calibration second.** Accumulate registered 2m/5m/8m
   probability forecasts and official settlement outcomes; Brier and
   calibration versus market midpoint, by regime and day, then test
   only preregistered alternatives. No after-the-fact relabeling.
3. **Exit-reality third.** Compare early take-profit, loss stop,
   time exits, no-trade and hold-to-settlement in chronological windows,
   after full entry and exit fees. Stops may fill worse than target.
4. **Cross-data marginal utility fourth.** Add futures basis/flow,
   funding, liquidation and real-time news **only** after validating
   timestamp integrity and measurable improvement over quote-only
   market baseline. More variables can easily worsen out-of-sample EV.
5. **Account adapter after independent proof.** Authenticated
   Polymarket US account verification, official supported order
   preview, idempotent state machine, reconciliation, capital limits,
   compliance and rollback must each pass tests before any order path
   can be unlocked.

Prioritize expected after-cost return **and uncertainty/downside**, not
paper win rate or strategy leaderboard rank. When all candidates fail,
**ABSTAIN** is the correct capital decision.

**State of implementation:** the local paper engine and historical
research code can run and be reviewed now. These new data-frontier
hypotheses are a research backlog, **not** integrated or experimentally
validated historical predictors.
