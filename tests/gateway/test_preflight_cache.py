"""Preflight cache: stale-while-revalidate + boot warmup."""

from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Point preflight storage at an isolated temp dir."""
    from gateway.operator_shell import preflight as pf

    monkeypatch.setattr(pf, "_DIR", tmp_path)
    monkeypatch.setattr(pf, "_PATH", tmp_path / "preflight-cache.json")
    return tmp_path


def test_st_ttls_are_explicit(cache_dir):
    from gateway.operator_shell.preflight import _TTL

    for action in ("st_status", "st_health", "st_reconcile", "st_money"):
        assert _TTL[action] == 120, action
    assert _TTL["builds"] == 60


def test_cache_get_returns_stale_entry(cache_dir):
    """Past TTL still returns the payload — phone must not cold-block."""
    from gateway.operator_shell import preflight as pf

    pf.cache_put("st_status", "Store: ok", [[("🏠", "estate:refresh")]])
    # Age the entry past the 120s TTL.
    data = pf._load()
    data["st_status"]["ts"] = time.time() - 999
    pf._store(data)

    hit = pf.cache_get("st_status")
    assert hit is not None
    text, buttons, fresh = hit
    assert text == "Store: ok"
    # JSON round-trip turns tuples into lists — buttons are still usable as rows.
    assert buttons == [[["🏠", "estate:refresh"]]]
    assert fresh is False


def test_cache_get_marks_fresh_within_ttl(cache_dir):
    from gateway.operator_shell import preflight as pf

    pf.cache_put("builds", "Builds: green", [])
    hit = pf.cache_get("builds")
    assert hit is not None
    assert hit[0] == "Builds: green"
    assert hit[2] is True


def test_cache_get_empty_is_none(cache_dir):
    from gateway.operator_shell.preflight import cache_get

    assert cache_get("st_status") is None


def test_warmup_stores_without_raising(cache_dir):
    from gateway.operator_shell import preflight as pf

    calls = []
    done = []

    def fake_render(action: str):
        calls.append(action)
        return f"warm:{action}", [[("x", f"estate:{action}")]]

    # Track put completion by wrapping cache_put.
    real_put = pf.cache_put

    def tracking_put(action, text, buttons, ok=True):
        real_put(action, text, buttons, ok=ok)
        done.append(action)

    pf.cache_put = tracking_put  # type: ignore[method-assign]
    try:
        pf.warmup_slow_panels(
            actions=("st_status", "builds"),
            render_fn=fake_render,
        )
        deadline = time.time() + 3.0
        while time.time() < deadline and len(done) < 2:
            time.sleep(0.05)
    finally:
        pf.cache_put = real_put  # type: ignore[method-assign]

    assert set(calls) == {"st_status", "builds"}
    assert set(done) == {"st_status", "builds"}
    for action in ("st_status", "builds"):
        hit = pf.cache_get(action)
        assert hit is not None
        assert hit[0] == f"warm:{action}"
        assert hit[2] is True


def test_warmup_actions_include_all_store_verbs():
    from gateway.operator_shell.preflight import _WARMUP_ACTIONS

    for a in ("st_status", "st_health", "st_reconcile", "st_money", "builds", "refresh"):
        assert a in _WARMUP_ACTIONS


def test_warmup_skips_fresh_entries(cache_dir):
    from gateway.operator_shell import preflight as pf

    pf.cache_put("st_status", "already warm", [])
    calls = []

    pf.warmup_slow_panels(
        actions=("st_status",),
        render_fn=lambda a: calls.append(a) or ("nope", []),
    )
    time.sleep(0.2)
    assert calls == []
    assert pf.cache_get("st_status")[0] == "already warm"


# --- a failed render must not become the answer ------------------------------------
#
# 2026-08-06: the boot warmup rendered the home card inside the window where a gateway
# restart had the coordinator bridge down. "🔴 UNKNOWN — estate unavailable" was cached
# and served for 26 minutes while the coordinator was answering with 422 task rows. The
# founder's report was "nothing works". Nothing was broken except this cache.


def test_failed_render_does_not_replace_a_recent_good_entry(cache_dir):
    from gateway.operator_shell import preflight as pf

    pf.cache_put("refresh", "🎛 Cockpit · healthy", [[("🏠", "estate:refresh")]])
    pf.cache_put("refresh", "🔴 UNKNOWN — estate unavailable", [], ok=False)

    assert pf.cache_get("refresh")[0] == "🎛 Cockpit · healthy"


def test_failed_render_is_stored_when_there_is_nothing_better(cache_dir):
    """An empty cache has no last-good answer to protect — show the truth."""
    from gateway.operator_shell import preflight as pf

    pf.cache_put("refresh", "🔴 UNKNOWN — estate unavailable", [], ok=False)
    hit = pf.cache_get("refresh")
    assert hit is not None and "unavailable" in hit[0]


def test_failed_render_wins_once_the_good_entry_is_stale(cache_dir):
    """The grace window is bounded on purpose: a stale-good card served through a real
    outage is the same silent lie in the opposite direction."""
    from gateway.operator_shell import preflight as pf

    pf.cache_put("refresh", "🎛 Cockpit · healthy", [])
    data = pf._load()
    data["refresh"]["ts"] = time.time() - (pf._FAILURE_GRACE_S + 5)
    pf._store(data)

    pf.cache_put("refresh", "🔴 UNKNOWN — estate unavailable", [], ok=False)
    assert "unavailable" in pf.cache_get("refresh")[0]


def test_cache_refresh_propagates_the_ok_flag(cache_dir):
    """The background/warmup path has no PanelView, so ok must ride in the return."""
    from gateway.operator_shell import preflight as pf

    pf.cache_put("refresh", "🎛 Cockpit · healthy", [])
    pf.cache_refresh("refresh", lambda: ("🔴 estate unavailable", [], False))
    time.sleep(0.3)
    assert pf.cache_get("refresh")[0] == "🎛 Cockpit · healthy"


def test_cache_refresh_still_accepts_the_two_tuple_contract(cache_dir):
    from gateway.operator_shell import preflight as pf

    pf.cache_refresh("builds", lambda: ("builds: green", []))
    deadline = time.time() + 3.0
    while time.time() < deadline and pf.cache_get("builds") is None:
        time.sleep(0.05)
    assert pf.cache_get("builds")[0] == "builds: green"


def test_render_for_cache_reports_the_panels_own_ok_flag(monkeypatch):
    """`refresh` must inherit render_panel_view's ok rather than a second copy of the
    'estate unavailable' string that can drift away from it."""
    from gateway.operator_shell import estate

    monkeypatch.setattr(
        estate, "render_panel_view",
        lambda: estate.PanelView(text="🔴 estate unavailable", buttons=[], ok=False),
    )
    text, buttons, ok = estate._render_for_cache("refresh")
    assert ok is False and "unavailable" in text


def test_dispatch_hands_the_views_ok_flag_to_the_cache(cache_dir, monkeypatch):
    """The seam, not the halves: a degraded PanelView must reach cache_put as ok=False."""
    from gateway.operator_shell import activity, estate
    from gateway.operator_shell import preflight as pf

    seen = {}
    monkeypatch.setattr(estate, "_dispatch",
                        lambda a, r: estate.PanelView(text="🔴 estate unavailable", ok=False))
    monkeypatch.setattr(activity, "record", lambda *a, **k: None)
    monkeypatch.setattr(pf, "cache_put",
                        lambda action, text, buttons, ok=True: seen.update(ok=ok))

    estate.handle_estate_action("refresh")
    assert seen.get("ok") is False, "a degraded render was cached as though it were an answer"
