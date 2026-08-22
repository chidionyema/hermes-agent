"""Qabalah and isopsephy — the half the summary card never had.

``summary_card`` scores Latin letters with three ciphers and calls the result
an Isopsephy Card. Isopsephy is the Greek practice, and the old card could not
read a single Greek letter: ``render_summary_card("Λόγος")`` drops every
character and scores 0. Hebrew is dropped the same way, which is awkward for a
card whose second cipher is named after it. And the qabalah the founder asked
for — the sephirot, the twenty-two paths — was never there at all.

This module adds three things and changes none of the old ones:

* **Greek isopsephy.** The 24 letters plus the three numeral-only archaic
  letters (digamma 6, koppa 90, sampi 900) that the classical system needs to
  reach 999. Accents and breathings are stripped; final sigma scores 200.
* **Hebrew gematria** over actual Hebrew script — mispar hechrechi by default,
  mispar gadol on request, which is the only place the five final forms score
  differently.
* **Qabalah.** The root of any value lands on a sephirah; every Hebrew letter
  sits on one of the twenty-two paths with its Tarot trump and its Sefer
  Yetzirah class. Both tables are the Golden Dawn (Kircher) attributions.

And a game, because the founder played the old card like one. Isopsephy has
one native game and it is 2,000 years old: find the words that share a number.
``daily_target`` picks the day's number from the date alone, so every player
sees the same one and nothing has to be stored to keep score.

**No value in this module is written down.** The tables hold letters, the
canon holds words, and every number on screen is summed at render time. That
is deliberate: a stored total is a claim, and a claim about gematria that
nobody recomputes is how bad tables survive for years. The old card's Hebrew
cipher gives W the same value as A and nobody noticed.
"""

from __future__ import annotations

import datetime as _dt
import unicodedata
from dataclasses import dataclass

# ===========================================================================
# GREEK — isopsephy (ἰσοψηφία)
# ===========================================================================
# Alpha 1 … theta 9, iota 10 … pi 80, rho 100 … omega 800, with the three
# letters Greek kept only as numerals: digamma/stigma 6, koppa 90, sampi 900.
# Without them the units stop at 5 and the hundreds at 800, and no classical
# isopsephy works out.
_GREEK_ROWS: tuple[tuple[str, int], ...] = (
    ("α", 1), ("β", 2), ("γ", 3), ("δ", 4), ("ε", 5), ("ϝ", 6), ("ζ", 7),
    ("η", 8), ("θ", 9),
    ("ι", 10), ("κ", 20), ("λ", 30), ("μ", 40), ("ν", 50), ("ξ", 60),
    ("ο", 70), ("π", 80), ("ϙ", 90),
    ("ρ", 100), ("σ", 200), ("τ", 300), ("υ", 400), ("φ", 500), ("χ", 600),
    ("ψ", 700), ("ω", 800), ("ϡ", 900),
)

GREEK: dict[str, int] = {}
for _ch, _v in _GREEK_ROWS:
    GREEK[_ch] = _v
    GREEK[_ch.upper()] = _v
# Variants that carry the same value: final sigma, lunate sigma, and the
# stigma ligature that stands in for digamma in most printed texts.
for _alias, _base in (("ς", "σ"), ("ϲ", "σ"), ("Ϲ", "σ"), ("ϛ", "ϝ"), ("Ϛ", "ϝ"),
                      ("ϟ", "ϙ"), ("Ϟ", "ϙ")):
    GREEK[_alias] = GREEK[_base]

GREEK_NAMES: dict[str, str] = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ϝ": "digamma", "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota",
    "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi",
    "ο": "omicron", "π": "pi", "ϙ": "koppa", "ρ": "rho", "σ": "sigma",
    "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi",
    "ω": "omega", "ϡ": "sampi",
}

# ===========================================================================
# HEBREW — mispar hechrechi, and mispar gadol for the finals
# ===========================================================================
_HEBREW_ROWS: tuple[tuple[str, str, int], ...] = (
    ("א", "aleph", 1), ("ב", "beth", 2), ("ג", "gimel", 3), ("ד", "daleth", 4),
    ("ה", "he", 5), ("ו", "vav", 6), ("ז", "zayin", 7), ("ח", "cheth", 8),
    ("ט", "teth", 9), ("י", "yod", 10), ("כ", "kaph", 20), ("ל", "lamed", 30),
    ("מ", "mem", 40), ("נ", "nun", 50), ("ס", "samekh", 60), ("ע", "ayin", 70),
    ("פ", "pe", 80), ("צ", "tzaddi", 90), ("ק", "qoph", 100), ("ר", "resh", 200),
    ("ש", "shin", 300), ("ת", "tav", 400),
)

