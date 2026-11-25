---
name: log-action
description: Record one project action as a guard event
code: LOG
added: 2026-07-17
updated: 2026-11-25
type: script-backed
---

# Log Action

## What Success Looks Like

Given an action description and a project name, exactly one new event is recorded. It says which development-cycle stage the action concerns, which skill or agent was executing, and, when the action concerns a document, that document's status, path and SHA-256 (never its content). When the action is a confidence-scoring event, it also records the score, its Low/Medium/High label and a one-line rationale. Nothing already recorded is changed.

## Your Approach

### Resolve the user

Resolve `{user_name}` by reading, in order, `{project-root}/_bmad/config.user.yaml`, then `{project-root}/_bmad/bmm/config.yaml`, then `{project-root}/_bmad/config.yaml` — use the first `user_name` value found. Do this even if `{user_name}` already appears to be set from an earlier activation step, since this skill is often called from inside another skill's flow. Never substitute git identity, an email address or any other ambient signal; if no config file defines `user_name`, use `unknown`. (The event store adds the verified developer from their key, so this value is a label, not proof.)

### Resolve the project name

Check `{project-root}/_bmad/custom/project.json` for a non-empty `project_name` field. If it's there, use it directly — no prompt. Only if the file is missing or the field is empty, ask the user for the project name, then offer to save it to `{project-root}/_bmad/custom/project.json` (`{"project_name": "<name>"}`, keeping any other fields) so the team resolves the same name next time.

### Resolve the stage, agent and document

- `{stage}` — the development-cycle stage the action concerns, e.g. `brief`, `prd`, `architecture`, `stories`. The calling flow should pass it. If the action has no stage, leave it empty.
- `{agent}` — the skill or persona executing this log call (e.g. `agent-approval-grant/GRANT`). The calling skill should pass its own name. For a bare user request, leave it empty.
- `{doc_status}` — the document's current frontmatter `status` (e.g. `draft`, `approved`), read from the document. Leave it empty if the action has no document.
- `{doc_path}` — the document's path. Only the path and its SHA-256 are stored.

Never block or prompt the user for these. Missing values are recorded as absent, not as errors.

### Resolve confidence fields (only for a confidence-scoring action)

- `{confidence}` — the 0-100 score just written to the document's frontmatter.
- `{confidence_label}` — Low, Medium or High.
- `{confidence_rationale}` — the rationale; stored as one short line.

### Resolve the outcome

- `{event}` — a stable code for what happened (e.g. `PRD_APPROVED`, `GATE_BLOCKED`). It becomes the event's reason code. Default `ACTION`.
- `{verdict}` — `allow`, `warn`, `block` or `info` (default). If the caller passes only `{result}` (`started`, `passed`, `blocked`, `completed`, `failed`), `blocked` is recorded as `block`, `failed` as `warn`, everything else as `info`.
- `{run_id}`, `{duration_seconds}`, `{details}` — optional; `details` must be short and non-sensitive.

### Log the action

Run:

```
python3 scripts/log_action.py --project-name "<name>" --action "<one-line description>" --user "{user_name}" --stage "<stage>" --agent "<agent>" --doc-status "<doc_status>" --doc-path "<doc_path>" --confidence "<confidence>" --confidence-label "<confidence_label>" --confidence-rationale "<confidence_rationale>" --event "<event>" --verdict "<verdict>" --result "<result>" --run-id "<run_id>" --duration-seconds "<duration_seconds>" --details "<details>"
```

Leave out any flag whose value is empty. Run `python3 scripts/log_action.py --help` for the full reference. The old `--pattern` and `--output-dir` flags are still accepted and ignored.

The script prints JSON with the recorded event and a status: `queued` events are waiting locally, `sent` events reached the event store. Report back the reason code, stage and action text. If the script prints a warning that events could not be sent, tell the user once; the events are safe in the local queue.
