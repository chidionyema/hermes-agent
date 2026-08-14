"""Operator Telegram Bot menu profile (≤30 commands)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

# Tier-0 operator shell — must fit Telegram's practical menu and stay typed-first.
# Keep in sync with ~/.hermes/scripts/set-cockpit-menu.py (chat-scoped wins).
#
# P0 of the Operator UX programme (OPERATOR_UX_SPEC.md, §1): both `agent_model` and `model`
# are Tier-0 operator intent (one global brain switch, one session-scoped switch) and were
# previously invisible on the Telegram menu because the operator profile hard-capped this
# list at 12 names. MAX_COMMANDS_PER_SCOPE in gateway/platforms/telegram.py is 30, so the
# scarcity that justified hiding them does not exist. Both render current state before
# offering a change (state-before-verb, principle 3 of the spec):
#   - `agent_model` → gateway.operator_shell.estate:handle_estate_action("agent_model")
#     → render_agent_model_panel, which prints the resolved current model + provider in a
#     "NOW" chip grid (text_mode_cards.py:198-205) before any change affordance.
#     CORRECTION 2026-08-08: an earlier draft of this comment claimed the panel also prints
#     a "role table". It does not. `switches` is four hardcoded behaviour toggles —
#     agent_model, personality, reasoning, busy (estate.py:619-624). The 13 per-role models
#     in config.yaml:119-212 have NO Telegram renderer; that is P2, still unbuilt. The claim
#     is left here as a correction rather than deleted because a comment asserting an
#     unbuilt capability is the exact defect class the programme exists to kill.
#   - `model`       → gateway/slash_commands._handle_model_command, which prints
#     current_label (model + provider) at the top of the picker and the text fallback.
# Both stay typed-dispatchable when removed from the menu; the spec for P1 is the
# persistent reply keyboard, which renders the operator shell rather than this list.
OPERATOR_TELEGRAM_MENU: Tuple[str, ...] = (
    "panel",
    # See ~/.hermes/scripts/set-cockpit-menu.py: dashboard took sethome's slot,
    # projects took fleet's. Both displaced commands still work when typed.
    "projects",
    "dashboard",
    "status",
    "inbox",
    "brief",
    "cron",
    "busy",
    "notify",
    "revert",
    "missions",
    # `agent_model` and `model` are the two brain-switch doors. `agent_model` is the
    # global/role switch (operator_shell/estate.py), `model` is the session-scoped switch
    # (slash_commands._handle_model_command). Both were registered but never advertised;
    # both jump straight to a state-before-verb surface that names the current model.
    "agent_model",
    "model",
    # `code` opens a coding-agent session on a repo and turns this chat into its
    # terminal (gateway/operator_shell/coding_session.py). Advertised rather than
    # typed-only because the whole point is that launching one takes no recall.
    "code",
    "help",
)


def resolve_telegram_menu_profile(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Return ``operator`` | ``default`` from config."""
    if cfg is None:
        try:
            from gateway.run import _load_gateway_config

            cfg = _load_gateway_config() or {}
        except Exception:
            cfg = {}
    block = cfg.get("operator_shell") if isinstance(cfg.get("operator_shell"), dict) else {}
    profile = str(block.get("menu_profile") or "").strip().lower()
    if profile in {"operator", "default", "full"}:
        return "operator" if profile == "operator" else profile
    # Also allow telegram.menu_profile
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    profile = str(tg.get("menu_profile") or "").strip().lower()
    if profile in {"operator", "default", "full"}:
        return profile
    return "default"


def filter_operator_menu(
    commands: Sequence[Tuple[str, str]],
) -> list[Tuple[str, str]]:
    """Keep only Tier-0 commands, in OPERATOR_TELEGRAM_MENU order."""
    by_name = {name: desc for name, desc in commands}
    out: list[Tuple[str, str]] = []
    for name in OPERATOR_TELEGRAM_MENU:
        if name in by_name:
            out.append((name, by_name[name]))
    return out
