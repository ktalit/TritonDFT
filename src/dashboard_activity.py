"""Sanitized, append-only activity events for browser workflow monitoring."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

ACTIVITY_FILE = "dashboard_activity.jsonl"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passphrase|totp|one[- ]?time(?: password)?|otp|api[_ -]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(/s/|/api/s/|/ws/)[A-Za-z0-9_-]{24,}"),
)


def sanitize_activity_text(value: object) -> str:
    text = str(value or "").replace("\x00", " ")[:2000]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "[REDACTED]", text)
    return text


def append_activity(run_dir: str | Path, event: str, detail: str = "", state: str = "info") -> None:
    root = Path(run_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.time(),
        "event": sanitize_activity_text(event),
        "detail": sanitize_activity_text(detail),
        "state": state if state in {"info", "working", "waiting_terminal", "success", "error"} else "info",
    }
    with (root / ACTIVITY_FILE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_activity(run_dir: str | Path, limit: int = 250) -> list[dict[str, Any]]:
    path = Path(run_dir).expanduser().resolve() / ACTIVITY_FILE
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        records.append({
            "timestamp": float(item.get("timestamp", 0)),
            "event": sanitize_activity_text(item.get("event", "Activity")),
            "detail": sanitize_activity_text(item.get("detail", "")),
            "state": item.get("state", "info"),
        })
    return records
