# /// script
# requires-python = ">=3.10"
# ///
"""Install the bmad-guard module into a project that already has BMAD.

What it does (ADR 0001, Phase 0 spike T2):
- copies each guard skill into every tool skill folder that exists (.claude/skills, .agents/skills);
- copies overrides into _bmad/custom/;
- copies git hooks into _bmad/custom/guard/hooks/ (every file there is made executable; --check reports one that is not)
  and points core.hooksPath there;
- merges the Claude Code hook entries and sensitive-path deny rules into .claude/settings.json (Phase 2, T1;
  the definition lives in module/skills/tool-guard/scripts/hook_wiring.py). The project's own entries are kept;
  ours are found by their exact command, so a re-run changes nothing. Skipped when the project has no
  .claude/skills folder (the hooks would point at scripts that are not there);
- adds .bmad-guard/ (local event queue) to the project's .gitignore;
- records what it installed in _bmad/custom/guard/manifest.json (file hashes + module version).

Safety rules:
- A file the module did not install, or that changed since it was installed, is never overwritten
  or removed without --force. It is reported as a conflict instead.
- An existing core.hooksPath that points elsewhere is not replaced without --force.
- A hook entry that looks like ours but is not the expected command, or "disableAllHooks": true, is a
  conflict in .claude/settings.json; it is left as it is without --force. Other settings are never touched.
- --dry-run prints the plan (and the settings.json diff) and changes nothing. --check reports whether the project
  is up to date and exits 1 if not.

Per-user credential file (ADR 0007, optional: without it events only queue):
- `--credentials --endpoint URL --user ID` writes ~/.config/bmad-guard/credentials once (folder 700, file 600,
  created with that mode). The key comes from a hidden prompt, or from BMAD_GUARD_KEY for that run; never from the
  command line. Same values: nothing changes. Different values: a conflict, replaced only with --force. It never
  writes into a project, and needs no --project.
- `--check` also notes a missing file, reports a file that is too open (with the fix), and warns on Windows
  (not supported yet, D10). These lines do not change the exit code.

Uninstall (`--uninstall`, GUARD-3 T5): takes out what setup added and nothing else, using the manifest. Run
`--uninstall --dry-run` first. It removes the files the manifest lists (skill copies, overrides, git hooks) that are
unchanged since install, our entries and deny rules in .claude/settings.json (the file goes when nothing else is in it),
core.hooksPath when it is ours, our .gitignore lines, and _bmad/custom/guard/ when it is empty. It never touches stock
BMAD files, the project's own files, or paths the setup script does not install. A file changed since install, a link,
or a hook entry that only looks like ours is a conflict: with any conflict nothing is changed (`--force` removes
changed files and lookalike hook entries; a link or a path outside setup's folders is never removed). The local event
queue `.bmad-guard/` is left (setup did not create it). A project with the manifest is guarded by the hooks (ADR 0008),
so uninstall also turns the L1 guards off for this project.

Machine folder (ADR 0008): `--check` also compares the manifest's version with `VERSION` in the machine folder
(`install_machine.py`; the OS default, or `--machine-dir`) and prints a warning line when they differ, or a note when
there is no machine folder. These lines do not change the exit code.

Usage:
    python3 scripts/setup_guard.py --project <dir> [--module <dir>] [--dry-run | --check] [--force]
    python3 scripts/setup_guard.py --project <dir> --uninstall [--dry-run] [--force]
    python3 scripts/setup_guard.py --credentials --endpoint <url> --user <id> [--force]
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import getpass
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

TOOL_SKILL_DIRS = (".claude/skills", ".agents/skills")
GUARD_DIR = "_bmad/custom/guard"
HOOKS_REL = f"{GUARD_DIR}/hooks"
MANIFEST_REL = f"{GUARD_DIR}/manifest.json"
SETTINGS_REL = ".claude/settings.json"
WIRING_REL = "module/skills/tool-guard/scripts/hook_wiring.py"
WIRING_TRIGGER = ".claude/skills/tool-guard/scripts/pre_tool_use.py"  # installed only when .claude/skills exists
EVENTS_REL = "module/skills/agent-project-logger/scripts/guard_events.py"  # Ledger's sender: reads the credential file
CREDENTIALS_REL = ".config/bmad-guard/credentials"  # under HOME; ADR 0007
GITIGNORE_LINE = ".bmad-guard/"
GITIGNORE_COMMENT = "# bmad-guard local event queue"
# Uninstall only removes files under these folders (what setup installs); a manifest cannot point anywhere else.
UNINSTALL_PREFIXES = (*(f"{d}/" for d in TOOL_SKILL_DIRS), "_bmad/custom/")
# Folders that uninstall never removes, even when empty: they belong to BMAD and the tools, not to setup.
KEEP_DIRS = ("", ".claude", ".agents", "_bmad", "_bmad/custom", *TOOL_SKILL_DIRS)
VERSION_NAME = "VERSION"  # in the machine folder (scripts/install_machine.py)
SKIP_NAMES = {"__pycache__", "tests", ".DS_Store"}
DEFAULT_MODULE = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    """The project cannot be set up (missing git or BMAD, bad module)."""


@dataclass
class Action:
    kind: str  # create, update, remove, keep, conflict
    path: str  # relative to the project
    reason: str = ""


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)  # project path -> module source
    hooks_path_now: str | None = None
    hooks_path_ok: bool = True
    hooks_path_conflict: bool = False
    gitignore_ok: bool = True
    version: str = "unknown"
    version_now: str | None = None
    settings_new: str | None = None  # new text for .claude/settings.json, None = nothing to write
    settings_diff: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def changes(self) -> list[Action]:
        return [a for a in self.actions if a.kind in ("create", "update", "remove")]

    def conflicts(self) -> list[Action]:
        return [a for a in self.actions if a.kind == "conflict"]

    def up_to_date(self) -> bool:
        return (
            not self.changes()
            and not self.conflicts()
            and self.hooks_path_ok
            and self.gitignore_ok
            and self.version == self.version_now
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def module_version(module_dir: Path) -> str:
    out = git(["describe", "--tags", "--always", "--dirty"], module_dir)
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"


def _walk(root: Path):
    """Yield files under root, skipping tests, caches and folder READMEs."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part in SKIP_NAMES for part in rel.parts):
            continue
        if rel == Path("README.md"):
            continue
        yield path, rel


