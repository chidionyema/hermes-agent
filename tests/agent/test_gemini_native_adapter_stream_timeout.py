"""Transport errors on the Gemini SSE stream must stay httpx types.

A one-off read stall on the streaming connection used to arrive at the
streaming retry gate wrapped in GeminiAPIError. That gate decides
retryability with isinstance(e, httpx.ReadTimeout) and friends, so the
wrapped error matched nothing, HERMES_STREAM_RETRIES was never spent, and
the whole turn failed on a blip.
"""

from __future__ import annotations

import httpx
import pytest


def _client_raising(exc: BaseException):
    from agent.gemini_native_adapter import GeminiNativeClient

    client = GeminiNativeClient(api_key="AIza-test")

    def _boom(*args, **kwargs):
        raise exc

    client._http.stream = _boom  # type: ignore[method-assign]
    return client


def test_stream_read_timeout_is_not_wrapped():
    client = _client_raising(httpx.ReadTimeout("The read operation timed out"))

    stream = client._stream_completion(
        model="gemini-2.5-flash", request={}, timeout=None
    )

    with pytest.raises(httpx.TimeoutException):
        for _ in stream:
            pass


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.PoolTimeout("pool timed out"),
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("server disconnected"),
        httpx.ReadError("read failed"),
    ],
)
def test_stream_transport_errors_are_not_wrapped(exc):
    client = _client_raising(exc)

    stream = client._stream_completion(
        model="gemini-2.5-flash", request={}, timeout=None
    )

    with pytest.raises(type(exc)):
        for _ in stream:
            pass


def test_non_transport_http_error_still_wraps():
    """Only transport-class errors pass through; the wrap is kept otherwise."""
    from agent.gemini_native_adapter import GeminiAPIError

    client = _client_raising(httpx.DecodingError("bad body"))

    stream = client._stream_completion(
        model="gemini-2.5-flash", request={}, timeout=None
    )

    with pytest.raises(GeminiAPIError) as excinfo:
        for _ in stream:
            pass

    assert excinfo.value.code == "gemini_stream_error"
