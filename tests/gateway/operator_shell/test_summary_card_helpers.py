"""Summary Card helpers — direct unit tests for rendering utilities.

These cover the helper functions used by the platform renderers. They were
uncovered before F-NEW-IMPROVE-8, which left bugs like the broken SMS
parser free to regress.
"""
from __future__ import annotations

import pytest

from gateway.operator_shell.summary_card import (
    _MAX_INPUT_CHARS,
    _anagram_factorial_str,
    _breakdown_table,
    _escape_markdownv2,
    _ladder_diagram,
    _letter_count_dropped,
    _letters_only,
    _md_to_html,
    _md_to_mrkdwn,
    _ratio_bar,
    _render_sms,
    _score_chip,
    _strip_markdown,
    parse_compare_args,
    render_compare_card,
    render_summary_card,
    render_summary_json,
)


# ── _letters_only + _letter_count_dropped (F-NEW-IMPROVE-2) ────────────────


def test_letters_only_keeps_ascii_only():
    assert _letters_only("Hello World") == ["H", "E", "L", "L", "O", "W", "O", "R", "L", "D"]


def test_letters_only_drops_cyrillic_silently():
    """Non-ASCII letters must not reach the cipher dict (KeyError risk)."""
    assert _letters_only("Привет") == []


def test_letters_only_drops_accented():
    assert _letters_only("café") == ["C", "A", "F"]


def test_letter_count_dropped_cyrillic():
    assert _letter_count_dropped("Привет") == 6  # П, р, и, в, е, т


def test_letter_count_dropped_accented():
    assert _letter_count_dropped("café") == 1  # é


def test_letter_count_dropped_pure_ascii():
    assert _letter_count_dropped("Hello World 123!") == 0


def test_render_summary_card_handles_cyrillic_without_crash():
    """Cyrillic input must not raise KeyError (regression: F-NEW-IMPROVE-2)."""
    card = render_summary_card("Привет мир")
    assert "non-ASCII letter(s) dropped" in card


def test_render_summary_card_truncates_huge_input():
    """Defensive cap: >_MAX_INPUT_CHARS inputs are truncated, not crashed."""
    huge = "A" * (_MAX_INPUT_CHARS + 1000)
    card = render_summary_card(huge)
    assert "truncated" in card
    assert "chars" in card


# ── _score_chip ──────────────────────────────────────────────────────────


def test_score_chip_basic():
    assert _score_chip(7, False) == "**7**"


def test_score_chip_master():
    out = _score_chip(22, True)
    assert "22" in out
    assert "master" in out
    assert "⚡" in out


def test_score_chip_power_numbers():
    for n in (1, 8, 9):
        out = _score_chip(n, False)
        assert "power" in out


# ── _ratio_bar ───────────────────────────────────────────────────────────


def test_ratio_bar_full():
    bar = _ratio_bar(1.0, width=10)
    assert bar == "█" * 10


def test_ratio_bar_empty():
    bar = _ratio_bar(0.0, width=10)
    assert bar == "░" * 10


def test_ratio_bar_half():
    bar = _ratio_bar(0.5, width=10)
    assert bar.count("█") == 5
    assert bar.count("░") == 5


def test_ratio_bar_default_width():
    bar = _ratio_bar(0.0)
    assert len(bar) == 20  # default width


# ── _breakdown_table ──────────────────────────────────────────────────────


def test_breakdown_table_empty():
    assert "No letters" in _breakdown_table([], 0)


def test_breakdown_table_contains_letters():
    out = _breakdown_table([("A", 1), ("B", 2), ("C", 3)], 6)
    # Backticks wrap each letter for inline-code styling
    assert "| `A` |" in out
    assert "| `B` |" in out
    assert "| `C` |" in out


def test_breakdown_table_total():
    out = _breakdown_table([("A", 1), ("B", 2)], 3)
    assert "**3**" in out


# ── _ladder_diagram ──────────────────────────────────────────────────────


