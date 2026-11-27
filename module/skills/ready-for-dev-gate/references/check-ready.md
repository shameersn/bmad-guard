---
name: check-ready
description: Count open questions, open assumptions, unrecorded resolutions, deleted items, confidence and the epic summary, and allow or block a story's move to development
code: READY
added: 2026-11-25
updated: 2026-11-26
type: script-backed
---

# Check Ready

## What Success Looks Like

A story moves to planning or coding only when the script says `allow`. On `block`, the user sees every reason with its ids, and in `{agent.mode}` = `enforce` (the default) the work stops; in `advisory` mode the user decides knowingly.

## The markup (short version)

- Frontmatter: `confidence: <0-100>`; optional `gate: generic` for chore stories (skips the gate; only a person sets it); `gate: technical` for setup work from the architecture (not skipped, see below); `epic_summary: <path from project root>` or `epic: <n>`; `feature: F-<nnn>` for a story built from a PRD feature's FRD; `requirements: [FR-F001-02]` for the FR ids the story delivers (a thin story, written by Sloane); `story`, `depends_on` (the gate does not read them).
- Open questions: list items under `## Open questions`, each starting with an id `Q-1`, `Q-2`, ... Resolved when the item has a line `Resolved: <answer> — <name>, <YYYY-MM-DD>`.
- Assumptions: inline `[ASSUMPTION A-1: ...]`; resolved as `[ASSUMPTION-RESOLVED A-1: ... — <name>, <YYYY-MM-DD>]`.
- Feature FRD: when the story has `feature: F-<nnn>`, the FRD at `{agent.frds_dir}/F-<nnn>/frd.md` is the functional part. It must exist and have `approval_status: approved` (`FRD_UNAPPROVED`, also for a missing FRD or a `feature` not in the `F-<nnn>` format) and no open question, open assumption, unrecorded resolution, id gap or duplicate id (`FRD_OPEN_ITEMS`). The epic summary then needs only its technical part.
- Thin story: `requirements` is one line, `[FR-F001-02]`, or `[FR-F001-04#1-4]` for acceptance criteria 1 to 4. Every id must be in the feature's FRD, not *Withdrawn*, and name only criterion numbers the FR has (`STORY_FR_UNKNOWN`), and must be of the story's own feature (`STORY_FR_OTHER_FEATURE`). A feature story with no `requirements` is not blocked for that. An FR that is in no story never blocks here: Sloane's coverage check and the commit warning report it. The acceptance criteria are not in the story: `bmad-build` reads them from the FRD by FR id.
- Technical story: `gate: technical` with `feature: none` and no `requirements`. It has no epic summary and no FRD. The approved, unchanged architecture (`{agent.architecture_doc_type}` in Warden's record, Sentry's check) takes their place, or `ARCH_UNAPPROVED`. A technical story that names a feature or requirements is `TECHNICAL_STORY_INVALID`. Its own questions, assumptions and confidence are still counted.
- Ids are never reused or removed. A missing id (a gap, or an id present in the baseline version) counts as open.
- `--` may be typed instead of `—`.

## Your Approach

### Logging

The script records the decision as a guard event itself (guard `ready-for-dev`, `READY_ALLOW` or `READY_BLOCK`), with counts and reason codes and no document text. You do not call Ledger for the result.

### Run the check

```
python3 {skill-root}/scripts/ready_for_dev.py "<story_path>" \
  --min-confidence {agent.min_confidence} \
  --epics-dir "{agent.epics_dir}" \
  --frds-dir "{agent.frds_dir}" \
  --architecture-doc-type "{agent.architecture_doc_type}" \
  --baseline "{agent.baseline}"
```

Add `--epic-summary "<epic_summary>"` when the caller gave one. Leave out `--baseline` when `{agent.baseline}` is empty. Run `--help` for the full argument list. Parse the single JSON object it prints.

### If the command itself fails

If the output is not one JSON object with a `result` field (a traceback, `python3: command not found`, empty output), this is NOT a pass. Treat it as a block: tell the user the ready-for-dev gate could not run (say what happened). Log it via `skill:agent-project-logger` with action `'Ready-for-dev gate could not run'`, `--stage "ready-for-dev"`, `--agent "ready-for-dev-gate/READY"`, `--event "READY_ERROR"`, `--verdict block`. Then follow "Handle a block" below.

### Handle the result

- **`allow`** — tell the user in one line that the story passed (confidence and counts; or "generic story, gate skipped" when `gate` is `generic`, or "technical story, architecture approved" when `gate` is `technical`). Let the caller continue.
- **`block`** — list every reason as `CODE: detail`. Then handle the block.

Reason codes: `OPEN_QUESTIONS`, `OPEN_ASSUMPTIONS`, `RESOLUTION_MISSING_WHO_OR_WHEN`, `DELETED_ITEMS`, `DUPLICATE_IDS`, `LOW_CONFIDENCE`, `MISSING_CONFIDENCE`, `MALFORMED_FRONTMATTER`, `MISSING_EPIC_SUMMARY`, `EPIC_SUMMARY_INCOMPLETE`, `FRD_UNAPPROVED`, `FRD_OPEN_ITEMS`, `STORY_FR_UNKNOWN`, `STORY_FR_OTHER_FEATURE`, `ARCH_UNAPPROVED`, `TECHNICAL_STORY_INVALID`, and (exit code 2) `UNREADABLE_STORY`, `BASELINE_UNAVAILABLE`.

### Handle a block

- **`{agent.mode}` = `enforce`** — stop. Tell the user what must change in the story (answers recorded by a named person with a date, deleted ids restored, confidence re-scored). Do not offer to proceed anyway.
- **`{agent.mode}` = `advisory`** (a project's own choice; the write gate hook still blocks source writes) — after listing the reasons, ask the user whether to continue despite the block. Continue only on an explicit yes. Log the answer via `skill:agent-project-logger` with action `'Ready-for-dev block overridden by user'` or `'Ready-for-dev block accepted'`, `--stage "ready-for-dev"`, `--agent "ready-for-dev-gate/READY"`, `--event "READY_OVERRIDDEN"` or `"READY_STOPPED"`, `--verdict allow` or `block`, and `--doc-path "<story_path>"`. Never put story text in the log.

### Never

- Never write a `Resolved:` line, an `[ASSUMPTION-RESOLVED ...]` tag, or a `gate: generic` marker, and never raise a confidence value, to make the gate pass. Only the people named in a resolution record it.
- Never remove or renumber a question or an assumption.
