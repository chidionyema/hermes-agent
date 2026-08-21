"""/lanes groups open GitHub issues by lane label and priority.

The handler talks to the public GitHub API. These tests fake the HTTP reply so
they run offline and assert the only thing that matters: what the founder sees
on his phone is what the labels say.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from gateway.slash_commands import GatewaySlashCommandsMixin


class _Handler(GatewaySlashCommandsMixin):
    """Just the mixin. The handler touches no other gateway state."""


def _event(args: str = ""):
    return SimpleNamespace(get_command_args=lambda: args)


def _issue(number: int, title: str, labels: list[str], is_pr: bool = False) -> dict:
    row = {
        "number": number,
        "title": title,
        "labels": [{"name": name} for name in labels],
    }
    if is_pr:
        row["pull_request"] = {"url": "x"}
    return row


def _run(payload, args: str = "", monkeypatch=None, status: int = 200) -> str:
    def _transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, content=json.dumps(payload), headers={"content-type": "application/json"}
        )

    real_client = httpx.AsyncClient

    def _fake_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(_transport)
        return real_client(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _fake_client)
    return asyncio.run(_Handler()._handle_lanes_command(_event(args)))


def test_groups_by_lane_and_ranks_by_priority(monkeypatch):
    payload = [
        _issue(1, "engine p2", ["lane: Engine", "P2"]),
        _issue(2, "engine p0", ["lane: Engine", "P0"]),
        _issue(3, "ops, no priority", ["lane: Ops"]),
        _issue(4, "a pull request", ["lane: API", "P1"], is_pr=True),
        _issue(5, "nobody owns this", []),
    ]
    out = _run(payload, monkeypatch=monkeypatch)

    assert "Engine: 2 open (1 P0/P1)" in out
    # P0 sorts above P2 inside the lane.
    assert out.index("P0 #2") < out.index("P2 #1")
    # A priority-less item is still counted, and not counted as urgent.
    assert "Ops: 1 open" in out and "Ops: 1 open (" not in out
    assert "P- #3" in out
    # A pull request is marked as one, not as an issue.
    assert "P1 PR4" in out
    assert "API: 1 open (1 P0/P1)" in out
    # An unlabelled item is reported, never silently dropped.
    assert "unlaned: 1" in out


def test_one_lane_argument_shows_only_that_lane(monkeypatch):
    payload = [
        _issue(1, "engine", ["lane: Engine"]),
        _issue(2, "ops", ["lane: Ops"]),
    ]
    out = _run(payload, args="ops", monkeypatch=monkeypatch)
    assert "Ops: 1 open" in out
    assert "Engine" not in out


def test_unknown_lane_names_the_four(monkeypatch):
    out = _run([], args="backend", monkeypatch=monkeypatch)
    assert "No lane called 'backend'" in out
    for lane in ("engine", "api", "ui", "ops"):
        assert lane in out


def test_full_page_says_it_is_a_lower_bound(monkeypatch):
    payload = [_issue(n, f"issue {n}", ["lane: Ops"]) for n in range(100)]
    out = _run(payload, monkeypatch=monkeypatch)
    assert "lower bound" in out
    assert "... and 92 more" in out


def test_a_short_page_makes_no_lower_bound_claim(monkeypatch):
    out = _run([_issue(1, "one", ["lane: Ops"])], monkeypatch=monkeypatch)
    assert "lower bound" not in out


def test_github_failure_is_reported_not_swallowed(monkeypatch):
    out = _run({"message": "rate limited"}, monkeypatch=monkeypatch, status=403)
    assert "could not reach GitHub" in out
    assert "chidionyema/prospector" in out


def test_repo_is_overridable(monkeypatch):
    monkeypatch.setenv("HERMES_LANES_REPO", "someone/else")
    out = _run([], monkeypatch=monkeypatch)
    assert "LANES - someone/else" in out
