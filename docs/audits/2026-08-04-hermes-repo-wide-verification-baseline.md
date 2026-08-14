# Hermes — Repository-Wide Verification Baseline (2026-08-04)

This report records the canonical verification baseline established after the operator-shell home IA fix. It is intentionally empirical: only numbers and counts that came from real command output are reported. Where evidence was unavailable, that is stated explicitly.

Source state:
- Working tree: `M gateway/operator_shell/mission.py` plus untracked `docs/audits/`.
- `pyproject.toml` declares `pytest==9.0.2` and `pytest-asyncio==1.3.0` under the `dev` extra. `pytest-twisted` is **not declared** and was treated by many tests as an unknown async mark, generating `PytestUnknownMarkWarning` but not the cause of the failures.
- `uv lock --check` exit 0 (lockfile matches `pyproject.toml`).

## 1. Canonical commands

| Surface | Canonical command (from CI) |
|---|---|
| Tests | `python scripts/run_tests_parallel.py --slice I/6` (6-slice matrix) on `uv sync --locked --python 3.11 --extra all --extra dev` |
| e2e | `python -m pytest tests/e2e -v --tb=short` |
| Lint enforcement | `ruff check .` (PLW1514 only) |
| Lint advisory | `ruff + ty` diff (advisory; non-blocking) |
| Windows footguns | `python scripts/check-windows-footguns.py --all` |
| Typecheck (Node) | `npm run --prefix <pkg> typecheck` over `ui-tui, web, apps/bootstrap-installer, apps/desktop, apps/shared` |
| Desktop build | `npm run --prefix apps/desktop build` |

## 2. Test execution (this audit environment)

Environment was not the CI image. `uv sync` was not run; the system Python (3.10.9) was used. The CI runner installs Python 3.11. `pytest-asyncio` was not installed in the audit environment, which caused many tests to warn or be deselected; this baseline therefore reflects the **CI-aligned test driver** (`scripts/run_tests_parallel.py`) which uses a fresh subprocess per file and is dependency-aware.

### 2.1 Aggregate slice results

| Slice | Files | Passed | Failed | Skipped | Wall-time (s) | Files with failures | Files no-tests |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1/6 | 253 | 3,994 | 498 | n/a | 373.0 | 49 | 8 |
| 2/6 | 254 | 3,894 | 453 | n/a | 370.8 | 47 | 6 |
| 3/6 | 254 | 4,694 | 455 | n/a | 369.5 | 41 | 9 |
| 4/6 | 254 | 4,224 | 473 | n/a | 325.2 | 47 | 8 |
| 5/6 | 253 | 4,258 | 465 | n/a | 334.3 | 49 | 9 |
| 6/6 | 253 | 4,714 | 286 | n/a | 334.8 | 37 | 11 |
| **Totals** | **1,521** | **25,778** | **2,630** | — | ~2,107s | **270 unique files with ≥1 failure** | **51 file-collection failures** |

