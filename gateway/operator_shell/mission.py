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
                # Full title — same reasoning as the operator-facing decision below:
                # the operator reads the cause here, not from an inbox inspect two panels
                # away. Money/identity fences never get clipped on the cockpit card.
                return (
                    f"APPROVE [{(fences['risk_class'] or '').upper()}] "
                    f"`{fences['id'][:8]}` {fences['title']}"
                )
        except Exception:
            pass
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if dec:
            # Most severe first — never bury money under probe noise (was dec[-1]).
            money = [
                d for d in dec
                if str(_row_val(d, "risk_class") or "").lower()
                in ("money", "identity", "contract")
            ]
            d = money[0] if money else dec[0]
            tag = "APPROVE" if _row_val(d, "status") == "awaiting_approval" else "BLOCKED"
            risk = str(_row_val(d, "risk_class") or "").upper()
            risk_bit = f" [{risk}]" if risk in ("MONEY", "IDENTITY", "CONTRACT") else ""
            return f"{tag}{risk_bit} `{str(_row_val(d, 'id'))[:8]}` {_row_val(d, 'title')}"
        # Blocked product missions (often quota) — surface on card
        try:
            import flight

            for m in flight.list_missions(conn):
                if m["status"] == "blocked":
                    # Same rule: full mission name is the operator's signal, not a brand
                    # label. Mission names are short on purpose; the truncation at 28 was
                    # the bug, not the data.
                    return f"MISSION `{m['id'][:8]}` {m['name']} blocked (quota?)"
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
            # Full mission name + full milestone title. The old `clip(..., 28)` showed
            # "M4: Land the acceptance test as…" with no way to know whether the missing
            # half was "as a real failing test (red)" or "as a regression net" — the kind
            # of detail that changes the operator's next action. Telegram wraps long lines,
            # so the message is still scannable.
            return (
                f"🚀 `{m['name']}` {st} · "
                f"M{cur['seq']+1}: {cur['title']}"
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


def _row_val(row, key, default=""):
    """sqlite3.Row has no .get — using it silently emptied the whole concern ladder."""
    try:
        if row is None:
            return default
        if isinstance(row, dict):
            return row.get(key, default)
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _concerns(conn, C, verdict: str) -> List[Tuple[str, str]]:
    """What needs the operator, most severe first — each item is a one-tap fix.

    Home shows at most two of these. Everything else lives under Run / Tune / Find.
    """
    out: List[Tuple[str, str]] = []

    def add(label: str, action: str) -> None:
        if not any(a == action for _l, a in out):
            out.append((label, action))

    if C.estate_paused():
        return [("▶️ Resume estate spend", "estate:resume")]

    # Daemon / gateway down
    try:
        gateway_ok = C.gateway_alive() if hasattr(C, "gateway_alive") else True
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
            add("⚙️ Fix gateway", "estate:daemons")
    except Exception:
        pass

    # Money/identity — awaiting_approval OR escalated (escalated money was invisible before)
    try:
        fence = conn.execute(
            "SELECT id, source, status, risk_class, title FROM tasks "
            "WHERE status IN ('awaiting_approval','escalated') "
            "AND risk_class IN ('money','identity','contract') "
            "ORDER BY CASE WHEN status='awaiting_approval' THEN 0 ELSE 1 END, "
            "CASE WHEN source='code:telegram' THEN 0 ELSE 1 END, created_at DESC "
            "LIMIT 1"
        ).fetchone()
        if fence:
            fid = str(_row_val(fence, "id"))
            src = str(_row_val(fence, "source"))
            st = str(_row_val(fence, "status"))
            risk = str(_row_val(fence, "risk_class") or "").upper()
            short = fid[:8]
            if src == "code:telegram":
                add(f"💰 Code fence {short}", f"estate:task:{short}")
            else:
                # Both awaiting_approval and escalated money/identity can be approved
                # (coordinator.approve accepts either). One tap — never a detour to Inbox.
                add(f"✅ Approve {risk or 'MONEY'} {short}", f"estate:approve:{short}")
    except Exception:
        pass

    claude_ok, agy_ok, _ = _cb_bits(C)
    if not claude_ok and not agy_ok:
        add("⛽ Fix fuel / CB", "estate:system_fuel")

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
            top = dec[0]
            short = str(_row_val(top, "id"))[:8]
            st = str(_row_val(top, "status"))
            if short and st == "awaiting_approval":
                add(f"✅ Decide {short}", f"estate:approve:{short}")
            n = len(dec)
            add(f"📥 {n} waiting" if n > 1 else "📥 1 waiting", "estate:inbox")
    except Exception:
        pass

    blocked = _blocked_missions(conn)
    if blocked:
        add(f"🚀 {blocked} blocked", "estate:missions")

    if "BUDGET" in verdict:
        add("⛽ Fuel", "estate:system_fuel")
    if "DEGRADED" in verdict or "CB" in verdict:
        add("⚙️ Daemons", "estate:daemons")

    # Signal engine down (money rail) — only when health says so, plain words
    try:
        from gateway.operator_shell.signal_engine import health

        h = health()
        v = str(h.get("verdict") or "")
        if v in ("tcc_denied", "down", "stalled", "unsupervised", "not_installed"):
            add("💹 Fix trading engine", "estate:signal_engine")
    except Exception:
        pass

    return out


def _primary_cta(conn, C, verdict: str) -> Tuple[str, str]:
    """The single most severe thing outstanding, or Fleet when nothing is."""
    concerns = _concerns(conn, C, verdict)
    return concerns[0] if concerns else ("🚀 Fleet", "estate:fleet")


# Quiet day: Pause + spine only. Browse is Map (Atlas), not a home mall.
# Kept for tests that assert the old domain map — Atlas Rooms still expose these panels.
# Home no longer renders this grid (founder 2026-08-01: "confusing joke").
_SURFACES: List[ButtonRow] = [
    [("🛒 Store", "estate:st_status"), ("🗓 Cron", "estate:pd_cron"), ("📥 Inbox", "estate:inbox")],
    [("🚀 Fleet", "estate:fleet"), ("📋 Missions", "estate:missions"), ("🏗 CI", "estate:builds")],
    [("⚙️ Daemons", "estate:daemons"), ("🧠 RSI", "estate:rsi"), ("📸 Changed", "estate:diff")],
]

_MAX_CONCERNS = 2


def mission_buttons(
    paused: bool, primary: Tuple[str, str], concerns: Optional[List[Tuple[str, str]]] = None
) -> List[ButtonRow]:
    """Home: at most 2 full-width fixes, then daemon controls, quick actions, SDLC, then the spine.

    No destination mall. Map / Run / Tune are how you browse and act beyond fires.
    """
    pause_or_resume = (
        ("▶️ Resume estate spend", "estate:resume")
        if paused
        else ("⏸ Pause estate spend", "estate:pause")
    )
    try:
        from gateway.operator_shell.delivery import cron_delivery_state

        cron_ok = bool(cron_delivery_state().get("ok"))
    except Exception:
        cron_ok = bool(os.getenv("TELEGRAM_CRON_THREAD_ID", "").strip())

    live = list(concerns or [])
    if not any(a == primary[1] for _l, a in live):
        live.insert(0, primary)
    # Drop the idle Fleet fallback — quiet home is fires-only.
    if len(live) == 1 and live[0][1] == "estate:fleet":
        live = []

    rows: List[ButtonRow] = [[c] for c in live[:_MAX_CONCERNS]]

    if not cron_ok:
        rows.append([("🗓 Fix cron delivery", "estate:setup_cron_topic")])

    # ── SDLC pipeline button ──
    rows.append([("💻 Full SDLC pipeline", "estate:sdlc")])

    # ── Daemon controls row ──
    rows.append([
        ("♻️ Restart GW", "estate:daemon_restart_now:gateway"),
        ("🔄 Restart Coord", "estate:restart"),
    ])

    # ── Quick actions row ──
    rows.append([
        ("📊 Status", "estate:status"),
        ("📝 Assign", "estate:code_prompt"),
        ("❓ Help", "estate:help"),
    ])

    if not any(a == pause_or_resume[1] for _l, a in rows_actions(rows)):
        rows.append([pause_or_resume])
    rows.append(nav())
    return rows


def rows_actions(rows: List[ButtonRow]) -> List[Tuple[str, str]]:
    """Flatten button rows to (label, action) pairs."""
    return [b for row in rows for b in row]


def card_headline(verdict: str, detail: str) -> str:
    """Line 0 of the mission card — Telegram's pinned banner shows only this line."""
    return f"🏠 *Otto* · *{verdict}* — {detail}"


def _inflight_section(conn, C) -> str:
    """In-flight work summary: code runs + blocked missions."""
    lines = []
    # Code runs
    try:
        code = _inflight_code(conn)
        if code:
            tid, st = code
            lines.append(f"💻 Code `{tid[:8]}` — {st}")
        else:
            # Check for any code tasks even if not the most recent
            rows = conn.execute(
                "SELECT id, status FROM tasks WHERE source='code:telegram' "
                "AND status IN ('open','diagnosed','executing','verifying','awaiting_approval') "
                "ORDER BY created_at DESC LIMIT 3"
            ).fetchall()
            if rows:
                for r in rows[:2]:
                    rid = r["id"] if hasattr(r, "keys") else r[0]
                    st = r["status"] if hasattr(r, "keys") else r[1]
                    lines.append(f"💻 Code `{str(rid)[:8]}` — {st}")
    except Exception:
        pass
    # Blocked missions
    try:
        blocked = _blocked_missions(conn)
        if blocked:
            lines.append(f"🚀 {blocked} mission(s) blocked")
    except Exception:
        pass
    if not lines:
        return "💤 No in-flight work"
    return "\n".join(lines)


def _render_unavailable_card() -> Tuple[str, bool, List[ButtonRow]]:
    """Minimal card when the estate coordinator is unavailable."""
    from datetime import datetime, timezone

    edit_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "🏠 *Otto* · *🔴 UNKNOWN* — estate unavailable",
        "",
        "⚠️ Estate bridge down — coordinator not reachable.",
        "Gateway chat still works.",
        "",
        "*⚙️ In-Flight*",
        "💤 No in-flight work",
        "",
        "💻 *SDLC* — [Full pipeline](estate:sdlc)",
        f"💰 Spend `n/a`",
        f"_{edit_iso}_",
    ]
    text = "\n".join(lines)
    buttons: List[ButtonRow] = [
        [("💻 Full SDLC pipeline", "estate:sdlc")],
        [
            ("♻️ Restart GW", "estate:daemon_restart_now:gateway"),
            ("🔄 Restart Coord", "estate:restart"),
        ],
        [
            ("📊 Status", "estate:status"),
            ("📝 Assign", "estate:code_prompt"),
            ("❓ Help", "estate:help"),
        ],
        nav(),
    ]
    return text, False, buttons


