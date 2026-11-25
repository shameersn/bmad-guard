# /// script
# requires-python = ">=3.10"
# ///
"""Command guard for the Bash tool (Phase 2, T2): destructive commands and data export. Fails closed.

The rule list is in the skill's customize.toml (see guard_config.py); a project can add rules, never remove one.
Each rule has a stable id that is also the reason code in the block message and the event. Events carry the rule id,
never the command text.

Blocked, with the rule id:
  CMD_RM_RECURSIVE, CMD_FIND_DELETE            recursive delete (or find -delete) outside the scratch folders
  CMD_GIT_FORCE_PUSH                           git push --force, -f, --force-with-lease, +refspec
  CMD_GIT_RESET_HARD, CMD_GIT_CLEAN, CMD_GIT_BRANCH_DELETE
  CMD_GIT_NO_VERIFY, CMD_GIT_COMMIT_SKIP_HOOKS  --no-verify, git commit -n
  CMD_GIT_HOOKS_PATH                           any change of core.hooksPath
  CMD_SQL_DESTRUCTIVE                          DROP, TRUNCATE, DELETE without WHERE in a SQL client
  CMD_CHMOD_WORLD_WRITABLE                     chmod -R 777 and the like
  EXPORT_DB_REMOTE, EXPORT_DB_HOST_UNKNOWN     database clients and dumps against a host that is not local
  EXPORT_S3_DOWNLOAD                           aws s3 cp, sync, mv from a bucket
  CMD_UNPARSABLE                               the command cannot be read: blocked
  PROJECT_CMD_<ID>                             a rule the project added
  EXT_BAD_FILE                                 the project's extension file is not usable: blocked
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard_config as gc  # noqa: E402 - sibling modules, found through the line above
import hook_common as hc  # noqa: E402
import shell_parse as sp  # noqa: E402

GUARD = "command-guard"
MAX_SQL_FILE = 1_000_000  # bytes of an SQL script we read to look for DROP and friends

# Per git subcommand: short option letters and long options that take a value (so a message is not read as a flag).
GIT_ARGS = {
    "commit": ("mFCct", ("--message", "--file", "--author", "--date", "--template", "--cleanup", "--reuse-message")),
    "push": ("o", ("--repo", "--push-option", "--receive-pack", "--exec")),
    "merge": ("mFsX", ("--message", "--file", "--strategy", "--strategy-option")),
    "rebase": ("sXxC", ("--strategy", "--strategy-option", "--onto", "--exec")),
    "am": ("pC", ()),
    "clean": ("e", ("--exclude",)),
    "branch": ("u", ("--set-upstream-to",)),
}
GIT_GLOBAL_WITH_VALUE = {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--attr-source"}


# --- context ---------------------------------------------------------------------------------------


@dataclass
class Context:
    root: Path  # real path of the project
    cwd: Path  # real path of the directory the command starts in
    rules: gc.Rules
    home: Path
    environ: Mapping[str, str]

    def __post_init__(self):
        self.relative_scratch: list[tuple[bool, tuple[str, ...]]] = []  # (any depth, folder parts)
        self.absolute_scratch: list[str] = [os.path.realpath(tempfile.gettempdir())]  # $TMPDIR
        for entry in self.rules.scratch:
            if entry.startswith("/"):
                self.absolute_scratch.append(os.path.realpath(entry))
            else:
                anywhere = entry.startswith("**/")
                parts = tuple(p for p in (entry[3:] if anywhere else entry).split("/") if p)
                self.relative_scratch.append((anywhere, parts))

    def in_scratch(self, word: sp.Word) -> bool:
        """True only when the path is provably inside a scratch folder. A variable or substitution is not provable."""
        text = word.text
        if word.dynamic or not text:
            return False
        if text == "~" or text.startswith("~/"):
            text = str(self.home) + text[1:]
        elif text.startswith("~"):
            return False
        if word.glob:  # tmp/* is fine, tmp* or */x is not: the folder part must be written out
            first = min(text.index(c) for c in "*?[" if c in text)
            if "/" not in text[:first] or ".." in text[first:].split("/"):
                return False
            text = text[:first].rsplit("/", 1)[0] or "/"
        path = os.path.realpath(os.path.join(self.cwd, text))
        root = str(self.root)
        if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
            parts = tuple(p for p in path[len(root) :].split(os.sep) if p)
            for anywhere, folder in self.relative_scratch:
                if parts[: len(folder)] == folder and parts:
                    return True
                if anywhere and any(parts[i : i + len(folder)] == folder for i in range(len(parts))):
                    return True
            return False
        return any(path.startswith(folder.rstrip(os.sep) + os.sep) for folder in self.absolute_scratch)

    def is_local(self, host: str) -> bool:
        host = host.strip().strip("[]").lower()
        return not host or host in self.rules.local_hosts or host.startswith(("127.", "/"))


# --- git -------------------------------------------------------------------------------------------


def git_split(args: list[sp.Word]) -> tuple[list[str], str, list[sp.Word]]:
    """(values of -c options, subcommand, words after the subcommand) for `git [global options] <subcommand> ...`."""
    overrides: list[str] = []
    i = 0
    while i < len(args):
        text = args[i].text
        if text == "-c":
            overrides.append(args[i + 1].text if i + 1 < len(args) else "")
            i += 2
        elif text in GIT_GLOBAL_WITH_VALUE:
            i += 2
        elif text.startswith("--config-env="):
            overrides.append(text.partition("=")[2])
            i += 1
        elif text.startswith("-c") and not text.startswith("--"):
            overrides.append(text[2:])
            i += 1
        elif text.startswith("-"):
            i += 1
        else:
            return overrides, text, args[i + 1 :]
    return overrides, "", []


def check_git_hooks_path(inv: sp.Invocation, ctx: Context) -> bool:
    """Any write to core.hooksPath: `git config`, `git -c`, or GIT_CONFIG_* in the environment. Reading it is fine."""
    if inv.name.lower() != "git":
        return False
    if any("hookspath" in value.lower() for key, value in inv.env.items() if key.startswith("GIT_CONFIG")):
        return True
    overrides, sub, rest = git_split(inv.args)
    if any("hookspath" in value.lower() for value in overrides):
        return True
    if sub != "config":
        return False
    opts = sp.parse_options(rest, "f", ("--file", "--blob", "--type", "--default"))
    names = [i for i, word in enumerate(opts.positionals) if word.text.lower() == "core.hookspath"]
    if not names:
        return False
    if sp.has_flag(opts, "--unset|--unset-all|--add|--replace-all|--edit|-e"):
        return True
    if sp.has_flag(opts, "--get|--get-all|--get-regexp|--list|-l"):
        return False
    return len(opts.positionals) > names[0] + 1  # a value follows the name: a write


# --- rm, find, chmod -------------------------------------------------------------------------------


def check_rm_recursive(inv: sp.Invocation, ctx: Context) -> bool:
    if inv.name.lower() != "rm":
        return False
    opts = sp.parse_options(inv.args)
    if "--no-preserve-root" in opts.flags:
        return True
    if not sp.has_flag(opts, "-r|-R|--recursive"):
        return False
    return inv.dynamic_tail or any(not ctx.in_scratch(target) for target in opts.positionals)


def check_find_delete(inv: sp.Invocation, ctx: Context) -> bool:
    if inv.name.lower() != "find" or not any(a.text == "-delete" for a in inv.args):
        return False
    starts = []
    for word in inv.args:
        if word.text.startswith(("-", "(", "!")):
            break
        starts.append(word)
    return not starts or any(not ctx.in_scratch(word) for word in starts)


WHO_RE = re.compile(r"([ugoa]*)([-+=])([rwxXst]*)")


def check_chmod_world_writable(inv: sp.Invocation, ctx: Context) -> bool:
    if inv.name.lower() != "chmod":
        return False
    opts = sp.parse_options(inv.args)
    if not sp.has_flag(opts, "-R|--recursive") or not opts.positionals:
        return False
    mode = opts.positionals[0].text
    if re.fullmatch(r"[0-7]{3,4}", mode):
        return int(mode[-1]) & 2 == 2
    for clause in mode.split(","):  # o+w, a+rwx, ugo=rwx: written for others. A bare +w is the owner (umask).
        match = WHO_RE.fullmatch(clause)
        if match and match.group(2) in "+=" and "w" in match.group(3) and set(match.group(1)) & {"o", "a"}:
            return True
    return False


# --- SQL -------------------------------------------------------------------------------------------

SQL_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "usql", "pgcli", "mycli", "litecli"}
SQL_START = re.compile(r"(?is)\s*(drop|truncate|delete)\b(.*)")
TRIVIAL_WHERE = re.compile(r"(?is)\bwhere\s+(1\s*=\s*1|true|1|''\s*=\s*'')\s*\)?\s*$")


def _plain_sql(text: str) -> str:
    """The SQL without comments, and with every quoted string or name emptied, so keywords inside them do not count."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            end = i + 1
            while end < n and not (text[end] == c and text[end : end + 2] != c * 2):
                end += 2 if text[end] == c else 1
            out.append(c * 2)
            i = end + 1
        elif text.startswith("--", i) or c == "#":
            end = text.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def sql_is_destructive(text: str) -> bool:
    """DROP, TRUNCATE, or DELETE with no WHERE (or a WHERE that is always true), as a statement of its own."""
    for statement in _plain_sql(text).split(";"):
        match = SQL_START.fullmatch(statement)
        if not match:
            continue
        verb, rest = match.group(1).lower(), match.group(2)
        if verb in ("drop", "truncate"):
            if re.match(r"\s+\w", rest):
                return True
        elif re.match(r"\s+(from\b|\w+\s+from\b)", rest, re.IGNORECASE):
            if not re.search(r"(?i)\bwhere\b", rest) or TRIVIAL_WHERE.search(rest):
                return True
    return False


