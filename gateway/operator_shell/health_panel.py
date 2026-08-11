"""
Health panel — makes all 7 self-improvement tiers visible in Telegram.

Shows Otto score breakdown, Tier 0-7 evidence, weekly learning digest.
This is the "single pane of glass" for "is Otto actually getting better?"
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple

ButtonRow = List[Tuple[str, str]]
HERMES_HOME = Path.home() / ".hermes"
SCRIPTS = HERMES_HOME / "scripts"


def render_health(project_key: str = "") -> Tuple[str, List[ButtonRow]]:
    """Health panel: Otto score + all Tier 0-7 evidence."""
    from gateway.operator_shell.panel_chrome import nav, with_nav
    sys.path.insert(0, str(SCRIPTS))

    # Otto Health Score
    try:
        sys.path.insert(0, str(HERMES_HOME / "hermes-agent"))
        from gateway.operator_shell.otto_health import _compute_score, _count_policies
        score_data = _compute_score()
        score = score_data["score"]
        breakdown = score_data["breakdown"]
        pcounts = _count_policies()
        raw = score_data.get("raw", {})
    except Exception:
        score = 0.5
        breakdown = {}
        pcounts = {"created_this_week": 0}
        raw = {}

    score_pct = int(score * 100)
    score_emoji = "🟢" if score >= 0.7 else ("🟡" if score >= 0.4 else "🔴")

    lines = [f"🧠 *Otto Health* — {score_emoji} {score_pct}%", ""]

    dims = [
        ("Auto-fixes", "auto_fixes"), ("Injections", "injection_relevance"),
        ("Policy firings", "policy_firings"), ("Learning", "learning"),
        ("Estate health", "estate_health"), ("Cron health", "cron_health"),
    ]
    bars = "▁▂▃▄▅▆▇█"
    for label, key in dims:
        val = breakdown.get(key, 0)
        pct = int(val * 100)
        bar_len = max(int(val * 8), 1)
        bar = bars[min(bar_len, 7)] * bar_len
        lines.append(f"{label:16s} {bar} {pct}%")

    lines.append("")

    # Tier evidence
    try:
        from outcome_tracker import OutcomeTracker
        ot = OutcomeTracker(HERMES_HOME)
        ostats = ot.stats(window_days=7)
        t = ostats['trend']['direction']
        lines.append(f"📊 *Outcomes:* {ostats['success_rate']:.0%} success · {ostats['total']} tasks · {t}")
    except Exception:
        lines.append("📊 *Outcomes:* no data yet")

    try:
        from constitutional_validator import validate
        inv = validate(HERMES_HOME)
        lines.append(f"🛡️ *Invariants:* {'✅ All 7 passing' if inv.passed else f'❌ {len(inv.violations)} violations'}")
    except Exception:
        lines.append("🛡️ *Invariants:* unavailable")

    try:
        from cost_policy_mgmt import PolicyCompressor
        pc = PolicyCompressor(HERMES_HOME)
        pa = pc.analyze()
        lines.append(f"📋 *Policies:* {pa['active']}/{pa['ceiling']} active · {pa['unscoped_policies']} unscoped")
    except Exception:
        lines.append("📋 *Policies:* unavailable")

    try:
        from auto_close_identity import AgentIdentity
        ai = AgentIdentity(HERMES_HOME)
        ident = ai.current_version()
        snaps = ai.list_snapshots()
        lines.append(f"📜 *Identity:* {ident['agent']} v{ident['version']} · {len(snaps)} snapshots")
    except Exception:
        lines.append("📜 *Identity:* unavailable")

    try:
        from holdout_eval import HoldoutManager
        hm = HoldoutManager(HERMES_HOME)
        hr = hm.validate_policies()
        if "holdout_pass_rate" in hr:
            lines.append(f"🎯 *Holdout:* {hr['holdout_pass_rate']:.0%} pass rate")
    except Exception:
        pass

    lines.append("")
    lines.append("*This week Otto learned:*")
    lines.append(f"• {pcounts['created_this_week']} new policies created")
    lines.append(f"• {raw.get('total_injections','?')} policy injections ({raw.get('relevant_injections','?')} relevant)")
    lines.append(f"• {raw.get('total_firings','?')} policy enforcements fired")

    buttons: List[ButtonRow] = [
        [("🧠 Otto health", "estate:otto_health"), ("📜 Compliance", "estate:compliance")],
        [("🗺 Browse", "estate:find"), ("🛠 Restart stuck jobs", "estate:fix_all")],
    ]
    return "\n".join(lines), with_nav(buttons)


def render_weekly_digest() -> Tuple[str, List[ButtonRow]]:
    """Weekly learning digest — designed for Monday morning push notification."""
    from gateway.operator_shell.panel_chrome import nav, with_nav
    sys.path.insert(0, str(SCRIPTS))

    try:
        sys.path.insert(0, str(HERMES_HOME / "hermes-agent"))
        from gateway.operator_shell.otto_health import _compute_score, _count_policies
        score_data = _compute_score()
        score = score_data["score"]
        pcounts = _count_policies()
        raw = score_data.get("raw", {})
    except Exception:
        score = 0.5
        pcounts = {"created_this_week": 0}
        raw = {}

    lines = [
        "🧠 *Otto Weekly Learning Digest*",
        "",
        "*This week Otto:*",
        f"• Created {pcounts['created_this_week']} new policies",
        f"• Injected policies into {raw.get('total_injections','?')} tasks",
        f"• Fired {raw.get('total_firings','?')} policy enforcements",
        f"• Auto-paused Prospector {raw.get('auto_fixes','?')} times",
        f"• Self-assessment score: {int(score*100)}%",
        "",
    ]

    # Top policy
    try:
        import json as _json
        firings_file = HERMES_HOME / "logs" / "policy-firings.jsonl"
        if firings_file.is_file():
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            policy_counts = {}
            for line in firings_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    f = _json.loads(line)
                    ts_str = f.get("timestamp", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts >= cutoff:
                            pid = f.get("policy_id", "unknown")
                            policy_counts[pid] = policy_counts.get(pid, 0) + 1
                except Exception:
                    pass
            if policy_counts:
                top = max(policy_counts, key=policy_counts.get)
                lines.append(f"*Most active policy:* `{top}` — {policy_counts[top]} enforcements")
                lines.append("")
    except Exception:
        pass

    lines.append("*Needs human attention:*")

    try:
        from auto_close_identity import GapCloser
        gc = GapCloser(HERMES_HOME)
        escalated = gc.get_escalated()
        if escalated:
            for e in escalated[:3]:
                lines.append(f"• 🔧 {e.get('domain','?')}: {e.get('description','')[:80]}")
        else:
            lines.append("• ✅ No escalated gaps")
    except Exception:
        lines.append("• Gap data unavailable")

    try:
        from constitutional_validator import validate
        inv = validate(HERMES_HOME)
        if not inv.passed:
            lines.append(f"• 🚨 {len(inv.violations)} invariant violations")
    except Exception:
        pass

    buttons: List[ButtonRow] = [
        [("🩺 Health", "estate:health"), ("🧠 Otto health", "estate:otto_health")],
    ]
    return "\n".join(lines), with_nav(buttons)
