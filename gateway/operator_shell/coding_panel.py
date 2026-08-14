"""The tappable door to `/code`. Nothing here should need to be remembered.

`/code prospector pi` is three things to recall: that the command exists, what
the repo is called, and that a second word picks the brain. On a phone that is
two too many. This module renders the same surface as buttons:

    /code           ->  a repo picker, one button per git repo actually on disk
    (tap a repo)    ->  the session opens, and its card carries End / Diff
    /code           ->  while open, that card again

The picker is DERIVED from the filesystem, never a hand-maintained list — the
estate has been bitten repeatedly by hand-maintained tables drifting from the
thing they describe, and a repo list is exactly that shape.

Every screen here is built with `panel_chrome.compose`, so it carries the same
spine as every other cockpit panel (🏠 Home · 🗂 Projects · ⚡ Actions · 💻 SDLC ·
⚙️ Tune · 🗺 Browse) and the same self-refresh. The first draft did not, and the
result was a dead end: once the operator was on a code screen the ONLY way off
it was to end the session, because a panel that renders its own bespoke rows and
omits the spine has silently opted out of the cockpit's navigation. Local moves
(⬅ Session) are additions to that spine, never a replacement for it.

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
from gateway.operator_shell.panel_chrome import Group, compose, with_nav

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

# A phone keyboard, not a directory listing. Beyond this the picker stops being
# faster than typing the name. Six is three rows of two — the density every other
# cockpit panel keeps (`panel_chrome.MAX_GROUP_ROWS`); ten repos filled five rows
# and pushed the nav spine down the card. Recency ordering is what makes six
# enough: the repo you last worked in is the first button.
MAX_REPOS = 6
# Buttons per row. Repo names are long; two is the point where they start eliding.
REPOS_PER_ROW = 2
# Changed paths shown on the diff card before it stops being scannable on a phone.
MAX_DIFF_LINES = 20

_BACKEND_LABELS = {"claude": "🧠 claude", "pi": "⚡ pi"}

# git's porcelain codes are not English. `AD` on a phone is a puzzle; "deleted"
# is not. Only the first column pair matters here — this is a card, not a review.
_PORCELAIN_WORDS = {
    "??": "new",
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "conflict",
}


def _touched_at(repo: Path) -> float:
    """When this repo was last worked in. Newest of the three cheap signals.

    No subprocess: the picker renders on every tap, and `git log -1` per repo
    would put a fork/exec per candidate in front of the operator's thumb.
    `.git/index` moves on add/status, `.git/HEAD` on commit/checkout, and the
    directory itself on any top-level file change.
    """
    best = 0.0
    for probe in (repo / ".git" / "index", repo / ".git" / "HEAD", repo):
        try:
            best = max(best, probe.stat().st_mtime)
        except OSError:
            continue
    return best


def _is_worktree(repo: Path) -> bool:
    """True for a linked worktree (and for a submodule) — `.git` is a FILE there.

    Not a judgement about validity; only about which name an operator means when
    they reach for a repo. `<root>/.git` being a directory is the ordinary case.
    """
    return (repo / ".git").is_file()


def discover_repos(limit: int = MAX_REPOS) -> list[Path]:
    """The repos most recently worked in, newest first.

    One level deep on purpose: `~/Documents/code` holds the repos, and recursing
    would surface every vendored checkout and every `.claude/worktrees/*` copy —
    an estate-wide recursive walk here was measured at 169k files elsewhere.

    Ranked by RECENCY across every root, then capped — not capped per root in
    alphabetical order, which is what the first version did and why it was
    unusable: ten names from `~/Documents/code` filled the keyboard at `crux`
    and `modeltrainer_backup`, and `hermes-agent` (a different root) could not
    be opened from the picker AT ALL. A picker whose top row is not the thing
    you last touched is a list, not a shortcut.
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
                # First root wins a name collision: roots are searched in the
                # order the operator declared them.
                seen.setdefault(child.name, child)
        except OSError:
            # An unreadable root is a fact about one root, not a reason to render
            # nothing — the other roots still have the repo the operator wants.
            logger.debug("coding picker: cannot read %s", root, exc_info=True)

    # Worktrees sort BELOW real checkouts at equal recency: they are the most
    # recently touched directories on this machine and would otherwise own the
    # whole keyboard (`wt-176`, `wt-close-loop`, `prospector-latest` are all one
    # repo). Demoted, not dropped — `/code wt-176` still resolves by name.
    ranked = sorted(seen.values(), key=lambda p: (_is_worktree(p), -_touched_at(p), p.name))
    return ranked[:limit]


def render_picker(backend: str = "claude") -> Tuple[str, List[ButtonRow]]:
    """The no-recall door: pick a repo, tap it, you are in."""
    backend = backend if backend in _BACKEND_LABELS else "claude"
    repos = discover_repos()

    if not repos:
        roots = "\n".join(f"  · `{r}`" for r in cs._search_roots())
        return compose(
            header=[
                "🛠 *Code*",
                "",
                "No git repos found. Looked in:",
                roots,
                "",
                "_Set `HERMES_CODE_ROOTS` to point it somewhere else._",
            ],
            groups=[],
            self_action="code",
            with_legend=False,
        )

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
    brain_note = (
        "no memory between turns — send whole instructions"
        if backend == "pi"
        else "holds the conversation across turns"
    )

    return compose(
        header=["🛠 *Code* — this chat becomes the terminal"],
        groups=[
            Group(
                "📁 Repo",
                rows,
                note="tap one to open a session; then just type",
            ),
            Group(
                "🧠 Brain",
                # Emoji-first like every other button on the estate — a label that
                # starts with a word is the one your eye skips on a phone.
                [[(f"🔀 Use {_BACKEND_LABELS[other]}", f"estate:code:pick:{other}")]],
                status=f"*{_BACKEND_LABELS[backend]}*",
                note=brain_note,
            ),
        ],
        self_action="code",
        with_legend=False,
    )


