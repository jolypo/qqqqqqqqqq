from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.probability.engine import ProbabilityEngine
from app.risk.levels import build_long_levels, build_quote_long_levels


@dataclass
class Signal:
    trade_id: str
    symbol: str
    name: str
    name_en: str
    direction: str
    entry_low: float
    entry_high: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr_tp1: float
    score: float
    probability: float
    probability_status: str
    probability_samples: int
    probability_bucket: str
    strategy: str
    market_regime: str
    sector: str
    discovered_at: str
    expected_tp1: str
    expected_tp2: str
    expected_tp3: str

    def to_dict(self):
        return asdict(self)


class SignalEngine:
    def __init__(self, settings, history):
        self.s = settings
        self.p = ProbabilityEngine(history)

    def _probability(self, strategy, regime, score, rr):
        return self.p.estimate(strategy, regime, score, rr)

    def _finish(self, candidate, regime, sector, levels, strategy, expected):
        probability, samples, status, bucket = self._probability(
            strategy, regime, candidate.score, levels["rr_tp1"]
        )

        # Never invent probability. If there is enough empirical history,
        # enforce MIN_PROBABILITY; otherwise permit a clearly UNVALIDATED
        # paper-trade signal so the system can accumulate its first samples.
        if status == "VALIDATED" and probability < self.s.min_probability:
            return None

        now = datetime.now(timezone.utc)
        trade_id = f"TASI-{now.strftime('%Y%m%d-%H%M%S')}-{candidate.quote.symbol}"
        return Signal(
            trade_id=trade_id,
            symbol=candidate.quote.symbol,
            name=candidate.quote.name,
            name_en=candidate.quote.name_en,
            direction="BUY",
            **levels,
            score=round(candidate.score, 2),
            probability=probability,
            probability_status=status,
            probability_samples=samples,
            probability_bucket=bucket,
            strategy=strategy,
            market_regime=regime,
            sector=sector or "Unknown",
            discovered_at=now.isoformat(),
            expected_tp1=expected[0],
            expected_tp2=expected[1],
            expected_tp3=expected[2],
        )

    def build(self, candidate, regime, sector, features):
        """Historical-analysis path for Starter+ plans."""
        if not features or not self.s.allow_long:
            return None
        if not (
            features["ema9"] > features["ema20"] > features["ema50"]
            and 50 <= features["rsi"] <= 72
            and features["relative_volume"] >= 1.1
            and features["macd"] >= features["macd_signal"]
            and features["close"] >= features["vwap20"]
        ):
            return None

        levels = build_long_levels(
            candidate.quote.price * 0.995,
            candidate.quote.price * 1.005,
            features["atr14"],
            features.get("support20"),
            self.s.min_rr,
        )
        if not levels or candidate.score < self.s.min_score:
            return None

        return self._finish(
            candidate,
            regime,
            sector,
            levels,
            "MOMENTUM_BREAKOUT",
            ("1–3 days", "1–2 weeks", "1–2 months"),
        )

    def build_free(self, candidate, regime, sector):
        """Quote-only paper signal for the SAHMK Free plan.

        Uses only fields actually returned by the single-quote endpoint. It does
        not claim EMA/RSI/MACD/ATR analysis when historical OHLCV is unavailable.
        """
        if not self.s.allow_long or candidate.score < self.s.min_score:
            return None
        if regime == "BEARISH":
            return None

        q = candidate.quote
        raw = q.raw or {}
        change = float(q.change_percent or 0)

        # Avoid weak moves and very extended chases.
        if not (0.5 <= change <= 5.0):
            return None

        def _num(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        open_price = _num(raw.get("open"))
        high = _num(raw.get("high"))
        low = _num(raw.get("low"))

        if open_price and open_price > 0 and q.price < open_price:
            return None

        if high and low and high > low:
            position = (q.price - low) / (high - low)
            if position < 0.55:
                return None

        if q.bid is not None and q.ask is not None and q.price > 0:
            spread = (q.ask - q.bid) / q.price * 100
            if spread > 0.75:
                return None

        liquidity = raw.get("liquidity") if isinstance(raw.get("liquidity"), dict) else {}
        net_value = _num(liquidity.get("net_value"))
        if net_value is not None and net_value < 0:
            return None

        levels = build_quote_long_levels(q.price, low, high, self.s.min_rr)
        if not levels:
            return None

        return self._finish(
            candidate,
            regime,
            sector,
            levels,
            "FREE_QUOTE_MOMENTUM",
            ("same day–2 sessions", "1–3 sessions", "2–5 sessions"),
        )