HEBREW: dict[str, int] = {ch: v for ch, _n, v in _HEBREW_ROWS}
HEBREW_NAMES: dict[str, str] = {ch: n for ch, n, _v in _HEBREW_ROWS}

# The five sofit (final) forms. Standard counting gives them their ordinary
# value; mispar gadol continues the series into the hundreds. This is the only
# difference between the two systems, which is why one flag covers it.
_SOFIT: dict[str, tuple[str, int]] = {
    "ך": ("כ", 500), "ם": ("מ", 600), "ן": ("נ", 700),
    "ף": ("פ", 800), "ץ": ("צ", 900),
}
for _sof, (_base, _gadol) in _SOFIT.items():
    HEBREW[_sof] = HEBREW[_base]
    HEBREW_NAMES[_sof] = HEBREW_NAMES[_base] + " sofit"

HEBREW_GADOL: dict[str, int] = dict(HEBREW)
for _sof, (_base, _gadol) in _SOFIT.items():
    HEBREW_GADOL[_sof] = _gadol

# ===========================================================================
# ENGLISH — the extended cipher, which is the one the old card meant to have
# ===========================================================================
# summary_card's `_GEMATRIA` maps A–V onto the 22 Hebrew values and then wraps,
# so W scores 1 and collides with A, X with B, Y with C, Z with D. Four letters
# of the alphabet are unreadable in it, and any word of A–I letters scores
# identically under Pythagorean and Hebrew because both are 1–9 there.
#
# This table is the ordinary extension to 26 letters: units, tens, hundreds.
# It is added alongside the old one, never in place of it — the founder has
# years of readings under the old numbers and this must not move them.
ENGLISH_EXT: dict[str, int] = {}
for _i, _ch in enumerate("ABCDEFGHI"):
    ENGLISH_EXT[_ch] = _i + 1
for _i, _ch in enumerate("JKLMNOPQR"):
    ENGLISH_EXT[_ch] = (_i + 1) * 10
for _i, _ch in enumerate("STUVWXYZ"):
    ENGLISH_EXT[_ch] = (_i + 1) * 100

# ===========================================================================
# THE TREE — ten sephirot, twenty-two paths
# ===========================================================================


@dataclass(frozen=True)
class Sephirah:
    number: int
    name: str
    hebrew: str
    meaning: str
    sphere: str


SEPHIROT: tuple[Sephirah, ...] = (
    Sephirah(1, "Kether", "כתר", "Crown", "Primum Mobile"),
    Sephirah(2, "Chokmah", "חכמה", "Wisdom", "The Zodiac"),
    Sephirah(3, "Binah", "בינה", "Understanding", "Saturn"),
    Sephirah(4, "Chesed", "חסד", "Mercy", "Jupiter"),
    Sephirah(5, "Geburah", "גבורה", "Severity", "Mars"),
    Sephirah(6, "Tiphareth", "תפארת", "Beauty", "Sol"),
    Sephirah(7, "Netzach", "נצח", "Victory", "Venus"),
    Sephirah(8, "Hod", "הוד", "Splendour", "Mercury"),
    Sephirah(9, "Yesod", "יסוד", "Foundation", "Luna"),
    Sephirah(10, "Malkuth", "מלכות", "Kingdom", "Earth"),
)


@dataclass(frozen=True)
class Path:
    number: int          # 11–32, continuing the sephirot
    letter: str
    name: str
    trump: str
    kind: str            # mother / double / simple
    attribution: str


