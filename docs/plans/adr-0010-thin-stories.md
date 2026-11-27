# Plan: thin stories from the FRD (ADR 0010)

> **Approved by Shameer on 2026-11-27, with the suggested defaults for all five open questions.** ADR 0010 is
> still Proposed; the team decides in the PR. Nothing here changes the guards until the PR is approved.

| | |
|---|---|
| Ticket | GUARD-3 |
| Branch | `feature/GUARD-3-adr-frd-thin-stories` |
| Time box | about 5 days (Phase 1 and Phase 3 of ADR 0010) |
| Approvers | Shameer, Manesh G (PR approval) |
| Inputs | ADR 0010, ADR 0004, ADR 0001; `docs/guides/ba-pm.md` steps 14–19; the clinic-demo pilot run |
| Out of scope | ADR 0010 Phase 2 (sync to ADO/Jira). It waits for the Phase 4 MCP spike. |

## Goal

The FRD's acceptance criteria are written once. At the end of this plan:

1. A new guard skill writes **thin stories** from approved FRDs: front matter and pointers, no functional text.
2. A script proves it: every FR id is covered, no FR id is outside its feature, no FRD Section 4 text is copied.
3. `bmad-build` reads the acceptance criteria from the FRD by FR id, not from the story.
4. Setup work gets `gate: technical` (checked against the approved architecture) instead of `gate: generic`.
5. Every guide, demo step and doc in this repo tells the same story.

## Design

### Story file

```markdown
---
story: F001-02
feature: F-001
requirements: [FR-F001-02]
depends_on: [F001-01]
epic_summary: _bmad-output/planning-artifacts/stories/F-001/technical-summary.md
confidence: 85
confidence_label: High
confidence_rationale: <1-2 sentences>
---
# F001-02 List free slots through the API

Delivers FR-F001-02 of FRD-CLINIC-F001. Acceptance criteria: the FRD, Section 4.2.

## Technical notes
<from the approved architecture only>

## Open questions
- None.
```

- **Place:** `_bmad-output/planning-artifacts/stories/F-<nnn>/<story>-<slug>.md`. Technical stories in
  `stories/technical/T-<nn>-<slug>.md`.
- **Story id:** `F<nnn>-<nn>` (feature and order), `T-<nn>` for technical. The id matches the write gate's
  existing rule (`<id>.md` or `<id>-*.md`), so the write gate needs no change to find it.
- **Build order:** `stories/stories.md`, one ordered list the slicer writes (`- [F001-01](F-001/…) — FR-F001-01`).
  It is the only place the order lives.
- **Epic = feature.** The technical part of the old epic summary moves to
  `stories/F-<nnn>/technical-summary.md` (`## Technical` only). The story links it with `epic_summary:`. The gate
  already accepts a Technical-only summary when the story has a `feature`, so **no gate change** for this.
- **Split FR:** `requirements: [FR-F001-04#1-4]` (AC numbers after `#`). Merge: two ids in the list.

### Rules the script checks (`story_coverage.py`)

| Code | Blocks when | Where it runs |
|---|---|---|
| `STORY_FR_UNKNOWN` | a `requirements` id is not in the FRD, is *Withdrawn*, or names AC numbers the FR does not have | gate (blocks), slicer end |
| `STORY_FR_OTHER_FEATURE` | an FR id of another feature | gate (blocks), slicer end |
| `STORY_FR_UNCOVERED` | an approved FRD has a non-withdrawn FR (or AC number) in no story | slicer end, pre-commit (warn) |
| `STORY_FR_TWICE` | the same FR or AC number in two stories | slicer end, pre-commit (warn) |
| `STORY_FRD_TEXT` | a story line matches an FRD Section 4 or 5 line (normalised, 8+ words) | slicer end (blocks), pre-commit (warn) |
| `STORY_ORDER` | `depends_on` names a later story, a missing story, or a cycle | slicer end |

One shared parser, `frd_parse.py` (FR blocks, ids, AC numbers, *Withdrawn*, Sections 4, 5, 8.1), lives in the
ready-for-dev-gate skill next to `ready_for_dev.py`, so the gate, the slicer and the build use the same reading.

### Technical stories (`gate: technical`)

- The story has `feature: none` and `gate: technical`. The gate does **not** skip it: it still counts its own
  questions, assumptions and confidence, and it checks that the architecture is approved and unchanged
  (Sentry logic via `approval_core.py`, doc_type `architecture`). New reason `ARCH_UNAPPROVED`.
