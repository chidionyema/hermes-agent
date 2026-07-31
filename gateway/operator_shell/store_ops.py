"""Store money-rail control from the phone — a thin bridge onto `storeops`.

Deliberately thin. Every question this answers is already answered by
``prospector/store_platform/scripts/storeops``, which is the thing a human runs in a terminal
and the thing CI would run. Reimplementing any of that logic here would create a second
opinion about whether the store is sellable, and the phone's opinion would be the one nobody
tests. So this module only shells out and formats.

Two fences, both deliberate:

* **``deploy`` is not exposed.** ``storeops deploy`` already refuses under ``--brief``, but the
  verb is absent from the router as well so a fat-fingered message can never reach it. Shipping
  production is a terminal action.
* **Read verbs are safe to spam; ``status`` uses ``--quick``.** The full health probe mints a
  live Stripe checkout session. A status pull that anyone can repeat must not leave a trail of
  real payment objects behind it.

Only read verbs are routed. ``storeops`` also has ``pause``/``resume``, but those toggle
``store/scheduler/PAUSE`` — literally the same file the existing ``pause prospector`` verb
already owns. A second phrase for one switch means two descriptions of one truth, and the
phone's description is the one nobody tests. Say ``pause prospector``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

REPO = Path.home() / "Documents" / "code" / "prospector"
STOREOPS = REPO / "store_platform" / "scripts" / "storeops"

# Wall-clock ceilings. reconcile pages Stripe and then hits the store once per paid session,
# so it is the slow one; status runs reconcile plus a quick probe.
_TIMEOUT = {"status": 240, "health": 300, "reconcile": 240, "money": 900}

_BUTTONS: List[ButtonRow] = [
    [("🔄 Store status", "estate:st_status"), ("🩺 Health", "estate:st_health")],
    [("🧾 Reconcile", "estate:st_reconcile"), ("📋 Panel", "estate:refresh")],
]


def _run(verb: str, extra: Optional[List[str]] = None) -> Tuple[int, str]:
    """Run `storeops <verb> --brief`. Returns (exit code, output).

    A missing script or a timeout returns a non-zero code with the reason in the text — never
    an empty string, because a blank panel on the phone reads as "fine".
    """
    if not STOREOPS.exists():
        return 127, f"storeops not found at {STOREOPS}"
    # --brief is a GLOBAL flag on storeops, so it goes before the verb, not after it.
    cmd = [str(STOREOPS), "--brief", verb] + (extra or [])
    try:
        p = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True,
            timeout=_TIMEOUT.get(verb, 180),
        )
    except subprocess.TimeoutExpired:
        return 124, f"`storeops {verb}` timed out after {_TIMEOUT.get(verb, 180)}s"
    except OSError as exc:
        return 126, f"could not run storeops: {exc}"
    out = (p.stdout or "").strip() or (p.stderr or "").strip()
    return p.returncode, out or f"`storeops {verb}` exited {p.returncode} with no output"


def _icon(rc: int) -> str:
    """storeops uses the verify_store.sh convention: 0 good, 1 broken, 3 could-not-check.

    3 is NOT folded into either. "I could not check" wearing a green tick is the single
    failure mode this whole tool exists to prevent.
    """
    return {0: "🟢", 1: "🔴", 3: "🟡"}.get(rc, "⚠️")


def render(verb: str, extra: Optional[List[str]] = None) -> Tuple[str, List[ButtonRow]]:
    """Panel for a read verb (status / health / reconcile / money)."""
    rc, out = _run(verb, extra)
    title = {
        "status": "Store status",
        "health": "Store health — can a stranger pay us?",
        "reconcile": "Paid vs delivered",
        "money": "Money-path proof",
    }.get(verb, f"Store {verb}")
    text = f"{_icon(rc)} *{title}*\n\n```text\n{out[:1500]}\n```"
    if rc == 3:
        text += "\n_Unproven — a check could not run. Not the same as healthy._"
    return text, _BUTTONS


def buttons() -> List[ButtonRow]:
    return _BUTTONS
