# Install guide

| | |
|---|---|
| Ticket | GUARD-3, T0 (pilot pack) |
| For | Pilot developers and BAs |
| Checked | 2026-11-26 on macOS, with BMAD 6.12.4 and Claude Code 2.1.326. Commands and output below were run in a throwaway project. |
| Next | Your role guide: [developer](developer.md), [BA / PM](ba-pm.md), [reviewer](reviewer.md). How it works: [architecture](../architecture.md). |

This guide puts the guards into one project. It takes about 10 minutes.

## What you get

- Guards that run **outside the model** and can block it.
- They check every prompt, every tool call, every commit and every push.
- A block message always tells you the reason (a rule id) and what to do next.
- Nothing is sent anywhere. Events stay on your laptop for now.

## 1. Before you start

| You need | Check with | Notes |
|---|---|---|
| macOS or Linux | | **Windows is not supported yet** (decision D10 in the Phase 3 plan). `setup_guard.py --check` does not warn about Windows yet; that warning is planned (Phase 3, T3). |
| Python 3.10 or later, as `python3` | `python3 --version` | The macOS system Python is 3.9 and is **too old**: the pre-commit hook blocks every commit and says so. Put a newer `python3` first in your `PATH`. |
| git | `git --version` | |
| BMAD 6.12.4 (exactly) | | Installed in step 2. The guards are tested only with this version. |
| Node.js with `npx` | `npx --version` | Needed by the BMAD installer. Note: the minimum Node version is not checked here; see the BMAD docs. |
| Claude Code 2.1.295 or later | `claude --version` | The `PreToolUse` hook uses `"onFailure": "block"`, added in 2.1.295 (checked on 2.1.326). With an older version a crashing hook may not block. |
| `uv` (recommended) | `uv --version` | BMAD skills run `uv run ...` to read their settings. |
| A checkout of this repo (bmad-guard) | | In the steps below its folder is written `<bmad-guard>`. |

Use a project that is already a git repository. A new project is fine: run `git init` first.

## 2. Install BMAD

Run this in the project folder. It is the same command the install test uses.

```bash
npx -y bmad-method@6.12.4 install --directory . --modules bmm \
  --tools claude-code,github-copilot --user-name "<your name>" --yes
```

- Keep the output folder at the default, `_bmad-output`. The write gate looks for plans there only. A renamed folder blocks every source write.
- Your name is used as the approver name when you approve a document with Warden.
- Commit the result before you go on, so the guard setup is a separate commit:

```bash
git add -A && git commit -m "Add BMAD 6.12.4"
```

Already have BMAD 6.12.4 in the project? Skip this step.

## 3. Run the setup script

The script only changes the project you name. It never touches stock BMAD files.

First look at what it will do:

```bash
python3 <bmad-guard>/scripts/setup_guard.py --project . --dry-run
```

Real output (the first lines, then the end; the numbers change with the module version):

```text
module version: 0933758 (installed: none)
create   .claude/skills/agent-approval-gate-check/SKILL.md
create   .claude/skills/agent-approval-gate-check/customize.toml
...
create   .claude/settings.json (add deny rule Read(~/.config/bmad-guard/**))
set      core.hooksPath = _bmad/custom/guard/hooks
append   .gitignore: .bmad-guard/
144 change(s), 0 conflict(s), 0 unchanged
dry run: nothing changed
```

`--dry-run` also prints the planned diff of `.claude/settings.json` before the last line.

If the plan looks right, run it for real:

```bash
python3 <bmad-guard>/scripts/setup_guard.py --project .
```

It ends with `done`. Then check it:

```bash
python3 <bmad-guard>/scripts/setup_guard.py --project . --check
```

```text
module version: 0933758 (installed: 0933758)
0 change(s), 0 conflict(s), 120 unchanged
up to date
```

`--check` changes nothing. It exits 0 when the project is up to date and 1 when it is not (`NOT up to date: run setup_guard.py without --check`). It exits 2 when the folder is not a git repository, has no `_bmad/` folder, or `<bmad-guard>` is not a checkout.

Commit the setup (this commit already goes through the new pre-commit hook):

```bash
git add -A && git commit -m "Add bmad-guard"
```

### All options

These are exactly what `setup_guard.py --help` prints:

| Option | Meaning |
|---|---|
| `--project PROJECT` | Project root (a git repo with `_bmad/`). Required. |
| `--module MODULE` | bmad-guard checkout. Default: the repo that holds the script. |
| `--dry-run` | Print the plan, change nothing. |
| `--check` | Exit 1 unless the project is up to date. |
| `--force` | Overwrite conflicts and replace `core.hooksPath`. |

`--dry-run` and `--check` cannot be used together.