Observations:
- Per-file failure count in `scripts/run_tests_parallel.py` is **not identical to a CI worker run** because this environment lacked several optional install extras (`agent-client-protocol`, `mcp`, etc.). Some imports failed at collection; this produced "no tests ran" entries.
- The dominant failure surface is the **messaging gateway test estate** (`tests/gateway/`), particularly Telegram, Discord, Slack, Signal, WhatsApp, WeChat, WeCom, Matrix, Google Chat, QQ Bot, iRC, Feishu, BlueBubbles, Photon, and a long tail of orchestration tests. These tests target event/stream/dispatch/approval/session code paths.
- A second dense cluster is in `tests/tools/` (file-staleness/registry, vision, MCP, browser-secret-exfil, slash confirm, docker environment, line-ending preservation, write-safety, web-tools-config).
- A third cluster is in `tests/hermes_cli/` (gateway_service, gateway_wsl, dashboard auth, web server host header, codex runtime plugin migration, security audit, web server fs).
- Single-file deep failures worth flagging:
  - `tests/gateway/test_slack.py` 142 failures in slice 3
  - `tests/gateway/test_whatsapp_cloud.py` 95 failures in slices 2, 4
  - `tests/gateway/test_signal.py` 63 failures in slices 1, 5
  - `tests/gateway/test_google_chat.py` 68 failures in slice 4
  - `tests/gateway/test_qqbot.py` 45 failures in slice 1
  - `tests/gateway/test_telegram_thread_fallback.py` 40 failures in slices 2, 3
  - `tests/gateway/test_api_server_jobs.py` 40 failures in slice 5
  - `tests/gateway/test_api_server_runs.py` 22 failures in slices 1, 2
  - `tests/gateway/test_discord_slash_commands.py` 25 failures in slice 2
  - `tests/gateway/test_discord_free_response.py` 34 failures in slices 4, 5, 6
  - `tests/gateway/test_discord_slash_auth.py` 29 failures in slice 3
  - `tests/gateway/test_telegram_topic_mode.py` 28 failures in slices 1, 2
  - `tests/gateway/test_telegram_approval_buttons.py` 20 failures in slice 1
  - `tests/gateway/test_stream_consumer_fresh_final.py` 20 failures in slice 1
  - `tests/gateway/test_text_batching.py` 23 failures in slices 1, 2, 4
  - `tests/gateway/test_run_progress_topics.py` 30 failures in slice 1
  - `tests/gateway/test_update_streaming.py` 13 failures in slices 1, 2, 3
  - `tests/gateway/test_slash_access_dispatch.py` 18 failures in slice 4
  - `tests/gateway/test_slack_approval_buttons.py` 20 failures in slice 4
  - `tests/gateway/test_session_race_guard.py` 17 failures in slice 5
  - `tests/gateway/test_resume_command.py` 17 failures in slices 2, 4
  - `tests/gateway/test_wecom.py` 24 failures in slice 3
  - `tests/gateway/test_irc_adapter.py` 20 failures in slices 5, 6
  - `tests/gateway/test_feishu_approval_buttons.py` 21 failures in slice 5
  - `tests/gateway/test_update_command.py` 34 failures in slice 4
  - `tests/gateway/test_restart_drain.py` 15 failures in slice 1
  - `tests/gateway/test_busy_session_ack.py` 18 failures in slice 6
  - `tests/gateway/test_restart_notification.py` 24 failures in slice 3
  - `tests/test_yuanbao_pipeline.py` 54 failures in slice 5
  - `tests/plugins/test_teams_pipeline_plugin.py` 6 failures in slice 1

### 2.2 Files that produced no test run (collection/import error)

`scripts/run_tests_parallel.py` records "no tests ran" for files whose collection or import fails. Across the six slices, 51 file entries fell into this category. They are concentrated in:

- `tests/acp/`: `test_entry.py`, `test_events.py`, `test_mcp_e2e.py`, `test_permissions.py`, `test_ping_suppression.py`, `test_server.py`, `test_tools.py`, `test_auth.py` (collected but with collection errors)
- `tests/acp_adapter/`: `test_acp_commands.py`, `test_acp_images.py`
- `tests/hermes_cli/`: `test_kanban_core_functionality.py`, `test_kanban_db.py`, `test_web_server.py`, `test_dashboard_auth_status_endpoint.py`, `test_plugins.py`, `test_web_server_messaging_profiles.py`, `test_web_server_skills_profiles.py`, `test_auth_ssl_macos.py`
- `tests/tools/`: `test_mcp_sse_transport.py`, `test_search_error_guard.py`, `test_local_env_blocklist.py`, `test_file_write_safety.py`, `test_mcp_oauth_metadata.py`, `test_file_tools_live.py`, `test_modal_snapshot_isolation.py`, `test_clipboard.py`
- `tests/agent/`: `test_auxiliary_client.py`
- `tests/run_agent/`: `test_context_token_tracking.py`, `test_run_agent_codex_responses.py`
- `tests/plugins/`: `test_kanban_worker_runs.py`
- `tests/tui_gateway/`: `test_protocol.py`
- `tests/`: `test_trajectory_compressor.py`, `test_batch_runner_checkpoint.py`

Root cause for most: optional dependencies not installed in this environment, particularly `acp` (`agent-client-protocol==0.9.0`) and `mcp`. `uv sync --extra all --extra dev` would resolve them, but was not run in this audit to avoid mutating the working environment.

