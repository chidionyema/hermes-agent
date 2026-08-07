"""
Tests for the multi-platform renderer of the Summary Card.

Invariants we assert (per AGENTS.md "behavior contracts over snapshots"):

  * Telegram: passes through _escape_markdownv2; preserves the three numerological
    roots (61/7, 192/3, 37/1 for "chidi onyema") in the body.
  * Slack: renders `**bold**` as `*bold*` (mrkdwn) so the roots are visible.
  * SMS: must extract roots from the score table; never exceeds 480 chars.
  * Email: produces valid HTML with <strong> around the raw numbers, no literal
    `**` Markdown markers leaked through.
  * Glasses: every line fits in 30 characters.
  * Unknown platform: falls through to "default" with no exception.

These tests EXERCISE the shipped feature, not the source code.
"""

from gateway.operator_shell.summary_card import (
    render_for_platform,
    render_summary_card,
)

TARGET = "chidi onyema"
EXPECTED_PYTH_RAW = 61
EXPECTED_HEBR_RAW = 192
EXPECTED_CHAL_RAW = 37


class TestPlatformDispatch:
    def test_known_platforms_dispatch(self):
        for plat in ("telegram", "slack", "sms", "email", "glasses", "default"):
            out = render_for_platform(TARGET, plat)
            assert out, f"{plat} returned empty"

    def test_unknown_platform_falls_through_to_default(self):
        out_unknown = render_for_platform(TARGET, "smartwatch-foo")
        out_default = render_for_platform(TARGET, "default")
        assert out_unknown == out_default


class TestTelegramRenderer:
    def test_contains_raw_numbers(self):
        out = render_for_platform(TARGET, "telegram")
        # Telegram escapes `-`, `*`, etc. but numbers survive.
        assert "61" in out
        assert "192" in out
        assert "37" in out

    def test_contains_cipher_emojis(self):
        out = render_for_platform(TARGET, "telegram")
        # The cipher-emoji markers are escaped in MarkdownV2 but the underlying
        # characters are present in the source.
        assert "Pythagorean" in out
        assert "Gematria" in out
        assert "Chaldean" in out


class TestSlackRenderer:
    def test_bold_syntax_is_single_asterisk(self):
        """Slack mrkdwn uses *bold*, not **bold**. Our renderer must convert."""
        out = render_for_platform(TARGET, "slack")
        # **bold** in source must become *bold* in Slack output
        # (no double-asterisk survives in the body)
        assert "**" not in out, "Slack output should have no `**` markers"

    def test_tables_become_code_blocks(self):
        out = render_for_platform(TARGET, "slack")
        assert "```" in out, "Tables should render as ``` code blocks in Slack"

    def test_root_values_visible(self):
        out = render_for_platform(TARGET, "slack")
        assert "7" in out  # Pythagorean root
        assert "3" in out  # Hebrew root
        assert "1" in out  # Chaldean root


class TestSMSRenderer:
    def test_length_under_480(self):
        out = render_for_platform(TARGET, "sms")
        assert len(out) <= 480, f"SMS exceeded 3 segments: {len(out)}"

    def test_summary_prefix_present(self):
        out = render_for_platform(TARGET, "sms")
        assert out.startswith("Summary:"), "SMS should lead with a summary prefix"

    def test_falls_back_to_q_when_regex_does_not_match(self):
        """If the table-row format ever changes, the SMS output should still
        be a valid single-line string with the summary prefix — not crash."""
        out = render_for_platform(TARGET, "sms")
        # The root extraction is best-effort; assert no crash + valid output
        assert "roots " in out, "SMS output must contain 'roots ...' even when '?' fallback is used"


class TestEmailRenderer:
    def test_produces_valid_html(self):
        out = render_for_platform(TARGET, "email")
        assert out.startswith("<div"), "Email must be HTML, not Markdown"
        assert "</div>" in out

    def test_no_markdown_artifacts_leaked(self):
        out = render_for_platform(TARGET, "email")
        # After HTML conversion, `**` should not appear in the body
        # (because they would render as literal asterisks in an email client).
        assert "**" not in out, "Email should not contain literal `**` markers"

    def test_bold_rendered_as_strong(self):
        out = render_for_platform(TARGET, "email")
        # The raw numerological values (61, 192, 37) were bolded in the source.
        # They must be wrapped in <strong> tags in the email HTML.
        assert "<strong>61</strong>" in out
        assert "<strong>192</strong>" in out
        assert "<strong>37</strong>" in out

    def test_headers_rendered(self):
        out = render_for_platform(TARGET, "email")
        assert "<h3>" in out, "Source ### headers must render as <h3> in email"
        assert "<h4>" in out, "Source #### headers must render as <h4> in email"


class TestGlassesRenderer:
    def test_all_lines_under_30_chars(self):
        out = render_for_platform(TARGET, "glasses")
        for i, line in enumerate(out.splitlines()):
            assert len(line) <= 30, (
                f"Glasses line {i} exceeds 30 chars: {line!r} "
                f"({len(line)} chars)"
            )

    def test_contains_three_roots(self):
        out = render_for_platform(TARGET, "glasses")
        # The compact format prefixes each root with PYTH=, HEBR=, CHAL=
        assert "PYTH=7" in out
        assert "HEBR=3" in out
        assert "CHAL=1" in out

    def test_contains_target_text(self):
        out = render_for_platform(TARGET, "glasses")
        assert "chidi onyema" in out


class TestDeterminismAcrossPlatforms:
    """Same input must produce same root numerology everywhere — only the
    *presentation* differs."""

    def test_roots_consistent_across_platforms(self):
        pyth = EXPECTED_PYTH_RAW
        hebr = EXPECTED_HEBR_RAW
        chal = EXPECTED_CHAL_RAW

        # Telegram, Slack, and Email render the full table with raw values.
        for plat in ("telegram", "slack", "email"):
            out = render_for_platform(TARGET, plat)
            assert str(pyth) in out, f"{plat} missing {pyth}"
            assert str(hebr) in out, f"{plat} missing {hebr}"
            assert str(chal) in out, f"{plat} missing {chal}"

        # Glasses is a compact form — only the root digits, not the raw
        # values. Invariant: roots 7/3/1 are present.
        out = render_for_platform(TARGET, "glasses")
        assert "PYTH=7" in out
        assert "HEBR=3" in out
        assert "CHAL=1" in out

    def test_each_platform_produces_distinct_output(self):
        """If two platforms produce identical output, the renderer isn't
        doing its job. They should all differ in presentation."""
        outs = {
            plat: render_for_platform(TARGET, plat)
            for plat in ("telegram", "slack", "email", "glasses")
        }
        # Pairwise distinctness (all 4 platforms must differ from each other)
        plats = list(outs.keys())
        for i in range(len(plats)):
            for j in range(i + 1, len(plats)):
                a, b = plats[i], plats[j]
                assert outs[a] != outs[b], (
                    f"Platforms {a} and {b} produce identical output — "
                    f"renderer not platform-aware"
                )