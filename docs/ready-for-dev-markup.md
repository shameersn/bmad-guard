# Ready-for-dev markup

| | |
|---|---|
| Status | Agreed by Manesh G and the BA lead (2026-11-25) |
| Decide | Manesh G and one BA lead (Phase 1 plan, open question 5) |
| Related | ADR 0004 (ready-for-dev gate), Phase 1 task T7; ADR 0010 (thin stories, Proposed) |
| Script | `module/skills/ready-for-dev-gate/scripts/ready_for_dev.py` |

## Why we need a format

The gate is a script. It counts things in the story. It can only count what it can find.
So questions, assumptions and answers need one fixed, easy-to-type format.

The gate lets a story into development only when:

- there are 0 open questions;
- there are 0 open assumptions;
- every answer says **who** and **when**;
- no question or assumption was deleted;
- `confidence` is 80 or more;
- the epic has an `epic-summary.md` with a functional and a technical part (a thin story points to its
  feature's technical summary instead, see 5);
- every FR id in the story's `requirements` is in the feature's FRD (thin stories, see 1 and 5);
- a technical story has an approved, unchanged architecture (see 1 and 5).

The format is agreed, so the gate is **enforced**: a block is a hard stop. A project can set the skill's `mode` to `advisory` for the chat flow, but the write gate hook (tool-guard) blocks source writes either way.

## The format

### 1. Frontmatter

A story with an epic summary (the form before ADR 0010, still read by the gate):

```yaml
---
confidence: 85
epic: 2
---
```

- `confidence`: a whole number from 0 to 100. Required.
- `epic: <n>` or `epic_summary: <path from the project root>`: tells the gate where the epic summary is.
- `gate: generic`: only for chore stories (no feature, no architecture link). The gate skips the story (it still
  logs that it did). A person sets it; the AI never does.
- `gate: technical`: for setup work the architecture asks for (project starter, tooling, pipeline). The gate does
  **not** skip it (see 5). Sloane writes it, with `feature: none` and no `requirements`.

A **thin story** (ADR 0010, written by Sloane, `experion-story-slicer`) adds these keys. The acceptance criteria
stay in the FRD; the story only points at them:

```yaml
---
story: F001-02
feature: F-001
requirements: [FR-F001-02]
depends_on: [F001-01]
epic_summary: _bmad-output/planning-artifacts/stories/F-001/technical-summary.md
confidence: 85
---
```

- `story`: the permanent id. `F<nnn>-<nn>` for a feature story (feature number, then order in the feature),
  `T-<nn>` for a technical story. Never renumbered, never reused. The file is `<id>-<slug>.md`.
- `feature: F-<nnn>`: as before. The FRD of that feature must be approved and clean.
- `requirements`: the FR ids the story delivers, as **one line** in square brackets: `[FR-F001-02]`. A merge has
  two ids. A split FR names its acceptance criteria numbers after `#`: `[FR-F001-04#1-4]`.
- `depends_on`: story ids that must be built first, one line, or `none`. The gate does not read it; Sloane's
  coverage script does (`STORY_ORDER`).
- `epic_summary`: the feature's `technical-summary.md` (see 5).

A technical story:

```yaml
---
story: T-01
feature: none
gate: technical
depends_on: none
confidence: 85
---
```

A feature story with no `requirements` (or `requirements: none`) is checked as before. Stories written before
ADR 0010 keep working.

Why frontmatter: BMAD already puts `confidence` there. One place, easy for a script.

**Known limits** (also in `docs/guides/ba-pm.md`):

- `requirements` and `depends_on` must be **one line**. A list over several lines reads as empty, so the story
  is checked as an old-style story. Sloane writes them in one line.
- There is no "withdrawn story" mark. A story whose FR was *Withdrawn* in the FRD blocks with
  `STORY_FR_UNKNOWN`. A person removes the story file and its line in `stories.md`. The id is never reused.
- A withdrawn FR is marked with the word in emphasis, `*Withdrawn*`, in its heading
  (`### 4.3. FR-F001-03 — List free slots *Withdrawn*`) or on the first non-blank line under it. Plain text
  "Withdrawn" is not read as the mark.
- A commit that only deletes a story file gets no coverage warning (`COMMIT_STORY_COVERAGE` does not see deletions).

### 2. Open questions

Put questions in a list under a `## Open questions` heading. Each item starts with an id.

```markdown
## Open questions

- Q-1: Can a user have two active plans at once?
- Q-2: Which roles can export the report?
```

A question is **resolved** when it has a `Resolved:` line with the answer, a name and a date:

```markdown
- Q-1: Can a user have two active plans at once?
  Resolved: No. One active plan per user. — Priya BA, 2026-11-20
```

Rules:

- Ids are `Q-1`, `Q-2`, `Q-3`, ... in order.
- **Never reuse, renumber or remove an id.** Keep the question and add the answer.
- The `Resolved:` line ends with `— <name>, <YYYY-MM-DD>`. You may type `--` instead of `—`.
- No open questions? Write `None.` under the heading (or leave the section out).

Why ids: they let the script see a deleted question (see 4). Why the answer stays under the question:
the story keeps its own record of who decided what, in git.

### 3. Assumptions

Write an assumption inline, where it matters, with an id:

```markdown
The export runs nightly [ASSUMPTION A-1: the data team accepts a 24-hour delay].
```

When someone confirms it, change the tag to `ASSUMPTION-RESOLVED` and add a name and a date:

```markdown
The export runs nightly [ASSUMPTION-RESOLVED A-1: the data team accepts a 24-hour delay — Ravi PO, 2026-11-21].
```

Rules:

- Ids are `A-1`, `A-2`, ... and follow the same rules as question ids.
- An assumption that turned out wrong: resolve it with the correct answer, then fix the text around it.
- A tag in any other form (for example `[ASSUMPTION: ...]` without an id) counts as open.

Why inline: an assumption belongs next to the sentence that depends on it. Why one tag: it is easy to
search for and hard to miss.

### 4. Deleted items

Deleting a question is not answering it. The gate finds deleted items in two ways:

- **Id gaps.** `Q-1` and `Q-3` are there, `Q-2` is not: `Q-2` was deleted, so it counts as open.
- **Baseline.** With `--baseline <git-ref>` (default in the skill: `HEAD`), every id in that git
  version of the story must still be in the file. This also catches deleting the last item or the
  whole section.

To bring a story back to "ready", restore the deleted item and resolve it.

### 5. Epic summary

Each epic has `epic-summary.md` (default place: `_bmad-output/planning-artifacts/epics/epic-<n>/epic-summary.md`)
with two parts, each with some text:

```markdown
## Functional
What the epic gives users, in business words.

## Technical
Main components, data and integrations.
```

There is no check for "link between screens" (team decision).

**Thin stories (ADR 0010).** There is no `epic-summary.md` and no epic layer. One PRD feature is one FRD, so
the feature is the epic. For each feature Sloane writes `stories/F-<nnn>/technical-summary.md`: only a
`## Technical` part, taken from the approved architecture. The story links it with `epic_summary:`. The
functional part is the FRD, so the check above already accepts it (a story with `feature`).

**Technical stories.** A story with `gate: technical` has no feature, so it has no FRD and no summary. The
approved architecture is its source instead. The gate asks Warden's record for the document type set in
`architecture_doc_type` (default `architecture`; also the CLI flag `--architecture-doc-type`) and runs
Sentry's check on it. The architecture must be approved and unchanged, or the story blocks with
`ARCH_UNAPPROVED` (the detail carries Sentry's word, for example `unapproved`, `stale` or `superseded`). A story that has
`gate: technical` and also names a feature or `requirements` blocks with `TECHNICAL_STORY_INVALID`: the marker
cannot be used to skip an FRD. The story's own questions, assumptions and confidence are still counted.

**Requirements.** For a story with `requirements`, the gate reads the feature's FRD and blocks when an id is
not in it, is *Withdrawn*, is not a well-formed id, or names an acceptance criterion number the FR does not
have (`STORY_FR_UNKNOWN`), or is an id of another feature (`STORY_FR_OTHER_FEATURE`). An FR that is in no
story is **not** a block here. A gap in one feature should not stop another story. Sloane's coverage check
(`STORY_FR_UNCOVERED`) and the commit warning (`COMMIT_STORY_COVERAGE`) report it.

**Feature FRD.** A story built from a PRD feature names it in its front matter: `feature: F-001`.
The gate then reads that feature's Functional Requirements Document
(`_bmad-output/planning-artifacts/frds/F-001/frd.md`, written by Fenn, `experion-frd`). The FRD is
the functional part, so the epic summary needs only `## Technical`. The gate blocks **every story of
the feature** until:

- the FRD exists and its front matter says `approval_status: approved` (only Warden writes it;
  `FRD_UNAPPROVED`, also when `feature` is not in the `F-<nnn>` format);
- its `## 12. Open Questions` list and its assumption tags have nothing open, every resolution has
  who and when, and no id is missing or used twice (`FRD_OPEN_ITEMS`).

A story without `feature` is not checked against an FRD. The markup in the FRD is the same as in a
story. A numbered heading (`## 12. Open Questions`) counts like `## Open questions`, in an FRD and in
a story. Figma links and ADO ids in an FRD are optional: "Not linked" is not a question. An FRD edited
after approval is caught by Sentry in `bmad-build` (doc_type `frd-F-001`) and by the pre-commit check.

## Worked example

A thin story from the clinic demo (fake data):
`_bmad-output/planning-artifacts/stories/F-001/F001-02-list-free-slots-through-the-api.md`.

```markdown
---
story: F001-02
feature: F-001
requirements: [FR-F001-02]
depends_on: [F001-01]
epic_summary: _bmad-output/planning-artifacts/stories/F-001/technical-summary.md
confidence: 85
confidence_label: High
confidence_rationale: One FR, one story. The architecture names the route style and the error shape.
---
# F001-02 List free slots through the API

Delivers FR-F001-02 of FRD-CLINIC-F001. Acceptance criteria: the FRD, Section 4.2.

## Technical notes

- Route style and the error shape: architecture, section "API conventions".

## Open questions

- None.
```

With the FRD of F-001 approved and nothing open in it, and the feature's `technical-summary.md` in place, the
gate allows this story. It has no acceptance criteria: `bmad-build` reads them from Section 4.2 of the FRD.

Now a story with problems (`F001-03`, also fake):

```markdown
---
story: F001-03
feature: F-001
requirements: [FR-F001-09, FR-F002-01]
depends_on: [F001-02]
epic_summary: _bmad-output/planning-artifacts/stories/F-001/technical-summary.md
confidence: 84
confidence_label: Medium
confidence_rationale: Two ids are not checked yet and one question is open.
---
# F001-03 Cancel a slot

Delivers FR-F001-09 and FR-F002-01. Acceptance criteria: the FRD, Section 4.

## Technical notes

- The route lives under `/api` [ASSUMPTION A-1: the architecture keeps this prefix for all routes].

## Open questions

- Q-1: Which error shape does the cancel route use?
  Resolved: The shared error shape. — Ravi PO, 2026-11-20
- Q-3: Does the route need its own rate limit?
```

The gate blocks it:

```json
{"result": "block", "reasons": [
  {"code": "OPEN_QUESTIONS", "detail": "1 open question(s): Q-3"},
  {"code": "OPEN_ASSUMPTIONS", "detail": "1 open assumption(s): A-1"},
  {"code": "DELETED_ITEMS", "detail": "ids missing (deleted items count as open): Q-2"},
  {"code": "STORY_FR_UNKNOWN", "detail": "FR-F001-09 is not in this FRD"},
  {"code": "STORY_FR_OTHER_FEATURE", "detail": "FR-F002-01 belongs to another feature, not F-001"}
], "counts": {...}}
```

To pass: restore and answer `Q-2`, answer `Q-3`, resolve `A-1`, each with a name and a date. Then fix the ids:
the unknown FR is a question for Sloane (a wrong id) or for Fenn (the FRD lacks the requirement); the id of
another feature belongs in a story of that feature.

A technical story (`T-01`) with the same gate but an unapproved architecture:

```json
{"result": "block", "reasons": [
  {"code": "ARCH_UNAPPROVED", "detail": "the architecture ('architecture') is not approved and unchanged: unapproved"}
], "counts": {...}, "gate": "technical"}
```

## All block reasons

| Code | Meaning |
|---|---|
| `OPEN_QUESTIONS` | A question has no `Resolved:` line, or a list item has no id |
| `OPEN_ASSUMPTIONS` | An `[ASSUMPTION ...]` tag is not resolved, or is not in the format |
| `RESOLUTION_MISSING_WHO_OR_WHEN` | A resolution has no `— <name>, <YYYY-MM-DD>` |
| `DELETED_ITEMS` | An id is missing (gap, or present in the baseline) |
| `DUPLICATE_IDS` | The same id is used twice |
| `LOW_CONFIDENCE` | `confidence` is below 80 |
| `MISSING_CONFIDENCE` | No `confidence`, or not a whole number from 0 to 100 |
| `MALFORMED_FRONTMATTER` | The frontmatter cannot be read (fails closed) |
| `MISSING_EPIC_SUMMARY` | No epic summary linked, or the file cannot be read |
| `EPIC_SUMMARY_INCOMPLETE` | The summary has no functional or no technical part with text (a feature story needs only the technical part) |
| `FRD_UNAPPROVED` | The story's feature has no FRD, the FRD is not approved, or `feature` is not in the `F-<nnn>` format |
| `FRD_OPEN_ITEMS` | The FRD has an open question or assumption, a resolution without who and when, a missing id or a duplicate id |
| `STORY_FR_UNKNOWN` | An id in `requirements` is not in the FRD, is *Withdrawn*, is not a well-formed id, or names an acceptance criterion number the FR does not have |
| `STORY_FR_OTHER_FEATURE` | An id in `requirements` belongs to another feature |
| `ARCH_UNAPPROVED` | A `gate: technical` story, and the architecture is not approved and unchanged (Sentry's check) |
| `TECHNICAL_STORY_INVALID` | A `gate: technical` story names a feature or has `requirements` |
| `UNREADABLE_STORY` | The story file cannot be read (exit code 2) |
| `BASELINE_UNAVAILABLE` | The baseline git ref cannot be used (exit code 2) |

## Questions for the reviewers

1. Is the `Resolved:` line under the question easy enough for BAs? (Alternative: a separate
   `## Resolutions` table. More structure, more typing.)
2. Is a name plus date enough for "who and when", or do we need a role too?
3. Is `HEAD` the right default baseline? It catches deletions not yet committed. A deletion that was
   already committed is caught only by an id gap, or with an older ref (for example the commit
   where the story was approved).
4. Where do epic summaries live in your projects? The default folder above is a proposal.
   **Answered by ADR 0010 (if accepted):** there are no epic summaries. Each feature has
   `stories/F-<nnn>/technical-summary.md`, written by Sloane from the approved architecture. The story links it
   with `epic_summary:`. Stories written before ADR 0010 can still use `epic:` and the folder above.
5. Who may mark a story `gate: generic`? ADR 0004 asks us to review its use in the pilot.
   **Answered by ADR 0010 (if accepted):** setup work from the architecture is `gate: technical`, written by
   Sloane, and the gate checks it (questions, assumptions, confidence and the approved architecture).
   `gate: generic` stays for real chores only. A person sets it, never the AI: file-tool edits of it are
   blocked, and the pre-commit hook warns when it changes (`COMMIT_GATE_GENERIC`). Its use is still to be
   reviewed in the pilot.
