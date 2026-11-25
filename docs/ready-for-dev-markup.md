# Ready-for-dev markup

| | |
|---|---|
| Status | Agreed by Manesh G and the BA lead (2026-11-25) |
| Decide | Manesh G and one BA lead (Phase 1 plan, open question 5) |
| Related | ADR 0004 (ready-for-dev gate), Phase 1 task T7 |
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
- the epic has an `epic-summary.md` with a functional and a technical part.

Until this format is agreed, the gate is **advisory**: it reports a block, and the user decides.

## The format

### 1. Frontmatter

```yaml
---
confidence: 85
epic: 2
---
```

- `confidence`: a whole number from 0 to 100. Required.
- `epic: <n>` or `epic_summary: <path from the project root>`: tells the gate where the epic summary is.
- `gate: generic`: only for setup and chore stories. The gate skips the story (it still logs that it did).

Why frontmatter: BMAD already puts `confidence` there. One place, easy for a script.

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

## Worked example

`_bmad-output/implementation-artifacts/2-3-export-report.md` (fake data):

```markdown
---
title: Export the monthly report
confidence: 84
epic: 2
---

# Story 2.3: Export the monthly report

As a manager, I want to export the monthly report as CSV, so that I can share it.

## Acceptance criteria

1. The export button is on the report page.
2. The file has one row per team member
   [ASSUMPTION-RESOLVED A-1: inactive members are left out — Ravi PO, 2026-11-21].
3. The export runs nightly [ASSUMPTION A-2: the data team accepts a 24-hour delay].

## Open questions

- Q-1: Which roles can export?
  Resolved: Managers and admins. — Priya BA, 2026-11-20
- Q-3: Should the file name include the month?
```

With a complete epic 2 summary in place, the gate blocks this story:

```json
{"result": "block", "reasons": [
  {"code": "OPEN_QUESTIONS", "detail": "1 open question(s): Q-3"},
  {"code": "OPEN_ASSUMPTIONS", "detail": "1 open assumption(s): A-2"},
  {"code": "DELETED_ITEMS", "detail": "ids missing (deleted items count as open): Q-2"}
], "counts": {...}}
```

To pass: restore and answer `Q-2`, answer `Q-3`, resolve `A-2`, each with a name and a date.

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
| `EPIC_SUMMARY_INCOMPLETE` | The summary has no functional or no technical part with text |
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
5. Who may mark a story `gate: generic`? ADR 0004 asks us to review its use in the pilot.
