"""
Project registry and new Home panel for Hermes Telegram menu.

Replaces the old smart_home with a project-first architecture:
- Home shows ALL tracked projects with status
- Tap a project → Project Dashboard (SDLC, activity, health)
- Projects.json is the single source of truth
- Client onboarding flow built in

Architecture:
  Home → [Project A] [Project B] [Project C] ...
         ↓ tap
  Project Dashboard → [SDLC] [CI] [Activity] [Health] [Config]
"""

import json
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ButtonRow type used by all panels
ButtonRow = List[Tuple[str, str]]

HERMES_HOME = Path.home() / ".hermes"
CODE = Path.home() / "Documents" / "code"
REGISTRY = HERMES_HOME / "projects.json"


def load_registry() -> dict:
    """Load the project registry. Single source of truth."""
    if not REGISTRY.is_file():
        return {"projects": []}
    try:
        return json.loads(REGISTRY.read_text())
    except (json.JSONDecodeError, OSError):
        return {"projects": []}


def save_registry(data: dict):
    """Save the project registry."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    REGISTRY.write_text(json.dumps(data, indent=2))


def get_active_projects() -> List[dict]:
    """Get projects that should appear on Home (active + incubating)."""
    reg = load_registry()
    return [p for p in reg.get("projects", [])
            if p.get("status") in ("active", "incubating")]


def get_archived_projects() -> List[dict]:
    """Get archived/superseded projects."""
    reg = load_registry()
    return [p for p in reg.get("projects", [])
            if p.get("status") == "archived"]


def get_project(key: str) -> Optional[dict]:
    """Get a single project by key."""
    for p in load_registry().get("projects", []):
        if p["key"] == key:
            return p
    return None


# ═══════════════════════════════════════════════════
# Repo health probes
# ═══════════════════════════════════════════════════

def _repo_git_status(repo_path: Path) -> str:
    """Get git status for a repo: clean/dirty/missing."""
    if not repo_path.is_dir():
        return "missing"
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return "error"
        return "clean" if not r.stdout.strip() else "dirty"
    except Exception:
        return "unknown"


def _repo_last_commit(repo_path: Path) -> str:
    """Get last commit age as human-readable string."""
    if not repo_path.is_dir():
        return "—"
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return "—"
        ts = int(r.stdout.strip())
        age = max(0, int(time.time() - ts))
        if age < 3600:
            return f"{age // 60}m ago"
        if age < 86400:
            return f"{age // 3600}h ago"
        if age < 604800:
            return f"{age // 86400}d ago"
        return f"{age // 604800}w ago"
    except Exception:
        return "—"


def _repo_branch(repo_path: Path) -> str:
    """Get current branch."""
    if not repo_path.is_dir():
        return "—"
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or "—"
    except Exception:
        return "—"


def _repo_ci_status(repo_path: Path) -> Optional[str]:
    """Get latest GitHub Actions CI status. Returns emoji + conclusion or None."""
    if not (repo_path / ".github" / "workflows").is_dir():
        return None
    try:
        r = subprocess.run(
            ["gh", "run", "list", "-R", str(repo_path), "-L", "1",
             "--json", "conclusion,status,displayTitle"],
            capture_output=True, text=True, timeout=15,
            cwd=str(repo_path),
        )
        if r.returncode != 0:
            return None
        runs = json.loads(r.stdout)
        if not runs:
            return None
        run = runs[0]
        conc = run.get("conclusion") or run.get("status") or "?"
        if conc == "success":
            return "🟢 pass"
        elif conc in ("failure", "timed_out", "cancelled"):
            return "🔴 fail"
        elif conc in ("in_progress", "queued", "waiting"):
            return "🟡 running"
        return f"⚪ {conc}"
    except Exception:
        return None


SCRIPTS = HERMES_HOME / "scripts"


# ═══════════════════════════════════════════════════
# Home panel — all projects at a glance
# ═══════════════════════════════════════════════════

def render_home() -> Tuple[str, List[ButtonRow]]:
    """Attention-first Home: triage by severity.

    🔴 Critical — CI failing, money/identity projects stale
    🟡 Watch — dirty client repos, active projects with no recent activity
    🟢 Clear — everything else, summarized on one line
    """
    from gateway.operator_shell.panel_chrome import nav, with_nav
    from gateway.operator_shell.smart_home import _quick_status as _estate_status

    projects = get_active_projects()
    estate = _estate_status()

    # ── Classify every project ──
    critical, watch, clear = [], [], []

    for p in projects:
        key = p["key"]
        risk = p.get("risk", "low")
        primary = CODE / p.get("primary_repo", key)
        git_status = _repo_git_status(primary)
        ci = _repo_ci_status(primary)
        age_str = _repo_last_commit(primary)

        # Parse age
        age_days = 999
        try:
            if 'm ago' in age_str: age_days = 0
            elif 'h ago' in age_str: age_days = int(age_str.split('h')[0]) / 24
            elif 'd ago' in age_str: age_days = int(age_str.split('d')[0])
            elif 'w ago' in age_str: age_days = int(age_str.split('w')[0]) * 7
        except Exception: pass

        p["_detail"] = age_str
        p["_ci"] = ci
        p["_git"] = git_status
        p["_age_days"] = age_days

        if ci and "fail" in ci:
            critical.append((p, f"CI failing · {age_str}", "🔴"))
        elif risk in ("money", "identity") and (git_status == "dirty" or age_days > 30):
            critical.append((p, f"{git_status} · {age_str} · {risk} project", "🔴"))
        elif git_status == "dirty" and p.get("type") == "client":
            watch.append((p, f"{git_status} · {age_str} · client", "🟡"))
        elif age_days > 14 and p.get("status") == "active":
            watch.append((p, f"{age_str} · no recent activity", "🟡"))
        else:
            clear.append((p, age_str, "🟢"))

    # ── Build panel ──
    # `projects` here is get_active_projects(), which is active + incubating (:47-51).
    # Counted from the registry rather than described from memory: the first draft of
    # this line called all 10 of them "active" and then claimed the 4 incubating ones
    # were "not shown" while they were rendering as buttons directly underneath.
    reg_all = load_registry().get("projects", [])
    n_active = sum(1 for p in reg_all if p.get("status") == "active")
    n_incub = sum(1 for p in reg_all if p.get("status") == "incubating")
    n_arch = sum(1 for p in reg_all if p.get("status") == "archived")

    lines = ["🗂 *Projects*"]
    prospector_status = estate.get("prospector", "?")
    spend = estate.get("spend", 0)
    estate_emoji = "🔴" if "🔴" in prospector_status else ("🟡" if estate.get("incidents", 0) > 0 else "🟢")
    lines.append(f"{estate_emoji} ${spend:.2f} spent · {n_active} active · {n_incub} incubating")
    if n_arch:
        lines.append(f"_{n_arch} archived, not shown_")

    if critical:
        lines.append("")
        lines.append("*🔴 Needs attention*")
        for p, detail, _ in critical:
            f = " 🔐" if p.get("risk") in ("money", "identity") else ""
            lines.append(f"• *{p['name']}*{f} — {detail}")

    if watch:
        lines.append("")
        lines.append("*🟡 Watch*")
        for p, detail, _ in watch[:4]:
            f = " 🔐" if p.get("risk") in ("money", "identity") else ""
            lines.append(f"• *{p['name']}*{f} — {detail}")

    if clear:
        names = ", ".join(p["name"] for p, _, _ in clear[:6])
        lines.append("")
        lines.append(f"*🟢 Clear* — {names}")

    # ── Buttons: EVERY active project is tappable, worst first ──
    #
    # This was `critical[:4]`, so a healthy estate rendered a list of project names
    # with not one of them tappable — you could read that six projects existed and
    # open none of them. The subject list IS this screen; severity decides the
    # ORDER, never whether a project can be opened.
    buttons: List[ButtonRow] = []
    row: ButtonRow = []
    for p, _, glyph in critical + watch + clear:
        row.append((f"{glyph} {p['name'][:16]}", f"estate:project:{p['key']}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row: buttons.append(row)

    # Estate-wide verbs. Every callback below is dispatch-verified against
    # handle_estate_action — the previous row offered fix_all / dashboard / onboard,
    # and all three answered "⚠️ Unknown action". Dead buttons on the front door are
    # why the cockpit read as broken rather than as empty.
    buttons.append([("🖥 Web dashboard", "estate:dashboard"), ("🧠 Health", "estate:health")])
    buttons.append([("📥 Inbox", "estate:inbox"), ("⚙️ Settings", "estate:tune")])

    # ── Self-improvement summary line (always visible on Home) ──
    try:
        import sys as _sys
        _sys.path.insert(0, str(HERMES_HOME / "hermes-agent"))
        from gateway.operator_shell.otto_health import _compute_score
        sd = _compute_score()
        sc = int(sd["score"] * 100)
        se = "🟢" if sd["score"] >= 0.7 else ("🟡" if sd["score"] >= 0.4 else "🔴")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{se} *Otto Learning:* {sc}% · {sd['raw'].get('policies_created_this_week','?')} new policies · {sd['raw'].get('total_injections','?')} injections")
        lines.append(f"   Auto-fixes: {sd['raw'].get('auto_fixes','?')} · Firings: {sd['raw'].get('total_firings','?')} · Cron: {int(sd['breakdown'].get('cron_health',0)*100)}% healthy")
    except Exception:
        pass

    buttons = with_nav(buttons)
    return "\n".join(lines), buttons


# ═══════════════════════════════════════════════════
# Project Dashboard
# ═══════════════════════════════════════════════════

def render_project_dashboard(project_key: str, client_mode: bool = False) -> Tuple[str, List[ButtonRow]]:
    """Per-project dashboard. In client_mode, hides internal details."""
    from gateway.operator_shell.panel_chrome import nav, with_nav

    p = get_project(project_key)
    if not p:
        return f"❌ Project `{project_key}` not found", [nav()]

    name = p["name"]
    risk = p.get("risk", "low")
    fence = " 🔐" if risk in ("money", "identity") else ""
    primary = CODE / p.get("primary_repo", project_key)

    commit_age = _repo_last_commit(primary)
    git_status = _repo_git_status(primary)
    ci = _repo_ci_status(primary)
    branch = _repo_branch(primary)

    # Status line
    if ci and "fail" in ci:
        status_emoji, status_text = "🔴", f"CI failing · {commit_age}"
    elif ci and "pass" in ci:
        status_emoji, status_text = "🟢", f"CI passing · {commit_age}"
    elif ci and "running" in ci:
        status_emoji, status_text = "🟡", f"CI running · {commit_age}"
    elif git_status == "dirty":
        status_emoji, status_text = "🟡", f"Uncommitted changes · {commit_age}"
    else:
        status_emoji, status_text = "⚪", f"{git_status} · {commit_age}"

    lines = [f"📁 *{name}*{fence}", "", f"{status_emoji} {status_text}"]

    if not client_mode:
        lines.append(f"Branch: `{branch}` · {len(p.get('repos', []))} repos")

    if p.get("description"):
        lines.append(f"_{p['description'][:80]}_")

    lines.append("")

    # ── Actions (client gets simplified) ──
    if client_mode:
        lines.append("*What would you like to do?*")
        buttons: List[ButtonRow] = [
            [("📊 Status", f"estate:project:{project_key}"),
             ("🚢 Deploy", f"estate:deploy:{project_key}")],
            [("📜 History", f"estate:activity:{project_key}"),
             ("💬 Feedback", "estate:inbox")],
        ]
    else:
        lines.append("*SDLC Pipeline*")
        lines.append("Assign → Board → Fleet → Review → Ship → Learn")
        lines.append("")
        buttons = [
            [("📊 SDLC", f"estate:sdlc:{project_key}"),
             ("🚢 CI", f"estate:builds:{project_key}")],
            [("📜 Activity", f"estate:activity:{project_key}"),
             ("🧠 Health", f"estate:health:{project_key}")],
            # ⚙️ Config used to sit here pointing at estate:project_config, which has
            # no renderer of any name (quarantined in test_every_button_dispatches
            # _UNBUILT). Dropped rather than replaced: the obvious replacement was a
            # "🗂 All projects" tile, but the spine now carries 🗂 Projects on every
            # panel, and a tile that repeats the spine is the duplicate-callback
            # defect (memory: cockpit-no-duplicate-callbacks). Spine wins, tile gives way.
            [("📋 Missions", f"estate:missions:{project_key}")],
        ]

    buttons = with_nav(buttons)
    return "\n".join(lines), buttons


# ═══════════════════════════════════════════════════
# Onboarding flow
# ═══════════════════════════════════════════════════

def render_onboarding() -> Tuple[str, List[ButtonRow]]:
    """Client/project onboarding wizard — step by step."""
    from gateway.operator_shell.panel_chrome import nav, with_nav

    lines = [
        "➕ *Onboard New Project*",
        "",
        "Add a new project or client to the estate.",
        "",
        "*What kind of project?*",
    ]

    buttons: List[ButtonRow] = [
        [("🆕 New Product", "estate:onboard:new_product"),
         ("👤 Client Project", "estate:onboard:client")],
        [("🔍 Discover Repos", "estate:onboard:discover"),
         ("📋 From Template", "estate:onboard:template")],
        [("🏠 Cancel", "estate:refresh")],
    ]
    buttons = with_nav(buttons)
    return "\n".join(lines), buttons


def render_onboard_discover() -> Tuple[str, List[ButtonRow]]:
    """Auto-discover unregistered git repos in ~/Documents/code."""
    from gateway.operator_shell.panel_chrome import nav, with_nav

    registered_keys = {p["key"] for p in load_registry().get("projects", [])}
    registered_repos = set()
    for p in load_registry().get("projects", []):
        for r in p.get("repos", []):
            registered_repos.add(r)

    discovered = []
    for d in sorted(CODE.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        if "worktree" in d.name.lower():
            continue
        git_dir = d / ".git"
        if not git_dir.is_dir():
            continue
        if d.name in registered_repos:
            continue

        branch = _repo_branch(d)
        commit_age = _repo_last_commit(d)
        has_ci = (d / ".github" / "workflows").is_dir()

        discovered.append({
            "name": d.name,
            "branch": branch,
            "commit_age": commit_age,
            "has_ci": has_ci,
        })

    if not discovered:
        lines = [
            "🔍 *Discover Repos*",
            "",
            "✅ No unregistered repos found.",
            "All git repos in ~/Documents/code are tracked.",
            "",
            "[🏠 Home](estate:refresh)",
        ]
        return "\n".join(lines), [nav()]

    lines = [
        "🔍 *Discovered Repos*",
        "",
        f"Found {len(discovered)} unregistered repo(s):",
        "",
    ]
    for d in discovered:
        ci_mark = " · CI" if d["has_ci"] else ""
        lines.append(f"• *{d['name']}* — `{d['branch']}` · {d['commit_age']}{ci_mark}")

    lines.append("")
    lines.append("_Tap a repo to onboard it, or 'Add All' to register everything._")

    buttons: List[ButtonRow] = []
    row: ButtonRow = []
    for d in discovered[:8]:  # Telegram 8-button limit
        row.append((f"➕ {d['name']}", f"estate:onboard:add:{d['name']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([("📦 Add All", "estate:onboard:add_all")])
    buttons = with_nav(buttons)
    return "\n".join(lines), buttons


def onboard_project(repo_name: str, project_type: str = "incubating") -> dict:
    """Register a new project from a discovered repo.

    Returns the new project dict.
    """
    repo_path = CODE / repo_name
    if not repo_path.is_dir():
        return {"error": f"Repo {repo_name} not found"}

    branch = _repo_branch(repo_path)
    has_ci = (repo_path / ".github" / "workflows").is_dir()
    is_client = "client" in project_type

    # Detect risk level from repo content
    risk = "low"
    try:
        # Quick scan for money/identity patterns
        for f in repo_path.rglob("*.py"):
            if f.stat().st_size > 50000:
                continue
            try:
                content = f.read_text(errors="replace")[:2000].lower()
                if any(w in content for w in ("stripe", "payment", "charge", "billing", "subscription")):
                    risk = "money"
                    break
                if any(w in content for w in ("passport", "kyc", "pii", "identity", "gdpr", "ssn")):
                    risk = "identity"
                    break
            except Exception:
                continue
    except Exception:
        pass

    # Generate key from repo name
    key = repo_name.replace("-", "_").replace(".", "_")

    # Determine display name
    name = repo_name.replace("-", " ").replace("_", " ").title()
    # Clean up common suffixes
    for suffix in [" Platform", " Py", " Ts", " Api", " Cli"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]

    project = {
        "key": key,
        "name": name,
        "type": "client" if is_client else project_type,
        "status": "active" if has_ci else "incubating",
        "risk": risk,
        "repos": [repo_name],
        "primary_repo": repo_name,
        "ci_provider": "github" if has_ci else None,
        "fly_app": None,
        "self_improvement": False,
        "description": f"{'Client' if is_client else 'New'} project — {repo_name}",
        "owner": "Client" if is_client else "Chidi",
        "onboarded": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tags": (["client"] if is_client else []) + (["ci"] if has_ci else []),
    }

    # Add to registry
    reg = load_registry()
    reg["projects"].append(project)
    save_registry(reg)

    return project
