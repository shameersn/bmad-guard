# Phase 1 plan: Guard module

| | |
|---|---|
| Ticket | GUARD-1 |
| Time box | 2 weeks (10 working days) |
| Approvers | Shameer, Manesh G (PR approval). Markup format (T7): BA team too. |
| Status | Draft, waiting for approval |
| Inputs | ADRs 0001–0005; spike reports T1, T2, T3 |

## Goal

Build the guard module in this repo and install it on stock BMAD with zero stock edits. At the end of Phase 1:

1. Every guard from `experion-bmad` lives here, fixed, with unit tests for every script.
2. A setup script installs the module into a project on BMAD v6.12.4 (Claude Code + Copilot), and a re-install or upgrade does not touch it.
3. All Experion behaviour that used to be an edit to a stock skill is now an override in `_bmad/custom`.
4. The ready-for-dev gate script (ADR 0004) exists and is tested.
5. Guard events go to the local queue in the T3 format, ready for the collector in Phase 3.

## Decisions already made (input to this phase)

- Stock BMAD v6.12.4 + external guard module. No stock edits, no re-patch scripts (ADR 0001).
- Install by our setup script, not the BMAD installer's custom-module option (T2).
- Python 3.10+, standard library only, `unittest`, PEP 723 headers.
- Event format and local queue as in `spikes/collector/guard_events.py` (ADR 0003).
- Gate rule: 0 open questions, 0 open `[ASSUMPTION]` tags, confidence ≥ 80, every resolution recorded (ADR 0004).
- `experion-bmad` is read-only; we copy files, not history (ADR 0005).

## Out of scope

- Claude Code and git hooks that **enforce** the guards, and managed settings (Phase 2). Phase 1 builds the scripts the hooks will call.
- Collector, ClickHouse, Grafana deployment (Phase 3).
- A gateway server: dropped by ADR 0006 (25 Nov). Prompts are checked locally instead (T8).
- The token-optimiser work on `bmad-token-optimisation` (needs its own decision; see open questions).
- CI checks in projects; org rollout.

## What we harvest

From `experion-bmad` (`main`, plus one fix from `bmad-token-optimisation`):

| Item | Today | Tests today | Note |
|---|---|---|---|
| `agent-approval-gate-check` (Sentry) | 1 script | none | Reads `approval-state.json`; content-hash staleness |
| `agent-approval-grant` (Warden) | 2 scripts (grant, reopen) | none | Double confirmation; supersede, never delete |
| `agent-project-logger` (Ledger) | 1 script, CSV | 1 file | `.claude/skills` and `skills/` copies differ; branch commit 75b3099 fixes the CSV columns |
| `confidence-scorer` | 1 script | 1 file | Deterministic code score |
| `experion-brief-review` (Voss) | prompt only | none | Review prompt, writes a findings report |
| `prompt-gateway-guard` + `.claude/hooks/prompt_gateway_check.py` | client script + hook | 1 file | Fails open with a warning (matches ADR 0002) |
| `_bmad/custom/*.toml` + `templates/` | 12 overrides, 4 templates | n/a | 3 target skills that 6.12.4 removed |
| Confidence-scoring edits in 5 stock skills | 9 edited + 5 added files | n/a | Move to overrides (T1) |
| `scripts/revert-brief-status.js` (Husky) | Node | none | Port to Python in Phase 2 |
| `service/prompt-gateway` branch | gateway server | ? | Not moved (ADR 0006). Its pattern rules are the starting point for the local check (T8). |

## Tasks

One branch and one PR per task: `feature/GUARD-1-<short-name>`.

### T1. Module skeleton and test runner (Day 1)

- Layout: `module/skills/`, `module/hooks/` (empty until Phase 2), `module/overrides/` (files copied into `_bmad/custom/`), `scripts/` (setup, tools), `tests/`.
- One command runs every test: `python3 -m unittest discover`.
- `ruff` for lint.
- GitHub Actions job: run `unittest` and `ruff` on every PR (decided, open question 3).

**Done when:** the empty layout, the test command and the CI job are in place, and the CI job passes on the T1 PR.

### T2. Setup script (Days 1–2)

Grow `spikes/install-upgrade/setup_guard.py` into `scripts/setup_guard.py`:

- Copies module skills into every tool skill folder that exists (`.claude/skills`, `.agents/skills`).
- Copies overrides into `_bmad/custom/`. Never overwrites a project's own override files without `--force`; prints what it would change (`--dry-run`).
- Sets `core.hooksPath` (the hooks themselves come in Phase 2).
- Writes the installed module version to `_bmad/custom/guard/VERSION`.
- `--check`: reports what is missing or out of date, changes nothing.
- Refuses to run without git and BMAD.

