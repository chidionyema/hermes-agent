"""Signal Engine (se_* + signal_engine panel) dispatch — extracted from estate._dispatch for velocity.

Owns estate:signal_engine and estate:se_* verbs.
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
    if action in ("signal_engine", "signalengine", "se", "money_rail"):
        from gateway.operator_shell.signal_engine import render_signal_engine

        text, buttons = render_signal_engine()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Signal Engine",
                proof_receipt=_proof(
                    "signal_engine", "done", "Signal Engine status", request_id=rid
                ),
            )
        )

    if action.startswith("se_"):
        from gateway.operator_shell.signal_engine import (
            arm_card as se_arm_card,
            confirm_card as se_confirm,
            confirm_set_param as se_confirm_set,
            render_logs as se_logs,
            render_params as se_render_params,
            render_signal_engine,
            run_op as se_run,
            set_param as se_set_param,
        )

        rest = action[len("se_") :]

        if rest == "params":
            text, buttons = se_render_params()
            return _finish(
                PanelView(
                    text=text,
                    buttons=buttons,
                    toast="Knobs",
                    proof_receipt=_proof(
                        "se_params", "done", "Signal Engine knobs", request_id=rid
                    ),
                )
            )

        if rest == "logs":
            text, buttons = se_logs()
            return _finish(
                PanelView(
                    text=text,
                    buttons=buttons,
                    toast="Logs",
                    proof_receipt=_proof(
                        "se_logs", "done", "Signal Engine logs", request_id=rid
                    ),
                )
            )

        # Apply a knob: estate:se_set_confirm:<key>:<value>
        if rest == "set_confirm":
            parts_kv = (arg or "").split(":", 1)
            key = parts_kv[0] if parts_kv else ""
            val = parts_kv[1] if len(parts_kv) > 1 else ""
            ok, detail, need_restart = se_set_param(key, val)
            evidence = [detail]
            if ok and need_restart:
                # A knob written to config.yaml is not in effect until the daemon
                # re-reads it, so the receipt must carry the restart result too —
                # "set" with a failed restart is a change that never took.
                rok, rdetail = se_run("restart")
                evidence.append(f"restart: {rdetail}")
                ok = ok and rok
                detail = detail + " · " + rdetail
            receipt = _proof(
                "se_set",
                "done" if ok else "failed",
                f"set `{key}={val}`",
                request_id=rid,
                evidence=evidence,
            )
            # Land back in the group the knob came from, not on the read panel. Tuning is
            # rarely a single change — leverage AND max_positions AND stop_loss before a rail
            # move — and returning to `se_params` cost 3 taps to reach the very next knob
            # (group link -> knob -> confirm). From the group it is 1. Falls back to the read
            # panel if the key is not in any group, so an un-grouped knob still renders.
            text, buttons = _knob_landing(key, se_render_params)
            return _finish(
                PanelView(
                    text=receipt + "\n\n" + text,
                    buttons=buttons,
                    toast=("✅ set " + key) if ok else "⚠️ Failed",
                    ok=ok,
                    proof_receipt=receipt,
                )
            )

        # A rejected knob (unknown key, value off the allowlist) renders as a card
        # with no way forward. That is a refusal, so the view must not claim ok —
        # the adapter styles ok=False differently and the receipt log needs the truth.
        def _offers_next_step(rows: List[ButtonRow]) -> bool:
            return any(
                cb.startswith(("estate:se_set_confirm", "estate:se_arm"))
                for row in rows
                for _lbl, cb in row
            )

        # Second screen for rail knobs: estate:se_arm:<key>:<value>
        if rest == "arm":
            parts_kv = (arg or "").split(":", 1)
            key = parts_kv[0] if parts_kv else ""
            val = parts_kv[1] if len(parts_kv) > 1 else ""
            text, buttons = se_arm_card(key, val)
            ok = _offers_next_step(buttons)
            return _finish(
                PanelView(
                    text=text,
                    buttons=buttons,
                    ok=ok,
                    toast="Arm check" if ok else "⚠️ Rejected",
                )
            )

        # First screen for any knob: estate:se_set:<key>:<value>
        if rest == "set":
            parts_kv = (arg or "").split(":", 1)
            key = parts_kv[0] if parts_kv else ""
            val = parts_kv[1] if len(parts_kv) > 1 else ""
            text, buttons = se_confirm_set(key, val)
            ok = _offers_next_step(buttons)
            return _finish(
                PanelView(
                    text=text,
                    buttons=buttons,
                    ok=ok,
                    toast="Confirm set" if ok else "⚠️ Rejected",
                )
            )

        # ONE-TAP: estate:se_<op>_now executes immediately (start/restart).
        # Stop/pause still go through confirm because they move money out of the rail.
        if rest.endswith("_now"):
            _candidate = rest[: -len("_now")]
            if _candidate in ("start", "restart"):
                op_name = _candidate
                ok, detail = se_run(op_name)
                receipt = _proof(
                    f"se_{op_name}",
                    "done" if ok else "failed",
                    f"Signal Engine {op_name}",
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = render_signal_engine()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ " + op_name) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

        # Execute: estate:se_<op>_confirm
        if rest.endswith("_confirm"):
            op_name = rest[: -len("_confirm")]
            ok, detail = se_run(op_name)
            receipt = _proof(
                f"se_{op_name}",
                "done" if ok else "failed",
                f"Signal Engine {op_name}",
                request_id=rid,
                evidence=[detail],
            )
            text, buttons = render_signal_engine()
            return _finish(
                PanelView(
                    text=receipt + "\n\n" + text,
                    buttons=buttons,
                    toast=("✅ " + op_name) if ok else "⚠️ Failed",
                    ok=ok,
                    proof_receipt=receipt,
                )
            )

        # Confirm prompt for start/stop/restart/pause/resume/reset
        text, buttons = se_confirm(rest)
        return _finish(PanelView(text=text, buttons=buttons, toast="Confirm"))


    return None