def module_files(module_dir: Path, project_dir: Path) -> dict[str, Path]:
    """Map each project-relative destination to its module source file."""
    files: dict[str, Path] = {}
    skills = module_dir / "module" / "skills"
    if skills.is_dir():
        for tool_dir in TOOL_SKILL_DIRS:
            if (project_dir / tool_dir).is_dir():
                for src, rel in _walk(skills):
                    files[f"{tool_dir}/{rel.as_posix()}"] = src
    overrides = module_dir / "module" / "overrides"
    if overrides.is_dir():
        for src, rel in _walk(overrides):
            files[f"_bmad/custom/{rel.as_posix()}"] = src
    hooks = module_dir / "module" / "hooks"
    if hooks.is_dir():
        for src, rel in _walk(hooks):
            files[f"{HOOKS_REL}/{rel.as_posix()}"] = src
    return files


def load_wiring(module_dir: Path):
    """The module's hook_wiring module (loaded by path), or None when the module does not ship it."""
    path = module_dir / WIRING_REL
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("bmad_guard_hook_wiring", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


def plan_settings(plan: Plan, project_dir: Path, wiring, force: bool) -> None:
    """Add the actions that bring .claude/settings.json to the wiring. Reads only."""
    try:
        current = wiring.load_settings(project_dir / SETTINGS_REL)
    except ValueError as err:
        raise SetupError(f"{SETTINGS_REL}: {err}") from err
    result = wiring.merge(current, force)
    kind = "create" if current is None else "update"
    plan.actions += [Action(kind, SETTINGS_REL, change) for change in result.changes]
    plan.actions += [Action("conflict", SETTINGS_REL, why) for why in result.conflicts]
    if result.changes:
        plan.settings_new = wiring.render(result.settings)
        old = wiring.render(current).splitlines() if current is not None else []
        plan.settings_diff = list(
            difflib.unified_diff(
                old, plan.settings_new.splitlines(), f"a/{SETTINGS_REL}", f"b/{SETTINGS_REL}", lineterm=""
            )
        )


def read_manifest(project_dir: Path) -> dict:
    path = project_dir / MANIFEST_REL
    if not path.is_file():
        return {"version": None, "files": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as err:
        raise SetupError(f"{MANIFEST_REL} is not valid JSON; fix or delete it") from err
    return {"version": data.get("version"), "files": dict(data.get("files", {}))}


def check_project(project_dir: Path, module_dir: Path) -> None:
    if not project_dir.is_dir():
        raise SetupError(f"not a folder: {project_dir}")
    if git(["rev-parse", "--show-toplevel"], project_dir).returncode != 0:
        raise SetupError(f"not a git repository: {project_dir}")
    if not (project_dir / "_bmad").is_dir():
        raise SetupError(f"BMAD is not installed in {project_dir} (no _bmad/ folder)")
    if not (module_dir / "module").is_dir():
        raise SetupError(f"not a bmad-guard checkout: {module_dir} (no module/ folder)")


def make_plan(project_dir: Path, module_dir: Path, force: bool = False) -> Plan:
    check_project(project_dir, module_dir)
    plan = Plan(version=module_version(module_dir))
    manifest = read_manifest(project_dir)
    plan.version_now = manifest["version"]
    installed: dict[str, str] = manifest["files"]
    plan.files = module_files(module_dir, project_dir)

    for rel, src in plan.files.items():
        dest = project_dir / rel
        new_hash = sha256(src)
        if not dest.exists():
            plan.actions.append(Action("create", rel))
            continue
        now = sha256(dest)
        if now == new_hash:
            # git skips a hook that is not executable, so a hook file without the bit is not "up to date"
            if rel.startswith(HOOKS_REL + "/") and not os.access(dest, os.X_OK):
                plan.actions.append(Action("update", rel, "not executable"))
            else:
                plan.actions.append(Action("keep", rel))
        elif rel in installed and installed[rel] == now:
            plan.actions.append(Action("update", rel))
        elif force:
            plan.actions.append(Action("update", rel, "forced"))
        else:
            why = "changed since install" if rel in installed else "not installed by bmad-guard"
            plan.actions.append(Action("conflict", rel, why))

    for rel, recorded in installed.items():
        if rel in plan.files:
            continue
        dest = project_dir / rel
        if not dest.exists():
            continue
        if sha256(dest) == recorded or force:
            plan.actions.append(Action("remove", rel, "no longer in the module"))
        else:
            plan.actions.append(Action("conflict", rel, "removed from the module but changed locally"))

    wiring = load_wiring(module_dir) if WIRING_TRIGGER in plan.files else None
    if wiring:
        plan_settings(plan, project_dir, wiring, force)
    else:
        plan.notes.append(
            f"skip     {SETTINGS_REL}: no .claude/skills folder or no tool-guard skill, so no Claude Code hooks"
        )

    hooks_now = git(["config", "--local", "core.hooksPath"], project_dir).stdout.strip() or None
    plan.hooks_path_now = hooks_now
    if hooks_now != HOOKS_REL:
        plan.hooks_path_ok = False
        plan.hooks_path_conflict = hooks_now is not None and not force

    gitignore = project_dir / ".gitignore"
    lines = gitignore.read_text().splitlines() if gitignore.is_file() else []
    plan.gitignore_ok = GITIGNORE_LINE in (line.strip() for line in lines)
    return plan


def apply_plan(plan: Plan, project_dir: Path) -> None:
    manifest = read_manifest(project_dir)["files"]
    for action in plan.actions:
        if action.path == SETTINGS_REL:  # merged below, never copied or recorded in the manifest
            continue
        dest = project_dir / action.path
        if action.kind in ("create", "update"):
            src = plan.files[action.path]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if action.path.startswith(HOOKS_REL + "/"):
                dest.chmod(0o755)
            manifest[action.path] = sha256(dest)
        elif action.kind == "keep":
            manifest[action.path] = sha256(dest)
        elif action.kind == "remove":
            dest.unlink()
            manifest.pop(action.path, None)
            _prune_empty_dirs(dest.parent, project_dir)

    if plan.settings_new is not None:
        settings = project_dir / SETTINGS_REL
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(plan.settings_new, encoding="utf-8")

    if not plan.hooks_path_ok and not plan.hooks_path_conflict:
        (project_dir / HOOKS_REL).mkdir(parents=True, exist_ok=True)
        result = git(["config", "--local", "core.hooksPath", HOOKS_REL], project_dir)
        if result.returncode != 0:
            raise SetupError(f"could not set core.hooksPath: {result.stderr.strip()}")

    if not plan.gitignore_ok:
        gitignore = project_dir / ".gitignore"
        text = gitignore.read_text() if gitignore.is_file() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        gitignore.write_text(f"{text}# bmad-guard local event queue\n{GITIGNORE_LINE}\n")

    manifest_path = project_dir / MANIFEST_REL
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"version": plan.version, "files": dict(sorted(manifest.items()))}, indent=2))


