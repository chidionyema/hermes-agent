"""The sub-tick panel reads a trail written by another process, on a machine with a bad clock.

Every test here pins a way the READER can lie, not a way the writer can. That split is
deliberate: the writer cannot promise a closing row (a SIGKILLed daemon emits nothing), so
"opened and never closed" is a state the reader must resolve correctly or the panel reports a
candidate as in flight forever.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gateway.operator_shell import prospector_inflight as IF

NOW = datetime(2026, 8, 10, 20, 0, 0, tzinfo=timezone.utc)


def _row(event: str, *, ts: datetime | str = NOW, seq: int = 1, **fields) -> dict:
    return {
        "ts": ts if isinstance(ts, str) else ts.isoformat(),
        "event": event,
        "run_id": fields.pop("run_id", "run0"),
        "pid": fields.pop("pid", os.getpid()),
        "seq": seq,
        **fields,
    }


@pytest.fixture()
def audit_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "audit"
    d.mkdir()
    monkeypatch.setattr(IF, "AUDIT_DIR", d)
    return d


def _write(audit_dir: Path, name: str, rows: list[dict]) -> Path:
    p = audit_dir / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Reading the tail
# ---------------------------------------------------------------------------

def test_tail_drops_the_partial_first_line(tmp_path):
    """Seeking into the middle of a file lands mid-record. That fragment is not a row."""
    p = tmp_path / "big.jsonl"
    rows = [_row("search", seq=i, query="x" * 400, status="ok") for i in range(1, 400)]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert p.stat().st_size > 4096

    got = IF.tail_rows(p, max_bytes=4096)
    assert got, "tail returned nothing from a file full of rows"
    assert len(got) < len(rows), "test did not actually exercise the seek path"
    assert all(isinstance(r, dict) and r.get("event") for r in got)
    # The boundary row was discarded whole, never half-parsed into a bogus record.
    assert got[0]["seq"] > 1


def test_tail_skips_a_torn_or_corrupt_line(tmp_path):
    p = tmp_path / "torn.jsonl"
    p.write_text(
        json.dumps(_row("candidate_start", seq=1, candidate_id="a")) + "\n"
        + "{not json at all\n"
        + json.dumps(_row("candidate_start", seq=2, candidate_id="b")) + "\n"
        + '{"event": "candidate_start", "candidate_id": "c"',  # torn tail, no newline
        encoding="utf-8",
    )
    got = IF.tail_rows(p)
    assert [r["candidate_id"] for r in got] == ["a", "b"]


def test_missing_file_and_missing_dir_are_empty_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(IF, "AUDIT_DIR", tmp_path / "does-not-exist")
    assert IF.day_files() == []
    assert IF.latest_day_file() is None
    assert IF.tail_rows(tmp_path / "nope.jsonl") == []


def test_latest_day_file_is_the_newest_not_todays(audit_dir):
    """On an idle day today's file does not exist; the honest answer is the last day with work."""
    _write(audit_dir, "2026-08-08.jsonl", [_row("search", status="ok")])
    _write(audit_dir, "2026-08-09.jsonl", [_row("search", status="ok")])
    assert IF.latest_day_file().name == "2026-08-09.jsonl"


# ---------------------------------------------------------------------------
# Folding: open, closed, stalled
# ---------------------------------------------------------------------------

def test_open_and_recent_is_in_flight():
    rows = [
        _row("candidate_start", seq=1, candidate_id="c1", title="A thing"),
        _row("check_result", seq=2, candidate_id="c1", check="incumbency",
             verdict="supported", idx=3, total=7),
    ]
    st = IF.fold(rows, now=NOW + timedelta(seconds=30))
    assert len(st["in_flight"]) == 1
    e = st["in_flight"][0]
    assert e["candidate_id"] == "c1" and e["title"] == "A thing"
    assert e["check"] == "incumbency" and e["idx"] == 3 and e["total"] == 7
    assert st["stalled"] == []


def test_candidate_done_closes_it():
    rows = [
        _row("candidate_start", seq=1, candidate_id="c1"),
        _row("check_result", seq=2, candidate_id="c1", check="pain_reality", verdict="refuted"),
        _row("candidate_done", seq=3, candidate_id="c1", decision="kill", gate="pain_reality"),
    ]
    st = IF.fold(rows, now=NOW + timedelta(seconds=5))
    assert st["in_flight"] == [] and st["stalled"] == []
    assert st["done"][-1]["gate"] == "pain_reality"


