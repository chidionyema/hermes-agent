"""Opening a panel must not corrupt the history that panel draws.

`_save_daily_snapshot` is called from `render_otto_health` (otto_health.py:276), so every
tap on the Otto Health screen ran it. The daily JSON it writes was always idempotent
(`path.write_text`), but the velocity file was appended to unconditionally — one new row
per render, all stamped the same date. `_velocity_data` then returned `entries[-14:]`
under a comment reading "last 14 days", feeding a panel that labels the sparkline a
"14-day trend".

Measured on the live estate before this fix: `~/.hermes/logs/self-audit/velocity.jsonl`
held 76 rows across 4 distinct dates — {'2026-08-02': 60, '2026-08-03': 4, '2026-08-05':
11, '2026-08-06': 1}. The last 14 rows therefore spanned 3 dates, 11 of the 14 bars being
one day re-sampled by an operator who had done nothing but look at the screen.

Two independent guards, because either alone leaves the defect reachable: the WRITER must
upsert per date (so the file stops growing), and the READER must collapse per date (so
history already written renders honestly without rewriting the operator's audit file
underneath them).
"""

from __future__ import annotations

import json

import pytest

from gateway.operator_shell import otto_health as OH


@pytest.fixture
def velocity(tmp_path, monkeypatch):
    path = tmp_path / "velocity.jsonl"
    monkeypatch.setattr(OH, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(OH, "DAILY_DIR", tmp_path / "daily")
    monkeypatch.setattr(OH, "VELOCITY_FILE", path)
    return path


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_rendering_the_panel_twice_does_not_add_a_second_row_for_today(velocity):
    OH._save_daily_snapshot()
    after_one = _rows(velocity)
    for _ in range(5):
        OH._save_daily_snapshot()
    after_six = _rows(velocity)

    assert len(after_one) == 1
    assert len(after_six) == 1, (
        f"six renders wrote {len(after_six)} rows — the panel is still logging its own views"
    )
    assert after_six[0]["date"] == after_one[0]["date"]


def test_an_earlier_days_history_survives_todays_upsert(velocity):
    velocity.write_text(
        json.dumps({"date": "2026-08-01", "score": 40}) + "\n"
        + json.dumps({"date": "2026-08-02", "score": 55}) + "\n"
    )
    OH._save_daily_snapshot()
    rows = _rows(velocity)

    # The rewrite must not be a truncation: yesterday's scores are the trend.
    assert [r["date"] for r in rows][:2] == ["2026-08-01", "2026-08-02"]
    assert [r["score"] for r in rows][:2] == [40, 55]
    assert len(rows) == 3


def test_the_fourteen_day_trend_is_fourteen_DAYS_not_fourteen_rows(velocity):
    """The exact live corruption, reconstructed: 60 samples of one date, then three more
    dates. `entries[-14:]` returned 14 rows spanning 3 dates. It must return one point per
    date, so the sparkline's x-axis is time."""
    lines = [json.dumps({"date": "2026-08-02", "score": 10 + i}) for i in range(60)]
    lines += [json.dumps({"date": "2026-08-03", "score": 20 + i}) for i in range(4)]
    lines += [json.dumps({"date": "2026-08-05", "score": 30 + i}) for i in range(11)]
    lines += [json.dumps({"date": "2026-08-06", "score": 41})]
    velocity.write_text("\n".join(lines) + "\n")

    data = OH._velocity_data()

    assert len(data) == 4, f"4 distinct dates in the file, {len(data)} points on the trend"
    assert [d["date"] for d in data] == [
        "2026-08-02",
        "2026-08-03",
        "2026-08-05",
        "2026-08-06",
    ]
    # Last write wins within a date — the freshest score for that day, not the first.
    assert [d["score"] for d in data] == [69, 23, 40, 41]


def test_the_trend_is_ordered_by_date_even_if_the_file_is_not(velocity):
    velocity.write_text(
        json.dumps({"date": "2026-08-06", "score": 3}) + "\n"
        + json.dumps({"date": "2026-08-02", "score": 1}) + "\n"
        + json.dumps({"date": "2026-08-05", "score": 2}) + "\n"
    )
    assert [d["score"] for d in OH._velocity_data()] == [1, 2, 3]