def _sql_texts(inv: sp.Invocation, ctx: Context) -> list[str]:
    """Everything that could be SQL for this client: its arguments, stdin text, and script files it is told to read."""
    texts = list(inv.stdin_texts)
    for word in inv.args:
        text = word.text
        texts.append(text)
        if text.startswith("--") and "=" in text:  # --execute='DROP TABLE x'
            texts.append(text.partition("=")[2])
        elif text.startswith("-") and not text.startswith("--") and len(text) > 2:  # -e'DROP TABLE x'
            texts.append(text[2:])
    short = "f" if inv.name.lower() == "psql" else ""  # in mysql -f is --force
    files = [w.text for key, w in sp.parse_options(inv.args, short, ("--file",)).pairs if key in ("-f", "--file")]
    files += [word.text for op, word in inv.redirects if op == "<"]
    for name in files:
        path = Path(os.path.expanduser(name))
        path = path if path.is_absolute() else ctx.cwd / path
        try:
            if path.is_file() and path.stat().st_size <= MAX_SQL_FILE:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue  # unreadable: nothing to look at (the path guard decides about sensitive files)
    return texts


def check_sql_destructive(inv: sp.Invocation, ctx: Context) -> bool:
    return inv.name.lower() in SQL_CLIENTS and any(sql_is_destructive(t) for t in _sql_texts(inv, ctx))


