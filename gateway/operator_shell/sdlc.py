"""SDLC pipeline — consolidated 6-stage view: Assign → Board → Fleet → Review → Ship → Learn.

One screen to see the full software lifecycle. Every section has a button to open the
full panel. Graceful degradation: if a data source fails, show "—" not crash.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Tuple

logger = logging.getLogger(__name__)

from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav

ButtonRow = List[Tuple[str, str]]


def _coord():
    from gateway.operator_shell.estate import _load_coordinator
    return _load_coordinator()


def _inflight_code_snapshot() -> str:
    """Brief snapshot of in-flight code runs. Graceful on any failure."""
    try:
        C = _coord()
        conn = C.connect()
        try:
            rows = conn.execute(
                "SELECT id, status, title FROM tasks WHERE source='code:telegram' "
                "AND status IN ('open','diagnosed','executing','verifying','awaiting_approval') "
                "ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            if not rows:
                return "_No active code runs_"
            lines = []
            for r in rows:
                rid = str(r["id"] if hasattr(r, "keys") else r[0])
                st = str(r["status"] if hasattr(r, "keys") else r[1])
                title = str(r["title"] if hasattr(r, "keys") else r[2])
                lines.append(f"  `{rid[:8]}` {st} — {title[:60]}")
            return "\n".join(lines)
        finally:
            conn.close()
    except Exception:
        return "—"


def _missions_snapshot() -> str:
    """Active missions snapshot. Graceful on any failure."""
    try:
        import flight
        C = _coord()
        conn = C.connect()
        try:
            missions = flight.list_missions(conn)
            active = [m for m in missions if m.get("status") in ("flying", "blocked", "plotting")]
            if not active:
                return "_No active missions_"
            lines = []
            for m in active[:3]:
                ms = m.get("status", "?").upper()
                lines.append(f"  🚀 `{m.get('name', '?')}` {ms}")
            return "\n".join(lines)
        finally:
            conn.close()
    except Exception:
        return "—"


def _fleet_snapshot() -> str:
    """Fleet/repos snapshot. Graceful on any failure."""
    try:
        from gateway.operator_shell.fleet import _load_projects, _repo_health
        projects = _load_projects()
        if not projects:
            return "_No projects_"
        health = _repo_health()
        lines = []
        for p in projects[:3]:
            name = p.get("name", "?")
            h = health.get(name, {})
            status = h.get("status", "?")
            lines.append(f"  📦 {name} — {status}")
        return "\n".join(lines)
    except Exception:
        return "—"


def _builds_snapshot() -> str:
    """CI/builds snapshot. Graceful on any failure."""
    try:
        import subprocess, json, os
        # Try gh CLI for recent workflow runs.
        # Narrow env: don't leak secrets to the gh subprocess. We only need
        # PATH, HOME, and the GH token if the user has one configured.
        _gh_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
        for var in ("GH_TOKEN", "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            if var in os.environ:
                _gh_env[var] = os.environ[var]
        result = subprocess.run(
            ["gh", "run", "list", "--limit", "3", "--json", "status,displayTitle,headBranch"],
            capture_output=True, text=True, timeout=15,
            env=_gh_env,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if not data:
                return "_No recent builds_"
            lines = []
            for r in data[:3]:
                status = r.get("status", "?")
                title = r.get("displayTitle", "?")[:50]
                lines.append(f"  {'✅' if status == 'completed' else '🔄'} {title}")
            return "\n".join(lines)
        return "—"
    except Exception:
        return "—"


def _inbox_snapshot() -> str:
    """Inbox/decisions snapshot. Graceful on any failure."""
    try:
        C = _coord()
        conn = C.connect()
        try:
            rows = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
            if not rows:
                return "_Inbox clear_"
            lines = []
            for r in rows[:3]:
                rid = str(r["id"])[:8]
                title = str(r.get("title", "") or "")[:50]
                lines.append(f"  📥 `{rid}` {title}")
            return "\n".join(lines)
        finally:
            conn.close()
    except Exception:
        return "—"


def _rsi_snapshot() -> str:
    """RSI/learning snapshot. Graceful on any failure."""
    try:
        from gateway.operator_shell.rsi_panel import OFF_SWITCH, PENDING_DIR
        armed = not OFF_SWITCH.is_file()
        pending_count = 0
        try:
            pending_count = len(list(PENDING_DIR.glob("*.json"))) if PENDING_DIR.is_dir() else 0
        except Exception:
            pass
        status = "🟢 ARMED" if armed else "⚪ DISARMED"
        if pending_count:
            return f"  {status} · {pending_count} pending change(s)"
        return f"  {status} · idle"
    except Exception:
        return "—"


def render_sdlc() -> Tuple[str, List[ButtonRow]]:
    """Render the SDLC pipeline panel: 6 stages, data snapshots, nav spine.

    Returns:
        (text, buttons) — text for the panel body, buttons for the button rows.
    """
    edit_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── 1. Assign ────────────────────────────────────────────────────
    assign_data = _inflight_code_snapshot()

    # ── 2. Board ─────────────────────────────────────────────────────
    board_data = _missions_snapshot()

    # ── 3. Fleet ─────────────────────────────────────────────────────
    fleet_data = _fleet_snapshot()

    # ── 4. Review ────────────────────────────────────────────────────
    review_data = _inbox_snapshot()

    # ── 5. Ship ──────────────────────────────────────────────────────
    ship_data = _builds_snapshot()

    # ── 6. Learn ─────────────────────────────────────────────────────
    learn_data = _rsi_snapshot()

    lines = [
        "💻 *SDLC Pipeline* — end-to-end software lifecycle",
        "",
        "*1. Assign* — coding runs",
        assign_data,
        "",
        "*2. Board* — active missions",
        board_data,
        "",
        "*3. Fleet* — repositories",
        fleet_data,
        "",
        "*4. Review* — decisions / inbox",
        review_data,
        "",
        "*5. Ship* — CI / builds / deploys",
        ship_data,
        "",
        "*6. Learn* — RSI / self-improvement",
        learn_data,
        "",
        "_Ready for action. Tap any stage to go deep._",
        f"_{edit_iso}_",
    ]

    text = "\n".join(lines)

    buttons: List[ButtonRow] = [
        [
            ("📝 Assign", "estate:code_prompt"),
            ("📋 Board", "estate:missions"),
        ],
        [
            ("🚀 Fleet", "estate:fleet"),
            ("👁 Review", "estate:inbox"),
        ],
        [
            ("🚀 Ship", "estate:builds"),
            ("🧠 Learn", "estate:rsi"),
        ],
    ]

    # Append nav spine
    buttons = with_nav(buttons)

    return text, buttons