def _prune_empty_dirs(folder: Path, stop: Path) -> None:
    while folder != stop and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def describe(plan: Plan) -> list[str]:
    lines = [f"module version: {plan.version} (installed: {plan.version_now or 'none'})"]
    for action in plan.actions:
        if action.kind == "keep":
            continue
        reason = f" ({action.reason})" if action.reason else ""
        lines.append(f"{action.kind:8} {action.path}{reason}")
    if plan.hooks_path_conflict:
        lines.append(f"conflict core.hooksPath is '{plan.hooks_path_now}'; use --force to point it at {HOOKS_REL}")
    elif not plan.hooks_path_ok:
        lines.append(f"set      core.hooksPath = {HOOKS_REL}")
    if not plan.gitignore_ok:
        lines.append(f"append   .gitignore: {GITIGNORE_LINE}")
    lines += plan.notes
    kept = sum(1 for a in plan.actions if a.kind == "keep")
    conflicts = len(plan.conflicts()) + plan.hooks_path_conflict  # the core.hooksPath line above is a conflict too
    lines.append(f"{len(plan.changes())} change(s), {conflicts} conflict(s), {kept} unchanged")
    return lines


# ---------------------------------------------------------------- uninstall (GUARD-3 T5)


@dataclass
class Removal:
    """What `--uninstall` would do. Every action is a line the developer reads before anything changes."""

    actions: list[Action] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # manifest files to delete (project-relative)
    hard: set[str] = field(default_factory=set)  # conflict paths that --force never overrides
    settings_new: str | None = None  # new text for .claude/settings.json
    settings_delete: bool = False
    unset_hooks_path: bool = False
    gitignore_new: str | None = None
    gitignore_delete: bool = False
    notes: list[str] = field(default_factory=list)

    def conflicts(self) -> list[Action]:
        return [a for a in self.actions if a.kind == "conflict"]

    def changes(self) -> list[Action]:
        return [a for a in self.actions if a.kind != "conflict"]


