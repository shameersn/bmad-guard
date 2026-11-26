# bmad-guard

Guard module for "Guarded BMAD": stock [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD)
plus guards that run outside the model and can block.

- Stock BMAD, pinned version (v6.12.4). No fork, no edits to BMAD files.
- All Experion changes live in this module and in `_bmad/custom` overrides.
- Guards are small, tested Python scripts (standard library only) with a clear verdict: allow or block.
- Every guard decision is an event: OpenTelemetry collector → ClickHouse → Grafana.

Status: Phase 2 (enforcement) is built and the red-team suite passes in a throwaway sandbox (`docs/reports/phase-2-red-team.md`). Open before a pilot: the real-session checks listed in `docs/plans/phase-2.md`. Phase 3 is a draft (`docs/plans/phase-3.md`). Not ready for use.

## Layout

| Path | What |
|---|---|
| `module/skills/` | Guard skills, copied into a project's `.claude/skills/` and `.agents/skills/` |
| `module/hooks/` | Git hooks, plain Python, copied to `_bmad/custom/guard/hooks/` (Phase 2): `pre-commit` (scan, approval fields, tech stack) and `pre-push` (independent review record, T7). Rules and limits: `module/hooks/README.md`. |
| `module/overrides/` | Files copied into a project's `_bmad/custom/` |
| `module/managed-settings/` | Managed-settings template for a later IT rollout (not installed by anything here) |
| `scripts/` | Repo tools: setup script (also wires the Claude Code hooks into `.claude/settings.json`), install test (Gate 1), red-team suite (Gate 2). See `scripts/README.md`. |
| `tests/` | Unit tests and repo rules |
| `docs/plans/` | One plan per phase. Each plan is approved by PR before its work starts. |
| `docs/reports/` | Generated test reports: `phase-1-install-test.md` (install test), `phase-2-red-team.md` (red-team suite). |
| `docs/adr/` | Architecture decision records. |
| `docs/guides/`, `docs/architecture.md` | Pilot pack: install and role guides, trial run, architecture with diagrams. |
| `docs/hooks.md` | How Claude Code hooks behave in the pinned version, with sources. `docs/managed-settings-rollout.md`: what IT installs. Command, path, document, write gate, review record and content scan rules and limits: `module/skills/tool-guard/references/guard-rules.md`. |
| `spikes/` | Throwaway experiments from Phase 0. Each has a `REPORT.md`. Not linted. |

## Pilot pack

Guides for pilot users (Phase 3, T0): [`docs/guides/`](docs/guides/README.md).

- [Install guide](docs/guides/install.md), then a guide for your role: [developer](docs/guides/developer.md), [BA / PM](docs/guides/ba-pm.md), [reviewer](docs/guides/reviewer.md).
- [Architecture](docs/architecture.md): layers, guards, process flow with diagrams, events and privacy, limits.
- [Trial run](docs/guides/trial-run.md): the clinic-booking trial script.
- Deck: link added after review.

## Checks

Run before every PR (CI runs the same on Python 3.10 and 3.13):

```bash
python3 -m unittest discover
uvx ruff@0.17.6 check .
uvx ruff@0.17.6 format --check .
```

Two end-to-end scripts build throwaway BMAD sandboxes and need network, Node (`npx`), `uv` and git, so they are not part of the unit tests:

```bash
python3 scripts/install_test.py --report docs/reports/phase-1-install-test.md
python3 scripts/red_team.py --report docs/reports/phase-2-red-team.md
```

Rules enforced by `tests/test_repo_rules.py`: every Python file under `module/` and `scripts/` has a PEP 723 header with `requires-python`, and imports only the standard library or a sibling module.

## Related

- Prompt checks run locally in the `prompt-guard` skill (ADR 0006); there is no gateway server.
- `experion-bmad`: the earlier prototype. Read-only; we harvest from it.

## Working rules

- Branches: `feature/GUARD-<phase>-<short-name>`.
- Commits: `GUARD-<phase>: what changed`.
- Never commit secrets, `.env` files, developer keys or event queues.
