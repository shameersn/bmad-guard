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
| macOS, Linux, or Windows 10 or 11 | | **Windows** is built for the Claude Code (L1) guards only and **has not been run on a real machine yet** (ADR 0009). It needs Git for Windows (Git Bash) and a `python3` that works in Git Bash: see "Windows" in step 4. |
| Python 3.10 or later, as `python3` | `python3 --version` | The macOS system Python is 3.9 and is **too old**: the pre-commit hook blocks every commit and says so. Put a newer `python3` first in your `PATH`. |
| git | `git --version` | |
| BMAD 6.12.4 (exactly) | | Installed in step 3. The guards are tested only with this version. |
| Node.js with `npx` | `npx --version` | Needed by the BMAD installer. Note: the minimum Node version is not checked here; see the BMAD docs. |
| Claude Code 2.1.295 or later, installed and signed in | `claude --version` | The `PreToolUse` hook uses `"onFailure": "block"`, added in 2.1.295 (checked on 2.1.326). With an older version a crashing hook may not block. Do the guarded work in Claude Code: it is the only tool with the L1 guards. |
| GitHub Copilot (optional) | | Only if you also use BMAD from Copilot. Copilot has no blocking hooks, so only the git hooks (L2) guard work done there. |
| `uv` (recommended) | `uv --version` | BMAD skills run `uv run ...` to read their settings. |
| Read access to the bmad-guard GitHub repo | `git ls-remote git@github.com:shameersn/bmad-guard.git` | Ask Shameer for access. You get the code in step 2. |

Use a project that is already a git repository. A new project is fine: run `git init` first.

## 2. Get bmad-guard

bmad-guard is not a package. You clone it once per machine, **outside** your project, and the setup script copies what a project needs into it.

```bash
git clone git@github.com:shameersn/bmad-guard.git ~/tools/bmad-guard
cd ~/tools/bmad-guard && git checkout <pilot tag>
```

- `<pilot tag>`: the version for the pilot. Shameer announces it (for example `pilot-1`). Do not use `main`: every pilot user must have the same version.
- In the steps below `<bmad-guard>` means this folder, `~/tools/bmad-guard`.
- Use `git clone`, not a ZIP download. The setup script reads the version from git and writes it into your project; from a ZIP it records `unknown`, and nobody can tell which version you run.
- One clone serves every project on the machine.

**Update to a new version** when Shameer announces one:

```bash
cd ~/tools/bmad-guard && git fetch --tags && git checkout <new pilot tag>
python3 ~/tools/bmad-guard/scripts/setup_guard.py --project <your project> --check
```

`--check` then says `NOT up to date`. Run the setup script without `--check` (step 4), and commit the result:

```bash
python3 ~/tools/bmad-guard/scripts/setup_guard.py --project <your project>
python3 ~/tools/bmad-guard/scripts/setup_guard.py --project <your project> --check
```

- Run setup again after **every** update. The module in your project is a copy and does not change by itself.
- `--check` must now say `up to date`. If it does not, send the output to Shameer.
- The next tag after `pilot-1` will be `pilot-2`.

## 3. Install BMAD

Run this in the project folder. It is the same command the install test uses.

`--tools` does **not** install Claude Code or Copilot (you have those already, see step 1). It tells the BMAD installer which tools to write BMAD's skills and agents for: `claude-code` writes `.claude/skills/`, `github-copilot` writes `.github/agents/` and `.agents/skills/`. Both are installed so a BMAD agent works the same in either tool (ADR 0001). The guard setup then copies the guard skills into each of those folders.

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

## 4. Run the setup script

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
| `--check` | Exit 1 unless the project is up to date. On Windows also exit 1 when Git Bash or `python3` is not usable. |
| `--force` | Overwrite conflicts and replace `core.hooksPath`. With `--uninstall`: also remove changed files and hook entries that only look like ours. |
| `--uninstall` | Take out what setup added, using the manifest (see section 8). Run it with `--dry-run` first. |
| `--machine-dir DIR` | With `--check`: where the machine folder is (default: the OS path). For IT and tests. |
| `--credentials` | Write your per-user collector file instead of setting up a project (see "Optional: send events to the collector" below). Needs `--endpoint` and `--user`, and cannot be used with `--project`. |
| `--endpoint URL`, `--user ID` | With `--credentials`: the collector URL and your developer id. |

