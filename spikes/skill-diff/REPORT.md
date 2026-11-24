# T1 report: skill diff

| | |
|---|---|
| Ticket | GUARD-0 (T1) |
| Date | 2026-11-24 |
| Result | **Pass.** Every changed file is classified. Every real edit has a new home. |
| Source | `experion-bmad`, branch `main` (5598670), plus a look at the other branches |

## Answer

The "~40 edited skills" are **not** 40 edited skills.

- On `main`, only **5 stock skills** have real Experion edits: **9 files edited + 5 files added**. All 14 are for one feature: confidence scoring.
- Everything else is **version noise**. `.claude/skills` holds a full **BMAD v6.10.0** install, while `_bmad/` and `.agents/skills` are **v6.11.0**. Diffing against 6.11.0 makes every skill look edited.
- 223 stock files are byte-identical to upstream BMAD v6.10.0. The BMad Builder skills (123 files) are byte-identical to `bmad-builder` v2.1.0.

## What is installed on `main`

| Folder | Read by | What it really is |
|---|---|---|
| `_bmad/` | all | BMAD 6.11.0 (manifest says 6.11.0, IDE `github-copilot` only) |
| `.agents/skills/` | GitHub Copilot | BMAD 6.11.0 + bmb. Matches a clean 6.11.0 install. No guard edits. |
| `.claude/skills/` | Claude Code | BMAD **6.10.0** + bmb v2.1.0 + 6 custom skills + confidence-scoring edits. **Not managed by the installer** (manifest lists no `claude-code`). |
| `skills/` (root) | none | Source copies of 4 custom skills (bmb output folder) |
| `_bmad/custom/` | all | Team overrides (correct place already) |

So Claude Code users and Copilot users run **different BMAD versions** today. Only Claude Code gets confidence scoring in the planning skills.

## How we checked

1. Installed clean BMAD 6.11.0 (`core`, `bmm`; Claude Code + Copilot) into `work/clean-6.11.0/`. The installer runs fully non-interactive:
   `npx bmad-method@6.11.0 install --directory <dir> --modules bmm --tools claude-code,github-copilot --user-name dev --yes`
2. Diffed `_bmad/`, `.agents/skills/` and `.claude/skills/` against it. `.claude/skills` had a different skill set (7 stock skills only in 6.11.0; 4 stock skills plus 5 bmb skills only in experion), which pointed to a version gap.
3. Compared the git blob hash of every stock file in `.claude/skills` with the upstream `bmad-code-org/BMAD-METHOD` tree at tag `v6.10.0` (read through `gh api`, no package download). Same for bmb files against `bmad-code-org/bmad-builder` tags v1.8.1 to v2.2.2.
4. Diffed the remaining files against upstream v6.10.0 file content.
5. Read the history of all remote branches.

Evidence (file paths and status only): `evidence/hash-check-vs-6.10.0.txt`.

| Check | Result |
|---|---|
| Stock BMAD files in `.claude/skills` | 234 files: 223 identical, 11 differ |
| The 11 that differ | 9 confidence-scoring edits, 2 CSV files with Windows line endings only |
| Files with no upstream match | 5 `confidence-scoring.md` (added) + 123 bmb files |
| bmb files vs `bmad-builder` | v2.1.0: 123/123 identical (v2.2.2: 91/123) |
| `_bmad/` vs clean 6.11.0 | Only generated config (project name, user name, dates, output path) and leftover files |
| `.agents/skills` vs clean 6.11.0 | Identical, except 1 CSV line-ending change and the bmb skills |

## Classification

### Guard edits (real, keep)

All 14 add one feature: the LLM self-scores a finished planning document (0–100, Low/Medium/High, rationale) and writes it to frontmatter. Open questions and `[ASSUMPTION]` tags lower the score.

| Skill | File | Change | New home |
|---|---|---|---|
| bmad-product-brief | `SKILL.md` | Adds a "Confidence score" step before handoff; frontmatter lists confidence fields | `_bmad/custom/bmad-product-brief.toml`: `persistent_facts` or `on_complete` step pointing at the guard reference |
| bmad-product-brief | `assets/brief-template.md` | Adds frontmatter block with confidence fields | `_bmad/custom/templates/brief-template.md` (override already exists; add the fields) |
| bmad-product-brief | `references/confidence-scoring.md` | New file | Guard module, one shared copy |
| bmad-prd | `SKILL.md` | Adds "Confidence score" step 6; frontmatter lists confidence fields | `_bmad/custom/bmad-prd.toml` |
| bmad-prd | `assets/prd-template.md` | Adds `status` and confidence fields | `_bmad/custom/templates/prd-template.md` (override exists; add the fields) |
| bmad-prd | `references/confidence-scoring.md` | New file | Guard module |
| bmad-architecture | `SKILL.md` | Adds "Confidence score" step 5 | `_bmad/custom/bmad-architecture.toml` |
| bmad-architecture | `assets/spine-template.md` | Adds confidence fields | `_bmad/custom/templates/architecture-spine-template.md` (override exists; add the fields) |
| bmad-architecture | `references/confidence-scoring.md` | New file | Guard module |
| bmad-ux | `SKILL.md` | Adds "Confidence score" bullet (EXPERIENCE.md only) | `_bmad/custom/bmad-ux.toml` |
| bmad-ux | `references/confidence-scoring.md` | New file | Guard module |
| bmad-create-epics-and-stories | `steps/step-04-final-validation.md` | Adds "Confidence Scoring" section before save | `_bmad/custom/bmad-create-epics-and-stories.toml` (no template key exists, so the instruction must also add the frontmatter fields) |
| bmad-create-epics-and-stories | `templates/epics-template.md` | Adds confidence fields | Same toml as above |
| bmad-create-epics-and-stories | `references/confidence-scoring.md` | New file | Guard module |

