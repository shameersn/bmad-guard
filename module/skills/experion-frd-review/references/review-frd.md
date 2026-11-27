---
name: review-frd
description: Review one FRD against its PRD feature and the Experion FRD template, and write a version-tagged review beside it that Warden checks
code: RF
---

# Review an FRD

## What Success Looks Like

A review at `{feature-folder}/{agent.review_filename}` — one canonical file, not one per run; history lives in git — whose front matter carries the version tag Warden checks, `title` and `date`, followed by findings someone deciding whether to approve the FRD can read without you in the room. The FRD itself is not changed by this flow.

The version tag is the front matter field `frd-<feature>_version` (the FRD's doc_type plus `_version`; `<feature>` is the FRD's front matter `feature`, e.g. `frd-F-001_version`), set to `target_version`: the FRD's current front matter `version` (0 if absent) plus one, the version the FRD will carry once approved.

## Non-Negotiables

Never edit the FRD, never touch its `approval_status`, and never word anything as approval or a recommendation to proceed, even for an FRD with no findings. Approval goes only through Warden. When Warden calls this flow to regenerate a stale review, do exactly this flow and return; do not ask the user anything. Never put real personal, patient or customer data in the review.

## Approach

### Logging convention for this flow

Log through `skill:agent-project-logger` with `--stage "frd"`, `--agent "experion-frd-review/RF"`, `--user "{user_name}"`, `--doc-path "<FRD path>"` and `--doc-status "<FRD approval_status, read fresh>"`. Log `'FRD review started'` (`--event FRD_REVIEW_STARTED`) before reading. Never put findings or FRD text in a log call.

### Read first

Read the whole FRD, then the PRD in `{planning_artifacts}` (the feature with the FRD's `feature` id, the PRD's NFRs, and its front matter `version`). If `{agent.review_filename}` already exists, read it too: it holds the last round's findings and any disputes.

### Checks

Check, in this order, and give each finding a severity: **critical** (a story built from this would be wrong or blocked), **moderate** (fix before approval), **minor** (a note). A check with nothing wrong is stated as clean, not skipped.

1. **Structure.** All 13 sections, in order, with the template's headings. No `<placeholder>` text and no *italic* template guidance left. Front matter has `title`, `frd_id`, `feature`, `prd_version`, `date`, `version`, `approval_status` and the three confidence fields. The header table names the source PRD and its version.
2. **Traceability to the PRD feature.** The `feature` id exists in the PRD. `prd_version` matches the PRD's current `version` (a lower value is a moderate finding: the PRD changed after the FRD was written). Every functional requirement the PRD gives this feature is covered by an `FR-<Fnnn>-<nn>`. No requirement adds scope the feature does not have.
3. **Requirements (Section 4).** Every block has a unique, well-formed `FR` id (withdrawn ones marked *Withdrawn*, not deleted), a Screen line, numbered acceptance criteria that a tester could check, and a Key Transactions table with a happy path, an alternate path and a validation failure. UI text in quotes. Formulas in words. Variants named.
4. **Screens (Section 5).** Every screen named in Section 4 has a Section 5 entry with the six-column attribute table (a message-only modal may have none). Field Type and Data Type use the template's values. Limits and defaults are stated or counted as unknown.
5. **NFRs (Section 8).** Every PRD NFR whose "Applies to" is "All" or this feature is in 8.1, with the PRD's target unchanged. Each 8.2 `FNFR` is specific to this feature and measurable.
6. **Unknowns and gate markup.** Every "To be confirmed" (other than Figma and ADO) has a `Q-<n>` or an `[ASSUMPTION A-<n>: …]`. Section 12 items each start with `Q-<n>`; answered items have a `Resolved:` line ending `— <name>, <YYYY-MM-DD>`; assumption tags are in the agreed format. Report the open question and open assumption counts: each open item blocks every story of this feature at the ready-for-dev gate.
7. **Optional links.** Figma links and ADO ids are optional. "Not linked" is never a finding. A link that is given but malformed is a minor finding.

### Finding ids and disputes

- Each finding has an id `R-<n>`. On a re-review, a finding that is still there keeps its id and gets status **Open**; one that is fixed gets status **Fixed** (keep it in the file for this round, then drop it in the next); a new finding takes the next free number. Never reuse an id.
- The BA may answer a finding in this file with a line under it: `Disputed: <reason> — <name>, <YYYY-MM-DD>`. Keep every such line word for word when you rewrite the file. A disputed finding that is still there gets status **Disputed** and goes into the section **For the Product Owner**: the finding, the BA's reason, and one sentence on why you still hold it. Do not argue it in chat. The PO decides (workflow step 17).
- You never write a `Disputed:` line yourself.

### Write and commit

Compute `target_version` first. Write or overwrite `{agent.review_filename}` with front matter `frd-<feature>_version: {target_version}`, `title` and `date`; then a short summary (counts by severity and status, open items, disputed count), the **For the Product Owner** section (or "No disputed findings."), and the findings by check. Commit only that file, leaving other staged work alone: `git add -- {agent.review_filename} && git commit -m "Review FRD <feature> v{target_version}" -- {agent.review_filename}`, run from `{feature-folder}`. A failed commit does not undo the write: report the git error.

Log `'FRD review completed - version {target_version}'` (`--event FRD_REVIEW_COMPLETED --doc-path "<review path>"`). Tell the user the path, the counts and the disputed findings, and stop.
