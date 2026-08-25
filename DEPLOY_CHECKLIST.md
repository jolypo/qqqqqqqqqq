# Render deployment checklist

1. Upload this project to GitHub. Do **not** commit a real `.env` file.
2. On Render, create/sync a **Web Service / Blueprint** using `render.yaml` and Docker.
3. Add only these secret values:
   - `SIGNAL_BOT_TOKEN`
   - `PROFIT_BOT_TOKEN`
   - `LOSS_BOT_TOKEN`
   - `REPORT_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `SAHMK_API_KEY`
4. Keep `TELEGRAM_MODE=auto`, `SAHMK_PLAN=free`, `MANUAL_QUOTES_PER_SIGNAL=50`, `DETAIL_QUOTES_PER_SIGNAL=5`.
5. Expected startup log on Render:
   - `[telegram] webhook started: https://.../telegram/webhook`
   - `[main] service + Telegram webhook started`
   - `[scheduler] started: monitor/report only; automatic signal discovery is OFF`
   - `Uvicorn running on http://0.0.0.0:10000`
6. Test `/start`, `/health`, `/status`, `/market`. Test `/signal` only during Saudi market hours (Sunday–Thursday, 10:00–15:00 Riyadh time).

## Infrastructure limitation

Render Free can sleep after 15 minutes without inbound HTTP and its local filesystem is ephemeral. Therefore continuous trade monitoring and persistent JSON history are **not guaranteed** on Render Free. For 24/7 monitoring + durable Paper Trade history, use a paid Render instance with a Persistent Disk or a durable external datastore.
