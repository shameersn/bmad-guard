# Windows check (manual, on a real machine)

| | |
|---|---|
| Ticket | GUARD-3, T9 (Windows L1 hooks) |
| For | One named BA with a Windows 10 or 11 machine, and Shameer or a developer to help |
| Status | **Not run yet.** Nothing in this guide has been done on a Windows machine. |
| Why | Windows support is built and tested only by simulation on a Mac (ADR 0009: `os.path` as ntpath, a patched platform, fake `icacls`). Simulation checks our string logic. It does not show that anything works on Windows. |
| Next | When every step below passes, ADR 0009 can move from Proposed to Accepted (team approval by PR). |

Use a **fake project** and fake data. Never real client data.

How to use this page:

- Do the steps in order. Write what you saw in the **Result** column of the table at the end.
- **VERIFY** marks a thing we could not know without a Windows machine. Write the exact answer, also when it is "as expected".
- If a step fails, stop and send the output to Shameer. Do not work around it.

## 1. Tools

Install Git for Windows (it brings Git Bash), Node.js and Python 3.10 or later. Claude Code runs hooks and the Bash tool in Git Bash.

Make `python3` work in Git Bash. The python.org installer gives `python.exe` and the `py` launcher, not `python3`. Windows also has a Store alias that only prints a hint.

1. In a normal terminal: `where python`. Note the folder of the first real `python.exe`.
2. Copy `python.exe` to `python3.exe` in that same folder.
3. Turn off the Store alias: Settings, Apps, Advanced app settings, App execution aliases, switch off the two `python` entries.
4. Open a **new** Git Bash and run:

```bash
python3 --version
bash -c 'python3 -c "import sys; print(sys.version_info[:2])"'
```

**Expect:** `Python 3.10` or later, and a line like `(3, 12)`.

- **VERIFY:** which Python and Git the BA has (version numbers); that the Store alias stub no longer answers; that a new Git Bash finds `python3` without a restart of Claude Code.

## 2. Install and setup

In Git Bash, in the fake project (a git repo with BMAD 6.12.4 installed, see the [install guide](install.md)):

```bash
python3 <bmad-guard>/scripts/setup_guard.py --project . --dry-run
python3 <bmad-guard>/scripts/setup_guard.py --project .
python3 <bmad-guard>/scripts/setup_guard.py --project . --check
git config core.hooksPath
```

**Expect:**

- The dry run says `note     L2 git hooks are not installed on Windows (the Claude Code hooks are)` and plans `.gitattributes` lines.
- No `core.hooksPath` is set (the last command prints nothing), and `_bmad/custom/guard/hooks` does not exist.
- `--check` prints `ok       Git Bash: <path>`, `ok       python3 in Git Bash: 3.x`, `up to date`, and exits 0 (`echo $?`).
- No line says "not supported".

If `bash.exe` is not found, `--check` says so and exits 1. Set `CLAUDE_CODE_GIT_BASH_PATH` to the `bash.exe` of Git for Windows and run it again.

- **VERIFY:** that `--check` finds Git Bash by itself; the exit code; that setup writes `.claude/settings.json` with `PowerShell` in the matcher (`Bash|PowerShell|Read|...`).

## 3. What Claude Code sends (log one payload)

We do not know the path forms in the hook input on Windows. Find out once, with a throwaway hook.

1. By hand, add to `.claude/settings.local.json` of the fake project a `PreToolUse` hook that appends its input and `CLAUDE_PROJECT_DIR` to a file outside the project, for example `~/hook-log.txt`. (Use a short `python3 -c` command. The agent cannot edit this file: the guard blocks it. You do it.)
2. Start Claude Code. Ask it to read `README.md` and to write a small file `notes.txt`.
3. Open `~/hook-log.txt`. Note the form of `cwd`, `tool_input.file_path` and `CLAUDE_PROJECT_DIR`: `C:\...`, `C:/...` or `/c/...`.
4. **Remove the hook and the log file.**

