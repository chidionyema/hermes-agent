"""Project fleet tiles — prospector / signal / TIE / haworks."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gateway.operator_shell.panel_chrome import nav

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

_KEYS = (
    ("prospector", "Prospector"),
    ("signalengine", "Signal"),
    ("tie", "TIE"),
    ("haworks-platform", "Haworks"),
)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _load_projects() -> List[Dict[str, Any]]:
    path = _hermes_home() / "projects.json"
    try:
        data = json.loads(path.read_text())
        return list(data.get("projects") or [])
    except Exception:
        return []


def _repo_health() -> Dict[str, Dict[str, Any]]:
    path = _hermes_home() / "logs" / "health" / "repo-health.jsonl"
    out: Dict[str, Dict[str, Any]] = {}
    if not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-80:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            name = str(row.get("repo") or row.get("name") or "").lower()
            if name:
                out[name] = row
    except Exception:
        pass
    return out


def _git_short(repo: Path) -> str:
    if not repo.is_dir():
        return "missing"
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dirty = len([l for l in (r.stdout or "").splitlines() if l.strip()])
        return "clean" if dirty == 0 else f"dirty({dirty})"
    except Exception:
        return "unverified"


_BLOCKER_WORD = re.compile(r"\b(blocker|blocked|next step|next up)\b", re.I)
# Headings whose body IS a status statement, best first. These reports are graphify
# ARCHITECTURE analyses, not status reports — most carry no status section at all, and the
# old code's "first line of the file" rule is what turned that absence into a fake blocker.
_STATUS_HEADINGS = (
    (re.compile(r"^#{1,4}\s*Suggested next objective", re.I), "next"),
    (re.compile(r"^#{1,4}\s*Status read", re.I), "status"),
)


def _status_report(key: str) -> Tuple[str, str]:
    """Return (label, line) — or ("", "") when the report says nothing about status.

    The label travels with the text on purpose. The panel used to print everything under
    "blocker:", so a report with no blockers still showed one:

        blocker: ---
        blocker: - **Analysis Source File**: [graphify-out/.graphify_ana
        blocker: This status report is generated from the structural…

    None of those is a blocker. A field that is wrong whenever it is non-empty is worse than
    a field that is absent, because it is still read.
    """
    path = _hermes_home() / "reports" / f"project-status-{key}.md"
    if not path.is_file():
        return "", ""
    try:
        from gateway.operator_shell.panel_chrome import first_meaningful_line

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for pattern, label in _STATUS_HEADINGS:
            for i, line in enumerate(lines):
                if pattern.match(line.strip()):
                    body = first_meaningful_line("\n".join(lines[i + 1 : i + 8]))
                    if body:
                        return label, body
        for line in lines:
            if _BLOCKER_WORD.search(line) and line.strip(" -*#_"):
                return "blocker", first_meaningful_line(line)
        return "", ""
    except Exception:
        return "", ""


def _inflight(key: str) -> int:
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        conn = C.connect()
        try:
            if hasattr(C, "project_task_inflight"):
                return int(C.project_task_inflight(conn, key) or 0)
            rows = C.backlog_view(conn)
            return sum(1 for r in rows if key in str(r.get("title", "")).lower())
        finally:
            conn.close()
    except Exception:
        return 0


def render_fleet() -> Tuple[str, List[ButtonRow]]:
    projects = {p.get("key"): p for p in _load_projects()}
    health = _repo_health()
    lines = ["🚀 *Fleet*", ""]
    for key, label in _KEYS:
        p = projects.get(key) or {}
        repo = Path(str(p.get("repo") or "").replace("~", str(Path.home()))).expanduser()
        git = _git_short(repo) if repo.parts else "n/a"
        h = health.get(key) or health.get(repo.name.lower() if repo.parts else "") or {}
        state = h.get("state") or git
        inflight = _inflight(key)
        note_label, note = _status_report(key)
        # 🔴 is reserved for something that is actually broken. An uncommitted working tree is
        # the normal state of a repo several agents are editing — every project showed red on
        # dirty() alone, so red carried no information at all. Same failure as a probe that is
        # permanently red: it stops being read.
        if state == "fail":
            emoji = "🔴"
        elif inflight:
            emoji = "🟡"
        elif state in ("clean", "pass", "ok"):
            emoji = "🟢"
        else:
            emoji = "⚪"
        lines.append(f"{emoji} *{label}* · {state} · inflight {inflight}")
        if note:
            lines.append(f"   {note_label}: {note}")
        lines.append("")

    buttons: List[ButtonRow] = [
        [
            ("🏗 Builds", "estate:builds"),
            ("⚙️ Prospect daemons", "estate:prospector_daemon"),
        ],
        [
            ("💹 Signal Engine", "estate:signal_engine"),
            ("💰 Risk knobs", "estate:se_params"),
        ],
        [
            ("⚡️ Run Prospector", "estate:run_prospector"),
            ("⚙️ Estate daemons", "estate:daemons"),
        ],
        [
            ("🛒 Store", "estate:st_status"),
        ],
        nav("fleet"),
    ]
    # Prefixed glance for daemon health. Signal Engine goes first: it is the money
    # rail, and it is the one that spent 37 days dead without anyone seeing it.
    glances: List[str] = []
    try:
        from gateway.operator_shell.signal_engine import glance_line as se_glance

        line = se_glance()
        if line:
            glances.append(line)
    except Exception:
        pass
    try:
        from gateway.operator_shell.prospector_daemon import glance_line

        line = glance_line()
        if line:
            glances.append(line)
    except Exception:
        pass
    if glances:
        for offset, line in enumerate(glances):
            lines.insert(1 + offset, line)
    # Collapse runs of blank lines. The glance block and the header each contribute their own
    # separator, so the panel opened with a double gap on every render.
    out: List[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).rstrip(), buttons