### When setup reports a conflict

The script never overwrites a file that it did not install, or that changed after it installed it. It lists it as a conflict and leaves it alone:

```text
conflict _bmad/custom/bmad-prd.toml (changed since install)
```

Same for a `core.hooksPath` that points somewhere else (for example a Husky folder).

- Look at the file. Did you mean to change it? The guard overrides are owned by the module: put project-specific text in `_bmad/custom/project/<skill>/*.md` instead.
- If you accept losing your change, run with `--force`. Run `--dry-run --force` first and read the plan.
- A project that already uses Husky or another `core.hooksPath` needs a decision first. Ask Shameer.

### After you clone, and after an update

- `core.hooksPath` is local git config, so **every person runs the setup script once after cloning**. The `SessionStart` check warns you if you forget (`SETUP_INCOMPLETE`).
- When the bmad-guard module is updated: `git pull` in `<bmad-guard>`, run setup again, run `--check`, commit the changes.
- Setup can be run again at any time. A second run changes nothing.

### Quick check, no model needed

```bash
git config core.hooksPath
python3 .claude/skills/tool-guard/scripts/write_gate.py --check
```

The first prints `_bmad/custom/guard/hooks`. The second prints a block, because there is no approved plan yet. That is correct:

```text
[tool-guard write gate] Blocked: source changes are not allowed yet:
- GATE_NO_ACTIVE_PLAN (write-gate): No bmad-build plan is in progress, so ...
```

Then start Claude Code in the project folder. No `[tool-guard]` warning at the start means the setup is in place.

## 4. What setup changes

