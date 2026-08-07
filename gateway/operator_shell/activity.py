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

import contextvars
import json
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterator, List, Optional, Tuple

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

# HOW the operator asked, as opposed to WHICH process answered (`live`, above).
#
# The two are independent and both are needed. `live` separates a human from a probe; this
# separates a tap from a typed line. Before it existed the field was hardcoded to "button"
# at its only default, so all 1,051 non-cache rows claimed a tap — including every `/panel`,
# every CEO command and every natural-language request that reached the same actions. The
# cockpit's own docs say typed and tapped are equal citizens; the log could not show one.
#
# Why a contextvar rather than a `source=` parameter threaded through the call sites:
# `handle_estate_action` is a fan-in reached from five modules (`telegram.py`,
# `slash_commands.py`, `chat_router.py`, `run.py`, the otto-inbound plugin). Threading a
# parameter would need an edit in every one of them — including `run.py`, which is off
# limits — and would silently regress to a wrong default the next time someone adds a
# caller. Setting it once per INBOUND UPDATE, at the three python-telegram-bot handlers,
# covers every present and future downstream path with no edit at the call sites.
# `contextvars` propagate into `asyncio.to_thread` and `create_task` (verified), and a
# `.set()` inside a per-update task cannot leak back into the parent context.
#
# The default is "unknown", never "button": an un-instrumented caller (a probe, a test, a
# future platform adapter) must read as unattributed rather than impersonate a human tap.
# That is the exact failure this field was added to end.
#
# Known gap, stated rather than hidden: `AdapterBase.handle_message` returns quickly by
# spawning background tasks, and `create_task` copies the context AT CREATION — which is
# exactly why the origin survives the ingress scope exiting. A request that instead gets
# queued as a pending message and drained later by a pre-existing owner task runs in that
# task's older context and records as "unknown". Non-telegram adapters are un-instrumented
# and do the same. Both degrade to unattributed, never to a fabricated tap.
_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar("activity_source", default="")

# Origins the cockpit knows how to name. Anything else is stored verbatim but rendered as
# itself — an unrecognised origin is data, not an error.
SOURCE_BUTTON = "button"      # inline-keyboard tap
SOURCE_COMMAND = "command"    # typed slash command (/panel, /missions, …)
SOURCE_CHAT = "chat"          # typed prose routed to an action by the CEO/chat router
SOURCE_UNKNOWN = "unknown"    # no ingress declared one — probes, tests, direct calls


@contextmanager
def source_scope(name: str) -> Iterator[None]:
    """Declare how the operator asked, for the duration of one inbound update.

    Nesting is allowed and the innermost scope wins, so a generic ingress can set a coarse
    origin and a more specific layer can refine it. The token is always reset, so this is
    safe even on a shared context where task isolation does not apply.
    """
    token = _SOURCE.set((name or "").strip().lower() or SOURCE_UNKNOWN)
    try:
        yield
    finally:
        _SOURCE.reset(token)


def current_source() -> str:
    """The declared origin, or "unknown" when no ingress declared one."""
    return _SOURCE.get() or SOURCE_UNKNOWN


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
    source: Optional[str] = None,
    served: str = "",
) -> None:
    """Append one row. Never raises — see module docstring.

    `source` is WHO asked and defaults to the ambient `source_scope` (see `_SOURCE`); pass it
    explicitly only to override that. `served` is HOW the answer was produced — currently
    only "cache". They are separate keys because they are separate facts: a typed command
    answered from the pre-flight cache is still a typed command, and the previous code lost
    that by writing "cache" into `source` itself.
    """
    try:
        raw = (action or "").strip()
        head, _, arg = raw.partition(":")
        row: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "iso": datetime.now().isoformat(timespec="seconds"),
            "action": head.lower(),
            "arg": arg,
            "request_id": request_id,
            "source": source if source is not None else current_source(),
            "status": status,
            "ms": round(ms, 1),
        }
        if served:
            row["served"] = served
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


def origin(row: Dict[str, Any]) -> str:
    """How the operator asked for this row, normalised for reading.

    Rows written before `served` existed stored the string "cache" in `source`, which
    overwrote the origin instead of sitting beside it. Those rows genuinely do not know
    whether they were tapped or typed, so they read as "unknown" rather than being guessed
    into a bucket — 228 of them exist and inventing an origin for them would put fiction
    into the one file that is supposed to be evidence.
    """
    val = str(row.get("source") or "").strip().lower()
    if not val or val == "cache":
        return SOURCE_UNKNOWN
    return val


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
    by_source: Counter = Counter()
    served_cache = 0
    durations: Dict[str, List[float]] = {}
    for r in rows:
        lab = _label(r)
        used[lab] += 1
        by_source[origin(r)] += 1
        if str(r.get("served") or "") == "cache" or str(r.get("source") or "") == "cache":
            served_cache += 1
        if _failed(r):
            failed[lab] += 1
        ms = float(r.get("ms") or 0.0)
        if ms > 0:
            durations.setdefault(lab, []).append(ms)
    # One row per ACTION, not per call. Ranked by worst because "Slowest" is asking about the
    # worst experience the operator had — but a single outlier among a hundred fast calls
    # would then read as a chronically slow action, so `typical` (the median) ships beside it
    # and `n` says how many calls it summarises. Without the de-dup, one repeated action fills
    # every row: measured on the live store 2026-08-06, st_health×3 + st_money×2 took all 5,
    # hiding st_status (65.7s), run (37.4s) and refresh (36.7s) entirely.
    slow: List[Tuple[float, str, int, float]] = []
    for lab, vals in durations.items():
        vals.sort()
        slow.append((vals[-1], lab, len(vals), median(vals)))
    slow.sort(key=lambda e: e[0], reverse=True)
    return {
        "days": days,
        "total": len(rows),
        "synthetic": len(everything) - len(rows),
        "distinct": len(used),
        "top": used.most_common(8),
        "failures": failed.most_common(6),
        "failure_total": sum(failed.values()),
        "slowest": slow[:5],
        "by_source": dict(by_source),
        "served_cache": served_cache,
        "rows": rows,
    }
