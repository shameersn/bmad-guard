# /// script
# requires-python = ">=3.10"
# ///
"""Install the bmad-guard hook scripts once per machine (ADR 0008). Run by IT or MDM as root, not by a developer.

The managed settings file (ADR 0002) locks the hook entries. This folder holds the scripts those entries run, so a
developer, or the agent that runs as the developer, cannot edit them per project and a project without the module does
not block every tool call. Default folder (a sibling of the managed settings file):

    macOS            /Library/Application Support/ClaudeCode/bmad-guard/
    Linux and WSL    /etc/claude-code/bmad-guard/

What goes in (the same `skills/<skill>/scripts` layout as a project, because the hooks load sibling skills by a path
relative to their own file):

    VERSION, manifest.json                     the module version and the SHA-256 of every file (for --check)
    otel_headers.py, guard_events.py           the telemetry helper (`otelHeadersHelper`) and the sender it imports
    skills/tool-guard, prompt-guard, ready-for-dev-gate, agent-project-logger, agent-approval-gate-check
                                               scripts/ and customize.toml (the built-in rules)

The files are copied to `<folder>.new`, checked, and then renamed into place, so a half-copied folder is never in use.
An existing folder is replaced only when it has a `manifest.json` (it is ours). Files are mode 644, folders 755, and
owned by root when this runs as root. The script never calls sudo and never writes into a project.

Order matters (ADR 0008, "Rollback"): put the folder in place BEFORE the managed file, and remove the managed file
BEFORE `--uninstall`. A managed file that points at a missing folder blocks every tool call in every project.
`--uninstall` refuses while the managed file still points here (`--force` overrides).

Usage:
    python3 scripts/install_machine.py [--target DIR] [--version V] [--dry-run]       install or update
    python3 scripts/install_machine.py [--target DIR] --check                         is the folder right and safe?
    python3 scripts/install_machine.py [--target DIR] [--dry-run] --uninstall [--force]
    python3 scripts/install_machine.py [--target DIR] --print-template [macos|linux]   the managed settings file

`--target` is for tests (a temp folder: no root needed; the folder is then yours, which --check reports as unsafe unless
`--expect-owner` says so) and for a site that keeps the folder elsewhere (then also `--managed-dir`). The version is
`--version`, else a `VERSION` file in the module checkout, else `git describe` there.

`--check` exits 1 unless: every file matches the manifest and nothing else is in the folder; the folder and its files
are owned by root (or `--expect-owner`) and not writable by anyone else; the folder matches this module; `python3` is
3.10 or later; and, when a managed file is found, its hook commands (and `otelHeadersHelper`) point into this folder.
Exit codes: 0 fine, 1 a problem, 2 the script could not run (bad arguments, no rights, a folder that is not ours).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import setup_guard as sg  # noqa: E402 - sibling module, found through the line above

SKILLS = ("tool-guard", "prompt-guard", "ready-for-dev-gate", "agent-project-logger", "agent-approval-gate-check")
# Next to the folder's top: the telemetry helper and the sender it imports (the template's `otelHeadersHelper`).
TOP_FILES = ("otel_headers.py", "guard_events.py")
TOP_FILES_SKILL = "agent-project-logger"
VERSION_NAME, MANIFEST_NAME = "VERSION", "manifest.json"
PLATFORM_NAMES = {"macos": "darwin", "linux": "linux"}
FILE_MODE, DIR_MODE = 0o644, 0o755
MIN_PYTHON = (3, 10)
SHOW = 6  # problems of one kind listed before "(+N more)"


class InstallError(Exception):
    """The script cannot go on (bad arguments, no rights, a folder that is not ours). Exit 2."""


@dataclass
class Plan:
    version: str
    sources: dict[str, Path]  # folder-relative path -> module source file
    hashes: dict[str, str] = field(default_factory=dict)  # folder-relative path -> SHA-256 (VERSION included)

    def version_bytes(self) -> bytes:
        return f"{self.version}\n".encode()


# ---------------------------------------------------------------- what goes in


def load_hook_wiring(module_dir: Path):
    wiring = sg.load_wiring(module_dir)
    if wiring is None:
        raise InstallError(f"not a bmad-guard checkout: {module_dir} (no {sg.WIRING_REL})")
    return wiring


def module_files(module_dir: Path) -> dict[str, Path]:
    """Folder-relative destination -> module source, for the five skills and the two top files."""
    skills = module_dir / "module" / "skills"
    files: dict[str, Path] = {}
    for skill in SKILLS:
        scripts = skills / skill / "scripts"
        if not scripts.is_dir():
            raise InstallError(f"not a bmad-guard checkout: {module_dir} (no module/skills/{skill}/scripts)")
        for src, rel in sg._walk(scripts):
            files[f"skills/{skill}/scripts/{rel.as_posix()}"] = src
        config = skills / skill / "customize.toml"
        if config.is_file():
            files[f"skills/{skill}/customize.toml"] = config
    for name in TOP_FILES:
        src = skills / TOP_FILES_SKILL / "scripts" / name
        if not src.is_file():
            raise InstallError(
                f"not a bmad-guard checkout: {module_dir} (no module/skills/{TOP_FILES_SKILL}/scripts/{name})"
            )
        files[name] = src
    return files


def module_version(module_dir: Path, given: str | None) -> str:
    """`--version`, else a VERSION file in the checkout, else `git describe` there. Never "unknown": the folder's
    version is what --check and the SessionStart skew event compare."""
    if given is not None:
        version = given.strip()
    elif (module_dir / VERSION_NAME).is_file():
        version = (module_dir / VERSION_NAME).read_text(encoding="utf-8").strip()
    else:
        version = sg.module_version(module_dir)
    if not version or version == "unknown" or any(ord(c) < 32 for c in version):
        raise InstallError(
            "cannot tell the module version (git may refuse a checkout owned by another user). Pass --version <v>."
        )
    return version


def make_plan(module_dir: Path, version: str | None) -> Plan:
    plan = Plan(module_version(module_dir, version), module_files(module_dir))
    plan.hashes = {rel: sg.sha256(src) for rel, src in plan.sources.items()}
    plan.hashes[VERSION_NAME] = hashlib.sha256(plan.version_bytes()).hexdigest()
    return plan


def manifest_text(plan: Plan) -> str:
    return json.dumps({"version": plan.version, "files": dict(sorted(plan.hashes.items()))}, indent=2) + "\n"


# ---------------------------------------------------------------- where


def default_target(wiring, platform: str | None = None) -> Path:
    base = wiring.machine_base(platform or sys.platform)
    if base is None:
        raise InstallError(f"no machine folder is defined for {platform or sys.platform} yet: pass --target")
    return Path(base)


def resolve_places(args: argparse.Namespace, wiring) -> tuple[Path, Path | None]:
    """(target folder, managed settings folder or None). With --target and no --managed-dir, the managed file is not
    looked at: a test folder has no managed file next to it."""
    if args.target is not None:
        target = Path(os.path.abspath(args.target))
        managed = Path(os.path.abspath(args.managed_dir)) if args.managed_dir is not None else None
    else:
        target = default_target(wiring)
        managed = Path(os.path.abspath(args.managed_dir)) if args.managed_dir is not None else target.parent
    return target, managed


def refuse_project_places(target: Path, module_dir: Path) -> None:
    """The installer never writes into a project or into the module checkout."""
    real = Path(os.path.realpath(target))
    if real == module_dir or module_dir in real.parents:
        raise InstallError(f"{target} is inside the module checkout; pick a machine folder")
    for folder in (real, *real.parents):
        if (folder / "_bmad").exists():
            raise InstallError(f"{target} is inside a BMAD project ({folder}); this script never writes into a project")


def nearest_existing(path: Path) -> Path:
    for folder in (path, *path.parents):
        if folder.exists():
            return folder
    return Path("/")


def need_rights(target: Path) -> InstallError | None:
    folder = nearest_existing(target.parent)
    if os.access(folder, os.W_OK | os.X_OK):
        return None
    return InstallError(
        f"no rights to write under {folder}. Run this as root (for example from your MDM tool, or `sudo python3 "
        "scripts/install_machine.py`). The script never calls sudo itself."
    )


# ---------------------------------------------------------------- reading a folder


def load_manifest(target: Path) -> tuple[dict | None, str]:
    """(manifest, "") or (None, why). The manifest is {"version": str, "files": {rel: sha256}}."""
    path = target / MANIFEST_NAME
    if not path.is_file() or path.is_symlink():
        return None, f"no {MANIFEST_NAME} in {target} (not a bmad-guard machine folder, or damaged)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"{MANIFEST_NAME} is not valid JSON"
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return None, f"{MANIFEST_NAME} has no usable file list"
    if not all(_safe_rel(rel) for rel in files):
        return None, f"{MANIFEST_NAME} lists a path outside the folder"
    return {"version": data.get("version"), "files": files}, ""


def _safe_rel(rel: str) -> bool:
    path = Path(rel)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def folder_files(target: Path) -> list[str]:
    """Every file and link under `target` as a folder-relative path (the manifest excluded), without following links."""
    found = []
    for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
        base = Path(dirpath)
        found += [(base / n).relative_to(target).as_posix() for n in filenames]
        found += [(base / n).relative_to(target).as_posix() for n in dirnames if (base / n).is_symlink()]
    return sorted(f for f in found if f != MANIFEST_NAME)


def _hash_of(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _more(items: list[str]) -> str:
    shown = ", ".join(items[:SHOW])
    return shown + (f" (+{len(items) - SHOW} more)" if len(items) > SHOW else "")


def content_problems(target: Path, manifest: dict) -> list[str]:
    """Files that are missing, changed since the install, or not in the manifest."""
    files: dict[str, str] = manifest["files"]
    missing = [rel for rel in files if not (target / rel).is_file() or (target / rel).is_symlink()]
    changed = [rel for rel, digest in files.items() if rel not in missing and _hash_of(target / rel) != digest]
    extra = [rel for rel in folder_files(target) if rel not in files]
    problems = []
    if missing:
        problems.append(f"missing file(s): {_more(missing)}")
    if changed:
        problems.append(f"changed since the install: {_more(changed)}")
    if extra:
        problems.append(f"file(s) not in the manifest: {_more(extra)}")
    return problems


def safety_problems(target: Path, expect_uid: int) -> list[str]:
    """Owner and modes. The folder is safe only when `expect_uid` (root) owns every entry and nobody else can write."""
    wrong_owner, open_mode, links = [], [], []
    entries = [target]
    for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
        entries += [Path(dirpath) / n for n in dirnames + filenames]
    for path in entries:
        try:
            info = path.lstat()
        except OSError:
            continue
        rel = path.relative_to(target).as_posix() if path != target else "."
        if stat.S_ISLNK(info.st_mode):
            links.append(rel)
        if info.st_uid != expect_uid:
            wrong_owner.append(rel)
        if info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode):
            open_mode.append(rel)
    problems = []
    if links:
        problems.append(f"link(s) in the folder: {_more(links)}")
    if wrong_owner:
        problems.append(f"not owned by uid {expect_uid}: {_more(wrong_owner)}")
    if open_mode:
        problems.append(f"writable by group or other: {_more(open_mode)}")
    if os.geteuid() != expect_uid and os.access(target, os.W_OK):
        problems.append(f"{target} is writable by the current user (uid {os.geteuid()})")
    return problems


def python_problem() -> str | None:
    """`python3` as the user who runs the check sees it: the hooks start `python3`. Under 3.10 the fail-closed entries
    would block every project."""
    try:
        out = subprocess.run(
            ["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        major, minor = (int(part) for part in out.stdout.strip().split("."))
    except (OSError, ValueError, subprocess.SubprocessError):
        return "`python3` was not found on PATH, so the hooks would fail and block every tool call"
    if (major, minor) < MIN_PYTHON:
        return f"`python3` is {major}.{minor}; the hooks need 3.10 or later (they would block every tool call)"
    return None


# ---------------------------------------------------------------- the managed file


def managed_commands(managed: Path, wiring) -> tuple[list[str], list[str], bool]:
    """(hook commands, helper commands, found) from the managed file and its drop-ins. ValueError on a bad file."""
    commands: list[str] = []
    helpers: list[str] = []
    found = False
    for path in [managed / "managed-settings.json", *sorted((managed / "managed-settings.d").glob("*.json"))]:
        data = wiring.load_settings(path)
        if data is None:
            continue
        found = True
        hooks = data.get("hooks")
        for groups in hooks.values() if isinstance(hooks, dict) else ():
            commands += [cmd for _g, _h, cmd in wiring._commands(groups)] if isinstance(groups, list) else []
        if isinstance(data.get("otelHeadersHelper"), str):
            helpers.append(data["otelHeadersHelper"])
    return commands, helpers, found


def managed_problems(managed: Path | None, target: Path, wiring) -> tuple[list[str], bool]:
    """(problems, points_here). Problems when a managed file exists but its hooks do not point into `target`.
    `points_here` is True when any command in it runs a script under `target` (the uninstall order check)."""
    if managed is None:
        return [], False
    try:
        commands, helpers, found = managed_commands(managed, wiring)
    except ValueError as err:
        return [f"managed settings in {managed}: {err}"], False
    if not found:
        return [], False
    base = str(target)
    ours = [c for c in commands if any(m in c for m in wiring.MARKERS)]
    wanted = {wiring.command(skill, script, base) for _e, _m, skill, script, _x in wiring.HOOKS}
    points_here = any(c in wanted for c in ours) or any(f'"{base}/' in c for c in [*ours, *helpers])
    problems = []
    if not target.is_dir() and points_here:
        problems.append(f"the managed file points to {target}, which does not exist: every tool call would be blocked")
    elif not set(ours) >= wanted:
        elsewhere = [c for c in ours if c not in wanted]
        why = f"runs {elsewhere[0]!r}" if elsewhere else "has no bmad-guard hooks"
        problems.append(f"the managed file does not run all three hooks from {target}: it {why}")
    expected_helper = f'python3 "{base}/otel_headers.py"'
    if helpers and any(h != expected_helper for h in helpers):
        problems.append(f"otelHeadersHelper in the managed file is not {expected_helper}")
    return problems, points_here


# ---------------------------------------------------------------- doing it


def _chown_root(path: Path) -> None:
    if os.geteuid() == 0:
        os.lchown(path, 0, 0)


def stage_folder(plan: Plan, staging: Path) -> None:
    """Write the whole folder into `staging` (new, empty) with its modes and owner."""
    staging.mkdir(mode=DIR_MODE)
    for rel, src in plan.sources.items():
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (staging / VERSION_NAME).write_bytes(plan.version_bytes())
    (staging / MANIFEST_NAME).write_text(manifest_text(plan), encoding="utf-8")
    for dirpath, dirnames, filenames in os.walk(staging):
        for name in dirnames:
            (Path(dirpath) / name).chmod(DIR_MODE)
            _chown_root(Path(dirpath) / name)
        for name in filenames:
            (Path(dirpath) / name).chmod(FILE_MODE)
            _chown_root(Path(dirpath) / name)
    staging.chmod(DIR_MODE)
    _chown_root(staging)
    bad = [rel for rel, digest in plan.hashes.items() if _hash_of(staging / rel) != digest]
    if bad:
        raise InstallError(f"the staged copy does not match the module ({_more(bad)}); nothing was changed")


def remove_ours(folder: Path) -> None:
    """Remove a leftover `<target>.new` or `<target>.old` of ours. Only a folder with our manifest is removed."""
    if not folder.exists() and not folder.is_symlink():
        return
    if folder.is_symlink() or not folder.is_dir() or not (folder / MANIFEST_NAME).is_file():
        raise InstallError(f"{folder} is in the way and is not a bmad-guard machine folder; move it and run again")
    shutil.rmtree(folder)


def current_state(target: Path, plan: Plan) -> str:
    """none, same, differs or foreign, for the folder that is there now. "same" needs the right content and safe modes;
    the owner counts only when this runs as root (a user cannot change it, and a test folder is the user's)."""
    if not target.exists() and not target.is_symlink():
        return "none"
    manifest, _why = load_manifest(target)
    if target.is_symlink() or not target.is_dir() or manifest is None:
        return "foreign"
    same = manifest["version"] == plan.version and manifest["files"] == plan.hashes
    if same and not content_problems(target, manifest) and not safety_problems(target, os.geteuid()):
        return "same"
    return "differs"


def install(plan: Plan, target: Path, dry_run: bool) -> list[str]:
    lines = [f"module version: {plan.version}", f"folder: {target}"]
    state = current_state(target, plan)
    if state == "foreign":
        raise InstallError(
            f"{target} exists and is not a bmad-guard machine folder (no {MANIFEST_NAME}); not touching it"
        )
    if state == "same":
        return [*lines, f"unchanged: {len(plan.hashes)} file(s) already in place", "done"]
    rights = need_rights(target)
    if rights is not None and not dry_run:
        raise rights
    verb = "create" if state == "none" else "replace"
    lines.append(f"{verb} {target} with {len(plan.hashes) + 1} file(s) (copy to {target.name}.new, then rename)")
    if rights is not None:
        lines.append(f"warning: no rights to write under {nearest_existing(target.parent)}; a real run would stop here")
    if os.geteuid() != 0:
        lines.append(
            "warning: not running as root, so the files will be owned by you and you can edit them. Fine for a test "
            "folder; on a real machine run this as root."
        )
    if dry_run:
        return [*lines, "dry run: nothing changed"]
    staging, old = target.with_name(target.name + ".new"), target.with_name(target.name + ".old")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() and old.is_dir() and (old / MANIFEST_NAME).is_file():
        os.rename(old, target)  # an earlier run stopped between its two renames: put the old folder back first
    remove_ours(staging)
    remove_ours(old)
    stage_folder(plan, staging)
    try:
        if target.exists():
            os.rename(target, old)
        os.rename(staging, target)
    except OSError:
        if old.exists() and not target.exists():
            os.rename(old, target)
        raise
    remove_ours(old)
    return [*lines, "done"]


def check(plan: Plan | None, target: Path, managed: Path | None, wiring, expect_uid: int) -> tuple[bool, list[str]]:
    lines: list[str] = []
    problems = 0

    def report(ok: bool, text: str) -> None:
        nonlocal problems
        lines.append(f"{'ok      ' if ok else 'problem '} {text}")
        problems += 0 if ok else 1

    manifest, why = load_manifest(target) if target.is_dir() else (None, f"{target} does not exist")
    if manifest is None:
        report(False, why)
    else:
        report(True, f"{target}: {len(manifest['files'])} file(s), version {manifest['version']}")
        found = content_problems(target, manifest)
        for text in found:
            report(False, text)
        if not found:
            report(True, "every file matches the manifest, nothing else is in the folder")
        unsafe = safety_problems(target, expect_uid)
        for text in unsafe:
            report(False, text)
        if not unsafe:
            report(True, f"owned by uid {expect_uid}, not writable by anyone else")
        if plan is not None:
            if manifest["version"] != plan.version:
                report(
                    False,
                    f"version skew: machine folder {manifest['version']}; this module {plan.version}. They differ "
                    "(update the machine folder first, then the projects)",
                )
            stale = sorted(
                rel for rel in {*plan.hashes, *manifest["files"]} if plan.hashes.get(rel) != manifest["files"].get(rel)
            )
            stale = [rel for rel in stale if rel != VERSION_NAME]
            if stale:
                report(False, f"the folder differs from this module: {_more(stale)}")
            elif manifest["version"] == plan.version:
                report(True, "the folder matches this module")
    py = python_problem()
    report(py is None, py or "python3 is 3.10 or later")
    found_problems, _points = managed_problems(managed, target, wiring)
    for text in found_problems:
        report(False, text)
    if managed is None:
        lines.append("note     no managed settings folder to compare (pass --managed-dir)")
    elif not found_problems:
        try:
            _c, _h, present = managed_commands(managed, wiring)
        except ValueError:
            present = True
        lines.append(
            "ok       the managed file runs the hooks from this folder"
            if present
            else f"note     no managed settings file in {managed}"
        )
    return problems == 0, lines


def uninstall(target: Path, managed: Path | None, wiring, dry_run: bool, force: bool) -> tuple[int, list[str]]:
    """Remove what the manifest lists. Refuses while the managed file points here (ADR 0008, "Rollback")."""
    manifest, why = load_manifest(target) if target.is_dir() else (None, f"{target} does not exist")
    if manifest is None:
        raise InstallError(f"nothing to uninstall: {why}")
    _problems, points_here = managed_problems(managed, target, wiring)
    if points_here and not force:
        raise InstallError(
            f"the managed settings in {managed} still point to this folder. Remove managed-settings.json first, "
            "then run --uninstall again: a managed file that points to a missing folder blocks every tool call in "
            "every project. (--force skips this check.)"
        )
    listed = sorted([*manifest["files"], MANIFEST_NAME])
    lines = [f"{'would remove' if dry_run else 'remove'} {len(listed)} file(s) from {target}"]
    if points_here:
        lines.append("warning: the managed file still points here (--force): tool calls will be blocked")
    if dry_run:
        return 0, [*lines, "dry run: nothing changed"]
    rights = need_rights(target)
    if rights is not None:
        raise rights
    for rel in listed:
        path = target / rel
        if path.is_file() or path.is_symlink():
            path.unlink()
    for dirpath, _dirnames, _files in list(os.walk(target, topdown=False)):  # children first
        with contextlib.suppress(OSError):  # a folder that still holds something is not ours to remove
            Path(dirpath).rmdir()
    left = folder_files(target) if target.exists() else []
    if left:
        return 1, [*lines, f"left in place (not installed by this script): {_more(left)}"]
    return 0, [*lines, "done"]


# ---------------------------------------------------------------- command line


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, help="machine folder (default: the OS path; tests pass a temp folder)")
    parser.add_argument(
        "--module", type=Path, default=sg.DEFAULT_MODULE, help="bmad-guard checkout (default: this repo)"
    )
    parser.add_argument("--version", help="module version to record (default: VERSION file or git describe)")
    parser.add_argument(
        "--managed-dir", type=Path, help="folder of managed-settings.json (default: next to the target)"
    )
    parser.add_argument("--expect-owner", type=int, default=0, help="uid that must own the folder (default 0: root)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    parser.add_argument(
        "--force", action="store_true", help="with --uninstall: go on while the managed file points here"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 unless the folder is right and safe")
    mode.add_argument("--uninstall", action="store_true", help="remove what the manifest lists")
    mode.add_argument(
        "--print-template",
        nargs="?",
        const="current",
        choices=["current", *PLATFORM_NAMES],
        metavar="macos|linux",
        help="print the managed settings file for this folder and exit",
    )
    args = parser.parse_args(argv)
    if args.dry_run and (args.check or args.print_template):
        parser.error("--dry-run goes with install or --uninstall")
    if args.force and not args.uninstall:
        parser.error("--force goes with --uninstall")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    module_dir = args.module.resolve()
    try:
        if sys.platform == "win32" and args.print_template is None:
            raise InstallError("Windows is not supported yet (Phase 3 decision D10)")
        wiring = load_hook_wiring(module_dir)
        if args.print_template is not None:
            platform = sys.platform if args.print_template == "current" else PLATFORM_NAMES[args.print_template]
            base = os.path.abspath(args.target) if args.target is not None else str(default_target(wiring, platform))
            print(wiring.render(wiring.managed_settings(base)), end="")
            return 0
        target, managed = resolve_places(args, wiring)
        refuse_project_places(target, module_dir)
        if args.uninstall:
            code, lines = uninstall(target, managed, wiring, args.dry_run, args.force)
        elif args.check:
            try:
                plan = make_plan(module_dir, args.version)
            except InstallError:
                plan = None  # no way to name this module's version: the folder is still checked on its own
            ok, lines = check(plan, target, managed, wiring, args.expect_owner)
            if plan is None:
                lines.append("note     module version unknown (pass --version to compare it)")
            code = 0 if ok else 1
        else:
            plan = make_plan(module_dir, args.version)
            lines, code = install(plan, target, args.dry_run), 0
    except (InstallError, ValueError) as err:  # ValueError: a folder path the hook commands cannot hold
        print(f"bmad-guard install_machine: {err}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"bmad-guard install_machine: {type(err).__name__}: {err.strerror or err}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
