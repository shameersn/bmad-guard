# 0008 — Machine-level script location: guard scripts installed once per machine

- **Status:** Proposed (2026-11-26). **To be tested in T5** (Phase 3 plan). It is not Accepted until the T5 tests pass and the team approves by PR.
- **Date:** 2026-11-26
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR
- **Review:** Shameer accepted the recommended answers on 2026-11-26: allow everything in an unguarded project for the pilot; the setup manifest is the marker; one managed template per OS; a separate `install_machine.py`; `review_record.py` accepts the project copy (T5, case MACH-3); the managed `Edit` deny is deferred.

## Context
Managed settings (ADR 0002, `docs/managed-settings-rollout.md`) lock the hook entries, but the entries point into the project. Each command is:

`python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/<skill>/scripts/<script>.py"` (`hook_wiring.py:76-78`, `managed-settings.json:8,19,31`).

Two problems follow:
1. **A project without the module blocks every tool call.** Managed settings apply to every project on the machine. If the script is missing, Python exits with code 2, and for `PreToolUse` that is a block (`docs/hooks.md` section 9; rollout doc, "Read this before rolling out", item 1; test step 7). So the managed file can go only on machines where every project has the module.
2. **The scripts can be edited per project.** `allowManagedHooksOnly` locks the entries, not the scripts (rollout doc item 2; `docs/hooks.md:132`). The guard skills in the project are protected only by `DOC_GUARD_SCRIPTS`, and that guard cannot see `python3 -c` or a script file.

Both are todos in the Phase 2 plan ("Machine-level script location", before the full IT rollout). The Phase 3 plan (D7) proposes a root-owned folder next to the managed settings, and this ADR records the options. T5 builds and tests it.

What the hooks need to keep working:
- They still need the **project**: its folder (`CLAUDE_PROJECT_DIR`, and `cwd` in the hook input), its config under `_bmad/custom/`, its records and its approval state.
- They load sibling skills by a path relative to their own file (`parents[2] / <skill> / scripts`). A machine folder must keep the same layout.

Constraints:
- The scripts must not be editable by the developer's user, or by the agent that runs as that user.
- One delivery channel for IT: the managed file and the scripts move together.
- Windows is out of scope (D10).
- Python 3.10, standard library only.

## Options
### Option A — A root-owned folder next to the managed settings
- macOS: `/Library/Application Support/ClaudeCode/bmad-guard/`
- Linux (and WSL): `/etc/claude-code/bmad-guard/`
- Pros:
  - The same place and the same owner as `managed-settings.json`. IT copies the folder and the file in one step; updates use the same channel.
  - Root-owned, so the developer's user and the agent cannot edit the scripts. This closes problem 2 for L1.
  - The hook command is an absolute path. A project without the module is no longer a missing-script case (problem 1).
  - The built-in rules (`customize.toml`) are read from the machine copy, so a project cannot change them.
- Cons:
  - The macOS path has a space (the command must stay quoted).
  - `/etc` is for configuration by Linux convention, not for code. We accept it for the one-channel benefit.
  - A machine folder and a project copy now exist side by side: version skew (see below).
  - IT must keep the folder and the managed file in step. A managed file without the folder blocks every tool call.
  - Not verified: that Claude Code ignores an extra folder in its managed directory, and the owner and mode of these folders on a fresh macOS.
- Cost / effort: medium (installer, template, tests).

### Option B — A per-user folder, for example `~/.local/share/bmad-guard/`
- Pros: no root needed; no per-project copies; a project without the module is not a missing-script case.
- Cons: the folder is owned by the developer's user, so the agent can edit it. It fixes problem 1 but not problem 2. It also needs a delivery channel per developer.
- Cost / effort: low to medium.

### Option C — Keep per-project copies; ship managed settings only where every project has the module
This is the state today (rollout doc, "pilot machine check").
- Pros: nothing to build; the setup script already works.
- Cons: a new clone without setup blocks the developer. IT must keep a list of machines and their projects. The scripts stay editable per project. It cannot scale beyond the pilot.
- Cost / effort: none now, a permanent manual cost.

