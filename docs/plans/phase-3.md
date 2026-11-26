# Phase 3 plan: Events, credentials and rollout path

> **DRAFT. To be grilled and approved by Shameer and Manesh G before work starts.**
> Nothing here is decided. Every task, number and order below is a proposal taken from earlier documents (sources are
> named). Where a document gives no answer, the plan lists an open question with a suggested default instead of
> choosing. Dates and the time box are not set.

| | |
|---|---|
| Ticket | GUARD-3 |
| Time box | To be decided (open question 1) |
| Approvers | Shameer, Manesh G (PR approval) |
| Status | Draft (2026-11-26). Not approved. |
| Inputs | ADRs 0001–0006 (0003 event logging, 0002 enforcement layers); Phase 1 plan (open item 1, credential storage); Phase 2 plan and its todo lists; `docs/reports/phase-2-red-team.md`; `docs/hooks.md`; `docs/managed-settings-rollout.md`; `spikes/collector/REPORT.md` |

## Goal

Phase 2 made the guards block on a laptop. Phase 3 makes them **visible and rollable**. At the end of Phase 3 (proposal):

1. Guard events leave the laptop: sender → OTel collector → ClickHouse → Grafana, with the user taken from the developer's key, never from the event (ADR 0003).
2. The developer's ingest key is stored where the AI agent cannot read it (credential storage, ADR 0007).
3. The managed-settings rollout can go beyond a pilot machine: the guard scripts are installed once per machine and a project without the module is not blocked (Phase 2 todo "Machine-level script location").
4. The local-hook rules that `--no-verify` and other clients skip are also checked server-side in CI (L3), at least for the projects where the plan says so (open question 5).
5. The open Phase 2 checks and pilot numbers are closed or moved on, with a name and a date for each.

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
- Copilot and Cursor hooks, until they offer blocking hooks (Phase 2 open item 4).
- The semantic prompt check (Phase 1 open item 2, ADR 0006): needs its own spike. Open question 9 asks whether it belongs here.
- A real OS sandbox for the agent (Phase 2 plan: "the real limit is an OS sandbox, not in scope").

## Tasks (proposed)

One branch and one PR per task: `feature/GUARD-3-<short-name>`. Days are not set: they depend on the time box (open question 1).

### T1. ADRs first: credential storage, machine-level scripts (days TBD)

Two decisions block the build tasks, so they are written first, with `/adr`.

- **ADR 0007, credential storage** (Phase 1 open item 1, raised by Shameer on 25 Nov). Proposal in the Phase 1 plan: a per-user file outside every repo, `~/.config/bmad-guard/credentials`, mode 600, written once by the setup script; scripts read the environment variable `BMAD_GUARD_KEY` first, then the file. Later option: the OS keychain. Not a `.env` file in the project (the agent works in that folder; a wrong `.gitignore` commits it).
- **ADR for the machine-level script location** (Phase 2 todo, "Before the full IT rollout"). Install the guard scripts once per machine and point the managed hooks there, so a project without the module is not blocked and the scripts cannot be edited per project. Needs the answer to open question 8.

**Done when:** both ADRs are approved by PR, with options and trade-offs.

### T2. Collector, ClickHouse and Grafana in `deploy/collector/` (days TBD)

Move the spike config (`spikes/collector/`) into `deploy/collector/` with pinned image tags, and add what ADR 0003 lists as Phase 3 follow-up work:

- bcrypt key hashes in the htpasswd file and a key-rotation process (the spike used salted SHA1, which is too weak);
- TLS (ALB + ACM) and the endpoint question (open question 2);
- retention period (the spike used 180 days; open question 4);
- Grafana plugin auto-update turned off and plugins pinned (the exact Grafana 13 setting is not known; check first);
- backups;
- pin the collector version and re-test the ClickHouse exporter on each upgrade (logs are beta).

**Done when:** the stack runs from the repo config; the spike tests 1–5 (happy path, user stamping, bad key, server down, insert-only) pass again against the hardened config, and the result is in a report.

### T3. Sender, credentials and queue (days TBD)

- Ledger's sender reads the key from `BMAD_GUARD_KEY`, then the credential file (T1).
- The setup script writes the credential file once (mode 600), never into the project, and `--check` reports a missing or too open file. Setup stays idempotent.
- A queue lock that works on Windows (ADR 0003 follow-up), if Windows is in scope (open question 6).
- Where the endpoint comes from (open question 3).
- Sending stays best effort: a failure queues the event and never blocks a commit or a tool call.

