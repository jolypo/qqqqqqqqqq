import argparse
import asyncio
import os

import uvicorn

from app.config.settings import settings
from app.data.providers.sahmk import SahmkProvider
from app.telegram.bots import TelegramBots
from app.service import TradingService
from app.scheduler.runner import Scheduler
from app.web import app, configure


async def run_web():
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main(args):
    provider = SahmkProvider(
        settings.sahmk_api_key,
        settings.sahmk_base_url,
        min_request_interval=settings.sahmk_min_request_interval,
        local_daily_request_limit=settings.sahmk_local_daily_limit,
        timezone_name=settings.timezone,
    )
    bots = TelegramBots(settings)
    scheduler_task = None

    try:
        if args.test_telegram:
            await bots.test()
            print("[test] Telegram connection test passed")
            return

        if args.test_data:
            companies = await provider.companies("TASI")
            print("TASI companies:", len(companies))
            print("top volume:", len(await provider.top_volume(10, "TASI")))
            print("summary:", await provider.market_summary())
            return

        service = TradingService(settings, provider, bots)
        configure(service, bots)
        await bots.start_commands()
        print(f"[main] service + Telegram {bots.mode} started")

        scheduler_task = asyncio.create_task(Scheduler(settings, service).run())
        await run_web()
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        try:
            await bots.stop_commands()
        finally:
            await provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--test-data", action="store_true")
    asyncio.run(main(parser.parse_args()))
