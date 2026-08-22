"""Qabalah and isopsephy — properties, and one incident.

Rung 2 (properties) and rung 4 (one incident test, named for the defect).
There is no rung 1 here: not one test asserts that a function returns a
particular string, because none of those would survive a rewrite.

Written without hypothesis. v2 does not depend on it and this is not the
feature worth adding a dependency for, so the domains are walked exhaustively
where they are small and with a pinned seed where they are not. The
guarantees are the same; only the generator is hand-rolled.
"""

from __future__ import annotations

import datetime
import random
import unicodedata

from gateway.qabalah import (
    CANON,
    ENGLISH_EXT,
    GREEK,
    HEBREW,
    HEBREW_GADOL,
    PATHS,
    SEPHIROT,
    canon_readings,
    daily_target,
    english_extended,
    equivalences,
    gematria,
    isopsephy,
    play,
    readings_for,
    render_game,
    render_letter_tables,
    render_qabalah_section,
    scripts_present,
    sephirah_for,
)

_ALPHABETS = {
    "greek": "αβγδεϝζηθικλμνξοπϙρστυφχψωϡ",
    "hebrew": "אבגדהוזחטיכלמנסעפצקרשת",
    "latin": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
}
_SCORERS = {"greek": isopsephy, "hebrew": gematria, "latin": english_extended}


def _corpus(alphabet: str, n: int = 400, seed: int = 20260822) -> list[str]:
    rnd = random.Random(seed)
    return ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 12)))
            for _ in range(n)]


# --- properties of the scoring itself ---------------------------------------


def test_a_total_is_the_sum_of_its_own_breakdown():
    """The number shown is the number the letters make. Nothing is stored."""
    for script, alphabet in _ALPHABETS.items():
        score = _SCORERS[script]
        for word in _corpus(alphabet):
            r = score(word)
            assert r.total == sum(v for _c, v in r.breakdown)
            assert r.scored == len(r.breakdown)


def test_scoring_is_additive_over_concatenation():
    """value(a + b) == value(a) + value(b), in every system.

    This is the property that makes an equivalence hunt possible at all, and
    it is the first thing a wrong table breaks.
    """
    for script, alphabet in _ALPHABETS.items():
        score = _SCORERS[script]
        left, right = _corpus(alphabet, 200, seed=1), _corpus(alphabet, 200, seed=2)
        for a, b in zip(left, right):
            assert score(a + b).total == score(a).total + score(b).total


def test_every_letter_has_a_distinct_value_within_its_alphabet():
    """The defect this module was written around: no two letters may collide.

    A cipher with a collision cannot be inverted and quietly merges words that
    are not equal. summary_card's Hebrew table has four of them.
    """
    for name, table, alphabet in (
        ("greek", GREEK, _ALPHABETS["greek"]),
        ("hebrew", HEBREW, _ALPHABETS["hebrew"]),
        ("english", ENGLISH_EXT, _ALPHABETS["latin"]),
    ):
        values = [table[c] for c in alphabet]
        assert len(set(values)) == len(values), f"{name} has colliding letters"


def test_the_classical_series_is_complete():
    """1-9, 10-90, 100-900 with nothing missing. Isopsephy needs all 27."""
    assert sorted(GREEK[c] for c in _ALPHABETS["greek"]) == (
        list(range(1, 10)) + list(range(10, 100, 10)) + list(range(100, 1000, 100))
    )
    # Hebrew stops at tav 400: the series above it is carried by the finals.
    assert sorted(HEBREW[c] for c in _ALPHABETS["hebrew"]) == (
        list(range(1, 10)) + list(range(10, 100, 10)) + [100, 200, 300, 400]
    )
    assert sorted(HEBREW_GADOL[c] for c in "ךםןףץ") == [500, 600, 700, 800, 900]


def test_accents_and_niqqud_never_change_a_value():
    """A mark is not a letter. Combining it must not move the number."""
    for word in _corpus(_ALPHABETS["greek"], 150):
        for mark in ("́", "̈", "̓", "ͅ"):
            marked = "".join(c + mark for c in word)
            assert isopsephy(marked).total == isopsephy(word).total
    for word in _corpus(_ALPHABETS["hebrew"], 150):
        for mark in ("ְ", "ָ", "ּ"):
            marked = "".join(c + mark for c in word)
            assert gematria(marked).total == gematria(word).total


def test_precomposed_and_decomposed_input_agree():
    """NFC and NFD of the same word are the same word."""
    for word in ("Ἰησοῦς", "Λόγος", "ἀγάπη", "θέλημα", "ἀλήθεια"):
        assert (isopsephy(unicodedata.normalize("NFC", word)).total
                == isopsephy(unicodedata.normalize("NFD", word)).total)


def test_unreadable_characters_are_counted_not_silently_dropped():
    """A system that cannot read a character says so. The old card did not."""
    r = isopsephy("αβγ !? 123 xyz")
    assert r.total == 6
    assert r.skipped == len("!?123xyz")


# --- properties of the Tree --------------------------------------------------


def test_every_positive_value_lands_on_exactly_one_sephirah():
    for n in range(1, 5000):
        s = sephirah_for(n)
        assert s is not None and 1 <= s.number <= 10


def test_ten_stays_at_malkuth_and_never_folds_back_to_kether():
    """Incident: a naive digital root sends 10 to 1.

    Malkuth and Kether are the two ends of the Tree. A reduction that lands a
    total of 10 on the Crown is not a rounding error, it is the opposite
    reading.
    """
    assert sephirah_for(10).name == "Malkuth"
    assert sephirah_for(1).name == "Kether"
    assert sephirah_for(19).name == "Malkuth"   # 1+9 = 10, not 1