def test_ladder_diagram_single():
    # Single-step is annotated, not raw
    assert _ladder_diagram([7]) == "`7` _(single step)_"


def test_ladder_diagram_full():
    assert _ladder_diagram([61, 13, 4]) == "61 → 13 → 4"


# ── _anagram_factorial_str ────────────────────────────────────────────────


def test_anagram_factorial_str_small():
    assert _anagram_factorial_str(3) == "6"


def test_anagram_factorial_str_large():
    out = _anagram_factorial_str(20)
    assert "20!" in out
    assert "10^" in out


# ── _strip_markdown ───────────────────────────────────────────────────────


def test_strip_markdown_bold():
    assert _strip_markdown("**hello**") == "hello"


def test_strip_markdown_details_keeps_summary():
    out = _strip_markdown("<details>\n<summary>Pythagorean</summary>\nbody\n</details>")
    assert "Pythagorean" in out
    assert "<details>" not in out


def test_strip_markdown_pipe_table():
    out = _strip_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "|" not in out
    assert "A" in out and "B" in out


def test_strip_markdown_collapses_whitespace():
    assert "  " not in _strip_markdown("hello\n\n\n  world")


# ── _escape_markdownv2 ────────────────────────────────────────────────────


def test_escape_markdownv2_special_chars():
    """Per Telegram Bot API, special chars must be escaped."""
    out = _escape_markdownv2("a_b*c[d]e(f)g~h`i>j#k+l-m=n|o{p}q.r!s")
    for ch in "_*[]()~`>#+-=|{}.!":
        assert "\\" + ch in out, f"{ch} not escaped"


def test_escape_markdownv2_preserves_code_blocks():
    out = _escape_markdownv2("```\ncode_with_special*chars\n```")
    # The fenced block content should be preserved unescaped (slated for code rendering)
    assert "code_with_special*chars" in out


# ── _md_to_mrkdwn ─────────────────────────────────────────────────────────


def test_md_to_mrkdwn_bold_double_to_single():
    # Slack uses single asterisks; double → single conversion
    out = _md_to_mrkdwn("**hello**")
    assert "*hello*" in out
    assert "**hello**" not in out  # original double-asterisk replaced


def test_md_to_mrkdwn_pipe_table_becomes_code():
    out = _md_to_mrkdwn("| A | B |\n| 1 | 2 |")
    assert "```" in out


def test_md_to_mrkdwn_bullets_to_unicode():
    out = _md_to_mrkdwn("- one\n- two")
    assert "• one" in out
    assert "• two" in out


# ── _md_to_html ───────────────────────────────────────────────────────────


def test_md_to_html_bold_to_strong():
    out = _md_to_html("**hello**")
    assert "<strong>hello</strong>" in out


def test_md_to_html_header_levels():
    out = _md_to_html("# Title\n## Sub\n### Tiny")
    assert "<h1>Title</h1>" in out
    assert "<h2>Sub</h2>" in out
    assert "<h3>Tiny</h3>" in out


def test_md_to_html_escapes_user_content():
    """User text with < > & must be escaped, NOT injected as HTML."""
    out = _md_to_html("safe **bold** with <script>")
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_md_to_html_link_preserved():
    out = _md_to_html("[label](https://example.com)")
    assert "<a href='https://example.com'>label</a>" in out


# ── _render_sms (F-NEW-IMPROVE-1: was returning ?/?/?) ──────────────────


def test_render_sms_extracts_correct_roots():
    """Bug fix: SMS parser used to look for pipe tables that don't exist."""
    card = render_summary_card("HELLO")
    sms = _render_sms(card, "HELLO")
    assert "Summary: roots 7/7/5" in sms  # matches live observation


def test_render_sms_includes_structural_profile():
    card = render_summary_card("HELLO WORLD")
    sms = _render_sms(card, "HELLO WORLD")
    # V/C and unique counts must survive
    assert "V/" in sms
    assert "unique" in sms


