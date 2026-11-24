# bmad-guard

Guard module for "Guarded BMAD": stock [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD)
plus guards that run outside the model and can block.

- Stock BMAD, pinned version (v6.12.4). No fork, no edits to BMAD files.
- All Experion changes live in this module and in `_bmad/custom` overrides.
- Guards are small, tested Python scripts (standard library only) with a clear verdict: allow or block.
- Every guard decision is an event: OpenTelemetry collector → ClickHouse → Grafana.

Status: Phase 0 (decide). Not ready for use.

## Layout

| Path | What |
|---|---|
| `docs/plans/` | One plan per phase. Each plan is approved by PR before its work starts. |
| `docs/adr/` | Architecture decision records. |
| `spikes/` | Throwaway experiments from Phase 0. Each has a `REPORT.md`. |

## Related

- `bmad-guard-gateway`: the prompt gateway server. Each project hosts its own.
- `experion-bmad`: the earlier prototype. Read-only; we harvest from it.

## Working rules

- Branches: `feature/GUARD-<phase>-<short-name>`.
- Commits: `GUARD-<phase>: what changed`.
- Never commit secrets, `.env` files, developer keys or event queues.
