---
name: slice-stories
description: Slice approved FRDs into thin, ordered stories (pointers to FR ids), write the technical stories and technical summaries from the approved architecture, and check coverage with a script
code: SS
---

# Slice FRDs into Thin Stories

## What Success Looks Like

For every approved FRD the user chose, `{feature-folder}` holds one thin story per slice: front matter and pointers, no functional text. Every non-withdrawn FR of the FRD is in exactly one story (a split FR by its top-level acceptance criteria numbers). The technical stories the approved architecture calls for are in `{agent.stories_dir}/technical`. Each feature has a `technical-summary.md` written from the architecture only. `{agent.stories_dir}/{agent.order_filename}` lists every story in build order. Every template placeholder is replaced and every italic guidance note is removed, including the template's "How to use this template" note. The coverage script prints `allow`, or the user has every reason it printed and knows which are not Sloane's to fix. The user knows the confidence scores and what happens next.

## Non-Negotiables

- Never write functional text in a story: no acceptance criteria, no restated rule, no screen label, no copied FRD line. Point to the FR id and the FRD section. If a sentence would explain what the system does, leave it out.
- Never write `approval_status`, `approved_by`, `approved_at`, a `Resolved:` line, an `[ASSUMPTION-RESOLVED …]` tag or `gate: generic`. The guards block these writes; do not try another way.
- Never raise a confidence score, remove a question or shorten a story's text to make a check pass. Fix the cause, or report it.
- Never renumber or reuse a story id. On an update keep every existing id, file and place in the order. The next new story takes the next free number: higher than any id in the folder, in `{agent.order_filename}` and in what the user told you was dropped.
- Never delete a story file or a line of `{agent.order_filename}` yourself. A story that has to go is named to the user, who decides.
- A functional question found while slicing (an FR that is unclear, contradicts another, or cannot be built as written) is never a story question and never a guess. Tell the user which FR and why, and that the FRD must be reopened with Fenn (`experion-frd`), reviewed, and approved again with Warden. Do not slice that FR until then.
- Never edit an FRD, the architecture, an FRD review or the approval record. Never put a story in a feature folder of another feature.
- Technical stories and technical summaries come from the approved architecture only. Never invent setup work the architecture does not name.
- No real personal, patient or customer data anywhere. Use made-up values if a note needs an example.

## Approach

### Logging convention for this flow

Log through `skill:agent-project-logger`. Every call passes `--stage "stories"`, `--agent "experion-story-slicer/SS"`, `--user "{user_name}"` and `--doc-path "<path>"`. Never put FRD text, story text, questions or answers in a log call: one short action line only. The team override logs the start and the end of the workflow and the confidence; this flow logs only what the override does not.

### 1. Check the approvals, find the sources and the templates

- **The architecture.** The team override's Sentry check approved it. The approved file is the one the approval record names under `doc_type='architecture'` (by default `{agent.architecture_dir}/<architecture folder>/{agent.architecture_filename}`). Read it in full. If the check did not pass, stop.
- **Each FRD.** For every feature the user chose, run `skill:agent-approval-gate-check with doc_type='frd-<feature>', doc_path=<{agent.frds_dir}/F-<nnn>/frd.md>, doc_label='FRD for <feature>', downstream_label='story slicing'`. `<feature>` is the FRD's front matter `feature`, exactly as written (for example `frd-F-001`). A feature whose FRD is not `ok` gets no stories in this run. Tell the user which feature and why (missing, not approved, edited since approval) and carry on with the others. If no feature is left, stop.
- **Templates.** If `{agent.project_template}` exists, use it for the story and tell the user so in one line; otherwise use `{agent.story_template}`. The technical summary uses `{agent.technical_summary_template}`.
- **Existing stories.** If `{agent.stories_dir}` already holds stories or an order file for these features, this is an **update**: read every story file and the order file first. Keep what exists (see "Updating" below).

### 2. Read each approved FRD

