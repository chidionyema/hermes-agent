"""Held coding-agent sessions — the Telegram chat becomes the terminal.

One `CodingSession` per (platform, chat, thread). While one is open, plain text in
that chat is a TURN for the coding agent, not a message for the Hermes brain.

Why a HELD session and not a spawn per turn: MCP tool registration over ACP is
asynchronous and was measured at ~30s (`docs/CLAUDE_CLI_BRAIN.md`). A fresh child
per turn pays that on every message and, worse, intermittently runs with NO tools
at all — the agent then answers from nothing and looks like it hallucinated.
Holding the process and the sessionId is the whole point: measured 17.1s spawning
turn 1 vs 4.6s on turn 2 against the same pid + sessionId.

Backends are pluggable behind `Backend` so a second flavour (pi) is one class, not
a second copy of the session machinery. The two are NOT interchangeable and the
difference is stated to the operator rather than hidden: the Claude backend holds
a real conversation, `pi` is one non-interactive shot per turn and starts cold
every time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tunables. Every one of these is a liability rail, not a preference.
# --------------------------------------------------------------------------- #

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


# A coding turn edits files and runs tests; it is NOT a chat completion. A tight
# cap here is the exact defect that produced fabricated "done" reports before —
# the child was killed mid-work and the partial text read as a finished answer.
TURN_TIMEOUT_S = _env_float("HERMES_CODE_TURN_TIMEOUT", 600.0)
# The FIRST turn additionally absorbs async MCP registration (~30s measured).
FIRST_TURN_TIMEOUT_S = _env_float("HERMES_CODE_FIRST_TURN_TIMEOUT", TURN_TIMEOUT_S + 60.0)
# Backstop above the backend's OWN timeout, for a backend that fails to honour it.
# Normally the backend times out first and raises; this only catches a wedged child.
TURN_GRACE_S = _env_float("HERMES_CODE_TURN_GRACE", 30.0)
# Each open session holds a live child process. Idle ones are reaped.
IDLE_TTL_S = _env_float("HERMES_CODE_IDLE_TTL", 1800.0)
# Two concurrent coding agents is already two full toolchains on one laptop.
MAX_SESSIONS = _env_int("HERMES_CODE_MAX_SESSIONS", 2)

# Words that close a session from inside it, so the operator never has to recall
# a slash command mid-flow.
CLOSE_WORDS = {"/end", "end", "exit", "quit", "done", "/quit", "/exit", "end session"}


# The pi executor is a metered third-party brain writing to the tree. A cheap
# executor with a 900s default would spend for 15 minutes on a phone message.
PI_MODEL = os.getenv("HERMES_CODE_PI_MODEL", "").strip() or "minimax/MiniMax-M3"
# How much of pi's own report reaches Telegram. Its stdout is a full work log.
PI_REPORT_CHARS = _env_int("HERMES_CODE_PI_REPORT_CHARS", 2500)


class CodingSessionError(RuntimeError):
    """Anything the operator needs told in plain words."""


# --------------------------------------------------------------------------- #
# Founder fence — money rail / identity / contract / migrations never leave Claude
# --------------------------------------------------------------------------- #
#
# `/code prospector pi` puts MiniMax at a shell in a real repo with `--approve`.
# The estate's standing rule is that money-rail, identity, contract and migration
# work never leaves Claude, and the pi MCP bridge enforces it in code rather than
# in prose. This surface needs the same fence for the same reason, so the pattern
# list is COPIED from `~/.claude/mcp/pi_bridge.py` rather than imported: the
# gateway must not fail open (or fail to start) because a file outside the repo
# moved. `test_fence_has_not_drifted_from_the_pi_bridge` reads that file when it
# is present and fails on any divergence, which is the half that cannot be
# achieved by writing "keep these in sync" in a comment.
#
# Two properties of the patterns are deliberate and were each paid for:
#   * They name the money SURFACE, not its parent directory. Banning a directory
#     refused 414 files to protect ~40, and a fence that blocks work it was never
#     meant to block gets routed around by hand — which deletes it.
#   * No TRAILING \b on the word patterns: it needs a non-word character, and
#     CamelCase never gives one, so `\bcheckout\b` silently missed
#     CheckoutEndpoints.cs. Leading \b only.
FENCE_PATTERNS = [
    # the money rail proper
    r"\bbridge\.py\b",
    r"\bpricing\.py\b",
    r"/Payments?/",
    r"\bstripe",
    r"\bpaddle",
    r"\bcheckout",
    r"\bwebhook",
    r"\bentitlement",
    r"PackPrice",
    r"MoneyRail",
    # identity
    r"/Auth/",
    r"/Identity/",
    # contract
    r"/Contracts?/",
    # migrations
    r"\bmigrations?/",
    r"\balembic\b",
]
FENCE_RE = re.compile("|".join(FENCE_PATTERNS), re.IGNORECASE)


def fence_violations(text: str) -> list[str]:
    """Fenced tokens in an operator's instruction. A prose check, so evadable."""
    return sorted({m.group(0) for m in FENCE_RE.finditer(text or "")})


