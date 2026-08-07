"""The web dashboard's address, as a probe rather than a saved string.

ONE implementation behind two doors: the `/dashboard` slash command and the
`estate:dashboard` cockpit button. They used to be zero doors — the founder typed
/dashboard twice on 2026-08-06 (gateway.log 20:30:29, 20:42:19) and got
"Unrecognized slash command" both times, while `estate:dashboard` sat in the
button-gate quarantine as "No render_dashboard()".

Why a probe: the public address is a cloudflared quick tunnel, which dies when the
Mac sleeps and comes back on a NEW hostname. A command that prints the last known
URL is therefore wrong most of the time, and wrong in the worst way — it looks like
the whole product is broken. So we curl it before we answer.

Two call styles, deliberately different:
  * heal=False (the button) — probe only, bounded by `timeout`. A Telegram callback
    cannot sit still for a minute, so a tap never shells out.
  * heal=True (the typed command) — probe, and if it fails run dashboard-up.sh and
    probe again. The command path runs in a thread and can afford the wait.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("gateway.run")

ButtonRow = List[Tuple[str, str]]

STATE_FILE = Path.home() / ".hermes" / "state" / "dashboard_access.json"
UP_SCRIPT = Path.home() / ".hermes" / "scripts" / "dashboard-up.sh"
HEAL_TIMEOUT_S = 180


def _read_state() -> Tuple[str, str]:
    """(public_url, local_url) as last published by dashboard-up.sh."""
    try:
        data = json.loads(STATE_FILE.read_text())
    except Exception:
        return "", "http://127.0.0.1:9119"
    return (data.get("url") or ""), (data.get("local_url") or "http://127.0.0.1:9119")


def probe(url: str, timeout: float = 8.0) -> bool:
    """True iff url answers 200. Any error is a False, never an exception —
    a dead tunnel must render a panel, not take the cockpit down."""
    if not url:
        return False
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def dashboard_status(heal: bool = False, timeout: float = 8.0) -> Tuple[str, str, bool]:
    """(public_url, local_url, public_ok) after an actual reachability check."""
    url, local = _read_state()
    if probe(url, timeout=timeout):
        return url, local, True

    if heal and UP_SCRIPT.exists():
        try:
            subprocess.run(
                ["/bin/bash", str(UP_SCRIPT)],
                capture_output=True,
                timeout=HEAL_TIMEOUT_S,
            )
        except Exception as exc:
            logger.warning("dashboard-up.sh failed: %s", exc)
        url, local = _read_state()
        if probe(url, timeout=timeout):
            return url, local, True

    return url, local, False


def render_text(heal: bool = False) -> str:
    """The message body. Shared so the command and the panel say the same thing."""
    url, local, ok = dashboard_status(heal=heal)
    if ok:
        return (
            "🖥  Hermes dashboard\n\n"
            f"{url}\n\n"
            "Tap it. The page logs you in on its own, so there is nothing to type.\n\n"
            f"On the Mac itself: {local}\n\n"
            "Keep that link private. Anyone holding it is signed in."
        )
    if heal:
        return (
            "🖥  Hermes dashboard is NOT reachable right now.\n\n"
            "I tried to restart it and the public link still did not answer.\n"
            f"On the Mac itself, try: {local}\n\n"
            "Logs to check:\n"
            "  ~/.hermes/logs/dashboard.log\n"
            "  ~/.hermes/logs/dashboard-tunnel.log"
        )
    # Button path: say who CAN fix it rather than blocking the tap for a minute.
    return (
        "🖥  *Web dashboard* — 🔴 not answering\n\n"
        "The public tunnel is down. Quick tunnels die when the Mac sleeps.\n\n"
        "Send `/dashboard` and I will restart it and reply with the new link "
        "(a typed command can wait the ~40s; a button tap cannot).\n\n"
        f"On the Mac itself: {local}"
    )


def render_web_dashboard() -> Tuple[str, List[ButtonRow]]:
    """`estate:dashboard` — the cockpit button. Probe only, never shells out."""
    from gateway.operator_shell.panel_chrome import with_nav

    return render_text(heal=False), with_nav([], "estate:dashboard")
