---
name: reopen-document
description: Confirm with the user, then reopen an approved document for revision and mark its approval-state entry superseded
code: REOPEN
added: 2026-07-23
updated: 2026-11-25
type: script-backed
---

# Reopen Document

## What Success Looks Like

The document's frontmatter status is `{agent.reopen_value}` and the matching entry in the shared state file is kept but flagged `superseded: true`, with who and when — never deleted, since it is the audit record of the approval that came before. Sentry now blocks anything built on this document until Warden approves it again. Nothing moves until the user has explicitly confirmed, by name, that this document should be reopened and understood that it will need approving again afterward.

## Your Approach

### Resolve the user

Resolve `{user_name}` the same way Grant Approval does: read, in order, `{project-root}/_bmad/config.user.yaml`, then `{project-root}/_bmad/bmm/config.yaml`, then `{project-root}/_bmad/config.yaml`, and use the first `user_name` value found. Never substitute git identity, an email address or any other ambient signal; if none is defined, use `unknown`.

### Logging

The reopen script records the reopen itself as a guard event (guard `warden`, reason `REOPENED`). Log the human steps through `skill:agent-project-logger` with `--stage "<doc_type>"`, `--agent "agent-approval-grant/REOPEN"` and `--doc-path "<doc_path>"`.

### Confirm — by name, with the consequence stated

Ask a single, explicit confirmation that names the `{doc_label}` and states the consequence: "Reopening the {doc_label} will put it back in draft and it will need to be approved again before anything downstream can build on it. Do you want to reopen it?" Wait for an explicit affirmative.

- Decline or no clear yes: leave the document and its approval-state entry untouched, log `'{doc_label} reopen declined'` (`--event REOPEN_DECLINED`), and stop here.
- Affirmative: continue.

### Reopen it

Only after the explicit affirmative, run:

```
python3 {skill-root}/scripts/reopen_document.py \
  --doc-path "<doc_path>" \
  --doc-type "<doc_type>" \
  --state-file "{agent.state_file}" \
  --status-field "{agent.status_field}" \
  --reopen-value "{agent.reopen_value}" \
  --reopened-by "{user_name}"
```

This sets the document's status to `{agent.reopen_value}` and, if the matching `{doc_type}` entry names this document, marks it `superseded` with `superseded_by` and `superseded_at`, without touching any other doc_type. Run `python3 {skill-root}/scripts/reopen_document.py --help` for the full argument reference.

Report back `doc_path` and whether an approval entry was found and superseded. Remind the user to commit the document and `{agent.state_file}` so teammates see the reopen.
