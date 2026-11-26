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
- **D5. Endpoint:** in the credential file `~/.config/bmad-guard/credentials`, next to the key. The environment variable `BMAD_GUARD_ENDPOINT` overrides it. With no endpoint, events only queue. Moving to the real host is one setup re-run.
- **D6. Owner and retention:** Shameer owns the stack (keys, rotation, backups) for the pilot. An IT contact is named before the move to real hosting. Retention: 180 days; the compliance owner approves it before Gate 3 (todo).
- **D7. Machine-level script folder:** decided in the T1 ADR. Proposal to test: `/Library/Application Support/ClaudeCode/bmad-guard/` (macOS) and `/etc/claude-code/bmad-guard/` (Linux), next to the managed settings, root-owned so the agent cannot edit them. IT copies them with the managed file; updates use the same channel.

### CI (T6)

- **D8. Small:** one HIPAA project, GitHub Actions only. Checks: review record, secret and PHI scan of the added lines. Approval fields and the tech-stack list come later, with Bitbucket.
- **D9. Skippable at setup:** `setup_guard.py --with-ci` writes the workflow; without the flag no workflow is written. `[ci] mode = "off" | "warn" | "enforce"` in `customize.toml` controls what it does. Default `off`, so pilot dev machines need nothing. A HIPAA project goes `warn` first, then `enforce`.

### Platforms and pilot group

- **D10. macOS and Linux only** in Phase 3. `setup_guard.py --check` warns on Windows that it is not supported. Windows todo below: BAs and PMs use Windows.
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

- **ADR 0007, credential storage** (Phase 1 open item 1). A per-user file outside every repo, `~/.config/bmad-guard/credentials`, mode 600, written once by the setup script, holding the key and the endpoint (D5); scripts read `BMAD_GUARD_KEY` and `BMAD_GUARD_ENDPOINT` first, then the file. Later option: the OS keychain. Not a `.env` file in the project.
- **ADR 0008, machine-level script location** (D7).

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

### T3. Sender, credentials and queue (weeks 1–2)

- Ledger's sender reads the key and the endpoint from `BMAD_GUARD_KEY` / `BMAD_GUARD_ENDPOINT`, then the credential file (T1, D5). No endpoint: queue only.
- The setup script writes the credential file once (mode 600), never into the project, and `--check` reports a missing or too open file. Setup stays idempotent.
- `--check` warns on Windows that it is not supported (D10).
- Sending stays best effort: a failure queues the event and never blocks a commit or a tool call.

**Done when:** tests cover key from the environment, from the file, from neither (queue only, no spam), a wrong key (HTTP 401, event kept), no endpoint, and a file with open permissions; a red-team case shows the agent cannot read the credential file (SENS-3 stays green).

### T4. Claude Code telemetry and dashboards (week 3)

