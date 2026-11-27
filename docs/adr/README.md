# Architecture decision records

Format: context, options, decision, consequences. Status goes Proposed → Accepted (after team approval by PR) → Superseded by NNNN.

| No. | Title | Status | Date |
|---|---|---|---|
| 0001 | [Stock BMAD plus an external guard module](0001-architecture.md) | Accepted | 2026-11-24 |
| 0002 | [Enforcement layers: local hooks now, CI later](0002-enforcement-layers.md) | Accepted; gateway part superseded by 0006 | 2026-11-24 |
| 0003 | [Event logging: OTel collector → ClickHouse → Grafana](0003-event-logging.md) | Accepted | 2026-11-24 |
| 0004 | [Ready-for-dev gate: counted, not self-scored](0004-ready-for-dev-gate.md) | Accepted | 2026-11-24 |
| 0005 | [Repo layout: module repo, gateway repo, plans and ADRs in the repo](0005-repo-layout.md) | Accepted; gateway repo superseded by 0006 | 2026-11-24 |
| 0006 | [Local prompt check replaces the prompt gateway](0006-local-prompt-check.md) | Accepted | 2026-11-25 |
| 0007 | [Credential storage: a per-user file, environment variables override](0007-credential-storage.md) | Proposed | 2026-11-26 |
| 0008 | [Machine-level script location: guard scripts installed once per machine](0008-machine-level-script-location.md) | Proposed (built and tested in temp folders in T5; real-machine test open) | 2026-11-26 |
| 0009 | [Windows support for the Claude Code (L1) layer](0009-windows-l1.md) | Proposed (built and tested by simulation in T9; real-machine check open) | 2026-11-26 |
| 0010 | [FRD is the requirement; stories are thin pointers](0010-frd-source-thin-stories.md) | Proposed (team to review the example, then decide) | 2026-11-27 |
