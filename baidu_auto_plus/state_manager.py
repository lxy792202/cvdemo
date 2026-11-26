import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent

def state_path(config):
    p = ROOT / config["state_file"]
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def load_state(config):
    p = state_path(config)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(config, state):
    p = state_path(config)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def set_task(config, state, task_key, status, detail=""):
    state[task_key] = {
        "status": status,
        "detail": detail,
        "updated_at": datetime.now().isoformat(timespec="seconds")
    }
    save_state(config, state)
