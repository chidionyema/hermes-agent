"""Estate intelligence panels — the five read-only reports that had buttons and no doors.

Every renderer here wraps a function that already existed under `~/.hermes/scripts/` and
returned a dict nobody rendered. The buttons were shipped; the panels never were. Tapping any
of them printed `⚠️ Unknown action`.

Three rules this module follows, each because of a defect already paid for elsewhere:

- **Underscores are italic markers.** Every value from these scripts is a snake_case
  identifier (`policy_firings`, `tcc_permission`, `api_credits`) and MarkdownV2 reads `_` as
  emphasis, so one unbalanced pair draws a 400 for the whole send. `_words()` humanises them,
  which also happens to be the point: an operator reading a phone should see "policy firings".
- **A report that could not be read says so.** These scripts touch launchd, git, and logs that
  may not exist. Every renderer catches, and prints what failed. An empty section rendered as
  "0" reads as a measurement; it is not one.
- **Nothing here mutates.** The one auto-fix that does (`auto_fixer.auto_fix_all`) lives in
  `estate.py` behind the two-screen confirm, not here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from gateway.operator_shell.panel_chrome import panel_stamp, with_nav

ButtonRow = List[Tuple[str, str]]

HERMES_HOME = Path.home() / ".hermes"
SCRIPTS = HERMES_HOME / "scripts"


def _words(value: object, limit: int = 60) -> str:
    """A snake_case identifier as words, safe to drop into MarkdownV2.

    `_` is the italic marker, so `auto_fixes` opens an emphasis span that the next identifier
    closes in the wrong place — the send fails with a 400 and the operator sees nothing at
    all. Stripping the rest of the specials is the same defence `prospector_daemon._plain`
    applies to model-written titles.
    """
    text = str(value if value is not None else "—").replace("_", " ")
    for ch in "*[]()~`>#+=|{}\\":
        text = text.replace(ch, "")
    text = " ".join(text.split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def _call(module: str, func: str, *args: Any) -> Tuple[Any, str]:
    """Import a `~/.hermes/scripts/` module and call one function. Returns (result, error).

    The same lazy, path-based import `health_panel.py:21` uses. Never raises: a report that
    cannot run must render as a panel saying why, not as the dispatcher's generic
    "Action failed", which tells the operator nothing about which report broke.
    """
    try:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import importlib

        mod = importlib.import_module(module)
        return getattr(mod, func)(*args), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _broken(title: str, what: str, err: str, stamp: str) -> Tuple[str, List[ButtonRow]]:
    """The panel a failed report renders. Names the script, so the next step is obvious."""
    return (
        "\n".join([
            f"{title}",
            "",
            f"_Could not run {what}._",
            f"`{_words(err, 160)}`",
            "",
            panel_stamp(stamp),
        ]),
        with_nav([], stamp),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 🩺 Estate health — cross_project.estate_health_score()
# ──────────────────────────────────────────────────────────────────────────────

def render_estate_health() -> Tuple[str, List[ButtonRow]]:
    """One number per subsystem, and the average. Read-only: plists and files on disk."""
    data, err = _call("cross_project", "estate_health_score")
    if err or not isinstance(data, dict):
        return _broken("🩺 *Estate health*", "the estate health score", err, "estate_health")

    overall = data.get("overall")
    breakdown = data.get("breakdown") or {}
    face = "🟢" if isinstance(overall, (int, float)) and overall >= 70 else (
        "🟡" if isinstance(overall, (int, float)) and overall >= 40 else "🔴")

    lines = [f"🩺 *Estate health* — {face} {overall if overall is not None else '?'}/100", ""]
    if breakdown:
        for name, score in sorted(breakdown.items(), key=lambda kv: (kv[1] or 0, kv[0])):
            mark = "🟢" if (score or 0) >= 70 else ("🟡" if (score or 0) >= 40 else "🔴")
            lines.append(f"{mark} *{_words(name, 24)}* — {score}")
    else:
        lines.append("_No subsystem scored. The probe found nothing to measure._")

    lines += [
        "",
        "_Worst first. Each score is a file-and-plist check, not a live call — a subsystem "
        "can score well and still be failing its work._",
        "",
        panel_stamp("estate_health"),
    ]
    buttons: List[ButtonRow] = [
        [("🔗 Dependencies", "estate:dependencies"), ("🔗 Linked failures", "estate:correlate")],
        [("🔍 Diagnose", "estate:diagnose_panel"), ("⚙️ Daemons", "estate:daemons")],
    ]
    return "\n".join(lines), with_nav(buttons, "estate_health")


# ──────────────────────────────────────────────────────────────────────────────
# 🔗 Dependencies — cross_project.dependency_map()
# ──────────────────────────────────────────────────────────────────────────────

def render_dependencies() -> Tuple[str, List[ButtonRow]]:
    """What each subsystem needs, and what is currently blocking it."""
    data, err = _call("cross_project", "dependency_map")
    if err or not isinstance(data, dict):
        return _broken("🔗 *Dependencies*", "the dependency map", err, "dependencies")

    deps = data.get("dependencies") or {}
    lines = ["🔗 *Dependencies* — what each part needs to work", ""]
    if not deps:
        lines.append("_Nothing mapped._")
    blocked = 0
    for name, info in sorted(deps.items()):
        if not isinstance(info, dict):
            continue
        status = str(info.get("status") or "unknown")
        mark = {"healthy": "🟢", "blocked": "🔴"}.get(status, "⚪")
        if status == "blocked":
            blocked += 1
        lines.append(f"{mark} *{_words(name, 24)}* — {_words(status, 16)}")
        needs = info.get("depends_on") or []
        if needs:
            lines.append("   needs " + ", ".join(_words(n, 20) for n in needs[:4]))
        blockers = info.get("blocked_by") or []
        if blockers:
            lines.append("   ⛔ blocked by " + ", ".join(_words(b, 20) for b in blockers[:4]))

    lines += [
        "",
        f"_{blocked} blocked._" if blocked else "_Nothing is reported blocked._",
        "_⚪ means the probe had no signal to read, not that the part is fine._",
        "",
        panel_stamp("dependencies"),
    ]
    buttons: List[ButtonRow] = [
        [("🩺 Estate health", "estate:estate_health"),
         ("🔗 Linked failures", "estate:correlate")],
        [("🔍 Diagnose", "estate:diagnose_panel")],
    ]
    return "\n".join(lines), with_nav(buttons, "dependencies")


# ──────────────────────────────────────────────────────────────────────────────
# 🔗 Linked failures — BOTH correlators
# ──────────────────────────────────────────────────────────────────────────────

def render_correlate() -> Tuple[str, List[ButtonRow]]:
    """Failures that happened together, from both correlators.

    `correlate` was quarantined as ambiguous: `predictor.correlate_failures()` groups events
    into 30-minute windows across four logs, and `cross_project.correlate_estate()` clusters
    ops-monitor warnings by shared cause. They are not rivals — they read different inputs at
    different grain. Picking one would have thrown away half the evidence, so the panel shows
    both under headings that say which is which.
    """
    windows, werr = _call("predictor", "correlate_failures")
    estate, eerr = _call("cross_project", "correlate_estate")
    if werr and eerr:
        return _broken("🔗 *Linked failures*", "either correlator", werr or eerr, "correlate")

    lines = ["🔗 *Linked failures* — things that broke together", ""]

    lines.append("*By time window* — errors within 30 minutes of each other")
    if werr:
        lines.append(f"_unavailable: {_words(werr, 80)}_")
    else:
        clusters = (windows or {}).get("clusters") or []
        if not clusters:
            lines.append("_" + _words((windows or {}).get("summary")
                                      or "No clusters found.", 120) + "_")
        for c in clusters[:5]:
            lines.append(f"• `{_words(c.get('window'), 12)}` — "
                         + ", ".join(_words(t, 20) for t in (c.get("failure_types") or [])[:4]))
            if c.get("hypothesis"):
                lines.append(f"   _{_words(c['hypothesis'], 80)}_")

    lines += ["", "*By shared cause* — recent warnings and errors, grouped"]
    if eerr:
        lines.append(f"_unavailable: {_words(eerr, 80)}_")
    else:
        clusters = (estate or {}).get("clusters") or []
        if not clusters:
            lines.append("_No cluster large enough to report (needs 3+ recent warnings)._")
        for c in clusters[:5]:
            lines.append(f"• {c.get('count', '?')} events — "
                         f"cause: {_words(c.get('shared_cause'), 40)}")
            types = c.get("failure_types") or []
            if types:
                lines.append("   " + ", ".join(_words(t, 20) for t in types[:4]))

    lines += [
        "",
        "_Co-occurrence, not causation. Two things failing in the same half hour is a lead._",
        "",
        panel_stamp("correlate"),
    ]
    buttons: List[ButtonRow] = [
        [("🚨 View incidents", "estate:incidents"), ("🔍 Diagnose", "estate:diagnose_panel")],
        [("🩺 Estate health", "estate:estate_health"),
         ("🔗 Dependencies", "estate:dependencies")],
    ]
    return "\n".join(lines), with_nav(buttons, "correlate")


# ──────────────────────────────────────────────────────────────────────────────
# 📜 Compliance — auto_close_identity.AgentIdentity().compliance_report()
# ──────────────────────────────────────────────────────────────────────────────

def _identity_report() -> Tuple[Any, str]:
    """`compliance_report` is a METHOD on `AgentIdentity`, not a module function.

    `auto_close_identity.py:833` is the only other caller and constructs it with no arguments;
    this mirrors that exactly rather than inventing a construction the script never uses.
    """
    try:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import importlib

        mod = importlib.import_module("auto_close_identity")
        return mod.AgentIdentity().compliance_report(), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def render_compliance() -> Tuple[str, List[ButtonRow]]:
    """Otto's governance report: what it may change about itself, and what is logged.

    Rendered GENERICALLY — section heading per top-level key, one line per leaf — because the
    report's shape is owned by the script and grows a section at a time. A renderer that named
    its keys would print an empty panel the day a section was added, which is the failure mode
    that reads as "compliant" when it means "not read".
    """
    data, err = _identity_report()
    if err or not isinstance(data, dict):
        return _broken("📜 *Compliance*", "the compliance report", err, "compliance")

    lines = ["📜 *Compliance* — what Otto may change, and what is on the record", ""]
    for section, body in data.items():
        lines.append(f"*{_words(section, 40)}*")
        if isinstance(body, dict):
            for key, value in body.items():
                if isinstance(value, (list, tuple)):
                    shown = ", ".join(_words(v, 22) for v in list(value)[:4]) or "none"
                    if len(value) > 4:
                        shown += f" +{len(value) - 4}"
                    lines.append(f"  • {_words(key, 30)}: {shown}")
                elif isinstance(value, bool):
                    lines.append(f"  {'✅' if value else '❌'} {_words(key, 40)}")
                else:
                    lines.append(f"  • {_words(key, 30)}: {_words(value, 70)}")
        else:
            lines.append(f"  {_words(body, 100)}")
        lines.append("")

    lines += [
        "_Self-reported by the identity module. It states what the code enforces, which is "
        "not the same as a run that proved it._",
        "",
        panel_stamp("compliance"),
    ]
    buttons: List[ButtonRow] = [
        [("🧠 Otto health", "estate:otto_health"), ("🧠 RSI", "estate:rsi")],
    ]
    return "\n".join(lines), with_nav(buttons, "compliance")


# ──────────────────────────────────────────────────────────────────────────────
# 📈 Score — score_driver.score_burndown() + check_score_regression()
# ──────────────────────────────────────────────────────────────────────────────

def render_score() -> Tuple[str, List[ButtonRow]]:
    """Where the self-improvement score is, what is holding it down, and what to do.

    Two of `score_driver`'s three dict-returning functions, because one alone answers half the
    question: the burndown says where the score is and which factor is furthest from its
    ceiling; the regression check says whether it is falling. `score_leaderboard()` is left
    out — weekly averages are a trend the burndown already implies, and a third block would
    push the panel past a phone screen.
    """
    burn, berr = _call("score_driver", "score_burndown")
    reg, rerr = _call("score_driver", "check_score_regression")
    if berr and rerr:
        return _broken("📈 *Score*", "the score driver", berr or rerr, "score")

    lines = ["📈 *Score* — self-improvement, against its target", ""]
    if berr:
        lines.append(f"_Burndown unavailable: {_words(berr, 80)}_")
    else:
        b = burn or {}
        current, target = b.get("current_score"), b.get("target_score")
        pct = b.get("pct_complete")
        lines.append(f"*{current}* of *{target}* target"
                     + (f" — {pct}% there" if pct is not None else ""))
        gap = b.get("biggest_gap") or {}
        if gap:
            lines += [
                "",
                f"*Biggest gap:* {_words(gap.get('factor'), 40)}",
                f"  at {gap.get('current')} of a possible {gap.get('max')} "
                f"(short by {gap.get('gap')})",
            ]
        if b.get("action"):
            lines += ["", f"*Do this next:* {_words(b['action'], 160)}"]

    lines.append("")
    if rerr:
        lines.append(f"_Regression check unavailable: {_words(rerr, 80)}_")
    else:
        r = reg or {}
        if r.get("regression"):
            lines.append(f"🔴 *Falling* — {r.get('consecutive_drops', '?')} days down in a row")
            if r.get("alert"):
                lines.append(f"_{_words(r['alert'], 140)}_")
        else:
            lines.append("🟢 *Not falling* — no run of consecutive drops")
        scores = r.get("scores") or []
        if scores:
            lines.append("recent: " + " → ".join(str(s) for s in list(scores)[-6:]))

    lines += ["", panel_stamp("score")]
    buttons: List[ButtonRow] = [
        [("🧠 Otto health", "estate:otto_health"), ("🧠 RSI", "estate:rsi")],
        [("📅 Weekly Digest", "estate:weekly_digest")],
    ]
    return "\n".join(lines), with_nav(buttons, "score")


# ──────────────────────────────────────────────────────────────────────────────
# 📜 Find a log — the chooser the bare `logs` button never had
# ──────────────────────────────────────────────────────────────────────────────

def render_log_picker() -> Tuple[str, List[ButtonRow]]:
    """Three subsystems write logs and each renderer needs a unit the bare button never sent.

    The chooser points at the SUBSYSTEM panels rather than straight at each log, for a reason
    that is not laziness: `estate:pd_logs:scheduler` and `estate:se_logs` are both labelled
    "📜 Logs" everywhere they appear, and one destination may carry only one name
    (`test_destination_vocabulary`). Renaming them here to fit on one screen would break that
    for every other panel. Routing by subject also puts the status in front of the log, which
    is the order the operator actually needs: the panel usually answers the question.
    """
    lines = [
        "📜 *Find a log*",
        "",
        "Logs live with the thing that writes them. Pick a subsystem — its panel has a "
        "*📜 Logs* button, and usually answers the question before you need it.",
        "",
        "• *Prospector* — the idea factory: ticks, batches, verdicts",
        "• *Engine* — signal intake",
        "• *Daemons* — everything else launchd runs, with per-unit logs",
        "",
        panel_stamp("logs"),
    ]
    buttons: List[ButtonRow] = [
        [("🔭 Prospector", "estate:prospector_daemon"), ("💹 Engine", "estate:signal_engine")],
        [("⚙️ Daemons", "estate:daemons")],
    ]
    return "\n".join(lines), with_nav(buttons, "logs")


# ──────────────────────────────────────────────────────────────────────────────
# 🛠 Restart stuck jobs — auto_fixer.auto_fix_all(), the only MUTATING report here
# ──────────────────────────────────────────────────────────────────────────────
#
# This button was quarantined as undeliverable: eleven sites across four unrelated problem
# domains (moat credits, incidents, Otto policy, per-project CI) all said "🛠 Fix all", and the
# only callable behind the name fixes none of those — `auto_fixer.auto_fix_all()` restarts
# failed cron jobs, kickstarts a stale coordinator, and retries a stuck config push. A shared
# handler would have been silently wrong at most of the eleven sites.
#
# The fix is not a smarter handler, it is an honest name. All eleven now say "🛠 Restart stuck
# jobs", which is exactly what happens, and the panel states what it does NOT cover so the
# operator on the moat-down screen is not left believing their credits problem was addressed.
#
# `auto_fix_all` takes `dry_run`, which makes the two-screen confirm real rather than
# ceremonial: screen one is the actual dry run, so the confirm card lists the jobs about to be
# kicked instead of promising in general terms.

_FIX_SCOPE = (
    "*Covers:* failed cron jobs, a stale coordinator, a stuck config push.\n"
    "*Does not cover:* API credits, open incidents, Otto policy, project CI."
)


def _fix_lines(results: dict, verb: str) -> List[str]:
    """One line per job. `verb` is "would restart" on the preview, "restarted" after."""
    out: List[str] = []
    for bucket, mark, label in (("fixed", "✅", verb),
                                ("skipped", "⚪", "nothing to do"),
                                ("failed", "🔴", "failed")):
        for item in results.get(bucket) or []:
            problem = _words((item or {}).get("problem"), 24)
            detail = (item or {}).get("detail") or {}
            action = _words(detail.get("action") if isinstance(detail, dict) else detail, 40)
            out.append(f"{mark} *{problem}* — {label}" + (f" ({action})" if action != "—" else ""))
    return out


def auto_fix(dry_run: bool) -> Tuple[Any, str]:
    """`auto_fixer.auto_fix_all(dry_run=...)`. Returns (results, error)."""
    return _call("auto_fixer", "auto_fix_all", dry_run)


def render_fix_preview() -> Tuple[str, List[ButtonRow]]:
    """Screen one: the real dry run, so the confirm card names the jobs it would kick."""
    results, err = auto_fix(True)
    if err or not isinstance(results, dict):
        return _broken("🛠 *Restart stuck jobs*", "the auto-fixer", err, "fix_all")

    body = _fix_lines(results, "would restart")
    nothing = not any(results.get(b) for b in ("fixed", "skipped", "failed"))
    lines = ["🛠 *Restart stuck jobs?*", ""]
    if nothing:
        lines += ["✅ _Nothing is stuck. There is nothing to restart._", ""]
    else:
        lines += body + [""]
    lines += [_FIX_SCOPE, "", panel_stamp("fix_all")]

    buttons: List[ButtonRow] = []
    if not nothing:
        buttons.append([("✅ Confirm", "estate:fix_all_confirm"), ("✗ Cancel", "estate:refresh")])
    buttons.append([("🔍 Diagnose", "estate:diagnose_panel")])
    return "\n".join(lines), with_nav(buttons, "fix_all")


def render_fix_result(results: Any, err: str) -> Tuple[str, List[ButtonRow]]:
    """Screen two: what actually happened, with the verification the fixer recorded."""
    if err or not isinstance(results, dict):
        return _broken("🛠 *Restart stuck jobs*", "the auto-fixer", err, "fix_all")

    lines = ["🛠 *Restart stuck jobs* — done", ""] + (_fix_lines(results, "restarted")
                                                     or ["_Nothing needed restarting._"])
    verified = results.get("verified") or []
    if verified:
        lines += ["", "*Checked afterwards:*"]
        for v in verified[:6]:
            ver = (v or {}).get("verify") or {}
            mark = "✅" if ver.get("verified") else "🔴"
            lines.append(f"{mark} {_words((v or {}).get('problem'), 24)} — "
                         f"{_words(ver.get('evidence'), 70)}")
    lines += ["", _FIX_SCOPE, "", panel_stamp("fix_all")]
    buttons: List[ButtonRow] = [
        [("🛠 Restart stuck jobs", "estate:fix_all"), ("🔍 Diagnose", "estate:diagnose_panel")],
    ]
    return "\n".join(lines), with_nav(buttons, "fix_all")


_RENDERERS: Dict[str, Callable[[], Tuple[str, List[ButtonRow]]]] = {
    "estate_health": render_estate_health,
    "dependencies": render_dependencies,
    "correlate": render_correlate,
    "compliance": render_compliance,
    "score": render_score,
    "logs": render_log_picker,
}
