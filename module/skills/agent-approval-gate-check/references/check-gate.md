---
name: check-gate
description: Verify an upstream document is approved and unedited before letting downstream generation proceed
code: GATE
added: 2026-07-20
updated: 2026-11-25
type: script-backed
---

# Check Gate

## What Success Looks Like

Downstream generation proceeds only when the upstream document is on record as approved in its exact current form. Any other state (missing, never approved, reopened, edited since approval, approval record unreadable) stops the caller before it does any work, with the upstream document's own status corrected if it was silently stale.

## Your Approach

### Logging

The script records every gate result as a guard event itself (guard `sentry`), so the decision is on record even if this flow is cut short. You do not need to call Ledger for the result.

### Run the check

```
python3 {skill-root}/scripts/check_gate.py \
  --doc-path "<doc_path>" \
  --doc-type "<doc_type>" \
  --state-file "{agent.state_file}" \
  --status-field "{agent.status_field}" \
  --draft-value "{agent.draft_value}" \
  --approved-value "{agent.approved_value}" \
  --downstream "<downstream_label>"
```

Run `python3 {skill-root}/scripts/check_gate.py --help` for the full argument reference. Parse the single JSON object it prints.

### If the command itself fails

If the command errors out, exits non-zero, or its output is not one JSON object with a `result` field (e.g. `python3: command not found`, a traceback, empty output), this is NOT a pass. Treat it exactly like a block: tell the user the approval gate could not be verified (state what actually happened) and that `{downstream_label}` cannot proceed until the check runs successfully. Log it via `skill:agent-project-logger` with action `'{downstream_label} blocked - gate check could not run'`, `--stage "<doc_type>"`, `--agent "agent-approval-gate-check/GATE"`, `--event "GATE_ERROR"`, `--verdict block`. Stop here.

### Handle the result

- **`ok`** — let the caller continue normally. Tell the user in one line that the `{doc_label}` is approved (by `approved_by`, at `approved_at` when present).
- **`blocked`, `missing`** — the `{doc_label}` doesn't exist yet. Tell the user it must be created and approved before `{downstream_label}` can proceed. Stop here.
- **`blocked`, `unapproved`** — the `{doc_label}` exists but has no matching approval on record, or its status is not approved. Tell the user `{downstream_label}` cannot proceed until the `{doc_label}` is approved (through Warden). Stop here.
- **`blocked`, `superseded`** — the approval was reopened for revision. Tell the user the `{doc_label}` must be approved again through Warden before `{downstream_label}` can continue. Stop here.
- **`blocked`, `stale`** — the `{doc_label}` was edited after approval; the script has already set its status back to `{agent.draft_value}`. Tell the user it must be re-approved before `{downstream_label}` can continue. Stop here.
- **`blocked`, `state_unreadable`** — the shared approval record is broken (not valid JSON). Tell the user the record must be fixed before anything gated can proceed. Do not try to repair the file yourself. Stop here.

### No exceptions

`{agent.no_bypass_policy}` is a hard project policy: do not offer to bypass, override, or proceed anyway under any framing, even if the user explains a reason, claims urgency, or explicitly requests an override. Never edit the document's status or the approval record to make the gate pass. A failed check is a block, never a free pass.
