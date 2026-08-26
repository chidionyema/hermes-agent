"""HERMES_LANGFUSE_TAGS: trace tags keep the two fixed tags first, carry every non-blank entry
once with no surrounding whitespace, and an unset variable adds nothing. Exhaustive over every
ordering of a small alphabet so no example is hand-picked (crew#286 CP6)."""
import importlib.util
import itertools
import pathlib
import sys

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parents[3] / "plugins" / "observability" / "langfuse" / "__init__.py"


@pytest.fixture(scope="module")
def plugin():
    spec = importlib.util.spec_from_file_location("langfuse_plugin_under_test", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolves the module by name at class creation
    spec.loader.exec_module(mod)
    return mod


PARTS = ["crew#286", " crew#286 ", "", "  ", "hermes", "lane:idp"]


@pytest.mark.parametrize("parts", [p for n in range(0, 4) for p in itertools.permutations(PARTS, n)])
def test_tags_property(monkeypatch, plugin, parts):
    monkeypatch.setenv("HERMES_LANGFUSE_TAGS", ",".join(parts))
    tags = plugin._tags()
    assert tags[:2] == ["hermes", "langfuse"]
    assert len(tags) == len(set(tags))
    assert all(t and t == t.strip() for t in tags)
    assert all(p.strip() in tags for p in parts if p.strip())


def test_tags_unset_adds_nothing(monkeypatch, plugin):
    monkeypatch.delenv("HERMES_LANGFUSE_TAGS", raising=False)
    assert plugin._tags() == ["hermes", "langfuse"]
    monkeypatch.setenv("HERMES_LANGFUSE_TAGS", "crew#286, crew#286 ,")
    assert plugin._tags() == ["hermes", "langfuse", "crew#286"]
