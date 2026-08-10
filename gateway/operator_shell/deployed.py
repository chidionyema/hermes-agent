"""
🚀 Deployed — the estate-wide answer to "what is actually running, and is it today's code?"

Why this panel exists
---------------------
On 2026-08-10 the founder had to ask, in words, whether a change shipped that morning was
live. Answering it took eight hand-run shell calls comparing a file mtime to a process start
time. Nothing was broken — the code WAS deployed — but there was no surface that said so, and
a ledger in `docs/TELEGRAM_OPERATOR_PROGRAM.md` had been asserting `NOT STARTED` for work that
had already shipped. That is the defect this file closes.

The rule that makes it hold
---------------------------
**Every row is a probe, not a stored string.** Nothing here reads a status someone wrote down;
each cell is computed at render time from the live process table, the live filesystem, git, and
the network. A written status goes stale silently. A computed one cannot.

Adding an estate component is one row in `_LOCAL` / `_ENGINES` / `_REMOTE` / `_REPOS` below —
not a new code path — so a thing that gets deployed cannot quietly fail to appear here.

Bounding
--------
Telegram gives a callback a few seconds before the operator assumes the bot is dead, so every
probe is bounded and they all run concurrently against one wall-clock deadline
(`_DEADLINE_S`). A probe that overruns renders as `⏱ timeout`, never as green, and never as a
blank that reads like health.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ButtonRow = List[Tuple[str, str]]

HOME = Path.home()
HERMES = HOME / ".hermes"
HERMES_AGENT = HERMES / "hermes-agent"
PROSPECTOR = HOME / "Documents" / "code" / "prospector"

# `fly` lives in /usr/local/bin, which launchd does NOT put on PATH for a LaunchAgent — a bare
# `fly` resolves interactively and fails under the gateway. Absolute path, with the bare name
# kept only as a fallback for a machine that installed it elsewhere.
_FLY_CANDIDATES = ("/usr/local/bin/fly", "/opt/homebrew/bin/fly", "fly")

_GREEN, _AMBER, _RED, _GREY = "🟢", "🟡", "🔴", "⚪"

# Whole-panel wall clock. Individual probes get their own, smaller, timeouts.
#
# 22s, not the 12s this started at, and the reason is worth stating: the engine fingerprint is
# not a cheap read. Recomputing it means importing the whole `prospector` package in a subprocess
# (`code_fingerprint` hashes every module's bytes), which measured ~10-13s cold and pushed the
# panel past a 12s deadline — rendering the one row that matters most as `⏱ timed out`. The
# result is cached for `_FP_TTL_S`, so a re-probe tap is instant and only a cold render pays.
# `incident_panel` already documents a ~30s worst case in this codebase, so this is in keeping.
_DEADLINE_S = 22.0
_FP_TTL_S = 120.0

# ── The estate registry ─────────────────────────────────────────────────────────────────────
# (label, launchctl job name, the code this process actually loads)
#
# The third column is the one that is easy to get wrong and expensive to get wrong. Only two of
# these four daemons run the hermes-agent repo; `coordinator` and `idle-engine` run standalone
# scripts in `~/.hermes/scripts/`. Pointing all four at the repo — the obvious guess — made an
# edit to `estate.py` report `coordinator` as running stale code, which is false and is exactly
# the kind of amber that teaches an operator to stop reading the panel.
#
# Each value below was read off `launchctl print gui/<uid>/<job>` on 2026-08-10:
#   gateway      venv/bin/python -m hermes_cli.main gateway     → the repo
#   coordinator  /bin/zsh coordinator-daemon.sh → coordinator.py → the script
#   otto-server  /bin/bash otto-daemon.sh → cd hermes-agent, venv python, PYTHONPATH=repo → repo
#   idle-engine  python idle_engine.py --daemon                  → the script
# `tests/gateway/operator_shell/test_deployed_panel.py` re-derives them from launchctl so this
# table cannot drift away from what the machine is really running.
_LOCAL: List[Tuple[str, str, Path]] = [
    ("gateway",     "ai.hermes.gateway",     HERMES_AGENT),
    ("coordinator", "ai.hermes.coordinator", HERMES / "scripts" / "coordinator.py"),
    ("otto-server", "ai.hermes.otto-server", HERMES_AGENT),
    ("idle-engine", "ai.hermes.idle-engine", HERMES / "scripts" / "idle_engine.py"),
]

# Engines re-exec themselves in place (`os.execv` preserves PID *and* start time), so process
# age is worthless for them and a content fingerprint is the only honest probe. `label, repo,
# log marker, the command that recomputes the fingerprint on disk`.
#
# `code_fingerprint(config_path)` — the argument is NOT optional in practice and the default is a
# trap. Argless it hashes the package only; the daemon passes its config path
# (`run_scheduled.py:1416`), so config.yaml is inside the hash it logs. Calling it the argless way
# here produced `033b7d4b1855` against a logged `776a692b1a3e` and painted a healthy engine
# 🔴 STALE CODE on this panel's first run. A probe must call the function exactly as the process
# under test calls it, or it is measuring a different thing and reporting it as drift.
_ENGINES: List[Tuple[str, Path, str, List[str]]] = [
    (
        "scheduler",
        PROSPECTOR,
        "Running code fingerprint:",
        [
            str(PROSPECTOR / ".venv" / "bin" / "python"),
            "-c",
            "from prospector.scheduler.run_scheduled import code_fingerprint;"
            "print(code_fingerprint('config.yaml') or '')",
        ],
    ),
]

# (label, fly app name, public URL that proves what is SERVING — not what fly thinks it deployed,
#  set of status codes that mean "up" for THIS component)
#
# The per-component code set exists because an API that 404s its own root is healthy, and painting
# that amber trains the operator to ignore amber. Each entry states what "up" means for that
# component rather than assuming 200 everywhere.
_REMOTE: List[Tuple[str, str, str, frozenset]] = [
    ("store-api", "prospector-store-api", "https://api.mumchimp.com/catalog", frozenset({200})),
    ("store-web", "prospector-store-web", "https://mumchimp.com/",            frozenset({200})),
    ("tie-api",   "tie-api",              "https://tie-api.fly.dev/",         frozenset({200, 404})),
    ("tie-web",   "tie-web",              "https://tie-web.fly.dev/",         frozenset({200})),
]

# (label, repo path, the ref that counts as "pushed")
_REPOS: List[Tuple[str, Path, str]] = [
    ("hermes-agent", HERMES_AGENT, "backup/main"),
    ("prospector",   PROSPECTOR,   "origin/main"),
]


# ── primitives ──────────────────────────────────────────────────────────────────────────────
def _run(cmd: List[str], timeout: float, cwd: Optional[Path] = None) -> Tuple[int, str]:
    """Bounded subprocess. Returns (rc, stdout). rc 124 == timed out, rc 127 == not found."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        return p.returncode, (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except (FileNotFoundError, OSError):
        return 127, ""


