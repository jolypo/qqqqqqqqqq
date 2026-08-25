def _fmt(value, digits=2):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def signal_message(t):
    probability_status = t.get("probability_status", "UNVALIDATED")
    if probability_status == "VALIDATED":
        probability_line = (
            f"📊 Probability: {_fmt(t.get('probability'), 1)}% "
            f"({t.get('probability_samples', 0)} samples)"
        )
    else:
        probability_line = (
            "📊 Probability: غير موثقة بعد "
            f"| العينات: {t.get('probability_samples', 0)}"
        )

    strategy = t.get("strategy", "—")
    basis = (
        "SAHMK Free quote-only screening"
        if strategy == "FREE_QUOTE_MOMENTUM"
        else "Historical technical analysis"
    )

    return (
        "🚨 فرصة تداول ورقية جديدة\n\n"
        f"السهم: {t['name']}\n"
        f"الرمز: {t['symbol']}\n\n"
        "📈 الاتجاه: شراء\n\n"
        f"💰 منطقة الدخول: {_fmt(t['entry_low'])} – {_fmt(t['entry_high'])}\n"
        f"🛑 وقف الخسارة: {_fmt(t['sl'])}\n"
        f"🎯 TP1: {_fmt(t['tp1'])}\n"
        f"🎯 TP2: {_fmt(t['tp2'])}\n"
        f"🎯 TP3: {_fmt(t['tp3'])}\n\n"
        f"{probability_line}\n"
        f"⭐ Screening Score: {_fmt(t['score'], 1)}/100\n"
        f"⚖️ R/R: 1:{_fmt(t['rr_tp1'])}\n"
        f"🏦 القطاع: {t.get('sector', 'Unknown')}\n"
        f"🌐 حالة السوق: {t.get('market_regime', 'NEUTRAL')}\n"
        f"🧭 الاستراتيجية: {strategy}\n"
        f"🕒 وقت الاكتشاف: {t.get('discovered_at', '—')}\n\n"
        "⏳ التوقع الزمني\n"
        f"TP1: {t.get('expected_tp1', '—')}\n"
        f"TP2: {t.get('expected_tp2', '—')}\n"
        f"TP3: {t.get('expected_tp3', '—')}\n\n"
        f"🔬 أساس التحليل: {basis}\n"
        "📡 البيانات: SAHMK delayed (~15 min on Free)\n"
        "⚠️ Paper Trading — لا يوجد تداول حقيقي"
    )


def profit_message(t, price, delta):
    pct = (price - t["entry"]) / t["entry"] * 100
    return (
        "🟢 تحديث الأرباح\n\n"
        f"{t['name']} — {t['symbol']}\n"
        f"الدخول: {_fmt(t['entry'])}\n"
        f"السعر الحالي: {_fmt(price)}\n"
        f"الحركة: {delta:+.2f} ريال\n"
        f"الربح: {pct:+.2f}%\n"
        "الحالة: الصفقة مستمرة."
    )


def loss_message(t, price):
    pct = (price - t["entry"]) / t["entry"] * 100
    return (
        "🔴 وقف الخسارة تحقق\n\n"
        f"{t['name']} — {t['symbol']}\n"
        f"الدخول: {_fmt(t['entry'])}\n"
        f"الخروج: {_fmt(price)}\n"
        f"النتيجة: {pct:+.2f}%\n"
        "الحالة: الصفقة مغلقة."
    )


def near_sl_message(t, price):
    stop = t.get("trailing_stop") or t.get("sl")
    return (
        "⚠️ اقتراب من وقف الخسارة\n\n"
        f"السهم: {t['name']} ({t['symbol']})\n"
        f"السعر الحالي: {_fmt(price)}\n"
        f"وقف الخسارة الفعّال: {_fmt(stop)}\n"
        "الحالة: الصفقة ما زالت مفتوحة."
    )


def tp_message(t, tp_name, price):
    pct = (price - t["entry"]) / t["entry"] * 100
    return (
        f"🎯 {tp_name} تحقق\n\n"
        f"السهم: {t['name']} ({t['symbol']})\n"
        f"الدخول: {_fmt(t['entry'])}\n"
        f"السعر: {_fmt(price)}\n"
        f"الربح: {pct:+.2f}%\n"
        f"الحالة: {tp_name} تحقق."
    )
