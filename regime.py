def classify_tasi(summary):
    v=summary.get("change_percent",summary.get("change_pct"))
    try: v=float(v)
    except (TypeError,ValueError): return "NEUTRAL"
    return "BULLISH" if v>=.75 else "BEARISH" if v<=-.75 else "NEUTRAL"
