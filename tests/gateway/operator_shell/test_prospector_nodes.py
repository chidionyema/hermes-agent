"""🧠 Nodes — which brain does which step, and which of them are benched.

WHAT WAS MISSING

Three chains do the engine's thinking and they are not interchangeable: the VERDICT chain rules
(and only `claude_cli`/`claude` rule finally), the NON-CRITICAL chain generates, prescreens and
scores (and can never rule, which is why its head may be the cheapest live brain), and the
ARTIFACT chain writes the prose the buyer pays for. The phone could see none of them.

A dead head is the failure mode that hides itself. The chain fails over, the run succeeds,
nothing looks wrong — and every call pays a guaranteed failure, and until the breaker trips a
full timeout, before it starts. That went unnoticed for weeks in the engine.

The non-critical order was a module constant in the engine's `run.py` until 2026-08-10, so the
one knob whose entire subject is what the ancillary work COSTS was the only one that needed a
source edit and a daemon re-exec to move, while every throughput knob beside it was a config
line. Its head changed three times in two weeks, each time by editing code to state a billing
fact.

WHAT IS PINNED, AND WHY EACH ONE

1. The fence is in the WRITER, not in the button table. A panel that only offers safe presets
   is a selection-time fence, and a selection-time fence misses a runtime substitution — a
   refusal routed around by a caller the button list never saw.
2. A preset only REORDERS. Dropping a tier shortens the chain silently, and a chain with one
   tier has no failover — the failure the tiering exists to prevent.
3. The write touches one key. `operator:`, `noncritical_operator:` and `artifact_operator:` are
   three list keys whose names are prefixes of each other's substrings.
4. A benched head is called out in words, not just a colour. It is the specific thing an
   operator cannot infer from a healthy-looking run.
5. A hand-edited order is not relabelled as a preset.
6. What the phone writes, a real YAML parser reads back as the same list.
"""
from __future__ import annotations

import json
import textwrap
import time

import pytest
import yaml

from gateway.operator_shell import prospector_daemon as pd

_CONFIG = """\
# prose that mentions `operator: [x]` in assignment form — the scanner must ignore comments
operator: [claude_cli, standardcompute, minimax]   # verdict chain — trusted head required
noncritical_operator: [standardcompute, claude_cli, minimax]
artifact_operator: [claude_cli, minimax]
active_market: ""
active_profile: ""
schedule: { batch_size: 15, market_rotation: "" }
"""


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(_CONFIG), encoding="utf-8")
    monkeypatch.setattr(pd, "_CONFIG", p)
    monkeypatch.setattr(pd, "REPO", tmp_path)  # health files live under REPO/store
    return p


