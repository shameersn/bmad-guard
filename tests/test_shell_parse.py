# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/shell_parse.py. Fake commands only."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/tool-guard/scripts"
spec = importlib.util.spec_from_file_location("shell_parse", SCRIPTS / "shell_parse.py")
sp = importlib.util.module_from_spec(spec)
sys.modules["shell_parse"] = sp
spec.loader.exec_module(sp)


def calls(script: str) -> list[list[str]]:
    """Each invocation as [name, *args text]."""
    return [[inv.name, *(a.text for a in inv.args)] for inv in sp.walk(script)]


def names(script: str) -> list[str]:
    return [inv.name for inv in sp.walk(script)]


class SplitTests(unittest.TestCase):
    def test_separators_split_commands(self):
        self.assertEqual(names("a && b || c; d | e & f"), list("abcdef"))

    def test_newlines_and_line_continuations(self):
        self.assertEqual(names("a\nb\n\nc"), ["a", "b", "c"])
        self.assertEqual(calls("rm -r \\\n  -f x"), [["rm", "-r", "-f", "x"]])

    def test_comments_are_ignored_even_with_a_quote_in_them(self):
        self.assertEqual(calls("echo hi # don't do this"), [["echo", "hi"]])
        self.assertEqual(calls("# only a comment\nls"), [["ls"]])
        self.assertEqual(calls("echo a#b"), [["echo", "a#b"]])

    def test_quotes_are_removed_and_join_adjacent_text(self):
        self.assertEqual(calls("echo 'a b' \"c d\" e'f'\"g\""), [["echo", "a b", "c d", "efg"]])
        self.assertEqual(calls("rm \"-rf\" 'x'"), [["rm", "-rf", "x"]])

    def test_backslash_escapes_and_empty_strings(self):
        self.assertEqual(calls("\\rm a\\ b"), [["rm", "a b"]])
        self.assertEqual(calls("echo \"\" ''"), [["echo", "", ""]])

    def test_ansi_c_quoting_is_decoded(self):
        self.assertEqual(calls("$'\\x72m' -rf x")[0][0], "rm")
        self.assertEqual(calls("echo $'a\\tb'"), [["echo", "a\tb"]])

    def test_subshell_and_grouping_words_are_not_commands(self):
        self.assertEqual(names("(cd x && ls)"), ["cd", "ls"])
        self.assertEqual(names("{ a; b; }"), ["a", "b"])
        self.assertEqual(names("if a; then b; elif c; then d; else e; fi"), list("abcde"))
        self.assertEqual(names("while a; do b; done"), ["a", "b"])
        self.assertEqual(names("f() { rm -rf x; }; f"), ["f", "rm", "f"])

    def test_assignments_before_the_command_become_env(self):
        inv = next(iter(sp.walk("A=1 B='two words' cmd arg")))
        self.assertEqual((inv.name, inv.env), ("cmd", {"A": "1", "B": "two words"}))
        self.assertEqual(names("A=1"), [])

    def test_empty_and_blank_scripts_have_no_commands(self):
        self.assertEqual(names(""), [])
        self.assertEqual(names("   \n\t "), [])

    def test_unclosed_quotes_and_substitutions_raise(self):
        for script in ("echo 'x", 'echo "x', "echo `x", "echo $(x", "echo ${x", "echo $'x"):
            with self.subTest(script=script), self.assertRaises(sp.ParseError):
                list(sp.walk(script))

    def test_nesting_that_is_too_deep_raises(self):
        script = "echo " + "$(echo " * 9 + "x" + ")" * 9
        with self.assertRaises(sp.ParseError):
            list(sp.walk(script))


class RedirectTests(unittest.TestCase):
    def redirects(self, script: str) -> list[tuple[str, str]]:
        return [(op, word.text) for inv in sp.walk(script) for op, word in inv.redirects]

    def test_redirect_targets_are_found_with_and_without_spaces(self):
        self.assertEqual(self.redirects("cat < in > out"), [("<", "in"), (">", "out")])
        self.assertEqual(self.redirects("cat <in >>out"), [("<", "in"), (">>", "out")])
        self.assertEqual(self.redirects("echo hi &>all"), [("&>", "all")])

    def test_file_descriptors_are_not_arguments(self):
        self.assertEqual(calls("cmd 2>err 1>&2"), [["cmd"]])
        self.assertEqual(self.redirects("cmd 2>err 2>&1"), [(">", "err"), (">&", "1")])

    def test_a_number_argument_stays_an_argument(self):
        self.assertEqual(calls("sleep 5 && head -n 2 f"), [["sleep", "5"], ["head", "-n", "2", "f"]])

    def test_here_string(self):
        inv = next(iter(sp.walk("psql <<< 'DROP TABLE x'")))
        self.assertEqual(inv.stdin_texts, ["DROP TABLE x"])


