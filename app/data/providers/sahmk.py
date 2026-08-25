import asyncio
import time
from datetime import date, datetime, timedelta

import httpx

from .base import DataProvider, Quote


class SahmkProvider(DataProvider):
    """
    SAHMK data provider.

    Supports:
    - Single quote: /quote/{symbol}/
    - Batch quotes: /quotes/ (max 50 symbols/request)
    - Companies
    - Market summary
    - Historical data

    The provider uses delayed market data.
    """

    BATCH_SIZE = 50

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

        # Retry configuration
        self.max_retries = 2

        # Keep requests separated to reduce rate-limit risk.
        self.min_request_interval = 1.0

        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()

        # Global cooldown after 429
        self._cooldown_until = 0.0

        # Statistics
        self._request_count = 0
        self._rate_limit_count = 0
        self._request_errors = 0

        # Cache
        self.quote_cache = {}

        # Cache lifetime in seconds
        self.quote_cache_ttl = 600

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    # ------------------------------------------------------------------
    # RATE LIMIT
    # ------------------------------------------------------------------

    async def _rate_limit(self):
        """
        Ensure requests are not sent too quickly.

        Also respects the global cooldown created after HTTP 429.
        """
        async with self._request_lock:
            now = time.monotonic()

            # Respect cooldown
            if now < self._cooldown_until:
                wait_time = self._cooldown_until - now

                print(
                    f"[SAHMK] cooldown active: "
                    f"waiting {wait_time:.1f}s"
                )

                await asyncio.sleep(wait_time)
                now = time.monotonic()

            # Minimum interval between requests
            elapsed = now - self._last_request_time

            if elapsed < self.min_request_interval:
                wait_time = self.min_request_interval - elapsed
                await asyncio.sleep(wait_time)

            self._last_request_time = time.monotonic()

    # ------------------------------------------------------------------
    # HTTP GET
    # ------------------------------------------------------------------

    async def _get(self, path, params=None):
        """
        Perform GET request with:
        - rate limiting
        - retry
        - exponential backoff
        - 429 handling
        - temporary server error handling
        """

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

                print(
                    f"[SAHMK] network error "
                    f"path={path} "
                    f"attempt={attempt + 1}: {exc}"
                )

                if attempt < self.max_retries:
                    wait = min(2 ** attempt, 10)

                    await asyncio.sleep(wait)

                    continue

                raise exc

            # ----------------------------------------------------------
            # RATE LIMIT
            # ----------------------------------------------------------

            if response.status_code == 429:

                self._rate_limit_count += 1

                retry_after = response.headers.get("Retry-After")

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

            # ----------------------------------------------------------
            # TEMPORARY SERVER ERRORS
            # ----------------------------------------------------------

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

            # ----------------------------------------------------------
            # NORMAL RESPONSE
            # ----------------------------------------------------------

            response.raise_for_status()

            self._request_count += 1

            return response.json()

        raise RuntimeError(
            "SAHMK request failed"
        )

    # ------------------------------------------------------------------
    # STATS
    # ------------------------------------------------------------------

    def stats(self):
        """
        Return provider statistics.
        """
        return {
            "requests": self._request_count,
            "rate_limits": self._rate_limit_count,
            "errors": self._request_errors,
        }

    # ------------------------------------------------------------------
    # COMPANIES
    # ------------------------------------------------------------------

    async def companies(self, market="TASI"):
        """
        Load all companies from SAHMK.

        Uses pagination with 100 companies per request.
        """

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

            # Safety limit
            if offset > 2000:
                break

        print(
            f"[SAHMK] companies loaded: {len(out)}"
        )

        return out

    # ------------------------------------------------------------------
    # SINGLE QUOTE
    # ------------------------------------------------------------------

    async def quote(self, symbol):
        """
        Get a single stock quote.

        Kept for cases where one specific symbol is needed.
        For scanning the entire market, use quotes().
        """

        symbol = str(symbol).strip()

        if not symbol:
            return None

        # --------------------------------------------------------------
        # CACHE
        # --------------------------------------------------------------

        cached = self.quote_cache.get(symbol)

        if cached:

            cached_time, cached_quote = cached

            if (
                time.monotonic() - cached_time
                < self.quote_cache_ttl
            ):
                return cached_quote

        # --------------------------------------------------------------
        # API
        # --------------------------------------------------------------

        data = await self._get(
            f"/quote/{symbol}/",
            {
                "data_mode": "delayed",
            },
        )

        quote = self._parse_quote(
            data,
            fallback_symbol=symbol,
        )

        if quote is not None:

            self.quote_cache[symbol] = (
                time.monotonic(),
                quote,
            )

        return quote

    # ------------------------------------------------------------------
    # BATCH QUOTES
    # ------------------------------------------------------------------

    async def quotes(
        self,
        symbols,
        data_mode="delayed",
    ):
        """
        Get multiple stock quotes using SAHMK batch endpoint.

        SAHMK batch limit:
            Maximum 50 symbols per request.

        Example:
            270 symbols
            -> 50
            -> 50
            -> 50
            -> 50
            -> 50
            -> 20

        Total:
            6 API requests.
        """

        # --------------------------------------------------------------
        # CLEAN SYMBOLS
        # --------------------------------------------------------------

        cleaned_symbols = []

        for symbol in symbols:

            symbol = str(symbol).strip()

            if symbol:
                cleaned_symbols.append(symbol)

        # Remove duplicates while preserving order
        cleaned_symbols = list(
            dict.fromkeys(cleaned_symbols)
        )

        if not cleaned_symbols:

            print(
                "[SAHMK] batch quotes: "
                "no symbols"
            )

            return []

        total = len(cleaned_symbols)

        results = []

        # --------------------------------------------------------------
        # SPLIT INTO BATCHES OF 50
        # --------------------------------------------------------------

        for start in range(
            0,
            total,
            self.BATCH_SIZE,
        ):

            batch = cleaned_symbols[
                start:start + self.BATCH_SIZE
            ]

            batch_start = start + 1
            batch_end = start + len(batch)

            print(
                f"[SAHMK] batch quotes "
                f"{batch_start}-{batch_end}/{total}"
            )

            try:

                payload = await self._get(
                    "/quotes/",
                    {
                        "symbols": ",".join(batch),
                        "data_mode": data_mode,
                    },
                )

            except Exception as exc:

                print(
                    f"[SAHMK] batch failed "
                    f"{batch_start}-{batch_end}/{total}: "
                    f"{exc}"
                )

                # Do not switch to 50 individual requests.
                # That would defeat the purpose of batch scanning.
                continue

            # ----------------------------------------------------------
            # PARSE RESPONSE
            # ----------------------------------------------------------

            quotes_data = payload.get(
                "quotes",
                payload.get(
                    "results",
                    [],
                ),
            )

            if not isinstance(
                quotes_data,
                list,
            ):

                print(
                    "[SAHMK] invalid batch response: "
                    "quotes/results is not a list"
                )

                continue

            for data in quotes_data:

                try:

                    quote = self._parse_quote(
                        data,
                        fallback_symbol="",
                    )

                    if quote is None:
                        continue

                    self.quote_cache[
                        quote.symbol
                    ] = (
                        time.monotonic(),
                        quote,
                    )

                    results.append(quote)

                except (
                    TypeError,
                    ValueError,
                ) as exc:

                    print(
                        "[SAHMK] invalid quote data: "
                        f"{exc}"
                    )

        print(
            f"[SAHMK] batch complete: "
            f"{len(results)}/{total} quotes"
        )

        return results

    # ------------------------------------------------------------------
    # QUOTE PARSER
    # ------------------------------------------------------------------

    def _parse_quote(
        self,
        data,
        fallback_symbol="",
    ):
        """
        Convert SAHMK response into Quote object.
        """

        if not isinstance(data, dict):
            return None

        symbol = str(
            data.get(
                "symbol",
                fallback_symbol,
            )
            or ""
        ).strip()

        if not symbol:
            return None

        # --------------------------------------------------------------
        # UPDATED AT
        # --------------------------------------------------------------

        updated_at = None

        raw_updated_at = data.get(
            "updated_at"
        )

        if raw_updated_at:

            try:

                updated_at = datetime.fromisoformat(
                    str(
                        raw_updated_at
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

        # --------------------------------------------------------------
        # NUMERIC VALUES
        # --------------------------------------------------------------

        def to_float(value, default=0.0):

            if value is None:
                return default

            try:
                return float(value)

            except (
                TypeError,
                ValueError,
            ):
                return default

        price = to_float(
            data.get("price")
        )

        change_percent = to_float(
            data.get("change_percent")
        )

        volume = to_float(
            data.get("volume")
        )

        value = to_float(
            data.get(
                "value",
                data.get(
                    "net_liquidity",
                    0,
                ),
            )
        )

        bid = (
            to_float(data.get("bid"))
            if data.get("bid") is not None
            else None
        )

        ask = (
            to_float(data.get("ask"))
            if data.get("ask") is not None
            else None
        )

        # --------------------------------------------------------------
        # QUOTE OBJECT
        # --------------------------------------------------------------

        return Quote(
            symbol,
            data.get(
                "name",
                "",
            )
            or "",
            data.get(
                "name_en",
                "",
            )
            or "",
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

    # ------------------------------------------------------------------
    # MARKET SUMMARY
    # ------------------------------------------------------------------

    async def market_summary(self):

        return await self._get(
            "/market/summary/"
        )

    # ------------------------------------------------------------------
    # HISTORICAL
    # ------------------------------------------------------------------

    async def historical(
        self,
        symbol,
        days=250,
    ):
        """
        Get historical daily data.
        """

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
