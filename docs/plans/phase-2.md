# Phase 2 plan: Enforcement

| | |
|---|---|
| Ticket | GUARD-2 |
| Time box | 3 weeks (15 working days) |
| Approvers | Shameer, Manesh G (PR approval) |
| Status | Built (T1–T8). Gate 2: 5 of 6 boxes ticked; the last one waits for dates from Shameer (2026-11-26). See "Phase 2 status". |
| Inputs | ADRs 0001–0006; Phase 1 plan and install test; "Guarded BMAD: Org Rollout Plan" (Claude doc) |

## Phase 2 status (2026-11-26)

**Done.** All eight tasks are built (T1 to T6 are merged to `main`; T7 and T8 are on stacked branches, not merged yet). The hooks, the git hooks, the setup wiring and the managed-settings template exist, and the four rollout-doc cases are blocked in an automated run (`docs/reports/phase-2-red-team.md`: 66 of 66 cases, 4 of 4 rollout cases, with close-but-safe control rows). The Phase 3 draft is `docs/plans/phase-3.md`.

**Open, and not closable by code:**

- **Real-session checks.** No hook has been driven by a live Claude Code model session (the red-team suite drives the hooks with the documented stdin JSON). The open checks are in `docs/hooks.md` (1, 2, 5, 7, 8) and in each todo list below under "Real block seen", "A real review", "A real push", "A real commit" and "Real warning seen". The subagent session-id question (check 7) decides how strong the same-session proof is.
- **Dates.** Every item in "Managed settings rollout todos" has an owner or `TBD`, and `Date: TBD (Shameer to set)`. No date was invented. Gate 2 box 6 stays open until Shameer sets them.
- **Pilot numbers.** False-block numbers for every guard come from the pilot, not from this repo. The todo lists below keep each decision that waits for them (owners are named there).
- **Phase 3 or later:** machine-level script location, uninstall for the wiring, the collector and credential storage, CI. See `docs/plans/phase-3.md`.

Todo lists: "Managed settings rollout todos", "Command guard todos", "Write gate todos", "Review record todos", "Content scan todos", "Pre-commit todos" and "Open items for later phases", all below.

## Goal

Turn the Phase 1 guard scripts into guards that run outside the model and block. At the end of Phase 2:

1. Claude Code hooks (L1) block destructive commands, sensitive reads and data export, AI writes to approval fields, deleted open questions, PHI and secrets in written files, and source writes without an approved plan that is ready for development.
2. Plain Python git hooks (L2) block PHI and secrets at commit, unapproved brief status changes, dependencies outside an optional tech-stack list, and a push without an independent review record.
3. The setup script wires all of it into a project; a managed-settings template exists for a later IT rollout.
4. The four red-team cases from the rollout doc are blocked in an automated test: **PHI, wrong DB, edit after approval, same-session review.**

## Decisions already made (input to this phase)

From the grill session on 25 Nov (Shameer):

- **Scope** follows the rollout doc's Phase 2 box and guard table, not only the Phase 1 "out of scope" list.
- **Hook delivery:** the setup script merges the hook wiring into the project's `.claude/settings.json`. We also ship a managed-settings template and a short rollout doc; the real MDM / IT rollout comes later. Bypass risk is accepted (ADR 0002).
- **Copilot:** no Copilot hooks. Copilot users are covered by the git hooks only. Known gap.
- **Destructive commands:** a deny-list in a `PreToolUse` hook (fail closed), plus Claude Code `permissions.deny` rules as a second layer. No allow-list.
- **Fail modes:** `PreToolUse` guards and git hooks fail closed (a crash blocks). The prompt guard fails closed in HIPAA projects and open with a warning elsewhere. Event logging never blocks.
- **Brief status:** the Python port of `revert-brief-status.js` blocks the commit with a clear message; it no longer changes the status silently.
- **Tech-stack list is optional.** A project may add one; with a list, pre-commit blocks a new dependency that is not on it. With no list, pre-commit records an info event only (no message).
- **One write gate:** plan-hash check first, then the ready-for-dev check, one block message with all reasons.
- **Gate 2 proof:** a red-team script in a throwaway sandbox, like the Phase 1 install test.

## Out of scope

- Collector, ClickHouse, Grafana, the "commits with no guard events" report, credential storage (Phase 3).
- Semantic prompt check (later phase, after a spike).
- CI checks (later, per project).
- Copilot and Cursor hooks.
- Managed settings rolled out by IT / MDM (template only in this phase).

## How the hooks fit

| Layer | Event | Guard | Blocks |
|---|---|---|---|
| L1 | `SessionStart` | setup check | Never; warns when `core.hooksPath` or the hook wiring is missing |
| L1 | `UserPromptSubmit` | prompt-guard (Phase 1) | Secrets; PHI in HIPAA projects |
| L1 | `PreToolUse` Bash | command guard | Destructive commands, data-export commands, `--no-verify` |
| L1 | `PreToolUse` Read / Grep / Glob / Bash | sensitive-path guard | Environment files, keys, dumps, `~/.config/bmad-guard/` |
| L1 | `PreToolUse` Write / Edit / MultiEdit / NotebookEdit, and Bash | document guard | AI writes to approval fields, `approval-state.json`, guard records and guard config; removal of an unresolved question or assumption |
| L1 | `PreToolUse` Write / Edit / MultiEdit / NotebookEdit, and Bash | content scan | Secrets; PHI in HIPAA projects (a warning elsewhere); Bash only for text a command writes to a file |
| L1 | `PreToolUse` Write / Edit / MultiEdit / NotebookEdit, and Bash write targets | write gate | Source writes without an approved, unchanged plan, or with a story that is not ready for dev; records the build session |
| L2 | `pre-commit` | scan, brief status, tech stack | Same scan on staged files; brief status changes not made by Warden; dependency not on the list (only when a list exists) |
| L2 | `pre-push` | review record | No review record, same session as the build, or a record for another commit |

