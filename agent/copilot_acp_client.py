"""OpenAI-compatible shim that forwards Hermes requests to an ACP agent.

This adapter lets Hermes treat an Agent Client Protocol server as a chat-style
backend. Each request sends the formatted conversation as a single prompt,
collects text chunks, and converts the result back into the minimal shape Hermes
expects from an OpenAI client.

It was written for `copilot --acp` and that remains the default in every
respect. It also drives Claude Code via @agentclientprotocol/claude-agent-acp,
which needs two things Copilot does not — real MCP tools instead of the prose
<tool_call> contract, and a session held open across prompts. Both are opt-in
and off by default; see _resolve_flavor / _resolve_mcp_servers /
_session_reuse_enabled below and ~/.hermes/docs/CLAUDE_CLI_BRAIN.md.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.file_safety import get_read_block_error, is_write_denied
from agent.redact import redact_sensitive_text

logger = logging.getLogger(__name__)

ACP_MARKER_BASE_URL = "acp://copilot"
_DEFAULT_TIMEOUT_SECONDS = 900.0

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TOOL_CALL_JSON_RE = re.compile(r"\{\s*\"id\"\s*:\s*\"[^\"]+\"\s*,\s*\"type\"\s*:\s*\"function\"\s*,\s*\"function\"\s*:\s*\{.*?\}\s*\}", re.DOTALL)

# Stderr fingerprint of the deprecated `gh copilot` CLI extension
# (https://github.blog/changelog/2025-09-25-upcoming-deprecation-of-gh-copilot-cli-extension).
# We require BOTH the literal product name ("gh-copilot") AND a deprecation
# marker, so generic stderr from the NEW `@github/copilot` CLI — whose repo
# is github.com/github/copilot-cli and which legitimately mentions "copilot-cli"
# in its own banners and error messages — doesn't get misclassified as the
# deprecated extension.
_DEPRECATION_REQUIRED = ("gh-copilot",)
_DEPRECATION_MARKERS = (
    "has been deprecated",
    "no commands will be executed",
)


def _is_gh_copilot_deprecation_message(stderr_text: str) -> bool:
    """True iff stderr looks like the deprecated gh-copilot extension's banner."""

    lower = stderr_text.lower()
    if not any(req in lower for req in _DEPRECATION_REQUIRED):
        return False
    return any(marker in lower for marker in _DEPRECATION_MARKERS)


def _resolve_command() -> str:
    return (
        os.getenv("HERMES_COPILOT_ACP_COMMAND", "").strip()
        or os.getenv("COPILOT_CLI_PATH", "").strip()
        or "copilot"
    )


def _resolve_args(flavor: str | None = None) -> list[str]:
    raw = os.getenv("HERMES_COPILOT_ACP_ARGS", "")
    if raw.strip():
        return shlex.split(raw)
    if raw:
        # Explicitly set to whitespace — the caller means "no arguments". This
        # is the only way to express that, and claude-agent-acp needs it: it
        # speaks ACP on stdio with no flags and rejects `--acp --stdio`.
        return []
    if flavor == _FLAVOR_CLAUDE:
        return []
    return ["--acp", "--stdio"]


# ── ACP flavor ────────────────────────────────────────────────────────────
# This module was written for `copilot --acp`, which honours the prose
# tool-call contract in _format_messages_as_prompt (describe the OpenAI tools
# array, ask for <tool_call>{...}</tool_call> blocks, regex them back out).
#
# Claude Code, driven through @agentclientprotocol/claude-agent-acp, cannot use
# that contract — measured 2026-08-05, see ~/.hermes/docs/CLAUDE_CLI_BRAIN.md:
#   * it checks its real tool list and refuses to fabricate a call for a tool it
#     does not have, and
#   * when it does call a tool the call arrives as an ACP session/update event,
#     never as assistant text, so _extract_tool_calls_from_text can never see it.
# For that flavor the prose contract is not merely useless, it is harmful: it
# spends tokens asking for output the agent will refuse to produce. Tools reach
# it the real way instead — as MCP servers passed to session/new.
_FLAVOR_COPILOT = "copilot"
_FLAVOR_CLAUDE = "claude"

