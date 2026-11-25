# /// script
# requires-python = ">=3.10"
# ///
"""Write gate (Phase 2, T4): no source write without an approved, unchanged plan and a ready story. Fails closed.

Checked on Write, Edit, MultiEdit and NotebookEdit (`file_path` / `notebook_path`) and on the explicit write targets
of a Bash command (redirects, `tee`, `cp` / `mv` destinations, `sed -i`, `rm`, ... the same reader as
bash_write_guard.py). A write is gated when its path is inside the project and not in an allowed path or a scratch
folder. The reason code is the rule id:

  GATE_NO_ACTIVE_PLAN       no bmad-build plan with `status: in-progress` (or `in-review`, see below).
  GATE_MANY_ACTIVE_PLANS    more than one plan is in progress; the guard cannot tell which story is being built.
  GATE_PLAN_NOT_APPROVED    the plan has no approval, or the approval was reopened (Sentry: unapproved, superseded).
  GATE_PLAN_CHANGED         the plan was edited after it was approved (Sentry: stale; its build hash no longer matches).
  GATE_STATE_UNREADABLE     _bmad/custom/approval-state.json cannot be read (Sentry: state_unreadable).
  GATE_STORY_NOT_FOUND      the plan's `story` field names a story file that cannot be found (or is not unique).
  GATE_READY_<CODE>         the ready-for-dev gate (ready_for_dev.py) blocks the story. One id per reason it gives:
                            GATE_READY_OPEN_QUESTIONS, _OPEN_ASSUMPTIONS, _LOW_CONFIDENCE, _MISSING_CONFIDENCE,
                            _RESOLUTION_MISSING_WHO_OR_WHEN, _DELETED_ITEMS, _DUPLICATE_IDS, _MALFORMED_FRONTMATTER,
                            _MISSING_EPIC_SUMMARY, _EPIC_SUMMARY_INCOMPLETE, _UNREADABLE_STORY, _BASELINE_UNAVAILABLE.
  GATE_RECORD_FAILED        the build session record could not be written (T7 needs it), so the write is blocked.
  GATE_ERROR                the gate cannot run (a sibling skill is missing, too many files to scan).

The plan check runs first, then the ready-for-dev check; one answer lists every reason. The CLI (`--check`) prints
the same reasons before coding starts.

The current plan is the Markdown file under `_bmad-output/` that bmad-build marked `status: in-progress` and that has a
plan marker (`route`, `approval_status`, `review_loop_iteration` or `baseline_commit`). Plans in `in-review` count only
when no plan is in progress: bmad-build patches code during review, and an old plan waiting for review must not hide
the next story's plan. The story is the file named by the plan's `story` field (a project path, or an id such as
`2-3` that matches `<id>.md` or `<id>-*.md` under `_bmad-output/`); a plan without `story` is its own story.

Sentry (agent-approval-gate-check) and the ready-for-dev gate are loaded from their sibling skill folders and called
as libraries; their rules are not copied here. The hook only asks: it does not reset a stale plan (Sentry's own run
does that).

When a write passes, the hook records the build session id (from the hook input `session_id`, never from the model)
in `_bmad-output/guard/builds/<story>.json` (see `record_build_session`). The event and the message carry the rule id
and the plan or story name, never file content.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import bash_write_guard as bwg  # noqa: E402 - sibling modules, found through the line above
import command_guard as cg  # noqa: E402
import document_guard as dg  # noqa: E402
import document_text as dt  # noqa: E402
import guard_config as gc  # noqa: E402
import hook_common as hc  # noqa: E402
import shell_parse as sp  # noqa: E402

GUARD = "write-gate"
TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash")
ALLOWED_PATHS = ("_bmad", "_bmad-output", "docs", ".claude", ".agents", ".bmad-guard")  # project root relative
PLAN_FOLDER = "_bmad-output"
STATE_REL = "_bmad/custom/approval-state.json"
RECORD_REL = "_bmad-output/guard/builds"
READY_SKILL = "ready-for-dev-gate"
SENTRY_SKILL = "agent-approval-gate-check"
IN_PROGRESS, IN_REVIEW = "in-progress", "in-review"
PLAN_MARKERS = {"route", "approval_status", "review_loop_iteration", "baseline_commit"}
MARKDOWN = (".md", ".markdown")
MAX_FILES = 5000  # Markdown files looked at under _bmad-output
HEAD_BYTES = 16384  # the frontmatter of a plan is read from the first bytes only
SKIP_FOLDERS = {"node_modules", ".git"}
KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
NEXT_PLAN = (
    "Run bmad-build to write the plan, ask the developer to approve it with Warden (agent-approval-grant), "
    "then set the plan's status to in-progress."
)
READY_NEXT = (
    "A person answers questions and resolves assumptions (a Resolved: line with name and date). "
    "Do not edit them, or the gate marker, to pass."
)
CONSOLE = "tool-guard write gate"


class GateError(Exception):
    """The gate cannot run. The message says why (never file content)."""


@dataclass(frozen=True)
class Plan:
    path: Path  # real path
    rel: str  # project-relative, with /
    status: str
    fields: dict


@dataclass(frozen=True)
class Outcome:
    verdicts: list[hc.Verdict]
    plan: Plan | None = None
    story: Path | None = None  # None when no plan was found


def verdict(code: str, reason: str) -> hc.Verdict:
    return hc.Verdict(code, reason, GUARD)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- sibling skills ------------------------------------------------------------------------------------


def _load(skill: str, script: str):
    """A script of a sibling skill, loaded once. GateError when the skill is not installed."""
    if script in sys.modules:
        return sys.modules[script]
    path = HERE.parents[1] / skill / "scripts" / f"{script}.py"
    if not path.is_file():
        raise GateError(f"the {skill} skill is not installed, so the gate cannot check")
    spec = importlib.util.spec_from_file_location(script, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[script] = module
    spec.loader.exec_module(module)
    return module


def ready_config(root: Path) -> tuple[int, str, str]:
    """(min_confidence, epics_dir, baseline) from the ready-for-dev skill's own customize.toml. A project override
    file is not read: it is not protected, and a lower minimum would weaken the gate."""
    path = HERE.parents[1] / READY_SKILL / "customize.toml"
    try:
        agent = gc.parse_toml(path.read_text(encoding="utf-8")).get("agent", {})
        minimum = agent.get("min_confidence", 80)
        epics, baseline = agent.get("epics_dir", ""), agent.get("baseline", "")
    except (OSError, ValueError) as err:
        raise GateError(f"the ready-for-dev settings cannot be read ({type(err).__name__})") from None
    if not (isinstance(minimum, int) and 0 <= minimum <= 100 and isinstance(epics, str) and isinstance(baseline, str)):
        raise GateError("the ready-for-dev settings are not in the expected format")
    return minimum, epics.replace("{project-root}", str(root)), baseline


# --- the plan ------------------------------------------------------------------------------------------


def _scalar(value: str, keep_case: bool = False) -> str:
    """A frontmatter value without quotes and a trailing `# comment` (the bmad-build template has one)."""
    text = dt.COMMENT_RE.sub("", value).strip().strip("'\"").strip()
    return text if keep_case else text.lower()


def scan_plans(root: Path) -> list[Plan]:
    """Every Markdown file under _bmad-output with an active `status` and a plan marker. `root` is a real path."""
    rfd = dt.ready_for_dev()
    base = root / PLAN_FOLDER
    plans: list[Plan] = []
    seen = 0
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_FOLDERS)
        for name in sorted(files):
            if not name.lower().endswith(MARKDOWN):
                continue
            seen += 1
            if seen > MAX_FILES:
                raise GateError(f"more than {MAX_FILES} Markdown files under {PLAN_FOLDER}, so no plan can be found")
            path = Path(os.path.realpath(Path(folder) / name))
            if not str(path).startswith(str(root) + os.sep):
                continue  # a link to a file outside the project
            try:
                with open(path, "rb") as handle:
                    head = handle.read(HEAD_BYTES).decode("utf-8", errors="replace")
            except OSError:
                continue
            fields, _, error = rfd.parse_frontmatter(head)
            status = _scalar(fields.get("status", ""))
            if error or status not in (IN_PROGRESS, IN_REVIEW) or not PLAN_MARKERS & set(fields):
                continue
            plans.append(Plan(path, path.relative_to(root).as_posix(), status, fields))
    return plans


def current_plan(root: Path) -> tuple[Plan | None, list[hc.Verdict]]:
    plans = scan_plans(root)
    chosen = [p for p in plans if p.status == IN_PROGRESS] or [p for p in plans if p.status == IN_REVIEW]
    if not chosen:
        return None, [
            verdict(
                "GATE_NO_ACTIVE_PLAN",
                f"No bmad-build plan is in progress, so source changes are not allowed yet. {NEXT_PLAN}",
            )
        ]
    if len(chosen) > 1:
        names = ", ".join(p.rel for p in chosen[:5]) + (" ..." if len(chosen) > 5 else "")
        return None, [
            verdict(
                "GATE_MANY_ACTIVE_PLANS",
                f"{len(chosen)} plans are in progress at once ({names}), so the story being built is not clear. "
                "Ask the developer to finish or set aside all but one (set its status to done or draft).",
            )
        ]
    return chosen[0], []


def plan_reasons(root: Path, plan: Plan) -> list[hc.Verdict]:
    """Sentry's answer for the plan, as write gate reasons. The hook does not reset a stale plan."""
    sentry = _load(SENTRY_SKILL, "check_gate")
    result, _ = sentry.check(
        plan.path,
        f"build:{plan.rel}",
        root / STATE_REL,
        "approval_status",
        "pending-approval",
        "approved",
        reset_stale=False,
    )
    why = result.get("reason", "")
    if result.get("result") == "ok":
        return []
    if why == "state_unreadable":
        return [
            verdict(
                "GATE_STATE_UNREADABLE",
                f"{STATE_REL} cannot be read, so the plan's approval cannot be checked. "
                "Ask the developer to fix or restore it (it is in git).",
            )
        ]
    if why == "stale":
        return [
            verdict(
                "GATE_PLAN_CHANGED",
                f"The plan {plan.rel} was edited after it was approved. "
                "Ask the developer to read the change and approve the plan again with Warden (agent-approval-grant).",
            )
        ]
    reopened = " The approval was reopened." if why == "superseded" else ""
    return [
        verdict(
            "GATE_PLAN_NOT_APPROVED",
            f"The plan {plan.rel} is not approved.{reopened} Ask the developer to approve it with Warden "
            "(agent-approval-grant). Never write approval_status: approved yourself.",
        )
    ]


