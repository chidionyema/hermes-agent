"""Navigation history — the ← Back button and the breadcrumb, for every panel at once.

**This module was dead code from the day it was written.** Its previous docstring claimed
"the nav() function reads this stack and adds ← Back / → Forward buttons". It did not:
`panel_chrome.py` contained zero references to `nav_stack`, and no module anywhere imported
it (measured 2026-08-14: `rg -n nav_stack gateway/**/*.py` matched only this file). So the
cockpit had no way back on any of its 63 panels, while a file describing the way back sat
beside them claiming to be wired. That is the `built-and-unreachable` defect class, and a
docstring asserting its own integration is exactly the prose that "state is a probe, not a
paragraph" exists to forbid.

It is wired now, at ONE seam — `estate.handle_estate_action`, the single funnel every tap
passes through — so all 63 panels gain history without any of them being edited.

Three real defects fixed while wiring it:

1. `HERMES = Path(os.environ.get("HERMES_HOME", ...))` was evaluated at IMPORT time. Tests
   monkeypatch `HERMES_HOME` to a tempdir (`tests/conftest.py:360`), but a module imported
   before that patch keeps the real path — so the suite would have written the founder's live
   `~/.hermes/state/nav-stack.json` on every run. Same class as memory
   `tests-polluted-the-production-audit-log`. The path is now resolved per call.
2. The write was `STACK_FILE.write_text(...)` — non-atomic. A crash mid-write leaves truncated
   JSON, which reads back as a char-0 error rather than as bad JSON (memory
   `a-truncating-write-is-an-empty-read-not-bad-json`). Now tmp + `os.replace`.
3. `except:` bare — it swallowed `KeyboardInterrupt` and `SystemExit` too. Now typed, so a
   corrupt file degrades to an empty stack but a Ctrl-C still interrupts.

Nothing here may raise into a render. A panel that failed to draw because the *back button*
could not be computed would be a worse defect than having no back button, so every public
function is total: on any failure it returns the value meaning "no history".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_STACK = 50

# The navigation verbs themselves must never enter history — pushing `back` would make
# Back walk into itself.
_NEVER_PUSH = frozenset({"back", "forward"})

_EMPTY: Dict[str, Any] = {"stack": [], "forward_stack": [], "current": None}


def _stack_file() -> Path:
    """Resolved per call, never at import — see defect 1 in the module docstring."""
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "state" / "nav-stack.json"


def _load() -> Dict[str, Any]:
    path = _stack_file()
    try:
        if not path.is_file():
            return dict(_EMPTY)
        data = json.loads(path.read_text())
    except OSError:
        return dict(_EMPTY)
    except (ValueError, TypeError):
        # Corrupt or truncated: treat as no history rather than propagating. The next
        # push overwrites it.
        logger.warning("nav stack unreadable at %s; starting empty", path)
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    # Defensive: a hand-edited or half-migrated file must not KeyError a render.
    return {
        "stack": data.get("stack") or [],
        "forward_stack": data.get("forward_stack") or [],
        "current": data.get("current"),
    }


def _save(data: Dict[str, Any]) -> None:
    path = _stack_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write beside the target on the same filesystem, then rename. A rename
        # is atomic on POSIX, so a reader never sees a partial stack.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".nav-stack-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except BaseException:
            # Includes KeyboardInterrupt: still clean up the temp file before re-raising.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        # Read-only volume, full disk, permissions. History is a convenience; losing it
        # must never cost the operator the panel they asked for.
        logger.warning("could not persist nav stack to %s", path, exc_info=True)


def push_nav(action: str, label: str = "") -> None:
    """Record that the operator arrived at `action`. Called once per tap, at the funnel."""
    act = (action or "").strip()
    if not act or act in _NEVER_PUSH:
        return
    data = _load()
    current_entry = data.get("current")
    # Re-tapping the same panel (a 🔄 refresh) updates in place rather than stacking
    # duplicates — otherwise five refreshes cost five taps of Back to escape.
    if current_entry and current_entry.get("action") == act:
        current_entry["label"] = label or current_entry.get("label") or act
        current_entry["ts"] = time.time()
        _save(data)
        return
    if current_entry:
        data["stack"].append(current_entry)
        if len(data["stack"]) > MAX_STACK:
            data["stack"] = data["stack"][-MAX_STACK:]
    # Navigating somewhere new invalidates forward history, exactly like a browser.
    data["forward_stack"] = []
    data["current"] = {"action": act, "label": label or act, "ts": time.time()}
    _save(data)


def go_back() -> Optional[Dict[str, Any]]:
    """Step back one panel. Returns the entry to render, or None when already at the root."""
    data = _load()
    if not data["stack"]:
        return None
    if data.get("current"):
        data["forward_stack"].append(data["current"])
    prev = data["stack"].pop()
    data["current"] = prev
    _save(data)
    return prev


def go_forward() -> Optional[Dict[str, Any]]:
    """Step forward after a Back. Returns the entry to render, or None if there is none."""
    data = _load()
    if not data["forward_stack"]:
        return None
    if data.get("current"):
        data["stack"].append(data["current"])
    nxt = data["forward_stack"].pop()
    data["current"] = nxt
    _save(data)
    return nxt


def can_go_back() -> bool:
    return bool(_load()["stack"])


def can_go_forward() -> bool:
    return bool(_load()["forward_stack"])


def current() -> Optional[Dict[str, Any]]:
    return _load().get("current")


def reset() -> None:
    """Drop all history. Used by tests and by a deliberate return to the home card."""
    _save(dict(_EMPTY))


def breadcrumb(max_depth: int = 3) -> str:
    """`Home › Diagnose › Moat` — where you are, in words.

    Returns "" when there is no trail to show, so a caller can append it unconditionally
    without producing a stray separator on the home card. One entry is not a trail: it is
    just the name of the screen you are already looking at, which the header already says.
    """
    data = _load()
    trail: List[str] = []
    for entry in (data.get("stack") or [])[-max_depth:]:
        trail.append(short_label(entry.get("label") or entry.get("action") or "?"))
    cur = data.get("current") or {}
    if cur:
        trail.append(short_label(cur.get("label") or cur.get("action") or "?"))
    if len(trail) <= 1:
        return ""
    if len(trail) > max_depth + 1:
        trail = ["…"] + trail[-max_depth:]
    return " › ".join(trail)


def short_label(label: str) -> str:
    """Strip the leading glyph and clip, for the breadcrumb line.

    The previous implementation carried a hardcoded list of 28 emoji prefixes and stripped
    only those, so every glyph added later silently kept its emoji in the trail. Leading
    non-word characters are stripped generically instead — there is no fixed set to fall
    behind.
    """
    s = " ".join((label or "").split())
    i = 0
    while i < len(s) and not (s[i].isalnum() or s[i] in "/#"):
        i += 1
    s = s[i:].strip() or " ".join((label or "").split())
    return s if len(s) <= 20 else s[:17] + "…"