- **VERIFY:** the three forms. The guards handle all three (`winpaths.py`), but only a real payload proves which one arrives.

## 4. Start and `/status`

Start Claude Code in the fake project. Run `/status`.

**Expect:** the project settings are listed; no `[tool-guard]` warning at the start (no `SETUP_INCOMPLETE`).

- **VERIFY:** that SessionStart is silent. If it warns about `core.hooksPath`, that is a bug (it must not on Windows).

## 5. Blocks

In Claude Code, ask for each action. A block shows a rule id.

| Ask for | Expect |
|---|---|
| Read `.env.local` (create the fake file first) | `PATH_ENV_FILE` |
| Write `_bmad\custom\approval-state.json` | `DOC_APPROVAL_STATE` |
| Bash: `cat /c/Users/<you>/.ssh/id_rsa` | `PATH_SSH_DIR` (the file need not exist) |
| Bash: `cat $USERPROFILE/.aws/credentials` | `PATH_AWS_DIR` (Git Bash expands the variable at run time; the guard reads the text) |
| Read `.ENV` (upper case) and `.env.` (trailing dot) | `PATH_ENV_FILE` |
| A call to the **PowerShell tool** (for example `Get-ChildItem`) | `PS_TOOL_UNSUPPORTED`, with the message to use the Bash tool |

- **VERIFY:** that every one blocks; that the PowerShell tool is called `PowerShell` in the hook input (`tool_name`); whether the PowerShell tool is on by default for your account; that the Bash tool really runs in Git Bash.
- Known limit (not a bug): `cmd /c ...` and `powershell -c ...` typed inside a Bash command are not parsed (same class as `python3 -c`, ADR 0002).

## 6. The planning flow (the goal of T9)

Do the BA path of the [BA and PM guide](ba-pm.md): brief, then PRD, with Warden and Sentry.

1. Write the product brief. Approve it with `agent-approval-grant` (Warden). Answer both confirmations.
2. Start the PRD. `agent-approval-gate-check` (Sentry) must allow it.
3. Edit the approved brief. The next gate check must block (`stale` or the hash changed).
4. Reopen the brief, approve again, and the gate allows.

**Expect:** the same answers as on a Mac. Warden's and Sentry's scripts run with `python3` from the skill folders.

- **VERIFY:** that Warden and Sentry run without an encoding error (names with an accent are a good test); that the approval state file is read and written with LF.

## 7. Credential file and `icacls`

Run once, outside any project, with a fake collector URL and a fake developer id:

```bash
python3 <bmad-guard>/scripts/setup_guard.py --credentials --endpoint https://collector.example.invalid --user dev-a
```

Type a fake key at the hidden prompt. Then, in a normal terminal:

```text
icacls "%USERPROFILE%\.config\bmad-guard"
icacls "%USERPROFILE%\.config\bmad-guard\credentials"
```

**Expect:** the setup output has `acl      folder: only <you> has access (icacls)` and the same for the file; `icacls` lists only your user; the project `--check` has no `problem` line. Asking Claude to read the file is blocked (`PATH_GUARD_CONFIG`).

- **VERIFY:** that `icacls ... /inheritance:r /grant:r "<user>:(OI)(CI)F"` works as written; what `%USERNAME%` is (a domain account?); that a second run with the same values prints `keep` and runs `icacls` again; that HOME in Git Bash (`echo $HOME`) can differ from `%USERPROFILE%` and the sender still finds the file.

## 8. Events: queue, then flush

1. Cause a block (step 5). Look at `.bmad-guard\events\queue.jsonl`: one line per event.
2. Set a collector. For a test, use the local stack in `deploy/collector/` or any fake receiver, and put its URL in the credential file (step 7).
3. Run: `python3 .claude/skills/agent-project-logger/scripts/guard_events.py flush`.

**Expect:** the JSON answer has `"sent": N`, the queue is empty afterwards. A new Claude Code session also sends the queue once at its start.

