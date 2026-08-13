"""Coding-agent sessions: lifecycle, rails, and the one wiring claim that matters.

The interception-order test is not decoration. An explicitly-opened session that
loses a race to a phrase matcher fails in the worst possible way — the operator
types "run the tests", the estate router answers with a panel, and the coding
agent looks alive while receiving nothing.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.operator_shell import coding_session as cs


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

class FakeBackend:
    name = "fake"

    def __init__(self, *, reply: str = "ok", alive: bool = True, delay: float = 0.0):
        self.reply = reply
        self._alive = alive
        self.delay = delay
        self.started_on: Path | None = None
        self.closed = False
        self.prompts: list[str] = []

    def start(self, repo: Path) -> None:
        self.started_on = repo

    def turn(self, prompt: str, *, timeout_s: float) -> str:
        self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        return self.reply

    def alive(self) -> bool:
        return self._alive and not self.closed

    def close(self) -> None:
        self.closed = True


def _source(chat="42", thread=None, platform="telegram"):
    return SimpleNamespace(platform=platform, chat_id=chat, thread_id=thread)


@pytest.fixture(autouse=True)
def _clean_registry():
    cs.close_all()
    yield
    cs.close_all()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "demo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


@pytest.fixture
def _fake_backend(monkeypatch):
    made: list[FakeBackend] = []

    def _build(name: str):
        if name in {"pi", "minimax"}:
            raise cs.CodingSessionError("pi not wired")
        b = FakeBackend()
        made.append(b)
        return b

    monkeypatch.setattr(cs, "build_backend", _build)
    return made


# --------------------------------------------------------------------------- #
# repo resolution
# --------------------------------------------------------------------------- #

def test_resolves_an_absolute_git_repo(repo):
    assert cs.resolve_repo(str(repo)) == repo.resolve()


def test_rejects_a_directory_that_is_not_a_repo(tmp_path):
    plain = tmp_path / "notarepo"
    plain.mkdir()
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.resolve_repo(str(plain))
    assert "No git repo" in str(exc.value)


def test_accepts_a_worktree_where_dot_git_is_a_FILE(tmp_path):
    """In a git worktree `.git` is a file containing `gitdir:`, not a directory.

    Testing is_dir() here would reject every worktree — and worktrees are the
    documented way to work in this estate.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere/.git/worktrees/wt\n")
    assert cs._is_git_repo(wt) is True
    assert cs.resolve_repo(str(wt)) == wt.resolve()


