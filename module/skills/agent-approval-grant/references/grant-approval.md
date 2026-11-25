---
name: grant-approval
description: Double-confirm with the user, then mark a document approved and record it in the shared approval-state.json
code: GRANT
added: 2026-07-20
updated: 2026-11-25
type: script-backed
---

# Grant Approval

## What Success Looks Like

The document's frontmatter status is `{agent.approved_value}`, its frontmatter `version` has been bumped by one, and the shared state file has a fresh `{doc_type}` entry with a content hash of the saved document, who approved it and when — but only after the user gave two separate, explicit affirmatives. Any earlier approval of the same `{doc_type}` stays in the entry's history. A decline at either point leaves the document untouched, in draft, and logged. The approved document and the state file are committed together under the new version number, though a failed commit does not undo the approval itself. If a companion review skill is wired up for this `doc_type` and its review artifact is missing or predates the document's current content, that review is regenerated, targeted at the version about to be approved, before anything is asked of the user.

## Your Approach

### Build integration mode

When `bmad-build` invokes this capability, obtain the same two separate explicit confirmations required for every other document. Build plans have no companion review artifact: skip the pre-flight commit and review-currency sections, then follow both confirmation sections below. Grant with `--status-field approval_status --create-status-field --hash-mode build --no-commit`, plus `--approved-by "{user_name}"` and the passed `--requested-at`. Preserve the Build phase `status`, and return `approved_at` plus `review_duration_seconds` for lifecycle logging. A declined or deferred confirmation leaves `approval_status` pending and returns control without granting approval.

### Resolve the user

Resolve `{user_name}` by reading, in order, `{project-root}/_bmad/config.user.yaml`, then `{project-root}/_bmad/bmm/config.yaml`, then `{project-root}/_bmad/config.yaml` — use the first `user_name` value found. Do this even if `{user_name}` already appears to be set from an earlier activation step, since this skill is often called from inside another skill's flow. Never substitute git identity, an email address or any other ambient signal; if no config file defines `user_name`, use `unknown`. (The script separately records git's `user.name` as `git_user`.)

### Logging

The grant script records the approval itself as a guard event (guard `warden`, reason `APPROVED`). Log the human steps of this flow through `skill:agent-project-logger`, each with `--stage "<doc_type>"`, `--agent "agent-approval-grant/GRANT"`, `--doc-path "<doc_path>"` and `--doc-status "<current frontmatter {agent.status_field}>"`, using the `--event` codes named below.

Log `'{doc_label} approval flow started'` (`--event APPROVAL_STARTED`) now.

### Pre-flight: commit any pending edits to the document

Run `git status --porcelain -- <doc_path>` from the project root. If it reports pending changes (staged or unstaged), that content was edited outside this flow and never committed on its own. Commit it now, before anything else touches this file: `git add -- <doc_path> && git commit -m "Update {doc_type} (draft)" -- <doc_path>`.

Why: the approval commit must contain only the status and version change. If a pending content edit were swept into it, a hook that guards approved documents against edits smuggled past approval could not tell the two apart.

If nothing is pending, continue. If there was something to commit, log `'{doc_label} pending edit committed before approval flow'` (`--event PREFLIGHT_COMMIT`) and continue. If the commit fails, tell the user the git error and stop here.

### Check the review is current

Resolve `review_skill` from the argument if given, else `{agent.review_skill_defaults}[doc_type]`. Resolve `review_artifact_label` from the argument if given, else `{agent.review_artifact_label_defaults}[doc_type]`, else `"review report"` if a `review_skill` was resolved. If no `review_skill` resolves, there is no companion review to keep current; continue straight to the first confirmation.

Otherwise, read `doc_path`'s current frontmatter `version` (0 if absent) and compute `target_version = version + 1` — the version this document will carry once this approval lands. Resolve the review artifact's filename as the `review_artifact_filename` argument if given, else `{agent.review_artifact_filename_defaults}[doc_type]`, else `"review-report.md"`, in the same folder as `doc_path`. The version tag that file must carry is the frontmatter field `{doc_type}_version` (e.g. `brief_version`).

- If that file doesn't exist, or its version tag is absent or not equal to `target_version`, the review is missing or stale. Log `'{doc_label} review stale or missing for v{target_version} - regenerating via {review_skill}'` (`--event REVIEW_REGENERATED`), then invoke `skill:{review_skill}` against `doc_path` — its **Validate** (or equivalent review-only) intent, never Create or Update — to regenerate the review for `target_version`. Then re-read the version tag; if it still doesn't equal `target_version`, tell the user the review could not be regenerated and stop here.
- Otherwise, continue.

### First confirmation — reviewed and approved

Tell the user the `{doc_label}` is ready{, and the `{review_artifact_label}` is ready, if one resolved}. Ask directly:

- If `review_artifact_label` resolved: "Have you read the {review_artifact_label} and do you approve the {doc_label}?"
- Otherwise: "Do you approve the {doc_label}?"

Wait for an explicit affirmative.

- Decline or no clear yes: tell them the `{doc_label}` remains in draft, log `'{doc_label} approval declined or deferred'` (`--event APPROVAL_DECLINED --verdict warn`), and stop here.
- Affirmative: log `'{doc_label} first confirmation received'` (`--event CONFIRMATION_1`) and continue.

### Second confirmation — read the document itself

Ask a second, separate confirmation: "Please confirm you have read through the entire {doc_label} document itself, not just the {review_artifact_label, or 'summary'}, before I mark it approved." Wait for an explicit second affirmative.

- Decline: leave status as draft, log `'{doc_label} approval declined at second confirmation'` (`--event APPROVAL_DECLINED --verdict warn`), and stop here.
- Affirmative: log `'{doc_label} second confirmation received'` (`--event CONFIRMATION_2`) and continue.

### Grant it

Only after both confirmations are explicitly given, run:

```
python3 {skill-root}/scripts/grant_approval.py \
  --doc-path "<doc_path>" \
  --doc-type "<doc_type>" \
  --state-file "{agent.state_file}" \
  --status-field "{agent.status_field}" \
  --approved-value "{agent.approved_value}" \
  --approved-by "{user_name}"
```

This sets the status, bumps `version`, saves, hashes the saved content, records the approval under `{doc_type}` in the shared state file (path relative to the project, earlier approvals kept in `history`, other doc_types untouched), commits the document and the state file together with message `Approve {doc_type} v{version}`, and records the `APPROVED` event. Run `python3 {skill-root}/scripts/grant_approval.py --help` for the full argument reference.

Report back `doc_path`, `approved_hash` and `version` from the script's output.

Check `git_commit` in the output. If `git_commit.ok` is `false`, the document is still approved — do not treat it as a failed approval — but tell the user plainly that the commit did not go through, with `git_commit.error`, so they can commit the document and `{agent.state_file}` themselves. Teammates only see the approval once both are committed.