- `gate: generic` stays for real chores, as today. The pre-commit warning `COMMIT_GATE_GENERIC` is unchanged.

### What the stock story skill does now

`bmad-create-epics-and-stories` stays installed (stock BMAD, ADR 0001). Its team override gets one prepend
step: in a guarded project, say that stories come from the slicer, name it, and stop. No other behaviour is
kept.

## Tasks

Small commits, one per task. Tests and lint (`ruff`) pass after each.

### T1. Shared FRD parser (0.5 day)
- `module/skills/ready-for-dev-gate/scripts/frd_parse.py`: FR blocks with AC numbers, *Withdrawn*, screen
  links, Section 8.1 rows.
- Refactor `ready_for_dev.py` `check_frd` to use it (behaviour unchanged).
- Tests: `tests/test_frd_parse.py` (the clinic F-001 shape, withdrawn FR, sub-criteria `7.1`, missing
  section, duplicate id).

### T2. Coverage script (1 day)
- `module/skills/experion-story-slicer/scripts/story_coverage.py`: the six codes above; JSON out like
  `ready_for_dev.py`; logs one guard event (`STORIES_CHECKED`) with counts and codes, no text.
- Add the event name in `agent-project-logger/scripts/guard_events.py` if it keeps a list.
- Tests: `tests/test_story_coverage.py`: full cover, gap, twice, other feature, withdrawn, split by AC, copied
  line, short shared phrase not flagged, order cycle.

### T3. Slicer skill (1 day)
- `module/skills/experion-story-slicer/`: `SKILL.md` (persona, see open question 1), `customize.toml`
  (`frds_dir`, `stories_dir`, `architecture` path), `references/slice-stories.md`, `assets/story-template.md`,
  `assets/technical-summary-template.md`.
- Flow: Sentry on the architecture and on each FRD → default 1 FR = 1 story, merge or split only with a
  one-line reason → technical stories from the architecture → `stories.md` order → confidence → run
  `story_coverage.py` → stop on a block.
- A new functional question during slicing: tell the user to reopen the FRD (Warden). Never a story `Q-n`.
- Override `module/overrides/experion-story-slicer.toml`: Sentry prepend, logger start and end, confidence
  event, coverage run in `on_complete`.
- Tests: `tests/test_story_slicer_skill.py` (like `test_frd_skill.py`); add the skill to
  `ON_COMPLETE_LIST_OK` in `tests/test_overrides.py`; `test_skills.py` picks it up by itself.

### T4. Gate and write gate (1 day)
- `ready_for_dev.py`: for a story with `requirements`, run `STORY_FR_UNKNOWN` and `STORY_FR_OTHER_FEATURE`
  through `frd_parse.py`; `gate: technical` with `ARCH_UNAPPROVED`. New `GATE_READY_*` names follow by
  themselves in `write_gate.py`; add the constants it imports.
- `ready-for-dev-gate/customize.toml`: add `architecture_doc_type = "architecture"`.
- `guard_pre_commit.py`: warn `COMMIT_STORY_COVERAGE` (uncovered, twice, copied text) for staged story files.
- Tests: extend `test_ready_for_dev.py`, `test_write_gate.py` (story id `F001-02`, `T-01`), `test_pre_commit.py`.
- `scripts/red_team.py`: one case, "story with an unknown FR id gets to code" (expect `GATE_READY_STORY_FR_UNKNOWN`).

### T5. Build and story overrides (0.5 day)
- `module/overrides/bmad-build.toml`, the Feature FRD fact: when the story has `requirements`, run
  `frd_parse.py --frd <path> --ids <ids>` and use its output (Section 4 blocks, their Section 5 screens, the
  Section 8.1 rows) as the acceptance criteria; the plan's Scope Summary names the FR ids. Plan confidence
  counts the FRD as confirmed input.
- `module/overrides/bmad-create-epics-and-stories.toml`: replace the facts with the redirect-and-stop step.
- `module/overrides/bmad-code-review.toml`: the reviewer checks the code against the FRD blocks of the
  story's FR ids (same `frd_parse.py` call).
- Tests: `tests/test_overrides.py` still passes (structure); add a check that the build override names
  `frd_parse.py`.

### T6. Documents, demo and articles in this repo (1 day)

