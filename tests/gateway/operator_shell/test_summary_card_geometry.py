"""The card's fixed-width art must be generated, never typed.

Measured 2026-08-19, before this file existed: not one box in the rendered
Summary Card closed, and no two borders of the same box agreed. The header
rules were 44 columns; the AT-A-GLANCE top was 45 and its bottom 46; three
different ``╭─`` openers (39, 40 and 45) were all closed by the same 44-wide
``╰──╯``; and the content lines between them (22, 41, 49, 54, 67) carried no
right edge at all. Every one of those borders was a string literal typed by
hand, so the art could not be right by construction, and no test measured a
width, so nothing noticed.

The class this file closes: **decorative fixed-width art built from string
literals**. It is closed three ways.

1. Every rule and band in the rendered card is measured, and must be exactly
   ``_CARD_WIDTH``.
2. The module source is scanned (with comments stripped, so the section
   separators in the prose are not graded) for long runs of box-drawing
   characters. A re-typed border fails here even if it happens to be the
   right width today.
3. A mutation proof: move ``_CARD_WIDTH`` and the rendered widths must move
   with it. Without this, a test asserting "== 34" passes against art that
   ignores the constant entirely.

It also pins two rendering facts that cost the reader real information:
Telegram does not render Markdown inside a fenced code block (the card used
to show ``🧮 **7**`` on screen), and a line ending in a right-hand box glyph
cannot align for every reader because emoji occupy different numbers of
columns in different clients.
"""
from __future__ import annotations

import inspect
import re

import pytest

from gateway.operator_shell import summary_card as sc

# Both rule glyphs used by the card.
_RULE_CHARS = "─━"
# Everything that implies a closed box. None of these may appear.
_BOX_EDGES = "╔╗╚╝║╭╮╰╯│┌┐└┘"

_SAMPLES = [
    "Anna",                                    # short, single part, palindrome
    "Chidi Onyema",                            # two parts
    "Mumchimp Limited trading as Prospector",  # many parts, long
    "A",                                       # one letter
    "X Y",                                     # parts too short to qualify
]


def _fenced_lines(card: str) -> list[str]:
    """Lines inside ``` fences, which is where the fixed-width art lives."""
    inside = False
    out: list[str] = []
    for line in card.split("\n"):
        if line.startswith("```"):
            inside = not inside
            continue
        if inside:
            out.append(line)
    return out


def _is_generated_art(line: str) -> bool:
    """True for a rule or a titled band, false for a content line."""
    stripped = line.strip()
    if not stripped:
        return False
    if all(c in _RULE_CHARS for c in stripped):
        return True
    # A band: opens with two rule chars and closes with rule chars.
    return stripped[:2].strip(_RULE_CHARS) == "" and stripped[-1] in _RULE_CHARS


@pytest.mark.parametrize("text", _SAMPLES)
def test_every_rule_and_band_is_exactly_one_width(text: str) -> None:
    art = [ln for ln in _fenced_lines(sc.render_summary_card(text)) if _is_generated_art(ln)]
    assert art, f"no fixed-width art found in the card for {text!r}"
    widths = {len(ln) for ln in art}
    assert widths == {sc._CARD_WIDTH}, (
        f"borders disagree for {text!r}: widths {sorted(widths)}, "
        f"expected only {sc._CARD_WIDTH}\n"
        + "\n".join(f"{len(ln):3d} |{ln}|" for ln in art)
    )


@pytest.mark.parametrize("text", _SAMPLES)
def test_no_line_implies_a_closed_box(text: str) -> None:
    """Emoji width differs between clients, so a right edge cannot align."""
    offenders = [
        ln for ln in sc.render_summary_card(text).split("\n")
        if any(c in _BOX_EDGES for c in ln)
    ]
    assert not offenders, "box edges cannot align for every reader:\n" + "\n".join(offenders)


@pytest.mark.parametrize("text", _SAMPLES)
def test_no_markdown_inside_a_code_fence(text: str) -> None:
    """Telegram shows the asterisks; it does not bold inside a fence."""
    offenders = [ln for ln in _fenced_lines(sc.render_summary_card(text)) if "**" in ln]
    assert not offenders, "Markdown inside a fence is shown literally:\n" + "\n".join(offenders)


def test_the_source_contains_no_hand_typed_borders() -> None:
    """A re-typed literal fails here even if today it is the right width.

    Comments are stripped first: the file's own section separators are prose
    and grading them would be grading the comments, not the code.
    """
    code = "\n".join(
        line for line in inspect.getsource(sc).split("\n")
        if not line.lstrip().startswith("#")
    )
    # Strip docstrings too — the module documents the old widths on purpose.
    code = re.sub(r'"""(?:.|\n)*?"""', "", code)
    runs = re.findall(r"[─━═╔╗╚╝╭╮╰╯┌┐└┘]{4,}", code)
    assert not runs, (
        "fixed-width art must come from _rule()/_band(), never a literal: " + repr(runs)
    )


def test_widths_follow_the_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation proof. Without it, `== 34` can pass against art that ignores
    the constant."""
    monkeypatch.setattr(sc, "_CARD_WIDTH", 21)
    art = [ln for ln in _fenced_lines(sc.render_summary_card("Chidi Onyema")) if _is_generated_art(ln)]
    assert art
    assert {len(ln) for ln in art} == {21}, sorted({len(ln) for ln in art})


def test_band_titles_never_overflow_the_width() -> None:
    """A title longer than the card is truncated to a bare head, not padded
    into a negative-width run (which would silently emit no rule at all)."""
    long_title = "A" * (sc._CARD_WIDTH * 2)
    band = sc._band(long_title)
    assert band.startswith("── ")
    assert long_title in band
    assert not band.endswith(" ")


@pytest.mark.parametrize("text", ["AF", "AO", "BI", "BU", "CL", "CU"])
def test_partial_agreement_names_the_shared_root_not_the_odd_one_out(text: str) -> None:
    """Two ciphers agreeing on 7 while a third says 9 must report 7.

    The old code took ``next(iter({7, 9}))`` — an arbitrary member of a set —
    so the card could state "2 of 3 ciphers reduce to 9", naming the
    singleton. Measured over all 676 two-letter inputs: 154 of them hit the
    ordering that names the wrong root. The six pinned here are from that
    set, so this test fails against the old code rather than depending on
    which way the hash happens to fall.
    """
    roots = [
        sc.pythagorean(text).root,
        sc.hebrew(text).root,
        sc.chaldean(text).root,
    ]
    assert len(set(roots)) == 2, f"{text!r} is no longer a partial-agreement case: {roots}"
    m = re.search(r"2 of 3 ciphers reduce to (\d+)", sc.render_summary_card(text))
    assert m, "the partial-agreement branch did not render"
    claimed = int(m.group(1))
    assert roots.count(claimed) == 2, (
        f"{text!r}: card claims 2 of 3 reduce to {claimed}, actual roots {roots}"
    )
