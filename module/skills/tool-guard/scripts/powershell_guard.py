# /// script
# requires-python = ">=3.10"
# ///
"""PowerShell tool guard (GUARD-3 T9, ADR 0009): block every PowerShell tool call in a guarded project. Fails closed.

Claude Code has a PowerShell tool (`tool_name` "PowerShell", `tool_input.command`). It is on by default for claude.ai
accounts on Windows and opt-in on macOS and Linux. No guard here can read PowerShell: the command parser is for POSIX
shells, and the write guards know no PowerShell or cmd write verbs. Without this guard the tool skips every L1 guard,
including the one for approval-state.json. So the call is blocked and the model is told to use the Bash tool.
Real PowerShell guards are Phase 4.

The reason code is PS_TOOL_UNSUPPORTED. The event and the message never carry the command text.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import hook_common as hc  # noqa: E402 - sibling module, found through the line above

GUARD = "powershell-guard"
REASON = (
    "The guard cannot check PowerShell commands, so the PowerShell tool is off in this project. "
    "Use the Bash tool instead (Git Bash on Windows)."
)


def check(payload: dict, root) -> list[hc.Verdict]:
    return [hc.Verdict("PS_TOOL_UNSUPPORTED", REASON, GUARD)]


GUARDS = [hc.Guard(GUARD, ("PowerShell",), check)]
