# 0001 — Stock BMAD plus an external guard module

- **Status:** Accepted (2026-11-25)
- **Date:** 2026-11-24
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR

## Context
Experion uses the BMAD Method with its own guards: Sentry (gate check), Warden (approval), Ledger (log), Voss (brief review), a confidence scorer and a prompt gateway. Today these live in `experion-bmad`, mixed with the BMAD install.

Problems:
- Guard changes were made inside BMAD's own skill files. A BMAD re-install wiped them (T1: commit fa7d1a3 on `bmad-extension` removed all 14 confidence-scoring changes).
- Claude Code and Copilot run different BMAD versions today (T1: `.claude/skills` is 6.10.0, `.agents/skills` is 6.11.0).
- Guards are prompt text, so the model can skip them.

Constraints: small platform team; Python is the team's script language; BMAD moves fast (6.10 → 6.12 in a few months); some projects are HIPAA / SOC 2.

## Options
### Option A — Fork BMAD (edit stock files)
- Pros: full control; one repo.
- Cons: every BMAD upgrade is a merge; edits get lost on re-install (T1 proved it); the token-optimiser branch already needs a "re-patch after update" script.
- Cost / effort: low now, high on every upgrade.

### Option B — Stock BMAD + external guard module + `_bmad/custom` overrides (no fork)
- Pros: BMAD upgrades stay a plain installer run; guard code lives in our own folders that the installer never touches; guards are tested Python scripts, not prompt text.
- Cons: overrides can only append (T2); some behaviour may need a guard skill instead of an edit; we depend on BMAD's override points staying stable.
- Cost / effort: medium (build the module and setup script once).

### Option C — Drop BMAD; use a lighter kit (see Work-OS ADR experion/0001, AI-SDLC kit)
- Pros: very small; no upstream to track.
- Cons: throws away BMAD's planning workflows the team already uses; still needs the same guards.
- Cost / effort: medium; retrains the team.

## Decision
We choose **Option B** because the spikes showed it works and Option A has already lost work:
- T1: only 5 stock skills had real edits (all confidence scoring), and all of them can move into overrides.
- T2: a module installed by our setup script survived a BMAD quick-update, a full update and a 6.11.0 → 6.12.4 upgrade, with zero stock edits.

Scope:
- The pilot supports **Claude Code only**.
- BMAD is pinned at **v6.12.4**, installed with both `claude-code` and `github-copilot` tools so both runtimes match.

## Consequences
- Good:
  - BMAD upgrades are a plain installer run plus one commit of generated config.
  - Guards are testable code.
  - Claude Code and Copilot stay on the same version.
- Bad / risks:
  - We must track BMAD's override points on each upgrade.
  - The installer's own custom-module install is buggy in 6.12.4 (T2), so we rely on our setup script.
  - 6.12.4 removed `bmad-dev-story`, `bmad-quick-dev` and `bmad-create-story` (replaced by `bmad-build`). Existing overrides for them must move.
- Rules from now on:
  - No edits to stock BMAD files.
  - No scripts that re-patch stock files after an update.
- Follow-up work:
  - Phase 1 builds the module.
  - Move the 5 confidence-scoring edits into `_bmad/custom`.
  - Decide what to do with the token-optimiser branch (it edits stock files).

## Related
- Past ADRs: Work-OS `experion/0001` (AI-SDLC kit), Proposed. This ADR does not supersede it; it picks BMAD for this pilot.
- Links: `docs/plans/phase-0.md`; `spikes/skill-diff/REPORT.md` (T1); `spikes/install-upgrade/REPORT.md` (T2); ADR 0002, 0005.