# --- the story -----------------------------------------------------------------------------------------


def find_story(root: Path, plan: Plan) -> tuple[Path | None, hc.Verdict | None]:
    """The story file of a plan: the plan itself when it has no `story` field."""
    value = _scalar(plan.fields.get("story", ""), keep_case=True)
    if not value:
        return plan.path, None

    def missing(why: str) -> tuple[None, hc.Verdict]:
        return None, verdict(
            "GATE_STORY_NOT_FOUND",
            f"The plan {plan.rel} names a story that {why}. Ask the developer to fix the plan's story field "
            f"(a project path, or an id that matches a file name under {PLAN_FOLDER}).",
        )

    if "/" in value or value.lower().endswith(MARKDOWN):
        path = Path(os.path.realpath(root / value))
        if not (str(path).startswith(str(root) + os.sep) and path.is_file()):
            return missing("is not a file in the project")
        return path, None
    found = []
    for folder, dirs, files in os.walk(root / PLAN_FOLDER):
        dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS]
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() in MARKDOWN and (stem == value or stem.startswith(value + "-")):
                path = Path(os.path.realpath(Path(folder) / name))
                if path != plan.path and str(path).startswith(str(root) + os.sep):
                    found.append(path)
    if len(found) != 1:
        return missing("cannot be found" if not found else "matches more than one file")
    return found[0], None


