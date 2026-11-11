"""Cloud-run integration of the *real* Telegram adapter with Bot API 10.4 HTTP.

The network endpoint is replaced by an in-memory HTTPX transport; no live
Telegram credentials, misleading green receipts, or private chat messages.
Live phone evidence is a separate acceptance condition, not asserted here.
"""
import json
from types import SimpleNamespace

import httpx
import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram import adapter as telegram_adapter
from plugins.platforms.telegram import founder_widget as widget


@pytest.fixture
def telegram_server(monkeypatch):
    wire = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.host == "api.telegram.org"
        assert request.url.path.startswith("/bot123:TEST-ONLY/")
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content.decode("utf-8"))
        wire.append((method, payload))
        value = {"message_id": 713} if method == "sendWidget" else True
        return httpx.Response(200, json={"ok": True, "result": value})

    original_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(widget.httpx, "AsyncClient", client)
    return wire


def _query(query_id="q1", from_id=101, chat_id=101, chat_type="private", data="brief"):
    return {
        "id": query_id,
        "from": {"id": from_id},
        "chat": {"id": chat_id, "type": chat_type},
        "message_id": 713,
        "type": {"type": "data"},
        "data": data,
    }


def _snapshot():
    return {
        "r": 4,
        "at": 1794391200000,
        "id": "INS-100",
        "proof": "https://github.com/chidionyema/idp/actions/runs/100",
        "headline": "One item independently landed",
        "delivered": 1,
        "active": 2,
        "issues": 1,
    }


@pytest.mark.asyncio
async def test_bot_api_http_send_refresh_and_silent_edit(telegram_server):
    api = widget.OttoWidgetTransport(
        "123:TEST-ONLY", "101", snapshot_provider=_snapshot
    )
    assert await api.send() == 713
    assert (await api.handle_query(_query())) == "answered"
    assert [method for method, _ in telegram_server] == [
        "sendWidget", "answerWidgetQuery", "editMessageWidgetData",
    ]
    send = telegram_server[0][1]
    assert send["chat_id"] == 101
    assert send["is_shared"] is False and send["allow_queries"] is True
    assert "TelegramWidget" in send["html"]
    assert json.loads(send["chat_data"])["proof"].startswith("https://github.com/")
    assert telegram_server[1][1]["widget_query_id"] == "q1"
    assert json.loads(telegram_server[1][1]["data"])["delivered"] == 1
    assert telegram_server[2][1]["message_id"] == 713
    assert json.loads(telegram_server[2][1]["chat_data"])["r"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    _query("wrong-user", from_id=999),
    _query("wrong-chat", chat_id=999),
    _query("wrong-kind", chat_type="supergroup"),
    _query("bad-data", data="delete_everything"),
    {**_query("inline"), "inline_message_id": "inline-private"},
    {**_query("shared"), "widget_id": "forwarded"},
    {**_query("wrong-type"), "type": {"type": "sign_data"}},
    {**_query("bad-message"), "message_id": True},
])
async def test_widget_queries_fail_closed_without_exposing_evidence(telegram_server, query):
    api = widget.OttoWidgetTransport("123:TEST-ONLY", "101", snapshot_provider=_snapshot)
    assert await api.handle_query(query) == "refused"
    assert len(telegram_server) == 1
    method, payload = telegram_server[0]
    assert method == "answerWidgetQuery"
    assert json.loads(payload["data"]) == {"error": "not_authorized"}
    assert "INS-100" not in payload["data"]


@pytest.mark.asyncio
async def test_no_ledger_does_not_make_up_finished_work(telegram_server):
    api = widget.OttoWidgetTransport("123:TEST-ONLY", "101")
    assert await api.send() == 713
    state = json.loads(telegram_server[0][1]["chat_data"])
    assert state["r"] == 0 and state["at"] == 0
    assert "delivered" not in state and "proof" not in state


@pytest.mark.asyncio
async def test_malformed_evidence_refuses_optimistic_green(telegram_server):
    api = widget.OttoWidgetTransport(
        "123:TEST-ONLY", "101",
        snapshot_provider=lambda: {"r": 9, "at": 1794391200000, "delivered": 999},
    )
    assert await api.handle_query(_query()) == "answered"
    assert json.loads(telegram_server[0][1]["data"])["r"] == 0


@pytest.mark.asyncio
async def test_duplicate_query_does_not_repeat_bot_writes(telegram_server):
    api = widget.OttoWidgetTransport("123:TEST-ONLY", "101")
    assert await api.handle_query(_query()) == "answered"
    assert await api.handle_query(_query()) == "duplicate"
    assert len(telegram_server) == 2


@pytest.mark.asyncio
async def test_app_adapter_handles_widget_without_model_turn(telegram_server, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FOUNDER_CHAT_ID", "101")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "101")
    adapter = telegram_adapter.TelegramAdapter(PlatformConfig(enabled=True, token="123:TEST-ONLY"))
    adapter._verified_founder_brief_provider = _snapshot
    # SDK 22.8 surfaces new Telegram 10.4 fields through api_kwargs.
    update = SimpleNamespace(api_kwargs={"widget_query": _query()}, widget_query=None)
    await adapter._on_platform_update(update, None)
    assert [method for method, _ in telegram_server] == [
        "answerWidgetQuery", "editMessageWidgetData",
    ]


@pytest.mark.asyncio
async def test_app_adapter_refuses_without_explicit_founder_allowlist(telegram_server, monkeypatch):
    monkeypatch.setenv("TELEGRAM_FOUNDER_CHAT_ID", "101")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    adapter = telegram_adapter.TelegramAdapter(PlatformConfig(enabled=True, token="123:TEST-ONLY"))
    await adapter._on_platform_update(SimpleNamespace(api_kwargs={"widget_query": _query()}), None)
    assert telegram_server == []


def test_ptb_serialization_carries_bot_api_new_widget_event():
    """SDK upgrade or a raw-update hook is mandatory if PTB discards this field."""
    from telegram import Update

    raw = {"update_id": 200, "widget_query": _query()}
    parsed = Update.de_json(raw)
    assert widget.widget_query_from_update(parsed) == raw["widget_query"]


def test_polling_and_webhook_both_subscribe_to_widget_updates():
    source = widget.WIDGET_HTML.parent.parent.joinpath("adapter.py").read_text("utf-8")
    assert source.count('allowed_updates=[*Update.ALL_TYPES, "widget_query"]') == 2
