# Phase 3, T4: Claude Code telemetry, dashboards and audit reports (live check)

| | |
|---|---|
| Ticket | GUARD-3, T4 |
| Date | 2026-11-26 |
| Branch | `feature/GUARD-3-telemetry` |
| Script | `deploy/collector/run_telemetry_live.py` (60 checks, 0 failed in the last run; first run 40 checks before metrics were added) |
| Stack | Local Docker, the pinned images of `deploy/collector/` (collector 0.165.0, ClickHouse 26.8.45.6, Grafana 13.3.1). Run under the compose project names `bmad-guard-t4` (first run, and the migration of an existing stack) and `bmad-guard-t4m` (fresh volumes, final run), so the volumes of the T2 stack were not touched. |
| Claude Code | 2.1.332 (`claude -p`, model `haiku`, one turn). Logged in on this machine, the model call worked. |
| BMAD | 6.12.4, installed in a throwaway temp project like `scripts/install_test.py` does; `setup_guard.py` run; temp HOME |
| Data | One fake developer (`t4-live`), fake files, a fake secret and a fake phone number built or read from the test suite. Removed at the end. |

## Answer

Guard events, Claude Code events and Claude Code metrics all arrive in ClickHouse and Grafana with `user` taken from the developer's key. No
prompt text is stored. Both reports give the expected answer on test data.

## What was checked

| Area | Result |
|---|---|
| L1 hook through the installed `pre_tool_use.py` | `git push --force` blocked (`CMD_GIT_FORCE_PUSH`, exit 2); `echo hello` allowed |
| L2 pre-commit through real `git commit` | clean commit allowed; fake phone number warns (`COMMIT_PHI`, non-HIPAA project); fake secret blocked (`COMMIT_SECRET`) |
| Bypass | The refused tree committed again with `--no-verify`; one new tree committed with `--no-verify` |
| Review record | The hook wrote `_bmad-output/guard/reviews/9-9.json` and sent `REVIEW_RECORDED` with path and hash; one record `hand-made.json` written by hand |
| Events in ClickHouse | A block, a warn, an allow with `tree`, a block with `tree`, and a `REVIEW_RECORDED` with `target_path` and `target_sha256`, all with `User = t4-live`. Only one distinct user in the table. |
| Guard event fields stored | `agent, detail, event_id, file_count, guard, reason_code, rule, session_id, stage, target_path, target_sha256, tool, tree, user, verdict, warnings`. No command text, no matched value, no file content (a search of the stored attributes for the fake token prefix, the command word `force` and the phone digits found only a hex tree id that happens to contain `555`). |
| Claude Code events | 77 events in one session, user `t4-live` (from the key, through the helper). Names in `Body`: `claude_code.plugin_loaded`, `hook_execution_start`, `hook_execution_complete`, `hook_registered`, `user_prompt`, `assistant_response`, `api_request`, `managed_settings_resolved`, `mcp_server_connection`. `ServiceName` is `claude-code`, `EventName` is empty. |
| Prompt text | The marker in the prompt is stored nowhere (0 rows in `Body`, log or resource attributes). `prompt`, `prompt_text` and `response` are `<REDACTED>` or empty. |
| Scrub | `user.email`, `user.account_uuid`, `user.account_id`, `user.id`, `organization.id`, `workspace.host_paths` absent from every row. A made-up record that carries all of them, plus `host.name` and `host.id`, was stored without them; `session.id` and `terminal.type` stayed; `user` was the key's, not the one in the record (`someone-else`). |
| Grafana (API, `/api/ds/query` as `grafana_reader`) | All 11 panels run and show rows: four for guard events and Claude Code events (3, 2, 1, 9 rows) and seven metric panels per user (sessions 1, tokens 4, cost 1, lines of code 1, commits and PRs 2, edit tool decisions 1, active time 1). |
| Metrics (real session) | `claude_code.session.count`, `token.usage` (input, output, cacheRead, cacheCreation), `cost.usage`, `active_time.total` arrived in `guard.otel_metrics_sum` with `Attributes['user'] = t4-live` (the only user in the table). Datapoint attribute keys: `anthropic.custom_base_url, anthropic.plan, anthropic.provider, app.entrypoint, app.version, effort, model, query_source, session.id, start_type, terminal.type, type, user`. Resource keys: `host.arch, os.type, os.version, service.name, service.version`. None of the personal attributes is present. |
| Metrics (made-up datapoints) | One counter with `user.email`, `user.account_uuid`, `user.account_id`, `user.id`, `organization.id` on the datapoint and `host.name`, `host.id` on the resource, sent as the fake developer: stored with only `run_id` and `type`, resource `service.name` only, `user` = the key's (not `someone-else`). Four more counters (lines of code, commits, pull requests, edit tool decision) were sent so those panels have rows: a reply-only session does not produce them. |
| Report `commits` (range from the setup commit) | Exit 1, lists exactly the two bypassed commits: `no-event` (the new tree) and `blocked-only` (the refused tree). The two commits made with the hooks on are not listed. |
| Report `reviews` | Exit 1, lists only `hand-made.json`. The hook-written record is clean. |

