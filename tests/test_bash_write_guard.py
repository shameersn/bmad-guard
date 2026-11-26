# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/bash_write_guard.py (Phase 2, T3): Bash writes to the approval
state, the guard records and the guard config. One block and one close-but-safe allow per kind of write.

Fake commands and fake paths only. Nothing is run.
"""

from __future__ import annotations

import importlib
import json
import os
import unittest
from unittest import mock

from tests.guard_harness import GuardTestCase, queue

bwg = importlib.import_module("bash_write_guard")

STATE = "_bmad/custom/approval-state.json"
RECORDS = "_bmad-output/guard"
CONFIG = "_bmad/custom/project/guard"


class WriteTests(GuardTestCase):
    def mine(self, command: str) -> list[str]:
        """Reason codes of this guard only (the command guard has its own rules for `rm -r`)."""
        verdicts = self.call("Bash", {"command": command})[1]
        return [v.code for v in verdicts if v.guard == bwg.GUARD]

    def only(self, command: str, code: str):
        self.assertEqual(self.mine(command), [code], command)

    def allows(self, *commands: str):
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.mine(command), [], command)

    def blocks(self, code: str, *commands: str):
        for command in commands:
            with self.subTest(command=command):
                self.only(command, code)

    def test_redirects_into_the_state_file(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f"echo '{{}}' > {STATE}",
            f"echo '{{}}' >> {STATE}",
            f"echo '{{}}' >| {STATE}",
            f"echo '{{}}' &> {STATE}",
            f"echo '{{}}' 2> {STATE}",
            f"cat new.json > {STATE}",
            f'cat <<EOF > "{STATE}"\n{{}}\nEOF',
            f"echo x >{STATE}",
            f"printf x | tee {STATE}",
            f"printf x | tee -a {STATE}",
            f"printf x | sudo tee {STATE}",
            f"echo x > ./_bmad/../{STATE}",
            f"echo x > {self.project}/{STATE}",
            "echo x > /elsewhere/approval-state.json",
            "echo x > _bmad/custom/Approval-State.json",
        )

    def test_redirects_into_the_guard_folders(self):
        self.blocks(
            "DOC_GUARD_RECORD",
            f"echo '{{}}' > {RECORDS}/reviews/2-3.json",
            f"echo x >> {RECORDS}/builds.json",
            f"printf x | tee {RECORDS}/x",
        )
        self.blocks(
            "DOC_GUARD_CONFIG",
            f"echo '{{}}' > {CONFIG}/tool-rules.json",
            f"echo 'a = 1' >> {CONFIG}/tech-stack.toml",
            f"cat rules.json | tee {CONFIG}/tool-rules.json",
        )

    def test_a_file_descriptor_redirect_is_not_a_file(self):
        self.allows("make 2>&1", "make >&2", "make >/dev/null 2>&1", "echo x >&-")

    def test_commands_that_read_the_protected_files_are_allowed(self):
        self.allows(
            f"cat {STATE}",
            f"cat {STATE} | jq .",
            f"grep approved {STATE}",
            f"head -5 {STATE}",
            f"jq '.prd' {STATE}",
            f"ls {RECORDS}",
            f"ls -la {CONFIG}",
            f"cat {CONFIG}/tool-rules.json",
            f"diff {STATE} other.json",
            f"git diff {STATE}",
            f"git log -p -- {CONFIG}",
            f"git show HEAD:{STATE}",
            f"git status {RECORDS}",
            f"wc -l {STATE}",
            f"cat {STATE} > copy.json",
            f"cp {STATE} /tmp/state-copy.json",
            f"cp {RECORDS}/reviews/2-3.json docs/review.json",
            f"sed -n 1,5p {STATE}",
            f"sed 's/a/b/' {CONFIG}/tool-rules.json > out.json",
            f"find {RECORDS} -name '*.json'",
            f"find {RECORDS} -type f -print",
        )

    def test_copy_move_link_and_install_into_a_protected_place(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f"cp new.json {STATE}",
            f"cp -f new.json {STATE}",
            f"mv new.json {STATE}",
            f"ln -sf new.json {STATE}",
            f"install -m 644 new.json {STATE}",
            f"rsync -a new.json {STATE}",
            f"cp -t _bmad/custom {STATE}",
            f"cp {STATE} _bmad/custom/approval-state.json.bak && cp x.json {STATE}",
            "cp -r backup/approval-state.json _bmad/custom/",
        )
        self.blocks(
            "DOC_GUARD_RECORD",
            f"cp review.json {RECORDS}/reviews/",
            f"mv review.json {RECORDS}/",
            f"cp -t {RECORDS} review.json",
        )
        self.blocks(
            "DOC_GUARD_CONFIG", f"cp rules.json {CONFIG}/tool-rules.json", f"cp -r mine/. {CONFIG}/", f"mv x {CONFIG}"
        )

    def test_copy_and_move_that_only_read_the_protected_place_are_allowed(self):
        self.allows(
            f"cp {STATE} backup/state.json",
            f"cp {RECORDS}/reviews/2-3.json /tmp/",
            f"cp -r {CONFIG} /tmp/guard-copy",
            f"ln -s {STATE} link.json",
            "ln -s docs/prd.md",
            "cp a.md b.md",
            "mv a.md docs/b.md",
        )

    def test_moving_a_protected_file_away_blocks(self):
        self.blocks("DOC_APPROVAL_STATE", f"mv {STATE} /tmp/old.json", f"mv {STATE} {STATE}.old")
        self.blocks("DOC_GUARD_RECORD", f"mv {RECORDS}/reviews/2-3.json docs/")
        self.blocks("DOC_GUARD_CONFIG", f"mv {CONFIG}/tool-rules.json docs/")

    def test_in_place_editors(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f"sed -i 's/false/true/' {STATE}",
            f"sed -i.bak 's/a/b/' {STATE}",
            f"sed -ni p {STATE}",
            f"sed --in-place 's/a/b/' {STATE}",
            f"sed -i -e 's/a/b/' -e 's/c/d/' {STATE}",
            f"sed -E -i '' 's/a/b/' {STATE}",
            f"perl -pi -e 's/a/b/' {STATE}",
            f"perl -i.bak -pe 's/a/b/' {STATE}",
        )
        self.blocks("DOC_GUARD_CONFIG", f"sed -i 's/a/b/' {CONFIG}/tool-rules.json")
        self.blocks("DOC_GUARD_RECORD", f"sed -i 's/a/b/' {RECORDS}/reviews/2-3.json")
        self.allows("sed -i 's/a/b/' docs/prd.md", "perl -pi -e 's/a/b/' src/app.pl", f"perl -ne 'print' {STATE}")

    def test_other_writers(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f"dd if=new.json of={STATE}",
            f"curl -o {STATE} https://example.invalid/x",
            f"curl --output {STATE} https://example.invalid/x",
            f"wget -O {STATE} https://example.invalid/x",
            f"patch {STATE} fix.diff",
            f"patch -o {STATE} old.json fix.diff",
        )
        self.allows(
            f"dd if={STATE} of=/tmp/copy.json",
            f"curl -o out.json https://example.invalid/{STATE}",
            "wget -O out.json https://example.invalid/approval-state.json",
            "patch src/app.py fix.diff",
        )

    def test_removing_a_protected_file_or_a_folder_that_holds_it(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f"rm {STATE}",
            f"rm -f {STATE}",
            f"unlink {STATE}",
            f"truncate -s 0 {STATE}",
            f"shred -u {STATE}",
            f"git rm {STATE}",
            f"git rm --cached {STATE}",
            "rm -r _bmad/custom/approval-state.json",
        )
        self.blocks(
            "DOC_GUARD_RECORD",
            f"rm {RECORDS}/reviews/2-3.json",
            f"rm -r {RECORDS}",
            "rm -rf _bmad-output",
            f"git rm -r {RECORDS}",
        )
        self.blocks(
            "DOC_GUARD_CONFIG", f"rm {CONFIG}/tool-rules.json", f"rm -rf {CONFIG}", "rm -r _bmad/custom/project"
        )
        self.assertEqual(
            sorted(self.mine("rm -rf _bmad")), ["DOC_APPROVAL_STATE", "DOC_GUARD_CONFIG", "DOC_GUARD_SCRIPTS"]
        )
        self.assertEqual(
            sorted(self.mine("rm -rf _bmad/custom")), ["DOC_APPROVAL_STATE", "DOC_GUARD_CONFIG", "DOC_GUARD_SCRIPTS"]
        )
        self.allows(
            "rm docs/old.md",
            "rm -rf tmp/x",
            "rm _bmad-output/planning-artifacts/old.md",
            "rm -r _bmad-output/implementation-artifacts",
            "rm _bmad/custom/project.json",
            "git rm docs/old.md",
        )

    def test_find_delete_and_exec(self):
        self.blocks(
            "DOC_GUARD_RECORD",
            f"find {RECORDS} -delete",
            f"find {RECORDS} -exec rm {{}} \\;",
        )
        self.allows(
            "find . -name '*.pyc' -exec rm {} \\;",
            "find docs -name '*.md' -delete",
            f"find {RECORDS} -name x -print",
            "find _bmad-output/implementation-artifacts -delete",
        )

    def test_git_commands_that_rewrite_the_file(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f"git restore {STATE}",
            f"git checkout -- {STATE}",
            f"git checkout HEAD~1 -- {STATE}",
            f"git restore --source=HEAD~1 {STATE}",
            f"git mv {STATE} old.json",
        )
        self.blocks("DOC_GUARD_CONFIG", f"git checkout -- {CONFIG}/tool-rules.json", f"git restore {CONFIG}")
        self.allows(
            "git checkout main",
            "git checkout -b feature/GUARD-2-x",
            "git switch main",
            "git checkout -- docs/prd.md",
            f"git add {STATE}",
            f"git add -- {RECORDS}",
            f"git commit -m 'Approve prd v2' -- {STATE}",
            f"git diff --stat -- {STATE}",
            "git restore --staged docs/prd.md",
        )

    def test_a_cd_into_a_protected_folder_is_followed(self):
        self.blocks(
            "DOC_GUARD_RECORD",
            f"cd {RECORDS} && echo x > review.json",
            f"cd {RECORDS}; rm review.json",
            f"cd {RECORDS} && cp ../x.json .",
        )
        self.blocks(
            "DOC_GUARD_CONFIG", f"cd {CONFIG} && echo x > tool-rules.json", f"pushd {CONFIG} && tee tool-rules.json"
        )
        self.blocks("DOC_APPROVAL_STATE", "cd _bmad/custom && echo x > approval-state.json")
        self.allows(
            "cd docs && echo x > a.md", "cd _bmad/custom && echo x > project.json", f"cd {RECORDS} && cat review.json"
        )

    def test_wrappers_and_nesting_do_not_hide_a_write(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            f'sh -c "echo x > {STATE}"',
            f"bash -lc 'echo x > {STATE}'",
            f"sudo -u root cp new.json {STATE}",
            f"env A=1 nice rm {STATE}",
            f"echo $(echo x > {STATE})",
            f"(echo x > {STATE})",
            f"{{ echo x > {STATE}; }}",
            f"true && echo x > '{STATE}'",
            f'echo x > "{STATE}"',
            "echo x > _bmad/custom/'approval-state.json'",
            f"timeout 5 tee {STATE}",
        )

    def test_a_variable_in_the_target_is_blocked_only_when_it_spells_a_protected_name(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            'echo x > "$DIR/approval-state.json"',
            "echo x > $(pwd)/_bmad/custom/approval-state.json",
        )
        self.blocks("DOC_GUARD_RECORD", 'echo x > "$ROOT/_bmad-output/guard/x.json"')
        self.blocks("DOC_GUARD_CONFIG", "cp x $ROOT/_bmad/custom/project/guard/tool-rules.json")
        self.allows("echo x > $FILE", 'echo x > "$TMPDIR/out.json"', "cp x $DEST/", "tee $OUT")

    def test_globs(self):
        self.blocks(
            "DOC_APPROVAL_STATE",
            "rm _bmad/custom/*.json",
            "rm _bmad/custom/approval-*.json",
            "rm _bmad/custom/approval-state.js?n",
        )
        self.blocks("DOC_GUARD_RECORD", f"rm {RECORDS}/*", f"cp x {RECORDS}/reviews/*.json")
        self.blocks("DOC_GUARD_CONFIG", f"rm {CONFIG}/tool-rules.*")
        self.allows(
            "rm docs/*.json",
            "rm *",
            "rm *.tmp",
            "rm src/*/approval-state.json.md",
            "cp x _bmad-output/planning-artifacts/*",
        )

    def warden_calls(self) -> list[str]:
        """The commands Warden and Sentry tell the agent to run (references/*.md), with fake documents."""
        skills = ".claude/skills"
        grant = f"python3 {skills}/agent-approval-grant/scripts/grant_approval.py"
        state = f'--state-file "{self.project / STATE}"'
        plan = "_bmad-output/implementation-artifacts/spec-2-3.md"
        return [
            # Grant, as references/grant-approval.md shows it
            f'{grant} --doc-path "docs/prd.md" --doc-type "prd" {state} --status-field "approval_status" '
            '--approved-value "approved" --approved-by "Priya BA"',
            # Grant in bmad-build mode, with the project path in the script path and a relative state file
            f'python3 "{self.project}/{skills}/agent-approval-grant/scripts/grant_approval.py" '
            f'--doc-path "{plan}" --doc-type "build:{plan}" --state-file "{STATE}" --status-field approval_status '
            '--create-status-field --hash-mode build --no-commit --approved-by "Priya BA" '
            "--requested-at 2026-11-25T09:00:00Z",
            # Reopen
            f'python3 {skills}/agent-approval-grant/scripts/reopen_document.py --doc-path "docs/prd.md" '
            f'--doc-type "prd" {state} --status-field "approval_status" --reopen-value "draft" '
            '--reopened-by "Priya BA"',
            # Sentry
            f'python3 {skills}/agent-approval-gate-check/scripts/check_gate.py --doc-path "docs/prd.md" '
            f'--doc-type "prd" {state} --status-field approval_status --draft-value pending-approval',
            # The pre-flight commit Warden runs before it asks
            "git status --porcelain -- docs/prd.md && git add -- docs/prd.md "
            '&& git commit -m "Update prd (draft)" -- docs/prd.md',
            # From inside the skill folder, and through uv
            f"cd {skills}/agent-approval-grant && python3 scripts/grant_approval.py --help",
            f"uv run {skills}/agent-approval-grant/scripts/grant_approval.py --doc-path docs/prd.md "
            f'--doc-type prd --state-file "{STATE}"',
        ]

    def test_the_warden_and_sentry_script_calls_are_allowed(self):
        self.allows(*self.warden_calls())

    def test_the_warden_calls_pass_every_registered_guard(self):
        # a full dispatcher run: the command guard, the path guard and the document guards see the same call
        for command in self.warden_calls():
            with self.subTest(command=command[:60]):
                self.assertEqual(self.call("Bash", {"command": command})[1], [])


class FailClosedTests(GuardTestCase):
    def test_a_command_that_cannot_be_read_is_reported_by_the_command_guard_only(self):
        self.assertEqual(self.bash(f'echo "unclosed > {STATE}'), ["CMD_UNPARSABLE"])

    def test_a_call_without_command_text_is_reported_by_the_command_guard_only(self):
        self.assertEqual(self.codes("Bash", {}), ["CMD_UNPARSABLE"])
        self.assertEqual(self.codes("Bash", {"command": 5}), ["CMD_UNPARSABLE"])

    def test_a_guard_that_crashes_blocks(self):
        with mock.patch.object(bwg, "targets", side_effect=RuntimeError("boom")):
            self.assertEqual(self.bash("echo hi"), ["GUARD_ERROR"])
            code, out, err = self.entry(json.dumps(self.payload("Bash", {"command": "echo hi"})))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("GUARD_ERROR (bash-write-guard)", err)
        self.assertNotIn("boom", err)

    def test_only_bash_is_routed_here(self):
        self.assertEqual([g.tools for g in bwg.GUARDS], [("Bash",)])
        self.assertEqual(self.codes("Read", {"file_path": str(self.project / STATE)}), [])

    def test_the_working_directory_decides_what_a_relative_target_means(self):
        sub = self.project / "_bmad/custom"
        sub.mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.bash("echo x > approval-state.json", cwd=str(sub)), ["DOC_APPROVAL_STATE"])
        self.assertEqual(self.bash("echo x > project/guard/tool-rules.json", cwd=str(sub)), ["DOC_GUARD_CONFIG"])
        self.assertEqual(self.bash("echo x > project.json", cwd=str(sub)), [])

    def test_a_link_to_a_protected_folder_is_followed(self):
        (self.project / "_bmad-output/guard").mkdir(parents=True)
        os.symlink(self.project / "_bmad-output/guard", self.project / "notes")
        self.assertEqual(self.bash("echo x > notes/review.json"), ["DOC_GUARD_RECORD"])


class MessageAndEventTests(GuardTestCase):
    def test_events_and_messages_never_carry_the_command_text_or_the_path(self):
        marker = "SECRET-COMMAND-MARKER-777"
        command = f"echo {marker} > {self.project}/{STATE}; echo {marker} > {RECORDS}/{marker}.json"
        code, out, err = self.entry(json.dumps(self.payload("Bash", {"command": command})))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("DOC_APPROVAL_STATE (bash-write-guard)", err)
        self.assertIn("DOC_GUARD_RECORD (bash-write-guard)", err)
        self.assertIn("Warden", err)
        events = queue(self.project)
        self.assertEqual(sorted(e["reason_code"] for e in events), ["DOC_APPROVAL_STATE", "DOC_GUARD_RECORD"])
        for event in events:
            self.assertEqual(
                (event["guard"], event["verdict"], event["tool"], event["rule"], event["session_id"]),
                ("tool-guard", "block", "Bash", "bash-write-guard", "s-test"),
            )
            self.assertEqual(event.get("path", ""), "")
        for text in (err, json.dumps(events)):
            self.assertNotIn(marker, text)
            self.assertNotIn("echo", text)
            self.assertNotIn(str(self.project), text)

    def test_one_reason_per_rule_however_many_targets_match(self):
        self.assertEqual(self.bash(f"echo x > {STATE}; cp a {STATE}; rm {STATE}"), ["DOC_APPROVAL_STATE"])

    def test_an_allowed_command_records_nothing(self):
        self.assertEqual(self.entry(json.dumps(self.payload("Bash", {"command": f"cat {STATE}"}))), (0, "", ""))
        self.assertEqual(queue(self.project), [])


class GuardScriptsTests(GuardTestCase):
    mine = WriteTests.mine
    allows = WriteTests.allows

    def test_shell_writes_to_the_guard_files_are_blocked(self):
        for command in (
            "echo x > .claude/settings.json",
            "echo '{}' >> .claude/settings.local.json",
            "sed -i 's/a/b/' .claude/skills/tool-guard/scripts/pre_tool_use.py",
            "cp /dev/null .claude/skills/prompt-guard/scripts/prompt_guard_hook.py",
            "rm _bmad/custom/guard/hooks/pre-commit",
            "rm -rf .claude/skills/tool-guard",
            "tee .agents/skills/tool-guard/customize.toml < x",
            "mv .claude/settings.json /tmp/x",
        ):
            with self.subTest(command=command):
                self.assertIn("DOC_GUARD_SCRIPTS", self.mine(command), command)

    def test_shell_writes_to_guard_overrides_are_blocked(self):
        self.assertIn("DOC_GUARD_SCRIPTS", self.mine("echo 'phi = \"warn\"' > _bmad/custom/prompt-guard.toml"))
        self.assertIn("DOC_GUARD_SCRIPTS", self.mine("sed -i 's/block/warn/' _bmad/custom/prompt-guard.toml"))
        self.allows("cat _bmad/custom/prompt-guard.toml", "echo x > _bmad/custom/bmad-prd.toml")

    def test_reading_and_running_the_guard_files_is_allowed(self):
        self.allows(
            "cat .claude/settings.json",
            "python3 .claude/skills/tool-guard/scripts/session_start.py < /dev/null",
            "git diff .claude/settings.json",
            "cp .claude/settings.json /tmp/settings-copy.json",
            "echo x > .claude/skills/my-skill/notes.md",
        )


if __name__ == "__main__":
    unittest.main()
