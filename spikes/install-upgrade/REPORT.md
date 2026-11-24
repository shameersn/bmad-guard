# T2 report: install and re-install

| | |
|---|---|
| Ticket | GUARD-0 (T2) |
| Date | 2026-11-24 |
| BMAD | v6.12.4 (stretch: upgrade from v6.11.0) |
| Result | **Pass** with the setup-script method. The BMAD installer's own custom-module method is **not safe** in 6.12.4. |

## Answer

- A guard module installed by **our setup script** survives a BMAD quick-update, a full update and a 6.11.0 → 6.12.4 upgrade. Zero changes to the module, the override or `core.hooksPath`. No stock file was edited.
- `_bmad/custom/bmad-prd.toml` survives every run and still resolves through `resolve_customization.py`.
- The installer **can** install a custom module from a local folder (`--custom-source <path>`). But it cannot find that folder again on later runs. In one case the update failed half-way and left `_bmad/` broken. **Do not use it for the guard module in 6.12.4.**

**Recommendation for Phase 1:** BMAD installer for stock BMAD only; our setup script for the guard module and hooks.

## What we built

| File | What |
|---|---|
| `test-module/` | Minimal module: one skill `guard-hello` (prints "guard ok"), `module.yaml`, `module-help.csv`, `.claude-plugin/marketplace.json`, one git hook `hooks/pre-commit` |
| `setup_guard.py` | Setup script, Python standard library only. Copies module skills into `.claude/skills/` and `.agents/skills/`, copies hooks into `_bmad/custom/guard/hooks/`, sets `core.hooksPath` to that folder. |
| `tests/test_setup_guard.py` | 6 unit tests (`python3 -m unittest tests/test_setup_guard.py`): all pass |

## Install methods found in 6.12.4

| Method | How | Result |
|---|---|---|
| a. BMAD installer, local custom module | `npx bmad-method@6.12.4 install --directory . --custom-source <abs path> --tools claude-code,github-copilot --yes` | Installs. Needs `.claude-plugin/marketplace.json` listing the skills, plus `module.yaml` + `module-help.csv` next to them. Copies **skills only**, not hooks. Writes the **absolute path** of the source folder into `_bmad/_config/manifest.yaml`. |
| b. Our setup script | `python3 setup_guard.py --module <module dir> --project .` | Installs skills and hooks, sets `core.hooksPath`. The installer does not know about it and leaves it alone. |

Other installer facts:

- Non-interactive works: `--yes`, `--modules`, `--tools`, `--directory`, `--action install|update|quick-update`.
- With `--yes` on an existing install, the default action is `quick-update`.
- 6.12.4 checks for `uv` and its skills call `uv run`.

## Test results

Each sandbox is a git repo. We committed before each step and read `git status` / `git diff --stat` after.

| Sandbox | Steps | Guard files kept | Override resolves | `core.hooksPath` kept | Changes outside generated files |
|---|---|---|---|---|---|
| `sandbox-b` (setup script) | install 6.12.4 → setup script + override → quick-update → update | 4/4 | yes | yes | **0 files** |
| `sandbox-a` (installer + setup script for hooks) | install 6.12.4 → `--custom-source` → override + hooks → quick-update → update | 4/4 | yes | yes | 0 files, but warning "no source available: bgtest" on both runs |
| `only-installer-*` (installer only) | install 6.12.4 → `--custom-source` → quick-update | skill kept | n/a | n/a | 0 files, same "no source" warning. Module metadata in the manifest lost (`version: null`, `source: unknown`). |
| `only-installer-retry` (installer only, cache folder present) | same as above | skill kept | n/a | n/a | **quick-update failed**: "Source for module 'bgtest' is not available". `core/config.yaml` and `bmm/config.yaml` deleted, staging folders left in `_bmad/`. |
| `sandbox-upgrade` (stretch) | install 6.11.0 → setup script + 2 overrides → upgrade to 6.12.4 (`--yes`) | 5/5, 0 diff lines | yes | yes | 0 lines in guard or override files. 294 stock files changed (the upgrade itself). |

