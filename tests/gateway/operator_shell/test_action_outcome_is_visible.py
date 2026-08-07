"""An action's own account of what it did must reach the operator.

``PanelView.toast`` is assigned at 86 sites and read at exactly one --
``activity.py:145``, which appends it to a JSONL file on disk. ``PanelView.ok``
is read only there too (``activity.py:148``, to stamp ``status="failed"``).
Neither ever reached a screen. So ``estate.py:1262`` --

    view.toast = "♻️ Restarted" if ok else "⚠️ Failed"
    view.ok = ok

-- computed the difference between a working restart and a broken one, logged
it where nobody was looking, and showed the operator the same card either way.

The toast BUBBLE cannot carry this. ``answerCallbackQuery`` accepts one call
per query and must fire immediately (the query id expires in ~15s, while a
handler that probes Stripe or GitHub runs 60s+), so it is answered long before
``toast`` exists. The card is the only channel left.
"""

from __future__ import annotations

import pytest

from gateway.operator_shell.estate import PanelView
from gateway.operator_shell.mdv2 import parse, render_panel
from gateway.platforms.telegram import _action_ack_label, _outcome_line


# ------------------------------------------------------- the immediate ack


def test_ack_names_the_action_instead_of_an_ellipsis():
    assert _action_ack_label("restart_gateway") == "⏳ Restart gateway"
    assert _action_ack_label("fleet") == "⏳ Fleet"


def test_ack_distinguishes_taps_that_used_to_look_identical():
    """All 108 buttons answered with a bare "…"; a slow handler was then
    indistinguishable from having tapped the wrong thing."""
    labels = {_action_ack_label(a) for a in ("fleet", "status", "sdlc", "builds")}
    assert len(labels) == 4


def test_ack_uses_the_verb_not_the_argument():
    """estate:prospector:3 and estate:prospector:9 are the same action."""
    assert _action_ack_label("prospector:3") == _action_ack_label("prospector:9")


def test_ack_never_returns_empty():
    """query.answer(text="") is not an acknowledgement."""
    for junk in ("", ":", None):
        assert _action_ack_label(junk).strip()


# ------------------------------------------------------- the outcome line


def test_outcome_surfaces_a_toast_the_card_does_not_already_state():
    v = PanelView(text="*Gateway*\nrunning, 4m", toast="♻️ Restarted")
    assert _outcome_line(v) == "♻️ Restarted"


def test_navigation_labels_are_suppressed_as_duplicates():
    """Most toasts name the panel you just opened. Repeating that would train
    the operator to ignore the line that matters."""
    v = PanelView(text="*Fleet* — 3 up\n_4m ago_", toast="Fleet")
    assert _outcome_line(v) == "", "duplicate of the card's own title"


def test_suppression_compares_rendered_text_not_markup():
    """'*Fleet*' must still count as the card saying 'Fleet'."""
    assert _outcome_line(PanelView(text="*Activity*", toast="Activity")) == ""


def test_a_failure_is_never_silent_even_with_no_toast():
    assert _outcome_line(PanelView(text="*Store*", toast="", ok=False)) == "⚠️ Action failed"


def test_success_with_no_toast_adds_nothing():
    assert _outcome_line(PanelView(text="*Store*", toast="")) == ""


def test_restart_success_and_failure_no_longer_render_identically():
    """The exact regression: estate.py:1262's two branches must differ."""
    body = "*Gateway*\nlaunchd: com.hermes.gateway"
    ok_view = PanelView(text=body, toast="♻️ Restarted", ok=True)
    bad_view = PanelView(text=body, toast="⚠️ Failed", ok=False)

    assert _outcome_line(ok_view) != _outcome_line(bad_view)
    assert _outcome_line(bad_view) == "⚠️ Failed"


# ------------------------------------------------- it must survive the send


@pytest.mark.parametrize(
    "toast",
    [
        "⚠️ Failed",
        "Prospector ×3",
        "Cron → this chat",
        "OK $0.0123",  # '.' is a MarkdownV2 special
        "Stopped 3 (2 left)",  # parens are specials
        "keep_awake failed",  # snake_case must not italicise
    ],
)
def test_outcome_line_survives_the_markdownv2_send_path(toast):
    """An outcome that draws a 400 is worse than no outcome at all."""
    v = PanelView(text="*Panel*\nbody", toast=toast)
    line = _outcome_line(v)
    assert line, "precondition: this toast should be surfaced"

    card = v.text + "\n\n↳ " + line
    plain, _ents = parse(render_panel(card))  # raises ParseError if invalid
    assert toast in plain, "the outcome must be readable in the delivered text"