class SubstitutionTests(unittest.TestCase):
    def test_command_substitution_is_walked(self):
        self.assertEqual(sorted(names("echo $(rm -rf x) `git clean -fd`")), ["echo", "git", "rm"])

    def test_substitution_inside_double_quotes_is_walked_single_quotes_are_not(self):
        self.assertIn("rm", names('echo "a $(rm -rf x) b"'))
        self.assertNotIn("rm", names("echo '$(rm -rf x)'"))

    def test_nested_substitution(self):
        self.assertIn("rm", names("echo $(echo $(rm -rf x))"))

    def test_process_substitution(self):
        self.assertIn("rm", names("diff <(rm -rf x) b"))

    def test_words_with_expansions_are_dynamic(self):
        inv = [i for i in sp.walk("rm -rf $DIR ${DIR}/x $(pwd) `pwd` plain") if i.name == "rm"][0]
        self.assertEqual([a.dynamic for a in inv.args], [False, True, True, True, True, False])
        self.assertTrue(next(iter(sp.walk("rm {a,b}"))).args[0].dynamic)
        self.assertFalse(next(iter(sp.walk("rm '{a,b}'"))).args[0].dynamic)

    def test_quoted_dollar_is_text(self):
        self.assertEqual(calls("echo \"cost: 5$\" '$HOME' \\$HOME"), [["echo", "cost: 5$", "$HOME", "$HOME"]])
        self.assertFalse(any(a.dynamic for a in next(iter(sp.walk("echo '$HOME'"))).args))

    def test_glob_flag_only_for_unquoted_wildcards(self):
        inv = next(iter(sp.walk("ls *.py '*.txt' a?")))
        self.assertEqual([a.glob for a in inv.args], [True, False, True])

    def test_dynamic_program_name_has_an_empty_name(self):
        self.assertEqual(names("$CMD -rf x"), [""])


class HeredocTests(unittest.TestCase):
    def test_body_is_stdin_text_not_commands(self):
        script = "cat <<EOF\nrm -rf x\nEOF\nls"
        self.assertEqual(names(script), ["cat", "ls"])
        self.assertEqual(next(iter(sp.walk(script))).stdin_texts, ["rm -rf x"])

    def test_tab_stripped_delimiter(self):
        script = "cat <<-EOF\n\tbody\n\tEOF\nls"
        self.assertEqual(names(script), ["cat", "ls"])

    def test_substitution_in_an_unquoted_body_runs_in_a_quoted_one_it_does_not(self):
        self.assertIn("rm", names("cat <<EOF\n$(rm -rf x)\nEOF"))
        self.assertNotIn("rm", names("cat <<'EOF'\n$(rm -rf x)\nEOF"))

    def test_commit_message_heredoc_with_an_apostrophe_parses(self):
        script = "git commit -m \"$(cat <<'EOF'\nFix: don't crash\n\nBody line\nEOF\n)\""
        self.assertEqual(names(script), ["cat", "git"])

    def test_two_heredocs_on_one_line(self):
        script = "cmd <<A <<B\none\nA\ntwo\nB\nnext"
        self.assertEqual(names(script), ["cmd", "next"])

    def test_missing_delimiter_takes_the_rest(self):
        self.assertEqual(names("cat <<EOF\nline\nline2"), ["cat"])


class PipelineTests(unittest.TestCase):
    def test_piped_echo_is_stdin_text_of_the_next_command(self):
        invs = list(sp.walk("echo 'DROP TABLE x' | psql"))
        self.assertEqual(invs[1].stdin_texts, ["DROP TABLE x"])

    def test_piped_heredoc_reaches_the_next_command(self):
        invs = list(sp.walk("cat <<EOF | psql\nDROP TABLE x;\nEOF"))
        self.assertEqual(invs[1].stdin_texts, ["DROP TABLE x;"])

    def test_newline_after_a_pipe_keeps_the_link(self):
        invs = list(sp.walk("echo 'a b' |\n xargs rm -rf"))
        self.assertEqual([a.text for a in invs[-1].args], ["-rf", "a", "b"])


