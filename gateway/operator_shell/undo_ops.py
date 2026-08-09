"""L3 — act-and-undo, generalised from two hardcoded families to a registry.

``push_undo``/``pop_undo`` (``operator_shell/proof.py:106,138``) have existed since the
cockpit shipped, and they work. What did not scale was the *reverse* half: applying a
recorded undo was an ``if/elif`` chain inside ``estate._dispatch`` that knew exactly two
action families — ``set_paused`` and ``cron_action``. Every other state-changing action
in the panel therefore recorded no undo at all, because there was nowhere for its reverse
to be applied. The undo button was real and its coverage was two verbs wide.

That matters most on a phone. P2 puts a per-role model switch one tap from the door, and
a fat-finger there is silent: the role keeps working, just on a different brain, and
nothing surfaces until a bill or a bad approval. So the fence ships WITH the surface that
needs it, not after (spec §2 P2, "ships with L3, not after it").

The generalisation is a keyed registry rather than a decorator. A decorator on the
dispatch function was the first design and it is the wrong shape here: ``_dispatch`` has
dozens of return paths and each action computes its own reverse payload from state it
reads *before* mutating, which a wrapper cannot see. A registry keyed on the reverse
payload's own field name puts the knowledge where the knowledge is — the action declares
what undoes it, this module knows how to apply it, and adding a third state-changing
family is a ``register_reverser`` call rather than another ``elif``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

# reverse-payload key -> callable(payload) -> bool applied
_REVERSERS: Dict[str, Callable[[Any], bool]] = {}


def register_reverser(key: str, fn: Callable[[Any], bool]) -> None:
    """Declare how to apply a reverse payload stored under ``key``.

    Called at import time by this module for the built-ins; available to any other
    module that starts recording undo records with a new payload shape.
    """
    _REVERSERS[key] = fn


def known_keys() -> Tuple[str, ...]:
    return tuple(sorted(_REVERSERS))


def apply_reverse(rev: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Apply every recognised reverse in ``rev``. Returns (ok, applied_keys).

    ``ok`` is False when the record carried nothing this process knows how to undo —
    which is the case worth surfacing, because it means the operator tapped Undo and
    got a panel refresh that looked like success. A reverser that raises is logged and
    counted as not-applied rather than propagated: undo is the recovery path, and a
    recovery path that can itself crash the panel is not one.
    """
    applied: List[str] = []
    for key, fn in _REVERSERS.items():
        if key not in rev:
            continue
        try:
            if fn(rev[key]):
                applied.append(key)
        except Exception as exc:
            logger.exception("undo reverser %s failed: %s", key, exc)
    return bool(applied), applied


# ---------------------------------------------------------------------------
# built-in reversers — behaviour-identical to the if/elif chain they replace
# ---------------------------------------------------------------------------

def _reverse_set_paused(payload: Any) -> bool:
    from gateway.operator_shell.estate import _load_coordinator

    _load_coordinator().set_estate_paused(bool(payload))
    return True


def _reverse_cron_flat(rev: Dict[str, Any]) -> bool:
    """``{"cron_action": "pause"|"resume", "job_id": ...}`` is stored FLAT.

    The old chain read ``cron_action`` and ``job_id`` as siblings of the reverse dict
    rather than as a nested payload, so this one takes the whole record and is applied
    by ``apply_reverse_record`` instead of going through the keyed registry. Kept flat
    deliberately: changing the stored shape would strand every undo record already
    sitting in ``~/.hermes/undo.jsonl``.
    """
    from gateway.operator_shell.cron_ops import format_cron_command

    verb = str(rev.get("cron_action") or "").strip()
    job_id = rev.get("job_id")
    if verb not in ("pause", "resume") or not job_id:
        return False
    format_cron_command(f"{verb} {job_id}")
    return True


def _reverse_aux_model(payload: Any) -> bool:
    from gateway.operator_shell.brains import restore_role_model

    if not isinstance(payload, dict):
        return False
    return restore_role_model(
        payload.get("role", ""),
        payload.get("provider", "auto"),
        payload.get("model", ""),
    )


def _reverse_aux_table(payload: Any) -> bool:
    from gateway.operator_shell.brains import restore_role_table

    if not isinstance(payload, dict):
        return False
    return restore_role_table(payload)


register_reverser("set_paused", _reverse_set_paused)
register_reverser("aux_model", _reverse_aux_model)
register_reverser("aux_table", _reverse_aux_table)


def apply_reverse_record(rev: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """``apply_reverse`` plus the flat cron shape, which is not a nested payload."""
    ok, applied = apply_reverse(rev)
    if "cron_action" in rev:
        try:
            if _reverse_cron_flat(rev):
                applied.append("cron_action")
                ok = True
        except Exception as exc:
            logger.exception("undo reverser cron_action failed: %s", exc)
    return ok, applied
