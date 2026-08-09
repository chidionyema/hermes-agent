"""🤖 BRAINS — every scope that has a brain, and what it is right now.

The founder's question on 2026-08-08 was *"there is a telegram ui feature to see and
change the underlying model of the hermes agent/coordinator etc roles, i cant see it
— does it exist?"*. Measured answer: the **agent** scope had a picker
(``brain.py``), the **session** scope had one (``slash_commands._handle_model_command``),
and the 13 **role** scopes had none on any surface the founder uses. They were
configurable in ``config.yaml`` and dispatched against in code, with no renderer —
spec Principle 4, "a config table with N entries and no renderer is a hidden control
panel".

This module is the third renderer, and it deliberately borrows rather than invents:

* the role list is ``hermes_cli.config.AUXILIARY_TASK_KEYS`` — the same tuple the web
  Models page and the ``hermes model`` CLI picker now derive from, so the three
  surfaces cannot disagree about which roles exist (they disagreed 13/12/11 until
  2026-08-08; see tests/hermes_cli/test_auxiliary_role_coverage.py).
* the model shortlist is ``brain._CHOICES`` — the same curated list the agent picker
  offers, so a role can never be pointed at a model the agent picker calls unreachable.
* the write goes through ``hermes_cli.config.save_config`` — the same atomic writer
  ``/api/model/set`` uses, so the phone and the dashboard cannot produce different
  file states.

WHAT THIS ADDS THAT DID NOT EXIST (spec §3 "writer requirements"), measured 2026-08-08:

* **Timestamped backup.** ``save_config`` is atomic (``utils.atomic_yaml_write``,
  tempfile+fsync+os.replace) but keeps no history. The only timestamped backup in the
  codebase was ``_backup_corrupt_config`` (config.py:42), which fires on a parse
  failure at LOAD, never on a write. A fat-finger from a phone was unrecoverable.
* **An append-only audit row.** There was no audit log for config mutations at all.
  ``operator_shell.proof._proof()`` renders a receipt for display and persists nothing.
* **A fence on sensitive roles.** No allowlist existed for auxiliary roles anywhere
  (``tools/mcp_tool.py:801`` has one, but it is per-MCP-server, not per-role).

HONEST LIMITS, stated because the panel states them to the operator:

* ``provider: auto`` + ``model: ''`` means the role inherits. What it inherits is
  resolved at dispatch by ``agent/auxiliary_client._resolve_auto`` (:3222), whose step 1
  is the main provider+model but which then walks fallback chains when the main is
  unhealthy. This panel renders the INHERIT TARGET, labelled as such. It does not claim
  to predict a failover, and it must not: rendering a resolved model as though it were
  guaranteed is spec risk R5.
* A change lands on the role's NEXT dispatch, never an in-flight one. ``load_config``
  caches on the config file's (mtime_ns, size) and ``save_config`` writes a fresh inode,
  so the next load repopulates — but an already-constructed client keeps its provider.
  The panel says "next dispatch" for exactly that reason (spec risk R1).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

# How many roles the panel shows before collapsing behind an explicit expander.
# An expander, never silent truncation (spec §3): a list that quietly stops is
# indistinguishable from a list that ended.
_PREVIEW_ROLES = 5

# Roles whose brain is a safety decision, not a cost one. A change here takes a
# second, explicit tap. `approval` decides whether a command is allowed to run, so
# retargeting it to a weak model from a phone in one tap is the fat-finger with the
# largest blast radius on this panel.
#
# This is a CONFIRM fence, not an allowlist. Spec risk R2 asks for an allowlist of
# permitted models per sensitive role, enforced in the writer — the mechanism is
# implemented below (`_allowlist_for`), but it ships UNPOPULATED by default, because
# which models are strong enough to arbitrate approvals is a founder policy call and
# inventing a list here would be exactly the assert-without-proof this programme exists
# to stop. Populate `operator_shell.role_model_allowlist` in config.yaml to arm it;
# until then the confirm step is the live half of R2 and the panel says so.
SENSITIVE_ROLES: Tuple[str, ...] = ("approval",)

_INHERIT_KEY = "auto"


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _meta_dir() -> Path:
    d = _hermes_home() / "meta"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path() -> Path:
    return _meta_dir() / "config_audit.jsonl"


def backup_dir() -> Path:
    d = _meta_dir() / "config_backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# reading state
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    from hermes_cli.config import load_config

    return load_config() or {}


def role_keys() -> Tuple[str, ...]:
    from hermes_cli.config import AUXILIARY_TASK_KEYS

    return AUXILIARY_TASK_KEYS


def role_state(cfg: Optional[dict] = None) -> List[Dict[str, Any]]:
    """Per-role brain state, in config order.

    ``overridden`` is the operator-visible distinction: a role either inherits the
    agent's brain (provider ``auto``, empty model) or has been pointed somewhere
    specific. Counting them is what makes drift visible from the header without
    reading 13 rows.
    """
    cfg = cfg if cfg is not None else _cfg()
    aux = cfg.get("auxiliary")
    aux = aux if isinstance(aux, dict) else {}
    out: List[Dict[str, Any]] = []
    for role in role_keys():
        block = aux.get(role)
        block = block if isinstance(block, dict) else {}
        provider = str(block.get("provider") or "auto").strip()
        model = str(block.get("model") or "").strip()
        overridden = bool(model) or (provider.lower() not in ("", "auto"))
        out.append(
            {
                "role": role,
                "provider": provider,
                "model": model,
                "overridden": overridden,
            }
        )
    return out


def _inherit_target(cfg: Optional[dict] = None) -> str:
    """What an `auto` role inherits *as its first choice*.

    Deliberately not called `resolved`: `_resolve_auto` falls through several chains
    when the main provider is unhealthy, so the only honest claim from a render path
    (which must not instantiate clients) is "this is what it inherits", not "this is
    what will answer".
    """
    try:
        from gateway.operator_shell.brain import current

        model, _provider = current()
        return model or "?"
    except Exception:
        cfg = cfg if cfg is not None else _cfg()
        raw = cfg.get("model")
        if isinstance(raw, dict):
            return str(raw.get("default") or "?")
        return str(raw or "?")


def _render_role_value(entry: Dict[str, Any], inherit: str) -> str:
    if not entry["overridden"]:
        return f"auto → {inherit}"
    model = entry["model"] or "(provider default)"
    return f"{model} · {entry['provider']}"


# ---------------------------------------------------------------------------
# the fence
# ---------------------------------------------------------------------------

def _allowlist_for(role: str, cfg: Optional[dict] = None) -> Optional[List[str]]:
    """Permitted model keys for ``role``, or None when unrestricted.

    Config-declared (directive: params live in config), read fresh on every write so
    arming the fence does not need a restart::

        operator_shell:
          role_model_allowlist:
            approval: [opus, sonnet]

    Delegates to ``hermes_cli.config.role_model_allowlist`` — one reader, because the
    policy is enforced twice and the second enforcement point lives outside this package
    (``tools/approval.py`` refuses an ANSWER from an unlisted brain, which this function's
    caller cannot see; ``call_llm`` substitutes providers silently when one is out of
    credits). Two copies of a fence's definition is one copy that goes stale.
    """
    from hermes_cli.config import role_model_allowlist

    return role_model_allowlist(role, cfg if cfg is not None else _cfg())


def fence_check(role: str, key: str, cfg: Optional[dict] = None) -> Tuple[bool, str]:
    """Is this role allowed to be pointed at this model key?

    Enforced in the WRITER, never only in the keyboard: a keyboard that omits an
    option is not a fence, because the callback can still be replayed (spec R2).
    """
    allowed = _allowlist_for(role, cfg)
    if allowed is None:
        return True, ""
    k = (key or "").strip().lower()
    if k == _INHERIT_KEY:
        # Reverting to inherit is always permitted: it can only move the role back
        # onto the agent brain, which is itself fenced by whoever set it.
        return True, ""
    if k not in allowed:
        return False, (
            f"`{role}` is fenced by `operator_shell.role_model_allowlist` to "
            f"{', '.join(allowed)} — `{k}` refused"
        )
    return True, ""


# ---------------------------------------------------------------------------
# the writer
# ---------------------------------------------------------------------------

def _backup_config(reason: str) -> Optional[str]:
    """Timestamped copy of config.yaml before we touch it. Returns the path.

    Best-effort by design: a backup failure must not block the write (the write is
    itself atomic), but it IS recorded in the audit row so a restore attempt never
    silently assumes a backup that was never taken.
    """
    try:
        from hermes_cli.config import get_config_path

        src = Path(get_config_path())
        if not src.is_file():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        dst = backup_dir() / f"config.yaml.{stamp}.{reason}.bak"
        shutil.copy2(src, dst)
        _prune_backups()
        return str(dst)
    except Exception as exc:
        logger.warning("config backup failed: %s", exc)
        return None


def _prune_backups(keep: int = 50) -> None:
    """Keep the most recent ``keep`` backups. Unbounded history is its own bug."""
    try:
        files = sorted(
            backup_dir().glob("config.yaml.*.bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[keep:]:
            stale.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("backup prune skipped: %s", exc)


def audit(event: str, **fields: Any) -> None:
    """Append-only audit row: who, what, old→new, when.

    Never raises. An audit facility that can take down the action it audits is worse
    than none, because it converts an observability gap into an outage.
    """
    try:
        row = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event}
        row.update(fields)
        with open(audit_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        logger.warning("config audit row dropped: %s", exc)


def resolve_choice(choice: Any, cfg: dict) -> Tuple[bool, str, str, str]:
    """Alias → (ok, resolved_model, resolved_provider, error).

    Routed through the SAME resolver the agent picker uses
    (``brain.set_model`` → ``hermes_cli.model_switch.switch_model``), for two reasons
    that are not stylistic:

    * ``Choice.alias`` is a short name (``haiku``), not a model id. Writing the alias
      straight into ``auxiliary.<role>.model`` would put a string in config that the
      aux client has to re-resolve, and the panel would then render an alias while the
      agent scope renders a resolved id — the two surfaces disagreeing about the same
      model, which is the defect this whole module exists to remove.
    * The resolver is also the credential check. ``switch_model`` fails for a model this
      estate cannot actually reach, so an unreachable choice is refused at write time
      rather than becoming a role that fails on its next dispatch.

    ``is_global=False`` matches ``brain.set_model``: the call resolves, it does not
    persist. This function's caller does the writing.
    """
    from gateway.operator_shell.brain import current
    from hermes_cli.model_switch import switch_model

    cur_model, cur_provider = current()
    try:
        result = switch_model(
            raw_input=choice.alias,
            current_provider=cur_provider if cur_provider != "?" else "",
            current_model=cur_model if cur_model != "?" else "",
            is_global=False,
            explicit_provider=choice.provider,
            user_providers=cfg.get("providers") or {},
            custom_providers=cfg.get("custom_providers") or [],
        )
    except Exception as exc:
        logger.warning("brains: switch_model raised: %s", exc)
        return False, "", "", f"resolver failed: {exc}"[:200]
    if not getattr(result, "success", False):
        return False, "", "", (getattr(result, "error_message", "") or "switch failed")[:200]
    return True, str(result.new_model), str(result.target_provider), ""


def set_role_model(
    role: str,
    key: str,
    *,
    actor: str = "operator",
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Point one role's brain at ``key`` (a ``brain._CHOICES`` key, or ``auto``).

    Returns ``(ok, detail, reverse)`` where ``reverse`` is the payload that restores
    the previous value — handed to ``push_undo`` by the caller so L3's undo covers
    this action like any other state change.
    """
    from gateway.operator_shell.brain import _BY_KEY

    role = (role or "").strip()
    key = (key or "").strip().lower()

    if role not in role_keys():
        return False, f"unknown role `{role}`", None

    cfg = _cfg()
    ok, why = fence_check(role, key, cfg)
    if not ok:
        audit("role_model_refused", role=role, requested=key, actor=actor, reason=why)
        return False, why, None

    if key == _INHERIT_KEY:
        provider, model = "auto", ""
        label = "inherit (auto)"
    else:
        choice = _BY_KEY.get(key)
        if choice is None:
            return False, f"unknown model `{key}`", None
        resolved = resolve_choice(choice, cfg)
        if not resolved[0]:
            return False, resolved[3], None
        _ok, model, provider, _ = resolved
        label = choice.label

    aux = cfg.get("auxiliary")
    aux = aux if isinstance(aux, dict) else {}
    block = aux.get(role)
    block = dict(block) if isinstance(block, dict) else {}

    old_provider = str(block.get("provider") or "auto")
    old_model = str(block.get("model") or "")
    if old_provider == provider and old_model == model:
        return True, f"`{role}` already on {label} — no write", None

    backup = _backup_config("role")

    # Only the two routing fields move. timeout/extra_body/download_timeout are the
    # role's own tuning and are none of this panel's business — a picker that
    # rewrites a whole config block silently reverts hand-tuning.
    block["provider"] = provider
    block["model"] = model
    aux[role] = block
    cfg["auxiliary"] = aux

    from hermes_cli.config import save_config

    save_config(cfg)

    audit(
        "role_model_set",
        role=role,
        actor=actor,
        old={"provider": old_provider, "model": old_model},
        new={"provider": provider, "model": model},
        backup=backup,
    )

    reverse = {"aux_model": {"role": role, "provider": old_provider, "model": old_model}}
    detail = f"{role}: {old_model or 'auto'} → {model or 'auto'} (backup: {backup or 'none'})"
    return True, detail, reverse


