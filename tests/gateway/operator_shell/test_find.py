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
    """Estate ops that take an argument — the ones `match_natural_op` must accept.

    Slash commands are excluded because they are not natural-language ops: they are routed
    by `resolve_command`, not by `_PATTERNS`, so typing one at `match_natural_op` correctly
    returns None. The same "what we tell you to type must be real" invariant applies to
    them, against their own router — see `test_every_slash_hint_actually_resolves`.
    """
    return [e for e in find._index() if e.needs_arg and not e.typed]


def _slash_entries():
    return [e for e in find._index() if e.typed]


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


def test_every_slash_hint_actually_resolves():
    """Same invariant as above, for the slash commands: type it and it must be accepted.

    Search now carries the 49 commands Telegram's `/` list hides, which is only worth doing
    if the string it prints is the string the router takes. `resolve_command` is that
    router; a hint it returns None for is a command that does not exist.
    """
    from hermes_cli.commands import resolve_command

    entries = _slash_entries()
    assert len(entries) > 20, "the slash commands should be in the index, not a handful"
    failures = []
    for entry in entries:
        assert entry.usage, f"{entry.label}: slash entry with no usage prints nothing to type"
        # The hint is "/name [args]" — the router is given the name, which is what a
        # dispatcher does with the first token of the line.
        name = entry.usage.split()[0]
        assert name.startswith("/"), f"{entry.usage!r} does not read as a command"
        if resolve_command(name) is None:
            failures.append(f"{entry.usage!r} -> resolve_command({name!r}) is None")
    assert not failures, "slash hints that do not resolve:\n" + "\n".join(failures)


def test_a_hidden_command_is_findable_by_what_it_does():
    """The whole point: Telegram shows 9 commands and hides 49, and search reintroduces them.

    `/compress` is one of the hidden ones. Before the slash index it scored zero matches —
    the feature existed, dispatched fine, and was unreachable for anyone who did not already
    know its name.
    """
    text, _rows = find.render_find("compress")
    assert "/compress" in text
    assert "⌨️" in text, "a slash command must be shown as type-this, never as a dead button"


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
    search for "search" showed the destination as both a ⌨️ line and a button."""
    seen = [(e.action, e.label) for e in find._index()]
    duplicates = {pair for pair in seen if seen.count(pair) > 1}
    assert not duplicates, f"same destination listed more than once: {duplicates}"

    text, buttons = find.render_find("search")
    labels = [label for row in buttons for label, _cb in row]
    assert labels.count("Map — rooms + search") <= 1
    assert "⌨️ *Map — rooms + search*" not in text, "shown as a button already"


def test_render_find_without_query_opens_map():
    """Empty Map is Atlas — rooms first, not a help essay with an index count."""
    text, buttons = find.render_find()
    assert "Map" in text
    flat = {cb for row in buttons for _l, cb in row}
    assert "estate:room:money" in flat
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
