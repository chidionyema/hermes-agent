"""SDLC panel invariants — narrow env passed to gh, no secret leaks to subprocess.

F-NEW-10 regression test (commit 8c0de1d14d). Before the fix,
sdlc._builds_snapshot() passed \`env={**os.environ, ...}\` to subprocess.run,
which leaked every secret in the parent env (API keys, tokens) to the gh
child process. The fix narrows env to PATH/HOME/GH_*/XDG_* + GH_NO_UPDATE_NOTIFIER.
"""
from __future__ import annotations

import os
import subprocess
from unittest import mock

import pytest


_EXPECTED_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "GH_NO_UPDATE_NOTIFIER",
    # optional, present only if parent has them:
    "GH_TOKEN",
    "GH_CONFIG_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
})

_LEAKED_SECRET_NAMES = (
    "MINIMAX_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "EXA_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)


def test_builds_snapshot_passes_narrow_env(monkeypatch):
    """The subprocess.run call must use a narrow env, not os.environ spread."""
    from gateway.operator_shell import sdlc

    captured_kwargs = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        # Return a successful empty result so _builds_snapshot returns
        # '_No recent builds_' cleanly.
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "[]"
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = sdlc._builds_snapshot()
    assert "_No recent builds_" in out or out == "—"

    assert "env" in captured_kwargs, "subprocess.run was called without env= — env leak regression"
    env = captured_kwargs["env"]

    # The narrow env must only contain the documented keys (plus optional ones
    # when the parent has them).
    unexpected = set(env.keys()) - _EXPECTED_ENV_KEYS
    assert not unexpected, f"Unexpected env keys passed to gh: {unexpected}"


def test_builds_snapshot_does_not_leak_secrets(monkeypatch):
    """Regression for the F-NEW-10 env leak: parent secrets must not reach gh."""
    from gateway.operator_shell import sdlc

    # Plant fake secrets in the parent env. If _builds_snapshot spreads
    # os.environ into the subprocess env, these will appear in captured env.
    for var in _LEAKED_SECRET_NAMES:
        monkeypatch.setenv(var, f"LEAKED_{var}")

    captured_env = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "[]"
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    sdlc._builds_snapshot()

    leaked = [v for v in _LEAKED_SECRET_NAMES if v in captured_env]
    assert not leaked, f"Secrets leaked to gh subprocess: {leaked}"


def test_builds_snapshot_includes_gh_token_if_parent_has_it(monkeypatch):
    """GH_TOKEN is the only credential that should pass through."""
    from gateway.operator_shell import sdlc

    monkeypatch.setenv("GH_TOKEN", "ghp_FAKE_TOKEN_FOR_TEST")

    captured_env = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "[]"
        result.stderr = ""
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    sdlc._builds_snapshot()

    assert captured_env.get("GH_TOKEN") == "ghp_FAKE_TOKEN_FOR_TEST"
    # Sanity: the leaked-secret check still holds
    for var in _LEAKED_SECRET_NAMES:
        if var != "GH_TOKEN":
            assert var not in captured_env, f"{var} leaked despite not being a gh-relevant var"