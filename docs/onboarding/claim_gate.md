# claim_gate

**What it is for.** A reply whose status line opens `DONE:` claims the work is finished. If the session's verification ledger (`$HERMES_HOME/verification_evidence.db`, written by `agent/verification_evidence.py`) shows files edited after the last green verification run, the word is rewritten to `UNVERIFIED:` and one footer line says why. The reply is never blocked or shortened; the reader gets the agent's words plus an honest label.

**Where it lives.** `gateway/claim_gate.py`, called from the reply path in `gateway/run.py`. Entry point: `stamp_unproven_done(text, session_id=...)`.

**What it costs.** One SQLite read per `DONE:` reply. Nothing on any other reply.

**How to stop it.** `HERMES_CLAIM_GATE_DISABLED=1` in the gateway environment. Every stamp and every bypass is logged (`claim_gate stamped DONE -> UNVERIFIED for session ...`), so both rates are greppable from the gateway log.

**Fails open.** No ledger, no session id, or any error inside the gate returns the text unchanged. Sessions whose only edits are prose are never stamped.
