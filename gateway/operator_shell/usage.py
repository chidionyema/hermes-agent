"""Turn a router regex into the command an operator can actually type.

The cockpit search finds an op and then has to answer "so what do I type?". Printing the
internal action name is what it used to do, and it was wrong for six of the ten ops that
take an argument: `se_set <id>`, `brain_set <id>` and `code_assign <id>` are not commands
this estate understands — `match_natural_op` returns None for every one of them. A search
result that names a dead command is worse than no result, because the operator types it,
gets nothing, and concludes the feature is broken.

The regex already holds the answer. `^\\s*(?:use|switch\\s+to)\\s+(?:the\\s+)?(opus|sonnet|...)`
says in full that "use opus" works. So the example is DERIVED from the pattern, the same
rule the find index follows: a hand-written usage string drifts on the first rename, and
this one cannot.

Derivation walks the parsed pattern rather than the source text, so regex grammar is the
parser's problem, not ours. First alternative of every branch, literals kept, optional
decoration (backticks, trailing "?") dropped, and a capture becomes either a concrete value
(when the regex enumerates them) or a placeholder naming what it wants.

Correctness is not assumed: `tests/gateway/operator_shell/test_find.py` feeds every derived
example back through `match_natural_op` and fails if one does not route to its own action.
That round-trip is what makes a derived string safe to print.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set

try:  # Python 3.11+ moved the regex parser behind re._parser
    import re._parser as _sre_parse
    from re import _constants as _sre
except ImportError:  # pragma: no cover - Python < 3.11
    import sre_parse as _sre_parse  # type: ignore[no-redef]
    import sre_constants as _sre  # type: ignore[no-redef]

ID = "<id>"
NUM = "<n>"
TEXT = "<text>"
VALUE = "<value>"

# Optional literals that are punctuation for the parser, not for the person typing: the
# backticks Telegram users wrap ids in, and the trailing "?" of a spoken question.
_DECORATION = "`?"


def _is_space(items) -> bool:
    return any(op is _sre.CATEGORY and val is _sre.CATEGORY_SPACE for op, val in items)


def _placeholder(items) -> str:
    """Name what a character class wants, in the operator's words.

    Every id in this table is spelled as a hex class ([0-9a-fA-F]{4,12}); a class that
    admits letters past 'f' is a free value — a knob setting, a model name — not an id.
    """
    letters = digits = False
    hex_only = True
    for op, val in items:
        if op is _sre.CATEGORY and val is _sre.CATEGORY_DIGIT:
            digits = True
        elif op is _sre.RANGE:
            lo, hi = val
            if chr(lo).isdigit():
                digits = True
            if chr(lo).isalpha():
                letters = True
                if chr(hi).lower() != "f":
                    hex_only = False
        elif op is _sre.LITERAL:
            ch = chr(val)
            if ch.isalpha():
                letters = True
                if ch.lower() > "f":
                    hex_only = False
            elif ch.isdigit():
                digits = True
    if letters:
        return ID if hex_only else VALUE
    return NUM if digits else TEXT


def _has_capture(node) -> bool:
    """Is there a capturing group anywhere in this subtree?

    An optional run is decoration when it holds no capture, and the argument itself when it
    does — `run prospector(?:\\s+(\\d+))?` must still show its <n>.
    """
    for op, av in node:
        if op is _sre.SUBPATTERN:
            if av[0] is not None or _has_capture(av[3]):
                return True
        elif op in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
            if _has_capture(av[2]):
                return True
        elif op is _sre.BRANCH:
            if any(_has_capture(b) for b in av[1]):
                return True
    return False


def _render(node, out: List[str]) -> List[str]:
    for op, av in node:
        if op is _sre.LITERAL:
            ch = chr(av)
            if ch not in _DECORATION:
                out.append(ch)
        elif op is _sre.ANY:
            out.append(TEXT)
        elif op is _sre.IN:
            out.append(" " if _is_space(av) else _placeholder(av))
        elif op in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
            minimum, _maximum, sub = av
            if len(sub) == 1 and sub[0][0] is _sre.IN and _is_space(sub[0][1]):
                if minimum >= 1:
                    out.append(" ")  # a whitespace run is one space
                continue
            if minimum == 0 and not _has_capture(sub):
                continue  # optional decoration
            _render(sub, out)
        elif op is _sre.SUBPATTERN:
            group, _add_flags, _del_flags, sub = av
            if group is None:
                _render(sub, out)
            else:
                out.append(_capture(sub))
        elif op is _sre.BRANCH:
            _render(av[1][0], out)  # first alternative is the canonical phrasing
        # AT (anchors) and anything exotic contribute no typed characters
    return out


def _capture(sub) -> str:
    """A capture is a concrete value when the regex lists the options, else a placeholder.

    `(opus|sonnet|haiku)` yields "opus" — a complete, working example beats `<model>`,
    because the operator can copy it verbatim.
    """
    if len(sub) == 1 and sub[0][0] is _sre.BRANCH:
        first = sub[0][1][1][0]
        if all(o is _sre.LITERAL for o, _ in first):
            return "".join(chr(v) for _, v in first)
    if len(sub) == 1 and sub[0][0] in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
        inner = sub[0][1][2]
        if len(inner) == 1:
            inner_op, inner_av = inner[0]
            if inner_op is _sre.IN:
                return _placeholder(inner_av)
            if inner_op is _sre.ANY:
                return TEXT
    return "".join(_render(sub, [])).strip() or TEXT


def example_command(pattern: re.Pattern) -> Optional[str]:
    """The shortest thing a human can type that this pattern accepts, or None.

    None rather than a guess: the caller prints "type it in plain words" instead, which is
    unhelpful but true, where a wrong command sends the operator somewhere that does not exist.
    """
    try:
        parsed = _sre_parse.parse(pattern.pattern, pattern.flags)
        text = re.sub(r"\s+", " ", "".join(_render(parsed, []))).strip()
    except Exception:  # a private parser API must never take the panel down
        return None
    return text or None


def placeholders(text: str) -> Set[str]:
    """The placeholder tokens present in a derived example."""
    return set(re.findall(r"<[a-z]+>", text or ""))
