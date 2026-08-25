from dataclasses import dataclass

from app.data.providers.base import Quote


@dataclass
class Candidate:
    quote: Quote
    score: float
    reasons: list[str]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fast_score(q: Quote, regime: str):
    """Fast score using fields available on the SAHMK Free quote endpoints.

    The score is a screening score, not a probability of profit.
    """
    score = 45.0
    reasons = []
    raw = q.raw or {}

    change = float(q.change_percent or 0)
    if 0.5 <= change <= 4.0:
        score += 12
        reasons.append("positive_momentum")
    elif 4.0 < change <= 7.0:
        score += 6
        reasons.append("extended_momentum")
    elif change < 0:
        score -= 10

    volume = float(q.volume or 0)
    if volume >= 1_000_000:
        score += 8
        reasons.append("high_volume")
    elif volume >= 250_000:
        score += 5
    elif volume > 0:
        score += 2

    value = float(q.value or 0)
    if value >= 50_000_000:
        score += 10
        reasons.append("high_value")
    elif value >= 10_000_000:
        score += 6
    elif value >= 2_000_000:
        score += 3

    if q.bid is not None and q.ask is not None and q.price > 0:
        spread = (q.ask - q.bid) / q.price * 100
        if spread <= 0.25:
            score += 10
            reasons.append("tight_spread")
        elif spread <= 0.50:
            score += 5
        elif spread > 1.0:
            score -= 15

    open_price = _num(raw.get("open"))
    high = _num(raw.get("high"))
    low = _num(raw.get("low"))

    if open_price and open_price > 0:
        if q.price >= open_price:
            score += 5
            reasons.append("above_open")
        else:
            score -= 5

    if high and low and high > low and q.price > 0:
        position = (q.price - low) / (high - low)
        if position >= 0.70:
            score += 5
            reasons.append("upper_session_range")
        elif position < 0.35:
            score -= 5

    liquidity = raw.get("liquidity") if isinstance(raw.get("liquidity"), dict) else {}
    net_value = _num(liquidity.get("net_value"))
    if net_value is not None:
        if net_value > 0:
            score += 5
            reasons.append("positive_net_liquidity")
        elif net_value < 0:
            score -= 5

    if regime == "BULLISH":
        score += 5
        reasons.append("bullish_tasi")
    elif regime == "BEARISH":
        score -= 10

    return Candidate(q, max(0.0, min(100.0, score)), reasons)
