import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.data.universe import normalize_universe
from app.market.regime import classify_tasi
from app.scanner.screener import fast_score
from app.indicators.technical import latest_features
from app.signal_engine.engine import SignalEngine
from app.telegram.messages import (
    near_sl_message,
    profit_message,
    signal_message,
    tp_message,
)
from app.database.json_store import JsonStore
from app.trades.manager import TradeManager
from app.reports.weekly import report as build_weekly_report


class TradingService:
    """
    Core application service.

    New-signal discovery is MANUAL only via /signal.

    Scheduler:
        - monitors open paper trades
        - sends market-close message
        - sends weekly report
        - NEVER creates new signals

    Manual /signal:
        - scans a limited batch of TASI stocks
        - uses SAHMK bulk quotes
        - default batch size is 50
        - continues from the previous cursor
    """

    def __init__(self, settings, provider, bots):
        self.s = settings
        self.p = provider
        self.b = bots

        self.store = JsonStore(settings.state_dir)
        self.trade_manager = TradeManager(self.store, settings)

        self.universe = []

        self.last_refresh = None
        self.last_scan = None
        self.last_monitor = None

        self.last_market_summary = None
        self.last_market_summary_at = None

        self.scan_cursor = 0
        self.monitor_cursor = 0

        self.scan_lock = asyncio.Lock()
        self.monitor_lock = asyncio.Lock()

        self.last_report_key = None
        self.last_market_close_key = None

        self.tz = ZoneInfo(self.s.timezone)

        self.b.attach_service(self)

    # =========================================================
    # TIME
    # =========================================================

    def _utc_now(self):
        return datetime.now(timezone.utc)

    def _local_now(self):
        return self._utc_now().astimezone(self.tz)

    # =========================================================
    # UNIVERSE
    # =========================================================

    async def refresh(self):
        self.universe = normalize_universe(
            await self.p.companies("TASI")
        )

        self.last_refresh = self._utc_now()

        if self.universe:
            self.scan_cursor = min(
                self.scan_cursor,
                max(0, len(self.universe) - 1),
            )
        else:
            self.scan_cursor = 0

        state = self.store.state()

        state["meta"]["last_universe_refresh"] = (
            self.last_refresh.isoformat()
        )

        state["meta"]["universe_size"] = len(
            self.universe
        )

        self.store.save_state(state)

        print(
            f"[universe] {len(self.universe)} companies"
        )

    # =========================================================
    # STATE
    # =========================================================

    def is_paused(self):
        return bool(
            self.store.state().get(
                "paused",
                False,
            )
        )

    def set_paused(self, paused):
        state = self.store.state()

        state["paused"] = bool(paused)

        state["meta"]["paused_at"] = (
            self._utc_now().isoformat()
        )

        self.store.save_state(state)

    def can_send(self):
        state = self.store.state()

        today = (
            self._local_now()
            .date()
            .isoformat()
        )

        return (
            not state.get("paused", False)
            and len(state["open_trades"])
            < self.s.max_open_trades
            and state["daily_signals"].get(
                today,
                0,
            )
            < self.s.max_daily_signals
            and self.s.paper_mode
        )

    # =========================================================
    # CURSOR
    # =========================================================

    def _next_batch(
        self,
        size,
        cursor_name,
    ):
        """
        Return the next batch from the universe.

        Example with 270 stocks and batch size 50:

            1st /signal -> 1-50
            2nd /signal -> 51-100
            3rd /signal -> 101-150
            4th /signal -> 151-200
            5th /signal -> 201-250
            6th /signal -> 251-270
            7th /signal -> starts again

        """

        if not self.universe:
            return []

        total = len(self.universe)

        size = min(
            max(1, int(size)),
            total,
        )

        cursor = getattr(
            self,
            cursor_name,
        )

        end = cursor + size

        if end <= total:
            batch = self.universe[
                cursor:end
            ]
        else:
            batch = (
                self.universe[cursor:]
                + self.universe[: end - total]
            )

        setattr(
            self,
            cursor_name,
            end % total,
        )

        return batch

    # =========================================================
    # MARKET
    # =========================================================

    async def _market(self, force=False):
        now = self._utc_now()

        if (
            not force
            and self.last_market_summary is not None
            and self.last_market_summary_at
        ):
            age = (
                now
                - self.last_market_summary_at
            ).total_seconds()

            if (
                age
                < self.s.market_cache_seconds
            ):
                return self.last_market_summary

        try:
            data = await self.p.market_summary()

            self.last_market_summary = data
            self.last_market_summary_at = now

            return data

        except Exception as exc:
            print(
                f"[market] summary failed: {exc}"
            )
            return None

    # =========================================================
    # HISTORICAL DATA
    # =========================================================

    @staticmethod
    def _rows_to_df(payload):
        if not isinstance(
            payload,
            dict,
        ):
            return None

        rows = payload.get(
            "data",
            payload.get(
                "results",
                payload.get(
                    "historical",
                    [],
                ),
            ),
        )

        if (
            not isinstance(rows, list)
            or len(rows) < 60
        ):
            return None

        df = pd.DataFrame(rows)

        rename_map = {}

        for column in df.columns:
            key = str(column).lower()

            if key in ("o", "open"):
                rename_map[column] = "open"

            elif key in ("h", "high"):
                rename_map[column] = "high"

            elif key in ("l", "low"):
                rename_map[column] = "low"

            elif key in ("c", "close"):
                rename_map[column] = "close"

            elif key in ("v", "volume"):
                rename_map[column] = "volume"

        df = df.rename(
            columns=rename_map
        )

        required = {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }

        if not required.issubset(
            df.columns
        ):
            return None

        return df

    # =========================================================
    # MANUAL SIGNAL SCAN
    # =========================================================

    async def scan_once(
        self,
        source="telegram",
    ):
        """
        Manual signal discovery.

        IMPORTANT:
        This function uses the provider's bulk quotes()
        instead of calling quote() individually.

        Example:
            manual_quotes_per_signal = 50

        Result:
            50 stocks are requested through the bulk endpoint,
            instead of 50 individual quote requests.
        """

        if self.scan_lock.locked():
            return (
                "⏳ يوجد فحص يدوي جارٍ حاليًا."
            )

        async with self.scan_lock:

            self.last_scan = self._utc_now()

            state = self.store.state()

            state["meta"]["last_scan"] = (
                self.last_scan.isoformat()
            )

            state["meta"]["last_scan_source"] = (
                source
            )

            self.store.save_state(state)

            # -------------------------------------------------
            # PAUSE
            # -------------------------------------------------

            if self.is_paused():
                return (
                    "⏸️ النظام متوقف مؤقتًا. "
                    "استخدم /resume أولًا."
                )

            # -------------------------------------------------
            # PAPER MODE
            # -------------------------------------------------

            if not self.s.paper_mode:
                return (
                    "🛑 PAPER_MODE غير مفعّل؛ "
                    "تم منع إنشاء الصفقة حفاظًا على السلامة."
                )

            # -------------------------------------------------
            # DAILY / OPEN TRADE LIMITS
            # -------------------------------------------------

            if not self.can_send():
                return (
                    "ℹ️ لا توجد إشارة جديدة: "
                    "تم بلوغ حد الصفقات أو الإشارات اليومية."
                )

            # -------------------------------------------------
            # REFRESH UNIVERSE
            # -------------------------------------------------

            if (
                not self.universe
                or not self.last_refresh
                or (
                    self._utc_now()
                    - self.last_refresh
                ).total_seconds()
                > self.s.universe_refresh_seconds
            ):
                try:
                    await self.refresh()

                except Exception as exc:
                    print(
                        f"[universe] refresh failed: {exc}"
                    )

                    return (
                        "⚠️ تعذر تحديث قائمة "
                        "أسهم TASI حاليًا."
                    )

            if not self.universe:
                return (
                    "⚠️ قائمة أسهم TASI فارغة."
                )

            # -------------------------------------------------
            # MARKET SUMMARY
            # -------------------------------------------------

            market_data = await self._market()

            if market_data is None:
                return (
                    "⚠️ بيانات السوق غير متاحة حاليًا."
                )

            regime = classify_tasi(
                market_data
            )

            # -------------------------------------------------
            # GET NEXT 50 STOCKS
            # -------------------------------------------------

            batch_size = max(
                1,
                int(
                    self.s.manual_quotes_per_signal
                ),
            )

            batch = self._next_batch(
                batch_size,
                "scan_cursor",
            )

            symbols = []

            for item in batch:
                symbol = item.get("symbol")

                if symbol:
                    symbols.append(
                        str(symbol).strip()
                    )

            symbols = list(
                dict.fromkeys(symbols)
            )

            print(
                f"[manual-scan] "
                f"source={source} "
                f"quotes={len(symbols)} "
                f"cursor={self.scan_cursor}/"
                f"{len(self.universe)}"
            )

            if not symbols:
                return (
                    "⚠️ لم يتم العثور على رموز "
                    "صالحة للفحص."
                )

            # -------------------------------------------------
            # BULK QUOTES
            # -------------------------------------------------

            try:
                quotes = await self.p.quotes(
                    symbols
                )

            except Exception as exc:
                print(
                    f"[manual-scan] bulk quotes failed: "
                    f"{exc}"
                )

                return (
                    f"⚠️ تعذر جلب بيانات "
                    f"{len(symbols)} سهمًا من SAHMK حاليًا."
                )

            print(
                f"[manual-scan] "
                f"bulk quotes returned "
                f"{len(quotes)}/{len(symbols)}"
            )

            # -------------------------------------------------
            # FAST SCREEN
            # -------------------------------------------------

            candidates = []

            for item in batch:

                symbol = item.get("symbol")

                if not symbol:
                    continue

                symbol = str(symbol).strip()

                quote = quotes.get(symbol)

                if quote is None:
                    print(
                        f"[quote] {symbol}: "
                        "no bulk quote returned"
                    )
                    continue

                # ---------------------------------------------
                # DATA AGE CHECK
                # ---------------------------------------------

                if quote.updated_at:

                    try:
                        updated_at = quote.updated_at

                        # إذا كان التاريخ بدون timezone
                        if (
                            updated_at.tzinfo
                            is None
                        ):
                            updated_at = updated_at.replace(
                                tzinfo=timezone.utc
                            )

                        age = (
                            self._utc_now()
                            - updated_at
                        ).total_seconds() / 60

                        if (
                            age
                            > self.s.data_max_delay_minutes
                        ):
                            print(
                                f"[quote] {symbol} "
                                f"skipped: stale "
                                f"{age:.1f}m"
                            )
                            continue

                    except Exception as exc:
                        print(
                            f"[quote] {symbol} "
                            f"age check failed: {exc}"
                        )
                        continue

                # ---------------------------------------------
                # FAST SCORE
                # ---------------------------------------------

                try:
                    candidate = fast_score(
                        quote,
                        regime,
                    )

                    if (
                        candidate.score
                        >= max(
                            55,
                            self.s.min_score - 10,
                        )
                    ):
                        candidates.append(
                            (
                                candidate,
                                item,
                            )
                        )

                except Exception as exc:
                    print(
                        f"[score] {symbol} "
                        f"failed: {exc}"
                    )

            # -------------------------------------------------
            # NO CANDIDATES
            # -------------------------------------------------

            if not candidates:

                return (
                    f"🔎 انتهى الفحص اليدوي.\n"
                    f"📊 تم فحص {len(symbols)} سهمًا "
                    f"من أصل {len(self.universe)}.\n\n"
                    "لم تظهر فرصة أولية تستحق "
                    "التحليل العميق."
                )

            # -------------------------------------------------
            # SORT BEST CANDIDATES
            # -------------------------------------------------

            candidates.sort(
                key=lambda x: x[0].score,
                reverse=True,
            )

            engine = SignalEngine(
                self.s,
                self.store.history(),
            )

            # -------------------------------------------------
            # DEEP ANALYSIS
            # -------------------------------------------------

            for candidate, item in candidates[
                : min(3, len(candidates))
            ]:

                symbol = candidate.quote.symbol

                try:

                    print(
                        f"[analysis] "
                        f"{symbol} "
                        f"starting historical analysis"
                    )

                    historical = (
                        await self.p.historical(
                            symbol,
                            250,
                        )
                    )

                    df = self._rows_to_df(
                        historical
                    )

                    if df is None:
                        print(
                            f"[history] {symbol}: "
                            "insufficient OHLCV"
                        )
                        continue

                    features = latest_features(
                        df
                    )

                    signal = engine.build(
                        candidate,
                        regime,
                        item.get(
                            "sector",
                            "",
                        ),
                        features,
                    )

                    if not signal:
                        print(
                            f"[analysis] {symbol}: "
                            "signal rejected"
                        )
                        continue

                    # -----------------------------------------
                    # ADD PAPER TRADE
                    # -----------------------------------------

                    if self.trade_manager.add(
                        signal
                    ):

                        state = self.store.state()

                        day = (
                            self._local_now()
                            .date()
                            .isoformat()
                        )

                        state[
                            "daily_signals"
                        ][day] = (
                            state[
                                "daily_signals"
                            ].get(day, 0)
                            + 1
                        )

                        self.store.save_state(
                            state
                        )

                        # -------------------------------------
                        # SEND SIGNAL
                        # -------------------------------------

                        await self.b.send_signal(
                            signal_message(
                                signal.to_dict()
                            )
                        )

                        print(
                            f"[signal] sent {symbol}"
                        )

                        return (
                            "✅ تم اكتشاف وإرسال "
                            "إشارة جديدة:\n"
                            f"📌 {signal.name} "
                            f"({signal.symbol})\n"
                            f"📊 Score: "
                            f"{signal.score:.1f}\n"
                            f"🎯 Probability: "
                            f"{signal.probability:.1f}%\n"
                            f"📡 SAHMK delayed\n"
                            f"🔎 تم فحص "
                            f"{len(symbols)} سهمًا "
                            "في هذه الجولة."
                        )

                except Exception as exc:
                    print(
                        f"[analysis] {symbol} "
                        f"failed: {exc}"
                    )

            # -------------------------------------------------
            # NO FINAL SIGNAL
            # -------------------------------------------------

            return (
                f"🔎 اكتمل الفحص اليدوي.\n"
                f"📊 تم فحص {len(symbols)} سهمًا "
                f"من أصل {len(self.universe)}.\n\n"
                "لم توجد صفقة مستوفية "
                "لجميع الشروط."
            )

    # =========================================================
    # TRADE MONITOR
    # =========================================================

    async def monitor_once(self):
        """
        Monitor open paper trades only.

        NEVER creates new trades.
        """

        if self.monitor_lock.locked():
            return

        async with self.monitor_lock:

            self.last_monitor = (
                self._utc_now()
            )

            state = self.store.state()

            if not state["open_trades"]:
                return

            batch_size = max(
                1,
                int(
                    self.s.trade_monitor_quotes_per_cycle
                ),
            )

            trades = state["open_trades"]

            selected = []

            total = len(trades)

            start = (
                self.monitor_cursor
                % total
            )

            for i in range(
                min(batch_size, total)
            ):
                selected.append(
                    trades[
                        (start + i) % total
                    ]
                )

            self.monitor_cursor = (
                start + len(selected)
            ) % total

            for trade in selected:

                symbol = trade["symbol"]

                try:

                    quote = await self.p.quote(
                        symbol
                    )

                    updated, events = (
                        self.trade_manager.update(
                            symbol,
                            quote.price,
                        )
                    )

                    if not updated:
                        continue

                    # -----------------------------------------
                    # TRADE EVENTS
                    # -----------------------------------------

                    for event in events:

                        if event == "CLOSE_TP3":

                            await self.b.send_profit(
                                tp_message(
                                    updated,
                                    "TP3",
                                    quote.price,
                                )
                            )

                        elif event == "SL":

                            await self.b.send_loss_for_trade(
                                updated,
                                quote.price,
                            )

                        elif event in {
                            "TP1",
                            "TP2",
                        }:

                            await self.b.send_profit(
                                tp_message(
                                    updated,
                                    event,
                                    quote.price,
                                )
                            )

                    # -----------------------------------------
                    # PROFIT ALERTS
                    # -----------------------------------------

                    pct = (
                        (
                            quote.price
                            - float(
                                updated["entry"]
                            )
                        )
                        / float(
                            updated["entry"]
                        )
                        * 100
                    )

                    sent = set(
                        updated.get(
                            "profit_alerts_sent",
                            [],
                        )
                    )

                    try:

                        thresholds = [
                            float(x.strip())
                            for x in (
                                self.s.profit_alert_thresholds
                                .split(",")
                            )
                            if x.strip()
                        ]

                    except ValueError:

                        thresholds = [
                            2,
                            5,
                            10,
                            15,
                            20,
                        ]

                    for threshold in thresholds:

                        if (
                            pct >= threshold
                            and threshold
                            not in sent
                        ):

                            await self.b.send_profit(
                                profit_message(
                                    updated,
                                    quote.price,
                                    quote.price
                                    - float(
                                        updated[
                                            "entry"
                                        ]
                                    ),
                                )
                            )

                            sent.add(
                                threshold
                            )

                    # -----------------------------------------
                    # NEAR SL WARNING
                    # -----------------------------------------

                    stop = float(
                        updated.get(
                            "trailing_stop"
                        )
                        or updated["sl"]
                    )

                    distance_pct = (
                        abs(
                            quote.price
                            - stop
                        )
                        / float(
                            updated["entry"]
                        )
                        * 100
                    )

                    if (
                        quote.price > stop
                        and distance_pct
                        <= self.s.near_sl_warning_pct
                        and not updated.get(
                            "near_sl_warning_sent"
                        )
                    ):

                        await self.b.send_near_sl(
                            updated,
                            quote.price,
                        )

                        updated[
                            "near_sl_warning_sent"
                        ] = True

                    # -----------------------------------------
                    # SAVE MONITOR STATE
                    # -----------------------------------------

                    current = self.store.state()

                    for item in current[
                        "open_trades"
                    ]:

                        if (
                            item["symbol"]
                            == symbol
                        ):

                            item[
                                "profit_alerts_sent"
                            ] = sorted(sent)

                            item[
                                "near_sl_warning_sent"
                            ] = updated.get(
                                "near_sl_warning_sent",
                                False,
                            )

                    self.store.save_state(
                        current
                    )

                except Exception as exc:

                    print(
                        f"[monitor] {symbol} "
                        f"failed: {exc}"
                    )

    # =========================================================
    # SCHEDULED TASKS
    # =========================================================

    async def scheduled_tasks(self):
        """
        Scheduler-safe tasks.

        No automatic signal discovery.
        """

        await self.monitor_once()

        await self._scheduled_market_close_message()

        await self._scheduled_weekly_report()

    async def _scheduled_market_close_message(
        self,
    ):

        local = self._local_now()

        key = local.date().isoformat()

        market_close_hour = int(
            self.s.market_close.split(":")[0]
        )

        market_close_minute = int(
            self.s.market_close.split(":")[1]
        )

        if (
            key
            == self.last_market_close_key
            or not (
                local.hour
                == market_close_hour
                and local.minute
                >= market_close_minute
            )
        ):
            return

        self.last_market_close_key = key

        await self.b.send_market_close(
            local.strftime(
                "%Y-%m-%d %H:%M %Z"
            )
        )

    async def _scheduled_weekly_report(
        self,
    ):

        local = self._local_now()

        key = local.date().isoformat()

        if not self.s.weekly_report_enabled:
            return

        if (
            local.weekday()
            != self.s.weekly_report_weekday
        ):
            return

        report_minute = (
            self.s.weekly_report_hour
            * 60
            + self.s.weekly_report_minute
        )

        current_minute = (
            local.hour * 60
            + local.minute
        )

        if (
            current_minute
            < report_minute
            or self.last_report_key
            == key
        ):
            return

        self.last_report_key = key

        await self.weekly_report(
            send=True
        )

    # =========================================================
    # MARKET TEXT
    # =========================================================

    async def market_text(self):

        data = await self._market()

        if not data:
            return (
                "⚠️ بيانات السوق "
                "غير متاحة حاليًا."
            )

        return (
            "📊 حالة السوق السعودي\n\n"
            f"TASI: "
            f"{data.get('value', data.get('index', '—'))}\n"
            f"التغير: "
            f"{data.get('change_percent', data.get('change_pct', '—'))}%\n"
            f"Market Regime: "
            f"{classify_tasi(data)}\n"
            f"الأسهم الصاعدة: "
            f"{data.get('advancers', data.get('advancing', '—'))}\n"
            f"الأسهم الهابطة: "
            f"{data.get('decliners', data.get('declining', '—'))}\n"
            f"قيمة التداول: "
            f"{data.get('trading_value', data.get('value_traded', '—'))}\n\n"
            "📡 المصدر: SAHMK delayed\n"
            "⚠️ لا يتم إنشاء إشارات تلقائية. "
            "استخدم /signal."
        )

    # =========================================================
    # OPEN TRADES
    # =========================================================

    def open_trades_text(self):

        trades = self.store.state()[
            "open_trades"
        ]

        if not trades:
            return (
                "📭 لا توجد صفقات مفتوحة حاليًا."
            )

        lines = [
            "📂 الصفقات المفتوحة",
            "",
        ]

        for trade in trades:

            lines.append(
                f"{trade['name']} "
                f"({trade['symbol']})\n"
                f"دخول: "
                f"{float(trade['entry']):.2f} | "
                f"الحالي: "
                f"{float(trade.get('current_price', trade['entry'])):.2f}\n"
                f"SL: "
                f"{float(trade['sl']):.2f} | "
                f"TP1: "
                f"{float(trade['tp1']):.2f} | "
                f"TP2: "
                f"{float(trade['tp2']):.2f} | "
                f"TP3: "
                f"{float(trade['tp3']):.2f}"
            )

        return "\n\n".join(lines)

    # =========================================================
    # WEEKLY REPORT
    # =========================================================

    async def weekly_report(
        self,
        send=True,
    ):

        history = self.store.history()

        cutoff = (
            self._utc_now()
            - timedelta(days=7)
        )

        weekly = []

        for item in history:

            stamp = (
                item.get("exit_time")
                or item.get("discovered_at")
            )

            try:

                when = datetime.fromisoformat(
                    str(stamp).replace(
                        "Z",
                        "+00:00",
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            if when >= cutoff:
                weekly.append(item)

        out = (
            Path(self.s.state_dir)
            / "reports"
            / "weekly_report.png"
        )

        build_weekly_report(
            weekly,
            out,
        )

        if send:

            await self.b.send_report(
                "📊 التقرير الأسبوعي — آخر 7 أيام",
                str(out),
            )

        if send:
            return (
                "📊 تم إنشاء وإرسال "
                "التقرير الأسبوعي."
            )

        return (
            "📊 تم إنشاء التقرير الأسبوعي."
        )

    # =========================================================
    # PERFORMANCE
    # =========================================================

    def performance_text(self):

        history = self.store.history()

        wins = [
            x
            for x in history
            if x.get("result") == "WIN"
        ]

        losses = [
            x
            for x in history
            if x.get("result") == "LOSS"
        ]

        closed = (
            len(wins)
            + len(losses)
        )

        win_rate = (
            len(wins)
            / closed
            * 100
            if closed
            else 0
        )

        avg = (
            sum(
                float(
                    x.get("result_pct")
                    or 0
                )
                for x in history
            )
            / len(history)
            if history
            else 0
        )

        gross_win = sum(
            max(
                0,
                float(
                    x.get("result_pct")
                    or 0
                ),
            )
            for x in history
        )

        gross_loss = abs(
            sum(
                min(
                    0,
                    float(
                        x.get("result_pct")
                        or 0
                    ),
                )
                for x in history
            )
        )

        pf = (
            gross_win
            / gross_loss
            if gross_loss
            else 0
        )

        return (
            "📈 أداء Paper Trading\n\n"
            f"الصفقات المغلقة: {closed}\n"
            f"الرابحة: {len(wins)}\n"
            f"الخاسرة: {len(losses)}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"متوسط العائد: {avg:+.2f}%\n"
            f"Profit Factor: {pf:.2f}\n"
            f"الصفقات المفتوحة: "
            f"{len(self.store.state()['open_trades'])}"
        )

    # =========================================================
    # STATUS
    # =========================================================

    def status_text(self):

        state = self.store.state()

        return (
            "🤖 حالة النظام\n\n"
            "New Signals: MANUAL (/signal)\n"
            "Scheduler: MONITOR ONLY\n"
            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"
            f"Paused: "
            f"{'YES' if state.get('paused') else 'NO'}\n"
            f"Universe: "
            f"{len(self.universe)}\n"
            f"Open Trades: "
            f"{len(state['open_trades'])}\n"
            f"Last Manual Scan: "
            f"{state['meta'].get('last_scan', '—')}\n"
            f"Last Trade Monitor: "
            f"{self.last_monitor.isoformat() if self.last_monitor else '—'}"
        )

    # =========================================================
    # HEALTH
    # =========================================================

    async def health_text(self):

        state = self.store.state()

        telegram_ok = False
        sahmk_ok = False

        try:

            await self.b.signal.get_me()

            telegram_ok = True

        except Exception as exc:

            print(
                f"[health] telegram failed: "
                f"{exc}"
            )

        try:

            sahmk_ok = (
                await self._market()
            ) is not None

        except Exception as exc:

            print(
                f"[health] SAHMK failed: "
                f"{exc}"
            )

        stats = (
            self.p.stats()
            if hasattr(self.p, "stats")
            else {}
        )

        return (
            "🟢 SYSTEM HEALTH\n\n"
            f"Telegram: "
            f"{'OK' if telegram_ok else 'ERROR'}\n"
            f"SAHMK: "
            f"{'OK' if sahmk_ok else 'ERROR'}\n"
            "Scheduler: RUNNING "
            "(monitor only)\n"
            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"
            f"Universe: "
            f"{len(self.universe)}\n"
            f"Open Trades: "
            f"{len(state['open_trades'])}\n"
            f"Last Manual Scan: "
            f"{state['meta'].get('last_scan', '—')}\n"
            f"Last Universe Update: "
            f"{state['meta'].get('last_universe_refresh', '—')}\n"
            f"SAHMK Requests: "
            f"{stats.get('requests', '—')} | "
            f"429: "
            f"{stats.get('rate_limits', '—')}"
        )

    # =========================================================
    # SETTINGS
    # =========================================================

    def settings_text(self):

        return (
            "⚙️ الإعدادات الآمنة\n\n"
            f"Min Score: "
            f"{self.s.min_score}\n"
            f"Min Probability: "
            f"{self.s.min_probability}%\n"
            f"Max Daily Signals: "
            f"{self.s.max_daily_signals}\n"
            f"Max Open Trades: "
            f"{self.s.max_open_trades}\n"
            f"Manual Quote Cap: "
            f"{self.s.manual_quotes_per_signal}\n"
            f"Monitor Quote Cap/Cycle: "
            f"{self.s.trade_monitor_quotes_per_cycle}\n"
            f"Monitor Interval: "
            f"{self.s.scan_interval_seconds}s\n"
            f"Data Max Delay: "
            f"{self.s.data_max_delay_minutes} min\n"
            f"Min R/R: "
            f"{self.s.min_rr}\n"
            f"Risk/Trade: "
            f"{self.s.max_risk_per_trade:.2%}\n"
            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"
            "Secrets: HIDDEN"
        )

    # =========================================================
    # RISK
    # =========================================================

    def risk_text(self):

        return (
            "🛡️ إدارة المخاطر\n\n"
            f"الحد الأقصى للمخاطرة لكل صفقة: "
            f"{self.s.max_risk_per_trade:.2%}\n"
            f"الحد الأدنى R/R: "
            f"{self.s.min_rr}\n"
            f"الحد الأقصى للصفقات المفتوحة: "
            f"{self.s.max_open_trades}\n"
            f"Trailing Stop: "
            f"{'ON' if self.s.trailing_stop_enabled else 'OFF'}\n"
            "الوضع: Paper Trading فقط"
        )
