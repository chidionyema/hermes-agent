"""
Tests for multi-token name parsing in the Summary Card.

The Summary Card engine treats whitespace-separated tokens (with ≥2 letters)
as name *parts* and analyzes each one independently in addition to the
combined string. This lets users type a full name and get a per-token
breakdown plus the overall numbers.

Note: the card's presentation was upgraded from tables to framed code blocks
(see the visual upgrade). Tests assert the *behavior* (parts exist, totals
match, insights fire) rather than the exact rendering.

Every band string below is DERIVED from ``_band``, the function the renderer
uses, instead of typed out. On 2026-08-19 the layout changed and two tests
here kept passing while asserting that a "Name Parts" header was absent,
against a header the renderer had stopped emitting at all: a ``not in``
assertion on a dead string can never fail. Deriving the string means a
renamed band breaks the test that pins it instead of silencing it.
"""
from __future__ import annotations

import pytest

from gateway.operator_shell.summary_card import _band, render_summary_card


TARGET_FULL = "Chidiebere onyema"
TARGET_SINGLE = "Chidiebere"


def _part_band(token: str, letters: int) -> str:
    """The in-card band the renderer emits for one name part."""
    return _band(f"{token.upper()} \u00b7 {letters} letters")


class TestSingleWordInput:
    """Single-word targets must NOT trigger the Name Parts section."""

    def test_no_name_parts_section(self):
        out = render_summary_card(TARGET_SINGLE)
        # One word, so no per-part band and no COMBINED band: the single
        # SCORES band carries the numbers instead.
        assert _band("SCORES") in out
        assert _part_band(TARGET_SINGLE, 10) not in out
        assert _band("COMBINED \u00b7 10 letters") not in out

    def test_core_sections_present(self):
        out = render_summary_card(TARGET_SINGLE)
        # Header band, score section, profile, breakdowns all present
        assert "Isopsephy Card" in out
        assert _band("SCORES") in out
        assert "Structural Profile" in out
        assert "Detailed Breakdowns" in out


class TestMultiWordInput:
    """Multi-word targets MUST produce a Name Parts section."""

    def test_name_parts_section_present(self):
        out = render_summary_card(TARGET_FULL)
        assert _part_band("Chidiebere", 10) in out, (
            "Multi-word input must produce a band per name part"
        )
        assert _part_band("onyema", 6) in out

    def test_each_part_appears_in_its_block(self):
        """Each token must have a framed block with its name."""
        out = render_summary_card(TARGET_FULL)
        assert _part_band("Chidiebere", 10) in out
        assert _part_band("onyema", 6) in out
        # The combined string is rendered as a final band
        assert _band("COMBINED \u00b7 16 letters") in out

    def test_combined_totals_match_full_string(self):
        """Σ Combined block's numbers must equal the full-string analysis."""
        out_full = render_summary_card(TARGET_FULL)
        # The combined string analysis: Pythagorean raw 87, root 6.
        # Pinned as exact lines: this is the one place the card's number
        # formatting is asserted literally rather than derived.
        assert "  🧮 Pythag · 87 → 6" in out_full
        assert "  ✡️ Hebrew · 299 → 2" in out_full
        assert "  🌙 Chaldean · 56 → 11 ⚡ master" in out_full
        assert _band("COMBINED \u00b7 16 letters") in out_full


class TestInsightCallouts:
    """When parts reveal discoveries, they must surface in the 💡 section."""

    def test_master_number_insight_present(self):
        """Chaldean yields master 11 for the combined string — must surface."""
        out = render_summary_card(TARGET_FULL)
        assert "💡 Insights" in out
        assert "Master Number 11" in out

    def test_atbash_high_density_insight(self):
        """Combined string has 3 mirror pairs — must surface as high density."""
        out = render_summary_card(TARGET_FULL)
        assert "Atbash mirror pairs" in out
        assert "high mirror density" in out

    def test_power_number_insight_for_onyema(self):
        """'onyema' Pythagorean root is 1 (power) — must surface."""
        out = render_summary_card(TARGET_FULL)
        assert "power number" in out
        assert "onyema" in out


class TestEdgeCases:
    """Whitespace and token-length edge cases."""

    def test_extra_whitespace_collapsed(self):
        out1 = render_summary_card("Chidiebere   onyema")
        out2 = render_summary_card("Chidiebere onyema")
        # Same letter count in combined block
        assert "16 letters" in out1
        assert "16 letters" in out2

    def test_short_tokens_excluded(self):
        """Tokens with ≥2 letters (including 'von') are included."""
        out = render_summary_card("von Chidiebere")
        assert _part_band("von", 3) in out
        assert _part_band("Chidiebere", 10) in out

    def test_single_letter_token_excluded(self):
        """Tokens with <2 letters are dropped ('J Smith' → just Smith)."""
        out = render_summary_card("J Smith")
        # 'J' has one letter, so only 'Smith' survives — one part is no parts.
        assert _band("SCORES") in out
        assert _part_band("Smith", 5) not in out
        assert _part_band("J", 1) not in out