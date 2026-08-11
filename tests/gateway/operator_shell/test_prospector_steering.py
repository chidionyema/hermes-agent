"""Steering the engine from the phone: focus, market, and the UK⇄US rotation.

WHAT WAS MISSING

Every phone-editable knob was throughput or a rail — interval, concurrency, batch_size,
daily_cap, backlog_cap, grounding_gate. All six answer "how hard is it running"; none answers
"at what". The steering machinery existed in the engine and was switched OFF: `active_profile`
was absent from config.yaml entirely, so `load_config` had nothing to apply and every unattended
batch generated blue-sky across unrelated sectors.

WHAT IS PINNED, AND WHY EACH ONE

1. The value regex handles a QUOTED scalar. `schedule.market_rotation: "uk,us"` lives inside a
   flow mapping, where a bare comma terminates a value — the old pattern read it back as `"uk`.
   Same defect class as the URL extractor that truncated at `)`.
2. What the phone WRITES, a YAML parser READS as the same value. The engine does not use these
   regexes; it uses `yaml.safe_load`. A setter that satisfies only its own reader is the bug
   that made the phone display a batch_size the daemon had never been given.
3. `""` is a real value, not a failed read. No focus / markets.default / rotation off are all
   legitimate states, and printing `?` for them sends the operator looking for a fault.
4. Comments on the line survive the write. On the `schedule:` flow mapping the prose beside a
   knob is how the next reader learns why it is set that way.
5. The allowlist is enforced. `focus` is injected verbatim into the generation prompt: a typo
   does not fail, it generates a whole batch against nonsense and bills for it.
6. Every button on the two new panels dispatches to a key the setter actually accepts.
"""
from __future__ import annotations

import textwrap

import pytest
import yaml

from gateway.operator_shell import cockpit, prospector_daemon as pd

# Mirrors the real config.yaml's shape, including the two traps: the `schedule:` FLOW MAPPING
# and prose that quotes its own knobs in assignment form (`_yaml_assign_lines` must ignore the
# comment, or the setter rewrites the documentation instead of the setting).
_CONFIG = """\
# `batch_size: 15` mints up to 15 rows per tick — prose, not an assignment.
active_market: ""                  # "" => markets.default
active_profile: ""
profiles:
  tech_ai_all:
    generation:
      focus: |
        AI-native products, tech-vertical businesses and businesses selling to tech.
  no_focus_profile:
    generation:
      structural_forms: [pack]
schedule: { cadence: daily, batch_size: 15, backlog_cap: 0,
            gate_generation_on_grounding: true, market_rotation: "" }
"""


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """A throwaway config.yaml. Never the live one — this module WRITES."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(_CONFIG), encoding="utf-8")
    monkeypatch.setattr(pd, "_CONFIG", p)
    return p


# ---------------------------------------------------------------------------
# 1 + 2. Quoted values round-trip, and a real YAML parser agrees
# ---------------------------------------------------------------------------

def test_a_quoted_comma_value_is_one_token(cfg):
    ok, detail, restart = pd.set_param("rotate", "uk_us")
    assert ok, detail
    assert restart is False, "a config.yaml knob is picked up at the next tick, not by restart"
    text = cfg.read_text(encoding="utf-8")
    assert pd._read_yaml_scalar(text, "market_rotation") == '"uk,us"'
    assert pd._unquote(pd._read_yaml_scalar(text, "market_rotation")) == "uk,us"


def test_what_the_phone_writes_is_what_the_engine_parses(cfg):
    """The setter's own reader agreeing with itself proves nothing — this is the real reader."""
    pd.set_param("rotate", "uk_us")
    pd.set_param("focus", "tech_ai_all")
    pd.set_param("market", "us-ca")
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["schedule"]["market_rotation"] == "uk,us"
    assert [c.strip() for c in raw["schedule"]["market_rotation"].split(",")] == ["uk", "us"]
    assert raw["active_profile"] == "tech_ai_all"
    assert raw["active_market"] == "us-ca"
    # The rest of the flow mapping is intact — a patch that ate a sibling key would still
    # satisfy every assertion above.
    assert raw["schedule"]["batch_size"] == 15
    assert raw["schedule"]["gate_generation_on_grounding"] is True


def test_turning_steering_off_writes_an_empty_string_not_a_hole(cfg):
    """`_patch_yaml_scalar` refuses on 0 assignments, so a value must never become absent."""
    pd.set_param("focus", "tech_ai_all")
    ok, detail, _ = pd.set_param("focus", "off")
    assert ok, detail
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert raw["active_profile"] == ""
    # Still settable afterwards: the line kept a token to rewrite.
    assert pd.set_param("focus", "ai_native")[0]


# ---------------------------------------------------------------------------
# 3. "" is a value, not a failed read
# ---------------------------------------------------------------------------

