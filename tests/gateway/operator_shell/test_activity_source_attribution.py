"""Origin attribution: the log must be able to tell a tap from a typed line.

Two separate defects, both measured on the live store on 2026-08-06:

1. **Nothing could declare an origin.** `activity.record`'s `source` defaulted to the literal
   string "button" and `handle_estate_action` had no `source` parameter at all, so a `/panel`
   command, a CEO instruction and a button tap entered the same funnel with the same arguments
   and came out labelled identically. 1,051 of the 1,279 rows in the live file claimed a tap.
   The only non-default value in the codebase was "cache" — which is not an origin at all.
2. **Nothing read the field.** `source` was written on every row since the log shipped and
   grepped zero readers across `gateway/`, `plugins/` and `tests/`. Flipping every row's
   source produced a byte-identical Activity panel. This is the same shape as the `toast`
   defect: a correct value written where no one looks is not an improvement.

The fix is a contextvar set once per inbound update, so the origin is declared at the ingress
and every downstream caller inherits it. These tests cover the recording and reading halves;
`tests/gateway/test_telegram_action_attribution.py` drives the real Telegram handlers to prove
the scopes are in the right place.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from gateway.operator_shell import activity


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)
    monkeypatch.setattr(activity, "_gw_pid_cache", (0.0, None))
    return tmp_path


def _rows(tmp_path):
    f = sorted(tmp_path.glob("*.jsonl"))[0]
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


# --- recording ------------------------------------------------------------------------------


def test_an_undeclared_caller_is_unknown_not_button(_isolated):
    """The defect, stated as the property that replaces it.

    The old default made a probe, a test sweep and a direct call all claim a human tap. An
    origin that was never declared must read as unattributed — the log is evidence, and
    "unknown" is a true answer where "button" was a false one.
    """
    activity.record("restart:gateway")
    assert _rows(_isolated)[0]["source"] == "unknown"


@pytest.mark.parametrize("origin", ["button", "command", "chat"])
def test_the_declared_origin_reaches_the_row(_isolated, origin):
    with activity.source_scope(origin):
        activity.record("missions")
    assert _rows(_isolated)[0]["source"] == origin


def test_the_scope_does_not_outlive_the_update(_isolated):
    """A leaked scope would relabel every later action on a long-lived loop context."""
    with activity.source_scope("button"):
        activity.record("a")
    activity.record("b")
    assert [r["source"] for r in _rows(_isolated)] == ["button", "unknown"]


def test_a_nested_scope_refines_rather_than_conflicts(_isolated):
    """A coarse ingress may set a broad origin and a specific layer narrow it."""
    with activity.source_scope("chat"):
        with activity.source_scope("command"):
            activity.record("inner")
        activity.record("outer")
    assert [r["source"] for r in _rows(_isolated)] == ["command", "chat"]


def test_the_scope_survives_asyncio_to_thread(_isolated):
    """The load-bearing mechanism.

    Every estate action runs as `await asyncio.to_thread(handle_estate_action, ...)`. If the
    context did not propagate across that hop, the origin would be declared at the ingress and
    lost before the row was written — the fix would be inert and every row would say unknown.
    """
    async def main():
        with activity.source_scope("command"):
            await asyncio.to_thread(activity.record, "missions")

    asyncio.run(main())
    assert _rows(_isolated)[0]["source"] == "command"


def test_a_task_spawned_inside_the_scope_keeps_it_after_the_scope_exits(_isolated):
    """The property the whole design rests on, and the least obvious one.

    `AdapterBase.handle_message` "returns quickly by spawning background tasks" — so the
    `with` block at the ingress has already exited by the time the action runs. This works
    only because `asyncio.create_task` COPIES the context at creation, and the parent's
    `reset` mutates the parent's copy, not the child's.

    The limitation this also pins down: a task spawned OUTSIDE the scope gets nothing. So a
    request queued as a pending message and drained later by a pre-existing owner task
    records as "unknown", not as its true origin. That is a real gap, and it degrades in the
    safe direction — unattributed, never a fabricated tap.
    """
    async def main():
        gate = asyncio.Event()

        async def worker(tag):
            await gate.wait()
            activity.record(tag)

        with activity.source_scope("chat"):
            inside = asyncio.create_task(worker("inside"))
        outside = asyncio.create_task(worker("outside"))
        gate.set()
        await asyncio.gather(inside, outside)

    asyncio.run(main())
    got = {r["action"]: r["source"] for r in _rows(_isolated)}
    assert got == {"inside": "chat", "outside": "unknown"}


def test_an_explicit_source_argument_overrides_the_scope(_isolated):
    with activity.source_scope("button"):
        activity.record("x", source="command")
    assert _rows(_isolated)[0]["source"] == "command"


# --- cache is not an origin -----------------------------------------------------------------


def test_being_served_from_cache_does_not_erase_who_asked(_isolated):
    """`source="cache"` overwrote the origin instead of sitting beside it.

    A typed command answered from the pre-flight cache is still a typed command. 228 live rows
    lost their origin this way.
    """
    with activity.source_scope("command"):
        activity.record("st_status", served="cache")
    row = _rows(_isolated)[0]
    assert row["source"] == "command"
    assert row["served"] == "cache"


def test_served_is_absent_when_not_cached(_isolated):
    """Absence is meaningful; an always-present "" would make every row look cache-touched."""
    activity.record("st_status")
    assert "served" not in _rows(_isolated)[0]


def test_a_legacy_cache_row_reads_as_unknown_not_guessed(_isolated):
    """Rows written before `served` existed genuinely cannot say tap or typed.

    Bucketing them as taps would put fiction into the one file that exists to be evidence.
    """
    assert activity.origin({"source": "cache"}) == "unknown"
    assert activity.origin({"source": ""}) == "unknown"
    assert activity.origin({}) == "unknown"
    assert activity.origin({"source": "Button"}) == "button"


# --- reading --------------------------------------------------------------------------------


def _write(tmp_path, pairs):
    """pairs: list of (source, count). Rows are live so rollup does not filter them."""
    import os
    path = tmp_path / "2026-08-06.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        i = 0
        for src, n in pairs:
            for _ in range(n):
                i += 1
                fh.write(json.dumps({
                    "ts": 1_754_000_000 + i, "iso": "2026-08-06T01:00:00",
                    "action": "refresh", "arg": "", "status": "ok", "ms": 5.0,
                    "source": src, "pid": os.getpid(), "live": True,
                }) + "\n")


def test_rollup_splits_the_window_by_origin(_isolated, monkeypatch):
    _write(_isolated, [("button", 6), ("command", 3), ("chat", 2), ("cache", 4)])
    monkeypatch.setattr(activity, "_path", lambda day=None: _isolated / "2026-08-06.jsonl")
    r = activity.rollup(days=1)
    assert r["by_source"] == {"button": 6, "command": 3, "chat": 2, "unknown": 4}
    assert r["served_cache"] == 4


def test_the_panel_says_how_the_operator_asked(_isolated, monkeypatch):
    """The reading half. Without this the field is write-only and the defect survives."""
    from gateway.operator_shell import cockpit

    monkeypatch.setattr(activity, "_path", lambda day=None: _isolated / "2026-08-06.jsonl")
    _write(_isolated, [("button", 6), ("command", 3)])
    text, _ = cockpit.render_activity(days=1)
    assert "6 tapped" in text
    assert "3 typed" in text


def test_flipping_every_rows_origin_changes_the_panel(_isolated, monkeypatch):
    """The exact assertion that failed before this shipped: the panel was byte-identical."""
    from gateway.operator_shell import cockpit

    monkeypatch.setattr(activity, "_path", lambda day=None: _isolated / "2026-08-06.jsonl")
    _write(_isolated, [("button", 4)])
    tapped, _ = cockpit.render_activity(days=1)
    _write(_isolated, [("command", 4)])
    typed, _ = cockpit.render_activity(days=1)
    assert tapped != typed


def test_empty_origin_buckets_are_omitted(_isolated, monkeypatch):
    from gateway.operator_shell import cockpit

    monkeypatch.setattr(activity, "_path", lambda day=None: _isolated / "2026-08-06.jsonl")
    _write(_isolated, [("button", 4)])
    text, _ = cockpit.render_activity(days=1)
    assert "tapped" in text
    assert "typed" not in text and "asked" not in text and "unattributed" not in text


def test_an_origin_the_cockpit_has_no_label_for_is_still_shown(_isolated, monkeypatch):
    """A new ingress should appear the day it is added, not be silently dropped."""
    from gateway.operator_shell import cockpit

    monkeypatch.setattr(activity, "_path", lambda day=None: _isolated / "2026-08-06.jsonl")
    _write(_isolated, [("voice", 2)])
    text, _ = cockpit.render_activity(days=1)
    assert "voice 2" in text
