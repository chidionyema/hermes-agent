"""🎛 Now — the engine readout panel.

The on-demand counterpart to the per-tick digest the engine pushes after every scheduling
pass. Loads `prospector/scheduler/status.py::status_snapshot()` path-based from the live
engine checkout, renders the digest as a Telegram message, and gives the operator buttons
that lead back to the engine's tooling (daemons, params, cron).

Read-only. No subprocess invocations. Pure function of the engine's on-disk state.

The path-based import mirrors the engine's own `_load_hermes_sender` in
`prospector/scheduler/alerts.py:296-357` — the renderer must work whether the engine is
in the main checkout, on a worktree branch, or missing entirely. The test fence is the
missing case: a moved engine must degrade to a one-line "engine unreachable" rather than
raise into the cockpit. NEVER RAISES by convention; every failure path returns a
degraded render.

Resolution order (`_candidate_paths()`): `PROSPECTOR_REPO` env var → the live checkout at
`~/Documents/code/prospector` → the engine branch under development at
`~/Documents/code/prospector/.worktrees/feat-now-telegram-status-digest`. The env var is
re-read on every call so a moved engine is picked up without a restart; the module-level
list `_PROSPECTOR_PATHS` is the fallback chain and is the surface tests monkeypatch.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple

from gateway.operator_shell.panel_chrome import LEGEND, nav, panel_stamp

ButtonRow = List[Tuple[str, str]]


# Module-level fallback chain. Mutable so tests can monkeypatch it to verify the
# missing-path contract (see `test_render_never_raises_even_with_path_missing`).
_PROSPECTOR_PATHS: List[Path] = [
    Path.home() / "Documents" / "code" / "prospector",
    Path.home() / "Documents" / "code" / "prospector" / ".worktrees" / "feat-now-telegram-status-digest",
]


def _candidate_paths() -> List[Path]:
    """Fresh list per call: env var first, then the module's fallback chain.

    The env var is read at call time (not module init) so a moved engine is picked up
    without restarting the cockpit, and so a test that `delenv`s `PROSPECTOR_REPO` is
    not silently overridden by a stale module-level constant.
    """
    out: List[Path] = []
    env = os.environ.get("PROSPECTOR_REPO", "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.extend(_PROSPECTOR_PATHS)
    return out


def _format_age(seconds: Any) -> str:
    """Compact "Xs/Xm/Xh ago" string. None → "—". Never empty."""
    if seconds is None:
        return "—"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if s < 0:
        s = 0.0
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def _format_money(value: Any) -> str:
    """USD figure with two decimals; None / non-numeric → "—". Never empty."""
    if value is None:
        return "—"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _format_count(value: Any) -> str:
    """Int with no decimals; None / non-numeric → "—"."""
    if value is None:
        return "—"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _severity(snap: dict) -> Tuple[str, str]:
    """Return (glyph, severity_text). Computed from the snapshot, not remembered.

    The four-state glyph convention is the same one `status_summary.py` and
    `panel_chrome.LEGEND` use: 🟢 healthy, 🟡 watch, 🔴 act, ⚠️ alert. The order
    matters — moat_blind outranks an alert (the moat being down means nothing
    can be vetted, so a coincident alert is moot), and an alert outranks dead
    providers (an active alert is what the operator must look at, not the dead
    list behind it).
    """
    providers = snap.get("providers") or {}
    alerts = snap.get("alerts") or {}
    if providers.get("moat_blind"):
        return "🔴", "MOAT BLIND"
    active = alerts.get("active") or []
    if alerts.get("active_count") or active:
        return "⚠️", "ALERT"
    dead = [d for d in (providers.get("dead") or []) if isinstance(d, str)]
    if dead:
        return "🟡", f"{len(dead)} provider(s) dead"
    return "🟢", "healthy"


def _render_snapshot(snap: dict) -> Tuple[str, List[ButtonRow]]:
    """Pure: render a snapshot dict to a Telegram-ready (text, buttons) pair.

    Pure means no I/O, no path resolution, no env reads. Tests pass a fake snapshot
    directly; the live path (`render_prospector_now`) is responsible for fetching the
    snapshot from the engine and converting failures into the unreachable sentinel.
    The renderer is defensive against missing fields (a half-built snapshot still
    renders, with "—" where the data is missing) so a single None field never
    tears down the whole card.
    """
    snap = snap if isinstance(snap, dict) else {}
    daemon = snap.get("daemon") or {}
    last = snap.get("last_tick") or {}
    spend = snap.get("spend") or {}
    providers = snap.get("providers") or {}
    alerts = snap.get("alerts") or {}
    backlog = snap.get("backlog") or {}

    glyph, severity_text = _severity(snap)

    # Daemon phase / age / pid — every field individually optional.
    phase = daemon.get("phase") if isinstance(daemon.get("phase"), str) else "idle"
    age = _format_age(daemon.get("last_tick_age_s"))
    pid = daemon.get("pid")
    pid_s = f" · pid {pid}" if isinstance(pid, int) else ""
    daemon_line = f"⚙️ _{phase} ({age} ago{pid_s})_"

    # Last tick — None / empty dict both degrade to "no tick on record".
    if isinstance(last, dict) and last:
        dossiers = _format_count(last.get("dossiers"))
        passes = _format_count(last.get("passes"))
        kills = _format_count(last.get("kills"))
        defers = _format_count(last.get("defers"))
        cost = last.get("cost_usd")
        cost_s = f" · ${float(cost):.2f}" if isinstance(cost, (int, float)) else ""
        tick_line = f"📊 _tick: {dossiers} dossiers · {passes} pass · {kills} kill · {defers} defer{cost_s}_"
    else:
        tick_line = "📊 _no tick on record — idle_"

    # Spend — three figures (today / daily cap / subscription), each independently optional.
    spend_line = (
        f"💰 _spend {_format_money(spend.get('today_usd'))} / "
        f"{_format_money(spend.get('daily_cap_usd'))} · "
        f"sub {_format_money(spend.get('today_subscription_usd'))}_"
    )

    # Providers — moat_blind dominates, then dead list, then green.
    if providers.get("moat_blind"):
        reason = providers.get("blind_reason")
        reason_s = f" — {reason}" if isinstance(reason, str) and reason else ""
        providers_line = f"🔴 _moat blind{reason_s}_"
    else:
        dead = [d for d in (providers.get("dead") or []) if isinstance(d, str)]
        if dead:
            providers_line = f"🟡 _{len(dead)} dead: {','.join(dead[:5])}_"
        else:
            providers_line = "🟢 _providers healthy_"

    # Alerts — first title is enough; the count is in the severity header.
    active = alerts.get("active") or []
    if active and isinstance(active[0], dict):
        first = active[0]
        title = first.get("title") or first.get("key") or "alert"
        alerts_line = f"⚠ _{title}_"
    else:
        alerts_line = "✓ _alerts clear_"

    # Backlog — None fields render as "—" rather than "0" so the operator can tell
    # "I have no idea" from "the deferred queue is empty".
    deferred = _format_count(backlog.get("deferred"))
    provisional = _format_count(backlog.get("provisional"))
    backlog_line = f"📦 _backlog: {deferred} deferred · {provisional} provisional_"

    # Dead-provider list, surfaced once near the top so the operator sees what to fund
    # without scrolling past the spend and backlog lines.
    dead = [d for d in (providers.get("dead") or []) if isinstance(d, str)]
    dead_block = ""
    if dead and not providers.get("moat_blind"):
        dead_block = f"   _{', '.join(dead[:8])}_"

    lines = [
        f"{glyph} *Prospector* — {severity_text}",
        "",
        daemon_line,
        tick_line,
        spend_line,
        providers_line,
    ]
    if dead_block:
        lines.append(dead_block)
    lines.extend(
        [
            alerts_line,
            backlog_line,
            "",
            f"_{LEGEND}_",
            panel_stamp("prospector_now"),
        ]
    )

    text = "\n".join(lines)

    # Action buttons lead back to the engine's tooling — the renderer surfaces the
    # readout, the buttons are how the operator acts on it. The nav spine is the
    # last row, same as every other panel. "🔄 Refresh" re-renders this panel so the
    # operator can see whether the engine state changed without leaving the screen.
    buttons: List[ButtonRow] = [
        [("🔄 Refresh", "estate:prospector_now")],
        [("⚙️ Daemons", "estate:daemons"), ("🔧 Params", "estate:tune")],
        [("🗓 Cron", "estate:pd_cron")],
        nav(),
    ]
    return text, buttons


def _load_engine_status(repo_root: Path):
    """Path-based load of the engine's `prospector.scheduler.status` module.

    Returns the loaded module on success, None on any failure. The repo root is
    temporarily inserted into sys.path so the engine's own `from prospector.scheduler
    import paths` resolves; the insert is rolled back in the `finally` so a failed
    load does not pollute the import path for the next caller.
    """
    status_path = repo_root / "prospector" / "scheduler" / "status.py"
    if not status_path.is_file():
        return None
    root_str = str(repo_root)
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        spec = importlib.util.spec_from_file_location(
            "_prospector_status_now", status_path
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None
    finally:
        if inserted:
            try:
                sys.path.remove(root_str)
            except ValueError:
                pass


def _load_snapshot() -> Optional[dict]:
    """Try each candidate path. First one whose `status_snapshot(cfg)` returns a
    dict wins. Returns None on every failure — the caller emits the unreachable
    sentinel. Never raises: the engine module may import half a dozen of its own
    modules, any of which may be missing on the operator's machine.
    """
    for repo_root in _candidate_paths():
        try:
            mod = _load_engine_status(repo_root)
        except Exception:
            mod = None
        if mod is None:
            continue
        try:
            # Minimal cfg — the engine's `paths.py` needs `store_dir`, nothing else.
            # Resolve the store directory alongside the engine checkout so the engine
            # reads its own files. A SimpleNamespace is sufficient because `paths.py`
            # is duck-typed (it `getattr`s and raises on missing).
            cfg = SimpleNamespace(store_dir=str(repo_root / "store"))
            snap = mod.status_snapshot(cfg)
        except Exception:
            continue
        if isinstance(snap, dict):
            return snap
    return None


def _render_unreachable() -> Tuple[str, List[ButtonRow]]:
    """The one-line "engine unreachable" message + a Home button so the operator
    is never stranded. Used when no candidate path loaded, or when the engine's
    `status_snapshot` raised (a half-installed engine on the wrong Python, etc.).
    """
    text = (
        "⚠️ *Prospector* — engine unreachable\n\n"
        "The engine readout path did not load. The cockpit still works; "
        "this is the per-tick-digest counterpart, missing until the engine is on disk.\n\n"
        f"_{panel_stamp('prospector_now')}_"
    )
    buttons: List[ButtonRow] = [
        [("🏠 Home", "estate:refresh")],
        nav(),
    ]
    return text, buttons


def render_prospector_now() -> Tuple[str, List[ButtonRow]]:
    """Render the engine's `status_snapshot()` as a Telegram message with action buttons.

    The public entry. Guarantees:
      - returns `(text, buttons)` — never raises;
      - on a loaded engine, calls `_render_snapshot(snap)` with the real snapshot;
      - on a missing / broken engine, emits the one-line "engine unreachable" sentinel
        with a Home button so the operator can navigate away.

    The renderer is fault-tolerant by design: a moved engine, a half-installed
    dependency, a corrupted store — none of these reach the cockpit as an
    exception. They degrade to the unreachable sentinel.

    Returns (text, button_rows). The text is the digest + a severity legend; the
    buttons lead back to the engine's tooling (daemons, params, cron) so the
    operator can act on what the digest shows.
    """
    try:
        snap = _load_snapshot()
    except Exception:
        snap = None
    if snap is None:
        return _render_unreachable()
    try:
        return _render_snapshot(snap)
    except Exception:
        # A snapshot that fails to render is also "unreachable" from the operator's
        # point of view — never let an unexpected shape tear down the card.
        return _render_unreachable()