def _write_health(tmp_path, filename, **dead):
    d = tmp_path / "store"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(
        json.dumps({"providers": {k: {"dead_until": v} for k, v in dead.items()}}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Reading the three chains
# ---------------------------------------------------------------------------

def test_all_three_chains_are_read(cfg):
    p = pd.read_params()
    assert p["verdict_chain"] == ["claude_cli", "standardcompute", "minimax"]
    assert p["noncritical_chain"] == ["standardcompute", "claude_cli", "minimax"]
    assert p["artifact_chain"] == ["claude_cli", "minimax"]


def test_prose_quoting_a_chain_is_not_read_as_the_chain(cfg):
    """Line 1 of the fixture says `operator: [x]` inside a comment."""
    assert pd._read_yaml_list(cfg.read_text(encoding="utf-8"), "operator")[0] == "claude_cli"


def test_an_unreadable_config_says_so_rather_than_inventing_a_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "_CONFIG", tmp_path / "nope.yaml")
    p = pd.read_params()
    assert p["verdict_chain"] is None and p["noncritical_chain"] is None
    text, _ = pd.render_nodes()
    assert "could not read the chain" in text


# ---------------------------------------------------------------------------
# 1 + 2 + 3. The fence, the preserved set, the single key
# ---------------------------------------------------------------------------

def test_a_preset_reorders_and_never_drops_a_tier():
    for name, order in pd._NODE_ORDERS.items():
        assert sorted(order) == sorted(pd._NODE_ORDERS["cheapest"]), f"{name} changed the set"
        assert len(order) == len(set(order)), f"{name} repeats a tier"


def test_setting_nodes_moves_only_the_generation_chain(cfg):
    ok, detail, restart = pd.set_param("nodes", "quality")
    assert ok, detail
    assert restart is False, "a config.yaml key is picked up at the next tick"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["noncritical_operator"] == ["claude_cli", "standardcompute", "minimax"]
    # The other two list keys are untouched — their names are substrings of each other's.
    assert raw["operator"] == ["claude_cli", "standardcompute", "minimax"]
    assert raw["artifact_operator"] == ["claude_cli", "minimax"]
    assert raw["schedule"]["batch_size"] == 15


def test_the_trailing_comment_on_a_chain_line_survives(cfg):
    pd.set_param("nodes", "thrift")
    text = cfg.read_text(encoding="utf-8")
    assert "# verdict chain — trusted head required" in text


@pytest.mark.parametrize("head", ["minimax", "standardcompute", "deepseek", ""])
def test_the_writer_refuses_an_untrusted_head_on_the_verdict_chain(cfg, head):
    """Not via the buttons — directly at the writer, which is where the fence has to hold."""
    values = [head, "claude_cli"] if head else []
    assert pd._fence_chain("operator", values) is not None
    text = cfg.read_text(encoding="utf-8")
    assert pd._patch_yaml_list(text, "operator", values) is None
    assert cfg.read_text(encoding="utf-8") == text


def test_a_trusted_head_on_the_verdict_chain_is_allowed_by_the_fence(cfg):
    """The fence must permit the legitimate case, or it is just a disabled feature."""
    assert pd._fence_chain("operator", ["claude_cli", "minimax"]) is None


def test_an_empty_chain_is_refused_on_any_key():
    assert pd._fence_chain("noncritical_operator", []) is not None


def test_a_value_outside_the_allowlist_writes_nothing(cfg):
    before = cfg.read_text(encoding="utf-8")
    ok, detail, _ = pd.set_param("nodes", "claude_cli")
    assert not ok and "not allowed" in detail
    assert cfg.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# 4 + 5. What the panel says
# ---------------------------------------------------------------------------

def test_a_benched_head_is_called_out_in_words(cfg, tmp_path):
    _write_health(tmp_path, "provider_health_noncritical.json",
                  standardcompute=time.time() + 3600)
    text, _ = pd.render_nodes()
    assert "🔴 standardcompute" in text
    assert "the head is benched" in text


def test_an_expired_dead_mark_is_not_a_dead_brain(cfg, tmp_path):
    _write_health(tmp_path, "provider_health_noncritical.json",
                  standardcompute=time.time() - 3600)
    text, _ = pd.render_nodes()
    assert "🔴" not in text and "benched" not in text


def test_a_dead_tail_is_marked_but_not_escalated(cfg, tmp_path):
    _write_health(tmp_path, "provider_health_noncritical.json", minimax=time.time() + 3600)
    text, _ = pd.render_nodes()
    assert "🔴 minimax" in text
    assert "the head is benched" not in text, "a dead failover tier is not a dead head"


def test_the_live_preset_is_named_and_ticked(cfg):
    text, buttons = pd.render_nodes()
    assert "Generation is on *cheapest*" in text
    ticked = [label for row in buttons for label, _cb in row if label.startswith("✅")]
    assert len(ticked) == 1 and "Cheapest first" in ticked[0]


def test_a_hand_edited_order_is_not_relabelled_as_a_preset(cfg):
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "noncritical_operator: [standardcompute, claude_cli, minimax]",
            "noncritical_operator: [claude_cli, minimax]",
        ),
        encoding="utf-8",
    )
    text, buttons = pd.render_nodes()
    assert "custom order" in text
    assert not [label for row in buttons for label, _ in row if label.startswith("✅")]


def test_the_read_only_chains_offer_no_setter(cfg):
    _text, buttons = pd.render_nodes()
    setters = [cb for row in buttons for _l, cb in row if ":pd_set:" in cb]
    assert setters and all(cb.startswith("estate:pd_set:nodes:") for cb in setters), (
        "the verdict and artifact chains must not be settable from this panel"
    )


def test_every_button_sends_an_allowlisted_value(cfg):
    _text, buttons = pd.render_nodes()
    allowed = pd._SAFE_PARAMS["nodes"][1]
    seen = set()
    for row in buttons:
        for _label, cb in row:
            if cb.startswith("estate:pd_set:nodes:"):
                value = cb.rsplit(":", 1)[1]
                assert value in allowed, f"{cb} sends {value!r}"
                seen.add(value)
    assert seen == set(allowed), "an allowlisted preset with no button is unreachable"


# ---------------------------------------------------------------------------
# 6. Round-trip, and the confirm screen tells the truth
# ---------------------------------------------------------------------------

def test_what_the_phone_writes_the_engine_parses(cfg):
    pd.set_param("nodes", "thrift")
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["noncritical_operator"] == pd._NODE_ORDERS["thrift"]
    assert pd.read_params()["noncritical_chain"] == pd._NODE_ORDERS["thrift"]


def test_the_confirm_screen_shows_the_chain_being_replaced(cfg):
    text, buttons = pd.confirm_set_param("nodes", "quality")
    assert "standardcompute → claude_cli → minimax" in text, "the CURRENT order must be shown"
    assert "claude_cli, standardcompute, minimax" in text, "and the one being written"
    assert "verdict chain is untouched" in text
    assert any(cb == "estate:pd_set_confirm:nodes:quality" for row in buttons for _l, cb in row)