# --- database export -------------------------------------------------------------------------------

PG_LONG = (
    "--host",
    "--port",
    "--username",
    "--dbname",
    "--command",
    "--file",
    "--output",
    "--field-separator",
    "--record-separator",
    "--pset",
    "--set",
    "--variable",
    "--log-file",
    "--table-attr",
    "--schema",
    "--exclude-schema",
    "--table",
    "--exclude-table",
    "--format",
    "--jobs",
    "--compress",
    "--encoding",
    "--role",
)
MYSQL_LONG = (
    "--host",
    "--port",
    "--user",
    "--database",
    "--execute",
    "--socket",
    "--protocol",
    "--default-character-set",
    "--result-file",
    "--tab",
)
MONGO_LONG = (
    "--host",
    "--port",
    "--username",
    "--password",
    "--authenticationDatabase",
    "--authenticationMechanism",
    "--eval",
    "--file",
    "--db",
    "--collection",
    "--out",
    "--uri",
    "--query",
    "--fields",
    "--sort",
    "--limit",
    "--skip",
    "--tlsCAFile",
    "--tlsCertificateKeyFile",
)
MYSQL_HIDDEN = ("--defaults-file", "--defaults-extra-file", "--login-path", "--defaults-group-suffix")


@dataclass(frozen=True)
class DbClient:
    style: str  # "pg", "mysql" or "mongo"
    host_flags: tuple[str, ...]
    uri_flags: tuple[str, ...]  # options whose value may be a URI or connection string
    positional_uri: bool  # a positional word may be a URI, connection string or host
    short_arg: str
    long_arg: tuple[str, ...]
    env_host: tuple[str, ...]
    hidden: tuple[str, ...] = ()  # options that hide the host (service file, login path)
    attached_arg: str = ""  # short letters whose value is glued on (mysql -psecret)