Notes:

- The 5 `confidence-scoring.md` files are identical. Phase 1 keeps **one** copy in the guard module.
- `_bmad/custom/*.toml` already logs the score through Ledger in `on_complete`. Only the "compute the score" step lives in stock files. Moving it into the same tomls is enough.
- The custom templates in `_bmad/custom/templates/` do **not** have the confidence fields yet. Phase 1 must add them, or the move loses them.
- This self-score is still the LLM's own opinion. The ready-for-dev gate (ADR 0004) should count open questions and `[ASSUMPTION]` tags with a script, not trust the score alone.

### Other content edits

None on `main`.

### Version noise and generated files (drop)

| What | Files | Why |
|---|---|---|
| BMAD 6.10.0 stock skills | 223 | Identical to upstream v6.10.0 |
| bmb skills (agent-builder, bmb-setup, eval-runner, module-builder, workflow-builder) | 123 | Identical to `bmad-builder` v2.1.0 |
| `bmad-brainstorming` CSVs | 2 | Windows line endings only |
| `_bmad/config.toml`, `core/config.yaml`, `bmm/config.yaml`, `_config/manifest.yaml`, `_config/files-manifest.csv` | 5 | Installer output (project name, user, dates) |
| `_bmad/*.bak`, `_bmad/config.yaml`, `_bmad/module-help.csv` | 4 | Leftovers from upgrades and bmb setup |

Phase 1 installs a fresh BMAD 6.12.4 + bmb from the installer. None of these are carried over.

## The "lost edits" event (`bmad-extension` branch)

Commit fa7d1a3 ("Installed the bmad method again - File changes found") re-ran the installer:

- BMAD 6.11.0 → **6.12.0**, plus bmb **v2.2.2** from the external module.
- This time the installer also wrote `.claude/skills` (486 files changed).
- It replaced the 6.10.0 skills in `.claude/skills`. All 14 confidence-scoring changes were lost: the 9 edited files were overwritten, and the 5 added `confidence-scoring.md` files were deleted, even though they were new files. The installer owns the whole stock skill folder.
- The 6 custom skills (own folders) survived.

Cause: edits lived inside installer-owned folders. With the T2 approach (overrides + guard module in its own folders), a re-install has nothing to overwrite.

## Other branches (for Phase 1, not moved now)

| Branch | BMAD | Edits to stock skills | Note |
|---|---|---|---|
| `bmad-token-optimisation` | 6.12.0 (on top of fa7d1a3) | Yes: `bmad-architecture`, `bmad-prd`, `bmad-ux`, `bmad-spec`, `bmad-create-epics-and-stories` (`SKILL.md`) and 7 `bmad-build` step files, in both `.claude` and `.agents` | Ships `_bmad/custom/apply-token-optimizer-adapters.py`, which re-patches stock files after each update. This is the same "edit stock files" problem in a new form. Needs its own decision in Phase 1. |
| `demo` | 6.10.0 | Wholesale `.agents/skills` changes (older install) | Copies the same guard edits. Nothing new found for the module. |
| `feature/llm-wiki-integ` | 6.10.0 | Same as `demo` | Adds 4 `wiki-*` custom skills. Out of scope. |
| `service/prompt-gateway` | no manifest | Whole skill folders replaced | Gateway server code moves to `bmad-guard-gateway` in Phase 1, history removed. |

We did not diff these branches file by file. The token-optimisation branch is the only one with new stock edits that matter.

## Input for later tasks

- **T2:** the 6.11.0 installer runs non-interactively (`--yes`, `--modules`, `--tools`, `--directory`). It also has `--custom-source <git URL or local path>` for custom modules. Test that first in 6.12.4.
- **T2:** the installer writes skills to every selected tool folder (`.claude/skills`, `.agents/skills`). The setup script must select both tools, so Claude Code and Copilot get the same version.
- **ADR 0001 / 0005:** no stock edits, ever. Also no "re-patch after update" scripts (token-optimisation branch).
- **ADR 0004:** confidence is a self-score. The gate needs a counted check as well.
- **Phase 1 plan:** move list = 1 shared `confidence-scoring.md`, 5 toml updates, 3 template updates, the 6 custom skills, and a decision on the token optimizer.

## Not covered

- Content of the 6 custom skills (Sentry, Warden, Ledger, confidence-scorer, Voss, gateway guard). They are harvested in Phase 1.
- File-by-file diff of the non-`main` branches.
