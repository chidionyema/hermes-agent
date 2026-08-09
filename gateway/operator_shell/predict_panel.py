"""Predict panel — Telegram-native forecast card with sparkline and action buttons."""
from typing import List, Tuple
from gateway.operator_shell.panel_chrome import nav, panel_stamp, with_nav
ButtonRow = List[Tuple[str, str]]
import json, os, subprocess, sys
from pathlib import Path
SCRIPTS = Path(os.path.expanduser("~/.hermes/scripts"))

def _run_predict(target="credits"):
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS/"predictor.py"), "--predict", target],
                          capture_output=True, text=True, timeout=15)
        try: return json.loads(r.stdout)
        except: return {"error": r.stdout[:200]}
    except Exception as e: return {"error": str(e)}

def render_predict(target="credits"):
    data = _run_predict(target)
    lines = [f"🔮 *Prediction{f': {target}' if target else ''}*", ""]
    buttons = []
    if "error" in data:
        lines.append(f"⚠️ {data['error'][:100]}")
    else:
        # ONE flat record, not a map of provider -> record. `predictor.py:370-372` calls
        # `predict_credit_exhaustion()` whatever the target, and it returns
        # {"provider", "errors_last_6h", "rate_per_hour", "estimated_exhaustion_h", "action"}
        # (`predictor.py:118-124`) — every value a scalar. The old `isinstance(info, dict)`
        # loop was therefore never true once, so this panel rendered a header and a
        # timestamp and nothing else. Measured on the live estate 2026-08-06 it was hiding
        # "211 errors in 6h, exhausts in ~0.7h".
        hours = data.get("estimated_exhaustion_h")
        errors = data.get("errors_last_6h", 0)
        emoji = "🔴" if (hours is not None and hours < 6) else ("🟡" if errors else "🟢")
        lines.append(f"{emoji} *{data.get('provider', 'unknown')}*: {errors} errors in 6h")
        if data.get("rate_per_hour"):
            lines.append(f"  {data['rate_per_hour']}/hour")
        if hours is not None:
            lines.append(f"  Exhausts in ~{hours}h at current rate")
        if data.get("action"):
            lines.append(f"  → {data['action']}")
    lines.append(""); lines.append(panel_stamp("predict"))
    buttons.append([("🔍 Diagnose", "estate:diagnose_panel"), ("💳 Fix credits", "estate:fix_guide:credits")])
    buttons = with_nav(buttons, "predict_panel")
    return "\n".join(lines), buttons
