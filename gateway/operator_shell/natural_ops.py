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
    # "Is it deployed / is it live / did it ship" — the question the founder had to ask in words
    # on 2026-08-10, which then took eight hand-run shell calls to answer. `status` is about
    # whether things are HEALTHY; this is about whether what is running is the code we shipped.
    # The phrasings below are the ones actually used, not invented ones.
    (re.compile(
        r"^\s*(deployed|deploy(ment)?s?|is\s+it\s+(deployed|live|shipped|out)|"
        r"what'?s\s+deployed|whats\s+deployed|did\s+it\s+(ship|deploy|go\s+out)|"
        r"are\s+we\s+(deployed|live)|is\s+it\s+running\s+the\s+new\s+code|"
        r"what\s+is\s+(running|live))\s*\??\s*$", re.I),
     "deployed", "", "Deployed — estate-wide"),
    # "What is it doing RIGHT NOW" — the sub-tick question (R5). Distinct from `deployed`
    # (is the running code the code we shipped) and from `last run` (the last COMPLETED batch):
    # this one is answered from the live audit trail, mid-vet, between tick summaries.
    # `what is running` deliberately stays with `deployed` above — it asks about processes.
    (re.compile(
        r"^\s*(in\s*flight|inflight|what\s+is\s+it\s+doing|what'?s\s+it\s+doing|"
        r"what\s+are\s+you\s+doing|current\s+(work|candidate|check)|"
        r"progress|what'?s\s+in\s+flight|whats\s+in\s+flight|"
        r"what\s+is\s+being\s+(vetted|worked\s+on))\s*(now|right\s+now)?\s*\??\s*$", re.I),
     "pd_in_flight", "", "In flight — sub-tick"),
    (re.compile(
        r"^\s*(what'?s\s+on\s+fire|on\s+fire|mission|cockpit|panel|otto|"
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
    # Projects / fleet.
    #
    # These were ONE pattern routing "projects" and "portfolio" to Fleet, which is a
    # fixed list of 4 hardcoded repos (fleet.py:19-22). The registry holds 14
    # projects (~/.hermes/projects.json), so asking for "projects" answered with a
    # different, smaller set and no route to the other 10. Projects is listed FIRST
    # because this list is ordered and first match wins — a `projects?` left inside
    # the fleet alternation would keep stealing the word.
    (re.compile(
        r"^\s*(projects?|portfolio)\s*\??\s*$", re.I),
     "projects", "", "Projects"),
    (re.compile(
        r"^\s*(fleet)\s*\??\s*$", re.I),
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
     "daemon_restart_now", "gateway", "Restart gateway (one-tap)"),
    # One word to remember when Otto is unresponsive. "stuck", "restart otto",
    # "otto is frozen", "fix otto", "hung" — all one-tap gateway restart.
    (re.compile(
        r"^\s*(restart|bounce|fix|kick)\s+otto\s*$", re.I),
     "daemon_restart_now", "gateway", "Restart gateway (one-tap)"),
    (re.compile(
        r"^\s*(?:otto\s+)?(?:is\s+)?(stuck|hung|frozen|unresponsive|dead|broken)\s*$", re.I),
     "daemon_restart_now", "gateway", "Restart gateway (one-tap)"),
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
    # ── Estate panels that had buttons and no words ──────────────────────────────
    #
    # estate.py::_PANELS registers 24 read-only panels. Measured 2026-08-17: only TWO of
    # them (projects, deployed) could be reached by typing anything. The other 22 were
    # reachable ONLY by tapping a button you had already been shown, so a panel you had
    # not seen recently did not exist as far as the operator was concerned. That is the
    # same defect class as the estate_intel panels themselves — "the buttons were shipped;
    # the panels never were" (estate_intel.py:4) — one layer up.
    #
    # These sit ABOVE the Find/help patterns on purpose. `find`, `commands` and `help`
    # match broadly, and this list is ordered first-match-wins, so a door placed below
    # them would be swallowed. Words already owned by another route are NOT taken here:
    # bare `dashboard`, `now` and `health` still go to the mission card, and bare
    # `commands` still goes to Find. Each panel below is a real entry in _PANELS with
    # arg mode "none" — nothing here invents a capability.
    (re.compile(
        r"^\s*(depend(enc(y|ies))?|deps|dependency\s+map|"
        r"what\s+depends\s+on\s+what)\s*\??\s*$", re.I),
     "dependencies", "", "Dependencies"),
    (re.compile(
        r"^\s*(correlate|linked\s+failures?|related\s+failures?|"
        r"root\s+causes?|what\s+fails\s+together)\s*\??\s*$", re.I),
     "correlate", "", "Linked failures"),
    (re.compile(
        r"^\s*(capabilit(y|ies)(\s+status)?|what\s+is\s+dark|whats\s+dark|"
        r"which\s+capabilit(y|ies)|liveness|"
        r"what\s+can\s+(you|u)\s+do)\s*\??\s*$", re.I),
     "capabilities", "", "Capabilities"),
    # "system health" added 2026-08-17. tests/test_rounds_d_h.py:148 had asserted it since
    # the panel was written and it had never matched anything — the phrase an operator
    # actually types was the one phrase not in the pattern.
    (re.compile(
        r"^\s*(estate\s+health|system\s+health|health\s+score)\s*\??\s*$", re.I),
     "estate_health", "", "Estate health"),
    # The bare form. The subject-carrying form is the next pattern; both land on the same
    # panel, which takes an optional argument (estate.py _PANELS: diagnose_panel is _ARG_OPT).
    (re.compile(
        r"^\s*(diagnose|diagnosis|what\s+is\s+wrong|whats\s+wrong)\s*\??\s*$", re.I),
     "diagnose_panel", "", "Diagnose"),
    # "diagnose moat", "why is prospector failing" — asserted in tests/test_rounds_d_h.py:137
    # and never routed. Anchored AFTER the bare form so "diagnose" alone keeps the empty arg.
    (re.compile(
        r"^\s*(?:diagnose|diagnosis\s+of)\s+(.+?)\s*\??\s*$", re.I),
     "diagnose_panel", "{g1}", "Diagnose"),
    (re.compile(
        r"^\s*why\s+is\s+(.+?)\s+(?:failing|broken|down|dark)\s*\??\s*$", re.I),
     "diagnose_panel", "{g1}", "Diagnose"),
    # fix_all must sit ABOVE the `fix <subject>` pattern below. "fix all" is exactly one
    # bare word after "fix", so that pattern matched it first and routed the operator to the
    # fix GUIDE for a subject named "all". The action existed and dispatched correctly the
    # whole time; only the door was missing. Asserted in tests/test_rounds_i_k.py since the
    # action was written, and red there since — nothing ran that file (see
    # ~/.hermes/tests/run.sh, which gained its bash lane on 2026-08-19).
    (re.compile(
        r"^\s*(fix\s+(all|everything)|auto[\s-]?fix|fix\s+it\s+all)\s*\??\s*$", re.I),
     "fix_all", "", "Fix all"),
    (re.compile(
        r"^\s*(fix\s+guide|how\s+do\s+i\s+fix\s+(it|this)|repair\s+guide)\s*\??\s*$", re.I),
     "fix_guide", "", "Fix guide"),
    # "fix credits" — the subject-carrying form of the fix guide, which is also _ARG_OPT.
    # Exactly ONE bare word after "fix". Anything longer is a coding request and must fall
    # through to code_assign: "fix the login bug" is work, "fix credits" is a panel.
    (re.compile(
        r"^\s*fix\s+(?!guide\b)([\w-]+)\s*\??\s*$", re.I),
     "fix_guide", "{g1}", "Fix guide"),
    (re.compile(
        r"^\s*(features?|feature\s+list|what\s+features?(\s+exist)?)\s*\??\s*$", re.I),
     "features_panel", "", "Features"),
    # The trailing subject is accepted and dropped: the forecast panel is _ARG_NONE in
    # estate.py, so "predict credits" and "predict" are the same panel. Added 2026-08-17
    # because tests/test_rounds_d_h.py:139 asserted the subject form and it never matched.
    (re.compile(
        r"^\s*(forecast|predict(ions?)?|what\s+happens\s+next)"
        r"(\s+[\w-]+)?\s*\??\s*$", re.I),
     "predict_panel", "", "Forecast"),
    (re.compile(
        r"^\s*((active|open|recent|current)\s+)?"
        r"(incidents?|incident\s+log|outages?)\s*\??\s*$", re.I),
     "incidents", "", "Incidents"),
    (re.compile(
        r"^\s*(compliance|policy\s+check)\s*\??\s*$", re.I),
     "compliance", "", "Compliance"),
    (re.compile(
        r"^\s*(idle|idle\s+status|what\s+do\s+you\s+do\s+when\s+idle)\s*\??\s*$", re.I),
     "idle_status", "", "Idle"),
    (re.compile(
        r"^\s*(self[-\s]?audit|otto\s+health|audit\s+yourself)\s*\??\s*$", re.I),
     "otto_health", "", "Self-audit"),
    (re.compile(
        r"^\s*(score|estate\s+score|score\s+(history|target|trend))\s*\??\s*$", re.I),
     "score", "", "Score"),
    (re.compile(
        r"^\s*(changes|rsi\s+changes|what\s+changed|what\s+have\s+you\s+changed)\s*\??\s*$",
        re.I),
     "rsi_changes", "", "Changes"),
    # "report" and "weekly report" added 2026-08-17. tests/test_commercial_bridge.py:91
    # asserted both and neither matched: the panel was called "digest" and every operator
    # types "report". The panel is the same one; only the words an operator uses were missing.
    (re.compile(
        r"^\s*(digest|weekly\s+digest|week\s+in\s+review|report|weekly\s+report)\s*\??\s*$",
        re.I),
     "weekly_digest", "", "Digest"),
    (re.compile(
        r"^\s*(logs?|log\s+picker|show\s+(me\s+)?(the\s+)?logs?)\s*\??\s*$", re.I),
     "logs", "", "Logs"),
    (re.compile(
        r"^\s*(web\s+dashboard|open\s+(the\s+)?dashboard|browser\s+dashboard)\s*\??\s*$",
        re.I),
     "dashboard", "", "Dashboard"),
    (re.compile(
        r"^\s*(command\s+palette|all\s+commands|every\s+command)\s*\??\s*$", re.I),
     "commands", "", "Commands"),
    (re.compile(
        r"^\s*(prospector\s+now|now\s+prospector|what\s+is\s+prospector\s+doing)\s*\??\s*$",
        re.I),
     "prospector_now", "", "Now"),
    (re.compile(
        r"^\s*(health\s+panel|subsystem\s+health|health\s+detail)\s*\??\s*$", re.I),
     "health", "", "Health"),
    (re.compile(
        r"^\s*(otto\s+panel|otto\s+status)\s*\??\s*$", re.I),
     "otto", "", "Otto"),
    # Find. The answer to "I don't know where anything is" — anchored above code_assign so
    # "find the spend cap" is a lookup, not a coding task.
    (re.compile(r"^\s*(?:find|search|lookup|look\s+up)\s+(.+?)\s*\??\s*$", re.I),
     "find", "{g1}", "Map — rooms + search"),
    (re.compile(
        r"^\s*(?:where\s+(?:is|are|do\s+i\s+find)|how\s+do\s+i)\s+(.+?)\s*\??\s*$", re.I),
     "find", "{g1}", "Map — rooms + search"),
    (re.compile(r"^\s*(?:find|search|menu|commands?|map)\s*\??\s*$", re.I),
     "find", "", "Map — rooms + search"),
    # Simple help — what Otto can do, with the one word to trigger each.
    (re.compile(
        r"^\s*(help|what\s+(can|could)\s+you\s+(do|help\s+with)|"
        r"what\s+are\s+you\s+(capable\s+of|able\s+to\s+do)|capabilities|"
        r"what\s+do\s+you\s+do|how\s+do\s+I\s+use\s+you|"
        r"how\s+(can|do)\s+I\s+get\s+help)\s*\??\s*$", re.I),
     "help", "", "Help — what Otto can do"),
    # Atlas / Rooms — job map behind empty Find
    (re.compile(
        r"^\s*(atlas|map|where\s+is\s+everything)\s*\??\s*$", re.I),
     "find", "", "Browse — Atlas rooms"),
    (re.compile(
        r"^\s*rooms?\s*\??\s*$", re.I),
     "find", "", "Browse — rooms"),
    (re.compile(
        r"^\s*(money\s+room|room\s+money)\s*\??\s*$", re.I),
     "room", "money", "Money room"),
    # SDLC pipeline (must be before the room:code entry)
    (re.compile(
        r"^\s*(sdlc|pipeline)\s*\??\s*$", re.I),
     "sdlc", "", "SDLC pipeline"),
    (re.compile(
        r"^\s*(code\s+room|room\s+code|software\s+lifecycle|"
        r"code\s+estate)\s*\??\s*$", re.I),
     "sdlc", "", "SDLC pipeline"),
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