def _git(repo: Path, *args: str, timeout: float = 5.0) -> str:
    rc, out = _run(["git", "-C", str(repo), *args], timeout)
    return out if rc == 0 else ""


def _ago(seconds: float) -> str:
    s = int(max(seconds, 0))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _boot_epoch() -> Optional[float]:
    """System boot time. The floor under every process start time, and the anchor that rescues
    the pids whose own start time is unreadable."""
    rc, out = _run(["sysctl", "-n", "kern.boottime"], 5.0)
    if rc != 0:
        return None
    m = re.search(r"sec\s*=\s*(\d+)", out)
    return float(m.group(1)) if m else None


def _proc_start_epoch(pid: int) -> Tuple[Optional[float], bool]:
    """(start epoch, is_exact). `is_exact=False` means the value is a lower bound, not a reading.

    On this machine `ps -o lstart` returns `Thu  1 Jan 01:45:24 1970` for two long-lived daemons
    (pids 1705 and 1732) — a start time BEFORE the kernel's own boot time, so it is not a slow
    clock, it is an unreadable value. Believing it would make every file on disk look newer than
    the process and paint those rows amber forever; discarding it entirely throws away a fact we
    do have. A process cannot predate its kernel, so boot time is used as a lower bound and the
    row says `up ≥ …` rather than claiming a precision it does not have.
    """
    boot = _boot_epoch()
    rc, out = _run(["ps", "-o", "lstart=", "-p", str(pid)], 5.0)
    if rc != 0 or not out:
        return (boot, False) if boot else (None, False)
    # macOS `ps` renders lstart in the locale's order. On this machine that is
    # `Sun  9 Aug 17:35:05 2026` — day BEFORE month, with the day space-padded — not the
    # `Sun Aug  9 ...` that %a %b %d expects. Guessing one order made every local daemon read
    # "start time unreadable", i.e. four amber rows for a healthy estate. Both orders are tried,
    # and whitespace is collapsed first so the padded single-digit day cannot break the match.
    normalised = " ".join(out.split())
    started = None
    for fmt in ("%a %d %b %H:%M:%S %Y", "%a %b %d %H:%M:%S %Y"):
        try:
            started = datetime.strptime(normalised, fmt).astimezone()
            break
        except ValueError:
            continue
    if started is None:
        return (boot, False) if boot else (None, False)
    epoch = started.timestamp()
    if boot and epoch < boot:
        return boot, False  # a process cannot predate its kernel — bound it, don't believe it
    return epoch, True