def _under_setup_folders(rel: str) -> bool:
    """True for a project-relative path inside a folder that setup installs into, with no `..` and not absolute."""
    parts = Path(rel).parts
    return not Path(rel).is_absolute() and ".." not in parts and "\\" not in rel and rel.startswith(UNINSTALL_PREFIXES)


def _inside(path: Path, roots: list[Path]) -> bool:
    """True when `path` (its folder, with links resolved) is inside one of `roots` (resolved)."""
    try:
        folder = path.parent.resolve()
    except (OSError, RuntimeError):
        return False
    return any(folder == root or root in folder.parents for root in roots)


def plan_removal_files(removal: Removal, project_dir: Path, installed: dict[str, str], force: bool) -> None:
    """One action per manifest file. A file that changed since install is a conflict (removed with --force); a path that
    is not under setup's folders, a link and a folder are hard conflicts (never removed)."""
    roots = [project_dir.resolve(), *(project_dir / d for d in TOOL_SKILL_DIRS)]
    roots = [r.resolve() for r in roots]
    for rel, recorded in sorted(installed.items()):
        dest = project_dir / rel
        if not _under_setup_folders(rel) or not _inside(dest, roots):
            removal.actions.append(Action("conflict", rel, "not a path setup installs, never removed"))
            removal.hard.add(rel)
        elif dest.is_symlink() or (dest.exists() and not dest.is_file()):
            removal.actions.append(Action("conflict", rel, "is a link or a folder, never removed"))
            removal.hard.add(rel)
        elif not dest.exists():
            continue  # already gone (an earlier run stopped half way)
        elif sha256(dest) == recorded:
            removal.actions.append(Action("remove", rel))
            removal.files.append(rel)
        elif force:
            removal.actions.append(Action("remove", rel, "changed since install, forced"))
            removal.files.append(rel)
        else:
            removal.actions.append(Action("conflict", rel, "changed since install"))


