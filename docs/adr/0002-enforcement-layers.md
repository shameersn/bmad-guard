# 0002 — Enforcement layers: local hooks now, CI later

- **Status:** Accepted (2026-11-25). Prompt-gateway part superseded by [0006](0006-local-prompt-check.md).
- **Date:** 2026-11-24
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR

## Context
Prompt rules alone do not stop a model. In the team review we saw:
- an AI writing "approved" into a markdown approval field;
- code written before the plan was approved;
- a prompt gateway that failed silently, so destructive commands were not checked.

We need guards that run **outside the model** and can block.

Constraints:
- CI platforms differ per project (GitHub, Bitbucket), so there is no CI check for now (team decision, 24 Nov).
- Commits must never wait on the log server.
- A Husky commit guard was felt to be too strict, and it adds Node to Python projects.

## Options
### Option A — Prompt rules only (today)
- Pros: nothing to install.
- Cons: the model can skip them; no audit; failed in practice.
- Cost / effort: none.

### Option B — Layered local enforcement now, CI later
- L0: prompt rules in `_bmad/custom` overrides (guidance only).
- L1: Claude Code hooks (`UserPromptSubmit`, `PreToolUse`, `SessionStart`), set by managed settings so a developer cannot turn them off.
- L2: plain Python git hooks, installed by setting `core.hooksPath`. No Husky.
- L3: CI required checks, deferred; added per project later.
- Pros: blocks at the moment of action; works on any CI; small Python scripts with tests.
- Cons: local hooks can be bypassed (`git commit --no-verify`, other editors, setup script not run).
- Cost / effort: medium.

### Option C — CI required checks now
- Pros: cannot be bypassed locally.
- Cons: different per CI platform; feedback comes late (after push); not wanted for the pilot.
- Cost / effort: high, per project.

## Decision
We choose **Option B** because it blocks at the right moment and fits every project, and we accept the bypass risk with the mitigations below.

Fail modes:

| Guard | If it fails or cannot run |
|---|---|
| Destructive-command check (`PreToolUse`, local) | **Fail closed**: block. It runs locally, so it does not depend on the network. |
| AI write to approval fields (`PreToolUse`, local) | **Fail closed**: block. Only Warden's script, after human confirmation, may set them. |
| Ready-for-dev gate (ADR 0004) | **Fail closed**: block the move to development. |
| ~~Prompt gateway (remote)~~ | ~~Fail open~~. Superseded by ADR 0006: a local prompt check that can block. |
| Event logging (ADR 0003) | Never blocks; events queue locally. |

## Consequences
- Good:
  - Real blocks for the risky actions.
  - Every guard decision is an event.
  - No Node or Husky in Python projects.
- Bad / risks:
  - Local hooks can be skipped.
  - `core.hooksPath` is local git config (T2), so each developer must run the setup script after cloning.
- Mitigations:
  - Managed settings for the Claude Code hooks.
  - A `SessionStart` check that warns when `core.hooksPath` is not set.
  - A daily "commits with no guard events" report (needs ADR 0003).
  - CI checks first for HIPAA / SOC 2 projects when L3 starts.
- Follow-up work:
  - Phase 2 builds the hooks.
  - Define the managed-settings rollout.
  - Define the list of destructive commands.

## Related
- Past ADRs: 0001 (architecture), 0003 (event logging), 0004 (ready-for-dev gate).
- Links: `spikes/install-upgrade/REPORT.md` (T2: `core.hooksPath` survives BMAD updates).
