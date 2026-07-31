"""Restart the gateway when its own source changes.

The complaint this exists for: "gateway not picking up latest changes." It was never a
packaging problem — hermes_agent is installed editable (`__editable__.hermes_agent-*.pth`),
so every start imports straight from `~/.hermes/hermes-agent`. The gap was that *nothing
connected an edit to a restart*. The process does restart often (110 logged connects), but
for unrelated reasons — a watchdog, a `--replace`, a kickstart — so whether the running code
matched the tree was luck. An operator edits a panel, taps the button, and gets the old one.

So: watch the tree, and when it settles after a change, exit. launchd's KeepAlive brings the
process straight back on the new code. This is safe by construction here — the gateway
already drains in-flight work on SIGTERM and re-schedules interrupted sessions on the way
back up (`run.py::_resume_interrupted_sessions`), which is the same path a manual restart
takes.

Three guards, because a self-restarting process that gets any of them wrong is a crash loop:

- **Only when supervised.** If nothing will restart us, exiting turns "stale code" into "no
  cockpit". `os.getppid() == 1` is the signal, the same one the shutdown forensics log uses.
- **Only after quiet.** A save mid-edit, a `git checkout`, a rebase touching forty files —
  all produce a burst. The tree must be unchanged for `quiet_for` seconds before we act, so a
  burst costs one restart at the end, not one per file.
- **Never twice.** The flag latches; if the exit does not take, we do not queue another.

Off switch: `HERMES_GATEWAY_AUTORELOAD=0`.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent

# The packages whose code this process actually runs. Tests, docs and data are excluded:
# a changed fixture is not a reason to drop a live Telegram poll.
_WATCHED = ("gateway", "hermes_cli", "agent", "sentinel")

_SKIP_DIRS = {"__pycache__", ".git", "node_modules", "venv", ".venv", "tests", "worktrees"}

# The tree as it was when this process imported its code. The cockpit reads it to answer
# "am I looking at my latest change?" without guessing from a pid or an uptime.
_BASELINE: Optional[Tuple[int, int, int]] = None
_WATCHING = False


def _iter_sources(root: Path = _ROOT, packages: Iterable[str] = _WATCHED):
    for pkg in packages:
        base = root / pkg
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if name.endswith(".py"):
                    yield Path(dirpath) / name


def fingerprint(root: Path = _ROOT) -> Tuple[int, int, int]:
    """(file count, newest mtime in ns, total bytes) — cheap and stat-only.

    Not a hash: hashing thousands of files every 15s to detect an edit is the wrong trade,
    and this only has to answer "did anything change", not "what". The triple catches the
    cases a bare mtime misses — a file deleted, or one replaced by a same-mtime copy of a
    different length, both of which `git checkout` produces.
    """
    count = 0
    newest = 0
    total = 0
    for path in _iter_sources(root):
        try:
            st = path.stat()
        except OSError:
            continue  # vanished mid-walk: the next pass will see the settled tree
        count += 1
        total += st.st_size
        if st.st_mtime_ns > newest:
            newest = st.st_mtime_ns
    return count, newest, total


def is_supervised() -> bool:
    """True when something will restart us. Reparenting to init/launchd is the tell."""
    if os.getenv("HERMES_GATEWAY_SUPERVISED", "").strip() in {"1", "true", "yes"}:
        return True
    try:
        return os.getppid() == 1
    except Exception:
        return False


def enabled() -> bool:
    return os.getenv("HERMES_GATEWAY_AUTORELOAD", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def start_watcher(
    interval: float = 15.0,
    quiet_for: float = 20.0,
    root: Path = _ROOT,
    on_restart=None,
) -> Optional[threading.Thread]:
    """Begin watching. Returns the thread, or None if the watcher is not applicable here."""
    if not enabled():
        logger.info("Source watch: disabled (HERMES_GATEWAY_AUTORELOAD=0)")
        return None
    if not is_supervised():
        # Deliberately not a warning: running the gateway in a terminal is a normal thing
        # to do, and it is exactly the case where exiting would be destructive.
        logger.info(
            "Source watch: inactive — no supervisor (ppid=%s), so a restart would not come back",
            os.getppid(),
        )
        return None

    global _BASELINE, _WATCHING
    baseline = fingerprint(root)
    _BASELINE = baseline
    fired = threading.Event()

    def _restart():
        logger.warning(
            "Source watch: gateway source changed and settled — restarting so the new code "
            "is live (in-flight sessions drain and auto-resume)"
        )
        if on_restart is not None:
            try:
                on_restart()
            except Exception:
                logger.exception("Source watch: on_restart hook failed; exiting anyway")
        # SIGTERM, not os._exit: it runs the same graceful shutdown a kickstart would, so
        # sessions drain and get re-scheduled instead of being cut mid-turn.
        os.kill(os.getpid(), signal.SIGTERM)

    def _loop():
        current = baseline
        changed_at = 0.0
        while not fired.is_set():
            time.sleep(interval)
            try:
                now_fp = fingerprint(root)
            except Exception:
                logger.debug("Source watch: fingerprint failed", exc_info=True)
                continue
            if now_fp != current:
                current = now_fp
                changed_at = time.time()
                logger.info("Source watch: change detected, waiting %.0fs for quiet", quiet_for)
                continue
            if changed_at and current != baseline and (time.time() - changed_at) >= quiet_for:
                fired.set()
                _restart()
                return

    thread = threading.Thread(target=_loop, name="source-watch", daemon=True)
    thread.start()
    _WATCHING = True
    logger.info(
        "Source watch: active — %d source files, restarting %.0fs after the tree settles",
        baseline[0], quiet_for,
    )
    return thread


def status(root: Path = _ROOT) -> dict:
    """What the cockpit shows: is the running code the code on disk?

    `stale` is only meaningful once a baseline exists. Without the watcher (a terminal run,
    or autoreload off) there is nothing to compare against, and reporting False would be a
    reassurance this cannot actually give.
    """
    if _BASELINE is None:
        return {"watching": False, "known": False, "stale": None, "files": 0}
    try:
        now = fingerprint(root)
    except Exception:
        return {"watching": _WATCHING, "known": False, "stale": None, "files": _BASELINE[0]}
    return {
        "watching": _WATCHING,
        "known": True,
        "stale": now != _BASELINE,
        "files": now[0],
    }
