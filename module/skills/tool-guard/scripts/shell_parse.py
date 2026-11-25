# /// script
# requires-python = ">=3.10"
# ///
"""A small, careful shell command reader for the command and path guards (Phase 2, T2). Standard library only.

It does not run or expand anything. It turns a Bash command string into `Invocation`s: one per simple command,
including the commands inside `$(...)`, backticks, `<(...)`, `sh -c "..."`, `eval`, `xargs`, `find -exec`, and behind
wrappers such as `sudo`, `env VAR=x`, `nohup`, `timeout 5`. A guard then looks at each invocation.

Best effort, by design. It cannot see through variables (`$CMD -rf x`), aliases, functions, scripts it does not open
(`bash script.sh`), `python -c`, or text built at run time. See `references/guard-rules.md` for the known limits.
Anything it cannot read (unclosed quote, nesting too deep) raises `ParseError`; callers block (fail closed).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace

MAX_DEPTH = 6  # nesting of $(...), sh -c, eval
ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
VAR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]|[@*#?$!\-]")
BRACE_RE = re.compile(r"\{[^{}]*(,|\.\.)[^{}]*\}")
REDIRECT_OPS = ("<<<", "<<-", "&>>", "<<", "&>", ">>", ">&", ">|", "<&", "<>", ">", "<")
RESERVED = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"}
LONE_KEYWORDS = {"fi", "done", "esac"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "fish"}
ANSI_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "v": "\v"}


class ParseError(ValueError):
    """The command text cannot be read safely. The guard blocks."""


@dataclass(frozen=True)
class Word:
    text: str  # quotes removed; an expansion such as $HOME or $(...) stays as written
    dynamic: bool = False  # holds an expansion whose value is not known at hook time
    quoted: bool = False  # any quote or backslash was used
    glob: bool = False  # an unquoted * ? [
    subs: tuple[str, ...] = ()  # scripts inside $(...), backticks, <(...)


@dataclass
class Heredoc:
    body: str = ""
    expand: bool = True  # unquoted delimiter: $(...) in the body runs


@dataclass
class Command:
    words: list[Word] = field(default_factory=list)
    redirects: list[tuple[str, Word]] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)
    herestrings: list[Word] = field(default_factory=list)
    prev: Command | None = None  # the stage before this one in a pipeline


@dataclass
class Invocation:
    name: str  # base name of the program, "" when it is not known (e.g. $CMD)
    args: list[Word]
    env: dict[str, str]
    redirects: list[tuple[str, Word]]
    stdin_texts: list[str]  # literal text that feeds stdin: heredoc, here-string, piped echo/printf
    dynamic_tail: bool = False  # more arguments arrive from stdin (xargs) that we cannot see


@dataclass
class Opts:
    flags: set[str] = field(default_factory=set)  # "-r", "--force"; a short cluster "-rf" gives "-r" and "-f"
    values: dict[str, str] = field(default_factory=dict)  # option -> last value
    pairs: list[tuple[str, Word]] = field(default_factory=list)  # every (option, value) in order
    positionals: list[Word] = field(default_factory=list)


# --- lexer -----------------------------------------------------------------------------------------


class _Lexer:
    """One pass over the text. With `nested` it stops at the `)` that closes a `$(` (index left in `self.i`)."""

    def __init__(self, text: str, start: int = 0, depth: int = 0, nested: bool = False):
        if depth > MAX_DEPTH:
            raise ParseError("commands are nested too deeply")
        self.s, self.i, self.n = text, start, len(text)
        self.depth, self.nested, self.parens = depth, nested, 0
        self.commands: list[Command] = []
        self.cur = Command()
        self.pipe_prev: Command | None = None
        self.pending: list[tuple[Heredoc, str, bool]] = []
        self.redirect: str | None = None

    # -- structure

    def run(self) -> list[Command]:
        s = self.s
        while self.i < self.n:
            c = s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\\" and s.startswith("\n", self.i + 1):
                self.i += 2
            elif c == "\n":
                self.i += 1
                self._end(pipe=False, keep_pipe=True)
                self._heredoc_bodies()
            elif c == "#":
                end = s.find("\n", self.i)
                self.i = self.n if end < 0 else end
            elif c in "<>" and s.startswith("(", self.i + 1):
                self._process_substitution()
            elif c in "<>":
                self._redirect_op()
            elif c == "&" and s.startswith(">", self.i + 1):
                self._redirect_op()
            elif c in ";&|()":
                if self._operator(c):
                    return self.commands
            else:
                self._word()
        if self.nested:
            raise ParseError("unclosed $(")
        self._end(pipe=False)
        return self.commands

    def _operator(self, c: str) -> bool:
        """Handle a separator. True when a nested lexer reached its closing `)`."""
        s, i = self.s, self.i
        two = s[i : i + 2]
        if c == ")":
            if self.nested and self.parens == 0:
                self._end(pipe=False)
                return True
            self.parens = max(0, self.parens - 1)
            self.i += 1
            self._end(pipe=False)
        elif c == "(":
            self.parens += 1
            self.i += 1
            self._end(pipe=False)
        elif two in ("||", "|&"):
            self.i += 2
            self._end(pipe=two == "|&")
        elif c == "|":
            self.i += 1
            self._end(pipe=True)
        elif two in ("&&", ";;"):
            self.i += 2
            self._end(pipe=False)
        else:
            self.i += 1
            self._end(pipe=False)
        return False

    def _end(self, pipe: bool, keep_pipe: bool = False) -> None:
        """Close the current command. `pipe`: the next command reads from it. `keep_pipe`: a newline after `|`."""
        cur = self.cur
        if cur.words or cur.redirects or cur.heredocs or cur.herestrings:
            self.commands.append(cur)
            self.cur = Command(prev=cur if pipe else None)
        elif not keep_pipe:
            self.cur = Command()
        self.redirect = None

    # -- words

    def _word(self) -> None:
        word = self._read_word()
        if word is None:
            return
        # "2>file": digits stuck to a redirect operator are a file descriptor, not an argument
        if word.text.isdigit() and not word.quoted and self.s[self.i : self.i + 1] in ("<", ">"):
            if not self.s.startswith("(", self.i + 1):
                self._redirect_op()
                return
        self._add(word)

    def _add(self, word: Word) -> None:
        op, self.redirect = self.redirect, None
        cur = self.cur
        if op is None:
            cur.words.append(word)
        elif op in ("<<", "<<-"):
            heredoc = Heredoc(expand=not word.quoted)
            cur.heredocs.append(heredoc)
            self.pending.append((heredoc, word.text, op == "<<-"))
        elif op == "<<<":
            cur.herestrings.append(word)
        else:
            cur.redirects.append((op, word))

    def _redirect_op(self) -> None:
        for op in REDIRECT_OPS:
            if self.s.startswith(op, self.i):
                self.i += len(op)
                self.redirect = op
                return
        raise ParseError("bad redirect")  # unreachable: the caller checked the first character

    def _process_substitution(self) -> None:
        start = self.i
        inner_start = self.i + 2
        end = self._close_paren(inner_start)
        inner = self.s[inner_start:end]
        self.i = end + 1
        self._add(Word(self.s[start : end + 1], dynamic=True, subs=(inner,)))

    def _close_paren(self, inner_start: int) -> int:
        """Index of the `)` that closes a `$(` or `<(` whose body starts at `inner_start`."""
        sub = _Lexer(self.s, inner_start, self.depth + 1, nested=True)
        sub.run()
        return sub.i

    def _read_word(self) -> Word | None:
        s, n = self.s, self.n
        buf: list[str] = []
        subs: list[str] = []
        dynamic = quoted = glob = False
        while self.i < n:
            c = s[self.i]
            if c in " \t\n;&|()<>":
                break
            if c == "\\":
                nxt = s[self.i + 1 : self.i + 2]
                if nxt == "\n":
                    self.i += 2
                elif nxt == "":
                    buf.append("\\")
                    self.i += 1
                else:
                    buf.append(nxt)
                    quoted = True
                    self.i += 2
            elif c == "'":
                end = s.find("'", self.i + 1)
                if end < 0:
                    raise ParseError("unclosed single quote")
                buf.append(s[self.i + 1 : end])
                quoted = True
                self.i = end + 1
            elif c == '"':
                self.i += 1
                quoted = True
                text, dyn, found = self._double_quoted()
                buf.append(text)
                dynamic |= dyn
                subs += found
            elif c == "$" or c == "`":
                text, dyn, found, ansi = self._expansion(in_quotes=False)
                buf.append(text)
                dynamic |= dyn
                subs += found
                quoted |= ansi
            else:
                if c in "*?[":
                    glob = True
                buf.append(c)
                self.i += 1
        text = "".join(buf)
        if not quoted and BRACE_RE.search(text):
            dynamic = True
        if not buf and not quoted:
            return None
        return Word(text, dynamic, quoted, glob, tuple(subs))

    def _double_quoted(self) -> tuple[str, bool, list[str]]:
        s, n = self.s, self.n
        buf: list[str] = []
        subs: list[str] = []
        dynamic = False
        while self.i < n:
            c = s[self.i]
            if c == '"':
                self.i += 1
                return "".join(buf), dynamic, subs
            if c == "\\":
                nxt = s[self.i + 1 : self.i + 2]
                if nxt in ("$", "`", '"', "\\"):
                    buf.append(nxt)
                    self.i += 2
                elif nxt == "\n":
                    self.i += 2
                else:
                    buf.append("\\")
                    self.i += 1
            elif c == "$" or c == "`":
                text, dyn, found, _ = self._expansion(in_quotes=True)
                buf.append(text)
                dynamic |= dyn
                subs += found
            else:
                buf.append(c)
                self.i += 1
        raise ParseError("unclosed double quote")

    def _expansion(self, in_quotes: bool) -> tuple[str, bool, list[str], bool]:
        """At a `$` or backtick. Returns (text, dynamic, scripts to analyse, was ANSI-C quoted)."""
        s = self.s
        i = self.i
        if s[i] == "`":
            end = i + 1
            while end < self.n and s[end] != "`":
                end += 2 if s[end] == "\\" else 1
            if end >= self.n:
                raise ParseError("unclosed backtick")
            inner = s[i + 1 : end].replace("\\`", "`").replace("\\\\", "\\")
            self.i = end + 1
            return s[i : end + 1], True, [inner], False
        nxt = s[i + 1 : i + 2]
        if nxt == "(":
            end = self._close_paren(i + 2)
            self.i = end + 1
            return s[i : end + 1], True, [s[i + 2 : end]], False
        if nxt == "{":
            depth, j = 1, i + 2
            while j < self.n and depth:
                depth += {"{": 1, "}": -1}.get(s[j], 0)
                j += 1
            if depth:
                raise ParseError("unclosed ${")
            self.i = j
            return s[i:j], True, [], False
        if nxt == "'" and not in_quotes:
            end = i + 2
            while end < self.n and s[end] != "'":
                end += 2 if s[end] == "\\" else 1
            if end >= self.n:
                raise ParseError("unclosed $'")
            self.i = end + 1
            return _ansi_c(s[i + 2 : end]), False, [], True
        if nxt == '"' and not in_quotes:
            self.i += 1  # $"..." is a double-quoted string; drop the $
            return "", False, [], False
        match = VAR_RE.match(s, i + 1)
        if match:
            self.i = match.end()
            return s[i : match.end()], True, [], False
        self.i += 1
        return "$", False, [], False

    # -- here-documents

    def _heredoc_bodies(self) -> None:
        s = self.s
        for heredoc, delimiter, strip_tabs in self.pending:
            lines: list[str] = []
            while self.i < self.n:
                end = s.find("\n", self.i)
                line = s[self.i : self.n if end < 0 else end]
                self.i = self.n if end < 0 else end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                lines.append(line)
            heredoc.body = "\n".join(lines)
        self.pending = []


def _ansi_c(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 >= len(text):
            out.append(c)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in ANSI_SIMPLE:
            out.append(ANSI_SIMPLE[nxt])
            i += 2
        elif nxt == "x" and re.match(r"[0-9A-Fa-f]{1,2}", text[i + 2 :]):
            digits = re.match(r"[0-9A-Fa-f]{1,2}", text[i + 2 :]).group(0)
            out.append(chr(int(digits, 16)))
            i += 2 + len(digits)
        elif nxt in "01234567":
            digits = re.match(r"[0-7]{1,3}", text[i + 1 :]).group(0)
            out.append(chr(int(digits, 8) & 0xFF))
            i += 1 + len(digits)
        elif nxt in "uU" and re.match(r"[0-9A-Fa-f]", text[i + 2 :]):
            limit = 4 if nxt == "u" else 8
            digits = re.match(f"[0-9A-Fa-f]{{1,{limit}}}", text[i + 2 :]).group(0)
            out.append(chr(min(int(digits, 16), 0x10FFFF)))
            i += 2 + len(digits)
        else:
            out.append(nxt)
            i += 2
    return "".join(out)


def parse(script: str, depth: int = 0) -> list[Command]:
    """The simple commands of `script`, in order, with their redirects and here-documents."""
    return _Lexer(script, 0, depth).run()


# --- options ---------------------------------------------------------------------------------------


def parse_options(
    args: Iterable[Word], short_arg: str = "", long_arg: Iterable[str] = (), attached_arg: str = ""
) -> Opts:
    """Split arguments into flags, option values and positionals.

    `short_arg`: short option letters that take a value (`-m "text"` or `-mtext`). `long_arg`: long options that take
    a separate value (`--message text`). `attached_arg`: short letters whose value is only the rest of the cluster
    (`mysql -psecret`; a bare `-p` takes nothing). `--name=value` always works. After `--` everything is positional.
    """
    words = list(args)
    long_values = set(long_arg)
    opts = Opts()

    def value(key: str, text: str, source: Word) -> None:
        opts.values[key] = text
        opts.pairs.append((key, replace(source, text=text)))

    i, done = 0, False
    while i < len(words):
        text = words[i].text
        if done:
            opts.positionals.append(words[i])
        elif text == "--":
            done = True
        elif text.startswith("--"):
            name, eq, rest = text.partition("=")
            opts.flags.add(name)
            if eq:
                value(name, rest, words[i])
            elif name in long_values and i + 1 < len(words):
                i += 1
                value(name, words[i].text, words[i])
        elif text.startswith("-") and len(text) > 1:
            cluster = text[1:]
            for k, letter in enumerate(cluster):
                opts.flags.add("-" + letter)
                if letter in attached_arg:
                    if cluster[k + 1 :]:
                        value("-" + letter, cluster[k + 1 :], words[i])
                    break
                if letter in short_arg:
                    if cluster[k + 1 :]:
                        value("-" + letter, cluster[k + 1 :], words[i])
                    elif i + 1 < len(words):
                        i += 1
                        value("-" + letter, words[i].text, words[i])
                    break
        else:
            opts.positionals.append(words[i])
        i += 1
    return opts


def has_flag(opts: Opts, spec: str) -> bool:
    """True when any alternative of `spec` ("-f|--force") is present.

    A long option also matches when it is a prefix of the spec (`--forc`, `--no-verif`): git and GNU tools accept it.
    """
    for alt in spec.split("|"):
        if alt in opts.flags:
            return True
        if alt.startswith("--") and any(
            flag.startswith("--") and len(flag) >= 6 and alt.startswith(flag) for flag in opts.flags
        ):
            return True
    return False


# --- wrappers, shells and friends ------------------------------------------------------------------


@dataclass(frozen=True)
class Wrapper:
    short_arg: str = ""  # short options that take a value
    long_arg: tuple[str, ...] = ()  # long options that take a separate value
    skip: int = 0  # positional words before the command (timeout DURATION)
    assigns: bool = False  # accepts NAME=value words before the command


WRAPPERS = {
    "sudo": Wrapper(
        "CDgpRrTtUu",
        (
            "--user",
            "--group",
            "--prompt",
            "--chdir",
            "--role",
            "--type",
            "--close-from",
            "--other-user",
            "--command-timeout",
            "--chroot",
        ),
        assigns=True,
    ),
    "doas": Wrapper("uC"),
    "env": Wrapper("uCS", ("--unset", "--chdir", "--split-string"), assigns=True),
    "command": Wrapper(),
    "builtin": Wrapper(),
    "exec": Wrapper("a"),
    "nohup": Wrapper(),
    "time": Wrapper("fo"),
    "nice": Wrapper("n", ("--adjustment",)),
    "ionice": Wrapper("cnpPtu"),
    "timeout": Wrapper("sk", ("--signal", "--kill-after"), skip=1),
    "setsid": Wrapper(),
    "stdbuf": Wrapper("ioe"),
    "watch": Wrapper("n", ("--interval",)),
    "unbuffer": Wrapper(),
    "busybox": Wrapper(),
}
XARGS_SHORT_ARG = "IELnPsda"
XARGS_LONG_ARG = ("--arg-file", "--delimiter", "--max-args", "--max-lines", "--max-procs", "--max-chars")
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}


def base_name(text: str) -> str:
    """`/bin/rm` -> `rm`, `rm.exe` -> `rm`."""
    name = text.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".exe") else name


def _unwrap(name: str, spec: Wrapper, words: list[Word]) -> tuple[list[Word], dict[str, str]] | None:
    """The command behind a wrapper and the variables the wrapper sets; None when it runs no command."""
    env: dict[str, str] = {}
    i, flags = 0, ""
    while i < len(words):
        text = words[i].text
        if text == "--":
            i += 1
            break
        if len(text) > 1 and text[0] == "-":
            if text.startswith("--"):
                if "=" not in text and text in spec.long_arg:
                    i += 1
            else:
                flags += text[1:]
                for k, letter in enumerate(text[1:]):
                    if letter in spec.short_arg:
                        if k == len(text) - 2:
                            i += 1
                        break
            i += 1
        elif spec.assigns and ASSIGN_RE.match(text):
            key, _, value = text.partition("=")
            env[key] = value
            i += 1
        else:
            break
    if name == "command" and ("v" in flags or "V" in flags):
        return None  # `command -v rm` only prints
    i += spec.skip
    return (words[i:], env) if i < len(words) else None


def _emitted_text(stage: Command | None) -> list[str] | None:
    """Literal text a pipeline stage writes: echo, printf, or cat of a here-document. None when unknown."""
    if stage is None or not stage.words:
        return None
    name = base_name(stage.words[0].text)
    args = stage.words[1:]
    if name == "echo":
        return [" ".join(w.text for w in args if not (w.text.startswith("-") and len(w.text) <= 3))]
    if name == "printf" and args and "%" not in args[0].text:
        return [" ".join(w.text for w in args)]
    if name in ("cat", "tee") and (stage.heredocs or stage.herestrings):
        return [h.body for h in stage.heredocs] + [w.text for w in stage.herestrings]
    return None


def walk(script: str, depth: int = 0) -> Iterator[Invocation]:
    """Every simple command in `script`, including the ones nested inside it. Raises ParseError."""
    if depth > MAX_DEPTH:
        raise ParseError("commands are nested too deeply")
    for command in parse(script, depth):
        yield from _walk_command(command, depth)


def _heredoc_scripts(body: str, depth: int) -> Iterator[str]:
    """The `$(...)` and backtick scripts inside an unquoted here-document body."""
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
        elif body.startswith("$(", i):
            lexer = _Lexer(body, i + 2, depth + 1, nested=True)
            lexer.run()
            yield body[i + 2 : lexer.i]
            i = lexer.i + 1
        elif body[i] == "`":
            end = body.find("`", i + 1)
            if end < 0:
                raise ParseError("unclosed backtick")
            yield body[i + 1 : end]
            i = end + 1
        else:
            i += 1


def _walk_command(command: Command, depth: int) -> Iterator[Invocation]:
    for word in [*command.words, *(t for _, t in command.redirects), *command.herestrings]:
        for sub in word.subs:
            yield from walk(sub, depth + 1)
    for heredoc in command.heredocs:
        if heredoc.expand:
            for sub in _heredoc_scripts(heredoc.body, depth):
                yield from walk(sub, depth + 1)
    words = list(command.words)
    env: dict[str, str] = {}
    while words and ASSIGN_RE.match(words[0].text):
        key, _, value = words.pop(0).text.partition("=")
        env[key] = value
    while words and words[0].text in RESERVED and not words[0].quoted:
        words.pop(0)
    if not words or (len(words) == 1 and words[0].text in LONE_KEYWORDS):
        return
    stdin = [h.body for h in command.heredocs] + [w.text for w in command.herestrings]
    piped = _emitted_text(command.prev) if command.prev is not None else None
    if piped:
        stdin += piped
    yield from _expand(words, env, command, stdin, piped, depth, False)


def _expand(
    words: list[Word],
    env: dict[str, str],
    command: Command,
    stdin: list[str],
    piped: list[str] | None,
    depth: int,
    dynamic_tail: bool,
) -> Iterator[Invocation]:
    while True:
        if words[0].dynamic:  # $CMD args: the program is not known
            yield Invocation("", words[1:], env, command.redirects, stdin, dynamic_tail)
            return
        name = base_name(words[0].text)
        spec = WRAPPERS.get(name)
        if spec is None:
            break
        inner = _unwrap(name, spec, words[1:])
        if inner is None:
            return
        words, extra = inner
        env = {**env, **extra}
    args = words[1:]
    invocation = Invocation(name, args, env, command.redirects, stdin, dynamic_tail)
    yield invocation
    if name in SHELLS:
        yield from _shell(invocation, depth)
    elif name == "su":
        opts = parse_options(args, "c", ("--command",))
        script = opts.values.get("-c") or opts.values.get("--command")
        if script:
            yield from walk(script, depth + 1)
    elif name == "eval":
        yield from walk(" ".join(a.text for a in args), depth + 1)
    elif name == "xargs":
        yield from _xargs(args, env, command, stdin, piped, depth)
    elif name == "find":
        yield from _find_exec(args, env, command, stdin, depth)


def _shell(invocation: Invocation, depth: int) -> Iterator[Invocation]:
    """`sh -c "script"`, `bash <<EOF`, `echo cmd | sh`: read the script when it is written in the command."""
    args = invocation.args
    i, has_c = 0, False
    while i < len(args):
        text = args[i].text
        if text == "--":
            i += 1
            break
        if text in ("--rcfile", "--init-file"):
            i += 2
        elif text.startswith("--"):
            i += 1
        elif len(text) > 1 and text[0] in "-+":
            has_c |= text[0] == "-" and "c" in text[1:]
            i += 2 if text[-1] in "oO" else 1
        else:
            break
    rest = args[i:]
    if has_c:
        if rest:
            yield from walk(rest[0].text, depth + 1)
    elif not rest:
        for text in invocation.stdin_texts:
            yield from walk(text, depth + 1)


def _xargs(
    args: list[Word], env: dict[str, str], command: Command, stdin: list[str], piped: list[str] | None, depth: int
) -> Iterator[Invocation]:
    i, replace_str = 0, ""
    while i < len(args):
        text = args[i].text
        if text == "--":
            i += 1
            break
        if text.startswith("--"):
            if text.startswith("--replace"):
                replace_str = text.partition("=")[2] or "{}"
            i += 2 if text in XARGS_LONG_ARG else 1
        elif len(text) > 1 and text[0] == "-":
            letter = text[1]
            if letter == "i":
                replace_str = text[2:] or "{}"
            elif letter == "I":
                replace_str = text[2:] or (args[i + 1].text if i + 1 < len(args) else "")
            i += 2 if letter in XARGS_SHORT_ARG and len(text) == 2 else 1
        else:
            break
    inner = args[i:]
    if not inner:
        return
    if replace_str:
        inner = [replace(w, dynamic=True) if replace_str in w.text else w for w in inner]
    tail = True
    if piped and not replace_str:  # echo a b | xargs rm: the names are known
        inner = inner + [Word(part) for text in piped for part in text.split()]
        tail = False
    yield from _expand(inner, env, command, stdin, None, depth, tail)


def _find_exec(
    args: list[Word], env: dict[str, str], command: Command, stdin: list[str], depth: int
) -> Iterator[Invocation]:
    """`find DIR ... -exec CMD {} ;`: CMD runs on files under DIR, so `{}` stands for the starting points."""
    starts: list[Word] = []
    for word in args:
        if word.text.startswith(("-", "(", "!")):
            break
        starts.append(word)
    starts = starts or [Word(".")]
    i = 0
    while i < len(args):
        if args[i].text in FIND_EXEC:
            inner: list[Word] = []
            i += 1
            while i < len(args) and args[i].text not in (";", "+"):
                if args[i].text == "{}":
                    inner += starts
                else:
                    inner.append(replace(args[i], dynamic=True) if "{}" in args[i].text else args[i])
                i += 1
            if inner:
                yield from _expand(inner, env, command, stdin, None, depth, False)
        i += 1
