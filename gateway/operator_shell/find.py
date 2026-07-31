"""Type what you want; get the button.

The complaint: "buttons may exist but the UI is so confusing I don't know where to find
anything." That is not fixed by adding buttons — the live sweep counted 131 destinations
across 76 panels. Past ~30, browsing stops working and search starts.

The index is DERIVED, never hand-written. `natural_ops._PATTERNS` is already the list of
things this estate can do and the words the operator would use to ask for them — the regex
literals *are* the vocabulary. Extracting from there means a new op is findable the moment
it is added, and a hand-kept second list can never drift out of sync with the first.

Two kinds of hit, kept apart on purpose:

- **Do it now** — the op takes no argument, so it is a real button (`estate:brain`).
- **Type this** — the op needs an id or a value (`approve <id>`), which no button can carry.
  Shown as text, because a button that cannot work is worse than no button.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from gateway.operator_shell.panel_chrome import nav
from gateway.operator_shell.usage import example_command

ButtonRow = List[Tuple[str, str]]

# Regex scaffolding and words that match everything are noise, not vocabulary.
_STOP: Set[str] = {
    "the", "and", "for", "are", "you", "your", "our", "was", "what", "whats",
    "how", "hows", "who", "why", "when", "where", "which", "that", "this", "with",
    "from", "into", "not", "any", "all", "can", "let", "get", "got", "put", "has",
    "have", "does", "did", "will", "would", "should", "could", "please", "just",
    "now", "then", "here", "there", "some", "one", "two", "out", "off", "yes",
    "yeah", "yep", "okay", "sup", "hey", "hello", "thanks", "thank", "thx",
    "cool", "nice", "hmm", "give", "show", "tell", "want", "need", "make", "very",
}

_WORD = re.compile(r"[a-z][a-z']{2,}")


def _keywords(pattern: str, label: str) -> Set[str]:
    """Literal words a human would type, pulled out of a regex source plus its label.

    Deliberately crude: lowercase alphabetic runs of 3+, minus stopwords. Regex operators
    are punctuation, so they fall out on their own; the odd class fragment that survives
    costs a spurious match on a word nobody types, which is cheaper than a missed one.
    """
    words = set(_WORD.findall(pattern.lower()))
    words |= set(_WORD.findall(label.lower()))
    return {w for w in words if w not in _STOP}


class Entry:
    __slots__ = ("action", "args", "label", "words", "needs_arg", "usage")

    def __init__(self, action: str, args: str, label: str, words: Set[str], usage: Optional[str] = None):
        self.action = action
        self.args = args
        self.label = label
        self.words = words
        # "{g1}" is a capture placeholder: the op is only meaningful with an id or value
        # the operator supplies, so it can be told about but not offered as a tap.
        self.needs_arg = "{g" in (args or "")
        # What to type, derived from the pattern itself. The action name is NOT it —
        # `match_natural_op("se_set …")` is None, so printing that sent the operator to a
        # command the router has never accepted.
        self.usage = usage

    @property
    def callback(self) -> str:
        return f"estate:{self.action}" + (f":{self.args}" if self.args and not self.needs_arg else "")


def _index() -> List[Entry]:
    from gateway.operator_shell.natural_ops import _PATTERNS

    seen: Dict[str, Entry] = {}
    for pat, action, args, label in _PATTERNS:
        if not label:
            continue  # unlabelled noise-absorbers ("ok", "👍") are not destinations
        entry = Entry(action, args, label, _keywords(pat.pattern, label),
                      example_command(pat) if "{g" in (args or "") else None)
        # Several patterns reach the same destination (there are five ways to ask for the
        # brief). Merge their vocabulary onto one result instead of listing it five times.
        key = f"{action}|{args}"
        if key in seen:
            seen[key].words |= entry.words
            if seen[key].usage is None:
                seen[key].usage = entry.usage
        else:
            seen[key] = entry
    return _collapse_same_label(list(seen.values()))


def _collapse_same_label(entries: List[Entry]) -> List[Entry]:
    """One destination, one row — even when it is reachable both by tap and by typing.

    `find` is registered twice: argless (open the panel) and with a capture (`find spend`).
    Distinct keys, identical label, so a search for "search" printed *Find anything* as a
    ⌨️ line **and** as a button — the same duplicate-button defect already fixed twice on
    the home card. The tappable form wins: it is the one a button can carry, and the panel
    it opens explains the typed form anyway.
    """
    by_label: Dict[str, Entry] = {}
    order: List[str] = []
    for entry in entries:
        key = f"{entry.action}|{entry.label}"
        if key not in by_label:
            by_label[key] = entry
            order.append(key)
            continue
        kept = by_label[key]
        if kept.needs_arg and not entry.needs_arg:
            entry.words |= kept.words
            entry.usage = entry.usage or kept.usage
            by_label[key] = entry
        else:
            kept.words |= entry.words
            kept.usage = kept.usage or entry.usage
    return [by_label[k] for k in order]


def search(query: str, limit: int = 8) -> List[Tuple[int, Entry]]:
    """Rank destinations against a free-text query. Exact word beats prefix beats nothing."""
    tokens = [t for t in _WORD.findall((query or "").lower()) if t not in _STOP]
    if not tokens:
        return []
    scored: List[Tuple[int, Entry]] = []
    for entry in _index():
        score = 0
        for tok in tokens:
            if tok in entry.words:
                score += 3
            elif any(w.startswith(tok) or tok.startswith(w) for w in entry.words):
                score += 1
        if score:
            scored.append((score, entry))
    # Sort by score, then label, so equal-scoring results come back in a stable order
    # rather than shuffling between renders of the same query.
    scored.sort(key=lambda pair: (-pair[0], pair[1].label))
    return scored[:limit]


def render_find(query: Optional[str] = None) -> Tuple[str, List[ButtonRow]]:
    query = (query or "").strip()
    if not query:
        total = len(_index())
        text = "\n".join([
            "🔎 *Find* — type what you want, not where it lives",
            "",
            f"`find <anything>` searches all {total} operations. For example:",
            "",
            "• `find restart` — every restart there is",
            "• `find spend` — caps, pause, budget",
            "• `find model` — which brain is thinking",
            "• `find approve` — what is waiting on you",
            "",
            "_Plain phrases work on their own too: “restart gateway”, “use opus”, “brief”._",
        ])
        return text, [nav("find")]

    hits = search(query)
    if not hits:
        text = "\n".join([
            f"🔎 Nothing matches *{query}*.",
            "",
            "Try a plainer word — `restart`, `spend`, `model`, `logs`, `approve`, `status`.",
            "Anything longer than a lookup is treated as a task for the agent, not a search.",
        ])
        return text, [nav("find")]

    lines = [f"🔎 *{query}* — {len(hits)} match{'' if len(hits) == 1 else 'es'}", ""]
    buttons: List[ButtonRow] = []
    row: ButtonRow = []
    for _score, entry in hits:
        if entry.needs_arg:
            if entry.usage:
                lines.append(f"⌨️ *{entry.label}* — type `{entry.usage}`")
            else:
                # Never invent one. An unroutable command reads as a broken feature.
                lines.append(f"⌨️ *{entry.label}* — ask for it in plain words")
            continue
        lines.append(f"• {entry.label}")
        row.append((entry.label, entry.callback))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(nav("find"))
    return "\n".join(lines), buttons
