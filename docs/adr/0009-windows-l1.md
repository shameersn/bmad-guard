# 0009 — Windows support for the Claude Code (L1) layer

- **Status:** Proposed (2026-11-26). Built and tested by simulation on macOS in T9. It is not Accepted until a named BA runs the manual check on a real Windows machine (`docs/guides/windows-check.md`) and the team approves by PR.
- **Date:** 2026-11-26
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR
- **Review:** Shameer approved the design and the five decisions below on 2026-11-26.

## Context
The pilot has BAs and PMs on Windows (Phase 3 plan, D11). D10 left Windows out. T9 brings in the Claude Code hooks (L1) only: the planning flow, brief to PRD with Warden approval. Git hooks (L2) stay out.

The guard code was written and tested on macOS and Linux. Reading it for Windows found four hard blockers and many smaller POSIX assumptions:
1. `PRE_TOOL_MATCHER` has no `PowerShell`. On Windows the PowerShell tool is on by default for claude.ai accounts (opt-in on macOS and Linux). A call to it skips every L1 guard, including the one for `approval-state.json`. This is a gap on every OS today.
2. `document_guard.Zones` builds zones with `/` and compares them to `\` paths. Every `DOC_*` zone is off on Windows.
3. `path_guard` splits a file name on `/` only. `C:\proj\.env.local` is not matched.
4. `read_credential_file` checks POSIX mode bits. On Windows they always look open, so the credential file is always refused.
5. The skills tell the agent to run `python3 ...`, and `python3` does not exist on a normal Windows install.

Also found: MSYS paths from Git Bash (`/c/Users/x`) join to `C:\c\Users\x` and miss the folder rules; Windows paths are case-insensitive; NTFS has `file::$DATA`, and a trailing dot or space is ignored; `fcntl` is missing, so the queue has no lock; text files default to cp1252; `core.hooksPath` and the exec bit do not work the same way; CRLF changes the hash of a skill file.

Constraints:
- POSIX behaviour must not change. All existing tests stay as they are.
- Fail closed stays. No guard allows more than before.
- Python 3.10, standard library only.
- No admin rights on pilot machines.
- We have no Windows machine in the loop yet. Tests run by simulation (`ntpath`, patched `sys.platform`, `os.name`, `IS_WINDOWS`).

## Options
### Decision 1 — the interpreter and the shell
**Option A — Python 3.10+ and Git for Windows; the hook command stays `python3`.**
- Claude Code runs Bash-tool commands and hook commands through Git Bash on Windows.
- Pros: one hook command everywhere; the skills and the templates do not change; the shell parser fits (Git Bash is POSIX).
- Cons: `python3` must resolve in Git Bash. The python.org installer gives `python.exe` and the `py` launcher, and the Store app alias is a stub. The fix is to copy `python.exe` to `python3.exe` next to it. `--check` tests this.

**Option B — a different command per OS (`python` or `py -3`).** Pros: no copy step. Cons: the hook command, the template and every skill text differ per OS; the machine template test doubles; a user who has both gets the wrong one.

**Option C — PowerShell hooks.** Cons: all guard parsing is for POSIX shells; PowerShell-only hosts are rare here; large work. Phase 4 at the earliest.

### Decision 2 — the PowerShell tool
**Option A — block it in guarded projects on every OS** (new guard, code `PS_TOOL_UNSUPPORTED`). The managed Windows template also sets `CLAUDE_CODE_USE_POWERSHELL_TOOL=0`.
- Pros: closes the gap now; simple; the agent is told to use the Bash tool. No guard can read PowerShell today (`bash_write_guard` knows no PowerShell or cmd write verbs).
- Cons: PowerShell stays unusable in guarded projects until Phase 4.

**Option B — leave it open and document it.** Cons: one tool call skips every guard. Rejected.

**Option C — write PowerShell guards now.** Cons: a second parser, no Windows machine to test on. Phase 4.

### Decision 3 — the credential file on Windows
**Option A — the same logical path, `%USERPROFILE%\.config\bmad-guard\credentials`, plus `icacls` on a best-effort basis.**
- Pros: the same path rule everywhere (`~/.config/bmad-guard`), one deny rule shape; `Path.home()` gives `USERPROFILE`.
- Cons: ACLs are not read back by the sender. The sender only refuses a reparse point (link or junction) and a non-regular file. `icacls` can fail; setup reports it.

**Option B — `%APPDATA%\bmad-guard`** (the idea in ADR 0007). Cons: a second path form for the deny rules and the path guard.

**Option C — Windows Credential Manager.** Cons: needs `ctypes` or an external tool; a later hardening (same as the keychain on macOS).

### Decision 4 — the queue lock
**Option A — no lock on Windows in this task.** `emit()` only appends one line in one write. Sending (read, send, truncate) happens only in `flush`. Pros: no event lost to a racing truncate; nothing to build. Cons: two `flush` calls at the same time could send a line twice. `msvcrt.locking` is a later step.

**Option B — `msvcrt.locking` now.** Cons: untestable without Windows.

### Decision 5 — the git layer (L2) and line endings
**Option A — setup on Windows skips the git layer** (no hook copy, no `core.hooksPath`, no exec bit) and says so. It writes `.gitattributes` lines (`.claude/skills/** text eol=lf` and the same for `.agents/skills` and `_bmad/custom/guard`), so the manifest hashes do not change with CRLF. This is written on all OSes, because a shared repo is cloned on Windows too.
- Cons: an extra file in the project that setup owns; uninstall removes its lines.

**Option B — normalise line endings when hashing.** Cons: changes the manifest rule on all OSes and weakens the byte check.

## Decision
- **Python 3.10+ and Git for Windows are required.** Hook command stays `python3`. `--check` runs `bash -c 'python3 -c "import sys; print(sys.version_info[:2])"'`, and reports a missing bash, a missing or stub `python3` or a version under 3.10, each with the fix. PowerShell-only hosts are out of scope.
- **Block the PowerShell tool** in guarded projects on all OSes. Real PowerShell guards are Phase 4.
- **Credential file:** `%USERPROFILE%\.config\bmad-guard\credentials`, `icacls` best effort in setup, Windows BAs send events. On Windows the sender skips the mode bits and refuses a reparse point.
- **No queue lock on Windows;** `emit()` appends only.
- **Git layer skipped on Windows; `.gitattributes` on all OSes.**
- **Pilot form is the project wiring** (no admin). The managed Windows template (`C:/Program Files/ClaudeCode/bmad-guard`, forward slashes in the command) and the installer branch are built and unit-tested, and marked "not run on a real machine".
- **One path helper** (`winpaths.py`): MSYS paths to Windows form, `\\?\` and stream suffix and trailing dot or space removed, case folded, both separators, more home forms. It changes nothing on POSIX.

### Known limits (accepted and documented)
- `cmd /c ...` and `powershell -c ...` from the Bash tool are not parsed (same class as `python -c`, ADR 0002).
- 8.3 short names for files that do not exist yet.
- Junctions are not seen by the setup symlink check (low risk).
- `rd /s`, `del /s` and `Remove-Item -Recurse` through Git Bash are not seen by the recursive-delete rule (Phase 4).
- `python3` is found by `PATH`. A planted `python3.exe` earlier in `PATH` runs instead (same as on macOS and Linux).
- Defender may slow hook start. Measured in the manual check.
- Not verified without a Windows machine: the hook payload path forms, the value of `CLAUDE_PROJECT_DIR`, `HOME` in Git Bash, and everything in `docs/guides/windows-check.md`.

## Consequences
- Good:
  - A real gap closes on every OS: the PowerShell tool no longer skips the guards.
  - Windows paths (separators, case, MSYS, `::$DATA`, trailing dot) are handled in one helper and tested.
  - Windows BAs can send events.
  - Nothing is weaker on macOS and Linux.
- Bad / risks:
  - Windows behaviour is tested by simulation only. Until the manual check passes, "works on Windows" is not claimed.
  - PowerShell is unusable in guarded projects.
  - `python3.exe` needs a manual step.
  - No L2 on Windows, no queue lock.
- Follow-up work (Phase 4): real PowerShell guards; `msvcrt` lock; Credential Manager; guards for native Windows delete commands; PostToolUse.

## Related
- Past ADRs: 0002 (layers, fail modes, known limits), 0007 (credential file), 0008 (machine folder, Windows path).
- Links: `docs/plans/phase-3.md` (D10, D11, T9); `docs/guides/windows-check.md`; `module/skills/tool-guard/scripts/winpaths.py`.