def plan_removal_settings(removal: Removal, project_dir: Path, wiring, force: bool) -> None:
    if wiring is None:
        removal.notes.append(f"skip     {SETTINGS_REL}: this module does not ship hook_wiring.py")
        return
    try:
        current = wiring.load_settings(project_dir / SETTINGS_REL)
    except ValueError as err:
        raise SetupError(f"{SETTINGS_REL}: {err}") from err
    if current is None:
        return
    result = wiring.unmerge(current, force)
    removal.actions += [Action("conflict", SETTINGS_REL, why) for why in result.conflicts]
    if not result.changes:
        return
    hooks = sum(1 for change in result.changes if change.startswith("remove hook "))
    rules = sum(1 for change in result.changes if change.startswith("remove deny rule "))
    what = f"{hooks} hook entries and {rules} deny rules"
    if result.settings:
        removal.actions.append(Action("update", SETTINGS_REL, f"remove {what}; the rest stays"))
        removal.settings_new = wiring.render(result.settings)
    else:
        removal.actions.append(Action("remove", SETTINGS_REL, f"{what} were all that was in it"))
        removal.settings_delete = True


def plan_removal_gitignore(removal: Removal, project_dir: Path, force: bool) -> None:
    """Remove our line with the comment setup wrote above it. The same line without that comment was the project's own:
    kept, unless --force."""
    path = project_dir / ".gitignore"
    if not path.is_file():
        return
    text = path.read_text()
    lines = text.splitlines()
    drop: set[int] = set()
    for i, line in enumerate(lines):
        if line.strip() != GITIGNORE_LINE:
            continue
        if i > 0 and lines[i - 1].strip() == GITIGNORE_COMMENT:
            drop |= {i - 1, i}
        elif force:
            drop.add(i)
        else:
            removal.notes.append(
                f"keep     .gitignore: {GITIGNORE_LINE} is there without setup's comment line, so it is yours "
                "(--force removes it)"
            )
    if not drop:
        return
    kept = [line for i, line in enumerate(lines) if i not in drop]
    removal.actions.append(Action("update", ".gitignore", f"remove {GITIGNORE_LINE}"))
    if any(line.strip() for line in kept):
        removal.gitignore_new = "\n".join(kept) + "\n"
    else:
        removal.gitignore_delete = True
        removal.actions[-1] = Action("remove", ".gitignore", f"nothing else in it; remove {GITIGNORE_LINE}")


def make_removal(project_dir: Path, module_dir: Path, force: bool = False) -> Removal:
    check_project(project_dir, module_dir)
    path = project_dir / MANIFEST_REL
    if not path.is_file():
        raise SetupError(
            f"no manifest ({MANIFEST_REL}): setup was not run in this project, or the manifest was deleted. Nothing "
            "to uninstall. Remove what you want by hand (docs/guides/install.md, 'Roll back by hand')."
        )
    removal = Removal()
    plan_removal_files(removal, project_dir, read_manifest(project_dir)["files"], force)
    plan_removal_settings(removal, project_dir, load_wiring(module_dir), force)
    hooks_now = git(["config", "--local", "core.hooksPath"], project_dir).stdout.strip() or None
    if hooks_now == HOOKS_REL:
        removal.unset_hooks_path = True
        removal.actions.append(Action("unset", "core.hooksPath", f"it is {HOOKS_REL}, which setup set"))
    elif hooks_now is not None:
        removal.notes.append(f"keep     core.hooksPath is '{hooks_now}': not ours, left as it is")
    plan_removal_gitignore(removal, project_dir, force)
    if (project_dir / ".bmad-guard").exists():
        removal.notes.append(
            "keep     .bmad-guard/ (the local event queue; setup did not create it). Delete it by hand if you want."
        )
    removal.actions.append(Action("remove", MANIFEST_REL, "last, so a stopped run can be run again"))
    return removal


def describe_removal(removal: Removal) -> list[str]:
    lines = []
    for action in removal.actions:
        reason = f" ({action.reason})" if action.reason else ""
        lines.append(f"{action.kind:8} {action.path}{reason}")
    lines += removal.notes
    lines.append(f"{len(removal.changes())} change(s), {len(removal.conflicts())} conflict(s)")
    return lines


