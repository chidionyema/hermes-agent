"""Simple help card — what Otto can do, with the one word to trigger each.

This is the user-facing directory, not the operator cockpit. Every entry on this
card has been end-to-end verified. The card is deliberately short — it fits on
one phone screen without scrolling.
"""

from __future__ import annotations

from typing import List, Tuple

from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav

ButtonRow = List[Tuple[str, str]]


def render_help() -> Tuple[str, List[ButtonRow]]:
    lines = [
        "🤖 *Otto* — your AI agent, live on Telegram",
        "",
        "*Just talk to me.* I answer questions, run code, search the web, manage files, and work through multi-step tasks. Everything below is optional shortcuts — you never need them.",
        "",
        "—— *quick actions* ——",
        "",
        "🔧 `stuck` — restart me if I'm unresponsive",
        "📊 `status` — estate health at a glance",
        "🗺 `rooms` — browse everything by category",
        "🔎 `find <word>` — search for a specific action",
        "🎛 `run` — all one-tap buttons (engine, daemons, prospector)",
        "⚙️ `tune` — settings and knobs",
        "",
        "—— *slash commands* ——",
        "",
        "`/restart` — restart gateway",
        "`/stop` — kill a stuck session",
        "`/new` — start a fresh conversation",
        "`/status` — session info",
        "",
        panel_stamp("help"),
    ]

    buttons: List[ButtonRow] = [
        [
            ("🔧 Restart me", "estate:daemon_restart_now:gateway"),
            ("📊 Status", "estate:status"),
        ],
        [
            ("🗺 Browse all", "estate:find"),
            ("🎛 Actions", "estate:run"),
        ],
        [
            ("⚙️ Tune", "estate:tune"),
            ("🔎 Find", "estate:find"),
        ],
    ]
    buttons = with_nav(buttons, "help")
    return "\n".join(lines), buttons
