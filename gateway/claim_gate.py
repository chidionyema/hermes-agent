"""Stamp a completion claim the verification ledger cannot back.

A reply whose status line opens ``DONE:`` is a claim that work is finished.
When the session's verification ledger shows edited files with no green
verification run after the last edit, the claim is unproven — so the status
word is rewritten to ``UNVERIFIED:`` and one footer line says why. The reply
is never blocked, shortened, or bounced back to the agent: the reader gets
the agent's own words plus an honest label (detection ranks below honesty,
but a false "DONE" outranks both).

Design constraints, in force:

* **Stamp, never block.** A gate that refuses correct work is an outage. The
  only mutation is the status word and one appended line.
* **Fail open.** Any error in here returns the text unchanged. A broken gate
  must degrade to no gate, not to a broken reply path.
* **Structured prefix only.** The trigger is the literal ``DONE:`` status
  line the agent's identity mandates — never prose word-matching, which
  measured 35% detection against 91% for a ledger join.
* **Doc-only work passes.** Sessions whose only edits are prose have nothing
  a test run could prove; they are never stamped.

Escape hatch (counted, per LAW 38): set ``HERMES_CLAIM_GATE_DISABLED=1`` to
turn the gate off; every stamp is logged so bypass and fire rates are
greppable from the gateway log.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# `[Architect]` / `[Architect][cron:foo]` labels precede the status word by
# mandate; the gate reads through any run of leading bracketed labels.
_LABEL_RUN = re.compile(r"^(?:\s*\[[^\]\n]{1,80}\])*\s*")

_FOOTER = (
    "⚠️ UNVERIFIED: files were edited this session and no "
    "verification run has passed since the last edit ({detail})."
)


def _split_label_prefix(text: str) -> tuple[str, str]:
    """Split leading bracketed labels (plus whitespace) from the rest."""
    m = _LABEL_RUN.match(text)
    if not m:
        return "", text
    return text[: m.end()], text[m.end() :]


def _verifiable(paths: list[str]) -> list[str]:
    try:
        from agent.verification_stop import _filter_verifiable_paths

        return _filter_verifiable_paths(paths)
    except Exception:
        return list(paths)


def stamp_unproven_done(text: str, *, session_id: str | None) -> str:
    """Rewrite ``DONE:`` to ``UNVERIFIED:`` when the ledger cannot back it.

    Returns ``text`` unchanged unless ALL of: the first line's status word
    (after any ``[label]`` run) is ``DONE:``; the session's ledger holds at
    least one root with verifiable changed paths whose latest verification
    event is missing, failed, or older than the last edit. ``WORKING:`` and
    ``BLOCKED:`` claim nothing finished and are never touched.
    """

    try:
        if not text or os.environ.get("HERMES_CLAIM_GATE_DISABLED"):
            return text
        labels, rest = _split_label_prefix(text)
        if not rest.startswith("DONE:"):
            return text

        from agent.verification_evidence import session_verification_gaps

        gaps = []
        for gap in session_verification_gaps(session_id):
            if _verifiable(gap.get("changed_paths") or []):
                gaps.append(gap)
        if not gaps:
            return text

        detail = "; ".join(
            "{}: {}".format(os.path.basename(g["root"].rstrip("/")) or g["root"], g["status"])
            for g in gaps[:3]
        )
        stamped = labels + "UNVERIFIED:" + rest[len("DONE:") :]
        stamped = stamped.rstrip() + "\n\n" + _FOOTER.format(detail=detail)
        logger.info(
            "claim_gate stamped DONE -> UNVERIFIED for session %s (%s)",
            session_id,
            detail,
        )
        return stamped
    except Exception:
        logger.debug("claim_gate failed open", exc_info=True)
        return text