def _code_mtime(root: Path) -> Optional[float]:
    """When the code at `root` last changed.

    A file root is its own mtime. A repo root is the newest mtime among *tracked* `.py` files —
    tracked-only on purpose, so an untracked scratch file or a `__pycache__` entry cannot read as
    an undeployed change.
    """
    try:
        if root.is_file():
            return root.stat().st_mtime
    except OSError:
        return None
    if not root.is_dir():
        return None

    out = _git(root, "ls-files", "-z", "--", "*.py", timeout=6.0)
    if not out:
        return None
    newest = 0.0
    for rel in out.split("\0"):
        if not rel:
            continue
        try:
            m = os.stat(root / rel).st_mtime
        except OSError:
            continue
        if m > newest:
            newest = m
    return newest or None


# ── probes ──────────────────────────────────────────────────────────────────────────────────
def _probe_local(label: str, job: str, code_root: Path) -> Tuple[str, str, str]:
    """(status, label, detail) for a launchd-supervised local daemon."""
    rc, out = _run(["launchctl", "list", job], 6.0)
    if rc != 0 or not out:
        return _RED, label, "not loaded in launchd"

    pid: Optional[int] = None
    m = re.search(r'"PID"\s*=\s*(\d+)', out)
    if m:
        pid = int(m.group(1))
    if pid is None:
        last = re.search(r'"LastExitStatus"\s*=\s*(-?\d+)', out)
        code = last.group(1) if last else "?"
        return _RED, label, f"not running (last exit {code})"

    started, exact = _proc_start_epoch(pid)
    if started is None:
        return _AMBER, label, f"pid {pid} · start time unreadable"

    age = ("up " if exact else "up ≥") + _ago(time.time() - started)
    changed = _code_mtime(code_root)
    if changed is None:
        return _AMBER, label, f"pid {pid} · {age} · code age unknown"
    if changed > started:
        # With an exact start time this is drift. With a bounded one it is only *possible* drift:
        # the process may have started after the edit and simply not be able to prove it. Saying
        # "may be" costs nothing; saying "STALE" about a healthy daemon costs the panel's credit.
        verb = "NEWER — restart" if exact else "newer than the bound — may be stale"
        return _AMBER, label, f"pid {pid} · {age} · code {_ago(time.time() - changed)} {verb}"
    return _GREEN, label, f"pid {pid} · {age} · code in sync"


_fp_cache: Dict[str, Tuple[float, str]] = {}


