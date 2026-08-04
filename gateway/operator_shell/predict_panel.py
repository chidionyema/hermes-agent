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
        for provider, info in data.items():
            if isinstance(info, dict):
                emoji = "🔴" if info.get("estimated_exhaustion_h", 99) < 6 else "🟡"
                lines.append(f"{emoji} *{provider}*: {info.get('errors_last_6h',0)} errors in 6h")
                if info.get("estimated_exhaustion_h"):
                    lines.append(f"  Exhausts in ~{info['estimated_exhaustion_h']}h at current rate")
                if info.get("action"):
                    lines.append(f"  → {info['action']}")
    lines.append(""); lines.append(panel_stamp("predict"))
    buttons.append([("🔍 Diagnose", "estate:diagnose_panel"), ("📋 Fix guide", "estate:fix_guide:credits")])
    buttons = with_nav(buttons, "predict_panel")
    return "\n".join(lines), buttons