**Done when:** tests cover key from the environment, from the file, from neither (queue only, no spam), a wrong key (HTTP 401, event kept), and a file with open permissions; a red-team case shows the agent cannot read the credential file (SENS-3 stays green).

### T4. Claude Code telemetry and dashboards (days TBD)

- OTel environment variables in the managed-settings template (and the setup script's project wiring, if wanted), with `OTEL_LOG_USER_PROMPTS` off.
- Keep the collector's scrub step and review its attribute list on each Claude Code upgrade (spike finding 2).
- Grafana panels: events per user and verdict, blocks per rule, warnings per rule. Claude Code events carry the name in `Body`, not `EventName` (spike finding 3).
- The two reports from ADR 0002 and the Phase 2 todo lists:
  - **commits with no guard events** (a commit that no hook saw: `--no-verify`, setup not run);
  - **review records with no `REVIEW_RECORDED` event** (a forged record; Phase 2 review-record todo).

**Done when:** one throwaway session shows events from the guards and from Claude Code in Grafana, with the user from the key and no prompt text; both reports run on test data.

### T5. Machine-level scripts and the managed-settings pilot (days TBD)

The Phase 2 todo lists "Before the managed file goes on any pilot machine" and "Before the full IT rollout" are the source of this task.

- Implement the machine-level script location from the T1 ADR: a setup script change, a template change and an install-test case.
- An uninstall for the wiring in the setup script (rollback is by hand today).
- Run the rollout doc test (steps 1–8) on one machine and record the results; test the rollback. Owner and date in Phase 2 todos: Shameer.
- Pilot machine list: every Claude Code project on a pilot machine has the module and `setup_guard.py --check` says `up to date`, until the machine-level location removes this need.
- Claude Code 2.1.295 or later on every pilot machine (`onFailure: "block"`).
- Close the open `PreToolUse` checks in `docs/hooks.md` in a real session (open checks 1, 2, 3, 4, 5, 8).

**Done when:** the managed file is on at least one pilot machine, the rollout doc test passes there, rollback works, and a project without the module is not blocked (if the machine-level location is built).

### T6. CI layer (L3) (days TBD)

ADR 0002 deferred required CI checks and said they come "first for HIPAA / SOC 2 projects when L3 starts". The pre-push and pre-commit hooks can be skipped (`--no-verify`, another git client); a server-side check cannot.

- A check that runs the same rules on a pull request: review record present, valid and not from a build session; approval fields have a Warden entry; secrets and PHI scan of the added lines; tech-stack list. Reuse the hook code (`guard_pre_commit.py`, `guard_pre_push.py`), not a copy.
- One CI platform first (open question 5). The platforms differ per project (GitHub, Bitbucket), which is why ADR 0002 left CI out.
- The Phase 2 todo "Server-side check (CI or branch protection) for the same record rule".

**Done when:** a pull request that skipped the local hooks fails the check on the chosen platform, and a clean pull request passes.

### T7. Phase 2 follow-ups from the pilot (days TBD)

These need real use, not code first. Each item is in a Phase 2 todo list with an owner there.

- **Real-session checks:** a real block, a real warning, a real review, a real push and a real commit seen in Claude Code; the subagent session-id question (open check 7).
- **False-block numbers** (target under 5%): command, path, document, write gate, content scan, pre-commit, pre-push. Then decide: person-only resolutions, `git checkout <path>` through the gate, `COMMIT_QUESTION_REMOVED` and `COMMIT_GATE_GENERIC` (warn or block), scanning the old text of an Edit, an unparsable prompt input in HIPAA projects, README and root files counting as source in pre-push.
- **Rules not yet written:** `git checkout .` / `git restore .`, `UPDATE` without `WHERE`, export rules (`aws s3api get-object`, `gsutil cp`, `az storage blob download`, `scp` / `rsync` from a remote host).
- **Smaller code todos:** Warden approving a plain TOML file (`tech-stack.toml` carries a `---` block today); a role check for the approver once real identities exist (the key gives one); tell a developer at setup time when `python3` is older than 3.10; read `state_file` and `implementation_artifacts` from the BMAD config if a project moves them.
- **Rebase, amend and squash** make a review record stale: decide whether a "re-review a rewritten branch" shortcut is needed.

**Done when:** each item is closed, or moved to a later phase with an owner and a date, and the Phase 2 todo lists are updated.

### T8. Red-team re-run, install test and Gate 3 (days TBD)

- Re-run `scripts/install_test.py` and `scripts/red_team.py`; write `docs/reports/phase-3-red-team.md`. Add cases for what Phase 3 adds: the credential file is unreadable by the agent, events leave the machine without content, a project without the module under managed settings (if built).
- Draft the Phase 4 plan (Jira / ADO MCP, `epic.md`).

**Done when:** the Gate 3 items below are true.

## Gate 3 (proposed)

Phase 4 starts only when all of these are true:

- [ ] A throwaway session shows guard events and Claude Code events in Grafana, with the user from the key and no content.
- [ ] A collector outage queues events and they drain later; a wrong key is rejected (re-run of spike tests 1–5 on the hardened stack).
- [ ] The credential file is mode 600, outside every repo, and the red-team report shows the agent cannot read it.
- [ ] The managed-settings file is on at least one pilot machine, the rollout doc test passes, rollback is tested.
- [ ] The Phase 2 "Managed settings rollout todos" each have an owner and a date, and the ones for this phase are closed.
- [ ] The CI check fails a pull request that skipped the local hooks (if T6 stays in the phase).
- [ ] `scripts/install_test.py` and `scripts/red_team.py` pass; the Phase 3 red-team report is committed.
- [ ] A draft Phase 4 plan exists.

## Open questions (for the grill session)

Each has a suggested default. None is decided.

1. **Time box and order.** ADR 0003 and the Phase 2 todos name about eight pieces of work; the Phase 2 time box was 3 weeks. Suggested: decide the time box after question 2 to 5; build T1, T2, T3 first and run the pilot (T5, T7) in parallel with them.
2. **Where does the collector run, and who reaches it?** ADR 0003 left "VPN or public endpoint" open. Suggested default: inside the company network, TLS through ALB + ACM, reached by VPN only; ask IT.
3. **Where does the endpoint address come from on a laptop?** Options: a managed-settings environment variable, the setup script, or the credential file. Suggested default: one setting in the credential file next to the key, with an environment variable override.
4. **Retention.** The spike used 180 days. Suggested default: keep 180 days until an audit (SOC 2) asks for a different period; ask the compliance owner.
5. **CI layer: in this phase or later, and for which project and platform first?** ADR 0002 says HIPAA / SOC 2 projects first. Suggested default: one HIPAA project on GitHub, as the pilot; Bitbucket later.
6. **Windows.** Do pilot developers use Windows? It needs a queue lock without `fcntl` and a check of the hook commands (`python3`, `sh`). Suggested default: macOS and Linux only in the pilot; Windows in a later phase.
7. **Who runs the stack?** Owner, on-call, backups, key issue and rotation. Suggested default: Shameer as owner for the pilot; a named IT contact for hosting.
8. **Machine-level script location: which folder, and how do updates reach it?** Options: under the managed-settings folder, or a package installed by MDM. Suggested default: decide in the T1 ADR, with IT.
9. **Semantic prompt check spike: in this phase or later?** Suggested default: later; the Phase 3 scope is already large.
10. **Do the pilot checks run before Phase 3 starts?** The real-session items (T5, T7) need a pilot group. Suggested default: start the pilot now, so Phase 3 begins with real numbers.

## Risks

| Risk | What we do |
|---|---|
| Three services to run (ADR 0003) | Pin versions; one owner; config in the repo; backups (T2) |
| ClickHouse logs exporter is beta (ADR 0003, spike finding 4) | Pin the collector; re-test on each upgrade |
| Claude Code telemetry carries personal data (spike finding 2) | Scrub in the collector; `OTEL_LOG_USER_PROMPTS` off; review the attribute list on each Claude Code upgrade |
| A key on a laptop is read by the agent | File outside the repo, mode 600, `PATH_GUARD_CONFIG` block, red-team case; keychain later (ADR 0007) |
| Managed settings block every project without the module (Phase 2 todo) | Pilot machines only until the machine-level location exists (T5) |
| Pilot numbers are missing, so rule tuning is guesswork | Start the pilot before or at the start of the phase (open question 10) |
| CI differs per platform (ADR 0002) | One platform first; reuse the hook code |
| Scope is large | T6 (CI) and the rule todos in T7 are the first to move to a later phase |

## How we work

- One branch per task, commits `GUARD-3: what changed`.
- Claude commits locally. Shameer pushes and opens the PRs.
- Each PR shows the test command and its result.
- This plan goes through the same approval as the earlier plans, after the grill session.