def story_reasons(root: Path, story: Path) -> list[hc.Verdict]:
    rfd = dt.ready_for_dev()
    minimum, epics_dir, baseline = ready_config(root)
    rel = story.relative_to(root).as_posix()
    try:
        text = story.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        return [verdict("GATE_READY_UNREADABLE_STORY", f"The story {rel} cannot be read ({type(err).__name__}).")]
    try:
        result = rfd.evaluate(text, story, minimum, None, epics_dir or None, baseline or None)
    except rfd.GateError as err:
        return [verdict(f"GATE_READY_{err.code}", f"The story {rel} cannot be checked: {err.detail}.")]
    return [
        verdict(f"GATE_READY_{r['code']}", f"The story {rel} is not ready for development: {r['detail']}. {READY_NEXT}")
        for r in result["reasons"]
    ]


def check_gate(root: Path) -> Outcome:
    """The plan check, then the ready-for-dev check. `root` is the project root. Raises GateError."""
    root = Path(os.path.realpath(root))
    plan, found = current_plan(root)
    if plan is None:
        return Outcome(found)
    story, missing = find_story(root, plan)
    verdicts = plan_reasons(root, plan)
    verdicts += [missing] if missing else story_reasons(root, story)
    return Outcome(verdicts, plan, story)


# --- the build session record (for T7) -----------------------------------------------------------------


