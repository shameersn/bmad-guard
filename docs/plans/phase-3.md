# Phase 3 plan: Events, credentials and rollout path

> **Grilled by Shameer on 2026-11-26. Waiting for PR approval (Shameer, Manesh G).**
> The decisions from the grill session are in "Decisions (grill session, 2026-11-26)" below. Dates for the weeks are
> set when the PR is approved.

| | |
|---|---|
| Ticket | GUARD-3 |
| Time box | 4 weeks (D2) |
| Approvers | Shameer, Manesh G (PR approval) |
| Status | Grilled (2026-11-26). Waiting for PR approval. |
| Inputs | ADRs 0001–0006 (0003 event logging, 0002 enforcement layers); Phase 1 plan (open item 1, credential storage); Phase 2 plan and its todo lists; `docs/reports/phase-2-red-team.md`; `docs/hooks.md`; `docs/managed-settings-rollout.md`; `spikes/collector/REPORT.md` |

## Goal

Phase 2 made the guards block on a laptop. Phase 3 makes them **usable by a pilot, visible and rollable**. At the end of Phase 3:

1. A pilot group uses the guards from week 1, with a pilot pack: install guide, role guides, architecture with process flows, a deck and a trial-run script (T0).
2. Guard events leave the laptop: sender → OTel collector → ClickHouse → Grafana, with the user taken from the developer's key, never from the event (ADR 0003). The pilot stack runs in local Docker; the hosting plan for the company network is written, not built (D4).
3. The developer's ingest key is stored where the AI agent cannot read it (credential storage, ADR 0007).
4. The managed-settings rollout can go beyond a pilot machine: the guard scripts are installed once per machine and a project without the module is not blocked.
5. A CI check (L3) runs the same rules on one HIPAA project on GitHub. It is off by default (D8, D9).
6. The open Phase 2 checks and pilot numbers are closed or moved on, with a name and a date for each.

## Decisions (grill session, 2026-11-26)

### Scope and order

- **D1. Pilot starts in week 1**, on the Phase 2 code, with project settings (`setup_guard.py`). The managed-settings file comes later (T5).
- **D2. Time box: 4 weeks.**
  - Week 1: T0, T1, pilot start.
  - Weeks 1–2: T2, T3.
  - Week 3: T4, T5.
  - Week 4: T6, T8.
  - T7 runs the whole time, alongside the pilot.
  - If time runs short, cut T6 first, then the T7 rule todos.
- **D3. Semantic prompt check:** out of Phase 3 (Phase 4 or later).

### Stack and credentials

- **D4. Hosting:** local Docker on a dev machine for the pilot. Target: company AWS, reached by VPN only (TLS through ALB + ACM). The hosting plan is written in Phase 3; building it is not.
- **D5. Endpoint:** in the credential file `~/.config/bmad-guard/credentials`, next to the key and the developer id (`user`: the sender needs all three; ADR 0007). The environment variables `BMAD_GUARD_ENDPOINT`, `BMAD_GUARD_USER` and `BMAD_GUARD_KEY` override it (`BMAD_GUARD_COLLECTOR` is a deprecated alias of the endpoint for one release). With no endpoint, events only queue. Moving to the real host is one setup re-run.
- **D6. Owner and retention:** Shameer owns the stack (keys, rotation, backups) for the pilot. An IT contact is named before the move to real hosting. Retention: 180 days; the compliance owner approves it before Gate 3 (todo).
- **D7. Machine-level script folder:** decided in the T1 ADR. Proposal to test: `/Library/Application Support/ClaudeCode/bmad-guard/` (macOS) and `/etc/claude-code/bmad-guard/` (Linux), next to the managed settings, root-owned so the agent cannot edit them. IT copies them with the managed file; updates use the same channel.

### CI (T6)

- **D8. Small:** one HIPAA project, GitHub Actions only. Checks: review record, secret and PHI scan of the added lines. Approval fields and the tech-stack list come later, with Bitbucket.
- **D9. Skippable at setup:** `setup_guard.py --with-ci` writes the workflow; without the flag no workflow is written. `[ci] mode = "off" | "warn" | "enforce"` in `customize.toml` controls what it does. Default `off`, so pilot dev machines need nothing. A HIPAA project goes `warn` first, then `enforce`.

### Platforms and pilot group

- **D10. macOS and Linux only** in Phase 3, except T9 (D11): the Claude Code (L1) guards on Windows, built and tested by simulation, not run on a real machine (ADR 0009). Git hooks (L2) on Windows stay out. Windows todo below: BAs and PMs use Windows.
- **D11. BAs and PMs in the pilot:** on a Mac or Linux machine if one exists. Manesh G checks by the end of week 1. If none is found, add **T9 "Windows L1 hooks"** (Claude Code hooks only, no git hooks: the `python` / `python3` command and paths) before T6, and T6 is the first task to move out.
- **D12. Pilot group:** Shameer names the people and the project. Start with 2–3 developers and 1 BA on one non-HIPAA project (`phi = "warn"`). Add one HIPAA project in week 2 if false blocks stay under 5%.

### Pilot pack and trial

- **D13. Pilot pack is task T0, in week 1**, before the pilot starts. T8 updates it for what Phase 3 adds (events, credentials, CI).
- **D14. Pilot pack contents:** install guide, a role guide each for developer, BA/PM and reviewer, architecture with Mermaid process flows (in the repo, reviewed in PRs), a deck of about 10 slides (a Slides artifact, exportable to .pptx or PDF, built from the docs). The docs are the source; the deck is made last.
- **D15. Shameer's trial run:** a "Clinic appointment booking" API (FastAPI or Node, SQLite, 3 stories: book, cancel, list slots), HIPAA mode (`phi = "block"`), with fake data only. Shameer runs it with the trial-run script before the pilot starts; the results are the first pilot entry.

## Decisions already made (input to this phase)

From earlier ADRs and plans. These are not new decisions.

