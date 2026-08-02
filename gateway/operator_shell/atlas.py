"""Atlas — four Rooms behind empty Map. Jobs, not a filing cabinet.

Spine: Now · Run · Tune · Map. Empty Map opens this atlas; typed Map stays search.
Rooms are Look + stage destinations only — verbs live on Run.
Code is an SDLC pipeline (Assign → Board → Repos → Review → Ship → Learn).
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from gateway.operator_shell.panel_chrome import (
    Group,
    VERDICT_GLYPHS,
    compose,
    panel_stamp,
)

ButtonRow = List[Tuple[str, str]]

# room id → (emoji title, one-line job)
_ROOM_META = {
    "money": ("💰 Money", "capital — engine, burn, store"),
    "code": ("💻 Code", "SDLC — assign → ship → learn"),
    "machine": ("🛠 Machine", "box — daemons, host, cron"),
    "brain": ("🧠 Brain", "meta — RSI + which model thinks"),
}

_ROOM_ORDER = ("money", "code", "machine", "brain")

_SPINE_CBS = frozenset({
    "estate:refresh",
    "estate:run",
    "estate:tune",
    "estate:find",
})


def _safe(fn: Callable[[], str], default: str = "unproven") -> str:
    try:
        return fn()
    except Exception:
        return default


def _glyph(key: str) -> str:
    return VERDICT_GLYPHS.get(key, VERDICT_GLYPHS["unproven"])


def _money_verdict() -> str:
    from gateway.operator_shell.signal_engine import health

    h = health()
    v = str(h.get("verdict") or "")
    if v in ("ok",):
        return "ok"
    if v in ("tcc_denied", "down", "stalled", "unsupervised", "not_installed"):
        return "act"
    if v:
        return "watch"
    return "unproven"


def _code_verdict() -> str:
    from gateway.operator_shell.estate import _load_coordinator

    C = _load_coordinator()
    conn = C.connect()
    try:
        from gateway.operator_shell.code_remote import find_inflight_code_runs

        inflight = find_inflight_code_runs(conn, C, limit=5)
        try:
            import flight

            blocked = sum(1 for m in flight.list_missions(conn) if m["status"] == "blocked")
        except Exception:
            blocked = 0
        if blocked:
            return "act"
        if inflight:
            return "watch"
        return "ok"
    finally:
        conn.close()


def _machine_verdict() -> str:
    from gateway.operator_shell.estate import _load_coordinator
    import time

    C = _load_coordinator()
    conn = C.connect()
    try:
        hb = C.get_meta(conn, "last_tick") if hasattr(C, "get_meta") else None
        tick_age = int(time.time() - hb["updated_at"]) if hb else None
        daemon_ok = tick_age is not None and tick_age < 200
        gateway_ok = (
            C.gateway_alive() if hasattr(C, "gateway_alive") else True
        )
        if not daemon_ok or not gateway_ok:
            return "act"
        return "ok"
    finally:
        conn.close()


def _brain_verdict() -> str:
    from gateway.operator_shell.rsi_panel import glance_line

    line = (glance_line() or "").lower()
    if not line:
        return "unproven"
    if "disarm" in line or "off" in line or "🔴" in line:
        return "watch"
    if "arm" in line or "on" in line or "🟢" in line:
        return "ok"
    return "watch"


_PROBES = {
    "money": _money_verdict,
    "code": _code_verdict,
    "machine": _machine_verdict,
    "brain": _brain_verdict,
}


def room_glyph(room_id: str) -> str:
    probe = _PROBES.get(room_id)
    if not probe:
        return _glyph("unproven")
    return _glyph(_safe(probe))


def all_room_destinations() -> List[Tuple[str, str]]:
    """Flatten every destination button Atlas/Rooms expose — orphan-guard input."""
    out: List[Tuple[str, str]] = []
    for rid in _ROOM_ORDER:
        _text, rows = render_room(rid, probes=False)
        for row in rows:
            for label, cb in row:
                if cb.startswith("estate:room:") or cb in _SPINE_CBS:
                    continue
                if cb.startswith("estate:"):
                    out.append((label, cb))
    # Atlas-level Brief
    out.append(("Brief", "estate:brief"))
    return out


def render_atlas() -> Tuple[str, List[ButtonRow]]:
    lines = [
        "🗺 *Map* — pick a room, or type a word",
        "",
    ]
    tiles: List[ButtonRow] = []
    row: ButtonRow = []
    for rid in _ROOM_ORDER:
        title, blurb = _ROOM_META[rid]
        g = room_glyph(rid)
        short = title.split(" ", 1)[1] if " " in title else title
        lines.append(f"{g} *{title}* — _{blurb}_")
        row.append((f"{g} {short}", f"estate:room:{rid}"))
        if len(row) == 2:
            tiles.append(row)
            row = []
    if row:
        tiles.append(row)

    lines.append("")
    lines.append("_Brief = sitrep · rooms = browse · type = search_")
    lines.append(panel_stamp("atlas"))
    from gateway.operator_shell.panel_chrome import LEGEND, nav, with_nav

    lines.extend(["", f"_{LEGEND}_"])
    buttons: List[ButtonRow] = list(tiles)
    buttons.append([("📋 Brief", "estate:brief")])
    buttons = with_nav(buttons, "find")
    return "\n".join(lines), buttons


def _inflight_task_buttons(limit: int = 2) -> ButtonRow:
    try:
        from gateway.operator_shell.estate import _load_coordinator
        from gateway.operator_shell.code_remote import find_inflight_code_runs

        C = _load_coordinator()
        conn = C.connect()
        try:
            rows = find_inflight_code_runs(conn, C, limit=limit)
        finally:
            conn.close()
        out: ButtonRow = []
        for r in rows:
            tid = str(r["id"] if hasattr(r, "keys") else r[0])
            st = str(r["status"] if hasattr(r, "keys") else "")
            label = f"💻 {tid[:8]}"
            if st:
                label = f"💻 {tid[:8]} · {st[:8]}"
            out.append((label[:28], f"estate:task:{tid[:8]}"))
        return out
    except Exception:
        return []


def _inbox_waiting_line() -> str:
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        conn = C.connect()
        try:
            dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
            n = len(dec)
            if n:
                return f"📥 {n} waiting in Inbox — fences land on *Now* first"
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def render_code_prompt() -> Tuple[str, List[ButtonRow]]:
    """Assign entry — Telegram cannot collect free text on a button, so teach the reply."""
    from gateway.operator_shell.panel_chrome import nav, with_nav

    lines = [
        "✍️ *Assign a coding run*",
        "",
        "Reply in this chat with one of:",
        "• `cc <what to build>`",
        "• `Otto code <what to build>`",
        "• `assign <what to build>`",
        "",
        "_Money/identity fences escalate to Now before tools run._",
        "",
        panel_stamp("code_prompt"),
    ]
    inflight = _inflight_task_buttons(limit=2)
    buttons: List[ButtonRow] = []
    if inflight:
        buttons.append(inflight)
    buttons.append([("💻 Code room", "estate:room:code")])
    buttons = with_nav(buttons, "code_prompt")
    return "\n".join(lines), buttons


def render_room(room_id: str, probes: bool = True) -> Tuple[str, List[ButtonRow]]:
    rid = (room_id or "").strip().lower()
    if rid not in _ROOM_META:
        return render_atlas()

    title, blurb = _ROOM_META[rid]
    glyph = room_glyph(rid) if probes else "⚪"

    if rid == "code":
        return _render_code_room(glyph, title, blurb)

    if rid == "money":
        groups = [
            Group("👁 Look", [
                [("💹 Engine", "estate:signal_engine"), ("⛽ Fuel", "estate:system_fuel")],
                [("🔭 Prospector", "estate:prospector_daemon"), ("💵 Spend", "estate:tune:spend")],
                [
                    ("🛒 Store", "estate:st_status"),
                    ("🩺 Health", "estate:st_health"),
                    ("📦 Reconcile", "estate:st_reconcile"),
                ],
            ], note="verbs for these live on Run"),
        ]
        return compose(
            [f"{glyph} *{title}*", f"_{blurb}_", "", panel_stamp(f"room:{rid}")],
            groups,
            self_action=f"room:{rid}",
        )

    if rid == "machine":
        groups = [
            Group("👁 Look", [
                [("📊 Status", "estate:status"), ("⚙️ Daemons", "estate:daemons")],
                [("🖥 Host", "estate:host"), ("🗓 Cron", "estate:pd_cron")],
                [("📜 Activity", "estate:activity")],
            ], note="restarts live on Run"),
        ]
        return compose(
            [f"{glyph} *{title}*", f"_{blurb}_", "", panel_stamp(f"room:{rid}")],
            groups,
            self_action=f"room:{rid}",
        )

    # brain — Look only; arm/disarm live on the RSI panel itself
    groups = [
        Group("👁 Look", [
            [("🧠 RSI", "estate:rsi"), ("🎛 Brain", "estate:brain")],
        ]),
    ]
    return compose(
        [f"{glyph} *{title}*", f"_{blurb}_", "", panel_stamp(f"room:{rid}")],
        groups,
        self_action=f"room:{rid}",
    )


def _render_code_room(glyph: str, title: str, blurb: str) -> Tuple[str, List[ButtonRow]]:
    """SDLC pipeline — stages in the body, buttons under each stage label."""
    waiting = _inbox_waiting_line()
    header = [
        f"{glyph} *{title}* — SDLC",
        f"_{blurb}_",
    ]
    if waiting:
        header.append(waiting)
    header.append("")
    header.append(
        "*Assign* → *Board* → *Repos* → *Review* → *Ship* → *Learn*"
    )
    header.append("")
    header.append(panel_stamp("room:code"))

    assign_row: ButtonRow = [("✍️ Assign", "estate:code_prompt")]
    assign_row.extend(_inflight_task_buttons(limit=2))
    # One row of up to 3: Assign + up to 2 inflight
    groups: List[Group] = [
        Group("1️⃣ Assign", [assign_row[:3]], note="or type `cc <task>`"),
    ]
    groups.append(Group("2️⃣ Board", [[("📋 Missions", "estate:missions")]]))
    groups.append(Group("3️⃣ Repos", [[("🚀 Fleet", "estate:fleet")]]))
    groups.append(Group("4️⃣ Review", [[("📸 Diff", "estate:diff")]]))
    groups.append(Group(
        "5️⃣ Ship",
        [[("🏗 CI", "estate:builds"), ("🛒 Store", "estate:st_status")]],
    ))
    groups.append(Group("6️⃣ Learn", [[("🧠 RSI", "estate:rsi"), ("📥 Inbox", "estate:inbox")]]))

    return compose(header, groups, self_action="room:code")
