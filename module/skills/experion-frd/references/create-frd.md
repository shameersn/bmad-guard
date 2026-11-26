---
name: create-frd
description: Write or update one epic's FRD in the Experion 14-section format from the approved PRD, UX, architecture and the epic's stories
code: CF
---

# Create or Update an FRD

## What Success Looks Like

`{epic-folder}/{agent.frd_filename}` exists for the epic the user chose. It follows `{agent.frd_template}` section for section (all 14 sections, in order), with every `<placeholder>` replaced and every *italic* guidance note removed, including the template's "How to use this template" section, its title line and its "Guarded BMAD rules" note. Every fact in it can be traced to a source or to the user. Every unknown is counted: a `Q-<n>` in Section 12 or an `[ASSUMPTION A-<n>: …]` tag. The cross-reference check below passes or its gaps are listed to the user. The FRD's front matter is complete and `approval_status` is `draft` (the team override may move it to `review` afterwards). The user knows the confidence score, the open question and assumption counts, and what happens next.

## Non-Negotiables

- Never write `approval_status: approved`, `approved_by`, `approved_at`, a `Resolved:` line or an `[ASSUMPTION-RESOLVED …]` tag. The guards block these writes; do not try another way.
- Never renumber, reuse or remove a `Q-<n>` or `A-<n>` id, also on an update. A question that is no longer relevant stays, and the user resolves it (for example "Resolved: No longer needed, story 3.4 was dropped. — <name>, <date>").
- Never edit the PRD, the UX design, the architecture, `epics.md` or any story. If a source looks wrong, say so to the user and add a question.
- Figma links and Jira IDs are optional. Use them when the sources or the user give them; otherwise write "Not linked". Never add a question or assumption only because a Figma link or Jira ID is missing.
- No real personal, patient or customer data in examples, sample values or UI text. Use made-up values.

## Approach

### Logging convention for this flow

Log through `skill:agent-project-logger`. Every call passes `--stage "frd"`, `--agent "experion-frd/CF"`, `--user "{user_name}"` and `--doc-path "<FRD path>"`. Never put FRD content, questions or answers in a log call: one short action line only. The team override logs the start and the end of the workflow; this flow logs only what the override does not.

### 1. Find the epic and the sources

- Ask which epic if the user did not say. Use the epic number `<n>` from `epics.md`; `{epic-folder}` is `{agent.epics_dir}/epic-<n>`.
- Load the sources from `{planning_artifacts}`: the PRD, the UX design (if any), the architecture and `epics.md`. Read only this epic's section of `epics.md` closely, and the other epics only for Sections 2 and 7.
- If `{epic-folder}/{agent.frd_filename}` exists, this is an **update**: read it fully first. Keep its ids, its version history and its open items.
- A missing PRD or `epics.md` is a stop: tell the user the FRD needs them. A missing UX design or architecture is not a stop: say so, and every screen or technical fact that would have come from it becomes a question.

### 2. Draft section by section

Work in the template's order. After each group of sections, tell the user in one line where you are (for example "Sections 1-3 drafted; starting Section 4, 6 capabilities").

- **Section 1-3.** From the PRD (vision, users, journeys) and `epics.md` (the epic's goal). Section 3 is a Mermaid flowchart: one subgraph per role or system, the entry points from 2.1, each decision, the hand-offs from 2.2.
- **Section 4.** One `4.<n>` block per capability the epic's stories deliver, named as a verb phrase. Acceptance criteria come from the stories' acceptance criteria and the PRD's functional requirements, restated in the template's pattern. Exact UI text in quotes. Every formula in words. Name each variant (role, record type, status).
- **Section 5.** One `5.<n>` per screen, panel, modal or grid from the UX design. Every screen named in Section 4 has a Section 5 entry. A field whose type, limit or default is not in the sources gets an `[ASSUMPTION A-<n>: …]` or a question.
- **Section 6.** Figma table if links exist; otherwise the single line "Not linked".
- **Section 7-11.** From the PRD, the architecture and the other epics. Non-functional targets and data volumes that no source gives are "To be confirmed" with a question.
- **Section 12.** `## 12. Open Questions` with one `- Q-<n>: <question> (raised by <name>)` item per question, numbered from the highest existing id plus one. "raised by" is the user's name when they raised it, otherwise "Fenn". Write "- None." only if there is truly nothing open.
- **Section 13.** One row per Section 4 capability, split by variant where behaviour differs. The Story column is the story id from `epics.md`. Jira ID as given, else "Not linked".
- **Section 14.** Links as given, else "Not linked". Glossary from the PRD's glossary where one exists.

When the sources disagree, do not pick one silently: ask the user, or add a question that names both sources.

### 3. Front matter

Set or keep: `title` (`FRD - <Epic ID> - <Feature Name>`), `epic` (`<n>`), `date` (today), `version` (keep the existing value; 0 for a new FRD — Warden raises it on approval), `approval_status` (`draft` for a new FRD), and the three confidence fields from step 5.

**Update of an approved FRD:** when the FRD's content changes in this conversation and its `approval_status` is `approved`, set it back to `draft` in the same save, and log `'FRD updated via chat: <one-line summary, no content>'` (`--event FRD_UPDATED`). One log per change, not batched. Re-approval goes through Warden.

### 4. Cross-reference check

Before saving the final draft, check and fix what you can:

1. Every `4.<n>` names a Section 5 screen that exists (or says why it has none, for example a background job).
2. Every `4.<n>` has at least one Section 13 row, and every Section 13 row maps to a `4.<n>` that exists.
3. Every Section 13 Story id exists in this epic in `epics.md`, and every story of this epic in `epics.md` appears in Section 13.
4. Every "To be confirmed" in the document, other than Figma and Jira, has a `Q-<n>` or an `[ASSUMPTION A-<n>: …]` next to it or in Section 12.
5. Question ids run from `Q-1` with no gaps; assumption ids run from `A-1` with no gaps; no id is used twice.

List any gap you could not fix to the user as a short table (check, where, what is missing). Do not hide a gap by deleting the reference.

### 5. Confidence

Self-assess as the Confidence Scoring facts describe (loaded by the team override). For an FRD, weigh: open questions and assumptions, sections drafted without a source (no UX design, no architecture), cross-reference gaps from step 4, and source conflicts you had to raise. Write `confidence`, `confidence_label` and `confidence_rationale` into the front matter.

### 6. Save and report

Save `{epic-folder}/{agent.frd_filename}` (create the folder if needed). Tell the user:

- the path, the confidence score and its rationale;
- the counts: capabilities, screens, stories mapped, open questions, open assumptions;
- that no story in this epic can go to development while the FRD has open questions or assumptions or is not approved, and that a named person records each answer as a `Resolved:` line (format in `docs/ready-for-dev-markup.md`);
- the next step: validate the FRD (`references/validate-frd.md`), then approve it with Warden.
