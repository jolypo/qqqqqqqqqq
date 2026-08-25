from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="Saudi TASI Signal Bot")
_service = None
_bots = None


def configure(service, bots=None):
    global _service, _bots
    _service = service
    _bots = bots


@app.get("/")
async def root():
    return {"service": "saudi-tasi-signal-bot", "status": "ok"}


@app.head("/")
async def root_head():
    return Response(status_code=200)


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
        "scheduler": "monitor_only_when_awake",
        "market_open": _service.market_is_open(),
        "sahmk_plan": _service.s.sahmk_plan,
        "universe": len(_service.universe),
        "open_trades": len(state["open_trades"]),
        "paused": state.get("paused", False),
        "last_scan": state["meta"].get("last_scan"),
        "last_universe_refresh": state["meta"].get("last_universe_refresh"),
        "sahmk_daily_requests": stats.get("daily_requests"),
        "sahmk_local_limit": stats.get("daily_limit"),
        "sahmk_server_remaining": stats.get("remaining"),
        "sahmk_429": stats.get("rate_limits"),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if _bots is None:
        raise HTTPException(status_code=503, detail="Telegram is starting")
    payload = await request.json()
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    accepted = await _bots.process_webhook(payload, secret)
    if not accepted:
        raise HTTPException(status_code=403, detail="Invalid webhook request")
    return {"ok": True}
