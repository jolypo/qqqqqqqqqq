import asyncio
import time
from datetime import date, datetime, timedelta

import httpx

from .base import DataProvider, Quote


class SahmkProvider(DataProvider):
    def __init__(self, api_key, base_url):
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

        # لا نرسل الطلبات بسرعة حتى لا نضرب Rate Limit
        self.min_request_interval = 1.0

        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._cooldown_until = 0.0

        self._request_count = 0
        self._rate_limit_count = 0
        self._request_errors = 0

        # Cache
        self.quote_cache = {}

        # 10 دقائق
        self.quote_cache_ttl = 600

        # SAHMK Bulk Quotes
        self.bulk_quote_limit = 50

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
                await asyncio.sleep(
                    self.min_request_interval - elapsed
                )

            self._last_request_time = time.monotonic()

    async def _get(self, path, params=None):
        for attempt in range(self.max_retries + 1):

            await self._rate_limit()

            try:
                response = await self.client.get(
                    self.base_url + path,
                    params=params,
                )

            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
            ) as exc:

                self._request_errors += 1

                if attempt < self.max_retries:
                    await asyncio.sleep(
                        min(2 ** attempt, 10)
                    )
                    continue

                raise exc

            # Rate limit
            if response.status_code == 429:

                self._rate_limit_count += 1

                retry_after = response.headers.get(
                    "Retry-After"
                )

                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(
                        2 ** (attempt + 1),
                        30,
                    )

                wait = max(
                    1.0,
                    min(wait, 60.0),
                )

                self._cooldown_until = max(
                    self._cooldown_until,
                    time.monotonic() + wait,
                )

                print(
                    f"[SAHMK] 429 {path}; "
                    f"cooldown {wait:.1f}s"
                )

                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
                    continue

                raise httpx.HTTPStatusError(
                    "SAHMK rate limit exceeded",
                    request=response.request,
                    response=response,
                )

            # Server errors
            if response.status_code in (
                500,
                502,
                503,
                504,
            ):

                if attempt < self.max_retries:
                    self._request_errors += 1

                    await asyncio.sleep(
                        min(2 ** attempt, 15)
                    )

                    continue

            response.raise_for_status()

            self._request_count += 1

            return response.json()

        raise RuntimeError(
            "SAHMK request failed"
        )

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
                {
                    "market": market,
                    "limit": 100,
                    "offset": offset,
                },
            )

            batch = payload.get(
                "results",
                payload.get(
                    "companies",
                    [],
                ),
            )

            if not isinstance(batch, list):
                break

            out.extend(batch)

            if len(batch) < 100:
                break

            offset += 100

            if offset > 2000:
                break

        return out

    def _parse_quote(self, data, fallback_symbol=None):

        if not isinstance(data, dict):
            return None

        symbol = str(
            data.get(
                "symbol",
                fallback_symbol or "",
            )
        )

        updated_at = None

        if data.get("updated_at"):

            try:
                updated_at = datetime.fromisoformat(
                    str(
                        data["updated_at"]
                    ).replace(
                        "Z",
                        "+00:00",
                    )
                )

            except (
                ValueError,
                TypeError,
            ):
                pass

        try:
            price = float(
                data.get("price") or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            price = 0.0

        try:
            change_percent = float(
                data.get(
                    "change_percent"
                ) or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            change_percent = 0.0

        try:
            volume = float(
                data.get("volume") or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            volume = 0.0

        try:
            value = float(
                data.get("value") or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            value = 0.0

        try:
            bid = (
                float(data["bid"])
                if data.get("bid") is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            bid = None

        try:
            ask = (
                float(data["ask"])
                if data.get("ask") is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            ask = None

        return Quote(
            symbol,
            data.get("name", "") or "",
            data.get("name_en", "") or "",
            price,
            change_percent,
            volume,
            value,
            bid,
            ask,
            updated_at,
            bool(
                data.get(
                    "is_delayed",
                    True,
                )
            ),
            data,
        )

    async def quote(self, symbol):

        symbol = str(symbol)

        cached = self.quote_cache.get(
            symbol
        )

        if (
            cached
            and time.monotonic()
            - cached[0]
            < self.quote_cache_ttl
        ):
            return cached[1]

        data = await self._get(
            f"/quote/{symbol}/",
            {
                "data_mode": "delayed"
            },
        )

        quote = self._parse_quote(
            data,
            symbol,
        )

        if quote is None:
            raise ValueError(
                f"Invalid quote response for {symbol}"
            )

        self.quote_cache[symbol] = (
            time.monotonic(),
            quote,
        )

        return quote

    async def quotes(self, symbols):

        """
        جلب أسعار عدة أسهم باستخدام Bulk Quotes.

        SAHMK يسمح حتى 50 رمزًا في الطلب الواحد
        في endpoint /quotes/.

        مثال:

        symbols = [
            "1010",
            "1020",
            "1030",
            ...
        ]

        سيتم تقسيمها تلقائيًا إلى مجموعات
        بحد أقصى 50 رمزًا.
        """

        symbols = [
            str(symbol).strip()
            for symbol in symbols
            if symbol is not None
            and str(symbol).strip()
        ]

        # إزالة التكرار مع الحفاظ على الترتيب
        symbols = list(
            dict.fromkeys(symbols)
        )

        if not symbols:
            return {}

        results = {}

        # استخدم الـ cache أولًا
        remaining = []

        now = time.monotonic()

        for symbol in symbols:

            cached = self.quote_cache.get(
                symbol
            )

            if (
                cached
                and now - cached[0]
                < self.quote_cache_ttl
            ):

                results[symbol] = cached[1]

            else:
                remaining.append(symbol)

        if not remaining:
            return results

        # تقسيم إلى مجموعات 50
        chunks = [
            remaining[i:i + self.bulk_quote_limit]
            for i in range(
                0,
                len(remaining),
                self.bulk_quote_limit,
            )
        ]

        print(
            f"[SAHMK] bulk quotes: "
            f"{len(remaining)} symbols "
            f"-> {len(chunks)} requests"
        )

        for index, chunk in enumerate(
            chunks,
            start=1,
        ):

            print(
                f"[SAHMK] bulk request "
                f"{index}/{len(chunks)}: "
                f"{len(chunk)} symbols"
            )

            try:

                payload = await self._get(
                    "/quotes/",
                    {
                        "symbols": ",".join(
                            chunk
                        ),
                        "data_mode": "delayed",
                    },
                )

                # بعض APIs ترجع:
                # {"results": [...]}
                # وبعضها:
                # {"data": [...]}
                # وبعضها قائمة مباشرة.

                if isinstance(
                    payload,
                    list,
                ):
                    rows = payload

                elif isinstance(
                    payload,
                    dict,
                ):

                    rows = payload.get(
                        "results",
                        payload.get(
                            "data",
                            payload.get(
                                "quotes",
                                [],
                            ),
                        ),
                    )

                else:
                    rows = []

                if not isinstance(
                    rows,
                    list,
                ):
                    rows = []

                for row in rows:

                    quote = self._parse_quote(
                        row
                    )

                    if quote is None:
                        continue

                    symbol = quote.symbol

                    results[symbol] = quote

                    self.quote_cache[
                        symbol
                    ] = (
                        time.monotonic(),
                        quote,
                    )

            except Exception as exc:

                print(
                    f"[SAHMK] bulk request "
                    f"{index} failed: {exc}"
                )

                # لا نوقف الفحص بالكامل
                # بسبب فشل مجموعة واحدة.
                continue

        print(
            f"[SAHMK] bulk quotes complete: "
            f"{len(results)}/{len(symbols)}"
        )

        return results

    async def market_summary(self):

        return await self._get(
            "/market/summary/"
        )

    async def historical(
        self,
        symbol,
        days=250,
    ):

        end = date.today()

        start = end - timedelta(
            days=max(
                days * 2,
                365,
            )
        )

        return await self._get(
            f"/historical/{symbol}/",
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "interval": "1d",
                "limit": 2000,
            },
        )
