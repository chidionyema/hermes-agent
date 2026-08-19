"""The summary card must be reachable without recall.

Measured 2026-08-19: ``summary`` was a fully registered gateway command
(``hermes_cli/commands.py``) that appeared in NO menu. Under the operator
profile ``filter_to_operator_menu`` keeps only the names listed in
``OPERATOR_TELEGRAM_MENU``, so /summary was filtered out of setMyCommands
entirely: the only way to reach it was to type it from memory. Founder,
2026-08-19: "the summary/numerology feature needs vast improvement and
surfacing as menu option and permanent link".

Two failure classes are pinned here.

1. A menu name that is not a registered command is dropped SILENTLY —
   ``filter_to_operator_menu`` keeps names it finds and ignores the rest, so
   a typo or a newly-gated command removes a menu entry with no error
   anywhere. ``test_every_menu_name_survives_the_filter`` fails instead.

2. The permanent link. ``t.me/<bot>?start=summary`` sends ``/start summary``,
   and every ``/start`` used to be swallowed as a platform ping, so any deep
   link this estate published would have been answered with silence.
"""
from __future__ import annotations

import asyncio

import gateway.operator_shell.menu as menu_mod
from gateway.operator_shell.menu import OPERATOR_TELEGRAM_MENU
from gateway.platforms.telegram import MAX_COMMANDS_PER_SCOPE
from gateway.slash_commands import GatewaySlashCommandsMixin
from hermes_cli.commands import telegram_menu_commands


class _Event:
    """The two attributes the deep-link handler reads."""

    def __init__(self, args: str) -> None:
        self._args = args
        self.source = None

    def get_command_args(self) -> str:
        return self._args


def _run(shell: "_Shell", event: "_Event"):
    """Call the async handler without a plugin dependency.

    pytest-asyncio is not installed in this interpreter: an
    ``@pytest.mark.asyncio`` test is collected, silently skipped as an
    un-awaited coroutine and reported as a PASS. A test that never runs is
    worse than no test.
    """
    return asyncio.run(shell._handle_start_deeplink(event))


class _Shell(GatewaySlashCommandsMixin):
    """The mixin with the access gate stubbed to a recorded verdict."""

    def __init__(self, denial: str | None = None) -> None:
        self._denial = denial
        self.checked: list[str] = []

    def _check_slash_access(self, source, canonical_cmd):  # noqa: ANN001
        self.checked.append(canonical_cmd)
        return self._denial


# ---------------------------------------------------------------- menu slot


def test_summary_is_in_the_operator_menu():
    assert "summary" in OPERATOR_TELEGRAM_MENU


def test_every_menu_name_survives_the_live_menu_build(monkeypatch):
    """No menu entry may be dropped silently on the way to setMyCommands.

    This runs the function the gateway itself calls
    (``gateway/platforms/telegram.py:2423``), not a stand-in.
    """
    monkeypatch.setattr(menu_mod, "resolve_telegram_menu_profile", lambda *a, **k: "operator")
    registered, _hidden = telegram_menu_commands(max_commands=MAX_COMMANDS_PER_SCOPE)
    kept = {name for name, _desc in registered}
    missing = [n for n in OPERATOR_TELEGRAM_MENU if n not in kept]
    assert not missing, (
        f"listed in OPERATOR_TELEGRAM_MENU but absent from the menu the "
        f"gateway registers, so unreachable except by recall: {missing}"
    )


def test_the_menu_fits_telegram_scope_cap():
    assert len(OPERATOR_TELEGRAM_MENU) <= MAX_COMMANDS_PER_SCOPE


def test_menu_has_no_duplicates():
    assert len(set(OPERATOR_TELEGRAM_MENU)) == len(OPERATOR_TELEGRAM_MENU)


# ------------------------------------------------------------ permanent link


def test_bare_start_stays_a_silent_ping():
    assert _run(_Shell(), _Event("")) is None
    assert _run(_Shell(), _Event("   ")) is None


def test_unknown_payload_stays_a_silent_ping():
    assert _run(_Shell(), _Event("nonsense")) is None
    assert _run(_Shell(), _Event("summaries")) is None


def test_summary_payload_renders_the_usage_card():
    out = _run(_Shell(), _Event("summary"))
    assert out is not None
    assert "Summary Card" in out
    assert "/summary" in out


def test_payload_underscores_become_spaces():
    """``?start=summary_Chidi_Onyema`` must render the card for that name.

    Telegram restricts the start payload to ``A-Za-z0-9_-``, so a space
    cannot travel literally.
    """
    out = _run(_Shell(), _Event("summary_Chidi_Onyema"))
    assert out is not None
    assert "Chidi Onyema" in out
    assert "Isopsephy Card" in out