# The cheapest Claude, and what an ACP-driven `claude` runs unless the operator
# names another. Mirrors prospector/claude_cli.py CHEAPEST_CLAUDE_MODEL, which
# exists for the same reason: measured 2026-08-19, an unpinned claude binary uses
# the MACHINE's Claude Code default, `opus[1m]`. Founder directive 2026-08-19 —
# both estates fall back to the cheapest Claude, enforced and documented.
#
# ANTHROPIC_MODEL is the only lever that works here. The model argument to
# chat.completions.create reaches the agent as PROSE ("Hermes requested model
# hint: ..."), and a sentence in a prompt does not choose the model. The bridge
# reads ANTHROPIC_MODEL from its own environment
# (@agentclientprotocol/claude-agent-acp 0.65.0, dist/acp-agent.js:5366-5403).
CHEAPEST_CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def _resolve_claude_model(requested: str | None = None) -> str:
    """Which Claude an ACP child may run. Env beats caller beats the cheap default."""
    return (
        os.getenv("HERMES_ACP_CLAUDE_MODEL", "").strip()
        or (requested or "").strip()
        or CHEAPEST_CLAUDE_MODEL
    )


def _resolve_flavor(command: str) -> str:
    """Which ACP agent are we driving? Explicit env wins, else infer from argv0."""
    raw = os.getenv("HERMES_ACP_FLAVOR", "").strip().lower()
    if raw in (_FLAVOR_CLAUDE, _FLAVOR_COPILOT):
        return raw
    return _FLAVOR_CLAUDE if "claude" in Path(command).name.lower() else _FLAVOR_COPILOT


def _hermes_mcp_server() -> dict[str, Any]:
    """Hermes' own tools, as an ACP mcpServers entry.

    `--accept-hooks` / HERMES_ACCEPT_HOOKS matters: without it the server can
    block on a hook prompt, and an ACP child has no TTY to answer it on.
    """
    return {
        "name": "hermes",
        "command": os.getenv("HERMES_MCP_COMMAND", "").strip()
        or str(Path.home() / ".local" / "bin" / "hermes"),
        "args": ["mcp", "serve", "--accept-hooks"],
        "env": [{"name": "HERMES_ACCEPT_HOOKS", "value": "1"}],
    }


def _resolve_mcp_servers() -> list[dict[str, Any]]:
    """MCP servers to hand the ACP agent at session/new.

    Default is [] — unchanged from the copilot-only behaviour. Opt in with
    HERMES_ACP_MCP_HERMES=1 (Hermes' own `hermes mcp serve`, 10 tools) or supply
    a full JSON array in HERMES_ACP_MCP_SERVERS.
    """
    raw = os.getenv("HERMES_ACP_MCP_SERVERS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            logger.warning(
                "HERMES_ACP_MCP_SERVERS is not valid JSON — ignoring it and "
                "starting the ACP session with no MCP servers."
            )
            return []
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
        logger.warning(
            "HERMES_ACP_MCP_SERVERS must be a JSON array of server objects, got %s "
            "— ignoring.", type(parsed).__name__,
        )
        return []
    if os.getenv("HERMES_ACP_MCP_HERMES", "").strip().lower() in {"1", "true", "yes", "on"}:
        return [_hermes_mcp_server()]
    return []


