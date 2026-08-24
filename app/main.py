import argparse
import asyncio
import os
import threading

import uvicorn

from app.config.settings import settings
from app.data.providers.sahmk import SahmkProvider
from app.telegram.bots import TelegramBots
from app.service import TradingService
from app.scheduler.runner import Scheduler
from app.web import app, configure


def web():
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        log_level="info",
    )


async def main(args):
    provider = SahmkProvider(settings.sahmk_api_key, settings.sahmk_base_url)
    bots = TelegramBots(settings)
    try:
        if args.test_telegram:
            await bots.test()
            print("[test] Telegram connection test passed")
            return

        if args.test_data:
            companies = await provider.companies("TASI")
            print("TASI companies:", len(companies))
            print("summary:", await provider.market_summary())
            return

        service = TradingService(settings, provider, bots)
        configure(service)
        threading.Thread(target=web, daemon=True).start()
        await bots.start_commands()
        print("[main] service + Telegram command polling started")
        await Scheduler(settings, service).run()
    finally:
        try:
            await bots.stop_commands()
        finally:
            await provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--test-data", action="store_true")
    asyncio.run(main(parser.parse_args()))
