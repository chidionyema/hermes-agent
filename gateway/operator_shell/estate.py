"""Estate control panel — mission card + one-tap ops + proof loop."""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

from gateway.operator_shell.panel_chrome import nav

ButtonRow = List[Tuple[str, str]]

# Pre-warm the coordinator import in a background thread so the first tap on 🎛 Run
# is instant. _load_coordinator() takes ~13s on first load (2938-line coordinator.py);
# without this the first operator to open Run waits 13s staring at nothing.
import threading


def _prewarm_coordinator() -> None:
    try:
        _load_coordinator()
    except Exception:
        pass


_thread = threading.Thread(target=_prewarm_coordinator, daemon=True)
_thread.start()


@dataclass
class PanelView:
    """Platform-agnostic panel payload (text + button rows)."""

    text: str
    paused: bool = False
    buttons: List[ButtonRow] = field(default_factory=list)
    toast: str = ""
    ok: bool = True
    # If set, telegram adapter should edit the pinned mission card message.
    pin_edit: bool = False
    proof_receipt: str = ""
    # Special: create cron topic (async work done by adapter)
    needs_cron_topic_setup: bool = False
    # Special: stop running agents (async — gateway runner)
    needs_stop_agent: bool = False
    # Special: run prospector with optional candidate count
    prospector_candidates: Optional[int] = None


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


_COORD_CACHE: Any = None
_COORD_ERROR: Optional[str] = None


