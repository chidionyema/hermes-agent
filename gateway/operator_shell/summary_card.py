"""Summary Card — world-class numerological, structural, and cryptographic analysis.

When triggered (via /summary or "summary <text>"), renders a rich card with:

* Three numerological ciphers (Pythagorean, Hebrew Gematria, Chaldean)
* Root-number ladder showing the iterative reduction from raw sum to single digit
* Per-cipher breakdowns in collapsible <details> blocks
* Structural profile: vowel/consonant ratio, character composition, mirrored pairs
* Anagram permutations (toggled, paginated for >200 results)
* Native Telegram interactions (Bot API 10.1): collapsible details, task lists
* Reverse / reverse-cumulative / digital-root sequences

Telegram's rich message endpoint already renders ``<details>``, ``<summary>``,
GFM task lists, and pipe tables natively.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass


# ===========================================================================
# NUMEROLOGY — three classical ciphers
# ===========================================================================

# --- Pythagorean: A=1, B=2, … I=9, J=1, K=2, … cyclically (mod 9) -----------
_PYTHAGOREAN: dict[str, int] = {}
_seq9 = (1, 2, 3, 4, 5, 6, 7, 8, 9)
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _PYTHAGOREAN[ch] = _seq9[i % 9]

# --- Hebrew Gematria (Mispar Hechrechi / standard value) --------------------
# 22 Hebrew letters: Aleph=1 … Tav=400. English letters map sequentially,
# wrapping after 22. W/X/Y/Z continue the cycle (U,V continue naturally).
_GEMATRIA: dict[str, int] = {}
_HEBREW_VALS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 30, 40, 50, 60, 70, 80, 90,
                100, 200, 300, 400]
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUV"):
    _GEMATRIA[ch] = _HEBREW_VALS[i % len(_HEBREW_VALS)]
for i, ch in enumerate("WXYZ"):
    _GEMATRIA[ch] = _HEBREW_VALS[i % len(_HEBREW_VALS)]

# --- Chaldean: 1-8 only (9 is sacred) ---------------------------------------
# A=1, B=2, C=3, D=4, E=5, F=8, G=3, H=5, I=1, J=1, K=2, L=3, M=4, N=5, O=7,
# P=8, Q=1, R=2, S=3, T=4, U=6, V=6, W=6, X=5, Y=1, Z=7
_CHALDEAN: dict[str, int] = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 8, "G": 3, "H": 5, "I": 1,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "O": 7, "P": 8, "Q": 1, "R": 2,
    "S": 3, "T": 4, "U": 6, "V": 6, "W": 6, "X": 5, "Y": 1, "Z": 7,
}

VOWELS = set("AEIOU")


@dataclass(frozen=True)
class CipherResult:
    name: str
    emoji: str
    raw: int          # sum before reduction
    root: int         # single digit (or master number)
    breakdown: list[tuple[str, int]]


def _letters_only(text: str) -> list[str]:
    return [c.upper() for c in text if c.isalpha()]


def _reduce(n: int) -> int:
    """Reduce to single digit, preserving master numbers 11, 22, 33."""
    while n > 9:
        if n in (11, 22, 33):
            return n
        n = sum(int(d) for d in str(n))
    return n


def _root_ladder(raw: int) -> list[int]:
    """Full reduction ladder: each step sums digits until < 10 or master."""
    ladder: list[int] = [raw]
    while ladder[-1] > 9:
        if ladder[-1] in (11, 22, 33):
            break
        ladder.append(sum(int(d) for d in str(ladder[-1])))
    return ladder


def _is_master(n: int) -> bool:
    return n in (11, 22, 33)


def pythagorean(text: str) -> CipherResult:
    letters = _letters_only(text)
    bd = [(c, _PYTHAGOREAN[c]) for c in letters]
    raw = sum(v for _, v in bd)
    return CipherResult("Pythagorean", "🧮", raw, _reduce(raw), bd)


def hebrew(text: str) -> CipherResult:
    letters = _letters_only(text)
    bd = [(c, _GEMATRIA[c]) for c in letters]
    raw = sum(v for _, v in bd)
    return CipherResult("Hebrew Gematria", "✡️", raw, _reduce(raw), bd)


def chaldean(text: str) -> CipherResult:
    letters = _letters_only(text)
    bd = [(c, _CHALDEAN[c]) for c in letters]
    raw = sum(v for _, v in bd)
    return CipherResult("Chaldean", "🌙", raw, _reduce(raw), bd)


# ===========================================================================
# STRUCTURAL PROFILE
# ===========================================================================

@dataclass(frozen=True)
class StructuralProfile:
    char_count: int
    letter_count: int
    vowel_count: int
    consonant_count: int
    digit_count: int
    space_count: int
    unique_letters: int
    bigram_diversity: float     # unique bigrams / total bigrams (0..1)
    vowel_ratio: float          # vowels / letters
    palindrome: bool
    isogram: bool               # no repeated letters
    mirrored_pairs: list[tuple[str, str]]  # A↔Z, B↔Y, etc.
    rarest_letter: tuple[str, int] | None
    most_common_letter: tuple[str, int] | None


# _MIRROR_MAP is the Atbash mapping for the English alphabet:
# A↔Z, B↔Y, C↔X, D↔W, E↔V, F↔U, G↔T, H↔S, I↔R, J↔Q, K↔P, L↔O, M↔N.
# Defined on all 26 letters so we can look up either half.
_MIRROR_MAP: dict[str, str] = {
    "A": "Z", "B": "Y", "C": "X", "D": "W", "E": "V",
    "F": "U", "G": "T", "H": "S", "I": "R", "J": "Q",
    "K": "P", "L": "O", "M": "N",
    # Second half (mirror of the first half)
    "N": "M", "O": "L", "P": "K", "Q": "J", "R": "I",
    "S": "H", "T": "G", "U": "F", "V": "E", "W": "D",
    "X": "C", "Y": "B", "Z": "A",
}


def structural_profile(text: str) -> StructuralProfile:
    raw = text.upper()
    letters = _letters_only(text)
    counter = Counter(letters)
    vowels = [c for c in letters if c in VOWELS]
    consonants = [c for c in letters if c.isalpha() and c not in VOWELS]
    digits = [c for c in text if c.isdigit()]
    spaces = [c for c in text if c == " "]

    # bigram diversity
    bigrams = ["".join(p) for p in zip(letters, letters[1:])]
    bigram_div = len(set(bigrams)) / len(bigrams) if bigrams else 0.0

    # mirrored pairs (Atbash-style): present pairs in text
    # A pair (A,B) is present when BOTH halves appear at least once.
    # Walk every letter whose mirror is lex-smaller so we add each pair once.
    mirrored: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for c in set(counter):  # dedup before iterating
        m = _MIRROR_MAP.get(c)
        if m is None or c == m:
            continue
        # Normalize so the smaller letter is always first
        pair = (c, m) if c < m else (m, c)
        if pair in seen_pairs:
            continue
        # Both halves must be present in the text
        if counter.get(c, 0) >= 1 and counter.get(m, 0) >= 1:
            mirrored.append(pair)
            seen_pairs.add(pair)
    mirrored.sort()

    rarest = min(counter.items(), key=lambda kv: kv[1]) if counter else None
    common = max(counter.items(), key=lambda kv: kv[1]) if counter else None

    palindrome = "".join(letters) == "".join(reversed(letters)) and len(letters) > 1
    isogram = len(counter) == len(letters)

    return StructuralProfile(
        char_count=len(text),
        letter_count=len(letters),
        vowel_count=len(vowels),
        consonant_count=len(consonants),
        digit_count=len(digits),
        space_count=len(spaces),
        unique_letters=len(counter),
        bigram_diversity=bigram_div,
        vowel_ratio=len(vowels) / len(letters) if letters else 0.0,
        palindrome=palindrome,
        isogram=isogram,
        mirrored_pairs=sorted(mirrored),
        rarest_letter=rarest,
        most_common_letter=common,
    )


# ===========================================================================
# ANAGRAMS (limited to prevent combinatorial explosion)
# ===========================================================================

_MAX_ANAGRAM_LETTERS = 8
_MAX_ANAGRAM_DISPLAY = 200


def generate_anagrams(text: str) -> list[str]:
    """All unique letter permutations (sorted, deduped). ≤ 8 letters only."""
    import itertools

    letters = _letters_only(text)
    if not letters or len(letters) > _MAX_ANAGRAM_LETTERS:
        return []
    perms = sorted({"".join(p) for p in itertools.permutations(letters)})
    return perms


def count_permutations(letter_count: int) -> int:
    import math
    return math.factorial(letter_count)


# ===========================================================================
# RENDERING
# ===========================================================================

def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _ratio_bar(ratio: float, width: int = 20) -> str:
    """Render a unicode block bar for visual proportion (0..1)."""
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def _score_chip(value: int, master: bool) -> str:
    if master:
        return f"⚡ **{value}** (master)"
    if value in (1, 8, 9):
        return f"🌀 **{value}** (power)"
    return f"**{value}**"


def _breakdown_table(breakdown: list[tuple[str, int]], total: int) -> str:
    if not breakdown:
        return "_No letters to score._"
    # compact: collapse consecutive same-letter runs
    counter = Counter(c for c, _ in breakdown)
    rows = ["| Letter | Count | Value | Total |", "|---|---|---|---|"]
    for letter, val in breakdown:
        n = counter[letter]
        rows.append(f"| `{letter}` | {n} | {val} | {val * n} |")
    rows.append(f"| **Σ** | | | **{total:,}** |")
    return "\n".join(rows)


def _ladder_diagram(ladder: list[int]) -> str:
    """Visual ladder showing iterative reduction."""
    if len(ladder) < 2:
        return f"`{ladder[0]}` _(single step)_"
    parts = [str(x) for x in ladder]
    return " → ".join(parts)


def _anagram_factorial_str(letter_count: int) -> str:
    n = count_permutations(letter_count)
    if n <= 1_000_000:
        return f"{n:,}"
    digits = int(math.log10(n))
    return f"{letter_count}! ≈ 10^{digits}"


def render_summary_card(text: str) -> str:
    """Render the world-class summary card for *text*."""
    text = text.strip()
    if not text:
        return "🔮 **Summary Card**\n\n_Send text to analyze._"

    # Run the three ciphers in parallel
    py = pythagorean(text)
    he = hebrew(text)
    ch = chaldean(text)

    # Structural profile
    prof = structural_profile(text)
    anagrams = generate_anagrams(text)
    letter_count = prof.letter_count
    anagram_count = len(anagrams)

    # Parse name parts (whitespace-separated tokens with ≥2 letters)
    parts = [tok for tok in text.split() if len(_letters_only(tok)) >= 2]
    has_parts = len(parts) >= 2

    # Line buffer
    out: list[str] = []

    # =======================================================================
    # HEADER — target + at-a-glance chip row
    # =======================================================================
    out.append("### 🔮 Summary Card")
    out.append("")
    out.append(f"**Target:** `{text}`")
    out.append("")

    # =======================================================================
    # AT-A-GLANCE CHIP ROW — three roots as a single readable line
    # =======================================================================
    py_chip = _score_chip(py.root, _is_master(py.root))
    he_chip = _score_chip(he.root, _is_master(he.root))
    ch_chip = _score_chip(ch.root, _is_master(ch.root))
    out.append(
        f"**At a glance:**  "
        f"🧮 {py_chip} · ✡️ {he_chip} · 🌙 {ch_chip}"
    )
    roots = {py.root, he.root, ch.root}
    if len(roots) == 1:
        resonance = f"🌀 **Resonance** — all three ciphers reduce to **{py.root}**"
    elif len(roots) == 2:
        shared = next(iter(roots))
        resonance = (
            f"🌗 **Partial agreement** — 2 of 3 ciphers reduce to **{shared}**"
        )
    else:
        resonance = "🌈 **All-different** — three distinct root numbers"
    out.append(f"▸ {resonance}")
    out.append("")

    # =======================================================================
    # DIVIDER — visual section break (Telegram collapses blank lines)
    # =======================================================================
    out.append("───")
    out.append("")

    # =======================================================================
    # SCORE CARD — three ciphers, raw + root + ladder preview
    # =======================================================================
    out.append("#### 🎯 Numerological Scores")
    out.append("")
    out.append("| Cipher | Raw | Root | Ladder |")
    out.append("|---|---|---|---|")
    for c in (py, he, ch):
        ladder = _root_ladder(c.raw)
        ladder_short = (
            f"`{c.raw}` → `{c.root}`"
            if len(ladder) <= 2
            else f"`{c.raw}`→`{ladder[1]}`→…→`{c.root}`"
        )
        out.append(
            f"| {c.emoji} {c.name} | **{_fmt_int(c.raw)}** | "
            f"{_score_chip(c.root, _is_master(c.root))} | {ladder_short} |"
        )
    out.append("")
    out.append("───")
    out.append("")

    # =======================================================================
    # STRUCTURAL PROFILE — quick-glance composition
    # =======================================================================
    out.append("#### 🧬 Structural Profile")
    out.append("")
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| Characters | **{prof.char_count}** |")
    out.append(f"| Letters | **{prof.letter_count}** |")
    out.append(f"| Unique letters | **{prof.unique_letters}** of 26 |")
    out.append(
        f"| Vowel ratio | `{_ratio_bar(prof.vowel_ratio)}` "
        f"**{prof.vowel_ratio * 100:.0f}%** "
        f"({prof.vowel_count} V / {prof.consonant_count} C)"
    )
    out.append(
        f"| Bigram diversity | `{_ratio_bar(prof.bigram_diversity)}` "
        f"**{prof.bigram_diversity * 100:.0f}%**"
    )
    if prof.isogram:
        out.append("| ✨ Isogram | _no letter repeats_ |")
    if prof.palindrome:
        out.append("| 🔁 Palindrome | _reads the same forwards & backwards_ |")
    if prof.rarest_letter:
        out.append(
            f"| Rarest letter | `{prof.rarest_letter[0]}` "
            f"×{prof.rarest_letter[1]}"
        )
    if prof.most_common_letter:
        out.append(
            f"| Most common | `{prof.most_common_letter[0]}` "
            f"×{prof.most_common_letter[1]}"
        )
    out.append("")

    # =======================================================================
    # NAME PARTS — multi-token breakdown (only when 2+ words, each with letters)
    # =======================================================================
    if has_parts:
        out.append("───")
        out.append("")
        out.append("#### 🪪 Name Parts")
        out.append("")
        out.append(
            f"_Each part analyzed independently — Pythagorean · "
            f"Hebrew · Chaldean_"
        )
        out.append("")
        out.append("| Part | Letters | 🧮 Pythag | ✡️ Hebrew | 🌙 Chaldean |")
        out.append("|---|---|---|---|---|")
        for tok in parts:
            tok_py = pythagorean(tok)
            tok_he = hebrew(tok)
            tok_ch = chaldean(tok)
            out.append(
                f"| `{tok}` | **{len(_letters_only(tok))}** | "
                f"**{tok_py.raw}**→{_score_chip(tok_py.root, _is_master(tok_py.root))} | "
                f"**{tok_he.raw}**→{_score_chip(tok_he.root, _is_master(tok_he.root))} | "
                f"**{tok_ch.raw}**→{_score_chip(tok_ch.root, _is_master(tok_ch.root))} |"
            )
        out.append("")
        # Combined sum row
        out.append(
            f"| **Σ Combined** | **{letter_count}** | "
            f"**{py.raw}**→{_score_chip(py.root, _is_master(py.root))} | "
            f"**{he.raw}**→{_score_chip(he.root, _is_master(he.root))} | "
            f"**{ch.raw}**→{_score_chip(ch.root, _is_master(ch.root))} |"
        )
        out.append("")

    # =======================================================================
    # DIVIDER — visual section break before collapsibles
    # =======================================================================
    out.append("───")
    out.append("")
    out.append("#### 📐 Detailed Breakdowns")
    out.append("")
    out.append("_Tap to expand each cipher's full math._")
    out.append("")

    # =======================================================================
    # COLLAPSIBLE: per-cipher breakdowns (the math)
    # =======================================================================
    for c in (py, he, ch):
        ladder = _root_ladder(c.raw)
        is_master = _is_master(c.root)
        ladder_str = " → ".join(str(x) for x in ladder)
        ladder_text = (
            " _(single step)_"
            if len(ladder) < 2
            else f"\n_Reduction ladder:_ `{ladder_str}`"
        )
        out.append("<details>")
        out.append(
            f"<summary>{c.emoji} {c.name} — raw {_fmt_int(c.raw)}, "
            f"root {'⚡ master ' if is_master else ''}{c.root}</summary>"
        )
        out.append("")
        out.append(_breakdown_table(c.breakdown, c.raw))
        out.append("")
        if is_master:
            out.append(
                f"⚡ **Master Number {c.root}** — preserved, "
                "not reduced further."
            )
        out.append(f"_{c.name} cipher:_ sum → digit sum until single digit, "
                   "preserving master numbers (11, 22, 33).")
        out.append(ladder_text)
        out.append("</details>")
        out.append("")

    # =======================================================================
    # COLLAPSIBLE: structural deep dive (mirrored pairs, bigrams)
    # =======================================================================
    out.append("<details>")
    out.append("<summary>🪞 Structural Deep Dive — Atbash mirrors & patterns</summary>")
    out.append("")
    if prof.mirrored_pairs:
        pairs_str = " · ".join(
            f"`{a}`↔`{z}`" for a, z in prof.mirrored_pairs
        )
        out.append(f"**Atbash mirrored pairs present:** {pairs_str}")
        out.append("")
        if len(prof.mirrored_pairs) >= 3:
            out.append("✨ High mirror density — strong symmetric signature.")
    else:
        out.append("_No Atbash mirror pairs in this text._")
    out.append("")
    if letter_count > 1:
        bigrams = ["".join(p) for p in zip(_letters_only(text), _letters_only(text)[1:])]
        bigram_counts = Counter(bigrams).most_common(5)
        if bigram_counts:
            out.append("**Top 5 letter bigrams:**")
            for bg, n in bigram_counts:
                out.append(f"- `{bg}` ×{n}")
    out.append("</details>")
    out.append("")

    # =======================================================================
    # COLLAPSIBLE: anagrams (paginated, capped)
    # =======================================================================
    out.append("<details>")
    if anagrams:
        showing = min(anagram_count, _MAX_ANAGRAM_DISPLAY)
        fact = _anagram_factorial_str(letter_count)
        out.append(
            f"<summary>🔤 Anagrams — {anagram_count:,} unique "
            f"({fact} permutations of {letter_count} letters)</summary>"
        )
        out.append("")
        # Render as a checklist so users can visually scan matches
        for i, a in enumerate(anagrams[:_MAX_ANAGRAM_DISPLAY]):
            # Em-dash separator so the list reads as a flow of words, not bullets
            out.append(f"- [ ] `{a}`")
        if anagram_count > _MAX_ANAGRAM_DISPLAY:
            out.append("")
            out.append(
                f"_Showing first {_MAX_ANAGRAM_DISPLAY:,} of {anagram_count:,}. "
                f"Try a shorter input to see them all._"
            )
    else:
        if letter_count == 0:
            out.append("<summary>🔤 Anagrams — no letters found</summary>")
        else:
            out.append(
                f"<summary>🔤 Anagrams — too many letters "
                f"({letter_count} > {_MAX_ANAGRAM_LETTERS})</summary>"
            )
        out.append("")
        out.append("_Send text with 1–8 letters for full anagram analysis._")
    out.append("</details>")
    out.append("")

    # =======================================================================
    # FOOTER — copy-back invitation
    # =======================================================================
    out.append("---")
    out.append(
        f"_Try:_ `/summary {text}` again · `/find summary` · "
        f"`/commands 1`"
    )

    return "\n".join(out)


# ===========================================================================
# MULTI-PLATFORM RENDERERS
# ===========================================================================
#
# The Summary Card engine emits *standard Markdown* (GFM-flavored). Each
# downstream platform speaks its own dialect:
#
#   - Telegram: MarkdownV2 (special chars escaped, **bold**, __italic__)
#   - Slack:    mrkdwn (*bold*, _italic_, no tables, no <details>)
#   - SMS:      Plain text, 160-char segments, no markup at all
#   - Email:    HTML (escaped, <strong>, <details>)
#   - Glasses:  30-char fields, fixed-width, monospace alignment
#   - Default:  Standard markdown (Discord, Matrix, Feishu, API server, …)
#
# Each renderer is a function (card: str, platform: str) -> str. The router
# ``render_for_platform`` dispatches by name and falls back to ``default``.
#
# These renderers do NOT touch platform adapters — adapters consume the
# output string via their own ``format_message`` override. This module is
# purely about *content shaping*, not delivery.

SUPPORTED_PLATFORMS = frozenset({
    "telegram", "slack", "sms", "email", "glasses", "default",
})


def _strip_markdown(text: str) -> str:
    """Reduce GFM markdown to a single plain-text line.

    Used by SMS (no markup) and as a fallback. Drops ``<details>`` blocks
    entirely (they don't render in plain text) and unwraps ``**bold**``,
    ``*italic*``, ``__under__``, ``_under_``, ```code```, ``# headers``,
    pipe tables, and bullet lists. Newlines within the original are
    collapsed to spaces.
    """
    import re

    out = text
    # Drop collapsible blocks — keep only their <summary> contents.
    out = re.sub(
        r"<details>\s*<summary>([^<]+)</summary>",
        r"\1: ",
        out,
        flags=re.DOTALL,
    )
    out = re.sub(r"</?details>", "", out)
    # Headers → plain text
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.MULTILINE)
    # Bold/italic/underline markers
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"\*(.+?)\*", r"\1", out)
    out = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", out)
    out = re.sub(r"~~(.+?)~~", r"\1", out)
    # Inline code
    out = re.sub(r"`([^`]+)`", r"\1", out)
    # Links → text only
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    # Task list markers
    out = re.sub(r"^\s*-\s*\[\s*[xX ]\s*\]\s*", "", out, flags=re.MULTILINE)
    # Bullets
    out = re.sub(r"^\s*[-*]\s+", "", out, flags=re.MULTILINE)
    # Pipe tables → row by row, pipe → space
    out = re.sub(r"^\s*\|", "", out, flags=re.MULTILINE)
    out = out.replace("|", " ")
    # Italic-emoji asterisks (used as visual breaks like "* footnote *")
    # already handled by *...* above
    # Collapse whitespace
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def _escape_markdownv2(text: str) -> str:
    """Escape the characters Telegram MarkdownV2 requires to be escaped.

    Per Telegram Bot API, the chars ``_ * [ ] ( ) ~ ` > # + - = | { } . !``
    must be escaped with a preceding ``\\`` when used outside of protected
    markup.  We protect code spans/backticks first, escape the rest.
    """
    import re

    # Protect inline code and code blocks
    placeholders: dict[str, str] = {}
    counter = [0]

    def _ph(value: str) -> str:
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    # Stash fenced code blocks
    out = re.sub(
        r"(```.*?```)",
        lambda m: _ph(m.group(1)),
        text,
        flags=re.DOTALL,
    )
    # Stash inline code
    out = re.sub(r"(`[^`\n]+`)", lambda m: _ph(m.group(1)), out)

    # Escape the special characters
    special = r"_*[]()~`>#+-=|{}.!"
    out = re.sub(f"([{re.escape(special)}])", r"\\\1", out)

    # Restore code blocks (they came pre-escaped or are themselves code)
    for key, val in placeholders.items():
        out = out.replace(key, val)
    return out


def _md_to_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn.

    Slack uses ``*bold*`` (single asterisks), ``_italic_``, ``~strike~``,
    ``<url|label>`` links, ``> quote``, and does NOT support GFM tables,
    ``<details>``, headers in body, or task lists.
    """
    import re

    # Slack doesn't render <details>; keep <summary> as a line
    out = re.sub(
        r"<details>\s*<summary>([^<]+)</summary>",
        r"> *\1*\n> ",
        text,
        flags=re.DOTALL,
    )
    out = re.sub(r"</?details>", "", out)
    # Headers → bold lines
    out = re.sub(r"^#{1,6}\s*(.+)$", r"> *\1*", out, flags=re.MULTILINE)
    # Bold: ** → * (single)
    out = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out)
    # Italic: __ → _
    out = re.sub(r"__(.+?)__", r"_\1_", out)
    # Strike: ~~ → ~
    out = re.sub(r"~~(.+?)~~", r"~\1~", out)
    # Inline code stays as-is (Slack uses single backticks too)
    # Convert pipe tables to a fixed-width-ish row per line
    out = re.sub(
        r"^\s*\|(.+)\|\s*$",
        lambda m: "```" + m.group(1).strip().replace("|", " | ") + "```",
        out,
        flags=re.MULTILINE,
    )
    # Bullets "- " → "• "
    out = re.sub(r"^\s*-\s+", "• ", out, flags=re.MULTILINE)
    return out


def _md_to_html(text: str) -> str:
    """Convert standard Markdown to a basic HTML email body.

    Supports headers, bold, italic, tables, lists, code, and links.
    Enough for an email-friendly Summary Card.

    Implementation uses a placeholder pattern: substitute MD→HTML tags into
    sentinel markers, escape the rest of the body, then restore the tags.
    This avoids the chicken-and-egg of "escape then inject tags vs inject
    then escape user content."
    """
    import html as _html
    import re

    placeholders: dict[str, str] = {}
    counter = [0]

    def _ph(html_tag: str) -> str:
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = html_tag
        return key

    out = text

    # 1. Headers — substitute before any escape
    out = re.sub(r"^#\s+(.+)$",
                 lambda m: _ph(f"<h1>{m.group(1)}</h1>"),
                 out, flags=re.MULTILINE)
    out = re.sub(r"^##\s+(.+)$",
                 lambda m: _ph(f"<h2>{m.group(1)}</h2>"),
                 out, flags=re.MULTILINE)
    out = re.sub(r"^###\s+(.+)$",
                 lambda m: _ph(f"<h3>{m.group(1)}</h3>"),
                 out, flags=re.MULTILINE)
    out = re.sub(r"^####\s+(.+)$",
                 lambda m: _ph(f"<h4>{m.group(1)}</h4>"),
                 out, flags=re.MULTILINE)
    out = re.sub(r"^#####\s+(.+)$",
                 lambda m: _ph(f"<h5>{m.group(1)}</h5>"),
                 out, flags=re.MULTILINE)

    # 2. Bold / italic / code / strike / links → placeholder-encoded
    #    BEFORE tables, so table cell contents inherit the formatting.
    out = re.sub(r"\*\*(.+?)\*\*",
                 lambda m: _ph(f"<strong>{m.group(1)}</strong>"), out)
    out = re.sub(r"__(.+?)__",
                 lambda m: _ph(f"<u>{m.group(1)}</u>"), out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
                 lambda m: _ph(f"<em>{m.group(1)}</em>"), out)
    out = re.sub(r"(?<!\w)_(.+?)_(?!\w)",
                 lambda m: _ph(f"<em>{m.group(1)}</em>"), out)
    out = re.sub(r"~~(.+?)~~",
                 lambda m: _ph(f"<s>{m.group(1)}</s>"), out)
    out = re.sub(r"`([^`\n]+)`",
                 lambda m: _ph(f"<code>{m.group(1)}</code>"), out)
    out = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: _ph(f"<a href='{m.group(2)}'>{m.group(1)}</a>"),
        out,
    )

    # 3. Pipe tables — substitution runs against text where bold/italic
    #    are already placeholder-encoded, so cell content renders with
    #    <strong>/<em> after the placeholder is restored.
    def _table(m: re.Match[str]) -> str:
        rows = [
            r.strip() for r in m.group(0).strip().splitlines()
            if r.strip() and not re.match(r"^\|[\s\-:|]+\|$", r)
        ]
        if not rows:
            return ""
        cells = [r.strip("|").split("|") for r in rows]
        header = cells[0]
        body = cells[1:]
        html_rows = [
            "<tr>" + "".join(f"<th>{c.strip()}</th>" for c in header) + "</tr>"
        ]
        for row in body:
            html_rows.append(
                "<tr>" + "".join(f"<td>{c.strip()}</td>" for c in row) + "</tr>"
            )
        return _ph("<table border='1' cellpadding='4'>"
                   + "".join(html_rows) + "</table>")

    out = re.sub(
        r"(?:^\|.+\|\s*$\n?)+",
        _table,
        out,
        flags=re.MULTILINE,
    )

    # 4. Bullets → <li>
    out = re.sub(r"^\s*-\s+(.+)$", r"<li>\1</li>", out, flags=re.MULTILINE)
    out = re.sub(r"((?:<li>.*</li>\n?)+)", r"<ul>\1</ul>", out)

    # 5. Escape user content (now safe — all tags are in placeholders,
    #    except <ul>/<li> which we just inserted; that's our own HTML).
    out = _html.escape(out, quote=False)
    out = out.replace("&lt;li&gt;", "<li>").replace("&lt;/li&gt;", "</li>")
    out = out.replace("&lt;ul&gt;", "<ul>").replace("&lt;/ul&gt;", "</ul>")

    # 6. Restore placeholders — repeat until no placeholders remain.
    #    A table placeholder may contain inner placeholders; restoring
    #    in insertion order only swaps the outer one. Repeat until stable.
    while True:
        changed = False
        for key, tag in placeholders.items():
            if key in out:
                out = out.replace(key, tag)
                changed = True
        if not changed:
            break

    # 7. Newlines → <br>
    out = out.replace("\n", "<br>\n")
    return f"<div style='font-family:monospace'>{out}</div>"


def _render_telegram(card: str, target: str) -> str:
    """Convert the standard-Markdown Summary Card to Telegram MarkdownV2."""
    # Telegram supports tables and <details>; we mainly need to escape.
    return _escape_markdownv2(card)


def _render_slack(card: str, target: str) -> str:
    """Convert to Slack mrkdwn. Tables become code blocks."""
    return _md_to_mrkdwn(card)


def _render_sms(card: str, target: str) -> str:
    """Plain text, single line, ≤ 480 chars (3 SMS segments).

    Drops all collapsibles, tables, and markup. Keeps only the score row
    and structural profile as a one-liner so the SMS user still gets the
    three numerological roots.
    """
    import re

    # The score table has rows like:
    #   | 🧮 Pythagorean | **61** | **7** | `61` → `7` |
    # Extract the Root column by splitting on '|' and grabbing field 3
    # (label | raw | root | ladder). Strip any '**' bold markers.
    roots: list[str] = []
    for line in card.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        if not any(e in line for e in "🧮✡️🌙"):
            continue
        parts = [p.strip().strip("*") for p in line.split("|")]
        # parts: ['', '<label>', '<raw>', '<root>', '<ladder>', '']
        if len(parts) >= 5 and parts[3].isdigit():
            roots.append(parts[3])
        if len(roots) == 3:
            break

    if len(roots) == 3:
        py_root, he_root, ch_root = roots
    else:
        py_root = he_root = ch_root = "?"

    plain = _strip_markdown(card)
    summary = f"Summary: roots {py_root}/{he_root}/{ch_root}. "
    out = (summary + plain)[:480]
    return out


def _render_email(card: str, target: str) -> str:
    """Convert to HTML email body."""
    return _md_to_html(card)


def _render_glasses(card: str, target: str) -> str:
    """30-char monospace row: target + 3 root numbers + 3 ratios.

    Output is a single block of fixed-width lines so it can render on a
    smartwatch HUD or smart-glasses overlay (Even Realities G1, Vuzix
    Shield, etc.) which typically cap at ~30 chars/line and ~6 lines.
    """
    py = pythagorean(target)
    he = hebrew(target)
    ch = chaldean(target)
    prof = structural_profile(target)

    def clip(s: str, n: int = 30) -> str:
        return s[:n].ljust(n)

    lines = [
        clip(f"Σ {target}"),
        clip(f"PYTH={py.root}  HEBR={he.root}  CHAL={ch.root}"),
        clip(f"V/C={prof.vowel_count}/{prof.consonant_count} "
             f"U={prof.unique_letters}"),
        clip(f"RARE={prof.rarest_letter[0] if prof.rarest_letter else '-'}"
             f"  COMMON={prof.most_common_letter[0] if prof.most_common_letter else '-'}"),
        clip(f"LEN={prof.letter_count}  ISO={'Y' if prof.isogram else 'N'}"
             f"  PAL={'Y' if prof.palindrome else 'N'}"),
        clip("—summary card—"),
    ]
    return "\n".join(lines)


def _render_default(card: str, target: str) -> str:
    """Default — return the standard-Markdown card untouched.

    Used by Discord, Matrix, Feishu, API server, web UI, and any adapter
    that doesn't have its own dialect.
    """
    return card


_PLATFORM_RENDERERS = {
    "telegram": _render_telegram,
    "slack":    _render_slack,
    "sms":      _render_sms,
    "email":    _render_email,
    "glasses":  _render_glasses,
    "default":  _render_default,
}


def render_for_platform(text: str, platform: str) -> str:
    """Render the Summary Card for a specific target platform.

    Args:
        text: The text to analyze.
        platform: One of "telegram", "slack", "sms", "email", "glasses",
                  "default". Unknown values fall through to "default".

    Returns:
        Platform-correct string ready for that adapter's send_message.
    """
    card = render_summary_card(text)
    platform = (platform or "default").lower()
    renderer = _PLATFORM_RENDERERS.get(platform, _render_default)
    return renderer(card, text)