def test_payload_is_case_insensitive():
    out = _run(_Shell(), _Event("SUMMARY_Anna"))
    assert out is not None
    assert "Isopsephy Card" in out


def test_the_deep_link_is_not_a_way_around_the_access_gate():
    shell = _Shell(denial="not allowed")
    out = _run(shell, _Event("summary_Anna"))
    assert out == "not allowed"
    assert shell.checked == ["summary"], (
        "the payload must be access-checked as the command it names, "
        "not as /start"
    )


def test_the_deep_link_renders_exactly_what_the_command_renders():
    from gateway.slash_commands import render_summary_reply

    linked = _run(_Shell(), _Event("summary_Anna"))
    assert linked == render_summary_reply("Anna")


# ── The permanent link ────────────────────────────────────────────────────
#
# Hermes has no public HTTP surface (deploy/hermes/fly.toml declares no
# [http_service]), so the permanent link to the summary card is a Telegram
# deep link. The class these tests close: a printed link whose payload the
# handler does not accept. The link and the handler are written in two files
# and nothing compared them until `test_the_printed_link_round_trips`.


def _with_username(name, fn):
    """Run *fn* with the recorded bot username set, then restore it."""
    from gateway.operator_shell import deeplink

    previous = deeplink.bot_username()
    deeplink.set_bot_username(name)
    try:
        return fn()
    finally:
        deeplink.set_bot_username(previous)


def test_no_username_means_no_link(monkeypatch):
    from gateway.operator_shell import deeplink
    from gateway.slash_commands import render_summary_reply

    monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)

    def _check():
        assert deeplink.build_deep_link("summary") is None
        out = render_summary_reply("")
        assert "t.me" not in out, "A link with no username must not be printed"
        assert "None" not in out

    _with_username(None, _check)


def test_env_username_is_a_fallback(monkeypatch):
    from gateway.operator_shell import deeplink

    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@FallbackBot")
    _with_username(None, lambda: (
        _assert_eq(deeplink.bot_username(), "FallbackBot")
    ))


def _assert_eq(got, want):
    assert got == want, f"{got!r} != {want!r}"


def test_usage_card_prints_the_permanent_link():
    from gateway.slash_commands import render_summary_reply

    out = _with_username("HermesBot", lambda: render_summary_reply(""))
    assert "https://t.me/HermesBot?start=summary" in out


def test_the_printed_link_round_trips():
    """The payload the link carries must be one the handler answers."""
    from gateway.operator_shell.deeplink import summary_deep_link

    link = _with_username("HermesBot", lambda: summary_deep_link("Chidi Onyema"))
    assert link is not None
    payload = link.split("?start=", 1)[1]
    reply = _run(_Shell(), _Event(payload))
    assert reply is not None, f"Handler ignored its own link payload {payload!r}"
    assert "Chidi Onyema" in reply
    assert "Isopsephy Card" in reply


def test_at_sign_and_case_are_normalised():
    from gateway.operator_shell.deeplink import build_deep_link

    assert _with_username("@HermesBot", lambda: build_deep_link()) == (
        "https://t.me/HermesBot"
    )


def test_payload_too_long_for_telegram_is_refused():
    from gateway.operator_shell.deeplink import build_deep_link, summary_deep_link

    assert _with_username("HermesBot", lambda: build_deep_link("x" * 65)) is None
    assert _with_username("HermesBot", lambda: build_deep_link("x" * 64)) is not None
    # 64 chars total, so "summary " + 56 characters of name is the ceiling.
    assert _with_username("HermesBot", lambda: summary_deep_link("y" * 57)) is None


def test_payload_outside_telegrams_charset_is_refused():
    from gateway.operator_shell.deeplink import build_deep_link

    for bad in ("summary caf\u00e9", "summary a/b", "summary a?b", "summary a&b"):
        assert _with_username("HermesBot", lambda: build_deep_link(bad)) is None, bad


def test_a_spaced_payload_is_legal_because_spaces_become_underscores():
    """`/summary A vs B` survives the round trip, so its link must too."""
    from gateway.operator_shell.deeplink import summary_deep_link

    link = _with_username("HermesBot", lambda: summary_deep_link("Anna vs Beth"))
    assert link == "https://t.me/HermesBot?start=summary_Anna_vs_Beth"
    reply = _run(_Shell(), _Event(link.split("?start=", 1)[1]))
    assert reply is not None
    assert "Anna" in reply and "Beth" in reply
