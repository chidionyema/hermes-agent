"""The tappable `/code` surface: buttons that exist AND reach a handler.

Two failure modes are specific to this file and both have bitten the cockpit
before. A button can be rendered and wired to nothing (`cockpit-dead-buttons`),
and a panel can be built and never reachable at all. So the tests here assert
the round trip — every callback the panel emits is dispatched by the estate
handler that Telegram actually calls — not just that the strings look right.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.operator_shell import coding_panel as cp
from gateway.operator_shell import coding_session as cs


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

class FakeBackend:
    name = "fake"
    needs_warmup = False
    note = ""

    def __init__(self):
        self.closed = False

    def start(self, repo: Path) -> None:
        self.repo = repo

    def turn(self, prompt: str, *, timeout_s: float) -> str:
        return "ok"

    def alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


def _source(chat="42", thread=None, platform="telegram"):
    return SimpleNamespace(platform=platform, chat_id=chat, thread_id=thread)


def _mkrepo(parent: Path, name: str) -> Path:
    r = parent / name
    r.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


@pytest.fixture(autouse=True)
def _clean_registry():
    cs.close_all()
    yield
    cs.close_all()


@pytest.fixture
def roots(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "code"
    root.mkdir()
    monkeypatch.setenv("HERMES_CODE_ROOTS", str(root))
    return root


@pytest.fixture
def fake_backend(monkeypatch):
    made: list[FakeBackend] = []

    def _build(name: str):
        b = FakeBackend()
        b.name = name
        made.append(b)
        return b

    monkeypatch.setattr(cs, "build_backend", _build)
    return made


def _callbacks(rows) -> list[str]:
    return [cb for row in rows for (_label, cb) in row]


# --------------------------------------------------------------------------- #
# discovery — the list is derived, never hand-maintained
# --------------------------------------------------------------------------- #

def test_discover_finds_git_repos_and_ignores_everything_else(roots: Path):
    _mkrepo(roots, "prospector")
    _mkrepo(roots, "hermes-agent")
    (roots / "notes").mkdir()            # a plain directory is not a repo
    _mkrepo(roots, ".cache")             # dotdirs are machinery, not projects
    (roots / "loose.txt").write_text("x")

    names = {p.name for p in cp.discover_repos()}
    assert names == {"prospector", "hermes-agent"}


def test_discovery_is_capped_so_the_keyboard_stays_a_keyboard(roots: Path):
    for i in range(6):
        _mkrepo(roots, f"repo{i}")
    assert len(cp.discover_repos(limit=3)) == 3


def test_the_repo_you_last_touched_is_the_first_button(roots: Path):
    """Ranked by recency, not by alphabet — the cap makes the order load-bearing.

    Alphabetical + per-root cap was the first version, and on the founder's own
    machine it filled all ten slots from `~/Documents/code` at `crux` …
    `modeltrainer_backup`, so `hermes-agent` (a different root) could not be
    opened from the picker AT ALL.
    """
    old = _mkrepo(roots, "aaa-ancient")
    mid = _mkrepo(roots, "mmm-lastweek")
    new = _mkrepo(roots, "zzz-today")
    for repo, when in ((old, 1_000_000), (mid, 2_000_000), (new, 3_000_000)):
        for probe in (repo / ".git" / "index", repo / ".git" / "HEAD", repo):
            if probe.exists():
                os.utime(probe, (when, when))

    assert [p.name for p in cp.discover_repos()] == [
        "zzz-today", "mmm-lastweek", "aaa-ancient",
    ]


def test_a_worktree_does_not_crowd_out_the_checkout_it_came_from(roots: Path):
    """Worktrees are the freshest directories on disk and would own the keyboard.

    `wt-176`, `wt-close-loop` and `prospector-latest` are one repo wearing three
    names. Demoted below real checkouts, never dropped — `/code wt-176` still
    resolves, because `resolve_repo` matches by name and not through this list.
    """
    real = _mkrepo(roots, "prospector")
    wt = roots / "wt-176"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {real}/.git/worktrees/wt-176\n")
    # The worktree is the more recently touched of the two.
    for probe in (real / ".git" / "index", real / ".git" / "HEAD", real):
        if probe.exists():
            os.utime(probe, (1_000_000, 1_000_000))

    assert [p.name for p in cp.discover_repos()] == ["prospector", "wt-176"]
    assert [p.name for p in cp.discover_repos(limit=1)] == ["prospector"]


def test_an_unreadable_root_does_not_erase_the_readable_one(tmp_path, monkeypatch):
    good = tmp_path / "good"
    good.mkdir()
    _mkrepo(good, "prospector")
    monkeypatch.setenv(
        "HERMES_CODE_ROOTS", f"{tmp_path / 'nope'}{__import__('os').pathsep}{good}"
    )
    assert [p.name for p in cp.discover_repos()] == ["prospector"]


# --------------------------------------------------------------------------- #
# the picker
# --------------------------------------------------------------------------- #

def test_picker_offers_one_button_per_repo_carrying_the_backend(roots: Path):
    _mkrepo(roots, "prospector")
    _text, rows = cp.render_picker("claude")
    assert "estate:code:open:claude:prospector" in _callbacks(rows)
    # The repo lives on the BUTTON, not in the prose — the point is that nothing
    # has to be read and retyped.
    assert any("prospector" in label for row in rows for (label, _cb) in row)


def test_picker_can_switch_brains_without_opening_anything(roots: Path):
    _mkrepo(roots, "prospector")
    _text, rows = cp.render_picker("claude")
    assert "estate:code:pick:pi" in _callbacks(rows)

    text, rows = cp.render_picker("pi")
    assert "estate:code:open:pi:prospector" in _callbacks(rows)
    assert "estate:code:pick:claude" in _callbacks(rows)
    # pi is stateless between turns; the picker says so BEFORE money is spent.
    assert "no memory" in text.lower()


def test_an_unknown_backend_falls_back_to_claude_rather_than_rendering_it(roots: Path):
    _mkrepo(roots, "prospector")
    _text, rows = cp.render_picker("gpt-9")
    assert "estate:code:open:claude:prospector" in _callbacks(rows)


def test_with_no_repos_the_picker_names_the_roots_it_searched(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CODE_ROOTS", str(tmp_path / "empty"))
    text, rows = cp.render_picker()
    assert str(tmp_path / "empty") in text
    # Even the empty state is not a dead end: the spine is still under it.
    assert "estate:refresh" in _callbacks(rows)


# --------------------------------------------------------------------------- #
# open / session card / end
# --------------------------------------------------------------------------- #

def test_tapping_a_repo_opens_a_session_for_that_chat(roots, fake_backend):
    _mkrepo(roots, "prospector")
    src = _source()
    text, buttons, _toast, ok = cp.handle("open:claude:prospector", src)

    assert ok is True
    assert cs.get(src) is not None
    assert cs.get(src).repo.name == "prospector"
    assert "prospector" in text
    assert "estate:code:end" in _callbacks(buttons)


def test_opening_a_repo_that_is_not_there_re_renders_the_picker(roots, fake_backend):
    _mkrepo(roots, "prospector")
    text, buttons, _toast, ok = cp.handle("open:claude:nosuch", _source())
    assert ok is False
    assert "estate:code:open:claude:prospector" in _callbacks(buttons)
    assert "nosuch" in text


def test_the_session_card_carries_the_live_controls(roots, fake_backend):
    _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)

    text, buttons = cp.render_session(src)
    cbs = _callbacks(buttons)
    assert {"estate:code:end", "estate:code:diff"} <= set(cbs)
    # A `Status` button here re-rendered the identical card. The spine's own 🔄
    # does that, and a button that appears to do nothing reads as a broken one.
    assert "estate:code:status" not in cbs
    assert "prospector" in text


def test_with_nothing_open_the_card_is_the_picker(roots, fake_backend):
    _mkrepo(roots, "prospector")
    _text, buttons = cp.render_session(_source())
    assert "estate:code:open:claude:prospector" in _callbacks(buttons)


def test_end_closes_this_chats_session_and_offers_the_picker(roots, fake_backend):
    _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)

    text, buttons, _toast, ok = cp.handle("end", src)
    assert ok is True
    assert cs.get(src) is None
    assert "Nothing was committed" in text
    assert "estate:code:open:claude:prospector" in _callbacks(buttons)


def test_end_in_a_chat_with_no_session_is_not_an_error(roots, fake_backend):
    _mkrepo(roots, "prospector")
    _text, _buttons, _toast, ok = cp.handle("end", _source())
    assert ok is True


def test_a_button_cannot_close_another_chats_session(roots, fake_backend):
    _mkrepo(roots, "prospector")
    mine, theirs = _source(chat="1"), _source(chat="2")
    cp.handle("open:claude:prospector", mine)
    cp.handle("open:claude:prospector", theirs)

    cp.handle("end", mine)
    assert cs.get(mine) is None
    assert cs.get(theirs) is not None


def test_an_action_without_a_chat_refuses_instead_of_guessing(roots, fake_backend):
    _mkrepo(roots, "prospector")
    text, _buttons, _toast, ok = cp.handle("end", None)
    assert ok is False
    assert "which chat" in text


# --------------------------------------------------------------------------- #
# diff — git is the receipt, the agent's prose is not
# --------------------------------------------------------------------------- #

def test_diff_reports_what_git_says_changed(roots, fake_backend):
    repo = _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)

    (repo / "touched.txt").write_text("hello")
    text, buttons, _toast, ok = cp.handle("diff", src)

    assert ok is True
    assert "touched.txt" in text
    # Nothing behind a button tap may commit an unreviewed executor's work.
    assert "Nothing is committed" in text
    assert "estate:code:end" in _callbacks(buttons)


def test_diff_ignores_an_inherited_git_dir(roots, fake_backend, monkeypatch, tmp_path):
    """`git -C <repo>` loses to `GIT_DIR`/`GIT_INDEX_FILE` in the environment.

    Found by the pre-commit hook itself: a git hook exports those, so this
    module's status call read ANOTHER repo's index against this repo's worktree
    and reported 5263 changed paths on a repo with one file. Anything the
    gateway runs from inside a git process would have shown the same lie.
    """
    other = _mkrepo(tmp_path, "elsewhere")
    repo = _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)
    (repo / "touched.txt").write_text("hello")

    # Poisoned only now: `git init` itself obeys GIT_DIR, so setting it earlier
    # would build the fixtures in the wrong place and prove nothing.
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

    text, _buttons, _toast, ok = cp.handle("diff", src)
    assert ok is True
    assert "touched.txt" in text
    assert "1 path(s) changed" in text


def test_the_diff_does_not_eat_the_first_letter_of_a_modified_path(roots, fake_backend):
    """`modified · EADME.md` — the receipt corrupted the name it exists to prove.

    Porcelain writes a two-column status, so an unstaged change starts with a
    SPACE; the reader stripped the whole block and then sliced from column 3,
    losing one character of the first line only. Seen by rendering the card, not
    by any test: every existing diff test used an untracked file (`?? path`),
    which has no leading space and so could never fail this way.
    """
    repo = _mkrepo(roots, "prospector")
    (repo / "README.md").write_text("hello\n")
    env = cs.git_env()
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "init"],
        check=True, env=env,
    )
    (repo / "README.md").write_text("hello\nworld\n")

    src = _source()
    cp.handle("open:claude:prospector", src)
    text = cp.handle("diff", src)[0]

    assert "modified · README.md" in text
    # …and not the truncated name it used to print. (`"EADME" not in text` is
    # NOT the check: "README" contains it.)
    assert "· EADME.md" not in text


def test_diff_on_a_clean_tree_says_nothing_changed(roots, fake_backend):
    _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)
    text, _buttons, _toast, ok = cp.handle("diff", src)
    assert ok is True
    assert "clean" in text.lower()


# --------------------------------------------------------------------------- #
# navigation — the founder's report was "no way to navigate backwards"
# --------------------------------------------------------------------------- #

def _screens(src) -> dict:
    """Every screen this panel can put in front of the operator."""
    return {
        "picker": cp.render_picker("claude"),
        "picker-pi": cp.render_picker("pi"),
        "session": cp.render_session(src),
        "diff": cp.render_diff(src),
        "controls": ("", cp.controls()),
    }


def test_no_screen_is_a_dead_end(roots, fake_backend):
    """Every /code screen carries the cockpit spine.

    The first version rendered bespoke rows and omitted `panel_chrome.nav()`, so
    once the operator was inside /code the only exit was to END the session — the
    destructive action was the navigation. A panel that skips the spine has
    silently opted out of the cockpit, and nothing else in the file notices.
    """
    _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)

    for name, (_text, rows) in _screens(src).items():
        cbs = _callbacks(rows)
        assert "estate:refresh" in cbs, f"{name} has no way home"
        assert "estate:sdlc" in cbs, f"{name} is missing the spine"
        assert rows[-1][0][1] == "estate:refresh", f"{name}: nav must be the LAST row"


def test_the_diff_leaf_offers_the_way_back_to_the_session(roots, fake_backend):
    _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)

    _text, rows = cp.render_diff(src)
    labels = {label: cb for row in rows for (label, cb) in row}
    back = [cb for label, cb in labels.items() if label.startswith("⬅")]
    assert back == ["estate:code"], "the diff must return to the session it came from"


def test_no_screen_offers_the_same_callback_twice(roots, fake_backend):
    """A duplicate button is the cockpit's own documented defect (nav() guards it)."""
    _mkrepo(roots, "prospector")
    src = _source()
    cp.handle("open:claude:prospector", src)

    for name, (_text, rows) in _screens(src).items():
        cbs = _callbacks(rows)
        assert len(cbs) == len(set(cbs)), f"{name} renders a duplicate callback"


