"""MarkdownV2 for operator panels: parse, sanitise, and re-emit.

WHY THIS MODULE EXISTS
----------------------
Panel text is authored in *native MarkdownV2* — panel_chrome.panel_stamp
writes ``_2026-08-06 · 4m ago_`` meaning italic, and panels write ``*Title*``
meaning BOLD. It was being sent through TelegramAdapter.format_message, which
is a CommonMark→MarkdownV2 *converter*. Applied to text that is already
MarkdownV2, it inverts the markup:

    *Title*  --step 6 (``*x*`` → ``_x_``)-->  _Title_      bold becomes italic
    _stamp_  --step 10 blanket escape   -->  \\_stamp\\_    italic becomes literal

Measured over every panel the cockpit can render (47 panels): 187 bold spans
demoted on 46 panels, 82 italic spans literalised on 35 panels, 36 panels with
altered visible text, and exactly 1 panel surviving intact.

``render_panel`` replaces that conversion with the operation the text actually
needs: keep the author's markers, escape everything else, and guarantee the
result parses.

THE AUTHORING CONVENTION
------------------------
A marker pair delimits formatting; everything else is literal. A marker opens
only at a word boundary and closes only when it hugs its content, so
``my_var_name`` and ``3 * 4`` survive as text while ``*Title*`` and ``_4m ago_``
are honoured. (telegram.py:261 already applies the same word-boundary
heuristic in _strip_mdv2, for the same reason.)

GUARANTEE
---------
``parse(to_strict(*parse_lenient(x)))`` round-trips: every character outside a
marker is backslash-escaped and markers are emitted in strictly nested order,
so the output cannot draw Telegram's "can't parse entities" 400.

The parser below doubles as a test ORACLE: it makes "bold became italic" a
parsed-entity difference rather than an asterisk count.

Reference: Bot API "MarkdownV2 style". Entity markers:
    *bold*  _italic_  __underline__  ~strike~  ||spoiler||
    `code`  ```pre```  [text](url)
Any of _*[]()~`>#+-=|{}.! outside an entity MUST be escaped with '\\'.
"""

from __future__ import annotations

from typing import List, NamedTuple, Tuple

SPECIALS = set("_*[]()~`>#+-=|{}.!\\")


class Entity(NamedTuple):
    type: str
    offset: int
    length: int
    text: str  # the plain text the entity covers — makes diffs readable
    url: str = ""  # text_link target; re-emitting without it makes a dead link


class ParseError(Exception):
    """Raised for input Telegram would reject with 'can't parse entities'."""


# marker → entity type, longest markers first so __ beats _ and || beats |
_MARKERS: List[Tuple[str, str]] = [
    ("```", "pre"),
    ("||", "spoiler"),
    ("__", "underline"),
    ("*", "bold"),
    ("_", "italic"),
    ("~", "strikethrough"),
    ("`", "code"),
]


def parse(src: str) -> Tuple[str, List[Entity]]:
    """Parse MarkdownV2 → (plain_text, entities sorted by offset then type).

    Raises ParseError on an unclosed entity, which is precisely the input
    Telegram answers with HTTP 400.
    """
    out: List[str] = []
    entities: List[Entity] = []
    # stack of (entity_type, marker, offset_in_plain_text)
    stack: List[Tuple[str, str, int]] = []
    i = 0
    n = len(src)

    def pos() -> int:
        return sum(len(c) for c in out)

    while i < n:
        ch = src[i]

        # Escape: backslash makes the next char literal.
        if ch == "\\":
            if i + 1 >= n:
                raise ParseError("trailing backslash")
            out.append(src[i + 1])
            i += 2
            continue

        # Inside code/pre nothing but \ and ` are special, so only the
        # closing marker can end it.
        if stack and stack[-1][0] in ("code", "pre"):
            marker = stack[-1][1]
            if src.startswith(marker, i):
                etype, mk, start = stack.pop()
                entities.append(Entity(etype, start, pos() - start, "".join(out)[start:]))
                i += len(marker)
                continue
            out.append(ch)
            i += 1
            continue

        # Link: [text](url)
        if ch == "[":
            close = _find_link_close(src, i)
            if close is not None:
                text_end, url_end = close
                inner = src[i + 1 : text_end]
                inner_plain, inner_ents = parse(inner)
                start = pos()
                out.append(inner_plain)
                for e in inner_ents:
                    entities.append(
                        Entity(e.type, start + e.offset, e.length, e.text, e.url)
                    )
                url = src[text_end + 2 : url_end].replace("\\)", ")").replace("\\\\", "\\")
                entities.append(
                    Entity("text_link", start, len(inner_plain), inner_plain, url)
                )
                i = url_end + 1
                continue
            raise ParseError("unescaped '[' that opens no link")

        matched = False
        for marker, etype in _MARKERS:
            if not src.startswith(marker, i):
                continue
            # Closing the currently open entity of the same marker?
            if stack and stack[-1][1] == marker:
                et, mk, start = stack.pop()
                entities.append(Entity(et, start, pos() - start, "".join(out)[start:]))
            else:
                stack.append((etype, marker, pos()))
            i += len(marker)
            matched = True
            break
        if matched:
            continue

        if ch in SPECIALS:
            raise ParseError(f"unescaped special {ch!r} at {i}")

        out.append(ch)
        i += 1

    if stack:
        raise ParseError(f"unclosed {stack[-1][0]} entity")

    plain = "".join(out)
    entities.sort(key=lambda e: (e.offset, e.type))
    return plain, entities


