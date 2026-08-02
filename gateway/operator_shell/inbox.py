"""Decision inbox — phone-native: money fences first, unmistakable APPROVE."""

from __future__ import annotations

import logging
from typing import List, Tuple

from gateway.operator_shell.panel_chrome import nav, panel_stamp

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]


def render_inbox() -> Tuple[str, List[ButtonRow]]:
    from gateway.operator_shell.estate import _load_coordinator

    C = _load_coordinator()
    conn = C.connect()
    try:
        rows = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        # Also surface money/identity awaiting_approval even if ranking quirks
        try:
            extra = conn.execute(
                "SELECT * FROM tasks WHERE status='awaiting_approval' "
                "AND risk_class IN ('money','identity','contract') "
                "ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
            seen = {r["id"] for r in rows}
            for e in extra:
                if e["id"] not in seen:
                    rows.append(e)
        except Exception:
            pass
        # Blocked missions (quota / escalate) — one line each
        blocked_missions = []
        try:
            import flight

            for m in flight.list_missions(conn):
                if m["status"] == "blocked":
                    blocked_missions.append(m)
        except Exception:
            pass
    finally:
        conn.close()

    buttons: List[ButtonRow] = []
    lines: List[str] = []

    if not rows and not blocked_missions:
        return (
            "📥 *Inbox* — clear\n\nNothing needs you.\n\n" + panel_stamp("inbox"),
            [
                [("🧠 RSI", "estate:rsi"), ("🚀 Fleet", "estate:fleet")],
                nav("inbox"),
            ],
        )

    money = [d for d in rows if (d["risk_class"] or "").lower() in ("money", "identity", "contract")
             and d["status"] == "awaiting_approval"]
    other = [d for d in rows if d not in money]

    if money:
        lines.append(f"💰 *MONEY/IDENTITY FENCE* — `{len(money)}` need APPROVE")
        lines.append("_No auto-run. Tap ✅ only when you mean it._")
        lines.append("")
        for d in money[:6]:
            short = d["id"][:8]
            risk = (d["risk_class"] or "").upper()
            # P1-3: drop the `[:40]` clip — the operator cannot tell a
            # `MONEY FENCE` blocker from a routine one when the title is cut.
            # The `👁` button still exists for the rest of the row.
            lines.append(f"⏸ `{short}` [{risk}] {d['title']}")
            buttons.append(
                [
                    (f"✅ APPROVE {short}", f"estate:approve:{short}"),
                    (f"👁 {short}", f"estate:inspect:{short}"),
                ]
            )
        lines.append("")

    if other:
        lines.append(f"📥 *Also* — `{len(other)}`")
        for d in other[:6]:
            short = d["id"][:8]
            tag = "⏸ APPROVE" if d["status"] == "awaiting_approval" else "🔴 BLOCKED"
            # P1-3: drop the `[:44]` clip — see money-section above
            lines.append(f"{tag} `{short}` {d['title']}")
            if d["status"] == "awaiting_approval":
                buttons.append(
                    [
                        (f"✅ {short}", f"estate:approve:{short}"),
                        (f"👁 {short}", f"estate:inspect:{short}"),
                    ]
                )
            else:
                buttons.append([(f"👁 {short}", f"estate:inspect:{short}")])
        lines.append("")

    if blocked_missions:
        lines.append(f"🚀 *Missions blocked* — `{len(blocked_missions)}`")
        for m in blocked_missions[:3]:
            mid = str(m["id"] if hasattr(m, "keys") else m[0])[:8]
            name = m["name"] if hasattr(m, "keys") else (m[1] if len(m) > 1 else "?")
            lines.append(f"🔴 `{mid}` {str(name)[:40]}")
            buttons.append([(f"👁 Mission {mid}", "estate:missions")])
        lines.append("_Usually Claude quota — see mission card blocker._")
        lines.append("")

    lines.append(panel_stamp("inbox"))
    buttons.append([("🧠 RSI", "estate:rsi"), ("🚀 Fleet", "estate:fleet")])
    buttons.append(nav("inbox"))
    return "\n".join(lines).strip(), buttons
