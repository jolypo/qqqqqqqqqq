class ProbabilityEngine:
    """Empirical probability from closed paper trades only.

    A probability is considered validated only after the configured minimum
    number of historical outcomes in the same bucket. This avoids inventing
    an AI confidence percentage from insufficient data.
    """

    def __init__(self, history, minimum_samples=30):
        self.history = history
        self.minimum_samples = minimum_samples

    def bucket(self, strategy, regime, score, rr):
        return f"{strategy}|{regime}|{int(score // 5)}|{round(rr, 1)}"

    def estimate(self, strategy, regime, score, rr):
        bucket = self.bucket(strategy, regime, score, rr)
        wins = sum(1 for t in self.history if t.get("probability_bucket") == bucket and t.get("result") == "WIN")
        losses = sum(1 for t in self.history if t.get("probability_bucket") == bucket and t.get("result") == "LOSS")
        samples = wins + losses
        if samples < self.minimum_samples:
            return 0.0, samples, "UNVALIDATED", bucket
        # Laplace smoothing prevents 0%/100% from tiny edge cases.
        probability = (wins + 1) / (samples + 2) * 100
        return round(probability, 2), samples, "VALIDATED", bucket
