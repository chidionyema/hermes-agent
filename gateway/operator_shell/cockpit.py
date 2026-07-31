"""Run and Tune — the two surfaces that pull verbs and knobs out of the read panels.

Measured problem this solves (BFS over the real button graph, 83 reachable destinations):

    1 tap:   9
    2 taps: 28
    3 taps: 45      <- 45 of 83, and *every one of them* is a parameter setter

Configuration outnumbered everything the operator actually does, 45 to ~10, and it won the
real estate: `se_params` rendered 28 buttons on one phone screen. Yet that same screen was
INCOMPLETE — 6 of the 29 allowlisted values had no button:

    per_instrument  0.05, 0.1, 0.2   (an entire risk cap, unreachable from the phone)
    stop_loss       0                (cannot disable the stop from the cockpit)
    llm_cap         2, 5

Density and coverage failing simultaneously is the signature of a wrong container, not of
too many features. So the knobs move here, grouped by *what they do to you* rather than by
which daemon owns them — sizing and safety are separated because they fail differently, and
spend is one screen because a spend ceiling is a spend ceiling whoever is burning it.

Result: every knob is 2 taps from home instead of 3, no group exceeds 9 knob buttons, and
all 29 allowlisted values are reachable. Nothing was removed to get there.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from gateway.operator_shell.panel_chrome import nav

ButtonRow = List[Tuple[str, str]]


# ── Tune ────────────────────────────────────────────────────────────────────
# (label, estate action, one-line "what this does to me")
_TUNE_GROUPS: List[Tuple[str, str, str]] = [
    ("⚡ Execution", "tune:exec", "which rail — sim, testnet, or real capital"),
    ("🎚 Sizing", "tune:sizing", "how big each position is allowed to get"),
    ("🛡 Safety", "tune:safety", "when the engine stops itself"),
    ("💵 Spend", "tune:spend", "daily ceilings — LLM and Prospector"),
    ("📦 Prospector", "tune:prospector", "batch size, concurrency, cadence"),
]

# Every allowlisted value, including the 6 that had no button before this module.
# Grouped by consequence. Keep each group <= 9 buttons so it fits a phone without scroll.
_KNOBS: Dict[str, Tuple[str, str, List[Tuple[str, ButtonRow]]]] = {
    "exec": (
        "⚡ Execution",
        "Which rail the Signal Engine trades on. Both of these route through the two-screen "
        "ARM confirmation — this panel cannot move capital by itself.",
        [
            ("execution.mode", [
                ("🧪 sim", "estate:se_set:exec_mode:internal_sim"),
                ("🧫 testnet", "estate:se_set:exec_mode:testnet"),
                ("🔴 LIVE", "estate:se_set:exec_mode:live"),
            ]),
            ("ramp.stage", [
                ("📄 paper", "estate:se_set:ramp_stage:paper_forward"),
                ("🪙 tiny_real", "estate:se_set:ramp_stage:tiny_real"),
                ("📈 scaled", "estate:se_set:ramp_stage:scaled"),
            ]),
            ("live_feed", [
                ("📡 feed on", "estate:se_set:live_feed:true"),
                ("📴 feed off", "estate:se_set:live_feed:false"),
            ]),
        ],
    ),
    "sizing": (
        "🎚 Sizing",
        "How much the engine is allowed to put on. These scale exposure; they do not stop it.",
        [
            ("risk.vol_target", [
                ("vol 5%", "estate:se_set:vol_target:0.05"),
                ("vol 10%", "estate:se_set:vol_target:0.10"),
                ("vol 20%", "estate:se_set:vol_target:0.20"),
            ]),
            ("caps.leverage", [
                ("lev 1x", "estate:se_set:leverage:1"),
                ("lev 2x", "estate:se_set:leverage:2"),
                ("lev 3x", "estate:se_set:leverage:3"),
            ]),
            # No button existed for per_instrument anywhere in the cockpit before this.
            ("caps.per_instrument", [
                ("inst 5%", "estate:se_set:per_instrument:0.05"),
                ("inst 10%", "estate:se_set:per_instrument:0.1"),
                ("inst 20%", "estate:se_set:per_instrument:0.2"),
            ]),
        ],
    ),
    "safety": (
        "🛡 Safety",
        "When the engine stops itself without being asked. Raising these widens the loss "
        "the engine will absorb before it halts.",
        [
            ("caps.portfolio_dd_killswitch", [
                ("kill 5%", "estate:se_set:killswitch:0.05"),
                ("kill 10%", "estate:se_set:killswitch:0.10"),
                ("kill 15%", "estate:se_set:killswitch:0.15"),
            ]),
            ("caps.max_positions", [
                ("pos 3", "estate:se_set:max_positions:3"),
                ("pos 5", "estate:se_set:max_positions:5"),
                ("pos 10", "estate:se_set:max_positions:10"),
            ]),
            # "stop off" is allowlisted (stop_loss: 0) but had no button — the cockpit could
            # tighten the stop and never release it. Labelled ⚠️ because 0 disables it.
            ("caps.stop_loss_pct", [
                ("⚠️ stop off", "estate:se_set:stop_loss:0"),
                ("stop 5%", "estate:se_set:stop_loss:0.05"),
                ("stop 10%", "estate:se_set:stop_loss:0.10"),
            ]),
        ],
    ),
    "spend": (
        "💵 Spend",
        "Daily ceilings. Prospector's cap is the automated liability backstop — it is what "
        "makes unattended generation permissible, so lowering it is always safe.",
        [
            # llm_cap allowed ("1","2","5"); only $1 had a button.
            ("signal engine llm.daily_cap_usd", [
                ("llm $1", "estate:se_set:llm_cap:1"),
                ("llm $2", "estate:se_set:llm_cap:2"),
                ("llm $5", "estate:se_set:llm_cap:5"),
            ]),
            ("prospector spend.daily_cap_usd", [
                ("💵 cap $10", "estate:pd_set:daily_cap:10"),
                ("💵 cap $20", "estate:pd_set:daily_cap:20"),
                ("💵 cap $40", "estate:pd_set:daily_cap:40"),
            ]),
        ],
    ),
    "prospector": (
        "📦 Prospector",
        "Throughput and cadence. None of these touch the moat — verification always runs "
        "the full six checks regardless of what is set here.",
        [
            ("batch_size", [
                ("📦 batch 3", "estate:pd_set:batch_size:3"),
                ("📦 batch 5", "estate:pd_set:batch_size:5"),
                ("📦 batch 10", "estate:pd_set:batch_size:10"),
            ]),
            ("concurrency", [
                ("⚡ conc 2", "estate:pd_set:concurrency:2"),
                ("⚡ conc 4", "estate:pd_set:concurrency:4"),
                ("⚡ conc 8", "estate:pd_set:concurrency:8"),
            ]),
            ("interval", [
                ("⏱ 1h", "estate:pd_set:interval:3600"),
                ("⏱ 2h", "estate:pd_set:interval:7200"),
                ("⏱ 4h", "estate:pd_set:interval:14400"),
            ]),
        ],
    ),
}

# Which live value to print beside each knob row. Read is best-effort: a knob whose current
# value cannot be read still renders its buttons, it just shows `?` — never a blank line and
# never a missing control.
_SE_KEYS = {
    "execution.mode": "exec_mode",
    "ramp.stage": "ramp_stage",
    "live_feed": "live_feed",
    "risk.vol_target": "vol_target",
    "caps.leverage": "leverage",
    "caps.per_instrument": "per_instrument",
    "caps.portfolio_dd_killswitch": "killswitch",
    "caps.max_positions": "max_positions",
    "caps.stop_loss_pct": "stop_loss",
    "signal engine llm.daily_cap_usd": "llm_cap",
}

# prospector_daemon.read_params() names the interval `interval_s` and the cap
# `daily_cap_usd` — the knob labels above are the config paths, so they need mapping.
_PD_KEYS = {
    "prospector spend.daily_cap_usd": "daily_cap_usd",
    "batch_size": "batch_size",
    "concurrency": "concurrency",
    "interval": "interval_s",
}


def _se_params() -> Dict[str, object]:
    try:
        from gateway.operator_shell.signal_engine import read_params

        return read_params() or {}
    except Exception:
        return {}


def _cron_label() -> str:
    """Where cron briefs land, as the delivery module itself reports it.

    `?` when the probe fails — never a guess. The env var is only the fallback because
    `~/.hermes/.env` is loaded lazily: read at import time it says UNSET, read during a
    render it says `main DM (ok)`, and only the second one is what the operator has.
    """
    try:
        from gateway.operator_shell.delivery import cron_delivery_state

        return str(cron_delivery_state().get("label") or "?")
    except Exception:
        return "?"


def _pd_params() -> Dict[str, object]:
    try:
        from gateway.operator_shell.prospector_daemon import read_params

        return read_params() or {}
    except Exception:
        return {}


def _current(label: str, se: Dict[str, object], pd: Dict[str, object]) -> str:
    key = _SE_KEYS.get(label)
    if key:
        val = se.get(key)
        return str(val) if val not in (None, "") else "?"
    val = pd.get(_PD_KEYS.get(label, label))
    if val in (None, ""):
        return "?"
    if label == "interval":  # stored in seconds; nobody thinks in seconds
        try:
            return f"{int(val) // 3600}h"
        except Exception:
            return str(val)
    return str(val)


def render_tune() -> Tuple[str, List[ButtonRow]]:
    """Index of knob groups. Two taps to any of the 29 values, down from three."""
    se = _se_params()
    armed = False
    try:
        from gateway.operator_shell.signal_engine import is_armed

        armed = bool(is_armed(se))
    except Exception:
        pass

    lines = ["⚙️ *Tune* — configuration only. Nothing here starts or stops anything.", ""]
    if armed:
        lines.append("🔴 *ARMED* — `exec_mode`/`ramp_stage` say real capital can move.")
    else:
        lines.append("🧪 *Paper* — nothing real moves at the current rail.")
    lines.append("")
    for label, _action, what in _TUNE_GROUPS:
        lines.append(f"*{label}* — {what}")
    # Where the cron briefs land is configuration too — it survives a restart, so it belongs
    # here and not on Run. The home card only offers it while it is BROKEN; this is the door
    # that stays open when it is healthy, so DM -> Topics is still a reachable change.
    lines.append(f"*🗓 Cron delivery* — where briefs land · now `{_cron_label()}`")
    lines += ["", "_Rail changes still require the two-screen ARM confirmation._"]

    rows: List[ButtonRow] = [
        [(_TUNE_GROUPS[0][0], f"estate:{_TUNE_GROUPS[0][1]}"),
         (_TUNE_GROUPS[1][0], f"estate:{_TUNE_GROUPS[1][1]}")],
        [(_TUNE_GROUPS[2][0], f"estate:{_TUNE_GROUPS[2][1]}"),
         (_TUNE_GROUPS[3][0], f"estate:{_TUNE_GROUPS[3][1]}")],
        [(_TUNE_GROUPS[4][0], f"estate:{_TUNE_GROUPS[4][1]}"),
         ("🗓 Cron delivery", "estate:setup_cron_topic")],
        nav("tune"),
    ]
    return "\n".join(lines), rows


def render_tune_group(group: str) -> Tuple[str, List[ButtonRow]]:
    """One knob group. Current value is printed above the buttons that change it."""
    entry = _KNOBS.get(group)
    if not entry:
        return ("⚙️ *Tune* — unknown group.", [nav("tune")])
    title, blurb, knobs = entry
    se, pd = _se_params(), _pd_params()

    lines = [f"⚙️ *{title}*", "", blurb, ""]
    rows: List[ButtonRow] = []
    for label, buttons in knobs:
        lines.append(f"• `{label}` = *{_current(label, se, pd)}*")
        rows.append(list(buttons))
    lines += ["", "_Every change is confirmed on a second screen and restarts the daemon._"]

    # No "All knobs" row — the spine's ⚙️ Tune already goes there, and the same callback twice
    # on one screen is the defect, not a convenience.
    rows.append(nav(f"tune:{group}"))
    return "\n".join(lines), rows


# ── Run ─────────────────────────────────────────────────────────────────────
# The verbs. Each entry is (label, action, predicate) — the predicate decides whether the
# button is offered at all, so the panel never shows "▶️ Start" for something already
# running or "⏸ Pause" for something already paused. A button that cannot do anything is
# worse than a missing one: it teaches the operator that taps are unreliable.
def _safe(fn: Callable[[], object], default: object = None) -> object:
    try:
        return fn()
    except Exception:
        return default


def _se_running() -> Optional[bool]:
    def _probe() -> Optional[bool]:
        from gateway.operator_shell.signal_engine import daemon_pid

        return daemon_pid() is not None

    return _safe(_probe)  # type: ignore[return-value]


def _estate_paused() -> Optional[bool]:
    def _probe() -> Optional[bool]:
        from gateway.operator_shell.estate import _load_coordinator

        return bool(_load_coordinator().estate_paused())

    return _safe(_probe)  # type: ignore[return-value]


def _pd_paused() -> Optional[bool]:
    def _probe() -> Optional[bool]:
        from gateway.operator_shell.prospector_daemon import read_params

        val = (read_params() or {}).get("paused")
        return None if val is None else bool(val)

    return _safe(_probe)  # type: ignore[return-value]


def _tri(state: Optional[bool], yes: str, no: str) -> str:
    """Render a probe that is allowed to fail. Unknown is printed as unknown, never as OK."""
    if state is None:
        return "`?`"
    return yes if state else no


def render_run() -> Tuple[str, List[ButtonRow]]:
    """The verbs, state-aware — only the transitions that are actually available."""
    paused = _estate_paused()
    se_up = _se_running()
    pd_paused = _pd_paused()

    lines = [
        "🎛 *Run* — the actions. Nothing here changes configuration.",
        "",
        f"💸 estate spend {_tri(paused, '`PAUSED`', '`live`')}",
        f"💹 signal engine {_tri(se_up, '`running`', '`stopped`')}",
        f"🔭 prospector {_tri(pd_paused, '`PAUSED`', '`live`')}",
        "",
    ]

    rows: List[ButtonRow] = []

    # Estate spend — its own row. It is the one control that halts everything at once.
    if paused is False:
        rows.append([("⏸ Pause all spend", "estate:pause")])
    elif paused is True:
        rows.append([("▶️ Resume all spend", "estate:resume")])
    else:
        # Probe failed. Offer both rather than guessing — a wrong guess here either
        # silently keeps burning or silently halts the estate.
        rows.append([("⏸ Pause all", "estate:pause"), ("▶️ Resume all", "estate:resume")])

    # Signal engine — start and stop are mutually exclusive, restart is always valid.
    se_row: ButtonRow = []
    if se_up is False:
        se_row.append(("▶️ Start engine", "estate:se_start"))
    elif se_up is True:
        se_row.append(("⏹ Stop engine", "estate:se_stop"))
    else:
        se_row.append(("💹 Engine", "estate:signal_engine"))
    se_row.append(("♻️ Restart", "estate:se_restart"))
    rows.append(se_row)

    # Prospector.
    pd_row: ButtonRow = [("⚡️ Run Prospector now", "estate:run_prospector")]
    rows.append(pd_row)
    if pd_paused is True:
        # estate:pd_unpause, NOT estate:pd_run_now — pd_run_now triggers a tick and leaves the
        # PAUSE file in place, so the daemon would go straight back to sleep. `prospector_daemon`
        # itself labels pd_unpause "▶️ Clear PAUSE" (prospector_daemon.py:459).
        rows.append([("▶️ Clear PAUSE", "estate:pd_unpause"), ("♻️ Restart", "estate:pd_restart:scheduler")])
    else:
        rows.append([("⏸ Arm PAUSE", "estate:pd_pause"), ("♻️ Restart", "estate:pd_restart:scheduler")])

    # Estate daemons — the restart verbs that used to sit two taps deep behind ⚙️ Daemons.
    rows.append([
        ("♻️ Coordinator", "estate:daemon_restart:coordinator"),
        ("♻️ Gateway", "estate:daemon_restart:gateway"),
    ])
    rows.append([
        ("▶️ Run watchdog", "estate:daemon_run_now:watchdog"),
        ("▶️ Run TIE review", "estate:daemon_run_now:tie-review"),
    ])
    # ⛽ Fuel and 📊 Status move off the home card to make room for the concern rows; they are
    # one tap from here instead. 🖥 Host is the interesting one: `grep -rn 'estate:host"'`
    # across every panel module returns NOTHING. The host panel — 331 lines of keep-awake,
    # power and TCC controls — had no inbound button anywhere in the cockpit and was reachable
    # only by typing the right phrase. That is why the hop probe never found it.
    rows.append([
        ("⚙️ All daemons", "estate:daemons"),
        ("📊 Status", "estate:status"),
    ])
    rows.append([
        ("⛽ Fuel / CB", "estate:system_fuel"),
        ("🖥 Host", "estate:host"),
    ])
    rows.append([("📜 Activity", "estate:activity")])
    rows.append(nav("run"))
    return "\n".join(lines), rows


def render_activity(days: int = 7) -> Tuple[str, List[ButtonRow]]:
    """What the operator actually did, and how it ended.

    Failures lead. A usage list is interesting; a list of buttons that *did not work* is the
    thing that changes what gets built next, so it prints first and prints even when empty
    (an explicit "none" is evidence; a missing section is ambiguous).
    """
    from gateway.operator_shell import activity as act

    r = act.rollup(days)
    rows_all = r["rows"]

    lines = [f"📜 *Activity* — last {days} days", ""]
    if not rows_all:
        lines += [
            "_Nothing recorded yet._",
            "",
            "Recording starts at the dispatcher, so this fills up as soon as the gateway "
            "running this code serves its first tap.",
        ]
        # No standalone Run row: nav() already carries 🎛 Run, and the same action twice on
        # one screen reads as a bug.
        return "\n".join(lines), [nav("activity")]

    fail_n = r["failure_total"]
    pct = (fail_n / r["total"] * 100.0) if r["total"] else 0.0
    lines.append(f"{r['total']} actions · {r['distinct']} distinct · *{fail_n} failed* ({pct:.0f}%)")
    lines.append("")

    lines.append("*Failed*")
    if r["failures"]:
        for lab, n in r["failures"]:
            tot = dict(r["top"]).get(lab)
            lines.append(f"🔴 `{lab}` — {n}×" + (f" of {tot}" if tot else ""))
    else:
        lines.append("✅ none")
    lines.append("")

    lines.append("*Most used*")
    for lab, n in r["top"]:
        lines.append(f"• `{lab}` — {n}×")

    if r["slowest"]:
        lines += ["", "*Slowest*"]
        for ms, lab in r["slowest"]:
            lines.append(f"⏱ `{lab}` — {ms/1000:.1f}s")

    lines += ["", "*Last 8*"]
    for row in rows_all[-8:][::-1]:
        stamp = str(row.get("iso") or "")[5:16].replace("T", " ")
        lab = f"{row.get('action')}:{row.get('arg')}" if row.get("arg") else str(row.get("action"))
        mark = "🔴" if (row.get("status") in ("failed", "error") or row.get("outcome") == "failed") else "·"
        lines.append(f"{mark} `{stamp}` {lab}")

    # Offer the windows you are NOT in. The current one is a no-op tap, and it would collide
    # with nav's 🔄 (same callback), which is the duplicate-button defect this cockpit
    # already fixed once on the home card.
    windows: ButtonRow = [
        (f"📅 {lab}", f"estate:activity:{n}")
        for n, lab in ((1, "24h"), (7, "7d"), (30, "30d"))
        if n != days
    ]
    buttons: List[ButtonRow] = [windows] if windows else []
    buttons.append(nav(f"activity:{days}"))
    return "\n".join(lines), buttons
