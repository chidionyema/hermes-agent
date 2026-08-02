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

    def tracking_put(action, text, buttons):
        real_put(action, text, buttons)
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
