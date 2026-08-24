"""Incident 2026-08-24: the gateway swallowed a founder clarify answer.

While a clarify prompt is pending, the interceptor in ``gateway/run.py`` takes
any non-slash inbound text as the answer, hands it to the blocked agent thread
in memory and returns ``""`` so adapters do not double-post.  Nothing on that
path wrote the text to the message store, and ``tools/clarify_gateway`` is
in-memory only.  Measured that morning on the founder's own DM: 46 characters
typed at 02:03:02, one INFO line recording their length, the holding turn
never flushed, and the words were unrecoverable from every store on the
machine.  A message accepted and then lost with no receipt is the worst shape
a gateway failure takes, because the sender cannot tell it happened.

``GatewayRunner._record_intercepted_input`` is the fix.  These tests hold it
to the two things that matter: the receipt lands on disk, and a failure to
write one never costs the founder the answer itself.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from gateway.run import GatewayRunner
from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def _runner(db, session_id="sess-1", session_key="telegram:12345"):
    """A GatewayRunner with only the two attributes this method reads.

    Constructing a real runner starts adapters and schedulers.  The method
    under test reaches for ``self._session_db._db`` and
    ``self._peek_session_state``, and nothing else.
    """
    runner = object.__new__(GatewayRunner)
    runner.__dict__["_sessions"] = {
        session_key: SimpleNamespace(
            turn=SimpleNamespace(agent=SimpleNamespace(session_id=session_id))
        )
    }
    runner._session_db = SimpleNamespace(_db=db)
    return runner


def test_intercepted_clarify_answer_reaches_the_transcript(db):
    """The pass half: his words are on disk before the agent thread sees them."""
    db.create_session("sess-1", source="gateway")
    runner = _runner(db)

    runner._record_intercepted_input(
        "telegram:12345",
        "kubernetes on hetzner, not eks",
        "clarify_answer",
        "818ca63ae1",
    )

    rows = db.get_messages("sess-1", include_inactive=True)
    assert [r["content"] for r in rows] == ["kubernetes on hetzner, not eks"]
    assert rows[0]["role"] == "user"
    assert rows[0]["display_kind"] == "clarify_answer"

    # It is a receipt, not a turn: the clarify tool result already carries the
    # answer into the model's history, so replaying this row as well would put
    # a live user message between an assistant tool call and its result.
    assert db.get_messages("sess-1") == []


def test_rejected_selection_text_is_recorded_even_though_no_agent_sees_it(db):
    """Text the clarify refuses is dropped entirely, so the receipt is the
    only evidence he typed anything at all."""
    db.create_session("sess-1", source="gateway")
    runner = _runner(db)

    runner._record_intercepted_input(
        "telegram:12345", "none of those", "clarify_rejected_selection", "c-9"
    )

    rows = db.get_messages("sess-1", include_inactive=True)
    assert [r["content"] for r in rows] == ["none of those"]
    assert rows[0]["display_kind"] == "clarify_rejected_selection"


def test_a_failed_receipt_never_raises_at_the_caller(db):
    """The refusal half: recording must not be able to cost him the answer.

    The caller is on its way to hand the text to a blocked agent thread.  If
    this method raised, a broken transcript write would turn a recoverable
    message into a hung turn -- a worse failure than the one it fixes.
    """
    class _Exploding:
        def append_message(self, *a, **kw):
            raise sqlite3.OperationalError("database is locked")

    runner = _runner(_Exploding())
    runner._record_intercepted_input("telegram:12345", "text", "clarify_answer")

    # No live session id is the other way it can fail, and it is also silent.
    orphan = object.__new__(GatewayRunner)
    orphan.__dict__["_sessions"] = {}
    orphan._session_db = SimpleNamespace(_db=db)
    orphan._record_intercepted_input("telegram:12345", "text", "clarify_answer")


def test_empty_text_writes_nothing(db):
    db.create_session("sess-1", source="gateway")
    runner = _runner(db)
    runner._record_intercepted_input("telegram:12345", "", "clarify_answer")
    assert db.get_messages("sess-1", include_inactive=True) == []