def story_key(plan: Plan, story: Path | None) -> str:
    """The name of a story in the build record: the plan's `story` value when it is a plain id (`2-3`), else the file
    name of the story (or of the plan when it names none)."""
    value = _scalar(plan.fields.get("story", ""), keep_case=True)
    if KEY_RE.fullmatch(value) and not value.lower().endswith(MARKDOWN):
        return value
    stem = re.sub(r"[^A-Za-z0-9._-]", "-", (story or plan.path).stem).lstrip("._-")[:100].rstrip("-")
    return stem or "story"


def record_path(root: Path, key: str) -> Path:
    return root / RECORD_REL / f"{key}.json"


def record_build_session(root: Path, key: str, session_id: str) -> None:
    """Add the session id to `_bmad-output/guard/builds/<story>.json`: {"story", "sessions", "first_at", "last_at"}.
    One atomic write. Raises OSError or ValueError when the record cannot be read or written."""
    path = record_path(root, key)
    stamp = now()
    record = {"story": key, "sessions": [], "first_at": stamp, "last_at": stamp}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ValueError(f"the build record cannot be read ({type(err).__name__})") from None
        if not (
            isinstance(old, dict)
            and isinstance(old.get("sessions"), list)
            and all(isinstance(s, str) for s in old["sessions"])
            and isinstance(old.get("first_at"), str)
        ):
            raise ValueError("the build record is not in the expected format")
        record["sessions"], record["first_at"] = old["sessions"], old["first_at"]
    if session_id not in record["sessions"]:
        record["sessions"].append(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(record, indent=2) + "\n")
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def record_reasons(root: Path, outcome: Outcome, payload: dict) -> list[hc.Verdict]:
    session = payload.get("session_id")
    if not (isinstance(session, str) and SESSION_RE.fullmatch(session)):
        return [
            verdict(
                "GATE_RECORD_FAILED",
                "The hook input has no usable session id, so the build session cannot be recorded. "
                "Ask the developer to check the hook setup.",
            )
        ]
    try:
        record_build_session(root, story_key(outcome.plan, outcome.story), session)
    except (OSError, ValueError) as err:
        return [
            verdict(
                "GATE_RECORD_FAILED",
                f"The build session record in {RECORD_REL} could not be updated ({type(err).__name__}), "
                "and the independent review needs it. Ask the developer to fix the folder or the file.",
            )
        ]
    return []


# --- which writes are gated ----------------------------------------------------------------------------


class Paths:
    """Decides, for one call, whether a path needs the gate: inside the project, not allowed, not scratch."""

    def __init__(self, payload: dict, root: Path):
        self.root = Path(os.path.realpath(root))
        cwd = payload.get("cwd")
        self.cwd = Path(os.path.realpath(cwd if isinstance(cwd, str) and cwd and Path(cwd).is_absolute() else root))
        self.home = os.path.realpath(Path.home())
        try:
            rules = gc.load(root)
        except gc.ExtensionError:
            # A broken extension file adds nothing here (stricter). path-guard and command-guard report the file.
            commands, scratch, hosts = gc.load_builtin()
            rules = gc.Rules(tuple(commands), tuple(scratch), tuple(hosts), ())
        self.ctx = cg.make_context(payload, root, rules)
        self.allowed = [(False, (name,)) for name in ALLOWED_PATHS]
        for entry in rules.write_allowed:
            anywhere = entry.startswith("**/")
            parts = (entry[3:] if anywhere else entry).split("/")
            self.allowed.append((anywhere, tuple(p.lower() for p in parts if p)))

    def _allowed(self, parts: tuple[str, ...]) -> bool:
        low = tuple(p.lower() for p in parts)
        for anywhere, folder in self.allowed:
            if low[: len(folder)] == folder or (
                anywhere and any(low[i : i + len(folder)] == folder for i in range(len(low)))
            ):
                return True
        return False

    def gated(self, path: str, cwd: str | None = None, folder: bool = False) -> bool:
        """True when a write to this path (absolute, or relative to `cwd`) needs the gate. `folder`: the path is the
        folder part of a wildcard, so a file inside it is written (the project root itself is then gated)."""
        real = os.path.realpath(os.path.join(cwd or self.cwd, path))
        if real == str(self.root):
            return folder
        if not real.startswith(str(self.root) + os.sep):
            return False  # outside the project (path-guard and the document guard still look at it)
        parts = tuple(p for p in real[len(str(self.root)) :].split(os.sep) if p)
        if self._allowed(parts):
            return False
        return not self.ctx.in_scratch(sp.Word(real))

    def target_gated(self, word: sp.Word, cwd: str) -> bool:
        """For a Bash write target. A variable or substitution cannot be followed: only a literal folder in front of
        it counts (`src/$f` is gated, `$OUT` is not). A wildcard counts by its folder part."""
        text = word.text
        if text.startswith(("$HOME/", "${HOME}/")):
            text = self.home + text[text.index("/") :]
        elif text == "~" or text.startswith("~/"):
            text = self.home + text[1:]
        if not text or text.startswith(("~", "$", "`")):
            return False
        cut = min((text.index(c) for c in "$`*?[" if c in text), default=-1)
        if cut >= 0:
            text = text[:cut].rpartition("/")[0] or ("/" if text.startswith("/") else ".")
        if text == "/dev" or text.startswith("/dev/"):
            return False
        return self.gated(text, cwd, folder=cut >= 0)


