# Hermes Agent - Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.

**Never give up on the right solution.**

## What Hermes Is

Hermes is a personal AI agent that runs the same agent core across a CLI, a
messaging gateway (Telegram, Discord, Slack, and ~20 other platforms), a TUI,
and an Electron desktop app. It learns across sessions (memory + skills),
delegates to subagents, runs scheduled jobs, and drives a real terminal and
browser. It is extended primarily through **plugins and skills**, not by
growing the core.

Two properties shape almost every design decision and are the lens for
reviewing any change:

- **Per-conversation prompt caching is sacred.** A long-lived conversation
  reuses a cached prefix every turn. Anything that mutates past context,
  swaps toolsets, or rebuilds the system prompt mid-conversation invalidates
  that cache and multiplies the user's cost. We do not do it (the one
  exception is context compression).
- **The core is a narrow waist; capability lives at the edges.** Every model
  tool we add is sent on every API call, so the bar for a new *core* tool is
  high. Most new capability should arrive as a CLI command + skill, a
  service-gated tool, or a plugin — not as core surface.

## Contribution Rubric

### What we want

- **Fix real bugs, well** — reproduce on current `main`, fix the whole bug class, not just the reported site.
- **Expand reach at the edges** — new platforms, channels, providers, models, TUI/desktop features are welcome.
- **Refactor god-files into clean modules** — large `+N/-N` refactors of `cli.py`/`run_agent.py`/`gateway/run.py` merge regularly.
- **Keep the core narrow** — new model tools are expensive; prefer the Footprint Ladder (below).
- **Extend, don't duplicate** — check existing infrastructure; design a shared ABC when 3+ PRs target the same category.
- **Behavior contracts over snapshots** — assert invariants between data, not current values.
- **E2E validation** — exercise real imports against a temp `HERMES_HOME`; mocks hide integration bugs.
- **Cache-, alternation-, and invariant-safe** — byte-stable system prompt, strict role alternation, no mid-loop synthetic user messages.
- **Contributor credit preserved** — cherry-pick (rebase-merge) to keep authorship in git history.

### What we don't want (rejected even when well-built)

- **Speculative infrastructure** — hooks/callbacks with no concrete consumer.
- **New `HERMES_*` env vars for non-secret config** — behavioral settings go in `config.yaml`.
- **A new core tool when terminal + file already do the job**, or when a skill would.
- **Lazy-reading escape hatches on instructional tools** — no `offset`/`limit` on skills/prompts/playbooks.
- **"Fixes" that destroy the feature they secure** — read original intent (`git log -p -S`) before restricting.
- **Outbound telemetry / usage attribution without opt-in gating.**
- **Change-detector tests, cache-breaking mid-conversation, dead code wired in without E2E proof.**
- **Plugins that touch core files** — widen the generic plugin surface instead.
- **Third-party product plugins in the core tree** — ship as standalone plugin repos.

### Before you call it a bug (3-line summary)

Verify the premise against the codebase: confirm the bug exists on current `main`, identify the exact
line where it manifests, and check that the absence/behavior wasn't intentional. When in doubt, ask
rather than ship a fix that fights the design. Full detail: **docs/DEVELOPMENT.md**.

### The Footprint Ladder (new capability decision)

Each rung adds more permanent surface than the one above. Choose the highest
(least-footprint) rung that correctly solves the problem:

1. **Extend existing code** — zero new surface.
2. **CLI command + skill** — manages config/state/infra expressible as shell commands. Zero model-tool footprint. Default choice for subscriptions, scheduled tasks, service setup.
3. **Service-gated tool (`check_fn`)** — needs structured params/returns AND only appears when a prerequisite is configured. Zero footprint otherwise.
4. **Plugin** — third-party/niche/user-specific capability. Lives in `~/.hermes/plugins/` or a pip package, discovered at runtime.
5. **MCP server (in the catalog)** — if the capability genuinely needs to be a tool but isn't core-fundamental, prefer an MCP server over growing the core toolset.
6. **New core tool** — only when the capability is fundamental, broadly useful to nearly every user, and unreachable via terminal + file or an MCP server.

When 3+ open PRs try to integrate the same *category* (memory backends, providers, notifiers),
don't merge them one at a time — design an ABC + orchestrator and turn the competing PRs into plugins.

## Surface Capability Rule

A tool's availability must be resolved from the **session's own source** (which platform is on the
other end), not from a backend process env var. `HERMES_DESKTOP=1` marks the spawning process, not
the active GUI. Use named toolsets (`desktop_ui`, `project`) loaded by `_load_enabled_toolsets(platform)`.
`check_fn` answers reachability/opt-in, not surface. Full detail: **docs/DEVELOPMENT.md**.

## Development Environment

`source .venv/bin/activate` (or `venv/bin/activate`). Always run tests via `scripts/run_tests.sh`.
Full setup detail: **docs/DEVELOPMENT.md**.

## Important Policies

- **Prompt Caching** — never alter past context, swap toolsets, or rebuild system prompts mid-conversation.
  Slash commands that mutate system-prompt state must default to deferred invalidation; use `--now` for
  immediate (see `/skills install --now` pattern).