class WrapperTests(unittest.TestCase):
    def test_wrappers_are_removed(self):
        for script in (
            "sudo rm -rf x",
            "sudo -u root -E rm -rf x",
            "sudo --user=root rm -rf x",
            "sudo --user root rm -rf x",
            "env A=1 rm -rf x",
            "env -i -u B A=1 rm -rf x",
            "nohup rm -rf x",
            "nice -n 5 rm -rf x",
            "nice -5 rm -rf x",
            "timeout 10 rm -rf x",
            "timeout -s KILL 10 rm -rf x",
            "time rm -rf x",
            "command rm -rf x",
            "exec rm -rf x",
            "stdbuf -oL rm -rf x",
            "ionice -c 3 rm -rf x",
            "sudo env A=1 nohup nice rm -rf x",
            "/usr/bin/env /bin/rm -rf x",
            "busybox rm -rf x",
            "\\rm -rf x",
        ):
            with self.subTest(script=script):
                self.assertEqual(calls(script)[-1], ["rm", "-rf", "x"])

    def test_wrapper_variables_are_collected(self):
        inv = list(sp.walk("sudo FOO=1 env BAR=2 cmd"))[-1]
        self.assertEqual((inv.name, inv.env), ("cmd", {"FOO": "1", "BAR": "2"}))

    def test_wrapper_without_a_command_runs_nothing(self):
        for script in ("sudo -l", "env", "command -v rm", "nohup"):
            with self.subTest(script=script):
                self.assertEqual(names(script), [])

    def test_absolute_path_and_exe_suffix_give_the_base_name(self):
        self.assertEqual(sp.base_name("/bin/rm"), "rm")
        self.assertEqual(sp.base_name("C:/tools/git.exe"), "git")
        self.assertEqual(sp.base_name("./rm"), "rm")


class NestedScriptTests(unittest.TestCase):
    def test_shell_dash_c(self):
        for script in (
            "sh -c 'rm -rf x'",
            "bash -c 'rm -rf x'",
            "bash -lc 'rm -rf x'",
            "bash -e -c 'rm -rf x'",
            "bash -o pipefail -c 'rm -rf x'",
            "sudo bash -c 'rm -rf x'",
            'zsh -c "rm -rf x"',
        ):
            with self.subTest(script=script):
                self.assertIn("rm", names(script))

    def test_nested_dash_c(self):
        self.assertIn("rm", names("bash -c \"sh -c 'rm -rf x'\""))

    def test_shell_reading_a_heredoc_or_a_pipe(self):
        self.assertIn("rm", names("bash <<EOF\nrm -rf x\nEOF"))
        self.assertIn("rm", names("echo 'rm -rf x' | sh"))
        self.assertIn("rm", names("bash <<< 'rm -rf x'"))

    def test_shell_with_a_script_file_is_not_opened(self):
        self.assertEqual(names("bash script.sh"), ["bash"])

    def test_eval_and_su(self):
        self.assertIn("rm", names("eval 'rm -rf x'"))
        self.assertIn("rm", names("eval rm -rf x"))
        self.assertIn("rm", names("su -c 'rm -rf x' root"))

    def test_xargs(self):
        inv = list(sp.walk("ls | xargs rm -rf"))[-1]
        self.assertEqual((inv.name, inv.dynamic_tail), ("rm", True))
        inv = list(sp.walk("echo a b | xargs rm -rf"))[-1]
        self.assertEqual(([a.text for a in inv.args], inv.dynamic_tail), (["-rf", "a", "b"], False))
        inv = list(sp.walk("xargs -n 1 -P 4 -0 sudo rm -rf"))[-1]
        self.assertEqual((inv.name, inv.dynamic_tail), ("rm", True))

    def test_xargs_replace_string_makes_the_argument_dynamic(self):
        inv = list(sp.walk("xargs -I{} rm -rf {}"))[-1]
        self.assertTrue(inv.args[-1].dynamic)
        inv = list(sp.walk("xargs -I X rm -rf tmp/X"))[-1]
        self.assertTrue(inv.args[-1].dynamic)

    def test_find_exec(self):
        for script in (
            "find . -name x -exec rm -rf {} \\;",
            "find . -execdir rm -rf {} +",
            "find . -ok rm -rf {} ';'",
        ):
            with self.subTest(script=script):
                inv = list(sp.walk(script))[-1]
                self.assertEqual((inv.name, [a.text for a in inv.args]), ("rm", ["-rf", "."]))

    def test_find_exec_placeholder_stands_for_the_starting_points(self):
        inv = list(sp.walk("find tmp build -name x -exec rm -rf {} +"))[-1]
        self.assertEqual([a.text for a in inv.args], ["-rf", "tmp", "build"])
        inv = list(sp.walk("find -name x -exec rm {} +"))[-1]
        self.assertEqual([a.text for a in inv.args], ["."])
        inv = list(sp.walk("find src -exec rm prefix{} +"))[-1]
        self.assertTrue(inv.args[-1].dynamic)

    def test_find_without_exec_is_one_invocation(self):
        self.assertEqual(names("find . -name x -type f"), ["find"])