def fenced_paths(paths: list[str]) -> list[str]:
    """The subset of `paths` on fenced surface. Exact, unlike the prose check.

    The pre-check reads only what the operator TYPED, and "update the payment
    provider adapter" contains no fenced token — so it passes, and the executor
    is then free to write StripeProvider.cs unchecked. git cannot be talked
    around, which is why the fence runs a second time on what was written.
    """
    return [p for p in paths if FENCE_RE.search(p)]


# --------------------------------------------------------------------------- #
# Repo resolution
# --------------------------------------------------------------------------- #

def _search_roots() -> list[Path]:
    raw = os.getenv("HERMES_CODE_ROOTS", "").strip()
    if raw:
        return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    home = Path.home()
    return [home / "Documents" / "code", home / ".hermes", home]


def _is_git_repo(path: Path) -> bool:
    # In a git WORKTREE `.git` is a FILE containing `gitdir:`, not a directory.
    # Testing is_dir() here would reject every worktree.
    return (path / ".git").exists()


def resolve_repo(token: str) -> Path:
    """Turn an operator's word into a git repo path, or say why it can't."""
    token = (token or "").strip()
    if not token:
        raise CodingSessionError("Name a repo: `/code prospector`")

    candidate = Path(token).expanduser()
    tried: list[str] = []
    if candidate.is_absolute() or token.startswith("."):
        if candidate.is_dir() and _is_git_repo(candidate):
            return candidate.resolve()
        tried.append(str(candidate))
    else:
        for root in _search_roots():
            probe = root / token
            if probe.is_dir() and _is_git_repo(probe):
                return probe.resolve()
            tried.append(str(probe))

    raise CodingSessionError(
        "No git repo called `{}`. Looked in:\n{}".format(
            token, "\n".join(f"  · {t}" for t in tried[:6])
        )
    )