def test_empty_reads_as_a_state_not_a_question_mark(cfg):
    p = pd.read_params()
    assert p["focus"] == "" and p["market"] == "" and p["rotation"] == ""
    assert cockpit._current("active_profile — tech + AI", {}, p) == "off (blue-sky)"
    assert cockpit._current("active_market", {}, p) == "default"
    assert cockpit._current("schedule.market_rotation", {}, p) == "off"


def test_unreadable_config_still_says_it_could_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "_CONFIG", tmp_path / "does-not-exist.yaml")
    p = pd.read_params()
    assert p["focus"] is None and p["market"] is None
    assert cockpit._current("active_profile — tech + AI", {}, p) == "?"


# ---------------------------------------------------------------------------
# The resolved focus text — the difference between "steering is set" and "it steers"
# ---------------------------------------------------------------------------

def test_the_card_shows_the_words_the_engine_was_actually_given(cfg):
    pd.set_param("focus", "tech_ai_all")
    body = "\n".join(pd._params_lines())
    assert "tech_ai_all" in body
    assert "AI-native products" in body, "the profile name alone cannot show what it constrains"


def test_a_profile_that_steers_nothing_says_so(cfg):
    """A profile with no `generation.focus` restricts forms but binds no subject."""
    pd._patch_yaml_scalar  # sanity: the writer exists
    text = cfg.read_text(encoding="utf-8").replace(
        'active_profile: ""', 'active_profile: "no_focus_profile"'
    )
    cfg.write_text(text, encoding="utf-8")
    body = "\n".join(pd._params_lines())
    assert "steers nothing" in body


def test_rotation_is_shown_to_win_over_market(cfg):
    pd.set_param("market", "uk")
    pd.set_param("rotate", "uk_us")
    body = "\n".join(pd._params_lines())
    assert "uk,us" in body and "wins over market" in body


# ---------------------------------------------------------------------------
# 4. Comments survive
# ---------------------------------------------------------------------------

def test_the_trailing_comment_survives_the_write(cfg):
    pd.set_param("market", "us")
    line = [
        ln for ln in cfg.read_text(encoding="utf-8").splitlines()
        if ln.startswith("active_market:")
    ][0]
    assert '"" => markets.default' in line, "the prose beside the knob was eaten"


def test_prose_quoting_a_knob_is_not_mistaken_for_the_knob(cfg):
    """Line 1 of the fixture says `batch_size: 15` inside a comment."""
    pd.set_param("batch_size", "5")
    lines = cfg.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#") and "batch_size: 15" in lines[0]
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["schedule"]["batch_size"] == 5


# ---------------------------------------------------------------------------
# 5 + 6. The allowlist, and every button lands on a key the setter accepts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,bad", [
    ("focus", "tech-ai-all"),      # a plausible typo of a real profile
    ("focus", "'; rm -rf /"),
    ("market", "atlantis"),
    ("rotate", "uk,us"),           # the WRITTEN form is not the allowlisted token
])
def test_values_outside_the_allowlist_are_refused_and_write_nothing(cfg, key, bad):
    before = cfg.read_text(encoding="utf-8")
    ok, detail, _ = pd.set_param(key, bad)
    assert not ok and "not allowed" in detail
    assert cfg.read_text(encoding="utf-8") == before


def test_every_steering_button_uses_an_accepted_key_and_value():
    """The panels and the setter cannot drift: read the buttons, check the allowlist."""
    seen = set()
    for group in ("focus", "market"):
        _title, _blurb, knobs = cockpit._KNOBS[group]
        for _label, buttons in knobs:
            for text, cb in buttons:
                assert cb.startswith("estate:pd_set:"), (text, cb)
                key, value = cb[len("estate:pd_set:"):].split(":", 1)
                assert key in pd._SAFE_PARAMS, f"{text!r} sets unknown key {key!r}"
                allowed = pd._SAFE_PARAMS[key][1]
                assert value in allowed, f"{text!r} sends {value!r}, allowed {allowed}"
                seen.add((key, value))
    # Every allowlisted steering value has a button. A value with no button is a control the
    # operator cannot reach — the exact gap that left `per_instrument` unreachable.
    for key in ("focus", "market", "rotate"):
        for value in pd._SAFE_PARAMS[key][1]:
            assert (key, value) in seen, f"{key}={value} is allowlisted but has no button"


def test_a_set_lands_back_in_its_own_group():
    """`_knob_landing` resolves the group from the callback; a miss dumps the operator home."""
    assert cockpit.group_for_key("focus") == "focus"
    assert cockpit.group_for_key("market") == "market"
    assert cockpit.group_for_key("rotate") == "market"


def test_the_tune_index_offers_every_group():
    """The index was hand-unrolled as _TUNE_GROUPS[0]..[5]; a 7th group had no button."""
    _text, rows = cockpit.render_tune()
    offered = {cb for row in rows for _label, cb in row}
    for _label, action, _what in cockpit._TUNE_GROUPS:
        assert f"estate:{action}" in offered, f"{action} has a description but no button"
