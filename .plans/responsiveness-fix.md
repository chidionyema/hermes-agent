# Hermes Responsiveness Fix — Address All Audit Concerns

**Status:** in progress
**Scope:** `run_agent.py`, `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `gateway/run.py`

---

## Problem

The deep audit identified eight bottlenecks causing slowness/unresponsiveness:

| # | Bottleneck | Severity |
|---|-----------|----------|
| 1 | Zero progress feedback (`gateway_notify_interval: 0`) | Critical |
| 2 | No token streaming — waits for full response | Critical |
| 3 | Polling-based interrupt (200ms loop) | High |
| 4 | Synchronous tool execution blocks everything | High |
| 5 | Heavy `build_turn_context()` overhead per turn | Medium |
| 6 | Max 60 turns allows tool-call spirals | Medium |
| 7 | No TTFB monitoring — user sees nothing | Medium |
| 8 | Persistent shell accumulates state | Low |

---

## Fixes (3 tiers)

### Tier 1: Config (already applied to `~/.hermes/config.yaml`)

| Setting | Old | New | Reason |
|---------|-----|-----|--------|
| `agent.gateway_notify_interval` | 0 | 30 | Users get status every 30s |
| `agent.gateway_timeout_warning` | 900 | 300 | Warn at 5min, not 15min |
| `agent.max_turns` | 60 | 30 | Prevent tool-call spirals |
| `terminal.timeout` | 300 | 120 | Limit stuck shell commands |
| `browser.command_timeout` | 30 | 15 | Faster browser feedback |

### Tier 2: TTFB watchdog + typing indicators (~60 lines)

**A. Typing indicator on message dispatch**

In `gateway/run.py` `_handle_message_with_agent()` — send typing indicator immediately
when a user message is accepted for processing, before `_run_agent` starts. Platforms
that don't support typing indicators (WhatsApp, Webhook) are no-ops.

**B. TTFB watchdog — "Working..." after 10s of silence**

In `gateway/run.py` `_run_agent()` — after the agent starts running in the thread pool,
spin an async task that sends a "Working on it…" status message if no response arrives
within 10 seconds. The status is cleared when the agent finishes or the first progress
event fires. No new env vars — uses existing `gateway_notify_interval` logic.

### Tier 3: Streaming Phase 1 (per `.plans/streaming-support.md`)

Feature-flagged with `streaming.enabled: false` (default). Zero risk to existing behavior.

1. **`run_agent.py`** — `_run_streaming_chat_completion()` (~65 lines)
   - Streams tokens from Chat Completions API with `stream=True`
   - Emits text deltas via `stream_callback`
   - Accumulates tool_call deltas into a fake non-streaming response
   - Falls back to non-streaming on any error

2. **`agent/chat_completion_helpers.py`** — wire streaming into `_interruptible_api_call()`
   - When `stream_callback is not None`, use `_run_streaming_chat_completion` instead of
     synchronous `client.chat.completions.create()`.
   - The existing `_call()` thread keeps interrupt support working unchanged.

3. **`agent/conversation_loop.py`** — pass `stream_callback` through
   - In `run_conversation()`, pass the callback through `_interruptible_api_call`.
   - The callback receives text deltas from the API and routes them to the consumer.

4. **`gateway/run.py`** — progressive message editing
   - When `streaming.enabled: true`, spin a `stream_preview` async task that
     progressively edits the response message on platforms that support editing
     (Telegram, Discord, Slack). WhatsApp/HA fall back to non-streaming.
   - Uses `_stream_q` (queue) + `_stream_done` (event) pattern from the spec.

---

## Acceptance

- **Config (Tier 1):** applicable immediately on next gateway restart.
- **Tier 2:** for any message, typing indicator appears within 1s; "Working…" status
  appears within 10s if API is slow. Both clear when response arrives.
- **Tier 3 (feature-flagged):**
  - `streaming.enabled: false` — all existing tests pass unchanged.
  - Unit tests for `_run_streaming_chat_completion` pass.
  - Integration test: with `streaming.enabled: true` on Telegram, the response is
    progressively edited rather than sent once.

## Files

| File | Tier | Change |
|---|---|---|
| `~/.hermes/config.yaml` | 1 | 5 values changed (done) |
| `gateway/run.py` | 2 | ~60 lines — typing indicator + TTFB watchdog |
| `run_agent.py` | 3 | ~65 lines — `_run_streaming_chat_completion()` |
| `agent/chat_completion_helpers.py` | 3 | ~10 lines — streaming branch in `_call()` |
| `agent/conversation_loop.py` | 3 | ~5 lines — pass callback through |
| `tests/test_streaming.py` | 3 | ~150 lines — unit tests |

---

## Out of scope

- Phases 2-4 of the streaming plan (gateway consumers, CLI streaming, API server SSE).
- Interrupt mechanism rewrite (the current polling approach stays).
- `build_turn_context()` optimization.
- Persistent shell state cleanup.
