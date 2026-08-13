"""The tappable door to `/code`. Nothing here should need to be remembered.

`/code prospector pi` is three things to recall: that the command exists, what
the repo is called, and that a second word picks the brain. On a phone that is
two too many. This module renders the same surface as buttons:

    /code           ->  a repo picker, one button per git repo actually on disk
    (tap a repo)    ->  the session opens, and its card carries End / Status / Diff
    /code           ->  while open, that card again

The picker is DERIVED from the filesystem, never a hand-maintained list — the
estate has been bitten repeatedly by hand-maintained tables drifting from the
thing they describe, and a repo list is exactly that shape.

Every callback is `estate:code:<verb>[:arg]`, so it rides the cockpit's existing
authorisation, ack-within-15s and card-edit machinery rather than growing a
second callback namespace beside it. `test_every_button_dispatches` then covers
these buttons for free, which a `code:` namespace of its own would not have been.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, List, Tuple

from gateway.operator_shell import coding_session as cs

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

# A phone keyboard, not a directory listing. Beyond this the picker stops being
# faster than typing the name.
MAX_REPOS = 10
# Buttons per row. Repo names are long; two is the point where they start eliding.
REPOS_PER_ROW = 2

_BACKEND_LABELS = {"claude": "🧠 claude", "pi": "⚡ pi"}


def discover_repos(limit: int = MAX_REPOS) -> list[Path]:
    """Git repos directly under the configured search roots.

    One level deep on purpose: `~/Documents/code` holds the repos, and recursing
    would surface every vendored checkout and every `.claude/worktrees/*` copy —
    an estate-wide recursive walk here was measured at 169k files elsewhere.
    """
    seen: dict[str, Path] = {}
    for root in cs._search_roots():
        try:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if child.name.startswith("."):
                    continue
                if not child.is_dir() or not cs._is_git_repo(child):
                    continue
                seen.setdefault(child.name, child)
                if len(seen) >= limit:
                    return list(seen.values())
        except OSError:
            # An unreadable root is a fact about one root, not a reason to render
            # nothing — the other roots still have the repo the operator wants.
            logger.debug("coding picker: cannot read %s", root, exc_info=True)
    return list(seen.values())


def render_picker(backend: str = "claude") -> Tuple[str, List[ButtonRow]]:
    """The no-recall door: pick a repo, tap it, you are in."""
    backend = backend if backend in _BACKEND_LABELS else "claude"
    repos = discover_repos()

    lines = ["🛠 *Code*", ""]
    if not repos:
        roots = "\n".join(f"  · `{r}`" for r in cs._search_roots())
        lines += [
            "No git repos found. Looked in:",
            roots,
            "",
            "_Set `HERMES_CODE_ROOTS` to point it somewhere else._",
        ]
        return "\n".join(lines), [[("🔄 Refresh", "estate:code")]]

    lines += [
        f"Brain: *{_BACKEND_LABELS[backend]}*",
        "",
        "Tap a repo to open a session. Every message after that is a turn;"
        " say `end` to close it.",
    ]
    if backend == "pi":
        lines += ["", "⚠️ _pi has no memory between turns — send whole instructions._"]

    rows: List[ButtonRow] = []
    row: ButtonRow = []
    for repo in repos:
        row.append((f"📁 {repo.name}", f"estate:code:open:{backend}:{repo.name}"))
        if len(row) == REPOS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # The brain switch re-renders the picker rather than opening anything, so the
    # operator can see which one the next tap will use BEFORE spending on it.
    other = "pi" if backend == "claude" else "claude"
    rows.append([(f"Switch to {_BACKEND_LABELS[other]}", f"estate:code:pick:{other}")])
    return "\n".join(lines), rows


def controls() -> List[ButtonRow]:
    """The three things an operator wants mid-session, none of them typed."""
    return [[
        ("⏹ End", "estate:code:end"),
        ("🔄 Status", "estate:code:status"),
        ("📊 Diff", "estate:code:diff"),
    ]]


def render_session(source: Any) -> Tuple[str, List[ButtonRow]]:
    """The open session's card, or the picker when there is nothing open here."""
    sess = cs.get(source)
    if sess is None:
        return render_picker()
    others = cs.active_count() - 1
    text = sess.status_line()
    if others > 0:
        text += f"\n_{others} more open in other chats._"
    return text, controls()


