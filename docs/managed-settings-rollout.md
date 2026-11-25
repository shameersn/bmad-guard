# Managed settings rollout (template only)

| | |
|---|---|
| Ticket | GUARD-2, T1 |
| Status | Template and instructions only. **Nothing is installed in a system path by this repo.** The real rollout is a later step with IT (Phase 2 plan, open item 3). |
| Template | `module/managed-settings/managed-settings.json` |
| Background | `docs/hooks.md` (what the keys do, with sources), ADR 0002 |

## What it does

Claude Code applies managed settings above every other settings file. A developer cannot override a managed key from `~/.claude/`, the project, or `--settings`. The template puts the same wiring as the setup script into that managed layer, and locks it:

| Key | Effect |
|---|---|
| `hooks` | The three guard hooks: `SessionStart` (setup check), `UserPromptSubmit` (prompt-guard), `PreToolUse` (tool-guard dispatcher). Same commands as the project wiring. |
| `permissions.deny` | The sensitive-path deny rules: the environment file and its variants except the example, `*.pem`, `*.key`, `~/.config/bmad-guard/**`. Deny rules cannot be overridden by allow rules. |
| `allowManagedHooksOnly: true` | Only managed hooks run. User, project, local and plugin hooks are switched off, so a developer cannot turn the guard hooks off with `disableAllHooks` in a lower file. |

The JSON file cannot hold comments, so this table is the explanation. The test `tests/test_hook_wiring.py` checks that the template and `hook_wiring.py` (used by the setup script) stay equal.

## Read this before rolling out

1. **Fail closed means a project without the module blocks.** Managed settings apply to every project on the machine. The hook commands run `${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/pre_tool_use.py`. In a project where the setup script has not been run, that file does not exist: Python exits with code 2 and **every tool call is blocked**. Roll out only to pilot machines whose Claude Code projects all have the module installed. The machine-level script location (open question 1) is required before the full rollout. Both are todos in the Phase 2 plan ("Managed settings rollout todos").
2. **The lock covers the entries, not the scripts.** The scripts live inside each project and can be edited there. Managed settings stop a developer from removing the hook; they do not stop an edit of `pre_tool_use.py`. Git review of `.claude/skills/` and the setup script's `--check` are the control for now.
3. **`allowManagedHooksOnly` is strict, and the pilot keeps it.** It also switches off hooks developers set up for themselves (personal hooks in `~/.claude/settings.json`, plugin hooks). Decision: it stays on in the pilot too, so the guard hooks cannot be turned off. Tell pilot developers before the file goes on.
4. **Bash is not fully covered by deny rules.** The deny rules reach `Read`, Grep, Glob, Edit, Write and a few analysed Bash commands (`cat`, `head`, redirections). A script that opens a file itself is not covered. The command guard (T2) adds checks; the real limit is an OS sandbox (not in scope).
5. **Claude Code version: 2.1.295 or later is required.** The `PreToolUse` entry uses `"onFailure": "block"`, added in 2.1.295. How older versions treat the key is unverified, so do not deploy to a machine with an older version. Checked version: 2.1.326.

## What IT installs, and where

One file, `managed-settings.json`, copied from the template:

| OS | Path |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux and WSL | `/etc/claude-code/managed-settings.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-settings.json` (the old `C:\ProgramData\ClaudeCode\...` path is not read) |

Other ways to deliver the same keys, from the Claude Code docs: a macOS configuration profile (domain `com.anthropic.claudecode`, same keys), a Windows registry value (HKLM) through Group Policy or Intune, a `managed-settings.d/` folder for parts owned by different teams, or server-managed settings from the Claude admin console. Source: <https://code.claude.com/docs/en/managed-settings>. Starter files for Jamf, Intune and Group Policy: <https://github.com/anthropics/claude-code/tree/main/examples/mdm>. Only the file form is described and tested here.

A developer with admin rights on the machine can edit or delete the file. MDM tooling that re-deploys it on a schedule is the answer (docs).

## How to test (one machine, one test project)

Use a throwaway git project with fake data, not a real project.

1. In the test project: install BMAD and run `python3 scripts/setup_guard.py --project <test project>` from the bmad-guard checkout. Check `python3 scripts/setup_guard.py --project <test project> --check` says `up to date`.
2. IT (or you, with admin rights) copies the template to the path above for your OS.
3. Start Claude Code in the test project. Run `/status`. The `Setting sources` line must show `Enterprise managed settings (file)`. Run `claude doctor` from a shell: it must list no rejected entries.
4. **Hooks locked:** in `.claude/settings.local.json` of the test project add `{"disableAllHooks": true}` and restart. The guard hooks must still run (step 6 shows it). Remove the line afterwards.
5. **Deny rule:** create a file with fake content named `.env` in the test project and ask Claude to read it. It must be refused. A file named `.env.example` must still be readable (this carve-out is unverified, see `docs/hooks.md` section 8; report what you see).
6. **Hook runs:** in the test project run `git config --local --unset core.hooksPath`, start a new session. A warning from `[tool-guard]` must appear at the start. Restore it with `git config --local core.hooksPath _bmad/custom/guard/hooks`. The event `SETUP_INCOMPLETE` is in `.bmad-guard/events/queue.jsonl`.
7. **No module:** start Claude Code in a folder where the module is not installed. Expect every tool call to be blocked with a message (limit 1 above). This is the reason for a pilot group first.

Write down the Claude Code version and the result of each step.

## How to roll back

1. Delete `managed-settings.json` from the path above (or remove the profile or registry value). Claude Code reloads a changed managed file while running; a new session surely picks it up.
2. `/status` no longer shows the managed source.
3. Project wiring in `.claude/settings.json` is not affected. To remove it too, delete our entries from that file by hand; the setup script has no "uninstall" yet.

If a bad file is deployed: a managed file that is not valid JSON makes Claude Code refuse to start (docs: "Managed settings document could not be parsed"). Validate with `python3 -m json.tool managed-settings.json` before deploying.

## Open questions for IT and Shameer

1. **Machine-level scripts.** Install the guard scripts once per machine (for example under the managed folder) and point the hooks there, so the scripts cannot be edited per project and projects without the module are not blocked. Needs a decision and a change to the setup and install test. Decision: required before the full IT rollout (Phase 2 plan todo).
2. **Who owns the file and the pilot group?** (Plan open item 3: after the pilot shows the hooks are stable.)
3. **SessionStart check under managed settings (done).** `session_start.py` accepts the wiring from the project `.claude/settings.json` or from the managed file for the OS plus its `managed-settings.d/*.json` drop-ins, read as one. It does not read a macOS configuration profile, the Windows registry or server-managed settings: with those, a project without its own wiring gets a false warning.
4. **Same hook in both places.** With `allowManagedHooksOnly` the project copy is ignored, so no double run. Without it, whether Claude Code runs an identical command twice is unverified.
