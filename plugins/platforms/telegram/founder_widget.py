"""OTTO Telegram 10.4 widget transport, without waiting for PTB SDK support.

Only the existing founder's private chat is eligible. This is a *read-only*
projection: absent or stale verified evidence must never become a success count.
The PTB Update 22.8 `api_kwargs` compatibility path is bound by adapter tests.
"""
from __future__ import annotations

import inspect
import json
import logging
import zlib
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

logger = logging.getLogger(__name__)
WIDGET_HTML = Path(__file__).resolve().parent / "widgets" / "otto_founder_compass.html"
MAX_STATE_BYTES = 1024
MAX_HTML_BYTES = 32768
MAX_COMPRESSED_BYTES = 4096

# No proof source installed? Do not invent counters, models or successful work.
UNVERIFIED = {
    "r": 0,
    "at": 0,
    "headline": "No independently verified estate briefing is available.",
    "priority": "The evidence source is unavailable. No completion claim.",
}


class WidgetUnavailable(RuntimeError):
    """No trusted destination or conforming widget payload."""


def _json_blob(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_STATE_BYTES:
        raise WidgetUnavailable("widget state exceeds Telegram's 1024-byte limit")
    return payload


def _state(raw: Any) -> dict[str, Any]:
    """Accept source-backed evidence only; omit claims without valid provenance."""
    if not isinstance(raw, dict):
        return dict(UNVERIFIED)
    revision = raw.get("r")
    timestamp = raw.get("at")
    instruction = raw.get("id")
    proof = raw.get("proof")
    if (
        type(revision) is not int or revision < 1
        or type(timestamp) is not int or timestamp <= 0
        or not isinstance(instruction, str) or not instruction.strip()
        or not isinstance(proof, str) or not proof.strip()
    ):
        return dict(UNVERIFIED)
    result: dict[str, Any] = {
        "r": revision, "at": timestamp,
        "id": instruction[:100], "proof": proof[:130],
    }
    for key in ("delivered", "active", "issues"):
        value = raw.get(key)
        if type(value) is int and 0 <= value < 1_000_000:
            result[key] = value
    for key in ("headline", "priority"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value[:125]
    _json_blob(result)
    return result


def widget_query_from_update(update: object) -> dict[str, Any] | None:
    """Older PTB accepts unknown Bot API fields in Update.api_kwargs."""
    direct = getattr(update, "widget_query", None)
    if isinstance(direct, dict):
        return direct
    api_kwargs = getattr(update, "api_kwargs", None)
    if isinstance(api_kwargs, Mapping):
        payload = api_kwargs.get("widget_query")
        if isinstance(payload, dict):
            return payload
    return None


class OttoWidgetTransport:
    def __init__(
        self,
        token: str,
        founder_chat_id: str,
        *,
        snapshot_provider: Callable[[], Any] | None = None,
        api_call: Callable[[str, dict[str, Any]], Any] | None = None,
    ):
        self._token = token
        self._founder = str(founder_chat_id or "").strip()
        self._snapshot_provider = snapshot_provider
        self._api_call = api_call
        self._processed: set[str] = set()

    @property
    def configured(self) -> bool:
        return bool(self._token and self._founder.isdecimal() and int(self._founder) > 0)

    async def _api(self, method: str, payload: dict[str, Any]) -> Any:
        if not self.configured:
            raise WidgetUnavailable("founder destination or bot token is missing")
        if self._api_call is not None:
            result = self._api_call(method, payload)
            if inspect.isawaitable(result):
                result = await result
        else:
            # All failures are redacted; never print the URL containing the token.
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
                    response = await client.post(
                        f"https://api.telegram.org/bot{self._token}/{method}",
                        json=payload,
                    )
                    response.raise_for_status()
                    result = response.json()
            except Exception as exc:
                raise WidgetUnavailable("Telegram Bot API request failed") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise WidgetUnavailable("Telegram Bot API refused the operation")
        return result.get("result")

    async def _snapshot(self) -> dict[str, Any]:
        if self._snapshot_provider is None:
            return dict(UNVERIFIED)
        try:
            snapshot = self._snapshot_provider()
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
            return _state(snapshot)
        except Exception:
            logger.warning("OTTO verified brief unavailable; showing no claims")
            return dict(UNVERIFIED)

    async def send(self) -> int:
        """Send HTML widget *only* to the explicitly configured founder DM."""
        if not self.configured:
            raise WidgetUnavailable("founder destination not configured")
        html = WIDGET_HTML.read_bytes()
        if len(html) > MAX_HTML_BYTES or len(zlib.compress(html, 9)) > MAX_COMPRESSED_BYTES:
            raise WidgetUnavailable("widget exceeds Telegram HTML budgets")
        result = await self._api("sendWidget", {
            "chat_id": int(self._founder),
            "html": html.decode("utf-8"),
            "allow_queries": True,
            "is_shared": False,
            "chat_data": _json_blob(await self._snapshot()),
            "caption": "OTTO Private Office — live evidence only. Refresh inside Telegram.",
        })
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int or message_id <= 0:
            raise WidgetUnavailable("Telegram sendWidget returned no message identifier")
        return message_id

    def _authorized(self, query: Mapping[str, Any]) -> bool:
        sender = query.get("from")
        chat = query.get("chat")
        q_type = query.get("type")
        return bool(
            self.configured
            and isinstance(sender, Mapping)
            and str(sender.get("id")) == self._founder
            and isinstance(chat, Mapping)
            and str(chat.get("id")) == self._founder
            and chat.get("type") == "private"
            and isinstance(query.get("message_id"), int)
            and not query.get("inline_message_id")
            and not query.get("widget_id")
            and (not isinstance(q_type, Mapping) or q_type.get("type") == "data")
            and query.get("data") == "brief"
        )

    async def handle_query(self, query: Mapping[str, Any]) -> str:
        """Answer every new query promptly; never disclose evidence to another chat."""
        query_id = query.get("id")
        if not isinstance(query_id, str) or not 0 < len(query_id) <= 128:
            return "malformed"
        if query_id in self._processed:
            return "duplicate"
        self._processed.add(query_id)
        if len(self._processed) > 1024:
            self._processed = {query_id}

        allowed = self._authorized(query)
        snapshot = await self._snapshot() if allowed else None
        reply = _json_blob(snapshot if snapshot is not None else {"error": "not_authorized"})
        # Answer first: Telegram rejects widget queries older than 15 seconds.
        await self._api("answerWidgetQuery", {"widget_query_id": query_id, "data": reply})
        if not allowed:
            return "refused"
        assert snapshot is not None
        try:
            await self._api("editMessageWidgetData", {
                "chat_id": int(self._founder),
                "message_id": query["message_id"],
                "chat_data": _json_blob(snapshot),
            })
        except WidgetUnavailable:
            # A duplicate/no-op edit is not a lost query; the answer was delivered.
            logger.warning("OTTO widget edit unavailable; query was answered")
            return "answered-no-edit"
        return "answered"
