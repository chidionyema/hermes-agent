"""Diagnose panel — Telegram-native diagnostic card with pass/fail checks and fix buttons."""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]
import json, os, subprocess, sys
from pathlib import Path
SCRIPTS = Path(os.path.expanduser("~/.hermes/scripts"))

def _run_diagnostic(target=None):
    args = [sys.executable, str(SCRIPTS/"diagnostics.py")]
    if target: args.extend(["--" + target] if not target.startswith("-") else [target])
    else: args.append("--diagnose")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        try: return json.loads(r.stdout)
        except: return {"error": r.stdout[:200]}
    except Exception as e: return {"error": str(e)}

def _sections(data, target):
    """`diagnostics.py` speaks two shapes; this panel has to render both.

    `--diagnose` (no target) nests each area under its name — `{"moat": {...},
    "engine": {...}}` (`diagnostics.py:294-299`). But `--moat` and `--engine` return
    the area *itself*, flat — `{"status", "checks", "root_cause", "fix"}`
    (`diagnostics.py:373-375`). The panel only ever read the nested shape, so the
    `estate:diagnose_panel:moat` button (`diagnose_panel.py:57`, the "Verify fix" the
    fix guide sends you to) rendered a header and a timestamp and nothing else.

    Returns [(area_name, payload)] for either shape.
    """
    nested = [(name, data[name]) for name in ("moat", "engine") if isinstance(data.get(name), dict)]
    if nested:
        return nested
    if "checks" in data or "status" in data:
        return [(target or "diagnostic", data)]
    return []


def render_diagnose(target=None):
    data = _run_diagnostic(target)
    lines = [f"🔍 *Diagnostic{f': {target}' if target else ''}*", ""]
    buttons = []
    if "error" in data:
        lines.append(f"⚠️ Diagnostic failed: {data['error'][:100]}")
    else:
        sections = _sections(data, target)
        if not sections:
            lines.append("_No diagnostic areas reported._")
        for name, area in sections:
            lines.append(f"*{name.title()}:* {'🟢' if area.get('status')=='ok' else '🔴'} {area.get('status','?')}")
            for check in area.get("checks", []):
                emoji = "🟢" if check.get("status")=="pass" else "🔴"
                lines.append(f"  {emoji} {check.get('check','?')}: {str(check.get('detail','?'))[:80]}")
            if area.get("root_cause"):
                lines.append(f"\n*Root cause:* {area['root_cause']}")
            if area.get("fix"):
                lines.append(f"\n*Fix:* {str(area['fix'])[:200]}")
            lines.append("")
        # One fix row for the panel, not one per area — `--diagnose` returns two areas and
        # both carry a `fix`, which would otherwise stack two identical button rows.
        if any(area.get("fix") for _, area in sections):
            buttons.append([("💳 Fix credits", "estate:fix_guide:credits"), ("🛠 Restart stuck jobs", "estate:fix_all")])

    lines.append(""); lines.append(panel_stamp("diagnose"))
    buttons.append([("🔍 Diagnose", "estate:diagnose_panel"), ("🛠 Restart stuck jobs", "estate:fix_all")])
    buttons = with_nav(buttons, "diagnose_panel")
    return "\n".join(lines), buttons

def render_fix_guide(target="credits"):
    if target == "credits":
        lines = ["💳 *Fix Credits* — step by step", "",
                 "Step 1: Open Cursor account", "  → [Open cursor.sh/account](https://cursor.sh/account)",
                 "Step 2: Top up or upgrade plan",
                 "Step 3: Add Anthropic credits", "  → [Open console.anthropic.com](https://console.anthropic.com)",
                 "Step 4: Verify by running `diagnose moat`", "",
                 "_Estimated time: 2 minutes_", "", panel_stamp("fix_guide")]
        buttons = [[("🔍 Diagnose moat", "estate:diagnose_panel:moat")],
                   [("🛠 Restart stuck jobs", "estate:fix_all")]]
    else:
        lines = [f"📋 *Fix Guide: {target}*", "", "_No guide available for this target yet._", "", panel_stamp("fix_guide")]
        buttons = []
    buttons = with_nav(buttons, "fix_guide")
    return "\n".join(lines), buttons