def test_open_and_silent_past_the_threshold_is_stalled_not_in_flight():
    """The failure this panel exists to avoid: reporting dead work as live work forever."""
    rows = [_row("candidate_start", seq=1, candidate_id="c1", title="Abandoned")]
    st = IF.fold(rows, now=NOW + timedelta(seconds=IF._STALE_S + 60))
    assert st["in_flight"] == []
    assert [e["candidate_id"] for e in st["stalled"]] == ["c1"]


def test_a_dead_pid_is_stalled_immediately_without_waiting_out_the_threshold():
    """That process can never emit its closing row, so the wait would only delay a known fact."""
    dead = 999_999_998  # not running; os.kill(pid, 0) -> ProcessLookupError
    rows = [_row("candidate_start", seq=1, candidate_id="c1", pid=dead)]
    st = IF.fold(rows, now=NOW + timedelta(seconds=1))
    assert st["in_flight"] == []
    assert st["stalled"] and st["stalled"][0]["pid_alive"] is False


def test_one_runs_done_does_not_close_another_runs_candidate():
    """A day-file interleaves runs. The same candidate_id legitimately appears in two of them."""
    rows = [
        _row("candidate_start", seq=1, candidate_id="dup", run_id="runA", title="first"),
        _row("candidate_start", seq=2, candidate_id="dup", run_id="runB", title="second"),
        _row("candidate_done", seq=3, candidate_id="dup", run_id="runB", decision="kill"),
    ]
    st = IF.fold(rows, now=NOW + timedelta(seconds=5))
    assert [e["title"] for e in st["in_flight"]] == ["first"], (
        "runB's closure closed runA's candidate — keyed on candidate_id alone"
    )


def test_retrieval_health_counts_only_search_rows():
    rows = [
        _row("search", seq=1, status="ok"),
        _row("search", seq=2, status="error"),
        _row("verify_search", seq=3, status="ok"),
        _row("candidate_start", seq=4, candidate_id="c1"),
    ]
    st = IF.fold(rows, now=NOW)
    assert (st["retrieval_ok"], st["retrieval_err"]) == (2, 1)


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def test_an_impossible_timestamp_reads_as_unknown_not_as_fifty_six_years():
    """store/scheduler/audit/1970-01-01.jsonl holds 13 rows of REAL work stamped 1970.

    Subtracting that from now yields a candidate "in flight" for half a century. An age that
    cannot be true is reported as unknown; it is never rendered as a number.
    """
    rows = [_row("candidate_start", ts="1970-01-01T16:02:00.956157+00:00",
                 seq=1, candidate_id="c1")]
    st = IF.fold(rows, now=NOW)
    entry = (st["in_flight"] + st["stalled"])[0]
    assert entry["age_s"] is None
    assert IF._age_str(entry["age_s"]) == "age unknown"


def test_trail_age_comes_from_append_order_not_from_seq():
    """`seq` is a per-PROCESS counter (audit.py:153), so it does not order rows across runs.

    A long-lived daemon reaches seq=90000 while a manual CLI run that started seconds ago is at
    seq=2. Ranking by seq picks the daemon's hours-old row as "newest" and reports a live trail
    as stale — which reads as an idle engine, the exact misdiagnosis this panel exists to end.
    """
    rows = [
        _row("search", ts=NOW - timedelta(hours=3), seq=90_000, run_id="daemon", status="ok"),
        _row("search", ts=NOW - timedelta(seconds=10), seq=2, run_id="cli", status="ok"),
    ]
    st = IF.fold(rows, now=NOW)
    assert st["trail_age_s"] == pytest.approx(10, abs=2), (
        "trail age was taken from the highest seq rather than the last row appended"
    )


def test_an_unparseable_or_absent_timestamp_does_not_raise():
    for ts in ("", "not-a-date", "2026-13-45T99:99:99"):
        st = IF.fold([_row("candidate_start", ts=ts, seq=1, candidate_id="c1")], now=NOW)
        assert (st["in_flight"] + st["stalled"])[0]["age_s"] is None


def test_a_naive_timestamp_is_read_as_utc_not_crashed_on():
    rows = [_row("candidate_start", ts="2026-08-10T19:59:30", seq=1, candidate_id="c1")]
    st = IF.fold(rows, now=NOW)
    assert st["in_flight"] and st["in_flight"][0]["age_s"] == pytest.approx(30, abs=2)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_render_with_no_trail_says_so_and_still_returns_buttons(tmp_path, monkeypatch):
    monkeypatch.setattr(IF, "AUDIT_DIR", tmp_path / "gone")
    text, buttons = IF.render_in_flight()
    assert "No audit trail" in text
    assert buttons and all(isinstance(r, list) for r in buttons)


