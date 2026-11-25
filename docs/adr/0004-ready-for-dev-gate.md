# 0004 — Ready-for-dev gate: counted, not self-scored

- **Status:** Accepted (2026-11-25)
- **Date:** 2026-11-24
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G, BA team; team approval by PR

## Context
Bugs reached development when stories had a confidence score of 70–72 and open questions nobody had answered. Other causes:
- missing details (for example, a missing link between screens);
- stories with many acceptance criteria built on assumptions.

Two weak points today:
1. **Confidence is the model's own opinion.** The confidence-scoring step (T1) asks the LLM to rate its document from 0 to 100. Nothing checks it.
2. **Questions can be deleted to raise the score.** Removing an open question looks the same as answering it.

## Options
### Option A — Keep the LLM self-score (today)
- Pros: already built.
- Cons: not checked; can be gamed; did not stop the bugs.
- Cost / effort: none.

### Option B — Scripted gate on counted facts, plus a recorded resolution for every item
A story moves to development only when a script finds:
- zero open questions;
- zero open `[ASSUMPTION]` tags;
- plan confidence of 80 or more.

Every closed question or assumption has a resolution record: who, when and the answer. A removed item without a record counts as still open.
- Pros: objective; cannot be gamed by deleting; gives a clear reason when it blocks.
- Cons: needs a fixed format for questions and assumptions in documents; BAs must record answers.
- Cost / effort: medium.

### Option C — Human sign-off only (Warden)
- Pros: a person is accountable.
- Cons: people approve under time pressure; no check of the content.
- Cost / effort: low.

## Decision
We choose **Option B, together with Warden's human approval**. Warden decides *who* approves; the gate checks *what* is approved.

Rules:
- Blocking conditions: open questions > 0, open `[ASSUMPTION]` tags > 0, confidence < 80.
- The confidence score stays, but only as one input. The counts are the hard check.
- Every resolution is stored with who and when. Deleting a question is not resolving it.
- An AI write to approval or resolution fields is blocked by a local hook (ADR 0002). Only the human-confirmed Warden flow may set them.
- Each epic gets an `epic-summary.md` with a functional part and a technical part. Stories link to it.
- No limit on the number of acceptance criteria per story.
- Generic stories (setup, chores) may skip the gate when they are marked as such.
- No check for the "link between screens" item; skipped by team decision.

## Consequences
- Good:
  - Blocks for a clear, countable reason.
  - Removes the "delete the question" shortcut.
  - Leaves an audit trail of who resolved what.
- Bad / risks:
  - Extra work for BAs to record answers.
  - The format must be stable, or the script misreads documents.
  - The "generic" marker can be misused; review its use in the pilot.
- Follow-up work (Phase 1):
  - Define the markup for questions, assumptions and resolutions.
  - Write the gate script with tests.
  - Hook it into `bmad-build`, which replaces `bmad-dev-story` and `bmad-quick-dev` in BMAD 6.12.4 (T2).
  - Log every gate decision as an event (ADR 0003).
  - Agree the format with the BA team.

## Related
- Past ADRs: 0001 (architecture), 0002 (enforcement layers), 0003 (event logging).
- Links: `spikes/skill-diff/REPORT.md` (T1: confidence scoring is a self-assessment).
