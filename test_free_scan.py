import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.data.providers.base import Quote
from app.service import TradingService


class FakeBots:
    def __init__(self):
        self.sent = []
        self.service = None
        self.signal = SimpleNamespace(get_me=self._get_me)

    def attach_service(self, service):
        self.service = service

    async def _get_me(self):
        return {"ok": True}

    async def send_signal(self, text):
        self.sent.append(text)

    async def send_profit(self, text):
        pass

    async def send_loss_for_trade(self, trade, price):
        pass

    async def send_near_sl(self, trade, price):
        pass

    async def send_market_close(self, text):
        pass

    async def send_report(self, text=None, image_path=None):
        pass


class FakeProvider:
    def __init__(self, now):
        self.now = now
        self.historical_called = False
        self.detail_requested = []

    async def companies(self, market="TASI"):
        return [
            {
                "symbol": str(2000 + i),
                "name": f"شركة {i}",
                "name_en": f"Company {i}",
                "sector": "Test",
                "security_type": "equity",
            }
            for i in range(60)
        ]

    async def market_summary(self):
        return {"change_percent": 1.0, "index": 12000}

    async def top_volume_quotes(self, limit=50, index="TASI"):
        return [
            Quote(
                symbol=str(2000 + i),
                name=f"شركة {i}",
                name_en=f"Company {i}",
                price=100.0,
                change_percent=1.5,
                volume=2_000_000 - i,
                value=0,
                updated_at=self.now,
                is_delayed=True,
                raw={"updated_at": self.now.isoformat()},
            )
            for i in range(limit)
        ]

    async def quotes(self, symbols):
        self.detail_requested = list(symbols)
        return {
            symbol: Quote(
                symbol=symbol,
                name=f"شركة {symbol}",
                name_en=f"Company {symbol}",
                price=100.0,
                change_percent=1.5,
                volume=2_000_000,
                value=100_000_000,
                bid=99.90,
                ask=100.10,
                updated_at=self.now,
                is_delayed=True,
                raw={
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "previous_close": 98.5,
                    "value": 100_000_000,
                    "liquidity": {"net_value": 10_000_000},
                    "updated_at": self.now.isoformat(),
                },
            )
            for symbol in symbols
        }

    async def quote(self, symbol):
        return (await self.quotes([symbol]))[symbol]

    async def historical(self, symbol, days=250):
        self.historical_called = True
        raise AssertionError("Free scan must not call historical")

    def stats(self):
        return {"daily_requests": 5, "daily_limit": 95, "rate_limits": 0, "errors": 0}


def make_settings(tmp_path):
    return SimpleNamespace(
        state_dir=str(tmp_path),
        timezone="Asia/Riyadh",
        market_open="10:00",
        market_close="15:00",
        allow_off_hours_scan=False,
        universe_refresh_seconds=21600,
        market_cache_seconds=600,
        manual_quotes_per_signal=50,
        detail_quotes_per_signal=5,
        min_score=75,
        min_probability=65,
        max_daily_signals=3,
        max_open_trades=5,
        max_risk_per_trade=0.01,
        data_max_delay_minutes=30,
        min_rr=1.5,
        allow_long=True,
        paper_mode=True,
        sahmk_plan="free",
        trade_monitor_quotes_per_cycle=1,
        trailing_stop_enabled=False,
        trailing_after_tp1_to_entry=True,
        trailing_after_tp2_atr=1.0,
        profit_alert_thresholds="2,5,10,15,20",
        near_sl_warning_pct=0.5,
        weekly_report_enabled=True,
        weekly_report_weekday=3,
        weekly_report_hour=15,
        weekly_report_minute=5,
        scan_interval_seconds=900,
    )


def test_free_scan_uses_active_50_and_no_historical(tmp_path):
    async def run():
        local = datetime(2026, 8, 25, 11, 0, tzinfo=ZoneInfo("Asia/Riyadh"))
        now_utc = local.astimezone(timezone.utc)
        provider = FakeProvider(now_utc)
        bots = FakeBots()
        service = TradingService(make_settings(tmp_path), provider, bots)
        service._local_now = lambda: local
        service._utc_now = lambda: now_utc

        result = await service.scan_once()

        assert "تم اكتشاف" in result
        assert len(provider.detail_requested) <= 5
        assert provider.historical_called is False
        assert len(bots.sent) == 1
        assert "Probability: غير موثقة" in bots.sent[0]
        assert "FREE_QUOTE_MOMENTUM" in bots.sent[0]

    asyncio.run(run())


def test_closed_market_does_not_consume_scan_api(tmp_path):
    async def run():
        local = datetime(2026, 8, 25, 4, 0, tzinfo=ZoneInfo("Asia/Riyadh"))
        now_utc = local.astimezone(timezone.utc)
        provider = FakeProvider(now_utc)
        bots = FakeBots()
        service = TradingService(make_settings(tmp_path), provider, bots)
        service._local_now = lambda: local
        service._utc_now = lambda: now_utc

        async def fail(*args, **kwargs):
            raise AssertionError("API must not be called while market is closed")

        provider.companies = fail
        provider.market_summary = fail
        provider.top_volume_quotes = fail

        result = await service.scan_once()
        assert "السوق السعودي مغلق" in result

    asyncio.run(run())