def test_render_names_the_candidate_and_the_check_in_flight(audit_dir, monkeypatch):
    monkeypatch.setattr(IF, "fold", lambda rows, **kw: {
        "in_flight": [{"candidate_id": "abc123", "title": "Postcode flood risk",
                       "tier": "", "full_vet": True, "check": "payer_solvency",
                       "verdict": "", "idx": 4, "total": 7, "age_s": 12.0,
                       "pid": 1, "pid_alive": True, "run_id": "r"}],
        "stalled": [], "done": [], "retrieval_ok": 9, "retrieval_err": 1,
        "trail_age_s": 12.0, "rows": 40,
    })
    _write(audit_dir, "2026-08-10.jsonl", [_row("search", status="ok")])

    text, buttons = IF.render_in_flight()
    assert "Postcode flood risk" in text
    assert "payer_solvency" in text
    assert "check 4/7" in text
    assert "FULL-VET" in text
    assert "in flight" in text
    assert "9 ok / 1 failed" in text
    flat = [cb for row in buttons for _, cb in row]
    assert "estate:pd_last_run" in flat and "estate:prospector_daemon" in flat


def test_render_marks_a_stalled_candidate_red_and_says_why(audit_dir, monkeypatch):
    monkeypatch.setattr(IF, "fold", lambda rows, **kw: {
        "in_flight": [], "stalled": [{"candidate_id": "zz", "title": "Dropped one",
                                      "tier": "", "full_vet": False, "check": "",
                                      "verdict": "", "idx": None, "total": None,
                                      "age_s": 4000.0, "pid": 5, "pid_alive": False,
                                      "run_id": "r"}],
        "done": [], "retrieval_ok": 0, "retrieval_err": 0, "trail_age_s": 4000.0, "rows": 3,
    })
    _write(audit_dir, "2026-08-10.jsonl", [_row("search", status="ok")])

    text, _ = IF.render_in_flight()
    assert "🔴" in text and "Dropped one" in text
    assert "process gone" in text
    assert "opened, never closed" in text


def test_hostile_titles_and_check_names_still_parse_on_the_send_path(audit_dir, monkeypatch):
    """A panel Telegram rejects with a 400 does not render at all — which on a phone looks
    exactly like the engine being down.

    Check names carry underscores by construction (`payer_solvency`), and candidate titles are
    model-generated free text that can contain `_`, `*` and backticks. The first version of
    this panel put the check name in `*bold*` and drew `unclosed italic entity`.
    """
    # The send path is `render_panel` (legacy markdown -> MarkdownV2) and THEN strict `parse`,
    # which is what test_mdv2_panel_rendering.py's sweep runs. Calling `parse` on the raw panel
    # is a DIFFERENT check that every panel in the shell fails, including panel_stamp's own
    # output — a preflight that is not the gate's own command proves nothing.
    from gateway.operator_shell.mdv2 import parse, render_panel

    monkeypatch.setattr(IF, "fold", lambda rows, **kw: {
        "in_flight": [{"candidate_id": "a_b_c", "title": "Weird `title` with _under_ *stars*",
                       "tier": "", "full_vet": False, "check": "payer_solvency",
                       "verdict": "un_verifiable", "idx": 2, "total": 7, "age_s": 5.0,
                       "pid": 1, "pid_alive": True, "run_id": "r"}],
        "stalled": [{"candidate_id": "x_y", "title": "another_one `here`", "tier": "",
                     "full_vet": False, "check": "", "verdict": "", "idx": None,
                     "total": None, "age_s": 9999.0, "pid": 2, "pid_alive": False,
                     "run_id": "r"}],
        "done": [{"candidate_id": "d_1", "decision": "kill", "gate": "min_composite",
                  "provisional": True}],
        "retrieval_ok": 3, "retrieval_err": 2, "trail_age_s": 5.0, "rows": 10,
    })
    _write(audit_dir, "2026-08-10.jsonl", [_row("search", status="ok")])

    text, _ = IF.render_in_flight()
    parse(render_panel(text))  # raises ParseError on anything Telegram answers with a 400
    assert "payer_solvency" in text and "min_composite" in text
    assert "`" in text
    # The backtick inside the title was neutralised, so the code span still closes.
    assert "`title`" not in text


def test_render_on_the_real_trail_never_raises():
    """The production day-files are 0.5-2.5 MB and include the 1970 rows. Read-only."""
    if not IF.AUDIT_DIR.is_dir():
        pytest.skip("no prospector audit trail on this machine")
    text, buttons = IF.render_in_flight()
    assert text.startswith("🔬 *In flight*")
    assert buttons
