#!/usr/bin/env python3
"""otto_panel — the one screen that answers "what is Otto doing, and is it working?"

Reached by `estate:otto` (registry) and `/otto` (typed). Read-only: it opens
`coordinator.db` with `mode=ro`, shells out to `launchctl list` once, and reads log
files. It writes nothing — unlike `otto_health`, which appends a velocity row on every
render (`otto_health.py:220-225`).

Why this exists (audit 2026-08-07, `checkpoints/2026-08-07-otto-audit.md`): every number
below was knowable only from a laptop with sqlite and a shell. `_PANELS` had 14 entries
and exactly two touched Otto, both of them scores rather than state. The four failures
that audit found — a 30s executor cap, 243 tasks stranded in a status the state machine
does not know, zero landed self-improvements, write-only idle learning — were each
invisible from the phone.

Two rules this file holds to, because the audit was caused by breaking them elsewhere:

  1. **No constant that looks like telemetry.** Every figure is read at render time from
     a file or a probe. `rsi_control.py:94-98` records the last time a hardcoded
     "110 pass / 15 fail" was rendered here as if it were live.
  2. **The lifecycle sets are PARSED from `scripts/coordinator.py`, not copied.** A status
     that appears in neither `ACTIVE` nor `TERMINAL` is stranded — no tick will ever
     select it. Copying the tuples would make this panel agree with a stale belief instead
     of with the daemon; parsing means the STRANDED line disappears by itself the day
     someone adds `failed` to a retry path.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ButtonRow = List[Tuple[str, str]]

# The five launchd labels that make up Otto. `otto-server` is included precisely because
# it is usually NOT loaded: a label absent from `launchctl list` prints nothing at all,
# which reads identically to "fine" unless the panel knows to expect it.
SERVICES = (
    ("ai.hermes.coordinator", "coordinator"),
    ("ai.hermes.idle-engine", "idle engine"),
    ("ai.hermes.rsi", "rsi"),
    ("ai.hermes.gateway", "gateway"),
    ("ai.hermes.otto-server", "otto server"),
)

_MD_ACTIVE = re.compile(r"[*_`\[\]]")


def _home() -> Path:
    """Resolved per call, not at import: the tests point HERMES_HOME at a tmp estate, and
    a module-level constant would bind to the real one at collection time."""
    return Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))


def _plain(text: str, limit: int = 120) -> str:
    """Strip Telegram legacy-markdown actives out of interpolated text.

    A single `_` from `prompt-tune(EXECUTE_PROMPT)` is an unbalanced italic marker and
    Telegram rejects the whole message, so the panel would render as a send failure
    rather than as a wrong character.
    """
    return _MD_ACTIVE.sub("", str(text)).strip()[:limit]


def _age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "never"
    if seconds < 0:
        return "just now"
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{int(seconds / 86400)}d ago"


def _epoch_age(value) -> Optional[float]:
    """`tasks.created_at` and `events.created_at` are float epochs, NOT ISO strings.
    A subagent reading them as ISO reported dates like 1785676039 as if they were real."""
    try:
        return time.time() - float(value)
    except (TypeError, ValueError):
        return None


def _iso_age(value) -> Optional[float]:
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


# ── source-of-truth parsing ─────────────────────────────────────────────────────────────


def _coordinator_literal(name: str, default):
    """Read one module-level literal out of `scripts/coordinator.py` without importing it.

    Importing coordinator pulls in its whole daemon surface (and `sys.path` games) for the
    sake of two tuples; `ast` gets the same answer with no side effects and no risk that
    rendering a panel starts something.
    """
    path = _home() / "scripts" / "coordinator.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return default
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    return default
    return default


def _lifecycle() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    active = tuple(_coordinator_literal("ACTIVE", ()))
    terminal = tuple(_coordinator_literal("TERMINAL", ()))
    return active, terminal


def _fallback_markers() -> Tuple[str, ...]:
    return tuple(_coordinator_literal("FALLBACK_MARKERS", ()))


# ── probes ──────────────────────────────────────────────────────────────────────────────


def _launchd() -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """label -> (pid or None, last exit status or None). Absent label => not loaded."""
    try:
        proc = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    table: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, status, label = parts[0].strip(), parts[1].strip(), parts[2].strip()
        table[label] = (None if pid == "-" else pid, status)
    return table


def _work() -> dict:
    """Task lifecycle out of coordinator.db. Read-only URI + close in `finally`:
    `with sqlite3.connect(...)` commits, it does not close (memory:
    sqlite-with-conn-does-not-close.md)."""
    db = _home() / "coordinator.db"
    out: dict = {"present": db.is_file(), "counts": {}, "newest_event_age": None,
                 "fallback_done": 0, "clean_done": 0, "error": ""}
    if not out["present"]:
        return out
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        out["error"] = _plain(exc)
        return out
    try:
        cur = conn.cursor()
        out["counts"] = {
            str(status): int(count)
            for status, count in cur.execute(
                "select status, count(*) from tasks group by status"
            )
        }
        row = cur.execute("select max(created_at) from events").fetchone()
        out["newest_event_age"] = _epoch_age(row[0] if row else None)

        markers = _fallback_markers()
        if markers:
            clause = " or ".join(["result like ?"] * len(markers))
            params = [f"%{m}%" for m in markers]
            out["fallback_done"] = int(
                cur.execute(
                    f"select count(*) from tasks where status='done' and ({clause})",
                    params,
                ).fetchone()[0]
            )
            out["clean_done"] = int(
                cur.execute(
                    f"select count(*) from tasks where status='done' "
                    f"and not ({clause})",
                    params,
                ).fetchone()[0]
            )
    except sqlite3.Error as exc:
        out["error"] = _plain(exc)
    finally:
        conn.close()
    return out


def _jsonl_count(path: Path, on_day: Optional[str] = None) -> int:
    """Rows in a .jsonl, optionally only those whose ts/timestamp starts with `on_day`."""
    if not path.is_file():
        return 0
    total = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            if on_day is None:
                total += 1
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            stamp = str(row.get("ts") or row.get("timestamp") or "")
            if stamp.startswith(on_day):
                total += 1
    except OSError:
        return 0
    return total


def _learning() -> dict:
    home = _home()
    policies = home / "policies"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    reflections = sorted((home / "logs" / "reflection").glob("*.md")) \
        if (home / "logs" / "reflection").is_dir() else []

    state, insights_pending = {}, None
    state_file = home / "state" / "idle_engine" / "state.json"
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}

    queue = home / "state" / "insight_queue.jsonl"
    if queue.is_file():
        insights_pending = _jsonl_count(queue)

    # `meta/OFF_SWITCH` PRESENT means ARMED. The polarity is inverted from what the name
    # suggests and is declared canonical in scripts/learning_switch.py:4-6; rsi_control's
    # own toggle writes a DIFFERENT file (logs/meta-improver/OFF_SWITCH), which is why its
    # pause button is quarantined rather than wired.
    return {
        "armed": (home / "meta" / "OFF_SWITCH").is_file(),
        "policies": len(list(policies.glob("*.json"))) if policies.is_dir() else 0,
        "firings_today": _jsonl_count(home / "logs" / "policy-firings.jsonl", today),
        "injections_today": _jsonl_count(home / "logs" / "injection-log.jsonl", today),
        "last_reflection": reflections[-1].stem if reflections else "never",
        "cycles": state.get("cycles", 0),
        "insights": state.get("insights", 0),
        "last_cycle_age": _iso_age(state.get("last_cycle")),
        "insights_pending": insights_pending,
    }


_TUNE = re.compile(r"prompt-tune\(([^)]*)\)\s+exit=(-?\d+)")


def _self_improvement() -> dict:
    home = _home()
    log = home / "logs" / "rsi-autorun.log"
    out = {
        "log": log.is_file(),
        "last_run_age": None,
        "last_verdict": "",
        "landed": 0,
        "attempts": 0,
        "goals_total": 0,
        "goals_unmeasured": 0,
    }
    if log.is_file():
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        # The only line that means a variant beat the gate and was staged.
        out["landed"] = text.count("Verification succeeded on attempt")
        out["attempts"] = len(_TUNE.findall(text))
        for line in reversed(text.splitlines()):
            match = _TUNE.search(line)
            if match:
                out["last_verdict"] = _plain(
                    f"{match.group(1)} exit={match.group(2)}", 60
                )
                break
        try:
            out["last_run_age"] = time.time() - log.stat().st_mtime
        except OSError:
            pass

    goals_file = home / "state" / "rsi-goals.json"
    if goals_file.is_file():
        try:
            goals = json.loads(goals_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            goals = []
        if isinstance(goals, list):
            out["goals_total"] = len(goals)
            out["goals_unmeasured"] = sum(
                1 for g in goals
                if isinstance(g, dict) and "pending" in str(g.get("progress", "")).lower()
            )
    return out


# ── render ──────────────────────────────────────────────────────────────────────────────


def render_otto() -> Tuple[str, List[ButtonRow]]:
    """Otto at a glance: services, work, learning, self-improvement."""
    from gateway.operator_shell.panel_chrome import panel_stamp, with_nav

    lines: List[str] = ["🤖 *Otto* — services, work, learning, RSI", ""]

    # ── SERVICES ────────────────────────────────────────────────────────────────────
    table = _launchd()
    lines.append("*Services*")
    if not table:
        lines.append("  ⚠️ `launchctl list` gave nothing — cannot judge service state")
    else:
        for label, name in SERVICES:
            if label not in table:
                lines.append(f"  ⚫ {name} — not loaded")
                continue
            pid, status = table[label]
            if pid:
                lines.append(f"  🟢 {name} — pid {pid}")
            else:
                lines.append(f"  🔴 {name} — stopped (last exit {status})")
    lines.append("")

    # ── WORK ────────────────────────────────────────────────────────────────────────
    work = _work()
    active, terminal = _lifecycle()
    lines.append("*Work*")
    if not work["present"]:
        lines.append("  ⚠️ coordinator.db not found")
    elif work["error"]:
        lines.append(f"  ⚠️ coordinator.db unreadable: {work['error']}")
    else:
        counts = work["counts"]
        total = sum(counts.values())
        in_flight = sum(counts.get(s, 0) for s in active)
        lines.append(
            f"  {total} tasks · {in_flight} in flight · "
            f"last event {_age(work['newest_event_age'])}"
        )
        if counts:
            lines.append(
                "  " + " · ".join(
                    f"{status} {n}" for status, n in
                    sorted(counts.items(), key=lambda kv: -kv[1])
                )
            )
        # The audit's finding, derived rather than asserted: a status in neither tuple
        # is selected by no query in the tick, so those rows can never be retried.
        if active or terminal:
            stranded = {
                s: n for s, n in counts.items()
                if s not in active and s not in terminal
            }
            if stranded:
                detail = ", ".join(f"{s} {n}" for s, n in sorted(stranded.items()))
                lines.append(
                    f"  🔴 STRANDED {sum(stranded.values())} ({detail}) — in neither "
                    f"ACTIVE nor TERMINAL, so no tick selects them"
                )
        else:
            lines.append("  ⚠️ could not read ACTIVE/TERMINAL from scripts/coordinator.py")

        done = work["fallback_done"] + work["clean_done"]
        if done:
            lines.append(
                f"  Completions: {work['clean_done']} with tool work · "
                f"{work['fallback_done']} narrated (fallback marker)"
            )
        elif not _fallback_markers():
            lines.append("  ⚠️ FALLBACK_MARKERS unreadable — completions not audited")
    lines.append("")

    # ── LEARNING ────────────────────────────────────────────────────────────────────
    learn = _learning()
    lines.append("*Learning*")
    lines.append(
        f"  {'🟢 ARMED' if learn['armed'] else '⚪ DISARMED'} · "
        f"{learn['policies']} policies · {learn['firings_today']} firings today · "
        f"{learn['injections_today']} injections today"
    )
    lines.append(
        f"  Idle engine: {learn['cycles']} cycles · {learn['insights']} insights · "
        f"last cycle {_age(learn['last_cycle_age'])}"
    )
    if learn["insights_pending"]:
        lines.append(
            f"  Insight queue: {learn['insights_pending']} rows, none consumed "
            f"(no reader acts on them)"
        )
    lines.append(f"  Last reflection: {_plain(learn['last_reflection'], 20)}")
    lines.append("")

    # ── SELF-IMPROVEMENT ────────────────────────────────────────────────────────────
    rsi = _self_improvement()
    lines.append("*Self-improvement*")
    if not rsi["log"]:
        lines.append("  ⚠️ no rsi-autorun.log — the tuner has never run here")
    else:
        landed_icon = "🟢" if rsi["landed"] else "🔴"
        lines.append(
            f"  {landed_icon} {rsi['landed']} landed / {rsi['attempts']} tune runs · "
            f"last {_age(rsi['last_run_age'])}"
        )
        if rsi["last_verdict"]:
            lines.append(f"  Last verdict: {rsi['last_verdict']}")
    if rsi["goals_total"]:
        lines.append(
            f"  Goals: {rsi['goals_total']} open · "
            f"{rsi['goals_unmeasured']} never measured"
        )

    lines += ["", panel_stamp("otto")]

    buttons: List[ButtonRow] = [
        [("🧠 RSI", "estate:rsi"), ("🧠 Otto health", "estate:otto_health")],
        [("🔄 Idle", "estate:idle_status"), ("📋 Recent changes", "estate:rsi_changes")],
        [("📥 Inbox", "estate:inbox"), ("🩺 Health", "estate:health")],
    ]
    return "\n".join(lines), with_nav(buttons, "otto")


def main() -> None:
    text, _buttons = render_otto()
    print(text)


if __name__ == "__main__":
    main()