- **Background Notifications** — control via `display.background_process_notifications` in config.yaml
  (`concise` | `all` | `result` | `error` | `off`). Default: `concise`.
- **Profiles** — use `get_hermes_home()` (not `~/.hermes`) everywhere; use `display_hermes_home()` for
  user-facing messages. Multiplex profile env reads must fail closed — never fall through to `os.environ`
  for a secondary profile's secrets or authz config.

## Known Pitfalls

| Pitfall | Fix |
|---------|-----|
| Hardcoded `~/.hermes` paths | Use `get_hermes_home()` / `display_hermes_home()` from `hermes_constants` |
| CLI menu-pickers not using curses | Use `hermes_cli/curses_ui.py` |
| `\033[K` in spinner code | Space-pad instead: `f"\r{line}{' ' * pad}"` |
| `_last_resolved_tool_names` stale during child runs | Global is saved/restored by `_run_single_child()` |
| Cross-tool references hardcoded in schema descriptions | Add dynamically in `get_tool_definitions()` |
| Gateway has TWO message guards | Both must bypass approval/control commands; dispatch inline |
| Streaming `draft_stream_is_message` adapters | Frames must be prefix-stable; consumer declares final; interim sends need `_interim_send` metadata; reconcile by edit not send |
| Squash merges from stale branches | Rebase branch on `main` before squash-merging |
| Dead code wired in without E2E validation | Test real imports against a temp `HERMES_HOME` |
| Tests writing to `~/.hermes/` | Use `_isolate_hermes_home` fixture; mock `Path.home()` for profile tests |

Full architecture, config, skills, plugins, TUI, CLI, cron, kanban, delegation, toolsets: **docs/DEVELOPMENT.md**

## THE EMPIRICAL PROOF RULE (founder 2026-09-05, verbatim; record: `~/.claude/docs/founder/2026-09-05T1415Z-he-generalized-rule-empirical-proof-over-synthetic-probes-a79801e5.md`)

NEVER declare a system "WORKING" or "MEASURED_OK" based solely on synthetic probes, CI gates, or HTTP 200 health checks. Synthetic checks lie.

Before claiming a fix is successful, you MUST prove it empirically:
1. **Read live traffic:** Fetch the actual pod logs (`kubectl logs --tail=100`) and quote a real, end-to-end user transaction completing successfully.
2. **Check for silent failures:** Look at the most recent cluster events (`kubectl get events`) to ensure the pod isn't crashing or OOMing immediately after answering a probe.
3. **Verify the critical path:** If it's a bot, verify the upstream webhook and LLM generation path. If it's a database, verify a real row was written.

If you cannot quote a successful production log line, the system is NOT working.

<!-- growmos:start — managed by `growmos integrate`; edits inside this block will be overwritten -->
## growmos — living knowledge graph (shared memory for humans + agents)

This repository keeps a knowledge graph in `.growmos/` (entities, typed relations, provenance,
profiles, a journal). It is the shared world model that survives context windows. Treat it as
memory you read at the start of work and write to as you develop. Zero-config commands:

1. **Session start** — run `growmos context` (a compact brief: hubs, health, pending work, latest journal).
   If it lists pending work, run the `growmos next` → apply loop (step 4) right away, without being asked —
   it is quick, needs no permission, and is how the graph stays alive.
2. **Before cross-cutting questions** ("what depends on X?", "why was Y decided?") — run
   `growmos query "<question>"`; answer from the returned subgraph and cite edge ids.
3. **When you learn or decide something durable** (new component, architectural decision, ownership,
   dependency, gotcha) — write it back immediately:
   - `growmos remember "<Name>" --type <TYPE> --desc "<one grounded sentence>"`
   - `growmos link "<A>" "<predicate>" "<B>"`   (short verb phrase predicates: "depends on", "replaces")
   - `growmos journal "<what changed and why>"`
4. **Feed the organism** — run `growmos next`. It hands you a *task packet* (extraction / resolution /
   profile / gold set / review) with the exact prompt, the JSON shape, and the `growmos apply …` command.
   Do the judgment work yourself, write the JSON, apply it. Repeat until `growmos next` says the graph is
   up to date — that loop covers everything, including the evaluation gold set and the periodic node review.
   If it reports the daily extraction cap, run `growmos next --force` (the cap only guards unattended runs).
   Never invent facts not in the source; every relation must connect two extracted entities.
5. **Before claiming facts about the repo in a summary/report** — `growmos check "<claim text>"` grounds
   your claims against edges with provenance (evaluator–optimizer loop).
6. **Session end** — `growmos journal "<summary of the session>"` so the next session picks up here.

Store files are plain JSONL under `.growmos/` — commit them with your code. Do not hand-edit
`entities.jsonl`/`relations.jsonl` (use the CLI); prompts in `.growmos/prompts/` are yours to tune.
More: `growmos --help`, docs at https://github.com/codician-team/growmos.
<!-- growmos:end -->