def test_named_repo_is_found_under_a_configured_root(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("HERMES_CODE_ROOTS", str(tmp_path))
    assert cs.resolve_repo("demo") == repo.resolve()


def test_unknown_repo_names_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CODE_ROOTS", str(tmp_path))
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.resolve_repo("nope")
    assert str(tmp_path / "nope") in str(exc.value)


def test_empty_token_asks_for_a_repo():
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.resolve_repo("")
    assert "Name a repo" in str(exc.value)


# --------------------------------------------------------------------------- #
# session keys
# --------------------------------------------------------------------------- #

def test_key_is_stable_and_thread_scoped():
    a = cs.session_key(_source(chat="1", thread=None))
    b = cs.session_key(_source(chat="1", thread=None))
    c = cs.session_key(_source(chat="1", thread="7"))
    assert a == b
    assert a != c, "a forum topic must be its own terminal"


def test_key_is_the_same_whether_the_ids_arrive_as_ints_or_strings():
    """Two ingresses, one chat. A typed /code carries Telegram's numeric ids;
    a button tap carries the same ids as strings off the callback query. If the
    key does not coerce, "⏹ End" addresses a session that does not exist while
    the one it was rendered from stays open — and no single-ingress test sees it.
    """
    assert cs.session_key(_source(chat=777, thread=12)) == cs.session_key(
        _source(chat="777", thread="12")
    )
    # An empty string and a missing thread are the same absence, not two chats.
    assert cs.session_key(_source(chat="1", thread="")) == cs.session_key(
        _source(chat="1", thread=None)
    )


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #

def test_open_get_close_roundtrip(repo, _fake_backend):
    src = _source()
    sess = cs.open_session(src, str(repo))
    assert sess.repo == repo.resolve()
    assert _fake_backend[0].started_on == repo.resolve()
    assert cs.get(src) is sess

    closed = cs.close_session(src)
    assert closed is sess
    assert _fake_backend[0].closed is True
    assert cs.get(src) is None


def test_second_open_in_the_same_chat_is_refused(repo, _fake_backend):
    src = _source()
    cs.open_session(src, str(repo))
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.open_session(src, str(repo))
    assert "already open" in str(exc.value)


def test_a_dead_child_is_reaped_by_get(repo, _fake_backend):
    src = _source()
    cs.open_session(src, str(repo))
    _fake_backend[0]._alive = False
    assert cs.get(src) is None, "a dead child must not read as a live session"
    assert cs.active_count() == 0


def test_session_cap_is_enforced(repo, _fake_backend, monkeypatch):
    monkeypatch.setattr(cs, "MAX_SESSIONS", 1)
    cs.open_session(_source(chat="1"), str(repo))
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.open_session(_source(chat="2"), str(repo))
    assert "cap 1" in str(exc.value)


def test_idle_sessions_are_reaped(repo, _fake_backend, monkeypatch):
    monkeypatch.setattr(cs, "IDLE_TTL_S", 0.0)
    src = _source()
    sess = cs.open_session(src, str(repo))
    sess.last_turn_at = time.time() - 10
    reaped = cs.reap_idle()
    assert reaped == [sess.key]
    assert _fake_backend[0].closed is True


def test_close_all_leaves_no_children(repo, _fake_backend):
    cs.open_session(_source(chat="1"), str(repo))
    cs.open_session(_source(chat="2"), str(repo))
    assert cs.close_all() == 2
    assert cs.active_count() == 0
    assert all(b.closed for b in _fake_backend)


# --------------------------------------------------------------------------- #
# turns
# --------------------------------------------------------------------------- #

def test_a_turn_sends_only_the_new_message(repo, _fake_backend):
    """The ACP session holds its own history; replaying ours would double it."""
    src = _source()
    sess = cs.open_session(src, str(repo))
    asyncio.run(cs.run_turn(sess, "first"))
    asyncio.run(cs.run_turn(sess, "second"))
    assert _fake_backend[0].prompts == ["first", "second"]
    assert sess.turns == 2


def test_a_timed_out_turn_says_it_was_cut_off(repo, _fake_backend, monkeypatch):
    """A truncated turn presented as a finished one is how a killed child gets
    read as a completed task. It must say it was cut off, and say where to look."""
    monkeypatch.setattr(cs, "FIRST_TURN_TIMEOUT_S", 0.01)
    monkeypatch.setattr(cs, "TURN_TIMEOUT_S", 0.01)
    monkeypatch.setattr(cs, "TURN_GRACE_S", 0.05)
    src = _source()
    sess = cs.open_session(src, str(repo))
    # This backend ignores its own timeout — exactly the wedged-child case the
    # outer grace exists for.
    _fake_backend[0].delay = 2.0

    with pytest.raises(cs.CodingSessionError) as exc:
        asyncio.run(cs.run_turn(sess, "slow"))
    msg = str(exc.value)
    assert "cut off" in msg
    assert "git status" in msg
    assert sess.turns == 0, "a turn that never completed must not count as one"


@pytest.mark.parametrize("exc_type", [TimeoutError, subprocess.TimeoutExpired])
def test_a_backend_that_times_out_itself_gets_the_same_sentence(
    repo, _fake_backend, exc_type
):
    """The PRODUCTION path: the ACP client enforces its own timeout and raises,
    so asyncio.TimeoutError is never reached. Translating only the latter would
    surface a raw traceback for the commonest failure there is."""
    src = _source()
    sess = cs.open_session(src, str(repo))

    def _boom(prompt, *, timeout_s):
        if exc_type is subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(cmd="claude-agent-acp", timeout=timeout_s)
        raise exc_type("timed out")

    _fake_backend[0].turn = _boom
    with pytest.raises(cs.CodingSessionError) as exc:
        asyncio.run(cs.run_turn(sess, "slow"))
    assert "cut off" in str(exc.value)
    assert sess.turns == 0


def test_turns_are_serialised_per_session(repo, _fake_backend):
    """Two Telegram messages arriving together must not interleave into one child."""
    src = _source()
    sess = cs.open_session(src, str(repo))
    _fake_backend[0].delay = 0.05

    order: list[str] = []

    async def _drive():
        async def one(tag):
            await cs.run_turn(sess, tag)
            order.append(tag)
        await asyncio.gather(one("a"), one("b"))

    asyncio.run(_drive())
    assert sorted(order) == ["a", "b"]
    assert sess.turns == 2
    assert _fake_backend[0].prompts in (["a", "b"], ["b", "a"])


def test_empty_reply_is_labelled_not_silent(repo, _fake_backend):
    src = _source()
    sess = cs.open_session(src, str(repo))
    _fake_backend[0].reply = ""
    out = asyncio.run(cs.run_turn(sess, "x"))
    assert "returned nothing" in out


# --------------------------------------------------------------------------- #
# backends
# --------------------------------------------------------------------------- #

def test_claude_backend_forces_session_reuse_and_passes_no_tools(monkeypatch, repo):
    """Both are load-bearing:

    * reuse_session must be passed EXPLICITLY, not exported into the environment,
      or opening a coding session changes every other ACP client in the gateway.
    * a spawn-per-turn client re-pays ~30s of async MCP registration every message
      and intermittently runs with no tools at all.
    """
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kw):
            seen.update(kw)
            self.is_closed = False
            self._live = None

        def close(self):
            self.is_closed = True

    import agent.copilot_acp_client as acp
    monkeypatch.setattr(acp, "CopilotACPClient", _FakeClient)
    monkeypatch.setattr(cs.ClaudeAcpBackend, "binary", staticmethod(lambda: "/usr/local/bin/claude-agent-acp"))

    b = cs.ClaudeAcpBackend(mcp_hermes=False)
    b.start(repo)

    assert seen["reuse_session"] is True
    assert seen["acp_cwd"] == str(repo)
    assert seen["acp_args"] == [], "claude-agent-acp takes no flags and exits non-zero on --acp --stdio"
    assert "tools" not in seen


