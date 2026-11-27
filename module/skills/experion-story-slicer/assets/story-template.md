---
story: <F001-02>
feature: <F-001>
requirements: [<FR-F001-02>]
depends_on: [<F001-01>]
epic_summary: _bmad-output/planning-artifacts/stories/<F-001>/technical-summary.md
confidence: <0-100>
confidence_label: <Low | Medium | High>
confidence_rationale: <1-2 sentences>
---
# <story id> <Story title, a verb phrase: what the story delivers>

Delivers <FR-F001-02> of <FRD-ABC-F001>. Acceptance criteria: the FRD, Section <4.2>.

## Technical notes

<Pointers to the approved architecture only: the decision, the section, the module or file it names. A few short bullets.>

## Open questions

- None.

## How to use this template

*Delete this whole section, with every italic line, before saving.*

- *A story is a pointer. The acceptance criteria, rules and screens stay in the FRD. Never copy or restate FRD text here: not a criterion, not a rule, not a screen label. Name the FR id and the Section 4 number only.*
- *Every list in the front matter is ONE line in square brackets, for example `[FR-F001-02, FR-F001-03]`. A list over several lines reads as empty. Write `depends_on: none` when the story waits for nothing.*
- *`story` is the permanent id. The file is `<story>-<slug>.md` in `stories/F-<nnn>/`. Never renumber or reuse an id.*
- *`requirements`: whole FRs (`FR-F001-02`). Merge: two or more ids in the list, and add one line under the title: "Merged: <reason>." Split: `FR-F001-04#1-4` (the top-level acceptance criteria numbers, as in the FRD), and add one line under the title: "Split: <reason>." Name the criteria in the FR line: "Acceptance criteria: the FRD, Section 4.4, criteria 1 to 4."*
- *`epic_summary` points to the feature's `technical-summary.md`. `feature` is the FRD's feature id exactly.*
- *Technical stories: `story: T-<nn>`, `feature: none`, add `gate: technical`, delete the `requirements` line and the `epic_summary` line, put the file in `stories/technical/`, and write "Delivers <what the architecture asks for>. Source: the approved architecture, <section>." in place of the FR line.*
- *`depends_on` names only stories that must be built first, and only earlier stories in `stories.md`.*
- *Technical notes: only what the approved architecture says. If it says nothing for this story, write "None." Never add a requirement or a business rule here.*
- *Open questions: technical questions only, as `- Q-<n>: <question> (raised by <name>)`. A functional question is not written here: the FRD is reopened instead. Keep `- None.` when there is none.*
- *Confidence: your own score for this story, as the Confidence Scoring facts describe. Never write `approval_status`, `approved_by` or `approved_at`, never write a `Resolved:` line, and never write `gate: generic`.*
