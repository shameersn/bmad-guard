# Claude Code hooks: what we rely on

| | |
|---|---|
| Ticket | GUARD-2, T1 (day-1 check) |
| Claude Code version checked | **2.1.326** (`claude --version`, 2026-11-25) |
| Written by | Claude (T1), for review by Shameer and Manesh G |

The Phase 2 guards run as Claude Code hooks. This page records what the hooks do in the version we pin, so a change in a later version is a visible diff. Re-check this page when the pinned version changes.

## How each fact was checked

| Mark | Meaning |
|---|---|
| **Verified (docs)** | Read in the official Claude Code docs (URLs below). Not run by us. |
| **Verified (run)** | Observed on 2.1.326: a throwaway project in a temp folder with a hook that logged its stdin and environment. |
| **Unverified** | Not found in the docs we could read, or not run. Do not build on it without a test. |

What we could not run: the headless test session (`claude -p`) could not call the model ("OAuth session expired and could not be refreshed" in the child process), so **no `PreToolUse` hook has been run by us**. `SessionStart` and `UserPromptSubmit` did run. Every `PreToolUse` item is therefore "Verified (docs)" at best. T2 must run a real blocked call once a model session works (see "Open checks").

Sources (the old `docs.claude.com/en/docs/claude-code/...` URLs answer with a 301 to these):

- Hooks: <https://code.claude.com/docs/en/hooks> (read about two thirds of the page; the last third was not read)
- Settings and precedence: <https://code.claude.com/docs/en/settings>
- Permissions: <https://code.claude.com/docs/en/permissions>
- Managed settings: <https://code.claude.com/docs/en/managed-settings>
- Environment variables: <https://code.claude.com/docs/en/env-vars>
- Settings reference: <https://code.claude.com/docs/en/settings-reference> (first fifth only; the `allowManagedHooksOnly` entry itself was not in the text we got, so its effects come from the hooks page)
- The old `iam` page URL now serves the authentication page, not permissions.

## 1. Hook input (stdin JSON)

| Field | Where | Status |
|---|---|---|
| `session_id` | every event | Verified (run) for SessionStart and UserPromptSubmit; Verified (docs) for PreToolUse |
| `hook_event_name` | every event | same |
| `cwd` | every event | same |
| `transcript_path` | every event | same |
| `permission_mode` | UserPromptSubmit (run); tool events (docs) | Verified (run) for UserPromptSubmit. Not present in the SessionStart input we saw. |
| `prompt_id`, `prompt` | UserPromptSubmit | Verified (run) |
| `source` (`startup`, `resume`, `clear`, `compact`, `fork`) | SessionStart | `startup` Verified (run); the rest Verified (docs) |
| `tool_name`, `tool_input`, `tool_use_id` | PreToolUse | Verified (docs). Example in the docs: `tool_input` for Bash holds `command`, `description`, `timeout`, `run_in_background`. |
| `mcp_server` (`name`, `source`) | PreToolUse for MCP tools, v2.1.274+ | Verified (docs) |
| `agent_id`, `agent_type` | inside a subagent | Verified (docs) |
| `scratchpad_dir`, `effort` | docs list them | Verified (docs), not used by us |

Observed keys on 2.1.326:

- SessionStart: `cwd`, `hook_event_name`, `session_id`, `source`, `transcript_path`.
- UserPromptSubmit: `cwd`, `hook_event_name`, `permission_mode`, `prompt`, `prompt_id`, `session_id`, `transcript_path`.

Design consequence: a guard must treat every field as optional and fail closed when `tool_name` or `tool_input` is missing or the wrong type (T1 dispatcher does).

## 2. How a hook blocks

