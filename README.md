# TASI KSA Signal Bot — Manual Signals + Paper Trading

مشروع Python للسوق السعودي TASI يعمل على Render مع 4 Telegram Bots وSAHMK.

## الوضع التشغيلي

**اكتشاف الصفقات ليس تلقائيًا.**

- `/signal` فقط يبدأ فحصًا يدويًا ويبحث عن فرصة جديدة.
- Scheduler لا ينشئ أي صفقة جديدة.
- Scheduler مخصص لمتابعة الصفقات الورقية المفتوحة والتقارير/إشعار إغلاق السوق.
- `PAPER_MODE=true` إلزامي في النسخة الحالية؛ لا يوجد تنفيذ شراء/بيع حقيقي.

## البوتات الأربعة

1. **Signal Bot** — الإشارة الجديدة + أوامر Telegram.
2. **Profit Bot** — TP1/TP2/TP3 وتحديثات الربح 2/5/10/15/20% مرة واحدة لكل مستوى.
3. **Loss Bot** — تحذير الاقتراب من SL ثم إشعار SL.
4. **Report Bot** — التقرير الأسبوعي كصورة إنجليزية.

## Telegram Commands

`/start` `/help` `/signal` `/market` `/open` `/performance` `/report` `/status` `/health` `/settings` `/risk` `/pause` `/resume`

`/pause` و`/resume` للمشرفين فقط.

## استهلاك SAHMK

الخطة المجانية ذات الحصة الصغيرة لا تسمح بفحص 270 Quote في كل دورة.

لذلك الإعداد الافتراضي الآمن هو:

- `MANUAL_QUOTES_PER_SIGNAL=5`: كل `/signal` يفحص 5 أسهم في الجولة الحالية.
- المؤشر `scan_cursor` يتحرك في Universe، لذلك استدعاءات `/signal` المتكررة تكمل تغطية السوق بدل إعادة أول الأسهم.
- `TRADE_MONITOR_QUOTES_PER_CYCLE=1`: المتابعة الدورية تراقب صفقة مفتوحة واحدة في كل دورة.
- `SCAN_INTERVAL_SECONDS=3600`: دورة المتابعة كل ساعة، وليست دورة اكتشاف.
- Market Summary وUniverse لهما cache/تحديث دوري.
- HTTP 429 يحترم `Retry-After` مع backoff وcooldown محدود.
- لا يوجد endpoint Bulk غير موثق.

**مهم:** هذا يعني أن `/signal` الواحد لا يستهلك Request واحدًا بالضرورة. قد يستخدم Market Summary + Quotes + Historical للسهم المرشح. كما أن المتابعة الدورية تستهلك Quote للصفقات المفتوحة. لذلك يجب مراقبة `/health` لمعرفة `SAHMK Requests` و`429`.

إذا كان الهدف فحص **كل 270+ سهمًا في أمر `/signal` واحد**، فالخطة المجانية لن تكون مناسبة ما لم يوفر مزود البيانات Bulk Quotes موثقًا وبحصة كافية.

## Probability

Probability ليست رقم AI. لا يتم إصدار Signal إلا عندما تكون الـProbability **VALIDATED** من نتائج Paper Trading السابقة في نفس bucket وبحد أدنى 30 نتيجة. في البداية قد لا يصدر النظام أي إشارة حتى تتوفر بيانات كافية.

## Data Delay

البيانات من SAHMK تعمل في `data_mode=delayed`. النظام لا يدّعي أنها Live، ويتحقق من عمر `updated_at` عندما يوفره المزود.

## التخزين

لا توجد Database. التخزين JSON:

- `data/state.json`
- `data/trade_history.json`
- `data/reports/weekly_report.png`

على Render Free التخزين المحلي ليس ضمانًا دائمًا بعد إعادة إنشاء الخدمة؛ هذا مناسب للتجربة/Paper Trading وليس سجلًا دائمًا موثوقًا.

## التشغيل المحلي

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows
# أو cp .env.example .env على Linux/macOS
python -m app.main --test-telegram
python -m app.main --test-data
python -m app.main
```

## Render

المشروع يستخدم Docker Web Service لأن FastAPI health endpoint مطلوب، وStart Command داخل Docker هو:

```text
python -m app.main
```

إذا أدخلت Start Command يدويًا في Render استخدم نفس الأمر.

لا ترفع `.env` إلى GitHub. ضع الأسرار في Render Environment Variables:

- `SIGNAL_BOT_TOKEN`
- `PROFIT_BOT_TOKEN`
- `LOSS_BOT_TOKEN`
- `REPORT_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SAHMK_API_KEY`

وبقية المتغيرات موجودة في `.env.example` و`render.yaml`.

## اختبار Telegram

```bash
python -m app.main --test-telegram
```

يجب أن يرسل أربعة رسائل اتصال إلى المجموعة.

ثم التشغيل الطبيعي:

```bash
python -m app.main
```

في Telegram اختبر بالترتيب:

```text
/start
/health
/status
/market
/signal
/open
/performance
/settings
/risk
/pause
/resume
/report
```

## Render Logs المتوقعة

```text
Application startup complete.
[telegram] command polling started
[main] service + Telegram command polling started
[scheduler] started: monitor/report only; automatic signal discovery is OFF
```

وعند `/signal`:

```text
[manual-scan] source=telegram quotes=5 cursor=5/270
```

وعند عدم وجود فرصة:

```text
🔎 اكتمل الفحص اليدوي...
```

وعند وجود صفقة validated:

```text
[signal] sent 1234
```

## الأمان

لا تضع Telegram Tokens أو SAHMK API Key في source code أو GitHub.

وبما أن التوكنات التي ظهرت سابقًا في المحادثة ينبغي اعتبارها مكشوفة، يفضّل تدويرها من BotFather قبل الاستخدام النهائي.
