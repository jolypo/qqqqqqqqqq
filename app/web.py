from datetime import datetime, timezone
from fastapi import FastAPI

app = FastAPI(title="Saudi TASI Signal Bot")
_service = None


def configure(service):
    global _service
    _service = service


@app.get("/")
async def root():
    return {"service": "saudi-tasi-signal-bot", "status": "ok"}


@app.get("/health")
async def health():
    if _service is None:
        return {"status": "starting", "time": datetime.now(timezone.utc).isoformat()}
    state = _service.store.state()
    stats = _service.p.stats() if hasattr(_service.p, "stats") else {}
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "paper_mode": _service.s.paper_mode,
        "signal_discovery": "manual_only",
        "scheduler": "monitor_only",
        "universe": len(_service.universe),
        "open_trades": len(state["open_trades"]),
        "paused": state.get("paused", False),
        "last_scan": state["meta"].get("last_scan"),
        "last_universe_refresh": state["meta"].get("last_universe_refresh"),
        "sahmk_requests": stats.get("requests"),
        "sahmk_429": stats.get("rate_limits"),
    }
