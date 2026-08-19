"""A brain that answers without reviewing must be recorded as a failure.

2026-08-13. The only curator run that ever wrote a machine-readable record filed this as the
skill review, with `llm_error = None` and `tool_calls = []`:

    You've used up your free trial — let's keep going.
    Continue at a flat monthly price — no per-token billing, no surprise charges.
    Set up your plan at https://standardcompute.com/dashboard/billing.

REPORT.md rendered it under `## LLM final summary`. Nothing raised, so nothing failed.

These cases pin the discriminator: no tool call AND no structured block means the model neither
acted nor answered in the form the prompt requires. Both have to be absent, so a real review that
genuinely found nothing to consolidate — which still emits the block with empty lists — is never
marked failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.curator import _mark_if_the_reviewer_never_reviewed  # noqa: E402

BILLING_PAGE = (
    "You've used up your free trial — let's keep going.\n\n"
    "Continue at a flat monthly price — no per-token billing, no surprise charges.\n\n"
    "Set up your plan at https://standardcompute.com/dashboard/billing."
)

GOOD_REVIEW = """I looked at all 16 agent-created skills.

## Structured summary (required)

```yaml
consolidations: []
prunings: []
```
"""


def _meta(**kw):
    base = {"final": "", "summary": "", "model": "m", "provider": "p",
            "tool_calls": [], "error": None}
    base.update(kw)
    return base


def test_the_billing_page_is_recorded_as_a_failure():
    m = _meta(final=BILLING_PAGE)
    _mark_if_the_reviewer_never_reviewed(m)
    assert m["error"], "a sales page filed as a skill review must not read as a successful run"
    assert "did not review anything" in m["error"]
    assert "free trial" in m["error"], "the operator needs to see what the model actually said"
    assert m["summary"] == m["error"]


def test_an_empty_response_is_recorded_as_a_failure():
    m = _meta(final="")
    _mark_if_the_reviewer_never_reviewed(m)
    assert m["error"]
    assert "(empty response)" in m["error"]


def test_a_real_review_with_nothing_to_do_is_not_marked_failed():
    """The one that would make this guard worse than useless if it fired."""
    m = _meta(final=GOOD_REVIEW)
    _mark_if_the_reviewer_never_reviewed(m)
    assert m["error"] is None


def test_a_review_that_acted_is_never_marked_failed():
    m = _meta(final="deleted two skills", tool_calls=[{"name": "delete_skill", "arguments": "{}"}])
    _mark_if_the_reviewer_never_reviewed(m)
    assert m["error"] is None


def test_an_existing_error_is_not_overwritten():
    m = _meta(final=BILLING_PAGE, error="HTTP 429: Token Plan usage limit reached")
    _mark_if_the_reviewer_never_reviewed(m)
    assert m["error"] == "HTTP 429: Token Plan usage limit reached"


def test_a_populated_structured_block_passes():
    m = _meta(final="""
## Structured summary (required)

```yaml
consolidations:
  - from: old-skill
    into: umbrella-skill
    reason: duplicate
prunings: []
```
""")
    _mark_if_the_reviewer_never_reviewed(m)
    assert m["error"] is None