def _probe_engine(
    label: str, repo: Path, log_marker: str, recompute: List[str]
) -> Tuple[str, str, str]:
    """An engine that re-execs in place: compare the fingerprint it LOGGED at startup with the
    fingerprint of the code on disk right now."""
    running_fp = ""
    for logdir in (HERMES / "logs", repo / "store" / "scheduler", repo / "logs"):
        if not logdir.is_dir():
            continue
        try:
            logs = sorted(logdir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for lg in logs[:6]:
            rc, out = _run(["grep", "-h", log_marker, str(lg)], 5.0)
            if rc == 0 and out:
                running_fp = out.strip().splitlines()[-1].split(log_marker)[-1].strip()
        if running_fp:
            break
    if not running_fp:
        return _AMBER, label, "no startup fingerprint logged"

    cached = _fp_cache.get(label)
    if cached and time.time() - cached[0] < _FP_TTL_S:
        rc, disk_fp = 0, cached[1]
    else:
        rc, disk_fp = _run(recompute, _DEADLINE_S - 4.0, cwd=repo)
        if rc == 0 and disk_fp:
            _fp_cache[label] = (time.time(), disk_fp)
    if rc == 124:
        return _AMBER, label, f"running {running_fp[:12]} · disk check ⏱ timeout"
    if rc != 0 or not disk_fp:
        return _AMBER, label, f"running {running_fp[:12]} · disk fingerprint unavailable"

    short_run, short_disk = running_fp[:12], disk_fp[:12]
    if short_run == short_disk:
        return _GREEN, label, f"fp {short_run} = disk"
    return _RED, label, f"running {short_run} ≠ disk {short_disk} — STALE CODE"


_fly_cache: Dict[str, object] = {"at": 0.0, "apps": {}}


def _fly_apps() -> Dict[str, dict]:
    """One `fly apps list --json` for the whole estate, cached 60s.

    Never trusted on its own: `fly auth whoami` is known to succeed against a dead token, so
    fly's view is reported alongside an independent HTTP probe and never instead of it.
    """
    if time.time() - float(_fly_cache["at"]) < 60 and _fly_cache["apps"]:
        return _fly_cache["apps"]  # type: ignore[return-value]
    for exe in _FLY_CANDIDATES:
        rc, out = _run([exe, "apps", "list", "--json"], 20.0)
        if rc == 0 and out:
            try:
                apps = {a["Name"]: a for a in json.loads(out) if a.get("Name")}
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
            _fly_cache["at"], _fly_cache["apps"] = time.time(), apps
            return apps
    return {}


def _http(url: str, timeout: float = 10.0) -> Tuple[Optional[int], float]:
    """GET (never HEAD — a HEAD-only probe passes against origins that 404 the real body).
    Reads a byte so a hung body counts as a failure, not a pass."""
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-deployed-probe/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
            return r.status, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0
    except Exception:
        return None, time.time() - t0


def _probe_remote(
    label: str, app: str, url: str, ok_codes: frozenset, apps: Dict[str, dict]
) -> Tuple[str, str, str]:
    meta = apps.get(app)
    fly_state = (meta or {}).get("Status") or ("unknown" if meta is None else "?")
    code, secs = _http(url)
    host = url.split("//", 1)[-1].split("/", 1)[0]

    # The HTTP probe outranks fly's own view, deliberately. `fly` reports what it last deployed,
    # from a token that is known to authenticate successfully after it has stopped working; the
    # request is what a buyer actually gets.
    if code is None:
        return _RED, label, f"fly:{fly_state} · {host} UNREACHABLE"
    if code >= 500 or code not in ok_codes:
        return _RED, label, f"fly:{fly_state} · {host} {code}"
    if fly_state not in ("deployed", "running"):
        return _AMBER, label, f"fly:{fly_state} · {host} {code} in {secs:.1f}s"
    if secs > 5.0:
        return _AMBER, label, f"fly:{fly_state} · {host} {code} but SLOW {secs:.1f}s"
    return _GREEN, label, f"fly:{fly_state} · {host} {code} in {secs:.1f}s"


def _probe_repo(label: str, repo: Path, pushed_ref: str) -> Tuple[str, str, str]:
    head = _git(repo, "rev-parse", "--short", "HEAD")
    if not head:
        return _RED, label, "not a readable git repo"
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    dirty = len([ln for ln in _git(repo, "status", "--porcelain").splitlines() if ln])
    ahead_raw = _git(repo, "rev-list", "--count", f"{pushed_ref}..HEAD")
    ahead = int(ahead_raw) if ahead_raw.isdigit() else -1

    bits = [f"`{head}` {branch}"]
    status = _GREEN
    if ahead > 0:
        bits.append(f"{ahead} unpushed vs {pushed_ref}")
        status = _AMBER
    elif ahead < 0:
        bits.append(f"{pushed_ref} unreadable")
        status = _AMBER
    else:
        bits.append("pushed")
    if dirty:
        bits.append(f"{dirty} uncommitted")
        status = _AMBER if status == _GREEN else status
    return status, label, " · ".join(bits)


# ── render ──────────────────────────────────────────────────────────────────────────────────
def _worst(statuses: List[str]) -> str:
    for s in (_RED, _AMBER, _GREY):
        if s in statuses:
            return s
    return _GREEN if statuses else _GREY


def render_deployed() -> Tuple[str, List[ButtonRow]]:
    """One screen: what is running across the estate, and is it the code we think it is."""
    from gateway.operator_shell.panel_chrome import with_nav

    t0 = time.time()
    jobs: List[Tuple[str, str, tuple]] = []
    for label, job, repo in _LOCAL:
        jobs.append(("local", label, (_probe_local, (label, job, repo))))
    for label, repo, marker, cmd in _ENGINES:
        jobs.append(("engine", label, (_probe_engine, (label, repo, marker, cmd))))
    for label, repo, ref in _REPOS:
        jobs.append(("repo", label, (_probe_repo, (label, repo, ref))))

    apps = _fly_apps()
    for label, app, url, ok_codes in _REMOTE:
        jobs.append(("remote", label, (_probe_remote, (label, app, url, ok_codes, apps))))

    results: Dict[str, List[Tuple[str, str, str]]] = {
        "local": [], "engine": [], "remote": [], "repo": []
    }
    order = {k: [lbl for k2, lbl, _ in jobs if k2 == k] for k in results}

    with ThreadPoolExecutor(max_workers=min(12, len(jobs))) as pool:
        futures = {pool.submit(fn, *args): (group, label) for group, label, (fn, args) in jobs}
        deadline = t0 + _DEADLINE_S
        done = set()
        try:
            for fut in as_completed(futures, timeout=max(1.0, deadline - time.time())):
                group, label = futures[fut]
                done.add(label)
                try:
                    results[group].append(fut.result())
                except Exception as exc:  # a probe that raises is amber, never green
                    results[group].append((_AMBER, label, f"probe error: {type(exc).__name__}"))
        except TimeoutError:
            pass
        for fut, (group, label) in futures.items():
            if label not in done:
                fut.cancel()
                results[group].append((_GREY, label, "⏱ timed out"))

    for group in results:
        rank = {lbl: i for i, lbl in enumerate(order[group])}
        results[group].sort(key=lambda r: rank.get(r[1], 99))

    all_status = [s for rows in results.values() for s, _, _ in rows]
    overall = _worst(all_status)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S %Z")

    lines = [
        f"{overall} *Deployed* — the whole estate",
        f"_probed {stamp} · every row computed, none stored_",
    ]

    def section(title: str, rows: List[Tuple[str, str, str]]) -> None:
        if not rows:
            return
        lines.append("")
        lines.append(f"*{title}*")
        width = max(len(lbl) for _, lbl, _ in rows)
        for status, label, detail in rows:
            lines.append(f"{status} `{label.ljust(width)}` {detail}")

    section("⚙️ Local daemons", results["local"])
    section("🔬 Engines (fingerprint, not uptime)", results["engine"])
    section("☁️ Remote — Fly + live HTTP", results["remote"])
    section("📦 Repos", results["repo"])

    reds = [lbl for s, lbl, _ in [r for rows in results.values() for r in rows] if s == _RED]
    if reds:
        lines += ["", f"🔴 *Needs you:* {', '.join(reds)}"]

    lines += ["", f"_{time.time() - t0:.1f}s to probe {len(jobs)} components_"]

    # No explicit "re-probe" button: `with_nav` already emits `🔄 → estate:deployed`, and this
    # estate enforces one-destination-one-name (`test_destination_vocabulary.py`). A second
    # button to the same place under a different label is how a menu starts lying about how
    # many things it can do.
    buttons: List[ButtonRow] = [
        [("🔍 Diagnose", "estate:diagnose_panel"), ("📊 Status", "estate:status")],
        [("🧠 Nodes", "estate:pd_nodes")],
    ]
    return "\n".join(lines), with_nav(buttons, "deployed")
