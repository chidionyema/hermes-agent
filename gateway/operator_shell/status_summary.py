"""Status summary — one-tap estate overview with drill-in buttons.

Probes daemons, cron, missions, and spend. Renders as a scannable card
with inline action buttons. Composes existing probes — no new infrastructure.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

from gateway.operator_shell.panel_chrome import clip, nav, panel_stamp, with_nav

ButtonRow = List[Tuple[str, str]]

HERMES = Path.home() / ".hermes"
COORD_DB = HERMES / "coordinator.db"
JOBS_PATH = HERMES / "cron" / "jobs.json"
ESTATE_SPEND_HISTORY = Path.home() / ".claude" / "estate-spend-history.jsonl"
ESTATE_BUDGET = Path.home() / ".claude" / "estate-budget.json"


def _spend_gauge(used: float, cap: float, source: str = "estate") -> str:
    """Visual spend gauge using block characters."""
    if source == "unavailable":
        return "⚪ Spend: unknown — no sentinel reading today (estate_cost_sentinel)"
    note = "" if source == "estate" else f"  ⚠️ {source}"
    if cap <= 0:
        return f"💰 ${used:.2f} (no cap){note}"
    pct = min(used / cap, 1.0)
    filled = int(pct * 10)
    empty = 10 - filled
    bar = "▓" * filled + "░" * empty
    emoji = "🟢" if pct < 0.75 else ("🟡" if pct < 0.9 else "🔴")
    return f"{emoji} ${used:.2f} / ${cap:.2f} {bar} {pct:.0%}  [estate today · warn cap]{note}"


from gateway.operator_shell.launchd_health import probe_estate as _probe_daemons, summarize as _summarize_daemons


def _count_daemons() -> Tuple[int, int]:
    """(ok, total) — periodic daemons counted correctly as healthy when scheduled."""
    hs = _probe_daemons()
    ok, total, _faults = _summarize_daemons(hs)
    return ok, total


def _daemon_faults() -> list:
    """List of daemons that are actually broken (not just between periodic ticks)."""
    hs = _probe_daemons()
    _ok, _total, faults = _summarize_daemons(hs)
    return faults


def _load_jobs() -> list:
    if not JOBS_PATH.is_file():
        return []
    try:
        data = json.loads(JOBS_PATH.read_text())
        return data if isinstance(data, list) else data.get("jobs", [])
    except Exception:
        return []


def _count_cron(jobs: list | None = None) -> Tuple[int, int, int]:
    """(ok, total, failing) for hermes cron jobs — enabled only for the headline counts."""
    jobs = jobs if jobs is not None else _load_jobs()
    if not jobs:
        return 0, 0, 0
    ok = sum(1 for j in jobs if j.get("last_status") == "ok" and j.get("enabled", True))
    failing = sum(
        1 for j in jobs if j.get("last_status") not in (None, "ok") and j.get("enabled", True)
    )
    return ok, len(jobs), failing


def _cron_orphans(jobs: list | None = None) -> list:
    """Disabled jobs that last failed — invisible when include_disabled=False on /cron."""
    jobs = jobs if jobs is not None else _load_jobs()
    orphans = []
    for j in jobs:
        if j.get("enabled", True):
            continue
        if j.get("last_status") not in (None, "ok"):
            orphans.append(j)
    return orphans


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


def _spend_today() -> Tuple[float, float, str]:
    """Today's ESTATE spend, the warn cap, and where the number came from.

    This read prospector's `store/scheduler/ticks.jsonl -> today_spend_usd` until
    2026-08-06. That ledger counts METERED API dollars only, so on that day this
    card rendered "$3.91 / $20.00 20% [daily cap]" in green while the estate had
    actually burned $1,020.34 — the founder's most-checked number, wrong by ~260x
    and coloured to say "fine". Subscription burn, which dominates the bill, is
    invisible to that ledger by construction; no amount of care at this call site
    could have fixed it, because the source could not see the money.

    Ground truth is the sentinel's history, appended by
    `~/.claude/scripts/estate_cost_sentinel.record()` from Claude Code's own
    transcripts. When there is no reading for today the honest answer is
    "unknown" — never $0.00 rendered green.
    """
    cap = 120.0
    try:
        cap = float(json.loads(ESTATE_BUDGET.read_text()).get("warn_usd") or cap)
    except Exception:
        pass

    today = time.strftime("%Y-%m-%d")
    try:
        if ESTATE_SPEND_HISTORY.is_file():
            newest = None
            for line in reversed(ESTATE_SPEND_HISTORY.read_text().strip().splitlines()):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if newest is None:
                    newest = row
                if row.get("day") == today:
                    return float(row.get("total") or 0.0), cap, "estate"
            if newest is not None:
                return (float(newest.get("total") or 0.0), cap,
                        f"stale — last reading {newest.get('day')}")
    except Exception:
        pass
    return 0.0, cap, "unavailable"


def render_status_summary() -> Tuple[str, List[ButtonRow]]:
    """One-tap status card: daemons · cron · missions · spend · active tasks."""
    jobs = _load_jobs()
    daemon_ok, daemon_total = _count_daemons()
    cron_ok, cron_total, cron_fail = _count_cron(jobs)
    orphans = _cron_orphans(jobs)
    used, cap, spend_source = _spend_today()

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
    cron_emoji = (
        "🔴" if cron_fail > 2 or orphans
        else ("🟡" if cron_fail > 0 else "🟢")
    )
    mission_emoji = "🟢" if missions_blocked == 0 else ("🟡" if missions_blocked <= 1 else "🔴")

    lines = [
        "*📊 Estate Status*",
        "",
        f"{daemon_emoji} Daemons: {daemon_ok}/{daemon_total} running",
        # Orphans force cron_emoji red, so the label has to name them or the line
        # reads as a contradiction: "🔴 Cron: 28/32 healthy · 0 failing".
        f"{cron_emoji} Cron: {cron_ok}/{cron_total} healthy · {cron_fail} failing"
        + (f" · {len(orphans)} orphaned" if orphans else ""),
        f"{mission_emoji} Missions: {missions_done} done / {missions_total} total · {missions_blocked} blocked",
        _spend_gauge(used, cap, spend_source),
        "",
    ]

    if orphans:
        lines.append("*Cron orphans (disabled · last error):*")
        for j in orphans[:5]:
            jid = str(j.get("id") or j.get("job_id") or "?")[:10]
            name = clip(j.get("name") or "(unnamed)", 36)
            err = clip(str(j.get("last_error") or j.get("paused_reason") or "error"), 48)
            lines.append(f"  🔴 `{jid}` {name}")
            lines.append(f"     _{err}_")
        if len(orphans) > 5:
            lines.append(f"  _…+{len(orphans) - 5} more — `/cron list --all`_")
        lines.append("")

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

    lines.append(panel_stamp("status"))

    buttons: List[ButtonRow] = [
        [("🚀 Fleet", "estate:fleet"), ("🗓 Cron", "estate:pd_cron")],
        [("📋 Missions", "estate:missions"), ("📸 Diff", "estate:diff")],
    ]
    buttons = with_nav(buttons, "status")

    return "\n".join(lines), buttons