def repo_head(repo: Path) -> str:
    """Short SHA + branch, or '?' — never raises, this is only a status line."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = out.stdout.strip() or "?"
        br = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        branch = br.stdout.strip()
        return f"{branch}@{sha}" if branch else sha
    except Exception:
        return "?"


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

class Backend(Protocol):
    name: str
    # Whether opening a session should burn a throwaway turn to warm the child.
    # It is worth ~30s of MCP registration on ACP and worth nothing — a paid call
    # that answers "READY" and edits nothing — on a one-shot executor.
    needs_warmup: bool
    # One clause for the status line, when the backend behaves unlike the other.
    note: str

    def start(self, repo: Path) -> None: ...
    def turn(self, prompt: str, *, timeout_s: float) -> str: ...
    def alive(self) -> bool: ...
    def close(self) -> None: ...


class ClaudeAcpBackend:
    """Claude Code driven over ACP, holding one process + sessionId.

    Two things here are load-bearing and were each proven by them failing:

    * `reuse_session=True` is passed explicitly, NOT via the environment, so
      opening a coding session cannot change the behaviour of every other ACP
      client in the gateway process.
    * No `tools=` is ever passed. Claude Code checks its REAL tool list and
      refuses to emit a prose `<tool_call>` block; leaving tool specs in the
      prompt is itself what triggers the refusal. Its tools arrive as genuine
      MCP servers via `session/new`, which is what `mcp_servers` is for.
    """

    name = "claude"
    needs_warmup = True
    note = ""

    def __init__(self, *, mcp_hermes: bool | None = None) -> None:
        self._client: Any = None
        self._repo: Path | None = None
        if mcp_hermes is None:
            mcp_hermes = os.getenv(
                "HERMES_CODE_MCP_HERMES", ""
            ).strip().lower() in {"1", "true", "yes", "on"}
        self._mcp_hermes = bool(mcp_hermes)

    @staticmethod
    def binary() -> str | None:
        return shutil.which("claude-agent-acp")

    def start(self, repo: Path) -> None:
        from agent.copilot_acp_client import CopilotACPClient, _hermes_mcp_server

        command = self.binary()
        if not command:
            raise CodingSessionError(
                "`claude-agent-acp` is not on PATH. Install it with:\n"
                "  `npm i -g @agentclientprotocol/claude-agent-acp`"
            )
        servers = [_hermes_mcp_server()] if self._mcp_hermes else []
        self._repo = repo
        self._client = CopilotACPClient(
            acp_command=command,
            # claude-agent-acp takes NO flags and exits non-zero on `--acp --stdio`.
            acp_args=[],
            acp_cwd=str(repo),
            mcp_servers=servers,
            reuse_session=True,
        )

    def turn(self, prompt: str, *, timeout_s: float) -> str:
        if self._client is None:
            raise CodingSessionError("Session was never started.")
        # ONE message, not the accumulated history: the ACP session is a real
        # Claude Code session that already holds its own conversation. Replaying
        # history here would send every earlier turn again on every message.
        resp = self._client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_s,
        )
        return (resp.choices[0].message.content or "").strip()

    def alive(self) -> bool:
        client = self._client
        if client is None or getattr(client, "is_closed", False):
            return False
        live = getattr(client, "_live", None)
        if live is None:
            # No turn taken yet — the child is spawned lazily on the first prompt.
            return True
        proc = getattr(live, "process", None)
        return proc is not None and proc.poll() is None

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.warning("ACP client close failed", exc_info=True)


class PiBackend:
    """The cheap executor: `pi -p -ne` on MiniMax, one non-interactive shot per turn.

    There is NO held child here, and that is not an omission of the session
    machinery — `pi -p` is non-interactive and is run `--no-session` deliberately.
    Each turn therefore starts COLD: it sees the repo, never the conversation. A
    backend that quietly forgot the last three turns underneath a transcript that
    looks continuous is indistinguishable from an agent ignoring the operator, so
    `note` says it on the open banner and on every status line. Give it whole,
    self-contained instructions; use `claude` when the work needs a conversation.

    Three flags are load-bearing, each measured rather than chosen:

    * `-ne` (no extensions) — without it pi completes the edit and then never
      exits (observed: work done, process still alive at a 300s timeout). With
      it: exit 0 in 7s. A held-open child would eat the turn cap every time.
    * `--no-session` — nothing to resume, and no session state to leak between
      two operators' chats.
    * `--approve` — a non-interactive run has nobody to answer the edit prompt,
      so without it the executor blocks until the timeout and reports nothing.

    The founder fence runs twice: once on what the operator typed, and again on
    what git says was actually written. The second is the one that cannot be
    talked around. A breach is FLAGGED, never auto-reverted — a revert would also
    destroy the legitimate half of the same run, and a silent destructive action
    is worse than an unmissable flag when the next step is reading the diff.
    """

    name = "pi"
    needs_warmup = False
    note = "no memory between turns — send whole instructions"

    def __init__(self, *, model: str | None = None) -> None:
        self._repo: Path | None = None
        self._model = (model or PI_MODEL).strip()
        self._closed = False

    @staticmethod
    def binary() -> str | None:
        return shutil.which("pi")

    def start(self, repo: Path) -> None:
        if not self.binary():
            raise CodingSessionError(
                "`pi` is not on PATH. The pi backend shells out to the pi CLI; "
                "use `/code <repo> claude` instead."
            )
        # A git repo is the only thing that makes a non-interactive executor's
        # writes reviewable and reversible. `resolve_repo` already requires one;
        # this is the check at the point of USE, so a backend built by hand in a
        # test or a future caller cannot skip it.
        if not _is_git_repo(repo):
            raise CodingSessionError(
                f"`{repo}` is not a git repository. Refusing to let a "
                "non-interactive executor write to an unversioned tree — the "
                "diff is the entire audit trail."
            )
        self._repo = repo

    def _dirty(self) -> set[str]:
        """Paths git already considers dirty. Never raises — this is bookkeeping."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self._repo), "status", "--porcelain"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            return set()
        return {ln[3:] for ln in out.stdout.splitlines() if len(ln) > 3}

    def turn(self, prompt: str, *, timeout_s: float) -> str:
        if self._repo is None or self._closed:
            raise CodingSessionError("Session was never started.")

        hits = fence_violations(prompt)
        if hits:
            raise CodingSessionError(
                "⛔ Founder fence — that instruction touches money-rail / identity "
                f"/ contract / migration surface: {', '.join(hits)}\n"
                "That work never leaves Claude. Open a `claude` session for it."
            )

        before = self._dirty()
        argv = [
            "pi", "-p", "-ne",
            "--provider", self._model.split("/", 1)[0],
            "--model", self._model.split("/", 1)[-1],
            "--no-session",
            "--approve",
            "--no-context-files",
            "You are the EXECUTOR working in this repository.\n"
            "Implement exactly what is asked — do NOT redesign, do NOT expand scope.\n"
            "Match the surrounding code's style, naming and comment density.\n"
            "Read every file before you edit it. If the instruction is ambiguous or\n"
            "wrong, STOP and report the ambiguity instead of guessing.\n"
            "Report every file you changed and what you changed in it. If you skipped\n"
            "part of it or something did not work, say so explicitly — a verifier\n"
            "checks your work.\n\n"
            "=== INSTRUCTION ===\n" + prompt,
        ]
        # No `except TimeoutExpired` here on purpose: `run_turn` translates it into
        # the one sentence that says the turn was CUT OFF and points at git status.
        # Swallowing it here would hand back a partial log that reads as finished.
        proc = subprocess.run(
            argv, cwd=str(self._repo), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=timeout_s,
        )

        after = self._dirty()
        touched = sorted(after - before)
        return self._report(proc, touched)

    def _report(self, proc: subprocess.CompletedProcess, touched: list[str]) -> str:
        head: list[str] = []
        breaches = fenced_paths(touched)
        if breaches:
            head.append(
                "🚨 FENCE BREACH — the executor wrote to money-rail / identity / "
                "contract / migration surface:\n"
                + "\n".join(f"  · {p}" for p in breaches)
                + "\nThe instruction named none of it, so the pre-check could not "
                "see it. Review these line by line before anything else, and "
                "revert unless you are certain:\n"
                f"  `git -C {self._repo} checkout -- {' '.join(breaches)}`"
            )
        if proc.returncode != 0:
            head.append(f"⚠️ pi exited {proc.returncode} — treat the report below as unfinished.")
        if proc.returncode == 0 and not touched:
            # An executor that reports success and changed nothing is the single
            # commonest way a "done" report is false.
            head.append("⚠️ pi reported success but changed NO files. Its report is unproven.")

        body = (proc.stdout or "").strip()
        if len(body) > PI_REPORT_CHARS:
            body = body[:PI_REPORT_CHARS] + "\n… (report truncated)"
        files = "\n".join(f"  · {p}" for p in touched) if touched else "  (none)"
        tail = f"\n--- files touched ---\n{files}"
        if proc.returncode != 0 and (proc.stderr or "").strip():
            tail += "\n--- stderr ---\n" + "\n".join(proc.stderr.strip().splitlines()[-10:])
        return "\n\n".join([*head, body or "_(pi printed nothing)_"]) + tail

    def alive(self) -> bool:
        # There is no child to outlive the turn, so "alive" means "still open".
        # The idle reaper is what ends a pi session; `get()`'s dead-child sweep
        # has nothing to sweep here.
        return not self._closed and self._repo is not None

    def close(self) -> None:
        self._closed = True