# Golden Dawn (Kircher) attributions. The three mothers are elements, the seven
# doubles are planets, the twelve simples are signs — that split is Sefer
# Yetzirah and predates the Tarot correspondence by centuries.
PATHS: tuple[Path, ...] = (
    Path(11, "א", "Aleph", "The Fool", "mother", "Air"),
    Path(12, "ב", "Beth", "The Magician", "double", "Mercury"),
    Path(13, "ג", "Gimel", "The High Priestess", "double", "Luna"),
    Path(14, "ד", "Daleth", "The Empress", "double", "Venus"),
    Path(15, "ה", "He", "The Emperor", "simple", "Aries"),
    Path(16, "ו", "Vav", "The Hierophant", "simple", "Taurus"),
    Path(17, "ז", "Zayin", "The Lovers", "simple", "Gemini"),
    Path(18, "ח", "Cheth", "The Chariot", "simple", "Cancer"),
    Path(19, "ט", "Teth", "Strength", "simple", "Leo"),
    Path(20, "י", "Yod", "The Hermit", "simple", "Virgo"),
    Path(21, "כ", "Kaph", "Wheel of Fortune", "double", "Jupiter"),
    Path(22, "ל", "Lamed", "Justice", "simple", "Libra"),
    Path(23, "מ", "Mem", "The Hanged Man", "mother", "Water"),
    Path(24, "נ", "Nun", "Death", "simple", "Scorpio"),
    Path(25, "ס", "Samekh", "Temperance", "simple", "Sagittarius"),
    Path(26, "ע", "Ayin", "The Devil", "simple", "Capricorn"),
    Path(27, "פ", "Pe", "The Tower", "double", "Mars"),
    Path(28, "צ", "Tzaddi", "The Star", "simple", "Aquarius"),
    Path(29, "ק", "Qoph", "The Moon", "simple", "Pisces"),
    Path(30, "ר", "Resh", "The Sun", "double", "Sol"),
    Path(31, "ש", "Shin", "Judgement", "mother", "Fire"),
    Path(32, "ת", "Tav", "The World", "double", "Saturn"),
)

_PATH_BY_LETTER: dict[str, Path] = {p.letter: p for p in PATHS}
for _sof, (_base, _g) in _SOFIT.items():
    _PATH_BY_LETTER[_sof] = _PATH_BY_LETTER[_base]


def sephirah_for(value: int) -> Sephirah | None:
    """The sephirah a value lands on: 1–10 direct, anything else by its root.

    A root of 0 (an empty or letterless input) is on the Tree nowhere, and
    returning Kether for it would be an invented reading.
    """
    if value <= 0:
        return None
    n = value if 1 <= value <= 10 else _digital_root(value)
    return SEPHIROT[n - 1] if 1 <= n <= 10 else None


def path_for(letter: str) -> Path | None:
    """The Tree path a Hebrew letter walks, final forms included."""
    return _PATH_BY_LETTER.get(letter)


def _digital_root(n: int) -> int:
    """Reduce to 1–10, stopping at 10 rather than folding it to 1.

    The ordinary digital root sends 10 to 1, which on the Tree means sending
    Malkuth to Kether — the bottom of the Tree to the top. They are the two
    ends of the same thing and swapping them inverts the reading.

    The reduction is the check on itself: מלכות sums to 496, and 496 → 19 → 10
    is Malkuth, which is the sephirah the word names.
    """
    while n > 10:
        n = sum(int(d) for d in str(n))
    return n


# ===========================================================================
# SCORING
# ===========================================================================


@dataclass(frozen=True)
class Reading:
    system: str                       # "Greek isopsephy" / "Hebrew gematria" / …
    script: str                       # greek / hebrew / latin
    total: int
    breakdown: tuple[tuple[str, int], ...]
    scored: int                       # letters that carried a value
    skipped: int                      # characters the system cannot read

    @property
    def root(self) -> int:
        return _digital_root(self.total) if self.total > 0 else 0


def _strip_marks(text: str) -> str:
    """Greek accents and breathings, and Hebrew niqqud, carry no value.

    NFD splits the mark off the letter; category Mn drops it. Recomposing is
    pointless afterwards because the tables are keyed on bare letters.
    """
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _score(text: str, table: dict[str, int], system: str, script: str) -> Reading:
    cleaned = _strip_marks(text)
    pairs: list[tuple[str, int]] = []
    skipped = 0
    for ch in cleaned:
        if ch in table:
            pairs.append((ch, table[ch]))
        elif not ch.isspace():
            skipped += 1
    return Reading(system, script, sum(v for _c, v in pairs), tuple(pairs),
                   len(pairs), skipped)


def isopsephy(text: str) -> Reading:
    """Greek isopsephy. ἸΗΣΟΥΣ and χξϛ both come out of this table."""
    return _score(text, GREEK, "Greek isopsephy", "greek")


