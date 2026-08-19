"""The bot's own Telegram deep links.

Hermes has no public HTTP surface: ``deploy/hermes/fly.toml`` declares no
``[http_service]``, so a "permanent link" to a feature cannot be a web URL
without standing up new infrastructure. Telegram already provides one.
``https://t.me/<bot>?start=<payload>`` opens the chat and sends ``/start
<payload>``, which ``GatewaySlashCommandsMixin._handle_start_deeplink``
answers.

The bot's username is not in the config and not in ``.env`` — it is only
learned at runtime, from ``get_me()`` during ``Application.initialize()``. So
the Telegram platform records it here once it is connected and anything that
needs to PRINT a link reads it back. When it is unknown the link functions
return ``None`` and the caller prints nothing: a link to
``https://t.me/None`` is worse than no link.

Telegram restricts the ``start`` payload to ``A-Za-z0-9_-`` and 64 characters,
so ``build_deep_link`` encodes spaces as underscores and refuses anything it
cannot encode rather than emitting a URL Telegram will silently truncate.
"""

from __future__ import annotations

import os
import re

__all__ = ["set_bot_username", "bot_username", "build_deep_link", "summary_deep_link"]

# Telegram's own limit on the ?start= payload.
_MAX_PAYLOAD = 64
_PAYLOAD_OK = re.compile(r"^[A-Za-z0-9_-]*$")

_bot_username: str | None = None


def set_bot_username(name: str | None) -> None:
    """Record the connected bot's username (``@`` and case are ignored)."""
    global _bot_username
    cleaned = (name or "").strip().lstrip("@")
    _bot_username = cleaned or None


def bot_username() -> str | None:
    """The connected bot's username, or None if it is not known yet.

    ``TELEGRAM_BOT_USERNAME`` is honoured as a fallback so a CLI or a test can
    render a link without a live Telegram connection.
    """
    if _bot_username:
        return _bot_username
    from_env = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    return from_env or None


def build_deep_link(payload: str = "") -> str | None:
    """A ``t.me`` deep link for *payload*, or None if it cannot be built.

    Returns None when the bot username is unknown, when the payload is longer
    than Telegram allows, or when it contains a character Telegram's payload
    charset does not cover. Spaces become underscores, which is what
    ``_handle_start_deeplink`` turns back into spaces.
    """
    username = bot_username()
    if not username:
        return None
    encoded = (payload or "").strip().replace(" ", "_")
    if len(encoded) > _MAX_PAYLOAD or not _PAYLOAD_OK.match(encoded):
        return None
    if not encoded:
        return f"https://t.me/{username}"
    return f"https://t.me/{username}?start={encoded}"


def summary_deep_link(text: str = "") -> str | None:
    """The permanent link to the summary card, optionally pre-filled."""
    payload = "summary"
    text = (text or "").strip()
    if text:
        payload = f"summary {text}"
    return build_deep_link(payload)
