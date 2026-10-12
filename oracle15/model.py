"""Conservative, uncalibrated baseline model and executable-cost-aware decisions."""
from dataclasses import dataclass
import math
import statistics


@dataclass(frozen=True)
class Settings:
    paper_bankroll: float = 50.0
    max_ticket_usd: float = 2.50
    max_open_exposure_usd: float = 7.50
    max_daily_loss_usd: float = 5.0
    max_trades_per_day: int = 6
    min_edge: float = 0.065
    model_uncertainty_buffer: float = 0.035
    slippage_usd_per_share: float = 0.01
    max_spread: float = 0.07
    min_seconds_after_start: int = 90
    min_seconds_before_end: int = 120


@dataclass(frozen=True)
class Forecast:
    p_up: float
    sigma_per_minute: float
    latest_index: float
    effective_sample: int
    model_version: str = "lognormal-shrunk-uncalibrated-v0"


@dataclass(frozen=True)
class Candidate:
    side: str
    ask_equivalent: float
    yes_limit: float
    expected_net_edge: float
    price_depth: float


def forecast_up(window, candles, now):
    """GBM approximation, shrunk toward 50/50. Not a verified alpha model.

    This is a *forward* estimate using only candles completed at decision time.
    Index is BRTI from Polymarket US, never Binance/Chainlink.
    """
    remaining = window.end - int(now)
    if remaining <= 0:
        raise ValueError("expired window")
    if len(candles) < 45:
        raise ValueError("insufficient data")
    series = [c.close for c in candles]
    if min(series) <= 0:
        raise ValueError("invalid index history")
    returns = [math.log(b / a) for a, b in zip(series, series[1:])]
    # Floor protects against false certainty in unusually quiet samples.
    sigma = max(statistics.stdev(returns[-90:]), 0.00022)
    # No drift claimed. Closing settlement is a one-minute BRTI average,
    # so add a fixed estimation noise floor instead of treating last price as final.
    horizon_mins = max(remaining / 60.0, 1.0)
    log_ratio = math.log(series[-1] / window.target)
    z = log_ratio / (sigma * math.sqrt(horizon_mins + 0.40))
    p_raw = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    # Explicit uncertainty shrinkage; must be calibrated against future outcomes.
    p = min(0.95, max(0.05, 0.5 + (p_raw - 0.5) * 0.60))
    return Forecast(p, sigma, series[-1], min(len(returns), 90))


def taker_fee_per_share(price, coefficient):
    if not (0 < price < 1 and 0 < coefficient <= 0.2):
        raise ValueError("invalid fee formula inputs")
    return coefficient * price * (1 - price)


def candidate(window, book, forecast, settings):
    """BUY_LONG for UP, BUY_SHORT for DOWN (short YES on US).

    For a NO contract at implied price 1-bid, the executable instrument
    reference is YES best bid; never pass 1-bid as the YES limit price.
    Live price semantics still require separate preproduction validation.
    """
    if book.ask - book.bid > settings.max_spread:
        return None, "spread_too_wide"
    options = []
    up = book.ask
    down = 1.0 - book.bid
    for side, price, yes_limit, p, depth in [
        ("UP", up, book.ask, forecast.p_up, book.ask_qty),
        ("DOWN", down, book.bid, 1 - forecast.p_up, book.bid_qty),
    ]:
        fee = taker_fee_per_share(price, window.fee_coeff)
        edge = p - price - fee - settings.slippage_usd_per_share - settings.model_uncertainty_buffer
        options.append(Candidate(side, price, yes_limit, edge, depth))
    best = max(options, key=lambda o: o.expected_net_edge)
    if best.expected_net_edge < settings.min_edge:
        return None, "insufficient_after_cost_edge"
    return best, "candidate"
