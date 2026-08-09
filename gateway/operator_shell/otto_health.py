#!/usr/bin/env python3
"""
otto-health.py — Operator shell panel: Otto Health monitoring dashboard.

Shows Otto's self-improvement metrics: compounding score, policy effectiveness,
injection relevance, and week-over-week velocity. The single pane of glass for
"is Otto actually getting better?"

Accessed via: estate:otto_health, natural language "otto health"
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

ButtonRow = List[Tuple[str, str]]

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
POLICIES_DIR = HERMES_HOME / "policies"
FIRINGS_LOG = HERMES_HOME / "logs" / "policy-firings.jsonl"
INJECTION_LOG = HERMES_HOME / "logs" / "injection-log.jsonl"
OPS_LOG = HERMES_HOME / "logs" / "ops-monitor.jsonl"
AUDIT_DIR = HERMES_HOME / "logs" / "self-audit"
DAILY_DIR = AUDIT_DIR / "daily"
VELOCITY_FILE = AUDIT_DIR / "velocity.jsonl"


def _load_jsonl(path: Path, since_hours: int = 168) -> list:
    if not path.is_file():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            ts_str = e.get("ts") or e.get("timestamp") or ""
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
            entries.append(e)
        except Exception:
            continue
    return entries


def _count_policies() -> dict:
    """Count policies by status and recency."""
    if not POLICIES_DIR.is_dir():
        return {"total": 0, "active": 0, "provisional": 0, "ops": 0, "auto": 0,
                "created_this_week": 0, "retired_this_week": 0}

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    counts = {"total": 0, "active": 0, "provisional": 0, "ops": 0, "auto": 0,
              "created_this_week": 0, "retired_this_week": 0}

    for f in POLICIES_DIR.iterdir():
        if f.suffix != ".json":
            continue
        try:
            p = json.loads(f.read_text())
            counts["total"] += 1
            status = p.get("status", "")
            if status in ("active",):
                counts["active"] += 1
            elif status in ("provisional",):
                counts["provisional"] += 1
            pid = p.get("id", "")
            if "ops" in pid or "pol-ops" in pid:
                counts["ops"] += 1
            if "auto" in pid:
                counts["auto"] += 1
            created = p.get("created", "")
            if created:
                try:
                    ct = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if ct >= week_ago:
                        counts["created_this_week"] += 1
                except Exception:
                    pass
        except Exception:
            continue

    # Check archived for retired count
    archived = POLICIES_DIR / "archived"
    if archived.is_dir():
        for f in archived.iterdir():
            if f.suffix == ".json":
                try:
                    p = json.loads(f.read_text())
                    retired = p.get("retired_at", "") or p.get("archived_at", "")
                    if retired:
                        rt = datetime.fromisoformat(str(retired).replace("Z", "+00:00"))
                        if rt >= week_ago:
                            counts["retired_this_week"] += 1
                except Exception:
                    continue

    return counts


def _compute_score() -> dict:
    """Compute the Otto Effectiveness Score (0.0-1.0)."""
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    # 1. Auto-fixes ratio (did Otto fix things before I noticed?)
    ops = _load_jsonl(OPS_LOG, since_hours=168)
    auto_fixes = sum(1 for o in ops if o.get("type") in ("moat_auto_pause",))
    total_ops_events = len(ops)
    auto_fix_ratio = auto_fixes / max(total_ops_events, 1)

    # 2. Injection relevance (are policies reaching Otto?)
    injections = _load_jsonl(INJECTION_LOG, since_hours=168)
    relevant = sum(1 for i in injections
                   if i.get("relevant_policies_count", i.get("active_policies_count", 0)) > 0)
    injection_relevance = relevant / max(len(injections), 1)

    # 3. Policy firings (is the enforcer working?)
    firings = _load_jsonl(FIRINGS_LOG, since_hours=168)
    firing_ratio = min(len(firings) / max(len(injections), 1), 1.0)

    # 4. Learning rate (is Otto creating policies?)
    policies = _count_policies()
    learning_score = min(policies["created_this_week"] / 3, 1.0)

    # 5. Estate health
    estate_healthy = 1.0  # default
    try:
        pause_file = Path.home() / "Documents/code/prospector/store/scheduler/PAUSE"
        if pause_file.is_file():
            estate_healthy = 0.5
    except Exception:
        pass

    score = (
        0.30 * auto_fix_ratio
        + 0.25 * injection_relevance
        + 0.20 * firing_ratio
        + 0.15 * learning_score
        + 0.10 * estate_healthy
    )

    return {
        "score": round(score, 3),
        "breakdown": {
            "auto_fixes": round(auto_fix_ratio, 3),
            "injection_relevance": round(injection_relevance, 3),
            "policy_firings": round(firing_ratio, 3),
            "learning": round(learning_score, 3),
            "estate_health": round(estate_healthy, 3),
        },
        "raw": {
            "auto_fixes": auto_fixes,
            "total_injections": len(injections),
            "relevant_injections": relevant,
            "total_firings": len(firings),
            "policies_created_this_week": policies["created_this_week"],
        },
    }


def _velocity_data() -> list:
    """Load velocity history for sparkline."""
    if not VELOCITY_FILE.is_file():
        return []
    entries = []
    for line in VELOCITY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    # One entry per DATE, last write wins, then the last 14 DATES. This was
    # `entries[-14:]` — the last 14 ROWS — under a comment claiming days, feeding a panel
    # that calls it a "14-day trend". Because the writer appended on every render, the live
    # file held 76 rows across 4 dates (60 of them 2026-08-02), so the trend showed 3 days
    # with 11 bars re-sampling one of them. Deduping on READ also repairs the display for
    # history already written, without rewriting the operator's audit file underneath them.
    by_date: dict = {}
    for e in entries:
        day = e.get("date")
        if day:
            by_date[day] = e
    return [by_date[d] for d in sorted(by_date)][-14:]


def _sparkline(scores: list, width: int = 14) -> str:
    """Text sparkline from score values."""
    if not scores:
        return "_no data yet_"
    bars = "▁▂▃▄▅▆▇█"
    values = [s.get("score", 0) for s in scores[-width:]]
    if not values:
        return "_no data_"
    mn, mx = min(values), max(values)
    if mx == mn:
        mx = mn + 0.01
    result = ""
    for v in values:
        idx = int((v - mn) / (mx - mn) * (len(bars) - 1))
        result += bars[min(idx, len(bars) - 1)]
    return result


def _save_daily_snapshot():
    """Save today's snapshot for compounding."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DAILY_DIR / f"{today}.json"

    score = _compute_score()
    policies = _count_policies()

    snapshot = {
        "date": today,
        "score": score["score"],
        "score_breakdown": score["breakdown"],
        "policies": policies,
        "raw": score["raw"],
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    path.write_text(json.dumps(snapshot, indent=2))

    # Upsert today's row; do NOT append. This function is called from render_otto_health
    # (:252), so opening the panel is a write — every tap used to add another row for the
    # same date and skew the trend the same panel then draws. The daily JSON above was
    # always idempotent (write_text); only this file grew. Written via tmp+replace so a
    # crash mid-rewrite cannot truncate the history.
    VELOCITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if VELOCITY_FILE.is_file():
        for line in VELOCITY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("date") != today:
                rows.append(entry)
    rows.append({"date": today, "score": score["score"]})
    tmp = VELOCITY_FILE.with_suffix(VELOCITY_FILE.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(VELOCITY_FILE)

    return snapshot


def _previous_score() -> dict:
    """Get the most recent previous daily snapshot for comparison."""
    if not DAILY_DIR.is_dir():
        return {}
    files = sorted(DAILY_DIR.glob("*.json"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prev = None
    for f in reversed(files):
        if f.stem < today:
            try:
                prev = json.loads(f.read_text())
                break
            except Exception:
                continue
    return prev or {}


def render_otto_health() -> Tuple[str, List[ButtonRow]]:
    """Render the Otto Health monitoring dashboard."""
    from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav

    # Save today's snapshot (idempotent — overwrites same day)
    snapshot = _save_daily_snapshot()
    score = snapshot["score"]
    policies = _count_policies()
    prev = _previous_score()
    velocity = _velocity_data()

    # Score delta
    prev_score = prev.get("score", score)
    delta = score - prev_score
    delta_str = f"↑ {delta:+.2f}" if delta > 0 else (f"↓ {delta:+.2f}" if delta < 0 else "—")

    # Score emoji
    if score >= 0.7:
        emoji = "🟢"
    elif score >= 0.4:
        emoji = "🟡"
    else:
        emoji = "🔴"

    lines = [
        "🧠 *Otto Health* — self-improvement dashboard",
        "",
        f"{emoji} *Score: {score:.2f}* ({delta_str} from yesterday)",
        f"  Auto-fixes: {snapshot['raw']['auto_fixes']} · "
        f"Injections: {snapshot['raw']['relevant_injections']}/{snapshot['raw']['total_injections']} relevant · "
        f"Firings: {snapshot['raw']['total_firings']} · "
        f"Learning: {policies['created_this_week']} policies created",
        "",
    ]

    # Sparkline
    spark = _sparkline(velocity)
    lines.append(f"*14-day trend:* {spark}")
    lines.append("")

    # Policies
    lines.append(f"*Policies:* {policies['total']} total "
                 f"({policies['active']} active, {policies['provisional']} provisional, "
                 f"{policies['ops']} ops, {policies['auto']} auto)")
    lines.append(f"  Created this week: {policies['created_this_week']} · "
                 f"Retired: {policies['retired_this_week']}")
    lines.append("")

    # Top gaps
    lines.append("*Top gaps:*")
    b = snapshot["score_breakdown"]
    gaps = []
    if b["policy_firings"] < 0.1:
        gaps.append("1. 🔴 Policy enforcer not firing — policies exist but never block actions")
    if b["injection_relevance"] < 0.5:
        gaps.append("2. 🟡 Policy injection relevance low — need better task matching")
    if b["auto_fixes"] < 0.3:
        gaps.append("3. 🟡 Auto-fix rate low — ops monitor may not be catching failures")
    if b["learning"] < 0.3:
        gaps.append("4. 🟡 Learning rate low — not enough new policies being created")
    if not gaps:
        gaps.append("✅ No critical gaps — system is healthy")
    lines.extend(gaps)

    lines += ["", panel_stamp("otto_health")]

    buttons: List[ButtonRow] = [
        [("📊 Status", "estate:status"), ("📜 Activity", "estate:activity:7")],
        [("🗺 Browse", "estate:find"), ("🧠 RSI", "estate:rsi")],
    ]
    buttons = with_nav(buttons, "otto_health")

    return "\n".join(lines), buttons


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Otto Health monitoring")
    parser.add_argument("--snapshot", action="store_true", help="Save daily snapshot")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.snapshot:
        snap = _save_daily_snapshot()
        if args.json:
            print(json.dumps(snap, indent=2))
        else:
            print(f"Snapshot saved: score={snap['score']}")
    else:
        score = _compute_score()
        if args.json:
            print(json.dumps(score, indent=2))
        else:
            print(f"Otto Effectiveness Score: {score['score']}")


if __name__ == "__main__":
    main()