PG = ("pg", ("-h", "--host"), ("-d", "--dbname"), True)
MYSQL = ("mysql", ("-h", "--host"), (), False)
MONGO_SHELL = ("mongo", ("--host",), ("--uri",), True)
MONGO_TOOL = ("mongo", ("--host", "-h"), ("--uri",), False)
DB_CLIENTS = {
    "psql": DbClient(*PG, "hpUdcfoFRPvLT", PG_LONG, ("PGHOST", "PGHOSTADDR"), ("--service",)),
    "pg_dump": DbClient(*PG, "hpUdfFjnNtTZES", PG_LONG, ("PGHOST", "PGHOSTADDR"), ("--service",)),
    "pg_dumpall": DbClient(*PG, "hpUflE", PG_LONG, ("PGHOST", "PGHOSTADDR"), ("--service",)),
    "mysql": DbClient(*MYSQL, "hPuDeS", MYSQL_LONG, ("MYSQL_HOST",), MYSQL_HIDDEN, "p"),
    "mariadb": DbClient(*MYSQL, "hPuDeS", MYSQL_LONG, ("MYSQL_HOST",), MYSQL_HIDDEN, "p"),
    "mysqldump": DbClient(*MYSQL, "hPurTS", MYSQL_LONG, ("MYSQL_HOST",), MYSQL_HIDDEN, "p"),
    "mariadb-dump": DbClient(*MYSQL, "hPurTS", MYSQL_LONG, ("MYSQL_HOST",), MYSQL_HIDDEN, "p"),
    "mongo": DbClient(*MONGO_SHELL, "upf", MONGO_LONG, ()),
    "mongosh": DbClient(*MONGO_SHELL, "upf", MONGO_LONG, ()),
    "mongodump": DbClient(*MONGO_TOOL, "hdcuopjvq", MONGO_LONG, ()),
    "mongoexport": DbClient(*MONGO_TOOL, "hdcuopjvqf", MONGO_LONG, ()),
}
URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
CONNINFO_HOST_RE = re.compile(r"(?:^|\s)(?:host|hostaddr)\s*=\s*('(?:[^'\\]|\\.)*'|\S+)")
CONNINFO_SERVICE_RE = re.compile(r"(?:^|\s)service\s*=")


def _host_only(value: str) -> str:
    value = unquote(value.strip().strip("'\""))
    if "/" in value and not value.startswith("/"):  # mongo replica set "rs0/host:port"
        value = value.split("/", 1)[1]
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value[1:]
    return value.split(":")[0] if value.count(":") == 1 else value


