"""Founder-only Telegram Bot API 10.4 widget transport.

Uses the existing Telegram bot's polling/webhook connection. PTB 22.8 stores
unknown widget_query update fields in Update.api_kwargs; it does not require
a second getUpdates consumer or access to the founder's laptop.

Fail closed: no founder allowlist, no private chat, no trusted evidence source,
or no persistent message binding means no fabricated delivery claims.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_HTML = Path(__file__).with_name("otto_founder_compass.html")
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_MAX_DATA = 1024
_MAX_HTML = 32768


def encode_data(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(body.encode("utf-8")) > _MAX_DATA:
        raise ValueError("Telegram widget data exceeds 1024 bytes")
    return body


def html_payload() -> str:
    body = _HTML.read_bytes()
    if len(body) > _MAX_HTML or len(zlib.compress(body, level=9)) > 4096:
        raise ValueError("Telegram widget HTML exceeds its 10.4 byte budget")
    return body.decode("utf-8")


def unavailable() -> dict[str, Any]:
    # Never invent 0 for an unavailable ledger; the widget renders null as —.
    return {"r": 0, "at": 0, "delivered": None, "active": None,
            "issues": None, "headline": "Verified estate evidence is currently unavailable.",
            "priority": "The evidence source has not supplied a current receipt.",
            "id": "", "proof": ""}


def project_ledger(source: Any, *, now_ms: int | None = None) -> dict[str, Any]:
    """Project ONLY source-backed, current Genesis records; never grade text claims."""
    fallback = unavailable()
    if not isinstance(source, dict):
        return fallback
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    date = source.get("generatedAt")
    if not isinstance(date, str):
        return fallback
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return fallback
        at = int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return fallback
    if at > now_ms + 60_000 or now_ms - at >= 600_000:
        return fallback
    instructions = source.get("instructions")
    yields = source.get("yieldReceipts")
    if not isinstance(instructions, list) or not isinstance(yields, list):
        return fallback

    proven = [r for r in yields if isinstance(r, dict)
              and isinstance(r.get("verifiedSha"), str)
              and bool(_SHA.fullmatch(r["verifiedSha"]))
              and r.get("yieldBps") == 10000
              and r.get("fabricationDeltas") == 0
              and r.get("outcomesSatisfied") == r.get("outcomesTotal")
              and isinstance(r.get("verifiedArtifacts"), list)
              and not r.get("missingArtifacts")]
    proven_ids = {r.get("instructionId") for r in proven
                  if isinstance(r.get("instructionId"), str)}
    settled = [i for i in instructions if isinstance(i, dict)
               and i.get("instructionId") in proven_ids
               and i.get("stage") == "RECONCILED_AND_RATIFIED"
               and i.get("reconciled") is True]
    in_flight = [i for i in instructions if isinstance(i, dict)
                 and i.get("stage") in ("INITIATED", "IN_FLIGHT_YIELDING", "CANDIDATE_EMITTED")]
    troubled = [i for i in instructions if isinstance(i, dict)
                and i.get("stage") in ("CIF_FALSIFIED", "WORK_STOLEN_REMEDIATING")]
    sample = settled[-1] if settled else None
    receipt = next((r for r in reversed(proven)
                    if sample is not None and r.get("instructionId") == sample.get("instructionId")), None)
    return {
        "r": 0, "at": at, "delivered": len(set(i["instructionId"] for i in settled)),
        "active": len(in_flight), "issues": len(troubled),
        "headline": f"{len(set(i['instructionId'] for i in settled))} instructions reconciled in the source ledger.",
        "priority": (f"{len(troubled)} falsified or recovering instructions need attention."
                     if troubled else "No falsified or recovering instructions in the current ledger."),
        "id": (str(sample["instructionId"])[:96] if sample else ""),
        "proof": (str(receipt.get("yieldId", ""))[:120] if receipt else ""),
    }


class OttoWidgetService:
    """One bot-token-scoped widget, bound to one private founder chat and message."""

    def __init__(self, *, token: str, founder_chat_id: str,
                 allowed_users: set[str], state_file: Path, evidence_url: str = "",
                 evidence_token: str = "", client: httpx.AsyncClient | None = None):
        if not token or not founder_chat_id.isdecimal() or int(founder_chat_id) <= 0:
            raise ValueError("Telegram founder token and private numeric chat ID required")
        if founder_chat_id not in allowed_users:
            raise ValueError("founder chat must be explicitly present in allowed users")
        self._token = token
        self._token_fingerprint = hashlib.sha256(token.encode('utf-8')).hexdigest()
        self._chat_id = founder_chat_id
        self._allowed_users = frozenset(allowed_users)
        self._state_file = Path(state_file)
        self._evidence_url = evidence_url
        self._evidence_token = evidence_token
        self._client = client
        self._lock = asyncio.Lock()
        self._seen: dict[str, float] = {}
        self._state = self._read_state()

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self._state_file.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and value.get("chat_id") == self._chat_id
                and value.get("bot_fingerprint") == self._token_fingerprint
                and type(value.get("message_id")) is int and value["message_id"] > 0
                and type(value.get("revision")) is int and value["revision"] >= 0):
                return value
        except (FileNotFoundError, OSError, ValueError, TypeError):
            pass
        return {}

    def _persist(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state["bot_fingerprint"] = self._token_fingerprint
        fd, name = tempfile.mkstemp(prefix=".otto-widget-", dir=str(self._state_file.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(self._state, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self._state_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def _api(self, method: str, data: dict[str, Any]) -> Any:
        # Never log an httpx exception: its URL may contain the bot token.
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            if self._client:
                response = await self._client.post(url, json=data, timeout=5.0)
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(url, json=data)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("Telegram rejected widget request")
            return result.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"Telegram widget {method} transport failed") from None

    async def _snapshot(self) -> dict[str, Any]:
        if not self._evidence_url or not self._evidence_token:
            return unavailable()
        # Operator-defined URL is not taken from Telegram input.
        if not self._evidence_url.startswith("https://"):
            return unavailable()
        try:
            headers = {"Authorization": f"Bearer {self._evidence_token}"}
            if self._client:
                response = await self._client.get(self._evidence_url, headers=headers, timeout=3.0)
            else:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    response = await client.get(self._evidence_url, headers=headers)
            response.raise_for_status()
            if "application/json" not in response.headers.get("content-type", ""):
                return unavailable()
            return project_ledger(response.json())
        except (httpx.HTTPError, ValueError, TypeError):
            return unavailable()

    def _revisioned(self, data: dict[str, Any]) -> dict[str, Any]:
        canonical = encode_data({k: v for k, v in data.items() if k != "r"})
        current = self._state.get("snapshot_hash")
        revision = self._state.get("revision", 0)
        if canonical != current:
            revision += 1
        result = {**data, "r": revision}
        encode_data(result)
        self._state["snapshot_hash"] = canonical
        self._state["revision"] = revision
        return result

    async def publish_once(self) -> int:
        """Send exactly once while the durable message binding exists."""
        async with self._lock:
            if self._state.get("message_id"):
                return self._state["message_id"]
            snapshot = self._revisioned(await self._snapshot())
            result = await self._api("sendWidget", {
                "chat_id": int(self._chat_id), "html": html_payload(),
                "allow_queries": True, "is_shared": False,
                "chat_data": encode_data(snapshot),
                "caption": "OTTO Private Office — source-backed estate brief. Open the widget for evidence and refresh.",
            })
            if not isinstance(result, dict) or type(result.get("message_id")) is not int:
                raise RuntimeError("sendWidget lacked a message_id; refusing false delivery")
            self._state.update(chat_id=self._chat_id, message_id=result["message_id"])
            self._persist()
            return result["message_id"]

    async def handle_update(self, update: Any) -> bool:
        """Receive PTB 22.8 forward-compatible widget_query from Update.api_kwargs."""
        raw = getattr(update, "api_kwargs", {})
        query = raw.get("widget_query") if hasattr(raw, "get") else None
        if query is None:
            return False
        if not isinstance(query, dict) or not isinstance(query.get("id"), str):
            return True  # no safe query id to acknowledge
        qid = query["id"]
        if len(qid) > 128 or not qid:
            return True
        now = time.monotonic()
        # Prevent repeated processing of the same query during a reconnect.
        self._seen = {k: t for k, t in self._seen.items() if now - t < 120}
        if qid in self._seen:
            return True
        self._seen[qid] = now
        source = query.get("from")
        chat = query.get("chat")
        authenticated = (
            isinstance(source, dict) and isinstance(chat, dict)
            and str(source.get("id", "")) in self._allowed_users
            and str(source.get("id", "")) == self._chat_id
            and str(chat.get("id", "")) == self._chat_id
            and chat.get("type") == "private"
            and query.get("message_id") == self._state.get("message_id")
            and query.get("data") == "brief"
        )
        if not authenticated:
            await self._api("answerWidgetQuery", {
                "widget_query_id": qid, "data": encode_data({"error": "unavailable"})
            })
            return True

        # No slow model invocation: evidence fetch is bounded to 3 seconds.
        async with self._lock:
            data = self._revisioned(await self._snapshot())
            encoded = encode_data(data)
            # Answer first so client-side request resolves within 15 seconds.
            await self._api("answerWidgetQuery", {"widget_query_id": qid, "data": encoded})
            previous = self._state.get("last_delivered")
            if encoded != previous:
                try:
                    await self._api("editMessageWidgetData", {
                        "chat_id": int(self._chat_id),
                        "message_id": self._state["message_id"],
                        "chat_data": encoded,
                    })
                    self._state["last_delivered"] = encoded
                except RuntimeError:
                    # The answer is already delivered; an edit failure remains
                    # undelivered and is retried on the next valid request.
                    pass
            self._persist()
        return True