### Option D — A package installed by MDM to a system path (for example `/usr/local/lib/bmad-guard`)
- Pros: normal for IT teams that already ship packages; package managers handle upgrade and removal.
- Cons:
  - We must build and sign a `.pkg` and `.deb`/`.rpm`. That is more work than a file copy.
  - It is a second channel next to the managed file.
  - `/usr/local` can be owned by the user on a Mac with Homebrew (not verified), so it is not always root-owned.
- Cost / effort: high.

## Decision
We choose **Option A** as the proposal to test in T5 (D7).
- IT copies the scripts together with the managed file. Updates go through the same channel.
- If T5 shows a problem we cannot fix cheaply, the fallback is Option C (pilot machines only) while we look again. Option D stays possible later: only the base path in the hook command would change.

### The machine folder
Layout: the same `skills/<skill>/` tree as a project's `.claude/skills/`, so the sibling lookups keep working.

```
.../ClaudeCode/bmad-guard/
  VERSION                         the module version (same string setup_guard.py writes to the project manifest)
  manifest.json                   file hashes, for --check
  skills/tool-guard/              scripts/ and customize.toml
  skills/prompt-guard/            scripts/ and customize.toml
  skills/ready-for-dev-gate/      scripts/ and customize.toml
  skills/agent-project-logger/    scripts/ (Ledger's sender)
  skills/agent-approval-gate-check/  scripts/ (Sentry; the write gate loads it)
```