def split_hosts(value: str) -> list[str]:
    return [_host_only(part) for part in value.split(",") if part.strip()]


def _text_hosts(text: str, spec: DbClient) -> tuple[list[str], bool]:
    """(hosts, host unknown) in a URI, connection string or mongo-style `host:port/db` word."""
    if URI_RE.match(text):
        match = re.match(r"^[^:]+://([^/?#]*)(.*)$", text)
        authority, rest = (match.group(1), match.group(2)) if match else ("", "")
        hosts = split_hosts(authority.rpartition("@")[2])
        hosts += [_host_only(v) for v in re.findall(r"[?&](?:host|hostaddr|socket)=([^&#]+)", rest)]
        return hosts or [""], False
    if spec.style == "pg" and re.search(r"(?:^|\s)\w+\s*=", text):
        hosts = [_host_only(h) for value in CONNINFO_HOST_RE.findall(text) for h in value.split(",")]
        return hosts, bool(CONNINFO_SERVICE_RE.search(text))
    if spec.style == "mongo" and not text.endswith((".js", ".mongodb")):
        head = text.split("/", 1)[0]
        if "/" in text or "." in head or ":" in head:
            return split_hosts(head), False
    return [], False


def db_state(inv: sp.Invocation, ctx: Context) -> str | None:
    """'remote', 'unknown' or None (local, or not a database client). Local means no host, a local host or a socket."""
    spec = DB_CLIENTS.get(inv.name.lower())
    if spec is None:
        return None
    opts = sp.parse_options(inv.args, spec.short_arg, spec.long_arg, spec.attached_arg)
    hosts: list[str] = []
    unknown = any(flag in opts.flags for flag in spec.hidden)
    for key, word in opts.pairs:
        if key in spec.host_flags or key in spec.uri_flags:
            if word.dynamic:
                unknown = True
            elif key in spec.host_flags:
                hosts += split_hosts(word.text)
            else:
                found, hidden = _text_hosts(word.text, spec)
                hosts += found
                unknown |= hidden
    if spec.positional_uri:
        for word in opts.positionals:
            if word.dynamic:
                unknown = True
            else:
                found, hidden = _text_hosts(word.text, spec)
                hosts += found
                unknown |= hidden
    if not hosts and not unknown:  # no host on the command line: the environment may name one
        for var in spec.env_host:
            value = inv.env.get(var) or ctx.environ.get(var) or ""
            hosts += split_hosts(value)
        if spec.style == "pg" and (inv.env.get("PGSERVICE") or ctx.environ.get("PGSERVICE")):
            unknown = True
    if any(not ctx.is_local(host) for host in hosts):
        return "remote"
    return "unknown" if unknown else None


def check_db_remote(inv: sp.Invocation, ctx: Context) -> bool:
    return db_state(inv, ctx) == "remote"


def check_db_host_unknown(inv: sp.Invocation, ctx: Context) -> bool:
    return db_state(inv, ctx) == "unknown"


# --- aws s3 ----------------------------------------------------------------------------------------

AWS_LONG = (
    "--profile",
    "--region",
    "--endpoint-url",
    "--output",
    "--query",
    "--ca-bundle",
    "--cli-read-timeout",
    "--cli-connect-timeout",
    "--color",
    "--exclude",
    "--include",
    "--acl",
    "--storage-class",
    "--sse",
    "--sse-kms-key-id",
    "--cache-control",
    "--content-type",
    "--content-encoding",
    "--content-disposition",
    "--expires",
    "--metadata",
    "--metadata-directive",
    "--grants",
    "--website-redirect",
    "--source-region",
    "--request-payer",
    "--expected-size",
    "--page-size",
)


