"""Mission home: one-tap approve for money fences (not inbox detour)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_concerns_money_fence_is_one_tap_approve(monkeypatch):
    from gateway.operator_shell import mission as M

    fence = {"id": "abcdef012345", "source": "prospector", "status": "awaiting_approval",
             "risk_class": "money", "title": "pay"}
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = fence

    C = MagicMock()
    C.estate_paused.return_value = False
    C.gateway_alive.return_value = True
    C.get_meta.return_value = {"updated_at": __import__("time").time()}
    C._proc_alive.return_value = True
    C._launchctl_running.return_value = True
    C.decisions_view.return_value = []
    C._is_operator_facing.return_value = True

    monkeypatch.setattr(M, "_cb_bits", lambda _C: (True, True, ""))
    monkeypatch.setattr(M, "_inflight_code", lambda _c: None)
    monkeypatch.setattr(M, "_blocked_missions", lambda _c: [])

    concerns = M._concerns(conn, C, "OPERATIONAL")
    actions = [a for _l, a in concerns]
    assert any(a.startswith("estate:approve:abcdef01") for a in actions), concerns
    assert "estate:inbox" not in actions or any(
        a.startswith("estate:approve:") for a in actions
    )
    assert not any(a == "estate:inbox" and "Approve fence" in l for l, a in concerns)


def test_concerns_top_decision_gets_approve(monkeypatch):
    from gateway.operator_shell import mission as M

    conn = MagicMock()
    # No fence
    conn.execute.return_value.fetchone.return_value = None

    C = MagicMock()
    C.estate_paused.return_value = False
    C.gateway_alive.return_value = True
    C.get_meta.return_value = {"updated_at": __import__("time").time()}
    C._proc_alive.return_value = True
    C._launchctl_running.return_value = True
    C.decisions_view.return_value = [
        {"id": "deadbeef99", "status": "awaiting_approval", "title": "x"}
    ]
    C._is_operator_facing.side_effect = lambda d: True

    monkeypatch.setattr(M, "_cb_bits", lambda _C: (True, True, ""))
    monkeypatch.setattr(M, "_inflight_code", lambda _c: None)
    monkeypatch.setattr(M, "_blocked_missions", lambda _c: [])

    concerns = M._concerns(conn, C, "OPERATIONAL")
    actions = [a for _l, a in concerns]
    assert "estate:approve:deadbeef" in actions
    assert "estate:inbox" in actions


def test_concerns_accepts_sqlite_row_shaped_decisions(monkeypatch):
    """Regression: Row.get crashed and emptied the ladder while the headline still said N."""
    from gateway.operator_shell import mission as M

    class FakeRow(dict):
        def keys(self):
            return dict.keys(self)

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None

    C = MagicMock()
    C.estate_paused.return_value = False
    C.gateway_alive.return_value = True
    C.get_meta.return_value = {"updated_at": __import__("time").time()}
    C._proc_alive.return_value = True
    C._launchctl_running.return_value = True
    C.decisions_view.return_value = [
        FakeRow(
            id="aabbccdd11",
            status="awaiting_approval",
            title="money hung",
            risk_class="money",
        )
    ]
    C._is_operator_facing.side_effect = lambda d: True

    monkeypatch.setattr(M, "_cb_bits", lambda _C: (True, True, ""))
    monkeypatch.setattr(M, "_inflight_code", lambda _c: None)
    monkeypatch.setattr(M, "_blocked_missions", lambda _c: [])

    concerns = M._concerns(conn, C, "OPERATIONAL")
    actions = [a for _l, a in concerns]
    assert "estate:approve:aabbccdd" in actions
    assert any("waiting" in l.lower() or a == "estate:inbox" for l, a in concerns)
