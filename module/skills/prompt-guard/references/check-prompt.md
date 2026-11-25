---
name: check-prompt
description: Check text locally for secrets, personal or health identifiers, prompt injection and size, and act on the allow, warn or block result
code: CHK
added: 2026-11-25
type: script-backed
---

# Check Prompt

## What Success Looks Like

The user gets a clear verdict (allow, warn or block) with categories and rule ids, and no part of the checked text appears in the reply, the output or the event log.

## Your Approach

### Run the check

Pass the exact text on stdin, never as a command-line argument (a command line is visible to other users on the machine):

```
python3 {skill-root}/scripts/prompt_check.py <<'BMAD_GUARD_TEXT'
<text>
BMAD_GUARD_TEXT
```

`--project-root <dir>` sets the project (default: `CLAUDE_PROJECT_DIR`, else the git top level). `--no-event` skips the event. Parse the single JSON object it prints:

```
{"result": "allow" | "warn" | "block",
 "findings": [{"category": "secrets", "rule_id": "secret.github-token"}],
 "counts": {"secrets": 1, "phi": 0, "injection": 0, "size": 0}}
```

Warnings about the configuration (a bad action value, a bad project patterns file) go to stderr; relay them.

### Handle the result

- **`block`**: state the categories and rule ids. Do not continue with the text and do not suggest a way around the check. The user removes the value (or uses an obvious fake) and checks again.
- **`warn`**: state the categories and rule ids as a warning and ask whether to go on. Continue only on an explicit yes.
- **`allow`**: confirm briefly. If injection findings are listed, mention them in one line; they do not change the verdict.

Never quote, mask or describe the matched value.

## Rules

| Category | Rule ids | Default action |
|---|---|---|
| `secrets` | `secret.aws-access-key-id`, `secret.aws-secret-access-key`, `secret.github-token`, `secret.github-fine-grained-pat`, `secret.gitlab-token`, `secret.slack-token`, `secret.slack-webhook`, `secret.stripe-live-key`, `secret.google-api-key`, `secret.openai-api-key`, `secret.anthropic-api-key`, `secret.npm-token`, `secret.private-key`, `secret.jwt`, `secret.connection-string`, `secret.credential-assignment`, `secret.high-entropy` | block |
| `phi` | `phi.card-number` (Luhn + card prefix), `phi.nhs-number` (mod 11), `phi.aadhaar-number` (Verhoeff), `phi.us-ssn` (invalid areas 000, 666, 9xx excluded), `phi.email`, `phi.phone-us`, `phi.phone-intl` | warn |
| `injection` | `injection.ignore-instructions`, `injection.system-prompt-override`, `injection.dan-jailbreak`, `injection.role-escape`, `injection.delimiter-confusion`, `injection.reveal-system-prompt`, `injection.base64-instruction`, `injection.unrestricted-ai`, `injection.bypass-guardrails` | record |
| `size` | `size.max-tokens` (characters / 4 above `max_prompt_tokens`) | warn |

NHS and Aadhaar numbers written as one plain run of digits count only with the word NHS / Aadhaar nearby; grouped with spaces or dashes they count on their own.

`secret.high-entropy`: 32 to 200 characters of `[A-Za-z0-9+=_-]` with upper case, lower case and at least 2 digits, and Shannon entropy of at least `log2(length) - 0.9` bits per character (capped at 4.3). Pure hex (git SHAs, hashes, UUIDs) and base64 that decodes to readable text are skipped.

Built-in allow-list (obvious fakes): anything containing `example` (the AWS documentation keys, `example.com`), Stripe `sk_test_` / `pk_test_` / `rk_test_` keys, `@localhost`, `@127.0.0.1`, `.invalid`, `noreply@` / `no-reply@`, the SSNs `000-00-0000`, `123-45-6789`, `078-05-1120`, the public card test numbers (`4111…`, `4242…`, `5555…4444`, `3782…0005`), and US `555-01xx` phone numbers.

The secret formats are written from public vendor documentation; no rule pack was copied. The injection patterns are ported from PromptGateway's `policy.yaml` and tightened where ordinary developer text matched.

## Configuration

Actions (`block`, `warn`, `record`, `off`) and `max_prompt_tokens` come from `{skill-root}/customize.toml`, then `{project-root}/_bmad/custom/prompt-guard.toml` (the team layer; the project owns this file, the module ships none), then `{project-root}/_bmad/custom/prompt-guard.user.toml` (personal; it may only make an action stricter). A HIPAA project sets:

```toml
[agent]
phi = "block"
```

## Project patterns and allow-listed fakes

`{project-root}/_bmad/custom/project/guard/prompt-patterns.json` (JSON, because Python 3.10 has no TOML reader):

```json
{
  "patterns": [{"id": "mrn", "category": "phi", "regex": "\\bMRN[- ]?\\d{8}\\b"}],
  "allow": ["MRN-00000000"]
}
```

- `id`: lower-case letters, digits, `.`, `_`, `-`; reported as `project.<id>`.
- `category`: `secrets`, `phi` or `injection`.
- `regex`: Python regular expression, at most 500 characters.
- `allow`: text of at least 4 characters; a match that contains it (case-insensitive) is skipped.

A file that is not valid JSON, and each bad entry, is skipped with a warning; the built-in rules always run.

## Logging

The script records one guard event itself whenever there are findings (guard `prompt-guard`, stage `prompt`): verdict `block`, `warn`, or `info` for record-only findings, and reason code `PROMPT_SECRET`, `PROMPT_PHI`, `PROMPT_INJECTION` or `PROMPT_SIZE` (the category with the strictest action). Fields: `result`, `categories`, `rule_ids`, `counts`; the hook adds `session_id`. Never the text. An `allow` with no findings is not recorded. When Ledger (`agent-project-logger`) is not installed, the check still runs and warns on stderr. You do not call Ledger.

## The hook

`scripts/prompt_guard_hook.py` runs the same check for Claude Code's `UserPromptSubmit` event (wired into settings in Phase 2):

- `block` → `{"decision": "block", "reason": ...}`; the reason names categories and rule ids only.
- `warn` → `systemMessage` for the user and `hookSpecificOutput.additionalContext` for Claude.
- `allow` → no output.
- An internal error in the guard fails **open**: a `systemMessage` says the prompt was not checked, and a `GUARD_ERROR` event is recorded. Review question: should HIPAA projects fail closed instead?

## Limits

- Free-text PHI (names, diagnoses in sentences) is not caught; a semantic check is parked (ADR 0006).
- Prompts are the smaller leak path: files and command output reach the model too (Phase 2 deny rules and `PreToolUse` hook).
- The token count is an estimate.

## History

This skill replaces `prompt-gateway-guard` (removed in T8, ADR 0006), which sent prompts to the PromptGateway server. Its events used guard `gateway` with these reason codes:

- `GATEWAY_BLOCK`: the gateway answered BLOCK (verdict `block`).
- `GATEWAY_FLAG`: the gateway answered FLAG (verdict `warn`).
- `GATEWAY_UNAVAILABLE`: the gateway could not be reached or returned an HTTP error; the prompt went through unchecked (verdict `warn`).

Fields were `request_id`, `risk_score`, `categories` and `gateway_status`.