def render_mission_card() -> Tuple[str, bool, List[ButtonRow]]:
    """Phone home: verdict, what to do, spend, in-flight work, SDLC, daemon controls."""
    try:
        C = _coord()
    except Exception:
        # Estate unavailable — return a minimal card with all required buttons
        return _render_unavailable_card()

    conn = C.connect()
    try:
        verdict, detail = _verdict(conn, C)
        burn = _burn_today(conn, C)
        paused = bool(C.estate_paused())
        concerns = _concerns(conn, C, f"{verdict} — {detail}")
        # Align headline with the ladder that actually drives buttons.
        if concerns and not paused and "CLEAR" in verdict:
            detail = f"{len(concerns)} need you"
            verdict = "🟡 ACT"
        elif concerns and "BLOCKED" in verdict:
            detail = f"{len(concerns)} need you"
        primary = concerns[0] if concerns else ("🚀 Fleet", "estate:fleet")
        blocker = _top_blocker(conn, C)
        inflight = _inflight_section(conn, C)
    finally:
        conn.close()

    try:
        from gateway.operator_shell.signal_engine import glance_line as se_glance, health

        _se_h = health()
        se_line = se_glance(_se_h) if str(_se_h.get("verdict")) != "ok" else ""
    except Exception:
        se_line = ""

    try:
        from gateway.operator_shell.delivery import cron_delivery_state

        cron = cron_delivery_state()
    except Exception:
        cron = {"ok": True, "label": "?", "mode": "unset"}

    from datetime import datetime, timezone

    edit_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [card_headline(verdict, detail)]
    if se_line:
        lines.append(se_line)

    if concerns:
        lines.append("")
        # One clear primary fire — full title from blocker when it matches.
        top_label = concerns[0][0]
        if blocker and blocker != "—":
            lines.append(f"👉 {blocker}")
        else:
            lines.append(f"👉 {top_label}")
        if len(concerns) > 1:
            lines.append(f"Also: {concerns[1][0]}")
        if len(concerns) > _MAX_CONCERNS:
            lines.append(f"_+{len(concerns) - _MAX_CONCERNS} more in Inbox / Run_")
    else:
        lines.append("")
        lines.append("✅ Nothing needs you.")
        lines.append("_🗺 Browse · ⚡ Actions · 💻 SDLC · ⚙️ Tune_")

    # In-flight work section
    lines.append("")
    lines.append("*⚙️ In-Flight*")
    lines.append(inflight)

    # SDLC summary line
    lines.append("")
    lines.append("💻 *SDLC* — [Full pipeline](estate:sdlc)")

    lines.append(f"💰 Spend `{burn}`")
    if not cron.get("ok"):
        lines.append("⚠️ Cron delivery unset — tap *Fix cron delivery*")

    lines.append(f"_{edit_iso}_")
    text = "\n".join(lines)
    return text, paused, mission_buttons(paused, primary, concerns)
