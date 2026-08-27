"""crew#284, 2026-08-27 00:08Z and 01:01Z: the founder's /sb-list reached the model as chat.

Rung 4 (incident). Both times the Telegram agent was mid-turn. The busy path resolves commands
with hermes_cli.commands.resolve_command(), which knows nothing about plugin-registered commands,
so /sb-list fell through to the steer logic and was injected into the running turn as text. The
gateway must answer a plugin command while busy, from the plugin, and must try that before it
steers or queues the message.
"""
import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock

RUN_PY = Path(__file__).resolve().parents[2] / "gateway" / "run.py"


def _runner():
    from gateway.run import GatewayRunner
    return object.__new__(GatewayRunner)


def _event(text):
    from gateway.platforms.base import MessageEvent
    return MessageEvent(text=text)


def test_busy_plugin_command_is_answered_by_the_plugin(monkeypatch):
    import hermes_cli.plugins as plugins
    seen = {}

    def handler(args):
        seen["args"] = args
        return "3 sessions"

    monkeypatch.setattr(plugins, "get_plugin_command_handler", lambda name: handler if name == "sb-list" else None)
    reply = asyncio.run(_runner()._dispatch_busy_plugin_command(_event("/sb_list all"), "sb_list"))
    assert reply == "3 sessions" and seen["args"] == "all"


def test_unknown_command_falls_through(monkeypatch):
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(plugins, "get_plugin_command_handler", lambda name: None)
    assert asyncio.run(_runner()._dispatch_busy_plugin_command(_event("/nothing"), "nothing")) is None


def test_busy_path_tries_the_plugin_before_it_steers():
    src = RUN_PY.read_text()
    i_builtin = src.index("return await self._dispatch_busy_slash_command(")
    i_plugin = src.index("self._dispatch_busy_plugin_command(event, _evt_cmd)", i_builtin)
    i_steer = src.index("effective_busy_input_mode = self._effective_busy_input_mode(source)", i_builtin)
    assert i_builtin < i_plugin < i_steer
