"""End-to-end Telegram Bot API 10.4 staging contract.

Real HTTPX request serialization, durable message binding, gateway Update
dispatch and fail-closed negative controls. The endpoint is a local transport,
not a claim that production Telegram or a phone client was exercised.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter
from plugins.platforms.telegram.widgets.otto_widget_service import (
    OttoWidgetService, encode_data, project_ledger, unavailable, html_payload,
)


@pytest.fixture
def api(tmp_path):
    requests = []

    def handler(req: httpx.Request):
        assert req.method == "POST" and req.url.host == "api.telegram.org"
        assert req.url.path.startswith("/bot123:TEST-ONLY/")
        method = req.url.path.rsplit("/", 1)[-1]
        data = json.loads(req.content)
        requests.append((method, data))
        value = {"message_id": 713} if method == "sendWidget" else True
        return httpx.Response(200, json={"ok": True, "result": value})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OttoWidgetService(
        token="123:TEST-ONLY", founder_chat_id="101",
        allowed_users={"101"}, state_file=tmp_path / "state" / "widget.json",
        client=client,
    )
    return service, requests, client


def _query(id="q1", sender=101, chat=101, chat_type="private", data="brief"):
    return SimpleNamespace(api_kwargs={"widget_query": {
        "id": id, "from": {"id": sender}, "chat": {"id": chat, "type": chat_type},
        "message_id": 713, "type": {"type": "data"}, "data": data,
    }})


@pytest.mark.asyncio
async def test_real_bot_api_http_send_answer_edit_and_durable_idempotence(api):
    service, requests, client = api
    try:
        assert await service.publish_once() == 713
        assert await service.publish_once() == 713  # no second chat message
        assert await service.handle_update(_query()) is True
        assert [method for method, _ in requests] == [
            "sendWidget", "answerWidgetQuery", "editMessageWidgetData",
        ]
        sent = requests[0][1]
        assert sent["chat_id"] == 101
        assert sent["is_shared"] is False and sent["allow_queries"] is True
        assert "TelegramWidget" in sent["html"]
        assert sent["caption"]
        # Absent live ledger remains unavailable, not a counterfeit 0.
        assert json.loads(sent["chat_data"])["at"] == 0
        assert json.loads(requests[1][1]["data"])["delivered"] is None
        assert requests[2][1]["message_id"] == 713
        assert service._state_file.exists()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"from": {"id": 999}},
    {"chat": {"id": 999, "type": "private"}},
    {"chat": {"id": 101, "type": "group"}},
    {"data": "delete_everything"},
    {"message_id": 909},
    {"chat": None},
])
async def test_refused_widget_request_cannot_disclose_founder_ledger(api, changes):
    service, requests, client = api
    try:
        await service.publish_once()
        raw = dict(_query("unauthorized").api_kwargs["widget_query"])
        raw.update(changes)
        assert await service.handle_update(SimpleNamespace(api_kwargs={"widget_query": raw})) is True
        assert [method for method, _ in requests] == ["sendWidget", "answerWidgetQuery"]
        assert json.loads(requests[1][1]["data"]) == {"error": "unavailable"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_widget_query_is_acknowledged_once(api):
    service, requests, client = api
    try:
        await service.publish_once()
        assert await service.handle_update(_query()) is True
        assert await service.handle_update(_query()) is True
        assert len(requests) == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_reconnected_gateway_recovers_message_binding(api, tmp_path):
    service, requests, client = api
    try:
        assert await service.publish_once() == 713
        recovered = OttoWidgetService(
            token="123:TEST-ONLY", founder_chat_id="101", allowed_users={"101"},
            state_file=service._state_file, client=client,
        )
        assert await recovered.publish_once() == 713
        assert len(requests) == 1
    finally:
        await client.aclose()


def test_bogus_or_stale_ledger_cannot_claim_a_verified_completion():
    assert unavailable()["delivered"] is None
    assert project_ledger(None)["at"] == 0
    stale = {
        "generatedAt": "2020-01-01T00:00:00Z",
        "instructions": [{"instructionId": "ABC", "reconciled": True, "stage": "RECONCILED_AND_RATIFIED"}],
        "yieldReceipts": [{"instructionId": "ABC", "verifiedSha": "a" * 40,
                          "yieldBps": 10000, "fabricationDeltas": 0,
                          "outcomesSatisfied": 1, "outcomesTotal": 1,
                          "verifiedArtifacts": [1]}],
    }
    assert project_ledger(stale)["delivered"] is None
    fresh = {**stale, "generatedAt": datetime.now(timezone.utc).isoformat()}
    assert project_ledger(fresh)["delivered"] == 1
    assert project_ledger({**fresh, "yieldReceipts": []})["delivered"] == 0
    assert "TelegramWidget" in html_payload()
    assert len(encode_data(unavailable()).encode("utf-8")) <= 1024


@pytest.mark.asyncio
async def test_real_gateway_dispatch_consumes_widget_without_model_turn(api, monkeypatch):
    service, requests, client = api
    try:
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "101")
        monkeypatch.setenv("TELEGRAM_FOUNDER_CHAT_ID", "101")
        adapter = TelegramAdapter(PlatformConfig(enabled=True, token="123:TEST-ONLY"))
        adapter._otto_widget_transport = service
        await service.publish_once()
        await adapter._on_platform_update(_query(), None)
        assert [method for method, _ in requests] == [
            "sendWidget", "answerWidgetQuery", "editMessageWidgetData",
        ]
    finally:
        await client.aclose()


def test_sdk_must_preserve_new_104_widget_query_on_real_update_object():
    from telegram import Update
    raw = _query("sdk-q").api_kwargs
    u = Update.de_json({"update_id": 200, **raw})
    assert u.api_kwargs.get("widget_query") == raw["widget_query"]


def test_both_inbound_pipelines_request_104_widget_updates():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] /
              "plugins/platforms/telegram/adapter.py").read_text("utf-8")
    assert source.count('allowed_updates=[*Update.ALL_TYPES, "widget_query"]') == 2