def build_backend(name: str) -> Backend:
    name = (name or "claude").strip().lower()
    if name in {"claude", "cc", "claude-code"}:
        return ClaudeAcpBackend()
    if name in {"pi", "minimax"}:
        return PiBackend()
    raise CodingSessionError(f"Unknown backend `{name}`. Known: claude, pi")


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

@dataclass
class CodingSession:
    key: str
    repo: Path
    backend: Backend
    opened_at: float = field(default_factory=time.time)
    last_turn_at: float = field(default_factory=time.time)
    turns: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def idle_s(self) -> float:
        return time.time() - self.last_turn_at

    def alive(self) -> bool:
        return self.backend.alive()

    def status_line(self) -> str:
        state = "🟢" if self.alive() else "🔴"
        line = (
            f"{state} `{self.repo.name}` @ {repo_head(self.repo)} · "
            f"{self.backend.name} · {self.turns} turn(s) · "
            f"idle {int(self.idle_s)}s"
        )
        # A backend that behaves unlike the other says so EVERY time, not once on
        # open: the operator reads this line hours later, having forgotten which
        # flavour they opened.
        note = getattr(self.backend, "note", "")
        return f"{line}\n_{note}_" if note else line


_SESSIONS: dict[str, CodingSession] = {}


def session_key(source: Any) -> str:
    """Stable per-conversation key. A forum topic is its own terminal."""
    platform = getattr(getattr(source, "platform", None), "value", None) or str(
        getattr(source, "platform", "?")
    )
    chat = getattr(source, "chat_id", "?")
    thread = getattr(source, "thread_id", None)
    return f"code:{platform}:{chat}:{thread or '-'}"