- **Event pipeline (ADR 0003, Option D):** OTel collector, ClickHouse, Grafana, one ingest key per developer. The collector stamps `user` from the key (`from_context: auth.username`, spike passed) and removes `user.email`, `user.account_uuid`, `user.account_id`. The ClickHouse writer is insert-only (`otel_writer`); Grafana reads with `grafana_reader` (`readonly = 2`). No database credentials on laptops. No S3.
- **Events never wait and never carry content (ADR 0002, 0003):** the local queue `.bmad-guard/events/queue.jsonl` stays the first stop; a logging outage never blocks a commit. Fields: guard, stage, verdict, reason code, path and SHA-256. Never prompt text, file content, command text or matched values (the red-team case LEAK-1 checks this on the queue).
- **Claude Code telemetry** goes to the same collector through the standard OTel environment variables set by managed settings. `OTEL_LOG_USER_PROMPTS` stays off (ADR 0003, spike T3).
- **Where the config lives (ADR 0005):** `deploy/collector/` in this repo.
- **Bypass is accepted (ADR 0002):** local hooks can be skipped. The mitigations that belong to this phase are the "commits with no guard events" report and CI checks.
- **Phase 2 already denies the agent access to `~/.config/bmad-guard/`** (`PATH_GUARD_CONFIG`; red-team case SENS-3).
- **Python 3.10+, standard library only, `unittest`, PEP 723 headers** (Phase 1). The sender stays standard library.

## Out of scope

