from datetime import datetime, timezone


class TradeManager:
    def __init__(self, store, settings):
        self.store = store
        self.s = settings

    def add(self, signal):
        state = self.store.state()
        trades = state["open_trades"]
        if len(trades) >= self.s.max_open_trades:
            return False
        if any(x["symbol"] == signal.symbol for x in trades):
            return False

        trade = signal.to_dict()
        trade.update(
            {
                "status": "OPEN",
                "current_price": signal.entry,
                "max_profit_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "sl_hit": False,
                "profit_alerts_sent": [],
                "near_sl_warning_sent": False,
                "trailing_stop": None,
                "exit": None,
                "exit_time": None,
                "result": None,
                "result_pct": None,
            }
        )
        trades.append(trade)
        self.store.save_state(state)
        return True

    def update(self, symbol, price):
        state = self.store.state()
        for trade in state["open_trades"]:
            if trade["symbol"] != symbol:
                continue

            entry = float(trade["entry"])
            price = float(price)
            pct = (price - entry) / entry * 100
            trade["current_price"] = price
            trade["max_profit_pct"] = max(float(trade.get("max_profit_pct", 0)), pct)
            trade["max_drawdown_pct"] = min(float(trade.get("max_drawdown_pct", 0)), pct)
            events = []

            effective_sl = float(trade.get("trailing_stop") or trade["sl"])
            if price <= effective_sl:
                trade["sl_hit"] = True
                trade["status"] = "CLOSED_SL"
                trade["exit"] = price
                trade["exit_time"] = datetime.now(timezone.utc).isoformat()
                trade["result"] = "LOSS" if pct < 0 else "WIN"
                trade["result_pct"] = pct
                events.append("SL")
            else:
                for key in ("tp1", "tp2", "tp3"):
                    hit_key = f"{key}_hit"
                    if price >= float(trade[key]) and not trade.get(hit_key, False):
                        trade[hit_key] = True
                        events.append(key.upper())

                if trade.get("tp3_hit") and trade.get("status") == "OPEN":
                    trade["status"] = "CLOSED_TP3"
                    trade["exit"] = price
                    trade["exit_time"] = datetime.now(timezone.utc).isoformat()
                    trade["result"] = "WIN"
                    trade["result_pct"] = pct
                    events.append("CLOSE_TP3")

            if trade.get("status", "").startswith("CLOSED"):
                history = self.store.history()
                history.append(dict(trade))
                state["open_trades"] = [x for x in state["open_trades"] if x is not trade]
                self.store.save_history(history)

            self.store.save_state(state)
            return trade, events

        return None, []

    def apply_trailing(self, trade, price, atr=None):
        if not self.s.trailing_stop_enabled or not trade or trade.get("status") != "OPEN":
            return False
        changed = False
        current = float(trade.get("trailing_stop") or trade["sl"])
        new_stop = current
        if trade.get("tp1_hit") and self.s.trailing_after_tp1_to_entry:
            new_stop = max(new_stop, float(trade["entry"]))
        if trade.get("tp2_hit") and atr and atr > 0:
            new_stop = max(new_stop, float(price) - float(atr) * self.s.trailing_after_tp2_atr)
        if new_stop > current:
            trade["trailing_stop"] = round(new_stop, 2)
            changed = True
        return changed
