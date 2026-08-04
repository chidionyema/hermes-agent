"""
Commercial UI features for Hermes Telegram bot.

Feature 1: Proactive CI alerts — push notifications on CI status changes
Feature 2: Natural language router — type commands, bot understands
Feature 3: Custom keyboard — persistent shortcuts below chat input
Feature 4: Client mode — white-label project view for clients
Feature 5: Conversational onboarding — interactive project wizard
Feature 6: Rich health cards — chart-enhanced messages
"""

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List

HERMES = Path.home() / ".hermes"
CODE = Path.home() / "Documents" / "code"
SCRIPTS = HERMES / "scripts"
ButtonRow = List[Tuple[str, str]]


# ═══════════════════════════════════════════════
# FEATURE 1: Proactive CI Alerts
# ═══════════════════════════════════════════════

class CIWatcher:
    """Watches CI status across all projects, pushes alerts on changes."""
    
    def __init__(self):
        self.state_file = HERMES / "state" / "ci-watcher-state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _load_state(self) -> dict:
        if self.state_file.is_file():
            return json.loads(self.state_file.read_text())
        return {}
    
    def _save_state(self, state: dict):
        self.state_file.write_text(json.dumps(state, indent=2))
    
    def check_and_alert(self) -> list[dict]:
        """Check all projects' CI. Return list of alerts to push.
        
        Only alerts when status CHANGES (not every check).
        """
        sys.path.insert(0, str(HERMES / "hermes-agent"))
        from gateway.operator_shell.projects import get_active_projects, _repo_ci_status
        
        projects = get_active_projects()
        prev_state = self._load_state()
        new_state = {}
        alerts = []
        
        for p in projects:
            key = p["key"]
            primary = CODE / p.get("primary_repo", key)
            
            # Only check projects with CI
            if not (primary / ".github" / "workflows").is_dir():
                continue
            
            ci = _repo_ci_status(primary)
            status = "pass" if ci and "pass" in ci else ("fail" if ci and "fail" in ci else "unknown")
            new_state[key] = status
            
            prev = prev_state.get(key, "")
            
            # Alert on state change: pass→fail or fail→pass
            if prev and prev != status:
                if status == "fail":
                    alerts.append({
                        "type": "ci_fail",
                        "project": p["name"],
                        "key": key,
                        "message": f"🔴 *{p['name']}* CI failed",
                        "detail": ci,
                        "action": f"estate:builds:{key}",
                    })
                elif status == "pass" and prev == "fail":
                    alerts.append({
                        "type": "ci_recover",
                        "project": p["name"],
                        "key": key,
                        "message": f"🟢 *{p['name']}* CI recovered",
                        "detail": ci,
                        "action": f"estate:builds:{key}",
                    })
        
        self._save_state(new_state)
        return alerts
    
    def push_alerts(self) -> int:
        """Check CI and push any alerts to Telegram. Returns count pushed."""
        alerts = self.check_and_alert()
        if not alerts:
            return 0
        
        for alert in alerts:
            text = f"{alert['message']}\n{alert['detail']}"
            try:
                subprocess.run(
                    ["hermes", "send", "--to", "telegram", text],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
        
        return len(alerts)


# ═══════════════════════════════════════════════
# FEATURE 2: Natural Language Router
# ═══════════════════════════════════════════════

class NaturalRouter:
    """Understands natural language commands, routes to actions.
    
    Type any of these instead of navigating buttons:
    - "deploy prospector" / "ship prospector"
    - "what's broken" / "status" / "health"
    - "fix all" / "fix prospector" / "fix ci"
    - "show tie" / "prospector dashboard"  
    - "onboard" / "add project" / "new client acme"
    - "what did otto learn" / "learning digest" / "weekly report"
    - "logs prospector error" / "show errors"
    - "who is working" / "inflight tasks"
    - "pause prospector" / "resume signal engine"
    - "client tie" / "operator mode"
    """
    
    PATTERNS = [
        # Deploy/ship
        (r"(?:deploy|ship|release)\s+(.+)", "deploy", "deploy {project}"),
        # What's broken / status
        (r"(?:what'?s?\s+)?(?:broken|wrong|failing|on fire)", "refresh", "Show what needs attention"),
        (r"(?:status|health|how are you)", "health", "Show health dashboard"),
        # Fix commands
        (r"fix\s+all", "fix_all", "Fix all issues"),
        (r"fix\s+(?:ci|build)\s+(.+)", "ci_fix", "Fix CI for {project}"),
        (r"fix\s+(.+)", "fix_project", "Fix {project}"),
        # Show project
        (r"(?:show|open|view)\s+(.+)", "project", "Open {project}"),
        # Onboarding
        (r"(?:onboard|add\s+(?:new\s+)?(?:project|client))\s*(.*)", "onboard", "Onboard new project"),
        # Learning
        (r"(?:what\s+did\s+otto\s+learn|learning\s+digest|weekly\s+report)", "weekly_digest", "Show learning digest"),
        # Logs
        (r"(?:logs?|show\s+errors?)\s*(.*)", "logs", "Search logs"),
        # Who is working
        (r"(?:who|what)\s+(?:is|are)\s+working", "missions", "Show active missions"),
        (r"inflight\s+tasks?\s*(.*)", "missions", "Show inflight tasks"),
        # Pause/resume
        (r"pause\s+(.+)", "pause", "Pause {project}"),
        (r"resume\s+(.+)", "resume", "Resume {project}"),
        # Client mode
        (r"client\s+(.+)", "client_mode", "Switch to client view: {project}"),
        (r"operator\s*(?:mode)?", "operator_mode", "Switch to operator view"),
        # Dashboard / compliance
        (r"(?:dashboard|web\s*app|mini\s*app)", "dashboard", "Open web dashboard"),
        (r"compliance", "compliance", "Show compliance report"),
        # Projects list
        (r"(?:all\s+)?projects|list\s+projects|fleet", "find", "Show all projects"),
    ]
    
    @classmethod
    def match(cls, text: str) -> Optional[dict]:
        """Try to match natural language to an action. Returns {action, args, label} or None."""
        text = text.strip().lower()
        if not text:
            return None
        
        for pattern, action, label in cls.PATTERNS:
            m = re.match(pattern, text)
            if m:
                groups = m.groups()
                args = groups[0].strip() if groups else ""
                # Resolve project names
                if args:
                    args = cls._resolve_project(args)
                return {"action": action, "args": args, "label": label.replace("{project}", args or "")}
        
        return None
    
    @classmethod
    def _resolve_project(cls, name: str) -> str:
        """Resolve fuzzy project name to key."""
        name = name.strip().lower()
        from gateway.operator_shell.projects import get_active_projects
        projects = get_active_projects()
        
        # Exact key match
        for p in projects:
            if p["key"] == name:
                return p["key"]
        
        # Name contains match
        for p in projects:
            if name in p["name"].lower():
                return p["key"]
        
        # Partial key match
        for p in projects:
            if name in p["key"]:
                return p["key"]
        
        return name  # Return as-is if no match


# ═══════════════════════════════════════════════
# FEATURE 3: Custom Keyboard
# ═══════════════════════════════════════════════

def get_persistent_keyboard(client_mode: str = "") -> list:
    """Return the persistent custom keyboard layout.
    
    Telegram ReplyKeyboardMarkup — always visible below chat input.
    Changes based on mode (operator vs client).
    """
    if client_mode:
        return [
            [{"text": "📊 Status"}, {"text": "📜 Activity"}],
            [{"text": "💬 Request Update"}, {"text": "👤 Operator Mode"}],
        ]
    else:
        return [
            [{"text": "🏠 Home"}, {"text": "🔍 Status"}, {"text": "🛠 Fix All"}],
            [{"text": "📁 Projects"}, {"text": "🧠 Health"}, {"text": "➕ New"}],
        ]


# ═══════════════════════════════════════════════
# FEATURE 4: Client Mode
# ═══════════════════════════════════════════════

class ClientMode:
    """White-label project view for clients. Hides internal details."""
    
    def __init__(self):
        self.mode_file = HERMES / "state" / "client-mode.json"
        self.mode_file.parent.mkdir(parents=True, exist_ok=True)
    
    def get_mode(self) -> Optional[str]:
        """Get current client mode project key, or None if operator mode."""
        if self.mode_file.is_file():
            data = json.loads(self.mode_file.read_text())
            return data.get("client_project")
        return None
    
    def set_client(self, project_key: str) -> bool:
        """Switch to client mode for a project."""
        from gateway.operator_shell.projects import get_project
        p = get_project(project_key)
        if not p:
            return False
        self.mode_file.write_text(json.dumps({
            "client_project": project_key,
            "client_name": p["name"],
            "switched_at": datetime.now(timezone.utc).isoformat(),
        }))
        return True
    
    def set_operator(self):
        """Switch back to operator mode."""
        if self.mode_file.is_file():
            self.mode_file.unlink()
    
    def render_client_home(self) -> Tuple[str, List[ButtonRow]]:
        """Render a client-safe home screen."""
        project_key = self.get_mode()
        if not project_key:
            return "Not in client mode", []
        
        from gateway.operator_shell.projects import get_project, _repo_ci_status, _repo_last_commit
        from gateway.operator_shell.panel_chrome import with_nav
        
        p = get_project(project_key)
        if not p:
            return "Project not found", []
        
        primary = CODE / p.get("primary_repo", project_key)
        ci = _repo_ci_status(primary) or "No CI configured"
        commit_age = _repo_last_commit(primary)
        
        status_emoji = "🟢" if ci and "pass" in ci else ("🔴" if ci and "fail" in ci else "⚪")
        
        lines = [
            f"🏠 *{p['name']}*",
            "",
            f"{status_emoji} Status: {'Healthy' if status_emoji == '🟢' else 'Needs attention'}",
            f"Last update: {commit_age}",
            f"CI: {ci}",
            "",
            f"_{p.get('description', '')}_" if p.get("description") else "",
            "",
            "*What would you like to do?*",
        ]
        
        buttons: List[ButtonRow] = [
            [("📊 Full Status", f"estate:project:{project_key}"),
             ("📜 Activity", f"estate:activity:{project_key}")],
            [("💬 Contact Team", "estate:inbox"),
             ("👤 Switch to Operator", "estate:operator_mode")],
        ]
        return "\n".join(lines), with_nav(buttons)


# ═══════════════════════════════════════════════
# FEATURE 5: Conversational Onboarding Wizard  
# ═══════════════════════════════════════════════

class OnboardingWizard:
    """Interactive conversational onboarding — no forms, just chat."""
    
    def __init__(self):
        self.sessions_file = HERMES / "state" / "onboarding-sessions.json"
        self.sessions_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _load_sessions(self) -> dict:
        if self.sessions_file.is_file():
            return json.loads(self.sessions_file.read_text())
        return {}
    
    def _save_sessions(self, sessions: dict):
        self.sessions_file.write_text(json.dumps(sessions, indent=2))
    
    def start(self, chat_id: str) -> dict:
        """Start onboarding for a chat. Returns the first question."""
        sessions = self._load_sessions()
        sessions[chat_id] = {
            "step": "name",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "data": {},
        }
        self._save_sessions(sessions)
        return {
            "text": (
                "➕ *Let's onboard your project!*\n\n"
                "I'll ask a few quick questions.\n\n"
                "*What's the project called?*"
            ),
            "step": "name",
        }
    
    def handle_response(self, chat_id: str, text: str) -> dict:
        """Handle a response in the onboarding flow."""
        sessions = self._load_sessions()
        session = sessions.get(chat_id, {})
        if not session:
            return {"text": "No active onboarding session. Say 'onboard' to start.", "done": True}
        
        step = session.get("step", "name")
        data = session.get("data", {})
        
        if step == "name":
            data["name"] = text.strip()
            session["step"] = "repo"
            sessions[chat_id] = session
            self._save_sessions(sessions)
            return {
                "text": (
                    f"*{data['name']}* — great name!\n\n"
                    "*Where's the code?*\n"
                    "Send me a GitHub URL, or just the repo name if it's in ~/Documents/code."
                ),
                "step": "repo",
            }
        
        elif step == "repo":
            # Try to find the repo
            repo_name = text.strip()
            # Strip github URLs
            if "github.com" in repo_name:
                repo_name = repo_name.split("/")[-1].replace(".git", "")
            
            repo_path = CODE / repo_name
            if repo_path.is_dir():
                data["repo"] = repo_name
                data["repo_path"] = str(repo_path)
                # Auto-detect risk
                from gateway.operator_shell.projects import onboard_project
                session["step"] = "confirm"
                sessions[chat_id] = session
                self._save_sessions(sessions)
                
                # Quick scan for risk
                risk = "low"
                try:
                    for f in repo_path.rglob("*.py"):
                        if f.stat().st_size > 50000: continue
                        content = f.read_text(errors="replace")[:2000].lower()
                        if any(w in content for w in ("stripe", "payment", "charge", "billing")):
                            risk = "money"; break
                        if any(w in content for w in ("passport", "kyc", "pii", "identity")):
                            risk = "identity"; break
                except Exception: pass
                
                data["risk"] = risk
                risk_label = {"low": "🟢 Low", "money": "💰 Money 🔐", "identity": "🛡️ Identity 🔐"}[risk]
                
                return {
                    "text": (
                        f"🔍 *Found it!*\n\n"
                        f"• Name: *{data['name']}*\n"
                        f"• Repo: `{repo_name}`\n"
                        f"• Risk: {risk_label}\n\n"
                        "Ready to onboard?\n"
                        "[Confirm] or type changes"
                    ),
                    "step": "confirm",
                }
            else:
                return {
                    "text": (
                        f"❌ Couldn't find `{repo_name}` in ~/Documents/code.\n\n"
                        "Try the full repo name, or type 'skip' to create without a repo."
                    ),
                    "step": "repo",
                }
        
        elif step == "confirm":
            if text.strip().lower() in ("yes", "confirm", "ok", "y", "go"):
                from gateway.operator_shell.projects import onboard_project
                result = onboard_project(data.get("repo", data.get("name", "").lower()), "product")
                if "error" in result:
                    return {"text": f"❌ {result['error']}", "done": True}
                
                del sessions[chat_id]
                self._save_sessions(sessions)
                return {
                    "text": (
                        f"✅ *{data['name']}* is onboarded!\n\n"
                        f"Key: `{result['key']}`\n"
                        f"Risk: {result['risk']}\n"
                        f"CI: {'✅' if result.get('ci_provider') else '❌'}\n\n"
                        "[📁 View Project](estate:project:{result['key']})"
                    ),
                    "done": True,
                }
            else:
                return {
                    "text": "Onboarding cancelled. Say 'onboard' to start over.",
                    "done": True,
                }
        
        return {"text": "Something went wrong. Say 'onboard' to start over.", "done": True}


# ═══════════════════════════════════════════════
# FEATURE 6: Rich Health Cards
# ═══════════════════════════════════════════════

def render_rich_health() -> str:
    """Rich health card with sparkline bars and prediction."""
    sys.path.insert(0, str(HERMES / "hermes-agent"))
    from gateway.operator_shell.otto_health import _compute_score
    
    score = _compute_score()
    s = score["score"]
    b = score["breakdown"]
    se = "🟢" if s >= 0.7 else ("🟡" if s >= 0.4 else "🔴")
    
    # Sparkline bars
    def bar(val, width=10):
        filled = int(val * width)
        return "█" * filled + "░" * (width - filled)
    
    dims = [
        ("Auto-fixes", b.get("auto_fixes", 0)),
        ("Injections", b.get("injection_relevance", 0)),
        ("Firings", b.get("policy_firings", 0)),
        ("Learning", b.get("learning", 0)),
        ("Estate", b.get("estate_health", 0)),
        ("Cron", b.get("cron_health", 0)),
    ]
    
    lines = [
        f"🧠 *Otto Health* — {se} {int(s*100)}%",
        "",
        "```",
    ]
    for label, val in dims:
        pct = int(val * 100)
        lines.append(f"{label:14s} {bar(val)} {pct}%")
    lines.append("```")
    lines.append("")
    
    # Trend from change-outcomes
    try:
        outcomes_file = HERMES / "logs" / "meta-improver" / "change-outcomes.jsonl"
        if outcomes_file.is_file():
            scores = []
            for line in outcomes_file.read_text().splitlines():
                if not line.strip(): continue
                try:
                    e = json.loads(line)
                    if "health_score" in e:
                        scores.append(e["health_score"])
                except Exception: pass
            
            if len(scores) >= 2:
                first, last = scores[0], scores[-1]
                delta = last - first
                direction = "📈 Improving" if delta > 0.01 else ("📉 Declining" if delta < -0.01 else "➡️ Stable")
                lines.append(f"{direction}: {delta:+.1%} this period")
                
                # Simple prediction
                if len(scores) >= 3:
                    trend = (scores[-1] - scores[0]) / max(len(scores) - 1, 1)
                    predicted = min(scores[-1] + trend * 3, 1.0)
                    lines.append(f"🔮 Predicted (3 cycles): {int(predicted*100)}%")
    except Exception:
        pass
    
    lines.append("")
    lines.append(f"📊 {score['raw'].get('total_injections','?')} injections · "
                f"{score['raw'].get('total_firings','?')} enforcements · "
                f"{score['raw'].get('auto_fixes','?')} auto-fixes")
    
    return "\n".join(lines)
