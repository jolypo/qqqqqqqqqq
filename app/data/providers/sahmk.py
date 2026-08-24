import asyncio
import time
from datetime import date, datetime, timedelta

import httpx

from .base import DataProvider, Quote


class SahmkProvider(DataProvider):
    def __init__(self, api_key, base_url):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=20,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "TASI-KSA-Trading-Bot/1.0",
            },
        )
        self.max_retries = 2
        self.min_request_interval = 1.0
        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self._request_count = 0
        self._rate_limit_count = 0
        self._request_errors = 0
        self.quote_cache = {}
        self.quote_cache_ttl = 600

    async def close(self):
        await self.client.aclose()

    async def _rate_limit(self):
        async with self._request_lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                await asyncio.sleep(self._cooldown_until - now)
                now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_request_interval:
                await asyncio.sleep(self.min_request_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _get(self, path, params=None):
        for attempt in range(self.max_retries + 1):
            await self._rate_limit()
            try:
                response = await self.client.get(self.base_url + path, params=params)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                self._request_errors += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue
                raise exc

            if response.status_code == 429:
                self._rate_limit_count += 1
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(2 ** (attempt + 1), 30)
                wait = max(1.0, min(wait, 60.0))
                self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
                print(f"[SAHMK] 429 {path}; cooldown {wait:.1f}s")
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
                    continue
                raise httpx.HTTPStatusError("SAHMK rate limit exceeded", request=response.request, response=response)

            if response.status_code in (500, 502, 503, 504) and attempt < self.max_retries:
                self._request_errors += 1
                await asyncio.sleep(min(2 ** attempt, 15))
                continue

            response.raise_for_status()
            self._request_count += 1
            return response.json()

        raise RuntimeError("SAHMK request failed")

    def stats(self):
        return {
            "requests": self._request_count,
            "rate_limits": self._rate_limit_count,
            "errors": self._request_errors,
        }

    async def companies(self, market="TASI"):
        out = []
        offset = 0
        while True:
            payload = await self._get(
                "/companies/",
                {"market": market, "limit": 100, "offset": offset},
            )
            batch = payload.get("results", payload.get("companies", []))
            if not isinstance(batch, list):
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
            if offset > 2000:
                break
        return out

    async def quote(self, symbol):
        symbol = str(symbol)
        cached = self.quote_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < self.quote_cache_ttl:
            return cached[1]

        data = await self._get(f"/quote/{symbol}/", {"data_mode": "delayed"})
        updated_at = None
        if data.get("updated_at"):
            try:
                updated_at = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        quote = Quote(
            str(data.get("symbol", symbol)),
            data.get("name", "") or "",
            data.get("name_en", "") or "",
            float(data.get("price") or 0),
            float(data.get("change_percent") or 0),
            float(data.get("volume") or 0),
            float(data.get("value") or 0),
            float(data["bid"]) if data.get("bid") is not None else None,
            float(data["ask"]) if data.get("ask") is not None else None,
            updated_at,
            bool(data.get("is_delayed", True)),
            data,
        )
        self.quote_cache[symbol] = (time.monotonic(), quote)
        return quote

    async def market_summary(self):
        return await self._get("/market/summary/")

    async def historical(self, symbol, days=250):
        end = date.today()
        start = end - timedelta(days=max(days * 2, 365))
        return await self._get(
            f"/historical/{symbol}/",
            {"from": start.isoformat(), "to": end.isoformat(), "interval": "1d", "limit": 2000},
        )
