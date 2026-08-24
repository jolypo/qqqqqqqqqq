import json
from pathlib import Path
from threading import Lock


DEFAULT_STATE = {
    "open_trades": [],
    "daily_signals": {},
    "paused": False,
    "meta": {},
}


class JsonStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self.state_path = self.directory / "state.json"
        self.history_path = self.directory / "trade_history.json"

    def _load(self, path, default):
        if not path.exists():
            return default.copy() if isinstance(default, dict) else list(default)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default.copy() if isinstance(default, dict) else list(default)

    def _save(self, path, value):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def state(self):
        with self.lock:
            state = self._load(self.state_path, DEFAULT_STATE)
            for key, default in DEFAULT_STATE.items():
                if key not in state:
                    state[key] = default.copy() if isinstance(default, dict) else list(default) if isinstance(default, list) else default
            return state

    def history(self):
        with self.lock:
            return self._load(self.history_path, [])

    def save_state(self, value):
        with self.lock:
            self._save(self.state_path, value)

    def save_history(self, value):
        with self.lock:
            self._save(self.history_path, value)