| File | Change |
|---|---|
| `docs/adr/0010-frd-source-thin-stories.md` | Link this plan under Related. Status stays Proposed until the PR. |
| `docs/adr/0004-ready-for-dev-gate.md` | Status line: "epic summary and generic marker changed by 0010 (if accepted)". |
| `docs/adr/README.md` | 0004 row: same note. |
| `docs/architecture.md` | Mermaid flow (line ~84): "Epics and stories" → "Thin stories (slicer), FR ids"; text at line ~111; skill table: add the slicer, update the gate row. |
| `docs/ready-for-dev-markup.md` | New front matter (`story`, `requirements`, `depends_on`, `epic_summary` to the technical summary, `gate: technical`); replace the "Story 2.3" example with a thin story; reasons table (5 new codes); open questions 4 and 5 answered. |
| `docs/guides/ba-pm.md` | Step 18 row; "What the flow looks like" step 5; FRD section "stories come later" line; new short section "Thin stories" (what a BA reviews: order and grouping, not text); "You must not": copy FRD text into a story. |
| `docs/guides/developer.md` | Line ~46 (ready rules); new line: the plan reads acceptance criteria from the FRD; new `GATE_READY_*` codes in the table. |
| `docs/guides/reviewer.md` | Review against the FRD blocks of the story's FR ids. |
| `docs/guides/install.md` | Overrides list (line ~284) and the remove command (line ~418): add `experion-story-slicer`; skills list. |
| `docs/guides/trial-run.md` | Fixture story 1.1 (line ~93) → thin story; steps 14g–14i → slicer; step 15/15b paths; new 14j (copied text blocked) and 15d (`gate: technical`); results table rows; known issue at line ~429. |
| `docs/guides/demo-run.md` | Timeline row 14–19 and the Stories row (line ~133): slicer instead of `bmad-create-epics-and-stories`; tag `demo/4-stories` keeps its name. |
| `demo/prompts.md` | 8b: `cat _bmad-output/planning-artifacts/stories/stories.md` and one story file, instead of `grep Feature: epics.md`; 9, 11a, 13: story ids `F001-01`, `F002-01`. |
| `demo/make-live-copy.sh`, `demo/prepare.sh` | Check only: no epic paths today. Change only if the rehearsal shows one. |
| `module/skills/README.md`, `module/overrides/README.md` | Add the slicer and its override. |
| `module/skills/experion-frd/SKILL.md`, `references/create-frd.md`, `assets/frd-template.md` | "The stories … come later" and the next-step line name the slicer; template note "Step 14/18". |
| `module/skills/ready-for-dev-gate/SKILL.md`, `references/check-ready.md` | Markup short version and reason codes. |
| `module/skills/tool-guard/references/guard-rules.md` | `GATE_READY_*` list; story id format; `COMMIT_STORY_COVERAGE`. |
| `module/hooks/README.md` | New pre-commit warning. |
| `docs/plans/phase-3.md` | One line under open items: ADR 0010 Phase 2 (ticket sync) moves to Phase 4 with the MCP spike. |
| Reports in `docs/reports/` | No change: they record past runs. |

### T7. Check the whole flow (0.5 day)
- `python3 -m pytest` and `ruff check .`; show the result.
- `scripts/install_test.py` in a temp folder: the slicer is installed in `.claude/skills` and `.agents/skills`.
- Dry run of trial-run steps 14–15 on a copy of clinic-demo (fake data only): F-001 (2 FRs) gives 2 stories,
  0 copied lines, gate allow after the FRD is approved with nothing open.
- Update clinic-demo and experion-bmad by re-running `setup_guard.py` **after** the PR is merged, not in this
  branch. Re-tag `demo/4-stories` in the demo project at the next rehearsal.

## Done when

- All new codes have tests that fail before and pass after.
- The trial-run dry run shows: no story holds FRD text; coverage 100%; `bmad-build` plan cites FR ids and
  Section 4 numbers.
- Every file in the T6 table is updated or marked "check only, no change".
- PR description lists the files and the pilot measures from ADR 0010.

## Open questions (defaults approved 2026-11-27)

1. **Skill and persona name.** Default: `experion-story-slicer`, persona "Sloane" (to match Fenn and Iris).
2. **Story id.** Default: `F001-02` and `T-01`. Alternative: keep BMAD's `1.2`, but it does not show the feature.
3. **Where the technical summary lives.** Default: `stories/F-<nnn>/technical-summary.md`, written by the
   slicer from the architecture. Alternative: drop it and let the approved architecture be the technical part
   (needs a gate change).
4. **Copied-text threshold.** Default: a story line of 8+ words that matches an FRD Section 4/5 line after
   normalising case and spaces. Tune after the dry run.
5. **Should `STORY_FR_UNCOVERED` block at the gate?** Default: no. It blocks at the slicer and warns at commit;
   a gap in one feature should not stop another story.
