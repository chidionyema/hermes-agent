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
import os
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

# Which process wrote a row. Without this the log cannot be used as evidence: on the day it
# shipped, 489 rows accumulated and **only 56 were real operator taps** — the rest were BFS
# reachability probes and test sweeps calling `handle_estate_action` directly, which is the
# same funnel a real tap goes through and therefore indistinguishable after the fact. A
# frequency-ranked layout built on that file would have been ranking its own instrumentation.
#
# Attribution is mechanical, not cooperative: a real tap is dispatched inside the gateway
# process, and every probe and test runs in its own interpreter. Nothing has to remember to
# declare itself.
_GW_PID_TTL = 30.0
_gw_pid_cache: Tuple[float, Optional[int]] = (0.0, None)


def _gateway_pid() -> Optional[int]:
    """PID of the live gateway, or None when it cannot be determined.

    None is a real answer and is recorded as such. Under pytest HERMES_HOME is a per-test
    tempdir with no pidfile, so attribution is genuinely unknown there — and a row that
    cannot be attributed must not be silently counted as either real or synthetic.
    """
    global _gw_pid_cache
    now = time.monotonic()
    stamp, cached = _gw_pid_cache
    if now - stamp < _GW_PID_TTL:
        return cached
    pid: Optional[int] = None
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path.home() / ".hermes"
    try:
        raw = (home / "gateway.pid").read_text(encoding="utf-8").strip()
        # The pidfile is JSON ({"pid": ..., "kind": ...}), but tolerate a bare integer:
        # this must never raise on the hot path of every tap.
        pid = int(json.loads(raw)["pid"]) if raw.startswith("{") else int(raw)
    except Exception:
        pid = None
    _gw_pid_cache = (now, pid)
    return pid


def is_live(row: Dict[str, Any]) -> bool:
    """Did a human tap produce this row?

    Unknown counts as live. The alternative — treating unattributable rows as synthetic —
    would silently discard every row written before this field existed, and would empty the
    Tune promotion list on any estate whose pidfile is missing.
    """
    return bool(row.get("live", True))


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
        # Attribution. `live` is omitted entirely when the gateway PID is unknown, so the
        # absence of the key means "cannot say", never "not real" — see `is_live`.
        row["pid"] = os.getpid()
        gw = _gateway_pid()
        if gw is not None:
            row["live"] = os.getpid() == gw
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


def recent_knob_keys(limit: int = 2, days: int = 30) -> List[str]:
    """Knobs the operator actually changed, most recent first, de-duplicated.

    This is what makes the Tune index adaptive instead of alphabetical. A knob you have
    touched is overwhelmingly the knob you will touch again — leverage before a rail change,
    daily_cap when the burn moves — while most of the 29 are set once and never revisited.
    Promoting the recent ones to the index puts them at two taps without recreating the
    28-button screen that grouping was introduced to kill.

    Only *successful* sets count. A failed set is not evidence of intent to keep using it,
    and promoting a knob because it keeps erroring would be exactly backwards.

    Only *human* sets count, for the same reason. A reachability probe walks every knob value
    on the estate — it set 41 of them on the day this shipped — so without the `live` filter
    the promotion list would be ranking whichever knob the last sweep happened to touch last.
    """
    keys: List[str] = []
    for row in reversed(read_days(days)):
        if str(row.get("action") or "") not in ("se_set_confirm", "pd_set_confirm"):
            continue
        if _failed(row) or not is_live(row):
            continue
        key = str(row.get("arg") or "").split(":")[0].strip()
        if key and key not in keys:
            keys.append(key)
        if len(keys) >= limit:
            break
    return keys


def _label(row: Dict[str, Any]) -> str:
    act = str(row.get("action") or "?")
    arg = str(row.get("arg") or "")
    return f"{act}:{arg}" if arg else act


def _failed(row: Dict[str, Any]) -> bool:
    return str(row.get("status")) in ("failed", "error") or str(row.get("outcome")) == "failed"


def rollup(days: int = 7, live_only: bool = True) -> Dict[str, Any]:
    """Usage and failure shape over the window — the part that drives improvement.

    `live_only` by default: the panel answers "what did *I* do and what broke", and a probe
    sweep would otherwise dominate every ranking on it. `synthetic` is reported alongside the
    total so a suppressed sweep is visible rather than silently dropped — a panel that hides
    how much it filtered is how you end up trusting a number you should not.
    """
    everything = read_days(days)
    rows = [r for r in everything if is_live(r)] if live_only else everything
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
        "synthetic": len(everything) - len(rows),
        "distinct": len(used),
        "top": used.most_common(8),
        "failures": failed.most_common(6),
        "failure_total": sum(failed.values()),
        "slowest": slow[:5],
        "rows": rows,
    }
