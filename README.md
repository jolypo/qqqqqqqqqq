# TASI KSA Signal Bot — SAHMK Free + Telegram + Render

نظام Paper Trading للأسهم السعودية TASI. اكتشاف الصفقات **يدوي فقط** عبر `/signal`، ولا يوجد تنفيذ أوامر شراء أو بيع حقيقية.

## ما الذي يفعله `/signal`؟

في وضع `SAHMK_PLAN=free`:

1. يرفض إنشاء إشارة إذا كان السوق مغلقًا، حتى لا يبني صفقة على سعر إغلاق قديم.
2. يستخدم endpoint المجاني `/market/volume/` لفرز **حتى 50 سهمًا نشطًا** ببيانات السعر/التغير/الحجم.
3. يعمل Screening Score على هذه الأسهم.
4. يطلب `/quote/{symbol}/` فقط لأقوى **5 مرشحين** (قابلة للتعديل).
5. يطبق شروط الزخم والسيولة والسبريد وحالة TASI.
6. يبني Entry/SL/TPs بطريقة Quote-only واضحة ومخصصة للـFree، بدون ادعاء استخدام EMA/RSI/MACD/ATR غير المتاحة بدون Historical.
7. يرسل إشارة ورقية واحدة فقط إذا اجتازت الشروط.

هذا التصميم يوفر الحصة بدل إرسال 50 طلب Quote فرديًا. SAHMK Free لديه 100 طلب/يوم و10 طلبات/دقيقة، لذلك `SAHMK_MIN_REQUEST_INTERVAL=6.5` افتراضيًا.

## Probability

لا يتم اختلاق Probability. في بداية المشروع تظهر كـ`UNVALIDATED` مع عدد العينات الفعلي، **ولا تمنع أول Paper Trades**. عندما تتوفر 30 نتيجة مغلقة في نفس bucket تصبح `VALIDATED`، وعندها فقط يطبق `MIN_PROBABILITY`.

## البوتات

- Signal Bot: الأوامر والإشارات.
- Profit Bot: TP وتحديثات الأرباح.
- Loss Bot: SL وتحذير الاقتراب منه.
- Report Bot: التقرير الأسبوعي كصورة.

الأوامر:

`/start` `/help` `/signal` `/market` `/open` `/performance` `/report` `/status` `/health` `/settings` `/risk` `/pause` `/resume`

`/pause` و`/resume` للمشرفين فقط.

## Telegram على Render

`TELEGRAM_MODE=auto`:

- على Render يستخدم **Webhook** تلقائيًا عبر `RENDER_EXTERNAL_URL`، وبالتالي لا يعتمد على `getUpdates` polling ولا يفترض وجود polling ثانٍ.
- محليًا يستخدم Polling.

## Render

المشروع Web Service + Docker. أمر التشغيل داخل Docker:

```text
python -m app.main
```

الأسرار التي تضيفها فقط في Render Environment:

- `SIGNAL_BOT_TOKEN`
- `PROFIT_BOT_TOKEN`
- `LOSS_BOT_TOKEN`
- `REPORT_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SAHMK_API_KEY`

بقية الإعدادات موجودة في `render.yaml` و`.env.example`.

## قيود Render Free المهمة

Render Free Web Service قد يتوقف بعد 15 دقيقة دون **حركة HTTP واردة**، والتخزين المحلي Ephemeral؛ لذلك:

- أوامر Telegram عبر Webhook يمكنها إيقاظ الخدمة، لكن قد يوجد Cold Start.
- Scheduler ومتابعة الصفقات ليست مضمونة أثناء نوم الخدمة.
- ملفات `data/state.json` و`data/trade_history.json` قد تضيع عند restart/redeploy/spin-down.

إذا أردت متابعة صفقات 24/7 وحفظ سجل Paper Trading بشكل دائم، استخدم خدمة Render مدفوعة مع Persistent Disk أو انقل الحالة إلى datastore دائم.

## التشغيل المحلي

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main --test-telegram
python -m app.main --test-data
python -m app.main
```

## Logs المتوقعة على Render

```text
[telegram] webhook started: https://...onrender.com/telegram/webhook
[main] service + Telegram webhook started
[scheduler] started: monitor/report only; automatic signal discovery is OFF
INFO: Uvicorn running on http://0.0.0.0:10000
```

أثناء السوق وعند `/signal` سترى شيئًا مثل:

```text
[manual-scan] source=telegram selection=top_volume screened=50/50 universe=270
[manual-scan] detailed quotes returned 5/5
```

خارج ساعات السوق سيرفض `/signal` إنشاء صفقة بدل استخدام بيانات قديمة.
