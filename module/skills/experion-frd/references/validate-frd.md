---
name: validate-frd
description: Check one epic's FRD against the template, its sources and the gate markup, and write a version-tagged review beside it for Warden
code: VF
---

# Validate an FRD

## What Success Looks Like

A review at `{epic-folder}/{agent.review_filename}` — one canonical file, not one per run — whose front matter carries the version tag Warden checks, `title` and `date`, followed by findings someone deciding whether to approve the FRD can read without you in the room. The FRD itself is not changed by this flow.

The version tag is the front matter field `frd-epic-<n>_version` (the FRD's doc_type plus `_version`), set to `target_version`: the FRD's current front matter `version` (0 if absent) plus one, the version the FRD will carry once approved.

## Non-Negotiables

Never edit the FRD, never touch its `approval_status`, and never word anything as approval or a recommendation to proceed, even for an FRD with no findings. Approval goes only through Warden. When Warden calls this flow to regenerate a stale review, do exactly this flow and return; do not ask the user anything.

## Approach

### Logging convention for this flow

Log through `skill:agent-project-logger` with `--stage "frd"`, `--agent "experion-frd/VF"`, `--user "{user_name}"`, `--doc-path "<FRD path>"` and `--doc-status "<FRD approval_status, read fresh>"`. Log `'FRD validation started'` (`--event FRD_REVIEW_STARTED`) before reading. Never put findings or FRD text in a log call.

### Checks

Read the whole FRD and this epic's part of `epics.md`. Check, in this order, and give each finding a severity: **critical** (a story built from this would be wrong or blocked), **moderate** (fix before approval), **minor** (a note).

1. **Structure.** All 14 sections, in order, with the template's headings. No `<placeholder>` text and no *italic* template guidance left. Front matter has `title`, `epic`, `date`, `version`, `approval_status` and the three confidence fields.
2. **Section 4 pattern.** Every `4.<n>` has a Screen line, numbered Acceptance Criteria and a Key Transactions table with a happy path, an alternate path and a validation failure. UI text is quoted. Formulas are in words. Variants are named.
3. **Section 5 tables.** Every screen has the six-column attribute table (a message-only modal may have none). Field Type and Data Type use the template's values. Limits and defaults are stated or counted as unknown.
4. **Cross-references.** The five checks of `references/create-frd.md` step 4: Section 4 → Section 5, Section 4 ↔ Section 13, Section 13 ↔ `epics.md`, every "To be confirmed" counted, ids without gaps or duplicates.
5. **Gate markup.** Section 12 items each start with `Q-<n>`; answered items have a `Resolved:` line ending `— <name>, <YYYY-MM-DD>`; assumption tags follow `[ASSUMPTION A-<n>: …]` or `[ASSUMPTION-RESOLVED A-<n>: … — <name>, <YYYY-MM-DD>]`. Report the open question and open assumption counts: each open item blocks every story in this epic at the ready-for-dev gate.
6. **Optional links.** Figma links and Jira IDs are optional. "Not linked" is never a finding. A link that is given but malformed (not a URL, not an id) is a minor finding.

A check with nothing wrong is stated as clean, not skipped.

### Write and commit

Compute `target_version` first. Write or overwrite `{agent.review_filename}` with front matter `frd-epic-<n>_version: {target_version}`, `title` and `date`, then a short summary (counts by severity, open items) and the findings by check. Commit only that file, leaving other staged work alone: `git add -- {agent.review_filename} && git commit -m "Review FRD epic <n> v{target_version}" -- {agent.review_filename}`, run from `{epic-folder}`. A failed commit does not undo the write: report the git error.

Log `'FRD validation completed - version {target_version}'` (`--event FRD_REVIEW_COMPLETED --doc-path "<review path>"`). Tell the user the path and the counts, and stop.
