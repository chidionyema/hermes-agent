"""Mac estate host liveness — honest always-on signal for /panel + phone ops.

Detects keepawake LaunchAgent, gateway heartbeat, watchdog tick, wake grace,
load/uptime, and coarse network reachability. Never claims CLEAR when the
host is at risk of sleep or the Telegram door heartbeat is stale.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
from gateway.operator_shell.panel_chrome import nav, panel_stamp

ButtonRow = List[Tuple[str, str]]

KEEPAWAKE_LABEL = "ai.hermes.keepawake"
GATEWAY_HEARTBEAT = Path.home() / ".hermes" / "gateway.heartbeat"
GW_HEARTBEAT_STALE_S = 1200  # match estate_watchdog
WAKE_GRACE_S = 900  # 15m — match estate_watchdog WAKE_GRACE_S
WATCHDOG_STALE_S = 900  # StartInterval 300s; >15m = watchdog not ticking


def _uid() -> int:
    return os.getuid()


def _keepawake_running() -> Dict[str, Any]:
    try:
        from gateway.operator_shell.daemons import launchctl_state

        st = launchctl_state(KEEPAWAKE_LABEL)
        return {
            "running": bool(st.get("running")),
            "pid": st.get("pid"),
            "state": st.get("state"),
            "detail": st.get("detail"),
            "installed": bool(st.get("installed")),
        }
    except Exception as exc:
        logger.warning("keepawake probe failed: %s", exc)
        return {
            "running": False,
            "pid": None,
            "state": "error",
            "detail": str(exc)[:40],
            "installed": False,
        }


def _gateway_heartbeat_age() -> Optional[int]:
    try:
        raw = GATEWAY_HEARTBEAT.read_text(encoding="utf-8").strip().split()[0]
        return int(time.time() - int(raw))
    except Exception:
        try:
            return int(time.time() - GATEWAY_HEARTBEAT.stat().st_mtime)
        except Exception:
            return None


def _watchdog_meta() -> Dict[str, Any]:
    """last_run age + wake grace from coordinator.db meta."""
    out: Dict[str, Any] = {
        "last_run_age": None,
        "in_wake_grace": False,
        "wake_age": None,
    }
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        conn = C.connect()
        try:
            last = C.get_meta(conn, "watchdog_last_run")
            if last:
                out["last_run_age"] = int(time.time() - last["updated_at"])
            wake = C.get_meta(conn, "watchdog_wake_at")
            if wake:
                wake_age = int(time.time() - wake["updated_at"])
                out["wake_age"] = wake_age
                out["in_wake_grace"] = wake_age < WAKE_GRACE_S
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("watchdog meta: %s", exc)
    return out


def _load_uptime() -> Dict[str, Any]:
    load: Optional[Tuple[float, float, float]] = None
    try:
        load = os.getloadavg()
    except Exception:
        pass
    uptime_s: Optional[int] = None
    try:
        r = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        # { sec = 1719…, usec = … } Tue …
        if r.returncode == 0 and "sec =" in (r.stdout or ""):
            part = r.stdout.split("sec =", 1)[1].split(",", 1)[0].strip()
            boot = int(part)
            uptime_s = int(time.time() - boot)
    except Exception:
        pass
    return {"load": load, "uptime_s": uptime_s}


def _net_ok(host: str = "1.1.1.1", port: int = 443, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _pmset_sleep() -> Optional[str]:
    """Best-effort sleep= value from pmset -g (no sudo)."""
    try:
        r = subprocess.run(
            ["pmset", "-g"], capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            return None
        for ln in (r.stdout or "").splitlines():
            s = ln.strip()
            if s.startswith("sleep"):
                return s.split()[-1]
    except Exception:
        return None
    return None


def probe_host() -> Dict[str, Any]:
    """Full host liveness snapshot for panel / alarms."""
    ka = _keepawake_running()
    gw_age = _gateway_heartbeat_age()
    wd = _watchdog_meta()
    lu = _load_uptime()
    net = _net_ok()
    sleep_setting = _pmset_sleep()

    gw_stale = gw_age is not None and gw_age > GW_HEARTBEAT_STALE_S
    wd_stale = (
        wd.get("last_run_age") is not None
        and int(wd["last_run_age"]) > WATCHDOG_STALE_S
    )
    in_grace = bool(wd.get("in_wake_grace"))

    # Sleep risk: keepawake down, or system sleep enabled without assertion
    sleep_risk = not ka.get("running")
    if sleep_setting and sleep_setting not in ("0", "0.0"):
        if not ka.get("running"):
            sleep_risk = True

    # Alarm-worthy (panel red) — suppress gw-stale false alarm during wake grace
    at_risk = False
    reasons: List[str] = []
    if not ka.get("running"):
        at_risk = True
        reasons.append("keepawake down")
    if gw_stale and not in_grace:
        at_risk = True
        reasons.append(f"gw heartbeat {gw_age // 60}m stale")
    elif gw_stale and in_grace:
        reasons.append("gw stale · wake grace")
    if wd_stale and not in_grace:
        reasons.append(f"watchdog {int(wd['last_run_age']) // 60}m")
    if not net:
        at_risk = True
        reasons.append("net unreachable")

    if at_risk:
        status = "at_risk"
        line = "🔴 Host at risk / heartbeat stale"
        if reasons:
            line = f"🔴 Host at risk · {' · '.join(reasons[:3])}"
    elif in_grace and (gw_stale or sleep_risk):
        status = "waking"
        line = "🟡 Host waking · grace 15m"
    elif ka.get("running") and not gw_stale and net:
        status = "awake"
        line = "🖥 Host: AWAKE · online"
    else:
        status = "degraded"
        line = "🟡 Host degraded · " + (" · ".join(reasons[:2]) or "check")

    return {
        "status": status,
        "line": line,
        "at_risk": at_risk,
        "keepawake": ka,
        "gateway_heartbeat_age": gw_age,
        "watchdog": wd,
        "load": lu.get("load"),
        "uptime_s": lu.get("uptime_s"),
        "net_ok": net,
        "sleep_setting": sleep_setting,
        "sleep_risk": sleep_risk,
        "in_wake_grace": in_grace,
        "reasons": reasons,
    }


def glance_line() -> str:
    """One mission-card line."""
    try:
        return str(probe_host().get("line") or "🖥 Host: ?")
    except Exception as exc:
        return f"🖥 Host: probe failed ({str(exc)[:40]})"


def start_keepawake() -> Tuple[bool, str]:
    """Bootstrap/kickstart ai.hermes.keepawake. Installs plist from recovery if missing."""
    plist = Path.home() / "Library" / "LaunchAgents" / f"{KEEPAWAKE_LABEL}.plist"
    src = Path.home() / ".hermes" / "recovery" / "launchd" / f"{KEEPAWAKE_LABEL}.plist"
    try:
        if not plist.is_file():
            if src.is_file():
                import re

                plist.parent.mkdir(parents=True, exist_ok=True)
                text = re.sub(r"<!--.*?-->", "", src.read_text(), flags=re.S)
                plist.write_text(text)
            else:
                return False, f"missing plist (expected {src})"
        target = f"gui/{_uid()}/{KEEPAWAKE_LABEL}"
        st = _keepawake_running()
        if st.get("state") in ("unloaded", "not_installed") or not st.get("running"):
            # bootout then bootstrap for clean load
            subprocess.run(
                ["launchctl", "bootout", target],
                capture_output=True,
                text=True,
                timeout=15,
            )
            br = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if br.returncode != 0 and "already" not in (br.stderr or "").lower():
                # try kickstart if already bootstrapped
                kr = subprocess.run(
                    ["launchctl", "kickstart", "-k", target],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if kr.returncode != 0:
                    return False, (br.stderr or br.stdout or kr.stderr or "bootstrap failed")[
                        :200
                    ]
        else:
            subprocess.run(
                ["launchctl", "kickstart", "-k", target],
                capture_output=True,
                text=True,
                timeout=30,
            )
        time.sleep(0.5)
        after = _keepawake_running()
        if after.get("running"):
            return True, f"keepawake running · pid {after.get('pid')}"
        return False, f"loaded but not running · {after.get('detail')}"
    except Exception as exc:
        return False, str(exc)[:200]


def render_host_panel() -> Tuple[str, List[ButtonRow]]:
    p = probe_host()
    ka = p.get("keepawake") or {}
    gw = p.get("gateway_heartbeat_age")
    wd = p.get("watchdog") or {}
    load = p.get("load")
    up = p.get("uptime_s")

    def _age(s: Optional[int]) -> str:
        if s is None:
            return "—"
        if s < 90:
            return f"{s}s"
        return f"{s // 60}m"

    load_s = (
        f"{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}" if load else "n/a"
    )
    up_s = _age(up) if up is None else (
        f"{up // 86400}d {(up % 86400) // 3600}h" if up >= 86400 else _age(up)
    )

    lines = [
        "🖥 *Estate host*",
        p["line"],
        "",
        f"Keep-awake: `{'ON' if ka.get('running') else 'OFF'}` · "
        f"{ka.get('detail') or ka.get('state')}",
        f"Gateway HB: `{_age(gw)}`"
        + (" · wake grace" if p.get("in_wake_grace") else ""),
        f"Watchdog tick: `{_age(wd.get('last_run_age'))}`",
        f"Net: `{'ok' if p.get('net_ok') else 'FAIL'}` · 1.1.1.1:443",
        f"Load: `{load_s}` · up `{up_s}`",
        f"pmset sleep: `{p.get('sleep_setting') or '?'}`",
        "",
        "_caffeinate -dims blocks idle/disk/AC-system sleep; display may sleep._",
        "_Lid close / battery / thermal can still sleep the Mac — physics limit._",
    ]
    if p.get("reasons"):
        lines.append("")
        lines.append("Reasons: " + " · ".join(p["reasons"]))

    # Keep-awake starts a real background process, so it gets a row to itself rather than
    # sitting beside a navigation button a thumb is aiming for. Hide Start when already on.
    buttons: List[ButtonRow] = []
    if not ka.get("running"):
        buttons.append([("▶️ Start keep-awake", "estate:host_keepawake_start")])
    buttons.append([("⚙️ Daemons", "estate:daemons")])
    buttons.append(nav("host"))
    lines.append("")
    lines.append(panel_stamp("host"))
    return "\n".join(lines), buttons
