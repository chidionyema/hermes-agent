"""Attribution: the activity log must be able to tell a human tap from a probe.

The defect this locks, measured on 2026-07-31: the live activity file held 489 rows and only
~56 were plausibly real operator taps. The rest were BFS reachability probes and test sweeps
calling `estate.handle_estate_action` — which is the correct funnel, the *same* funnel a real
tap goes through, and therefore indistinguishable after the fact. The `recent_knob_keys`
promotion list and every ranking on the Activity panel were reading their own instrumentation.

Attribution is mechanical, not cooperative: a real tap is dispatched inside the gateway
process, and every probe and test runs in its own interpreter. Nothing has to remember to
declare itself, so nothing can forget to.

The failure mode these tests exist to prevent is the *over*-correction: an attribution scheme
that treats "cannot tell" as "not real" would silently discard every row written before the
field existed and would empty the Tune promotion list on any estate whose pidfile is missing.
Unknown must count as live.
"""

from __future__ import annotations

import json
import os

import pytest

from gateway.operator_shell import activity


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Own log dir, and no leaked PID cache between tests.

    `_gw_pid_cache` is a module global with a 30s TTL — deliberately, because it sits on the
    hot path of every tap and must not stat a file per row. That makes it test-visible state.
    """
    monkeypatch.setattr(activity, "_dir", lambda: tmp_path)
    monkeypatch.setattr(activity, "_gw_pid_cache", (0.0, None))
    return tmp_path


def _rows(tmp_path):
    f = sorted(tmp_path.glob("*.jsonl"))[0]
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


# --- unknown is live -----------------------------------------------------------------------


def test_a_row_with_no_verdict_counts_as_live(_isolated):
    """The whole safety property. Rows predating this field, and any estate with no pidfile,
    must keep counting — otherwise shipping attribution silently empties the log."""
    assert activity.is_live({}) is True
    assert activity.is_live({"action": "refresh"}) is True


def test_no_pidfile_means_the_row_claims_nothing(_isolated, monkeypatch):
    """`live` is OMITTED, not set False. An absent key means "cannot say"; False would be a
    claim we have no evidence for."""
    monkeypatch.setattr(activity, "_gateway_pid", lambda: None)
    activity.record("refresh", "r1", ms=1.0)
    row = _rows(_isolated)[0]
    assert "live" not in row
    assert row["pid"] == os.getpid()
    assert activity.is_live(row) is True


# --- the mechanical test -------------------------------------------------------------------


def test_a_row_written_outside_the_gateway_process_is_synthetic(_isolated, monkeypatch):
    """This test IS the scenario: pytest is not the gateway, so its own rows are synthetic."""
    monkeypatch.setattr(activity, "_gateway_pid", lambda: os.getpid() + 1)
    activity.record("refresh", "probe-1", ms=1.0)
    row = _rows(_isolated)[0]
    assert row["live"] is False
    assert activity.is_live(row) is False


def test_a_row_written_inside_the_gateway_process_is_live(_isolated, monkeypatch):
    monkeypatch.setattr(activity, "_gateway_pid", lambda: os.getpid())
    activity.record("refresh", "tap-1", ms=1.0)
    assert _rows(_isolated)[0]["live"] is True


@pytest.mark.parametrize("raw", ['{"pid": %d, "kind": "gateway"}', "%d", "  %d\n"])
def test_the_pidfile_is_read_in_every_shape_it_ships_in(_isolated, monkeypatch, tmp_path, raw):
    """JSON today, but a bare integer is the older shape and both are on disk in the wild.
    A parse failure here would mark every row unknown, which fails open — but silently."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "gateway.pid").write_text(raw % 4242, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(activity, "_gw_pid_cache", (0.0, None))
    assert activity._gateway_pid() == 4242


def test_a_corrupt_pidfile_degrades_to_unknown_not_to_a_crash(_isolated, monkeypatch, tmp_path):
    """Recording must never break the action — module rule. A half-written pidfile is a
    routine event during a restart, not an exceptional one."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "gateway.pid").write_text('{"pid": ', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(activity, "_gw_pid_cache", (0.0, None))
    assert activity._gateway_pid() is None
    activity.record("refresh", "r", ms=1.0)
    assert "live" not in _rows(_isolated)[0]


# --- what the filter changes ---------------------------------------------------------------


def _mixed(monkeypatch):
    """Two synthetic knob sets, then one real one."""
    monkeypatch.setattr(activity, "_gateway_pid", lambda: os.getpid() + 1)
    activity.record("se_set_confirm:leverage:2", "probe-a", ms=1.0)
    activity.record("pd_set_confirm:batch_size:9", "probe-b", ms=1.0)
    monkeypatch.setattr(activity, "_gateway_pid", lambda: os.getpid())
    activity.record("se_set_confirm:daily_cap:30", "tap-a", ms=1.0)


def test_promotion_ranks_human_sets_only(_isolated, monkeypatch):
    """A sweep walks every knob on the estate — it set 41 of them the day this shipped — so
    without the filter the Tune index promotes whatever the last probe happened to touch."""
    _mixed(monkeypatch)
    assert activity.recent_knob_keys(limit=5) == ["daily_cap"]


def test_rollup_suppresses_synthetic_rows_but_states_how_many(_isolated, monkeypatch):
    """Filtering silently is the same disease as not filtering: the reader trusts a total
    that was quietly computed on 10% of the file."""
    _mixed(monkeypatch)
    r = activity.rollup(1)
    assert r["total"] == 1
    assert r["synthetic"] == 2
    assert [lab for lab, _n in r["top"]] == ["se_set_confirm:daily_cap:30"]


def test_rollup_can_be_asked_for_everything(_isolated, monkeypatch):
    """The rows are suppressed from the ranking, never deleted — debugging a probe run is a
    real need and the data must still be there."""
    _mixed(monkeypatch)
    r = activity.rollup(1, live_only=False)
    assert r["total"] == 3
    assert r["synthetic"] == 0


def test_activity_panel_never_reports_an_empty_log_as_nothing_happening(_isolated, monkeypatch):
    """A file with 489 probe rows and 0 taps must not render identically to an untouched
    estate. Same screen, two very different situations."""
    from gateway.operator_shell.cockpit import render_activity

    monkeypatch.setattr(activity, "_gateway_pid", lambda: os.getpid() + 1)
    for i in range(3):
        activity.record(f"refresh:{i}", f"probe-{i}", ms=1.0)

    text, _rows_out = render_activity(1)
    assert "+3 from probes/tests" in text, text
    assert "No operator taps recorded yet" in text


def test_the_panel_declares_the_suppression_when_there_are_real_rows_too(_isolated, monkeypatch):
    from gateway.operator_shell.cockpit import render_activity

    _mixed(monkeypatch)
    text, _rows_out = render_activity(1)
    assert "1 actions" in text
    assert "+2 from probes/tests" in text, text
