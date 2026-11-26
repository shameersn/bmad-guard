# Managed settings rollout

| | |
|---|---|
| Ticket | GUARD-2, T1; GUARD-3, T5 (machine scripts) |
| Status | Built and tested **in temp folders only** (GUARD-3 T5). **Nothing is installed in a system path by this repo.** The real-machine steps (install as root, deploy the managed file, run steps 1 to 12, test the rollback) are the checklist at the end of this page. They need Shameer or IT. |
| Templates | One per OS, written from `hook_wiring.py` (ADR 0008): `module/managed-settings/managed-settings.macos.json` and `managed-settings.linux.json`. Hook scripts run from the machine folder `bmad-guard/` next to the managed file. The first template, `managed-settings.json` (hook scripts inside each project), stays for a pilot machine where every project has the module, until the real-machine test of the machine mode passes. |
| Installer | `scripts/install_machine.py` (as root: copies the scripts, checks them, removes them) |
| Background | `docs/hooks.md` (what the keys do, with sources), ADR 0002, ADR 0008 (machine folder) |

## What it does

Claude Code applies managed settings above every other settings file. A developer cannot override a managed key from `~/.claude/`, the project, or `--settings`. The templates put the same wiring as the setup script into that managed layer, and lock it:

| Key | Effect |
|---|---|
| `hooks` | The three guard hooks: `SessionStart` (setup check), `UserPromptSubmit` (prompt-guard), `PreToolUse` (tool-guard dispatcher). The same hooks as the project wiring. In the machine templates each command is `python3 "<machine folder>/skills/<skill>/scripts/<script>.py"` (an absolute path, no `${CLAUDE_PROJECT_DIR}`); in the first template it is the project path. |
| `permissions.deny` | The sensitive-path deny rules (table below). Deny rules cannot be overridden by allow rules. |
| `env`, `otelHeadersHelper` | Claude Code telemetry to the guard collector (GUARD-3 T4; see "Claude Code telemetry" below). |
| `allowManagedHooksOnly: true` | Only managed hooks run. User, project, local and plugin hooks are switched off, so a developer cannot turn the guard hooks off with `disableAllHooks` in a lower file. |

The deny rules come from one list (`SENSITIVE_PATHS` in `hook_wiring.py`); the `PreToolUse` path guard matches the same list and reports the rule id. Each rule is written `Read(<pattern>)`; a Read deny also blocks Edit and Write on the same path.

| Rule id (hook) | Deny patterns | Stays readable |
|---|---|---|
| `PATH_ENV_FILE` | `.env`, `.env.*`, `*.env` | `.env.example` (`Read(!.env.example)`) |
| `PATH_PRIVATE_KEY` | `*.pem`, `*.key`, `id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*` | |
| `PATH_KEY_STORE` | `*.p12` | |
| `PATH_DB_DUMP` | `*dump*.sql` | |
| `PATH_DB_BACKUP` | `*.sql.bak`, `*.db.bak`, `*.sqlite.bak`, `*.sqlite3.bak`, `*dump*.bak`, `*database*.bak` | |
| `PATH_AWS_DIR` | `~/.aws/**` | |
| `PATH_SSH_DIR` | `~/.ssh/**` | |
| `PATH_GUARD_CONFIG` | `~/.config/bmad-guard/**` | |

The JSON file cannot hold comments, so these tables are the explanation. The test `tests/test_hook_wiring.py` checks that the template and `hook_wiring.py` (used by the setup script) stay equal.

## Claude Code telemetry (GUARD-3 T4)

