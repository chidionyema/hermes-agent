"""The cockpit search: does it find things, and is what it tells you to type real?

The load-bearing test here is `test_every_typed_hint_actually_routes`. Search shipped
printing the internal action name as the command — `se_set <id>`, `brain_set <id>`,
`code_assign <id>` — and `match_natural_op` returns None for all of them. Six of the ten
arg-taking ops named a command the estate does not accept, inside the one feature built to
answer "I don't know where to find anything". The round-trip below is what stops that
coming back: derive the hint, type it, and require it to land on the same action.
"""

import re

import pytest

from gateway.operator_shell import find, usage
from gateway.operator_shell.natural_ops import _PATTERNS, match_natural_op

# What a placeholder stands for, when we need to actually type one.
SAMPLES = {
    usage.ID: "abc123",       # hex, 4-12 chars, as every id pattern demands
    usage.NUM: "5",
    usage.TEXT: "fix the bug",
    usage.VALUE: "internal_sim",
}


def _typed(hint: str) -> str:
    out = hint
    for token, sample in SAMPLES.items():
        out = out.replace(token, sample)
    return out


def _arg_entries():
    return [e for e in find._index() if e.needs_arg]


def test_index_is_derived_and_non_empty():
    entries = find._index()
    assert len(entries) > 20, "the index should cover the estate's ops, not a handful"
    assert all(e.label for e in entries), "an unlabelled entry is not a destination"


def test_every_typed_hint_actually_routes():
    """Every `type this` string must be a command the router accepts, for its own action.

    This is the regression that shipped: the hint named the action, and the action name is
    not a command.
    """
    entries = _arg_entries()
    assert entries, "expected ops that take an argument"
    failures = []
    for entry in entries:
        assert entry.usage, f"{entry.action}: no derived usage, would print a non-answer"
        typed = _typed(entry.usage)
        matched = match_natural_op(typed)
        if matched is None or matched.action != entry.action:
            failures.append(
                f"{entry.action}: hint {entry.usage!r} typed as {typed!r} -> "
                f"{matched.action if matched else None}"
            )
    assert not failures, "hints that do not route:\n" + "\n".join(failures)


# There is deliberately no "the hint must differ from the action name" test: for approve,
# task and cancel the action name IS the word you type, so that rule fires on correct
# output. Whether a hint works is decided by typing it — the round-trip above — not by how
# it looks. The six that were broken are pinned by name below.


def test_known_ops_derive_the_phrasing_a_human_uses():
    derived = {e.action: e.usage for e in _arg_entries()}
    assert derived["brain_set"] == "use opus"
    assert derived["code_assign"] == "assign <text>"
    assert derived["pause_task"] == "pause <id>"
    assert derived["approve"] == "approve <id>"
    assert derived["se_set"] == "set signal exec_mode <value>"
    assert derived["pd_set"] == "set prospector interval <n>"


def test_no_destination_is_listed_twice():
    """The duplicate-button defect: `find` was registered argless and with a capture, so a
    search for "search" showed *Find anything* as both a ⌨️ line and a button."""
    seen = [(e.action, e.label) for e in find._index()]
    duplicates = {pair for pair in seen if seen.count(pair) > 1}
    assert not duplicates, f"same destination listed more than once: {duplicates}"

    text, buttons = find.render_find("search")
    labels = [label for row in buttons for label, _cb in row]
    assert labels.count("Find anything") <= 1
    assert "⌨️ *Find anything*" not in text, "shown as a button already"


def test_search_finds_the_documented_examples():
    """The panel promises these work; each must return something."""
    for query in ("restart", "spend", "model", "approve", "logs", "brief"):
        assert find.search(query), f"documented example {query!r} found nothing"


def test_search_ranks_exact_word_above_prefix():
    hits = find.search("restart")
    assert hits[0][0] >= 3, "an exact word match should score as one"
    assert all(hits[i][0] >= hits[i + 1][0] for i in range(len(hits) - 1))


def test_search_ignores_noise_and_unknown_words():
    assert find.search("") == []
    assert find.search("the and for you") == [], "stopwords alone are not a query"
    assert find.search("zzzqqq") == []


def test_callbacks_fit_telegram_limit():
    """Telegram rejects callback_data over 64 bytes; a generated callback must not trip it."""
    for entry in find._index():
        if not entry.needs_arg:
            assert len(entry.callback.encode()) <= 64, entry.callback


def test_render_find_without_query_lists_the_index_size():
    text, buttons = find.render_find()
    assert "Find" in text
    assert str(len(find._index())) in text, "the advertised count must be the real one"
    assert buttons, "the nav row is always present"


def test_render_find_with_no_match_says_so():
    text, buttons = find.render_find("zzzqqq")
    assert "Nothing matches" in text
    assert buttons


def test_render_find_offers_argless_ops_as_buttons():
    text, buttons = find.render_find("restart")
    labels = [label for row in buttons for label, _cb in row]
    assert any("estart" in label or "ounce" in label for label in labels), labels


@pytest.mark.parametrize(
    "pattern,expected",
    [
        (re.compile(r"^\s*approve\s+`?([0-9a-fA-F]{4,12})`?\s*$", re.I), "approve <id>"),
        (re.compile(r"^\s*(?:use|switch\s+to)\s+(?:the\s+)?(opus|sonnet)\s*$", re.I), "use opus"),
        (re.compile(r"^\s*run\s+prospector(?:\s+(\d+))?\s*$", re.I), "run prospector <n>"),
        (re.compile(r"^\s*(?:assign|code)\s*[:\-]?\s+(.+)$", re.I), "assign <text>"),
    ],
)
def test_example_command_derives_the_first_phrasing(pattern, expected):
    assert usage.example_command(pattern) == expected


def test_example_command_survives_a_pattern_it_cannot_read():
    """A private parser API must never take the panel down."""

    class Hostile:
        pattern = "(?P<x>" + "(" * 200  # unbalanced: parse() raises
        flags = 0

    assert usage.example_command(Hostile()) is None


def test_every_pattern_in_the_table_is_parseable():
    """Whole-table sweep: no op should fall back to the non-answer."""
    unparsed = [
        action for pat, action, args, label in _PATTERNS
        if label and "{g" in (args or "") and usage.example_command(pat) is None
    ]
    assert not unparsed, f"could not derive a command for: {unparsed}"
