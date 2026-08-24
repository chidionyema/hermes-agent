"""Incident 2026-08-23 and 2026-08-24: the agent's own rules lost their middle.

Twice in five hours the estate's laws file outgrew the context-file cap and the
prompt builder threw the middle away. 2026-08-23 21:47, cap 48000 against 74037
chars: the bodies of LAW 12 to LAW 30 went. 2026-08-24 02:30, cap 96000 against
100199: the back of LAW 34 and all of LAWS 35 to 40. Head 70% and tail 20% both
survive, so the agent kept the index of every law and lost the text of several,
with nothing in the prompt saying which. It then worked as though it had read
them.

Both times the repair was to raise the number, and both times the number fell
behind again on its own, because the file grew 26,162 chars in a single day.
These assert the rule instead: a rules file reaches the agent whole, nothing
else changes, and the lift is bounded by the model's window rather than by a
flat ceiling that would not fit in a small one.

Rungs, per the estate's testing ladder: incident tests for the two measured
failures, property tests for the invariants that must hold for every input, and
paired controls throughout because a guard that only proves it says no has not
been shown safe to install (LAW 38).
"""

import pytest

from agent import prompt_builder as pb


LAWS_2026_08_23 = "L" * 74_037   # size when the first incident happened
LAWS_2026_08_24 = "L" * 100_199  # size when the second one did

WINDOW = 200_000  # what config.yaml declares for The Architect

RULES_LABELS = [
    "AGENTS.md",
    "agents.md",
    "AGENTS.MD",
    "Agents.Md",
    "CLAUDE.md",
    "claude.md",
    "HERMES.md",
    ".hermes.md",
    "../AGENTS.md",              # a parent directory in the merged chain
    "../../AGENTS.md",
    "sub/dir/AGENTS.md",
    "/abs/path/AGENTS.md",
    "AGENTS.md (directory chain)",  # the label the merged result carries
    "../AGENTS.md (directory chain)",
]

NOT_RULES_LABELS = [
    "notes.md",
    "README.md",
    "data.txt",
    "AGENTS.py",
    "AGENTS.md.bak",
    "MY_AGENTS.md",     # substring, not the name
    "agentsmd",
    "",
    "AGENTS",
    "docs/AGENTS.md.txt",
]


# --------------------------------------------------------------------------
# The two incidents, asserted directly.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "content,cap,note",
    [
        (LAWS_2026_08_23, 48_000, "2026-08-23 21:47, dynamic cap, LAWS 12-30 lost"),
        (LAWS_2026_08_24, 96_000, "2026-08-24 02:30, pinned cap, LAWS 35-40 lost"),
    ],
)
def test_the_measured_incident_cannot_happen_again(content, cap, note):
    got = pb._cap_for_rules_file("AGENTS.md", content, cap, context_length=WINDOW)
    out = pb._truncate_content(content, "AGENTS.md", max_chars=got)
    assert len(out) == len(content), note
    assert "[...truncated" not in out, note


# --------------------------------------------------------------------------
# Every label a loader can pass, at both incident caps.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label", RULES_LABELS)
@pytest.mark.parametrize("cap", [20_000, 48_000, 96_000])
def test_a_rules_file_reaches_the_agent_whole(label, cap):
    content = LAWS_2026_08_24
    got = pb._cap_for_rules_file(label, content, cap, context_length=WINDOW)
    out = pb._truncate_content(content, label, max_chars=got)
    assert len(out) == len(content)
    assert "[...truncated" not in out


@pytest.mark.parametrize("label", NOT_RULES_LABELS)
def test_everything_else_is_still_capped(label):
    """The paired control. Lifting the cap for one class of file is only safe
    if it is provably not lifted for the rest: a 100K README injected whole is
    a cost regression no other test would catch."""
    cap = 48_000
    content = LAWS_2026_08_24
    assert pb._cap_for_rules_file(label, content, cap, context_length=WINDOW) == cap
    out = pb._truncate_content(content, label, max_chars=cap)
    assert len(out) < len(content)
    assert "[...truncated" in out


# --------------------------------------------------------------------------
# The bound. This is the half that keeps the fix from being worse than the bug.
# --------------------------------------------------------------------------

def test_a_small_window_still_truncates_a_rules_file():
    """An 8K-window model gets a quarter of its window of rules, not 125K
    tokens of them. Injecting a rules file whole into a window it does not fit
    in trades a silent truncation for a request that cannot be sent at all.

    The cap is still never lowered, so the configured 20,000 stands rather than
    dropping to the 8,000 ceiling. What the ceiling denies is the lift: the file
    is 30,000 chars and does not get one."""
    content = "L" * 30_000
    got = pb._cap_for_rules_file("AGENTS.md", content, 20_000, context_length=8_000)
    assert got == 20_000
    assert got < len(content)
    out = pb._truncate_content(content, "AGENTS.md", max_chars=got)
    assert len(out) < len(content)
    assert "[...truncated" in out


