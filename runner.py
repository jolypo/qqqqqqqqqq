import asyncio


class Scheduler:
    def __init__(self, settings, service):
        self.s = settings
        self.service = service

    async def run(self):
        """Never scans for new signals. It only monitors existing paper trades and scheduled reports."""
        print("[scheduler] started: monitor/report only; automatic signal discovery is OFF")
        while True:
            try:
                await self.service.scheduled_tasks()
            except Exception as exc:
                print(f"[scheduler] {exc!r}")
            await asyncio.sleep(max(60, self.s.scan_interval_seconds))
