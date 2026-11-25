# 0006 — Local prompt check replaces the prompt gateway

- **Status:** Proposed
- **Date:** 2026-11-25
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR
- **Supersedes:** the prompt-gateway parts of ADR 0002 (fail-open remote gateway) and ADR 0005 (`bmad-guard-gateway` repo)

## Context
The prompt gateway (PromptGateway, branch `service/prompt-gateway` in `experion-bmad`) screens each prompt before Claude sees it. Its code shows:

- Five checks are plain pattern matching (Python `re`): secrets, PII, prompt injection, a toxicity word list, and a token count (`tiktoken`). No model, no network.
- A sixth check, `llm_validator`, **sends the full prompt to OpenAI** (`api.openai.com`, enabled by default, 15% of the score).
- The instance in use runs on a public free-tier host (Render).

So the gateway meant to keep sensitive data away from an AI model sends every prompt, PHI included, to a **second AI vendor** and through a public host. For HIPAA projects that is a new party handling PHI (it needs its own BAA). It also needs a server, uptime, a key per developer, and it must fail open.

Constraints: PHI must not leave approved systems; guards are small, tested, standard-library Python (ADR 0001); local guards can fail closed (ADR 0002).

## Options
### Option A — Local pattern check in a Claude Code hook, no server
Port the gateway's pattern checks into a bmad-guard skill, run by a `UserPromptSubmit` hook on the developer's machine. Drop the LLM check.
- Pros: prompts never leave the laptop; no server, uptime or key; can block (fail closed); same shape as every other guard (script + tests + events).
- Cons: rules change only with a module release; pattern matching misses free-text PHI (names, diagnoses in sentences); the token count is an estimate (`tiktoken` is not in the standard library).
- Cost / effort: medium (Phase 1, T8).

### Option B — Keep the server, turn the LLM check off, host on internal AWS
- Pros: central rule updates.
- Cons: prompts still travel over the network to a server we run; uptime; per-developer keys; must fail open.
- Cost / effort: medium to high (hosting, TLS, keys).

### Option C — Keep the gateway as it is
- Pros: nothing to change.
- Cons: PHI goes to OpenAI and through a public host. Not acceptable for HIPAA projects.
- Cost / effort: none now; compliance risk.

## Decision
We choose **Option A** (Shameer, 25 Nov), because it removes the data exposure instead of moving it, and needs no server.

Design:
- **Checks** (all local, standard library):
  - secrets: a vendored subset of well-known secret patterns (gitleaks rules, MIT licence) plus an entropy check for random-looking strings;
  - PII / PHI identifiers: patterns with validation (Luhn for card numbers, mod-11 for NHS numbers, Verhoeff for Aadhaar) to cut false alarms;
  - prompt injection: patterns, **record only** (in a user's own prompt the user is not the attacker);
  - size: a token estimate.
- **Actions per category**, not one risk score: secrets block; PHI/PII block in projects marked HIPAA, warn elsewhere; injection is recorded.
- **Project patterns:** a project adds its own identifier formats and allow-listed fakes in `_bmad/custom/project/guard/` (the extension point from Phase 1, T6).
- **Events:** category and rule id, never the matched text (ADR 0003).
- **Quality:** a test set of fake positives and negatives; false-alarm and miss rates are measured in CI.

Later, not in this decision:
- **Semantic check** (PHI in free text): parked until the basic setup is done. Candidates: a Claude Code hook that asks a small Claude model (same vendor that already receives the prompt, so no new BAA), or a small local model (Presidio / GLiNER). Decided by a spike that measures accuracy, time per prompt and install size.
- **Other leak paths** (Phase 2): Claude Code deny rules in managed settings for sensitive paths, and a `PreToolUse` hook for reads and data-export commands. Most sensitive data reaches the model through files and command output, not typed prompts.

## Consequences
- Good: no prompt leaves the laptop for screening; no gateway server, Render instance or OpenAI key; the check can block.
- Bad / risks: free-text PHI is not caught until the semantic check lands; rule updates need a module release; false alarms annoy developers until the rules are tuned.
- Not changed: for HIPAA projects the main control stays contractual — a Claude setup covered by a BAA with zero data retention. This check is defence in depth.
- Follow-up work:
  - Phase 1, T8: build the local check (replaces "move the gateway code").
  - Retire the `prompt-gateway-guard` skill (it calls the server) once the local check is in.
  - `bmad-guard-gateway` repo is no longer needed (ADR 0005).
  - `experion-bmad`: turn off the OpenAI check and stop using the Render host now.
  - Phase 2: deny rules, `PreToolUse` hook, hook wiring. Later phase: semantic-check spike.

## Related
- Past ADRs: 0001 (architecture), 0002 (enforcement layers; gateway part superseded), 0003 (event logging), 0005 (repo layout; gateway repo superseded).
- Links: `docs/plans/phase-1.md` (T8); PromptGateway code on `experion-bmad` branch `service/prompt-gateway` (`validators/`, `policy.yaml`).
