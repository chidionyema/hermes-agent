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

from typing import Any, Callable, Dict, List, Optional, Tuple

from gateway.operator_shell.panel_chrome import Group, compose, nav, panel_stamp

ButtonRow = List[Tuple[str, str]]


# ── Tune ────────────────────────────────────────────────────────────────────
# (label, estate action, one-line "what this does to me")
# How many recently-changed knobs get promoted onto the Tune index. Two rows of values is the
# most that fits above the group grid on a phone; a third pushes the groups off the screen,
# which trades a depth win for a scroll.
_RECENT_KNOBS = 2

_TUNE_GROUPS: List[Tuple[str, str, str]] = [
    ("⚡ Execution", "tune:exec", "which rail — sim, testnet, or real capital"),
    ("🎚 Sizing", "tune:sizing", "how big each position is allowed to get"),
    ("🛡 Safety", "tune:safety", "when the engine stops itself"),
    ("💵 Spend", "tune:spend", "daily ceilings — LLM and Prospector"),
    ("📦 Prospector", "tune:prospector", "batch size, concurrency, cadence"),
    ("🚦 Rails", "tune:rails", "when the engine throttles its own generation"),
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
    # Its own group rather than five more buttons on Prospector: that screen is throughput
    # ("how much per tick"), these are the two rails that decide whether a tick generates
    # AT ALL, and the group cap of 9 exists so a phone does not scroll.
    "rails": (
        "🚦 Rails",
        "When the engine throttles its own generation. Neither touches the moat — anything "
        "generated still faces all six checks. These decide whether it is generated at all. "
        "`backlog_cap` is a stock brake (off at 0); the grounding gate is a rate brake that "
        "suppresses generation only while live retrieval is actually degraded.",
        [
            ("backlog_cap", [
                ("🚦 cap off", "estate:pd_set:backlog_cap:0"),
                ("🚦 cap 50", "estate:pd_set:backlog_cap:50"),
                ("🚦 cap 200", "estate:pd_set:backlog_cap:200"),
            ]),
            ("grounding_gate", [
                ("🌐 gate on", "estate:pd_set:grounding_gate:on"),
                ("🌐 gate off", "estate:pd_set:grounding_gate:off"),
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


def knob_by_key(key: str) -> Optional[Tuple[str, str, List[Tuple[str, str]]]]:
    """Find a knob by its config key. Returns (group, display_label, buttons).

    The key is what the callback carries (`estate:se_set:leverage:2` -> `leverage`), while
    `_KNOBS` is indexed by display label (`caps.leverage`). Matching on the callback rather
    than on the label is deliberate: the label is presentation and may be reworded, the key
    is the contract with `_SAFE_PARAMS`.
    """
    if not key:
        return None
    needles = (f":se_set:{key}:", f":pd_set:{key}:")
    for group, (_title, _blurb, entries) in _KNOBS.items():
        for label, buttons in entries:
            if buttons and any(n in buttons[0][1] for n in needles):
                return group, label, list(buttons)
    return None


def group_for_key(key: str) -> Optional[str]:
    """Which Tune group owns a knob — so a set can land back where it was made."""
    found = knob_by_key(key)
    return found[0] if found else None


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


def _brain_label() -> str:
    """The configured model, short. `?` on any failure — never a guessed default."""
    try:
        from gateway.operator_shell.brain import current

        model, provider = current()
        return f"{model} ({provider})"
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
    # The params card already renders these two as words. Printing the raw value here instead
    # would put `0` and `True` on one screen and `off` and `on` on the other for the same two
    # knobs, and `backlog_cap = 0` reads as "capped at nothing" when 0 means the brake is OFF.
    if label == "backlog_cap":
        try:
            return "off" if int(val) == 0 else str(int(val))
        except Exception:
            return str(val)
    if label == "grounding_gate":
        return "on" if str(val).strip().lower() in ("true", "on", "1") else "off"
    return str(val)


def render_tune() -> Tuple[str, List[ButtonRow]]:
    """Index of knob groups, plus the knobs this operator actually uses.

    Grouping fixed the 28-button screen but left every knob three taps away. The recently
    changed ones are promoted here, so the knobs that carry real traffic are two — measured
    from the activity log, not guessed.
    """
    se = _se_params()
    pd = _pd_params()  # a promoted prospector knob prints its current value like any other
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

    # Recently-changed knobs, promoted to this screen. Grouping fixed the 28-button screen but
    # left EVERY knob three taps away (Tune -> group -> value); this puts the ones you actually
    # use at two, without bringing the density back. Driven by the activity log rather than by
    # a guess about which knobs matter — a knob nobody has touched stays in its group.
    rows: List[ButtonRow] = []
    recent: List[Tuple[str, List[Tuple[str, str]]]] = []
    try:
        from gateway.operator_shell.activity import recent_knob_keys

        for key in recent_knob_keys(limit=_RECENT_KNOBS):
            found = knob_by_key(key)
            if found:
                _group, label, buttons = found
                recent.append((label, buttons))
    except Exception:
        recent = []

    if recent:
        lines.append("*Recently changed*")
        for label, _buttons in recent:
            lines.append(f"• `{label}` = *{_current(label, se, pd)}*")
        lines.append("")
        rows.extend(list(b) for _label, b in recent)

    for label, _action, what in _TUNE_GROUPS:
        lines.append(f"*{label}* — {what}")
    # Where the cron briefs land is configuration too — it survives a restart, so it belongs
    # here and not on Run. The home card only offers it while it is BROKEN; this is the door
    # that stays open when it is healthy, so DM -> Topics is still a reachable change.
    lines.append(f"*🗓 Cron delivery* — where briefs land · now `{_cron_label()}`")
    # The model is configuration in exactly the same sense: it persists, it costs money, and
    # it was previously only reachable by typing `/model <name>` from memory.
    lines.append(f"*🧠 Brain* — which model thinks · now `{_brain_label()}`")
    lines += ["", "_Rail changes still require the two-screen ARM confirmation._"]

    rows += [
        [(_TUNE_GROUPS[0][0], f"estate:{_TUNE_GROUPS[0][1]}"),
         (_TUNE_GROUPS[1][0], f"estate:{_TUNE_GROUPS[1][1]}")],
        [(_TUNE_GROUPS[2][0], f"estate:{_TUNE_GROUPS[2][1]}"),
         (_TUNE_GROUPS[3][0], f"estate:{_TUNE_GROUPS[3][1]}")],
        [(_TUNE_GROUPS[4][0], f"estate:{_TUNE_GROUPS[4][1]}"),
         (_TUNE_GROUPS[5][0], f"estate:{_TUNE_GROUPS[5][1]}")],
        [("🗓 Cron delivery", "estate:setup_cron_topic")],
        [("🧠 Brain", "estate:brain")],
        nav("tune"),
    ]
    return "\n".join(lines), rows


def _apply_note(knobs: List[Tuple[str, List[Tuple[str, str]]]]) -> str:
    """How this group's changes actually take effect — read from the knobs, not assumed.

    The footer used to say "restarts the daemon" on every group. That is true only of the two
    plist knobs; a config.yaml knob is picked up at the NEXT TICK, because `code_fingerprint`
    hashes config.yaml alongside the engine sources and the daemon re-execs when it moves
    (`prospector/scheduler/run_scheduled.py:1257`). Telling an operator their `backlog_cap`
    change restarted the daemon invites them to go looking for a restart that never happened.
    """
    kinds = set()
    for _label, buttons in knobs:
        for _text, cb in buttons:
            parts = str(cb).split(":")
            if len(parts) >= 3 and parts[1] == "pd_set":
                try:
                    from gateway.operator_shell.prospector_daemon import _SAFE_PARAMS
                except Exception:
                    return "Every change is confirmed on a second screen."
                entry = _SAFE_PARAMS.get(parts[2])
                if entry:
                    kinds.add(entry[0])
    if not kinds:
        return "Every change is confirmed on a second screen and restarts the daemon."
    if kinds <= {"yaml_scalar"}:
        return (
            "Every change is confirmed on a second screen and applies at the next tick — "
            "the daemon re-execs itself when config.yaml changes."
        )
    return "Every change is confirmed on a second screen and restarts the daemon."


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
    lines += ["", f"_{_apply_note(knobs)}_"]

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

        C = _load_coordinator()
        if C is None:
            return None
        return bool(C.estate_paused())

    return _safe(_probe, default=None)  # type: ignore[return-value]


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
    """The verbs, state-aware — only the transitions that are actually available.

    Organised as labelled groups. Destinations live on Map (Atlas rooms), not here.
    """
    paused = _estate_paused()
    se_up = _se_running()
    pd_paused = _pd_paused()

    groups: List[Group] = []

    # Estate spend — its own group. It is the one control that halts everything at once.
    if paused is False:
        estate_rows = [[("⏸ Pause estate spend", "estate:pause")]]
    elif paused is True:
        estate_rows = [[("▶️ Resume estate spend", "estate:resume")]]
    else:
        # Probe failed. Offer both rather than guessing — a wrong guess here either
        # silently keeps burning or silently halts the estate.
        estate_rows = [[
            ("⏸ Pause estate spend", "estate:pause"),
            ("▶️ Resume estate spend", "estate:resume"),
        ]]
    groups.append(Group(
        "💸 Whole estate", estate_rows,
        status=_tri(paused, "`PAUSED`", "`live`"),
        note="halts every spender at once",
    ))

    # Signal engine — start and stop are mutually exclusive, restart is always valid.
    # One-tap: start/restart execute immediately (reversible). Stop still confirms.
    se_row: ButtonRow = []
    if se_up is False:
        se_row.append(("▶️ Start engine", "estate:se_start_now"))
    elif se_up is True:
        se_row.append(("⏹ Stop engine", "estate:se_stop"))
    else:
        se_row.append(("💹 Engine", "estate:signal_engine"))
    se_row.append(("♻️ Restart", "estate:se_restart_now"))
    groups.append(Group(
        "💹 Signal engine", [se_row],
        status=_tri(se_up, "`running`", "`stopped`"),
    ))

    # Prospector.
    pd_rows: List[ButtonRow] = [[("⚡️ Run Prospector now", "estate:pd_run_now:scheduler")]]
    if pd_paused is True:
        # estate:pd_unpause, NOT estate:pd_run_now — pd_run_now triggers a tick and leaves the
        # PAUSE file in place, so the daemon would go straight back to sleep. `prospector_daemon`
        # itself labels pd_unpause "▶️ Clear PAUSE" (prospector_daemon.py:459).
        pd_rows.append([
            ("▶️ Clear Prospector PAUSE", "estate:pd_unpause"),
            ("♻️ Restart", "estate:pd_restart_now:scheduler"),
        ])
    else:
        pd_rows.append([
            ("⏸ Pause Prospector", "estate:pd_pause"),
            ("♻️ Restart", "estate:pd_restart_now:scheduler"),
        ])
    groups.append(Group(
        "🔭 Prospector", pd_rows,
        status=_tri(pd_paused, "`PAUSED`", "`live`"),
    ))

    # Estate daemons — the restart verbs that used to sit two taps deep behind ⚙️ Daemons.
    # One-tap: restart/run_now all execute immediately. Stop/start still go through confirm
    # (stop is destructive; start is idempotent but confirms so the operator sees what landed).
    groups.append(Group("⚙️ Daemons", [
        [
            ("♻️ Coordinator", "estate:daemon_restart_now:coordinator"),
            ("♻️ Gateway", "estate:daemon_restart_now:gateway"),
        ],
        [
            ("▶️ Run watchdog", "estate:daemon_run_now:watchdog"),
            ("▶️ Run TIE review", "estate:daemon_run_now:tie-review"),
        ],
    ]))

    # 🖥 Host is the interesting one: `grep -rn 'estate:host"'` across every panel module
    # returns NOTHING besides this row. The host panel — 331 lines of keep-awake, power and
    # TCC controls — had no inbound button anywhere in the cockpit and was reachable only by
    # typing the right phrase. That is why the hop probe never found it.
    return compose(
        [
            "🎛 *Run* — the verbs. Nothing here is a destination.",
            "_Browse: 🗺 Map (Money · Code · Machine · Brain)._",
        ],
        groups,
        self_action="run",
    )


def _group_failures_by_root_cause(
    rows: List[Dict[str, Any]],
    failures: List[Tuple[str, int]],
) -> List[Tuple[str, List[Tuple[str, int]]]]:
    """Cluster failed actions by likely shared cause.

    Heuristic: failures within a 30-second window whose action name shares a prefix (the
    part before the first `:`) are treated as one event — typically one botched prompt
    suggestion or one round-trip from a misbehaving caller that re-fires. Each cluster
    gets a single header line; the individual labels stay as sub-bullets so nothing is
    hidden, only collapsed. Unrelated failures (different prefix, different window) stay
    alone, exactly as before.
    """
    from datetime import datetime

    def _ts(r: Dict[str, Any]) -> float:
        iso = str(r.get("iso") or "")
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    def _prefix(label: str) -> str:
        return label.split(":", 1)[0] if ":" in label else label

    failed_rows = [
        r for r in rows
        if str(r.get("status")) in ("failed", "error") or str(r.get("outcome")) == "failed"
    ]
    if not failed_rows:
        return [(lab, [(lab, n)]) for lab, n in failures]

    # Group by (prefix, 30-second bucket)
    clusters: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for r in failed_rows:
        lab = (str(r.get("action") or "?")
               + (":" + str(r.get("arg") or "") if r.get("arg") else ""))
        pref = _prefix(lab)
        bucket = int(_ts(r) // 30)
        clusters.setdefault((pref, bucket), []).append(r)

    # Map each failure label to its cluster
    out: List[Tuple[str, List[Tuple[str, int]]]] = []
    seen_labels: set = set()
    for (pref, _bucket), members in clusters.items():
        if len(members) < 2:
            continue  # single-failure clusters don't dedup
        labels_in_cluster = [
            (str(m.get("action") or "?")
             + (":" + str(m.get("arg") or "") if m.get("arg") else ""))
            for m in members
        ]
        # Take only labels that are in the failures list
        labels_in_cluster = [l for l in labels_in_cluster if l in {f[0] for f in failures}]
        if len(labels_in_cluster) < 2:
            continue
        # Header: prefix + earliest timestamp
        earliest = min((_ts(m) for m in members), default=0.0)
        earliest_iso = ""
        if earliest:
            earliest_iso = datetime.fromtimestamp(earliest).strftime("%H:%M:%S")
        out.append((
            f"{pref} cluster @ {earliest_iso}" if earliest_iso else f"{pref} cluster",
            [(l, 1) for l in labels_in_cluster],
        ))
        seen_labels.update(labels_in_cluster)

    # Add the remaining un-grouped failures in their original order
    for lab, n in failures:
        if lab not in seen_labels:
            out.append((lab, [(lab, n)]))
    return out


# How the operator asked, in the order the panel should read them: the two deliberate input
# methods first, then prose, then the rows that cannot say.
_ORIGIN_LABELS: List[Tuple[str, str]] = [
    ("button", "👆 {n} tapped"),
    ("command", "⌨️ {n} typed"),
    ("chat", "💬 {n} asked"),
    ("unknown", "❔ {n} unattributed"),
]


def _origin_line(r: Dict[str, Any]) -> str:
    """One line splitting the window by how the action was requested.

    Without this the `source` field was write-only — recorded on every row since the log
    shipped and rendered nowhere, so "typed commands are invisible" stayed true even once
    they were being recorded correctly. Buckets with no rows are omitted rather than printed
    as zeroes: a screen read on a phone should carry facts, not empty columns.
    """
    by = r.get("by_source") or {}
    if not by:
        return ""
    parts = [tmpl.format(n=by[key]) for key, tmpl in _ORIGIN_LABELS if by.get(key)]
    # An origin the cockpit has no label for is still shown — a new ingress should appear
    # here the day it is added, not be silently dropped into a bucket it does not belong to.
    known = {key for key, _ in _ORIGIN_LABELS}
    parts += [f"{name} {n}" for name, n in sorted(by.items()) if name not in known and n]
    if not parts:
        return ""
    line = " · ".join(parts)
    cached = int(r.get("served_cache") or 0)
    if cached:
        line += f" · ⚡ {cached} from cache"
    return line


def render_activity(days: int = 7) -> Tuple[str, List[ButtonRow]]:
    """What the operator actually did, and how it ended.

    Failures lead. A usage list is interesting; a list of buttons that *did not work* is the
    thing that changes what gets built next, so it prints first and prints even when empty
    (an explicit "none" is evidence; a missing section is ambiguous).
    """
    from gateway.operator_shell import activity as act

    r = act.rollup(days)
    rows_all = r["rows"]
    # Rows written by a probe or a test run, suppressed from every ranking below. Always
    # stated, never silently dropped: on the day attribution shipped, 489 of 545 rows in the
    # live file were synthetic, and a panel that quietly filtered 90% of its input while
    # printing confident totals is exactly how you come to trust a number you should not.
    synth = int(r.get("synthetic") or 0)
    synth_line = f"_+{synth} from probes/tests, not counted._" if synth else ""

    lines = [f"📜 *Activity* — last {days} days", ""]
    if not rows_all:
        lines += [
            "_No operator taps recorded yet._",
            "",
            "Recording starts at the dispatcher, so this fills up as soon as the gateway "
            "running this code serves its first tap.",
        ]
        if synth_line:
            # The distinction that makes the empty state honest: "nothing happened" and
            # "everything here was a probe" look identical without it.
            lines += ["", synth_line]
        # No standalone Run row: nav() already carries 🎛 Run, and the same action twice on
        # one screen reads as a bug.
        lines += ["", panel_stamp("activity")]
        return "\n".join(lines), [nav("activity")]

    fail_n = r["failure_total"]
    pct = (fail_n / r["total"] * 100.0) if r["total"] else 0.0
    lines.append(f"{r['total']} actions · {r['distinct']} distinct · *{fail_n} failed* ({pct:.0f}%)")
    if synth_line:
        lines.append(synth_line)
    origin_line = _origin_line(r)
    if origin_line:
        lines.append(origin_line)
    lines.append("")

    # De-dup by root cause: if several failed actions share a 30-second window with the same
    # action prefix (e.g. 6 se_set:* from one botched prompt-suggestion), collapse into one
    # row so the operator sees ONE bug, not six noisy lines.
    lines.append("*Failed*")
    if r["failures"]:
        groups = _group_failures_by_root_cause(rows_all, r["failures"])
        for group_label, items in groups:
            if len(items) == 1:
                lab, n = items[0]
                tot = dict(r["top"]).get(lab)
                lines.append(f"🔴 `{lab}` — {n}×" + (f" of {tot}" if tot else ""))
            else:
                lines.append(f"🔴 *{group_label}* — {len(items)} actions, {sum(n for _l, n in items)}× total")
                for lab, n in items[:4]:
                    lines.append(f"  ↳ `{lab}` {n}×")
                if len(items) > 4:
                    lines.append(f"  ↳ _+{len(items)-4} more_")
    else:
        lines.append("✅ none")
    lines.append("")

    lines.append("*Most used*")
    for lab, n in r["top"]:
        lines.append(f"• `{lab}` — {n}×")

    # One row per action, same rule the *Failed* section above already uses: five rows of one
    # repeated action answer "which actions are slow" with a single action. `worst of N · typ`
    # is what separates "this action is always slow" from "it hung once".
    if r["slowest"]:
        lines += ["", "*Slowest*"]
        for ms, lab, n, typ in r["slowest"]:
            if n > 1:
                lines.append(f"⏱ `{lab}` — {ms/1000:.1f}s worst of {n} · typ {typ/1000:.1f}s")
            else:
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
    lines.append("")
    lines.append(panel_stamp("activity"))
    return "\n".join(lines), buttons