**Done when:** unit tests cover install, re-install, dry-run, check and the refusals; the T9 end-to-end test passes.

### T3. Ledger: events instead of CSV (Days 2–3)

- Harvest `agent-project-logger`, starting from the newest copy (branch commit 75b3099).
- Replace the CSV writer with `emit()` from the T3 sender: events go to `.bmad-guard/events/queue.jsonl`; sending is off until Phase 3 (no collector configured, so events stay queued; no warning spam).
- Event fields as in ADR 0003. No document content, only paths and SHA-256.
- Keep the skill's persona and flow; update `log-action.md` to the new fields.

**Done when:** tests cover every field, the "no content" rule and the queue.

### T4. Sentry and Warden (Days 3–5)

- Harvest `agent-approval-gate-check` and `agent-approval-grant` (grant + reopen).
- Write the missing tests: missing doc, unapproved, stale (hash changed), approved; grant; reopen marks the old entry superseded and keeps it.
- Every decision emits an event through Ledger.
- Record **who** approved from the git user and the BMAD user, both in the event.

**Done when:** each script has happy-path, edge and error tests.

### T5. Confidence scorer, Voss, gateway client (Days 5–6)

- Harvest `confidence-scorer` (keep its tests, add error paths).
- Harvest `experion-brief-review` (prompt only; check it still fits the 6.12.4 brief template).
- Harvest `prompt-gateway-guard` and its hook script. Keep fail-open with a warning; emit an event for BLOCK and FLAG.

**Done when:** tests pass; the gateway client works against a stub server in tests.

### T6. Overrides on 6.12.4 (Days 6–7)

- One shared `confidence-scoring.md` in the module (the 5 copies are identical; see `spikes/skill-diff/REPORT.md`).
- Confidence step for brief, PRD, architecture, UX, epics: into the 5 `_bmad/custom/<skill>.toml` files (`persistent_facts` / `on_complete`), not into stock skills.
- Add `confidence`, `confidence_label`, `confidence_rationale` to the 3 custom templates (brief, PRD, architecture spine). Epics have no template key: the toml instruction adds the fields.
  - *Changed in T6 (25 Nov):* no template overrides. The custom templates were copies of older stock templates (the PRD one already 111 lines behind 6.12.4), i.e. forks. The overrides tell each workflow to add the frontmatter fields instead.
- Move `bmad-dev-story.toml`, `bmad-quick-dev.toml`, `bmad-create-story.toml` to `bmad-build.toml` (6.12.4 removed those skills).
- Check every override with `resolve_customization.py` in a 6.12.4 sandbox.

**Done when:** every override resolves and a scripted check confirms zero stock files changed.

### T7. Ready-for-dev gate script (Days 7–9)

- **First:** agree the markup with the BA team (one short doc in `docs/`), for example:
  - an open question is a list item under `## Open questions` without a `Resolved:` line;
  - an assumption is `[ASSUMPTION: …]`, resolved as `[ASSUMPTION-RESOLVED: … — by <name>, <date>]`;
  - generic stories carry `gate: generic` in frontmatter.
- `scripts/ready_for_dev.py <story> [--epic-summary <file>]`: counts open questions and assumptions, reads confidence, checks every resolution has who and when, compares against the last approved version so deleted items count as open.
- Output: allow/block with reasons, plus one event.
- Check that each epic has an `epic-summary.md` with functional and technical parts.

**Done when:** tests cover pass, each block reason, deleted-question case, generic story, missing epic summary. (Wiring it into `bmad-build` as a hard block is Phase 2.)

### T8. Local prompt check (Day 9) — replaces "move the gateway code" (ADR 0006)

New skill `module/skills/prompt-guard/` with `scripts/prompt_check.py`, standard library only. It checks prompt text locally; nothing is sent anywhere.

- **Rules**, starting from PromptGateway's pattern validators (read the pattern code only; never copy secrets or client data — stop and report if any are found):
  - secrets: a vendored subset of gitleaks rules (MIT licence, keep the licence notice) plus an entropy check for random-looking strings;
  - PII / PHI identifiers with validation: Luhn (cards), mod-11 (NHS numbers), Verhoeff (Aadhaar), and common formats (SSN, email, phone);
  - prompt injection: patterns, recorded only;
  - size: token estimate.