| Where | What | Why |
|---|---|---|
| `.claude/skills/` and `.agents/skills/` (each one that exists) | 8 guard skills: `agent-approval-gate-check` (Sentry), `agent-approval-grant` (Warden), `agent-project-logger` (Ledger), `confidence-scorer`, `experion-brief-review` (Voss), `prompt-guard` (Marshal), `ready-for-dev-gate`, `tool-guard` | The guard code, and the skills BMAD workflows call. |
| `_bmad/custom/` | Overrides: `bmad-product-brief.toml`, `bmad-prd.toml`, `bmad-architecture.toml`, `bmad-create-epics-and-stories.toml`, `bmad-ux.toml`, `bmad-build.toml`, `bmad-code-review.toml`, `agent-approval-grant.toml`, `agent-approval-gate-check.toml` | They add the guard steps to BMAD workflows through BMAD's own override points. |
| `_bmad/custom/guard/hooks/` | The git hooks `pre-commit` and `pre-push` and their helper files | Layer L2. |
| `_bmad/custom/guard/references/` | The shared confidence rubric | Loaded by the planning overrides. |
| `_bmad/custom/guard/manifest.json` | Module version and a hash of every installed file | So a re-run can tell your edits from ours. |
| git config (local) | `core.hooksPath = _bmad/custom/guard/hooks` | Makes git run the hooks. |
| `.claude/settings.json` | Three hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`) and 21 deny rules (`Read(...)` for `.env`, keys, dumps, `~/.aws`, `~/.ssh`, `~/.config/bmad-guard`). Your own entries are kept. | Layer L1. Skipped when the project has no `.claude/skills` folder. |
| `.gitignore` | The line `.bmad-guard/` | The local event queue is never committed. |

The setup script does **not** create the HIPAA setting (next section) or any file under `_bmad/custom/project/`. Those are yours.

## 5. HIPAA setting

The setting is `phi` in the prompt-guard customize file. The default is `warn`. A HIPAA project sets `block`.

Create `_bmad/custom/prompt-guard.toml` **by hand** (the agent cannot write it: rule `DOC_GUARD_SCRIPTS`) and commit it:

```toml
[agent]
phi = "block"
```

| `phi` | A prompt, a file write or a commit with a personal or health identifier | If a guard crashes |
|---|---|---|
| `warn` (default) | Goes ahead. You and the model get a warning. | The prompt or write goes ahead with a warning that it was not checked. |
| `block` (HIPAA) | Blocked: `PROMPT_PHI`, `CONTENT_PHI`, `COMMIT_PHI`. | The prompt or write is blocked. |

- If the setting cannot be read (a broken file), the project counts as HIPAA.
- A personal file `_bmad/custom/prompt-guard.user.toml` may only make an action stricter, never weaker.
- Identifiers the rules know: US SSN, card, NHS and Aadhaar numbers, email and phone. **Names and diagnoses in a sentence are not seen** (regex rules; ADR 0006).
- Use fake data only. A fake value that looks real is blocked in a HIPAA project. Allow-list a fake in `_bmad/custom/project/guard/prompt-patterns.json`:

```json
{"allow": ["MRN-00000000"]}
```

- Values that are already allow-listed: `123-45-6789`, `000-00-0000`, `078-05-1120`, the public card test numbers, `555-01xx` phone numbers, anything with `example`, `@localhost`, `.invalid` or `noreply@`.
- The same file can add project rules: `{"patterns": [{"id": "mrn", "category": "phi", "regex": "MRN-\\d{8}"}]}`.

## 6. How to read a block message

Every block names a **rule id**. The rule id is the reason code. It is the same in the message, in the event and in the rules document.

One message lists **all** the reasons of one call. Fix them all, not only the first.

**A tool call** (Claude Code shows it to the model; real output):

```text
[tool-guard] Blocked Bash:
- CMD_GIT_NO_VERIFY (command-guard): --no-verify skips the git hooks that guard this project. Fix what the hook reports instead.
Do not retry the same call. Change the approach, or ask the developer.
```

Read it as: `- <RULE_ID> (<guard>): <reason>`. A second line with another id means a second reason.

**A prompt** (shown to you; the model never sees the prompt):

```text
Prompt blocked by prompt-guard: phi (phi.us-ssn). Remove that value (or use an obvious fake) and send again. The prompt was not sent.
```

The text after the category is the pattern id (`phi.us-ssn`). The message never repeats your text.

**A commit or a push** (shown in your terminal):

```text
bmad-guard pre-commit: the commit is blocked (1 reason).
- COMMIT_SECRET: secrets in the lines this commit adds: docs/hook-key.txt (secret.github-token). Remove the value ...
Fix this, stage the files again and commit again. Do not use --no-verify.
```

| Rule id starts with | Layer and guard | Where the rules are listed |
|---|---|---|
| `PROMPT_` | L1, prompt-guard. The message shows the category and the pattern id (for example `phi.us-ssn`). The event holds `PROMPT_SECRET`, `PROMPT_PHI`, `PROMPT_INJECTION` or `PROMPT_SIZE`. | [check-prompt.md](../../module/skills/prompt-guard/references/check-prompt.md) |
| `CMD_`, `EXPORT_` | L1, command-guard | [guard-rules.md](../../module/skills/tool-guard/references/guard-rules.md), "Command rules" |
| `PATH_` | L1, path-guard | same, "Path rules" |
| `DOC_` | L1, document-guard and bash-write-guard | same, "Document rules" |
| `GATE_` | L1, write-gate | same, "Write gate" |
| `CONTENT_` | L1, content-scan | same, "Content scan" |
| `REVIEW_` | L1, review-record | same, "Review record" |
| `GUARD_`, `BAD_HOOK_INPUT`, `EXT_BAD_FILE` | L1, the guard itself failed | same |
| `COMMIT_` | L2, pre-commit | [module/hooks/README.md](../../module/hooks/README.md), "pre-commit" |
| `PUSH_` | L2, pre-push | same, "pre-push" |
| `SETUP_INCOMPLETE` | L1, SessionStart (a warning) | [managed-settings-rollout.md](../managed-settings-rollout.md), test step 6 |

Inside a project, the rules document is at `.claude/skills/tool-guard/references/guard-rules.md`. The model can read it too.

## 7. Roll back by hand

**There is no uninstall command yet.** It is planned (Phase 3, T5). Until then, do these steps in **your own terminal**, not through Claude: the guard blocks the agent from editing its own files (`DOC_GUARD_SCRIPTS`).

1. Turn off the git hooks:

   ```bash
   git config --local --unset core.hooksPath
   ```

2. Remove the Claude Code hooks. Open `.claude/settings.json` in an editor and delete:
   - the three entries that run `session_start.py`, `prompt_guard_hook.py` and `pre_tool_use.py` (their commands contain `tool-guard` or `prompt-guard`);
   - the 21 `Read(...)` rules in `permissions.deny`. They are listed in `module/managed-settings/managed-settings.json`.

   Keep your own entries. If the file held only ours, delete the whole file. Check it is still valid: `python3 -m json.tool .claude/settings.json`.

3. Remove the installed files. The list of every file is in `_bmad/custom/guard/manifest.json`. These commands remove them from the working tree and the index. Skip the folder you do not have (`.agents` or `.claude`):

   ```bash
   for s in agent-approval-gate-check agent-approval-grant agent-project-logger confidence-scorer \
            experion-brief-review prompt-guard ready-for-dev-gate tool-guard; do
     git rm -r -q ".claude/skills/$s" ".agents/skills/$s"
   done
   git rm -q _bmad/custom/{agent-approval-gate-check,agent-approval-grant,bmad-architecture,bmad-build,bmad-code-review,bmad-create-epics-and-stories,bmad-prd,bmad-product-brief,bmad-ux}.toml
   git rm -r -q _bmad/custom/guard
   ```

   Remove the overrides together with the skills. The overrides call the guard skills, so one without the other breaks the BMAD workflows.

4. Optional: remove the line `.bmad-guard/` from `.gitignore`, and delete the local queue `.bmad-guard/`.
5. Keep your data, or delete it if the pilot lead says so: `_bmad/custom/approval-state.json` (who approved what), `_bmad-output/guard/` (build and review records), `_bmad/custom/prompt-guard.toml` and `_bmad/custom/project/guard/` (your own settings).
6. Commit.

Check: `git config core.hooksPath` prints nothing, and a new Claude Code session shows no guard hooks. (These steps were run in a throwaway project: the commands succeed and leave `_bmad/custom/config.toml` and the stock BMAD skills untouched.)

## 8. FAQ

### Why was I blocked?

A guard saw something in the call that a rule forbids. The rule id says which one. Find it in the table in section 6 and read the "Blocks" and "What to do" columns. The guard blocks the **agent's action**. It does not judge you.

### What do I do now?

- Do not ask the agent to try the same call again, or to find another way to do the same thing. The message says so too.
- Do the thing the message names: answer a question, approve the plan with Warden, remove the value, use a scratch folder, or do the step by hand in your terminal.
- If the rule looks wrong for your case, **do not work around it**. Write down the rule id and what you tried to do, and send it to Shameer (this is a "false block"; the pilot counts them).

### Can I use `--no-verify`?

No.

- `git commit --no-verify` and `git push --no-verify` are blocked for the agent: `CMD_GIT_NO_VERIFY`. So is `git commit -n` (`CMD_GIT_COMMIT_SKIP_HOOKS`), and so is changing `core.hooksPath` (`CMD_GIT_HOOKS_PATH`).
- The hooks are the second layer. Skipping them removes the secret scan, the PHI scan, the approval checks and the review check for that commit or push.
- A person in a terminal can technically skip a local hook. The design accepts this (ADR 0002), but it is against the pilot rules. A later phase adds a report of "commits with no guard events" and a CI check.
- Fix the cause the hook names instead. If the hook is wrong, tell Shameer.

### The hook crashed, or every tool is blocked

The guards **fail closed**: if a guard cannot run, it blocks. This is on purpose. Common causes:

| What you see | Likely cause | What to do |
|---|---|---|
| `GUARD_ERROR` or `BAD_HOOK_INPUT` | A guard file is missing or damaged, or an update is half done. | Run `--check`, then run setup again. |
| `EXT_BAD_FILE` | `_bmad/custom/project/guard/tool-rules.json` is not valid. | Fix the file by hand. The message names the problem. The agent must not edit it. |
| Every tool blocked, and the hook script is missing | The project was never set up (for example a managed-settings file is on, but this project has no module). | Run the setup script. |
| Every commit blocked, "needs Python 3.10 or later" | `python3` is too old. | Put a newer `python3` first in `PATH`. |
| `COMMIT_ERROR` or `PUSH_ERROR` | A git hook could not run. The message gives a short cause. | Run setup again. |

Steps:

1. Read the message and note the rule id.
2. Run `python3 <bmad-guard>/scripts/setup_guard.py --project . --check`.
3. If it says `NOT up to date`, run the same command without `--check` (use `--dry-run` first).
4. Still blocked? Send the message and the rule id to Shameer. Do **not** switch the hooks off as a fix.

### Does the first push after setup need a review?

No. The files BMAD and setup add outside the planning folders (the root `.gitignore` and `.github/agents/bmad-*.agent.md`) do not count as source, so the push goes through with `PUSH_NOTHING_TO_CHECK`. If your project already had source commits that were never pushed, those still need a review record (`PUSH_NO_RECORD`): run one review in a fresh session ([reviewer guide](reviewer.md)).

### The session starts with a `[tool-guard]` warning

`SETUP_INCOMPLETE`: `core.hooksPath` is wrong, or the hook wiring is missing. Run the setup script. This warning never blocks.

### My fake test value is blocked

In a HIPAA project a value that looks like a real identifier is blocked, even in test data. Use an allow-listed fake, or add yours to `_bmad/custom/project/guard/prompt-patterns.json` (section 5).

### Is my code or my prompt sent anywhere?

No. The guards run on your machine. Events go to `.bmad-guard/events/queue.jsonl` in the project, with rule ids and no content. Sending events to a central dashboard is planned for Phase 3 and is off for the pilot start. See [architecture](../architecture.md), "Events and privacy".

### Does it work with Copilot or Cursor?

No. The pilot supports Claude Code only. A Copilot or Cursor user is covered by the git hooks only.
