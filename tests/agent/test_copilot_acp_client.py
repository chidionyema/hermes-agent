"""Focused regressions for the Copilot ACP shim safety layer."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.copilot_acp_client import CopilotACPClient


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()


class CopilotACPClientSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CopilotACPClient(acp_cwd="/tmp")

    def _dispatch(self, message: dict, *, cwd: str) -> dict:
        process = _FakeProcess()
        handled = self.client._handle_server_message(
            message,
            process=process,
            cwd=cwd,
            text_parts=[],
            reasoning_parts=[],
        )
        self.assertTrue(handled)
        payload = process.stdin.getvalue().strip()
        self.assertTrue(payload)
        return json.loads(payload)

    def test_request_permission_is_not_auto_allowed(self) -> None:
        response = self._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/request_permission",
                "params": {},
            },
            cwd="/tmp",
        )

        outcome = (((response.get("result") or {}).get("outcome") or {}).get("outcome"))
        self.assertEqual(outcome, "cancelled")

    def test_read_text_file_blocks_internal_hermes_hub_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            blocked = home / ".hermes" / "skills" / ".hub" / "index-cache" / "entry.json"
            blocked.parent.mkdir(parents=True, exist_ok=True)
            blocked.write_text('{"token":"sk-test-secret-1234567890"}')

            with patch.dict(
                os.environ,
                {"HOME": str(home), "HERMES_HOME": str(home / ".hermes")},
                clear=False,
            ):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "fs/read_text_file",
                        "params": {"path": str(blocked)},
                    },
                    cwd=str(home),
                )

        self.assertIn("error", response)

    def test_read_text_file_redacts_sensitive_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            secret_file = root / "config.env"
            secret_file.write_text("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012")

            # agent.redact snapshots HERMES_REDACT_SECRETS at import time into
            # _REDACT_ENABLED, so patching os.environ is a no-op. Flip the
            # module-level constant directly for the duration of the call.
            with patch("agent.redact._REDACT_ENABLED", True):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "fs/read_text_file",
                        "params": {"path": str(secret_file)},
                    },
                    cwd=str(root),
                )

        content = ((response.get("result") or {}).get("content") or "")
        self.assertNotIn("abc123def456", content)
        self.assertIn("OPENAI_API_KEY=", content)

    def test_write_text_file_reuses_write_denylist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            target = home / ".ssh" / "id_rsa"
            target.parent.mkdir(parents=True, exist_ok=True)

            with patch("agent.copilot_acp_client.is_write_denied", return_value=True, create=True):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(target),
                            "content": "fake-private-key",
                        },
                    },
                    cwd=str(home),
                )

        self.assertIn("error", response)
        self.assertFalse(target.exists())

    def test_write_text_file_respects_safe_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            safe_root = root / "workspace"
            safe_root.mkdir()
            outside = root / "outside.txt"

            with patch.dict(os.environ, {"HERMES_WRITE_SAFE_ROOT": str(safe_root)}, clear=False):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(outside),
                            "content": "should-not-write",
                        },
                    },
                    cwd=str(root),
                )

        self.assertIn("error", response)
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()


# ── HOME env propagation tests (from PR #11285) ─────────────────────

from unittest.mock import patch as _patch
import pytest


def _make_home_client(tmp_path):
    return CopilotACPClient(
        api_key="copilot-acp",
        base_url="acp://copilot",
        acp_command="copilot",
        acp_args=["--acp", "--stdio"],
        acp_cwd=str(tmp_path),
    )


def _fake_popen_capture(captured):
    def _fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        raise FileNotFoundError("copilot not found")
    return _fake


def test_run_prompt_preserves_real_home_when_profile_home_available(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "home").mkdir(parents=True)
    real_home = tmp_path / "real-home"
    real_home.mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    captured = {}
    client = _make_home_client(tmp_path)

    with _patch("agent.copilot_acp_client.subprocess.Popen", side_effect=_fake_popen_capture(captured)):
        with pytest.raises(RuntimeError, match="Could not start Copilot ACP command"):
            client._run_prompt("hello", timeout_seconds=1)

    assert captured["kwargs"]["env"]["HOME"] == str(real_home)
    assert captured["kwargs"]["env"]["HERMES_REAL_HOME"] == str(real_home)


def test_run_prompt_passes_home_when_parent_env_is_clean(monkeypatch, tmp_path):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    captured = {}
    client = _make_home_client(tmp_path)

    with _patch("agent.copilot_acp_client.subprocess.Popen", side_effect=_fake_popen_capture(captured)):
        with pytest.raises(RuntimeError, match="Could not start Copilot ACP command"):
            client._run_prompt("hello", timeout_seconds=1)

    assert "env" in captured["kwargs"]
    assert captured["kwargs"]["env"]["HOME"]


# ── Claude Code flavor: MCP tools instead of the prose <tool_call> contract ──
#
# Measured 2026-08-05 (~/.hermes/docs/CLAUDE_CLI_BRAIN.md): Claude Code refuses to
# fabricate <tool_call> blocks for tools it does not hold, and emits real calls as
# ACP session/update events that _extract_tool_calls_from_text can never see. So
# for that flavor the prose contract must be dropped and tools must arrive as MCP
# servers on session/new. Every default below stays on the copilot behaviour.

from agent.copilot_acp_client import (  # noqa: E402
    _FLAVOR_CLAUDE,
    _FLAVOR_COPILOT,
    _format_messages_as_prompt,
    _resolve_args,
    _resolve_flavor,
    _resolve_mcp_servers,
    _session_reuse_enabled,
)


def test_flavor_is_inferred_from_the_binary_name(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_FLAVOR", raising=False)
    assert _resolve_flavor("/usr/local/bin/claude-agent-acp") == _FLAVOR_CLAUDE
    assert _resolve_flavor("/usr/local/bin/claude-code-acp") == _FLAVOR_CLAUDE
    assert _resolve_flavor("copilot") == _FLAVOR_COPILOT
    assert _resolve_flavor("/opt/homebrew/bin/copilot") == _FLAVOR_COPILOT


def test_explicit_flavor_env_overrides_the_binary_name(monkeypatch):
    monkeypatch.setenv("HERMES_ACP_FLAVOR", "copilot")
    assert _resolve_flavor("/usr/local/bin/claude-agent-acp") == _FLAVOR_COPILOT
    monkeypatch.setenv("HERMES_ACP_FLAVOR", "claude")
    assert _resolve_flavor("copilot") == _FLAVOR_CLAUDE


def test_default_args_differ_by_flavor(monkeypatch):
    # claude-agent-acp speaks ACP on stdio with no flags; copilot needs both.
    monkeypatch.delenv("HERMES_COPILOT_ACP_ARGS", raising=False)
    assert _resolve_args(_FLAVOR_COPILOT) == ["--acp", "--stdio"]
    assert _resolve_args(_FLAVOR_CLAUDE) == []
    assert _resolve_args() == ["--acp", "--stdio"]


def test_whitespace_args_env_means_no_arguments(monkeypatch):
    # The only way to express "no arguments" through an env var.
    monkeypatch.setenv("HERMES_COPILOT_ACP_ARGS", "  ")
    assert _resolve_args(_FLAVOR_COPILOT) == []


def test_mcp_servers_default_to_empty(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_MCP_SERVERS", raising=False)
    monkeypatch.delenv("HERMES_ACP_MCP_HERMES", raising=False)
    assert _resolve_mcp_servers() == []


def test_hermes_mcp_server_is_opt_in(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_MCP_SERVERS", raising=False)
    monkeypatch.setenv("HERMES_ACP_MCP_HERMES", "1")
    servers = _resolve_mcp_servers()
    assert [s["name"] for s in servers] == ["hermes"]
    # --accept-hooks matters: an ACP child has no TTY to answer a hook prompt on.
    assert "--accept-hooks" in servers[0]["args"]


def test_malformed_mcp_servers_json_degrades_to_empty(monkeypatch):
    monkeypatch.setenv("HERMES_ACP_MCP_SERVERS", "{not json")
    assert _resolve_mcp_servers() == []
    monkeypatch.setenv("HERMES_ACP_MCP_SERVERS", '{"name": "hermes"}')  # object, not array
    assert _resolve_mcp_servers() == []


def test_session_reuse_is_off_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_REUSE_SESSION", raising=False)
    assert _session_reuse_enabled() is False
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "1")
    assert _session_reuse_enabled() is True
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "off")
    assert _session_reuse_enabled() is False


def test_claude_flavor_prompt_drops_the_prose_tool_contract():
    tools = [{"function": {"name": "get_weather", "description": "d", "parameters": {}}}]
    messages = [{"role": "user", "content": "hi"}]

    copilot_prompt = _format_messages_as_prompt(messages, tools=tools, flavor=_FLAVOR_COPILOT)
    assert "<tool_call>" in copilot_prompt
    assert "get_weather" in copilot_prompt

    claude_prompt = _format_messages_as_prompt(messages, tools=tools, flavor=_FLAVOR_CLAUDE)
    assert "<tool_call>" not in claude_prompt
    assert "get_weather" not in claude_prompt


def test_default_flavor_argument_keeps_the_copilot_contract():
    prompt = _format_messages_as_prompt(
        [{"role": "user", "content": "hi"}],
        tools=[{"function": {"name": "get_weather", "parameters": {}}}],
    )
    assert "<tool_call>" in prompt


def test_client_wires_flavor_args_and_mcp_together(monkeypatch):
    monkeypatch.setenv("HERMES_COPILOT_ACP_COMMAND", "/usr/local/bin/claude-agent-acp")
    monkeypatch.delenv("HERMES_COPILOT_ACP_ARGS", raising=False)
    monkeypatch.delenv("HERMES_ACP_FLAVOR", raising=False)
    monkeypatch.delenv("HERMES_ACP_MCP_SERVERS", raising=False)
    monkeypatch.setenv("HERMES_ACP_MCP_HERMES", "1")
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "1")

    client = CopilotACPClient(acp_cwd="/tmp")
    assert client._acp_flavor == _FLAVOR_CLAUDE
    assert client._acp_args == []
    assert [s["name"] for s in client._mcp_servers] == ["hermes"]
    assert client._reuse_session is True
    assert client._live is None


def test_close_drops_the_cached_session(monkeypatch):
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "1")
    client = CopilotACPClient(acp_cwd="/tmp")
    client._live = object()
    client.close()
    assert client._live is None


# ── the blocking fork change: mcpServers must reach session/new ──
#
# Before this, session/new hardcoded "mcpServers": [] (copilot_acp_client.py, the
# line now at :680), so an agent-flavored child had no Hermes tools at all and the
# only way to give it any was the prose <tool_call> contract it refuses to honour.


class _FakeACPProcess:
    """Minimum surface _run_prompt_locked touches on a spawned ACP child."""

    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = iter(())      # reader thread drains and exits
        self.stderr = iter(())
        self.pid = 4242
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


def _drive(client, monkeypatch, *, spawns, requests):
    """Run _run_prompt with the JSON-RPC layer stubbed; record spawns + requests."""

    def _fake_popen(argv, **kwargs):
        spawns.append(argv)
        return _FakeACPProcess()

    def _fake_request_on(self, transport, method, params, **kwargs):
        requests.append((method, params))
        if method == "session/new":
            return {"sessionId": "sess-1"}
        return {}

    monkeypatch.setattr("agent.copilot_acp_client.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(CopilotACPClient, "_request_on", _fake_request_on)
    return client._run_prompt("hello", timeout_seconds=5)


def test_mcp_servers_are_sent_on_session_new(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_MCP_SERVERS", raising=False)
    monkeypatch.setenv("HERMES_ACP_MCP_HERMES", "1")
    monkeypatch.delenv("HERMES_ACP_REUSE_SESSION", raising=False)

    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []
    _drive(client, monkeypatch, spawns=spawns, requests=requests)

    new = [p for m, p in requests if m == "session/new"]
    assert len(new) == 1
    assert [s["name"] for s in new[0]["mcpServers"]] == ["hermes"]


def test_no_mcp_servers_configured_sends_an_empty_list(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_MCP_SERVERS", raising=False)
    monkeypatch.delenv("HERMES_ACP_MCP_HERMES", raising=False)

    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []
    _drive(client, monkeypatch, spawns=spawns, requests=requests)

    new = [p for m, p in requests if m == "session/new"][0]
    assert new["mcpServers"] == []


def test_reuse_keeps_one_child_and_one_session_across_turns(monkeypatch):
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "1")
    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []

    _drive(client, monkeypatch, spawns=spawns, requests=requests)
    first = client._live
    assert first is not None and first.session_id == "sess-1"

    client._run_prompt("second turn", timeout_seconds=5)

    # The whole point: no second spawn, no second initialize/session/new — those
    # are what cost the ~30s async MCP registration.
    assert len(spawns) == 1
    assert [m for m, _ in requests].count("session/new") == 1
    assert [m for m, _ in requests].count("initialize") == 1
    assert [m for m, _ in requests].count("session/prompt") == 2
    assert client._live is first


def test_without_reuse_every_turn_spawns_a_fresh_child(monkeypatch):
    monkeypatch.delenv("HERMES_ACP_REUSE_SESSION", raising=False)
    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []

    _drive(client, monkeypatch, spawns=spawns, requests=requests)
    assert client._live is None
    client._run_prompt("second turn", timeout_seconds=5)

    assert len(spawns) == 2
    assert [m for m, _ in requests].count("session/new") == 2


def test_a_dead_child_is_not_reused(monkeypatch):
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "1")
    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []

    _drive(client, monkeypatch, spawns=spawns, requests=requests)
    client._live.process.returncode = 1      # child exited between turns
    client._run_prompt("second turn", timeout_seconds=5)

    assert len(spawns) == 2
    assert [m for m, _ in requests].count("session/new") == 2


def test_a_failed_turn_drops_the_cached_session(monkeypatch):
    monkeypatch.setenv("HERMES_ACP_REUSE_SESSION", "1")
    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []
    _drive(client, monkeypatch, spawns=spawns, requests=requests)
    assert client._live is not None

    def _boom(self, transport, method, params, **kwargs):
        raise RuntimeError("pipe died mid-turn")

    monkeypatch.setattr(CopilotACPClient, "_request_on", _boom)
    with pytest.raises(RuntimeError, match="pipe died mid-turn"):
        client._run_prompt("second turn", timeout_seconds=5)

    # Prompting into a dead pipe forever is the failure this guards.
    assert client._live is None


def test_spawned_argv_uses_the_flavor_defaults(monkeypatch):
    monkeypatch.setenv("HERMES_COPILOT_ACP_COMMAND", "/usr/local/bin/claude-agent-acp")
    monkeypatch.delenv("HERMES_COPILOT_ACP_ARGS", raising=False)
    monkeypatch.delenv("HERMES_ACP_FLAVOR", raising=False)
    client = CopilotACPClient(acp_cwd="/tmp")
    spawns, requests = [], []
    _drive(client, monkeypatch, spawns=spawns, requests=requests)

    # claude-agent-acp exits non-zero on --acp/--stdio; it must be spawned bare.
    assert spawns[0] == ["/usr/local/bin/claude-agent-acp"]
