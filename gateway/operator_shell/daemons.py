"""Estate daemons — live launchctl status + start/stop/restart/logs from phone.

Honesty: KeepAlive vs interval/calendar oneshots. Idle between interval ticks
is 🟢 armed, not 🔴 down. Gateway start is fenced (token door).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

# kind: keepalive | interval | calendar
_ESTATE: Tuple[Tuple[str, str, str, Tuple[Path, ...]], ...] = (
    (
        "ai.hermes.gateway",
        "gateway",
        "keepalive",
        (
            Path.home() / ".hermes" / "logs" / "gateway.log",
            Path.home() / ".hermes" / "logs" / "gateway.error.log",
        ),
    ),
    (
        "ai.hermes.coordinator",
        "coord",
        "keepalive",
        (
            Path.home() / ".hermes" / "logs" / "coordinator.log",
            Path.home() / ".hermes" / "logs" / "coordinator.error.log",
        ),
    ),
    (
        "ai.hermes.watchdog",
        "watch",
        "interval",
        (
            Path.home() / ".hermes" / "logs" / "estate-watchdog.out.log",
            Path.home() / ".hermes" / "logs" / "estate-watchdog.err.log",
        ),
    ),
    (
        "ai.hermes.progress",
        "prog",
        "interval",
        (
            Path.home() / ".hermes" / "logs" / "progress-snapshot.out.log",
            Path.home() / ".hermes" / "logs" / "progress-snapshot.err.log",
        ),
    ),
    (
        "ai.hermes.rsi",
        "rsi",
        "calendar",
        (
            Path.home() / ".hermes" / "logs" / "rsi-autorun.out.log",
            Path.home() / ".hermes" / "logs" / "rsi-autorun.err.log",
        ),
    ),
    (
        "ai.hermes.otto-server",
        "otto-http",
        "keepalive",
        (
            Path.home() / ".hermes" / "logs" / "otto-server.log",
            Path.home() / ".hermes" / "logs" / "otto-server.err",
        ),
    ),
)

# Optional product LaunchAgents (shown if plist exists)
_EXTRA: Tuple[Tuple[str, str, str, Tuple[Path, ...]], ...] = (
    (
        "com.tie.ai-review",
        "tie-review",
        "calendar",
        (
            Path.home()
            / "Documents"
            / "code"
            / "the-introduction-exchange"
            / "review"
            / "logs"
            / "stdout.log",
            Path.home()
            / "Documents"
            / "code"
            / "the-introduction-exchange"
            / "review"
            / "logs"
            / "stderr.log",
        ),
    ),
)

_RETIRED = frozenset({"ai.hermes.cockpit", "ai.hermes.ngrok"})
_FENCED_START = frozenset({"ai.hermes.gateway", "ai.hermes.cockpit", "ai.hermes.ngrok"})

_BY_LABEL = {u[0]: u for u in _ESTATE + _EXTRA}
_SHORT = {u[0]: u[1] for u in _ESTATE + _EXTRA}
_SHORT.update({"ai.hermes.cockpit": "cockpit", "ai.hermes.ngrok": "ngrok"})
_KIND = {u[0]: u[2] for u in _ESTATE + _EXTRA}
_LOGS = {u[0]: u[3] for u in _ESTATE + _EXTRA}


def _uid() -> int:
    return os.getuid()


def _plist_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def discover_labels() -> List[str]:
    found: List[str] = []
    try:
        for p in sorted(_plist_dir().glob("ai.hermes.*.plist")):
            found.append(p.stem)
        for label, _, _, _ in _EXTRA:
            if (_plist_dir() / f"{label}.plist").is_file() and label not in found:
                found.append(label)
    except Exception:
        pass
    ordered: List[str] = []
    for label, _, _, _ in _ESTATE:
        if label not in ordered:
            ordered.append(label)
    for label in found:
        if label not in ordered and label not in _RETIRED:
            ordered.append(label)
    for label in sorted(_RETIRED):
        if label in found and label not in ordered:
            ordered.append(label)
    return ordered


def launchctl_state(label: str) -> Dict[str, object]:
    """Live launchctl — honest oneshot vs KeepAlive."""
    kind = _KIND.get(label, "keepalive")
    plist = _plist_dir() / f"{label}.plist"
    if not plist.is_file():
        return {
            "running": False,
            "pid": None,
            "state": "not_installed",
            "detail": "plist missing",
            "installed": False,
            "kind": kind,
            "armed": False,
        }
    target = f"gui/{_uid()}/{label}"
    try:
        r = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 and (
            "Could not find service" in out or "not be found" in out.lower()
        ):
            return {
                "running": False,
                "pid": None,
                "state": "unloaded",
                "detail": "plist on disk, not loaded",
                "installed": True,
                "kind": kind,
                "armed": False,
            }
        state = "unknown"
        pid: Optional[int] = None
        last_exit: Optional[str] = None
        runs: Optional[int] = None
        for ln in out.splitlines():
            s = ln.strip()
            if s.startswith("state ="):
                state = s.split("=", 1)[1].strip()
            if s.startswith("pid ="):
                try:
                    pid = int(s.split("=", 1)[1].strip())
                except Exception:
                    pid = None
            if s.startswith("last exit code ="):
                last_exit = s.split("=", 1)[1].strip()
            if s.startswith("runs ="):
                try:
                    runs = int(s.split("=", 1)[1].strip())
                except Exception:
                    runs = None

        disabled = False
        try:
            raw = plist.read_text(encoding="utf-8", errors="replace")
            idx = raw.find("<key>Disabled</key>")
            if idx >= 0 and "<true/>" in raw[idx : idx + 80]:
                disabled = True
        except Exception:
            pass
        if disabled and not (pid and pid > 0):
            return {
                "running": False,
                "pid": None,
                "state": "disabled",
                "detail": "disabled",
                "installed": True,
                "kind": kind,
                "armed": False,
            }

        running = state == "running" or (pid is not None and pid > 0)
        # Interval/calendar: loaded + not disabled = armed (idle between ticks is normal)
        if kind in ("interval", "calendar") and not running and state not in (
            "unloaded",
            "not_installed",
            "disabled",
        ):
            bits = ["armed"]
            if last_exit is not None:
                bits.append(f"last exit {last_exit}")
            if runs is not None:
                bits.append(f"runs {runs}")
            return {
                "running": False,
                "pid": None,
                "state": "armed",
                "detail": " · ".join(bits),
                "installed": True,
                "kind": kind,
                "armed": True,
                "last_exit": last_exit,
                "runs": runs,
            }

        return {
            "running": running,
            "pid": pid,
            "state": state,
            "detail": f"pid {pid}" if pid else state,
            "installed": True,
            "kind": kind,
            "armed": running if kind == "keepalive" else True,
            "last_exit": last_exit,
            "runs": runs,
        }
    except Exception as exc:
        logger.warning("launchctl_state %s: %s", label, exc)
        return {
            "running": False,
            "pid": None,
            "state": "error",
            "detail": str(exc)[:40],
            "installed": True,
            "kind": kind,
            "armed": False,
        }


def _emoji(st: Dict[str, object]) -> str:
    if st.get("state") == "disabled":
        return "⚪"
    if st.get("state") in ("unloaded", "not_installed"):
        return "⚫"
    if st.get("state") == "armed" or st.get("armed"):
        if st.get("kind") in ("interval", "calendar") and not st.get("running"):
            return "🟢"
    if st.get("running"):
        return "🟢"
    return "🔴"


def _tail(paths: Tuple[Path, ...], n: int = 4) -> str:
    for path in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            chunk = lines[-n:] if lines else []
            if not chunk:
                continue
            return "\n".join(f"   `{ln[:90]}`" for ln in chunk)
        except Exception:
            continue
    return "   _(no log)_"


def render_daemons() -> Tuple[str, List[ButtonRow]]:
    lines = [
        "⚙️ *Daemons* — live `launchctl`",
        "_KeepAlive = pid · interval/calendar = armed between ticks_",
        "",
    ]
    down: List[str] = []
    for label in discover_labels():
        st = launchctl_state(label)
        short = _SHORT.get(label, label.replace("ai.hermes.", "").replace("com.", ""))
        kind = st.get("kind") or _KIND.get(label, "?")
        tag = ""
        if label in _FENCED_START:
            tag = " · fenced"
        if label in _RETIRED:
            tag = " · retired"
        if kind in ("interval", "calendar"):
            tag += f" · {kind}"
        lines.append(f"{_emoji(st)} `{short}` · {st.get('detail')}{tag}")
        # Only flag KeepAlive down / unloaded oneshots — not idle armed
        if label in _RETIRED or st.get("state") == "disabled":
            continue
        if st.get("state") in ("unloaded", "not_installed"):
            down.append(short)
        elif kind == "keepalive" and not st.get("running"):
            down.append(short)

    if down:
        lines.append("")
        lines.append(f"⬇️ needs attention: {', '.join(down)}")
    lines.append("")
    lines.append("_Gateway start fenced. Prospect gen → Prospect daemons._")

    buttons: List[ButtonRow] = [
        [
            ("♻️ Restart coord", "estate:daemon_restart:coordinator"),
            ("▶️ Start coord", "estate:daemon_start:coordinator"),
        ],
        [
            ("▶️ Run watch", "estate:daemon_run_now:watchdog"),
            ("♻️ RSI", "estate:daemon_restart:rsi"),
            ("♻️ Progress", "estate:daemon_restart:progress"),
        ],
        [
            ("📜 Coord logs", "estate:daemon_logs:coordinator"),
            ("📜 Watch logs", "estate:daemon_logs:watchdog"),
        ],
        [
            ("⏹ Stop coord", "estate:daemon_stop:coordinator"),
            ("♻️ Bounce gateway", "estate:daemon_restart:gateway"),
        ],
        [
            ("⚙️ Prospect daemons", "estate:prospector_daemon"),
            ("🔄 Refresh", "estate:daemons"),
            ("🎛 Mission", "estate:refresh"),
        ],
    ]
    # TIE if installed
    if (_plist_dir() / "com.tie.ai-review.plist").is_file():
        buttons.insert(
            3,
            [
                ("▶️ Run TIE review", "estate:daemon_run_now:tie-review"),
                ("📜 TIE logs", "estate:daemon_logs:tie-review"),
            ],
        )
    return "\n".join(lines), buttons


def render_logs(unit_arg: str = "coordinator") -> Tuple[str, List[ButtonRow]]:
    label = _resolve_short(unit_arg) or "ai.hermes.coordinator"
    short = _SHORT.get(label, unit_arg)
    paths = _LOGS.get(label) or ()
    lines = [f"📜 *`{short}` logs*", ""]
    if not paths:
        lines.append("_(no log paths configured)_")
    else:
        for path in paths:
            lines.append(f"*{path.name}*")
            if not path.is_file():
                lines.append("   _(missing)_")
            else:
                lines.append(_tail((path,), n=8))
            lines.append("")
    buttons: List[ButtonRow] = [
        [
            ("⚙️ Daemons", "estate:daemons"),
            ("🔄 Refresh", f"estate:daemon_logs:{short}"),
        ]
    ]
    return "\n".join(lines).rstrip(), buttons


def _resolve_short(arg: str) -> Optional[str]:
    a = (arg or "").strip().lower().replace("ai.hermes.", "").replace("com.", "")
    if not a:
        return None
    aliases = {
        "gateway": "ai.hermes.gateway",
        "gw": "ai.hermes.gateway",
        "coord": "ai.hermes.coordinator",
        "coordinator": "ai.hermes.coordinator",
        "watch": "ai.hermes.watchdog",
        "watchdog": "ai.hermes.watchdog",
        "prog": "ai.hermes.progress",
        "progress": "ai.hermes.progress",
        "rsi": "ai.hermes.rsi",
        "otto": "ai.hermes.otto-server",
        "otto-server": "ai.hermes.otto-server",
        "otto-http": "ai.hermes.otto-server",
        "tie": "com.tie.ai-review",
        "tie-review": "com.tie.ai-review",
        "ai-review": "com.tie.ai-review",
    }
    if a in aliases:
        return aliases[a]
    full = f"ai.hermes.{a}"
    if full in discover_labels() or full in _BY_LABEL:
        return full
    if a.startswith("tie"):
        return "com.tie.ai-review"
    return None


def confirm_card(op: str, label: str) -> Tuple[str, List[ButtonRow]]:
    if not label:
        return ("Unknown daemon", [[("⚙️ Daemons", "estate:daemons")]])
    short = _SHORT.get(label, label)
    kind = _KIND.get(label, "keepalive")
    warn = ""
    if label == "ai.hermes.gateway":
        warn = "\n\n⚠️ Drops Telegram for a few seconds. Prefer planned restart."
    if label in _RETIRED:
        warn = "\n\n⚠️ Retired dual-door — only if you know why."
    if kind in ("interval", "calendar") and op in ("start", "restart"):
        op = "run_now"
    if kind in ("interval", "calendar") and op == "run_now":
        warn += "\n\nOneshot/calendar job — exits after one run (normal)."
    op_word = {
        "start": "Start",
        "stop": "Unload" if kind in ("interval", "calendar") else "Stop",
        "restart": "Restart",
        "run_now": "Run now",
    }.get(op, op)
    short_cb = _SHORT.get(label, label.replace("ai.hermes.", ""))
    text = f"⚙️ *{op_word}* `{short}`?{warn}"
    buttons: List[ButtonRow] = [
        [
            ("✅ Confirm", f"estate:daemon_{op}_confirm:{short_cb}"),
            ("✗ Cancel", "estate:daemons"),
        ]
    ]
    return text, buttons


def run_op(op: str, label: str) -> Tuple[bool, str]:
    """Execute start/stop/restart/run_now via launchctl. Returns (ok, detail)."""
    if op == "start" and label in _FENCED_START:
        return False, f"`{label}` start is fenced — use Bounce gateway confirm or CLI"
    kind = _KIND.get(label, "keepalive")
    if kind in ("interval", "calendar") and op in ("start", "restart"):
        op = "run_now"
    target = f"gui/{_uid()}/{label}"
    plist = _plist_dir() / f"{label}.plist"
    try:
        before = launchctl_state(label)
        if op == "restart":
            cmd = ["launchctl", "kickstart", "-k", target]
        elif op == "run_now":
            st = launchctl_state(label)
            if st.get("state") == "unloaded":
                br = subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if br.returncode != 0 and "already" not in (br.stderr or "").lower():
                    return False, (br.stderr or br.stdout or "bootstrap failed")[:200]
            cmd = ["launchctl", "kickstart", "-k", target]
        elif op == "stop":
            cmd = ["launchctl", "bootout", target]
        elif op == "start":
            if not plist.is_file():
                return False, f"no plist {plist.name}"
            st = launchctl_state(label)
            if st.get("state") == "unloaded":
                cmd = ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
            else:
                cmd = ["launchctl", "kickstart", target]
        else:
            return False, f"unknown op {op}"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        detail = ((r.stderr or r.stdout or "").strip() or "ok")[:200]
        ok = r.returncode == 0
        if op == "stop" and r.returncode != 0 and "No such process" in detail:
            ok = True
        after = launchctl_state(label)
        if op in ("restart", "start") and before.get("pid") and after.get("pid"):
            detail = f"{detail} · pid {before.get('pid')} → {after.get('pid')}"
        elif op == "run_now":
            detail = f"{detail} · kicked oneshot/calendar"
        return ok, detail
    except Exception as exc:
        return False, str(exc)[:200]