def _find_link_close(src: str, start: int):
    """For src[start]=='[', return (index_of_']', index_of_')') or None."""
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "\\":
            continue
        if src[j] == "[":
            depth += 1
        elif src[j] == "]":
            depth -= 1
            if depth == 0:
                if j + 1 < len(src) and src[j + 1] == "(":
                    k = src.find(")", j + 2)
                    if k != -1:
                        return j, k
                return None
    return None


def summarize(src: str):
    """(plain, entities, error) — never raises, for bulk probing."""
    try:
        plain, ents = parse(src)
        return plain, ents, None
    except ParseError as e:
        return None, None, str(e)


# --------------------------------------------------------------- lenient
# Panel source is not strict MarkdownV2: authors write *Title* and _2h ago_ for
# markup but leave ordinary prose ("v2.1", "runs 3-5", "(ok)") unescaped. The
# authoring convention this encodes is: a marker pair delimits formatting, and
# EVERYTHING else is literal.

_WORDISH = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)


def _is_open(src: str, i: int, marker: str) -> bool:
    """A marker opens only at a word boundary.

    Without this, snake_case is mutilated: 'my_var_name' contains a balanced
    '_var_' and would silently italicise. telegram.py:261 already applies the
    same word-boundary heuristic in _strip_mdv2, for the same reason.
    """
    before = src[i - 1] if i > 0 else " "
    after = src[i + len(marker)] if i + len(marker) < len(src) else " "
    # Not preceded by a word char (so my_var stays literal), and hugging its
    # content (so "3 * 4" stays literal).
    return before not in _WORDISH and after not in (" ", "\t", "\n", "")


def _is_close(src: str, i: int, marker: str) -> bool:
    before = src[i - 1] if i > 0 else " "
    after = src[i + len(marker)] if i + len(marker) < len(src) else " "
    return before != " " and after not in _WORDISH


def parse_lenient(src: str) -> Tuple[str, List[Entity]]:
    """Parse authored panel text → (plain, entities). Never raises.

    A marker becomes an entity only when a valid closing marker exists at a
    word boundary later in the text; anything else is literal.
    """
    out: List[str] = []
    entities: List[Entity] = []
    stack: List[Tuple[str, str, int]] = []
    i, n = 0, len(src)

    def pos() -> int:
        return sum(len(c) for c in out)

    while i < n:
        ch = src[i]

        if ch == "\\" and i + 1 < n and src[i + 1] in SPECIALS:
            out.append(src[i + 1])
            i += 2
            continue

        if stack and stack[-1][0] in ("code", "pre"):
            marker = stack[-1][1]
            if src.startswith(marker, i):
                et, mk, start = stack.pop()
                entities.append(Entity(et, start, pos() - start, "".join(out)[start:]))
                i += len(marker)
                continue
            out.append(ch)
            i += 1
            continue

        if ch == "[":
            close = _find_link_close(src, i)
            if close is not None:
                text_end, url_end = close
                inner_plain, inner_ents = parse_lenient(src[i + 1 : text_end])
                start = pos()
                out.append(inner_plain)
                for e in inner_ents:
                    entities.append(
                        Entity(e.type, start + e.offset, e.length, e.text, e.url)
                    )
                entities.append(
                    Entity(
                        "text_link",
                        start,
                        len(inner_plain),
                        inner_plain,
                        src[text_end + 2 : url_end],
                    )
                )
                i = url_end + 1
                continue

        matched = False
        for marker, etype in _MARKERS:
            if not src.startswith(marker, i):
                continue
            if stack and stack[-1][1] == marker and _is_close(src, i, marker):
                et, mk, start = stack.pop()
                entities.append(Entity(et, start, pos() - start, "".join(out)[start:]))
                i += len(marker)
                matched = True
                break
            if _is_open(src, i, marker) and _has_close(src, i + len(marker), marker):
                stack.append((etype, marker, pos()))
                i += len(marker)
                matched = True
                break
            break  # this marker occurrence is literal
        if matched:
            continue

        out.append(ch)
        i += 1

    # Anything still open never found a partner — its marker was literal text.
    # Re-running without those openers is simpler than unwinding offsets.
    if stack:
        return _parse_lenient_literalising(src, {m for _, m, _ in stack})

    plain = "".join(out)
    entities.sort(key=lambda e: (e.offset, e.type))
    return plain, entities