Claude Code attribute keys seen in the live session (log attributes): `additional_context_chars, agent_path_count,
anthropic.custom_base_url, anthropic.plan, anthropic.provider, cache_*_tokens, client_request_id, command_path_count,
cost_usd, cost_usd_micros, duration_ms, effort, enabled_via, event.name, event.sequence, event.timestamp, has_hooks,
has_mcp, hook_*, host_owned_mcp, initial_user_message_chars, input_tokens, is_plugin, managed_only,
managed_settings.*, marketplace.name, message.uuid, model, num_*, output_tokens, plugin.*, plugin_id_hash, prompt,
prompt.id, prompt_length, prompt_text, query_source, request_id, response, response_length, safe_mode, server_scope,
session.id, skill_path_count, speed, status, stdout_chars, system_message_chars, terminal.type, total_duration_ms,
transport_type, ttft_ms, user`. Resource attributes: `host.arch, os.type, os.version, service.name, service.version`.
`anthropic.custom_base_url` holds `false` (a flag, not a URL).

## Follow-up changes (Shameer's review)

| Change | Evidence |
|---|---|
| Session start and pre-push send the queue once | `tests/test_flush_hooks.py` (10 tests): sent at session start; no endpoint gives no network, no message, queue unchanged; collector down gives exit 0 and stays under the timeout; wrong key keeps events; pre-push sends the queue and its own event; second push in the backoff is silent and fast; pre-commit never sends. The old test "the hook never sends events" was changed: pre-push now warns once when the collector is down. |
| Metrics on | Template `OTEL_METRICS_EXPORTER=otlp` and the six metric switches set on purpose. Collector metrics pipeline with the same auth, user stamp and scrub. Tables `guard.otel_metrics_sum` and `otel_metrics_gauge` from `clickhouse/init/02-metrics.sh`, 180-day TTL, INSERT only for `otel_writer`, collector still `create_schema: false`. Checked on fresh volumes (init) and on an existing stack (`02-metrics.sh` by hand, run twice). The exporter's own DDL was generated in a scratch database and compared by column count (21 and 23). |
| `keys.py init` makes an empty key file | A first `docker compose up` on fresh volumes started the collector without error (empty file, mode 644; `tests/test_collector_keys.py`). Mode 644 and not 600 on purpose: the collector container runs as another user and must read the file; it holds no secret when empty and only hashes after `add`. |
| `commits` range documented | README, script docstring and `--help`. |

## Design: how the reports match (ADR 0003 update)

Events had no commit SHA, story or record hash. Pre-commit runs before the commit exists. Smallest change that works,
inside ADR 0003's allowed fields (target: path and SHA-256; where: commit):

- Pre-commit events carry `tree`, the id of the staged tree (`git write-tree`). The commit that follows holds that tree.
- `REVIEW_RECORDED` carries the record's path and the SHA-256 of the record file as written.

The rejected options (user and time window; a post-commit hook; the reviewed SHA alone) are in the ADR. A commit counts as
seen only when its tree has an **allow** event. A tree with only block events means the hook refused it and the commit
came from another route: the report calls that `blocked-only`. This case was in the live test.

## Not verified, or limits found

- **The managed-settings file itself was not deployed.** Writing to the system path needs admin rights and is T5's rollout
  test. The same variables were set in the child process and the helper was passed with `claude --settings <file>`;
  the helper worked from there. Whether the helper runs from the managed file's path with a space on macOS is not verified.
- `OTEL_METRICS_INCLUDE_ACCOUNT_UUID=false` is in the template. The docs say it covers metrics and events; this login
  carries no account ids anyway, so the effect is not seen live (the collector deletes them either way).
- `user.id`, `organization.id`, the email and `workspace.host_paths` did not appear in this session (no organization on
  this login, no workspace paths). Their scrub rules are proven by the made-up record, not by real Claude Code output.
- Pre-commit events are queue-only (by design). They are now sent at the next session start, at the next pre-push, by a
  Claude Code tool call or with `guard_events.py flush`. A developer who only commits in a terminal and never pushes or
  starts Claude Code still sends nothing; run the `commits` report after pushes.
- Metrics: only sum and gauge tables exist. A histogram or summary from a future Claude Code would be refused by the exporter.
- The metric panels' lines, commits, PRs and tool-decision rows come from made-up datapoints, not from a real coding session.
- The new metric tables are not in the backup script's list (`backup.sh` backs up `guard.guard_events` only): not changed here; listed as an open item.
- Warnings panel: shows data only when there are `warn` events (it did in the last run, from `COMMIT_PHI`).
- TLS and the company-network host are not part of this check (T2 hosting plan).

## Problems found

- None in the guard code. Two mistakes of mine in the live script were fixed during the work: a wrong expected event
  count (the L1 hook sends the queue itself, so `flush` sends only the rest), and a check that needed the collector to be
  running before the fake user existed.
- A fresh `docker compose up` without `local/devkeys.htpasswd` stopped the collector. Fixed: `keys.py init` creates an empty file.
- `backup.sh` covers `guard.guard_events` only, so the metrics tables are not in the backup. Not changed (open item).

## Left running or behind

- Containers: none (`docker compose down` done).
- Volumes kept on purpose (fake data only): `bmad-guard-t4_*` (clickhouse-data, clickhouse-backups, grafana-data; also holds a scratch database `scratch` with the exporter's generated tables) and `bmad-guard-t4m_*` (same three). The T2 volumes `bmad-guard-collector_*` were not used or changed.
- `deploy/collector/local/devkeys.htpasswd`: exists and is empty (the fake user was removed, as the script does at the end). `secrets.local.ini` was
  created (git-ignored, random passwords).
- The throwaway project folder was deleted by the script.
