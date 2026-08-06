"""Assigning coding work from Telegram must be tappable, and must know which repo.

Two defects, both 2026-08-06:

- `code_assign` was HANDLED but emitted by no literal button anywhere (proved by
  `test_every_button_dispatches._declared`), so the product's most important verb — give
  the machine work — was reachable only by already knowing the words `cc` / `Otto code`.
- `start_code_run(body)` took text and nothing else, so a run could not say which project
  it was for. On a laptop you `cd` into the repo first; from the phone there was no `cd`.

The failure mode that makes the scope worth testing rather than eyeballing: a run that
looks entirely correct while editing the WRONG repository. Hence the unknown-key and
expiry cases below — both must degrade to unscoped, never to a guess.

NOTE the fake coordinator. `start_code_run` calls `C.progress_notify`, which sends a real
Telegram message (memory: coordinator-test-suite-messaged-the-founder), and `C.open_task`,
which writes the real durable ledger. Neither may happen in a test.
"""

from __future__ import annotations

import json
import time

import pytest

from gateway.operator_shell import code_remote as CR


@pytest.fixture(autouse=True)
def _isolated_scope_file(tmp_path, monkeypatch):
    """Never touch ~/.hermes/state/assign_scope.json from a test."""
    monkeypatch.setattr(CR, "_SCOPE_FILE", str(tmp_path / "assign_scope.json"))


# --- the scope itself ---------------------------------------------------------------


def test_scope_round_trips():
    CR.set_assign_scope("prospector")
    assert CR.get_assign_scope() == "prospector"


def test_scope_expires_to_unscoped(monkeypatch):
    CR.set_assign_scope("prospector")
    monkeypatch.setattr(CR.time, "time", lambda: time.time() + CR._SCOPE_TTL_S + 1)
    assert CR.get_assign_scope() is None, "an expired scope must not silently pick a repo"


def test_missing_and_corrupt_scope_files_are_unscoped(tmp_path, monkeypatch):
    assert CR.get_assign_scope() is None
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(CR, "_SCOPE_FILE", str(bad))
    assert CR.get_assign_scope() is None


def test_clear_is_idempotent():
    CR.clear_assign_scope()
    CR.set_assign_scope("prospector")
    CR.clear_assign_scope()
    CR.clear_assign_scope()
    assert CR.get_assign_scope() is None


# --- resolving a key to a real directory --------------------------------------------


def test_unknown_key_resolves_to_nothing():
    assert CR.resolve_project_repo("no-such-project-anywhere") is None
    assert CR.resolve_project_repo("") is None


def test_key_whose_repo_is_not_on_disk_resolves_to_nothing(tmp_path, monkeypatch):
    reg = tmp_path / "projects.json"
    reg.write_text(
        json.dumps({"projects": [{"key": "ghost", "name": "Ghost",
                                  "primary_repo": "definitely-not-cloned-xyz"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        CR.os.path, "expanduser",
        lambda p: str(reg) if p.endswith("projects.json") else p,
    )
    assert CR.resolve_project_repo("ghost") is None


# --- the card -----------------------------------------------------------------------


def test_scoped_card_sets_the_scope_and_names_the_repo():
    text, buttons = CR.render_assign_card("prospector")
    assert CR.get_assign_scope() == "prospector"
    assert "prospector" in text.lower()
    assert "cc " in text, "the card must teach the exact reply, not just imply it"
    flat = [cb for row in buttons for _l, cb in row]
    assert "estate:projects" in flat


def test_unknown_project_says_so_and_does_not_set_a_scope():
    text, _buttons = CR.render_assign_card("no-such-project-anywhere")
    assert "unknown project" in text.lower()
    assert CR.get_assign_scope() is None, (
        "an unresolvable key must not leave a scope behind — that is how a run "
        "silently edits the wrong repo"
    )


def test_argless_card_renders_and_points_at_the_project_list():
    text, buttons = CR.render_assign_card()
    assert "assign work" in text.lower()
    assert CR.get_assign_scope() is None
    flat = [cb for row in buttons for _l, cb in row]
    assert "estate:projects" in flat


# --- the repo actually reaching the run body ----------------------------------------


class _FakeConn:
    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return []

    def close(self):
        pass


class _FakeCoordinator:
    """Captures what would have been written, and sends nothing."""

    def __init__(self):
        self.opened = {}

    def connect(self):
        return _FakeConn()

    def _circuit_breaker_status(self, _name):
        return True

    def open_task(self, _conn, **kw):
        self.opened = kw
        return "deadbeefcafe1234"

    def get_task(self, _conn, _tid):
        return {"id": "deadbeefcafe1234"}

    def progress_notify(self, *_a, **_k):
        pass

    def _set(self, *_a, **_k):
        pass

    def add_event(self, *_a, **_k):
        pass


@pytest.fixture
def fake_coord(monkeypatch):
    fake = _FakeCoordinator()
    monkeypatch.setattr(CR, "_coord", lambda: fake)
    return fake


def test_scoped_run_carries_the_repo_path_into_the_task_body(fake_coord):
    CR.start_code_run("tidy the readme", created_by="test", project_key="prospector")
    body = fake_coord.opened["body"]
    assert "REPO: " in body and "prospector" in body
    assert "TASK:\ntidy the readme" in body
    assert fake_coord.opened["title"].startswith("💻 CODE [")


def test_unscoped_run_is_byte_for_byte_the_old_behaviour(fake_coord):
    CR.start_code_run("tidy the readme", created_by="test")
    body = fake_coord.opened["body"]
    assert "REPO:" not in body
    assert fake_coord.opened["title"] == "💻 CODE: tidy the readme"


def test_unresolvable_scope_degrades_to_unscoped_not_to_a_bogus_path(fake_coord):
    CR.start_code_run("tidy the readme", created_by="test", project_key="no-such-xyz")
    assert "REPO:" not in fake_coord.opened["body"]


def test_money_fence_still_fires_on_a_scoped_run(fake_coord):
    """Scoping must not become a way around the money/identity fence."""
    ack, _tid, _b = CR.start_code_run(
        "change the stripe payout settlement", created_by="test",
        project_key="prospector",
    )
    assert "fenced" in ack.lower()