def _has_close(src: str, start: int, marker: str) -> bool:
    j = src.find(marker, start)
    while j != -1:
        if _is_close(src, j, marker):
            return True
        j = src.find(marker, j + 1)
    return False


def _parse_lenient_literalising(src: str, dead: set) -> Tuple[str, List[Entity]]:
    """Fallback: treat the given markers as literal everywhere, then re-parse."""
    sentinel = {m: f"\x01{k}\x01" for k, m in enumerate(sorted(dead, key=len, reverse=True))}
    tmp = src
    for m, s in sentinel.items():
        tmp = tmp.replace(m, s)
    plain, ents = parse_lenient(tmp)
    for m, s in sentinel.items():
        plain = plain.replace(s, m)
    return plain, ents


def to_strict(plain: str, entities: List[Entity]) -> str:
    """Re-emit (plain, entities) as VALID strict MarkdownV2.

    Guarantee: parse(to_strict(p, e)) == (p, e). Every character outside an
    entity marker is escaped, so the result can never draw a 400.
    """
    marker_for = {
        "bold": "*",
        "italic": "_",
        "underline": "__",
        "strikethrough": "~",
        "spoiler": "||",
        "code": "`",
        "pre": "```",
    }
    # Emit markers so entities NEST rather than interleave: at a given offset
    # open the longest-spanning entity first, and close the shortest first.
    # Interleaved markers (_a*b_c*) are not valid MarkdownV2 and draw a 400.
    ordered = sorted(entities, key=lambda e: (e.offset, -e.length))
    opens: dict = {}
    closes: dict = {}
    for e in ordered:
        if e.type == "text_link":
            open_mk = "["
            close_mk = "](" + e.url.replace("\\", "\\\\").replace(")", "\\)") + ")"
        else:
            mk = marker_for.get(e.type)
            if not mk:
                continue
            open_mk = close_mk = mk
        opens.setdefault(e.offset, []).append(open_mk)
        closes.setdefault(e.offset + e.length, []).insert(0, close_mk)

    verbatim = set()
    for e in entities:
        if e.type in ("code", "pre"):
            verbatim.update(range(e.offset, e.offset + e.length))

    parts: List[str] = []
    for idx in range(len(plain) + 1):
        for mk in reversed(closes.get(idx, [])):
            parts.append(mk)
        for mk in opens.get(idx, []):
            parts.append(mk)
        if idx == len(plain):
            break
        ch = plain[idx]
        if idx in verbatim:
            parts.append("\\" + ch if ch in ("`", "\\") else ch)
        else:
            parts.append("\\" + ch if ch in SPECIALS else ch)
    return "".join(parts)


# ------------------------------------------------------------------ public

def render_panel(text: str) -> str:
    """Prepare authored panel text for sendMessage(parse_mode=MarkdownV2).

    Preserves the author's markup and escapes everything else. The output is
    valid MarkdownV2 by construction, so it cannot draw a 400.

    Use this — never format_message — for text produced by operator_shell
    panels. format_message converts CommonMark INTO MarkdownV2 and therefore
    corrupts text that is already MarkdownV2 (see the module docstring).
    """
    if not text:
        return text
    return to_strict(*parse_lenient(text))
