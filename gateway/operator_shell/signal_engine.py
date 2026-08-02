"""Signal Engine phone control — com.signalengine.daemon + control-file protocol.

Mirrors prospector_daemon.py, with three differences that come from this being the
MONEY rail rather than the generation rail:

1. Two supervisors, two truths. launchd owns the process; the daemon owns its own
   control file (data_store/daemon_control.json, rewritten by write_state() every
   cycle — daemon.py:292,310). Neither alone is honest: launchd says "running" for a
   wedged process, and a fresh control file says nothing about who will restart it.
   Both are reported, and a disagreement is shown as a disagreement.

2. TCC_DENIED and UNSUPERVISED are first-class states, not "🔴 stopped". On
   2026-07-31 this daemon was found dead for 37 days while its watchdog reported ok
   2,732 times, because launchd could not read the repo (EX_CONFIG/78 — python@3.12
   has no Full Disk Access grant) and the watchdog exited 0 regardless. A panel that
   renders that as a plain red dot invites "just tap Start", which cannot work. The
   fix is a founder GUI action, so the panel says so.

3. Money knobs are settable from the phone (founder's explicit call, 2026-07-31) but
   anything that can move real capital — execution.mode, ramp.stage — takes a second
   ARM screen that prints live equity and the drawdown killswitch first. Ops knobs
   keep the single confirm used everywhere else. Secrets are never read or shown.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav

ButtonRow = List[Tuple[str, str]]

REPO = Path.home() / "Documents" / "code" / "signalengine"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL = "com.signalengine.daemon"
PLIST = PLIST_DIR / f"{LABEL}.plist"
CONFIG = REPO / "config.yaml"
CONTROL = REPO / "data_store" / "daemon_control.json"
# Logs live OUTSIDE ~/Documents, and that is load-bearing. launchd opens
# StandardOutPath/StandardErrorPath itself, before exec'ing the program — and the
# per-user launchd has no TCC grant for the Documents folder, so a log path under
# ~/Documents/code/signalengine made the whole job die with 78/EX_CONFIG before
# Python ever started. Proven 2026-07-31: the identical unit, with only these two
# paths changed to /tmp, went from `last exit code = 78` to `state = running`.
# Granting the interpreter Documents access does NOT fix this; launchd is the one
# opening the file, not python.
LOG_DIR = Path.home() / ".hermes" / "logs"
ERR_LOG = LOG_DIR / "signalengine-daemon.err.log"
OUT_LOG = LOG_DIR / "signalengine-daemon.out.log"
# The pre-2026-07-31 logs stay readable from the phone: they hold the 37-day
# outage and everything before it.
LEGACY_ERR_LOG = REPO / "daemon.err.log"
LEGACY_OUT_LOG = REPO / "daemon.out.log"
LOGS: Tuple[Path, ...] = (ERR_LOG, OUT_LOG, LEGACY_ERR_LOG, LEGACY_OUT_LOG)

# Anchored on the `-m <module>` argv pair. The bare module name false-positives on
# any command line that merely quotes it — measured 2026-07-31, the loose pattern
# matched a hermes_queue.py alert whose text was "signal_engine.daemon was not
# running...". Same guard as ~/.hermes/scripts/signal-engine-daemon-watchdog.sh.
PGREP_GUARD = r"[-]m signal_engine\.daemon"

# tick_interval_sec defaults to 60 (config.py:215) and each cycle rewrites the
# control file, so anything past 10 cycles is wedged rather than merely between ticks.
STALE_AFTER_S = 600

# Path to hand the founder for the Full Disk Access grant. Derived from the venv
# interpreter, which is a symlink into the Cellar; TCC keys on the resolved binary.
_TCC_HINT_PATH = (
    "/usr/local/Cellar/python@3.12/3.12.8/Frameworks/Python.framework/"
    "Versions/3.12/bin/python3.12"
)


def _uid() -> int:
    return os.getuid()  # windows-footgun: ok — POSIX launchd (macOS) helper


# ── Observation ─────────────────────────────────────────────────────────────


def installed() -> bool:
    return PLIST.is_file()


def daemon_pid() -> Optional[int]:
    """PID of a running daemon regardless of who started it (launchd or a shell)."""
    try:
        r = subprocess.run(
            ["pgrep", "-f", PGREP_GUARD], capture_output=True, text=True, timeout=5
        )
        for ln in (r.stdout or "").split():
            try:
                return int(ln)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def launchctl_state() -> Dict[str, object]:
    """launchd's view only. `state` is one of:

    not_installed | unloaded | tcc_denied | crashing | running | not running | error
    """
    if not PLIST.is_file():
        return {
            "running": False,
            "pid": None,
            "state": "not_installed",
            "detail": "plist missing",
            "installed": False,
            "last_exit": None,
            "runs": None,
        }
    target = f"gui/{_uid()}/{LABEL}"
    try:
        r = subprocess.run(
            ["launchctl", "print", target], capture_output=True, text=True, timeout=5
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
                "last_exit": None,
                "runs": None,
            }
        state = "unknown"
        pid: Optional[int] = None
        last_exit: Optional[str] = None
        runs: Optional[int] = None
        for ln in out.splitlines():
            s = ln.strip()
            if s.startswith("state ="):
                state = s.split("=", 1)[1].strip()
            elif s.startswith("pid ="):
                try:
                    pid = int(s.split("=", 1)[1].strip())
                except Exception:
                    pid = None
            elif s.startswith("last exit code ="):
                last_exit = s.split("=", 1)[1].strip()
            elif s.startswith("runs ="):
                try:
                    runs = int(s.split("=", 1)[1].strip())
                except Exception:
                    runs = None
        running = state == "running" or (
            pid is not None and pid > 0 and state != "not running"
        )
        # 78 = EX_CONFIG. For this unit it has exactly one proven cause: the venv
        # interpreter cannot read ~/Documents (TCC.db 2026-07-31: python@3.14 → 2,
        # cpython-3.11.15 → 0, python@3.12 → no row). KeepAlive will retry this
        # forever and never succeed, so it is NOT a crash loop — it is a config wall.
        if not running and last_exit and last_exit.split(":")[0].strip() == "78":
            return {
                "running": False,
                "pid": None,
                "state": "tcc_denied",
                "detail": "EX_CONFIG(78) — interpreter denied Full Disk Access",
                "installed": True,
                "last_exit": last_exit,
                "runs": runs,
            }
        if not running and last_exit and last_exit.split(":")[0].strip() not in ("0", ""):
            return {
                "running": False,
                "pid": None,
                "state": "crashing",
                "detail": f"exiting {last_exit} · runs {runs or 0}",
                "installed": True,
                "last_exit": last_exit,
                "runs": runs,
            }
        return {
            "running": running,
            "pid": pid,
            "state": state,
            "detail": (f"pid {pid}" if (running and pid) else state),
            "installed": True,
            "last_exit": last_exit,
            "runs": runs,
        }
    except Exception as exc:
        return {
            "running": False,
            "pid": None,
            "state": "error",
            "detail": str(exc)[:60],
            "installed": True,
            "last_exit": None,
            "runs": None,
        }


def _ago(ts: float) -> str:
    age = max(0, int(time.time() - ts))
    if age < 90:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"


def read_control() -> Dict[str, object]:
    """Control file as-is. Never raises — a missing/corrupt file is a fact to show."""
    if not CONTROL.is_file():
        return {}
    try:
        data = json.loads(CONTROL.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"_read_error": str(exc)[:80]}


def heartbeat_age_s() -> Optional[int]:
    """Seconds since the daemon last rewrote its control file. None if never."""
    try:
        return int(time.time() - CONTROL.stat().st_mtime)
    except Exception:
        return None


def health() -> Dict[str, object]:
    """Combined truth: launchd + process table + heartbeat.

    `verdict` is what the panel and the fleet glance line both key off:
      ok | stalled | unsupervised | tcc_denied | down | not_installed
    """
    st = launchctl_state()
    pid = daemon_pid()
    age = heartbeat_age_s()
    fresh = age is not None and age < STALE_AFTER_S

    if st.get("state") == "tcc_denied" and not pid:
        verdict = "tcc_denied"
    elif not pid:
        verdict = "not_installed" if st.get("state") == "not_installed" else "down"
    elif not fresh:
        verdict = "stalled"
    elif st.get("state") in ("unloaded", "not_installed"):
        # A live process launchd does not own. It works until the Mac reboots or the
        # shell that spawned it goes away, and nothing will restart it. Distinct from
        # healthy: this is exactly the shape the 37-day outage hid behind.
        verdict = "unsupervised"
    else:
        verdict = "ok"

    ctrl = read_control()
    state_blk = ctrl.get("state") or {} if isinstance(ctrl, dict) else {}
    return {
        "verdict": verdict,
        "launchd": st,
        "pid": pid,
        "heartbeat_s": age,
        "paused": bool(state_blk.get("paused")),
        "equity": state_blk.get("equity"),
        "running_flag": state_blk.get("running"),
        "last_warmup_at": state_blk.get("last_warmup_at") or "",
        "control": ctrl,
    }


_VERDICT_EMOJI = {
    "ok": "🟢",
    "stalled": "🟠",
    "unsupervised": "🟡",
    "tcc_denied": "🚫",
    "down": "🔴",
    "not_installed": "⚫",
}

_VERDICT_WORD = {
    "ok": "healthy (launchd-supervised)",
    "stalled": "process alive but heartbeat STALE",
    "unsupervised": "running, but launchd does NOT own it",
    "tcc_denied": "BLOCKED — EX_CONFIG(78), launchd refused before exec",
    "down": "DOWN",
    "not_installed": "no LaunchAgent installed",
}


def _tail_lines(paths: Tuple[Path, ...], n: int = 4) -> str:
    for path in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            nontrivial = [ln for ln in lines if ln.strip()]
            if not nontrivial:
                continue
            clean = [
                re.sub(r"\x1b\[[0-9;]*m", "", ln)[:90] for ln in nontrivial[-n:]
            ]
            return "\n".join(f"   `{ln}`" for ln in clean)
        except Exception:
            continue
    return "   _(no log yet)_"


# ── Params ──────────────────────────────────────────────────────────────────
# (yaml_key_regex, allowed values, tier, human label)
# tier: ops | money | arm
#   ops   → single confirm, same as every other panel
#   money → single confirm, flagged 💰 (changes risk appetite, not the rail itself)
#   arm   → two screens; the second prints live equity + killswitch before applying,
#           because these are the two keys that decide whether real capital moves.

_ParamSpec = Tuple[str, Tuple[str, ...], str, str]

_SAFE_PARAMS: Dict[str, _ParamSpec] = {
    "exec_mode": (
        r"(^\s*mode:\s*)\S+",
        ("internal_sim", "testnet", "live"),
        "arm",
        "execution.mode",
    ),
    "ramp_stage": (
        r"(^\s*stage:\s*)\S+",
        ("paper_forward", "tiny_real", "scaled"),
        "arm",
        "ramp.stage",
    ),
    "vol_target": (
        r"(^\s*vol_target:\s*)[0-9.]+",
        ("0.05", "0.10", "0.20"),
        "money",
        "risk.vol_target",
    ),
    "leverage": (
        r"(^\s*leverage:\s*)[0-9.]+",
        ("1", "2", "3"),
        "money",
        "risk.caps.leverage",
    ),
    "per_instrument": (
        r"(^\s*per_instrument:\s*)[0-9.]+",
        ("0.05", "0.1", "0.2"),
        "money",
        "risk.caps.per_instrument",
    ),
    "killswitch": (
        r"(^\s*portfolio_dd_killswitch:\s*)[0-9.]+",
        ("0.05", "0.10", "0.15"),
        "money",
        "risk.caps.portfolio_dd_killswitch",
    ),
    "max_positions": (
        r"(^\s*max_positions:\s*)[0-9]+",
        ("3", "5", "10"),
        "money",
        "risk.caps.max_positions",
    ),
    "stop_loss": (
        r"(^\s*stop_loss_pct:\s*)[0-9.]+",
        ("0", "0.05", "0.10"),
        "money",
        "risk.caps.stop_loss_pct",
    ),
    "llm_cap": (
        r"(^\s*daily_cap_usd:\s*)[0-9.]+",
        ("1", "2", "5"),
        "money",
        "llm.spend_budget.daily_cap_usd",
    ),
    "live_feed": (
        r"(^\s*enabled:\s*)\S+",
        ("true", "false"),
        "ops",
        "live_feed.enabled",
    ),
}

# Read-back patterns are the same regexes with the value captured instead of the key.
_READ_PATTERNS: Dict[str, str] = {
    "exec_mode": r"^\s*mode:\s*(\S+)",
    "ramp_stage": r"^\s*stage:\s*(\S+)",
    "vol_target": r"^\s*vol_target:\s*([0-9.]+)",
    "leverage": r"^\s*leverage:\s*([0-9.]+)",
    "per_instrument": r"^\s*per_instrument:\s*([0-9.]+)",
    "killswitch": r"^\s*portfolio_dd_killswitch:\s*([0-9.]+)",
    "max_positions": r"^\s*max_positions:\s*([0-9]+)",
    "stop_loss": r"^\s*stop_loss_pct:\s*([0-9.]+)",
    "llm_cap": r"^\s*daily_cap_usd:\s*([0-9.]+)",
    "live_feed": r"^\s*enabled:\s*(\S+)",
}

# Values that mean "real capital is or could be in play".
_HOT_VALUES = {"exec_mode": ("testnet", "live"), "ramp_stage": ("tiny_real", "scaled")}


def read_params() -> Dict[str, object]:
    """Current allowlisted knobs, straight from config.yaml. No secrets are read."""
    out: Dict[str, object] = {}
    try:
        text = CONFIG.read_text(encoding="utf-8")
    except Exception as exc:
        return {"_config_err": str(exc)[:80]}
    for key, pat in _READ_PATTERNS.items():
        m = re.search(pat, text, re.M)
        out[key] = m.group(1).strip().strip('"').strip("'") if m else None
    return out


def is_armed(params: Optional[Dict[str, object]] = None) -> bool:
    """True when config says real (or exchange-connected) capital can move."""
    p = params if params is not None else read_params()
    return (
        str(p.get("exec_mode")) in _HOT_VALUES["exec_mode"]
        or str(p.get("ramp_stage")) in _HOT_VALUES["ramp_stage"]
    )


def set_param(key: str, value: str) -> Tuple[bool, str, bool]:
    """Apply an allowlisted knob to config.yaml.

    Returns (ok, detail, needs_restart). Every write is verified by re-reading the
    file and rolling back the original bytes if the value did not land — a config.yaml
    left half-patched on the money rail is worse than a rejected tap.
    """
    key = (key or "").strip().lower()
    value = str(value).strip()
    if key not in _SAFE_PARAMS:
        return False, f"param `{key}` is not phone-editable", False
    pattern, allowed, _tier, label = _SAFE_PARAMS[key]
    if value not in allowed:
        return False, f"`{value}` not allowed for {key} (use {', '.join(allowed)})", False
    if not CONFIG.is_file():
        return False, f"config.yaml missing at `{CONFIG}`", False

    original = CONFIG.read_text(encoding="utf-8")
    hits = len(re.findall(pattern, original, re.M))
    if hits != 1:
        # Ambiguity is a refusal, not a guess: patching the wrong `mode:` on the
        # money rail is unrecoverable from a phone.
        return False, f"{label}: expected 1 match in config.yaml, found {hits}", False

    new_text = re.sub(pattern, rf"\g<1>{value}", original, count=1, flags=re.M)
    if new_text == original:
        return True, f"{label} already `{value}` (no change)", False

    CONFIG.write_text(new_text, encoding="utf-8")
    after = read_params().get(key)
    if str(after) != value:
        CONFIG.write_text(original, encoding="utf-8")
        return False, f"{label}: write did not verify (read back `{after}`) — rolled back", False
    old = re.search(_READ_PATTERNS[key], original, re.M)
    old_val = old.group(1) if old else "?"
    return True, f"{label} `{old_val}` → `{value}` (config.yaml)", True


# ── Control-file protocol (pause/resume/restart/reset) ──────────────────────

_CONTROL_ACTIONS = ("pause", "resume", "restart", "reset")


def _atomic_write_control(data: Dict[str, object]) -> None:
    CONTROL.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTROL.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, CONTROL)


def send_command(action: str) -> Tuple[bool, str]:
    """Queue a command for the daemon and PROVE it landed.

    The daemon rewrites this same file every cycle (write_state), so a read-modify-
    write can be clobbered by a tick landing in between. Rather than assume, the
    command id is read back; a lost write is retried once and then reported as failed.
    """
    action = (action or "").strip().lower()
    if action not in _CONTROL_ACTIONS:
        return False, f"unknown control action `{action}`"
    if not CONTROL.parent.is_dir():
        return False, f"data_store missing at `{CONTROL.parent}`"

    for attempt in (1, 2):
        cid = str(uuid.uuid4())
        data = read_control()
        data.pop("_read_error", None)
        data["command"] = {
            "action": action,
            "id": cid,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _atomic_write_control(data)
        except Exception as exc:
            return False, f"write failed: {str(exc)[:80]}"
        time.sleep(0.2)
        landed = (read_control().get("command") or {}).get("id")
        if landed == cid:
            return True, f"`{action}` queued as `{cid[:8]}` (applies next tick, ≤60s)"
        if attempt == 1:
            continue
        return False, f"command lost — daemon overwrote it (saw `{str(landed)[:8]}`)"
    return False, "unreachable"


def ack_line() -> str:
    ack = read_control().get("ack") or {}
    if not isinstance(ack, dict) or not ack.get("id"):
        return ""
    mark = "✅" if ack.get("status") == "applied" else "⚠️"
    err = f" · `{str(ack.get('error'))[:50]}`" if ack.get("error") else ""
    return f"{mark} ack `{str(ack.get('action'))}` {ack.get('status')} `{str(ack.get('at'))[:19]}`{err}"


def _under_documents(path: Path) -> bool:
    """True when launchd would have to open this path inside the TCC-gated folder."""
    try:
        path.relative_to(Path.home() / "Documents")
        return True
    except ValueError:
        return False


# ── Rendering ───────────────────────────────────────────────────────────────


def _tcc_block() -> List[str]:
    """Explain EX_CONFIG(78), most-likely cause first.

    Ordering matters — this is what the founder reads on a phone. On 2026-07-31 the
    actual cause was cause #1 and the panel originally only listed cause #2, which
    would have sent them to a GUI screen that could not have fixed it.
    """
    lines = [
        "🚫 *launchd cannot start this daemon.* It exits `EX_CONFIG(78)`",
        "before Python runs, and KeepAlive retries forever.",
        "",
        "*1. A path launchd itself must open is inside ~/Documents.*",
        "launchd opens StandardOut/ErrorPath before exec, and it has no",
        "Documents grant. Fixable from here — no GUI needed:",
    ]
    for label, path in (("stdout", OUT_LOG), ("stderr", ERR_LOG)):
        inside = "❌ inside ~/Documents" if _under_documents(path) else "✅ outside"
        lines.append(f"  {label}: `{path}` — {inside}")
    lines += [
        "",
        "*2. Or the interpreter cannot read the repo.* Founder, one-time",
        "(GUI only — cannot be scripted): System Settings → Privacy &",
        "Security → Files and Folders → Documents (or Full Disk Access) → `+`",
        f"`{_TCC_HINT_PATH}`",
    ]
    return lines


def _status_lines(h: Dict[str, object]) -> List[str]:
    st = h.get("launchd") or {}
    verdict = str(h.get("verdict"))
    emoji = _VERDICT_EMOJI.get(verdict, "⚪")
    lines = [f"{emoji} *{_VERDICT_WORD.get(verdict, verdict)}*"]

    hb = h.get("heartbeat_s")
    hb_txt = "never" if hb is None else f"{_ago(time.time() - hb)} ago"
    pid = h.get("pid")
    lines.append(
        f"• process `{pid or 'none'}` · launchd `{st.get('detail')}` · heartbeat `{hb_txt}`"
    )

    eq = h.get("equity")
    eq_txt = f"${float(eq):,.2f}" if isinstance(eq, (int, float)) else "?"
    paused = "⏸ PAUSED" if h.get("paused") else "▶️ active"
    lines.append(f"• equity `{eq_txt}` · {paused}")

    p = read_params()
    hot = is_armed(p)
    rail = "🔴 ARMED — real capital rail" if hot else "🧪 paper (`internal_sim`)"
    lines.append(
        f"• {rail} · mode `{p.get('exec_mode')}` · ramp `{p.get('ramp_stage')}`"
    )
    lines.append(
        f"• killswitch `{p.get('killswitch')}` · leverage `{p.get('leverage')}` · "
        f"vol_target `{p.get('vol_target')}`"
    )
    ack = ack_line()
    if ack:
        lines.append(f"• {ack}")

    if verdict == "unsupervised":
        lines += [
            "",
            "⚠️ This process was started by hand. Nothing restarts it if it dies,",
            "and it will not survive a reboot. Load the LaunchAgent to fix.",
        ]
    if verdict == "tcc_denied":
        lines += [""] + _tcc_block()
    if verdict == "stalled":
        lines += [
            "",
            f"⚠️ Alive but no control-file write in {hb}s (limit {STALE_AFTER_S}s).",
            "Wedged, not dead — Restart is the right action.",
        ]
    return lines


def render_signal_engine() -> Tuple[str, List[ButtonRow]]:
    """Main control card."""
    if not REPO.is_dir():
        return (
            "💹 *Signal Engine*\n\n"
            "⚫ repo missing at `~/Documents/code/signalengine` — not wired.\n\n"
            + panel_stamp("signal_engine"),
            [[("🚀 Fleet", "estate:fleet")], nav()],
        )

    h = health()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "💹 *Signal Engine control* (money rail)",
        f"_captured {now_utc} · unit `{LABEL}` (KeepAlive)_",
        "",
    ]
    lines += _status_lines(h)
    lines += ["", "*Recent log*", _tail_lines(LOGS, n=3)]

    verdict = str(h.get("verdict"))
    running = verdict in ("ok", "stalled", "unsupervised") and bool(
        h.get("pid") or h.get("running_flag")
    )
    buttons: List[ButtonRow] = []
    # The first row is whatever actually helps in THIS state, not a fixed toolbar.
    primary_restart = False
    if verdict == "tcc_denied":
        buttons.append([("📜 Logs", "estate:se_logs"), ("🔄 Re-check", "estate:signal_engine")])
    elif verdict == "unsupervised":
        buttons.append([("🛡 Load LaunchAgent", "estate:se_start")])
    elif verdict == "stalled":
        buttons.append([("♻️ Restart", "estate:se_restart")])
        primary_restart = True

    # Context-aware ops row: don't duplicate primary Restart; hide Start when up; hide Stop when dead.
    ops: ButtonRow = []
    if not primary_restart:
        ops.append(("♻️ Restart", "estate:se_restart"))
    if running:
        ops.append(("⏹ Stop", "estate:se_stop"))
    else:
        ops.append(("▶️ Start", "estate:se_start"))
    if ops:
        buttons.append(ops)
    buttons.append(
        [
            ("▶️ Resume engine" if h.get("paused") else "⏸ Pause engine",
             "estate:se_resume" if h.get("paused") else "estate:se_pause"),
            ("💰 Knobs", "estate:tune"),
            ("📜 Logs", "estate:se_logs"),
        ]
    )
    buttons.append(
        [("💰 Money room", "estate:room:money"), ("🔭 Prospector", "estate:prospector_daemon")]
    )
    buttons = with_nav(buttons, "signal_engine")
    lines.append("")
    lines.append(panel_stamp("signal_engine"))
    return "\n".join(lines).rstrip(), buttons


def render_params() -> Tuple[str, List[ButtonRow]]:
    p = read_params()
    if p.get("_config_err"):
        return (
            f"💰 *Signal Engine knobs*\n\n⚠️ `{p['_config_err']}`",
            [[("💹 Back", "estate:signal_engine")]],
        )
    armed = is_armed(p)
    lines = [
        "💰 *Signal Engine knobs* (config.yaml — no secrets)",
        "",
        ("🔴 *ARMED* — these settings can move real capital." if armed
         else "🧪 *Paper* — `internal_sim` / `paper_forward`, nothing real moves."),
        "",
        "*Rail* 💰💰",
        f"• execution.mode `{p.get('exec_mode')}` · ramp.stage `{p.get('ramp_stage')}`",
        "*Risk* 💰",
        f"• vol_target `{p.get('vol_target')}` · leverage `{p.get('leverage')}` · "
        f"per_instrument `{p.get('per_instrument')}`",
        f"• dd_killswitch `{p.get('killswitch')}` · max_positions `{p.get('max_positions')}` · "
        f"stop_loss `{p.get('stop_loss')}`",
        "*Spend / feed*",
        f"• llm daily_cap `${p.get('llm_cap')}` · live_feed `{p.get('live_feed')}`",
        "",
        "_Rail changes require a second ARM screen. All changes restart the daemon._",
    ]
    # This screen used to carry all 23 setter buttons itself — the densest in the cockpit —
    # and was STILL incomplete: `per_instrument` (all 3 values), `stop_loss: 0` and
    # `llm_cap` 2 and 5 were allowlisted in _SAFE_PARAMS but had no button, so 6 of the 29
    # allowlisted values could not be reached from the phone at all.
    #
    # The setters now live in Tune, grouped by consequence, where all 29 fit without any
    # group exceeding 9 buttons. This screen keeps its job — showing what the values ARE —
    # and hands off to the group that changes them. One place per knob, not two.
    buttons: List[ButtonRow] = [
        [("⚡ Execution", "estate:tune:exec"), ("🎚 Sizing", "estate:tune:sizing")],
        [("🛡 Safety", "estate:tune:safety"), ("💵 Spend", "estate:tune:spend")],
        [("💹 Daemon", "estate:signal_engine"), ("📜 Logs", "estate:se_logs")],
        nav("se_params"),
    ]
    return "\n".join(lines), buttons


def confirm_set_param(key: str, value: str) -> Tuple[str, List[ButtonRow]]:
    """First confirm screen. Rail keys route to the ARM screen, not straight to apply."""
    key = (key or "").strip().lower()
    value = str(value).strip()
    if key not in _SAFE_PARAMS:
        return f"Unknown/unsafe knob `{key}`", [[("💰 Knobs", "estate:se_params")]]
    _pat, allowed, tier, label = _SAFE_PARAMS[key]
    if value not in allowed:
        return (
            f"Value `{value}` not in allowlist for `{key}`: {', '.join(allowed)}",
            [[("💰 Knobs", "estate:se_params")]],
        )
    p = read_params()
    cur = p.get(key)
    if str(cur) == value:
        return (
            f"`{label}` is already `{value}` — nothing to change.",
            [[("💰 Knobs", "estate:se_params")]],
        )

    hot = value in _HOT_VALUES.get(key, ())
    if tier == "arm":
        next_cb = f"estate:se_arm:{key}:{value}"
        head = "🔴 *RAIL CHANGE*" if hot else "🧪 *Rail change (back to safe)*"
        text = (
            f"{head}\n\n"
            f"`{label}`: `{cur}` → `{value}`\n\n"
            + ("This decides whether real capital can move. One more screen with live "
               "equity and the killswitch before it applies."
               if hot else
               "This moves the rail toward paper. Confirm on the next screen.")
        )
        return text, [
            [("➡️ Review", next_cb), ("✗ Cancel", "estate:se_params")]
        ]

    flag = "💰 " if tier == "money" else ""
    text = (
        f"⚙️ *{flag}Set `{label}` = `{value}`?*\n\n"
        f"Current `{cur}` → `{value}`\n"
        f"Daemon restarts to pick it up (a few seconds of no ticks)."
    )
    return text, [
        [
            ("✅ Confirm", f"estate:se_set_confirm:{key}:{value}"),
            ("✗ Cancel", "estate:se_params"),
        ]
    ]


def arm_card(key: str, value: str) -> Tuple[str, List[ButtonRow]]:
    """Second screen for rail changes — the live numbers, then apply."""
    key = (key or "").strip().lower()
    value = str(value).strip()
    if key not in _SAFE_PARAMS or _SAFE_PARAMS[key][2] != "arm":
        return f"`{key}` is not a rail knob", [[("💰 Knobs", "estate:se_params")]]
    _pat, allowed, _tier, label = _SAFE_PARAMS[key]
    if value not in allowed:
        return (
            f"Value `{value}` not in allowlist: {', '.join(allowed)}",
            [[("💰 Knobs", "estate:se_params")]],
        )
    h = health()
    p = read_params()
    eq = h.get("equity")
    eq_txt = f"${float(eq):,.2f}" if isinstance(eq, (int, float)) else "unknown"
    hot = value in _HOT_VALUES.get(key, ())
    lines = [
        "🔴 *ARM CHECK*" if hot else "🧪 *Disarm check*",
        "",
        f"`{label}`: `{p.get(key)}` → *`{value}`*",
        "",
        f"• equity now `{eq_txt}`",
        f"• dd killswitch `{p.get('killswitch')}` · leverage `{p.get('leverage')}` · "
        f"per_instrument `{p.get('per_instrument')}`",
        f"• stop_loss `{p.get('stop_loss')}` · max_positions `{p.get('max_positions')}`",
        f"• daemon `{_VERDICT_WORD.get(str(h.get('verdict')), '?')}`",
        "",
    ]
    if hot:
        lines += [
            "*This authorises the money rail.* Orders stop being simulated.",
            "Nothing else in the estate can undo this from a phone except you.",
        ]
    else:
        lines += ["This returns the rail to a safer setting. Positions are not closed by this change."]
    return "\n".join(lines), [
        [
            ("🔴 ARM IT" if hot else "✅ Apply", f"estate:se_set_confirm:{key}:{value}"),
            ("✗ Cancel", "estate:se_params"),
        ]
    ]


def render_logs() -> Tuple[str, List[ButtonRow]]:
    lines = ["📜 *Signal Engine logs*", ""]
    for path in LOGS:
        lines.append(f"*{path.name}*")
        if not path.is_file():
            lines.append("   _(missing)_")
        else:
            try:
                lines.append(f"   _mtime {_ago(path.stat().st_mtime)} ago_")
            except Exception:
                pass
            lines.append(_tail_lines((path,), n=8))
        lines.append("")
    return "\n".join(lines).rstrip(), [
        [("💹 Daemon", "estate:signal_engine"), ("💰 Knobs", "estate:se_params")],
        [("🚀 Fleet", "estate:fleet")],
        nav("se_logs"),
    ]


def confirm_card(op: str) -> Tuple[str, List[ButtonRow]]:
    """Confirm screen for start/stop/restart/pause/resume/reset."""
    h = health()
    verdict = str(h.get("verdict"))
    if op in ("start", "restart") and not installed():
        return (
            f"⚫ `{LABEL}` NOT INSTALLED.\n\n"
            f"Founder:\n`launchctl bootstrap gui/{_uid()} {PLIST}`",
            [[("💹 Back", "estate:signal_engine")]],
        )
    if op in ("start", "restart") and verdict == "tcc_denied":
        # Refusing here is the honest move: the tap cannot work, and pretending
        # otherwise is what let a dead daemon look serviceable for 37 days.
        return (
            "🚫 *Start will not work yet.*\n\n" + "\n".join(_tcc_block()),
            [[("🔄 Re-check", "estate:signal_engine"), ("📜 Logs", "estate:se_logs")]],
        )

    words = {
        "start": "Start / load",
        "stop": "Stop (bootout)",
        "restart": "Restart",
        "pause": "Pause trading loop",
        "resume": "Resume trading loop",
        "reset": "Reset daemon state",
    }
    warn = {
        "stop": "\n\nNo ticks, no fills, no risk checks until it is started again.",
        "restart": "\n\nA few seconds of no ticks. Open positions are untouched.",
        "pause": "\n\nThe process keeps running and keeps its state; it just stops trading.",
        "reset": "\n\n⚠️ Clears daemon runtime state (warmup, applied command id). "
                 "Equity and positions come from the store, not from this file.",
    }.get(op, "")
    if is_armed() and op in ("stop", "pause", "reset"):
        warn += "\n\n🔴 Rail is ARMED — open positions will be left unmanaged."
    text = f"💹 *{words.get(op, op)}* Signal Engine?{warn}"
    return text, [
        [("✅ Confirm", f"estate:se_{op}_confirm"), ("✗ Cancel", "estate:signal_engine")]
    ]


# ── Operations ──────────────────────────────────────────────────────────────


def _launchctl(cmd: List[str]) -> Tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    detail = ((r.stderr or r.stdout or "").strip() or "ok")[:220]
    return r.returncode == 0, detail


def run_op(op: str) -> Tuple[bool, str]:
    """start/stop/restart via launchd; pause/resume/reset via the control file.

    Every branch ends by re-reading real state, so the receipt quotes what IS, not
    what was requested. `ok` is False whenever the observed end state disagrees.
    """
    op = (op or "").strip().lower()
    target = f"gui/{_uid()}/{LABEL}"

    if op in ("pause", "resume", "reset"):
        pid = daemon_pid()
        if not pid:
            return False, f"daemon is not running — `{op}` would never be read"
        ok, detail = send_command(op)
        return ok, detail

    if not installed():
        return False, f"NOT INSTALLED — no `{PLIST}`"

    before = health()
    try:
        if op == "start":
            if before.get("verdict") == "ok":
                return True, f"already healthy · `{(before.get('launchd') or {}).get('detail')}`"
            # An unsupervised hand-started copy must go before launchd starts its own,
            # or two daemons trade the same book. Proven possible: launchd's KeepAlive
            # does not know about a process it did not spawn.
            killed = ""
            if before.get("verdict") == "unsupervised" and before.get("pid"):
                try:
                    os.kill(int(before["pid"]), 15)  # type: ignore[arg-type]
                    time.sleep(1.5)
                    killed = f"stopped unsupervised pid {before['pid']} first · "
                except Exception as exc:
                    return False, f"could not stop unsupervised pid: {str(exc)[:60]}"
            st = launchctl_state()
            if st.get("state") in ("unloaded", "not_installed"):
                ok, detail = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(PLIST)]
                )
            else:
                ok, detail = _launchctl(["launchctl", "kickstart", target])
            if not ok and "already" in detail.lower():
                ok, detail = _launchctl(["launchctl", "kickstart", target])
            time.sleep(2.0)
            after = health()
            v = str(after.get("verdict"))
            if v == "tcc_denied":
                return False, (
                    f"{killed}{detail} · unit loaded but exits EX_CONFIG(78) — "
                    "Full Disk Access grant still missing"
                )
            return bool(ok and v in ("ok", "unsupervised")), (
                f"{killed}{detail} · now `{_VERDICT_WORD.get(v, v)}` pid `{after.get('pid')}`"
            )

        if op == "stop":
            ok, detail = _launchctl(["launchctl", "bootout", target])
            if not ok and (
                "No such process" in detail
                or "Could not find" in detail
                or "not found" in detail.lower()
            ):
                ok = True
            time.sleep(1.0)
            after = health()
            # bootout does not touch a hand-started process; say so instead of
            # reporting a stop that did not stop anything.
            leftover = after.get("pid")
            if leftover:
                return False, (
                    f"{detail} · unit unloaded BUT pid `{leftover}` is still alive "
                    "(hand-started, launchd cannot stop it)"
                )
            return bool(ok), f"{detail} · now `{_VERDICT_WORD.get(str(after.get('verdict')))}`"

        if op == "restart":
            ok, detail = _launchctl(["launchctl", "kickstart", "-k", target])
            if not ok:
                _launchctl(["launchctl", "bootout", target])
                time.sleep(0.4)
                ok_b, d_b = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(PLIST)]
                )
                ok_k, d_k = _launchctl(["launchctl", "kickstart", "-k", target])
                ok, detail = ok_k, f"fallback bootout/bootstrap: {d_b}; {d_k}"
            time.sleep(2.0)
            after = health()
            v = str(after.get("verdict"))
            changed = after.get("pid") != before.get("pid")
            if v == "tcc_denied":
                return False, f"{detail} · EX_CONFIG(78) — Full Disk Access grant missing"
            return bool(ok and v == "ok"), (
                f"{detail} · was pid {before.get('pid')} → now pid {after.get('pid')} "
                f"`{_VERDICT_WORD.get(v, v)}`" + (" · pid changed" if changed else "")
            )

        return False, f"unknown op `{op}`"
    except Exception as exc:
        return False, str(exc)[:200]


def glance_line(h: Optional[Dict[str, object]] = None) -> str:
    """One-liner for the fleet / mission card.

    Accepts a pre-computed health() so a caller that already decided whether to show
    the line does not pay for a second launchctl+pgrep round trip on every render.
    """
    h = h if h is not None else health()
    v = str(h.get("verdict"))
    if v == "not_installed" and not h.get("pid"):
        return "💹 Signal Engine: NOT INSTALLED"
    eq = h.get("equity")
    eq_txt = f"${float(eq):,.0f}" if isinstance(eq, (int, float)) else "?"
    hb = h.get("heartbeat_s")
    hb_txt = "never" if hb is None else _ago(time.time() - hb)
    rail = "ARMED" if is_armed() else "paper"
    paused = " ⏸" if h.get("paused") else ""
    return (
        f"{_VERDICT_EMOJI.get(v, '⚪')} signal `{_VERDICT_WORD.get(v, v)}`{paused} · "
        f"eq `{eq_txt}` · hb `{hb_txt}` · `{rail}`"
    )
