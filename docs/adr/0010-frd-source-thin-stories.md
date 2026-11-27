# 0010 — FRD is the requirement; stories are thin pointers

- **Status:** Proposed (team to review the example, then decide)
- **Date:** 2026-11-27
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G, BA team; team approval by PR
- **Changes:** 0004 (epic summary: Technical part only; `gate: technical` for setup stories)

## Context
The flow is Brief → PRD → FRD (one per PRD feature) → Epics & Stories → Build.
In the team review, the BA team asked: the FRD is already story-level, so why does an agent write
epics and stories again? They are worried about ambiguity.

What we found (bmad-guard and the clinic-demo pilot):
- **Content repeats.** The story override says "acceptance criteria come from those FRD requirements".
  The agent rewrites them in its own words. The same rule then exists in two wordings, and the
  wordings drift.
- **`epics.md` has no approval.** The FRD is approved by Warden and hashed. The story is not. A story
  can change the meaning and no guard sees it.
- **The epic layer is a copy.** One PRD feature = one FRD = one epic.
- **Stories carry some things the FRD does not:** build order across features, setup and technical
  work from the architecture, a size that fits one `bmad-build` session, and the key that the
  ready-for-dev gate, write gate, build and review records and pre-push check use.

## Example: what repeats today, and what would not
FRD-CLINIC-F001, `FR-F001-02 — List Free Slots through the API`, has 7 acceptance criteria
(Section 4.2.1) and 4 key transactions.

**Today: story written by `bmad-create-epics-and-stories`**

```markdown
### Story 1.2: List free slots via API
Feature: F-001 · Requirements: FR-F001-02
As an API client, I want to get the free slots, so that I can show them.
**Acceptance Criteria**
Given a client calls GET /api/slots
When free slots exist
Then the response is 200 with a list of slots, earliest first
And each slot has a start date and time
Given there are no free slots ... Then an empty list is returned
Given a wrong host ... Then an error is returned
```

The same rules are written a second time, with less detail. The format `YYYY-MM-DDTHH:MM` is gone,
the exact error codes and messages are gone, and the rule "unknown query parameters are ignored" is
gone. A developer who reads only the story builds something different from the FRD.

**Proposed: thin story**

```markdown
---
story: 1.2
feature: F-001
requirements: [FR-F001-02]
depends_on: [1.1]
confidence: 85
---
# 1.2 List free slots through the API

Delivers FR-F001-02 of FRD-CLINIC-F001. Acceptance criteria: the FRD, Section 4.2.

## Technical notes
Architecture AD-6: handler in the slots route; reuse the slot rules module from 1.1.

## Open questions
- None.
```

No functional text. The build loads Section 4.2 (and its NFRs from 8.1) from the approved FRD.

## Options
### Option A — Keep as is
- Pros: no change; works with stock BMAD tools (sprint planning, epic context).
- Cons: content repeats and drifts; an unapproved layer sits between approved ones; BAs review the
  same content twice.
- Cost / effort: none.

### Option B — Skip epics and stories; build straight from the FRD
- Pros: one source; least BA work.
- Cons: the gate, write gate, build and review records and pre-push check all key on a story and need
  re-keying. The build agent decides order and splitting inside a coding session, where no BA sees
  it. Setup work has no home and drifts to `gate: generic`. Big FRDs (8–15 FRs) go past
  `bmad-build`'s single-goal size. One FRD per ticket is too big for a sprint.
- Cost / effort: high (changes guards we finished testing in Phase 2).

### Option C — Skip epics; thin stories that point at the FRD
- Epic = PRD feature, in FRD order. No epic design step.
- Default: 1 FR = 1 story. Merge only when one FR cannot ship alone. Split only when one FR is bigger
  than one build; the story then names its AC numbers (`FR-F001-04 AC 1–4`).
- A story holds `feature`, `requirements`, `depends_on`, `confidence` and technical notes from the
  architecture. **It holds no acceptance criteria and no functional text.**
- Setup and technical stories: `feature: none`, `gate: technical`, checked against the approved
  architecture.
- A new functional question during slicing reopens the FRD (Warden). It never becomes a story `Q-n`.
- A script checks coverage: every non-withdrawn FR id is in a story; no FR id is outside its feature;
  no FRD Section 4 text is copied into a story.
- Pros: one source of truth; no unapproved requirement content; the guards keep their story key (one
  small gate change); order, setup work and sizing stay.
- Cons: a new skill and script to build and maintain; stock BMAD epic context and sprint planning are
  not used for guarded projects; developers open the FRD, not only the story.
- Cost / effort: medium-low (about 3–4 days, Phase 1).

## How C is built (stays inside ADR 0001)
Overrides can only add to a stock skill. Stock `bmad-create-epics-and-stories` writes epics and
Given/When/Then criteria in its own steps, and an override cannot remove those steps. So C is a small
guard skill of our own (working name `experion-story-slicer`), next to Fenn. It is not an override of
the stock skill. The coverage and no-copy check is a script, not an instruction to the model.

## Decision
**Proposed: Option C.** The team reviews the example above and decides in the PR.

## Consequences
- Good:
  - The FRD's acceptance criteria are written once, in an approved and hashed document.
  - BAs review a short ordered list of FR groups, not a second document.
  - A ticket has one clear spec: the link to the FRD.
- Bad / risks:
  - Developers must read the FRD section. The build loads it for them, but a person reading ADO sees
    only the link and a summary.
  - Our own skill must be kept in step with BMAD upgrades (the same as Fenn).
  - The no-copy check can give false blocks on short shared phrases. Tune it in the pilot.
- Follow-up work:
  - Phase 1: slicer skill; `story_coverage.py` with tests (gap, out-of-feature id, copied AC, split
    FR, setup story, withdrawn FR); a `bmad-build` override that loads FR blocks by id; update
    `ba-pm.md` step 18 and `ready-for-dev-markup.md`; re-run the clinic-demo trial.
  - Phase 2: sync from repo to ADO/Jira (one Feature/Epic per FRD, one Story per thin story, linked at
    the approved commit). Ticket ids go in `frds/F-nnn/links.json`, never in the FRD. Depends on the
    Phase 4 MCP spike.
  - Phase 3: `gate: technical` against the architecture; count `gate: generic` uses before and after.
- Pilot measures: 0 stories with FRD text copied in; 100% FR coverage; BA time from FRD approval to
  ready stories; 0 build plans that re-ask a question the FRD already answers.

## Related
- Past ADRs: 0001 (no fork; own skills where an override cannot do it), 0004 (ready-for-dev gate,
  epic summary, generic marker).
- Docs: `docs/guides/ba-pm.md` (steps 14–19), `docs/plans/phase-2.md` (no Warden on `epics.md`), the
  team doc "Guarded BMAD: Can we skip Epics & Stories after the FRD?".
- Plan: `docs/plans/adr-0010-thin-stories.md` (GUARD-3, Phase 1 and Phase 3 of this ADR).
