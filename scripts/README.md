# Scripts

Repo tools: setup script (T2), install test (T9), red-team suite (Phase 2, T8). The ready-for-dev gate (T7) is a skill: `module/skills/ready-for-dev-gate/`. The local prompt check (T8) is a skill too: `module/skills/prompt-guard/`.

## Install test (T9, Gate 1)

`install_test.py` installs BMAD and the guard module into throwaway git sandboxes (a new temp folder)
and checks that the module survives BMAD quick-update, full update and a 6.11.0 → 6.12.4 upgrade:
only generated files change, guard files and `core.hooksPath` stay, the Claude Code hook wiring in `.claude/settings.json` stays (Phase 2, T1), every override resolves with
BMAD's `resolve_customization.py`, every guard script runs (`--help` or a smoke call with fake data), the write gate works end to end (a source write is blocked with no plan; after Warden approves a fake plan it passes and the build session is recorded; Phase 2, T4), and the pre-commit hook works through real `git commit` calls (a fake key and a hand-set approved status are blocked, a plain file and a Warden approval go through; Phase 2, T6).

Needs network (npx downloads `bmad-method@6.12.4` and `@6.11.0`), Node, `uv` and git
(uv also provides the Python 3.11+ used to read the override TOML files).
It is not part of `python3 -m unittest discover`; its helpers are unit-tested in `tests/test_install_test.py`.

```bash
python3 scripts/install_test.py --report docs/reports/phase-1-install-test.md
python3 scripts/install_test.py --keep            # keep the sandboxes and the command log
python3 scripts/install_test.py --skip-upgrade    # fresh 6.12.4 only
```

Exit code: 0 all checks pass, 1 a check failed, 2 a tool is missing.

## Red-team suite (Phase 2, T8, Gate 2)

`red_team.py` attacks the installed guards in two throwaway BMAD 6.12.4 sandboxes (one HIPAA project with `phi = "block"`, one general), using fake data only. It drives the **real installed entry points**:

- **L1, Claude Code hooks:** it reads the hook commands from the sandbox's `.claude/settings.json` and runs them as Claude Code does (a shell, `CLAUDE_PROJECT_DIR` set, the hook JSON on stdin). A call only reaches a hook if the wiring's matcher covers its tool. No model is involved.
- **L2, git hooks:** real `git commit` and `git push` (to a bare remote in the temp folder) run the installed pre-commit and pre-push hooks.

Every case has an expected answer (block, allow or warn) and an expected rule id, and the report lists layer, entry point, expected, actual, rule seen and PASS / FAIL. Control rows are close-but-safe actions that must pass. The four rollout-doc cases (PHI, wrong DB, edit after approval, same-session review) are listed on their own. A case that did not run is reported as NOT RUN, never as PASS. The last case checks that no fake value and no command text is in any queued event.

Needs network (npx downloads `bmad-method@6.12.4`), Node, `uv` and git. Not part of `python3 -m unittest discover`; its helpers, the case table and every case that needs no state (run against the real hook in an empty project) are tested offline in `tests/test_red_team.py`.

```bash
python3 scripts/red_team.py --report docs/reports/phase-2-red-team.md
python3 scripts/red_team.py --keep                  # keep the sandboxes and the command log
python3 scripts/red_team.py --python python3.10     # run the hooks with another interpreter as python3
```

Exit code: 0 every case passed, 1 a case failed or did not run, 2 a tool is missing. Only the temp folder the script creates is removed (unless `--keep`).