Every decision emits one event through Ledger (ADR 0003): guard, stage, verdict, reason code, path and SHA-256. Never content.

## Tasks

One branch and one PR per task: `feature/GUARD-2-<short-name>`.

### T1. Hook wiring, session ids and settings (Days 1–2)

- **Day 1 check first (half a day):** against the Claude Code version we pin, confirm and write down in `docs/hooks.md`:
  - the hook input fields per event (`session_id`, `tool_name`, `tool_input`, `cwd`) and how a hook blocks (exit code and JSON output);
  - **how a guard script learns the session id.** Hooks get it in their input; a script the model runs does not. Design rule: session ids are written by hooks, never typed by the model. If no clean way exists, T7 records ids from the `PreToolUse` hook instead.
- One small dispatcher per hook event (`module/skills/tool-guard/scripts/`), so `settings.json` holds few entries and each guard stays a separate, tested module.
- Setup script: merge the hook entries and deny rules into `.claude/settings.json`. Keep the project's own entries; `--dry-run` shows the diff; `--check` reports missing wiring; never overwrite without `--force`.
- `module/managed-settings/managed-settings.json` template (same hooks, deny rules, hooks marked as managed) and `docs/managed-settings-rollout.md` (what IT installs, where, how to test).
- `SessionStart` check: warn when `core.hooksPath` is not `_bmad/custom/guard/hooks` or the hook wiring is missing.

**Done when:** tests cover the settings merge (empty file, existing hooks, conflict, dry-run, check); the install test checks the wiring survives a BMAD update; the session-id question has a written answer.

### T2. Command and sensitive-path guard (Days 2–4)

- **Destructive commands (deny-list, fail closed):** `rm -rf` and friends outside the project's scratch folders, `git push --force` / `-f`, `git reset --hard`, `git clean -f`, `git branch -D`, `git commit --no-verify` / `git push --no-verify`, `git config core.hooksPath`, `DROP` / `TRUNCATE` / `DELETE` without `WHERE` in SQL clients, `chmod -R 777`. The list lives in `customize.toml`; a project can add, not remove.
- **Data export:** database clients and dumps against non-local hosts (`psql`, `mysql`, `mongo*`, `pg_dump`, `mongodump`), `aws s3 cp|sync` from a bucket. Block, with a reason code.
- **Sensitive paths:** `.env*` (not `.env.example`), `*.pem`, `*.key`, `id_*`, `*.p12`, `*dump*.sql`, `*.bak` of databases, `~/.aws/`, `~/.ssh/`, `~/.config/bmad-guard/`. Checked on Read, Grep, Glob and on paths inside Bash commands (best effort). Same list as `permissions.deny` rules.
- Project extensions in `_bmad/custom/project/guard/`.

**Done when:** a test per rule (block and a close-but-safe allow); a malformed hook input blocks; events carry the rule id, never the command text.

### T3. Document guard (Days 4–6)

- **Approval fields:** block an AI Write / Edit that changes `_bmad/custom/approval-state.json`, or that changes `status: approved`, `approval_status`, `approved_by`, `approved_at` or a `Resolved:` / `[ASSUMPTION-RESOLVED: …]` line in a planning document. Bash writes to the same files are blocked by a separate shell check (`bash_write_guard.py`: redirects, `tee`, `cp` / `mv` / `ln`, `sed -i`, `rm`, `git checkout` / `restore`; best effort). T2's path check does not cover them: its list is secrets only. Warden's own script is not a tool write or a shell write to that file (the state file is only an argument of `python3`), so it still works after human confirmation.
- **Guard records:** block AI Write / Edit to the review and build records of T7 (`_bmad-output/guard/`); only the guard scripts and hooks write them.
- **Guard config:** block AI Write / Edit (and Bash writes, best effort) to `_bmad/custom/project/guard/` (for example `tool-rules.json`). Otherwise the agent can add a scratch folder and widen `rm -r`. From the T2 review.
- **Question delete:** on an edit to a story, plan or PRD, compare old and new text with the ready-for-dev markup (`docs/ready-for-dev-markup.md`). Removing an open question or `[ASSUMPTION: …]` that has no resolution record blocks.

**Done when:** tests cover each field, a legitimate edit that keeps the item, and a delete with and without a resolution.

**Built (T3), what differs from the text above:** rules and limits are in `module/skills/tool-guard/references/guard-rules.md`.

- The Markdown rules apply to every `.md` / `.markdown` file, not to a folder list (BMAD output folders can be renamed). Only the frontmatter counts for the approval fields.
- `status` and `approval_status` block only for the value `approved` (added, removed or changed). `pending-approval` and BMAD's phase values are ordinary edits, because `bmad-build` writes them.
- An AI edit cannot add, change or remove a `Resolved:` line or `[ASSUMPTION-RESOLVED ...]` tag (`DOC_APPROVAL_FIELD`), so a question is answered by a person. `DOC_QUESTION_REMOVED` finds the item that is simply gone; an id still present, open or resolved, is not removed.
- `MultiEdit` is in the matcher (it exists in 2.1.326; `docs/hooks.md` section 11). Run the setup script again to update a project's wiring.
- New rule ids: `DOC_APPROVAL_FIELD`, `DOC_APPROVAL_STATE`, `DOC_GUARD_RECORD`, `DOC_GUARD_CONFIG`, `DOC_GUARD_SCRIPTS`, `DOC_QUESTION_REMOVED`.
- **Guard protects itself** (`DOC_GUARD_SCRIPTS`, from the T3 review): agent writes to `_bmad/custom/guard/**`, `.claude/settings.json` / `settings.local.json`, the guard skills and every file in the setup manifest are blocked, for file tools and shell writes.
- **T3 review decisions:** resolutions stay person-only in the pilot (revisit after the false-block numbers); every Markdown file is checked; no Bash check for approval fields in `.md` files (T6 pre-commit is the backstop); `MultiEdit` stays.