def restore_role_model(role: str, provider: str, model: str, *, actor: str = "undo") -> bool:
    """Put a role back exactly as it was. The undo path for ``set_role_model``."""
    if role not in role_keys():
        return False
    cfg = _cfg()
    aux = cfg.get("auxiliary")
    aux = aux if isinstance(aux, dict) else {}
    block = aux.get(role)
    block = dict(block) if isinstance(block, dict) else {}
    block["provider"] = provider or "auto"
    block["model"] = model or ""
    aux[role] = block
    cfg["auxiliary"] = aux

    backup = _backup_config("undo")
    from hermes_cli.config import save_config

    save_config(cfg)
    audit(
        "role_model_restored",
        role=role,
        actor=actor,
        new={"provider": provider or "auto", "model": model or ""},
        backup=backup,
    )
    return True


def reset_all_roles(*, actor: str = "operator") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Every role back to inherit. Reversible: the prior table is the undo payload."""
    cfg = _cfg()
    before = {e["role"]: {"provider": e["provider"], "model": e["model"]} for e in role_state(cfg)}
    overridden = [r for r, v in before.items() if v["model"] or v["provider"].lower() != "auto"]
    if not overridden:
        return True, "nothing to reset — every role already inherits", None

    backup = _backup_config("reset")
    aux = cfg.get("auxiliary")
    aux = aux if isinstance(aux, dict) else {}
    for role in role_keys():
        block = aux.get(role)
        block = dict(block) if isinstance(block, dict) else {}
        block["provider"] = "auto"
        block["model"] = ""
        aux[role] = block
    cfg["auxiliary"] = aux

    from hermes_cli.config import save_config

    save_config(cfg)
    audit("role_model_reset_all", actor=actor, cleared=overridden, backup=backup)
    return (
        True,
        f"reset {len(overridden)} role(s): {', '.join(overridden)}",
        {"aux_table": before},
    )


def restore_role_table(table: Dict[str, Dict[str, str]], *, actor: str = "undo") -> bool:
    """Undo for ``reset_all_roles``."""
    cfg = _cfg()
    aux = cfg.get("auxiliary")
    aux = aux if isinstance(aux, dict) else {}
    for role, vals in (table or {}).items():
        if role not in role_keys():
            continue
        block = aux.get(role)
        block = dict(block) if isinstance(block, dict) else {}
        block["provider"] = vals.get("provider") or "auto"
        block["model"] = vals.get("model") or ""
        aux[role] = block
    cfg["auxiliary"] = aux
    _backup_config("undo")
    from hermes_cli.config import save_config

    save_config(cfg)
    audit("role_model_table_restored", actor=actor, roles=sorted(table or {}))
    return True


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------

def render_brains_panel(show_all: bool = False) -> Tuple[str, List[ButtonRow]]:
    """State before verb (Principle 3): what every brain IS, then how to change it."""
    from gateway.operator_shell.panel_chrome import panel_stamp, with_nav

    cfg = _cfg()
    inherit = _inherit_target(cfg)
    entries = role_state(cfg)
    overridden = [e for e in entries if e["overridden"]]
    total = len(entries)

    lines = [
        "🤖 *BRAINS* — who is answering",
        "",
        f"*Agent* · `{inherit}`  — the default for everything",
        "*Session* · per-chat — `/model` (this chat only)",
        "",
        f"*ROLES* ({total} · {len(overridden)} overridden, {total - len(overridden)} inheriting)",
    ]

    shown = entries if show_all else entries[:_PREVIEW_ROLES]
    width = max((len(e["role"]) for e in shown), default=0)
    for e in shown:
        mark = "▸" if e["overridden"] else " "
        pad = "." * max(1, (width - len(e["role"])) + 2)
        lines.append(f"{mark} `{e['role']}` {pad} {_render_role_value(e, inherit)}")

    buttons: List[ButtonRow] = []
    row: ButtonRow = []
    for e in shown:
        row.append((("● " if e["overridden"] else "") + e["role"], f"estate:brains_role:{e['role']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if not show_all and total > len(shown):
        lines.append(f"  … {total - len(shown)} more")
        buttons.append([(f"▾ Show all {total}", "estate:brains:all")])

    lines += [
        "",
        "_`auto → x` means the role inherits the agent brain. A change lands on that "
        "role's NEXT dispatch, not an in-flight one._",
    ]
    if _allowlist_for("approval", cfg) is None:
        lines.append(
            "_Fence: `approval` asks for confirmation. No model allowlist is armed — "
            "set `operator_shell.role_model_allowlist` to restrict it._"
        )

    buttons.append([("🧠 Change agent brain", "estate:brain")])
    if overridden:
        buttons.append([("↺ Reset all overrides", "estate:brains_reset")])
    buttons = with_nav(buttons, "brains")
    lines.append("")
    lines.append(panel_stamp("brains"))
    return "\n".join(lines), buttons


def render_role_picker(role: str) -> Tuple[str, List[ButtonRow]]:
    """One role: what it is now, and every brain it may be pointed at."""
    from gateway.operator_shell.brain import choices
    from gateway.operator_shell.panel_chrome import panel_stamp, with_nav

    role = (role or "").strip()
    if role not in role_keys():
        return f"⚠️ Unknown role `{role}`.", with_nav([[("🤖 Brains", "estate:brains")]], "brains")

    cfg = _cfg()
    inherit = _inherit_target(cfg)
    entry = next(e for e in role_state(cfg) if e["role"] == role)
    allowed = _allowlist_for(role, cfg)
    sensitive = role in SENSITIVE_ROLES

    lines = [
        f"🤖 *{role}*",
        "",
        f"Now: {_render_role_value(entry, inherit)}",
        "",
    ]
    if sensitive:
        lines.append("⚠️ _Safety-bearing role — a change asks for confirmation._")
    if allowed:
        lines.append(f"🔒 _Fenced to: {', '.join(allowed)}_")
    lines.append("")

    buttons: List[ButtonRow] = []
    row: ButtonRow = []
    verb = "brains_confirm" if sensitive else "brains_set"
    for c in choices():
        if allowed is not None and c.key not in allowed:
            continue
        live = entry["overridden"] and c.alias.lower() in (entry["model"] or "").lower()
        lines.append(f"{'▸ ' if live else '  '}{c.label} — {c.why} · {c.cost}")
        row.append((("● " if live else "") + c.label, f"estate:{verb}:{role}|{c.key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if entry["overridden"]:
        buttons.append([(f"↺ Inherit agent brain ({inherit})", f"estate:{verb}:{role}|auto")])

    lines += ["", "_Takes effect on this role's next dispatch._"]
    buttons.append([("🤖 All brains", "estate:brains:all")])
    buttons = with_nav(buttons, "brains")
    lines.append("")
    lines.append(panel_stamp("brains"))
    return "\n".join(lines), buttons


def render_confirm(role: str, key: str) -> Tuple[str, List[ButtonRow]]:
    """The second tap for a safety-bearing role."""
    from gateway.operator_shell.brain import _BY_KEY
    from gateway.operator_shell.panel_chrome import panel_stamp, with_nav

    label = "inherit (auto)" if key == _INHERIT_KEY else (
        _BY_KEY[key].label if key in _BY_KEY else key
    )
    lines = [
        f"⚠️ *Confirm* — `{role}` → {label}",
        "",
        f"`{role}` is safety-bearing: it arbitrates whether work is allowed to proceed.",
        "A weaker brain here widens what gets approved without asking you.",
        "",
        "_This is reversible — the panel offers Undo straight after._",
        "",
        panel_stamp("brains"),
    ]
    buttons = [
        [("✅ Yes, change it", f"estate:brains_set:{role}|{key}")],
        [("✖ Cancel", f"estate:brains_role:{role}")],
    ]
    return "\n".join(lines), with_nav(buttons, "brains")
