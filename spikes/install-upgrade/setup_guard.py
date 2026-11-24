# /// script
# requires-python = ">=3.10"
# ///
"""T2 spike: install the guard test module into a project without the BMAD installer.

Copies each module skill into every tool skill folder that BMAD uses, copies the
git hooks into `_bmad/custom/guard/hooks/`, and points `core.hooksPath` at them.

Usage:
    python3 setup_guard.py --module <test-module dir> --project <project dir>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIRS = (".claude/skills", ".agents/skills")
HOOKS_REL = "_bmad/custom/guard/hooks"


def install(module_dir: Path, project_dir: Path) -> list[str]:
    """Install the module. Returns the list of paths written, relative to the project."""
    if not (project_dir / ".git").exists():
        raise SystemExit(f"not a git repository: {project_dir}")
    if not (project_dir / "_bmad").is_dir():
        raise SystemExit(f"BMAD is not installed in: {project_dir}")

    written: list[str] = []
    skills_src = module_dir / "skills"
    skills = sorted(p for p in skills_src.iterdir() if (p / "SKILL.md").is_file())
    for rel in SKILL_DIRS:
        target_root = project_dir / rel
        if not target_root.is_dir():
            continue
        for skill in skills:
            dest = target_root / skill.name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(skill, dest, ignore=shutil.ignore_patterns("__pycache__"))
            written.append(f"{rel}/{skill.name}")

    hooks_dest = project_dir / HOOKS_REL
    hooks_dest.mkdir(parents=True, exist_ok=True)
    for hook in sorted((module_dir / "hooks").iterdir()):
        dest = hooks_dest / hook.name
        shutil.copy2(hook, dest)
        dest.chmod(0o755)
        written.append(f"{HOOKS_REL}/{hook.name}")

    subprocess.run(
        ["git", "config", "core.hooksPath", HOOKS_REL],
        cwd=project_dir,
        check=True,
    )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    args = parser.parse_args(argv)
    for path in install(args.module.resolve(), args.project.resolve()):
        print(f"installed {path}")
    print(f"core.hooksPath = {HOOKS_REL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
