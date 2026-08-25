import asyncio
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .base import DataProvider, Quote


class SahmkProvider(DataProvider):
    """SAHMK provider optimized for the Free plan.

    Free-plan facts used by this implementation:
    - Single quote endpoint is allowed.
    - Market volume ranking is allowed.
    - Historical OHLCV and bulk quotes are not used in Free mode.
    - Requests are throttled below the documented 10 requests/minute burst cap.
    """

    def __init__(
        self,
        api_key,
        base_url,
        min_request_interval=6.5,
        local_daily_request_limit=95,
        timezone_name="Asia/Riyadh",
    ):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "TASI-KSA-Trading-Bot/1.0",
            },
        )

        self.max_retries = 2
        self.min_request_interval = max(6.1, float(min_request_interval))
        self.local_daily_request_limit = max(1, min(int(local_daily_request_limit), 100))
        self.tz = ZoneInfo(timezone_name)

        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._cooldown_until = 0.0

        self._request_count = 0
        self._rate_limit_count = 0
        self._request_errors = 0
        self._daily_request_count = 0
        self._daily_request_date = self._today()
        self._rate_limit_remaining = None
        self._rate_limit_reset = None

        self.quote_cache = {}
        self.quote_cache_ttl = 600

    def _today(self):
        return datetime.now(self.tz).date()

    async def close(self):
        await self.client.aclose()

    def _reset_daily_counter_if_needed(self):
        today = self._today()
        if today != self._daily_request_date:
            self._daily_request_date = today
            self._daily_request_count = 0

    def _can_make_request(self):
        self._reset_daily_counter_if_needed()
        return self._daily_request_count < self.local_daily_request_limit

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

    def _capture_rate_headers(self, response):
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        try:
            self._rate_limit_remaining = int(remaining) if remaining is not None else self._rate_limit_remaining
        except (TypeError, ValueError):
            pass
        try:
            self._rate_limit_reset = int(reset) if reset is not None else self._rate_limit_reset
        except (TypeError, ValueError):
            pass

    async def _get(self, path, params=None):
        for attempt in range(self.max_retries + 1):
            if not self._can_make_request():
                raise RuntimeError("SAHMK local daily request safety limit reached")

            await self._rate_limit()

            try:
                response = await self.client.get(self.base_url + path, params=params)
                self._request_count += 1
                self._daily_request_count += 1
                self._capture_rate_headers(response)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                self._request_errors += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue
                raise exc

            if response.status_code == 403:
                self._request_errors += 1
                print(f"[SAHMK] 403 Forbidden: {path}")
                raise httpx.HTTPStatusError(
                    "SAHMK endpoint returned 403 Forbidden",
                    request=response.request,
                    response=response,
                )

            if response.status_code == 429:
                self._rate_limit_count += 1
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(2 ** (attempt + 1) * 3, 30)
                wait = max(6.5, min(wait, 60.0))
                self._cooldown_until = max(
                    self._cooldown_until,
                    time.monotonic() + wait,
                )
                print(f"[SAHMK] 429 {path}; cooldown {wait:.1f}s")
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
                    continue
                raise httpx.HTTPStatusError(
                    "SAHMK rate limit exceeded",
                    request=response.request,
                    response=response,
                )

            if response.status_code in (500, 502, 503, 504):
                self._request_errors += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** attempt, 15))
                    continue

            response.raise_for_status()
            return response.json()

        raise RuntimeError("SAHMK request failed")

    def stats(self):
        self._reset_daily_counter_if_needed()
        return {
            "requests": self._request_count,
            "daily_requests": self._daily_request_count,
            "daily_limit": self.local_daily_request_limit,
            "rate_limits": self._rate_limit_count,
            "errors": self._request_errors,
            "remaining": self._rate_limit_remaining,
            "reset": self._rate_limit_reset,
        }

    async def companies(self, market="TASI"):
        out = []
        offset = 0
        while True:
            payload = await self._get(
                "/companies/",
                {"market": market, "limit": 100, "offset": offset},
            )
            if isinstance(payload, list):
                batch = payload
            elif isinstance(payload, dict):
                batch = payload.get(
                    "results",
                    payload.get("companies", payload.get("data", [])),
                )
            else:
                batch = []

            if not isinstance(batch, list):
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
            if offset > 2000:
                break
        return out

    def _parse_updated_at(self, value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def quote_from_payload(self, data, fallback_symbol=None):
        if not isinstance(data, dict):
            return None

        symbol = str(data.get("symbol", fallback_symbol or "")).strip()
        if not symbol:
            return None

        price = self._float(data.get("price"))
        if price <= 0:
            return None

        value = self._float(
            data.get("value", data.get("trading_value", data.get("net_liquidity", 0)))
        )
        bid = self._float(data.get("bid"), None) if data.get("bid") is not None else None
        ask = self._float(data.get("ask"), None) if data.get("ask") is not None else None

        return Quote(
            symbol=symbol,
            name=data.get("name", "") or "",
            name_en=data.get("name_en", "") or "",
            price=price,
            change_percent=self._float(data.get("change_percent", data.get("change_pct", 0))),
            volume=self._float(data.get("volume")),
            value=value,
            bid=bid,
            ask=ask,
            updated_at=self._parse_updated_at(data.get("updated_at")),
            is_delayed=bool(data.get("is_delayed", True)),
            raw=data,
        )

    async def quote(self, symbol):
        symbol = str(symbol).strip()
        if not symbol:
            raise ValueError("Empty symbol")

        cached = self.quote_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < self.quote_cache_ttl:
            return cached[1]

        data = await self._get(
            f"/quote/{symbol}/",
            {"data_mode": "delayed"},
        )
        quote = self.quote_from_payload(data, symbol)
        if quote is None:
            raise ValueError(f"Invalid or empty quote response for {symbol}")

        self.quote_cache[symbol] = (time.monotonic(), quote)
        return quote

    async def quotes(self, symbols):
        """Free-plan fallback for a small set of detailed quotes.

        Do not use this to scan the entire market. The service screens active
        stocks through /market/volume/ first, then calls this only for a small
        number of finalists.
        """
        normalized = list(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))
        results = {}
        for symbol in normalized:
            try:
                results[symbol] = await self.quote(symbol)
            except Exception as exc:
                print(f"[SAHMK] quote {symbol} failed: {exc}")
        return results

    async def top_volume(self, limit=50, index="TASI"):
        limit = max(1, min(int(limit), 100))
        payload = await self._get(
            "/market/volume/",
            {"limit": limit, "index": index, "data_mode": "delayed"},
        )
        if isinstance(payload, dict):
            rows = payload.get("stocks", payload.get("results", payload.get("data", [])))
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        return rows if isinstance(rows, list) else []

    async def top_volume_quotes(self, limit=50, index="TASI"):
        rows = await self.top_volume(limit=limit, index=index)
        quotes = []
        for row in rows:
            quote = self.quote_from_payload(row)
            if quote is not None:
                quotes.append(quote)
        return quotes

    async def market_summary(self):
        return await self._get("/market/summary/")

    async def historical(self, symbol, days=250):
        """Starter+ compatibility path. Free mode does not call this method."""
        symbol = str(symbol).strip()
        end = date.today()
        start = end - timedelta(days=max(days * 2, 365))
        return await self._get(
            f"/historical/{symbol}/",
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "interval": "1d",
                "limit": 2000,
            },
        )
