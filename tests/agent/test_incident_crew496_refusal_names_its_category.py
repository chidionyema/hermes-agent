"""Incident test, crew#496 (2026-08-27): Otto refused six founder turns and the
log said ``refusal=(no text)``. Anthropic had sent ``stop_details.category`` on
every one; the transport dropped it. The rule: a refusal is never logged without
its category. Both ways: a refusal carries it, a normal stop carries nothing."""
from types import SimpleNamespace

from agent.transports.anthropic import AnthropicTransport, refusal_summary


def _resp(stop_reason, stop_details=None):
    return SimpleNamespace(content=[], stop_reason=stop_reason, stop_details=stop_details)


def test_refusal_carries_anthropic_category():
    r = AnthropicTransport().normalize_response(
        _resp("refusal", SimpleNamespace(category="cyber", explanation=None))
    )
    assert r.finish_reason == "content_filter"
    assert r.provider_data["refusal_details"] == {"category": "cyber", "explanation": None}
    assert refusal_summary(r) == "category=cyber"


def test_refusal_without_named_category_still_says_so():
    r = AnthropicTransport().normalize_response(_resp("refusal", None))
    assert refusal_summary(r) == "category=none"


def test_normal_stop_carries_no_refusal_details():
    r = AnthropicTransport().normalize_response(_resp("end_turn"))
    assert r.finish_reason == "stop"
    assert not (r.provider_data or {}).get("refusal_details")
    assert refusal_summary(r) == "category=none"