class GitSplitTests(unittest.TestCase):
    def split(self, text: str):
        overrides, sub, rest = sp.git_split([sp.Word(t) for t in text.split()])
        return overrides, sub, [w.text for w in rest]

    def test_global_options_are_skipped_and_config_overrides_are_collected(self):
        self.assertEqual(self.split("push --force origin"), ([], "push", ["--force", "origin"]))
        self.assertEqual(self.split("-C dir push"), ([], "push", []))
        self.assertEqual(self.split("-c a=1 -c b=2 --no-pager log -n 3"), (["a=1", "b=2"], "log", ["-n", "3"]))
        self.assertEqual(self.split("-ca=1 status"), (["a=1"], "status", []))
        self.assertEqual(self.split("--config-env=a=VAR --git-dir x commit"), (["a=VAR"], "commit", []))

    def test_no_subcommand(self):
        self.assertEqual(self.split("--version"), ([], "", []))
        self.assertEqual(self.split(""), ([], "", []))


class OptionTests(unittest.TestCase):
    def opts(self, text: str, short_arg: str = "", long_arg=()):
        words = [sp.Word(t) for t in text.split()]
        return sp.parse_options(words, short_arg, long_arg)

    def test_clusters_long_flags_and_positionals(self):
        opts = self.opts("-rf --force a b")
        self.assertEqual(opts.flags, {"-r", "-f", "--force"})
        self.assertEqual([w.text for w in opts.positionals], ["a", "b"])

    def test_options_with_values(self):
        opts = self.opts("-m msg -n --message=text --author who x", "m", ("--author",))
        self.assertEqual(opts.values, {"-m": "msg", "--message": "text", "--author": "who"})
        self.assertEqual(opts.flags, {"-m", "-n", "--message", "--author"})
        self.assertEqual([w.text for w in opts.positionals], ["x"])

    def test_attached_value_stops_the_cluster(self):
        opts = self.opts("-nmtext", "m")
        self.assertEqual((opts.flags, opts.values), ({"-n", "-m"}, {"-m": "text"}))

    def test_a_value_that_looks_like_a_flag_is_a_value(self):
        opts = self.opts("-m -n", "m")
        self.assertNotIn("-n", opts.flags)

    def test_double_dash_ends_options(self):
        opts = self.opts("-f -- -r --force")
        self.assertEqual(opts.flags, {"-f"})
        self.assertEqual([w.text for w in opts.positionals], ["-r", "--force"])

    def test_single_dash_is_a_positional(self):
        self.assertEqual([w.text for w in self.opts("-").positionals], ["-"])

    def test_has_flag_alternatives_and_long_prefixes(self):
        opts = self.opts("-fd --forc --no-verif")
        self.assertTrue(sp.has_flag(opts, "-f|--force"))
        self.assertTrue(sp.has_flag(opts, "-d"))
        self.assertTrue(sp.has_flag(opts, "--force"))
        self.assertTrue(sp.has_flag(opts, "--no-verify"))
        self.assertFalse(sp.has_flag(opts, "-n|--dry-run"))
        self.assertFalse(sp.has_flag(self.opts("--fo"), "--force"))


if __name__ == "__main__":
    unittest.main()