def _cd_target(inv: sp.Invocation, cwd: str | None, paths: Paths) -> str | None:
    """The folder after `cd` or `pushd`: None when it cannot be known."""
    word = next((a for a in inv.args if not a.text.startswith("-")), None)
    if word is None:
        return paths.home if inv.name == "cd" else cwd
    if word.dynamic or "$" in word.text or word.text == "-" or cwd is None:
        return None
    text = paths.home + word.text[1:] if word.text == "~" or word.text.startswith("~/") else word.text
    return os.path.realpath(os.path.join(cwd, text))


def write_targets(inv: sp.Invocation, cwd: str) -> list[tuple[sp.Word, str]]:
    """bash_write_guard's write targets, without the sed script (the first operand when there is no -e or -f)."""
    found = bwg.targets(inv, cwd)
    if inv.name.lower() == "sed":
        values = bwg._opts(inv).values
        if not any(key in values for key in ("-e", "-f", "--expression", "--file")):
            return found[1:]
    return found


def bash_gated(command: str, paths: Paths) -> bool:
    """True when one of the command's explicit write targets needs the gate. The working folder follows `cd` in the
    order of the command (a subshell is not scoped); after a `cd` we cannot follow, only absolute targets count."""
    try:
        invocations = list(sp.walk(command))
    except sp.ParseError:
        return False  # command_guard blocks a command it cannot read
    cwd: str | None = str(paths.cwd)
    for inv in invocations:
        name = inv.name.lower()
        if name in ("cd", "pushd"):
            cwd = _cd_target(inv, cwd, paths)
            continue
        if name == "find" or (name == "git" and sp.git_split(inv.args)[1] in ("checkout", "restore")):
            continue  # not writes by name: `git checkout main`, `find . -exec ...`
        for word, _ in write_targets(inv, cwd or str(paths.cwd)):
            if cwd is None and not word.text.startswith(("/", "~", "$HOME", "${HOME}")):
                continue
            if paths.target_gated(word, cwd or str(paths.cwd)):
                return True
    return False


def gated(payload: dict, root: Path) -> bool:
    tool, tool_input = payload["tool_name"], payload["tool_input"]
    paths = Paths(payload, root)
    if tool == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and bash_gated(command, paths)
    target = tool_input.get(dg.PATH_KEY.get(tool, ""))
    if not isinstance(target, str) or not target.strip():
        return False  # the document guard reports a call with no path
    text = target.strip()
    if text == "~" or text.startswith("~/"):
        text = paths.home + text[1:]
    return paths.gated(text)


# --- the guard and the CLI -----------------------------------------------------------------------------


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    if not gated(payload, root):
        return []
    try:
        outcome = check_gate(root)
        if outcome.verdicts:
            return outcome.verdicts
        return record_reasons(Path(os.path.realpath(root)), outcome, payload)
    except GateError as err:
        return [verdict("GATE_ERROR", f"The write gate cannot run: {err}. Ask the developer to check the setup.")]


def report(verdicts: list[hc.Verdict]) -> str:
    if not verdicts:
        return f"[{CONSOLE}] OK: the plan is approved and unchanged, and the story is ready. Code can be written."
    lines = [f"[{CONSOLE}] Blocked: source changes are not allowed yet:"]
    lines += [f"- {v.code} ({v.guard}): {v.reason}" for v in verdicts]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write gate: is the current plan approved and the story ready?")
    parser.add_argument("--check", action="store_true", required=True, help="print the reasons; exit 1 when blocked")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, else the git top level)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else hc.project_root()
    try:
        verdicts = check_gate(root).verdicts
    except GateError as err:
        verdicts = [verdict("GATE_ERROR", f"The write gate cannot run: {err}. Ask the developer to check the setup.")]
    print(report(verdicts))
    return 1 if verdicts else 0


GUARDS = [hc.Guard(GUARD, TOOLS, check)]

if __name__ == "__main__":
    sys.exit(main())