def gematria(text: str, gadol: bool = False) -> Reading:
    """Hebrew gematria over Hebrew script. ``gadol`` scores the finals 500–900."""
    table = HEBREW_GADOL if gadol else HEBREW
    name = "Hebrew gematria (mispar gadol)" if gadol else "Hebrew gematria"
    return _score(text, table, name, "hebrew")


def english_extended(text: str) -> Reading:
    """The 26-letter English cipher: A–I units, J–R tens, S–Z hundreds."""
    return _score(text.upper(), ENGLISH_EXT, "English extended", "latin")


def scripts_present(text: str) -> set[str]:
    """Which alphabets are actually in *text*, by the tables that can read them."""
    cleaned = _strip_marks(text)
    found: set[str] = set()
    for ch in cleaned:
        if ch in GREEK:
            found.add("greek")
        elif ch in HEBREW:
            found.add("hebrew")
        elif ch.isascii() and ch.isalpha():
            found.add("latin")
    return found


def readings_for(text: str) -> list[Reading]:
    """Every reading *text* supports, in the order they should be shown.

    Latin always yields one, because the old card's three ciphers already cover
    Latin and this adds the fourth. Greek and Hebrew appear only when their
    letters do, so an English word does not get two zero rows it never asked
    for.
    """
    present = scripts_present(text)
    out: list[Reading] = []
    if "greek" in present:
        out.append(isopsephy(text))
    if "hebrew" in present:
        out.append(gematria(text))
        gad = gematria(text, gadol=True)
        if gad.total != out[-1].total:
            out.append(gad)
    if "latin" in present:
        out.append(english_extended(text))
    return out


# ===========================================================================
# THE CANON — words worth sharing a number with
# ===========================================================================
# Words only. Every value beside them on screen is summed from the tables at
# render time, so a wrong entry shows up as a wrong number rather than hiding
# behind a stored one.
CANON: tuple[tuple[str, str], ...] = (
    # Hebrew
    ("יהוה", "the Tetragrammaton"),
    ("אלהים", "Elohim"),
    ("אדני", "Adonai"),
    ("אהיה", "Ehyeh — I Am"),
    ("אחד", "echad — one"),
    ("אהבה", "ahavah — love"),
    ("חי", "chai — life"),
    ("אמת", "emet — truth"),
    ("שלום", "shalom — peace"),
    ("תורה", "Torah"),
    ("משיח", "Mashiach"),
    ("נחש", "nachash — serpent"),
    ("כתר", "Kether — Crown"),
    ("חכמה", "Chokmah — Wisdom"),
    ("בינה", "Binah — Understanding"),
    ("חסד", "Chesed — Mercy"),
    ("גבורה", "Geburah — Severity"),
    ("תפארת", "Tiphareth — Beauty"),
    ("נצח", "Netzach — Victory"),
    ("הוד", "Hod — Splendour"),
    ("יסוד", "Yesod — Foundation"),
    ("מלכות", "Malkuth — Kingdom"),
    # Greek
    ("Ἰησοῦς", "Iesous — Jesus"),
    ("Χριστός", "Christos"),
    ("Λόγος", "Logos — the Word"),
    ("ἀγάπη", "agape — love"),
    ("θέλημα", "thelema — will"),
    ("ἀλήθεια", "aletheia — truth"),
    ("σοφία", "Sophia — wisdom"),
    ("ψυχή", "psyche — soul"),
    ("κόσμος", "kosmos — order, world"),
    ("ἀρχή", "arche — beginning"),
    ("Ἀμήν", "amen"),
    ("Ἀβρασάξ", "Abrasax"),
    ("Μίθρας", "Mithras"),
    ("Ἅγιον", "hagion — holy"),
    ("χξϛ", "the number of the beast, as written"),
)


def canon_readings() -> list[tuple[str, str, Reading]]:
    """Every canon word with its computed reading. Nothing here is stored."""
    out = []
    for word, gloss in CANON:
        present = scripts_present(word)
        r = isopsephy(word) if "greek" in present else gematria(word)
        out.append((word, gloss, r))
    return out