- Org-wide rollout (the roadmap's Phase 5, "out of scope for now", Phase 0 T5).
- Jira / ADO MCP check and the `epic.md` source (Phase 4: Phase 2 open items 1 and 2).
- ADR 0010 Phase 2 (sync of the thin stories to ADO or Jira): moves to Phase 4, with the MCP spike.
- Copilot and Cursor hooks, until they offer blocking hooks (Phase 2 open item 4).
- The semantic prompt check (D3).
- Building the company-network hosting (D4: the plan is written, the build is later).
- Windows, except T9 if D11 needs it (D10).
- CI on Bitbucket, and CI rules beyond the review record and the secret and PHI scan (D8).
- A real OS sandbox for the agent (Phase 2 plan: "the real limit is an OS sandbox, not in scope").

## Tasks

One branch and one PR per task: `feature/GUARD-3-<short-name>`.

### T0. Pilot pack (week 1; updated in T8)

The pilot starts in week 1 (D1), so its users need the docs on day 1. Today `docs/` has ADRs, `hooks.md`, the rollout doc and reports, but no guide for a person and no diagram.

- **Install guide** `docs/guides/install.md`: prerequisites (Python 3.10+, git, BMAD 6.12.4, Claude Code version), setup with `setup_guard.py`, `--check`, how to read a block message, rollback, FAQ ("why was I blocked?", "what do I do now?").
- **Role guides** `docs/guides/developer.md`, `docs/guides/ba-pm.md`, `docs/guides/reviewer.md`: what each role sees, does and must not do (the review in a fresh session, Warden approvals, no `--no-verify`).
- **Architecture** `docs/architecture.md`: the layers (L1 Claude Code hooks, L2 git hooks, L3 CI, planned), the guard list with rule families, and process flows as Mermaid diagrams: story → approval → build → review → push, and the hook flow for one tool call and one commit. Events and privacy (what is and is not logged).
- **Trial-run script** `docs/guides/trial-run.md`: the clinic booking project (D15), 15–20 "try this, expect that" steps taken from the red-team cases, and a results table to fill in.
- **Deck**, about 10 slides: problem, layers, the flow, red-team results, pilot plan, asks. A Slides artifact, built from the docs.

**Done when:** every command in the guides is checked against the code (`--help`, the install test sandbox); the Mermaid diagrams render on GitHub; the deck link is in the README; Shameer has reviewed the guides.

### T1. ADRs first: credential storage, machine-level scripts (week 1)

Two decisions block the build tasks, so they are written first, with `/adr`.

- **ADR 0007, credential storage** ([`docs/adr/0007-credential-storage.md`](../adr/0007-credential-storage.md); Phase 1 open item 1). A per-user file outside every repo, `~/.config/bmad-guard/credentials`, mode 600, written once by the setup script, holding the endpoint, the developer id and the key (D5); scripts read `BMAD_GUARD_ENDPOINT`, `BMAD_GUARD_USER` and `BMAD_GUARD_KEY` first, then the file. Later option: the OS keychain. Not a `.env` file in the project.
- **ADR 0008, machine-level script location** ([`docs/adr/0008-machine-level-script-location.md`](../adr/0008-machine-level-script-location.md); D7). Status Proposed: it is tested in T5.

**Done when:** both ADRs are approved by PR, with options and trade-offs.

### T2. Collector, ClickHouse and Grafana in `deploy/collector/` (weeks 1–2)

Move the spike config (`spikes/collector/`) into `deploy/collector/` with pinned image tags, runnable as local Docker on a dev machine (D4), and add what ADR 0003 lists as follow-up work:

- bcrypt key hashes in the htpasswd file and a key-rotation process (the spike used salted SHA1, which is too weak);
- retention of 180 days (D6);
- Grafana plugin auto-update turned off and plugins pinned (the exact Grafana 13 setting is not known; check first);
- backups;
- pin the collector version and re-test the ClickHouse exporter on each upgrade (logs are beta);
- **hosting plan** `deploy/collector/HOSTING.md`: company AWS, VPN only, TLS through ALB + ACM, the IT contact, retention approved by compliance. Written, not built (D4).

**Done when:** the stack runs from the repo config in local Docker; the spike tests 1–5 (happy path, user stamping, bad key, server down, insert-only) pass again against the hardened config, and the result is in a report; the hosting plan is written.

**Built (T2), 2026-11-26** (branch `feature/GUARD-3-collector`; not yet reviewed):

- `deploy/collector/`: stack with exact image tags (README table), ports on `127.0.0.1`, bcrypt hashes, `keys.py` (add, rotate, remove, init), 180-day TTL, `otel_writer` insert-only, `grafana_reader` with `readonly = 2`, Grafana plugin updates off and plugins pinned, `backup.sh` (backup, verify, restore), README with the upgrade steps, and `HOSTING.md` with the open items and owners.
- bcrypt: the pinned collector 0.165.0 accepts `$2y$`. Python has no bcrypt, so `keys.py` uses `htpasswd` from a pinned `httpd` image through `docker run`; the key goes in on stdin and is never written.
- Grafana: `preinstall_auto_update`, `plugin_admin_enabled` and `check_for_plugin_updates` set to false; checked in `defaults.ini` and in the running Grafana. Finding: Grafana also downloads its own default apps at the latest version, so all six plugins are pinned.
- Report: `docs/reports/phase-3-collector.md` (spike tests 1-5 pass again; rotation, hardening and dashboard checks pass). Not tested: TLS, a real sender, Claude Code telemetry through the new stack.
- Tests: `tests/test_collector_keys.py` (24, no Docker). The live script is `deploy/collector/run_live_tests.py`.
- Open: IT contact, compliance approval of 180 days, key rotation period (see `HOSTING.md`).

### T3. Sender, credentials and queue (weeks 1–2)

- Ledger's sender reads the endpoint, user and key from `BMAD_GUARD_ENDPOINT` / `BMAD_GUARD_USER` / `BMAD_GUARD_KEY`, then the credential file (ADR 0007, D5). No endpoint: queue only.
- Done early (T1 review, 2026-11-26): the `BMAD_GUARD_ENDPOINT` rename with the `BMAD_GUARD_COLLECTOR` alias, a send backoff so an unreachable collector does not stall every hook, and red-team cases for the credential folder through Bash and Grep.
- The setup script writes the credential file once (mode 600), never into the project, and `--check` reports a missing or too open file. Setup stays idempotent.
- `--check` warned on Windows that it is not supported (D10). T9 removes that line (Windows mode of `--check`).
- Sending stays best effort: a failure queues the event and never blocks a commit or a tool call.

**Built (T3):**
- `guard_events.py`: `read_credential_file()` (opens with `O_NOFOLLOW`, judges the open handle with `fstat`: regular file, yours, no group or other bits, folder not writable by others; `configparser` with `interpolation=None`; a parse error gives only the error type) and `Config.for_project(root, env=None, credentials_file=None)`. Each value: environment first, then the file, read only when a value is missing. With an explicit `env` and no `HOME`, the file is skipped. `Config` has `key_source` and `credential_problem`; the 401 hint says where the key came from; `flush` shows the reason in `error`.
- `setup_guard.py --credentials --endpoint URL --user ID`: key from a hidden prompt or `BMAD_GUARD_KEY`, never argv; folder 700 and file 600 created with the mode (temp file, then `os.replace`); same values change nothing; different values need `--force`; the result is re-read with the sender's own check. Refused on Windows (exit 2) until T9, which writes the file there and runs `icacls`. `--check` adds a note (no file) and a problem line (too open, with the fix); none changes the exit code.
- `session_start.py`: a `credential` item in `problems()` and `FIXES`, using Ledger's check. A missing file is no warning.
- Tests no longer read the real home: `tests/fake_home.py` gives a temp `HOME` to the subprocess tests, and `scripts/install_test.py` runs its sandbox with a temp `HOME` (npm and uv caches kept at their real places).
- Docs: `docs/guides/install.md` (optional section), `docs/architecture.md`, the Ledger `SKILL.md`.
- Not done here: the red-team case for the file itself stays as T1 left it (SENS-3, Bash and Grep cases); the Gate boxes are not ticked.

**Done when:** tests cover key from the environment, from the file, from neither (queue only, no spam), a wrong key (HTTP 401, event kept), no endpoint, and a file with open permissions; a red-team case shows the agent cannot read the credential file (SENS-3 stays green).

### T4. Claude Code telemetry and dashboards (week 3)

- OTel environment variables in the managed-settings template (and the setup script's project wiring, if wanted), with `OTEL_LOG_USER_PROMPTS` off.
- Keep the collector's scrub step and review its attribute list on each Claude Code upgrade (spike finding 2).
- Grafana panels: events per user and verdict, blocks per rule, warnings per rule. Claude Code events carry the name in `Body`, not `EventName` (spike finding 3).
- The two reports from ADR 0002 and the Phase 2 todo lists:
  - **commits with no guard events** (a commit that no hook saw: `--no-verify`, setup not run);
  - **review records with no `REVIEW_RECORDED` event** (a forged record; Phase 2 review-record todo).

**Done when:** one throwaway session shows events from the guards and from Claude Code in Grafana (local Docker stack), with the user from the key and no prompt text; both reports run on test data.

**Built (T4), 2026-11-26** (branch `feature/GUARD-3-telemetry`; not yet reviewed; Gate items not ticked):

- **Telemetry settings:** the managed template has an `env` block (logs `otlp`, metrics `otlp` with delta temporality, `http/json`, endpoint placeholder, the five content switches `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_RAW_API_BODIES` all `0`, account ids off) and `otelHeadersHelper`. The key is not in the file: `otel_headers.py` (Ledger) prints the `Authorization` header from the developer's own key (env, then the credential file), so `user` is stamped per developer. Names checked in the Claude Code docs. `hook_wiring.py` and `session_start.py` did not change (they check hooks and deny rules only). Decision: `setup_guard.py` does **not** write telemetry settings to the project: the docs say a repository's settings ignore the OTel exporter variables, and a key in a project file is the risk ADR 0007 avoids. Details: `docs/managed-settings-rollout.md`.
- **Scrub list** widened and dated in `deploy/collector/README.md` ("Scrub list"): also `user.id`, `organization.id`, `workspace.host_paths` and (resource) `host.name`, `host.id`. `session.id` and `terminal.type` stay.
- **Dashboard:** panels for blocks per rule, warnings per rule and Claude Code events by name (`Body`), next to events per user and verdict. All plain `SELECT`s through `grafana_reader`.
- **Reports:** `deploy/collector/reports/guard_reports.py` (`commits`, `reviews`), standard library, read-only HTTP queries. They live next to the stack, not in `module/` (they need the reader's password and run where the stack runs). Match keys, chosen in the update of ADR 0003: pre-commit events carry `tree` (the staged tree id), `REVIEW_RECORDED` carries the record's path and file hash. A tree with only block events is `blocked-only`, not seen. What they cannot see is in the README ("Audit reports").
- **Tests:** `tests/test_guard_reports.py`, `tests/test_otel_headers.py`, `TreeTests` in `tests/test_pre_commit.py`, additions in `tests/test_review_record.py` and `tests/test_collector_keys.py`. Live script: `deploy/collector/run_telemetry_live.py` (40 checks pass). Report: `docs/reports/phase-3-telemetry.md`.
- **Follow-ups from the review (same branch):**
  - Metrics are on: template `OTEL_METRICS_EXPORTER=otlp` and the six metric switches set on purpose; collector metrics pipeline (same auth, user stamp, scrub); tables `guard.otel_metrics_sum` and `otel_metrics_gauge` from `clickhouse/init/02-metrics.sh` (180-day TTL, INSERT only for the writer, `create_schema: false`; an existing stack runs the script by hand); seven metric panels per user. Same database, not a separate one (ADR 0003 update).
  - `session_start.py` and the pre-push hook send the queue once, best effort (no endpoint: no network, no message; 3 s timeout and 5-minute backoff; never blocks). Pre-commit stays queue-only. Tests in `tests/test_flush_hooks.py`.
  - `keys.py init` creates an empty `local/devkeys.htpasswd` (mode 644: the collector container must read it).
  - The `commits` report docs say: routine runs use `origin/main..<feature branch>` before any rebase.
  - Live check: 60 checks pass on fresh volumes.
- **Not done / not verified:** the managed file was not deployed (needs admin; T5's rollout test, new step 9); the helper's machine-level path is the proposed ADR 0008 folder; metric rows for lines of code, commits, PRs and tool decisions come from made-up datapoints (a reply-only session does not produce them); only sum and gauge tables exist; `backup.sh` does not cover the metrics tables; a developer who only commits in a terminal and never pushes or starts Claude Code still sends nothing.

### T5. Machine-level scripts and the managed-settings pilot (week 3)

The Phase 2 todo lists "Before the managed file goes on any pilot machine" and "Before the full IT rollout" are the source of this task.

- Implement the machine-level script location from ADR 0008: a setup script change, a template change and an install-test case.
  - The machine folder must also hold `otel_headers.py` and `guard_events.py` (from `module/skills/agent-project-logger/scripts/`): the managed template's `otelHeadersHelper` points at `<machine folder>/otel_headers.py` (GUARD-3 T4). Replace the endpoint placeholder in the deployed file, and add rollout doc step 9 (telemetry) to the test.
- An uninstall for the wiring in the setup script (rollback is by hand today).
- Run the rollout doc test (steps 1–9) on one machine and record the results; test the rollback.
- Pilot machine list: every Claude Code project on a pilot machine has the module and `setup_guard.py --check` says `up to date`, until the machine-level location removes this need.
- Claude Code 2.1.295 or later on every pilot machine (`onFailure: "block"`).
- Close the open `PreToolUse` checks in `docs/hooks.md` in a real session (open checks 1, 2, 3, 4, 5, 8).

**Done when:** the managed file is on at least one pilot machine, the rollout doc test passes there, rollback works, and a project without the module is not blocked.

**Built (T5), 2026-11-26** (branch `feature/GUARD-3-machine-scripts`; not yet reviewed; Gate items not ticked; ADR 0008 is still Proposed). Everything that can run without admin rights was built and tested in temp folders. **Not done: the real-machine part** (see "Not done" below and the checklist in `docs/managed-settings-rollout.md`).

- **`scripts/install_machine.py`** (standard library): `--target DIR` (default: the OS machine folder; tests pass a temp folder), `--dry-run`, `--check`, `--uninstall`, `--print-template macos|linux`, plus `--version`, `--managed-dir`, `--expect-owner` and `--force` (uninstall only). It copies the five skills the hooks need (`scripts/` and `customize.toml`), `otel_headers.py` and `guard_events.py` at the top (where the helper imports the sender), `VERSION` and `manifest.json`, by staging `<folder>.new`, checking the hashes and renaming. A folder is replaced only when it has a `manifest.json` (it is ours); a leftover `.new` or `.old` is handled; it never calls sudo, stops with a clear message when the parent is not writable, warns when it does not run as root, refuses a target inside a BMAD project or the module checkout. `--check`: manifest hashes, no extra files, no links, owner and modes (root, 644/755, nobody else can write), the folder matches this module (version skew line), `python3` 3.10 or later, and, when a managed file is found, its hook commands and `otelHeadersHelper` point into this folder (and a warning when the folder it points to is missing). `--uninstall` removes what the manifest lists and refuses while the managed file points to the folder (the rollback order).
- **One definition:** `hook_wiring.py` has the machine base per OS (`MANAGED_DIRS`, `machine_base()`), `command()`, `desired_hooks()`, `script_paths()` and `merge()` with an optional base, `managed_settings(base)` (hooks, deny rules, lock, telemetry `env`, `otelHeadersHelper`) and `unmerge()`. `session_start.py` uses `hook_wiring.MANAGED_DIRS` (defined once). **Templates:** `managed-settings.macos.json` and `managed-settings.linux.json` are the output of `install_machine.py --print-template`; `tests/test_hook_wiring.py` compares each with `hook_wiring`. The first template `managed-settings.json` stays (project paths), because the machine mode has not passed a real-machine test.
- **The unguarded-project rule** (`hook_common.guard_state()`, a copy in `prompt_check.py`, a test keeps them equal), asked first in `pre_tool_use.py`, `prompt_guard_hook.py` and `session_start.py`, before any guard is imported: no `_bmad/` allows silently (no output, no event, no file written); `_bmad/` without `_bmad/custom/guard/manifest.json` allows, and `SessionStart` prints one line; the manifest means all guards, fail closed. **Crafted input:** the project is looked at in `CLAUDE_PROJECT_DIR`, the input's `cwd` and the process folder (each also with links resolved), the manifest also in every parent folder, and any one of them with a manifest means guarded. A `cwd` of the wrong type, a path with a null byte, a permission error while looking, and any exception all mean guarded. Tests in `tests/test_unguarded.py` (a missing variable, a `cwd` outside the project, in a sub-folder of a project that is not a git repository, a link to the project, a variable pointing at an unguarded folder, a broken guard module in an unguarded project). **Found while building:** removing the marker, or `mv _bmad`, turns the L1 guards off for that project, so MACH-2 covers Write, Edit, `rm` and `mv` of the manifest and of `_bmad/`; `python -c`, a script file and `rsync --delete` are not seen (the known limit of every guard, ADR 0002).
- **Fixes from the ADR 0008 table:** `review_record.py` accepts the project copy of its script (`.claude/skills` or `.agents/skills`, a real file in this project) as well as its own path (break 1, MACH-3); `wiring_ok()` accepts the machine commands from the managed file, found next to the `bmad-guard/` folder, without a false `SETUP_INCOMPLETE` (break 2); the templates (break 3); `MANAGED_DIRS` once (break 4); the rule (break 5). `SessionStart` records an `info` event `MACHINE_VERSION_SKEW` (fields `machine_version` and `project_version`) when it runs from a machine folder whose `VERSION` differs from the project manifest.
- **`setup_guard.py --uninstall`** (dry-run first; uses the manifest): removes the manifest files that are unchanged, our hook entries and deny rules (`hook_wiring.unmerge()`; the settings file goes when only ours was in it), `core.hooksPath` when it is the guard folder, our `.gitignore` lines (only with the comment line setup wrote), and `_bmad/custom/guard/`; never stock BMAD files, project files, a link, or a path outside setup's folders (a doctored manifest cannot delete other files); with any conflict it changes nothing unless `--force`; the manifest goes last, so a stopped run can be repeated. `--check` also prints the machine-version line (`--machine-dir`).
- **Tests:** `tests/test_install_machine.py` (61), `tests/test_unguarded.py` (36), uninstall and `--check` additions in `tests/test_setup_guard.py`, `unmerge` and template tests in `tests/test_hook_wiring.py`, additions in `tests/test_review_record.py` and `tests/test_tool_guard.py`. Fake test projects that drive a hook entry now carry the setup manifest (`tests/guarded.py`). **Install test, stage "machine" (M-I1 to M-I9, 33 checks, no BMAD and no network; `--machine-only`)** and **red-team family "Machine scripts" (MACH-1 to MACH-3, MACH-C1, MACH-C2; the suite is 73 cases)** all pass. Root ownership, the OS paths and a real managed file cannot run here: `--check`'s owner test is simulated with `--expect-owner`, and the install test says so.
- **Measured (break 8):** a `PreToolUse` call from a read-only machine folder takes about 0.09 to 0.11 s on a Mac (Python 3.13 and 3.10) with no bytecode cache. No pre-compile step was added. Not measured on a pilot Mac under managed settings.
- **Deviations from ADR 0008** (all small): the installer also has `--print-template`, `--managed-dir`, `--expect-owner` and `--force`; the swap is two renames (target to `.old`, `.new` to target), so there is a tiny moment with no folder (a stopped run is recovered by the next run); `--check` tests "not writable by the current user" as owner root plus modes plus `os.access`, so it works from any user; the rule also looks in the input's `cwd` and the process folder and in parent folders (stricter than "decide from `CLAUDE_PROJECT_DIR`"); the event fields are `machine_version` and `project_version` (the name `project` is taken by the event builder); the old template is kept, as the ADR says.
- **Not done / needs a real machine:** install as root (owner, mode, `chown`), the OS paths, a real managed file and `/status`, rollout test steps 1 to 12, the rollback test, the `PreToolUse` open checks 1, 2, 3, 4, 5 and 8 in a real session, "a project without the module is not blocked" in Claude Code itself, a hook from a path with a space on macOS under `allowManagedHooksOnly`, the telemetry helper from the machine folder, and the pilot machine list. Windows is covered by T9 (simulated; not run on a real machine). The managed `Edit` deny rule is deferred.

### T6. CI layer (L3), small (week 4; first to cut)

ADR 0002 deferred required CI checks and said they come "first for HIPAA / SOC 2 projects when L3 starts". The pre-push and pre-commit hooks can be skipped (`--no-verify`, another git client); a server-side check cannot.

- A GitHub Actions check on a pull request (D8): review record present, valid and not from a build session; secret and PHI scan of the added lines. Reuse the hook code (`guard_pre_commit.py`, `guard_pre_push.py`), not a copy.
- `setup_guard.py --with-ci` writes the workflow; `[ci] mode = "off" | "warn" | "enforce"`, default `off` (D9).
- The Phase 2 todo "Server-side check (CI or branch protection) for the same record rule".

**Done when:** on one HIPAA project in `enforce` mode, a pull request that skipped the local hooks fails the check and a clean pull request passes; in `off` mode the check does nothing; setup without `--with-ci` writes no workflow.

### T7. Phase 2 follow-ups from the pilot (weeks 1–4)

These need real use, not code first. Each item is in a Phase 2 todo list with an owner there.

- **Real-session checks:** a real block, a real warning, a real review, a real push and a real commit seen in Claude Code; the subagent session-id question (open check 7).
- **False-block numbers** (target under 5%; D12 uses them for the HIPAA project in week 2): command, path, document, write gate, content scan, pre-commit, pre-push. Then decide: person-only resolutions, `git checkout <path>` through the gate, `COMMIT_QUESTION_REMOVED` and `COMMIT_GATE_GENERIC` (warn or block), scanning the old text of an Edit, an unparsable prompt input in HIPAA projects, README and root files counting as source in pre-push.
- **Rules not yet written:** `git checkout .` / `git restore .`, `UPDATE` without `WHERE`, export rules (`aws s3api get-object`, `gsutil cp`, `az storage blob download`, `scp` / `rsync` from a remote host).
- **Smaller code todos:** Warden approving a plain TOML file (`tech-stack.toml` carries a `---` block today); a role check for the approver once real identities exist (the key gives one); tell a developer at setup time when `python3` is older than 3.10; read `state_file` and `implementation_artifacts` from the BMAD config if a project moves them.
- **Found in T0 (pilot pack):**
  - [x] **First push blocks (fixed, `fix/GUARD-3-first-push`).** The BMAD install commit and the setup commit counted as source in pre-push (`PUSH_NO_RECORD`). Now the root `.gitignore` and `.github/agents/bmad-*.agent.md` are not source in pre-push (`write_gate.install_file`); the write gate still gates them, and `.github/workflows` and other `.github` files stay source. Other IDE targets of the BMAD installer (Cursor, Windsurf and so on) are not covered: the pilot uses Claude Code; add them when a project installs them.
  - `setup_guard.py --project <missing folder>` crashes with a traceback; it should exit 2 with a message, like a folder that is not a git repo.
  - `setup_guard.py --check` prints `0 conflict(s)` when `core.hooksPath` points elsewhere (it does end with `NOT up to date`): fix the wording.
  - `epics.md` gets no Warden step. Decision: none for the pilot; the ready-for-dev gate and the plan approval hold stories. Look again with the pilot numbers.
  - Is `/clear` a fresh session for the review guard? Not verified: the reviewer guide says to quit and restart `claude`. Check in a real session (with open check 7).
  - Minimum Node version for the BMAD installer: not checked. Owner: Shameer, during the trial run.
- **Rebase, amend and squash** make a review record stale: decide whether a "re-review a rewritten branch" shortcut is needed.

**Pilot 1 results (clinic-booking trial, 2026-11-26).** 21 steps pass, no hook crashed, no bypass attempt seen. Wrong blocks: 1 of 15 in the model test sessions (about 7%), 3 of about 22 with the trial driver session (about 14%). Both are above the 5% target, so the wrong-block fixes come before the HIPAA project (D12). T6 (CI) is deferred.

Findings:

| #   | Finding | Fix |
| --- | ------- | --- |
| A1  | `grep -rl seed --exclude='.env*' .` blocked by `PATH_ENV_FILE` | path-guard: values of `--exclude` / `--exclude-dir` (and similar filter flags) are not reads of the file |
| A2  | `sed -i` on a file outside the project blocked by `GATE_NO_ACTIVE_PLAN`; the Write tool to the same file is allowed | write gate: Bash write targets outside the project root are not source, same as the Write tool |
| A3  | `\n@app.get` in Python text read as an email (`phi.email`) | content scan: a backslash escape ends a token |
| B   | A secret built by a shell command (`python3 -c` string joins) is written without a tool-guard block; pre-commit catches it | Accepted as a known limit (pre-commit holds). Phase 4 todo: PostToolUse scan that warns after the write |
| C1  | `DOC_GUARD_SCRIPTS` message lists "hook scripts, guard skills, git hooks, .claude/settings.json" but the file was `_bmad/custom/prompt-guard.toml` | message names the file that was hit and includes `_bmad/custom/` guard config |
| C2  | "Approve with Warden": Claude cannot find a skill named Warden | messages say `agent-approval-grant (Warden)` |
| C3  | Claude Code shows every correct block as `PreToolUse:Bash hook error` | Spike, 1 hour: does `permissionDecision: deny` show a cleaner message and still block in `bypassPermissions`? Switch only if both are true; else explain the label in the guides. **Result: not switched.** Claude Code 2.1.332, test hook in a throwaway project, `claude -p`, `--setting-sources project,local`. Exit 2 + stderr and the JSON deny form (exit 0) both block in default and in `bypassPermissions` (the command did not run), and the model got the reason each time. Message: exit 2 `PreToolUse:Bash hook error: [python3 ".../hook.py"]: <reason>`; JSON `PreToolUse:Bash hook error: <reason>`. Only the command path is dropped; the label stays. No real gain, so the dispatcher is unchanged (fail-closed stays exit 2). The label is explained in the developer guide |
| D   | A blocked prompt is echoed locally as `Original prompt: <value>` (not sent to the model) | Check whether the value is stored in the local `~/.claude` transcript; document the result (HIPAA relevant). **Result: stored.** Claude Code 2.1.332, `claude -p` in a HIPAA-mode throwaway project, fake SSN not allow-listed plus a marker: the prompt was blocked, and the transcript `~/.claude/projects/<path>/<session>.jsonl` holds the marker on 2 lines and the fake SSN on 2 lines; the `stream-json` stdout holds each on 2 lines. Documented in the developer guide (no real PHI, even to test; delete the session transcript if it happens) |
| E1  | Trial script step 13 undo also throws away the 12b reword | undo with `git restore --staged`, then remove the line by hand |
| E2  | Global `~/.claude` deny/ask rules and hooks hide the project guard (4b, 5b, 6b) | trial guide says so and shows `--setting-sources project,local` |
| E3  | Installed module older than the source (1a) | install guide: re-run setup after each bmad-guard update; tag `pilot-2` |

Order:

1. **T7a code fixes** (`fix/GUARD-3-pilot-fixes`): A1–A3 with regression tests, C1, C2, and the two T0 setup bugs (`--project <missing folder>` exits 2; `--check` wording when `core.hooksPath` points elsewhere).
2. **T7b docs and checks**: E1–E3, the D transcript check, the C3 spike, B recorded as a known limit.
3. **Tag `pilot-2`.** Re-run setup in clinic-booking, then the pending developer steps (1b, 13, 18a, 19, 15, 14/16/17/20) and the three wrong-block cases again.
4. **T7c decisions** with the new numbers: the false-block decision list above. The rest moves to Phase 4 with an owner and a date.

**Built (T7b, `fix/GUARD-3-pilot-followups`).**

- [x] **E1** trial-run step 13 undo is `git restore --staged docs/trial-fixture.md`, then the line is removed by hand (the 12b reword stays).
- [x] **E2** trial-run rules and developer guide say that global `~/.claude` settings can block first, and show `claude --setting-sources project,local`.
- [x] **E3** install guide: after an update, run setup, then `--check` must say `up to date`; the next tag is `pilot-2` (not created yet).
- [x] **B** recorded as a known limit in `architecture.md` and the developer guide; the Phase 4 todo (PostToolUse scan) is in the Todos list.
- [x] **D** checked, result above; developer guide has a section on it. No code change.
- [x] **C3** spiked, result above; not switched; developer guide explains the "hook error" label.

Moved to Phase 4 (owner Shameer, date set in the Phase 4 plan): the rules not yet written, the approver role check, the re-review shortcut for rewritten branches, the PostToolUse scan (B).

**Done when:** each item is closed, or moved to a later phase with an owner and a date, and the Phase 2 todo lists are updated.

### T8. Red-team re-run, install test, pilot pack update and Gate 3 (week 4)

- Re-run `scripts/install_test.py` and `scripts/red_team.py`; write `docs/reports/phase-3-red-team.md`. Add cases for what Phase 3 adds: the credential file is unreadable by the agent, events leave the machine without content, a project without the module under managed settings, the CI check in `off` and `enforce` mode.
- Update the pilot pack (T0) for events, credentials and CI.
- Draft the Phase 4 plan (Jira / ADO MCP, `epic.md`, semantic prompt check, Windows).

**Done when:** the Gate 3 items below are true.

### T9. Windows L1 hooks (only if D11 needs it; before T6)

Claude Code hooks on Windows for BAs and PMs: the hook command (`python` / `python3`), paths, `setup_guard.py` and `--check`. No git hooks, no queue lock without `fcntl`.

**Done when:** a BA on Windows runs the planning flow (brief → PRD with Warden approval) with the L1 guards on, and the install test has a Windows case or a written manual check.

**Decisions (Shameer, 2026-11-26).** (1) BAs have Python 3.10+ and Git for Windows. The hook command stays `python3`; `python3` must resolve in Git Bash (copy `python.exe` to `python3.exe`); `--check` tests it and fails with the fix. PowerShell-only machines are out of scope. (2) No admin on pilot machines: the pilot uses the project form. The managed Windows template and the installer branch are built and unit-tested, "not run on a real machine". (3) The PowerShell tool is blocked in guarded projects on all OSes (`PS_TOOL_UNSUPPORTED`); real PowerShell guards are Phase 4. (4) The credential file is `%USERPROFILE%\.config\bmad-guard\credentials`, `icacls` best effort; Windows BAs send events. (5) One named BA runs the manual check (`docs/guides/windows-check.md`); a `windows-latest` CI job runs the Windows-safe tests (its own commit).

**Built (T9)** (all simulated on macOS: `os.path` as ntpath, a patched platform, fake `icacls` and Git Bash. **Nothing has run on a real Windows machine.** ADR 0009 stays Proposed until the manual check passes):
- **A real gap closed on every OS:** `PowerShell` was not in the `PreToolUse` matcher, so the tool skipped every L1 guard (including `approval-state.json`). It is now in the matcher and `powershell_guard.py` returns `PS_TOOL_UNSUPPORTED`. Tested in the real dispatcher on macOS (`tests/test_powershell_guard.py`) and by red-team case PS-1.
- **`winpaths.py`:** Git Bash paths (`/c/x`, `/cygdrive/c/x`, `/mnt/c/x`), both separators, case folding, `\\?\`, `::$DATA`, trailing dots and spaces, `$USERPROFILE` and friends, a `\\.\` device path blocks (`PATH_DEVICE`). Used by `path_guard`, `document_guard`, `bash_write_guard`, `command_guard` (scratch folders), `write_gate`, `shell_parse.base_name` (a command by its Windows path) and `content_scan` (`> nul`). Each security fix has a test in `tests/test_windows_guards.py` that fails without it (checked against the previous code). Off Windows nothing changes: all earlier tests pass; the few that changed on purpose are named in the commit bodies.
- **Credential file and queue** (`guard_events.py`): on Windows the home is `Path.home()` (not `HOME`); no mode checks, a link or junction is refused; no queue lock, `emit()` only appends one line and `flush` sends and keeps lines appended during the send.
- **Hook entries:** UTF-8 output so a cp1252 console cannot turn a block reason into `GUARD_ERROR`; `CLAUDE_PROJECT_DIR` in Git Bash form is made native in both `project_root` copies; `SessionStart` does not warn about `core.hooksPath` on Windows and accepts the Windows managed file.
- **`setup_guard.py` on Windows:** no git hooks, no `core.hooksPath`, a project set up on another OS keeps its hooks; `--check` tests Git Bash and `python3` there and exits 1 with the fix; `--credentials` writes the file and runs `icacls`; UTF-8 file I/O; `.gitattributes` lines on all OSes.
- **`.gitattributes` finding:** the design listed three broad lines (`.claude/skills/**` and so on). The full install test showed the cost: BMAD's own CRLF files (for example `brain-methods.csv`) then show as modified, so the check "stock files unchanged" failed. The lines are now one per installed guard skill folder, the guard folder, and each override file. A real-git test covers both the clone with `core.autocrlf=true` and the stock file.
- **Managed (built, unit-tested, not run on a machine):** `machine_base("win32")` is `C:/Program Files/ClaudeCode/bmad-guard`; `module/managed-settings/managed-settings.windows.json` (forward slashes, `CLAUDE_CODE_USE_POWERSHELL_TOOL=0`); `install_machine.py` Windows branch (elevated terminal, no chmod or chown, `icacls` write-right check with "check by hand" for a list it cannot read, renames retried, `python3` tested in Git Bash).
- **Tests and checks:** `tests/test_winpaths.py`, `tests/test_windows_guards.py`, `tests/test_windows_hooks.py`, `tests/test_powershell_guard.py`, `tests/test_setup_guard_windows.py`, `tests/test_install_machine_windows.py`, Windows cases in `tests/test_guard_events.py` and `tests/test_hook_wiring.py`. **Install test stage "Windows (static, simulated)" (W-I1 to W-I6)** and **red-team family "Windows paths" (WIN-1 to WIN-8, WIN-C1 to WIN-C3) plus PS-1 and PS-C1** pass; the report line says "simulated Windows flavour on macOS; not run on Windows".
- **Manual check:** `docs/guides/windows-check.md` (11 steps, a results table). A `windows-latest` CI job runs only the Windows-safe tests (its own commit).
- **Accepted and documented:** `cmd /c` and `powershell -c` from Bash (like `python -c`); 8.3 short names for files that do not exist yet; a junction in the setup link check; `python3` found by `PATH`; native `rd /s`, `del /s`, `Remove-Item -Recurse` through Git Bash; Defender start time (measured in the checklist).

- [ ] **T9 manual check on a real Windows machine** (`docs/guides/windows-check.md`). Owner: one named BA, with Shameer. Not run.
- [ ] **Questions for Manesh G (do not block):** which Python and which Git do the BAs have? Are there PowerShell-only machines (no Git for Windows)? Is `python3` the Microsoft Store stub on any machine?
- [ ] **Phase 4 (Windows):** real PowerShell guards; an `msvcrt` queue lock; Windows Credential Manager for the key; guards for native Windows delete commands (`rd /s`, `del /s`, `Remove-Item -Recurse`); a PostToolUse scan; git hooks (L2) for Windows.

## Gate 3

Phase 4 starts only when all of these are true:

- [ ] The pilot pack (T0) matches the shipped code, including the Phase 3 additions.
- [ ] A throwaway session shows guard events and Claude Code events in Grafana (the local Docker stack counts), with the user from the key and no content.
- [ ] A collector outage queues events and they drain later; a wrong key is rejected (re-run of spike tests 1–5 on the hardened stack).
- [ ] The hosting plan is written: IT contact, VPN, TLS, retention approved by the compliance owner. Built is not required.
- [ ] The credential file is mode 600, outside every repo, and the red-team report shows the agent cannot read it.
- [ ] The managed-settings file is on at least one pilot machine, the rollout doc test passes, rollback is tested.
- [ ] The Phase 2 "Managed settings rollout todos" each have an owner and a date, and the ones for this phase are closed.
- [ ] The CI check fails a pull request that skipped the local hooks on one project in `enforce` mode; the default stays `off` (if T6 stays in the phase).
- [ ] A BA on Windows runs the planning flow with the L1 guards (T9 is built and simulated; the real-machine check `docs/guides/windows-check.md` is open).
- [ ] `scripts/install_test.py` and `scripts/red_team.py` pass; the Phase 3 red-team report is committed.
- [ ] A draft Phase 4 plan exists.

## Todos

- [ ] **T5 review answers (Shameer, 2026-11-26).** (1) Retire `managed-settings.json` (project paths) once the real-machine test passes. (2) A BMAD project without the manifest stays "allow" for the pilot; decide on "block" after the pilot from the T4 `commits` report. (3) The managed `Edit` deny for the machine folder stays deferred. (4) `setup_guard.py --uninstall` keeps removing a deny rule that equals one of ours (documented). (5) Uninstall keeps the event queue `.bmad-guard/`. Owner: Shameer.
- [ ] **T5 real-machine checklist** in `docs/managed-settings-rollout.md` ("Manual steps for Shameer and IT"): root install, managed file, rollout steps 1–12, rollback, open checks 1, 2, 3, 4, 5, 8. IT replaces the collector endpoint placeholder. Owner: Shameer with IT.
- [ ] **Backups of the metrics tables** (`backup.sh` covers the events table only; T4). Owner: Shameer.
- [ ] **Pilot people and project** (D12). Owner: Shameer. Date: end of week 1 at the latest.
- [ ] **Mac or Linux machine for a BA** (D11). Owner: Manesh G. Date: end of week 1. Decides whether T9 exists.
- [ ] **PostToolUse scan** that warns after a write when a secret was built by a shell command at run time (pilot 1, finding B; the pre-commit hook catches it today). Owner: Shameer. Phase 4.
- [ ] **Windows support** for BAs and PMs beyond L1 (git hooks, an `msvcrt` queue lock, real PowerShell guards, Credential Manager, native delete commands, the install test on a Windows runner). L1 itself is T9 (simulated; the manual check is open). Owner: Shameer. Phase 4 at the latest.
- [ ] **Retention approved** by the compliance owner (D6). Owner: Shameer. Date: before Gate 3.
- [ ] **IT contact** for hosting the stack (D6). Owner: Shameer. Date: before the hosting plan is final (T2).
- [ ] **Trial run** of the clinic booking project (D15). Owner: Shameer. Date: before the pilot starts.

## Risks

| Risk | What we do |
|---|---|
| Three services to run (ADR 0003) | Pin versions; one owner; config in the repo; backups (T2) |
| ClickHouse logs exporter is beta (ADR 0003, spike finding 4) | Pin the collector; re-test on each upgrade |
| Claude Code telemetry carries personal data (spike finding 2) | Scrub in the collector; `OTEL_LOG_USER_PROMPTS` off; review the attribute list on each Claude Code upgrade |
| A key on a laptop is read by the agent | File outside the repo, mode 600, `PATH_GUARD_CONFIG` block, red-team case; keychain later (ADR 0007) |
| Managed settings block every project without the module (Phase 2 todo) | Pilot machines only until the machine-level location exists (T5) |
| Pilot docs drift from the code | Docs in the repo, reviewed in PRs; T8 updates them; Gate 3 item |
| BAs and PMs on Windows cannot join the pilot, so planning guards get no numbers | Mac or Linux for one BA (D11); else T9, which is built but **not run on a real Windows machine** (manual check open) |
| Local Docker stack sees only one machine's events | Accepted for the pilot; the hosting plan is written in T2 (D4) |
| CI differs per platform (ADR 0002) | GitHub first; reuse the hook code; off by default (D8, D9) |
| Scope is large (4 weeks) | Cut T6 first, then the T7 rule todos (D2) |

## How we work

- One branch per task, commits `GUARD-3: what changed`.
- Claude commits locally. Shameer pushes and opens the PRs.
- Each PR shows the test command and its result.
