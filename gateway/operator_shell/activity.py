"""Append-only record of what the operator did in the cockpit, and how it ended.

Why this exists: `Proof` receipts were *rendered and discarded*. The cockpit could tell you
the outcome of the action you had just taken and nothing else — no way to answer "what did I
tap last night", "which button fails most", "did that restart actually work". `undo_stack.jsonl`
only ever held the small subset of actions that were undoable.

Two rules this file keeps:

- **Log at the funnel, not at the call sites.** Every panel and verb reaches
  `estate.handle_estate_action`, so one wrapper there captures 100% of paths — including the
  ones that raise, which are exactly the ones worth auditing. Per-action logging would drift
  the moment someone adds a branch.
- **Recording must never break the action.** Every write is inside a bare `except Exception`.
  An audit trail that can take the cockpit down is a liability, not an asset.

Storage: `~/.hermes/meta/operator_shell/activity/<YYYY-MM-DD>.jsonl`, one JSON object per line,
daily files so a read never has to parse history it does not need.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Proof.render() writes "✅ *DONE* — ..." / "⚠️ *FAILED* — ...". Pull the status back out
# rather than threading it separately: the receipt is what the operator actually saw.
_STATUS_RE = re.compile(r"\*([A-Z_]+)\*")

_RETENTION_DAYS = 90


def _dir() -> Path:
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path.home() / ".hermes"
    d = home / "meta" / "operator_shell" / "activity"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(day: Optional[datetime] = None) -> Path:
    return _dir() / f"{(day or datetime.now()).strftime('%Y-%m-%d')}.jsonl"


def record(
    action: str,
    request_id: str = "",
    *,
    status: str = "ok",
    ms: float = 0.0,
    view: Any = None,
    error: str = "",
    source: str = "button",
) -> None:
    """Append one row. Never raises — see module docstring."""
    try:
        raw = (action or "").strip()
        head, _, arg = raw.partition(":")
        row: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "iso": datetime.now().isoformat(timespec="seconds"),
            "action": head.lower(),
            "arg": arg,
            "request_id": request_id,
            "source": source,
            "status": status,
            "ms": round(ms, 1),
        }
        if error:
            # Bound it: a traceback repr can be kilobytes and this file is read on a phone.
            row["error"] = error[:300]
        if view is not None:
            receipt = getattr(view, "proof_receipt", "") or ""
            m = _STATUS_RE.search(receipt)
            if m:
                row["outcome"] = m.group(1).lower()
            toast = getattr(view, "toast", "") or ""
            if toast:
                row["toast"] = toast[:80]
            if getattr(view, "ok", True) is False:
                row["status"] = "failed"
            paused = getattr(view, "paused", None)
            if paused is not None:
                row["paused"] = bool(paused)
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def prune(retention_days: int = _RETENTION_DAYS) -> int:
    """Drop day-files older than the retention window. Returns how many were removed."""
    removed = 0
    try:
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        for p in _dir().glob("*.jsonl"):
            if p.stem < cutoff:
                p.unlink()
                removed += 1
    except Exception:
        pass
    return removed


def read_days(days: int = 7) -> List[Dict[str, Any]]:
    """Rows from the last `days` day-files, oldest first. Bad lines are skipped, not fatal."""
    out: List[Dict[str, Any]] = []
    now = datetime.now()
    for i in range(days - 1, -1, -1):
        # _path() touches the filesystem (mkdir), so it can raise on a full or read-only disk.
        # Reading the audit trail must degrade to "no rows", never take the panel down.
        try:
            p = _path(now - timedelta(days=i))
        except Exception:
            continue
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            continue
    return out


def _label(row: Dict[str, Any]) -> str:
    act = str(row.get("action") or "?")
    arg = str(row.get("arg") or "")
    return f"{act}:{arg}" if arg else act


def _failed(row: Dict[str, Any]) -> bool:
    return str(row.get("status")) in ("failed", "error") or str(row.get("outcome")) == "failed"


def rollup(days: int = 7) -> Dict[str, Any]:
    """Usage and failure shape over the window — the part that drives improvement."""
    rows = read_days(days)
    used: Counter = Counter()
    failed: Counter = Counter()
    slow: List[Tuple[float, str]] = []
    for r in rows:
        lab = _label(r)
        used[lab] += 1
        if _failed(r):
            failed[lab] += 1
        ms = float(r.get("ms") or 0.0)
        if ms > 0:
            slow.append((ms, lab))
    slow.sort(reverse=True)
    return {
        "days": days,
        "total": len(rows),
        "distinct": len(used),
        "top": used.most_common(8),
        "failures": failed.most_common(6),
        "failure_total": sum(failed.values()),
        "slowest": slow[:5],
        "rows": rows,
    }
