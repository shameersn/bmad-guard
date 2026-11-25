# Phase 2 plan: Enforcement

| | |
|---|---|
| Ticket | GUARD-2 |
| Time box | 3 weeks (15 working days) |
| Approvers | Shameer, Manesh G (PR approval) |
| Status | Draft (2026-11-25) |
| Inputs | ADRs 0001–0006; Phase 1 plan and install test; "Guarded BMAD: Org Rollout Plan" (Claude doc) |

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
| L1 | `PreToolUse` Write / Edit | content scan | Secrets; PHI in HIPAA projects |
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

### T6. Git hooks: pre-commit (Days 9–11)

- Plain Python hooks in `module/hooks/`, copied to `_bmad/custom/guard/hooks/` by the setup script. No Husky, no Node.
- `pre-commit`:
  - **scan** staged files with the T5 rules (fail closed);
  - **brief status** (port of `scripts/revert-brief-status.js`): block a commit that changes a document's approval status unless `approval-state.json` holds a matching Warden entry; clear message, no silent revert;
  - **tech stack (optional):** if `_bmad/custom/project/guard/tech-stack.toml` exists and is approved through Warden (Architect), block a new dependency that is not on it. Reads `package.json`, `requirements*.txt` and `pyproject.toml`; other dependency files warn. No list → one info event, no message.
- The hook never waits on the network: events go to the local queue.

**Done when:** tests cover each block, the no-list case, a crash (blocks), and a commit with nothing to check.

### T7. Independent review record and pre-push (Days 11–13)

- `bmad-code-review` override: at the end of a review, run a script that writes a committed review record, e.g. `_bmad-output/guard/reviews/<story>.json`: story id, reviewed commit SHA, reviewer session id, verdict, time.
- The build session id is recorded by the write gate hook (T4) for each story, also in a committed file. Session ids come from hook input (T1 check), never from the model.
- `pre-push`: for each pushed commit that changes source files of a story, block when the record is missing, the reviewer session equals a build session, or the record's SHA is not an ancestor of the pushed head.

**Done when:** tests cover a valid record, a missing record, the same session, and a stale SHA.

### T8. Red-team suite and Gate 2 (Days 13–15)

- `scripts/red_team.py`: in a throwaway sandbox (fresh BMAD 6.12.4 + setup script), drive each case through the real hook entry points with fake data:
  1. **PHI:** a fake patient identifier in a prompt, in a written file and in a commit (HIPAA sandbox).
  2. **Wrong DB:** a sandbox with an approved tech-stack list; adding a database driver not on it is blocked at commit.
  3. **Edit after approval:** change an approved plan, then write a source file: blocked; the AI writing `approval_status: approved`: blocked.
  4. **Same-session review:** a review record with the build session id: push blocked.
  Plus one case per other guard (destructive command, sensitive read, data export, deleted question, `--no-verify` via the agent).
- Output: `docs/reports/phase-2-red-team.md` with pass/fail per case.

**Done when:** every case is blocked and the report is committed.

## Gate 2

Phase 3 starts only when all of these are true:

- [ ] Every new hook script has unit tests (happy path, edge, error) and they pass.
- [ ] The red-team report shows all four rollout-doc cases blocked: PHI, wrong DB, edit after approval, same-session review.
- [ ] The Phase 1 install test still passes, with the hook wiring surviving a BMAD update.
- [ ] The managed-settings template and rollout doc exist.
- [ ] A draft Phase 3 plan exists.
- [ ] Every item in "Managed settings rollout todos" below has an owner and a date.

## Managed settings rollout todos (from T1)

From the T1 review (`docs/hooks.md`, `docs/managed-settings-rollout.md`). None of these can be skipped: the managed file applies to **every** project on a machine, and the `PreToolUse` hook fails closed.

**Before the managed file goes on any pilot machine:**

- [ ] **Pilot machine check.** Every Claude Code project on the machine has the module installed and `setup_guard.py --check` says `up to date`. A project without it blocks every tool call. Keep a list of pilot machines and their projects. Owner: Shameer with IT.
- [ ] **Claude Code 2.1.295 or later** on every pilot machine (`onFailure: "block"` needs it). Checked version: 2.1.326. Owner: IT.
- [ ] **Keep `allowManagedHooksOnly: true` in the pilot.** Tell pilot developers first: it turns off their personal and plugin hooks. Owner: Shameer.
- [ ] **Run the rollout doc test** ("How to test", steps 1–8) on one machine and record the results. Owner: Shameer.
- [ ] **Rollback tested** on that machine (delete the file, `/status` no longer shows it).

**Before the full IT rollout (open item 3):**

- [ ] **Machine-level script location.** Install the guard scripts once per machine and point the managed hooks there, so projects without the module are not blocked and the scripts cannot be edited per project. Needs an ADR, a setup script change, a template change and an install-test case. Owner: Shameer. Phase 3 at the latest.
- [ ] **Uninstall for the wiring** in the setup script (rollback is by hand today).
- [ ] **Open `PreToolUse` checks** from `docs/hooks.md` closed: a real block and a crash with `onFailure` seen in Claude Code, the `.env.example` carve-out, a double run when the same hook is in managed and project settings.

## Command guard todos (from T2)

- [x] **T3:** block agent writes to `_bmad/custom/project/guard/` (see T3, "Guard config"). Done: `DOC_GUARD_CONFIG`, file tools and best-effort shell writes.
- [ ] **Rule:** `git checkout .` / `git checkout -- .` and `git restore .` (throw away uncommitted work). Needs a close-but-safe allow test (`git checkout -- file`).
- [ ] **Rule:** `UPDATE` without `WHERE` in the SQL rule.
- [ ] **Export rules:** `aws s3api get-object`, `gsutil cp`, `az storage blob download`, `scp` / `rsync` from a remote host.
- [ ] **Real block seen** in Claude Code for one command rule and one path rule (rollout doc, test step 8).
- [ ] **Revisit person-only resolutions** (`DOC_APPROVAL_FIELD` on `Resolved:` lines) with the pilot's false-block numbers. Owner: Shameer.
- [ ] **Protect developer-written guard overrides** (`_bmad/custom/prompt-guard.toml`, `_bmad/custom/tool-guard.toml`) that are not in the setup manifest, if the pilot shows agents editing them.

## Write gate todos (from T4)

- [ ] **Subagent session ids.** Check in a real session whether a hook that runs for a subagent's tool call has the parent's `session_id` (`docs/hooks.md`, open check 7). T7 needs the answer before the same-session test means anything. Owner: Shameer.
- [x] **`gate: generic` in a separate story file.** Done: `gate: generic` is in `DOC_APPROVAL_FIELD` (T4 review).
- [ ] **`git checkout <path>` / `git restore <path>` through the gate.** Not gated now (false blocks on branch switches). Look again with the pilot's false-block numbers. Owner: Shameer.
- [ ] **`story` field format.** A path or an id is supported. Check with the pilot BAs that this is how they fill it. Owner: Manesh G.
- [ ] **Plan outside `_bmad-output/`** (a renamed output folder, or a spec folder elsewhere) is not found, so every source write blocks. Read `implementation_artifacts` from the BMAD config if a pilot project renames it.
- [ ] **Real block seen** in Claude Code for a `GATE_*` rule and for a Bash write target (rollout doc, test step 8).
- [ ] **False-block numbers** for the Bash gate (tools that write source through an option, not a redirect, are not seen; tools that use a redirect into source are gated). Measure in the pilot.

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
