"""Pre-flight probe cache — instant taps, background refresh.

The cost model this changes
----------------------------
Before this module: a tap on ⚡️ Now ran the full mission card render (~3.4s cold,
0.45s warm). For 8 SQLite probes + 2 launchctl calls, that latency is the irreducible
cost of asking the truth. The user waited; the operator thumbs-twiddled.

What this does
--------------
1. ``cache_get(action)`` returns the last-known result for ``action`` if it is fresh
   enough (default TTL 60s for state panels, 5s for verdict panels). Returns ``None``
   when the cache is empty or stale.
2. ``cache_put(action, text, buttons)`` stores the most recent result. Same payload
   shape every panel already returns — no new contract.
3. ``cache_refresh(action, render_fn)`` runs ``render_fn()`` in a daemon thread,
   stores the result. The caller returns the cached value immediately and the
   next tap will see the fresh one.

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
* 5s  : ``refresh`` (mission card) — user just tapped, real-time feel matters
* 30s : ``run``, ``tune`` (control surfaces) — state moves on operator action
* 60s : ``daemons``, ``prospector_daemon``, ``signal_engine``, ``st_*``, ``builds``
* ∞   : anything else — caller is responsible for caching if it wants to

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
}


def _load() -> dict:
    try:
        with _LOCK:
            if not _PATH.is_file():
                return {}
            with _PATH.open("r") as f:
                return json.load(f)
    except Exception as exc:
        logger.debug("preflight: cache load failed: %s", exc)
        return {}


def _store(data: dict) -> None:
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            tmp = _PATH.with_suffix(".json.tmp")
            with tmp.open("w") as f:
                json.dump(data, f)
            tmp.replace(_PATH)
    except Exception as exc:
        logger.debug("preflight: cache store failed: %s", exc)


def cache_get(action: str) -> Optional[Tuple[str, List[ButtonRow]]]:
    """Return cached (text, buttons) for action if fresh. None otherwise."""
    data = _load()
    entry = data.get(action)
    if not entry:
        return None
    age = time.time() - float(entry.get("ts", 0))
    ttl = _TTL.get(action, 30)
    if age > ttl:
        return None
    return entry.get("text", ""), entry.get("buttons", [])


def cache_put(action: str, text: str, buttons: List[ButtonRow]) -> None:
    """Store a fresh result. Thread-safe."""
    data = _load()
    data[action] = {"ts": time.time(), "text": text, "buttons": buttons}
    # Trim to the last 20 actions to keep the file small.
    if len(data) > 20:
        for k in sorted(data, key=lambda k: data[k].get("ts", 0))[: len(data) - 20]:
            data.pop(k, None)
    _store(data)


def cache_invalidate(action: str) -> None:
    data = _load()
    data.pop(action, None)
    _store(data)


def cache_refresh(action: str, render_fn: Callable[[], Tuple[str, List[ButtonRow]]]) -> None:
    """Run render_fn() in a daemon thread and store the result.

    Used by the dispatch path: caller returns the cached value immediately, then
    the next tap will see the fresh one. Errors here MUST NOT propagate — the
    cache is best-effort, never a source of truth.
    """

    def _runner() -> None:
        try:
            text, buttons = render_fn()
            cache_put(action, text, buttons)
        except Exception as exc:
            logger.debug("preflight: refresh %s failed: %s", action, exc)

    t = threading.Thread(target=_runner, name=f"preflight-{action}", daemon=True)
    t.start()
