"""CEO natural language → structured estate actions (no freeform guessing).

Keep patterns SHORT and anchored. Long tasking ("rewrite prospector…") must
return None so Otto inject / agent can run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class NaturalOp:
    action: str  # estate action (or action:arg)
    args: str = ""
    proof_label: str = ""


# Order matters: first match wins. Prefer specific (approve id) before broad (status).
_PATTERNS = [
    # Noise → mission card (same contract as otto CEO free-chat)
    (re.compile(
        r"^\s*(ok|okay|k|kk|hi|hey|hello|yo|sup|thanks|thank you|thx|ty|"
        r"👍|👌|\.|…+|hmm+|yep|yeah|cool|nice)\s*$", re.I),
     "refresh", "", "Mission card"),
    # Spend / estate power
    (re.compile(
        r"^\s*(pause\s+(all\s+)?spend|pause\s+estate|freeze\s+spend|"
        r"pause\s+everything|stop\s+spending)\s*$", re.I),
     "pause", "", "Pause spend"),
    (re.compile(
        r"^\s*(resume(\s+(spend|estate|everything))?|unfreeze|unpause|go\s+live)\s*$",
        re.I),
     "resume", "", "Resume spend"),
    # Mission / status / sitrep
    # Spine: Now / Run / Tune — bare one-word navigation (the panel_chrome nav bar)
    (re.compile(
        r"^\s*(now|home|dashboard|main)\s*\??\s*$", re.I),
     "refresh", "", "Mission card (Now)"),
    (re.compile(
        r"^\s*(run|execute|actions?)\s*\??\s*$", re.I),
     "run", "", "Run — the verb panel"),
    (re.compile(
        r"^\s*(tune|tuning|settings?|config|configure|knobs?|parameters?)\s*\??\s*$", re.I),
     "tune", "", "Tune — the 29-knob panel"),
    # Estate status summary — the discoverable "status" door (P1-4). Bare `status`
    # must NOT land on the mission card; that card is `now` / `mission` / `panel`.
    (re.compile(
        r"^\s*(status|estate\s+status|status\s+summary|"
        r"how'?s\s+the\s+(estate|ship|system)|"
        r"estate\s+overview)\s*\??\s*$", re.I),
     "status", "", "Estate status"),
    (re.compile(
        r"^\s*(what'?s\s+on\s+fire|on\s+fire|mission|cockpit|panel|"
        r"health|are\s+we\s+(ok|good|clear)|all\s+good|everything\s+ok)\s*\??\s*$",
        re.I),
     "refresh", "", "Mission card"),
    (re.compile(
        r"^\s*(brief|briefing|sitrep|sit[- ]?rep|rundown|catch\s+me\s+up|"
        r"fill\s+me\s+in|update\s+me|what'?s\s+going\s+on|whats\s+going\s+on|"
        r"how'?s\s+it\s+going|how\s+are\s+we|how\s+are\s+things|"
        r"executive\s+brief|summary)\s*\??\s*$", re.I),
     "brief", "", "Executive brief"),
    # Inbox / decisions
    (re.compile(
        r"^\s*(inbox|decisions?|approvals?|what\s+needs\s+me|"
        r"needs\s+(my|your)\s+(call|approval)|waiting\s+on\s+me|"
        r"what'?s\s+blocked|whats\s+blocked)\s*\??\s*$", re.I),
     "inbox", "", "Inbox"),
    # Approve short id
    (re.compile(r"^\s*approve\s+`?([0-9a-fA-F]{4,12})`?\s*$", re.I),
     "approve", "{g1}", "Approve"),
    # Fleet / missions
    (re.compile(
        r"^\s*(fleet|projects?|portfolio)\s*\??\s*$", re.I),
     "fleet", "", "Fleet"),
    (re.compile(
        r"^\s*(builds?|ci|cicd|ci\/?cd|deploys?|ship\s+status|"
        r"github\s+actions?|deploy\s+status)\s*\??\s*$", re.I),
     "builds", "", "Builds"),
    (re.compile(
        r"^\s*(missions?|mission\s+board|autopilot)\s*\??\s*$", re.I),
     "missions", "", "Missions"),
    # RSI / learning
    (re.compile(
        r"^\s*(rsi|learning|self[-\s]?improv\w*|are\s+you\s+learning|"
        r"are\s+you\s+improving|how\s+are\s+you\s+improving|"
        r"self[-\s]?improv\w*\s+status|rsi\s+status)\s*\??\s*$", re.I),
     "rsi", "", "RSI"),
    (re.compile(
        r"^\s*(arm\s+(self[-\s]?improv\w*|learning|rsi)|"
        r"enable\s+(self[-\s]?improv\w*|learning|rsi))\s*$", re.I),
     "arm_learning", "", "Arm learning"),
    (re.compile(
        r"^\s*(disarm\s+(self[-\s]?improv\w*|learning|rsi)|"
        r"disable\s+(self[-\s]?improv\w*|learning|rsi))\s*$", re.I),
     "disarm_learning", "", "Disarm learning"),
    # Host / keep-awake / always-on (before daemons)
    (re.compile(
        r"^\s*(host|keep\s*awake|keep-awake|estate\s+online|"
        r"always\s*on|mac\s+(awake|online|status)|is\s+the\s+(mac|host)\s+"
        r"(awake|online|up))\s*\??\s*$", re.I),
     "host", "", "Host status"),
    (re.compile(
        r"^\s*(start\s+keep\s*awake|start\s+keep-awake|"
        r"enable\s+keep\s*awake|caffeinate\s+on)\s*$", re.I),
     "host_keepawake_start", "", "Start keep-awake"),
    # Estate / Prospector daemons (before generic "run prospector")
    (re.compile(
        r"^\s*(daemons?|services?|launchctl|estate\s+daemons?)\s*\??\s*$", re.I),
     "daemons", "", "Daemons"),
    (re.compile(
        r"^\s*(restart|bounce)\s+(the\s+)?gateway\s*$", re.I),
     "daemon_restart", "gateway", "Bounce gateway (confirm)"),
    (re.compile(
        r"^\s*restart\s+(the\s+)?(coord|coordinator)\s*$", re.I),
     "daemon_restart", "coordinator", "Restart coordinator"),
    (re.compile(
        r"^\s*(coord|coordinator)\s+logs?\s*$", re.I),
     "daemon_logs", "coordinator", "Coordinator logs"),
    (re.compile(
        r"^\s*(run|fire|kick)\s+(hermes\s+)?watchdog\s*(now)?\s*$", re.I),
     "daemon_run_now", "watchdog", "Run Hermes watchdog now"),
    # Store money rail (read-only; every phrase carries "store"/"buyer" so none of these can
    # collide with the bare `status` / `health` mission pulls above).
    # No pause verb here on purpose — store/scheduler/PAUSE is already owned by
    # `pause prospector`, and one switch must not have two names.
    (re.compile(
        r"^\s*(store|storefront|shop)\s*(status)?\s*\??\s*$", re.I),
     "st_status", "", "Store status"),
    (re.compile(
        r"^\s*(store\s+(health|probe|sellable)|"
        r"(is\s+the\s+)?store\s+(ok|up|working|live)|"
        r"can\s+(we|anyone|someone)\s+(take\s+money|buy|pay\s+us)|"
        r"are\s+we\s+sellable)\s*\??\s*$", re.I),
     "st_health", "", "Store health"),
    (re.compile(
        r"^\s*(store\s+)?(reconcile|orders?|deliveries|delivery\s+check|"
        r"paid[\s-]?(without|no)[\s-]?deliver\w*|"
        r"did\s+(anyone|everyone|every\s+buyer)\s+get\s+(their|it)\w*|"
        r"buyers?)\s*\??\s*$", re.I),
     "st_reconcile", "", "Paid vs delivered"),
    (re.compile(
        r"^\s*store\s+(money|money\s+paths?|payments?|proof)\s*\??\s*$", re.I),
     "st_money", "", "Money-path proof"),
    # Signal Engine / money rail (before prospector — no overlap, but keep the
    # money rail early so a typo never lands on a generation command)
    (re.compile(
        r"^\s*(signal(\s*engine)?|signalengine|money\s+rail|trading\s+(daemon|engine)|"
        r"how'?s\s+(the\s+)?(signal|trading)(\s+engine)?|equity|pnl|p&l)\s*\??\s*$", re.I),
     "signal_engine", "", "Signal Engine"),
    (re.compile(
        r"^\s*restart\s+(the\s+)?(signal(\s*engine)?|signalengine|trading)\s*$", re.I),
     "se_restart", "", "Restart Signal Engine"),
    (re.compile(
        r"^\s*start\s+(the\s+)?(signal(\s*engine)?|signalengine|trading)\s*$", re.I),
     "se_start", "", "Start Signal Engine"),
    (re.compile(
        r"^\s*stop\s+(the\s+)?(signal(\s*engine)?|signalengine|trading)\s*$", re.I),
     "se_stop", "", "Stop Signal Engine"),
    (re.compile(
        r"^\s*pause\s+(the\s+)?(signal(\s*engine)?|signalengine|trading)\s*$", re.I),
     "se_pause", "", "Pause trading"),
    (re.compile(
        r"^\s*(resume|unpause)\s+(the\s+)?(signal(\s*engine)?|signalengine|trading)\s*$",
        re.I),
     "se_resume", "", "Resume trading"),
    (re.compile(
        r"^\s*(signal(\s*engine)?|signalengine|trading)\s+(logs?|log\s*tail|errors?)\s*$",
        re.I),
     "se_logs", "", "Signal Engine logs"),
    (re.compile(
        r"^\s*(signal(\s*engine)?|signalengine|trading|risk|money)\s+"
        r"(params?|settings?|knobs?|config|risk|caps?)\s*\??\s*$", re.I),
     "se_params", "", "Signal Engine knobs"),
    # Rail phrases route to the SET flow, which always shows confirm → ARM first.
    # Saying "go live" out loud must never be the last step before real orders.
    (re.compile(
        r"^\s*(arm\s+(the\s+)?(money\s+rail|live\s+trading|real\s+money)|"
        r"go\s+live\s+trading|live\s+trading\s+on)\s*$", re.I),
     "se_set", "exec_mode:live", "Arm money rail"),
    (re.compile(
        r"^\s*(disarm|paper\s+mode|go\s+paper|stop\s+(real|live)\s+trading|"
        r"disarm\s+(the\s+)?(money\s+rail|trading))\s*$", re.I),
     "se_set", "exec_mode:internal_sim", "Back to paper"),
    (re.compile(
        r"^\s*set\s+signal\s+"
        r"(exec_mode|ramp_stage|vol_target|leverage|per_instrument|killswitch|"
        r"max_positions|stop_loss|llm_cap|live_feed)\s+([A-Za-z0-9_.]+)\s*$", re.I),
     "se_set", "{g1}:{g2}", "Set Signal Engine knob"),
    (re.compile(
        r"^\s*(prospector\s+daemons?|prospect\s+daemons?|"
        r"prospector\s+(status|health)|daemon\s+status\s+prospector|"
        r"how's\s+prospector(\s+daemon)?|how\s+is\s+prospector(\s+daemon)?)\s*\??\s*$",
        re.I),
     "prospector_daemon", "", "Prospector daemons"),
    (re.compile(
        r"^\s*restart\s+prospector(\s+daemon|\s+scheduler|\s+sched)?\s*$", re.I),
     "pd_restart", "scheduler", "Restart Prospector scheduler"),
    (re.compile(
        r"^\s*start\s+prospector(\s+daemon|\s+scheduler|\s+sched)?\s*$", re.I),
     "pd_start", "scheduler", "Start Prospector scheduler"),
    (re.compile(
        r"^\s*stop\s+prospector(\s+daemon|\s+scheduler|\s+sched)?\s*$", re.I),
     "pd_stop", "scheduler", "Stop Prospector scheduler"),
    (re.compile(
        r"^\s*(run|fire|kick)\s+prospector\s+watchdog\s*(now)?\s*$", re.I),
     "pd_run_now", "watchdog", "Run Prospector watchdog now"),
    (re.compile(
        r"^\s*restart\s+prospector\s+watchdog\s*$", re.I),
     "pd_run_now", "watchdog", "Run Prospector watchdog now"),
    (re.compile(
        r"^\s*start\s+prospector\s+watchdog\s*$", re.I),
     "pd_run_now", "watchdog", "Run Prospector watchdog now"),
    (re.compile(
        r"^\s*(stop|unload)\s+prospector\s+watchdog\s*$", re.I),
     "pd_stop", "watchdog", "Unload Prospector watchdog"),
    (re.compile(
        r"^\s*prospector\s+(logs?|log\s*tail|errors?)\s*$", re.I),
     "pd_logs", "scheduler", "Prospector logs"),
    (re.compile(
        r"^\s*prospector\s+(params?|settings?|knobs|interval|flags)\s*\??\s*$", re.I),
     "pd_params", "", "Prospector params"),
    (re.compile(
        r"^\s*prospector\s+(cron|schedule|outcomes?|ticks?)\s*\??\s*$", re.I),
     "pd_cron", "", "Prospector cron"),
    (re.compile(
        r"^\s*pause\s+prospector(\s+gen(eration)?)?\s*$", re.I),
     "pd_pause", "", "Pause Prospector gen"),
    (re.compile(
        r"^\s*(unpause|resume)\s+prospector(\s+gen(eration)?)?\s*$", re.I),
     "pd_unpause", "", "Resume Prospector gen"),
    (re.compile(
        r"^\s*set\s+prospector\s+(interval|concurrency|batch_size|daily_cap)\s+(\d+)\s*$",
        re.I),
     "pd_set", "{g1}:{g2}", "Set Prospector param"),
    # Ops
    (re.compile(r"^\s*(stop\s+(the\s+)?agent|kill\s+(the\s+)?run|halt)\s*$", re.I),
     "stop_agent", "", "Stop agent"),
    (re.compile(r"^\s*run\s+prospector(?:\s+(\d+))?\s*$", re.I),
     "run_prospector", "{g1}", "Run prospector"),
    (re.compile(r"^\s*(undo|rollback)\s*$", re.I), "undo", "", "Undo"),
    (re.compile(
        r"^\s*(budget|spend\s+today|burn|fuel)\s*\??\s*$", re.I),
     "system_fuel", "", "Fuel"),
    (re.compile(
        r"^\s*(activity|audit(\s+log)?|what\s+(did|have)\s+i\s+"
        r"(do|done|tap|tapped)|what\s+broke)\s*\??\s*$", re.I),
     "activity", "", "Activity log"),
    # Coding run status / cancel (short pulls only — long tasking goes to code_remote)
    (re.compile(
        r"^\s*(?:task|run|job|how'?s\s+(?:that\s+)?task)\s+`?([0-9a-fA-F]{4,12})`?\s*\??\s*$",
        re.I),
     "task", "{g1}", "Task status"),
    (re.compile(
        r"^\s*(?:status\s+of\s+(?:task\s+)?|how'?s\s+)\s*`?([0-9a-fA-F]{4,12})`?\s*\??\s*$",
        re.I),
     "task", "{g1}", "Task status"),
    (re.compile(
        r"^\s*cancel\s+(?:task\s+)?`?([0-9a-fA-F]{4,12})`?\s*$", re.I),
     "cancel", "{g1}", "Cancel task"),
    (re.compile(
        r"^\s*pause\s+(?:task\s+)?`?([0-9a-fA-F]{4,12})`?\s*$", re.I),
     "pause_task", "{g1}", "Pause task"),
    # Find. The answer to "I don't know where anything is" — anchored above code_assign so
    # "find the spend cap" is a lookup, not a coding task.
    (re.compile(r"^\s*(?:find|search|lookup|look\s+up)\s+(.+?)\s*\??\s*$", re.I),
     "find", "{g1}", "Map — rooms + search"),
    (re.compile(
        r"^\s*(?:where\s+(?:is|are|do\s+i\s+find)|how\s+do\s+i)\s+(.+?)\s*\??\s*$", re.I),
     "find", "{g1}", "Map — rooms + search"),
    (re.compile(r"^\s*(?:find|search|menu|help|commands?|map)\s*\??\s*$", re.I),
     "find", "", "Map — rooms + search"),
    # Atlas / Rooms — job map behind empty Find
    (re.compile(
        r"^\s*(atlas|rooms?|map|where\s+is\s+everything)\s*\??\s*$", re.I),
     "atlas", "", "Atlas"),
    (re.compile(
        r"^\s*(money\s+room|room\s+money)\s*\??\s*$", re.I),
     "room", "money", "Money room"),
    (re.compile(
        r"^\s*(code\s+room|room\s+code|sdlc|software\s+lifecycle|"
        r"code\s+estate)\s*\??\s*$", re.I),
     "room", "code", "Code room — SDLC"),
    (re.compile(
        r"^\s*(machine\s+room|room\s+machine)\s*\??\s*$", re.I),
     "room", "machine", "Machine room"),
    (re.compile(
        r"^\s*(brain\s+room|room\s+brain)\s*\??\s*$", re.I),
     "room", "brain", "Brain room"),
    # Brain / model. Anchored above code_assign because that `cc|code` prefix is broad.
    (re.compile(
        r"^\s*(?:which\s+|what\s+)?(?:model|brain|llm)"
        r"(?:\s+(?:am\s+i|are\s+we)\s+(?:on|using))?\s*\??\s*$", re.I),
     "brain", "", "Brain picker"),
    (re.compile(
        r"^\s*(?:change|switch|swap|pick|set)\s+(?:the\s+)?(?:model|brain|llm)\s*$", re.I),
     "brain", "", "Brain picker"),
    (re.compile(
        r"^\s*(?:use|switch\s+to|change\s+to|swap\s+to|run\s+on)\s+(?:the\s+)?"
        r"(opus|sonnet|haiku|deepseek|minimax)\b.*$", re.I),
     "brain_set", "{g1}", "Switch brain"),
    # Explicit assign prefix (natural assign also lives in code_remote via chat_router)
    (re.compile(
        r"^\s*(?:assign|code|cc)\s*[:\-]?\s+(.+)$", re.I | re.DOTALL),
     "code_assign", "{g1}", "Assign code"),
    # Summary Card — Pythagorean + Gematria + anagram analysis
    (re.compile(
        r"^\s*(?:summary|analyze)\s+(.+)$", re.I | re.DOTALL),
     "summary", "{g1}", "Summary card"),
]


def match_natural_op(text: str) -> Optional[NaturalOp]:
    """Return a structured op if text is a short CEO command; else None."""
    if not text or len(text) > 140:
        return None
    raw = text.strip()
    # Never intercept slash commands or Otto task injections
    if raw.startswith("/"):
        return None
    if re.match(r"^\s*otto[,:]?\s+\S", raw, re.I):
        # Allow "Otto status" / "Otto, status" as CEO pulls (strip address)
        stripped = re.sub(r"^\s*otto[,:]?\s+", "", raw, flags=re.I).strip()
        # If remaining looks like a task (long / verb-heavy), don't intercept
        if len(stripped.split()) > 8:
            return None
        raw = stripped
    for pat, action, args_tmpl, label in _PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        args = args_tmpl
        if m.lastindex:
            for i in range(1, m.lastindex + 1):
                args = args.replace(f"{{g{i}}}", m.group(i) or "")
        return NaturalOp(action=action, args=args, proof_label=label)
    return None
