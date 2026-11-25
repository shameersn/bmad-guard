# Phase 0 plan: Decide

| | |
|---|---|
| Ticket | GUARD-0 |
| Time box | 1 week (5 working days). Each spike: 1 day at most. |
| Approvers | Shameer, Manesh G (PR approval). ADRs: whole team. |
| Status | Done (2026-11-25) |
| Overview doc | "Guarded BMAD: Org Rollout Plan" (Claude doc) |

## Goal

Prove the design before we build the guard module. At the end of Phase 0 we know:

1. Which changes in the old `experion-bmad` repo are real Experion edits, and where each one moves.
2. That a custom module and `_bmad/custom` overrides survive a BMAD install and re-install, with zero edits to stock files.
3. That the event pipeline works: guard event → collector → ClickHouse → Grafana, with the user taken from the key.
4. All decisions so far are written down as ADRs and approved.

## Decisions already made (input to this phase)

- Option C: stock BMAD + external guard module. No fork.
- BMAD pinned at v6.12.4.
- Pilot supports Claude Code only. No CI checks for now. Org rollout is out of scope.
- Module: Python 3.10+, standard library only, `unittest`, PEP 723 script headers.
- Git hooks: plain Python hooks, installed by setting `core.hooksPath`. No Husky.
- Prompt gateway: separate repo (`bmad-guard-gateway`), hosted per project on internal AWS. If down: allow, log, warn.
- Events: OpenTelemetry collector → ClickHouse → Grafana. Per-developer ingest-only key. No S3. No DB credentials on laptops.
- Commits never wait for the log server: events queue locally and the user is warned.
- Private-repo install of the module is not allowed for now: a setup script installs it.
- Ready-for-dev rule: zero open questions, zero open `[ASSUMPTION]` tags, plan confidence 80 or more. Every resolution is recorded with who made it.

## Out of scope

- Writing the guard module itself (Phase 1).
- Moving code from `experion-bmad` or the `service/prompt-gateway` branch (Phase 1).
- AWS deployment, TLS, ALB (later phase).
- CI checks, org rollout.

## Tasks

### T1. Spike: skill diff (Day 1)

**Question:** How many of the ~40 "edited" BMAD skills in `experion-bmad` are real Experion edits, and how many are a BMAD version difference or installer output?

**Branch:** `feature/GUARD-0-spike-skill-diff`
**Folder:** `spikes/skill-diff/` (raw install in `work/`, git-ignored)

Steps:

1. Install clean BMAD v6.11.0 (modules `core` and `bmm`, IDE Claude Code) into `spikes/skill-diff/work/clean-6.11.0/`, using `npx bmad-method@6.11.0 install`.
   - If the installer is interactive only, run it in a terminal tab together with Shameer.
2. Diff `work/clean-6.11.0/.claude/skills/` against `experion-bmad/.claude/skills/`. Also diff `_bmad/` (minus `_bmad/custom/`).
3. Look at the `bmad-extension` branch commit "Installed the bmad method again - File changes found". Note which files the installer changed back.
4. Classify every changed file:
   - **Guard edit:** added for Sentry, Warden, Ledger, confidence scoring, Voss or the gateway.
   - **Other content edit:** a real change to BMAD behaviour.
   - **Generated:** project name, paths, install config, manifest.
5. For each guard or content edit, name its new home: `_bmad/custom` override, guard module, or drop.

Output: `spikes/skill-diff/REPORT.md` with a table: skill · files changed · class · new home. Only file names and short summaries; no copied document content.

**Pass when:** every changed file is classified and every real edit has a new home.

### T2. Spike: install and re-install (Day 2)

**Question:** Do a custom module and `_bmad/custom` overrides survive a BMAD install and a re-install of v6.12.4, with zero edits to stock files? Can a setup script install the module from a local folder?

**Branch:** `feature/GUARD-0-spike-install-upgrade`
**Folder:** `spikes/install-upgrade/` (sandbox project in `work/`, git-ignored)

Steps:

1. Create a sandbox project with `git init`. Install BMAD v6.12.4 (`core`, `bmm`, Claude Code). Commit.
2. Find how v6.12.4 installs a custom module (installer option, `bmad-module-builder` output format, or local path). Write down what we found.
3. Build a minimal test module: one skill with one Python script that prints "guard ok".
4. Install the test module two ways:
   - a. through the BMAD installer (if it supports a local path);
   - b. through a small setup script that copies the module and sets `core.hooksPath` to a test hook.
5. Add one override, `_bmad/custom/bmad-prd.toml`, with one `persistent_facts` line and one `activation_steps_prepend` line. Check it resolves with BMAD's `resolve_customization.py`. Commit.
6. Re-run the v6.12.4 installer ("Modify" / update). Run `git diff`.
7. Stretch: install v6.11.0 first, add the module and override, then upgrade to v6.12.4. Run `git diff`.

Output: `spikes/install-upgrade/REPORT.md` with the commands used, the `git diff --stat` after each re-install, and the install method we recommend for Phase 1.

**Pass when:** after re-install, `git diff` shows no change to the module, the override, or the hook path, and the override still resolves. No stock BMAD file needed an edit.

