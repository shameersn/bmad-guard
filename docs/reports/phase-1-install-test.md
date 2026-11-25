# Phase 1 install test (T9)

Result: **PASS**: 72 passed, 0 failed.

| | |
|---|---|
| Date | 2026-11-25 06:35 UTC |
| BMAD | 6.12.4 (upgrade from 6.11.0: yes) |
| Tools | claude-code,github-copilot |
| Module | dbcf9f1 |
| Python | 3.13.12 |
| OS | Darwin 27.2.0 |
| node | v24.13.0 |
| uv | uv 0.8.17 (10960bc13 2025-09-10) |
| git | git version 2.54.0 (Apple Git-157) |
| Total time | 14s |

## Fresh BMAD 6.12.4

| Check | Result | Time | Detail |
|---|---|---|---|
| install BMAD 6.12.4 | PASS | 1.4s |  |
| setup_guard.py installs, no conflicts | PASS | 0.2s | 76 change(s), 0 conflict(s), 0 unchanged / done |
| core.hooksPath set | PASS |  | _bmad/custom/guard/hooks |

## Fresh BMAD 6.12.4: quick-update

| Check | Result | Time | Detail |
|---|---|---|---|
| BMAD quick-update | PASS | 1.5s |  |
| guard files untouched | PASS |  | 4 generated file(s) changed |
| core.hooksPath kept | PASS |  | _bmad/custom/guard/hooks |
| only generated files changed | PASS |  | 4 generated file(s) changed |
| setup_guard.py --check | PASS | 0.1s | 0 change(s), 0 conflict(s), 76 unchanged / up to date |
| stock files unchanged vs baseline commit | PASS |  |  |

## Fresh BMAD 6.12.4: update

| Check | Result | Time | Detail |
|---|---|---|---|
| BMAD update | PASS | 1.5s |  |
| guard files untouched | PASS |  | 4 generated file(s) changed |
| core.hooksPath kept | PASS |  | _bmad/custom/guard/hooks |
| only generated files changed | PASS |  | 4 generated file(s) changed |
| setup_guard.py --check | PASS | 0.1s | 0 change(s), 0 conflict(s), 76 unchanged / up to date |
| stock files unchanged vs baseline commit | PASS |  |  |

## Fresh BMAD 6.12.4: overrides

| Check | Result | Time | Detail |
|---|---|---|---|
| override agent-approval-gate-check [agent] resolves | PASS | 0.0s | 3 key(s) present |
| override agent-approval-grant [agent] resolves | PASS | 0.0s | 6 key(s) present |
| override bmad-architecture [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-build [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-code-review [workflow] resolves | PASS | 0.0s | 1 key(s) present |
| override bmad-create-epics-and-stories [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-prd [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-product-brief [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-ux [workflow] resolves | PASS | 0.0s | 2 key(s) present |

## Fresh BMAD 6.12.4: guard scripts

| Check | Result | Time | Detail |
|---|---|---|---|
| guard scripts found | PASS |  | 11 script(s) in .claude/skills |
| .claude/skills/agent-approval-gate-check/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-gate-check/scripts/check_gate.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/grant_approval.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/reopen_document.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/guard_events.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/log_action.py runs (--help) | PASS |  |  |
| .claude/skills/confidence-scorer/scripts/confidence_score.py runs (--help) | PASS |  |  |
| .claude/skills/prompt-gateway-guard/scripts/prompt_gateway_hook.py runs (stdin {}) | PASS |  |  |
| .claude/skills/prompt-gateway-guard/scripts/validate_prompt.py runs (--help) | PASS |  |  |
| .claude/skills/ready-for-dev-gate/scripts/ready_for_dev.py runs (--help) | PASS |  |  |

## Fresh BMAD 6.12.4: guard smoke calls

| Check | Result | Time | Detail |
|---|---|---|---|
| Ledger log_action writes one event to the queue | PASS |  | 1 event(s) queued |
| Sentry check_gate on a missing doc blocks | PASS |  | {"result": "blocked", "reason": "missing"} |
| Warden grant_approval, then Sentry allows | PASS |  | {"result": "ok", "approved_hash": "f0fdc78db3903c877da324d39125fed177fcd722c28ceb9b0de5da4ebce1a7e8", "approved_by": "dev", "approved_at": "2026-11-25T06:35:18.018185+00:00", "review_duration_seconds": null} |
| Warden reopen_document, then Sentry blocks | PASS |  | {"result": "blocked", "reason": "superseded"} |
| ready_for_dev blocks a story with an open question | PASS |  | block ['OPEN_QUESTIONS'] |
| confidence_score scores a file | PASS |  | confidence 100 |

## Upgrade 6.11.0 -> 6.12.4

| Check | Result | Time | Detail |
|---|---|---|---|
| install BMAD 6.11.0 | PASS | 1.6s |  |
| setup_guard.py installs, no conflicts | PASS | 0.2s | 76 change(s), 0 conflict(s), 0 unchanged / done |
| core.hooksPath set | PASS |  | _bmad/custom/guard/hooks |
| upgrade to BMAD 6.12.4 | PASS | 1.6s |  |
| guard files untouched | PASS |  | 7 generated file(s) changed |
| core.hooksPath kept | PASS |  | _bmad/custom/guard/hooks |
| stock files changed by the upgrade (info) | PASS |  | 301 file(s) |
| setup_guard.py --check | PASS | 0.1s | 0 change(s), 0 conflict(s), 76 unchanged / up to date |
| deprecated shim skills kept (info, expected) | PASS |  | bmad-dev-story, bmad-quick-dev, bmad-create-story |

## Upgrade 6.11.0 -> 6.12.4: overrides

| Check | Result | Time | Detail |
|---|---|---|---|
| override agent-approval-gate-check [agent] resolves | PASS | 0.0s | 3 key(s) present |
| override agent-approval-grant [agent] resolves | PASS | 0.0s | 6 key(s) present |
| override bmad-architecture [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-build [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-code-review [workflow] resolves | PASS | 0.0s | 1 key(s) present |
| override bmad-create-epics-and-stories [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-prd [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-product-brief [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-ux [workflow] resolves | PASS | 0.0s | 2 key(s) present |

## Upgrade 6.11.0 -> 6.12.4: guard scripts

| Check | Result | Time | Detail |
|---|---|---|---|
| guard scripts found | PASS |  | 11 script(s) in .claude/skills |
| .claude/skills/agent-approval-gate-check/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-gate-check/scripts/check_gate.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/grant_approval.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/reopen_document.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/guard_events.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/log_action.py runs (--help) | PASS |  |  |
| .claude/skills/confidence-scorer/scripts/confidence_score.py runs (--help) | PASS |  |  |
| .claude/skills/prompt-gateway-guard/scripts/prompt_gateway_hook.py runs (stdin {}) | PASS |  |  |
| .claude/skills/prompt-gateway-guard/scripts/validate_prompt.py runs (--help) | PASS |  |  |
| .claude/skills/ready-for-dev-gate/scripts/ready_for_dev.py runs (--help) | PASS |  |  |

Generated files (allowed to change on every BMAD run): _bmad/bmm/config.yaml, _bmad/config.toml, _bmad/core/config.yaml, _bmad/_config/*.

Re-run: `python3 scripts/install_test.py --report docs/reports/phase-1-install-test.md`