- **Exit code 2 + stderr: Verified (docs).** Exit 2 blocks `PreToolUse` and `UserPromptSubmit`. The stderr text is shown to the model as the reason (for `UserPromptSubmit` it is shown to the user as the reason). Docs: "If your hook also printed JSON that makes a blocking decision, Claude Code uses that decision's reason instead."
- **JSON, `PreToolUse`: Verified (docs).** Exit 0 and print `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}`. Values: `allow`, `deny`, `ask`, `defer` (`defer` only in `-p` mode). When hooks disagree: `deny` > `defer` > `ask` > `allow`. The top-level `decision` / `reason` for `PreToolUse` still works but is deprecated.
- **JSON, `UserPromptSubmit`: Verified (docs).** `{"decision": "block", "reason": "..."}`. `block` is the only valid value. This is what the Phase 1 prompt hook uses.
- **Other exit codes: Verified (docs).** Any exit code other than 0 and 2, including 1, is a *non-blocking* error: the action goes ahead and a "hook error" note appears. **A crash of a guard script (Python traceback, exit 1) does not block.** Python exits with code 2 when the script file is missing, which blocks.
- **`"onFailure": "block"` on a hook entry: Verified (docs), v2.1.295+.** Turns a failure, timeout or non-2xx result into a block. We set it on the `PreToolUse` entry as a second layer behind the dispatcher's own `try/except`. Its exact behaviour is Unverified (not run).
- **No decision on `SessionStart`, `InstructionsLoaded`, `Setup`:** they cannot block. Verified (docs).
- **Hooks do not override permission rules: Verified (docs).** A matching `deny` rule blocks the call whatever a `PreToolUse` hook returns, and a matching `ask` rule still asks when the hook says `allow`. So deny rules are a real second layer (and a hook cannot weaken them).
- **What we use:** `PreToolUse` guards: exit 2 and one message on stderr, for a verdict and for a failure alike (one mechanism, no JSON to get wrong). A warning that does not block (T5): exit 0 and JSON with `systemMessage` and `hookSpecificOutput.additionalContext` (section 13). `UserPromptSubmit`: the Phase 1 JSON `decision: block`. `SessionStart`: never blocks, exit 0 always.
- Whether exit 2 still blocks when the session runs in `bypassPermissions` mode: **Unverified.**

## 3. `SessionStart` output

Verified (docs):

- Plain text on stdout (exit 0) is added to the model's context.
- `hookSpecificOutput.additionalContext` is added to the model's context at the start of the conversation.
- `systemMessage` in the JSON is a **warning shown to the user**. Our setup check uses only this, so the model's context is not touched.
- Exit 2 on `SessionStart` shows stderr to the user only.
- Verified (run): a `SessionStart` hook that printed `{"systemMessage": ..., "hookSpecificOutput": {...}}` was reported by Claude Code as `outcome: success`, `exit_code: 0`, with our stdout captured. We did not see how the terminal UI draws it.

## 4. Matcher syntax (Verified, docs)

- Empty, omitted or `"*"`: matches all.
- Only letters, digits, `_`, `-`, space, `,`, `|`: an exact name or a list, e.g. `Edit|Write`.
- Anything else: an unanchored JavaScript regular expression, e.g. `^Notebook`, `mcp__memory__.*`.
- On tool events the matcher filters `tool_name`. MCP tools are `mcp__<server>__<tool>`; a bare `mcp__memory` matches nothing.
- Our `PreToolUse` matcher: `Bash|Read|Grep|Glob|Write|Edit|MultiEdit|NotebookEdit` (exact names). `MultiEdit` was added in T3 (section 11). A guard for another tool needs this string changed and the setup script re-run (`--check` reports the old one as out of date).

## 5. Hook entry, timeouts

Verified (docs). Shape inside `.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
  {"type": "command", "command": "...", "timeout": 10, "onFailure": "block"}]}]}}
```

- Fields: `type` (required), `command` (required), `args` (exec form, no shell), `timeout` (seconds), `if`, `statusMessage`, `once`, `async`, `shell`, `onFailure`.
- Default timeout: 600 s for `command` hooks, lowered to 30 s on `UserPromptSubmit`. We set 10 s (`SessionStart`, `PreToolUse`) and 30 s (`UserPromptSubmit`).
- Without `args`, `command` runs through a shell, so `"${CLAUDE_PROJECT_DIR}"` expands. Verified (run): with the command `python3 "${CLAUDE_PROJECT_DIR}/dump.py"` the hook ran. A project path with a space works when the variable is in double quotes (Verified, run, by hand with `sh -c`).
- Hooks from user, project, local and managed settings are all run; their lists merge. Whether two identical commands from two scopes run once or twice: **Unverified.**
- Settings files are reloaded while a session runs, including `hooks` and `permissions` (Verified, docs).