def _prune_dirs(folder: Path, project_dir: Path) -> None:
    """Remove `folder` and its parents while they are empty, but never a folder that belongs to BMAD or the tool."""
    keep = {(project_dir / rel).resolve() for rel in KEEP_DIRS}
    while folder.resolve() not in keep and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def apply_removal(removal: Removal, project_dir: Path) -> None:
    for rel in removal.files:
        dest = project_dir / rel
        dest.unlink()
        _prune_dirs(dest.parent, project_dir)
    settings = project_dir / SETTINGS_REL
    if removal.settings_delete:
        settings.unlink()
    elif removal.settings_new is not None:
        settings.write_text(removal.settings_new, encoding="utf-8")
    if removal.unset_hooks_path:
        result = git(["config", "--local", "--unset", "core.hooksPath"], project_dir)
        if result.returncode != 0:
            raise SetupError(f"could not unset core.hooksPath: {result.stderr.strip()}")
    gitignore = project_dir / ".gitignore"
    if removal.gitignore_delete:
        gitignore.unlink()
    elif removal.gitignore_new is not None:
        gitignore.write_text(removal.gitignore_new)
    manifest = project_dir / MANIFEST_REL
    manifest.unlink()
    _prune_dirs(manifest.parent, project_dir)


def uninstall_main(project_dir: Path, module_dir: Path, dry_run: bool, force: bool) -> int:
    try:
        removal = make_removal(project_dir, module_dir, force)
    except SetupError as err:
        print(f"bmad-guard setup: {err}", file=sys.stderr)
        return 2
    for line in describe_removal(removal):
        print(line)
    if dry_run:
        print("dry run: nothing changed")
        return 0
    if removal.conflicts():
        print(
            "uninstall stopped, nothing was changed. Fix the conflicts, or use --force (it removes changed files and "
            "lookalike hook entries; a link or a path setup does not install is never removed)."
        )
        return 1
    apply_removal(removal, project_dir)
    print("done: bmad-guard is removed from this project. The guard hooks no longer run here.")
    return 0


# ---------------------------------------------------------------- machine folder (ADR 0008)


