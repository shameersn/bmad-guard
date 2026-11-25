---
name: validate-prompt
description: Submit text to PromptGateway's /api/v1/validate endpoint and act on its ALLOW/FLAG/BLOCK verdict
code: VAL
added: 2026-07-27
type: script-backed
---

# Validate Prompt

## What Success Looks Like

The user gets an unambiguous answer: what the gateway decided, how risky it scored the text, and why — with a BLOCK stopping whatever prompted the check and a FLAG left for the user to decide on. If the gateway itself can't be reached, that's reported as plainly as a verdict would be, never mistaken for a pass.

## Your Approach

### Gather the text

Take the exact text the user wants checked — verbatim, no summarizing or paraphrasing it before sending, since the gateway needs to see what would actually go out. If it's over 50,000 characters, tell the user PromptGateway's limit before attempting the call.

### Run the check

Pass the text on stdin, never as a command-line argument (a command line is visible to other users on the machine):

```
python3 {skill-root}/scripts/validate_prompt.py \
  --base-url "{agent.base_url}" \
  --model "{agent.downstream_model}" \
  --timeout "{agent.timeout}" <<'BMAD_GUARD_TEXT'
<text>
BMAD_GUARD_TEXT
```

The API key is read from the `BMAD_GUARD_GATEWAY_KEY` environment variable; never put a key in a command, a file or this conversation. Run `python3 {skill-root}/scripts/validate_prompt.py --help` for the full argument reference. Parse the single JSON object it prints.

### If the service can't be reached

The script's `result` field is `"unreachable"` (connection failure), `"http_error"` (the service responded with an error status) or `"refused"` (bad URL or text; nothing was sent) rather than a `decision` field when the call didn't complete normally. Treat both exactly like a failed check, never a pass:

- `"unreachable"` — tell the user PromptGateway could not be reached at `{agent.base_url}` and that it needs to be running before this check can be done (e.g. `docker compose up` from the PromptGateway folder, or `uvicorn app:app --reload` inside its virtualenv). Stop here.
- `"refused"` — relay the error (e.g. the URL is not https, or the text is empty or too long), and stop.
- `"http_error"` — relay the status code and response body verbatim, and stop. A 400 usually means the optional `model` field isn't on PromptGateway's `allowed_models` list — if `{agent.downstream_model}` is set, mention that as the likely cause.

### Handle the decision

- **`BLOCK`** — state the decision, `risk_score`, `reason`, and `categories_triggered` plainly. Do not proceed with whatever action prompted this check, and do not offer to bypass it — a BLOCK is not something to talk around.
- **`FLAG`** — state the same details as a BLOCK, framed as a warning, then ask the user whether to proceed anyway. Only continue on an explicit yes.
- **`ALLOW`** — confirm briefly (decision and risk score are enough). If the response includes a non-empty `sanitized_prompt`, offer it as the version to use downstream, since it's the PII-redacted form of what was submitted.

Always show `request_id` if the user might want to look the check up later via PromptGateway's `/api/v1/audit/{request_id}` endpoint.

### Logging

The script records BLOCK, FLAG and an unavailable gateway as guard events itself (guard `gateway`), with the decision, risk score, categories and `request_id` — never the text. ALLOW is not recorded. You do not need to call Ledger.
