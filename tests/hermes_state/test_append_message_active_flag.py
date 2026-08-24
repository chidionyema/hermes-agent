"""``append_message(active=False)`` writes a durable, non-replayed receipt.

Incident, 2026-08-24: the gateway consumed a clarify answer from the founder
in memory, handed it to a blocked agent thread and acknowledged with an empty
string.  The turn holding it never flushed, so 46 characters he typed existed
only in RAM and in one log line giving their length.  The fix writes the text
to the transcript as a soft-archived row, which needs ``append_message`` to be
able to write ``active = 0`` at all -- it hardcoded ``1`` before.

Paired per LAW 38: the archived row must stay out of the live conversation
(the refusal half), and the default write must still land in it (the pass
half).  A change that fenced everything would satisfy the first alone.
"""

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def test_active_false_row_is_on_disk_but_out_of_the_replayed_conversation(db):
    db.create_session("s1", source="gateway")

    live_id = db.append_message("s1", "user", content="ordinary message")
    receipt_id = db.append_message(
        "s1",
        "user",
        content="the answer he typed",
        display_kind="clarify_answer",
        display_metadata={"clarify_id": "818ca63ae1"},
        active=False,
    )
    assert receipt_id != live_id

    # The pass half: the default write is still replayed to the provider.
    live = db.get_messages("s1")
    assert [m["content"] for m in live] == ["ordinary message"]

    # The refusal half: the receipt is not, so it cannot land between an
    # assistant tool call and its result and break the message sequence.
    assert all(m["id"] != receipt_id for m in live)

    # But it is on disk, which is the whole point of writing it.
    row = db._conn.execute(
        "SELECT content, active, display_kind FROM messages WHERE id = ?",
        (receipt_id,),
    ).fetchone()
    assert row["content"] == "the answer he typed"
    assert row["active"] == 0
    assert row["display_kind"] == "clarify_answer"

    # And it is readable by anything that audits the transcript.
    audited = db.get_messages("s1", include_inactive=True)
    assert "the answer he typed" in [m["content"] for m in audited]
