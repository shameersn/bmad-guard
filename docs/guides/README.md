# Pilot pack

Guides for the people who use Guarded BMAD in the pilot (GUARD-3, T0). Start with the install guide. Then read the guide for your role.

| Document | For | What |
|---|---|---|
| [install.md](install.md) | Everyone | Prerequisites, setup script, what it changes, HIPAA setting, how to read a block message, rollback by hand, FAQ. |
| [developer.md](developer.md) | Developers | Plan, approval, write gate, build record, pre-commit, sensitive paths, destructive commands. |
| [ba-pm.md](ba-pm.md) | BAs, PMs, architects | Warden approvals, the Sentry check, approval fields, open questions, the ready-for-dev format. |
| [reviewer.md](reviewer.md) | Reviewers | Review in a fresh session, the review record, pre-push rules. |
| [trial-run.md](trial-run.md) | Shameer | The clinic-booking trial: set-up, about 20 try-this / expect-that steps, a results table. |
| [demo-run.md](demo-run.md) | Shameer | The 45-minute live demo for senior review: prepare, rehearse and tag, live steps, Grafana close. Kit in [`demo/`](../../demo/README.md). |
| [../architecture.md](../architecture.md) | Everyone who wants to know how it works | Layers, guards, process flow, events and privacy, fail-closed behaviour, limits. |

Deck: [Guarded BMAD pilot briefing](https://claude.ai/artifact/GZAHhyHojnMoc6Hn2hktkD) (10 slides, a Claude artifact; private until Shameer shares it; exports to .pptx or PDF). Built from these docs: if they change, update the deck.

## Where the rules are

- Tool-call rules (`CMD_`, `PATH_`, `DOC_`, `GATE_`, `CONTENT_`, `REVIEW_`): [guard-rules.md](../../module/skills/tool-guard/references/guard-rules.md).
- Git hook rules (`COMMIT_`, `PUSH_`): [module/hooks/README.md](../../module/hooks/README.md).
- Question and answer format: [ready-for-dev-markup.md](../ready-for-dev-markup.md).
- How Claude Code hooks behave: [hooks.md](../hooks.md). Managed settings for IT: [managed-settings-rollout.md](../managed-settings-rollout.md).
- Decisions: [docs/adr/](../adr/README.md). The plan for the pilot: [phase-3.md](../plans/phase-3.md).

## Keep it true

Every command, flag, file path and rule id in these guides was checked against the code. When the code changes, change the guide in the same pull request. Phase 3 T8 updates the pack for events, credentials and CI.