def test_render_sms_within_three_segment_limit():
    """SMS gateway convention: ≤ 480 chars (3 segments × 160)."""
    card = render_summary_card("a" * 1000)
    sms = _render_sms(card, "x" * 1000)
    assert len(sms) <= 480


def test_render_sms_handles_long_input():
    """Even very long input must produce valid SMS, not crash."""
    card = render_summary_card("A" * 500)
    sms = _render_sms(card, "A" * 500)
    assert sms
    assert "Summary:" in sms


# ── JSON output (F-NEW-IMPROVE-6) ─────────────────────────────────────────


def test_render_summary_json_basic():
    out = render_summary_json("HELLO")
    assert out["target"] == "HELLO"
    assert out["truncated"] is False
    assert out["dropped_non_ascii"] == 0
    assert "pythagorean" in out["ciphers"]
    assert out["ciphers"]["pythagorean"]["root"] == 7
    assert out["ciphers"]["pythagorean"]["master"] is False
    assert isinstance(out["ciphers"]["pythagorean"]["ladder"], list)
    assert out["profile"]["letter_count"] == 5
    # HELLO has a repeated L (2 of them), so unique perms = 5!/2! = 60
    assert out["anagrams"]["count"] == 60


def test_render_summary_json_master_flag():
    """OTTO has Chaldean root 22 (master) — JSON must surface it."""
    out = render_summary_json("OTTO")
    assert out["ciphers"]["chaldean"]["master"] is True
    assert out["ciphers"]["chaldean"]["root"] == 22


def test_render_summary_json_dropped_count():
    out = render_summary_json("Привет")
    assert out["dropped_non_ascii"] == 6
    # target is preserved as-is (so consumers see original input);
    # profile reflects what was actually analyzed
    assert out["target"] == "Привет"
    assert out["profile"]["letter_count"] == 0  # all dropped


def test_render_summary_json_truncation_flag():
    huge = "A" * (_MAX_INPUT_CHARS + 500)
    out = render_summary_json(huge)
    assert out["truncated"] is True
    assert len(out["target"]) == _MAX_INPUT_CHARS


def test_render_summary_json_serializable():
    """Output must round-trip through json.dumps (no dataclass leak)."""
    import json
    out = render_summary_json("HELLO")
    encoded = json.dumps(out)  # must not raise
    assert "pythagorean" in encoded


# ── Compare mode (F-NEW-IMPROVE-5) ────────────────────────────────────────


def test_parse_compare_args_vs():
    assert parse_compare_args("HELLO vs WORLD") == ("HELLO", "WORLD")


def test_parse_compare_args_v():
    assert parse_compare_args("HELLO v WORLD") == ("HELLO", "WORLD")


def test_parse_compare_args_versus():
    assert parse_compare_args("HELLO versus WORLD") == ("HELLO", "WORLD")


def test_parse_compare_args_none():
    assert parse_compare_args("just plain text") is None


def test_render_compare_card_produces_md():
    out = render_compare_card("HELLO", "WORLD")
    assert "⚖️ Isopsephy Compare" in out
    assert "A: HELLO" in out
    assert "B: WORLD" in out
    assert "Pythagorean" in out


def test_render_compare_card_delta_signs():
    """Delta column must show positive/negative/zero correctly."""
    out = render_compare_card("HELLO", "WORLD")
    assert "+2" in out  # Pythagorean: 7 → 9
    assert "-2" in out  # Hebrew: 7 → 5


def test_render_compare_card_shared_letters():
    out = render_compare_card("HELLO", "WORLD")
    assert "Letter Overlap" in out
    # H and L and O are shared (HELLO∩WORLD)
    assert "**Shared**" in out


def test_render_compare_card_resonance_verdict():
    """When all 3 roots match, verdict should be full resonance."""
    # WORLD and WORLD have identical roots — they trivially resonate
    out = render_compare_card("WORLD", "WORLD")
    assert "Full resonance" in out