def equivalences(value: int, exclude: str = "") -> list[tuple[str, str, Reading]]:
    """Canon words whose value equals *value*. The whole point of isopsephy."""
    if value <= 0:
        return []
    return [(w, g, r) for w, g, r in canon_readings()
            if r.total == value and w != exclude]


# ===========================================================================
# THE GAME
# ===========================================================================
# One number a day, taken from the date so that nothing has to be stored for
# two people to be playing the same game. A stored scoreboard would be the
# first thing to rot: it needs a writable path on a container that is rebuilt
# on every deploy, and nobody would notice when it stopped saving.


def daily_target(today: _dt.date | None = None) -> tuple[int, str]:
    """The day's number, and the canon word it was drawn from.

    Drawn from the canon rather than at random, so the target is always
    reachable: there is at least one right answer, and it is a word with a
    meaning rather than an arbitrary integer.
    """
    day = today or _dt.date.today()
    pool = sorted({r.total: (w, g) for w, g, r in canon_readings()}.items())
    word, _gloss = pool[day.toordinal() % len(pool)][1]
    return pool[day.toordinal() % len(pool)][0], word


@dataclass(frozen=True)
class GameResult:
    target: int
    guess: str
    readings: tuple[Reading, ...]
    hits: tuple[Reading, ...]          # readings that landed exactly
    closest: Reading | None
    distance: int | None


def play(guess: str, today: _dt.date | None = None,
         target: int | None = None) -> GameResult:
    """Score *guess* against the day's number in every system it can be read in.

    A guess counts if ANY of its readings hits. Typing a Greek word to match a
    Hebrew target is not cheating, it is the game — the practice is finding the
    same number in different alphabets.
    """
    tgt = target if target is not None else daily_target(today)[0]
    reads = tuple(readings_for(guess))
    hits = tuple(r for r in reads if r.total == tgt)
    closest = min(reads, key=lambda r: abs(r.total - tgt)) if reads else None
    dist = abs(closest.total - tgt) if closest is not None else None
    return GameResult(tgt, guess, reads, hits, closest, dist)


# ===========================================================================
# RENDERING
# ===========================================================================
# The old card's house style: a fenced box for the numbers, pipe tables for
# detail, `####` for section heads. These append below it and have to look like
# the same card rather than a second one stapled on.

_CARD_WIDTH = 34
_SCRIPT_MARK = {"greek": "🏛", "hebrew": "✡️", "latin": "🔤"}


def _band(title: str) -> str:
    head = f"── {title} "
    return head + "─" * max(0, _CARD_WIDTH - len(head))


def _rule() -> str:
    return "━" * _CARD_WIDTH


def render_qabalah_section(text: str) -> str:
    """The qabalah half of the card for *text*, or "" when there is none.

    Empty for input with no letter any table can read, because a Tree section
    that says 0 → nowhere is noise on a card that already said the input was
    letterless.
    """
    reads = readings_for(text)
    if not reads or all(r.total == 0 for r in reads):
        return ""

    out: list[str] = ["### ✡️ Qabalah", "", "```", _rule()]

    out.append(_band("READINGS"))
    for r in reads:
        mark = _SCRIPT_MARK.get(r.script, "•")
        out.append(f"  {mark} {r.system} · {r.total}")
        seph = sephirah_for(r.total)
        if seph is not None:
            out.append(f"     root {r.root} → {seph.number} {seph.name} "
                       f"({seph.meaning})")
        if r.skipped:
            out.append(f"     {r.skipped} character(s) this system cannot read")

    totals = {r.total for r in reads if r.total > 0}
    if len(totals) > 1:
        out.append(_band("ACROSS ALPHABETS"))
        out.append("  the same word, different numbers:")
        out.append("  " + " · ".join(str(t) for t in sorted(totals)))
    out.append(_rule())
    out.append("```")

    # --- the Tree ---------------------------------------------------------
    primary = max(reads, key=lambda r: r.total)
    seph = sephirah_for(primary.total)
    if seph is not None:
        out += [
            "",
            "#### 🌳 On the Tree",
            "",
            "| | |",
            "|---|---|",
            f"| Sephirah | **{seph.number} {seph.name}** {seph.hebrew} |",
            f"| Meaning | {seph.meaning} |",
            f"| Sphere | {seph.sphere} |",
            f"| Reached by | {primary.system} {primary.total} → root {primary.root} |",
        ]

    # --- the paths, only when Hebrew letters are actually present ---------
    hebrew_letters = [c for c in _strip_marks(text) if c in _PATH_BY_LETTER]
    if hebrew_letters:
        out += ["", "#### 🛤 Paths walked", "",
                "| Letter | Path | Trump | Class | Attribution |",
                "|---|---|---|---|---|"]
        seen: set[str] = set()
        for ch in hebrew_letters:
            p = path_for(ch)
            if p is None or p.letter in seen:
                continue
            seen.add(p.letter)
            out.append(f"| {ch} {p.name} | {p.number} | {p.trump} | "
                       f"{p.kind} | {p.attribution} |")
        out.append("")
        out.append("_Golden Dawn (Kircher) attributions. The Thelemic tree swaps "
                   "He and Tzaddi, so the Emperor and the Star trade places there._")

    # --- equivalences, which is the practice itself -----------------------
    matches: list[str] = []
    for r in reads:
        for w, gloss, other in equivalences(r.total, exclude=text.strip()):
            matches.append(f"| {r.total} | {w} | {gloss} | {other.system} |")
    if matches:
        out += ["", "#### ⚖️ Equivalences", "",
                "| Value | Word | | System |", "|---|---|---|---|"]
        out += matches
    return "\n".join(out)


