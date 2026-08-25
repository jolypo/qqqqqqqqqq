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
from app.telegram.messages import profit_message, signal_message, tp_message
from app.database.json_store import JsonStore
from app.trades.manager import TradeManager
from app.reports.weekly import report as build_weekly_report


class TradingService:
    """Core service. New-signal discovery is manual only via /signal."""

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

    def _utc_now(self):
        return datetime.now(timezone.utc)

    def _local_now(self):
        return self._utc_now().astimezone(self.tz)

    @staticmethod
    def _minutes(clock_text):
        hour, minute = str(clock_text).split(":", 1)
        return int(hour) * 60 + int(minute)

    def market_is_open(self):
        local = self._local_now()
        # Saudi Exchange regular week: Sunday-Thursday.
        if local.weekday() in (4, 5):  # Friday, Saturday
            return False
        minute = local.hour * 60 + local.minute
        return self._minutes(self.s.market_open) <= minute < self._minutes(self.s.market_close)

    def _fresh_quote(self, quote):
        if quote is None or quote.price <= 0 or quote.updated_at is None:
            return False
        updated_at = quote.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = (self._utc_now() - updated_at).total_seconds() / 60
        if age < -5:
            return False
        return age <= self.s.data_max_delay_minutes

    async def refresh(self):
        self.universe = normalize_universe(await self.p.companies("TASI"))
        self.last_refresh = self._utc_now()
        self.scan_cursor = min(self.scan_cursor, max(0, len(self.universe) - 1)) if self.universe else 0
        state = self.store.state()
        state["meta"]["last_universe_refresh"] = self.last_refresh.isoformat()
        state["meta"]["universe_size"] = len(self.universe)
        self.store.save_state(state)
        print(f"[universe] {len(self.universe)} companies")

    def is_paused(self):
        return bool(self.store.state().get("paused", False))

    def set_paused(self, paused):
        state = self.store.state()
        state["paused"] = bool(paused)
        state["meta"]["paused_at"] = self._utc_now().isoformat()
        self.store.save_state(state)

    def can_send(self):
        state = self.store.state()
        today = self._local_now().date().isoformat()
        return (
            not state.get("paused", False)
            and len(state["open_trades"]) < self.s.max_open_trades
            and state["daily_signals"].get(today, 0) < self.s.max_daily_signals
            and self.s.paper_mode
        )

    def _next_batch(self, size, cursor_name):
        if not self.universe:
            return []
        total = len(self.universe)
        size = min(max(1, int(size)), total)
        cursor = getattr(self, cursor_name)
        end = cursor + size
        if end <= total:
            batch = self.universe[cursor:end]
        else:
            batch = self.universe[cursor:] + self.universe[: end - total]
        setattr(self, cursor_name, end % total)
        return batch

    async def _market(self, force=False):
        now = self._utc_now()
        if not force and self.last_market_summary is not None and self.last_market_summary_at:
            if (now - self.last_market_summary_at).total_seconds() < self.s.market_cache_seconds:
                return self.last_market_summary
        try:
            data = await self.p.market_summary()
            self.last_market_summary = data
            self.last_market_summary_at = now
            return data
        except Exception as exc:
            print(f"[market] summary failed: {exc}")
            return None

    @staticmethod
    def _rows_to_df(payload):
        if not isinstance(payload, dict):
            return None
        rows = payload.get("data", payload.get("results", payload.get("historical", [])))
        if not isinstance(rows, list) or len(rows) < 60:
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
        df = df.rename(columns=rename_map)
        required = {"open", "high", "low", "close", "volume"}
        return df if required.issubset(df.columns) else None

    async def _ensure_universe(self):
        if self.universe and self.last_refresh:
            age = (self._utc_now() - self.last_refresh).total_seconds()
            if age <= self.s.universe_refresh_seconds:
                return
        try:
            await self.refresh()
        except Exception as exc:
            # /market/volume is already scoped to TASI, so Free mode can still
            # screen safely without company metadata; sector will be Unknown.
            print(f"[universe] refresh failed, continuing without metadata: {exc}")

    async def scan_once(self, source="telegram"):
        if self.scan_lock.locked():
            return "⏳ يوجد فحص يدوي جارٍ حاليًا."

        async with self.scan_lock:
            self.last_scan = self._utc_now()
            state = self.store.state()
            state["meta"]["last_scan"] = self.last_scan.isoformat()
            state["meta"]["last_scan_source"] = source
            self.store.save_state(state)

            if self.is_paused():
                return "⏸️ النظام متوقف مؤقتًا. استخدم /resume أولًا."
            if not self.s.paper_mode:
                return "🛑 PAPER_MODE غير مفعّل؛ تم منع إنشاء الصفقة."
            if not self.can_send():
                return "ℹ️ تم بلوغ حد الصفقات المفتوحة أو الإشارات اليومية."
            if not self.s.allow_off_hours_scan and not self.market_is_open():
                return (
                    "🌙 السوق السعودي مغلق حاليًا.\n"
                    f"وقت الفحص المسموح: {self.s.market_open}–{self.s.market_close} بتوقيت الرياض، الأحد–الخميس.\n"
                    "لن يتم إنشاء إشارة من أسعار إغلاق قديمة."
                )

            await self._ensure_universe()
            universe_by_symbol = {
                str(item.get("symbol", "")).strip(): item
                for item in self.universe
                if item.get("symbol")
            }

            market_data = await self._market()
            regime = classify_tasi(market_data) if market_data else "NEUTRAL"

            screen_limit = min(max(1, int(self.s.manual_quotes_per_signal)), 50)
            detail_limit = min(max(1, int(self.s.detail_quotes_per_signal)), 10)

            # Free plan: /market/volume gives price/change/volume for active
            # stocks in one request. This is the 50-stock screening layer.
            try:
                screening_quotes = await self.p.top_volume_quotes(screen_limit, "TASI")
                selection_source = "top_volume"
            except Exception as exc:
                print(f"[manual-scan] top-volume failed: {exc}")
                screening_quotes = []
                selection_source = "fallback"

            # Safe fallback: never burn 50 individual requests if the ranking
            # endpoint is unavailable. Inspect only a small rotating sample.
            if not screening_quotes:
                fallback_items = self._next_batch(detail_limit, "scan_cursor")
                fallback_symbols = [str(x.get("symbol", "")).strip() for x in fallback_items if x.get("symbol")]
                details = await self.p.quotes(fallback_symbols)
                screening_quotes = list(details.values())

            fresh_screening = [q for q in screening_quotes if self._fresh_quote(q)]
            print(
                f"[manual-scan] source={source} selection={selection_source} "
                f"screened={len(fresh_screening)}/{len(screening_quotes)} universe={len(self.universe)}"
            )

            preliminary = []
            threshold = max(60.0, float(self.s.min_score) - 15.0)
            for quote in fresh_screening:
                candidate = fast_score(quote, regime)
                if candidate.score >= threshold:
                    preliminary.append(candidate)

            preliminary.sort(key=lambda c: (c.score, c.quote.volume, c.quote.value), reverse=True)
            finalists = preliminary[:detail_limit]

            if not finalists:
                return (
                    "🔎 اكتمل الفحص اليدوي.\n"
                    f"🎯 أسهم نشطة مستهدفة: {len(screening_quotes)}\n"
                    f"✅ بيانات حديثة صالحة: {len(fresh_screening)}\n"
                    "لم يظهر مرشح أولي يستحق طلب تفاصيل إضافية."
                )

            detailed_quotes = await self.p.quotes([c.quote.symbol for c in finalists])
            print(f"[manual-scan] detailed quotes returned {len(detailed_quotes)}/{len(finalists)}")

            engine = SignalEngine(self.s, self.store.history())
            plan = str(self.s.sahmk_plan).strip().lower()

            for preliminary_candidate in finalists:
                symbol = preliminary_candidate.quote.symbol
                quote = detailed_quotes.get(symbol)
                if quote is None or not self._fresh_quote(quote):
                    continue

                candidate = fast_score(quote, regime)
                item = universe_by_symbol.get(symbol, {})
                sector = item.get("sector", "")

                try:
                    if plan == "free":
                        signal = engine.build_free(candidate, regime, sector)
                    else:
                        historical = await self.p.historical(symbol, 250)
                        df = self._rows_to_df(historical)
                        if df is None:
                            continue
                        signal = engine.build(candidate, regime, sector, latest_features(df))
                except Exception as exc:
                    print(f"[analysis] {symbol} failed: {exc}")
                    continue

                if not signal:
                    continue

                if self.trade_manager.add(signal):
                    state = self.store.state()
                    day = self._local_now().date().isoformat()
                    state["daily_signals"][day] = state["daily_signals"].get(day, 0) + 1
                    self.store.save_state(state)
                    await self.b.send_signal(signal_message(signal.to_dict()))
                    print(f"[signal] sent {symbol} strategy={signal.strategy}")
                    return (
                        f"✅ تم اكتشاف وإرسال إشارة ورقية: {signal.name} ({signal.symbol})\n"
                        f"⭐ Score: {signal.score:.1f}/100\n"
                        f"📊 Probability status: {signal.probability_status}\n"
                        f"🔎 تم فرز {len(fresh_screening)} سهمًا نشطًا وجلب تفاصيل {len(detailed_quotes)} مرشحين."
                    )

            return (
                "🔎 اكتمل الفحص اليدوي.\n"
                f"🎯 الأسهم النشطة المفروزة: {len(fresh_screening)}\n"
                f"🔬 المرشحون بتفاصيل كاملة: {len(detailed_quotes)}\n"
                "لم توجد صفقة مستوفية لجميع شروط Paper Trading."
            )

    async def monitor_once(self):
        """Monitor open paper trades only; never creates new trades."""
        if self.monitor_lock.locked() or not self.market_is_open():
            return

        async with self.monitor_lock:
            self.last_monitor = self._utc_now()
            state = self.store.state()
            if not state["open_trades"]:
                return

            trades = state["open_trades"]
            total = len(trades)
            batch_size = min(max(1, int(self.s.trade_monitor_quotes_per_cycle)), total)
            start = self.monitor_cursor % total
            selected = [trades[(start + i) % total] for i in range(batch_size)]
            self.monitor_cursor = (start + len(selected)) % total

            for trade in selected:
                symbol = trade["symbol"]
                try:
                    quote = await self.p.quote(symbol)
                    if not self._fresh_quote(quote):
                        print(f"[monitor] {symbol}: stale/missing timestamp")
                        continue
                    updated, events = self.trade_manager.update(symbol, quote.price)
                    if not updated:
                        continue

                    for event in events:
                        if event == "CLOSE_TP3":
                            await self.b.send_profit(tp_message(updated, "TP3", quote.price))
                        elif event == "SL":
                            await self.b.send_loss_for_trade(updated, quote.price)
                        elif event in {"TP1", "TP2"}:
                            await self.b.send_profit(tp_message(updated, event, quote.price))

                    if updated.get("status") == "OPEN":
                        self.trade_manager.apply_trailing(updated, quote.price, atr=None)

                        pct = (quote.price - float(updated["entry"])) / float(updated["entry"]) * 100
                        sent = set(updated.get("profit_alerts_sent", []))
                        try:
                            thresholds = [
                                float(x.strip())
                                for x in self.s.profit_alert_thresholds.split(",")
                                if x.strip()
                            ]
                        except ValueError:
                            thresholds = [2, 5, 10, 15, 20]

                        for threshold in thresholds:
                            if pct >= threshold and threshold not in sent:
                                await self.b.send_profit(
                                    profit_message(updated, quote.price, quote.price - float(updated["entry"]))
                                )
                                sent.add(threshold)

                        stop = float(updated.get("trailing_stop") or updated["sl"])
                        distance_pct = abs(quote.price - stop) / float(updated["entry"]) * 100
                        if (
                            quote.price > stop
                            and distance_pct <= self.s.near_sl_warning_pct
                            and not updated.get("near_sl_warning_sent")
                        ):
                            await self.b.send_near_sl(updated, quote.price)
                            updated["near_sl_warning_sent"] = True

                        current = self.store.state()
                        for item in current["open_trades"]:
                            if item["symbol"] == symbol:
                                item["profit_alerts_sent"] = sorted(sent)
                                item["near_sl_warning_sent"] = updated.get("near_sl_warning_sent", False)
                                item["trailing_stop"] = updated.get("trailing_stop")
                        self.store.save_state(current)
                except Exception as exc:
                    print(f"[monitor] {symbol} failed: {exc}")

    async def scheduled_tasks(self):
        await self.monitor_once()
        await self._scheduled_market_close_message()
        await self._scheduled_weekly_report()

    async def _scheduled_market_close_message(self):
        local = self._local_now()
        if local.weekday() in (4, 5):
            return
        key = local.date().isoformat()
        if key == self.last_market_close_key:
            return
        if local.hour * 60 + local.minute < self._minutes(self.s.market_close):
            return
        self.last_market_close_key = key
        await self.b.send_market_close(local.strftime("%Y-%m-%d %H:%M %Z"))

    async def _scheduled_weekly_report(self):
        local = self._local_now()
        key = local.date().isoformat()
        if not self.s.weekly_report_enabled or local.weekday() != self.s.weekly_report_weekday:
            return
        report_minute = self.s.weekly_report_hour * 60 + self.s.weekly_report_minute
        if local.hour * 60 + local.minute < report_minute or self.last_report_key == key:
            return
        self.last_report_key = key
        await self.weekly_report(send=True)

    async def market_text(self):
        data = await self._market()
        if not data:
            return "⚠️ بيانات السوق غير متاحة حاليًا."
        return (
            "📊 حالة السوق السعودي\n\n"
            f"TASI: {data.get('value', data.get('index', '—'))}\n"
            f"التغير: {data.get('change_percent', data.get('change_pct', '—'))}%\n"
            f"Market Regime: {classify_tasi(data)}\n"
            f"الأسهم الصاعدة: {data.get('advancers', data.get('advancing', '—'))}\n"
            f"الأسهم الهابطة: {data.get('decliners', data.get('declining', '—'))}\n"
            f"قيمة التداول: {data.get('trading_value', data.get('value_traded', '—'))}\n\n"
            "📡 المصدر: SAHMK delayed\n"
            "⚠️ لا يتم إنشاء إشارات تلقائية. استخدم /signal."
        )

    def open_trades_text(self):
        trades = self.store.state()["open_trades"]
        if not trades:
            return "📭 لا توجد صفقات مفتوحة حاليًا."
        lines = ["📂 الصفقات المفتوحة", ""]
        for t in trades:
            lines.append(
                f"{t['name']} ({t['symbol']})\n"
                f"دخول: {float(t['entry']):.2f} | الحالي: {float(t.get('current_price', t['entry'])):.2f}\n"
                f"SL: {float(t['sl']):.2f} | TP1: {float(t['tp1']):.2f} | "
                f"TP2: {float(t['tp2']):.2f} | TP3: {float(t['tp3']):.2f}"
            )
        return "\n\n".join(lines)

    async def weekly_report(self, send=True):
        history = self.store.history()
        cutoff = self._utc_now() - timedelta(days=7)
        weekly = []
        for item in history:
            stamp = item.get("exit_time") or item.get("discovered_at")
            try:
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if when >= cutoff:
                weekly.append(item)
        out = Path(self.s.state_dir) / "reports" / "weekly_report.png"
        build_weekly_report(weekly, out)
        if send:
            await self.b.send_report("📊 التقرير الأسبوعي — آخر 7 أيام", str(out))
        return "📊 تم إنشاء وإرسال التقرير الأسبوعي." if send else "📊 تم إنشاء التقرير الأسبوعي."

    def performance_text(self):
        history = self.store.history()
        wins = [x for x in history if x.get("result") == "WIN"]
        losses = [x for x in history if x.get("result") == "LOSS"]
        closed = len(wins) + len(losses)
        win_rate = len(wins) / closed * 100 if closed else 0
        avg = sum(float(x.get("result_pct") or 0) for x in history) / len(history) if history else 0
        gross_win = sum(max(0, float(x.get("result_pct") or 0)) for x in history)
        gross_loss = abs(sum(min(0, float(x.get("result_pct") or 0)) for x in history))
        pf = gross_win / gross_loss if gross_loss else 0
        return (
            "📈 أداء Paper Trading\n\n"
            f"الصفقات المغلقة: {closed}\n"
            f"الرابحة: {len(wins)}\n"
            f"الخاسرة: {len(losses)}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"متوسط العائد: {avg:+.2f}%\n"
            f"Profit Factor: {pf:.2f}\n"
            f"الصفقات المفتوحة: {len(self.store.state()['open_trades'])}"
        )

    def status_text(self):
        state = self.store.state()
        return (
            "🤖 حالة النظام\n\n"
            "New Signals: MANUAL (/signal)\n"
            "Scheduler: MONITOR ONLY\n"
            f"Market: {'OPEN' if self.market_is_open() else 'CLOSED'}\n"
            f"SAHMK Plan: {self.s.sahmk_plan.upper()}\n"
            f"Paper Mode: {'ON' if self.s.paper_mode else 'OFF'}\n"
            f"Paused: {'YES' if state.get('paused') else 'NO'}\n"
            f"Universe: {len(self.universe)}\n"
            f"Open Trades: {len(state['open_trades'])}\n"
            f"Last Manual Scan: {state['meta'].get('last_scan', '—')}\n"
            f"Last Trade Monitor: {self.last_monitor.isoformat() if self.last_monitor else '—'}"
        )

    async def health_text(self):
        state = self.store.state()
        telegram_ok = False
        try:
            await self.b.signal.get_me()
            telegram_ok = True
        except Exception as exc:
            print(f"[health] telegram failed: {exc}")

        stats = self.p.stats() if hasattr(self.p, "stats") else {}
        return (
            "🟢 SYSTEM HEALTH\n\n"
            f"Telegram: {'OK' if telegram_ok else 'ERROR'}\n"
            f"SAHMK local budget: {stats.get('daily_requests', '—')}/{stats.get('daily_limit', '—')}\n"
            f"SAHMK server remaining: {stats.get('remaining', '—')}\n"
            f"SAHMK 429: {stats.get('rate_limits', '—')} | Errors: {stats.get('errors', '—')}\n"
            "Scheduler: RUNNING WHEN SERVICE IS AWAKE\n"
            f"Paper Mode: {'ON' if self.s.paper_mode else 'OFF'}\n"
            f"Universe: {len(self.universe)}\n"
            f"Open Trades: {len(state['open_trades'])}\n"
            f"Last Manual Scan: {state['meta'].get('last_scan', '—')}\n"
            f"Last Universe Update: {state['meta'].get('last_universe_refresh', '—')}"
        )

    def settings_text(self):
        return (
            "⚙️ الإعدادات الآمنة\n\n"
            f"SAHMK Plan: {self.s.sahmk_plan.upper()}\n"
            f"Active-stock screen: {self.s.manual_quotes_per_signal}\n"
            f"Detailed finalists: {self.s.detail_quotes_per_signal}\n"
            f"Min Score: {self.s.min_score}\n"
            f"Min Validated Probability: {self.s.min_probability}%\n"
            f"Max Daily Signals: {self.s.max_daily_signals}\n"
            f"Max Open Trades: {self.s.max_open_trades}\n"
            f"Monitor Quotes/Cycle: {self.s.trade_monitor_quotes_per_cycle}\n"
            f"Monitor Interval: {self.s.scan_interval_seconds}s\n"
            f"Data Max Delay: {self.s.data_max_delay_minutes} min\n"
            f"Min R/R: {self.s.min_rr}\n"
            f"Risk/Trade: {self.s.max_risk_per_trade:.2%}\n"
            f"Paper Mode: {'ON' if self.s.paper_mode else 'OFF'}\n"
            "Secrets: HIDDEN"
        )

    def risk_text(self):
        return (
            "🛡️ إدارة المخاطر\n\n"
            f"الحد الأقصى للمخاطرة لكل صفقة: {self.s.max_risk_per_trade:.2%}\n"
            f"الحد الأدنى R/R: {self.s.min_rr}\n"
            f"الحد الأقصى للصفقات المفتوحة: {self.s.max_open_trades}\n"
            f"Trailing Stop: {'ON' if self.s.trailing_stop_enabled else 'OFF'}\n"
            "الوضع: Paper Trading فقط"
        )