The `env` block and `otelHeadersHelper` send Claude Code's own events to the guard collector (`deploy/collector/`). Names
and values were checked against the Claude Code monitoring docs (<https://code.claude.com/docs/en/monitoring-usage>)
and a live run of Claude Code 2.1.332 against the local stack (`docs/reports/phase-3-telemetry.md`).

| Key | Value | Why |
|---|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY` | `1` | Required. |
| `OTEL_LOGS_EXPORTER` | `otlp` | Events go to the collector's logs pipeline. |
| `OTEL_METRICS_EXPORTER` | `otlp` | Metrics (sessions, tokens, cost, lines of code, commits, pull requests, tool decisions) go to the collector's metrics pipeline and `guard.otel_metrics_*` (needed for the pilot). |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` | The Claude Code default, set on purpose: the dashboard adds up values. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/json` | What the spike and the live check used. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://REPLACE-WITH-COLLECTOR-HOST` | **IT replaces this** with the real collector URL (no path: Claude Code adds `/v1/logs`). Left as is, exports fail quietly. |
| `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_RAW_API_BODIES` | `0` | Content switches, all explicitly off (they default to off). All five are in the docs for 2.1.332; `OTEL_LOG_TOOL_DETAILS` would put Bash commands in events. Checked live: `prompt`, `prompt_text` and `response` hold `<REDACTED>` or nothing. |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | `false` | Account ids off at the source. The docs say the switch covers metrics and events. The collector deletes them either way. |
| `OTEL_METRICS_INCLUDE_SESSION_ID`, `_VERSION`, `_ENTRYPOINT` | `true` | Not personal. `session.id` also stays on events (the docs: metrics and events). |
| `OTEL_METRICS_INCLUDE_REPOSITORY`, `_RESOURCE_ATTRIBUTES` | `false` | `vcs.*` would name the repository; custom resource keys would become labels on every series. |
| `otelHeadersHelper` | `python3 ".../bmad-guard/otel_headers.py"` | Prints the `Authorization` header from the developer's own credential file or environment. |

**No key in the managed file.** The file is the same for every developer and any local user can read it. A static
`OTEL_EXPORTER_OTLP_HEADERS` would be one shared key, so every event would carry the same `user`. The helper reads each
developer's own key (ADR 0007), so the collector stamps the right user. A machine with no credentials gets "otelHeadersHelper
failed" in `/status` and sends nothing.

**Helper path (built in T5, not tested on a real machine).** The helper sits at the top of the machine folder from
ADR 0008 (`/Library/Application Support/ClaudeCode/bmad-guard/otel_headers.py` on macOS,
`/etc/claude-code/bmad-guard/otel_headers.py` on Linux). `scripts/install_machine.py` puts `otel_headers.py` and
`guard_events.py` there (the helper imports the sender from its own folder), and the macOS and Linux templates point at
them. The unit tests run the helper from a temp folder with a space in its path through the template's
`otelHeadersHelper` command. Not verified: that Claude Code runs the helper from a path with a space on macOS under
managed settings (the live check ran the helper from a project path through `--settings`, which worked).

**Not tested through the managed file.** The live check set the same variables in the child's environment and gave the
helper through `claude --settings <file>`. The managed-file route itself is rollout test step 9 below, on a real machine.

**Why the setup script does not write this to the project.** Decision: no, for the pilot.
- The docs say a repository's `.claude/settings.json` and `settings.local.json` **ignore the OTel exporter variables**, so
  it would not work for the endpoint and protocol.
- A static key in a project file is committed by accident and read by the agent. The helper avoids that, but it is
  only safe in the machine-level folder where the agent cannot edit it.
- Without managed settings, a developer who wants Claude Code telemetry sets the same variables in the shell for that run
  (this is what the live check does). Guard events do not depend on any of this.

## Read this before rolling out

1. **Which template, and what a project without the module gets.**
   - **Machine templates** (`managed-settings.macos.json`, `managed-settings.linux.json`; ADR 0008): the hooks run from the machine folder, so the script is always there. A project without the module is **not blocked**. The hooks first ask whether the project uses the guard: no `_bmad/` folder means allow and say nothing (no event, no file written); `_bmad/` without `_bmad/custom/guard/manifest.json` means allow, and `SessionStart` prints one line ("bmad-guard is not installed here, the guards are off"); only a project with the setup manifest gets the guards, and it fails closed. **Decided for the pilot (2026-11-26): everything is allowed in an unguarded project.** That includes prompts and commands: no secret check, no `git push --force` block. A BMAD project where nobody ran setup has no L1 guard, only the deny rules. After the pilot, one line of code can change the second row to "block with the setup message".
   - **First template** (`managed-settings.json`, project paths): the hook commands run `${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/pre_tool_use.py`. In a project where setup has not been run that file does not exist, Python exits with code 2 and **every tool call is blocked**. Use it only on pilot machines whose Claude Code projects all have the module, and only until the machine mode passes the real-machine test.
2. **The lock covers the entries, and with the machine folder the scripts too.** In the machine mode the scripts are root-owned in the machine folder, so a developer, or the agent running as the developer, cannot edit them per project, and a project cannot change the built-in rules (`customize.toml` is read from the machine copy). The project copies of the guard skills still exist: the git hooks (L2) and the scripts the model runs (`write_gate.py --check`, Warden) use them, and `DOC_GUARD_SCRIPTS` still protects them. A managed `Edit` deny rule for the machine folder as a second layer is deferred (syntax not verified). In the first template the scripts live inside each project and can be edited there.
   - **Order matters.** Install the folder BEFORE the managed file, and remove the managed file BEFORE the folder. A managed file that points at a missing folder blocks every tool call in every project (Python exits with code 2). `install_machine.py --check` warns about it; `--uninstall` refuses while the managed file points there.
   - **`python3` 3.10 or later** must be on the developers' `PATH`: under 3.10 the fail-closed hooks would block every project. `install_machine.py --check` prints the version it sees (as the user who runs it).
   - **Version skew.** The machine folder (L1 hooks) and each project's copies (L2 hooks, the model's scripts) have a version each. Update the machine first, then the projects. `setup_guard.py --check` warns when they differ, and the `SessionStart` hook records an `info` event `MACHINE_VERSION_SKEW`.