For each feature, run `python3 {skill-root}/../ready-for-dev-gate/scripts/frd_parse.py --frd <FRD path>` and read its JSON: the FR blocks (id, title, section, top-level acceptance criteria numbers, *Withdrawn*), the screens and the NFR rows. If its `result` is not `ok`, or `problems` is not empty, tell the user (the FRD has defects for Fenn to fix) and skip that feature. Then read the FRD file's Section 7 (dependent features), only to find the build order. Read to understand and to point; never to copy.

### 3. Plan the slices

For each feature make one plan, a small table: story id, FR ids, title, depends on, and a reason when the story is not 1 FR = 1 story.

- Default: **1 FR = 1 story**. A *Withdrawn* FR gets no story.
- **Merge** two FRs into one story only when they cannot be built or checked apart. **Split** one FR only when it is too large to build and review in one go: cut by its top-level acceptance criteria numbers (`FR-F001-04#1-4`, `FR-F001-04#5-7`); a sub-criterion such as 7.1 goes with its top-level number; every number lands in exactly one story. Each merge or split carries a one-line reason, and that line is written in the story.
- Story ids: `F<nnn>-<nn>`, the feature number without its hyphen and two digits in build order within the feature (`F-001` gives `F001-01`, `F001-02`). Technical stories: `T-<nn>`.
- Order: FR order in the FRD, adjusted by Section 7. A story depends on another (`depends_on`) only when it cannot work without it, for example a story that needs a shared record another story creates. A two-way link in Section 7 is not a dependency by itself. Depend on stories of another feature only when Section 7 says so and that feature has stories. If the order would go in a circle, break it with the FR order and tell the user.
- A story that depends on later work is an order error. Put the dependency earlier.
- The technical stories (step 4) are part of the same plan table, so the user sees the whole order at once. Read the architecture for them now.

Show the plan. If it holds a merge, a split or an order choice, ask the user once to confirm it before writing. If every feature is plain 1 FR = 1 story in FR order, go on and say so.

### 4. Technical stories, from the architecture

Write these first. Read the approved architecture for work that must exist before any feature: the project starter or skeleton, tooling, a data store, the pipeline, shared services it names. One story per piece, `{agent.stories_dir}/technical/T-<nn>-<slug>.md`, from the template with the technical changes its note describes (`story: T-<nn>`, `feature: none`, `gate: technical`, no `requirements`, no `epic_summary`). The body says what the architecture asks for and names the architecture section it comes from; the Technical notes give pointers into it, not a copy. Number from `T-01` in build order; later technical stories may depend on earlier ones. Each feature's first story depends on the technical stories it needs (at least the starter). If the architecture names no such work, write none and say so.

A question the architecture does not settle is a counted `Q-<n>` in that story's Open questions (raised by "Sloane"). Do not guess it.

### 5. Technical summaries

For each feature, write `{feature-folder}/{agent.technical_summary_filename}` from `{agent.technical_summary_template}`, using the approved architecture only: the stack and structure this feature builds in, its interfaces, its data, the architecture's rules that apply. A line the architecture does not support is left out; if nothing is feature-specific, say that and point to the architecture section. Keep the `## Technical` heading and no sub-headings under it. On an update, rewrite it only when the architecture changed.

### 6. Feature stories

For each planned story, write `{feature-folder}/<story>-<slug>.md` from the template. The slug is the title in lower case with hyphens, a few words.

- Front matter: `story`, `feature` (`F-<nnn>`), `requirements` (one line `[FR-F001-02]`; split `[FR-F001-04#1-4]`; merge `[FR-F001-01, FR-F001-02]`), `depends_on` (one line `[F001-01]`, or `none`), `epic_summary` (`_bmad-output/planning-artifacts/stories/F-<nnn>/technical-summary.md`), and the three confidence fields from step 8.
- Title: a short verb phrase from the FR's title, in your own few words.
- The delivery line names the FR id, the FRD id and the FRD section. For a merge or split add the one-line reason ("Merged: …" or "Split: …").
- Technical notes: only pointers to the approved architecture that this story needs; "None." when it needs none.
- Open questions: `- None.` unless a technical question is open (see step 4).
- Check each line before saving: if it states what the system does, remove it.

### 7. Write the order

Write `{agent.stories_dir}/{agent.order_filename}`, one line per story, technical stories first where others depend on them, every story after the stories it depends on:

