# Overrides

Files copied into a project's `_bmad/custom/` by `scripts/setup_guard.py`. Only override points that
BMAD offers (`customize.toml` keys); never stock files, never copied templates.

| File | What |
|---|---|
| `<skill>.toml` | Team-layer override for one skill: BMAD 6.12.4 skills, or our guard skills |
| `experion-frd.toml` | Fenn (FRD per PRD feature): Sentry checks the PRD first; Iris (`experion-frd-review`) reviews; Warden approves each FRD as `frd-F-<nnn>` |
| `guard/references/confidence-scoring.md` | Shared confidence rubric, loaded by the planning overrides |

BMAD resolves three layers per skill: the skill's own `customize.toml`, the team file
`_bmad/custom/<skill>.toml` (ours), and a personal `_bmad/custom/<skill>.user.toml`.

## Project-specific additions

Projects do not edit these files (the setup script reports a changed file as a conflict). Each
workflow override loads `file:{project-root}/_bmad/custom/project/<skill>/*.md` as extra facts, so a
project adds its own rules there, for example which context files to load before a review:

```
_bmad/custom/project/bmad-code-review/context-files.md
```

## Checks

`tests/test_overrides.py` checks every override against the 6.12.4 key list in
`tests/fixtures/bmad-6.12.4-customize.json`. Regenerate that fixture when the BMAD pin changes.