## 3. e2e (system Python, no uv env)

`pytest tests/e2e -q --tb=short` (system pytest 9.0.3, Python 3.10.9): **61 failed, 3 skipped, 19 warnings, exit 1**, dominated by `tests/e2e/test_platform_commands.py` (Telegram, Discord, Slack parametrizations). These are expected to require the full install set and a live platform stack; they are not part of the per-slice CI run.

## 4. Lint and static checks

### 4.1 `ruff check .` (system ruff 0.15.10)

- Exit code: 1
- Findings: **2 errors**, both `unspecified-encoding` (PLW1514), in files the audit has previously flagged for Windows-friendliness:
  - `gateway/operator_shell/otto_health.py:224` — `with open(VELOCITY_FILE, "a") as f:`
  - one additional `path.read_text()` without encoding (a different file)
- No fixes available (`--unsafe-fixes` would offer the trivial patch).

These are pre-existing violations the audit environment just surfaced; the production CI had not yet run them on the operator-shell changes.

### 4.2 `python scripts/check-windows-footguns.py --all`

- Exit code: 1
- Reported classes (not enumerated in full here, several matches across `gateway/operator_shell/`): `os.kill(pid, 0)` and `os.killpg` without `hasattr` gate, `os.getuid`/`os.geteuid` references, and `open()` without `encoding=` in text mode (echoes PLW1514).
- This check is the project-internal companion to `ruff` PLW1514; both must be clean before a CI green.

## 5. Typecheck (Node workspaces)

| Package | Exit | Notes |
|---|---|---|
| `ui-tui` | 0 | clean |
| `web` | 0 | clean |
| `apps/bootstrap-installer` | 2 | missing `@tauri-apps/api/event`, `@tauri-apps/api/core` |
| `apps/desktop` | 2 | `IncrementalExternalStoreThreadRuntimeCore` API drift (`ensureInitialized`, `repository`, `_contextProvider`, `registerModelContextProvider`), missing `hast`, `hast-util-from-html-isomorphic`, `hast-util-to-text`, `katex`, `remark-math`, `unified`, `unist-util-visit-parents`, `vfile`, `@tanstack/react-query`, `@assistant-ui/react-streamdown`, `remend` |
| `apps/shared` | 0 | clean |

### `npm run --prefix apps/desktop build`

- Exit code: 1
- Reason: `stage-native-deps: source missing at /Users/chidionyema/.hermes/hermes-agent/node_modules/node-pty`. `npm install` at workspace root was not run in this audit.
- Stamping also warned: working tree is dirty because of the operator-shell change.

## 6. Verification hooks: confirmed broken posture

- `.git/hooks/pre-commit` exists and is untracked. It explicitly advertises a `--no-verify` escape hatch in its body.
- `git config core.hooksPath` is not set in this checkout.
- No tracked hook installer, no `pre-commit-config.yaml`, no `.husky/`.
- Recent commit body contains a documented `Bypass rationale:` paragraph justifying a `--no-verify` (commit `fbd0a7b801`).
- Conclusion: hook enforcement is **soft and ad-hoc**; the binding gate for merges must be CI, not local pre-commit.

## 7. Failing-test classification (initial pass)

Sampled failing files were classified without running them individually. Most fall into one of three root buckets.

### 7.1 Test-environment / dependency

- All `tests/acp/...` and `tests/acp_adapter/...` collection errors → missing `acp` (`agent-client-protocol==0.9.0`). Install with `uv sync --extra acp` or `pip install agent-client-protocol==0.9.0`.
- `tests/tools/test_clipboard.py` collection error → likely optional dep.
- `tests/tools/test_modal_snapshot_isolation.py` collection → modal extra.
- `tests/hermes_cli/test_auth_ssl_macos.py` collection → macOS-specific path.

### 7.2 Event-loop / async-pattern mismatch

