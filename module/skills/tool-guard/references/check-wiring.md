---
name: check-wiring
description: Check that the guard hooks and git hooks are wired in this project.
---

# Check Wiring

Run the SessionStart check by hand:

```bash
echo '{}' | python3 {skill-root}/scripts/session_start.py
```

- No output: `core.hooksPath` is `_bmad/custom/guard/hooks` and `.claude/settings.json` holds the guard hooks and deny rules.
- A JSON line with `systemMessage`: it names what is missing. Show it to the user as it is.

Fix it with the bmad-guard setup script, run by the user from their bmad-guard checkout:

```bash
python3 scripts/setup_guard.py --project <this project> --dry-run   # show the change
python3 scripts/setup_guard.py --project <this project>             # apply it
```

Do not edit `.claude/settings.json` or `core.hooksPath` yourself to fix this, and do not remove a hook that blocks you.

If a tool call was blocked by tool-guard, the stderr message lists each reason code and guard. Tell the user which codes were reported; do not retry the same call.
