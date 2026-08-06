"""The slash registry must not resolve one word to two different commands.

2026-08-06: `portfolio` was declared as an alias of BOTH `fleet` and the new `projects`.
That is not an error anywhere — `_build_command_lookup` (hermes_cli/commands.py:300)
writes into a plain dict, so the later entry silently loses and /portfolio kept opening
Fleet's four hardcoded repos while the same word typed as plain text opened the
14-project registry. One word, two destinations, no failure anywhere to notice it.
"""

from __future__ import annotations

from collections import defaultdict

from hermes_cli.commands import COMMAND_REGISTRY, resolve_command


def test_no_alias_is_claimed_by_two_commands():
    owners: dict[str, list[str]] = defaultdict(list)
    for cmd in COMMAND_REGISTRY:
        for alias in cmd.aliases:
            owners[alias.lower()].append(cmd.name)
    clashes = {a: n for a, n in owners.items() if len(n) > 1}
    assert not clashes, f"aliases claimed by more than one command: {clashes}"


def test_no_alias_shadows_a_real_command_name():
    names = {cmd.name.lower() for cmd in COMMAND_REGISTRY}
    shadowed = {
        f"{cmd.name}:{alias}"
        for cmd in COMMAND_REGISTRY
        for alias in cmd.aliases
        if alias.lower() in names and alias.lower() != cmd.name.lower()
    }
    assert not shadowed, f"aliases shadowing another command's real name: {shadowed}"


def test_every_alias_resolves_back_to_its_own_command():
    """The guard that would have caught the /portfolio bug directly."""
    for cmd in COMMAND_REGISTRY:
        for alias in cmd.aliases:
            got = resolve_command(alias)
            assert got is not None, f"/{alias} resolves to nothing"
            assert got.name == cmd.name, (
                f"/{alias} is declared on /{cmd.name} but resolves to /{got.name}"
            )