def test_claude_backend_says_so_when_the_binary_is_missing(monkeypatch, repo):
    monkeypatch.setattr(cs.ClaudeAcpBackend, "binary", staticmethod(lambda: None))
    b = cs.ClaudeAcpBackend()
    with pytest.raises(cs.CodingSessionError) as exc:
        b.start(repo)
    assert "claude-agent-acp" in str(exc.value)
    assert "npm i -g" in str(exc.value)


def test_unknown_backend_is_refused():
    with pytest.raises(cs.CodingSessionError):
        cs.build_backend("gpt")


# --------------------------------------------------------------------------- #
# pi backend
# --------------------------------------------------------------------------- #

def _pi_ok(stdout="changed a.py", rc=0, stderr=""):
    return subprocess.CompletedProcess(args=["pi"], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture
def pi(monkeypatch, repo):
    """A started PiBackend whose subprocess and git status are both fakes."""
    monkeypatch.setattr(cs.PiBackend, "binary", staticmethod(lambda: "/usr/local/bin/pi"))
    b = cs.PiBackend()
    b.start(repo)
    return b


@pytest.mark.parametrize("name", ["pi", "minimax", "PI"])
def test_pi_is_built_and_is_not_claude(name, monkeypatch):
    """A backend that silently substitutes another brain is undetectable, so the
    one thing that must never happen is `pi` returning a ClaudeAcpBackend."""
    monkeypatch.setattr(cs.PiBackend, "binary", staticmethod(lambda: "/usr/local/bin/pi"))
    b = cs.build_backend(name)
    assert isinstance(b, cs.PiBackend)
    assert b.name == "pi"


def test_pi_says_so_when_the_binary_is_missing(monkeypatch, repo):
    monkeypatch.setattr(cs.PiBackend, "binary", staticmethod(lambda: None))
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.PiBackend().start(repo)
    assert "not on PATH" in str(exc.value)
    assert "claude" in str(exc.value), "it must name the backend that still works"


def test_pi_refuses_an_unversioned_tree(monkeypatch, tmp_path):
    """A non-interactive executor with --approve writing where there is no diff
    to read leaves no audit trail at all."""
    monkeypatch.setattr(cs.PiBackend, "binary", staticmethod(lambda: "/usr/local/bin/pi"))
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(cs.CodingSessionError) as exc:
        cs.PiBackend().start(plain)
    assert "not a git repository" in str(exc.value)


def test_pi_argv_carries_the_three_load_bearing_flags(pi, monkeypatch):
    """`-ne` or pi never exits; `--approve` or it blocks on a prompt nobody can
    answer; `--no-session` or state leaks between two operators' chats."""
    seen: dict = {}

    def _run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return _pi_ok()

    monkeypatch.setattr(cs.subprocess, "run", _run)
    monkeypatch.setattr(cs.PiBackend, "_dirty", lambda self: set())
    pi.turn("add a docstring", timeout_s=42.0)

    assert seen["argv"][:3] == ["pi", "-p", "-ne"]
    assert "--approve" in seen["argv"]
    assert "--no-session" in seen["argv"]
    assert seen["kw"]["timeout"] == 42.0, "the turn cap must reach the child"
    assert seen["kw"]["cwd"] == str(pi._repo)
    assert "add a docstring" in seen["argv"][-1]


def test_pi_turn_timeout_is_not_swallowed(pi, monkeypatch):
    """`run_turn` owns the 'the turn was cut off' sentence. Catching the timeout
    here would hand back a partial work log that reads as a finished one."""
    def _run(argv, **kw):
        raise subprocess.TimeoutExpired(cmd="pi", timeout=kw.get("timeout", 1))

    monkeypatch.setattr(cs.subprocess, "run", _run)
    monkeypatch.setattr(cs.PiBackend, "_dirty", lambda self: set())
    with pytest.raises(subprocess.TimeoutExpired):
        pi.turn("do a thing", timeout_s=1.0)


def test_pi_needs_no_warmup_and_says_it_has_no_memory():
    """The warm-up exists to absorb ~30s of ACP MCP registration. On a one-shot
    executor it is a metered call that answers READY and edits nothing."""
    assert cs.PiBackend.needs_warmup is False
    assert cs.ClaudeAcpBackend.needs_warmup is True
    assert "no memory" in cs.PiBackend.note


def test_pi_note_is_on_every_status_line_not_just_the_banner(repo, monkeypatch):
    monkeypatch.setattr(cs.PiBackend, "binary", staticmethod(lambda: "/usr/local/bin/pi"))
    sess = cs.open_session(_source(), str(repo), "pi")
    assert "no memory" in sess.status_line()


def test_pi_reports_success_with_no_files_as_unproven(pi, monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", lambda argv, **kw: _pi_ok("all done!"))
    monkeypatch.setattr(cs.PiBackend, "_dirty", lambda self: set())
    out = pi.turn("do nothing", timeout_s=5)
    assert "changed NO files" in out
    assert "unproven" in out


def test_pi_surfaces_a_nonzero_exit_rather_than_the_log_alone(pi, monkeypatch):
    monkeypatch.setattr(
        cs.subprocess, "run",
        lambda argv, **kw: _pi_ok("partial work", rc=2, stderr="boom"),
    )
    monkeypatch.setattr(cs.PiBackend, "_dirty", lambda self: set())
    out = pi.turn("break something", timeout_s=5)
    assert "exited 2" in out
    assert "unfinished" in out
    assert "boom" in out


# ---- the founder fence ----------------------------------------------------- #

@pytest.mark.parametrize(
    "instruction",
    [
        "update the stripe webhook handler",
        "fix pricing.py",
        "add a field to store_platform/src/Store.Api/Auth/TokenService.cs",
        "write an alembic migration",
        "rename CheckoutEndpoints",  # no trailing \b: CamelCase gives no non-word char
    ],
)
def test_fence_refuses_money_identity_contract_migration_instructions(pi, instruction):
    with pytest.raises(cs.CodingSessionError) as exc:
        pi.turn(instruction, timeout_s=5)
    assert "Founder fence" in str(exc.value)
    assert "never leaves Claude" in str(exc.value)


@pytest.mark.parametrize(
    "ok",
    [
        "restyle the storefront facet bar",
        "add a screenshot harness for store_platform",
        "fix the catalogue card spacing",
    ],
)
def test_fence_does_not_refuse_ordinary_work(pi, monkeypatch, ok):
    """A fence that blocks work it was never meant to block gets routed around by
    hand, which is how it stops being a fence. Banning the `store_platform/`
    directory refused 414 files in order to protect roughly 40."""
    monkeypatch.setattr(cs.subprocess, "run", lambda argv, **kw: _pi_ok("ok"))
    monkeypatch.setattr(cs.PiBackend, "_dirty", lambda self: set())
    assert cs.fence_violations(ok) == []
    assert "Founder fence" not in pi.turn(ok, timeout_s=5)


def test_fence_runs_again_on_what_git_says_was_written(pi, monkeypatch):
    """THE fence claim. The pre-check reads only prose: 'update the payment
    provider adapter' contains no fenced token, so it passes — and the executor is
    then free to write StripeProvider.cs unchecked. git cannot be talked around.
    """
    instruction = "update the payment provider adapter"
    assert cs.fence_violations(instruction) == [], "the prose check must genuinely pass here"

    written = "store_platform/src/Store.Api/Payments/StripeProvider.cs"
    calls = {"n": 0}

    def _dirty(self):
        calls["n"] += 1
        return set() if calls["n"] == 1 else {written}

    monkeypatch.setattr(cs.subprocess, "run", lambda argv, **kw: _pi_ok("edited it"))
    monkeypatch.setattr(cs.PiBackend, "_dirty", _dirty)
    out = pi.turn(instruction, timeout_s=5)

    assert "FENCE BREACH" in out
    assert written in out
    assert "git -C" in out and "checkout --" in out, "the revert must be one paste away"


def test_a_fence_breach_is_flagged_not_auto_reverted(pi, monkeypatch):
    """A revert would also destroy the legitimate half of the same run. A silent
    destructive action is worse than an unmissable flag."""
    seen = []

    def _run(argv, **kw):
        seen.append(argv)
        return _pi_ok("edited it")

    calls = {"n": 0}

    def _dirty(self):
        calls["n"] += 1
        return set() if calls["n"] == 1 else {"src/Auth/Login.cs"}

    monkeypatch.setattr(cs.subprocess, "run", _run)
    monkeypatch.setattr(cs.PiBackend, "_dirty", _dirty)
    pi.turn("tidy the login screen copy", timeout_s=5)

    assert len(seen) == 1, "exactly the executor ran — nothing reverted anything"
    assert all("checkout" not in str(a) for a in seen)


def test_fence_has_not_drifted_from_the_pi_bridge():
    """The patterns are COPIED from ~/.claude/mcp/pi_bridge.py rather than
    imported, so the gateway cannot fail open (or fail to start) because a file
    outside this repo moved. This is the half a 'keep these in sync' comment
    cannot do. Skipped where the bridge is not installed — CI has no estate.
    """
    bridge = Path.home() / ".claude" / "mcp" / "pi_bridge.py"
    if not bridge.exists():
        pytest.skip("pi_bridge.py not installed on this box")
    import ast

    tree = ast.parse(bridge.read_text(encoding="utf-8"))
    theirs = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "FENCE_PATTERNS" for t in node.targets
        ):
            theirs = ast.literal_eval(node.value)
            break
    assert theirs is not None, "pi_bridge.py no longer declares FENCE_PATTERNS"
    assert sorted(cs.FENCE_PATTERNS) == sorted(theirs), (
        "the coding-session fence has drifted from the pi bridge's; "
        "reconcile them deliberately, in both files"
    )


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #

def test_code_is_registered_and_gateway_dispatchable():
    from hermes_cli.commands import resolve_command
    d = resolve_command("code")
    assert d is not None and d.name == "code"
    assert d.gateway_only is True
    assert resolve_command("cc").name == "code"


def test_code_is_on_the_operator_menu_and_within_the_cap():
    from gateway.operator_shell.menu import OPERATOR_TELEGRAM_MENU
    from gateway.platforms.telegram import MAX_COMMANDS_PER_SCOPE
    assert "code" in OPERATOR_TELEGRAM_MENU
    assert len(OPERATOR_TELEGRAM_MENU) <= MAX_COMMANDS_PER_SCOPE


def test_code_has_a_dispatch_arm_and_a_handler():
    run_src = Path(__file__).resolve().parents[3] / "gateway" / "run.py"
    slash_src = Path(__file__).resolve().parents[3] / "gateway" / "slash_commands.py"
    assert 'canonical == "code"' in run_src.read_text()
    assert "async def _handle_code_command" in slash_src.read_text()
    assert "async def _run_coding_turn" in slash_src.read_text()


def test_an_open_session_outranks_the_natural_ops_matcher():
    """THE wiring claim. `match_natural_op` is ~50 loose phrase patterns that run
    on plain Telegram text. If it runs first, an open coding session silently
    loses the operator's most ordinary instructions ("run the tests") to a panel.
    """
    run_src = (Path(__file__).resolve().parents[3] / "gateway" / "run.py").read_text()
    intercept = run_src.index("_coding.get(source)")
    natural = run_src.index("match_natural_op(_raw_text)")
    assert intercept < natural, (
        "the coding-session interception must precede match_natural_op; "
        "an explicitly-opened session outranks a phrase heuristic"
    )


def test_an_open_session_also_outranks_the_pre_dispatch_plugin_hook():
    """The otto-inbound plugin answers `pre_gateway_dispatch` with action=skip and
    `_handle_message` returns None ~1700 lines BEFORE the coding-session
    interception. Winning the natural_ops race is not enough on its own.
    """
    run_src = (Path(__file__).resolve().parents[3] / "gateway" / "run.py").read_text()
    bypass = run_src.index("_coding_session_active")
    hook = run_src.index('_invoke_hook(\n                    "pre_gateway_dispatch"')
    assert bypass < hook, "the bypass must be computed before the hook is invoked"
    assert "not _coding_session_active" in run_src, "the hook must actually be gated on it"
    # Slash commands must survive mid-session or /code end becomes unreachable.
    assert 'startswith("/")' in run_src[bypass:hook]


def test_close_words_cover_the_obvious_ones():
    for w in ("end", "exit", "quit", "/end"):
        assert w in cs.CLOSE_WORDS
