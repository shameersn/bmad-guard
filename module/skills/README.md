# Guard skills

Each folder is one skill (SKILL.md, customize.toml, scripts/). The setup script copies them into `.claude/skills/` and `.agents/skills/` of a project.

| Skill | Name | Job |
|---|---|---|
| `agent-approval-grant` | Warden | Two confirmations from a person, then marks a document approved. |
| `agent-approval-gate-check` | Sentry | Checks that an upstream document is approved and unchanged. |
| `agent-project-logger` | Ledger | Writes guard events to the local queue. |
| `confidence-scorer` | Audit | Scores each changed file after a build. |
| `experion-brief-review` | Voss | Reviews the product brief before approval. |
| `experion-frd` | Fenn | Writes one FRD per PRD feature. The FRD is the only place requirements are written. |
| `experion-frd-review` | Iris | Reviews an FRD against its PRD feature. |
| `experion-story-slicer` | Sloane | Writes thin stories (FR ids, no requirement text), technical stories and the build order from approved FRDs and the approved architecture. `story_coverage.py` checks that every FR is in one story and no FRD text was copied (ADR 0010). |
| `prompt-guard` | Marshal | Checks prompts for secrets, personal data and injection. |
| `ready-for-dev-gate` | Gatekeeper | Counts open items in a story, checks its FR ids against the FRD, and checks the architecture for a technical story. Also holds `frd_parse.py`, the shared FRD reader. |
| `tool-guard` | | The hooks: command, path, document and write gates, review record, content scan. |