def render_letter_tables() -> str:
    """The three tables in full, so nobody has to trust them unseen."""
    out = ["### 📜 Letter values", "", "#### Greek", "",
           "| Letter | Name | Value |", "|---|---|---|"]
    for ch, v in _GREEK_ROWS:
        out.append(f"| {ch} | {GREEK_NAMES[ch]} | {v} |")
    out += ["", "#### Hebrew", "", "| Letter | Name | Value | Sofit (gadol) |",
            "|---|---|---|---|"]
    inv = {b: s for s, (b, _g) in _SOFIT.items()}
    for ch, name, v in _HEBREW_ROWS:
        sof = inv.get(ch)
        tail = f"{sof} {HEBREW_GADOL[sof]}" if sof else "—"
        out.append(f"| {ch} | {name} | {v} | {tail} |")
    out += ["", "#### English extended", "",
            "| A–I | J–R | S–Z |", "|---|---|---|"]
    for i in range(8):
        a, j, s = "ABCDEFGHI"[i], "JKLMNOPQR"[i], "STUVWXYZ"[i]
        out.append(f"| {a} {ENGLISH_EXT[a]} | {j} {ENGLISH_EXT[j]} | "
                   f"{s} {ENGLISH_EXT[s]} |")
    out.append(f"| I {ENGLISH_EXT['I']} | R {ENGLISH_EXT['R']} | |")
    return "\n".join(out)


GAME_HELP = (
    "### 🎲 Isopsephy — the day's number\n\n"
    "One number, drawn from the canon so it is always reachable. "
    "Find a word that sums to it in **any** alphabet: Greek, Hebrew, or "
    "English extended. Matching a Hebrew target with a Greek word is the "
    "game, not a loophole.\n\n"
    "`/summary game` — today's number\n"
    "`/summary game <word>` — score a word against it\n"
    "`/summary game <n> <word>` — score against a number you pick\n"
    "`/summary tables` — every letter value, so you can work it out\n"
)


