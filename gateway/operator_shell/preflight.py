"""Pre-flight probe cache — instant taps, background refresh.

The cost model this changes
----------------------------
Before this module: a tap on ⚡️ Now ran the full mission card render (~3.4s cold,
0.45s warm). For 8 SQLite probes + 2 launchctl calls, that latency is the irreducible
cost of asking the truth. The user waited; the operator thumbs-twiddled.

What this does
--------------
1. ``cache_get(action)`` returns the last-known result for ``action`` even when past
   TTL (stale-while-revalidate). Returns ``None`` only when the cache is empty.
   The third element of the tuple is ``fresh`` — True when age ≤ TTL.
2. ``cache_put(action, text, buttons, ok=True)`` stores the most recent result. Same
   payload shape every panel already returns. ``ok=False`` marks a render that could
   not reach the truth; it is dropped while a recent good entry exists, so a transient
   bridge blip cannot pin the cockpit in a broken state (see cache_put).
3. ``cache_refresh(action, render_fn)`` runs ``render_fn()`` in a daemon thread,
   stores the result. The caller returns the cached value immediately and the
   next tap will see the fresh one.
4. ``warmup_slow_panels()`` pre-fills slow panels at gateway boot so the first
   phone tap is not a 60s+ cold probe.

Why a separate module
---------------------
* Two storage slots exist already (``state_meta`` in state.db, ``mission_cards``
  in state.db). Using a third (a JSON file in ``~/.hermes/state/``) keeps the
  cache out of the critical path of state.db and survives schema migrations
  without an upgrade step.
* The cache MUST never serve stale data to an action that mutates state. The
  only mutating callers (``handle_estate_action`` after a write) already
  invalidate by passing a fresh result via ``cache_put``; reads read from
  the cache and never write.

TTL ladder
----------
* 5s   : ``refresh`` (mission card) — user just tapped, real-time feel matters
* 30s  : ``run``, ``tune`` (control surfaces) — state moves on operator action
* 60s  : ``daemons``, ``prospector_daemon``, ``signal_engine``, ``builds``
* 120s : ``st_*`` — Stripe/reconcile probes; re-probe less often
* ∞    : anything else — caller is responsible for caching if it wants to

The TTL is a hint, not a contract: a stale entry will still be returned and
the result re-rendered in the background. The point of the TTL is to avoid
the *background* refresh firing on a tap the user is about to take again —
saving the work entirely.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Any]  # List[Tuple[str, str]] at the panel layer; loose import to avoid cycle.

_DIR = Path(os.path.expanduser("~/.hermes/state"))
_PATH = _DIR / "preflight-cache.json"
_LOCK = threading.Lock()

# How long the last GOOD entry outranks a failed render. See cache_put.
_FAILURE_GRACE_S = 300

_TTL: dict = {
    # action: TTL seconds
    "refresh": 5,
    "run": 30,
    "tune": 30,
    "daemons": 60,
    "prospector_daemon": 60,
    "signal_engine": 60,
    "rsi": 60,
    "inbox": 30,
    "activity": 30,
    "diff": 30,
    "status": 30,
    "builds": 60,
    "st_status": 120,
    "st_health": 120,
    "st_reconcile": 120,
    "st_money": 120,
}

# Panels whose cold path is felt on the phone — warm at gateway boot (P1-8).
# st_health / st_money mint live Stripe objects / run long proofs — still warmed
# when the cache is empty so the first phone tap is not a cold miss, but skipped
# when a fresh entry already exists (see warmup_slow_panels).
_WARMUP_ACTIONS = (
    "st_status",
    "st_health",
    "st_reconcile",
    "st_money",
    "builds",
    "refresh",
)


def _load_unlocked() -> dict:
    try:
        if not _PATH.is_file():
            return {}
        with _PATH.open("r") as f:
            return json.load(f)
    except Exception as exc:
        logger.debug("preflight: cache load failed: %s", exc)
        return {}


def _store_unlocked(data: dict) -> None:
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(data, f)
        tmp.replace(_PATH)
    except Exception as exc:
        logger.debug("preflight: cache store failed: %s", exc)


def _load() -> dict:
    with _LOCK:
        return _load_unlocked()


def _store(data: dict) -> None:
    with _LOCK:
        _store_unlocked(data)


def cache_get(action: str) -> Optional[Tuple[str, List[ButtonRow], bool]]:
    """Return ``(text, buttons, fresh)`` for action, or None if empty.

    Stale entries are still returned (``fresh=False``) so the phone never blocks
    on a cold probe when *any* prior result exists. Callers should background-
    refresh when ``fresh`` is False.
    """
    with _LOCK:
        data = _load_unlocked()
        entry = data.get(action)
        if not entry:
            return None
        age = time.time() - float(entry.get("ts", 0))
        ttl = _TTL.get(action, 30)
        fresh = age <= ttl
        return entry.get("text", ""), entry.get("buttons", []), fresh


def cache_put(action: str, text: str, buttons: List[ButtonRow], ok: bool = True) -> None:
    """Store a fresh result. Thread-safe (full read-modify-write under lock).

    ``ok=False`` means the render could not reach the truth (bridge down, exception) —
    NOT that it reached the truth and the truth is bad. A failing store proof is real
    information and is cached like any other answer; "estate unavailable" is the
    absence of an answer.

    A not-ok render is dropped while a good entry younger than ``_FAILURE_GRACE_S``
    exists. On 2026-08-06 the boot warmup rendered the home card inside the brief
    window when a gateway restart had the coordinator bridge down, cached
    "🔴 UNKNOWN — estate unavailable", and served it for 26 minutes over a healthy
    estate (422 task rows, agent running, connect OK). The founder's report was
    "nothing works". One transient render pinned the entire cockpit.

    Past the grace window the failure IS stored. "Never cache a failure" alone swaps
    one silent lie for another: a real outage would then sit behind a comfortable
    stale-good card indefinitely. A few minutes of the last known answer is a
    kindness; an hour of it is a lie.
    """
    with _LOCK:
        data = _load_unlocked()
        if not ok:
            prev = data.get(action)
            age = (time.time() - float(prev.get("ts", 0))) if prev else None
            if age is not None and age <= _FAILURE_GRACE_S:
                logger.info(
                    "preflight: dropped failed render for %s; serving last good (%.0fs old)",
                    action, age,
                )
                return
            logger.warning(
                "preflight: caching FAILED render for %s — no good entry within %ss",
                action, _FAILURE_GRACE_S,
            )
        data[action] = {"ts": time.time(), "text": text, "buttons": buttons, "ok": ok}
        # Trim to the last 20 actions to keep the file small.
        if len(data) > 20:
            for k in sorted(data, key=lambda k: data[k].get("ts", 0))[: len(data) - 20]:
                data.pop(k, None)
        _store_unlocked(data)


def cache_invalidate(action: str) -> None:
    with _LOCK:
        data = _load_unlocked()
        data.pop(action, None)
        _store_unlocked(data)


def cache_refresh(action: str, render_fn: Callable[[], Tuple[str, List[ButtonRow]]]) -> None:
    """Run render_fn() in a daemon thread and store the result.

    Used by the dispatch path: caller returns the cached value immediately, then
    the next tap will see the fresh one. Errors here MUST NOT propagate — the
    cache is best-effort, never a source of truth.
    """

    def _runner() -> None:
        try:
            rendered = render_fn()
            # 3-tuples carry the render's own ok flag; 2-tuples are the older contract
            # (external callers, tests) and are trusted as before. This path is the one
            # that poisoned the cockpit on 2026-08-06 — the boot warmup renders here,
            # with no PanelView in sight, so the flag has to travel in the return value.
            if len(rendered) == 3:
                text, buttons, ok = rendered
            else:
                text, buttons = rendered
                ok = True
            cache_put(action, text, buttons, ok=ok)
        except Exception as exc:
            logger.debug("preflight: refresh %s failed: %s", action, exc)

    t = threading.Thread(target=_runner, name=f"preflight-{action}", daemon=True)
    t.start()


def warmup_slow_panels(
    actions: Tuple[str, ...] = _WARMUP_ACTIONS,
    render_fn: Optional[Callable[[str], Tuple[str, List[ButtonRow]]]] = None,
) -> None:
    """Pre-fill slow panels in background threads at gateway boot.

    Best-effort: failures are logged and never raised. Does not block the caller.
    ``render_fn`` defaults to ``estate._render_for_cache`` so this module stays
    free of a hard import cycle at module load.
    """

    def _default_render(action: str) -> Tuple[str, List[ButtonRow]]:
        from gateway.operator_shell.estate import _render_for_cache

        return _render_for_cache(action)

    render = render_fn or _default_render

    for action in actions:
        # Skip if we already have a fresh entry — no need to re-probe on every restart.
        existing = cache_get(action)
        if existing is not None and existing[2]:
            continue
        cache_refresh(action, lambda a=action: render(a))
    logger.info("preflight: warmup started for %s", ", ".join(actions))
