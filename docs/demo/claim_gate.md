# claim_gate demo

Run from the hermes-agent checkout on 2026-08-26:

```
python3 -c 'from gateway.claim_gate import stamp_unproven_done as s; print(s("DONE: shipped the thing\nEvidence: none", session_id=None))'
```

Output (no session ledger, so the gate fails open and the text is unchanged):

```
DONE: shipped the thing
Evidence: none
```

With a session whose ledger holds edits and no green run after them, the same call returns:

```
UNVERIFIED: shipped the thing
Evidence: none
⚠️ UNVERIFIED: files were edited this session and no verification run has passed since the last edit (<detail>).
```

The gateway log carries one line per stamp: `claim_gate stamped DONE -> UNVERIFIED for session <id> (<files>)`.