"Generated files" = `_bmad/_config/*`, `_bmad/config.toml`, `_bmad/core/config.yaml`, `_bmad/bmm/config.yaml`. Every installer run rewrites them (dates, and `output_folder` changes from `_bmad-output` to `"{project-root}/_bmad-output"`). Expect one commit of these after every BMAD update.

## Why method a fails (root cause)

In `tools/installer/modules/custom-module-manager.js`, `findModuleSourceByCode()`:

1. If `~/.bmad/cache/custom-modules` does not exist, it returns `null` **before** it checks the manifest's `localPath`. Result: "no source available" on every run, for every developer who never installed a remote custom module.
2. If that folder exists, the lookup succeeds, but `official-modules.js` then calls `findModuleSource()` without the local path and throws. The quick-update stops half-way and leaves `_bmad/` broken.

To confirm point 1, we created the empty folder `~/.bmad/cache/custom-modules` for one run and removed it again (the empty `~/.bmad/` too).

This looks like an upstream bug. A fix may land in a later BMAD version; re-test before switching to method a.

## Override facts

- Arrays in `_bmad/custom/<skill>.toml` are **append-only**. Tested with `doc_standards`: the stock entry stays, ours is added after it. An override cannot remove a stock entry.
- `persistent_facts` and `activation_steps_prepend` both resolve as expected.
- `_bmad/custom/` is never touched by quick-update, update or upgrade.

## Findings for Phase 1

1. **Setup script is the install method.** It must:
   - copy guard skills into every tool skill folder that exists (`.claude/skills`, `.agents/skills`);
   - copy hooks into `_bmad/custom/guard/hooks/` and set `core.hooksPath` (relative path, works from the repo root);
   - be safe to run again (it replaces the guard skill folders; tested).
2. **`core.hooksPath` is local git config, not committed.** Every developer must run the setup script after cloning. Phase 1 needs a check, for example a Claude Code `SessionStart` hook that warns when `core.hooksPath` is not set.
3. **Install BMAD with both tools** (`--tools claude-code,github-copilot`), so Claude Code and Copilot get the same version (T1 found they differ today).
4. **Deprecated skills in 6.12.4.** A fresh 6.12.4 install has 29 skills. `bmad-dev-story`, `bmad-quick-dev` and `bmad-create-story` are gone (replaced by `bmad-build`). An upgrade from 6.11.0 keeps them as 21 deprecated shims, which v7 will remove. Experion's overrides `bmad-dev-story.toml`, `bmad-quick-dev.toml` and `bmad-create-story.toml` must move to `bmad-build.toml`.
5. **Commit generated files after each BMAD update.** They always change.
6. **Do not register the guard as a BMAD custom module** until the installer bug is fixed. If we do it later, the source must be a git URL, not a local path (the manifest stores the absolute local path, which differs per machine).

## Commands used

```bash
# fresh install, non-interactive
npx -y bmad-method@6.12.4 install --directory . --modules bmm --tools claude-code,github-copilot --user-name dev --yes

# method a: custom module from a local folder
npx -y bmad-method@6.12.4 install --directory . --custom-source /abs/path/test-module --tools claude-code,github-copilot --yes

# method b: setup script
python3 setup_guard.py --module ../../test-module --project .

# re-install
npx -y bmad-method@6.12.4 install --directory . --action quick-update --tools claude-code,github-copilot --yes
npx -y bmad-method@6.12.4 install --directory . --action update --tools claude-code,github-copilot --yes

# check the override
uv run _bmad/scripts/resolve_customization.py --skill .claude/skills/bmad-prd --key workflow.persistent_facts
```

## Not covered

- Windows (paths, `core.hooksPath`, executable bit).
- Remote git URL as `--custom-source`.
- Uninstall.
