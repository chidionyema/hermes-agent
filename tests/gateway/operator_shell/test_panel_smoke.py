"""Estate panel smoke — every read-only door returns a usable card.

Run live (hits real probes; Store can take minutes cold)::

    HERMES_HOME=~/.hermes python -m pytest \\
      tests/gateway/operator_shell/test_panel_smoke.py -q -m live

Default (CI / local): mocks the slow store/builds probes and asserts every
action returns non-empty text + at least one estate: button.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.operator_shell.estate import handle_estate_action
from gateway.operator_shell.natural_ops import match_natural_op
from gateway.operator_shell.panel_chrome import panel_stamp
from gateway.operator_shell.status_summary import _cron_orphans, render_status_summary

# Read-only panels that must always answer with a cockpit card.
_READ_ACTIONS = (
    "refresh",
    "run",
    "tune",
    "find",
    "brain",
    "inbox",
    "status",
    "diff",
    "fleet",
    "daemons",
    "host",
    "builds",
    "rsi",
    "activity",
    "st_status",
    "signal_engine",
)


def _assert_usable(view, action: str) -> None:
    assert view is not None, action
    assert (view.text or "").strip(), f"{action}: empty text"
    assert "Loading" not in (view.text or ""), f"{action}: left on Loading"
    buttons = view.buttons or []
    flat = [cb for row in buttons for _l, cb in row]
    assert flat, f"{action}: no buttons — bricked card"
    assert any(str(cb).startswith("estate:") for cb in flat), f"{action}: no estate nav {flat}"


@pytest.fixture
def _fast_probes(monkeypatch):
    """Avoid Stripe / gh / long subprocesses in the default smoke pass."""

    def fake_store(verb, extra=None):
        return f"🟢 *Store {verb}*\n\n```text\nOK smoke\n```\n\n_stamp_", [
            [("🩺 Health", "estate:st_health")],
            [("⚡️ Now", "estate:refresh")],
        ]

    monkeypatch.setattr(
        "gateway.operator_shell.store_ops.render",
        fake_store,
    )
    monkeypatch.setattr(
        "gateway.operator_shell.builds.render_builds",
        lambda: ("🏗 *Builds*\n\nok", [[("⚡️ Now", "estate:refresh")]]),
    )


@pytest.mark.parametrize("action", _READ_ACTIONS)
def test_read_panel_returns_usable_card(action, _fast_probes):
    view = handle_estate_action(action, f"smoke-{action}")
    _assert_usable(view, action)


def test_natural_status_opens_estate_summary_not_mission():
    op = match_natural_op("status")
    assert op is not None
    assert op.action == "status"
    op2 = match_natural_op("mission")
    assert op2 is not None
    assert op2.action == "refresh"


def test_panel_stamp_shows_relative_age():
    import time

    stamp = panel_stamp("status", rendered_at=time.time() - 120)
    assert "status" in stamp
    assert "2m ago" in stamp
    assert "just now" in panel_stamp("x", rendered_at=time.time())


def test_status_summary_surfaces_cron_orphans(tmp_path, monkeypatch):
    import json

    jobs = {
        "jobs": [
            {
                "id": "deadbeef01",
                "name": "rotting summarize",
                "enabled": False,
                "last_status": "error",
                "last_error": "HTTP 429",
            },
            {
                "id": "okjob",
                "name": "healthy",
                "enabled": True,
                "last_status": "ok",
            },
        ]
    }
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs))
    monkeypatch.setattr(
        "gateway.operator_shell.status_summary.JOBS_PATH", path
    )
    # Avoid live daemon/sqlite probes
    monkeypatch.setattr(
        "gateway.operator_shell.status_summary._count_daemons",
        lambda: (3, 3),
    )
    monkeypatch.setattr(
        "gateway.operator_shell.status_summary._spend_today",
        lambda: (1.0, 20.0, "estate"),
    )

    orphans = _cron_orphans()
    assert len(orphans) == 1
    assert orphans[0]["id"] == "deadbeef01"

    with patch("gateway.operator_shell.status_summary.sqlite3") as sql:
        sql.connect.side_effect = Exception("no db")
        text, buttons = render_status_summary()
    assert "Cron orphans" in text
    assert "rotting summarize" in text or "deadbeef" in text
    assert any("estate:status" in cb or cb == "estate:status"
               for row in buttons for _l, cb in row) or buttons
    # An orphan forces the cron emoji red; the label must name it, or the line
    # renders as the contradiction "red · 0 failing".
    assert "orphaned" in text


def test_spend_gauge_never_shows_green_when_the_reading_is_missing():
    """The regression that made this card worthless.

    On 2026-08-06 the gauge read "$3.91 / $20.00 20% [daily cap]" in GREEN while
    the estate had burned $1,020.34, because its source counted metered API
    dollars only. An absent or stale reading must therefore never be rendered as
    a healthy number — "unknown" is the honest output, and a stale one has to
    carry the date it came from.
    """
    from gateway.operator_shell.status_summary import _spend_gauge

    missing = _spend_gauge(0.0, 120.0, "unavailable")
    assert "unknown" in missing
    assert "🟢" not in missing and "$0.00" not in missing

    stale = _spend_gauge(50.0, 120.0, "stale — last reading 2026-08-01")
    assert "2026-08-01" in stale and "⚠️" in stale

    # A real reading over the cap must be red, not clamped into comfort.
    hot = _spend_gauge(1091.29, 120.0, "estate")
    assert "🔴" in hot and "1091.29" in hot


@pytest.mark.skipif(
    not __import__("os").environ.get("HERMES_LIVE_SMOKE"),
    reason="set HERMES_LIVE_SMOKE=1 for live probes",
)
@pytest.mark.parametrize("action", ("status", "refresh", "fleet", "inbox", "daemons"))
def test_live_fast_panels(action):
    """Opt-in live probes — skip Store (minutes)."""
    view = handle_estate_action(action, f"live-{action}")
    _assert_usable(view, action)
