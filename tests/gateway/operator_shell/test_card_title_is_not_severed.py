"""The cockpit headline must not read as corruption.

Live capture 2026-08-14 22:35 UTC — what the founder actually received:

    👉 BLOCKED [MONEY] `d7b0a113` The Introduction Exchange: Product next-move
    for The Introduction Exchange: inspect the repo at ~/Documents/code (README

119 chars, cut mid-word, inside an unclosed bracket. The title column holds a
raw agent prompt and the WRITER clips it; the cockpit printed the stump verbatim
as its top line. That is a large part of "cryptic ... not sure wtf is going on".

These tests pin the render-layer contract only: a severed string is ENDED so the
operator can see it was cut. They deliberately do NOT pin a maximum length — the
full-title decision at both call sites is intentional (see the comments there)
and re-introducing a clip would be a silent feature removal.
"""

from gateway.operator_shell.mission import _CLIP_EVIDENCE_LEN, _tidy_title


LIVE_CAPTURE = (
    "The Introduction Exchange: Product next-move for The Introduction "
    "Exchange: inspect the repo at ~/Documents/code (README"
)


def test_the_live_capture_stops_reading_as_garbage():
    out = _tidy_title(LIVE_CAPTURE)
    assert out.endswith("…"), out
    # The stump inside the unclosed bracket is gone, bracket and all.
    assert "(README" not in out
    assert "(" not in out
    # ...but the part that carries the meaning survives untouched.
    assert out.startswith("The Introduction Exchange: Product next-move")
    assert "inspect the repo" in out


def test_an_unclosed_bracket_is_cut_at_the_bracket():
    assert _tidy_title("Ship the pricing rung (see COST_PROG") == "Ship the pricing rung…"
    assert _tidy_title("Retry the drain [batch 4 of") == "Retry the drain…"


def test_a_closed_bracket_is_left_alone():
    src = "Ship the pricing rung (rung 4) and re-vet."
    assert _tidy_title(src) == src


def test_a_short_title_is_never_trimmed():
    """The destructive failure mode: mangling good data to tidy a case that
    is not happening. A short title without a full stop is just a title."""
    for src in (
        "Approve the pricing change",
        "Delist pack 3c346201",
        "Otto",
        "Restart the gateway",
    ):
        assert _tidy_title(src) == src, src


def test_a_long_title_that_ends_a_word_cleanly_still_gets_the_ellipsis():
    src = "x" * (_CLIP_EVIDENCE_LEN + 10) + " tail"
    out = _tidy_title(src)
    assert out.endswith("…")
    assert "tail" not in out


def test_punctuated_long_titles_are_untouched():
    src = "y" * (_CLIP_EVIDENCE_LEN + 20) + " and then we ship."
    assert _tidy_title(src) == src


def test_whitespace_is_collapsed_and_empty_survives():
    assert _tidy_title("  a   b  ") == "a b"
    assert _tidy_title("") == ""
    assert _tidy_title(None) == ""