### T4. Write gate (Days 6–8)

- On Write / Edit to a path outside the allowed list, run in order:
  1. plan check: the current story's `bmad-build` plan is approved and its hash still matches (Sentry, `hash_mode = build`);
  2. ready-for-dev check on the story (`ready_for_dev.py`).
  One block message lists every reason.
- **Allowed paths** (no gate): `_bmad/`, `_bmad-output/`, `docs/`, `.claude/`, `.agents/`, `.bmad-guard/`. A project adds paths in `_bmad/custom/project/guard/`.
- **Current story:** read from the plan that `bmad-build` marks `in-progress`. No in-progress plan → block with `NO_ACTIVE_PLAN`.
- Stories with `gate: generic` skip the ready-for-dev check (ADR 0004); they still need an approved plan.
- Switch the ready-for-dev gate default from `advisory` to `enforce` (the markup is agreed, Gate 1).
- `bmad-build` override: run the write gate's check before coding starts, so the developer sees a block before any code is written.

**Done when:** tests cover no plan, unapproved plan, edited plan, open question, open assumption, low confidence, generic story, allowed path, and all reasons in one message.

**Built (T4), what differs from the text above:** rules, design choices and limits are in `module/skills/tool-guard/references/guard-rules.md` ("Write gate").

- **Rule ids:** `GATE_NO_ACTIVE_PLAN` (the plan says `NO_ACTIVE_PLAN`), `GATE_MANY_ACTIVE_PLANS`, `GATE_PLAN_NOT_APPROVED`, `GATE_PLAN_CHANGED`, `GATE_STATE_UNREADABLE`, `GATE_STORY_NOT_FOUND`, `GATE_RECORD_FAILED`, `GATE_ERROR`, and one `GATE_READY_<CODE>` per ready-for-dev reason (open questions, open assumptions, low confidence, ...), so a test and a developer see the exact cause.
- **Where the plan lives (checked in BMAD 6.12.4, `bmm-skills/ship/bmad-build`):** step 1 sets `spec_file` to `{implementation_artifacts}/spec-{slug}.md` (default `_bmad-output/implementation-artifacts/`), or `{spec_folder}/stories/{story_id}-{slug}.md`. The spec template carries `status: draft | ready-for-dev | in-progress | in-review | done`; step 3 sets `in-progress` before coding, step 4 sets `in-review` before the review. The `story` and `approval_status` fields come from our override. The gate looks for the plan in any Markdown file under `_bmad-output/`.
- **`in-review` also counts** when no plan is in progress (bmad-build patches code in step 4). `in-progress` wins when both exist. Two plans in progress block.
- **The Bash gate is on** (best effort), for explicit write targets only; scratch folders, `/dev/null`, `/tmp` and anything outside the project are not gated. Writes outside the project are not gated at all.
- **Build record:** `_bmad-output/guard/builds/<story>.json` = `{"story", "sessions", "first_at", "last_at"}`; session ids from the hook input only. If it cannot be written, the write is blocked. T7 reads it and derives the story name the same way (`write_gate.story_key`).
- **Sentry** got a `reset_stale` option (default on, as before). The hook calls it with `False`: it asks and does not change the plan.
- **The ready-for-dev default is `enforce`.** The hook always enforces and reads the skill's own `customize.toml` only (not a project override, which the agent could edit).
- **Project paths:** `write_gate_allowed_paths` in `tool-rules.json`, add-only, validated; a bad file blocks through the path and command guards and adds nothing to the gate.
- **Install test:** smoke calls for the hook and the `--check` CLI, and an end-to-end case with a fake plan approved by Warden.
- **`gate: generic` is person-only (T4 review):** `DOC_APPROVAL_FIELD` now also blocks an AI edit that adds, removes or changes `gate: generic` in a Markdown frontmatter, so the agent cannot make a story skip ready-for-dev. A person sets it.
- **T4 review decisions:** `in-review` counts when nothing is in progress; two plans in progress block; the `story` field is a path or an id (`<id>.md`, `<id>-*.md`); the hook always enforces, `mode = advisory` is for the chat skill only; `git checkout <path>` / `git restore <path>` are not gated for now.

### T5. Content scan on writes and prompt wiring (Days 8–9)

- Reuse `prompt_rules.py` from prompt-guard on the content of Write / Edit: secrets block; PHI blocks in HIPAA projects, warns elsewhere; same allow-listed fakes.
- Wire `prompt_guard_hook.py` to `UserPromptSubmit`.
- Crash handling: prompt-guard fails closed when the project is marked HIPAA, open with a warning and a `GUARD_ERROR` event elsewhere.

**Done when:** a fake key in a written file is blocked; a fake patient identifier blocks in a HIPAA sandbox and warns in another; the event has no matched text.

**Built (T5), what differs from the text above:** rules, design choices and limits are in `module/skills/tool-guard/references/guard-rules.md` ("Content scan").