## 6. Environment variables and the session id

| Variable | Result | Status |
|---|---|---|
| `CLAUDE_PROJECT_DIR` | Set in hook processes. Project root where the session started; stays fixed inside a worktree. | Verified (run) and (docs) |
| `CLAUDE_CODE_SESSION_ID` | Set in hook command processes and equal to the hook JSON `session_id`. Also set in Bash-tool subprocesses (docs: it "matches the `session_id` field in the hook JSON input and is updated on `/clear`"). | Hook env equal to `session_id`: Verified (run). Bash tool: Verified (docs); also seen present in the Bash tool of the session that wrote this page (name only; value not compared). |
| `CLAUDE_ENV_FILE` | Path to a shell script that Claude Code runs before each Bash command. Available to `SessionStart`, `Setup`, `CwdChanged`, `FileChanged` hooks only. | Verified (run): set in SessionStart, absent in UserPromptSubmit. Verified (docs). |
| `CLAUDE_SESSION_ID` | Does not exist. | Verified (run): not set in hooks; not in the docs. |
| `CLAUDE_CODE_CHILD_SESSION` | `1` in Bash-tool and hook subprocesses. | Verified (docs) |

### Answer: how does a guard script learn the session id?

1. **A hook script reads `session_id` from its stdin JSON.** This is the source of truth. Verified (run, docs).
2. **A script the model starts through Bash can read `CLAUDE_CODE_SESSION_ID`** (the docs say Bash-tool subprocesses get it). Verified (docs); not run by us.
3. **But the model controls the Bash command.** It can set or override that variable on the command line, so a value read in a model-run script is not proof of identity. This is our reasoning, not a tested result.

**Design rule (kept from the plan): session ids are written by hooks, never typed by the model and never trusted from a model-run script.**

- T7's build record: the `PreToolUse` write-gate hook (T4) writes the build session id from its own stdin.
- T7's review record: a model-run script may *read* `CLAUDE_CODE_SESSION_ID` for convenience, but the pre-push check should compare against ids written by hooks. If a hook cannot write the reviewer id, the review record is weaker proof; say so in the Phase 2 report.
- A new `SessionStart` hook could also write the id to a file, but `PreToolUse` sees the id on every call, so we do not need a second writer.

## 7. Settings precedence and locking

Verified (docs). Order, highest first: **managed** > command line (`--settings`) > project local (`.claude/settings.local.json`) > shared project (`.claude/settings.json`) > user (`~/.claude/settings.json`).

- **Lists merge.** `permissions.allow` / `deny` and `hooks` are combined across files; a lower file cannot remove a higher file's entries.
- **`permissions.deny` in the project file** applies to everyone who clones the repo. `deny` and `ask` rules apply at once; `allow` rules wait until the folder is trusted.
- **Can managed settings lock hooks? Yes, Verified (docs).**
  - `allowManagedHooksOnly: true` (managed only): user, project, local and plugin hooks do not run; only managed hooks run. Its kind is "lock": the strictest value from any managed source applies.
  - `disableAllHooks: true` turns hooks off; a managed hook can only be turned off from the managed level.
  - Managed file paths: macOS `/Library/Application Support/ClaudeCode/managed-settings.json`; Linux and WSL `/etc/claude-code/managed-settings.json`; Windows `C:\Program Files\ClaudeCode\managed-settings.json`. Also a `managed-settings.d/` folder next to it, a macOS configuration profile (domain `com.anthropic.claudecode`) and a Windows registry key. `/status` shows `Setting sources: Enterprise managed settings (file)`.
