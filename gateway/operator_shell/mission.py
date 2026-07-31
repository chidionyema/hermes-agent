"""Pinned mission card — Elon cockpit: one glance, one CTA, RSI visible.

Honesty rule: never 🟢 CLEAR when anything is blocked/degraded/busy.
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

from gateway.operator_shell.panel_chrome import clip, nav

ButtonRow = List[Tuple[str, str]]


def _coord():
    from gateway.operator_shell.estate import _load_coordinator

    return _load_coordinator()


def _cb_bits(C) -> Tuple[bool, bool, str]:
    """Return (claude_ok, agy_ok, detail). True = healthy."""
    claude_ok = agy_ok = True
    try:
        if hasattr(C, "_circuit_breaker_status"):
            claude_ok = bool(C._circuit_breaker_status("claude"))
            agy_ok = bool(C._circuit_breaker_status("agy"))
    except Exception:
        pass
    if claude_ok and agy_ok:
        return True, True, ""
    if not claude_ok and not agy_ok:
        return False, False, "Claude+agy CB open"
    if not claude_ok:
        return False, True, "Claude CB open"
    return True, False, "agy CB open"


def _blocked_missions(conn) -> int:
    try:
        import flight

        return sum(1 for m in flight.list_missions(conn) if m["status"] == "blocked")
    except Exception:
        return 0


def _inflight_code(conn) -> Optional[Tuple[str, str]]:
    try:
        row = conn.execute(
            "SELECT id, status FROM tasks WHERE source='code:telegram' "
            "AND status IN ('open','diagnosed','executing','verifying') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        tid = row["id"] if hasattr(row, "keys") else row[0]
        st = row["status"] if hasattr(row, "keys") else row[1]
        return str(tid), str(st)
    except Exception:
        return None


def _verdict(conn, C) -> Tuple[str, str]:
    """Return (emoji_word, detail). Never false-CLEAR when estate needs attention."""
    try:
        hb = C.get_meta(conn, "last_tick")
        tick_age = int(time.time() - hb["updated_at"]) if hb else None
        daemon_ok = tick_age is not None and tick_age < 200
        gateway_ok = (
            C.gateway_alive() if hasattr(C, "gateway_alive") else C._proc_alive("gateway run")
        )
        daemon_proc = C._proc_alive("coordinator.py daemon")
        if hasattr(C, "_launchctl_running"):
            daemon_proc = daemon_proc or (
                C._launchctl_running("ai.hermes.coordinator") is True
            )
        paused = C.estate_paused()
        used = C.tasks_today(conn)
        budget = C.DAILY_TASK_BUDGET
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        blocked_n = _blocked_missions(conn)
        code = _inflight_code(conn)
        claude_ok, agy_ok, cb_detail = _cb_bits(C)

        if paused:
            return "🟡 PAUSED", "spend frozen"
        if not ((daemon_ok or daemon_proc) and gateway_ok):
            bits = []
            if not (daemon_ok or daemon_proc):
                bits.append(
                    f"daemon {tick_age}s" if tick_age is not None else "daemon down"
                )
            if not gateway_ok:
                bits.append("gateway down")
            return "🔴 DEGRADED", " · ".join(bits)
        if used >= budget:
            return "🔴 BUDGET", f"{used}/{budget} tasks"
        if not claude_ok and not agy_ok:
            return "🔴 CB", cb_detail
        if dec:
            return "🟡 BLOCKED", f"{len(dec)} need you"
        if blocked_n:
            return "🟡 BLOCKED", f"{blocked_n} mission(s) blocked"
        if code:
            tid, st = code
            return "🟡 BUSY", f"code `{tid[:8]}` {st}"
        if not claude_ok or not agy_ok:
            return "🟡 DEGRADED", cb_detail
        return "🟢 CLEAR", "go"
    except Exception as exc:
        logger.warning("verdict failed: %s", exc)
        return "🔴 UNKNOWN", clip(str(exc))


def _burn_today(conn, C) -> str:
    try:
        m = C.autonomy_ratio(conn, 86400)
        cost = float(m.get("total_cost", 0.0) or 0.0)
        used = C.tasks_today(conn)
        return f"${cost:.2f} · {used}/{C.DAILY_TASK_BUDGET}"
    except Exception:
        return "n/a"


def _top_blocker(conn, C) -> str:
    try:
        # Money/identity fences first — never bury under housekeeping
        try:
            fences = conn.execute(
                "SELECT id,title,risk_class FROM tasks WHERE status='awaiting_approval' "
                "AND risk_class IN ('money','identity','contract') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if fences:
                return (
                    f"APPROVE [{(fences['risk_class'] or '').upper()}] "
                    f"`{fences['id'][:8]}` {clip(fences['title'], 32)}"
                )
        except Exception:
            pass
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if dec:
            d = dec[-1]
            tag = "APPROVE" if d["status"] == "awaiting_approval" else "BLOCKED"
            risk = (d["risk_class"] or "").upper()
            risk_bit = f" [{risk}]" if risk in ("MONEY", "IDENTITY") else ""
            return f"{tag}{risk_bit} `{d['id'][:8]}` {clip(d['title'], 36)}"
        # Blocked product missions (often quota) — surface on card
        try:
            import flight

            for m in flight.list_missions(conn):
                if m["status"] == "blocked":
                    return f"MISSION `{m['id'][:8]}` {clip(m['name'], 28)} blocked (quota?)"
        except Exception:
            pass
        claude_ok, agy_ok, cb_detail = _cb_bits(C)
        if not claude_ok or not agy_ok:
            return f"CB {cb_detail}"
        code = _inflight_code(conn)
        if code:
            return f"CODE `{code[0][:8]}` {code[1]}"
        return "—"
    except Exception:
        return "—"


def _product_line(conn, C) -> str:
    """One line: active product mission + acceptance/blocker."""
    try:
        import flight

        for m in flight.list_missions(conn):
            if m["status"] not in ("flying", "blocked", "plotting"):
                continue
            cur = next(
                (x for x in flight.milestones(conn, m["id"]) if x["status"] != "done"),
                None,
            )
            if not cur:
                continue
            st = m["status"].upper()
            # clip(), not [:28] — the raw slice cut mid-word with no marker, so a clipped
            # milestone ("M4: Land the acceptance test as") read as a complete sentence.
            return (
                f"🚀 `{clip(m['name'], 18)}` {st} · "
                f"M{cur['seq']+1}: {clip(cur['title'], 28)}"
            )
    except Exception:
        pass
    return ""


def _product_autonomy(conn, C) -> str:
    try:
        m = C.autonomy_ratio(conn, 7 * 86400)
        return (
            f"`{m.get('product_autonomy_ratio', 0)*100:.0f}%` · "
            f"{m.get('product_auto_resolved', 0)} done / "
            f"{m.get('product_escalated', 0)} ask"
        )
    except Exception:
        return "n/a"


def _concerns(conn, C, verdict: str) -> List[Tuple[str, str]]:
    """Everything that currently wants the operator, most severe first.

    This ladder used to live inside `_primary_cta`, which walked all ten rungs, returned the
    FIRST hit, and threw the rest away — then a fixed nine-button menu was stapled underneath
    regardless of what was wrong. So the cockpit already knew that the money fence, the dead
    coordinator and the blocked missions were all outstanding; it just showed you one of them
    and a menu.

    Returning the whole ladder is what makes the home card severity-driven: each live concern
    prints its own line and carries its own button, so the fix for anything wrong is one tap
    from home instead of a navigation problem. `_primary_cta` is now `_concerns()[0]`.
    """
    out: List[Tuple[str, str]] = []

    def add(label: str, action: str) -> None:
        # De-duplicate by action: two rungs can legitimately point at the same panel
        # (BUDGET → Fuel and dual-CB → Fuel), and the same button twice reads as a bug.
        if not any(a == action for _l, a in out):
            out.append((label, action))

    if C.estate_paused():
        # Exclusive on purpose: nothing is burning, so nothing else is urgent yet.
        return [("▶️ Resume spend", "estate:resume")]
    # Daemon / gateway down → restart path (not Prospector)
    try:
        gateway_ok = (
            C.gateway_alive() if hasattr(C, "gateway_alive") else True
        )
        hb = C.get_meta(conn, "last_tick")
        tick_age = int(time.time() - hb["updated_at"]) if hb else None
        daemon_ok = tick_age is not None and tick_age < 200
        daemon_proc = C._proc_alive("coordinator.py daemon")
        if hasattr(C, "_launchctl_running"):
            daemon_proc = daemon_proc or (
                C._launchctl_running("ai.hermes.coordinator") is True
            )
        if not (daemon_ok or daemon_proc):
            add("♻️ Restart coord", "estate:restart")
        if not gateway_ok:
            add("⚙️ Daemons", "estate:daemons")
    except Exception:
        pass

    # Money/identity fence always wins — code fences deep-link to task card
    try:
        fence = conn.execute(
            "SELECT id, source FROM tasks WHERE status='awaiting_approval' "
            "AND risk_class IN ('money','identity','contract') "
            "ORDER BY CASE WHEN source='code:telegram' THEN 0 ELSE 1 END, created_at DESC "
            "LIMIT 1"
        ).fetchone()
        if fence:
            fid = fence["id"] if hasattr(fence, "keys") else fence[0]
            src = fence["source"] if hasattr(fence, "keys") else (
                fence[1] if len(fence) > 1 else ""
            )
            if src == "code:telegram":
                add(f"💰 Code fence {str(fid)[:8]}", f"estate:task:{str(fid)[:8]}")
            else:
                add("💰 Approve fence", "estate:inbox")
    except Exception:
        pass

    # Dual CB → fuel/honesty, not fake ship
    claude_ok, agy_ok, _ = _cb_bits(C)
    if not claude_ok and not agy_ok:
        add("⛽ Fuel / CB", "estate:system_fuel")

    # In-flight coding run
    code = _inflight_code(conn)
    if code:
        tid, st = code
        label = {
            "executing": "💻 Code run",
            "verifying": "🔎 Code verify",
        }.get(st, "💻 Code run")
        add(f"{label} {tid[:8]}", f"estate:task:{tid[:8]}")

    try:
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if dec:
            add(f"📥 Decide ({len(dec)})", "estate:inbox")
    except Exception:
        pass
    blocked = _blocked_missions(conn)
    if blocked:
        add(f"🚀 {blocked} blocked", "estate:missions")

    if "BUDGET" in verdict:
        add("⛽ Fuel", "estate:system_fuel")
    if "DEGRADED" in verdict or "CB" in verdict:
        add("⚙️ Daemons", "estate:daemons")

    # RSI live fire → surface before busywork
    try:
        from gateway.operator_shell.rsi_panel import (
            HASH_FILE,
            _last_idle,
            learning_armed,
        )

        idle = _last_idle()
        if learning_armed() and idle and (
            idle.get("exit") != 0 or idle.get("failed_phases")
        ):
            phases = str(idle.get("failed_phases") or "")
            idle_ts = float(idle.get("_ts") or 0)
            hash_m = HASH_FILE.stat().st_mtime if HASH_FILE.is_file() else 0.0
            cleared = (
                "Phase 0" in phases
                and hash_m
                and idle_ts
                and hash_m > idle_ts
            )
            if not cleared:
                add("🧠 RSI status", "estate:rsi")
    except Exception:
        pass

    return out


def _primary_cta(conn, C, verdict: str) -> Tuple[str, str]:
    """The single most severe thing outstanding, or Fleet when nothing is."""
    concerns = _concerns(conn, C, verdict)
    # Only when truly CLEAR — fleet overview beats Prospector tunnel vision
    return concerns[0] if concerns else ("🚀 Fleet", "estate:fleet")


# The surfaces that are always worth one tap, whatever is happening. This is deliberately a
# fixed grid and not state-driven: the concern rows above it already change with the estate,
# and if the stable part moved too there would be no position on the card a thumb could learn.
_SURFACES: List[ButtonRow] = [
    [("🚀 Fleet", "estate:fleet"), ("🛒 Store", "estate:st_status"), ("📥 Inbox", "estate:inbox")],
    [("⚙️ Daemons", "estate:daemons"), ("📋 Missions", "estate:missions"), ("🏗 CI", "estate:builds")],
    [("🧠 RSI", "estate:rsi"), ("🗓 Cron", "estate:pd_cron"), ("📸 Changed", "estate:diff")],
]

_MAX_CONCERNS = 3


def mission_buttons(
    paused: bool, primary: Tuple[str, str], concerns: Optional[List[Tuple[str, str]]] = None
) -> List[ButtonRow]:
    """Concerns first (one row each), then the fixed surfaces, then the spine.

    The card is now severity-driven at the top and stable at the bottom. When nothing is
    wrong the concern rows simply do not render, and the card is the three surface rows plus
    the spine — which is the quietest the cockpit has ever been on a good day.

    Capped at `_MAX_CONCERNS`: on a phone a fourth full-width row pushes the surface grid off
    the first screen, and a concern you have to scroll to is not a concern you will act on.
    The overflow is not lost — it is exactly what the panel each button points at is for.
    """
    pause_or_resume = (
        ("▶️ Resume", "estate:resume") if paused else ("⏸ Pause", "estate:pause")
    )
    try:
        from gateway.operator_shell.delivery import cron_delivery_state

        cron_ok = bool(cron_delivery_state().get("ok"))
    except Exception:
        cron_ok = bool(os.getenv("TELEGRAM_CRON_THREAD_ID", "").strip())

    live = list(concerns or [])
    if not any(a == primary[1] for _l, a in live):
        live.insert(0, primary)  # nothing outstanding → primary is the Fleet fallback
    rows: List[ButtonRow] = [[c] for c in live[:_MAX_CONCERNS]]

    # When cron destination unset — can't-miss dedicated row
    if not cron_ok:
        rows.append([("🗓 Cron delivery", "estate:setup_cron_topic")])

    rows.extend(_SURFACES)
    # Pause/Resume halts (or restarts) ALL estate spend. It is also the first button on the
    # Run panel, but it stays here too: an emergency halt at two taps is an emergency halt
    # you reach too late. This is the one deliberate duplicate in the cockpit.
    rows.append([pause_or_resume])
    # Every screen ends with the same spine, so Now / Run / Tune mean the same thing and sit
    # in the same place on literally every panel.
    rows.append(nav("refresh"))
    return rows


def render_mission_card() -> Tuple[str, bool, List[ButtonRow]]:
    """Compact forever-card — brand-dense, zero theater, honest verdict."""
    C = _coord()
    conn = C.connect()
    try:
        verdict, detail = _verdict(conn, C)
        burn = _burn_today(conn, C)
        blocker = _top_blocker(conn, C)
        prod = _product_autonomy(conn, C)
        product = _product_line(conn, C)
        paused = bool(C.estate_paused())
        concerns = _concerns(conn, C, f"{verdict} — {detail}")
        primary = concerns[0] if concerns else ("🚀 Fleet", "estate:fleet")
        blocked_n = _blocked_missions(conn)
    finally:
        conn.close()

    try:
        from gateway.operator_shell.rsi_panel import glance_line

        rsi_line = glance_line()
    except Exception:
        armed = os.path.isfile(
            os.path.expanduser("~/.hermes/meta/OFF_SWITCH")
        )
        rsi_line = f"🧠 RSI `{'ARMED' if armed else 'OFF'}`"

    try:
        from gateway.operator_shell.delivery import cron_delivery_state

        cron = cron_delivery_state()
    except Exception:
        cron = {
            "ok": bool(os.getenv("TELEGRAM_CRON_THREAD_ID", "").strip()),
            "label": "?",
            "mode": "unset",
        }

    try:
        from gateway.operator_shell.host import glance_line as host_glance

        host_line = host_glance()
    except Exception:
        host_line = "🖥 Host: ?"

    # Money rail on the mission card, not two taps away. The 2026-06-24 → 07-31
    # outage was invisible precisely because nothing on the top card mentioned it;
    # only a healthy rail is allowed to be quiet.
    try:
        from gateway.operator_shell.signal_engine import glance_line as se_glance, health

        _se_h = health()
        se_line = se_glance(_se_h) if str(_se_h.get("verdict")) != "ok" else ""
    except Exception:
        se_line = ""

    lines = [
        f"*{verdict}* — {detail}",
        host_line,
        f"💰 `{burn}`  ·  📈 {prod}",
        rsi_line,
        f"🧱 {blocker}",
    ]
    if se_line:
        lines.insert(2, se_line)
    if product:
        lines.append(product)
    if blocked_n:
        lines.append(
            f"🚀 *{blocked_n} blocked mission(s)* — tap *Open missions* "
            f"(or say `missions`) → resume/abort from the board."
        )
    lines.append(f"🧵 cron `{cron.get('label', '?')}`")
    if not cron.get("ok"):
        lines.extend(
            [
                "",
                "⚠️ *Cron destination unset*",
                "Home chat is a *private DM* — Telegram does *not* show a Topics "
                "toggle on the bot profile (that only exists for groups/forums).",
                "",
                "*What works:*",
                "1. Tap *🗓 Cron delivery* → *Keep cron in this chat* (recommended)",
                "2. Or later: make a private group → enable Topics → add Otto → "
                "open a Cron topic → send `/sethome`",
                "_API proof: createForumTopic → `chat is not a forum` on this DM._",
            ]
        )
    elif cron.get("mode") == "main_dm":
        lines.append("_Cron stays in this DM (no topic). Mute/filter as you like._")
    # Say what needs you, in severity order, instead of naming only the top item. The buttons
    # underneath are these lines in the same order, so the card reads top-to-bottom as
    # "here is what is wrong / here is the button that fixes it".
    if concerns:
        shown = concerns[:_MAX_CONCERNS]
        lines.append("")
        lines.append(f"*Needs you ({len(concerns)}):*")
        lines.extend(f"→ {c[0]}" for c in shown)
        if len(concerns) > len(shown):
            lines.append(f"_+{len(concerns) - len(shown)} more_")
    else:
        lines.extend(["", "✅ *Nothing needs you.*"])
    text = "\n".join(lines)
    return text, paused, mission_buttons(paused, primary, concerns)