def test_the_cockpit_offers_a_way_IN_without_typing_the_command(roots):
    """`/code` reachable only by typing it is the recall this surface removes."""
    from gateway.operator_shell.sdlc import render_sdlc

    _text, rows = render_sdlc()
    assert "estate:code" in _callbacks(rows)


# --------------------------------------------------------------------------- #
# the round trip: rendered callback -> the dispatcher Telegram actually calls
# --------------------------------------------------------------------------- #

def test_every_button_this_panel_renders_reaches_the_estate_dispatcher(roots, fake_backend):
    """A rendered button wired to nothing is the cockpit's oldest defect class."""
    from gateway.operator_shell.estate import handle_estate_action

    _mkrepo(roots, "prospector")
    src = _source()

    _text, picker = cp.render_picker("claude")
    rows = list(picker) + list(cp.controls())

    for cb in _callbacks(rows):
        assert cb.startswith("estate:"), f"{cb} would escape the estate button gate"
        action = cb.split(":", 1)[1]
        view = handle_estate_action(action, "rid", src)
        assert view is not None and view.text, f"{cb} dispatched to an empty view"


def test_the_estate_dispatcher_threads_the_chat_through_to_the_session(roots, fake_backend):
    from gateway.operator_shell.estate import handle_estate_action

    _mkrepo(roots, "prospector")
    src = _source(chat="777")

    view = handle_estate_action("code:open:claude:prospector", "rid", src)
    assert view.ok is True
    assert cs.get(src) is not None

    view = handle_estate_action("code:end", "rid", src)
    assert cs.get(src) is None


def test_a_tap_and_a_typed_command_address_the_same_session(roots, fake_backend):
    """A callback query gives chat_id as a str; a message may give an int.

    Uncoerced, those are two different keys for one chat — the End button would
    find nothing to end while the session it was rendered from stayed open.
    """
    _mkrepo(roots, "prospector")
    typed = _source(chat=777, thread=12)        # ints, as a MessageEvent carries them
    tapped = _source(chat="777", thread="12")   # strings, as a callback carries them

    cp.handle("open:claude:prospector", typed)
    assert cs.get(tapped) is not None
    cp.handle("end", tapped)
    assert cs.get(typed) is None
