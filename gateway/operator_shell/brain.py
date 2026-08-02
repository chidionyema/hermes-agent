"""Change the model the agent thinks with, from a button.

`/model opus` already worked from Telegram (`slash_commands.py:1291`) — but a slash command
you have to remember the spelling of is not a UI. This is the same switch as a panel: what
is running now, what else is available, what it costs, one tap.

VERIFIED 2026-07-31 by calling the real resolver (no persist):

    opus   -> claude-opus-5              provider=anthropic  mode=anthropic_messages  key=True
    sonnet -> claude-sonnet-5            provider=anthropic  mode=anthropic_messages  key=True
    haiku  -> claude-haiku-4-5-20251001  provider=anthropic  mode=anthropic_messages  key=True

Each choice pins its provider explicitly. Without that, `opus` resolves through OpenRouter
(`anthropic/claude-opus-5`, chat_completions) because an authenticated aggregator wins the
search — same weights, but billed through a middleman and on a different transport. The
direct route uses ANTHROPIC_API_KEY, which is present.

The switch is written to config.yaml (`model.default` / `model.provider` / `model.base_url`),
exactly as `/model --global` writes it. The running agent keeps its old model until the
current turn finishes: `run.py:16091` compares the live agent's model to the config model
after each successful run and evicts the cache on drift. So the honest promise is "your next
message", not "immediately" — and the panel says so rather than pretending.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]


class Choice:
    __slots__ = ("key", "label", "alias", "provider", "cost", "why")

    def __init__(self, key: str, label: str, alias: str, provider: str, cost: str, why: str):
        self.key = key
        self.label = label
        self.alias = alias
        self.provider = provider
        self.cost = cost
        self.why = why


# Keep this list short and honest. Every entry is a model this estate can actually reach —
# a picker that offers a model with no credential is a button that fails on tap.
_CHOICES: List[Choice] = [
    Choice("opus", "🧠 Opus 5", "opus", "anthropic", "$5/$25 per MTok",
           "deepest reasoning — money, identity, migrations"),
    Choice("sonnet", "🎯 Sonnet 5", "sonnet", "anthropic", "$3/$15 per MTok",
           "the workhorse — most engineering"),
    Choice("haiku", "⚡ Haiku 4.5", "haiku", "anthropic", "$1/$5 per MTok",
           "recon, triage, cheap sweeps"),
    Choice("deepseek", "🐋 DeepSeek v4 Pro", "deepseek-v4-pro", "deepseek", "~$0.3/$1.2 per MTok",
           "the standing default — cheapest capable"),
    Choice("minimax", "🔷 MiniMax M3", "MiniMax-M3", "minimax", "cheap",
           "the configured fallback"),
]

_BY_KEY: Dict[str, Choice] = {c.key: c for c in _CHOICES}


def choices() -> List[Choice]:
    return list(_CHOICES)


def _cfg() -> dict:
    from hermes_cli.config import load_config

    return load_config() or {}


def current() -> Tuple[str, str]:
    """(model, provider) as config.yaml has it — the value the gateway resolves from."""
    cfg = _cfg()
    raw = cfg.get("model")
    if isinstance(raw, dict):
        return str(raw.get("default") or "?"), str(raw.get("provider") or "?")
    if isinstance(raw, str) and raw.strip():
        return raw.strip(), str((cfg.get("model_provider") or "?"))
    return "?", "?"


def _matches(choice: Choice, model: str, provider: str) -> bool:
    """Is this choice the one currently running?

    Compares on the resolved model id, not the alias — config stores `claude-opus-5`, the
    choice is keyed `opus`, and a naive equality would mark nothing as current.
    """
    m = (model or "").lower()
    if choice.provider != (provider or "").lower():
        return False
    stem = choice.alias.lower()
    return m == stem or m.startswith(stem) or stem in m


def render_brain() -> Tuple[str, List[ButtonRow]]:
    model, provider = current()
    lines = [
        "🧠 *Brain* — the model the agent thinks with",
        "",
        f"Now: `{model}`  ·  via *{provider}*",
        "",
    ]
    buttons: List[ButtonRow] = []
    row: ButtonRow = []
    for c in _CHOICES:
        live = _matches(c, model, provider)
        lines.append(f"{'▸ ' if live else '  '}{c.label} — {c.why} · {c.cost}")
        row.append((("● " if live else "") + c.label, f"estate:brain_set:{c.key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    lines += [
        "",
        "_Takes effect on your next message — the running turn keeps its model._",
        "_A dearer brain spends the daily LLM cap faster; that ceiling is under 💵 Spend._",
    ]
    # append(nav(...)), not `+= nav(...)`: nav returns a ROW (a list of buttons), so `+=`
    # splices each button in as its own row and every one of them then fails to unpack.
    # No "Tune" button here: the nav spine already carries estate:tune, and offering it
    # twice on one screen is the duplicate-callback defect the cockpit was fixed for.
    buttons.append([("💵 Spend cap", "estate:tune:spend")])
    buttons = with_nav(buttons, "brain")
    lines.append("")
    lines.append(panel_stamp("brain"))
    return "\n".join(lines), buttons


def set_model(key: str) -> Tuple[bool, str]:
    """Switch the configured brain. Returns (ok, detail) — detail is the receipt evidence."""
    choice = _BY_KEY.get((key or "").strip().lower())
    if choice is None:
        # Never hand a callback argument to the resolver directly: the picker is a closed
        # set, and an unknown key is a bug or a malformed callback, not a model name.
        return False, f"unknown brain `{key}` — pick one from the panel"

    cfg = _cfg()
    model, provider = current()
    try:
        from hermes_cli.model_switch import switch_model

        result = switch_model(
            raw_input=choice.alias,
            current_provider=provider if provider != "?" else "",
            current_model=model if model != "?" else "",
            is_global=False,  # we persist below, with the same coercion /model --global uses
            explicit_provider=choice.provider,
            user_providers=cfg.get("providers") or {},
            custom_providers=cfg.get("custom_providers") or [],
        )
    except Exception as exc:
        logger.warning("brain: switch_model raised: %s", exc)
        return False, f"resolver failed: {exc}"[:200]

    if not getattr(result, "success", False):
        return False, (getattr(result, "error_message", "") or "switch failed")[:200]

    try:
        from hermes_cli.config import save_config

        raw = cfg.get("model")
        # Same coercion as slash_commands.py:1379 — a flat `model: <name>` string in
        # config.yaml would otherwise raise TypeError on item assignment.
        if isinstance(raw, dict):
            model_cfg = raw
        else:
            model_cfg = {"default": raw.strip()} if isinstance(raw, str) and raw.strip() else {}
            cfg["model"] = model_cfg
        model_cfg["default"] = result.new_model
        model_cfg["provider"] = result.target_provider
        if getattr(result, "base_url", ""):
            model_cfg["base_url"] = result.base_url
        save_config(cfg)
    except Exception as exc:
        logger.warning("brain: could not persist model switch: %s", exc)
        return False, f"resolved `{result.new_model}` but could not write config.yaml: {exc}"[:200]

    return True, (
        f"{model} → {result.new_model} via {result.target_provider} "
        f"({getattr(result, 'api_mode', '?')}) · live on your next message"
    )
