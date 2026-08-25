import hashlib
import hmac
import os

from telegram import Bot, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler

from app.telegram.messages import loss_message, near_sl_message


class TelegramBots:
    def __init__(self, settings):
        self.s = settings
        self.signal = Bot(settings.signal_bot_token)
        self.profit = Bot(settings.profit_bot_token)
        self.loss = Bot(settings.loss_bot_token)
        self.report = Bot(settings.report_bot_token)
        self.application = None
        self.service = None
        self.mode = self._resolve_mode()
        self.webhook_secret = hashlib.sha256(
            settings.signal_bot_token.encode("utf-8")
        ).hexdigest()[:48]

    def _resolve_mode(self):
        configured = str(getattr(self.s, "telegram_mode", "auto")).strip().lower()
        if configured in {"polling", "webhook"}:
            return configured
        return "webhook" if os.getenv("RENDER_EXTERNAL_URL") else "polling"

    def attach_service(self, service):
        self.service = service

    async def test(self):
        chat_id = self.s.telegram_chat_id
        await self.signal.send_message(chat_id, "🟢 SIGNAL BOT — اتصال ناجح")
        await self.profit.send_message(chat_id, "🟡 PROFIT BOT — اتصال ناجح")
        await self.loss.send_message(chat_id, "🔴 LOSS BOT — اتصال ناجح")
        await self.report.send_message(chat_id, "📊 REPORT BOT — اتصال ناجح")

    async def send_signal(self, text):
        await self.signal.send_message(self.s.telegram_chat_id, text)

    async def send_profit(self, text):
        await self.profit.send_message(self.s.telegram_chat_id, text)

    async def send_loss(self, text):
        await self.loss.send_message(self.s.telegram_chat_id, text)

    async def send_loss_for_trade(self, trade, price):
        await self.send_loss(loss_message(trade, price))

    async def send_near_sl(self, trade, price):
        await self.send_loss(near_sl_message(trade, price))

    async def send_report(self, text=None, image_path=None):
        if image_path:
            with open(image_path, "rb") as fh:
                await self.report.send_photo(
                    self.s.telegram_chat_id,
                    fh,
                    caption=text or "📊 التقرير الأسبوعي",
                )
        elif text:
            await self.report.send_message(self.s.telegram_chat_id, text)

    async def send_market_close(self, local_time_text):
        await self.signal.send_message(
            self.s.telegram_chat_id,
            "🔔 السوق أغلق اليوم\n\n"
            f"التاريخ والوقت: {local_time_text}\n\n"
            "📊 TASI — انتهت جلسة التداول اليوم.\n"
            "📡 البيانات: SAHMK delayed",
        )

    def _is_target_chat(self, update: Update):
        return bool(
            update.effective_chat
            and int(update.effective_chat.id) == int(self.s.telegram_chat_id)
        )

    async def _safe_reply(self, update, text):
        if update.effective_message:
            await update.effective_message.reply_text(text)

    async def _admin_only(self, update):
        if not update.effective_chat or not update.effective_user:
            return False
        try:
            member = await self.signal.get_chat_member(
                update.effective_chat.id,
                update.effective_user.id,
            )
            return member.status in {
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            }
        except Exception as exc:
            print(f"[telegram] admin check failed: {exc}")
            return False

    async def _guard(self, update):
        if not self._is_target_chat(update):
            await self._safe_reply(update, "⚠️ هذا البوت مخصص لمجموعة التداول المحددة.")
            return False
        return True

    async def start(self, update, context):
        if not await self._guard(update):
            return
        await self._safe_reply(
            update,
            "🤖 TASI KSA Trading Bot\n\n"
            "📊 إشارات أسهم سعودية — Paper Trading فقط\n\n"
            "/signal — فحص يدوي للأسهم النشطة\n"
            "/market — حالة السوق\n"
            "/open — الصفقات المفتوحة\n"
            "/performance — الأداء\n"
            "/report — التقرير الأسبوعي\n"
            "/status — الحالة\n"
            "/health — صحة النظام\n"
            "/settings — الإعدادات الآمنة\n"
            "/risk — إدارة المخاطر\n"
            "/pause — إيقاف الإشارات الجديدة\n"
            "/resume — استئناف الإشارات\n"
            "/help — المساعدة\n\n"
            "ℹ️ لا توجد إشارات تلقائية. /signal فقط يبدأ اكتشاف صفقة جديدة."
        )

    async def help(self, update, context):
        await self.start(update, context)

    async def signal_command(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(
            update,
            "🔎 بدأ الفحص اليدوي للأسهم النشطة... لن تُنشأ صفقة إلا إذا اجتازت الشروط."
        )
        try:
            result = await self.service.scan_once(source="telegram")
            await self._safe_reply(update, result)
        except Exception as exc:
            print(f"[telegram] /signal failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تعذر إكمال الفحص حاليًا. راجع Render Logs.")

    async def market(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(update, await self.service.market_text())

    async def open_trades(self, update, context):
        if await self._guard(update) and self.service:
            await self._safe_reply(update, self.service.open_trades_text())

    async def performance(self, update, context):
        if await self._guard(update) and self.service:
            await self._safe_reply(update, self.service.performance_text())

    async def report_command(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        try:
            await self._safe_reply(update, await self.service.weekly_report())
        except Exception as exc:
            print(f"[telegram] /report failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تعذر إنشاء التقرير حاليًا.")

    async def status(self, update, context):
        if await self._guard(update) and self.service:
            await self._safe_reply(update, self.service.status_text())

    async def health(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(update, await self.service.health_text())

    async def settings(self, update, context):
        if await self._guard(update) and self.service:
            await self._safe_reply(update, self.service.settings_text())

    async def risk(self, update, context):
        if await self._guard(update) and self.service:
            await self._safe_reply(update, self.service.risk_text())

    async def pause(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        if not await self._admin_only(update):
            await self._safe_reply(update, "🔒 أمر /pause متاح لمشرفي المجموعة فقط.")
            return
        self.service.set_paused(True)
        await self._safe_reply(update, "⏸️ تم إيقاف إنشاء الإشارات الجديدة. الصفقات المفتوحة تستمر في المتابعة.")

    async def resume(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        if not await self._admin_only(update):
            await self._safe_reply(update, "🔒 أمر /resume متاح لمشرفي المجموعة فقط.")
            return
        self.service.set_paused(False)
        await self._safe_reply(update, "▶️ تم استئناف إنشاء الإشارات الجديدة.")

    async def error(self, update, context):
        print(f"[telegram] handler error: {context.error!r}")

    def _add_handlers(self):
        for name, callback in {
            "start": self.start,
            "help": self.help,
            "signal": self.signal_command,
            "market": self.market,
            "open": self.open_trades,
            "performance": self.performance,
            "report": self.report_command,
            "status": self.status,
            "health": self.health,
            "settings": self.settings,
            "risk": self.risk,
            "pause": self.pause,
            "resume": self.resume,
        }.items():
            self.application.add_handler(CommandHandler(name, callback))
        self.application.add_error_handler(self.error)

    async def start_commands(self):
        if self.application is not None:
            return

        self.application = Application.builder().token(self.s.signal_bot_token).build()
        self._add_handlers()
        await self.application.initialize()
        await self.application.start()

        if self.mode == "webhook":
            base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
            if not base_url:
                raise RuntimeError("Webhook mode requires RENDER_EXTERNAL_URL")
            webhook_url = f"{base_url}/telegram/webhook"
            await self.application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=["message"],
                drop_pending_updates=True,
                secret_token=self.webhook_secret,
            )
            print(f"[telegram] webhook started: {webhook_url}")
        else:
            await self.application.bot.delete_webhook(drop_pending_updates=True)
            if self.application.updater is None:
                raise RuntimeError("Telegram updater is unavailable")
            await self.application.updater.start_polling(
                allowed_updates=["message"],
                drop_pending_updates=True,
            )
            print("[telegram] command polling started")

    async def process_webhook(self, payload, received_secret):
        if self.mode != "webhook" or self.application is None:
            return False
        if not received_secret or not hmac.compare_digest(received_secret, self.webhook_secret):
            return False
        update = Update.de_json(payload, self.application.bot)
        await self.application.update_queue.put(update)
        return True

    async def stop_commands(self):
        if self.application is None:
            return
        try:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
            if self.application.running:
                await self.application.stop()
            await self.application.shutdown()
        finally:
            self.application = None
