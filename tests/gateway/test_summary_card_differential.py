"""The old operator_shell card is the oracle for the v2 port of summary_card.

Rung 3. One assertion, thousands of cases, and the users already wrote them:
every render the old card has ever produced is the specification. It is a
migration tool, not a permanent test — when the old repo goes, this file goes
with it, and what remains is the property coverage in test_qabalah.py.

It skips rather than fails where the old tree is absent, because CI and the
Fly image have no copy of it and a port test that fails off the founder's
laptop would be noise, not a guard.
"""

from __future__ import annotations

import importlib.util
import itertools
import os
import string
import sys

import pytest

_OLD = os.path.expanduser(
    "~/Documents/code/hermes/hermes-agent/gateway/operator_shell/summary_card.py"
)

pytestmark = pytest.mark.skipif(
    not os.path.exists(_OLD),
    reason="the pre-v2 card is not on this machine; the port oracle is gone",
)


def _load_old():
    spec = importlib.util.spec_from_file_location("_summary_card_oracle", _OLD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_summary_card_oracle"] = mod
    spec.loader.exec_module(mod)
    return mod


_CORPUS = [
    "", " ", "a", "A", "Chidi", "Chidi Onyema", "Hello World", "Anna", "Beth",
    "abcdefghijklmnopqrstuvwxyz", "WXYZ", "AW", "Kether", "Malkuth", "YHWH",
    "Tetragrammaton", "אבג", "αβγ", "Λόγος", "666", "!!!", "level", "racecar",
    "The quick brown fox jumps over the lazy dog", "x" * 5000,
    "Ω", "Café", "naïve", "ONYEMA", "onyema", "O n y e m a", "9", "0",
] + [
    # every one- and two-letter word over a slice of the alphabet, which walks
    # the cipher tables and every reduction path they can produce
    "".join(t) for n in (1, 2) for t in itertools.product(string.ascii_uppercase[:6], repeat=n)
] + list(string.ascii_uppercase) + list(string.ascii_lowercase)

_PLATFORMS = ["telegram", "slack", "sms", "email", "glasses", "default", "nonesuch"]


def test_the_port_renders_what_the_old_card_rendered():
    old = _load_old()
    from gateway import summary_card as new

    for text in _CORPUS:
        assert old.render_summary_card(text) == new.render_summary_card(text), text
        assert old.render_summary_json(text) == new.render_summary_json(text), text


def test_the_port_renders_the_same_card_on_every_platform():
    """A smaller corpus on purpose.

    Each platform renderer re-renders the whole card, and the card regenerates
    anagrams every time — up to 8! permutations per call. Sweeping the full
    corpus across seven renderers costs minutes and proves nothing the shapes
    below do not: the renderers are transforms of one card, so what varies is
    the card's structure, not which word made it.
    """
    old = _load_old()
    from gateway import summary_card as new

    shapes = ["", "A", "Chidi", "Hello World", "WXYZ", "level", "666", "!!!",
              "Café", "x" * 5000, "The quick brown fox jumps over the lazy dog"]
    for text in shapes:
        for platform in _PLATFORMS:
            assert (old.render_for_platform(text, platform)
                    == new.render_for_platform(text, platform)), (text, platform)


def test_the_port_compares_two_texts_the_way_the_old_card_did():
    old = _load_old()
    from gateway import summary_card as new

    for a, b in itertools.islice(itertools.product(_CORPUS, repeat=2), 0, None, 211):
        assert old.render_compare_card(a, b) == new.render_compare_card(a, b), (a, b)
    for raw in ["a vs b", "a v b", "a versus b", "novs", "", "A vs B vs C", "  vs  "]:
        assert old.parse_compare_args(raw) == new.parse_compare_args(raw), raw