- **What this means for our wiring in the project file (not managed):** a developer can switch hooks off with `"disableAllHooks": true` in `.claude/settings.local.json` (local is above shared project). Then `SessionStart` does not run either, so it cannot warn about it. This is the bypass we accept in ADR 0002 until managed settings are rolled out. The setup script reports `disableAllHooks: true` found in the *project* file as a conflict; it does not look at the local file.
- **Even with `allowManagedHooksOnly`,** our hook commands run scripts under `${CLAUDE_PROJECT_DIR}/.claude/skills/`, which live in the project and can be edited there. Managed settings lock the *entries*, not the *scripts*. A machine-level script location would close that; it is not in T1 (open question in `docs/managed-settings-rollout.md`).

## 8. Permission deny rules (second layer)

Verified (docs):

- Syntax `Read(pattern)`, gitignore style. Anchors: `//abs/path` (filesystem root), `~/path` (home), `/path` (relative to the settings file's project), `path` or `./path` (relative to the current directory). A bare name such as `Read(.env)` matches at any depth under the current directory, the same as `Read(**/.env)`.
- Deny wins over allow and over a hook's `allow`.
- `Read` deny rules are applied "best effort" to Grep, Glob and `@file` mentions, and also block Edit and Write on the same path.
- In Bash, `Read` / `Edit` deny rules reach only commands Claude Code can analyse (`cat`, `head`, `tail`, `sed`, `tee`, redirections). They do **not** stop `grep -r pattern .` or a Python or Node script that opens the file itself. This is why the command guard (T2) is also needed, and why OS sandboxing would be the real limit.
- A `!` pattern is a gitignore negation and must come after the rule it carves from: `Read(*.env)` then `Read(!sample.env)`. We use `Read(.env.*)` then `Read(!.env.example)`. The negation with our exact bare-name patterns is **Unverified** (not run).

The list we install (`DENY_RULES` in `module/skills/tool-guard/scripts/hook_wiring.py`) is generated from `SENSITIVE_PATHS`, the same list the `PreToolUse` path guard matches (section 10): environment files and variants except `.env.example`, `*.pem`, `*.key`, SSH keys by name (`id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*`), `*.p12`, `*dump*.sql`, database backups (`*.sql.bak`, `*.db.bak`, `*.sqlite.bak`, `*.sqlite3.bak`, `*dump*.bak`, `*database*.bak`), `~/.aws/**`, `~/.ssh/**` and `~/.config/bmad-guard/**`. A test builds a sample file name from every pattern and checks that the hook blocks it.

## 9. What T1 builds on this

| Decision | Based on |
|---|---|
| One dispatcher per event; `PreToolUse` fails closed with exit 2 and `try/except` | §2: exit 1 does not block |
| `"onFailure": "block"` on the `PreToolUse` entry | §2, docs (v2.1.295+); pin Claude Code >= 2.1.295 |
| `UserPromptSubmit` points straight at `prompt_guard_hook.py` | It already speaks the `decision: block` JSON and owns its fail mode (ADR 0006, T5); a dispatcher would add a layer for nothing |
| `SessionStart` warns through `systemMessage` only | §3 |
| Hook command `python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/<skill>/scripts/<script>.py"` | §5, verified with a space in the path |
| Hooks only wired when `.claude/skills` exists in the project | A hook that points at a missing script exits 2 and would block every tool call |
| Session ids come from hook input | §6 |
| Managed template carries `allowManagedHooksOnly: true` | §7 |

## 10. What T2 builds (command and path guards)

Rules, reason codes, design choices and known limits: `module/skills/tool-guard/references/guard-rules.md` (shipped with the skill, so the model can read why it was blocked).

What the hook payloads give the guards (all Verified (docs), see section 1):

| Fact | Used for |
|---|---|
| `tool_input.command` (Bash), `file_path` (Read, Edit, Write), `notebook_path` (NotebookEdit), `path` and `glob` (Grep), `pattern` and `path` (Glob) | What each guard reads. The Grep `pattern` is search text, so it is not checked as a path. |
| `cwd` is in every payload | Relative paths in a command are resolved from it. `CLAUDE_PROJECT_DIR` is the project root (scratch folders and the extension file are relative to it). |
| One hook entry runs every guard in one process | Both guards look at the same call; each reports its own codes and the dispatcher prints them in one message. |
| A guard that raises, or a rule list that cannot be read, blocks (`GUARD_ERROR`) | A bad project extension file is a clear `EXT_BAD_FILE` block instead, with the problem named. |

Limits that matter for the rollout (full list in the rules doc): the guards read command text only. Text built at run time (`$CMD`, `eval "$x"`, `base64 | sh`), script files, `python -c` and remote commands (`ssh`, `kubectl exec`) are not seen. The `permissions.deny` rules and code review are the other layers; an OS sandbox is the real limit and is not in scope.

Not yet seen in a real Claude Code session (same reason as section 2: no model session in our test environment): a `PreToolUse` block by these guards, and how the model reacts to the multi-line stderr message.

## 11. What T3 builds (document guard)

Rules, reason codes, design choices and limits: `module/skills/tool-guard/references/guard-rules.md`.

The hook input of the file-writing tools. The hooks page we read only shows the Bash input, so these field names come from the **strings in the Claude Code 2.1.326 binary** (its tool schemas, permission code and diff code). Mark: **Verified (binary strings), not run.** A change in a later version shows as a failing guard test only if we re-run the check; re-read the fields when the pinned version changes.

| Tool | `tool_input` fields the guard reads |
|---|---|
| `Write` | `file_path`, `content` |
| `Edit` | `file_path`, `old_string`, `new_string`, `replace_all` (optional, default false) |
| `MultiEdit` | `file_path`, `edits`: a list of `{old_string, new_string, replace_all}`, applied in order |
| `NotebookEdit` | `notebook_path`, `new_source`, `edit_mode` (replace, insert, delete), `cell_id`, `cell_type`. The guard reads the path only. |

- **`MultiEdit` exists in 2.1.326.** The binary lists it with Write, Edit and NotebookEdit as a file-writing tool and in the permission rules. Whether the model is offered it by default is **Unverified**. We added it to the matcher and to the path guard and document guard anyway: a missing tool in the matcher would let one call get past every write guard, and an unused matcher entry costs nothing. After this change `setup_guard.py --check` reports the old matcher as out of date until the setup script is run again.
- **How Claude Code applies an edit** (binary): the `old_string` is replaced in the file text, once, or everywhere with `replace_all`; `MultiEdit` repeats that for each edit on the result of the one before. An `old_string` that is not in the file, or is not unique without `replace_all`, fails the edit. An edit with an empty `old_string` on a file that does not exist creates it. The guard does the same replacement on the file on disk to get the whole text before and after.
- **Paths.** The tool schemas ask for an absolute path. The guard also accepts a relative path (resolved from `cwd`) and `~`.

## 12. What T4 builds (write gate)

Rules, reason codes, design choices and limits: `module/skills/tool-guard/references/guard-rules.md` ("Write gate").

What the gate takes from the hook input and from BMAD:

| Fact | Used for | Status |
|---|---|---|
| `session_id` in the `PreToolUse` input | The build session id written to `_bmad-output/guard/builds/<story>.json`. Never `CLAUDE_CODE_SESSION_ID`, never a value the model types (section 6). | Verified (docs) for `PreToolUse`; the field is also in every payload we ran for other events |
| `cwd`, `tool_input.file_path` / `notebook_path` / `command` | Which path a write goes to (relative paths from `cwd`) and the Bash write targets (section 10 and 11) | Verified (docs, binary strings) |
| Plan file: `{implementation_artifacts}/spec-{slug}.md`, with frontmatter `status` set to `in-progress` by step 3 and `in-review` by step 4 | Finding the current plan | Verified by reading `bmm-skills/ship/bmad-build` in BMAD 6.12.4 (`step-01` to `step-04`, `spec-template.md`, `module.yaml`); not run |
| `story`, `approval_status`, `confidence` in the plan's frontmatter | Finding the story and the approval | Our override (`module/overrides/bmad-build.toml`); BMAD's template does not have them |

- **Subagents.** bmad-build hands the code to a subagent. Section 1 lists `agent_id` and `agent_type` for hooks that run inside a subagent, so those calls reach the same `PreToolUse` hook. Whether their `session_id` is the parent's is **Unverified**. The build record then holds whatever id the hook gets; T7 must compare the reviewer's id with it (see open check 7).
- **A block is exit 2 with the reasons on stderr** (section 2), like the other guards. The `--check` command line prints the same lines on stdout and exits 1, so the override can stop before any code is written.

## 13. What T5 builds (content scan, and a warning that does not block)

Rules, fail mode, the Bash content design and limits: `module/skills/tool-guard/references/guard-rules.md` ("Content scan").

**How a `PreToolUse` hook warns without blocking.** Exit 0 and print JSON:

```json
{"systemMessage": "...", "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "..."}}
```

| Part | What it does | Status |
|---|---|---|
| `hookSpecificOutput.additionalContext` | Text added to the model's context next to the tool result, as a system reminder. Supported for `PreToolUse` ("Add context for Claude" section; field table of the PreToolUse decision control). Ignored when `permissionDecision` is `defer`. Not shown in the chat. | **Verified (docs)**, 2026-11-25 (hooks page, read at about two thirds). Not run by us. No minimum version found in the text we read. |
| top-level `systemMessage` | "Warning message shown to the user." A field every event accepts. The page says some events drop it (Setup, InstructionsLoaded, MessageDisplay); the text we read does not say it is dropped for `PreToolUse`. | The field: **Verified (docs)**. That it is shown for `PreToolUse`: **Unverified** (not run, no model session). The model still gets the message through `additionalContext` if the user line is not drawn. |
| `permissionDecision` | **Not sent for a warning.** `allow` "skips the permission prompt" (deny and ask rules still apply). A warning must not approve the write, so the normal permission flow stays as it is. | **Verified (docs)** |

- The same text goes to both fields. All warnings of one call are in one message. When a call has a block and a warning, the call is blocked (exit 2) and the stderr message also lists the warnings (stdout JSON is not read on exit 2).
- **Unverified, to see in a real session:** how the terminal draws a `PreToolUse` `systemMessage`, and that the model reacts to the `additionalContext` reminder (open check 8).
- **Tool input fields the content scan reads** (section 11): Write `content`; Edit `new_string`; MultiEdit `edits[].new_string`; NotebookEdit `new_source`; Bash `command`. Same mark as section 11: strings in the 2.1.326 binary, not run.
- **`UserPromptSubmit` was already wired** by T1 (`hook_wiring.HOOKS`, the managed-settings template, the setup script and `--check`). T5 changed only the hook script: a crash now blocks in a HIPAA project (JSON `decision: block`, exit 0) and warns elsewhere (section 2).

## Open checks (to close in T2 or the pilot)

1. Run one real blocked `PreToolUse` call (exit 2, stderr reaches the model) on 2.1.326, and one crash with `onFailure: "block"`. Needs a working model session.
2. Confirm exit 2 blocks in `bypassPermissions` mode.
3. Confirm the `Read(!.env.example)` carve-out works with the bare-name patterns.
4. Confirm whether the same hook in managed and project settings runs once or twice (the template sets `allowManagedHooksOnly`, which should make this moot).
5. See how the terminal UI shows a `SessionStart` `systemMessage`.
6. Read the remaining third of the hooks page and the full settings reference for anything that changes the above.
7. Check whether a hook that runs for a subagent's tool call gets the parent's `session_id` or its own (T4 records it as the build session, T7 compares the reviewer's id with it). Needs a real model session.
8. See a real `PreToolUse` warning (`CONTENT_PHI` in a project that is not HIPAA) in Claude Code: is the `systemMessage` shown to the user, and does the model get the `additionalContext` reminder? Needs a real model session. (T5)