Why these five: they are the skills the three hooks load (`pre_tool_use.py`, `prompt_guard_hook.py`, `session_start.py` and the guards they import). The other skills (Warden's grant script, confidence scorer, brief review) are run by the model from the project, and the git hooks load their code from the project.
- Owner root, folders 755, files 644. A script run as `python3 <file>` needs no execute bit.
- Updates replace the whole folder in one step: copy to `bmad-guard.new` next to it, then rename. A half-copied folder would block tool calls.

### How the hook command changes
- Before: `python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/pre_tool_use.py"`
- After, macOS: `python3 "/Library/Application Support/ClaudeCode/bmad-guard/skills/tool-guard/scripts/pre_tool_use.py"`
- After, Linux: `python3 "/etc/claude-code/bmad-guard/skills/tool-guard/scripts/pre_tool_use.py"`

The script path has no `${CLAUDE_PROJECT_DIR}`. The project is still found the same way: the scripts read `CLAUDE_PROJECT_DIR` (set by Claude Code in hook processes; `hook_common.py:75`) or the `cwd` of the hook input. Nothing is passed as an argument.

Consequences for the files that define the command:
- `hook_wiring.py`: `command()` takes an optional base folder; `MARKERS` (line 73) already matches the machine path (`/skills/tool-guard/scripts/`). The machine base per OS is defined once here and used by the installer and by `session_start.py`.
- Managed template: one file per OS (`managed-settings.macos.json`, `managed-settings.linux.json`), because the path differs. The test that compares the template with `hook_wiring.py` (`tests/test_hook_wiring.py:275-286`) runs for each. The current template stays until the machine mode passes T5.
- Project wiring (`.claude/settings.json`, written by `setup_guard.py`) does not change. On a machine with managed settings and `allowManagedHooksOnly`, it is ignored. On a machine without them, it still runs the project copies.

### What the scripts read from the machine and from the project
I read every path the hooks resolve. Nothing finds the project from `__file__` (no `parents[4]` and the like): the project always comes from `CLAUDE_PROJECT_DIR` or `cwd`.

**From the machine folder (relative to the script's own file):**

| Where | What |
|---|---|
| `guard_config.py:32-33` | `tool-guard/customize.toml`: the built-in command rules, scratch folders, local hosts |
| `prompt_check.py:48,89` | `prompt-guard/customize.toml`: the base layer of the prompt-guard actions |
| `write_gate.py:140` | `ready-for-dev-gate/customize.toml`: `min_confidence`, `epics_dir`, `baseline` |
| `write_gate.py:127` | Sentry's `check_gate.py` |
| `hook_common.py:88`, `prompt_check.py:219`, `ready_for_dev.py:360`, `approval_core.py:183` (both copies), `confidence_score.py:238` | Ledger's `guard_events.py` |
| `content_scan.py:68` | `prompt_check.py` |
| `document_text.py:51` | `ready_for_dev.py` |

All of these use `parents[2]` or `HERE.parents[1]`, so they work only if the machine folder keeps `skills/<skill>/scripts/`. They are the reason for the layout above. A side effect that we want: a project can no longer change the built-in rules by editing its copy of a `customize.toml`.

**From the project (`CLAUDE_PROJECT_DIR`):**

| Where | What |
|---|---|
| `guard_config.py:34` | `_bmad/custom/project/guard/tool-rules.json` |
| `prompt_check.py:49,90-91` | `prompt-patterns.json`, `_bmad/custom/prompt-guard.toml`, `prompt-guard.user.toml` |
| `hook_common.py:102`, `prompt_check.py:245`, `ready_for_dev.py:374`, `approval_core.py:172` | `_bmad/custom/project.json` (project name for events) |
| `write_gate.py:70-71` | `_bmad/custom/approval-state.json`; `_bmad-output/guard/builds` |
| `review_record.py:58` | `_bmad-output/guard/reviews` |
| `document_guard.py:55-58` | `_bmad/custom/guard/manifest.json`, `.claude/settings*.json`, the project's guard skills |
| `guard_events.py:42` | `.bmad-guard/events/queue.jsonl` |
| `session_start.py:48` | `git config core.hooksPath` of the project |

### What breaks when the hooks run from outside the project
Found by reading the code, not by running it (marked "not run").

| # | Where | What breaks | Change needed (T5) |
|---|---|---|---|
| 1 | `review_record.py:65` and `:132-143` | The review record is written only when the Bash command runs "this very script": `SCRIPT` is `realpath(__file__)`. From the machine folder that is the machine path. The `bmad-code-review` override tells the model to run the **project** copy (`bmad-code-review.toml:16`). It no longer matches, so no record is written, the command exits 1, and pre-push blocks with `PUSH_NO_RECORD`. (not run) | `_script_arg` also accepts the project copy (`.claude/skills` and `.agents/skills` under the project). The copy only prints a receipt, so this does not weaken the check. Update `guard-rules.md:204`. |
| 2 | `hook_wiring.py:76-78` with `session_start.py:87-97` | `wiring_ok()` checks the managed file against the **project-path** commands. A machine command is a lookalike (`MARKERS`) and counts as a conflict. Every session start would then warn `SETUP_INCOMPLETE: hook_wiring` falsely. (not run) | `merge()` and `wiring_ok()` know the machine command form. |
| 3 | `managed-settings.json:8,19,31`; `tests/test_hook_wiring.py:275-286` | The template holds the project-path commands. | One template per OS (above); the test runs for each. |
| 4 | `session_start.py:41-45` | `MANAGED_DIRS` is defined here only. The installer, the template test and `--check` need the same folders. | Define the machine base once in `hook_wiring.py`. |
| 5 | `pre_tool_use.py:135-148`, `prompt_guard_hook.py:76-88`, `session_start.py:109-133` | A project with no guard still runs every guard. The write gate would block every source write there (no plan). (not run) | The "unguarded project" rule below. |
| 6 | `document_guard.py:55-58,100-101`, `bash_write_guard.py:54-59` | They protect the **project** copies of the guard skills. They do not protect the machine folder, and paths outside the project are not gated (`guard-rules.md:176`). | Nothing to change for L1: the OS protects the machine folder. The project copies still matter for L2, so the zones stay. A managed `Edit` deny for the machine folder as a second layer: deferred (2026-11-26), its syntax is not verified. |
| 7 | `guard_hook_lib.py:27,101-107` | The git hooks load skills from the project only. | Unchanged: L2 stays per project (below). |
| 8 | Python bytecode | The machine folder is not writable by the user, so Python cannot write `__pycache__` and compiles the scripts on each run. We measured 0.07 to 0.13 s per `PreToolUse` call with the cache off (Python 3.13, a Read call in a scratch project). The timeout is 10 s. Not measured on a pilot Mac with Python 3.10. | Measure in T5. If slow, the installer can pre-compile (`.pyc` are Python-version specific). |
| 9 | `scripts/install_test.py:349,364-391,631,792,830`; `scripts/red_team.py:449,464,869` | They test the project copies under `.claude/skills`. | New machine stage (below). |

Not a break, but a change: the model still runs `write_gate.py --check` and the skills' own scripts from the **project** copies (`bmad-build.toml:14`, `SKILL.md` files). Those copies and the machine copies can differ in version. Both read the same project data.

### What a project without the module gets
Today it gets a block on every tool call (problem 1). With machine scripts the script exists, so the missing-script block goes. But the script would still apply every guard, and the write gate would block all source writes in a project that has no plans. So the hooks must first ask "does this project use the guard?"

**Rule (proposal).** The project is the folder in `CLAUDE_PROJECT_DIR`. Decide in this order, as the first thing in each hook, before the guards are imported:

| Project | Meaning | `PreToolUse` | `UserPromptSubmit` | `SessionStart` |
|---|---|---|---|---|
| No `_bmad/` folder | Not a BMAD project | Allow, silent | Allow, silent | Silent |
| `_bmad/` exists, but no `_bmad/custom/guard/manifest.json` | BMAD, guard module not installed | Allow, silent | Allow, silent | One line: "BMAD found, bmad-guard is not installed here, the guards are off. Run `setup_guard.py`." |
| `_bmad/custom/guard/manifest.json` exists | Guarded | All guards, fail closed, as today | As today | As today, plus the version check below |

- "Allow, silent" means exit 0, no output, **no event and no file written in the project**. `emit()` creates `.bmad-guard/` (`guard_events.py:163`), and in a project that never ran setup that folder would show up as untracked.
- The marker is the setup manifest because it is the file that says "setup ran here" and it is committed (`docs/architecture.md`, section 7). So a fresh clone of a guarded project is guarded at once, even before the developer runs setup. `DOC_GUARD_SCRIPTS` already protects the manifest from the file tools and from literal shell targets.
- Any error while deciding counts as "guarded" (fail closed).
- `prompt_guard_hook.py` does not import `hook_common`. It gets the same three-line check, like `project_root()` which already exists in both places (`hook_common.py:73`, `prompt_check.py:231`). A test keeps them equal.
- The check runs before `load_guards()` (`pre_tool_use.py:135`) so that an import error cannot block an unguarded project.

**Risk of the rule:**
- Whoever removes `_bmad/custom/guard/manifest.json` (or `_bmad/`) turns the L1 guards off for that project. The file tools and literal shell targets are blocked (`DOC_GUARD_SCRIPTS`). `python3 -c` and a script file are not seen. This is the same known limit as for every other guard (ADR 0002 accepts bypass). The report "commits with no guard events" (T4) is the check.
- A BMAD project where nobody ran setup has **no L1 guard** under this rule, only the managed deny rules (they are path-based and work in every project). The developer sees the one-line notice. IT sees it in the same report. This is a deliberate choice for the rollout: friction is worse than a visible gap while the pilot is small.
- Prompts and commands in an unguarded project are not checked at all (secrets in prompts, `git push --force`, `rm -rf`). Running the command guard and the prompt guard in every project was considered and not chosen: those projects did not ask for the guard, and the first false block there would end the rollout. Decided (2026-11-26): allow everything in the pilot; look again after the pilot.
- When IT later wants every BMAD project guarded, the second row changes from "allow" to "block with the setup message". One line of code; not built now.
- A project with `_bmad/` in a sub-folder (a monorepo) is "not a BMAD project" for this rule. The write gate already assumes `_bmad-output/` at the project root.

### Git hooks (L2)
They stay per project, with `core.hooksPath` set by `setup_guard.py`. Reasons:
- `core.hooksPath` is local git config, per repo. A system-wide value (`git config --system`) would apply to every repo on the machine, including non-BMAD ones and repos with their own hooks, and a repo's local value overrides it anyway.
- The L2 hooks load their guard code from the project's skills (`guard_hook_lib.py:101-107`). They need the project copy.
- L2 is already known to be skippable (ADR 0002). The machine folder adds no strength there.
So a project that wants L2 still runs the setup script. The machine folder replaces only where the **L1** scripts run from.

### Version skew
Two copies of the scripts now exist: the machine copy (L1 hooks) and the project copy (L2 hooks, and the scripts the model runs). Each has a version: `VERSION` in the machine folder, and `version` in the project's `manifest.json` (`setup_guard.py:106-108,292-294`). Both are the output of `git describe`, so they can be compared for equality but not ordered.

| Machine | Project | What runs | Risk |
|---|---|---|---|
| Same | Same | Everything the same | None |
| Older | Newer | L1 old, L2 and skills new | A newer project file that an old script does not know. `guard_config.py` blocks a bad extension file (`EXT_BAD_FILE`), so an unknown key would block that project |
| Newer | Older | L1 new, L2 and skills old | Rules differ between the commit and the session |
| No machine folder | Any | Project wiring, if any | None: managed settings are not in use |

Rule for rollout: **update the machine first, then the projects.**

What `setup_guard.py --check` says (proposal):
- Machine folder found, versions equal: nothing extra.
- Machine folder found, versions differ: `machine scripts: <v1>; this project's module: <v2>. They differ.` A warning line. The exit code stays driven by the project state, because the developer cannot fix a machine folder.
- No machine folder: `machine scripts: none (fine unless managed settings point to them)`.
- The `SessionStart` hook records an `info` event `MACHINE_VERSION_SKEW` with both versions and shows no message, so a report can list stale projects.

### How `setup_guard.py` changes
**Recommendation: a separate installer, `scripts/install_machine.py`.** Not a `--machine` mode in `setup_guard.py`.
- Different user and rights: IT or MDM runs it as root. The setup script runs as the developer.
- Different input: `setup_guard.py` requires `--project` and checks git and `_bmad/` (`setup_guard.py:185-191,324`). The machine installer has no project.
- Different rules: root-owned files, whole-folder replace by rename, no conflicts with project files, a rollback.
- `module_version()` runs `git describe` in the module checkout (`setup_guard.py:106-108`). As root in a checkout owned by another user, git may refuse ("dubious ownership"; not verified). The installer takes `--version` or reads a `VERSION` file from a release bundle.
- It reuses `_walk`, `sha256` and the manifest format from the setup script (same folder).

The installer:
- `install_machine.py [--target DIR] [--dry-run | --check | --uninstall]`. The default target is the OS path. `--target` is for tests, so they need no root.
- Copies the five skills, writes `VERSION` and `manifest.json`, sets owner and modes.
- `--check`: files match the manifest; the folder is not writable by the current user; `python3` is 3.10 or later (as seen by the user who runs the check); and, if a managed file is found, that its hook paths point into this folder. Exit 1 if not.
- `--uninstall`: removes only what its manifest lists.

`setup_guard.py` itself changes only in `--check` (the lines above). It still writes the project wiring; that stays useful on machines without managed settings.

### Install tests and red-team cases for T5
Install test (`scripts/install_test.py`), a new stage "machine":
- **M-I1** `install_machine.py --target <temp>` installs the five skills, `VERSION` and `manifest.json`. A second run changes nothing.
- **M-I2** `--check` fails after a file is edited, and when a file is writable by the current user.
- **M-I3** The managed template for the OS equals the commands from `hook_wiring.py` for that base. Run each of the three hooks through its command, with a temp base and a path with a space.
- **M-I4** With the project's skill copies **deleted**, all three hooks still run from the machine folder (this proves the sibling layout).
- **M-I5** A project with no `_bmad/`: all three hooks exit 0, print nothing, and write no file in the project.
- **M-I6** A project with `_bmad/` and no manifest: `PreToolUse` and `UserPromptSubmit` allow; `SessionStart` prints the one line.
- **M-I7** A project with the manifest: the guards run (a `git push --force` is blocked).
- **M-I8** `--check` of the project prints the skew line when `VERSION` differs.
- **M-I9** `SessionStart` does not warn `hook_wiring` for a managed file with machine commands.

Red-team cases (`scripts/red_team.py`), a new family "Machine scripts":
- **MACH-1** The project's copy of `tool-guard/customize.toml` is edited (the destructive-command rule removed). `git push --force` is still blocked, because L1 reads the machine copy. This shows the benefit.
- **MACH-2** Write, Edit or `rm` of `_bmad/custom/guard/manifest.json` is blocked (`DOC_GUARD_SCRIPTS`). The agent cannot switch the guard off through the marker.
- **MACH-3** The review record flow works with machine hooks and the project-path command (REV-0 and REV-C1 pass). This covers break 1.
- **MACH-C1** Control: any tool call in a project with no `_bmad/` is allowed.
- **MACH-C2** Control: a BMAD project with no manifest is allowed, with the notice.

Manual steps (they need root and a real session), added to the rollout doc test:
- As the developer, try to write into the machine folder. Expect "permission denied".
- Check the owner and mode of the machine folder on a fresh macOS and Linux (not verified today).
- Start Claude Code in a folder with no `_bmad/`: every tool call must work (this replaces test step 7, which today expects a block).
- Print `python3 --version` as the developer. Under 3.10 the hooks fail, and the fail-closed entries would block every project.

### Rollback
1. Delete `managed-settings.json` (or the profile or registry value). Hooks stop running. This is the same as today's rollback.
2. Then run `install_machine.py --uninstall`, or delete the folder.
3. **Never in the other order.** A managed file that points to a missing folder makes Python exit 2, and every tool call in every project is blocked. `--check` warns when the managed file points to a missing folder. IT should script the order.
4. A bad update: install the previous release again (the installer replaces the whole folder by rename).
5. Project wiring is not affected. The projects keep working with their own copies under Option C.

## Consequences
- Good:
  - The L1 scripts and their built-in rules cannot be edited by the developer's user or by the agent.
  - A project without the module is not blocked. A new clone needs no setup to be allowed.
  - One delivery channel for the managed file and the scripts.
  - A guarded project is found by a committed file, so a clone is guarded at once.
- Bad / risks:
  - Version skew between the machine folder and project copies. Mitigation: the `--check` line, the info event, and the order "machine first".
  - A managed file without its folder blocks everything. Mitigation: the installer order, `--check`, the rollback order.
  - An unguarded BMAD project has no L1 guard. Mitigation: the notice and the T4 report.
  - Python older than 3.10 breaks the hooks in every project. IT checks `python3` before it deploys the managed file.
  - Code changes in six places (the table above), one of them (`review_record.py`) a guard rule.
- Follow-up work:
  - T5: build the installer, the templates and the rule; the tests and cases above; update `docs/managed-settings-rollout.md` (items 1 and 2, test step 7, open question 1) and `docs/hooks.md:132,155`.
  - Not decided: whether to run the command guard and prompt guard in unguarded projects; whether to add a managed `Edit` deny for the machine folder.
  - Later: Windows (`C:\Program Files\ClaudeCode\bmad-guard`; the Windows path is already the managed folder in `session_start.py:41-45`, but L1 on Windows is T9 or Phase 4); packages (Option D) if IT prefers them.

## Related
- Past ADRs: 0002 (enforcement layers; fail modes; managed settings), 0001 (the module and the setup script), 0005 (repo layout).
- Links: `docs/managed-settings-rollout.md`; `docs/hooks.md` (sections 5, 7, 9); `docs/plans/phase-2.md` ("Managed settings rollout todos"); `docs/plans/phase-3.md` (D7, T5); `module/skills/tool-guard/scripts/hook_wiring.py`; `module/skills/tool-guard/scripts/session_start.py`; `module/skills/tool-guard/scripts/review_record.py`; `scripts/setup_guard.py`.
