from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    signal_bot_name: str = "TASI_KSA_signal_bot"
    signal_bot_token: str
    profit_bot_name: str = "TASI_KSA_profit11_bot"
    profit_bot_token: str
    loss_bot_name: str = "TASI_KSA_loss1122_bot"
    loss_bot_token: str
    report_bot_name: str = "TASI_KSA_report112233_bot"
    report_bot_token: str
    telegram_chat_id: int

    sahmk_api_key: str
    sahmk_base_url: str = "https://api.sahmk.sa/api/v1"

    state_dir: str = "data"
    health_interval: int = 600
    # Scheduler is for monitoring/reporting only. It NEVER creates new signals.
    scan_interval_seconds: int = 3600
    trade_monitor_quotes_per_cycle: int = 1
    manual_quotes_per_signal: int = 50
    market_cache_seconds: int = 3600
    universe_refresh_seconds: int = 21600

    market_open: str = "10:00"
    market_close: str = "15:00"
    timezone: str = "Asia/Riyadh"

    min_score: float = 75
    min_probability: float = 65
    max_daily_signals: int = 3
    max_open_trades: int = 5
    max_risk_per_trade: float = 0.01
    data_max_delay_minutes: int = 30
    min_rr: float = 1.5
    tp1_percent: float = 30
    tp2_percent: float = 30
    tp3_percent: float = 40
    slippage_bps: float = 5
    fee_bps: float = 15
    allow_long: bool = True
    paper_mode: bool = True

    trailing_stop_enabled: bool = False
    trailing_after_tp1_to_entry: bool = True
    trailing_after_tp2_atr: float = 1.0
    profit_alert_thresholds: str = "2,5,10,15,20"
    near_sl_warning_pct: float = 0.5
    weekly_report_enabled: bool = True
    weekly_report_weekday: int = 3  # Thursday
    weekly_report_hour: int = 15
    weekly_report_minute: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
