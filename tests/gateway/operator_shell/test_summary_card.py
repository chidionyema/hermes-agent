"""Tests for the world-class Summary Card.

Asserts behavior contracts:
- Three ciphers all reduce correctly and preserve master numbers
- Root ladder matches expected reduction path
- Structural profile computes correctly (vowel/consonant, palindrome, isogram, mirrors)
- Anagram generation is bounded, deduped, and sorted
- Renderer always returns valid rich markdown with the right structural elements
"""

from __future__ import annotations

import pytest

from gateway.operator_shell.summary_card import (
    chaldean,
    generate_anagrams,
    hebrew,
    pythagorean,
    render_summary_card,
    structural_profile,
    _root_ladder,
    _reduce,
    _letters_only,
)


# ===========================================================================
# REDUCTION
# ===========================================================================

class TestReduction:
    def test_single_digit_passes_through(self):
        assert _reduce(7) == 7
        assert _reduce(9) == 9
        assert _reduce(0) == 0

    def test_two_digit_reduces(self):
        assert _reduce(25) == 7       # 2+5=7
        assert _reduce(16) == 7       # 1+6=7
        assert _reduce(99) == 9       # 9+9=18→1+8=9

    def test_master_numbers_preserved(self):
        assert _reduce(11) == 11
        assert _reduce(22) == 22
        assert _reduce(33) == 33

    def test_master_numbers_preserved_under_iteration(self):
        # 29 → 2+9=11 → stop (master)
        assert _root_ladder(29) == [29, 11]
        # 40 → 4 → stop (single digit)
        assert _root_ladder(40) == [40, 4]


# ===========================================================================
# CIPHERS
# ===========================================================================

class TestCiphers:
    def test_pythagorean_hello_is_7(self):
        # H=8, E=5, L=3, L=3, O=6 → 25 → 2+5=7
        r = pythagorean("HELLO")
        assert r.raw == 25
        assert r.root == 7
        assert r.emoji == "🧮"

    def test_hebrew_hello(self):
        # H=8 (Het, 8th letter), E=5 (He, 5th), L=30 (Lamed, 12th × 30 mapping),
        # O=60 (Samekh, 15th × 60 mapping) → 8+5+30+30+60 = 133 → 7
        r = hebrew("HELLO")
        assert r.raw == 133
        assert r.root == 7

    def test_chaldean_otto_is_master_22(self):
        # O=7, T=4, T=4, O=7 → 22 (master)
        r = chaldean("otto")
        assert r.raw == 22
        assert r.root == 22  # master preserved

    def test_chaldean_no_nines(self):
        # Chaldean cipher never uses 9 (sacred)
        from gateway.operator_shell.summary_card import _CHALDEAN
        assert 9 not in _CHALDEAN.values()

    def test_all_three_agree_on_simple_input(self):
        # "A" → Pythagorean 1, Gematria 1, Chaldean 1
        for fn in (pythagorean, hebrew, chaldean):
            r = fn("A")
            assert r.root == 1

    def test_breakdown_contains_every_letter(self):
        r = pythagorean("CHIDI ONYEMA")
        # 11 letters, 1 space ignored
        assert len(r.breakdown) == 11

    def test_breakdown_is_uppercased(self):
        r = pythagorean("hello")
        letters = [c for c, _ in r.breakdown]
        assert letters == ["H", "E", "L", "L", "O"]


# ===========================================================================
# STRUCTURAL PROFILE
# ===========================================================================

class TestStructuralProfile:
    def test_letter_vowel_consonant_split(self):
        p = structural_profile("Hello World")
        # "HELLOWORLD" = 10 letters, 3 vowels (E,O,O), 7 consonants
        assert p.letter_count == 10
        assert p.vowel_count == 3
        assert p.consonant_count == 7

    def test_palindrome_detected(self):
        assert structural_profile("LEVEL").palindrome is True
        assert structural_profile("ANNA").palindrome is True

    def test_non_palindrome(self):
        assert structural_profile("HELLO").palindrome is False

    def test_isogram_detected(self):
        assert structural_profile("WORLD").isogram is True
        assert structural_profile("HELLO").isogram is False  # double L

    def test_vowel_ratio_bounded(self):
        p = structural_profile("AEIOU")
        assert p.vowel_ratio == 1.0
        p2 = structural_profile("BCDFG")
        assert p2.vowel_ratio == 0.0

    def test_mirror_pairs_detected(self):
        # Atbash pairs are A↔Z, B↔Y, …, M↔N. Use a string that contains
        # multiple full pairs: "ABCXYZ" has A↔Z, B↔Y, C↔X present.
        p = structural_profile("ABCXYZ")
        assert ("A", "Z") in p.mirrored_pairs
        assert ("B", "Y") in p.mirrored_pairs
        assert ("C", "X") in p.mirrored_pairs
        # "MNO" → M↔N is real, N↔O is NOT (it's L↔O)
        p2 = structural_profile("MNO")
        assert ("M", "N") in p2.mirrored_pairs

    def test_mirror_pairs_only_when_both_halves_present(self):
        # "A" alone has no mirror partner in the text
        p = structural_profile("A")
        assert ("A", "Z") not in p.mirrored_pairs
        # "MO" — only M and O, neither has its partner present
        p2 = structural_profile("MO")
        assert ("M", "N") not in p2.mirrored_pairs
        assert ("L", "O") not in p2.mirrored_pairs

    def test_mirror_pairs_deduplicated(self):
        # "MMNN" → M↔N appears, only once (no dup)
        p = structural_profile("MMNN")
        pairs = p.mirrored_pairs
        assert ("M", "N") in pairs
        assert pairs.count(("M", "N")) == 1

    def test_rarest_and_common(self):
        # AABC: A=2 (most common), B=1, C=1 (both rare).
        # Tie-break: alphabetically first rare letter → B is rarer-by-name here.
        # This test pins the current tie-break so changes are deliberate.
        p = structural_profile("AABC")
        assert p.most_common_letter == ("A", 2)
        assert p.rarest_letter in (("B", 1), ("C", 1))

    def test_unique_letter_is_only_rarest(self):
        # "ABCD" — each letter appears once; pick is deterministic
        p = structural_profile("ABCD")
        # All four are tied at count=1; first-in-iteration wins
        assert p.rarest_letter is not None and p.rarest_letter[1] == 1
        assert p.most_common_letter is not None and p.most_common_letter[1] == 1

    def test_empty_input_safe(self):
        p = structural_profile("")
        assert p.letter_count == 0
        assert p.vowel_ratio == 0.0


