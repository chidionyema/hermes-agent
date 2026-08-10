"""Prospector daemon (pd_*) dispatch — extracted from estate._dispatch for velocity.

Owns estate:pd_* and related confirm/one-tap verbs.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple


def dispatch(
    action: str,
    arg: str,
    rid: str,
    *,
    PanelView: Any,
    _finish: Callable,
    _proof: Callable,
    _knob_landing: Callable,
) -> Optional[Any]:
    """Handle matching actions; return PanelView or None if not ours."""
    if action.startswith("pd_") or action in ("pd_logs",):
        from gateway.operator_shell.prospector_daemon import (
            confirm_card as pd_confirm,
            confirm_set_param,
            cron_action as pd_cron_action,
            render_cron as pd_render_cron,
            render_logs as pd_logs,
            render_last_run,
            render_nodes,
            render_params as pd_render_params,
            render_prospector_daemon,
            run_op as pd_run,
            set_param as pd_set_param,
            set_paused as pd_set_paused,
        )

        unit = arg
        if not action.startswith("pd_"):
            pass
        else:
            rest = action[len("pd_") :]

            # Params panel
            if rest == "params":
                text, buttons = pd_render_params()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Params",
                        proof_receipt=_proof(
                            "pd_params", "done", "Prospector params", request_id=rid
                        ),
                    )
                )

            # Last run: the batch diagnostics the engine has always written and nothing read.
            # Matched here, ahead of the generic op handling below, because `run` is also an op
            # prefix (`pd_run_now:<unit>`) and a later match would hand this to launchctl.
            if rest == "last_run":
                text, buttons = render_last_run(unit or "")
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Last run",
                        proof_receipt=_proof(
                            "pd_last_run", "done", "Last batch diagnostics", request_id=rid
                        ),
                    )
                )

            # In flight: the sub-tick view (R5). One level FINER than `last_run` — that shows
            # the last COMPLETED batch, this shows the candidate and the check in progress now.
            # Matched before the generic op handling for the same reason `last_run` is.
            if rest == "in_flight":
                from gateway.operator_shell.prospector_inflight import render_in_flight

                text, buttons = render_in_flight()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="In flight",
                        proof_receipt=_proof(
                            "pd_in_flight", "done", "Sub-tick engine progress", request_id=rid
                        ),
                    )
                )

            # Nodes: which brain does which step, and which of them are benched
            if rest == "nodes":
                text, buttons = render_nodes()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Nodes",
                        proof_receipt=_proof(
                            "pd_nodes", "done", "Brain chains per step", request_id=rid
                        ),
                    )
                )

            # Cron / outcomes panel
            if rest == "cron":
                text, buttons = pd_render_cron()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Cron",
                        proof_receipt=_proof(
                            "pd_cron", "done", "Prospector cron outcomes", request_id=rid
                        ),
                    )
                )

            # Pause / unpause generation (PAUSE file)
            if rest in ("pause", "unpause"):
                ok, detail = pd_set_paused(rest == "pause")
                receipt = _proof(
                    f"pd_{rest}",
                    "done" if ok else "failed",
                    detail,
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = render_prospector_daemon()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("⏸ PAUSE" if rest == "pause" else "▶️ Resume"),
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

            # Cron run/pause: pd_cron_run:id / pd_cron_pause:id
            if rest in ("cron_run", "cron_pause"):
                op = "run" if rest == "cron_run" else "pause"
                jid = unit or ""
                ok, detail = pd_cron_action(op, jid)
                receipt = _proof(
                    f"pd_cron_{op}",
                    "done" if ok else "failed",
                    f"cron {op} `{jid}`",
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = pd_render_cron()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ cron " + op) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

            # Apply param: estate:pd_set_confirm:interval:3600 → arg=interval:3600
            if rest == "set_confirm":
                parts_kv = (unit or "").split(":", 1)
                key = parts_kv[0] if parts_kv else ""
                val = parts_kv[1] if len(parts_kv) > 1 else ""
                ok, detail, need_restart = pd_set_param(key, val)
                evidence = [detail]
                if ok and need_restart:
                    rok, rdetail = pd_run("restart", "scheduler")
                    evidence.append(f"restart: {rdetail}")
                    ok = ok and rok
                    detail = detail + " · " + rdetail
                receipt = _proof(
                    "pd_set",
                    "done" if ok else "failed",
                    f"set `{key}={val}`",
                    request_id=rid,
                    evidence=evidence,
                )
                # Same as the Signal Engine knobs: land in the group, so the next change is
                # one tap rather than three. `nodes` has no Tune group — its screen IS the
                # Nodes panel — so it lands there rather than being dumped on Params, where
                # the operator would have to find their way back to see what they changed.
                if key == "nodes":
                    text, buttons = render_nodes()
                else:
                    text, buttons = _knob_landing(key, pd_render_params)
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ set " + key) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

            # Confirm prompt: estate:pd_set:interval:3600 → arg=interval:3600
            if rest == "set":
                parts_kv = (unit or "").split(":", 1)
                key = parts_kv[0] if parts_kv else ""
                val = parts_kv[1] if len(parts_kv) > 1 else ""
                text, buttons = confirm_set_param(key, val)
                return _finish(PanelView(text=text, buttons=buttons, toast="Confirm set"))

            # ONE-TAP: estate:pd_<op>_now:<unit> executes immediately, no confirm.
            # The op must be reversible from this same panel (restart/start/run_now) — those
            # are safe to fire on first tap because the operator can tap again to undo.
            # Stop/unload still goes through the confirm card (destructive: kills ticks).
            if rest.endswith("_now"):
                _candidate = rest[: -len("_now")]
                if _candidate in ("start", "restart", "run"):
                    op_name = _candidate
                    ok, detail = pd_run(op_name, unit or "scheduler")
                    receipt = _proof(
                        f"pd_{op_name}",
                        "done" if ok else "failed",
                        detail,
                        request_id=rid,
                        evidence=[detail],
                    )
                    text, buttons = render_prospector_daemon()
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
                ok, detail = pd_run(op_name, unit or "scheduler")
                receipt = _proof(
                    f"pd_{op_name}",
                    "done" if ok else "failed",
                    f"Prospector {op_name} `{unit or 'scheduler'}`",
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = render_prospector_daemon()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ " + op_name) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )
            if rest == "logs":
                text, buttons = pd_logs(unit or "scheduler")
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Logs",
                        proof_receipt=_proof(
                            "pd_logs", "done", f"Prospector logs `{unit}`", request_id=rid
                        ),
                    )
                )
            # confirm prompt for start/stop/restart/run_now
            text, buttons = pd_confirm(rest, unit or "scheduler")
            return _finish(PanelView(text=text, buttons=buttons, toast="Confirm"))


    return None
