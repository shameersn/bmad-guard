# Phase 1 install test (T9)

Result: **PASS**: 145 passed, 0 failed.

| | |
|---|---|
| Date | 2026-11-26 01:56 UTC |
| BMAD | 6.12.4 (upgrade from 6.11.0: yes) |
| Tools | claude-code,github-copilot |
| Module | ae1adc7 |
| Python | 3.13.12 |
| OS | Darwin 27.2.0 |
| node | v24.13.0 |
| uv | uv 0.8.17 (10960bc13 2025-09-10) |
| git | git version 2.54.0 (Apple Git-157) |
| Total time | 24s |

## Fresh BMAD 6.12.4

| Check | Result | Time | Detail |
|---|---|---|---|
| install BMAD 6.12.4 | PASS | 2.8s |  |
| setup_guard.py installs, no conflicts | PASS | 0.2s | 144 change(s), 0 conflict(s), 0 unchanged / done |
| core.hooksPath set | PASS |  | _bmad/custom/guard/hooks |
| Claude Code hook wiring in place | PASS |  | hooks, deny rules, scripts |

## Fresh BMAD 6.12.4: quick-update

| Check | Result | Time | Detail |
|---|---|---|---|
| BMAD quick-update | PASS | 1.3s |  |
| guard files untouched | PASS |  | 4 generated file(s) changed |
| core.hooksPath kept | PASS |  | _bmad/custom/guard/hooks |
| Claude Code hook wiring in place | PASS |  | hooks, deny rules, scripts |
| only generated files changed | PASS |  | 4 generated file(s) changed |
| setup_guard.py --check | PASS | 0.1s | 0 change(s), 0 conflict(s), 120 unchanged / up to date |
| stock files unchanged vs baseline commit | PASS |  |  |

## Fresh BMAD 6.12.4: update

| Check | Result | Time | Detail |
|---|---|---|---|
| BMAD update | PASS | 1.4s |  |
| guard files untouched | PASS |  | 4 generated file(s) changed |
| core.hooksPath kept | PASS |  | _bmad/custom/guard/hooks |
| Claude Code hook wiring in place | PASS |  | hooks, deny rules, scripts |
| only generated files changed | PASS |  | 4 generated file(s) changed |
| setup_guard.py --check | PASS | 0.1s | 0 change(s), 0 conflict(s), 120 unchanged / up to date |
| stock files unchanged vs baseline commit | PASS |  |  |

## Fresh BMAD 6.12.4: overrides