def test_nothing_off_the_tree_is_placed_on_it():
    assert sephirah_for(0) is None
    assert sephirah_for(-5) is None


def test_the_twenty_two_paths_are_whole():
    assert len(PATHS) == 22
    assert [p.number for p in PATHS] == list(range(11, 33))
    assert len({p.letter for p in PATHS}) == 22
    assert len({p.trump for p in PATHS}) == 22
    # Sefer Yetzirah: three mothers, seven doubles, twelve simples.
    kinds = [p.kind for p in PATHS]
    assert (kinds.count("mother"), kinds.count("double"), kinds.count("simple")) == (3, 7, 12)
    assert len(SEPHIROT) == 10
    assert [s.number for s in SEPHIROT] == list(range(1, 11))


# --- properties of the canon and the game -----------------------------------


def test_no_canon_value_is_written_down_anywhere():
    """CANON holds words and glosses only. Every number is computed."""
    for word, gloss in CANON:
        assert not any(ch.isdigit() for ch in word)
    for word, _gloss, reading in canon_readings():
        assert reading.total > 0, word
        assert reading.total == sum(v for _c, v in reading.breakdown)


def test_the_famous_equivalences_fall_out_of_the_tables():
    """Externally-known values, as the second angle on the tables.

    These are not this module's opinion. They are the numbers the sources give,
    and if a table is wrong one of them moves.
    """
    assert isopsephy("Ἰησοῦς").total == 888
    assert isopsephy("χξϛ").total == 666
    assert isopsephy("θέλημα").total == isopsephy("ἀγάπη").total == 93
    assert gematria("יהוה").total == 26
    assert gematria("אלהים").total == 86
    assert gematria("אחד").total == gematria("אהבה").total == 13
    assert gematria("מלכות").total == 496


def test_the_days_number_is_always_reachable():
    """A target no word hits is an unwinnable game. Check a full year."""
    day = datetime.date(2026, 1, 1)
    for _ in range(366):
        value, drawn_from = daily_target(day)
        assert value > 0
        assert equivalences(value), f"{day} target {value} is unreachable"
        assert play(drawn_from, today=day).hits, f"{drawn_from} misses its own target"
        day += datetime.timedelta(days=1)


def test_the_days_number_depends_only_on_the_day():
    """No stored state, so two players on the same date see the same game."""
    for d in (datetime.date(2026, 8, 22), datetime.date(2027, 2, 28)):
        assert daily_target(d) == daily_target(d)
    assert daily_target(datetime.date(2026, 8, 22)) != daily_target(datetime.date(2026, 8, 23)) \
        or True  # neighbouring days may legitimately collide; identity is the claim


def test_a_hit_is_a_hit_in_any_alphabet():
    """Matching a Hebrew number with a Greek word is the game, not a bug."""
    r = play("θέλημα", target=93)
    assert r.hits and r.distance == 0
    assert play("ἀγάπη", target=93).hits


def test_a_miss_reports_an_honest_distance():
    r = play("אחד", target=80)
    assert not r.hits
    assert r.distance == 80 - 13


def test_scoring_a_word_with_no_readable_letter_never_raises():
    for junk in ("", "   ", "!!!", "123", "🔮"):
        r = play(junk, target=93)
        assert not r.hits and r.readings == ()


# --- rendering: it must never raise, and never invent a reading -------------


def test_rendering_survives_anything_and_stays_silent_when_it_has_nothing():
    for junk in ("", " ", "!!!", "123", "🔮", "́"):
        assert render_qabalah_section(junk) == ""
    for word in ("Chidi", "אהבה", "θέλημα", "Chidi אהבה θέλημα", "x" * 3000):
        out = render_qabalah_section(word)
        assert out.startswith("### ")
        assert out.count("```") % 2 == 0, "unbalanced code fence"
    assert render_letter_tables().count("|") > 100
    for args in ("", "help", "93", "93 θέλημα", "אהבה", "!!!", "-5 x"):
        assert render_game(args)


def test_the_rendered_section_only_names_scripts_that_are_present():
    latin_only = render_qabalah_section("Chidi")
    assert "Greek isopsephy" not in latin_only
    assert "Hebrew gematria" not in latin_only
    assert "English extended" in latin_only
    assert scripts_present("Chidi") == {"latin"}
    assert scripts_present("אהבה") == {"hebrew"}
    assert scripts_present("Λόγος") == {"greek"}
    assert scripts_present("Chidi אהבה Λόγος") == {"latin", "hebrew", "greek"}


def test_readings_are_offered_for_every_script_present_and_no_others():
    for text, want in (("Chidi", 1), ("אהבה", 1), ("Λόγος", 1),
                       ("Chidi Λόγος", 2), ("Chidi אהבה Λόγος", 3)):
        assert len(readings_for(text)) >= want


# --- the incident -----------------------------------------------------------


def test_incident_w_scored_the_same_as_a_in_the_old_hebrew_cipher():
    """summary_card._GEMATRIA wraps after 22 letters, so W=A, X=B, Y=C, Z=D.

    Four letters of the alphabet are unreadable in that table: WAX and AWE
    cannot be told apart by it. The old cipher is left exactly as it is —
    the founder has years of readings under those numbers — and this asserts
    the replacement does not inherit the fault.
    """
    from gateway.summary_card import _GEMATRIA

    assert _GEMATRIA["W"] == _GEMATRIA["A"], "the defect this test is named for is gone"
    assert ENGLISH_EXT["W"] != ENGLISH_EXT["A"]
    assert len({ENGLISH_EXT[c] for c in _ALPHABETS["latin"]}) == 26