- OTel environment variables in the managed-settings template (and the setup script's project wiring, if wanted), with `OTEL_LOG_USER_PROMPTS` off.
- Keep the collector's scrub step and review its attribute list on each Claude Code upgrade (spike finding 2).
- Grafana panels: events per user and verdict, blocks per rule, warnings per rule. Claude Code events carry the name in `Body`, not `EventName` (spike finding 3).
- The two reports from ADR 0002 and the Phase 2 todo lists:
  - **commits with no guard events** (a commit that no hook saw: `--no-verify`, setup not run);
  - **review records with no `REVIEW_RECORDED` event** (a forged record; Phase 2 review-record todo).

**Done when:** one throwaway session shows events from the guards and from Claude Code in Grafana (local Docker stack), with the user from the key and no prompt text; both reports run on test data.

### T5. Machine-level scripts and the managed-settings pilot (week 3)

The Phase 2 todo lists "Before the managed file goes on any pilot machine" and "Before the full IT rollout" are the source of this task.

- Implement the machine-level script location from ADR 0008: a setup script change, a template change and an install-test case.
- An uninstall for the wiring in the setup script (rollback is by hand today).
- Run the rollout doc test (steps 1–8) on one machine and record the results; test the rollback.
- Pilot machine list: every Claude Code project on a pilot machine has the module and `setup_guard.py --check` says `up to date`, until the machine-level location removes this need.
- Claude Code 2.1.295 or later on every pilot machine (`onFailure: "block"`).
- Close the open `PreToolUse` checks in `docs/hooks.md` in a real session (open checks 1, 2, 3, 4, 5, 8).

**Done when:** the managed file is on at least one pilot machine, the rollout doc test passes there, rollback works, and a project without the module is not blocked.

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
  - **First push blocks.** The BMAD install commit (`.github/...`) and the setup commit (`.gitignore`) count as source in pre-push, so the first push of a pilot project needs a review record (`PUSH_NO_RECORD`). Decision: treat the install and setup files as non-source in pre-push. Until then, the install guide tells pilot projects to run one review before the first push. Owner: Shameer. Week 1, before the pilot starts.
  - `setup_guard.py --project <missing folder>` crashes with a traceback; it should exit 2 with a message, like a folder that is not a git repo.
  - `setup_guard.py --check` prints `0 conflict(s)` when `core.hooksPath` points elsewhere (it does end with `NOT up to date`): fix the wording.
  - `epics.md` gets no Warden step. Decision: none for the pilot; the ready-for-dev gate and the plan approval hold stories. Look again with the pilot numbers.
  - Is `/clear` a fresh session for the review guard? Not verified: the reviewer guide says to quit and restart `claude`. Check in a real session (with open check 7).
  - Minimum Node version for the BMAD installer: not checked. Owner: Shameer, during the trial run.
- **Rebase, amend and squash** make a review record stale: decide whether a "re-review a rewritten branch" shortcut is needed.

**Done when:** each item is closed, or moved to a later phase with an owner and a date, and the Phase 2 todo lists are updated.

### T8. Red-team re-run, install test, pilot pack update and Gate 3 (week 4)

- Re-run `scripts/install_test.py` and `scripts/red_team.py`; write `docs/reports/phase-3-red-team.md`. Add cases for what Phase 3 adds: the credential file is unreadable by the agent, events leave the machine without content, a project without the module under managed settings, the CI check in `off` and `enforce` mode.
- Update the pilot pack (T0) for events, credentials and CI.
- Draft the Phase 4 plan (Jira / ADO MCP, `epic.md`, semantic prompt check, Windows).

**Done when:** the Gate 3 items below are true.

### T9. Windows L1 hooks (only if D11 needs it; before T6)

Claude Code hooks on Windows for BAs and PMs: the hook command (`python` / `python3`), paths, `setup_guard.py` and `--check`. No git hooks, no queue lock without `fcntl`.

**Done when:** a BA on Windows runs the planning flow (brief → PRD with Warden approval) with the L1 guards on, and the install test has a Windows case or a written manual check.

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
- [ ] A BA on Windows runs the planning flow with the L1 guards (only if T9 exists).
- [ ] `scripts/install_test.py` and `scripts/red_team.py` pass; the Phase 3 red-team report is committed.
- [ ] A draft Phase 4 plan exists.

## Todos

- [ ] **Pilot people and project** (D12). Owner: Shameer. Date: end of week 1 at the latest.
- [ ] **Mac or Linux machine for a BA** (D11). Owner: Manesh G. Date: end of week 1. Decides whether T9 exists.
- [ ] **Windows support** for BAs and PMs beyond L1 (git hooks, queue lock without `fcntl`, a Windows CI runner for the install test). BAs and PMs use Windows. Owner: Shameer. Phase 4 at the latest.
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
| BAs and PMs on Windows cannot join the pilot, so planning guards get no numbers | Mac or Linux for one BA (D11); else T9 |
| Local Docker stack sees only one machine's events | Accepted for the pilot; the hosting plan is written in T2 (D4) |
| CI differs per platform (ADR 0002) | GitHub first; reuse the hook code; off by default (D8, D9) |
| Scope is large (4 weeks) | Cut T6 first, then the T7 rule todos (D2) |

## How we work

- One branch per task, commits `GUARD-3: what changed`.
- Claude commits locally. Shameer pushes and opens the PRs.
- Each PR shows the test command and its result.
