"""The streaming retry gate must see transport errors through a wrapper.

Defense in depth for the Gemini stream-timeout defect: even if some
provider adapter wraps a transport error in its own exception class, the
retry gate should still classify it as retryable rather than falling
through to the stub path.
"""

from __future__ import annotations

import httpx

from agent.chat_completion_helpers import _chain_isinstance

_TIMEOUTS = (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout)
_CONN_ERRS = (httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError)


def test_bare_timeout_matches():
    assert _chain_isinstance(httpx.ReadTimeout("The read operation timed out"), _TIMEOUTS)


def test_wrapped_timeout_matches_through_cause():
    try:
        raise httpx.ReadTimeout("The read operation timed out")
    except httpx.ReadTimeout as inner:
        wrapped = RuntimeError("Gemini streaming request failed")
        wrapped.__cause__ = inner

    assert _chain_isinstance(wrapped, _TIMEOUTS)


def test_wrapped_conn_error_matches_through_context():
    try:
        raise httpx.ConnectError("connection refused")
    except httpx.ConnectError as inner:
        wrapped = RuntimeError("wrapped")
        wrapped.__context__ = inner

    assert _chain_isinstance(wrapped, _CONN_ERRS)


def test_unrelated_error_does_not_match():
    assert not _chain_isinstance(ValueError("nope"), _TIMEOUTS)
    assert not _chain_isinstance(ValueError("nope"), _CONN_ERRS)


def test_self_referential_chain_terminates():
    err = RuntimeError("loop")
    err.__cause__ = err
    assert not _chain_isinstance(err, _TIMEOUTS)


def test_deep_chain_beyond_max_depth_does_not_match():
    """Depth is bounded at 5 — a deeper burial is intentionally not found."""
    inner = httpx.ReadTimeout("timed out")
    current: BaseException = inner
    for _ in range(8):
        outer = RuntimeError("wrap")
        outer.__cause__ = current
        current = outer

    assert not _chain_isinstance(current, _TIMEOUTS)
