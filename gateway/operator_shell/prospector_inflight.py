"""What the engine is doing RIGHT NOW — the sub-tick view (R5).

WHAT WAS ACTUALLY MISSING
-------------------------
`docs/TELEGRAM_OPERATOR_PROGRAM.md` R5 said the engine emits no per-candidate / per-check state.
Half of that was already false: `store/scheduler/audit/<day>.jsonl` has carried `verify_search`
rows with `candidate_id` + `check` all along (989 of them on 2026-08-10). What was missing was
the RULING (a check going looking was logged; a check deciding was not), the BOUNDARIES (no
start/done, so "working" and "abandoned" looked identical), and — the part this file fixes —
ANY READER AT ALL. The engine side landed in prospector `verify.py` / `run.py`.

WHY THIS TAILS BYTES AND NOT LINES
----------------------------------
The day-files run 0.5–2.5 MB (2026-08-06 is 2,488,875 bytes). A panel that must answer in
seconds cannot slurp that on every render, and `readlines()[-N:]` reads all of it anyway. So we
seek to `size - _TAIL_BYTES` and discard the first fragment, which is a partial line by
construction. That discard is not a heuristic: it is the same rule `prospector/jsonl_atomic.py`
applies at the other end of the file — a record is committed only when its terminating newline
is on disk.

WHY (run_id, pid, candidate_id) AND NOT candidate_id
----------------------------------------------------
A day-file is an interleaving of the daemon, backfills and manual CLI runs (audit.py:142). The
same candidate_id legitimately appears in two runs — a defer today and its `vet --resume`
tomorrow. Keying on candidate_id alone lets one run's `candidate_done` close another run's open
candidate, which renders as an engine that finished work it never started.

WHY A CLOCK READING IS CHECKED BEFORE IT IS BELIEVED
----------------------------------------------------
`store/scheduler/audit/1970-01-01.jsonl` in this very estate holds 13 rows of REAL work stamped
1970-01-01 — a daemon that ran ~60 hours believing it was 1970. Subtracting that from `now`
yields a 56-year-old candidate "in flight". An impossible age is reported as unknown, never as
a number: the same rule the Deployed panel already applies to `ps -o lstart` (deployed.py).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from gateway.operator_shell.panel_chrome import Group, compose, panel_stamp

ButtonRow = List[Tuple[str, str]]

REPO = Path.home() / "Documents" / "code" / "prospector"
STORE = REPO / "store" / "scheduler"
AUDIT_DIR = STORE / "audit"

#: How much of the tail to read. 512 KiB is ~1,500 rows at the measured ~350 bytes/row — far
#: more than one tick produces, and a bounded cost on a file that only grows.
_TAIL_BYTES = 512 * 1024
#: Hard cap on rows folded, so a pathologically small-row day cannot blow the render budget.
_TAIL_ROWS = 4000

#: A candidate whose newest row is older than this is not "in flight" any more, whatever the
#: absence of a `candidate_done` implies. Ticks are 2h apart and a vet is minutes, so 15
#: minutes of silence on an OPEN candidate means the work stopped, not that it is slow.
_STALE_S = 900

#: Beyond this, a timestamp is not a slow candidate — it is a broken clock (see module docstring).
_ABSURD_AGE_S = 365 * 24 * 3600

_CHECK_EVENTS = ("check_result", "verify_search")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def day_files() -> List[Path]:
    """Every audit day-file, oldest first. Names are YYYY-MM-DD, so name order is time order."""
    try:
        return sorted(p for p in AUDIT_DIR.glob("*.jsonl") if p.is_file())
    except OSError as exc:
        logger.warning("audit dir unreadable: %s", exc)
        return []


def latest_day_file() -> Optional[Path]:
    """The newest day-file, or None.

    Deliberately the newest FILE rather than today's date: on an idle day today's file does not
    exist, and the honest answer is "nothing since <that day>", not "no data".
    """
    files = day_files()
    return files[-1] if files else None


def tail_rows(path: Path, *, max_bytes: int = _TAIL_BYTES, max_rows: int = _TAIL_ROWS) -> List[dict]:
    """The last rows of a JSONL day-file, cheaply. Never raises; unreadable → []."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # discard the fragment before the first full record
            blob = fh.read()
    except OSError as exc:
        logger.warning("audit tail unreadable (%s): %s", path, exc)
        return []

    rows: List[dict] = []
    for raw in blob.decode("utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue  # a torn or corrupt line is skipped, exactly as iter_jsonl does
        if isinstance(rec, dict):
            rows.append(rec)
    return rows[-max_rows:]


def _parse_ts(row: dict) -> Optional[datetime]:
    raw = row.get("ts")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_s(row: dict, now: datetime) -> Optional[float]:
    """Seconds since `row` was written, or None when the clock reading cannot be believed."""
    dt = _parse_ts(row)
    if dt is None:
        return None
    age = (now - dt).total_seconds()
    if age < -60 or age > _ABSURD_AGE_S:
        return None
    return max(0.0, age)


def _pid_alive(pid) -> Optional[bool]:
    """True/False, or None when it cannot be determined (no pid, or not our process to signal)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return None


def _key(row: dict) -> Tuple[str, str, str]:
    return (str(row.get("run_id") or ""), str(row.get("pid") or ""),
            str(row.get("candidate_id") or ""))


# ---------------------------------------------------------------------------
# Folding
# ---------------------------------------------------------------------------

def fold(rows: List[dict], *, now: Optional[datetime] = None) -> dict:
    """Turn a window of audit rows into what the engine is doing.

    Returns `in_flight` (open, recent), `stalled` (open, silent past `_STALE_S` or dead pid),
    `done` (most recent closures), retrieval health over the window, and the age of the newest
    row of any kind — which is the only honest answer to "is this trail even live?".
    """
    now = now or datetime.now(timezone.utc)

    open_rows: Dict[Tuple[str, str, str], dict] = {}
    last_check: Dict[Tuple[str, str, str], dict] = {}
    done: List[dict] = []
    ok = err = 0
    newest: Optional[dict] = None

    for row in rows:
        ev = row.get("event")

        if ev == "candidate_start":
            open_rows[_key(row)] = row
        elif ev == "candidate_done":
            k = _key(row)
            open_rows.pop(k, None)
            last_check.pop(k, None)
            done.append(row)
        elif ev in _CHECK_EVENTS:
            last_check[_key(row)] = row
        elif ev in ("search", "verify_search"):
            pass

        if ev in ("search", "verify_search"):
            if row.get("status") == "ok":
                ok += 1
            elif row.get("status"):
                err += 1

    in_flight: List[dict] = []
    stalled: List[dict] = []
    for k, start in open_rows.items():
        chk = last_check.get(k)
        newest_for_cand = chk if chk is not None else start
        age = _age_s(newest_for_cand, now)
        alive = _pid_alive(start.get("pid"))
        entry = {
            "candidate_id": k[2],
            "title": str(start.get("title") or "")[:80],
            "tier": str(start.get("tier") or ""),
            "full_vet": bool(start.get("full_vet")),
            "check": str((chk or {}).get("check") or ""),
            "verdict": str((chk or {}).get("verdict") or ""),
            "idx": (chk or {}).get("idx"),
            "total": (chk or {}).get("total"),
            "age_s": age,
            "pid": start.get("pid"),
            "pid_alive": alive,
            "run_id": k[0],
        }
        # A dead pid is decisive on its own: that process can never emit its closing row, so
        # waiting out `_STALE_S` before saying so would just delay a fact already known.
        if alive is False or (age is not None and age > _STALE_S):
            stalled.append(entry)
        else:
            in_flight.append(entry)

    in_flight.sort(key=lambda e: (e["age_s"] if e["age_s"] is not None else 1e9))
    stalled.sort(key=lambda e: (e["age_s"] if e["age_s"] is not None else 1e9))

    # The newest row is the LAST one appended, not the one with the highest `seq`. `seq` is a
    # per-PROCESS counter (audit.py:153 — `itertools.count(1)` at module scope), so comparing
    # it across the daemon, a backfill and a manual CLI run in one day-file ranks rows by which
    # process had been running longest. File order is append order, which is what "newest"
    # means here — and it is the only ordering that survives the estate's bad clock as well.
    newest = rows[-1] if rows else None

    return {
        "in_flight": in_flight,
        "stalled": stalled,
        "done": done[-5:],
        "retrieval_ok": ok,
        "retrieval_err": err,
        "trail_age_s": _age_s(newest, now) if newest else None,
        "rows": len(rows),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _age_str(age: Optional[float]) -> str:
    if age is None:
        return "age unknown"
    if age < 90:
        return f"{int(age)}s ago"
    if age < 5400:
        return f"{int(age // 60)}m ago"
    if age < 172800:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def _code(s: str) -> str:
    """Content for a backtick span, with any backtick removed so the span cannot be broken.

    Candidate titles are model-generated free text and check names carry underscores. A bare
    `_` outside a code span opens a Telegram italic that never closes, and the API answers
    HTTP 400 — the panel does not render at all, which is indistinguishable on a phone from
    the engine being down. Caught by `test_mdv2_panel_rendering.py::
    test_every_panel_survives_the_send_path` before this ever reached the founder's screen.
    """
    return str(s).replace("`", "'")


def _progress(entry: dict) -> str:
    idx, total = entry.get("idx"), entry.get("total")
    if isinstance(idx, int) and isinstance(total, int) and total:
        return f"check {idx}/{total}"
    return "starting"


def render_in_flight() -> Tuple[str, List[ButtonRow]]:
    """The sub-tick panel: which candidate, which check, right now.

    Every line is computed from the trail at render time. Nothing here is stored state, and
    nothing is inferred from a tick summary — that is `📊 Last run`, one level coarser.
    """
    path = latest_day_file()
    if path is None:
        text = ("🔬 *In flight* — sub-tick\n\n"
                "_No audit trail on disk yet._\n"
                f"Looked in `{AUDIT_DIR}`.\n\n" + panel_stamp("pd_in_flight"))
        return text, [[("🔭 Prospector", "estate:prospector_daemon")]]

    state = fold(tail_rows(path))
    live, stalled, done = state["in_flight"], state["stalled"], state["done"]
    trail_age = state["trail_age_s"]

    if live:
        head_status = f"🟢 {len(live)} candidate(s) in flight"
    elif trail_age is not None and trail_age <= _STALE_S:
        head_status = "🟡 trail live, nothing being vetted (between ticks)"
    else:
        head_status = "🟡 idle — no engine activity in the window"

    header = [
        "🔬 *In flight* — what the engine is doing right now",
        # The day-file name sits OUTSIDE the italic span. `render_panel` cannot nest a code
        # span inside italic or bold: the closing marker lands after the code span, the entity
        # never closes, and Telegram answers HTTP 400 — the panel does not render at all.
        # Proven on the send path, not by reading the parser:
        #   parse(render_panel("_a `b.jsonl`_"))  -> ParseError: unclosed italic entity
        #   parse(render_panel("_a_ `b.jsonl`"))  -> OK
        f"_{head_status} · trail {_age_str(trail_age)}_ · `{path.name}`",
    ]

    groups: List[Group] = []

    body: List[str] = []
    for e in live:
        title = _code(e["title"] or e["candidate_id"][:12] or "(untitled)")
        line = f"  🟢 `{title}` — {_progress(e)}"
        if e["check"]:
            # A code span, not *bold*: check names carry underscores (`payer_solvency`), and
            # an underscore outside a code span opens an italic Telegram never closes.
            line += f" · `{_code(e['check'])}`"
            if e["verdict"]:
                line += f" → {_code(e['verdict'])}"
        line += f" · {_age_str(e['age_s'])}"
        if e["full_vet"]:
            line += " · FULL-VET"
        body.append(line)
    if not live:
        body.append("  _nothing mid-vet_")

    for e in stalled:
        title = _code(e["title"] or e["candidate_id"][:12] or "(untitled)")
        why = "process gone" if e["pid_alive"] is False else f"silent {_age_str(e['age_s'])}"
        body.append(f"  🔴 `{title}` — opened, never closed · {why}")

    for d in done[::-1]:
        dec = _code(str(d.get("decision") or "?").upper())
        gate = _code(str(d.get("gate") or ""))
        mark = "🟢" if dec == "PASS" else "⚪"
        tail = f" · gate `{gate}`" if gate else ""
        prov = " · provisional" if d.get("provisional") else ""
        cid = _code(str(d.get("candidate_id") or "")[:12])
        body.append(f"  {mark} {dec}{tail}{prov} — `{cid}`")

    ok, err = state["retrieval_ok"], state["retrieval_err"]
    if ok or err:
        total = ok + err
        rate = (err / total) * 100 if total else 0.0
        mark = "🟢" if rate < 20 else ("🟡" if rate < 50 else "🔴")
        body.append(f"  {mark} retrieval {ok} ok / {err} failed over the last {total} searches")

    header.append("")
    header.extend(body)
    header.append("")
    header.append(f"_folded {state['rows']} audit rows_")
    header.append(panel_stamp("pd_in_flight"))

    # `tail`, not a Group: a Group renders its title as a legend line in the text, and this
    # panel's text is already its own legend. An empty-titled Group would emit a blank line.
    del groups
    return compose(
        header,
        [],
        self_action="pd_in_flight",
        tail=[[("📊 Last run", "estate:pd_last_run"),
               ("🔭 Prospector", "estate:prospector_daemon")]],
        with_legend=False,
    )
