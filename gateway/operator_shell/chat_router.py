"""Single ordered Telegram CEO chat pipeline.

ONE owner for CEO verbs on Telegram: ``pre_gateway_dispatch`` (otto-inbound),
which calls :func:`route_telegram_ceo`. Gateway ``_handle_message_with_agent``
natural_ops is a **fallback only** when the plugin returns ``allow`` (e.g.
plugin unload / non-Telegram). Same verbs must never double-send.

Ordered pipeline
----------------
1. **natural_ops** → ``handle_estate_action`` (panels, daemons, pause/resume, RSI…)
2. **code_remote** → assign / steer / task card
3. **noise / slash surfaces** → mission / inbox / fleet / brief / rsi / daemons
   (only if natural_ops missed — keep for slash forms natural_ops ignores)
4. **allow** → agent (substantive free chat)

Free-chat contract (CEO mode, enforced by otto-inbound after this router):
- noise (ok/hi/…) → mission card (via natural_ops ``refresh`` or surface)
- substantive DM → ``allow`` → agent

Do not invent TELEGRAM_CRON_THREAD_ID here; cron topic setup is Telegram adapter only.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

_NOISE = re.compile(
    r"^(ok|okay|k|kk|hi|hey|hello|yo|sup|thanks|thank you|thx|ty|👍|👌|\.|…+|hmm+|yep|yeah|cool|nice)$",
    re.IGNORECASE,
)


@dataclass
class RouteResult:
    """Handled CEO route — caller sends panel and returns skip."""

    kind: str  # natural_op | code | surface
    reason: str
    text: str
    buttons: Optional[List[ButtonRow]] = None
    paused: Optional[bool] = None


def _ensure_agent_path() -> None:
    agent = os.path.expanduser("~/.hermes/hermes-agent")
    if agent not in sys.path:
        sys.path.insert(0, agent)


def route_telegram_ceo(text: str, who: str = "?") -> Optional[RouteResult]:
    """Run the single CEO pipeline. None ⇒ caller may continue (ground-truth / agent)."""
    raw = (text or "").strip()
    if not raw:
        return None

    _ensure_agent_path()

    # (1) Structured natural ops — same matcher gateway fallback uses
    try:
        from gateway.operator_shell.natural_ops import match_natural_op
        from gateway.operator_shell.estate import handle_estate_action

        nop = match_natural_op(raw)
        if nop is not None:
            action = nop.action if not nop.args else f"{nop.action}:{nop.args}"
            view = handle_estate_action(action)
            return RouteResult(
                kind="natural_op",
                reason=f"natural_op:{action}",
                text=view.text or "",
                buttons=view.buttons,
                paused=getattr(view, "paused", None),
            )
    except Exception as exc:
        logger.warning("chat_router: natural_op failed: %s", exc)

    # (2) Claude Code remote
    try:
        from gateway.operator_shell import code_remote as CR

        steer = CR.parse_steer(raw)
        if steer is not None:
            ref, instruction = steer
            msg, buttons = CR.steer_task(ref, instruction)
            return RouteResult(
                kind="code",
                reason=f"code_steer:{ref}",
                text=msg,
                buttons=buttons,
            )

        tq = CR.is_task_query(raw)
        if tq:
            msg, buttons = CR.render_task_card(tq)
            return RouteResult(
                kind="code",
                reason=f"code_task:{tq}",
                text=msg,
                buttons=buttons,
            )

        body = CR.is_code_command(raw) or CR.is_natural_code_assign(raw)
        if body:
            # The scope that tapping ⌨️ Assign on a project left behind, if still fresh.
            # None (never tapped, or expired) is the pre-2026-08-06 unscoped run, unchanged.
            ack, tid, buttons = CR.start_code_run(
                body, created_by=f"telegram:{who}", project_key=CR.get_assign_scope()
            )
            return RouteResult(
                kind="code",
                reason=f"code_run:{tid[:8] if tid else '?'}",
                text=ack,
                buttons=buttons,
            )
    except Exception as exc:
        logger.warning("chat_router: code_remote failed: %s", exc)

    # (3) Noise → mission (belt if natural_ops missed ok/hi)
    q = raw
    if re.match(r"^\s*otto[,:]?\s+", q, re.I):
        q = re.sub(r"^\s*otto[,:]?\s+", "", q, flags=re.I).strip()
    if _NOISE.match(q) or (len(q) <= 2 and q.isalpha()):
        try:
            from gateway.operator_shell.estate import handle_estate_action

            view = handle_estate_action("refresh")
            return RouteResult(
                kind="surface",
                reason="operator_shell:mission:noise",
                text=view.text or "",
                buttons=view.buttons,
                paused=getattr(view, "paused", None),
            )
        except Exception as exc:
            logger.warning("chat_router: noise→mission failed: %s", exc)

    return None


def telegram_plugin_owns_ceo() -> bool:
    """True when otto-inbound is expected to own Telegram CEO verbs."""
    plugin = os.path.expanduser("~/.hermes/plugins/otto-inbound/__init__.py")
    return os.path.isfile(plugin)
