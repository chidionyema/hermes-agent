"""Panel markup must survive the send path.

Panels are authored in native MarkdownV2 (``*Title*`` = bold, ``_4m ago_`` =
italic). They were being sent through TelegramAdapter.format_message, a
CommonMark→MarkdownV2 *converter*, which inverts that markup: ``*x*`` is
rewritten to ``_x_`` (bold → italic) and authored ``_x_`` is blanket-escaped to
``\\_x\\_`` (italic → literal underscores).

Measured before the fix, over every panel the cockpit can render: 187 bold
spans demoted across 46 of 47 panels, 82 italic spans literalised across 35,
and exactly 1 panel arriving intact.

These tests assert on PARSED ENTITIES, not on asterisk counts — "bold became
italic" is only a finding if Telegram's parser would see it that way.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

from gateway.operator_shell.mdv2 import (
    ParseError,
    parse,
    parse_lenient,
    render_panel,
    to_strict,
)

PKG = "gateway.operator_shell"


# ------------------------------------------------------------------ oracle
# The parser is the measuring instrument for everything below, so it is
# checked against hand-computed expectations first.


@pytest.mark.parametrize(
    "src,plain,ents",
    [
        ("*bold*", "bold", [("bold", 0, 4)]),
        ("_it_", "it", [("italic", 0, 2)]),
        ("__u__", "u", [("underline", 0, 1)]),
        ("a *b* c _d_", "a b c d", [("bold", 2, 1), ("italic", 6, 1)]),
        ("escaped \\* star", "escaped * star", []),
        ("`code_x`", "code_x", [("code", 0, 6)]),
        ("||sp||", "sp", [("spoiler", 0, 2)]),
        ("[t](http://x)", "t", [("text_link", 0, 1)]),
        ("~s~", "s", [("strikethrough", 0, 1)]),
        ("*b _i_ b*", "b i b", [("bold", 0, 5), ("italic", 2, 1)]),
    ],
)
def test_oracle_parses_markdownv2(src, plain, ents):
    got_plain, got_ents = parse(src)
    assert got_plain == plain
    assert [(e.type, e.offset, e.length) for e in got_ents] == ents


@pytest.mark.parametrize("bad", ["*unclosed", "dot. unescaped", "a_b", "trailing\\"])
def test_oracle_rejects_what_telegram_rejects(bad):
    """These are exactly the inputs that draw 'can't parse entities' (HTTP 400)."""
    with pytest.raises(ParseError):
        parse(bad)


# ------------------------------------------------- the authoring convention


@pytest.mark.parametrize(
    "src,expected",
    [
        ("*Mission* — 3 open, v2.1 (ok)", [("bold", "Mission")]),
        ("_2026-08-06T00:12 · 4m ago_", [("italic", "2026-08-06T00:12 · 4m ago")]),
        # snake_case contains a balanced '_var_' and must NOT italicise
        ("file my_var_name.py changed", []),
        ("3 * 4 = 12", []),
        ("unbalanced *start only", []),
        ("run `hermes send --to telegram`", [("code", "hermes send --to telegram")]),
        ("nested *outer _inner_ done*", [("bold", "outer inner done"), ("italic", "inner")]),
    ],
)
def test_authoring_convention(src, expected):
    _plain, ents = parse_lenient(src)
    assert [(e.type, e.text) for e in ents] == expected


def test_roundtrip_guarantee():
    """parse(to_strict(parse_lenient(x))) == parse_lenient(x), and always valid."""
    samples = [
        "*Mission* — 3 open, v2.1 (ok)",
        "_4m ago_ · my_var_name.py",
        "a - b + c = d! (e) {f} #h |i| ~j~",
        "💻 *SDLC* — [Full pipeline](estate:sdlc)",
        "100% done. next: 3-5 items",
        "```\ncode block\n```",
    ]
    for s in samples:
        intent_plain, intent_ents = parse_lenient(s)
        out = to_strict(intent_plain, intent_ents)
        got_plain, got_ents = parse(out)  # raises if invalid → test fails
        assert got_plain == intent_plain, s
        assert [(e.type, e.offset, e.length) for e in got_ents] == [
            (e.type, e.offset, e.length) for e in intent_ents
        ], s


