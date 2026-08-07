# Unified Cockpit — Build Spec

## Goal

Replace the fragmented operator shell UX with a single unified cockpit.
Type `otto` — one screen shows everything. Every action gives feedback.
Every code path is tested end-to-end.

## Files

| File | Change |
|------|--------|
| `gateway/operator_shell/mission.py` | Rewrite as Home panel — warm, clear, unified |
| `gateway/operator_shell/sdlc.py` | NEW — consolidated SDLC pipeline view |
| `gateway/operator_shell/panel_chrome.py` | Fix nav spine labels |
| `tests/test_unified_cockpit.py` | NEW — end-to-end tests for every button |

## Home Screen Layout (what `otto` shows)

```
🏠 *Otto*                              🟢 all good

—— *in flight* ——
🚀 Prospector US yield · flying
💻 fix-stale-contracts · plotting

—— *blocked* ——
⚠️ Store checkout · awaiting approval

—— *SDLC* ——
1️⃣ 2 active missions · 3️⃣ Fleet: 15 open PRs
5️⃣ CI: ✅ passing · 6️⃣ RSI: 🟢 armed
[💻 Full SDLC pipeline]

—— *quick actions* ——
[🔧 Restart gateway] [📊 Status]
[✍️ Assign task]     [📥 Inbox]
[⏸ Pause estate]    [❓ Help]

—— *daemons* ——
[♻️ Coordinator] [♻️ Gateway]
[▶️ Watchdog]    [▶️ TIE review]

[🏠 Home] [⚡ Actions] [💻 SDLC] [🗺 Browse] [❓ Help]
```

## SDLC Panel (what tapping `[💻 Full SDLC pipeline]` shows)

```
💻 *SDLC Pipeline*

1️⃣ *Assign*
[✍️ New task] [cc <what to build>]

2️⃣ *Board*
🚀 Prospector US yield · flying · M3: Build yield pipeline
🔧 fix-stale-contracts · plotting · M0: Spec
[📋 All missions] [📥 Inbox]

3️⃣ *Fleet*
prospector · main ✅ · 44 PRs · 15 open
hermes-agent · main ✅ · 2 PRs today
[🚀 Open repos]

4️⃣ *Review*
📥 3 decisions waiting
[📸 Diffs] [📥 Approve]

5️⃣ *Ship*
🏗 CI: prospector ✅ · hermes-agent ✅
[🏗 All builds] [🛒 Store]

6️⃣ *Learn*
🧠 RSI: 🟢 armed · 73% autonomy
[🧠 RSI panel]

[🏠 Home] [⚡ Actions] [💻 SDLC] [🗺 Browse] [❓ Help]
```

## Navigation Spine (panel_chrome.py change)

```python
_NAV_SPINE = [
    ("🏠 Home", "estate:refresh"),
    ("⚡ Actions", "estate:run"),
    ("💻 SDLC", "estate:sdlc"),
    ("🗺 Browse", "estate:find"),
    ("❓ Help", "estate:help"),
]
```

## Code Changes Detail

### mission.py → Home panel
- Keep: `render_mission_card()` function returns (text, ok, buttons)
- Keep: estate health check, burn, concerns, blocker
- Add: in-flight work from code_remote + missions
- Add: SDLC summary line with `[💻 Full SDLC pipeline]` button
- Add: daemon controls row (coordinator, gateway, watchdog, TIE)
- Change: headline from "🎛 *Cockpit*" to "🏠 *Otto*"
- Change: quick actions to be more user-friendly (was operator-focused)

### sdlc.py (NEW)
- Function: `render_sdlc()` → (text, buttons)
- Pull data from: code_remote, missions, fleet, builds, inbox, rsi_panel
- Reuse existing render functions — just compose them
- Each section has a button to open the full panel
- Graceful degradation: if a data source fails, show "—" not crash

### panel_chrome.py
- Change `_NOW`, `_RUN`, `_TUNE`, `_MAP` labels
- Add `_SDLC` entry
- Keep callback values the same for backward compatibility

### estate.py
- Add `if action == "sdlc"` handler that calls `render_sdlc()`

### natural_ops.py
- Add "sdlc" and "pipeline" as bare triggers → sdlc action

## Tests (tests/test_unified_cockpit.py)

```python
class TestHomeScreen:
    def test_renders_without_crashing(self): ...
    def test_has_all_five_nav_buttons(self): ...  # Home, Actions, SDLC, Browse, Help
    def test_shows_in_flight_work_when_present(self): ...
    def test_shows_blocked_items_when_present(self): ...
    def test_shows_sdlc_summary(self): ...
    def test_quick_actions_include_restart_and_status(self): ...
    def test_daemon_controls_present(self): ...

class TestSdlcPanel:
    def test_renders_without_crashing(self): ...
    def test_has_all_six_pipeline_stages(self): ...
    def test_assign_section_has_button(self): ...
    def test_board_shows_active_missions(self): ...
    def test_fleet_shows_repo_status(self): ...
    def test_review_shows_decisions(self): ...
    def test_ship_shows_ci_status(self): ...
    def test_learn_shows_rsi(self): ...
    def test_has_all_five_nav_buttons(self): ...

class TestNavigation:
    def test_nav_spine_has_five_entries(self): ...
    def test_nav_spine_labels_are_readable(self): ...  # No cryptic symbols
    def test_every_nav_callback_starts_with_estate(self): ...
```

## Acceptance

- `python3 -m pytest tests/test_unified_cockpit.py -v` — all tests pass
- `python3 -c "from gateway.operator_shell.mission import render_mission_card; t, ok, b = render_mission_card(); assert len(t) > 0"` works
- `python3 -c "from gateway.operator_shell.sdlc import render_sdlc; t, b = render_sdlc(); assert len(t) > 0"` works
- `python3 -c "from gateway.operator_shell.natural_ops import match_natural_op; assert match_natural_op('sdlc').action == 'sdlc'"` works

## Out of Scope

- Data freshness (live probes already exist, reused as-is)
- 36 existing panels (unchanged, still reachable via Browse/Search)
- Slash commands (unchanged)
- `menu.py` cleanup (separate PR)
- Moving modules between files (separate PR)