def _load_coordinator() -> Any:
    """Import coordinator.py without permanently polluting sys.path."""
    global _COORD_CACHE, _COORD_ERROR
    if _COORD_CACHE is not None:
        return _COORD_CACHE
    if _COORD_ERROR is not None:
        raise RuntimeError(_COORD_ERROR)

    scripts = _hermes_home() / "scripts"
    coord_path = scripts / "coordinator.py"
    if not coord_path.is_file():
        _COORD_ERROR = f"Estate coordinator not found at {coord_path}"
        raise RuntimeError(_COORD_ERROR)

    scripts_str = str(scripts)
    inserted = False
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
        inserted = True
    try:
        import coordinator as C  # type: ignore

        _COORD_CACHE = C
        return C
    except Exception as exc:
        try:
            spec = importlib.util.spec_from_file_location(
                "hermes_estate_coordinator", coord_path
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not create import spec for coordinator")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["hermes_estate_coordinator"] = mod
            spec.loader.exec_module(mod)
            _COORD_CACHE = mod
            return mod
        except Exception as exc2:
            _COORD_ERROR = f"Failed to load estate coordinator: {exc2 or exc}"
            if inserted:
                try:
                    sys.path.remove(scripts_str)
                except ValueError:
                    pass
            raise RuntimeError(_COORD_ERROR) from exc2


def _knob_landing(key: str, fallback):
    """Where to land after a knob is set: its own Tune group, else the panel's read view.

    Any failure here falls back rather than propagates — a knob that was successfully written
    must not render an error card just because the follow-up panel could not be built.
    """
    try:
        from gateway.operator_shell.cockpit import group_for_key, render_tune_group

        group = group_for_key(key)
        if group:
            return render_tune_group(group)
    except Exception:
        logger.warning("knob landing fell back to the read panel for %s", key, exc_info=True)
    return fallback()


def _proof(action: str, status: str, summary: str, **kwargs) -> str:
    from gateway.operator_shell.proof import Proof, new_request_id

    p = Proof(
        action=action,
        status=status,
        summary=summary,
        request_id=kwargs.get("request_id") or new_request_id(),
        cost_usd=kwargs.get("cost_usd"),
        evidence=kwargs.get("evidence") or [],
        undoable=kwargs.get("undoable", False),
        undo_token=kwargs.get("undo_token"),
    )
    return p.render()


def render_panel_view() -> PanelView:
    """Pinned mission card dashboard."""
    try:
        from gateway.operator_shell.budget import maybe_auto_pause
        from gateway.operator_shell.mission import render_mission_card

        notice = maybe_auto_pause()
        text, paused, buttons = render_mission_card()
        if notice:
            text = notice + "\n\n" + text
        # Fail-closed: when the coordinator bridge is down, render_mission_card
        # returns the _render_unavailable_card() sentinel — detectable by the
        # "estate unavailable" prefix it always emits. Set ok=False so callers
        # (test_panel_fail_closed_without_coordinator, monitor probes) know
        # the panel is in degraded mode.
        ok = "estate unavailable" not in text
        return PanelView(text=text, paused=paused, buttons=buttons, pin_edit=True, ok=ok)
    except Exception as exc:
        logger.error("render_panel_view failed: %s", exc, exc_info=True)
        return PanelView(
            text=(
                "⚠️ *Mission card unavailable*\n\n"
                f"```text\n{exc}\n```\n\n"
                "Gateway chat still works."
            ),
            ok=False,
            buttons=[[("🔄 Retry", "estate:refresh")]],
        )


def handle_estate_action(action: str, request_id: str = "") -> PanelView:
    # `now` is the literal word the mission card footer tells the operator to say
    # ("say `now` to force"). Without this alias, typing `now` falls through every
    # `if action == ...` branch and lands in the unknown-action guard, which prints
    # `Unknown action \`now\`` — the exact case the founder reported. Resolved here
    # at the outer entry so the cache check and the pre-flight key both see the
    # canonical action.
    if (action or "").strip().lower() == "now":
        action = "refresh"

    """Public entry point: dispatch, and record what happened.

    The recording lives HERE rather than inside `_dispatch` because `_dispatch` has dozens of
    return paths and can raise — and a raise is exactly the outcome worth auditing. Wrapping
    the whole call is the only version that cannot drift when someone adds a branch. `record`
    never raises (see `activity.py`), so this wrapper cannot take the cockpit down.

    Pre-flight cache: read-only panels whose probe latency is felt by the operator (st_*,
    builds) consult ``preflight.cache_get()`` first. A miss returns None and the normal
    render path runs. A hit returns the cached text/buttons immediately AND triggers a
    background refresh, so the NEXT tap in the same window is also instant. Mutating
    actions never use the cache — staleness there would be a real bug.
    """
    from gateway.operator_shell.activity import record

    t0 = time.time()
    # Pre-flight: only read-only actions whose latency matters.
    # `refresh` is the mission card — the single most-tapped panel (~10× more than any
    # other), and its cold path is 6s. Cache it for 5s so a re-tap is instant; the
    # background refresh fires immediately so the next tap is fresh too.
    if action in ("refresh", "st_status", "st_health", "st_reconcile", "st_money", "builds"):
        try:
            from gateway.operator_shell.preflight import cache_get, cache_refresh

            cached = cache_get(action)
            if cached is not None:
                cached_text, cached_buttons, fresh = cached
                # Stale-while-revalidate: serve instantly; only re-probe when past TTL
                # so a fresh tap does not kick another 60s Stripe/reconcile run.
                if not fresh:
                    cache_refresh(action, lambda: _render_for_cache(action))
                view = PanelView(
                    text=cached_text,
                    buttons=cached_buttons,
                    toast="cached" if fresh else "updating…",
                )
                record(action, request_id, view=view, ms=(time.time() - t0) * 1000.0,
                       source="cache")
                return view
        except Exception:
            pass  # cache miss / error → fall through to live render

    try:
        view = _dispatch(action, request_id)
    except Exception as exc:
        record(action, request_id, status="error", error=repr(exc),
               ms=(time.time() - t0) * 1000.0)
        raise

    # Post-render: store the result so the NEXT tap can skip the work.
    if action in ("refresh", "st_status", "st_health", "st_reconcile", "st_money", "builds"):
        try:
            from gateway.operator_shell.preflight import cache_put

            cache_put(action, view.text, view.buttons or [])
        except Exception:
            pass

    record(action, request_id, view=view, ms=(time.time() - t0) * 1000.0)
    return view


def _render_for_cache(action: str) -> Tuple[str, List[ButtonRow]]:
    """Render-only path used by the pre-flight background refresh."""
    if action == "refresh":
        from gateway.operator_shell.mission import render_mission_card

        text, _paused, btns = render_mission_card()
        return text, btns
    if action in ("st_status", "st_health", "st_reconcile", "st_money"):
        from gateway.operator_shell.store_ops import render
        verb = action[len("st_"):]
        return render(verb)
    if action == "builds":
        from gateway.operator_shell.builds import render_builds
        return render_builds()
    return "", []


def _dispatch(action: str, request_id: str = "") -> PanelView:
    """Dispatch estate:<action> with idempotency + proof receipts."""
    from gateway.operator_shell.proof import (
        check_idempotent,
        new_request_id,
        push_undo,
        store_idempotent,
        pop_undo,
    )

    raw = (action or "").strip()
    # support estate:approve:abc123
    parts = raw.split(":", 2)
    action = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if len(parts) > 2:
        # approve:shortid form when split wrong — fix
        action = parts[0].lower()
        arg = ":".join(parts[1:])

    # normalize approve/inspect
    if action.startswith("approve"):
        # action may be "approve" and arg short id, or "approve:id" already split
        pass
    rid = request_id or new_request_id()
    prior = check_idempotent(rid)
    if prior and prior.get("text"):
        return PanelView(
            text=prior["text"],
            buttons=prior.get("buttons") or [],
            toast="Already handled",
            pin_edit=True,
            proof_receipt=prior.get("proof") or "",
        )

    try:
        C = _load_coordinator()
    except Exception as exc:
        return PanelView(
            text=f"⚠️ Estate bridge down:\n```text\n{exc}\n```",
            ok=False,
            toast="Estate unavailable",
            buttons=[[("🔄 Retry", "estate:refresh")]],
        )

    def _finish(view: PanelView) -> PanelView:
        store_idempotent(
            rid,
            {
                "text": view.text,
                "buttons": view.buttons,
                "proof": view.proof_receipt,
            },
        )
        return view

    # ---- Mission / views ----
    if action in ("refresh", "mission", ""):
        view = render_panel_view()
        view.toast = "Refreshed"
        view.proof_receipt = _proof("refresh", "done", "Mission card refreshed", request_id=rid)
        return _finish(view)

    # ---- The spine: Now / Run / Tune ----
    # "Now" is the mission card (handled above as refresh). These two are its siblings: Run
    # holds the verbs, Tune holds the 29 knobs. Both are pure reads until a button is tapped,
    # so neither is idempotency-sensitive — but they still go through _finish so a repeated
    # request_id replays instead of re-probing launchctl.
    if action == "run":
        from gateway.operator_shell.cockpit import render_run

        text, buttons = render_run()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Run",
                proof_receipt=_proof("run", "done", "Action panel", request_id=rid),
            )
        )

    if action == "activity":
        from gateway.operator_shell.cockpit import render_activity

        # estate:activity → 7d; estate:activity:30 → that window. Anything unparseable falls
        # back to 7 rather than erroring: a bad arg must not hide the audit trail.
        try:
            days = max(1, min(90, int(arg))) if arg else 7
        except Exception:
            days = 7
        text, buttons = render_activity(days)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Activity",
                proof_receipt=_proof(
                    "activity", "done", f"Operator log ({days}d)", request_id=rid
                ),
            )
        )

    if action == "help":
        from gateway.operator_shell.help_card import render_help

        text, buttons = render_help()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Help",
                proof_receipt=_proof("help", "done", "help directory", request_id=rid),
            )
        )

    if action == "sdlc":
        from gateway.operator_shell.sdlc import render_sdlc

        text, buttons = render_sdlc()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="SDLC",
                proof_receipt=_proof("sdlc", "done", "SDLC pipeline", request_id=rid),
            )
        )

    if action == "find":
        from gateway.operator_shell.find import render_find

        text, buttons = render_find(arg)
        toast = "Map" if not arg else "Find"
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast=toast,
                proof_receipt=_proof(
                    "find", "done", f"search `{arg or '(atlas)'}`", request_id=rid
                ),
            )
        )

    if action == "atlas" or action == "map":
        from gateway.operator_shell.atlas import render_atlas

        text, buttons = render_atlas()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Map",
                proof_receipt=_proof("atlas", "done", "Atlas rooms", request_id=rid),
            )
        )

    if action == "room":
        from gateway.operator_shell.atlas import render_room

        text, buttons = render_room(arg or "")
        label = (arg or "atlas").title()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast=f"Room · {label}",
                proof_receipt=_proof(
                    "room", "done", f"Room `{arg or '?'}`", request_id=rid
                ),
            )
        )

    if action == "code_prompt":
        from gateway.operator_shell.atlas import render_code_prompt

        text, buttons = render_code_prompt()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Assign",
                proof_receipt=_proof(
                    "code_prompt", "done", "Assign coding run", request_id=rid
                ),
            )
        )

    if action == "brain":
        from gateway.operator_shell.brain import render_brain

        text, buttons = render_brain()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Brain",
                proof_receipt=_proof("brain", "done", "Model picker", request_id=rid),
            )
        )

    if action == "agent_model":
        # 🤖 Agent & Model door — the /panel user-shaped category for
        # behavior switches. Uses the text-mode-ui 5-element grammar so the
        # picker reads as a card, not a list.
        from gateway.text_mode_cards import render_agent_model_panel
        from gateway.operator_shell.brain import current

        model, provider = current()
        try:
            from hermes_cli.providers import get_label

            provider_label = get_label(provider)
        except Exception:
            provider_label = provider or "?"

        switches = [
            {"slug": "agent_model", "label": "⚙️ Model", "available": True},
            {"slug": "personality", "label": "🎭 Personality", "available": True},
            {"slug": "reasoning", "label": "🧠 Reasoning", "available": True},
            {"slug": "busy", "label": "🛎 Busy mode", "available": True},
        ]
        text, buttons = render_agent_model_panel(
            current_model=model,
            current_provider_label=provider_label,
            switches=switches,
        )
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Agent & Model",
                proof_receipt=_proof(
                    "agent_model", "done", "Agent & Model door", request_id=rid
                ),
            )
        )

    if action == "brain_set":
        from gateway.operator_shell.brain import render_brain, set_model

        ok, detail = set_model(arg or "")
        receipt = _proof(
            "brain_set",
            "done" if ok else "failed",
            f"brain → `{arg}`",
            request_id=rid,
            evidence=[detail],
        )
        text, buttons = render_brain()
        return _finish(
            PanelView(
                text=receipt + "\n\n" + text,
                buttons=buttons,
                toast="🧠 switched" if ok else "⚠️ Failed",
                ok=ok,
            )
        )

    if action == "tune":
        from gateway.operator_shell.cockpit import render_tune, render_tune_group

        # estate:tune → index; estate:tune:sizing → that group.
        text, buttons = render_tune_group(arg) if arg else render_tune()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Tune",
                proof_receipt=_proof(
                    "tune", "done", f"Knobs {arg or 'index'}", request_id=rid
                ),
            )
        )

    if action == "inbox":
        from gateway.operator_shell.inbox import render_inbox

        text, buttons = render_inbox()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Inbox",
                proof_receipt=_proof("inbox", "done", "Decision inbox", request_id=rid),
            )
        )

    if action in ("rsi", "learning", "self_improve", "self-improve"):
        from gateway.operator_shell.rsi_panel import render_rsi_panel

        text, buttons = render_rsi_panel()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="RSI",
                proof_receipt=_proof(
                    "rsi", "done", "Self-improvement status", request_id=rid
                ),
            )
        )

    if action in ("brief", "sitrep", "overview"):
        from gateway.operator_shell.voice_brief import render_executive_brief

        text, buttons = render_executive_brief()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Brief",
                proof_receipt=_proof("brief", "done", "Executive brief", request_id=rid),
            )
        )

    if action in ("missions", "mission_board"):
        try:
            import flight

            conn = C.connect()
            try:
                text = flight.mission_board(conn)
            finally:
                conn.close()
        except Exception:
            text = "🚀 *Missions*\n\nBoard unavailable — try `/missions` or tap Fleet."
        buttons = [
            [("🚀 Fleet", "estate:fleet")],
            nav("missions"),
        ]
        return _finish(
            PanelView(
                text=str(text)[:3500],
                buttons=buttons,
                toast="Missions",
                proof_receipt=_proof("missions", "done", "Mission board", request_id=rid),
            )
        )

    if action in ("arm_learning", "arm"):
        try:
            import learning_switch as LS

            LS.arm("armed via Telegram estate:arm_learning")
        except Exception:
            OFF = _hermes_home() / "meta" / "OFF_SWITCH"
            OFF.parent.mkdir(parents=True, exist_ok=True)
            OFF.write_text("armed via Telegram estate:arm_learning\n")
        from gateway.operator_shell.rsi_panel import render_rsi_panel

        text, buttons = render_rsi_panel()
        receipt = _proof(
            "arm_learning", "done", "Self-improvement ARMED", request_id=rid,
            evidence=[str(_hermes_home() / "meta" / "OFF_SWITCH")],
        )
        return _finish(
            PanelView(
                text=receipt + "\n\n" + text,
                buttons=buttons,
                toast="🟢 ARMED",
                proof_receipt=receipt,
            )
        )

    if action in ("disarm_learning", "disarm"):
        try:
            import learning_switch as LS

            LS.disarm()
        except Exception:
            OFF = _hermes_home() / "meta" / "OFF_SWITCH"
            if OFF.is_file():
                OFF.unlink()
        from gateway.operator_shell.rsi_panel import render_rsi_panel

        text, buttons = render_rsi_panel()
        receipt = _proof(
            "disarm_learning", "done", "Self-improvement DISARMED", request_id=rid
        )
        return _finish(
            PanelView(
                text=receipt + "\n\n" + text,
                buttons=buttons,
                toast="⚪ OFF",
                proof_receipt=receipt,
            )
        )

    if action in ("status", "status_summary"):
        from gateway.operator_shell.status_summary import render_status_summary

        text, buttons = render_status_summary()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Status",
                proof_receipt=_proof("status", "done", "Estate status summary", request_id=rid),
            )
        )

    if action == "summary":
        from gateway.operator_shell.summary_card import render_summary_card

        target = arg.strip() if arg else ""
        text = render_summary_card(target)
        return _finish(
            PanelView(
                text=text,
                buttons=nav("summary"),
                toast="Summary Card",
                proof_receipt=_proof("summary", "done", f"Analyzed: {target[:40]}", request_id=rid),
            )
        )

    if action in ("diff", "estate_diff"):
        import subprocess

        diff_out = subprocess.run(
            ["python3", str(Path.home() / ".hermes/scripts/estate-diff.py")],
            capture_output=True, text=True, timeout=30,
        )
        text = diff_out.stdout.strip() or "✅ *No changes* since last check"
        if diff_out.returncode != 0:
            text = f"⚠️ Diff probe failed:\n{diff_out.stderr[-400:]}"
        return _finish(
            PanelView(
                text=text,
                buttons=[[("📊 Status", "estate:status")], nav("diff")],
                toast="Estate diff",
                proof_receipt=_proof("diff", "done" if diff_out.returncode == 0 else "failed",
                                    "Estate diff", request_id=rid),
            )
        )

    if action == "fleet":
        from gateway.operator_shell.fleet import render_fleet

        text, buttons = render_fleet()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Fleet",
                proof_receipt=_proof("fleet", "done", "Fleet status", request_id=rid),
            )
        )

    if action in ("daemons", "daemon", "services", "launchctl"):
        from gateway.operator_shell.daemons import render_daemons

        text, buttons = render_daemons()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Daemons",
                proof_receipt=_proof("daemons", "done", "Estate daemon status", request_id=rid),
            )
        )

    if action in ("host", "keepawake", "keep_awake", "estate_online", "online"):
        from gateway.operator_shell.host import render_host_panel

        text, buttons = render_host_panel()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Host",
                proof_receipt=_proof("host", "done", "Estate host status", request_id=rid),
            )
        )

    if action in ("host_keepawake_start", "keepawake_start", "start_keepawake"):
        from gateway.operator_shell.host import render_host_panel, start_keepawake

        ok, detail = start_keepawake()
        text, buttons = render_host_panel()
        receipt = _proof(
            "host_keepawake_start",
            "done" if ok else "failed",
            detail,
            request_id=rid,
            evidence=[detail],
        )
        return _finish(
            PanelView(
                text=receipt + "\n\n" + text,
                buttons=buttons,
                toast="Keep-awake" if ok else "Keep-awake failed",
                ok=ok,
                proof_receipt=receipt,
            )
        )

    if action in (
        "prospector_daemon",
        "prospector_daemons",
        "pd",
        "prospect_daemon",
    ):
        from gateway.operator_shell.prospector_daemon import render_prospector_daemon

        text, buttons = render_prospector_daemon()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Prospector daemons",
                proof_receipt=_proof(
                    "prospector_daemon", "done", "Prospector daemon status", request_id=rid
                ),
            )
        )

    # ---- Store money rail (st_*) ----
    # Deliberately before pd_*: both touch store/scheduler/PAUSE, and a prefix that reads as
    # "store" must never fall through into the generation-daemon controls.
    if action.startswith("st_"):
        from gateway.operator_shell import store_ops as SO

        rest = action[len("st_"):]

        if rest in ("status", "health", "reconcile", "money"):
            text, buttons = SO.render(rest)
            return _finish(
                PanelView(
                    text=text,
                    buttons=buttons,
                    toast=rest.capitalize(),
                    proof_receipt=_proof(
                        f"st_{rest}", "done", f"Store {rest}", request_id=rid
                    ),
                )
            )

        # No st_deploy, on purpose — see store_ops.py. Anything else is a typo, and answering
        # a typo with a plausible panel is how the wrong verb gets trusted.
        return _finish(
            PanelView(
                text=(
                    f"⚠️ Unknown store verb `{rest}`.\n\n"
                    "Try: `store status`, `store health`, `store reconcile`.\n"
                    "To stop the daemon writing to prod, say `pause prospector`."
                ),
                buttons=SO.buttons(),
                ok=False,
                toast="Unknown verb",
            )
        )

    from gateway.operator_shell.estate_pd import dispatch as _pd_dispatch
    from gateway.operator_shell.estate_se import dispatch as _se_dispatch

    _pd_view = _pd_dispatch(
        action, arg, rid,
        PanelView=PanelView, _finish=_finish, _proof=_proof, _knob_landing=_knob_landing,
    )
    if _pd_view is not None:
        return _pd_view

    _se_view = _se_dispatch(
        action, arg, rid,
        PanelView=PanelView, _finish=_finish, _proof=_proof, _knob_landing=_knob_landing,
    )
    if _se_view is not None:
        return _se_view

    if action.startswith("daemon_") or action == "code_assign":
        if action == "code_assign":
            from gateway.operator_shell import code_remote as CR

            body = (arg or "").strip() or None
            if not body:
                from gateway.operator_shell.atlas import render_code_prompt

                text, buttons = render_code_prompt()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Assign",
                        ok=True,
                        proof_receipt=_proof(
                            "code_assign", "done", "Assign prompt", request_id=rid
                        ),
                    )
                )
            ack, tid, buttons = CR.start_code_run(
                body, created_by="telegram:estate"
            )
            return _finish(
                PanelView(
                    text=ack,
                    buttons=buttons,
                    toast="Coding run",
                    proof_receipt=_proof(
                        "code_assign",
                        "done",
                        f"code `{tid[:8] if tid else '?'}`",
                        request_id=rid,
                    ),
                )
            )

        from gateway.operator_shell.daemons import (
            confirm_card as d_confirm,
            render_daemons,
            render_logs as d_logs,
            run_op as d_run,
            _resolve_short,
        )

        rest = action[len("daemon_") :]
        unit = arg
        if rest == "logs":
            text, buttons = d_logs(unit or "coordinator")
            return _finish(
                PanelView(
                    text=text,
                    buttons=buttons,
                    toast="Logs",
                    proof_receipt=_proof(
                        "daemon_logs", "done", f"logs `{unit}`", request_id=rid
                    ),
                )
            )

        # ONE-TAP: estate:daemon_<op>_now:<unit> executes immediately (start/restart/run_now).
        # Stop/unload still requires confirm — taking a daemon offline without warning is the
        # one verb that can take the operator panel itself down.
        if rest.endswith("_now"):
            _candidate = rest[: -len("_now")]
            if _candidate in ("start", "restart", "run"):
                op_name = _candidate
                label = _resolve_short(unit or "")
                if not label:
                    view = render_panel_view()
                    view.text = f"Unknown daemon `{unit}`\n\n" + view.text
                    view.ok = False
                    return _finish(view)
                ok, detail = d_run(op_name, label)
                receipt = _proof(
                    f"daemon_{op_name}",
                    "done" if ok else "failed",
                    detail,
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = render_daemons()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ " + op_name) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

        if rest.endswith("_confirm"):
            op_name = rest[: -len("_confirm")]
            label = _resolve_short(unit or "")
            if not label:
                view = render_panel_view()
                view.text = f"Unknown daemon `{unit}`\n\n" + view.text
                view.ok = False
                return _finish(view)
            ok, detail = d_run(op_name, label)
            receipt = _proof(
                f"daemon_{op_name}",
                "done" if ok else "failed",
                f"{op_name} `{label}`",
                request_id=rid,
                evidence=[detail],
            )
            text, buttons = render_daemons()
            return _finish(
                PanelView(
                    text=receipt + "\n\n" + text,
                    buttons=buttons,
                    toast=("✅ " + op_name) if ok else "⚠️ Failed",
                    ok=ok,
                    proof_receipt=receipt,
                )
            )
        text, buttons = d_confirm(rest, _resolve_short(unit or "") or f"ai.hermes.{unit}")
        return _finish(PanelView(text=text, buttons=buttons, toast="Confirm"))

    if action in ("builds", "ci", "deploys", "ship"):
        from gateway.operator_shell.builds import render_builds

        text, buttons = render_builds()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Builds",
                proof_receipt=_proof("builds", "done", "CI / deploy status", request_id=rid),
            )
        )

    if action in ("pause", "resume"):
        prev = C.estate_paused()
        new_paused = C.set_estate_paused(action == "pause")
        token = push_undo(
            action,
            {"set_paused": prev},
            f"{'paused' if new_paused else 'resumed'} spend",
        )
        view = render_panel_view()
        view.toast = "⏸ Paused" if new_paused else "▶️ Resumed"
        view.proof_receipt = _proof(
            action,
            "done",
            "Spend frozen" if new_paused else "Spend resumed",
            request_id=rid,
            undoable=True,
            undo_token=token,
            evidence=[f"flag={_hermes_home() / 'meta' / 'ESTATE_PAUSED'}"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "undo":
        rec = pop_undo(arg or None)
        if not rec:
            view = render_panel_view()
            view.text = "↩ Nothing to undo.\n\n" + view.text
            view.toast = "No undo"
            return _finish(view)
        rev = rec.get("reverse") or {}
        if "set_paused" in rev:
            C.set_estate_paused(bool(rev["set_paused"]))
        elif rev.get("cron_action") == "resume" and rev.get("job_id"):
            from gateway.operator_shell.cron_ops import format_cron_command

            format_cron_command(f"resume {rev['job_id']}")
        elif rev.get("cron_action") == "pause" and rev.get("job_id"):
            from gateway.operator_shell.cron_ops import format_cron_command

            format_cron_command(f"pause {rev['job_id']}")
        view = render_panel_view()
        view.toast = "Undone"
        view.proof_receipt = _proof(
            "undo",
            "done",
            f"Reverted: {rec.get('summary')}",
            request_id=rid,
            evidence=[f"token={rec.get('token')}"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "stop_agent":
        view = render_panel_view()
        view.needs_stop_agent = True
        view.toast = "Stopping…"
        view.proof_receipt = _proof(
            "stop_agent",
            "pending_confirm",
            "Stop signal issued to active agents",
            request_id=rid,
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "run_prospector":
        n = 20
        if arg.isdigit():
            n = max(1, min(50, int(arg)))
        view = render_panel_view()
        view.prospector_candidates = n
        view.toast = f"Prospector ×{n}"
        view.proof_receipt = _proof(
            "run_prospector",
            "done",
            f"Queued prospector generate --candidates {n}",
            request_id=rid,
            evidence=[f"workdir={Path.home() / 'Documents' / 'code' / 'prospector'}"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "cron_use_main_dm":
        from hermes_cli.config import save_env_value

        save_env_value("TELEGRAM_CRON_IN_MAIN_DM", "1")
        os.environ["TELEGRAM_CRON_IN_MAIN_DM"] = "1"
        # Do not invent TELEGRAM_CRON_THREAD_ID
        view = render_panel_view()
        view.toast = "Cron → this chat"
        view.proof_receipt = _proof(
            "cron_use_main_dm",
            "done",
            "Accepted cron delivery in private DM (no topic)",
            request_id=rid,
            evidence=["TELEGRAM_CRON_IN_MAIN_DM=1", "TELEGRAM_CRON_THREAD_ID unset (honest)"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "setup_cron_topic":
        from gateway.operator_shell.delivery import cron_delivery_state

        cron = cron_delivery_state()
        view = render_panel_view()
        if cron.get("ok"):
            view.toast = "Cron routing ok"
            view.proof_receipt = _proof(
                "setup_cron_topic",
                "done",
                f"Cron already routed: {cron.get('label')}",
                request_id=rid,
            )
            view.text = view.proof_receipt + "\n\n" + view.text
            return _finish(view)

        # Show choose panel first — do NOT auto-fire createForumTopic on private DMs
        # (that API always returns "chat is not a forum" here). Retry button sets force.
        view.needs_cron_topic_setup = False
        view.toast = "Cron delivery"
        how = (
            "🗓 *Cron delivery*\n\n"
            "Your Otto home is a *private DM* (`getChat.type=private`).\n"
            "Telegram does *not* show a Topics toggle when you tap the bot name — "
            "that UI is for groups/forums. Live API: `createForumTopic` → "
            "`chat is not a forum`.\n\n"
            "*Recommended (works now):*\n"
            "→ Tap *Keep cron in this chat*\n\n"
            "*Optional later (real Topics):*\n"
            "1. Create a private group\n"
            "2. Group settings → enable *Topics*\n"
            "3. Add Otto as admin\n"
            "4. Open/create a *Cron* topic → send `/sethome`\n"
            "_No fake thread id will be written._"
        )
        view.proof_receipt = _proof(
            "setup_cron_topic",
            "pending_confirm",
            "Private DM — choose main-chat delivery or Topics group",
            request_id=rid,
        )
        view.text = how + "\n\n" + view.proof_receipt
        view.buttons = [
            [("✅ Keep cron in this chat", "estate:cron_use_main_dm")],
            [("🔄 Try create topic anyway", "estate:setup_cron_topic_force")],
            [("🚀 Missions", "estate:missions")],
            nav(),
        ]
        return _finish(view)

    if action == "setup_cron_topic_force":
        view = render_panel_view()
        view.needs_cron_topic_setup = True
        view.toast = "Trying topic…"
        view.proof_receipt = _proof(
            "setup_cron_topic_force",
            "pending_confirm",
            "Attempting createForumTopic (expected fail on private DM)",
            request_id=rid,
        )
        view.text = view.proof_receipt + "\n\n" + (view.text or "")
        return _finish(view)

    if action == "budget_override":
        # Resume despite trip; mark override for the day
        from gateway.operator_shell.budget import _state_path
        import json

        C.set_estate_paused(False)
        path = _state_path()
        path.write_text(
            json.dumps({"tripped_day": "", "override_at": time.time(), "note": "manual"})
        )
        view = render_panel_view()
        view.toast = "Budget override"
        view.proof_receipt = _proof(
            "budget_override",
            "done",
            "Hard-stop overridden — spend resumed",
            request_id=rid,
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "approve" and arg:
        conn = C.connect()
        try:
            # resolve short id
            rows = C.decisions_view(conn)
            match = None
            for r in rows:
                if str(r["id"]).startswith(arg):
                    match = r
                    break
            if not match:
                text = f"⚠️ No decision matching `{arg}`"
            else:
                C.approve(conn, match["id"])
                text = _proof(
                    "approve",
                    "done",
                    f"Approved `{match['id'][:8]}` — {match['title'][:40]}",
                    request_id=rid,
                    evidence=[f"task={match['id']}"],
                )
        finally:
            conn.close()
        from gateway.operator_shell.inbox import render_inbox

        inbox_text, buttons = render_inbox()
        return _finish(
            PanelView(text=text + "\n\n" + inbox_text, buttons=buttons, toast="Approved")
        )

    if action == "inspect" and arg:
        conn = C.connect()
        try:
            # dict(), not the raw row: these are sqlite3.Row, which has no .get() — and the
            # two views do not even select the same columns (backlog_view omits risk_class
            # and source), so subscripting would KeyError on half the matches. Every tap on
            # 👁 Inspect raised until this line.
            rows = [dict(r) for r in
                    list(C.decisions_view(conn)) + list(C.backlog_view(conn))]
            match = next((r for r in rows if str(r.get("id", "")).startswith(arg)), None)
            if not match:
                text = f"No task `{arg}`"
            else:
                text = (
                    f"👁 `{str(match.get('id', ''))[:12]}`\n"
                    f"*{match.get('status', '?')}* · {match.get('risk_class') or '?'}\n"
                    f"{match.get('title', '')}\n"
                    f"source: `{match.get('source') or '?'}`"
                )
        finally:
            conn.close()
        return _finish(
            PanelView(
                text=text,
                buttons=[nav()],
                toast="Detail",
            )
        )

    if action == "restart":
        # `Restart coordinator?` was hardcoded; the operator tapping from the mission/daemons
        # menu could not tell which daemon was about to be SIGKILLed. The confirm card now
        # says it explicitly, and shows the launchd label the next step will call. Mirrors
        # the Signal Engine / daemon confirm cards which already name their target.
        return _finish(
            PanelView(
                text=(
                    "♻️ *Restart coordinator?*\n\n"
                    "Kicks `ai.hermes.coordinator` via launchctl.\n"
                    "SIGKILLs the daemon; in-flight executors re-submit next tick.\n"
                    "Gateway stays up."
                ),
                buttons=[
                    [
                        ("✅ Confirm", "estate:restart_confirm"),
                        ("✗ Cancel", "estate:refresh"),
                    ]
                ],
                toast="",
            )
        )

    if action == "restart_confirm":
        label = f"gui/{os.getuid()}/ai.hermes.coordinator"  # windows-footgun: ok — POSIX launchd (macOS) helper, never invoked on Windows
        try:
            proc = subprocess.run(
                ["launchctl", "kickstart", "-k", label],
                capture_output=True,
                text=True,
                timeout=30,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "").strip()
        except Exception as exc:
            ok = False
            detail = str(exc)
        receipt = _proof(
            "restart",
            "done" if ok else "failed",
            "Coordinator relaunched" if ok else "Restart failed",
            request_id=rid,
            evidence=[detail[:200] or label],
        )
        view = render_panel_view()
        view.text = receipt + "\n\n" + view.text
        view.toast = "♻️ Restarted" if ok else "⚠️ Failed"
        view.ok = ok
        return _finish(view)

    if action == "system_fuel":
        from gateway.operator_shell.budget import check_budget

        ok, bmsg, metrics = check_budget()
        conn = C.connect()
        try:
            m = C.autonomy_ratio(conn)
            used = C.tasks_today(conn)
            msg = (
                "⛽ *Fuel*\n\n"
                f"• Tasks today: `{used}/{C.DAILY_TASK_BUDGET}`\n"
                f"• Cost (7d window fn): `${m.get('total_cost', 0):.4f}`\n"
                f"• Autonomy: `{int(m.get('autonomy_ratio', 0)*100)}%`\n"
                f"• Budget: {'OK' if ok else 'TRIPPED'} — {bmsg}\n"
            )
            if metrics:
                msg += f"• Hard ceiling: `{metrics.get('max_tasks_per_day')} tasks` / `${metrics.get('max_usd_per_day'):.2f}`\n"
        finally:
            conn.close()
        import os as _os
        ntfy = (_os.getenv("NTFY_TOPIC") or "").strip()
        if ntfy:
            msg += f"• NTFY: `{ntfy}` (dual-path P0 on)\n"
        else:
            msg += (
                "• NTFY: unset — optional P0 backup. "
                "Set `NTFY_TOPIC=your-private-topic` in ~/.hermes/.env "
                "(+ `OPERATOR_SHELL_ALWAYS_NTFY=1` to always fan out).\n"
            )
        buttons = [nav()]
        if not ok:
            buttons.insert(0, [("🔓 Override budget", "estate:budget_override")])
        return _finish(
            PanelView(
                text=msg + "\n" + _proof("fuel", "done", bmsg, request_id=rid),
                buttons=buttons,
                # Toast MUST match the panel's content state — the operator sees
                # the toast first and picks the right follow-up action from it.
                # `Fuel` was a static label that lied about a tripped budget
                # (U10). Now: TRIPPED → 🔓 Override, OK → `OK $X.XXXX`.
                toast=("🔓 Override" if not ok else f"OK ${m.get('total_cost', 0):.4f}"),
            )
        )

    if action == "list_active":
        conn = C.connect()
        try:
            active = C.list_active(conn)
            if not active:
                msg = "🗂️ No active tasks."
            else:
                lines = ["🗂️ *Active:*"]
                for t in active[:12]:
                    lines.append(f"• `{t['id'][:8]}` [{t['status']}] {t['title'][:40]}")
                msg = "\n".join(lines)
        finally:
            conn.close()
        return _finish(
            PanelView(
                text=msg,
                buttons=[nav()],
                toast="Active",
            )
        )

    if action == "view_logs":
        log_path = _hermes_home() / "logs" / "coordinator.log"
        if log_path.is_file():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-15:]
            msg = f"🪵 *Logs:*\n```text\n{''.join(lines)[-3000:]}\n```"
        else:
            msg = "⚠️ Log file missing."
        return _finish(
            PanelView(
                text=msg,
                buttons=[nav()],
                toast="Logs",
            )
        )

    if action == "cron_strip":
        from gateway.operator_shell.cron_ops import format_cron_command

        return _finish(
            PanelView(
                text=format_cron_command("list"),
                buttons=[nav()],
                toast="Cron",
            )
        )

    if action == "mute_progress":
        from gateway.operator_shell.delivery import cycle_telegram_tool_progress

        mode = cycle_telegram_tool_progress()
        view = render_panel_view()
        view.text = (
            _proof("mute", "done", f"Telegram progress → {mode}", request_id=rid)
            + "\n\n"
            + view.text
        )
        view.toast = f"Progress: {mode}"
        return _finish(view)

    # ---- Claude Code remote (task cards / cancel / pause / steer) ----
    if action == "task" and arg:
        from gateway.operator_shell.code_remote import render_task_card

        text, buttons = render_task_card(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Task",
                proof_receipt=_proof("task", "done", f"Task `{arg}`", request_id=rid),
            )
        )

    if action == "cancel" and arg:
        from gateway.operator_shell.code_remote import cancel_task

        text, buttons = cancel_task(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Cancelled",
                proof_receipt=_proof(
                    "cancel", "done", f"Cancelled `{arg}`", request_id=rid
                ),
            )
        )

    if action == "pause_task" and arg:
        from gateway.operator_shell.code_remote import pause_task

        text, buttons = pause_task(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Paused",
                proof_receipt=_proof(
                    "pause_task", "done", f"Paused `{arg}`", request_id=rid
                ),
            )
        )

    if action == "steer_prompt" and arg:
        from gateway.operator_shell.code_remote import steer_prompt_card

        text, buttons = steer_prompt_card(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Steer",
                proof_receipt=_proof(
                    "steer_prompt", "done", f"Steer help `{arg}`", request_id=rid
                ),
            )
        )

    view = render_panel_view()
    view.text = f"⚠️ Unknown action `{action}`\n\n" + view.text
    view.toast = "Unknown"
    view.ok = False
    return _finish(view)
