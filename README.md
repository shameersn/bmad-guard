# bmad-guard

Guard module for "Guarded BMAD": stock [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD)
plus guards that run outside the model and can block.

- Stock BMAD, pinned version (v6.12.4). No fork, no edits to BMAD files.
- All Experion changes live in this module and in `_bmad/custom` overrides.
- Guards are small, tested Python scripts (standard library only) with a clear verdict: allow or block.
- Every guard decision is an event: OpenTelemetry collector → ClickHouse → Grafana.

Status: Phase 1 (guard module). Not ready for use.

## Layout

| Path | What |
|---|---|
| `module/skills/` | Guard skills, copied into a project's `.claude/skills/` and `.agents/skills/` |
| `module/hooks/` | Git hooks, plain Python, copied to `_bmad/custom/guard/hooks/` (Phase 2): `pre-commit` (scan, approval fields, tech stack). Rules and limits: `module/hooks/README.md`. |
| `module/overrides/` | Files copied into a project's `_bmad/custom/` |
| `module/managed-settings/` | Managed-settings template for a later IT rollout (not installed by anything here) |
| `scripts/` | Repo tools: setup script (also wires the Claude Code hooks into `.claude/settings.json`), install test |
| `tests/` | Unit tests and repo rules |
| `docs/plans/` | One plan per phase. Each plan is approved by PR before its work starts. |
| `docs/adr/` | Architecture decision records. |
| `docs/hooks.md` | How Claude Code hooks behave in the pinned version, with sources. `docs/managed-settings-rollout.md`: what IT installs. Command, path, document, write gate and content scan rules and limits: `module/skills/tool-guard/references/guard-rules.md`. |
| `spikes/` | Throwaway experiments from Phase 0. Each has a `REPORT.md`. Not linted. |

## Checks

Run before every PR (CI runs the same on Python 3.10 and 3.13):

```bash
python3 -m unittest discover
uvx ruff@0.17.6 check .
uvx ruff@0.17.6 format --check .
```

Rules enforced by `tests/test_repo_rules.py`: every Python file under `module/` and `scripts/` has a PEP 723 header with `requires-python`, and imports only the standard library or a sibling module.

## Related

- Prompt checks run locally in the `prompt-guard` skill (ADR 0006); there is no gateway server.
- `experion-bmad`: the earlier prototype. Read-only; we harvest from it.

## Working rules

- Branches: `feature/GUARD-<phase>-<short-name>`.
- Commits: `GUARD-<phase>: what changed`.
- Never commit secrets, `.env` files, developer keys or event queues.