```markdown
# Build order

Written by Sloane (`experion-story-slicer`). This list is the only place the build order lives.

- [T-01](technical/T-01-set-up-the-project.md) — technical
- [F001-01](F-001/F001-01-show-free-slots.md) — FR-F001-01
- [F001-02](F-001/F001-02-list-free-slots-through-the-api.md) — FR-F001-02
```

The link text is the story id exactly. The text after the dash names the FR ids (`FR-F001-04#1-4` for a split) or "technical". Keep the lines in this form: the coverage script and the gate read it.

### 8. Confidence

Self-assess each story as the Confidence Scoring facts describe (loaded by the team override), and the run as a whole. For a story, weigh: open technical questions, how much the architecture actually says for its technical notes, a merge, split or order that was your own judgement, and an FR whose cut you were unsure of. The overall score is the lowest story score; name that story in the rationale. Write `confidence`, `confidence_label` and `confidence_rationale` into each story's front matter. Never raise a score to pass a check.

### 9. Check coverage and fix

Run the script for each feature you sliced:

```
python3 {skill-root}/scripts/story_coverage.py --stories-dir "{agent.stories_dir}" --frds-dir "{agent.frds_dir}" --feature F-<nnn> --no-event
```

If this run sliced every approved FRD, run it once more without `--feature`, so the technical stories and the whole order are checked too. Parse the JSON (`result`, `reasons`, `counts`). If the command itself fails or does not print one JSON object, this is a block, not a pass: tell the user what happened and stop.

On `block`, fix what is yours and run again, at most three times:

- `STORY_FR_UNCOVERED`, `STORY_FR_TWICE`: fix the `requirements` lists (a missing FR, a duplicate, a split with a gap or an overlap).
- `STORY_FRD_TEXT`: delete the lines it names (it gives line numbers and sections, never text) and point to the FR instead.
- `STORY_ORDER`, `STORY_FILE_INVALID`: fix `depends_on`, the order file, the file name or folder, the front matter.
- `STORY_FR_UNKNOWN`, `STORY_FR_OTHER_FEATURE`: fix a wrong id. If the FR is *Withdrawn* or gone from the FRD, that is not yours: see "Updating".
- `STORY_FRD_UNREADABLE`: the FRD has defects. Not yours: Fenn fixes the FRD.

Never fix a block by editing the FRD, by weakening the check or by hiding a reason. When a block remains that you cannot fix, report every reason in plain words (code, story or feature, what to do and who does it) and stop: the stories are not ready, and you do not say they are. The team override runs the recorded check when the flow ends.

### 10. Report

Tell the user:

- the paths: the order file, and the number of stories per feature plus technical stories;
- the counts from the script: features, FRs covered, stories, technical stories;
- the features skipped and why;
- every merge and split with its reason, and the order choices you made;
- the confidence score of each story and the overall one with its rationale;
- technical open questions, if any, and that a named person records each answer;
- any functional question you found (the FR, the reason) and that the FRD must be reopened with Fenn;
- the next step: a BA reads `{agent.order_filename}` for order and grouping (not for text, that stays in the FRD), and each story goes through the ready-for-dev gate before `bmad-build` plans it.

## Updating

An update run keeps every existing story file, id and place in the order. Do the following and nothing else:

- **A new FR** in an approved FRD: a new story with the next free number, added at the end of the order (after its dependencies). If it must be built before an existing story, stop and tell the user: changing the order of existing stories is theirs to decide.
- **A new acceptance criterion** on a split FR: a new story for that number (`FR-F001-04#8`), appended.
- **A changed FR or criterion** needs no change in the story: it points to the FR. Update its pointer line only if the section number moved.
- **A withdrawn FR**: its story can no longer stand (the check blocks a withdrawn FR in a story). The coverage check has no "withdrawn story" mark yet. Name the story to the user and ask them to remove its file and its line in the order file. Keep the id out of use. Report this as an open point.
- **A feature whose FRD is no longer approved** (edited since, or reopened): leave its stories as they are and tell the user they wait for the FRD to be approved again before more stories are added.
- If the architecture changed, update the technical summaries and any technical story notes it touches, keeping ids.