def test_link_target_survives():
    """A text_link re-emitted without its URL is a dead link (regression)."""
    out = render_panel("[Full pipeline](estate:sdlc)")
    _plain, ents = parse(out)
    link = next(e for e in ents if e.type == "text_link")
    assert link.url == "estate:sdlc"


def test_nested_entities_do_not_interleave():
    """Interleaved markers (_a*b_c*) are invalid MarkdownV2 and draw a 400."""
    out = render_panel("*outer _inner_ tail*")
    parse(out)  # raises ParseError if the markers interleave


# ------------------------------------------------------- the real cockpit


def _renderable_panels():
    """Every zero-argument panel renderer in operator_shell."""
    import gateway.operator_shell as pkg

    for mi in pkgutil.iter_modules(pkg.__path__):
        try:
            mod = importlib.import_module(f"{PKG}.{mi.name}")
        except Exception:
            continue
        for name, fn in vars(mod).items():
            if not inspect.isfunction(fn):
                continue
            if getattr(fn, "__module__", "") != f"{PKG}.{mi.name}":
                continue
            if not (name.startswith("render") or name in ("card", "panel")):
                continue
            try:
                sig = inspect.signature(fn)
            except (ValueError, TypeError):
                continue
            if any(
                p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                for p in sig.parameters.values()
            ):
                continue
            yield f"{mi.name}.{name}", fn


def _panel_text(result):
    if isinstance(result, str):
        return result
    if hasattr(result, "text"):
        return getattr(result, "text") or ""
    if isinstance(result, tuple):
        return next((x for x in result if isinstance(x, str)), "")
    return str(result)


def test_every_panel_survives_the_send_path():
    """No panel may lose a bold, lose an italic, or change its visible text.

    This is the whole of the defect, measured across the real cockpit.
    """
    corrupted, unparseable, checked = [], [], 0

    for label, fn in _renderable_panels():
        try:
            src = _panel_text(fn())
        except Exception:
            continue  # a panel needing live estate state is not this test's subject
        if not src:
            continue
        checked += 1

        intent_plain, intent_ents = parse_lenient(src)
        out = render_panel(src)
        try:
            got_plain, got_ents = parse(out)
        except ParseError as e:
            unparseable.append(f"{label}: {e}")
            continue

        lost = [
            (e.type, e.text)
            for e in intent_ents
            if not any(g.type == e.type and g.text == e.text for g in got_ents)
        ]
        if lost or got_plain != intent_plain:
            corrupted.append(f"{label}: lost={lost[:4]} text_changed={got_plain != intent_plain}")

    assert checked >= 20, f"only {checked} panels rendered — the sweep proved nothing"
    assert not unparseable, "panels that would draw a 400:\n" + "\n".join(unparseable)
    assert not corrupted, "panels whose markup was corrupted:\n" + "\n".join(corrupted)


def test_format_message_would_corrupt_the_same_panel_text():
    """Pins WHY render_panel exists: the CommonMark converter inverts markup.

    If this ever fails, format_message changed — re-check whether panels still
    need their own path before deleting anything.
    """
    telegram = pytest.importorskip("gateway.platforms.telegram")
    adapter = telegram.TelegramAdapter.__new__(telegram.TelegramAdapter)

    src = "*Fleet* — 3 up\n_4m ago_"
    converted = adapter.format_message(src)

    _plain, ents = parse(converted)
    kinds = {e.type for e in ents}
    assert "bold" not in kinds, (
        "format_message no longer demotes authored bold — re-evaluate render_panel"
    )

    _p2, ents2 = parse(render_panel(src))
    assert {e.type for e in ents2} == {"bold", "italic"}
