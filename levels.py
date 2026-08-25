def build_long_levels(low, high, atr, support, rr_min):
    entry = (low + high) / 2
    if entry <= 0 or atr <= 0:
        return None
    sl = min(support if support and support < entry else entry - 1.5 * atr, entry - atr)
    risk = entry - sl
    if risk <= 0:
        return None
    tp1 = entry + risk * max(rr_min, 1.5)
    tp2 = entry + risk * max(rr_min + 0.8, 2.3)
    tp3 = entry + risk * max(rr_min + 1.8, 3.3)
    return {
        "entry_low": round(low, 2),
        "entry_high": round(high, 2),
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "rr_tp1": round((tp1 - entry) / risk, 2),
    }


def build_quote_long_levels(price, day_low=None, day_high=None, rr_min=1.5):
    """Risk levels for Free-plan quote-only signals.

    This deliberately does not pretend to be ATR/support analysis. The stop is
    derived from the current session range with a bounded percentage fallback.
    """
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    low = _num(day_low)
    high = _num(day_high)
    session_range = (high - low) if high and low and high > low > 0 else 0.0

    # Keep paper-trade risk bounded between 0.8% and 2.0% of entry.
    risk = max(price * 0.008, session_range * 0.50)
    risk = min(risk, price * 0.02)

    entry = price
    entry_low = price * 0.9975
    entry_high = price * 1.0025
    sl = entry - risk

    tp1 = entry + risk * max(rr_min, 1.5)
    tp2 = entry + risk * max(rr_min + 0.8, 2.3)
    tp3 = entry + risk * max(rr_min + 1.8, 3.3)

    return {
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "rr_tp1": round((tp1 - entry) / risk, 2),
    }