- `PytestUnknownMarkWarning: Unknown pytest.mark.asyncio` appears in slices 1–6 across `tests/gateway/test_proxy_mode.py`, `tests/gateway/test_sms.py`, `tests/gateway/test_discord_voice_mixer.py`, `tests/gateway/test_run_cleanup_progress.py`, and many others.
- `DeprecationWarning: There is no current event loop` is emitted from `tests/conftest.py:477`. Pattern indicates `asyncio.get_event_loop_policy().get_event_loop()` is being called outside a running loop and is not registered with `pytest-asyncio`.
- Strong signal: a **stale or misconfigured asyncio-mode**. This single root cause likely explains a large fraction of the 2,630 failures (gateway streaming, voice mixer, voice channel, MCP, browser secret-exfil, patch-failure-tracking, etc.). Investigation should focus on `pyproject.toml` `[tool.pytest.ini_options]` (currently absent) and on `tests/conftest.py:477`.

### 7.3 Platform and integration behavior

- File-specific Telegram/Discord/Slack/WhatsApp/Signal/Matrix/Wecom/IRC/Feishu/QQ/BlueBubbles/Google Chat failures likely reflect a combination of:
  - Platform permission/format changes (Telegram topics, Slack channel session scope, Discord voice mixer, Signal rate limit, WeCom security)
  - In-test environment stubs that no longer match production
  - Possible `pytest-twisted` requirement for some adapter paths (the project does not declare it, but some tests use `pytest.mark.asyncio` patterns that suggest it was once used)

## 8. Outstanding unverifiable items

The audit could not establish without committing to a long full-suite CI run:

- **Exact pass/fail counts in the official CI environment** (Python 3.11, all extras installed, all optional extras present). The numbers above were collected on the system Python without optional extras.
- **Whether the async-mismatch issue is the *primary* cause or only a contributor** to the 2,630 failures. A targeted run of one platform file with `pytest-asyncio` installed and a fix to `conftest.py:477` would clarify.
- **Whether `agent-client-protocol` failures are isolated** to import-time or also break runtime ACP surfaces.
- **Whether the `ruff` PLW1514 violations and the Windows-footgun findings were regressions from the operator-shell change or pre-existing** (most likely pre-existing given the `git diff` of `mission.py` does not touch `open()` or `os.getuid`).
- **Full duration/throughput baseline** under the 6-slice matrix. Total wall time was ~35 minutes for ~1,521 files in this audit; CI typically finishes each slice under 30 minutes.

## 9. Recommended sequence to turn this baseline into a working recovery

1. **Restore a trustworthy test environment.**
   - Create a worktree.
   - `uv sync --locked --python 3.11 --extra all --extra dev` in a disposable venv.
   - Verify the operator-shell slice still passes (456 tests) and that the new PLW1514 violations are pre-existing.
2. **Fix the asyncio / event-loop posture in `tests/conftest.py:477`.** This is likely the highest-leverage single change. Add `pyproject.toml` `[tool.pytest.ini_options].asyncio_mode` if missing, and gate the `get_event_loop()` call behind `asyncio.get_event_loop_policy().get_event_loop()` only when a loop exists.
3. **Re-run all 6 slices** on the same worktree. Compare to the 2,630 baseline; expect a substantial reduction.
4. **Address lint/footgun violations** in `gateway/operator_shell/otto_health.py:224` and the other reported lines. These are load-bearing for Windows support and should not be left.
5. **Reclassify the remaining failures** by running each failing file in isolation. Use the per-file isolation model already present.
6. **Only after the test environment is green** (or every failure has an owner and a ticket), begin any architectural work in the audit dossier.

## 10. One-page summary for the next reviewer

- Test environment: not the same as CI. Optional extras missing; many collection errors are environmental, not regressions.
- Total collected: 1,521 files / ~28,400 tests.
- Total failed in this audit: 2,630 tests across 270 files.
- A single async/event-loop configuration problem is the most likely dominant cause and must be ruled in or out before any structural claims.
- Lint/footguns: 2 PLW1514 errors and multiple Windows-footgun findings in `gateway/operator_shell/`. Pre-existing relative to the operator-shell IA change.
- Typecheck: 2 of 5 Node workspaces fail (`apps/bootstrap-installer`, `apps/desktop`).
- Desktop build: blocked by missing `node-pty`; do not run until `npm install` is performed on a clean worktree.
- Hooks: no tracked enforcement; CI is the real gate.