| Check | Result | Time | Detail |
|---|---|---|---|
| override agent-approval-gate-check [agent] resolves | PASS | 0.0s | 3 key(s) present |
| override agent-approval-grant [agent] resolves | PASS | 0.0s | 6 key(s) present |
| override bmad-architecture [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-build [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-code-review [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-create-epics-and-stories [workflow] resolves | PASS | 0.1s | 2 key(s) present |
| override bmad-prd [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-product-brief [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-ux [workflow] resolves | PASS | 0.0s | 2 key(s) present |

## Fresh BMAD 6.12.4: guard scripts

| Check | Result | Time | Detail |
|---|---|---|---|
| guard scripts found | PASS |  | 26 script(s) in .claude/skills |
| .claude/skills/agent-approval-gate-check/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-gate-check/scripts/check_gate.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/grant_approval.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/reopen_document.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/guard_events.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/log_action.py runs (--help) | PASS |  |  |
| .claude/skills/confidence-scorer/scripts/confidence_score.py runs (--help) | PASS |  |  |
| .claude/skills/prompt-guard/scripts/prompt_check.py runs (--help) | PASS |  |  |
| .claude/skills/prompt-guard/scripts/prompt_guard_hook.py runs (stdin {}) | PASS |  |  |
| .claude/skills/prompt-guard/scripts/prompt_rules.py runs (import) | PASS |  |  |
| .claude/skills/ready-for-dev-gate/scripts/ready_for_dev.py runs (--help) | PASS |  |  |
| .claude/skills/tool-guard/scripts/bash_write_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/command_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/content_scan.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/document_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/document_text.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/guard_config.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/hook_common.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/hook_wiring.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/path_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/pre_tool_use.py runs (stdin {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}) | PASS |  |  |
| .claude/skills/tool-guard/scripts/review_record.py runs (stdin ) | PASS |  |  |
| .claude/skills/tool-guard/scripts/session_start.py runs (stdin {}) | PASS |  |  |
| .claude/skills/tool-guard/scripts/shell_parse.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/write_gate.py runs (--help) | PASS |  |  |

## Fresh BMAD 6.12.4: guard smoke calls

| Check | Result | Time | Detail |
|---|---|---|---|
| Ledger log_action writes one event to the queue | PASS |  | 1 event(s) queued |
| Sentry check_gate on a missing doc blocks | PASS |  | {"result": "blocked", "reason": "missing"} |
| Warden grant_approval, then Sentry allows | PASS |  | {"result": "ok", "approved_hash": "f0fdc78db3903c877da324d39125fed177fcd722c28ceb9b0de5da4ebce1a7e8", "approved_by": "dev", "approved_at": "2026-11-26T01:56:37.647773+00:00", "review_duration_seconds": null} |
| Warden reopen_document, then Sentry blocks | PASS |  | {"result": "blocked", "reason": "superseded"} |
| ready_for_dev blocks a story with an open question | PASS |  | block ['OPEN_QUESTIONS'] |
| confidence_score scores a file | PASS |  | confidence 100 |
| tool-guard PreToolUse blocks git push --force | PASS | 0.2s |  |
| tool-guard PreToolUse allows git push | PASS | 0.1s |  |
| tool-guard PreToolUse blocks a read of the env file | PASS | 0.2s |  |
| tool-guard PreToolUse allows a read of the env example | PASS | 0.1s |  |
| tool-guard PreToolUse blocks a write to the approval state | PASS | 0.2s |  |
| tool-guard PreToolUse blocks a new document that says approved | PASS | 0.2s |  |
| tool-guard PreToolUse blocks a shell write to the approval state | PASS | 0.2s |  |
| tool-guard PreToolUse allows a shell read of the approval state | PASS | 0.1s |  |
| tool-guard PreToolUse allows a new document that waits for approval | PASS | 0.1s |  |
| tool-guard PreToolUse blocks a source write when no plan is in progress | PASS | 0.2s |  |
| tool-guard PreToolUse blocks a shell write to source when no plan is in progress | PASS | 0.2s |  |
| tool-guard PreToolUse allows a shell write to /dev/null | PASS | 0.1s |  |
| tool-guard PreToolUse allows a shell write to a scratch folder | PASS | 0.1s |  |
| write_gate --check blocks when no plan is in progress | PASS | 0.1s |  |
| tool-guard PreToolUse blocks a source write while the plan is not approved | PASS | 0.2s | [tool-guard] Blocked Write: / - GATE_PLAN_NOT_APPROVED (write-gate): The plan _bmad-output/implementation-artifacts/spec-smoke.md is not approved. Ask the developer to approve it with Warden (agent-approval-grant). Never write approval_status: approved yourself. / Do not retry the same call. Change the approach, or ask the developer. |
| Warden approves a fake plan, then the source write passes the hook | PASS | 0.2s |  |
| the hook records the build session id | PASS |  | sessions ['s-smoke-build'] |
| write_gate --check passes for the approved plan | PASS | 0.1s |  |
| tool-guard PreToolUse blocks a fake key in a written file | PASS | 0.1s |  |
| tool-guard PreToolUse allows a plain written file | PASS | 0.1s |  |
| tool-guard PreToolUse warns about PHI in a non-HIPAA project | PASS | 0.2s |  |
| tool-guard PreToolUse blocks PHI in a HIPAA project | PASS | 0.2s |  |
| pre-commit hook is installed and executable | PASS |  | _bmad/custom/guard/hooks/pre-commit |
| pre-commit with nothing staged exits 0 at once | PASS | 0.2s |  |
| git commit with a fake key is blocked by the pre-commit hook | PASS |  | bmad-guard pre-commit: the commit is blocked (1 reason). / - COMMIT_SECRET: secrets in the lines this commit adds: docs/hook-key.txt (secret.github-token). Remove the value (read it from an environment variable), or use an obvious fake such as the word example. If it is a fake, add it to the allow list in _bmad/custom/project/guard/prompt-patterns.json. / Fix this, stage the files again and commit again. Do not use --no-verify. |
| git commit of a plain file goes through the pre-commit hook | PASS |  |  |
| git commit of a hand-set approved status is blocked | PASS |  | bmad-guard pre-commit: the commit is blocked (1 reason). / - COMMIT_APPROVAL_NO_ENTRY: docs/hook-forged.md: the approval status is approved (or approved_by / approved_at changed), but _bmad/custom/approval-state.json has no matching Warden approval for it that is still in force. Only Warden (agent-approval-grant, Grant Approval) sets these fields, after the developer confirms. Undo the change, or ask the developer to run Warden on the document. / Fix this, stage the files again and commit again. Do not use --no-verify. |
| git commit of a draft document goes through | PASS |  |  |
| Warden approves and commits through the pre-commit hook | PASS |  | {"ok": true, "message": "Approve hook-brief v1"} |
| the hook queued its events | PASS |  | COMMIT_NOTHING_TO_CHECK, COMMIT_SECRET, COMMIT_ALLOWED, COMMIT_APPROVAL_NO_ENTRY |
| pre-push hook is installed and executable | PASS |  | _bmad/custom/guard/hooks/pre-push |
| set-up: the sandbox history is on the bare remote | PASS |  |  |
| set-up: a source commit goes through the pre-commit hook | PASS |  |  |
| git push of source with no review record is blocked by the pre-push hook | PASS | 0.3s | - PUSH_NO_RECORD (refs/heads/smoke-push): 1 commit(s) change source (b6823df629), and the push has no review record. Run bmad-code-review in a fresh session, commit _bmad-output/guard/reviews/<story>.json on its own, then push again. / Fix this and push again. Do not use --no-verify. / error: failed to push some refs to '/var/folders/fm/c1g8rjks1v58jgjr89py6p5h0000gr/T/bmad-guard-install-test-8l4l4xu2/fresh-remote.git' |
| the tool-guard hook writes the review record with the hook's session id | PASS | 0.2s |  |
| the review record is committed through the pre-commit hook | PASS |  |  |
| git push with a valid review record goes through the pre-push hook | PASS | 0.4s | To /var/folders/fm/c1g8rjks1v58jgjr89py6p5h0000gr/T/bmad-guard-install-test-8l4l4xu2/fresh-remote.git /  * [new branch]      smoke-push -> smoke-push |
| the pre-push hook queued its events | PASS |  | PUSH_NO_RECORD, PUSH_ALLOWED |

## Upgrade 6.11.0 -> 6.12.4

| Check | Result | Time | Detail |
|---|---|---|---|
| install BMAD 6.11.0 | PASS | 1.4s |  |
| setup_guard.py installs, no conflicts | PASS | 0.2s | 144 change(s), 0 conflict(s), 0 unchanged / done |
| core.hooksPath set | PASS |  | _bmad/custom/guard/hooks |
| Claude Code hook wiring in place | PASS |  | hooks, deny rules, scripts |
| upgrade to BMAD 6.12.4 | PASS | 1.4s |  |
| guard files untouched | PASS |  | 7 generated file(s) changed |
| core.hooksPath kept | PASS |  | _bmad/custom/guard/hooks |
| Claude Code hook wiring in place | PASS |  | hooks, deny rules, scripts |
| stock files changed by the upgrade (info) | PASS |  | 301 file(s) |
| setup_guard.py --check | PASS | 0.1s | 0 change(s), 0 conflict(s), 120 unchanged / up to date |
| deprecated shim skills kept (info, expected) | PASS |  | bmad-dev-story, bmad-quick-dev, bmad-create-story |

## Upgrade 6.11.0 -> 6.12.4: overrides

| Check | Result | Time | Detail |
|---|---|---|---|
| override agent-approval-gate-check [agent] resolves | PASS | 0.0s | 3 key(s) present |
| override agent-approval-grant [agent] resolves | PASS | 0.0s | 6 key(s) present |
| override bmad-architecture [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-build [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-code-review [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-create-epics-and-stories [workflow] resolves | PASS | 0.0s | 2 key(s) present |
| override bmad-prd [workflow] resolves | PASS | 0.0s | 3 key(s) present |
| override bmad-product-brief [workflow] resolves | PASS | 0.1s | 3 key(s) present |
| override bmad-ux [workflow] resolves | PASS | 0.0s | 2 key(s) present |

## Upgrade 6.11.0 -> 6.12.4: guard scripts

| Check | Result | Time | Detail |
|---|---|---|---|
| guard scripts found | PASS |  | 26 script(s) in .claude/skills |
| .claude/skills/agent-approval-gate-check/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-gate-check/scripts/check_gate.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/approval_core.py runs (import) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/grant_approval.py runs (--help) | PASS |  |  |
| .claude/skills/agent-approval-grant/scripts/reopen_document.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/guard_events.py runs (--help) | PASS |  |  |
| .claude/skills/agent-project-logger/scripts/log_action.py runs (--help) | PASS |  |  |
| .claude/skills/confidence-scorer/scripts/confidence_score.py runs (--help) | PASS |  |  |
| .claude/skills/prompt-guard/scripts/prompt_check.py runs (--help) | PASS |  |  |
| .claude/skills/prompt-guard/scripts/prompt_guard_hook.py runs (stdin {}) | PASS |  |  |
| .claude/skills/prompt-guard/scripts/prompt_rules.py runs (import) | PASS |  |  |
| .claude/skills/ready-for-dev-gate/scripts/ready_for_dev.py runs (--help) | PASS |  |  |
| .claude/skills/tool-guard/scripts/bash_write_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/command_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/content_scan.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/document_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/document_text.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/guard_config.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/hook_common.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/hook_wiring.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/path_guard.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/pre_tool_use.py runs (stdin {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}) | PASS |  |  |
| .claude/skills/tool-guard/scripts/review_record.py runs (stdin ) | PASS |  |  |
| .claude/skills/tool-guard/scripts/session_start.py runs (stdin {}) | PASS |  |  |
| .claude/skills/tool-guard/scripts/shell_parse.py runs (import) | PASS |  |  |
| .claude/skills/tool-guard/scripts/write_gate.py runs (--help) | PASS |  |  |

Generated files (allowed to change on every BMAD run): _bmad/bmm/config.yaml, _bmad/config.toml, _bmad/core/config.yaml, _bmad/_config/*.

Re-run: `python3 scripts/install_test.py --report docs/reports/phase-1-install-test.md`
