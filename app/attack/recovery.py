import json
import time
from pathlib import Path

from app.config import RECOVERY_DIR


def recovery_state_path(task_id: int) -> Path:
    return RECOVERY_DIR / f"task_{task_id}.json"


def write_recovery_state(task_id: int, state: dict):
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["task_id"] = task_id
    payload["updated_at"] = time.time()
    path = recovery_state_path(task_id)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def read_recovery_state(task_id: int) -> dict | None:
    path = recovery_state_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_recovery_state(task_id: int):
    recovery_state_path(task_id).unlink(missing_ok=True)
