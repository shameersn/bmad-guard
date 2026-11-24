# 0005 — Repo layout: module repo, gateway repo, plans and ADRs in the repo

- **Status:** Proposed
- **Date:** 2026-11-24
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR

## Context
`experion-bmad` mixes several things in one repo:
- a BMAD install (two versions, T1);
- six custom skills;
- overrides;
- demo and experiment branches;
- a gateway server on one branch.

No clear owner, no tests in CI, and the history holds demo work and large generated diffs.

We need a home for the guard module, a home for the gateway, and a place for plans and decisions that the team approves by PR.

Constraints:
- A private-repo install of the module is not allowed for now (team decision), so projects get it through a setup script.
- The repos start under a personal account and move to the org later with history removed.

## Options
### Option A — Keep everything in `experion-bmad`
- Pros: no move.
- Cons: install output, custom code and experiments stay mixed; BMAD upgrades keep touching our code; old history comes along.
- Cost / effort: none now; high later.

### Option B — Two new repos
- `bmad-guard`: guard skills and scripts, setup script, `_bmad/custom` overrides to copy into projects, collector and ClickHouse config, plans, ADRs, spikes.
- `bmad-guard-gateway`: the prompt gateway server, hosted per project.
- Pros: clean history; the module has tests and a clear owner; the gateway deploys on its own.
- Cons: two repos to keep in step; harvesting old code takes work.
- Cost / effort: medium (Phase 1).

### Option C — One repo per project, each with its own copy of the guards
- Pros: each team owns its copy.
- Cons: copies drift; fixes do not spread.
- Cost / effort: high over time.

## Decision
We choose **Option B** because it separates our code from BMAD's install output and keeps decisions next to the code.

Layout of `bmad-guard`:

| Path | What |
|---|---|
| `docs/plans/` | One plan per phase, approved by PR before work starts |
| `docs/adr/` | These ADRs |
| `spikes/` | Phase 0 experiments, each with a report |
| `skills/`, `scripts/`, `hooks/`, `overrides/` | The module (Phase 1; exact names set in the Phase 1 plan) |
| `deploy/collector/` | Collector, ClickHouse and Grafana config (Phase 3) |

Working rules:
- Branches `feature/GUARD-<phase>-<name>`, commits `GUARD-<phase>: what changed`.
- One PR per task.
- No secrets in git.
- `experion-bmad` is read-only. We harvest from it and copy no history.

## Consequences
- Good:
  - Clean, reviewable history.
  - Plans and ADRs are approved the same way as code.
  - The gateway can be deployed and versioned on its own.
- Bad / risks:
  - Moving to the org later (history removed) changes the repo URLs in docs and setup scripts.
  - Projects get the module through the setup script, so we need a release and version scheme.
- Follow-up work:
  - Phase 1: harvest the 6 custom skills and the overrides; move the gateway code into `bmad-guard-gateway`; define how the setup script gets a pinned module version.
  - Later: move both repos to the org.

## Related
- Past ADRs: 0001 (architecture).
- Links: `README.md`; `spikes/skill-diff/REPORT.md` (T1, branch survey of `experion-bmad`).