- **VERIFY:** the queue file has one line per event and is readable. There is **no lock** on Windows: `emit()` only appends one line and `flush` sends. Run two Claude Code windows at once in the same project, cause several blocks in both, flush, and count: no event may be lost (a line may be sent twice).

## 9. CRLF clone

Git for Windows often sets `core.autocrlf=true`. A clone then turns files into CRLF, and the setup manifest holds hashes of the bytes.

```bash
git add -A && git commit -m "Add bmad-guard"
git clone -c core.autocrlf=true . ../clone-crlf
cd ../clone-crlf
python3 <bmad-guard>/scripts/setup_guard.py --project . --check
```

**Expect:** `up to date`. No file is named as changed. The `.gitattributes` lines that setup wrote keep the installed files LF.

- **VERIFY:** the result. Also check a stock BMAD file: `git status` in the clone must show no change from line endings.

## 10. Hook timing (Defender)

Windows Defender can slow the start of a Python hook. Measure it:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Read","tool_input":{"file_path":"README.md"},"cwd":"."}' > /tmp/in.json
time (python3 .claude/skills/tool-guard/scripts/pre_tool_use.py < /tmp/in.json)
```

Run it three times, and once more after a restart (cold start). **Expect:** well under 1 second. The hook timeout is 10 seconds, and a hook that times out **blocks** the call.

- **VERIFY:** write the four times. If the cold start is near 10 seconds, tell Shameer: we may exclude the project folder from Defender or raise the timeout.

## 11. Managed install (only if you have admin rights)

The pilot does **not** need this. The managed template and the installer branch are built and unit-tested, and not run on a real machine. Do this step only on a test machine.

In an elevated terminal (Run as administrator), from the bmad-guard checkout:

```text
python3 scripts\install_machine.py --dry-run
python3 scripts\install_machine.py
python3 scripts\install_machine.py --check
```

1. **Expect:** the folder `C:\Program Files\ClaudeCode\bmad-guard` exists; `--check` says `Users, Everyone and Authenticated Users have no write access (icacls)` and `python3 is 3.10 or later` (it tests python3 in Git Bash).
2. Print the template: `python3 scripts\install_machine.py --print-template windows`. Replace the collector placeholder. Put it at `C:\Program Files\ClaudeCode\managed-settings.json`.
3. Restart Claude Code. `/status` shows the managed settings; the hooks run from `C:/Program Files/ClaudeCode/bmad-guard/...`; the PowerShell tool is off (`CLAUDE_CODE_USE_POWERSHELL_TOOL=0`).
4. **Rollback order:** remove `managed-settings.json` **first**, then `python3 scripts\install_machine.py --uninstall`. A managed file that points to a missing folder blocks every tool call in every project.

- **VERIFY:** every line above, in particular: `icacls` output in a language other than English (`--check` reads English names and says "check it by hand" when it cannot read the list); that the hook command with a space in `C:/Program Files/...` runs under `allowManagedHooksOnly`; that the rename step works while Defender scans the new files.

## Results

| # | Step | Result (pass or fail, and what you saw) | Date and who |
|---|---|---|---|
| 1 | Tools, `python3` in Git Bash | | |
| 2 | Setup, `--check` | | |
| 3 | Hook payload path forms | | |
| 4 | Start, `/status`, SessionStart silent | | |
| 5 | The six blocks, PowerShell tool | | |
| 6 | Planning flow, brief to PRD | | |
| 7 | Credential file and `icacls` | | |
| 8 | Events: queue, flush, two windows | | |
| 9 | CRLF clone with `core.autocrlf=true` | | |
| 10 | Hook timing (Defender) | | |
| 11 | Managed install (optional) | | |

## Questions for the BA's manager

Recorded in the Phase 3 plan. Which Python and Git do the BAs have? Are there PowerShell-only machines (no Git for Windows)? PowerShell-only hosts are out of scope for T9.