`--dry-run` and `--check` cannot be used together. `--uninstall` goes with `--dry-run` and `--force`, not with `--check`.

With `--check`, the script may print extra lines about your per-user file: `note` (no file; sending is optional), `problem` (the file is not safe to use, with the fix). On Windows it also prints the Git Bash and `python3` lines (`ok` or `problem`, with the fix; a `problem` makes the exit code 1), and a `warning` when `core.autocrlf` is true and the `.gitattributes` lines are missing. It also compares your project with the machine folder that IT may have installed (see "Machine install" below): `warning  machine scripts: <v1>; this project's module: <v2>. They differ` or `note     machine scripts: none (fine unless managed settings point to them)`. None of these lines changes the exit code: you cannot fix a machine folder; tell IT.

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

### Windows (BAs and PMs)

**Status:** built and tested by simulation on a Mac only (ADR 0009). It is **not run on a real Windows machine yet**. One named BA runs the manual check in [windows-check.md](windows-check.md) first. Until that passes, do not say that it works.

What is the same: the Claude Code guards (L1). The setup script, `--check`, the planning guards (Warden, Sentry), the path, command and document guards.

What is different:

- **Git for Windows is required.** Claude Code runs the hooks and the Bash tool in Git Bash. Run every command in this guide in **Git Bash**, not in PowerShell or CMD. `--check` fails with a clear line when `bash.exe` is not found.
- **`python3` must work in Git Bash.** The hook command is `python3 "..."`. The python.org installer gives `python.exe`, not `python3.exe`. Fix: copy `python.exe` to `python3.exe` in the same folder, switch off the Microsoft Store aliases for python (Settings, Apps, Advanced app settings, App execution aliases), and open a new Git Bash. `--check` runs `bash -c 'python3 -c "import sys; print(sys.version_info[:2])"'` and says if `python3` is missing, the Store stub, or older than 3.10, each with this fix.
- **No git hooks (L2).** Setup copies none, sets no `core.hooksPath`, and says so (`note     L2 git hooks are not installed on Windows`). A commit and a push on Windows are not checked by hooks. Developers on a Mac or Linux still have them. The pilot (project form, no admin) uses setup only.
- **The PowerShell tool is blocked** in a guarded project on every OS: the call returns `PS_TOOL_UNSUPPORTED` and the model is told to use the Bash tool. No guard can read PowerShell commands yet (Phase 4). Without this rule the tool would skip every guard.
- **`.gitattributes`.** On every OS, setup adds lines (`text eol=lf`) for the files it installs, so a clone with `core.autocrlf=true` does not turn them into CRLF and change their hashes. `--check` warns when `core.autocrlf` is true and the lines are missing. `--uninstall` removes them.
- **The per-user file** is `%USERPROFILE%\.config\bmad-guard\credentials` (the same logical path). Windows has no mode bits, so `--credentials` runs `icacls` on the folder and the file to give access to your user only. This is best effort: the result is shown and the exit code stays 0. Run it again to repair. The sender does not read the ACL; it only refuses a link or junction. See the next section for the command.
- **No queue lock.** On Windows each hook only appends one line to the queue. The `SessionStart` hook and `guard_events.py flush` send it. Two sessions at the same time can send a line twice; no line is lost.
- **Known limits:** `cmd /c ...` and `powershell -c ...` inside a Bash command are not parsed (like `python3 -c`). A Windows 8.3 short name for a file that does not exist yet is not resolved. A junction is not seen by the setup link check. Native `rd /s`, `del /s` and `Remove-Item -Recurse` through Git Bash are not seen by the recursive-delete rule (Phase 4).
- **Managed settings and the machine install** have a Windows form (`C:/Program Files/ClaudeCode/bmad-guard`, `managed-settings.windows.json`). They are built and unit-tested and **not run on a real machine**. The pilot does not use them (no admin rights).

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

### Optional: send events to the collector

Skip this unless Shameer gives you a **developer id**, a **key** and the **collector URL** (one-time share from the password manager). Without them, events only queue on your laptop, and that is fine.

Run this once per machine, outside any project (the key is never written into a project):

```bash
python3 <bmad-guard>/scripts/setup_guard.py --credentials \
  --endpoint <collector URL> --user <your developer id>
```