def _session_reuse_enabled() -> bool:
    """Keep one ACP process + session alive across prompts?

    Off by default (the copilot path has always been spawn-per-prompt). It is
    effectively REQUIRED for the claude flavor with MCP: registration is
    asynchronous and took ~30s to complete in measurement, so a spawn-per-prompt
    client would pay that cost on every turn and would intermittently run with no
    tools at all.
    """
    raw = os.getenv("HERMES_ACP_REUSE_SESSION", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return False


def _resolve_home_dir() -> str:
    """Return a stable HOME for child ACP processes."""
    home = os.environ.get("HOME", "").strip()
    if home:
        return home

    expanded = os.path.expanduser("~")
    if expanded and expanded != "~":
        return expanded

    try:
        import pwd

        resolved = pwd.getpwuid(os.getuid()).pw_dir.strip()  # windows-footgun: ok — POSIX fallback inside try/except (pwd import fails on Windows)
        if resolved:
            return resolved
    except Exception:
        pass

    # Last resort: /tmp (writable on any POSIX system). Avoids crashing the
    # subprocess with no HOME; callers can set HERMES_HOME explicitly if they
    # need a different writable dir.
    return "/tmp"


def _build_subprocess_env(
    flavor: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    home = _resolve_home_dir()
    env["HOME"] = home
    from hermes_constants import apply_subprocess_home_env
    apply_subprocess_home_env(env)

    if flavor == _FLAVOR_CLAUDE:
        # Pin the model. Without this the child runs the machine's Claude Code
        # default and the response still reports the model the CALLER asked for,
        # so telemetry reads "haiku" while opus burns. See CHEAPEST_CLAUDE_MODEL.
        #
        # What this saves is PLAN WINDOW, not dollars. On a Pro/Max subscription
        # the marginal dollar cost of a call is zero; the scarce thing is the
        # 5-hour usage window, which Claude and Claude Code share. That window is
        # weighted by model, not by raw token count — an opus token draws it down
        # roughly 5x faster than a haiku token. So an unpinned child spends the
        # founder's own Claude Code capacity 5x faster to do the same work.
        env["ANTHROPIC_MODEL"] = _resolve_claude_model(model)

        # Drop the inherited API key. os.environ.copy() above carries
        # ANTHROPIC_API_KEY into the child, and Anthropic's own documentation is
        # explicit about what that does: "If you have an ANTHROPIC_API_KEY
        # environment variable set on your system, Claude Code will use this API
        # key for authentication instead of your Claude subscription ... resulting
        # in API usage charges rather than using your subscription's included
        # usage."  (support.claude.com, article 11145838.)
        #
        # So this is NOT about the key being dead. Subscription usage and API-key
        # usage are separate funding pools: plan usage is drawn by whatever signs
        # in over OAuth, API keys are drawn from prepaid Console credits bought at
        # platform.claude.com. An inherited key moves the call between pools
        # silently, and it does so whether or not the key is funded. Today this
        # machine's key answers `HTTP 400 Your credit balance is too low`, so the
        # call would simply die; funding the key would be worse, because the call
        # would succeed and quietly bill a second account.
        #
        # The interactive `claude` CLI is protected from this by its own approval
        # list (~/.claude.json customApiKeyResponses, where this key is already
        # `rejected`). claude-agent-acp has no such prompt, so the child needs the
        # key removed rather than refused. coordinator.py:1125 pops it for the
        # same reason. Opt back in with HERMES_ACP_CLAUDE_USE_API_KEY=1 only when
        # metered Console billing is deliberately what you want.
        if os.getenv("HERMES_ACP_CLAUDE_USE_API_KEY", "").strip() != "1":
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_TOKEN", None)
    return env


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _permission_denied(message_id: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": {
            "outcome": {
                "outcome": "cancelled",
            }
        },
    }


def _format_messages_as_prompt(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    flavor: str = _FLAVOR_COPILOT,
) -> str:
    native_tools = flavor == _FLAVOR_CLAUDE
    if native_tools:
        # No prose tool contract: this agent executes its own tools and reports
        # them over ACP, so asking for <tool_call> blocks buys nothing and it
        # will (correctly) refuse to fake calls for tools it does not hold.
        sections: list[str] = [
            "You are being used as the active ACP agent backend for Hermes.",
            "Use your own tools directly to complete the task, then answer.",
            "Do not describe or simulate tool calls in your reply text — run them.",
        ]
    else:
        sections = [
            "You are being used as the active ACP agent backend for Hermes.",
            "Use ACP capabilities to complete tasks.",
            "IMPORTANT: If you take an action with a tool, you MUST output tool calls using <tool_call>{...}</tool_call> blocks with JSON exactly in OpenAI function-call shape.",
            "If no tool is needed, answer normally.",
        ]
    if model:
        sections.append(f"Hermes requested model hint: {model}")

    if native_tools:
        tools = None

    if isinstance(tools, list) and tools:
        tool_specs: list[dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") or {}
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            tool_specs.append(
                {
                    "name": name.strip(),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        if tool_specs:
            sections.append(
                "Available tools (OpenAI function schema). "
                "When using a tool, emit ONLY <tool_call>{...}</tool_call> with one JSON object "
                "containing id/type/function{name,arguments}. arguments must be a JSON string.\n"
                + json.dumps(tool_specs, ensure_ascii=False)
            )

    if tool_choice is not None:
        sections.append(f"Tool choice hint: {json.dumps(tool_choice, ensure_ascii=False)}")

    transcript: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown").strip().lower()
        if role == "tool":
            role = "tool"
        elif role not in {"system", "user", "assistant"}:
            role = "context"

        content = message.get("content")
        rendered = _render_message_content(content)
        if not rendered:
            continue

        label = {
            "system": "System",
            "user": "User",
            "assistant": "Assistant",
            "tool": "Tool",
            "context": "Context",
        }.get(role, role.title())
        transcript.append(f"{label}:\n{rendered}")

    if transcript:
        sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))

    sections.append("Continue the conversation from the latest user request.")
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "").strip()
        if "content" in content and isinstance(content.get("content"), str):
            return str(content.get("content") or "").strip()
        return json.dumps(content, ensure_ascii=True)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip()


def _extract_tool_calls_from_text(text: str) -> tuple[list[SimpleNamespace], str]:
    if not isinstance(text, str) or not text.strip():
        return [], ""

    extracted: list[SimpleNamespace] = []
    consumed_spans: list[tuple[int, int]] = []

    def _try_add_tool_call(raw_json: str) -> None:
        try:
            obj = json.loads(raw_json)
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        fn = obj.get("function")
        if not isinstance(fn, dict):
            return
        fn_name = fn.get("name")
        if not isinstance(fn_name, str) or not fn_name.strip():
            return
        fn_args = fn.get("arguments", "{}")
        if not isinstance(fn_args, str):
            fn_args = json.dumps(fn_args, ensure_ascii=False)
        call_id = obj.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"acp_call_{len(extracted)+1}"

        extracted.append(
            SimpleNamespace(
                id=call_id,
                call_id=call_id,
                response_item_id=None,
                type="function",
                function=SimpleNamespace(name=fn_name.strip(), arguments=fn_args),
            )
        )

    for m in _TOOL_CALL_BLOCK_RE.finditer(text):
        raw = m.group(1)
        _try_add_tool_call(raw)
        consumed_spans.append((m.start(), m.end()))

    # Only try bare-JSON fallback when no XML blocks were found.
    if not extracted:
        for m in _TOOL_CALL_JSON_RE.finditer(text):
            raw = m.group(0)
            _try_add_tool_call(raw)
            consumed_spans.append((m.start(), m.end()))

    if not consumed_spans:
        return extracted, text.strip()

    consumed_spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in consumed_spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        parts.append(text[cursor:])

    cleaned = "\n".join(p.strip() for p in parts if p and p.strip()).strip()
    return extracted, cleaned



def _ensure_path_within_cwd(path_text: str, cwd: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        raise PermissionError("ACP file-system paths must be absolute.")
    resolved = candidate.resolve()
    root = Path(cwd).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Path '{resolved}' is outside the session cwd '{root}'.") from exc
    return resolved


class _ACPChatCompletions:
    def __init__(self, client: "CopilotACPClient"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _ACPChatNamespace:
    def __init__(self, client: "CopilotACPClient"):
        self.completions = _ACPChatCompletions(client)


class CopilotACPClient:
    """Minimal OpenAI-client-compatible facade for Copilot ACP."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        acp_command: str | None = None,
        acp_args: list[str] | None = None,
        acp_cwd: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        reuse_session: bool | None = None,
        model: str | None = None,
        **_: Any,
    ):
        self.api_key = api_key or "copilot-acp"
        self.base_url = base_url or ACP_MARKER_BASE_URL
        self._default_headers = dict(default_headers or {})
        self._acp_command = acp_command or command or _resolve_command()
        # Flavor first: it decides the default argv (claude-agent-acp takes no
        # flags, `copilot` needs --acp --stdio) and the prompt contract.
        self._acp_flavor = _resolve_flavor(self._acp_command)
        _explicit_args = acp_args if acp_args is not None else args
        self._acp_args = list(
            _explicit_args if _explicit_args is not None else _resolve_args(self._acp_flavor)
        )
        self._acp_cwd = str(Path(acp_cwd or os.getcwd()).resolve())
        # The model this client's child process is pinned to. Resolved ONCE, here,
        # because the pin is applied to the subprocess environment at spawn time
        # and a reused session outlives any single call — so a per-call model
        # cannot change what is already running. None for non-claude flavors,
        # which carry no pin.
        self._pinned_model = (
            _resolve_claude_model(model) if self._acp_flavor == _FLAVOR_CLAUDE else model
        )
        # Explicit kwargs beat the environment. A caller that holds ONE session per
        # chat (gateway/operator_shell/coding_session.py) must be able to turn reuse
        # on for itself without exporting HERMES_ACP_REUSE_SESSION into the whole
        # gateway process, which would silently change every other ACP client too.
        self._mcp_servers = (
            list(mcp_servers) if mcp_servers is not None else _resolve_mcp_servers()
        )
        self._reuse_session = (
            bool(reuse_session) if reuse_session is not None else _session_reuse_enabled()
        )
        self.chat = _ACPChatNamespace(self)
        self.is_closed = False
        self._active_process: subprocess.Popen[str] | None = None
        self._active_process_lock = threading.Lock()
        # An ACP session is inherently sequential — one prompt at a time — so a
        # reused session has to serialise turns rather than interleave them.
        self._live_lock = threading.Lock()
        # Live transport, only populated when _reuse_session is on. Holding the
        # process AND the sessionId is the point: MCP registration is async and
        # measured at ~30s, so a fresh session per prompt would keep paying it.
        self._live: SimpleNamespace | None = None

    def close(self) -> None:
        proc: subprocess.Popen[str] | None
        with self._active_process_lock:
            proc = self._active_process
            self._active_process = None
        self._live = None
        self.is_closed = True
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **_: Any,
    ) -> Any:
        if (
            self._pinned_model
            and model
            and model.strip()
            and model.strip() != self._pinned_model
        ):
            logger.warning(
                "ACP child is pinned to %s; ignoring per-call model %s. The pin is "
                "applied to the subprocess environment at spawn, so changing model "
                "needs a new client (HERMES_ACP_CLAUDE_MODEL or model=).",
                self._pinned_model, model.strip(),
            )
        prompt_text = _format_messages_as_prompt(
            messages or [],
            model=self._pinned_model or model,
            tools=tools,
            tool_choice=tool_choice,
            flavor=self._acp_flavor,
        )
        # Normalise timeout: run_agent.py may pass an httpx.Timeout object
        # (used natively by the OpenAI SDK) rather than a plain float.
        if timeout is None:
            _effective_timeout = _DEFAULT_TIMEOUT_SECONDS
        elif isinstance(timeout, (int, float)):
            _effective_timeout = float(timeout)
        else:
            # httpx.Timeout or similar — pick the largest component so the
            # subprocess has enough wall-clock time for the full response.
            _candidates = [
                getattr(timeout, attr, None)
                for attr in ("read", "write", "connect", "pool", "timeout")
            ]
            _numeric = [float(v) for v in _candidates if isinstance(v, (int, float))]
            _effective_timeout = max(_numeric) if _numeric else _DEFAULT_TIMEOUT_SECONDS

        response_text, reasoning_text = self._run_prompt(
            prompt_text,
            timeout_seconds=_effective_timeout,
        )

        tool_calls, cleaned_text = _extract_tool_calls_from_text(response_text)

        usage = SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        assistant_message = SimpleNamespace(
            content=cleaned_text,
            tool_calls=tool_calls,
            reasoning=reasoning_text or None,
            reasoning_content=reasoning_text or None,
            reasoning_details=None,
        )
        finish_reason = "tool_calls" if tool_calls else "stop"
        choice = SimpleNamespace(message=assistant_message, finish_reason=finish_reason)
        # Report what RAN, not what was requested. `model=model` echoed the
        # caller's argument back verbatim, so a caller asking for haiku on an
        # unpinned child was told it got haiku while the machine default (opus)
        # answered. A cost reader believes this field.
        return SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=self._pinned_model or model or "copilot-acp",
        )

    def _run_prompt(self, prompt_text: str, *, timeout_seconds: float) -> tuple[str, str]:
        if not self._reuse_session:
            return self._run_prompt_locked(prompt_text, timeout_seconds=timeout_seconds)
        with self._live_lock:
            return self._run_prompt_locked(prompt_text, timeout_seconds=timeout_seconds)

    def _live_transport(self) -> SimpleNamespace | None:
        """The cached process+session, or None if it is gone or was never kept."""
        live = self._live
        if live is None:
            return None
        if live.process.poll() is not None:
            logger.info(
                "ACP session %s ended (rc=%s) — a new one will be started.",
                live.session_id, live.process.returncode,
            )
            self._live = None
            return None
        return live

    def _run_prompt_locked(self, prompt_text: str, *, timeout_seconds: float) -> tuple[str, str]:
        live = self._live_transport() if self._reuse_session else None
        if live is not None:
            return self._prompt_on(live, prompt_text, timeout_seconds=timeout_seconds)

        try:
            proc = subprocess.Popen(
                [self._acp_command] + self._acp_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self._acp_cwd,
                env=_build_subprocess_env(self._acp_flavor, self._pinned_model),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Could not start Copilot ACP command '{self._acp_command}'. "
                "Install GitHub Copilot CLI or set HERMES_COPILOT_ACP_COMMAND/COPILOT_CLI_PATH."
            ) from exc

        if proc.stdin is None or proc.stdout is None:
            proc.kill()
            raise RuntimeError("Copilot ACP process did not expose stdin/stdout pipes.")

        self.is_closed = False
        with self._active_process_lock:
            self._active_process = proc

        inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=40)

        def _stdout_reader() -> None:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                try:
                    inbox.put(json.loads(line))
                except Exception:
                    inbox.put({"raw": line.rstrip("\n")})

        def _stderr_reader() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                stderr_tail.append(line.rstrip("\n"))

        out_thread = threading.Thread(target=_stdout_reader, daemon=True)
        err_thread = threading.Thread(target=_stderr_reader, daemon=True)
        out_thread.start()
        err_thread.start()

        transport = SimpleNamespace(
            process=proc,
            inbox=inbox,
            stderr_tail=stderr_tail,
            next_id=0,
            session_id="",
        )

        def _request(method: str, params: dict[str, Any], *, text_parts: list[str] | None = None, reasoning_parts: list[str] | None = None) -> Any:
            return self._request_on(
                transport,
                method,
                params,
                timeout_seconds=timeout_seconds,
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            )

        try:
            _request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {
                            "readTextFile": True,
                            "writeTextFile": True,
                        }
                    },
                    "clientInfo": {
                        "name": "hermes-agent",
                        "title": "Hermes Agent",
                        "version": "0.0.0",
                    },
                },
            )
            session = _request(
                "session/new",
                {
                    "cwd": self._acp_cwd,
                    "mcpServers": self._mcp_servers,
                },
            ) or {}
            session_id = str(session.get("sessionId") or "").strip()
            if not session_id:
                raise RuntimeError("Copilot ACP did not return a sessionId.")
            transport.session_id = session_id
            if self._mcp_servers:
                logger.info(
                    "ACP session %s started with %d MCP server(s): %s. Registration is "
                    "asynchronous — tools may not be visible on the first turn.",
                    session_id,
                    len(self._mcp_servers),
                    ", ".join(str(s.get("name") or "?") for s in self._mcp_servers),
                )

            result = self._prompt_on(transport, prompt_text, timeout_seconds=timeout_seconds)
        except BaseException:
            self.close()
            raise

        if self._reuse_session:
            self._live = transport
        else:
            self.close()
        return result

    def _prompt_on(
        self,
        transport: SimpleNamespace,
        prompt_text: str,
        *,
        timeout_seconds: float,
    ) -> tuple[str, str]:
        """Send one session/prompt on an already-initialised transport."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            self._request_on(
                transport,
                "session/prompt",
                {
                    "sessionId": transport.session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": prompt_text,
                        }
                    ],
                },
                timeout_seconds=timeout_seconds,
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            )
        except BaseException:
            # A failed turn may have left the session unusable; drop it so the
            # next call starts clean rather than prompting into a dead pipe.
            if self._live is transport:
                self.close()
            raise
        return "".join(text_parts), "".join(reasoning_parts)

    def _request_on(
        self,
        transport: SimpleNamespace,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        text_parts: list[str] | None = None,
        reasoning_parts: list[str] | None = None,
    ) -> Any:
        proc: subprocess.Popen[str] = transport.process
        inbox: queue.Queue[dict[str, Any]] = transport.inbox
        stderr_tail: deque[str] = transport.stderr_tail
        if proc.stdin is None:
            raise RuntimeError("Copilot ACP process has no stdin.")

        transport.next_id += 1
        request_id = int(transport.next_id)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                msg = inbox.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._handle_server_message(
                msg,
                process=proc,
                cwd=self._acp_cwd,
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            ):
                continue

            if msg.get("id") != request_id:
                continue
            if "error" in msg:
                err = msg.get("error") or {}
                raise RuntimeError(
                    f"Copilot ACP {method} failed: {err.get('message') or err}"
                )
            return msg.get("result")

        stderr_text = "\n".join(stderr_tail).strip()
        if proc.poll() is not None and stderr_text:
            if _is_gh_copilot_deprecation_message(stderr_text):
                raise RuntimeError(
                    "Hermes ACP mode requires the NEW GitHub Copilot CLI "
                    "(github.com/github/copilot-cli), but the binary it just "
                    "spawned is the deprecated `gh copilot` extension.\n\n"
                    "Install the new CLI:\n"
                    "  npm install -g @github/copilot\n"
                    "  # then verify with: copilot --help\n\n"
                    "If `copilot` already resolves to the new CLI but you still see this,\n"
                    "point Hermes at it explicitly:\n"
                    "  export HERMES_COPILOT_ACP_COMMAND=/path/to/new/copilot\n\n"
                    "Alternative: use the `copilot` provider (no ACP, hits the Copilot API\n"
                    "directly with a Copilot subscription token) via `hermes setup`.\n\n"
                    f"Original error:\n{stderr_text}"
                )
            raise RuntimeError(f"Copilot ACP process exited early: {stderr_text}")
        raise TimeoutError(f"Timed out waiting for Copilot ACP response to {method}.")

    def _handle_server_message(
        self,
        msg: dict[str, Any],
        *,
        process: subprocess.Popen[str],
        cwd: str,
        text_parts: list[str] | None,
        reasoning_parts: list[str] | None,
    ) -> bool:
        method = msg.get("method")
        if not isinstance(method, str):
            return False

        if method == "session/update":
            params = msg.get("params") or {}
            update = params.get("update") or {}
            kind = str(update.get("sessionUpdate") or "").strip()
            content = update.get("content") or {}
            chunk_text = ""
            if isinstance(content, dict):
                chunk_text = str(content.get("text") or "")
            if kind == "agent_message_chunk" and chunk_text and text_parts is not None:
                text_parts.append(chunk_text)
            elif kind == "agent_thought_chunk" and chunk_text and reasoning_parts is not None:
                reasoning_parts.append(chunk_text)
            return True

        if process.stdin is None:
            return True

        message_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "session/request_permission":
            response = _permission_denied(message_id)
        elif method == "fs/read_text_file":
            try:
                path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd)
                block_error = get_read_block_error(str(path))
                if block_error:
                    raise PermissionError(block_error)
                try:
                    content = path.read_text()
                except FileNotFoundError:
                    content = ""
                line = params.get("line")
                limit = params.get("limit")
                if isinstance(line, int) and line > 1:
                    lines = content.splitlines(keepends=True)
                    start = line - 1
                    end = start + limit if isinstance(limit, int) and limit > 0 else None
                    content = "".join(lines[start:end])
                if content:
                    content = redact_sensitive_text(content, force=True)
                response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "content": content,
                    },
                }
            except Exception as exc:
                response = _jsonrpc_error(message_id, -32602, str(exc))
        elif method == "fs/write_text_file":
            try:
                path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd)
                if is_write_denied(str(path)):
                    raise PermissionError(
                        f"Write denied: '{path}' is a protected system/credential file."
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(params.get("content") or ""))
                response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": None,
                }
            except Exception as exc:
                response = _jsonrpc_error(message_id, -32602, str(exc))
        else:
            response = _jsonrpc_error(
                message_id,
                -32601,
                f"ACP client method '{method}' is not supported by Hermes yet.",
            )

        process.stdin.write(json.dumps(response) + "\n")
        process.stdin.flush()
        return True
