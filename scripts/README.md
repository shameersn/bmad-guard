# Scripts

Repo tools: setup script (T2), install test (T9). The ready-for-dev gate (T7) is a skill: `module/skills/ready-for-dev-gate/`. The local prompt check (T8) is a skill too: `module/skills/prompt-guard/`.

## Install test (T9, Gate 1)

`install_test.py` installs BMAD and the guard module into throwaway git sandboxes (a new temp folder)
and checks that the module survives BMAD quick-update, full update and a 6.11.0 → 6.12.4 upgrade:
only generated files change, guard files and `core.hooksPath` stay, the Claude Code hook wiring in `.claude/settings.json` stays (Phase 2, T1), every override resolves with
BMAD's `resolve_customization.py`, every guard script runs (`--help` or a smoke call with fake data), and the write gate works end to end (a source write is blocked with no plan; after Warden approves a fake plan it passes and the build session is recorded; Phase 2, T4).

Needs network (npx downloads `bmad-method@6.12.4` and `@6.11.0`), Node, `uv` and git
(uv also provides the Python 3.11+ used to read the override TOML files).
It is not part of `python3 -m unittest discover`; its helpers are unit-tested in `tests/test_install_test.py`.

```bash
python3 scripts/install_test.py --report docs/reports/phase-1-install-test.md
python3 scripts/install_test.py --keep            # keep the sandboxes and the command log
python3 scripts/install_test.py --skip-upgrade    # fresh 6.12.4 only
```

Exit code: 0 all checks pass, 1 a check failed, 2 a tool is missing.
