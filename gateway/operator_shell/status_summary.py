"""Status summary — one-tap estate overview with drill-in buttons.

Probes daemons, cron, missions, and spend. Renders as a scannable card
with inline action buttons. Composes existing probes — no new infrastructure.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import List, Tuple

from gateway.operator_shell.panel_chrome import clip, nav

ButtonRow = List[Tuple[str, str]]

HERMES = Path.home() / ".hermes"
COORD_DB = HERMES / "coordinator.db"
JOBS_PATH = HERMES / "cron" / "jobs.json"


def _spend_gauge(used: float, cap: float) -> str:
    """Visual spend gauge using block characters."""
    if cap <= 0:
        return f"💰 ${used:.2f} (no cap)"
    pct = min(used / cap, 1.0)
    filled = int(pct * 10)
    empty = 10 - filled
    bar = "▓" * filled + "░" * empty
    emoji = "🟢" if pct < 0.75 else ("🟡" if pct < 0.9 else "🔴")
    return f"{emoji} ${used:.2f} / ${cap:.2f} {bar} {pct:.0%}  [daily cap]"


def _count_daemons() -> Tuple[int, int]:
    """(running, total) for estate daemons."""
    labels = [
        "ai.hermes.gateway",
        "ai.hermes.coordinator",
        "ai.hermes.watchdog",
        "ai.hermes.progress",
        "ai.hermes.rsi",
    ]
    running = 0
    uid = os.getuid()
    for label in labels:
        try:
            r = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{label}"],
                capture_output=True, text=True, timeout=3,
            )
            if "state = running" in (r.stdout or ""):
                running += 1
        except Exception:
            pass
    return running, len(labels)


def _count_cron() -> Tuple[int, int, int]:
    """(ok, total, failing) for hermes cron jobs."""
    if not JOBS_PATH.is_file():
        return 0, 0, 0
    try:
        data = json.loads(JOBS_PATH.read_text())
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        ok = sum(1 for j in jobs if j.get("last_status") == "ok" and j.get("enabled", True))
        failing = sum(1 for j in jobs if j.get("last_status") not in (None, "ok") and j.get("enabled", True))
        return ok, len(jobs), failing
    except Exception:
        return 0, 0, 0


def _count_missions(conn) -> Tuple[int, int, int]:
    """(done, total, blocked) for missions."""
    try:
        row = conn.execute(
            "SELECT status, COUNT(*) c FROM tasks WHERE kind='mission' GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["c"] for r in row}
        done = counts.get("done", 0)
        total = sum(counts.values())
        blocked = counts.get("escalated", 0) + counts.get("blocked", 0)
        return done, total, blocked
    except Exception:
        return 0, 0, 0


def _active_tasks(conn) -> List[dict]:
    """Active (non-terminal) operator-facing tasks."""
    try:
        rows = conn.execute(
            "SELECT id, title, status, started_at FROM tasks "
            "WHERE status NOT IN ('done','escalated','blocked') "
            "AND kind != 'mission' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _escalated_tasks(conn) -> List[dict]:
    """Escalated operator-facing tasks."""
    try:
        rows = conn.execute(
            "SELECT id, title, status, escalation_count FROM tasks "
            "WHERE status = 'escalated' AND kind != 'mission' "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _spend_today() -> Tuple[float, float]:
    """Estimate today's spend from the prospector guard probe."""
    try:
        ticks = Path.home() / "Documents/code/prospector/store/scheduler/ticks.jsonl"
        if ticks.is_file():
            lines = ticks.read_text().strip().splitlines()
            today = time.strftime("%Y-%m-%d")
            used = 0.0
            cap = None
            for line in reversed(lines):
                try:
                    t = json.loads(line)
                    if t.get("ts", "").startswith(today):
                        used = float(t.get("today_spend_usd", 0) or 0)
                        cap = float(t.get("daily_cap_usd", 20) or 20)
                        break
                except Exception:
                    continue
            return used, cap or 20.0
    except Exception:
        pass
    return 0.0, 20.0


def render_status_summary() -> Tuple[str, List[ButtonRow]]:
    """One-tap status card: daemons · cron · missions · spend · active tasks."""
    daemon_ok, daemon_total = _count_daemons()
    cron_ok, cron_total, cron_fail = _count_cron()
    used, cap = _spend_today()
    
    # Connect to coordinator DB
    missions_done = missions_total = missions_blocked = 0
    active = []
    escalated = []
    try:
        conn = sqlite3.connect(str(COORD_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        missions_done, missions_total, missions_blocked = _count_missions(conn)
        active = _active_tasks(conn)
        escalated = _escalated_tasks(conn)
        conn.close()
    except Exception:
        pass

    daemon_emoji = "🟢" if daemon_ok >= 3 else ("🟡" if daemon_ok >= 1 else "🔴")
    cron_emoji = "🟢" if cron_fail == 0 else ("🟡" if cron_fail <= 2 else "🔴")
    mission_emoji = "🟢" if missions_blocked == 0 else ("🟡" if missions_blocked <= 1 else "🔴")

    lines = [
        "*📊 Estate Status*",
        "",
        f"{daemon_emoji} Daemons: {daemon_ok}/{daemon_total} running",
        f"{cron_emoji} Cron: {cron_ok}/{cron_total} healthy · {cron_fail} failing",
        f"{mission_emoji} Missions: {missions_done} done / {missions_total} total · {missions_blocked} blocked",
        _spend_gauge(used, cap),
        "",
    ]

    if escalated:
        lines.append("*Escalated:*")
        for t in escalated[:5]:
            n = t.get("escalation_count", 0)
            occ = f" ({n}×)" if n > 1 else ""
            lines.append(f"  🔴 `{t['id'][:8]}` {clip(t['title'])}{occ}")
        lines.append("")

    if active:
        lines.append("*Active:*")
        now = time.time()
        for t in active[:3]:
            age = int(now - (t.get("started_at") or now))
            age_str = f"{age}s" if age < 90 else f"{age // 60}m"
            lines.append(f"  ⚙️ {clip(t['title'])} [{age_str}]")
        lines.append("")

    buttons: List[ButtonRow] = [
        [("🚀 Fleet", "estate:fleet"), ("🗓 Cron", "estate:pd_cron")],
        [("📋 Missions", "estate:missions"), ("📸 Diff", "estate:diff")],
        nav("status"),
    ]

    return "\n".join(lines), buttons
