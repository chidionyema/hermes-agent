"""📊 Last run — the batch diagnostics the engine always wrote and nothing ever read.

WHAT WAS MISSING

The phone could say a tick ran and stocked nothing. It could not say why. The engine's
`diagnose_batch()` has written a full funnel, a kill-gate histogram, the unverifiable rate and
the closest-to-passing kills to `store/scheduler/batch_diagnostics.jsonl` after every batch
since before the daemon existed — and to `DIAGNOSTICS_LATEST.txt` in rendered form. Both files
reached the disk and stopped there.

This is the only screen a steering change can be judged on. Focusing generation on tech/AI is
EXPECTED to lower the pass rate before it lifts it, so "0 passes" is not evidence either way;
the top kill gate moving off `moat_ungrounded` is.

WHAT IS PINNED, AND WHY EACH ONE

1. Gates are shown in descending order. "The top gate" is the whole product; unsorted, it is
   whatever order a Counter serialised in.
2. No diagnostics on disk says so, in words, with somewhere to go next. A panel that renders an
   empty histogram claims a batch killed nothing.
3. Candidate titles are stripped of markup. They are model output, panel text is MarkdownV2,
   and one stray `*` unbalances the message and draws a 400 from Telegram for the whole send.
4. `provisional` is labelled not-final. It was ruled outside MOAT_PRIMARY, never publishes on
   PASS, and is re-vetted later — counting it as an outcome overstates what the batch settled.
5. The dispatcher routes `pd_last_run` to the panel, NOT to launchctl. `run` is also an op
   prefix in that if-chain (`pd_run_now:<unit>`), and a later match would have tried to
   `launchctl` a unit called "last".
6. The reader never raises and never slurps the whole file.
"""
from __future__ import annotations

import json

import pytest

from gateway.operator_shell import prospector_daemon as pd

_ROW = {
    "ts": "2026-08-09T22:10:00+00:00",
    "funnel": {"generated": 15, "dedup_dropped": 3, "prescreened_out": 4, "vetted": 6},
    "decisions": {"pass": 0, "kill": 5, "defer": 1, "vetted": 6, "provisional": 2},
    "kill_gates": {"min_composite": 3, "moat_ungrounded": 7, "incumbency": 2},
    "unverifiable_pct": 41.2,
    "by_market": {"uk": {"vetted": 6, "pass": 0}},
    "thresholds": {"min_composite_to_pass": 3.2},
    "closest_kills": [[3.05, "Compliance *pack* for _clinics_"], [2.9, "Another"]],
}


@pytest.fixture()
def diag(tmp_path, monkeypatch):
    """A throwaway diagnostics pair. Never the live store — these tests must not read it."""
    j = tmp_path / "batch_diagnostics.jsonl"
    t = tmp_path / "DIAGNOSTICS_LATEST.txt"
    monkeypatch.setattr(pd, "_DIAG_JSONL", j)
    monkeypatch.setattr(pd, "_DIAG_TEXT", t)
    return j, t


def _write(path, *rows):
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# 1. The histogram, in order
# ---------------------------------------------------------------------------

def test_the_top_kill_gate_is_first(diag):
    _write(diag[0], _ROW)
    text, _buttons = pd.render_last_run()
    body = text.split("Why they were killed", 1)[1]
    assert body.index("moat_ungrounded") < body.index("min_composite") < body.index("incumbency")
    assert "moat_ungrounded" in body and " 7 " in body


def test_the_last_row_wins(diag):
    _write(diag[0], {**_ROW, "kill_gates": {"legality": 9}}, _ROW)
    text, _ = pd.render_last_run()
    assert "legality" not in text


def test_the_funnel_and_the_bar_are_shown(diag):
    _write(diag[0], _ROW)
    text, _ = pd.render_last_run()
    assert "generated 15" in text and "vetted 6" in text
    assert "bar 3.2" in text
    assert "41.2% of checks unverifiable" in text
    assert "retrieval is the bottleneck" in text, "41% unverifiable is a grounding verdict"


def test_market_attribution_is_shown(diag):
    """With rotation on, two consecutive batches are different populations."""
    _write(diag[0], _ROW)
    text, _ = pd.render_last_run()
    assert "uk 6" in text


# ---------------------------------------------------------------------------
# 2. Nothing on disk is a sentence, not an empty histogram
# ---------------------------------------------------------------------------