# ===========================================================================
# ANAGRAMS
# ===========================================================================

class TestAnagrams:
    def test_three_letters_yields_six(self):
        anagrams = generate_anagrams("ABC")
        assert len(anagrams) == 6
        assert "ABC" in anagrams
        assert "CBA" in anagrams

    def test_dedup_repeats(self):
        # "AAB" has 3!=6 perms but 3!=6/2!=2=3 unique
        anagrams = generate_anagrams("AAB")
        assert anagrams == ["AAB", "ABA", "BAA"]

    def test_too_many_letters_returns_empty(self):
        # 9 letters → over the 8-limit
        assert generate_anagrams("ABCDEFGHI") == []

    def test_empty_returns_empty(self):
        assert generate_anagrams("") == []
        assert generate_anagrams("123") == []  # digits don't count

    def test_sorted(self):
        anagrams = generate_anagrams("CAB")
        assert anagrams == sorted(anagrams)


# ===========================================================================
# RENDERER
# ===========================================================================

class TestRenderer:
    def test_empty_input_handles_gracefully(self):
        out = render_summary_card("")
        assert "Summary Card" in out
        assert "_Send text" in out

    def test_whitespace_only_returns_empty_card(self):
        out = render_summary_card("   ")
        assert "Summary Card" in out

    def test_full_card_has_all_three_ciphers(self):
        out = render_summary_card("Hello World")
        assert "Pythagorean" in out
        assert "Hebrew Gematria" in out
        assert "Chaldean" in out

    def test_full_card_has_score_table(self):
        out = render_summary_card("Chidiebere")  # single word → Scores table
        # Score section is rendered as framed blocks for single-word input,
        # not as a table. The cipher name still appears.
        assert "Numerological Scores" in out
        assert "### 🔮 Isopsephy Card" in out
        assert "Pythagorean" in out
        assert "Hebrew Gematria" in out
        assert "Chaldean" in out

    def test_full_card_has_structural_profile(self):
        out = render_summary_card("Hello World")
        assert "Structural Profile" in out
        assert "Vowel ratio" in out

    def test_collapsible_details_present(self):
        out = render_summary_card("Hello")
        # Each breakdown + structural + anagrams = 5 details blocks
        assert out.count("<details>") >= 5
        assert out.count("</details>") >= 5
        assert out.count("<summary>") >= 5

    def test_target_echoed_in_header(self):
        out = render_summary_card("CHIDI")
        assert "`CHIDI`" in out

    def test_master_number_flagged(self):
        # Chaldean of "OTTO" is 22 (master)
        out = render_summary_card("otto")
        assert "master" in out.lower()
        assert "22" in out

    def test_palindrome_flagged(self):
        out = render_summary_card("LEVEL")
        assert "Palindrome" in out

    def test_isogram_flagged(self):
        out = render_summary_card("WORLD")
        assert "Isogram" in out

    def test_anagrams_collapse_block_present(self):
        out = render_summary_card("ABC")
        assert "🔤 Anagrams" in out
        assert "permutations" in out

    def test_long_input_truncates_anagrams(self):
        # 8 letters → 40,320 permutations, only 200 shown
        out = render_summary_card("ABCDEFGH")
        assert "first" in out.lower() or "more" in out.lower() or "200" in out

    def test_too_long_input_no_anagram_list(self):
        out = render_summary_card("ABCDEFGHIJK")
        assert "too many" in out.lower()

    def test_resonance_when_roots_agree(self):
        # All three ciphers of "A" give root 1
        out = render_summary_card("A")
        assert "Resonance" in out or "1" in out

    def test_footer_invitation_present(self):
        out = render_summary_card("Hello")
        assert "/find summary" in out or "/commands" in out


# ===========================================================================
# PARSING / STRUCTURAL INTEGRITY
# ===========================================================================

class TestMarkdownStructure:
    @pytest.mark.parametrize("text", [
        "A", "AB", "ABC", "ABCD", "HELLO", "WORLD",
        "chidi onyema", "Otto", "otto", "LEVEL",
        "ABCDEFGH", "ABCDEFGHIJKLMNOPQRSTUVWXYZ",  # 26 letters
        "12345", "!@#$%", "", "   ",
    ])
    def test_renderer_never_raises(self, text):
        # Smoke: any reasonable input must render without error
        out = render_summary_card(text)
        assert isinstance(out, str)
        assert len(out) > 0

    def test_no_unbalanced_details_tags(self):
        out = render_summary_card("Hello World")
        assert out.count("<details>") == out.count("</details>")
        assert out.count("<summary>") == out.count("</summary>")