- **Actions per category** from `customize.toml`: secrets block; PHI/PII block when the project is marked HIPAA, else warn; injection recorded.
- **Project patterns and allow-listed fakes** in `_bmad/custom/project/guard/` (e.g. a client's MRN format; `sk_test_` keys).
- **Output:** one JSON object (allow / warn / block, category, rule id) and one event with category and rule id — never the matched text.
- **Hook script** for `UserPromptSubmit` in the same skill (wired into settings in Phase 2), replacing `prompt-gateway-guard`'s hook.
- Retire `prompt-gateway-guard` (it calls the server): remove it from the module; its event codes stay documented.
- **Test set:** fake positives and negatives; the test reports false-alarm and miss rates and fails above agreed limits.

**Done when:** tests pass, including the test set limits; no rule file contains a real secret or client identifier; a stub prompt with a fake key is blocked and the event has no key text.

### T9. End-to-end install test and Gate 1 (Day 10)

Automated script, in a throwaway sandbox:

1. Fresh BMAD 6.12.4 (Claude Code + Copilot), commit.
2. Run the setup script, commit.
3. BMAD quick-update and full update: `git diff` shows changes only in generated files.
4. Every override resolves; every guard script runs (`--help` or a smoke call).
5. Upgrade path: 6.11.0 + module → 6.12.4 → same checks.

Output: `docs/reports/phase-1-install-test.md` with results.

## Gate 1

Phase 2 starts only when all of these are true:

- [ ] Every harvested script has unit tests (happy path, edge, error) and they pass.
- [ ] The T9 install and upgrade test passes: no edits to upstream skill files.
- [ ] Every override resolves on 6.12.4.
- [ ] The ready-for-dev markup is agreed with the BA team.
- [ ] A draft Phase 2 plan exists.

## Open items for later phases

1. **Credential storage for developer keys (Phase 3, with ADR 0007).** The collector ingest key (`BMAD_GUARD_KEY`) arrives in Phase 3. (The gateway key is gone with the gateway, ADR 0006.) Today it comes only from an environment variable.
   - Proposed: a per-user file outside every repo, `~/.config/bmad-guard/credentials` (mode 600), written once by the setup script; scripts read the environment variable first, then this file. Later option: the OS keychain.
   - Not a `.env` file in the project: the AI agent works in that folder and could read the key into its context, and a wrong `.gitignore` commits it.
   - Phase 2 hooks should deny the agent access to `~/.config/bmad-guard/`.
   - Raised by Shameer, 25 Nov. Decide in the Phase 3 plan.
2. **Semantic prompt check (later phase, after the basic setup).** Catch PHI in free text, which patterns miss. Candidates: a Claude Code hook that asks a small Claude model (same vendor that already gets the prompt, no new BAA — check that this hook type works for prompts), or a small local model (Presidio / GLiNER). Decide by a spike measuring accuracy on the T8 test set, time per prompt and install size. Parked by Shameer, 25 Nov.
3. **Other leak paths (Phase 2).** Claude Code deny rules in managed settings for sensitive paths (environment files, `*.pem`, data dumps), and a `PreToolUse` hook that blocks sensitive reads and data-export commands. Most sensitive data reaches the model through files and command output, not typed prompts (ADR 0006).

## Open questions (all decided)

1. **Ledger CSV:** keep writing the CSV next to the event queue? **Decided (Shameer, 24 Nov): no.** The queue file is the local record; the CSV goes.
2. **Module version and delivery:** how does a project get a pinned version without a private-repo install? **Decided (Shameer, 24 Nov):** a git tag on `bmad-guard`; the developer clones it once and runs `setup_guard.py --module <path>`; the tag is written to `VERSION`.
3. **CI for this repo:** add a GitHub Actions job that runs `unittest` (and `ruff`) on every PR? **Decided (Shameer, 24 Nov): yes.** Added in T1.
4. **Token optimiser:** in Phase 1? **Decided (Shameer, 24 Nov): no.** Separate decision (ADR) after Phase 1; it must follow ADR 0001 (no stock edits).
5. **Who owns the markup decision (T7):** **Decided (Shameer, 24 Nov):** Manesh G with one BA lead.

## Risks

| Risk | What we do |
|---|---|
| BA team cannot agree the markup in time | T7 builds the script against the default format; the gate stays advisory until agreed |
| A harvested skill relies on behaviour 6.12.4 removed (e.g. `bmad-dev-story` steps) | Fix in the override or the guard skill; note it in the PR |
| Two copies of Ledger differ (`.claude/skills` vs `skills/` vs branch) | Start from the branch fix 75b3099; diff the others and keep any extra fix |
| PromptGateway rule files hold secrets or client data | Read pattern code only; stop and report before copying anything |
| Override append-only limits (T2) block a needed change | Put the behaviour in a guard skill instead; record it |

## How we work

- One branch per task, commits `GUARD-1: what changed`.
- Claude commits locally. Shameer pushes and opens the PRs.
- Each PR shows the test command and its result.