- **Rule ids:** `CONTENT_SECRET` (blocks), `CONTENT_PHI` (blocks in a HIPAA project, warns elsewhere), `GUARD_ERROR` (the scan itself failed). The event also carries the prompt-guard rule ids of the findings (`detail`), never matched text, content or path.
- **Same rules and settings as prompt-guard.** `content_scan.py` loads `prompt_check.py` from the sibling skill and calls `check()`, so the rules, the project patterns, the allow-listed fakes and the `secrets` / `phi` actions are prompt-guard's. A "HIPAA project" is `prompt_check.is_hipaa()`: `phi = "block"` in the merged prompt-guard config. Injection and size are not checked on writes.
- **What is scanned:** Write `content`, Edit `new_string` (not the old file text), each MultiEdit `new_string`, NotebookEdit `new_source`. Also MultiEdit and NotebookEdit, which the plan did not list.
- **Warn support in the dispatcher:** `Verdict` got `severity` (`block` default, or `warn`) and `detail` (event only). A call with only warnings exits 0 with JSON `systemMessage` + `hookSpecificOutput.additionalContext` (no `permissionDecision`, so the normal permission prompt stays). Warnings and blocks together: one message, exit 2. No existing guard changed. Status of the mechanism is in `docs/hooks.md` section 13: the fields are Verified (docs); the display of a `PreToolUse` `systemMessage` and the model's reaction are not run (open check 8).
- **Crash handling:** inside the content scan, not the dispatcher. HIPAA project: block with `GUARD_ERROR`. Other projects: allow with a warning and a `warn` `GUARD_ERROR` event. A HIPAA setting that cannot be read (bad file, bad encoding, prompt-guard missing) counts as HIPAA. The prompt hook (`prompt_guard_hook.py`) now follows the same rule (before: always fail open); a prompt it cannot parse is still let through.
- **Bash content (decided):** scan the literal text that a command writes to a file: here-documents and here-strings, text piped from `echo` / `printf`, and `echo` / `printf` arguments, when the same command redirects to a file or uses `tee` (the write targets come from `bash_write_guard`). `/dev/null` and friends do not count. A command that only reads is never scanned. Not "scan the whole command": that would block `curl` headers and searches, and the pre-commit scan (T6) is the backstop. Limits: `python -c`, scripts, `sed -i` replacements, variables.
- **`UserPromptSubmit` wiring:** already done in T1 (`hook_wiring.HOOKS`, the managed template, the setup script, `--check`, tests). T5 changed only the script's crash behaviour. A marker test shows the prompt hook's events hold rule ids and the error type only.
- **No path and no BAD_HOOK_INPUT from the scan:** a call with no text field passes this guard (the path guards report bad paths).
- **Install test:** four smoke calls: a fake key blocks, plain text passes, PHI warns, PHI blocks with a HIPAA setting written in the sandbox.
- **T5 review decisions:** the guard skills' override files are protected (`DOC_GUARD_SCRIPTS`); an unparsable `UserPromptSubmit` payload still passes in HIPAA projects (look again with pilot data); Edit scans `new_string` only (look again with false-block numbers); PHI outside HIPAA projects is a warning.

### T6. Git hooks: pre-commit (Days 9–11)

- Plain Python hooks in `module/hooks/`, copied to `_bmad/custom/guard/hooks/` by the setup script. No Husky, no Node.
- `pre-commit`:
  - **scan** staged files with the T5 rules (fail closed);
  - **brief status** (port of `scripts/revert-brief-status.js`): block a commit that changes a document's approval status unless `approval-state.json` holds a matching Warden entry; clear message, no silent revert;
  - **tech stack (optional):** if `_bmad/custom/project/guard/tech-stack.toml` exists and is approved through Warden (Architect), block a new dependency that is not on it. Reads `package.json`, `requirements*.txt` and `pyproject.toml`; other dependency files warn. No list → one info event, no message.
- The hook never waits on the network: events go to the local queue.

**Done when:** tests cover each block, the no-list case, a crash (blocks), and a commit with nothing to check.

**Built (T6), what differs from the text above:** rules, design choices and limits are in `module/hooks/README.md`.