def check_s3_download(inv: sp.Invocation, ctx: Context) -> bool:
    """aws s3 cp|sync|mv where a source (any operand but the last) is s3://. An unknown source counts too."""
    if inv.name.lower() != "aws":
        return False
    words = sp.parse_options(inv.args, "", AWS_LONG).positionals
    texts = [w.text for w in words]
    if "s3" not in texts:
        return False
    at = texts.index("s3")
    if len(texts) < at + 2 or texts[at + 1] not in ("cp", "sync", "mv"):
        return False
    operands = words[at + 2 :]
    if any(w.text.startswith("s3://") for w in operands[:-1]):
        return True
    return bool(operands) and operands[0].dynamic and not operands[-1].text.startswith("s3://")


CHECKS = {
    "rm_recursive": check_rm_recursive,
    "find_delete": check_find_delete,
    "git_hooks_path": check_git_hooks_path,
    "sql_destructive": check_sql_destructive,
    "chmod_world_writable": check_chmod_world_writable,
    "db_remote": check_db_remote,
    "db_host_unknown": check_db_host_unknown,
    "s3_download": check_s3_download,
}


# --- rules -----------------------------------------------------------------------------------------


def match_data_rule(rule: dict, inv: sp.Invocation) -> bool:
    """A rule written as data (customize.toml or the project file): command, subcommand, words, flags."""
    if inv.name.lower() != rule["command"].lower():
        return False
    if inv.name.lower() == "git":
        _, sub, rest = git_split(inv.args)
        if rule.get("subcommand") and sub not in rule["subcommand"]:
            return False
        opts = sp.parse_options(rest, *GIT_ARGS.get(sub, ("", ())))
    else:
        opts = sp.parse_options(inv.args)
    words = iter(p.text for p in opts.positionals)
    if not all(any(wanted == text for text in words) for wanted in rule.get("words", [])):
        return False
    triggers = []
    if "flag_sets" in rule:
        triggers.append(any(all(sp.has_flag(opts, alt) for alt in flags) for flags in rule["flag_sets"]))
    if "arg_prefixes" in rule:
        triggers.append(any(p.text.startswith(pre) for p in opts.positionals for pre in rule["arg_prefixes"]))
    if triggers and not any(triggers):
        return False
    return not any(sp.has_flag(opts, flag) for flag in rule.get("unless_flags", []))


def rule_matches(rule: dict, inv: sp.Invocation, ctx: Context) -> bool:
    if "check" in rule:
        check = CHECKS.get(rule["check"])
        if check is None:
            raise gc.ConfigError("a built-in rule names a check that does not exist")
        return check(inv, ctx)
    return match_data_rule(rule, inv)


def make_context(payload: dict, root: Path, rules: gc.Rules) -> Context:
    cwd = payload.get("cwd")
    start = Path(cwd) if isinstance(cwd, str) and cwd and Path(cwd).is_absolute() else root
    return Context(
        Path(os.path.realpath(root)),
        Path(os.path.realpath(start)),
        rules,
        Path(os.path.realpath(Path.home())),
        os.environ,
    )


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    command = payload.get("tool_input", {}).get("command")
    if not isinstance(command, str):
        return [hc.Verdict("CMD_UNPARSABLE", "The Bash call has no command text, so it cannot be checked.", GUARD)]
    try:
        rules = gc.load(root)
    except gc.ExtensionError as err:
        return [hc.Verdict("EXT_BAD_FILE", f"{err}. Fix the file by hand; Bash is blocked until then.", GUARD)]
    try:
        invocations = list(sp.walk(command))
    except sp.ParseError as err:
        return [
            hc.Verdict(
                "CMD_UNPARSABLE",
                f"The command cannot be read safely ({err}). Write it in a simpler way.",
                GUARD,
            )
        ]
    ctx = make_context(payload, root, rules)
    found: dict[str, hc.Verdict] = {}
    for inv in invocations:
        for rule in rules.commands:
            if rule["code"] not in found and rule_matches(rule, inv, ctx):
                found[rule["code"]] = hc.Verdict(rule["code"], rule["reason"], GUARD)
    return list(found.values())


GUARDS = [hc.Guard(GUARD, ("Bash",), check)]