def render_game(args: str, today: _dt.date | None = None) -> str:
    """The game reply. Bare shows the number; a word is scored against it."""
    args = (args or "").strip()
    target: int | None = None
    parts = args.split(None, 1)
    if parts and parts[0].lstrip("-").isdigit():
        target = int(parts[0])
        args = parts[1] if len(parts) > 1 else ""

    day_value, _drawn_from = daily_target(today)
    tgt = target if target is not None else day_value

    if not args:
        seph = sephirah_for(tgt)
        lines = [
            "### 🎲 Today's number",
            "",
            "```",
            _rule(),
            f"  {tgt}",
            f"  root {_digital_root(tgt)}"
            + (f" → {seph.number} {seph.name}" if seph else ""),
            _rule(),
            "```",
            "",
            "Find a word that sums to it. Greek, Hebrew, or English extended — "
            "any alphabet counts.",
            "",
            "`/summary game <word>` to score one · `/summary tables` for the "
            "letter values",
        ]
        if target is None:
            reachable = len(equivalences(tgt))
            lines.append("")
            lines.append(f"_Drawn from the canon, so it is reachable: "
                         f"{reachable} word(s) there hit it exactly. "
                         f"Naming them would be playing your turn for you._")
        return "\n".join(lines)

    res = play(args, today=today, target=tgt)
    if not res.readings:
        return (f"### 🎲 {tgt}\n\n`{args}` has no letter any table can read. "
                f"Try Greek, Hebrew, or A–Z.")

    out = [f"### 🎲 {args} → {tgt}?", "", "```", _rule()]
    for r in res.readings:
        mark = _SCRIPT_MARK.get(r.script, "•")
        flag = "  ✅ HIT" if r.total == tgt else f"  off by {abs(r.total - tgt)}"
        out.append(f"  {mark} {r.system}")
        out.append(f"     {r.total}{flag}")
    out.append(_rule())
    out.append("```")
    out.append("")

    if res.hits:
        systems = ", ".join(h.system for h in res.hits)
        out.append(f"**Hit.** `{args}` reaches {tgt} in {systems}.")
        others = equivalences(tgt, exclude=args.strip())
        if others:
            out.append("")
            out.append("It now shares that number with:")
            for w, gloss, r in others:
                out.append(f"- {w} — {gloss}")
    else:
        assert res.closest is not None
        out.append(f"**Miss by {res.distance}.** Closest was "
                   f"{res.closest.system} at {res.closest.total}.")
        reachable = len(equivalences(tgt, exclude=args.strip()))
        if reachable:
            out.append("")
            out.append(f"_{reachable} canon word(s) do reach {tgt}._")
    return "\n".join(out)


def render_reply(raw_args: str) -> str:
    """The whole `/summary` reply, whichever surface asked for it.

    One owner, because the CLI, the gateway and the TUI each dispatch their own
    slash commands and the old estate learned the hard way what happens when a
    second entry point renders its own version of a card.

    Four shapes: bare is the help, ``game`` and ``tables`` are new, ``A vs B``
    is the old compare card untouched, and anything else is the old summary
    card with the qabalah section under it.
    """
    try:
        from gateway.summary_card import (
            parse_compare_args,
            render_compare_card,
            render_summary_card,
        )
    except ImportError:
        # Run as a bare pair of files, with no Hermes package around them.
        # Crew DECISIONS entry 6 discontinues Hermes, so this module has to
        # outlive the gateway it was written for. summary_card.py sits beside
        # this file in both layouts, which is the whole of what it needs.
        from summary_card import (  # type: ignore[no-redef]
            parse_compare_args,
            render_compare_card,
            render_summary_card,
        )

    raw_args = (raw_args or "").strip()
    if not raw_args:
        return (
            "🔮 **Summary Card**\n\n"
            "Send `/summary <text>` to analyse it.\n\n"
            "• 🧮 Pythagorean, Chaldean and the old Hebrew cipher\n"
            "• ✡️ Hebrew gematria over Hebrew script\n"
            "• 🏛 Greek isopsephy, digamma and sampi included\n"
            "• 🌳 Sephirah, and the Tree paths a Hebrew word walks\n"
            "• 🔤 Anagram permutations\n"
            "• ⚖️ Compare two texts: `/summary A vs B`\n"
            "• 🎲 Play: `/summary game`\n\n"
            "_Example:_ `/summary Hello World` · `/summary אהבה` · `/summary θέλημα`"
        )

    head, _sep, rest = raw_args.partition(" ")
    low = head.lower()
    if low == "game":
        rest = rest.strip()
        if rest.lower() in ("help", "?", "how", "rules"):
            return GAME_HELP
        return render_game(rest)
    if low in ("tables", "table", "letters"):
        return render_letter_tables()

    compared = parse_compare_args(raw_args)
    if compared:
        a, b = compared
        return render_compare_card(a, b)

    card = render_summary_card(raw_args)
    qabalah = render_qabalah_section(raw_args)
    return f"{card}\n\n{qabalah}" if qabalah else card



def main(argv: list[str] | None = None) -> int:
    """Play it with no Hermes anywhere: `python3 qabalah.py <text>`.

    The gateway, the CLI and the TUI all route through ``render_reply`` too,
    so this entry point renders the identical text rather than a second
    version of the card.
    """
    import sys

    args = sys.argv[1:] if argv is None else argv
    print(render_reply(" ".join(args)))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
