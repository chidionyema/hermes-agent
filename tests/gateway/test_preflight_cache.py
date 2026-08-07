"""Preflight cache: stale-while-revalidate + boot warmup."""

from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Point preflight storage at an isolated temp estate.

    Sets HERMES_HOME instead of patching a module attribute. preflight resolves its path
    per call from the environment, so this is what actually keeps a test run out of
    ~/.hermes/state. The previous version patched ``pf._DIR``/``pf._PATH``, which
    isolated THIS file and left the other 1,303 HERMES_HOME test sites writing the
    production cache — that is how a suite came to serve the founder
    "🔴 UNKNOWN — estate unavailable" on 2026-08-07.
    """
    from gateway.operator_shell import preflight as pf

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    assert pf._path().parent == state, pf._path()
    return state


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


def test_cache_path_follows_hermes_home(tmp_path, monkeypatch):
    """The root cause of 2026-08-07: an import-time path ignores HERMES_HOME.

    A test suite that carefully points HERMES_HOME at a tmp estate still wrote
    ~/.hermes/state/preflight-cache.json, so its own degraded renders were served to the
    live cockpit. Resolution must happen per call, and it must not be reachable via a
    stale module attribute either.
    """
    from gateway.operator_shell import preflight as pf

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert pf._path() == tmp_path / "state" / "preflight-cache.json"

    other = tmp_path / "elsewhere"
    monkeypatch.setenv("HERMES_HOME", str(other))
    assert pf._path() == other / "state" / "preflight-cache.json", "path was cached, not re-resolved"

    # A write must land under the env-designated estate and nowhere else.
    pf.cache_put("builds", "scoped", [])
    assert (other / "state" / "preflight-cache.json").is_file()
    assert not (tmp_path / "state" / "preflight-cache.json").exists()

    # The old attributes must be GONE, so any test still patching them fails loudly
    # instead of silently writing production state.
    assert not hasattr(pf, "_DIR") and not hasattr(pf, "_PATH")


def test_a_stored_failure_does_not_grant_grace_to_the_next_failure(cache_dir):
    """Grace protects a GOOD entry. It used to read prev["ts"] without prev["ok"], so one
    stored failure shielded the next and logged "serving last good" over a failure."""
    from gateway.operator_shell import preflight as pf

    pf.cache_put("refresh", "🔴 first failure", [], ok=False)
    first_ts = pf._load()["refresh"]["ts"]

    pf.cache_put("refresh", "🔴 second failure", [], ok=False)
    entry = pf._load()["refresh"]

    assert entry["text"] == "🔴 second failure", "a failure granted grace to another failure"
    assert entry["ts"] >= first_ts, "the failure's ts never advanced"


def test_warmup_does_not_cache_a_failed_render(cache_dir):
    """The 2026-08-06 incident class: a boot pre-fill published a failure.

    Warmup runs while the coordinator bridge may still be down. Nobody is waiting on a
    pre-fill, so a not-ok warmup render must leave the cache untouched rather than
    become the card the founder sees.
    """
    from gateway.operator_shell import preflight as pf

    calls = []

    def failing_render(action: str):
        calls.append(action)
        return "🔴 UNKNOWN — estate unavailable", [], False

    pf.warmup_slow_panels(actions=("refresh",), render_fn=failing_render)
    deadline = time.time() + 3.0
    while time.time() < deadline and not calls:
        time.sleep(0.05)
    time.sleep(0.3)  # let the (non-)put settle

    assert calls == ["refresh"], "warmup never rendered"
    assert pf.cache_get("refresh") is None, "a failed warmup render was published to the cache"

    # An operator-requested render still caches its failure — the opposite lie is worse.
    pf.cache_put("refresh", "🔴 UNKNOWN — estate unavailable", [], ok=False)
    assert "unavailable" in pf.cache_get("refresh")[0]