3. **`allowManagedHooksOnly` is strict, and the pilot keeps it.** It also switches off hooks developers set up for themselves (personal hooks in `~/.claude/settings.json`, plugin hooks). Decision: it stays on in the pilot too, so the guard hooks cannot be turned off. Tell pilot developers before the file goes on.
4. **Bash is not fully covered by deny rules.** The deny rules reach `Read`, Grep, Glob, Edit, Write and a few analysed Bash commands (`cat`, `head`, redirections). A script that opens a file itself is not covered. The `PreToolUse` command guard and path guard (T2) add checks on the command text (rules and known limits: `module/skills/tool-guard/references/guard-rules.md`); the real limit is an OS sandbox (not in scope).
5. **Claude Code version: 2.1.295 or later is required.** The `PreToolUse` entry uses `"onFailure": "block"`, added in 2.1.295. How older versions treat the key is unverified, so do not deploy to a machine with an older version. Checked version: 2.1.326.

## What IT installs, and where

Two things, in this order:

1. **The machine folder**, from a bmad-guard release (as root, never through sudo inside the script):

   ```bash
   python3 scripts/install_machine.py --dry-run          # read the plan
   python3 scripts/install_machine.py                    # as root: copy, check, rename
   python3 scripts/install_machine.py --check            # exit 0 = right and safe
   ```

   It writes `VERSION`, `manifest.json`, `otel_headers.py`, `guard_events.py` and `skills/<skill>/` for `tool-guard`, `prompt-guard`, `ready-for-dev-gate`, `agent-project-logger` and `agent-approval-gate-check` (scripts and `customize.toml`). Owner root, folders 755, files 644. Updating replaces the whole folder by rename (a bad update: install the previous release again). Pass `--version <v>` when git cannot name the version (a checkout owned by another user).
2. **The managed file for the OS**, copied from the template, with the endpoint placeholder replaced:

| OS | Template | Machine folder | Managed file |
|---|---|---|---|
| macOS | `managed-settings.macos.json` | `/Library/Application Support/ClaudeCode/bmad-guard/` | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux and WSL | `managed-settings.linux.json` | `/etc/claude-code/bmad-guard/` | `/etc/claude-code/managed-settings.json` |
| Windows | `managed-settings.windows.json` (**built and unit-tested, not run on a real machine**: ADR 0009; the pilot has no admin rights and uses the project form) | `C:\Program Files\ClaudeCode\bmad-guard\` (in the hook commands: `C:/Program Files/ClaudeCode/bmad-guard`, forward slashes) | `C:\Program Files\ClaudeCode\managed-settings.json` (the old `C:\ProgramData\ClaudeCode\...` path is not read) |

A site that keeps the folder elsewhere writes its own file: `python3 scripts/install_machine.py --target <folder> --print-template linux > managed-settings.json`. The templates themselves are written the same way (`--print-template macos` and `linux`), from `hook_wiring.py`: one definition, and a test compares each file with it.

Other ways to deliver the same keys, from the Claude Code docs: a macOS configuration profile (domain `com.anthropic.claudecode`, same keys), a Windows registry value (HKLM) through Group Policy or Intune, a `managed-settings.d/` folder for parts owned by different teams, or server-managed settings from the Claude admin console. Source: <https://code.claude.com/docs/en/managed-settings>. Starter files for Jamf, Intune and Group Policy: <https://github.com/anthropics/claude-code/tree/main/examples/mdm>. Only the file form is described and tested here.

A developer with admin rights on the machine can edit or delete the file. MDM tooling that re-deploys it on a schedule is the answer (docs).

## How to test (one machine, one test project)

Use a throwaway git project with fake data, not a real project.

1. In the test project: install BMAD and run `python3 scripts/setup_guard.py --project <test project>` from the bmad-guard checkout. Check `python3 scripts/setup_guard.py --project <test project> --check` says `up to date`.
2. IT (or you, with admin rights) installs the machine folder (`install_machine.py` as root, then `--check`), then copies the template for your OS to the managed file path above and replaces the endpoint placeholder. Check the JSON: `python3 -m json.tool managed-settings.json`. (The first template needs no machine folder, but then every project must have the module.)
3. Start Claude Code in the test project. Run `/status`. The `Setting sources` line must show `Enterprise managed settings (file)`. Run `claude doctor` from a shell: it must list no rejected entries.
4. **Hooks locked:** in `.claude/settings.local.json` of the test project add `{"disableAllHooks": true}` and restart. The guard hooks must still run (step 6 shows it). Remove the line afterwards.
5. **Deny rule:** create a file with fake content named `.env` in the test project and ask Claude to read it. It must be refused. A file named `.env.example` must still be readable (this carve-out is unverified, see `docs/hooks.md` section 8; report what you see).
6. **Hook runs:** in the test project run `git config --local --unset core.hooksPath`, start a new session. A warning from `[tool-guard]` must appear at the start. Restore it with `git config --local core.hooksPath _bmad/custom/guard/hooks`. The event `SETUP_INCOMPLETE` is in `.bmad-guard/events/queue.jsonl`.
7. **No module (machine template):** start Claude Code in a folder with no `_bmad/`. Every tool call must work, and nothing may be written there (`git status` shows nothing new, no `.bmad-guard/`). Then in a folder with an empty `_bmad/` and no `_bmad/custom/guard/manifest.json`: tool calls work and the session start shows one line, "bmad-guard is not installed here". (With the first template this step expects a block on every tool call: limit 1 above.)
8. **Command and path guards (T2):** in the test project ask Claude to run `git push --force` (use a project with no remote, so nothing can happen). Expect a block with `CMD_GIT_FORCE_PUSH (command-guard)` in the message and a `block` event in `.bmad-guard/events/queue.jsonl` that has the rule id and no command text. Then ask it to run `git push` and to read `.env.example`: both must be allowed. Note whether Claude shows the message and stops, or tries another command.

9. **Telemetry (T4):** replace the endpoint placeholder in the deployed file, check that `otel_headers.py` and `guard_events.py` are in the machine folder (`install_machine.py` puts them there), give the developer credentials (`setup_guard.py --credentials`), run `claude -p "say ok"` in the test project. In ClickHouse (or the Grafana panel "Claude Code events by name") there must be `claude_code.*` events with the developer's `user` and no prompt text. With no credentials, `/status` shows "otelHeadersHelper failed" and nothing is sent. Note whether the helper runs from the macOS path with a space (unverified).
10. **Machine folder rights (machine template):**
    - As the developer (not root) try to write into the machine folder, for example `touch "<folder>/x"` and `echo x >> "<folder>/skills/tool-guard/scripts/pre_tool_use.py"`. Expect "permission denied" both times.
    - Write down the owner and mode (`ls -ldn` and `ls -ln` on the folder and a script) on a fresh macOS and a Linux machine: expected root, 755 and 644. Not verified on a fresh macOS today.
    - Run `python3 scripts/install_machine.py --check` as root and as the developer: both exit 0 (as the developer it also shows that nothing can be written), and `python3 --version` as the developer prints 3.10 or later.
11. **Hook time:** in a test project time a `PreToolUse` call from the machine folder (`time python3 "<folder>/skills/tool-guard/scripts/pre_tool_use.py" < call.json`). The folder is not writable, so Python cannot cache bytecode and compiles the scripts on each run. Measured in a temp folder made read-only: about 0.09 to 0.11 s on a Mac (Python 3.13 and 3.10); the hook timeout is 10 s. Write down the number on a pilot Mac.
12. **Version skew:** install a machine folder with a version that differs from the project's manifest (`install_machine.py --version v-test`, then `setup_guard.py --project <test project> --check`). Expect the line `warning  machine scripts: ...`. Start a session and look for the `MACHINE_VERSION_SKEW` event in `.bmad-guard/events/queue.jsonl`.

Write down the Claude Code version and the result of each step.

## How to roll back

Order matters. Never remove the folder first.

1. Delete `managed-settings.json` from the path above (or remove the profile or registry value). Claude Code reloads a changed managed file while running; a new session surely picks it up.
2. `/status` no longer shows the managed source.
3. Then remove the machine folder: `python3 scripts/install_machine.py --uninstall` (use `--dry-run` first). It removes only what its manifest lists, and it refuses while a managed file still points to the folder. A bad update is the other case: install the previous release again (the installer replaces the whole folder by rename).
4. Project wiring in `.claude/settings.json` is not affected, and the projects keep working with their own copies. To remove it from one project: `python3 scripts/setup_guard.py --project <project> --uninstall --dry-run`, read the plan, then run it without `--dry-run` (see `docs/guides/install.md`, section 8).

If a bad file is deployed: a managed file that is not valid JSON makes Claude Code refuse to start (docs: "Managed settings document could not be parsed"). Validate with `python3 -m json.tool managed-settings.json` before deploying.

## Manual steps for Shameer and IT (GUARD-3 T5 checklist)

What is built and tested without admin rights: the installer, the templates, the unguarded-project rule, the fixes, the project uninstall, the install-test cases M-I1 to M-I9 and the red-team cases MACH-1 to MACH-3, MACH-C1, MACH-C2 (all in temp folders). What follows needs root and a real Claude Code session. Do not tick the Gate boxes from this list: record the results first.

- [ ] Install the machine folder as root on one pilot machine: `python3 scripts/install_machine.py`, then `--check` (as root and as the developer). Record the owner and mode of the folder on macOS and on Linux.
- [ ] Deploy the managed file for the OS (replace the endpoint placeholder; validate the JSON). Check `/status` shows `Enterprise managed settings (file)` and `claude doctor` lists no rejected entries.
- [ ] Run test steps 1 to 12 above on that machine, with Claude Code 2.1.295 or later. Write down the Claude Code version and each result.
- [ ] Confirm the three open questions of this task on the real machine: the hooks run from a path with a space on macOS; `allowManagedHooksOnly` still lets the managed hooks run from the machine folder; the telemetry helper runs from the machine folder (step 9).
- [ ] Test the rollback in the right order: remove the managed file, check `/status`, run `install_machine.py --uninstall`, check the folder is gone and a new session in a project with the project wiring still works. Then try `--uninstall` while the managed file is still there: it must refuse.
- [ ] Close the open `PreToolUse` checks in `docs/hooks.md` (open checks 1, 2, 3, 4, 5 and 8) in a real Claude Code session, and write down what you saw (what the model does after a block, how a `systemMessage` shows, exit 2 in `bypassPermissions` mode, and so on, as listed there).
- [ ] Run `python3 scripts/setup_guard.py --project <a real pilot project> --uninstall --dry-run` and read the plan. Run it for real on a throwaway project only.
- [ ] Decide the pilot machine list and who owns the managed file (open question 2), and decide whether the first template (`managed-settings.json`) is retired now that the machine mode has passed the real-machine test.
- [ ] Record the results (a report in `docs/reports/`) and update ADR 0008 from Proposed to Accepted only after the steps above pass and the team approves by PR.

## Open questions for IT and Shameer

1. **Machine-level scripts (built in T5; real-machine test open).** The guard scripts are installed once per machine under the managed folder (`install_machine.py`), the machine templates point the hooks there, and a project without the module is not blocked (the unguarded-project rule). ADR 0008 stays Proposed until the manual checklist above passes on a real machine. Still open: the owner and mode of the folder on a fresh macOS and Linux; a managed `Edit` deny rule for the machine folder (deferred, syntax not verified); whether to run the command and prompt guards in unguarded BMAD projects (not chosen for the pilot); Windows: see the next paragraph.
   **Windows (T9, ADR 0009).** The Windows template (`install_machine.py --print-template windows`: three hooks with forward slashes, the deny rules, `allowManagedHooksOnly`, the telemetry block and `CLAUDE_CODE_USE_POWERSHELL_TOOL=0`) and the installer branch (run from an elevated terminal; no chmod or chown; `--check` reads `icacls` for write rights of Users, Everyone and Authenticated Users, and says "check by hand" when it cannot read the list; `python3` is tested in Git Bash; the renames are retried) are built and unit-tested. They have **not run on a real machine**: `docs/guides/windows-check.md`, step 11. Rollback order is the same: remove `managed-settings.json` first, then `--uninstall`. The registry (HKLM `SOFTWARE\Policies\ClaudeCode`, value `Settings`) is another way to deliver the same keys and is not tested.
2. **Who owns the file and the pilot group?** (Plan open item 3: after the pilot shows the hooks are stable.)
3. **SessionStart check under managed settings (done).** `session_start.py` accepts the wiring from the project `.claude/settings.json` or from the managed file for the OS plus its `managed-settings.d/*.json` drop-ins, read as one. It does not read a macOS configuration profile, the Windows registry or server-managed settings: with those, a project without its own wiring gets a false warning.
4. **Same hook in both places.** With `allowManagedHooksOnly` the project copy is ignored, so no double run. Without it, whether Claude Code runs an identical command twice is unverified.