def test_the_lift_never_exceeds_a_quarter_of_the_window():
    for window in (8_000, 32_000, 128_000, 200_000, 1_000_000):
        ceiling = pb._rules_file_ceiling(window)
        assert ceiling <= window * pb._CONTEXT_FILE_CHARS_PER_TOKEN * 0.25 + 1
        assert ceiling <= pb._CONTEXT_FILE_DYNAMIC_CEILING


def test_the_hard_ceiling_still_binds_on_a_huge_window():
    """A rules file someone lets grow to 600K is not injected whole even when
    a quarter of the window would allow it."""
    huge = "L" * 600_000
    got = pb._cap_for_rules_file("AGENTS.md", huge, 48_000, context_length=2_000_000)
    assert got == pb._CONTEXT_FILE_DYNAMIC_CEILING
    assert got < len(huge)


# --------------------------------------------------------------------------
# Edge cases on the inputs themselves.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad_window", [None, 0, -1, -200_000, "200000", 3.5])
def test_an_unusable_context_length_falls_back_to_the_hard_ceiling(bad_window):
    """An unknown window must not silently become a zero budget, which would
    truncate a rules file to nothing while looking like a deliberate cap."""
    got = pb._rules_file_ceiling(bad_window)
    assert got == pb._CONTEXT_FILE_DYNAMIC_CEILING


def test_a_rules_file_under_the_cap_is_untouched():
    """The cap is not raised when it does not need to be, so the common case
    keeps the configured budget and the configured cost."""
    assert pb._cap_for_rules_file(
        "AGENTS.md", "L" * 1_000, 48_000, context_length=WINDOW
    ) == 48_000


def test_content_exactly_at_the_cap_is_untouched():
    cap = 48_000
    assert pb._cap_for_rules_file(
        "AGENTS.md", "L" * cap, cap, context_length=WINDOW
    ) == cap


def test_content_one_char_over_the_cap_is_lifted():
    cap = 48_000
    assert pb._cap_for_rules_file(
        "AGENTS.md", "L" * (cap + 1), cap, context_length=WINDOW
    ) == cap + 1


def test_empty_content_is_untouched():
    assert pb._cap_for_rules_file("AGENTS.md", "", 48_000, context_length=WINDOW) == 48_000


def test_multibyte_content_is_measured_in_characters_not_bytes():
    """The cap is a character count. A file of 3-byte characters must not be
    lifted three times higher than an ASCII file of the same length."""
    content = "漢" * 50_000  # 50,000 chars, 150,000 bytes
    got = pb._cap_for_rules_file("AGENTS.md", content, 48_000, context_length=WINDOW)
    assert got == 50_000


def test_an_explicit_max_chars_is_still_honoured_verbatim():
    """_truncate_content only consults the rules-file lift when it is resolving
    the cap itself. A caller that passes max_chars has said what it wants."""
    content = LAWS_2026_08_24
    out = pb._truncate_content(content, "AGENTS.md", max_chars=1_000)
    assert len(out) < len(content)


# --------------------------------------------------------------------------
# Properties: these hold for every input, and survive a rewrite of the body.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label", RULES_LABELS + NOT_RULES_LABELS)
@pytest.mark.parametrize("size", [0, 1, 19_999, 20_000, 48_001, 100_199, 499_999, 500_001])
@pytest.mark.parametrize("cap", [20_000, 96_000])
def test_the_cap_is_never_lowered(label, size, cap):
    """Whatever else it does, this function may only ever raise a cap. Lowering
    one would truncate a file that used to fit, which is the incident again
    with the sign flipped."""
    got = pb._cap_for_rules_file(label, "L" * size, cap, context_length=WINDOW)
    assert got >= cap


@pytest.mark.parametrize("label", RULES_LABELS + NOT_RULES_LABELS)
@pytest.mark.parametrize("size", [0, 1, 100_199, 600_000])
def test_the_result_never_exceeds_the_ceiling_or_the_content(label, size):
    """The lift is bounded twice over: it never exceeds what the file actually
    needs, and never exceeds what the window allows."""
    cap = 20_000
    content = "L" * size
    got = pb._cap_for_rules_file(label, content, cap, context_length=WINDOW)
    assert got <= max(cap, len(content))
    assert got <= max(cap, pb._rules_file_ceiling(WINDOW))


@pytest.mark.parametrize("label", RULES_LABELS)
def test_is_rules_file_agrees_with_the_lift(label):
    assert pb._is_rules_file(label) is True


@pytest.mark.parametrize("label", NOT_RULES_LABELS)
def test_is_rules_file_rejects_everything_else(label):
    assert pb._is_rules_file(label) is False