### T3. Spike: event pipeline (Day 3)

**Question:** Does guard event → OTel collector → ClickHouse → Grafana work with off-the-shelf parts, with the user stamped from the key?

**Branch:** `feature/GUARD-0-spike-collector`
**Folder:** `spikes/collector/` (compose file and config committed; data and keys in `work/`, git-ignored)

Setup (Docker via OrbStack, image tags pinned):

- `otel/opentelemetry-collector-contrib` with `basicauth/server` (htpasswd), `attributes` processor (`from_context: auth.username`), ClickHouse exporter.
- `clickhouse/clickhouse-server`: admin creates database `guard` and the events table; the collector uses an insert-only user `otel_writer`.
- `grafana/grafana` with the ClickHouse data source plugin.
- Two fake developers, `dev-a` and `dev-b`, with generated keys. Keys and passwords live only in `work/` and a git-ignored `.env`; `.env.example` is committed.

Tests:

1. **Happy path:** the sample sender (standard-library Python) sends 10 events as `dev-a`. All 10 appear in ClickHouse within 1 minute.
2. **User stamping:** `dev-b` sends an event that claims `user=dev-a`. ClickHouse shows `dev-b`.
3. **Bad key:** a wrong key gets HTTP 401. Nothing is stored.
4. **Server down:** stop the collector, send events. The sender keeps them in the local queue and prints a warning. Start the collector; the queue drains.
5. **Insert-only:** `otel_writer` cannot `ALTER`, `DELETE` or `DROP` (each fails).
6. **Claude Code telemetry:** run one throwaway Claude Code session with the `OTEL_*` variables pointed at the local collector. Events arrive. No prompt text is stored (`OTEL_LOG_USER_PROMPTS` off).
7. **Dashboard:** one Grafana panel shows events per user and verdict.
8. Record the stability level of the ClickHouse exporter for the pinned collector version.

Fallback: if `from_context: auth.username` does not work, put a small nginx in front that sets the user header. Test 2 must still pass.

Output: `spikes/collector/REPORT.md` with test results, the working config, the exporter stability level and the measured delay.

**Pass when:** tests 1–5 pass. Tests 6–7 pass or have a clear workaround.

### T4. ADRs (Day 4)

**Branch:** `feature/GUARD-0-adrs`
**Folder:** `docs/adr/`, using the `/adr` format (context, options, decision, consequences).

| ADR | Decision |
|---|---|
| `0001-architecture.md` | Stock BMAD + external guard module, no fork, pilot on Claude Code only |
| `0002-enforcement-layers.md` | Local hooks (Claude Code + git), no CI for now, fail modes, destructive-command hook |
| `0003-event-logging.md` | OTel collector → ClickHouse → Grafana, per-developer keys, local queue, no S3 |
| `0004-ready-for-dev-gate.md` | Open questions, assumptions, confidence ≥ 80, recorded resolutions |
| `0005-repo-layout.md` | Module repo, gateway repo, plans and ADRs in the repo |

ADRs use the spike results. If a spike fails, the matching ADR says so and names the new option.

**Pass when:** all 5 ADRs are merged after team approval.

### T5. Update the roadmap (Day 5)

Update the roadmap diagram and phase text in the Claude doc:

- Phase 0: remove "private git repo" check; add the three spikes.
- Phase 2: remove "required CI checks"; local hooks only.
- Phase 3: OTel collector + ClickHouse + Grafana.
- Phase 5 (org rollout): marked out of scope for now.

### T6. Draft the Phase 1 plan (Day 5)

Write `docs/plans/phase-1.md` (guard module) from the spike results, on `feature/GUARD-1-phase-1-plan`. It goes through the same approval.

## Gate 0

Phase 1 starts only when all of these are true:

- [x] The 5 ADRs are approved by the team.
- [x] `REPORT.md` exists for each of the 3 spikes, with pass or fail and evidence.
- [x] The roadmap in the Claude doc is updated.
- [x] A draft Phase 1 plan exists.

## Risks

| Risk | What we do |
|---|---|
| The BMAD installer is interactive only, so it cannot be scripted | Run it in a terminal tab with Shameer. Note it as a Phase 1 problem for the setup script. |
| v6.12.4 has no way to install a module from a local path | The setup script (T2 step 4b) becomes the only install method. |
| `from_context: auth.username` does not work | nginx fallback (T3). |
| A spike runs over 1 day | Stop, write what we know in the report, and decide together. |
| Spike data leaks client or personal data | Only fake users and fake events. The skill-diff report lists file names and summaries, not document content. |

## Downloads (approved for Phase 0 only)

- npm: `bmad-method@6.11.0`, `bmad-method@6.12.4` (run through `npx`, into `spikes/*/work/`).
- Docker images: `otel/opentelemetry-collector-contrib`, `clickhouse/clickhouse-server`, `grafana/grafana`, and the Grafana ClickHouse plugin.
- Fallback only: `nginx`.

## How we work

- One branch per task. Commits: `GUARD-0: what changed`.
- Claude commits locally. Shameer pushes and opens the PRs.
- Each spike report is reviewed before the ADRs are written.
