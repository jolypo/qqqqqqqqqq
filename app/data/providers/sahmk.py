import asyncio
import time
from datetime import date, datetime, timedelta

import httpx

from .base import DataProvider, Quote


class SahmkProvider(DataProvider):
    """
    SAHMK data provider.

    Important:
    - Uses delayed data.
    - Does NOT use the /quotes/ bulk endpoint because it returned
      403 Forbidden in the deployed environment.
    - quotes() therefore falls back to individual /quote/{symbol}/
      requests.
    - Cache is used aggressively to reduce daily API usage.
    """

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

        # Retry only when it makes sense.
        self.max_retries = 2

        # Free plan protection.
        # One request approximately every second.
        self.min_request_interval = 1.0

        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._cooldown_until = 0.0

        self._request_count = 0
        self._rate_limit_count = 0
        self._request_errors = 0

        # ---------------------------------------------------------
        # Quote cache
        # ---------------------------------------------------------

        self.quote_cache = {}

        # Delayed data does not need to be requested repeatedly.
        # 10 minutes helps preserve the free-plan quota.
        self.quote_cache_ttl = 600

        # ---------------------------------------------------------
        # Daily/free-plan protection
        # ---------------------------------------------------------

        # This is NOT an exact server-side quota.
        # It is only a local safety cap.
        self.local_daily_request_limit = 90

        self._daily_request_count = 0
        self._daily_request_date = date.today()

        # ---------------------------------------------------------
        # Bulk endpoint intentionally disabled
        # ---------------------------------------------------------

        self.bulk_quotes_enabled = False

    async def close(self):
        await self.client.aclose()

    # =============================================================
    # DAILY COUNTER
    # =============================================================

    def _reset_daily_counter_if_needed(self):
        today = date.today()

        if today != self._daily_request_date:
            self._daily_request_date = today
            self._daily_request_count = 0

    def _can_make_request(self):
        self._reset_daily_counter_if_needed()

        return (
            self._daily_request_count
            < self.local_daily_request_limit
        )

    # =============================================================
    # RATE LIMIT
    # =============================================================

    async def _rate_limit(self):
        async with self._request_lock:
            now = time.monotonic()

            if now < self._cooldown_until:
                wait = self._cooldown_until - now

                print(
                    f"[SAHMK] cooldown active: "
                    f"{wait:.1f}s"
                )

                await asyncio.sleep(wait)

                now = time.monotonic()

            elapsed = now - self._last_request_time

            if elapsed < self.min_request_interval:
                await asyncio.sleep(
                    self.min_request_interval - elapsed
                )

            self._last_request_time = time.monotonic()

    # =============================================================
    # HTTP GET
    # =============================================================

    async def _get(self, path, params=None):
        """
        Make one API request.

        Returns decoded JSON.

        Raises on:
        - 403
        - 429 after retries
        - other HTTP errors
        """

        self._reset_daily_counter_if_needed()

        if not self._can_make_request():
            raise RuntimeError(
                "SAHMK local daily request safety limit reached"
            )

        for attempt in range(self.max_retries + 1):

            await self._rate_limit()

            try:
                response = await self.client.get(
                    self.base_url + path,
                    params=params,
                )

                # Count every actual HTTP request.
                self._request_count += 1
                self._daily_request_count += 1

            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
            ) as exc:

                self._request_errors += 1

                if attempt < self.max_retries:
                    wait = min(
                        2 ** attempt,
                        10,
                    )

                    print(
                        f"[SAHMK] network error "
                        f"{path}; retry in {wait}s"
                    )

                    await asyncio.sleep(wait)
                    continue

                raise exc

            # -----------------------------------------------------
            # 403
            # -----------------------------------------------------

            if response.status_code == 403:
                self._request_errors += 1

                print(
                    f"[SAHMK] 403 Forbidden: {path}"
                )

                # Do NOT retry 403.
                #
                # Retrying a permission/endpoint problem only
                # wastes the free API quota.
                raise httpx.HTTPStatusError(
                    "SAHMK endpoint returned 403 Forbidden",
                    request=response.request,
                    response=response,
                )

            # -----------------------------------------------------
            # 429
            # -----------------------------------------------------

            if response.status_code == 429:

                self._rate_limit_count += 1

                retry_after = response.headers.get(
                    "Retry-After"
                )

                try:
                    wait = float(retry_after)
                except (
                    TypeError,
                    ValueError,
                ):
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

            # -----------------------------------------------------
            # Server errors
            # -----------------------------------------------------

            if response.status_code in (
                500,
                502,
                503,
                504,
            ):

                self._request_errors += 1

                if attempt < self.max_retries:

                    wait = min(
                        2 ** attempt,
                        15,
                    )

                    print(
                        f"[SAHMK] server error "
                        f"{response.status_code} "
                        f"{path}; retry in {wait}s"
                    )

                    await asyncio.sleep(wait)
                    continue

            response.raise_for_status()

            return response.json()

        raise RuntimeError(
            "SAHMK request failed"
        )

    # =============================================================
    # STATS
    # =============================================================

    def stats(self):
        self._reset_daily_counter_if_needed()

        return {
            "requests": self._request_count,
            "daily_requests": self._daily_request_count,
            "daily_limit": self.local_daily_request_limit,
            "rate_limits": self._rate_limit_count,
            "errors": self._request_errors,
        }

    # =============================================================
    # COMPANIES
    # =============================================================

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

            if isinstance(payload, list):
                batch = payload

            elif isinstance(payload, dict):
                batch = payload.get(
                    "results",
                    payload.get(
                        "companies",
                        payload.get(
                            "data",
                            [],
                        ),
                    ),
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

    # =============================================================
    # QUOTE PARSER
    # =============================================================

    def _parse_quote(
        self,
        data,
        fallback_symbol=None,
    ):

        if not isinstance(data, dict):
            return None

        symbol = str(
            data.get(
                "symbol",
                fallback_symbol or "",
            )
        ).strip()

        if not symbol:
            return None

        # ---------------------------------------------------------
        # updated_at
        # ---------------------------------------------------------

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
                updated_at = None

        # ---------------------------------------------------------
        # price
        # ---------------------------------------------------------

        try:
            price = float(
                data.get("price") or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            price = 0.0

        # Invalid quote.
        if price <= 0:
            return None

        # ---------------------------------------------------------
        # change
        # ---------------------------------------------------------

        try:
            change_percent = float(
                data.get(
                    "change_percent",
                    data.get(
                        "change_pct",
                        0,
                    ),
                )
                or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            change_percent = 0.0

        # ---------------------------------------------------------
        # volume
        # ---------------------------------------------------------

        try:
            volume = float(
                data.get("volume") or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            volume = 0.0

        # ---------------------------------------------------------
        # traded value
        # ---------------------------------------------------------

        try:
            value = float(
                data.get(
                    "value",
                    data.get(
                        "trading_value",
                        0,
                    ),
                )
                or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            value = 0.0

        # ---------------------------------------------------------
        # bid
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # ask
        # ---------------------------------------------------------

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

    # =============================================================
    # SINGLE QUOTE
    # =============================================================

    async def quote(self, symbol):

        symbol = str(symbol).strip()

        if not symbol:
            raise ValueError(
                "Empty symbol"
            )

        # ---------------------------------------------------------
        # CACHE
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # API
        # ---------------------------------------------------------

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
                f"Invalid or empty quote response for {symbol}"
            )

        # ---------------------------------------------------------
        # CACHE
        # ---------------------------------------------------------

        self.quote_cache[symbol] = (
            time.monotonic(),
            quote,
        )

        return quote

    # =============================================================
    # MULTIPLE QUOTES
    # =============================================================

    async def quotes(self, symbols):
        """
        Fetch multiple quotes.

        IMPORTANT:
        The /quotes/ bulk endpoint is disabled because the
        deployed SAHMK environment returned 403 Forbidden.

        Therefore this method performs individual quote requests.

        Cache is checked first.

        Example:
            symbols = ["1010", "1120", "2010"]

        Maximum requested symbols can be controlled by the caller.
        """

        # ---------------------------------------------------------
        # Normalize
        # ---------------------------------------------------------

        normalized = []

        for symbol in symbols:

            if symbol is None:
                continue

            symbol = str(symbol).strip()

            if not symbol:
                continue

            if symbol not in normalized:
                normalized.append(symbol)

        if not normalized:
            return {}

        # ---------------------------------------------------------
        # Cache first
        # ---------------------------------------------------------

        results = {}

        remaining = []

        now = time.monotonic()

        for symbol in normalized:

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

        print(
            f"[SAHMK] quote scan: "
            f"requested={len(normalized)} "
            f"cached={len(results)} "
            f"api_needed={len(remaining)}"
        )

        # ---------------------------------------------------------
        # Nothing else needed
        # ---------------------------------------------------------

        if not remaining:

            print(
                f"[SAHMK] quote scan complete: "
                f"{len(results)}/{len(normalized)}"
            )

            return results

        # ---------------------------------------------------------
        # Local daily protection
        # ---------------------------------------------------------

        self._reset_daily_counter_if_needed()

        available = max(
            0,
            self.local_daily_request_limit
            - self._daily_request_count,
        )

        if available <= 0:

            print(
                "[SAHMK] daily local safety limit reached; "
                "no more quote requests"
            )

            return results

        # Do not request more than our remaining local budget.
        remaining = remaining[:available]

        # ---------------------------------------------------------
        # Sequential requests
        # ---------------------------------------------------------
        #
        # This intentionally does NOT use asyncio.gather().
        #
        # Why?
        # Because 50 simultaneous requests would be exactly the
        # opposite of what we want on the free SAHMK plan.
        #

        success = 0
        failed = 0

        for index, symbol in enumerate(
            remaining,
            start=1,
        ):

            try:

                quote = await self.quote(symbol)

                results[symbol] = quote

                success += 1

                print(
                    f"[SAHMK] quote "
                    f"{index}/{len(remaining)} "
                    f"{symbol}: OK"
                )

            except Exception as exc:

                failed += 1

                print(
                    f"[SAHMK] quote "
                    f"{index}/{len(remaining)} "
                    f"{symbol}: FAILED - {exc}"
                )

        print(
            f"[SAHMK] quote scan complete: "
            f"success={success} "
            f"failed={failed} "
            f"total={len(normalized)} "
            f"cached={len(results) - success}"
        )

        return results

    # =============================================================
    # MARKET SUMMARY
    # =============================================================

    async def market_summary(self):

        return await self._get(
            "/market/summary/"
        )

    # =============================================================
    # HISTORICAL DATA
    # =============================================================

    async def historical(
        self,
        symbol,
        days=250,
    ):

        symbol = str(symbol).strip()

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
