# /// script
# requires-python = ">=3.10"
# ///
"""Tests for scripts/install_machine.py (ADR 0008) and for the hooks running from a machine folder.

Every test installs into a temp folder (never a system path, never as root). Ownership checks that need root are
simulated with `--expect-owner` (the folder is owned by the test user); the real root-owned check is a manual step in
docs/managed-settings-rollout.md. Fake data only.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fake_home import isolated_env

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("install_machine_under_test", ROOT / "scripts/install_machine.py")
im = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = im
spec.loader.exec_module(im)

wiring = im.load_hook_wiring(ROOT)
UID = getattr(os, "getuid", lambda: 1)()
VERSION = "v9.9.9-test"
FAKE_KEY = "gh" + "p_" + "b7" * 18
NOT_ROOT = unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0, "a root user can write anywhere")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.target = self.base / "Application Support" / "bmad-guard"  # a space, like the macOS path
        self.managed = self.base / "managed"

    def run_main(self, *args: str, module: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = [*args, *(["--module", str(module)] if module else [])]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = im.main(argv)
            except SystemExit as exit_:  # argparse errors
                code = int(exit_.code)
        return code, out.getvalue(), err.getvalue()

    def install(self, *extra: str, module: Path | None = None):
        return self.run_main("--target", str(self.target), "--version", VERSION, *extra, module=module)

    def check(self, *extra: str):
        return self.run_main("--target", str(self.target), "--version", VERSION, "--check", *extra)

    def check_ok(self, *extra: str):
        return self.check("--expect-owner", str(UID), *extra)

    def installed(self) -> Path:
        code, out, err = self.install()
        self.assertEqual(code, 0, out + err)
        return self.target

    def module_copy(self) -> Path:
        """A copy of module/ and nothing else, so a test can change the module."""
        copy = self.base / "module-copy"
        shutil.copytree(ROOT / "module", copy / "module", ignore=shutil.ignore_patterns("__pycache__"))
        return copy


class LayoutTests(Base):
    def test_the_five_skills_and_the_two_top_files(self):
        files = im.module_files(ROOT)
        for skill in im.SKILLS:
            self.assertTrue(any(rel.startswith(f"skills/{skill}/scripts/") for rel in files), skill)
        for rel in (
            "skills/tool-guard/scripts/pre_tool_use.py",
            "skills/tool-guard/scripts/session_start.py",
            "skills/tool-guard/scripts/hook_wiring.py",
            "skills/prompt-guard/scripts/prompt_guard_hook.py",
            "skills/tool-guard/customize.toml",
            "skills/prompt-guard/customize.toml",
            "skills/ready-for-dev-gate/customize.toml",
            "skills/agent-project-logger/scripts/guard_events.py",
            "skills/agent-approval-gate-check/scripts/check_gate.py",
            "otel_headers.py",
            "guard_events.py",
        ):
            self.assertIn(rel, files)
        self.assertEqual(
            files["otel_headers.py"].read_bytes(),
            files["skills/agent-project-logger/scripts/otel_headers.py"].read_bytes(),
        )

    def test_other_skills_docs_and_caches_are_not_copied(self):
        files = im.module_files(ROOT)
        for rel in files:
            self.assertNotIn("SKILL.md", rel)
            self.assertNotIn("references/", rel)
            self.assertNotIn("__pycache__", rel)
        for skill in ("confidence-scorer", "experion-brief-review", "agent-approval-grant"):
            self.assertFalse(any(f"skills/{skill}/" in rel for rel in files), skill)


class InstallTests(Base):
    """M-I1."""

    def test_install_writes_the_folder_version_and_manifest(self):
        target = self.installed()
        self.assertEqual((target / "VERSION").read_text(), VERSION + "\n")
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(set(manifest["files"]), {*im.module_files(ROOT), "VERSION"})
        for rel, digest in manifest["files"].items():
            self.assertEqual(im._hash_of(target / rel), digest, rel)
        self.assertEqual(sorted(p.name for p in self.target.parent.iterdir()), ["bmad-guard"], "no .new or .old left")

    def test_modes_are_644_and_755(self):
        target = self.installed()
        for path in [target, *target.rglob("*")]:
            mode = stat.S_IMODE(path.lstat().st_mode)
            self.assertEqual(mode, 0o755 if path.is_dir() else 0o644, str(path))

    def test_a_second_run_changes_nothing(self):
        target = self.installed()
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in [target, *target.rglob("*")]}
        code, out, _ = self.install()
        self.assertEqual(code, 0)
        self.assertIn("unchanged", out)
        after = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in [target, *target.rglob("*")]}
        self.assertEqual(before, after)

    def test_a_new_version_replaces_the_whole_folder_by_rename(self):
        target = self.installed()
        (target / "skills/tool-guard/scripts/stale_file.py").write_text("x")  # a file the new release does not have
        marker = (target / "VERSION").stat().st_ino
        code, out, _ = self.run_main("--target", str(target), "--version", "v10")
        self.assertEqual(code, 0, out)
        self.assertEqual((target / "VERSION").read_text(), "v10\n")
        self.assertFalse((target / "skills/tool-guard/scripts/stale_file.py").exists())
        self.assertNotEqual((target / "VERSION").stat().st_ino, marker)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["bmad-guard"])

    def test_a_changed_module_file_is_picked_up(self):
        module = self.module_copy()
        self.installed()
        script = module / "module/skills/tool-guard/scripts/command_guard.py"
        script.write_text(script.read_text() + "\n# a change\n")
        code, out, _ = self.install(module=module)
        self.assertEqual(code, 0, out)
        self.assertTrue(
            (self.target / "skills/tool-guard/scripts/command_guard.py").read_text().endswith("# a change\n")
        )

    def test_an_edited_file_in_the_folder_is_repaired_by_the_next_install(self):
        target = self.installed()
        script = target / "skills/tool-guard/scripts/pre_tool_use.py"
        script.write_text("raise SystemExit(0)\n")
        code, out, _ = self.install()
        self.assertEqual(code, 0, out)
        self.assertEqual(script.read_bytes(), (ROOT / "module/skills/tool-guard/scripts/pre_tool_use.py").read_bytes())

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.install("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertFalse(self.target.parent.exists())
        self.installed()
        before = sorted(str(p) for p in self.base.rglob("*"))
        self.assertEqual(self.install("--dry-run")[0], 0)
        self.assertEqual(sorted(str(p) for p in self.base.rglob("*")), before)

    def test_a_folder_that_is_not_ours_is_never_replaced(self):
        self.target.mkdir(parents=True)
        (self.target / "mine.txt").write_text("keep me")
        code, _, err = self.install()
        self.assertEqual(code, 2)
        self.assertIn("not a bmad-guard machine folder", err)
        self.assertEqual([p.name for p in self.target.iterdir()], ["mine.txt"])

    def test_a_file_or_link_at_the_target_is_refused(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("a file")
        self.assertEqual(self.install()[0], 2)
        self.target.unlink()
        real = self.base / "real"
        real.mkdir()
        self.target.symlink_to(real)
        self.assertEqual(self.install()[0], 2)
        self.assertEqual(list(real.iterdir()), [])

    def test_a_leftover_staging_folder_that_is_not_ours_stops_the_run(self):
        self.target.parent.mkdir(parents=True)
        staging = self.target.with_name("bmad-guard.new")
        staging.mkdir()
        (staging / "mine.txt").write_text("keep me")
        code, _, err = self.install()
        self.assertEqual(code, 2)
        self.assertIn("in the way", err)
        self.assertTrue((staging / "mine.txt").exists())
        self.assertFalse(self.target.exists())

    def test_a_leftover_staging_folder_of_ours_is_cleaned_up(self):
        target = self.installed()
        shutil.copytree(target, target.with_name("bmad-guard.new"))
        code, out, _ = self.run_main("--target", str(target), "--version", "v11")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["bmad-guard"])

    def test_a_run_that_stopped_between_the_two_renames_is_recovered(self):
        target = self.installed()
        os.rename(target, target.with_name("bmad-guard.old"))  # what is left when the second rename never happened
        code, out, _ = self.install()
        self.assertEqual(code, 0, out)
        self.assertTrue((target / "manifest.json").is_file())
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["bmad-guard"])

    def test_a_staged_copy_that_does_not_match_stops_before_the_swap(self):
        target = self.installed()
        with mock.patch.object(im, "_hash_of", return_value="0" * 64), self.assertRaises(im.InstallError):
            im.stage_folder(im.make_plan(ROOT, "v1"), self.base / "stage")
        self.assertEqual(self.check_ok()[0], 0)  # the installed folder was not touched
        self.assertTrue(target.exists())

    @NOT_ROOT
    def test_without_rights_it_stops_and_says_so(self):
        parent = self.base / "locked"
        parent.mkdir()
        parent.chmod(0o555)
        self.addCleanup(parent.chmod, 0o755)
        code, _, err = self.run_main("--target", str(parent / "bmad-guard"), "--version", VERSION)
        self.assertEqual(code, 2)
        self.assertIn("Run this as root", err)
        self.assertIn("never calls sudo", err)
        self.assertEqual(list(parent.iterdir()), [])

    def test_a_dry_run_without_rights_warns_instead_of_stopping(self):
        if os.geteuid() == 0:
            self.skipTest("root has the rights")
        parent = self.base / "locked"
        parent.mkdir()
        parent.chmod(0o555)
        self.addCleanup(parent.chmod, 0o755)
        code, out, _ = self.run_main("--target", str(parent / "bmad-guard"), "--version", VERSION, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("no rights", out)

    def test_not_root_gets_a_clear_warning(self):
        if os.geteuid() == 0:
            self.skipTest("this is the root case")
        _, out, _ = self.install("--dry-run")
        self.assertIn("not running as root", out)

    def test_it_never_writes_into_a_project_or_the_module(self):
        project = self.base / "project"
        (project / "_bmad").mkdir(parents=True)
        for target in (project / "bmad-guard", project / "deep/er/bmad-guard", ROOT / "build-x"):
            code, _, err = self.run_main("--target", str(target), "--version", VERSION)
            self.assertEqual(code, 2, str(target))
            self.assertTrue("project" in err or "module checkout" in err, err)
        self.assertEqual(sorted(p.name for p in project.iterdir()), ["_bmad"])
        self.assertFalse((ROOT / "build-x").exists())

    def test_the_version_comes_from_the_flag_a_file_or_git_and_never_unknown(self):
        module = self.module_copy()  # no git repository here
        self.assertEqual(im.module_version(ROOT, " v1 "), "v1")
        (module / "VERSION").write_text("from-file\n")
        self.assertEqual(im.module_version(module, None), "from-file")
        (module / "VERSION").unlink()
        with self.assertRaises(im.InstallError):
            im.module_version(module, None)
        self.assertEqual(self.run_main("--target", str(self.target), module=module)[0], 2)
        self.assertFalse(self.target.exists())

    def test_a_folder_that_is_not_a_checkout_is_refused(self):
        empty = self.base / "empty"
        empty.mkdir()
        code, _, err = self.run_main("--target", str(self.target), "--version", VERSION, module=empty)
        self.assertEqual(code, 2)
        self.assertIn("not a bmad-guard checkout", err)

    def test_an_incomplete_module_is_refused_before_anything_is_written(self):
        module = self.module_copy()
        (module / "module/skills/prompt-guard/scripts/prompt_check.py").unlink()
        shutil.rmtree(module / "module/skills/agent-approval-gate-check")
        code, _, err = self.install(module=module)
        self.assertEqual(code, 2)
        self.assertIn("agent-approval-gate-check", err)
        self.assertFalse(self.target.parent.exists())


class CheckTests(Base):
    """M-I2 (the root part is simulated with --expect-owner and needs a real machine)."""

    def test_a_good_folder_passes(self):
        self.installed()
        code, out, _ = self.check_ok()
        self.assertEqual(code, 0, out)
        self.assertNotIn("problem", out)
        self.assertIn("the folder matches this module", out)

    def test_an_edited_file_fails(self):
        self.installed()
        script = self.target / "skills/tool-guard/scripts/pre_tool_use.py"
        script.write_text(script.read_text() + "\n# edited\n")
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("changed since the install: skills/tool-guard/scripts/pre_tool_use.py", out)

    def test_a_missing_and_an_extra_file_fail(self):
        self.installed()
        (self.target / "skills/tool-guard/scripts/shell_parse.py").unlink()
        (self.target / "skills/tool-guard/scripts/extra.py").write_text("x")
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("missing file(s): skills/tool-guard/scripts/shell_parse.py", out)
        self.assertIn("not in the manifest: skills/tool-guard/scripts/extra.py", out)

    def test_a_python_cache_in_the_folder_is_reported(self):
        """Python writes `__pycache__` only where it can: a folder it could write to is not root-owned."""
        self.installed()
        cache = self.target / "skills/tool-guard/scripts/__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"x")
        self.assertEqual(self.check_ok()[0], 1)

    def test_a_file_or_folder_that_others_can_write_fails(self):
        self.installed()
        script = self.target / "skills/tool-guard/scripts/pre_tool_use.py"
        script.chmod(0o666)
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("writable by group or other: skills/tool-guard/scripts/pre_tool_use.py", out)
        script.chmod(0o644)
        (self.target / "skills").chmod(0o775)
        code, out, _ = self.check_ok()
        self.assertEqual((code, "writable by group or other: skills" in out), (1, True))

    def test_a_link_in_the_folder_fails(self):
        self.installed()
        (self.target / "link.py").symlink_to(self.target / "VERSION")
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("link(s) in the folder", out)

    @NOT_ROOT
    def test_a_folder_owned_by_the_current_user_fails_the_default_root_check(self):
        """The default expectation is root. A test folder is ours, so this is the simulated 'not root-owned' case."""
        self.installed()
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("not owned by uid 0", out)
        self.assertIn("writable by the current user", out)

    def test_a_missing_folder_fails_with_one_clear_line(self):
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)

    def test_a_folder_with_a_broken_manifest_fails(self):
        self.installed()
        (self.target / "manifest.json").write_text("{not json")
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)

    def test_a_manifest_that_points_outside_the_folder_is_not_trusted(self):
        self.installed()
        (self.target / "manifest.json").write_text(json.dumps({"version": "v", "files": {"../outside": "0" * 64}}))
        code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("outside the folder", out)

    def test_a_different_module_version_is_reported_as_skew(self):
        """M-I8 for the installer: the folder and the module differ."""
        self.installed()
        code, out, _ = self.run_main(
            "--target", str(self.target), "--version", "v10", "--check", "--expect-owner", str(UID)
        )
        self.assertEqual(code, 1)
        self.assertIn("version skew: machine folder v9.9.9-test; this module v10", out)

    def test_a_folder_that_differs_from_the_module_fails(self):
        module = self.module_copy()
        self.installed()
        script = module / "module/skills/tool-guard/scripts/command_guard.py"
        script.write_text(script.read_text() + "\n# newer\n")
        args = ["--target", str(self.target), "--version", VERSION, "--check", "--expect-owner", str(UID)]
        code, out, _ = self.run_main(*args, module=module)
        self.assertEqual(code, 1)
        self.assertIn("the folder differs from this module: skills/tool-guard/scripts/command_guard.py", out)

    def test_python_older_than_3_10_fails(self):
        self.installed()
        old = subprocess.CompletedProcess([], 0, stdout="3.9\n", stderr="")
        with mock.patch.object(im.subprocess, "run", return_value=old):
            code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("`python3` is 3.9", out)

    def test_no_python3_fails(self):
        self.installed()
        with mock.patch.object(im.subprocess, "run", side_effect=FileNotFoundError("python3")):
            code, out, _ = self.check_ok()
        self.assertEqual(code, 1)
        self.assertIn("`python3` was not found", out)


class ManagedBase(Base):
    def put_managed(self, settings: dict | str, name: str = "managed-settings.json") -> None:
        path = self.managed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings if isinstance(settings, str) else json.dumps(settings))

    def check_managed(self):
        return self.check_ok("--managed-dir", str(self.managed))


class ManagedFileTests(ManagedBase):
    """`--check` and `--uninstall` look at the managed settings file next to the folder (ADR 0008, "Rollback")."""

    def test_a_managed_file_that_points_here_passes(self):
        self.installed()
        self.put_managed(wiring.managed_settings(str(self.target)))
        code, out, _ = self.check_managed()
        self.assertEqual(code, 0, out)
        self.assertIn("the managed file runs the hooks from this folder", out)

    def test_no_managed_file_is_a_note_not_a_failure(self):
        self.installed()
        code, out, _ = self.check_managed()
        self.assertEqual(code, 0)
        self.assertIn("no managed settings file", out)

    def test_a_managed_file_with_project_paths_fails(self):
        self.installed()
        template = json.loads((ROOT / "module/managed-settings/managed-settings.json").read_text())
        self.put_managed(template)
        code, out, _ = self.check_managed()
        self.assertEqual(code, 1)
        self.assertIn("does not run all three hooks from", out)
        self.assertIn("CLAUDE_PROJECT_DIR", out)

    def test_a_managed_file_that_points_to_another_folder_fails(self):
        self.installed()
        self.put_managed(wiring.managed_settings(str(self.base / "elsewhere")))
        code, out, _ = self.check_managed()
        self.assertEqual(code, 1)
        self.assertIn("does not run all three hooks from", out)
        self.assertIn("otelHeadersHelper", out)

    def test_a_managed_file_that_points_to_a_missing_folder_fails_loudly(self):
        """The dangerous order: the managed file is there, the folder is not."""
        self.put_managed(wiring.managed_settings(str(self.target)))
        code, out, _ = self.check_managed()
        self.assertEqual(code, 1)
        self.assertIn("which does not exist: every tool call would be blocked", out)

    def test_a_broken_managed_file_fails(self):
        self.installed()
        self.put_managed("{broken")
        code, out, _ = self.check_managed()
        self.assertEqual(code, 1)
        self.assertIn("managed settings in", out)

    def test_drop_ins_count(self):
        self.installed()
        full = wiring.managed_settings(str(self.target))
        self.put_managed({"hooks": full["hooks"]}, "managed-settings.d/10-hooks.json")
        code, out, _ = self.check_managed()
        self.assertEqual(code, 0, out)

    def test_the_default_place_is_next_to_the_target_only_for_the_os_folder(self):
        """With --target and no --managed-dir the managed file is not looked at (a test folder has none)."""
        args = im.parse_args(["--target", str(self.target)])
        self.assertIsNone(im.resolve_places(args, wiring)[1])
        args = im.parse_args([])
        target, managed = im.resolve_places(args, wiring)
        self.assertEqual(managed, target.parent)


class UninstallTests(ManagedBase):
    def test_uninstall_removes_what_the_manifest_lists_and_the_folder(self):
        self.installed()
        code, out, _ = self.run_main("--target", str(self.target), "--uninstall")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.target.parent.exists(), "only the folder itself goes")

    def test_a_file_that_is_not_in_the_manifest_stays_and_so_does_its_folder(self):
        self.installed()
        mine = self.target / "skills/tool-guard/mine.txt"
        mine.write_text("keep me")
        code, out, _ = self.run_main("--target", str(self.target), "--uninstall")
        self.assertEqual(code, 1)
        self.assertIn("left in place", out)
        self.assertEqual(mine.read_text(), "keep me")
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["skills"])

    def test_dry_run_lists_and_removes_nothing(self):
        self.installed()
        before = sorted(str(p) for p in self.target.rglob("*"))
        code, out, _ = self.run_main("--target", str(self.target), "--uninstall", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would remove", out)
        self.assertEqual(sorted(str(p) for p in self.target.rglob("*")), before)

    def test_it_refuses_while_the_managed_file_points_here(self):
        self.installed()
        self.put_managed(wiring.managed_settings(str(self.target)))
        code, _, err = self.run_main("--target", str(self.target), "--managed-dir", str(self.managed), "--uninstall")
        self.assertEqual(code, 2)
        self.assertIn("Remove managed-settings.json first", err)
        self.assertTrue((self.target / "manifest.json").exists())

    def test_force_goes_on_with_a_warning(self):
        self.installed()
        self.put_managed(wiring.managed_settings(str(self.target)))
        args = ["--target", str(self.target), "--managed-dir", str(self.managed), "--uninstall", "--force"]
        code, out, _ = self.run_main(*args)
        self.assertEqual(code, 0, out)
        self.assertIn("warning: the managed file still points here", out)
        self.assertFalse(self.target.exists())

    def test_the_right_order_works(self):
        """Delete the managed file, then uninstall (ADR 0008, rollback steps 1 and 2)."""
        self.installed()
        self.put_managed(wiring.managed_settings(str(self.target)))
        (self.managed / "managed-settings.json").unlink()
        args = ["--target", str(self.target), "--managed-dir", str(self.managed), "--uninstall"]
        self.assertEqual(self.run_main(*args)[0], 0)

    def test_nothing_to_uninstall_is_an_error_and_a_foreign_folder_is_not_touched(self):
        self.assertEqual(self.run_main("--target", str(self.target), "--uninstall")[0], 2)
        self.target.mkdir(parents=True)
        (self.target / "mine.txt").write_text("keep me")
        code, _, err = self.run_main("--target", str(self.target), "--uninstall")
        self.assertEqual(code, 2)
        self.assertIn("no manifest.json", err)
        self.assertEqual((self.target / "mine.txt").read_text(), "keep me")

    def test_a_manifest_cannot_make_it_delete_outside_the_folder(self):
        self.installed()
        outside = self.base / "outside.txt"
        outside.write_text("keep me")
        (self.target / "manifest.json").write_text(
            json.dumps({"version": "v", "files": {"../../outside.txt": "0" * 64}})
        )
        self.assertEqual(self.run_main("--target", str(self.target), "--uninstall")[0], 2)
        self.assertEqual(outside.read_text(), "keep me")

    def test_reinstall_after_uninstall_and_rollback_by_installing_the_previous_release(self):
        self.installed()
        self.assertEqual(self.run_main("--target", str(self.target), "--uninstall")[0], 0)
        self.assertEqual(self.install()[0], 0)
        self.assertEqual(self.run_main("--target", str(self.target), "--version", "v-new")[0], 0)
        self.assertEqual(
            self.run_main("--target", str(self.target), "--version", VERSION)[0], 0
        )  # the bad update: go back
        self.assertEqual((self.target / "VERSION").read_text(), VERSION + "\n")


class TemplateOutputTests(Base):
    def test_print_template_follows_target_and_os(self):
        code, out, _ = self.run_main("--print-template", "linux", "--target", "/opt/site/bmad-guard")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data, wiring.managed_settings("/opt/site/bmad-guard"))
        code, out, _ = self.run_main("--print-template", "macos")
        self.assertEqual(json.loads(out), wiring.managed_settings("/Library/Application Support/ClaudeCode/bmad-guard"))

    def test_a_target_that_would_break_the_quoting_is_refused(self):
        code, _, err = self.run_main("--print-template", "linux", "--target", '/opt/a"b')
        self.assertEqual(code, 2)
        self.assertIn("not a usable machine folder path", err)

    def test_options_that_do_not_go_together_are_refused(self):
        for args in (
            ["--check", "--uninstall"],
            ["--dry-run", "--check"],
            ["--force"],
            ["--dry-run", "--print-template"],
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(*args)[0], 2)


class HooksFromTheMachineFolderTests(Base):
    """M-I3 and M-I4: the three hooks, started through the managed commands, from a folder with a space in its path."""

    def setUp(self):
        super().setUp()
        self.installed()
        self.project = self.base / "project"
        (self.project / "_bmad/custom/guard").mkdir(parents=True)
        (self.project / "_bmad/custom/guard/manifest.json").write_text('{"version": "v0", "files": {}}')
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.commands = {
            event: groups[0]["hooks"][0]["command"] for event, groups in wiring.desired_hooks(str(self.target)).items()
        }

    def run_hook(self, event: str, payload: dict, project: Path | None = None) -> subprocess.CompletedProcess:
        project = project or self.project
        env = {**isolated_env(), "CLAUDE_PROJECT_DIR": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(
            ["sh", "-c", self.commands[event]],
            input=json.dumps({"cwd": str(project), "session_id": "s-1", **payload}),
            capture_output=True,
            text=True,
            env=env,
            cwd=project,
        )

    def test_the_commands_have_a_space_and_no_project_dir(self):
        for command in self.commands.values():
            self.assertIn("Application Support", command)
            self.assertNotIn("CLAUDE_PROJECT_DIR", command)

    def test_all_three_hooks_run_with_no_skill_copy_in_the_project(self):
        self.assertFalse((self.project / ".claude").exists())
        push = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push --force"}}
        proc = self.run_hook("PreToolUse", push)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("CMD_GIT_FORCE_PUSH", proc.stderr)
        ok = self.run_hook("PreToolUse", {**push, "tool_input": {"command": "git status"}})
        self.assertEqual((ok.returncode, ok.stdout, ok.stderr), (0, "", ""))
        prompt = {"hook_event_name": "UserPromptSubmit", "prompt": f"token {FAKE_KEY}"}
        self.assertEqual(json.loads(self.run_hook("UserPromptSubmit", prompt).stdout)["decision"], "block")
        start = self.run_hook("SessionStart", {"hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(start.returncode, 0)
        self.assertIn("core.hooksPath", json.loads(start.stdout)["systemMessage"])

    def test_the_built_in_rules_come_from_the_machine_copy(self):
        """MACH-1 in miniature: a project's own customize.toml changes nothing."""
        mine = self.project / ".claude/skills/tool-guard/customize.toml"
        mine.parent.mkdir(parents=True)
        mine.write_text("# a project copy with the destructive-command rules removed\n")
        push = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push --force"}}
        self.assertEqual(self.run_hook("PreToolUse", push).returncode, 2)

    def test_the_telemetry_helper_runs_from_the_machine_folder(self):
        env = {**isolated_env(), "BMAD_GUARD_USER": "fake-dev", "BMAD_GUARD_KEY": "fake-ingest-value-1"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        helper = wiring.managed_settings(str(self.target))["otelHeadersHelper"]
        proc = subprocess.run(["sh", "-c", helper], capture_output=True, text=True, env=env, cwd=self.base)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Authorization", json.loads(proc.stdout))

    def queue(self, project: Path | None = None) -> list[dict]:
        path = (project or self.project) / ".bmad-guard/events/queue.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    def test_session_start_records_version_skew_as_an_info_event_and_shows_no_message(self):
        """ADR 0008: machine VERSION against the version in the project's manifest."""
        start = {"hook_event_name": "SessionStart", "source": "startup"}
        git = ["git", "config", "core.hooksPath", "_bmad/custom/guard/hooks"]
        subprocess.run(git, cwd=self.project, check=True)
        settings = self.project / ".claude/settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(wiring.render(wiring.merge(None).settings))  # a set-up project: nothing else to warn about
        proc = self.run_hook("SessionStart", start)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        (event,) = self.queue()
        self.assertEqual((event["verdict"], event["reason_code"]), ("info", "MACHINE_VERSION_SKEW"))
        self.assertEqual((event["machine_version"], event["project_version"]), (VERSION, "v0"))
        manifest = self.project / "_bmad/custom/guard/manifest.json"
        manifest.write_text(json.dumps({"version": VERSION, "files": {}}))
        self.run_hook("SessionStart", start)
        self.assertEqual(len(self.queue()), 1, "the same version records nothing")

    def test_an_unguarded_project_is_allowed_from_the_machine_folder(self):
        """M-I5 / MACH-C1 through the managed command."""
        plain = self.base / "plain"
        plain.mkdir()
        before = sorted(str(p) for p in plain.rglob("*"))
        push = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push --force"}}
        for event, payload in (
            ("PreToolUse", push),
            ("UserPromptSubmit", {"prompt": f"token {FAKE_KEY}"}),
            ("SessionStart", {"source": "startup"}),
        ):
            proc = self.run_hook(event, payload, plain)
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""), event)
        self.assertEqual(sorted(str(p) for p in plain.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