- It asks for the key with a hidden prompt. There is no option for the key on the command line, so it does not land in your shell history or in `ps`.
- It writes `~/.config/bmad-guard/credentials` with mode 600 in a folder with mode 700. Run it again with the same values: nothing changes. With different values it stops and asks for `--force` (use that after Shameer rotates your key).
- **Do not `export BMAD_GUARD_KEY` in your shell profile.** A key in the environment is visible to every command the agent runs (`printenv`). The file is the normal way. The environment variables `BMAD_GUARD_ENDPOINT`, `BMAD_GUARD_USER` and `BMAD_GUARD_KEY` still override the file, for CI or a one-off test only.
- If the file is open to group or others, a link, or not yours, the guards **do not use it**: events keep queueing, and `setup_guard.py --project . --check` and the `SessionStart` warning say how to fix it (`chmod 600 ~/.config/bmad-guard/credentials`). Nothing is lost.
- If the collector answers HTTP 401, the warning says whether the key came from the environment or from the file. A stale `BMAD_GUARD_KEY` in your environment hides a new key in the file.
- On Windows the file is `%USERPROFILE%\.config\bmad-guard\credentials`, and setup also runs `icacls` on the folder and the file (best effort). The sender refuses the file only when it or its folder is a link or junction. Not run on a real machine yet.

### Machine install (for IT, not for you)