- **Files:** `pre-commit` (a thin launcher; it blocks if the checks cannot load, or if Python is older than 3.10), `guard_pre_commit.py` (the checks), `guard_tech_stack.py` (the list and the dependency readers), `guard_hook_lib.py` (git helpers, skill loading, local events; T7 reuses it). The library code is in the hook folder; the skills' code (prompt-guard, Warden's `approval_core`, `document_text`, Ledger) is loaded from `.claude/skills/` or `.agents/skills/` in the project, not copied. A missing skill blocks with `COMMIT_ERROR` and a message to run the setup script. A missing Ledger only warns.
- **Rule ids:** `COMMIT_SECRET`, `COMMIT_PHI`, `COMMIT_SCAN_PARTIAL`, `COMMIT_SCAN_CONFIG`, `COMMIT_APPROVAL_NO_ENTRY`, `COMMIT_APPROVAL_STALE`, `COMMIT_STATE_UNREADABLE`, `COMMIT_GATE_GENERIC`, `COMMIT_QUESTION_REMOVED`, `COMMIT_RESOLUTION_CHANGED`, `COMMIT_TECH_STACK_NOT_ALLOWED`, `COMMIT_TECH_STACK_UNAPPROVED`, `COMMIT_TECH_STACK_BAD_FILE`, `COMMIT_TECH_STACK_REMOVED`, `COMMIT_DEPENDENCY_UNCHECKED`, `COMMIT_NO_TECH_STACK`, `COMMIT_ERROR`, and the allow events `COMMIT_ALLOWED` and `COMMIT_NOTHING_TO_CHECK`.
- **Scan:** the lines a commit **adds** to the staged version (`git diff --cached`), as in T5. Binary files, deleted files and lock files are skipped; 1 MiB of added text per file is read and a longer file warns. A fake key staged and then removed from the work tree is still blocked; a key only in the work tree is not the commit's business. Unlike T5, a crash **always blocks** (the task text says fail closed; T5 warns outside HIPAA projects).
- **Brief status, the port:** the old script reverted the status to `draft` in the file and re-staged it, only for `brief-*`, `prd-*` and the architecture spine. The port blocks, names the file and the Warden step, never changes a file, and looks at every staged Markdown file. "Matching entry" = the staged `approval-state.json` has an entry whose `doc_path` is the file, that is not superseded, and whose `approved_hash` equals the hash of the staged text (Warden's own `content_hash`, entry's `hash_mode`, newlines folded). It catches both "status set to approved by hand" and "edited after approval" (the old script's case). Approved to anything else is allowed.
- **Which approval fields:** `status` / `approval_status: approved` and `approved_by` / `approved_at` need an entry (block). `gate: generic` and a removed open question only **warn**; a changed `Resolved:` line is an event only. A person writes those, and a commit cannot tell a person from the agent.
- **Tech stack:** the list is `tech-stack.toml` with a small `---` block on top, because Warden can only approve a file that has frontmatter. Format `[allowed]` with `npm` and `python` lists (`*` allowed); an ecosystem not in the file is not governed (warn). Approval = a Warden entry for the list (doc type `tech-stack`) that matches the staged text. A list that is not approved blocks any commit that adds a dependency, so the list cannot be widened in the same commit. Read: `package.json` (all four dependency sections, `npm:` aliases), `requirements*.txt` / `.in`, `pyproject.toml` (PEP 621, PEP 735, Poetry tables). The reader has no TOML library (Python 3.10 has none). No list: one info event, only when a dependency file changed, and no message.
- **Events:** local queue only, never sent (a collector in the environment is ignored). Rule id, check, file count and a detail (rule ids, a count, an error type). Approval events also carry the file path and SHA-256. No paths of scanned files, no matched text, no package names.
- **Setup and install test:** `setup_guard.py` copies every hook file, makes it executable and `--check` reports one that is not. The install test commits through the installed hook in the sandbox (block: fake key, hand-set approved status; allow: plain file, draft document, Warden's own approval commit).
- **Repo rules:** `tests/test_repo_rules.py` checks the hook launcher (no `.py` name) like a script: shebang, PEP 723 header, stdlib only, executable. `ruff.toml` lints it with `extend-include`.
- **T6 review decisions:** keep the `---` block on `tech-stack.toml` for now (Warden change is a todo); `COMMIT_GATE_GENERIC` and `COMMIT_QUESTION_REMOVED` warn in the pilot; `approved` back to any other value needs no Warden entry; lock files are skipped by the scan; a crash (`COMMIT_ERROR`) blocks in every project; `COMMIT_NO_TECH_STACK` is logged only when a dependency file changed; a list that is not approved blocks only commits that add a dependency.

### T7. Independent review record and pre-push (Days 11–13)

- `bmad-code-review` override: at the end of a review, run a script that writes a committed review record, e.g. `_bmad-output/guard/reviews/<story>.json`: story id, reviewed commit SHA, reviewer session id, verdict, time.
- The build session id is recorded by the write gate hook (T4) for each story, also in a committed file. Session ids come from hook input (T1 check), never from the model.
- `pre-push`: for each pushed commit that changes source files of a story, block when the record is missing, the reviewer session equals a build session, or the record's SHA is not an ancestor of the pushed head.

**Done when:** tests cover a valid record, a missing record, the same session, and a stale SHA.

**Built (T7), what differs from the text above:** rules, design choices and limits are in `module/skills/tool-guard/references/guard-rules.md` ("Review record") and `module/hooks/README.md` ("pre-push").

- **The hook writes the review record, not the script.** `review_record.py` (in tool-guard) is a `PreToolUse` guard and a command. When a Bash call is exactly one plain `python3 .../review_record.py --story <story> --verdict pass|fail`, the hook writes `_bmad-output/guard/reviews/<story>.json` = `{"story", "reviewed_sha", "sessions", "verdict", "reviewed_at"}` with `session_id` from its own input and `git rev-parse HEAD`. The command writes nothing and only checks that a fresh record exists. `CLAUDE_CODE_SESSION_ID` and a `VAR=` in the command are ignored, and the command has no session argument. The agent's tool and shell writes to the file are blocked by `DOC_GUARD_RECORD` (T3). This is the "hook writes the id" design the T1 check asked for.
- **Review record rule ids:** `REVIEW_IN_SUBAGENT`, `REVIEW_NO_SESSION`, `REVIEW_NO_COMMIT`, `REVIEW_RECORD_FAILED` (blocks of the record call); `REVIEW_RECORDED` (allow event).
- **What stock `bmad-code-review` does at its end (BMAD 6.12.4, read, not run):** step 4 writes findings into the story's `### Review Findings`, sets the story to `done` (all `decision-needed` and `patch` resolved, no unresolved `high` or `medium`) or `in-progress`, syncs `sprint-status.yaml`, then "On Complete" runs `workflow.on_complete` when it is not empty. It writes no verdict file. The override fills `on_complete`: `pass` for the `done` case, `fail` otherwise; the existing facts (fresh session, severity, diff-to-plan, confidence) are unchanged.
- **Pre-push rule ids:** `PUSH_NO_RECORD`, `PUSH_VERDICT_NOT_PASS`, `PUSH_SAME_SESSION`, `PUSH_RECORD_STALE`, `PUSH_COMMITS_AFTER_REVIEW`, `PUSH_RECORD_UNREADABLE`, `PUSH_BUILD_UNREADABLE`, `PUSH_ERROR`; events `PUSH_ALLOWED`, `PUSH_NOTHING_TO_CHECK`. Files: `module/hooks/pre-push` (launcher) and `guard_pre_push.py`; setup copies and `--check` covers them like pre-commit.
- **Commits are not mapped to stories.** A review record covers every source commit that is an ancestor of (or equal to) its `reviewed_sha`. A source commit is a pushed commit that changes a file outside the write gate's allowed paths (one definition: `write_gate.source_checker`). The reviewer must not be a session in **any** build record of the pushed tree (the story name in a review record is typed by the model). Only `pass` passes. Records and builds are read from the pushed tree. Only records committed in the push get a reason of their own.
- **Pushed commits** = `git rev-list <local> --not <remote sha> --remotes=<remote>`; new branch, delete, tag and non-commit objects are handled (tests with a real bare remote).
- **Subagent session ids stay unverified** (open check 7, todo below). The hook refuses the record call when the input has `agent_id` (a field the docs list; not run by us). Docs say: run the review in a fresh top-level session.
- **Install test:** `review_record.py --help` smoke, and an end-to-end case: a push with no record is blocked, the hook writes the record from a fresh session, the record is committed through pre-commit, the push goes through, and the queue holds `PUSH_NO_RECORD` and `PUSH_ALLOWED`.
- **T7 review decisions:** a session that built any story cannot review any story; the record call is refused when the hook input has `agent_id` (subagent); one review of the branch tip covers all earlier unpushed source commits (pilot, then ask the teams); the record is written by the `PreToolUse` hook before the permission prompt (a `PostToolUse` hook is deferred); the review record is committed apart from code commits (enforced through "covered").

### T8. Red-team suite and Gate 2 (Days 13–15)

- `scripts/red_team.py`: in a throwaway sandbox (fresh BMAD 6.12.4 + setup script), drive each case through the real hook entry points with fake data:
  1. **PHI:** a fake patient identifier in a prompt, in a written file and in a commit (HIPAA sandbox).
  2. **Wrong DB:** a sandbox with an approved tech-stack list; adding a database driver not on it is blocked at commit.
  3. **Edit after approval:** change an approved plan, then write a source file: blocked; the AI writing `approval_status: approved`: blocked.
  4. **Same-session review:** a review record with the build session id: push blocked.
  Plus one case per other guard (destructive command, sensitive read, data export, deleted question, `--no-verify` via the agent).
- Output: `docs/reports/phase-2-red-team.md` with pass/fail per case.

**Done when:** every case is blocked and the report is committed.

**Built (T8), what differs from the text above:** how to run it and what it covers is in `scripts/README.md`; the result is `docs/reports/phase-2-red-team.md`.

- **Two sandboxes, 66 cases.** `scripts/red_team.py` builds a HIPAA sandbox (`phi = "block"`) and a general one, each with fresh BMAD 6.12.4 and the setup script, in a `TemporaryDirectory` (only that folder is removed; `--keep` keeps it). Each case has an expected answer (block, allow or warn) and an expected rule id. A case passes only when both match.
- **Real entry points.** L1: the hook commands are read from the sandbox's `.claude/settings.json` and run like Claude Code runs them (a shell, `CLAUDE_PROJECT_DIR`, JSON on stdin); a call reaches a hook only if the wiring's matcher covers its tool. L2: real `git commit` and `git push` (to a bare remote in the temp folder). No model session. Reuses the install test's helpers; `Runner.run` got an `extra_env` argument for this.
- **The four rollout-doc cases** (PHI: PHI-1, 2, 3; wrong DB: DB-1, 2; edit after approval: EDIT-4, 1, 2; same-session review: REV-2) are listed on their own in the report. The approved tech-stack list is approved through Warden's real script; the review record is written by the real hook from the build session's id, then pushed.
- **More than asked:** one row per other guard (destructive command, sensitive read, data export, deleted question, `--no-verify`, guard self-protection, secret in a prompt, a written file, a here-document and a commit, ready-for-dev, session-start warning, fail closed on bad input), 19 control rows (close-but-safe actions that must pass), the non-HIPAA warn rows as information, and LEAK-1: no fake value and no command text in any queued event.
- **A case that did not run is NOT RUN**, never PASS, and fails the run. A control row with no matching hook entry fails too.
- **Checked that the suite can fail:** with the write gate taken out of the dispatcher for one run, GATE-1, GATE-2, GATE-3 and EDIT-4 failed, the set-up stopped, and the exit code was 1. The change was reverted.
- **Python:** all 66 cases also pass with the hooks started by Python 3.10.18 (`--python python3.10`). The committed report is the Python 3.13.12 run.
- **No guard bug was found.** No guard file changed in T8.
- **Not covered** (in the report): no live model session, subagent session ids, `permissions.deny` rules, bypass paths, free-text PHI, other OS and tools.

## Gate 2

Phase 3 starts only when all of these are true (checked 2026-11-26):

- [x] Every new hook script has unit tests (happy path, edge, error) and they pass. **Evidence:** `python3 -m unittest discover`: 961 tests OK on Python 3.13.12 and on 3.10.18 (8 skipped on 3.10: the tests that read TOML need 3.11, as before). `uvx ruff@0.17.6 check .` and `format --check .` pass. Note: `guard_hook_lib.py` has no test file of its own; its functions run in every pre-commit and pre-push test (real repositories), including the missing-skill case.
- [x] The red-team report shows all four rollout-doc cases blocked: PHI, wrong DB, edit after approval, same-session review. **Evidence:** `docs/reports/phase-2-red-team.md`, 66 of 66 cases PASS, 4 of 4 rollout cases BLOCKED. Limit: no live model session (see the report's "Not covered").
- [x] The Phase 1 install test still passes, with the hook wiring surviving a BMAD update. **Evidence:** `python3 scripts/install_test.py` on 2026-11-26: 145 passed, 0 failed (it checks the wiring after setup, quick-update, update and the 6.11.0 to 6.12.4 upgrade). `docs/reports/phase-1-install-test.md` is the older Phase 1 run and was not regenerated.
- [x] The managed-settings template and rollout doc exist. **Evidence:** `module/managed-settings/managed-settings.json`, `docs/managed-settings-rollout.md` (template only; nothing is installed in a system path).
- [x] A draft Phase 3 plan exists. **Evidence:** `docs/plans/phase-3.md` (draft, to be grilled and approved).
- [ ] Every item in "Managed settings rollout todos" below has an owner and a date. **Not true yet:** every item has an owner or `TBD` and a `Date: TBD (Shameer to set)`. Two items have no owner named in the plan (uninstall for the wiring; the open `PreToolUse` checks) and the rollback test has only an implied one (Shameer, with the rollout doc test). Shameer sets the dates and fills the owners; then this box can be ticked.

## Managed settings rollout todos (from T1)

From the T1 review (`docs/hooks.md`, `docs/managed-settings-rollout.md`). None of these can be skipped: the managed file applies to **every** project on a machine, and the `PreToolUse` hook fails closed.

**Before the managed file goes on any pilot machine:**

- [ ] **Pilot machine check.** Every Claude Code project on the machine has the module installed and `setup_guard.py --check` says `up to date`. A project without it blocks every tool call. Keep a list of pilot machines and their projects. Owner: Shameer with IT. Date: TBD (Shameer to set).
- [ ] **Claude Code 2.1.295 or later** on every pilot machine (`onFailure: "block"` needs it). Checked version: 2.1.326. Owner: IT. Date: TBD (Shameer to set).
- [ ] **Keep `allowManagedHooksOnly: true` in the pilot.** Tell pilot developers first: it turns off their personal and plugin hooks. Owner: Shameer. Date: TBD (Shameer to set).
- [ ] **Run the rollout doc test** ("How to test", steps 1–8) on one machine and record the results. Owner: Shameer. Date: TBD (Shameer to set).
- [ ] **Rollback tested** on that machine (delete the file, `/status` no longer shows it). Owner: Shameer (same machine test as above). Date: TBD (Shameer to set).

**Before the full IT rollout (open item 3):**

- [ ] **Machine-level script location.** Install the guard scripts once per machine and point the managed hooks there, so projects without the module are not blocked and the scripts cannot be edited per project. Needs an ADR, a setup script change, a template change and an install-test case. Owner: Shameer. Phase 3 at the latest (`docs/plans/phase-3.md`, T1 and T5). Date: TBD (Shameer to set).
- [ ] **Uninstall for the wiring** in the setup script (rollback is by hand today). Owner: TBD (the plan names none; Phase 3, T5). Date: TBD (Shameer to set).
- [ ] **Open `PreToolUse` checks** from `docs/hooks.md` closed: a real block and a crash with `onFailure` seen in Claude Code, the `.env.example` carve-out, a double run when the same hook is in managed and project settings. Owner: TBD (needs a real model session; the plan names none; Phase 3, T5). Date: TBD (Shameer to set).

## Command guard todos (from T2)

- [x] **T3:** block agent writes to `_bmad/custom/project/guard/` (see T3, "Guard config"). Done: `DOC_GUARD_CONFIG`, file tools and best-effort shell writes.
- [ ] **Rule:** `git checkout .` / `git checkout -- .` and `git restore .` (throw away uncommitted work). Needs a close-but-safe allow test (`git checkout -- file`).
- [ ] **Rule:** `UPDATE` without `WHERE` in the SQL rule.
- [ ] **Export rules:** `aws s3api get-object`, `gsutil cp`, `az storage blob download`, `scp` / `rsync` from a remote host.
- [ ] **Real block seen** in Claude Code for one command rule and one path rule (rollout doc, test step 8).
- [ ] **Revisit person-only resolutions** (`DOC_APPROVAL_FIELD` on `Resolved:` lines) with the pilot's false-block numbers. Owner: Shameer.
- [x] **Protect developer-written guard overrides** (`_bmad/custom/prompt-guard.toml`, `_bmad/custom/tool-guard.toml`, and the other guard skills' `.toml`). Done in the T5 review: `DOC_GUARD_SCRIPTS` covers `_bmad/custom/<guard skill>.toml`, so the HIPAA setting (`phi = "block"`) cannot be weakened by the agent.

## Write gate todos (from T4)

- [ ] **Subagent session ids.** Check in a real session whether a hook that runs for a subagent's tool call has the parent's `session_id`, and whether `agent_id` is in its input (`docs/hooks.md`, open check 7). T7 is built without the answer and documents the limit: until it is known the same-session check can not tell a subagent from its parent, and the review must run in a fresh top-level session. Owner: Shameer.
- [x] **`gate: generic` in a separate story file.** Done: `gate: generic` is in `DOC_APPROVAL_FIELD` (T4 review).
- [ ] **`git checkout <path>` / `git restore <path>` through the gate.** Not gated now (false blocks on branch switches). Look again with the pilot's false-block numbers. Owner: Shameer.
- [ ] **`story` field format.** A path or an id is supported. Check with the pilot BAs that this is how they fill it. Owner: Manesh G.
- [ ] **Plan outside `_bmad-output/`** (a renamed output folder, or a spec folder elsewhere) is not found, so every source write blocks. Read `implementation_artifacts` from the BMAD config if a pilot project renames it.
- [ ] **Real block seen** in Claude Code for a `GATE_*` rule and for a Bash write target (rollout doc, test step 8).
- [ ] **False-block numbers** for the Bash gate (tools that write source through an option, not a redirect, are not seen; tools that use a redirect into source are gated). Measure in the pilot.

## Review record todos (from T7)

- [ ] **A real review in Claude Code.** Run `bmad-code-review` in a fresh session: does `workflow.on_complete` run the record command as one plain command, does the hook write the record, and is the `REVIEW_RECORDED` event queued? Also: the record exists when the developer denies the permission prompt (the hook runs first). Needs a real model session. Owner: Shameer.
- [ ] **A real push by a developer** through Claude Code's Bash tool with the hooks path set by the setup script: confirm the block message is shown and the agent does not retry with `--no-verify`. Owner: Shameer.
- [ ] **Rebase, amend and squash merge.** Every rewrite makes the record stale, so the review is run again. Decide in the pilot whether a "re-review a rewritten branch" shortcut is needed (for example a record that names the tree, not the commit). Owner: Shameer.
- [ ] **Forged records.** `python -c` or a script file can still write a record (the shell guard is best effort). A cross-check of `REVIEW_RECORDED` events against the committed records fits Phase 3 (collector). Owner: Shameer.
- [ ] **One review for several stories on one branch.** The record covers every earlier source commit. Check with the pilot teams that this matches how they review. Owner: Manesh G.
- [ ] **False-block numbers** for pre-push in the pilot (README.md and other root files count as source; add `write_gate_allowed_paths` if needed). Owner: Manesh G.
- [ ] **Server-side check** (CI or branch protection) for the same record rule, because `git push --no-verify` skips the hook. Later phase (ADR 0002).

## Content scan todos (from T5)

- [ ] **Real warning seen** in Claude Code: a `CONTENT_PHI` warn in a project that is not HIPAA. Is the `systemMessage` shown, does the model get the `additionalContext` reminder (`docs/hooks.md`, open check 8)? Needs a real model session. Owner: Shameer.
- [ ] **False-block numbers** for the content scan in the pilot (regex PHI on emails and phone numbers in code and fixtures; a Write of a whole file that already holds a flagged value). Tune the allow list in `prompt-patterns.json`. Owner: Manesh G.
- [ ] **Edit: subtract what was already there?** An Edit reads its `new_string` only. If agents often retype a line that holds an old fake, scan the `old_string` too and block only findings that are new. Look at it with the false-block numbers.
- [ ] **A prompt hook input that cannot be parsed** is let through in every project (nothing to check). Decide in the pilot whether a HIPAA project should block it.
- [x] **Bash content gaps:** `sed -i 's/x/KEY/'`, `dd`, a script that writes the file. Done in T6: the pre-commit scan reads the staged lines, so a secret that any of these put into a file is found when the file is committed. A secret that is written and deleted before a commit is still not seen.

## Pre-commit todos (from T6)

- [ ] **Warden and a plain TOML file.** Warden approves through frontmatter, so `tech-stack.toml` starts with a `---` block and is not plain TOML. Either let Warden approve a TOML file (a `status` key), or move the list to Markdown. Owner: Shameer.
- [ ] **Role of the approver.** The hook checks that a Warden entry exists, not that the approver is the architect. Add a role check when Phase 3 brings real identities. Owner: Shameer.
- [ ] **Python version on developer machines.** The hook needs Python 3.10 or later (`#!/usr/bin/env python3`). The macOS system `python3` is 3.9.6 on this machine. The hook already blocks with a plain message ("needs Python 3.10 or later") when it starts on an older one. Still to do: tell the developer at setup time (the setup script, or `SessionStart`), before the first commit fails. Owner: Shameer.
- [ ] **False-block numbers** for the pre-commit scan and for the approval rules in the pilot (lock files are skipped; other generated files may need the allow list). Decide then whether `COMMIT_QUESTION_REMOVED` and `COMMIT_GATE_GENERIC` should block. Owner: Manesh G.
- [ ] **State file path.** The hook reads `_bmad/custom/approval-state.json` only. Read Warden's `state_file` setting if a pilot project moves it.
- [ ] **A real commit by a developer** (not the install test) with the hooks path set by the setup script, through Claude Code's Bash tool: confirm the block message is shown and the agent does not retry with `--no-verify`. Owner: Shameer.

## Open items for later phases

1. **Jira / ADO MCP not connecting (Phase 4, spike).** Sometimes the Jira or ADO MCP does not connect and the agent creates documents without the ticket data. Candidate: a `SessionStart` or `PreToolUse` check that the MCP is reachable before BMAD planning skills run. Owner: Shameer. From the rollout doc, 25 Nov.
2. **`epic.md` source (Phase 4, investigate).** An `epic.md` already exists; how it was created, and whether it differs from the ADO data, is unknown. Owner: Manesh G with one BA lead. From the rollout doc, 25 Nov.
3. **Managed settings rollout by IT / MDM** (after the pilot shows the hooks are stable). Blocked by the "Before the full IT rollout" todos above.
4. **Copilot / Cursor hooks** when they offer blocking hooks.
5. **Credential storage** (Phase 3, ADR 0007; see Phase 1 open item 1). Phase 2 already denies the agent access to `~/.config/bmad-guard/`.

## Risks

| Risk | What we do |
|---|---|
| Hook input or blocking behaviour differs from what we expect in the pinned Claude Code version | T1 day-1 check; record the version in `docs/hooks.md`; the install test fails on a change |
| No clean way to get the session id into a committed record | Hooks write ids (T1, T7); if that fails, the review record uses the hook's own event and pre-push reads it; note the weaker proof in the report |
| False blocks annoy developers (paths, commands, scans) | Project extensions; a close-but-safe allow test for every rule; measure blocks in the pilot (target under 5% false) |
| Write gate blocks normal work (no active plan) | Clear message naming the next step; allowed paths per project; generic stories |
| Hooks bypassed (`--no-verify`, settings edit, setup not run) | Accepted (ADR 0002); agent `--no-verify` blocked by T2; `SessionStart` warns; managed settings and CI later |
| Bash path checks miss obfuscated commands | Best effort; deny rules as a second layer; recorded as a known limit |
| 3 weeks is tight | T7 and the optional tech-stack check are the last to cut; Gate 2 still needs the wrong-DB and same-session cases |

## How we work

- One branch per task, commits `GUARD-2: what changed`.
- Claude commits locally. Shameer pushes and opens the PRs.
- Each PR shows the test command and its result.