def render_diff(source: Any) -> Tuple[str, List[ButtonRow]]:
    """What the agent has actually changed — from git, never from its own report.

    An executor's "I updated three files" is a claim. This is the receipt, and it
    is the whole reason a coding session is only allowed to run inside a repo.
    """
    sess = cs.get(source)
    if sess is None:
        return render_picker()

    def _git(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(sess.repo), *args],
                capture_output=True, text=True, timeout=20, env=cs.git_env(),
            )
            return out.stdout.strip()
        except Exception:
            logger.warning("coding diff: git %s failed", args, exc_info=True)
            return ""

    status = _git("status", "--porcelain")
    stat = _git("diff", "--stat")
    lines = [f"📊 *{sess.repo.name}* @ {cs.repo_head(sess.repo)}", ""]
    if not status:
        lines.append("_Working tree clean — nothing has been changed._")
    else:
        changed = status.splitlines()
        lines.append(f"*{len(changed)} path(s) changed*")
        lines.append("```text")
        lines += changed[:25]
        if len(changed) > 25:
            lines.append(f"… +{len(changed) - 25} more")
        lines.append("```")
        if stat:
            lines += ["```text", *stat.splitlines()[-12:], "```"]
        # Nothing here commits. A coding session that committed for you would put
        # an unreviewed executor's work into history behind a button tap.
        lines.append("_Nothing is committed. Review and commit yourself._")
    return "\n".join(lines), controls()


def handle(arg: str, source: Any) -> Tuple[str, List[ButtonRow], str, bool]:
    """Dispatch `estate:code[:verb[:args]]`. Returns (text, buttons, toast, ok).

    `source` is threaded down from the callback because a coding session is keyed
    on (platform, chat, thread) — a panel renderer that only knew the action could
    not tell two operators' sessions apart, and would happily close someone else's.
    """
    parts = [p for p in (arg or "").split(":") if p]
    verb = parts[0].lower() if parts else ""

    if source is None and verb in {"open", "end", "status", "diff"}:
        # Reachable from a hand-typed callback or a caller that has not been
        # threaded yet. Say so rather than acting on the wrong chat.
        return (
            "⚠️ This action needs to know which chat it is for.\n"
            "Use `/code` in the chat you want the session in.",
            [], "Needs a chat", False,
        )

    if verb == "pick":
        backend = parts[1] if len(parts) > 1 else "claude"
        text, buttons = render_picker(backend)
        return text, buttons, "Brain", True

    if verb == "open":
        backend = parts[1] if len(parts) > 1 else "claude"
        token = ":".join(parts[2:]) if len(parts) > 2 else ""
        try:
            sess = cs.open_session(source, token, backend)
        except cs.CodingSessionError as exc:
            text, buttons = render_picker(backend)
            return f"⚠️ {exc}\n\n{text}", buttons, "Could not open", False
        except Exception as exc:
            logger.exception("opening coding session from a button failed")
            text, buttons = render_picker(backend)
            return (
                f"⚠️ Could not open: {type(exc).__name__}: {exc}\n\n{text}",
                buttons, "Could not open", False,
            )
        note = getattr(sess.backend, "note", "")
        text = (
            f"🟢 *{sess.repo.name}* @ {cs.repo_head(sess.repo)} · {sess.backend.name}\n"
            + (f"⚠️ _{note}_\n" if note else "")
            + "\n_Type normally — every message is a turn. `end` closes it._"
        )
        return text, controls(), "Session up", True

    if verb == "end":
        sess = cs.close_session(source)
        if sess is None:
            text, buttons = render_picker()
            return "No session was open here.\n\n" + text, buttons, "Nothing open", True
        text, buttons = render_picker()
        return (
            f"👋 Closed `{sess.repo.name}` after {sess.turns} turn(s). "
            f"Nothing was committed.\n\n{text}",
            buttons, "Closed", True,
        )

    if verb == "diff":
        text, buttons = render_diff(source)
        return text, buttons, "Diff", True

    # Bare `estate:code`, `status`, and anything unrecognised land on the one
    # screen that is always correct: the session if there is one, else the picker.
    text, buttons = render_session(source)
    return text, buttons, "Code", True