On a machine with IT's **managed settings**, the Claude Code hooks may run from a root-owned **machine folder** instead of from your project (ADR 0008; macOS `/Library/Application Support/ClaudeCode/bmad-guard/`, Linux `/etc/claude-code/bmad-guard/`). IT installs it with `scripts/install_machine.py` as root, together with the managed file; the steps are in [managed-settings-rollout.md](../managed-settings-rollout.md). You do not run it, and your project's own copies stay (git hooks and the model's scripts use them). What you may notice:

- A project **without** BMAD, or with BMAD but no bmad-guard setup, is not blocked: tool calls work, and a BMAD project without setup shows one line at session start, "bmad-guard is not installed here, the guards are off". Run the setup script to turn the guards on.
- A project **with** the setup manifest (`_bmad/custom/guard/manifest.json`, committed) is guarded as soon as you clone it, even before you run setup. Run setup anyway: it sets `core.hooksPath` for the git hooks.
- Removing the manifest turns the Claude Code guards off for that project. The guard blocks the agent from doing it (`DOC_GUARD_SCRIPTS`); a person can. Do not.
- `setup_guard.py --check` warns when the machine folder and your project's module differ in version. Ask IT to update the machine folder first, then run setup again.

## 5. What setup changes

| Where | What | Why |
|---|---|---|
| `.claude/skills/` and `.agents/skills/` (each one that exists) | 10 guard skills: `agent-approval-gate-check` (Sentry), `agent-approval-grant` (Warden), `agent-project-logger` (Ledger), `confidence-scorer`, `experion-brief-review` (Voss), `experion-frd` (Fenn), `experion-frd-review` (Iris), `prompt-guard` (Marshal), `ready-for-dev-gate`, `tool-guard` | The guard code, and the skills BMAD workflows call. |
| `_bmad/custom/` | Overrides: `bmad-product-brief.toml`, `bmad-prd.toml`, `bmad-architecture.toml`, `bmad-create-epics-and-stories.toml`, `bmad-ux.toml`, `bmad-build.toml`, `bmad-code-review.toml`, `agent-approval-grant.toml`, `agent-approval-gate-check.toml`, `experion-frd.toml` | They add the guard steps to BMAD workflows through BMAD's own override points. |
| `_bmad/custom/guard/hooks/` | The git hooks `pre-commit` and `pre-push` and their helper files | Layer L2. |
| `_bmad/custom/guard/references/` | The shared confidence rubric | Loaded by the planning overrides. |
| `_bmad/custom/templates/` | Nothing. Setup never writes here. | A team may put its own `frd-template.md` here; Fenn then uses it instead of the built-in one. |
| `_bmad/custom/guard/manifest.json` | Module version and a hash of every installed file | So a re-run can tell your edits from ours. |
| git config (local) | `core.hooksPath = _bmad/custom/guard/hooks` | Makes git run the hooks. |
| `.claude/settings.json` | Three hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`) and 21 deny rules (`Read(...)` for `.env`, keys, dumps, `~/.aws`, `~/.ssh`, `~/.config/bmad-guard`). Your own entries are kept. | Layer L1. Skipped when the project has no `.claude/skills` folder. |
| `.gitignore` | The line `.bmad-guard/` | The local event queue is never committed. |

The setup script does **not** create the HIPAA setting (next section) or any file under `_bmad/custom/project/`. Those are yours.

## 6. HIPAA setting

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

## 7. How to read a block message

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

## 8. Roll back (uninstall)

Run these in **your own terminal**, not through Claude: the guard blocks the agent from editing its own files (`DOC_GUARD_SCRIPTS`). Uninstall also turns the Claude Code guards off for this project: a project without the manifest is not guarded (ADR 0008).

```bash
python3 <bmad-guard>/scripts/setup_guard.py --project . --uninstall --dry-run   # read the plan
python3 <bmad-guard>/scripts/setup_guard.py --project . --uninstall             # do it
```

It uses `_bmad/custom/guard/manifest.json` and removes only what setup added:

- the files the manifest lists (the guard skills in `.claude/skills` and `.agents/skills`, the overrides in `_bmad/custom/`, the git hooks), if they are unchanged since install;
- our three hook entries and our 21 `Read(...)` deny rules in `.claude/settings.json`. Your own entries stay. If nothing else was in the file, the file is deleted. A deny rule you had before setup with the same text is removed with ours (setup cannot tell them apart);
- `core.hooksPath`, if it is `_bmad/custom/guard/hooks` (a different value is kept);
- the `.bmad-guard/` line in `.gitignore` (and the comment line setup wrote above it). The same line without that comment is yours and stays, unless you use `--force`;
- `_bmad/custom/guard/`, the manifest last.

It never touches stock BMAD files, your own files, or a path setup does not install. The local event queue `.bmad-guard/`, your approval state (`_bmad/custom/approval-state.json`), the records in `_bmad-output/guard/` and your own settings (`_bmad/custom/prompt-guard.toml`, `_bmad/custom/project/guard/`) are left; delete them yourself if the pilot lead says so.

If any file changed since install, or a hook entry only looks like ours, or something is a link, the script **changes nothing** and lists the conflicts. Look at them. `--force` removes changed files and lookalike hook entries (a link, or a path setup does not install, is never removed). Then commit the result. Check: `git config core.hooksPath` prints nothing, and a new Claude Code session shows no guard hooks.

The by-hand steps below are the same thing, if you cannot run the script.

### By hand

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
            experion-brief-review experion-frd experion-frd-review prompt-guard ready-for-dev-gate tool-guard; do
     git rm -r -q ".claude/skills/$s" ".agents/skills/$s"
   done
   git rm -q _bmad/custom/{agent-approval-gate-check,agent-approval-grant,bmad-architecture,bmad-build,bmad-code-review,bmad-create-epics-and-stories,bmad-prd,bmad-product-brief,bmad-ux,experion-frd}.toml
   git rm -r -q _bmad/custom/guard
   ```

   Remove the overrides together with the skills. The overrides call the guard skills, so one without the other breaks the BMAD workflows.

4. Optional: remove the line `.bmad-guard/` from `.gitignore`, and delete the local queue `.bmad-guard/`.
5. Keep your data, or delete it if the pilot lead says so: `_bmad/custom/approval-state.json` (who approved what), `_bmad-output/guard/` (build and review records), `_bmad/custom/prompt-guard.toml` and `_bmad/custom/project/guard/` (your own settings).
6. Commit.

Check: `git config core.hooksPath` prints nothing, and a new Claude Code session shows no guard hooks. (These steps were run in a throwaway project: the commands succeed and leave `_bmad/custom/config.toml` and the stock BMAD skills untouched.)

## 9. FAQ

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
| Every tool blocked, and the hook script is missing | The project was never set up and IT's managed file is the **first** template (hook scripts inside each project). | Run the setup script. With the machine templates a project without the module is not blocked. |
| Every tool blocked, and the hook script is missing, on a machine with a machine folder | The managed file points to a machine folder that is gone or was never installed (ADR 0008). | Tell IT at once: the managed file must go before the folder does. Do not switch the hooks off. |
| One line at session start: `bmad-guard is not installed here, the guards are off` | BMAD is in the project, but setup was not run (no `_bmad/custom/guard/manifest.json`). | Run the setup script if the project should be guarded. |
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