def get(source: Any) -> CodingSession | None:
    """The live session for this chat, reaping it if its child died."""
    key = session_key(source)
    sess = _SESSIONS.get(key)
    if sess is None:
        return None
    if not sess.alive():
        logger.info("coding session %s: child is gone — dropping it", key)
        _SESSIONS.pop(key, None)
        try:
            sess.backend.close()
        except Exception:
            pass
        return None
    return sess


def open_session(source: Any, repo_token: str, backend_name: str = "claude") -> CodingSession:
    key = session_key(source)
    if get(source) is not None:
        raise CodingSessionError(
            "A session is already open here. `/code end` first, or just say `end`."
        )
    reap_idle()
    if len(_SESSIONS) >= MAX_SESSIONS:
        raise CodingSessionError(
            f"{len(_SESSIONS)} coding session(s) already open (cap {MAX_SESSIONS}). "
            "Close one first."
        )
    repo = resolve_repo(repo_token)
    backend = build_backend(backend_name)
    backend.start(repo)
    sess = CodingSession(key=key, repo=repo, backend=backend)
    _SESSIONS[key] = sess
    logger.info("coding session %s opened on %s (%s)", key, repo, backend.name)
    return sess


def close_session(source: Any) -> CodingSession | None:
    key = session_key(source)
    sess = _SESSIONS.pop(key, None)
    if sess is not None:
        try:
            sess.backend.close()
        except Exception:
            logger.warning("closing coding session %s failed", key, exc_info=True)
        logger.info("coding session %s closed after %d turn(s)", key, sess.turns)
    return sess


def reap_idle() -> list[str]:
    """Close sessions past their TTL. Each one holds a real child process."""
    reaped = []
    for key, sess in list(_SESSIONS.items()):
        if sess.idle_s > IDLE_TTL_S or not sess.alive():
            _SESSIONS.pop(key, None)
            try:
                sess.backend.close()
            except Exception:
                pass
            reaped.append(key)
    if reaped:
        logger.info("reaped %d idle coding session(s): %s", len(reaped), reaped)
    return reaped


def close_all() -> int:
    """Gateway shutdown: never leave orphaned agent children behind."""
    n = len(_SESSIONS)
    for key in list(_SESSIONS):
        sess = _SESSIONS.pop(key, None)
        if sess is not None:
            try:
                sess.backend.close()
            except Exception:
                pass
    return n


def active_count() -> int:
    return len(_SESSIONS)


async def run_turn(sess: CodingSession, prompt: str) -> str:
    """One operator message → one agent turn. Serialised per session.

    The lock matters: an ACP session is inherently sequential, and two Telegram
    messages arriving together would otherwise interleave into one child.
    """
    async with sess.lock:
        timeout = FIRST_TURN_TIMEOUT_S if sess.turns == 0 else TURN_TIMEOUT_S
        started = time.time()
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(sess.backend.turn, prompt, timeout_s=timeout),
                timeout=timeout + TURN_GRACE_S,
            )
        # The backend normally enforces `timeout` itself and raises its own flavour
        # of timeout; asyncio.TimeoutError is only reached when it failed to. Both
        # mean the same thing to the operator, so both get the same sentence.
        except (asyncio.TimeoutError, TimeoutError, subprocess.TimeoutExpired):
            # Say the turn was cut off. A truncated answer presented as a finished
            # one is how a killed child gets read as a completed task.
            raise CodingSessionError(
                f"⏱ Turn exceeded {int(timeout)}s and was cut off. The agent may have "
                f"left partial edits in `{sess.repo.name}` — check `git status` there. "
                "The session is still open; say `status` or `end`."
            )
        finally:
            sess.last_turn_at = time.time()
        sess.turns += 1
        logger.info(
            "coding session %s turn %d took %.1fs", sess.key, sess.turns, time.time() - started
        )
        return text or "_(the agent returned nothing)_"