def test_no_diagnostics_says_so_and_still_offers_a_way_out(diag):
    text, buttons = pd.render_last_run()
    assert "No batch diagnostics" in text
    assert "0" not in text.split("_No batch diagnostics", 1)[1][:60]
    assert any(cb for row in buttons for _l, cb in row if cb.startswith("estate:"))


def test_a_corrupt_row_does_not_raise(diag):
    diag[0].write_text("{not json\n", encoding="utf-8")
    text, _ = pd.render_last_run()
    assert "No batch diagnostics" in text


def test_the_reader_does_not_slurp_the_file(diag, monkeypatch):
    """The file is append-only and grows with uptime; one row must not cost the whole log."""
    _write(diag[0], _ROW)

    def _boom(*_a, **_k):
        raise AssertionError("read_text on an append-only log")

    monkeypatch.setattr(type(diag[0]), "read_text", _boom)
    assert pd._last_batch()["kill_gates"]


# ---------------------------------------------------------------------------
# 3 + 4. Titles are safe, provisional is honest
# ---------------------------------------------------------------------------

def test_model_written_titles_cannot_unbalance_the_markup(diag):
    _write(diag[0], _ROW)
    text, _ = pd.render_last_run()
    assert "Compliance pack for clinics" in text
    assert text.count("*") % 2 == 0, "an odd number of bold markers draws a Telegram 400"
    assert "_clinics_" not in text


def test_provisional_is_not_counted_as_a_result(diag):
    _write(diag[0], _ROW)
    text, _ = pd.render_last_run()
    assert "2 provisional (not final)" in text


def test_no_provisional_row_says_nothing_about_it(diag):
    _write(diag[0], {**_ROW, "decisions": {"pass": 1, "kill": 4, "defer": 0, "provisional": 0}})
    text, _ = pd.render_last_run()
    assert "provisional" not in text


# ---------------------------------------------------------------------------
# The full report
# ---------------------------------------------------------------------------

def test_the_full_view_shows_the_engines_own_report(diag):
    diag[1].write_text("\n".join(f"line {i}" for i in range(120)), encoding="utf-8")
    text, buttons = pd.render_last_run("full")
    assert "line 119" in text and "line 0" not in text, "the tail, not the head"
    assert "```" in text
    assert any(cb == "estate:pd_last_run" for row in buttons for _l, cb in row)


def test_the_full_view_with_no_report_does_not_render_an_empty_code_block(diag):
    text, _ = pd.render_last_run("full")
    assert "```" not in text and "No report on disk" in text


# ---------------------------------------------------------------------------
# 5. Dispatch
# ---------------------------------------------------------------------------

def test_last_run_dispatches_to_the_panel_not_to_launchctl(diag, monkeypatch):
    from gateway.operator_shell import estate_pd

    _write(diag[0], _ROW)
    called = {}

    def _no_launchctl(op, unit):
        called["launchctl"] = (op, unit)
        raise AssertionError(f"pd_last_run reached run_op({op!r}, {unit!r})")

    monkeypatch.setattr(pd, "run_op", _no_launchctl)
    view = estate_pd.dispatch(
        "pd_last_run", "", "r1",
        PanelView=_PanelView, _finish=lambda v: v, _proof=lambda *a, **k: None,
        _knob_landing=lambda *a, **k: None,
    )
    assert view is not None and "Why they were killed" in view.text
    assert "launchctl" not in called


def test_the_full_argument_reaches_the_renderer(diag, monkeypatch):
    from gateway.operator_shell import estate_pd

    diag[1].write_text("REPORT BODY", encoding="utf-8")
    view = estate_pd.dispatch(
        "pd_last_run", "full", "r1",
        PanelView=_PanelView, _finish=lambda v: v, _proof=lambda *a, **k: None,
        _knob_landing=lambda *a, **k: None,
    )
    assert "REPORT BODY" in view.text


class _PanelView:
    """The dispatcher's return shape, minimally. The real one lives in the estate module and
    dragging it in would make this test depend on the whole cockpit importing cleanly."""

    def __init__(self, text="", buttons=None, toast="", proof_receipt=None, **_kw):
        self.text = text
        self.buttons = buttons or []
        self.toast = toast
        self.proof_receipt = proof_receipt