def machine_report(module_dir: Path, version_now: str | None, machine_dir: Path | None) -> list[str]:
    """Lines for `--check`: the machine folder's VERSION against this project's installed version. They are only
    compared for equality (both are `git describe` output). None of these lines changes the exit code: the developer
    cannot fix a machine folder."""
    if machine_dir is None:
        wiring = load_wiring(module_dir)
        base = wiring.machine_base(sys.platform) if wiring is not None else None
        if base is None:
            return []
        machine_dir = Path(base)
    try:
        machine = (machine_dir / VERSION_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        machine = ""
    if not machine:
        return ["note     machine scripts: none (fine unless managed settings point to them)"]
    if machine == version_now:
        return []
    return [
        f"warning  machine scripts: {machine}; this project's module: {version_now or 'none'}. They differ "
        "(update the machine folder first, then the projects)"
    ]


def load_events(module_dir: Path):
    """The module's Ledger sender (guard_events), loaded by path, or None when the module does not ship it."""
    path = module_dir / EVENTS_REL
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("bmad_guard_events_for_setup", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


def credentials_file() -> Path:
    return Path(os.environ.get("HOME") or Path.home()) / CREDENTIALS_REL


def credential_report(module_dir: Path) -> list[str]:
    """Lines for `--check`: the Windows warning (D10), and a note or a problem about the per-user credential file.
    Never reads the key into the output. Sending is optional, so a missing file is only a note."""
    lines = []
    if sys.platform == "win32":
        lines.append("warning  Windows is not supported yet (macOS and Linux only; Phase 3 decision D10)")
    events = load_events(module_dir)
    if events is None:
        return lines
    path = credentials_file()
    _, problem = events.read_credential_file(path)
    if problem:
        lines.append(f"problem  {problem}")
    elif not os.path.lexists(path):
        lines.append(
            f"note     no credential file at ~/{CREDENTIALS_REL}: events are only queued "
            "(sending is optional; see docs/guides/install.md)"
        )
    return lines


def _clean_value(name: str, value: str) -> str:
    """A value for the file: trimmed, not empty, no control characters (a newline would add a line). Never echoes it."""
    value = value.strip()
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SetupError(f"{name} is empty or has control characters")
    return value


def write_credentials(endpoint: str, user: str, key: str, force: bool, module_dir: Path) -> tuple[int, list[str]]:
    """Write ~/.config/bmad-guard/credentials once (ADR 0007). Returns (exit code, output lines)."""
    events = load_events(module_dir)
    if events is None:
        raise SetupError(f"not a bmad-guard checkout: {module_dir} (no {EVENTS_REL})")
    new = {"endpoint": endpoint, "user": user, "key": key}
    path = credentials_file()
    shown = f"~/{CREDENTIALS_REL}"
    kind = "create"
    if os.path.lexists(path):
        old, problem = events.read_credential_file(path)
        if problem and not force:
            return 1, [f"conflict {shown}: {problem}", "use --force to replace it"]
        if not problem and old == new:
            return 0, [f"keep     {shown}", "done"]
        if not problem and not force:
            differs = ", ".join(name for name in new if old.get(name) != new[name])
            return 1, [f"conflict {shown} (differs in: {differs}); use --force to replace it"]
        kind = "update"
    folder = path.parent
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(folder, 0o700)
    except FileExistsError:
        pass
    text = f"[collector]\nendpoint = {endpoint}\nuser = {user}\nkey = {key}\n"
    temp = folder / f".credentials.{os.getpid()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    lines = [f"{kind:8} {shown}"]
    _, problem = events.read_credential_file(path)  # what the sender will see
    if problem:
        return 1, [*lines, f"problem  {problem}"]
    return 0, [*lines, "done"]


def credentials_main(args: argparse.Namespace) -> int:
    if sys.platform == "win32":
        print(
            "bmad-guard setup: Windows is not supported yet (macOS and Linux only; Phase 3 decision D10)",
            file=sys.stderr,
        )
        return 2
    try:
        endpoint = _clean_value("endpoint", args.endpoint)
        user = _clean_value("user", args.user)
        if not endpoint.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise SetupError("endpoint must use https (plain http only for localhost)")
        # The key never comes from the command line (shell history, ps): the prompt, or BMAD_GUARD_KEY for this run.
        key = os.environ.get("BMAD_GUARD_KEY") or getpass.getpass("Ingest key (hidden): ")
        key = _clean_value("key", key)
        code, lines = write_credentials(endpoint, user, key, args.force, args.module.resolve())
    except SetupError as err:
        print(f"bmad-guard setup: {err}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, help="project root (git repo with _bmad/)")
    parser.add_argument("--module", type=Path, default=DEFAULT_MODULE, help="bmad-guard checkout (default: this repo)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    mode.add_argument("--check", action="store_true", help="exit 1 unless the project is up to date")
    parser.add_argument("--force", action="store_true", help="overwrite conflicts and replace core.hooksPath")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="take out what setup added, using the manifest (run with --dry-run first); a conflict stops it "
        "unless --force",
    )
    parser.add_argument("--machine-dir", type=Path, help="with --check: machine folder (default: the OS path)")
    parser.add_argument(
        "--credentials",
        action="store_true",
        help="write the per-user file ~/.config/bmad-guard/credentials (needs --endpoint and --user; "
        "the key comes from a prompt or BMAD_GUARD_KEY); never writes into a project",
    )
    parser.add_argument("--endpoint", help="with --credentials: collector base URL")
    parser.add_argument("--user", help="with --credentials: your developer id")
    args = parser.parse_args(argv)

    if args.credentials:
        if args.project or args.dry_run or args.check:
            parser.error("--credentials cannot be used with --project, --dry-run or --check")
        if not (args.endpoint and args.user):
            parser.error("--credentials needs --endpoint and --user")
        return credentials_main(args)
    if args.project is None:
        parser.error("the following arguments are required: --project")
    if args.endpoint or args.user:
        parser.error("--endpoint and --user need --credentials")
    if args.uninstall and args.check:
        parser.error("--uninstall cannot be used with --check")
    if args.machine_dir and not args.check:
        parser.error("--machine-dir needs --check")

    project_dir, module_dir = args.project.resolve(), args.module.resolve()
    if args.uninstall:
        return uninstall_main(project_dir, module_dir, args.dry_run, args.force)
    try:
        plan = make_plan(project_dir, module_dir, force=args.force)
    except SetupError as err:
        print(f"bmad-guard setup: {err}", file=sys.stderr)
        return 2

    for line in describe(plan):
        print(line)

    if args.check:
        for line in credential_report(module_dir):
            print(line)
        for line in machine_report(module_dir, plan.version_now, args.machine_dir):
            print(line)
        print("up to date" if plan.up_to_date() else "NOT up to date: run setup_guard.py without --check")
        return 0 if plan.up_to_date() else 1
    if args.dry_run:
        for line in plan.settings_diff:
            print(line)
        print("dry run: nothing changed")
        return 0

    apply_plan(plan, project_dir)
    if plan.conflicts() or plan.hooks_path_conflict:
        print("done with conflicts: the files above were left as they are (use --force to overwrite)")
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
