from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.probability.engine import ProbabilityEngine
from app.risk.levels import build_long_levels


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

    def build(self, candidate, regime, sector, features):
        if not features or not self.s.allow_long:
            return None
        if not (
            features["ema9"] > features["ema20"] > features["ema50"]
            and features["rsi"] >= 50
            and features["rsi"] <= 72
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

        strategy = "MOMENTUM_BREAKOUT"
        probability, samples, status, bucket = self.p.estimate(
            strategy, regime, candidate.score, levels["rr_tp1"]
        )
        if status != "VALIDATED" or probability < self.s.min_probability:
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
            expected_tp1="1–3 days",
            expected_tp2="1–2 weeks",
            expected_tp3="1–2 months",
        )