def controls() -> List[ButtonRow]:
    """The mid-session grid: the two live verbs, then the cockpit spine.

    `🔄 Status` used to sit here and re-rendered the identical card — a button
    that appears to do nothing reads as a broken one. The spine's own 🔄 already
    re-renders the current screen, so the duplicate is gone rather than restyled.
    """
    return with_nav(
        [[("⏹ End", "estate:code:end"), ("📊 Diff", "estate:code:diff")]],
        "code",
    )


def render_session(source: Any) -> Tuple[str, List[ButtonRow]]:
    """The open session's card, or the picker when there is nothing open here."""
    sess = cs.get(source)
    if sess is None:
        return render_picker()

    header = [sess.status_line()]
    others = cs.active_count() - 1
    if others > 0:
        header.append(f"_{others} more open in other chats._")

    return compose(
        header=header,
        groups=[
            Group(
                "⌨️ Session",
                [[("⏹ End", "estate:code:end"), ("📊 Diff", "estate:code:diff")]],
                note="every message you type here is a turn · `end` closes it",
            )
        ],
        self_action="code",
        with_legend=False,
    )


def _humanise(porcelain_line: str) -> str:
    """`AD path` -> `deleted · path`. A card the operator can read at a glance.

    The path is taken from column 2 and re-stripped, NOT from the fixed column 3.
    Porcelain writes the status as two columns, so an unstaged change begins with
    a space — and any caller that has `.strip()`ed the block (this one did) hands
    us `M README.md`, one character short. Column-3 slicing then renders
    `modified · EADME.md`: a receipt that quietly corrupts the filename it exists
    to prove. Robust to both shapes rather than trusting the caller.
    """
    raw = porcelain_line.rstrip()
    code = raw[:2].strip() or "?"
    path = raw[2:].strip()
    word = _PORCELAIN_WORDS.get(code) or _PORCELAIN_WORDS.get(code[:1], code)
    return f"{word} · {path}"


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
            # rstrip, never strip: porcelain's first column is significant, and
            # stripping the block ate the leading space of line 1.
            return out.stdout.rstrip("\n")
        except Exception:
            logger.warning("coding diff: git %s failed", args, exc_info=True)
            return ""

    status = _git("status", "--porcelain")
    lines = [f"📊 *{sess.repo.name}* @ {cs.repo_head(sess.repo)}"]
    if not status:
        lines.append("")
        lines.append("_Working tree clean — nothing has been changed._")
    else:
        changed = status.splitlines()
        lines += ["", f"*{len(changed)} path(s) changed*", "```text"]
        lines += [_humanise(ln) for ln in changed[:MAX_DIFF_LINES]]
        if len(changed) > MAX_DIFF_LINES:
            lines.append(f"… +{len(changed) - MAX_DIFF_LINES} more")
        lines.append("```")
        stat = _git("diff", "--stat")
        if stat:
            lines += ["```text", *stat.splitlines()[-6:], "```"]
        # Nothing here commits. A coding session that committed for you would put
        # an unreviewed executor's work into history behind a button tap.
        lines.append("_Nothing is committed. Review and commit yourself._")

    return compose(
        header=lines,
        groups=[
            Group(
                "⌨️ Session",
                # ⬅ first: the diff is a leaf, and the way back to the thing you
                # came from must not be a spine button that leaves /code entirely.
                [[("⬅ Session", "estate:code"), ("⏹ End", "estate:code:end")]],
            )
        ],
        self_action="code:diff",
        with_legend=False,
    )


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
        text, buttons = compose(
            header=[
                "⚠️ This action needs to know which chat it is for.",
                "",
                "Use `/code` in the chat you want the session in.",
            ],
            groups=[],
            self_action="code",
            with_legend=False,
        )
        return text, buttons, "Needs a chat", False

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
        header = [
            f"🟢 *{sess.repo.name}* @ {cs.repo_head(sess.repo)} · {sess.backend.name}"
        ]
        if note:
            header.append(f"⚠️ _{note}_")
        text, buttons = compose(
            header=header,
            groups=[
                Group(
                    "⌨️ Session",
                    [[("⏹ End", "estate:code:end"), ("📊 Diff", "estate:code:diff")]],
                    note="type normally — every message is a turn · `end` closes it",
                )
            ],
            self_action="code",
            with_legend=False,
        )
        return text, buttons, "Session up", True

    if verb == "end":
        sess = cs.close_session(source)
        picker, buttons = render_picker()
        if sess is None:
            return "No session was open here.\n\n" + picker, buttons, "Nothing open", True
        return (
            f"👋 Closed `{sess.repo.name}` after {sess.turns} turn(s). "
            f"Nothing was committed.\n\n{picker}",
            buttons, "Closed", True,
        )

    if verb == "diff":
        text, buttons = render_diff(source)
        return text, buttons, "Diff", True

    # Bare `estate:code`, `status`, and anything unrecognised land on the one
    # screen that is always correct: the session if there is one, else the picker.
    text, buttons = render_session(source)
    return text, buttons, "Code", True
