# /// script
# requires-python = ">=3.10"
# ///
"""Bash writes to the protected document files (Phase 2, T3). Best effort.

The protected places are the ones in document_guard.py, with the same reason codes:
  DOC_APPROVAL_STATE (approval-state.json), DOC_GUARD_RECORD (_bmad-output/guard/**),
  DOC_GUARD_CONFIG (_bmad/custom/project/guard/**), DOC_GUARD_SCRIPTS (the guard files, see document_guard.py).
Reading them stays allowed (`cat`, `grep`, `git diff`, `python3 script.py --state-file ...`).

A command writes to a place when it names it as the target of:
  a redirect            `>`, `>>`, `>|`, `&>`, `<>`, `>& file`; also from `cat <<EOF > file`
  tee, dd of=, curl -o, wget -O, patch (file or -o)
  cp, install, ln, rsync, scp   the destination (last operand, or -t DIR); a folder destination counts when a
                        source would land inside it
  mv                    the destination, and every source (the file is moved away)
  sed -i, perl -i       the files
  rm, unlink, rmdir, shred, truncate, git rm   the files; with `rm -r` / `git rm -r` also a folder that holds a
                        protected place (`rm -r _bmad`)
  git mv                destination and sources, like mv
  find -delete, -exec   a start folder that is or is inside a protected place (`find . -delete` is the command guard's)
  git checkout, git restore    a path that is, or is inside, a protected place (it rewrites the file)

Not caught (see references/guard-rules.md): `python3 -c` or a script that opens the file, `node -e`, `awk -i inplace`,
`ed`, an editor, `tar -x`, a path built from variables. A `cd` into a protected folder is followed: the targets of the
same command are also tried from there. A target with a variable or `$(...)` is blocked when it spells the name of a
protected place.

Warden and Sentry are scripts the agent starts with `python3 <skill>/scripts/<name>.py --state-file ...`. The state
file there is an argument of python3, not a write target, so those calls pass.

A command that cannot be read is reported by command_guard, not here.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import document_guard as dg  # noqa: E402 - sibling modules, found through the line above
import hook_common as hc  # noqa: E402
import shell_parse as sp  # noqa: E402

GUARD = "bash-write-guard"
WRITE_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>", ">&"}
FRAGMENTS = (  # for a target with a variable in it: the part of the path that gives it away
    (dg.STATE_FILE_NAME, "DOC_APPROVAL_STATE"),
    ("_bmad-output/guard", "DOC_GUARD_RECORD"),
    ("_bmad/custom/project/guard", "DOC_GUARD_CONFIG"),
    (dg.GUARD_DIR_REL, "DOC_GUARD_SCRIPTS"),
    (".claude/settings", "DOC_GUARD_SCRIPTS"),
    *((f"/{skill}/", "DOC_GUARD_SCRIPTS") for skill in dg.GUARD_SKILLS),
    *((f"_bmad/custom/{skill}.toml", "DOC_GUARD_SCRIPTS") for skill in dg.GUARD_SKILLS),
)
COPY_COMMANDS = {"cp", "install", "ln", "rsync", "scp", "mv"}
REMOVE_COMMANDS = {"rm", "unlink", "rmdir", "shred", "truncate"}
FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
FD_RE = re.compile(r"^(\d+|-)$")
# Short letters and long options that take a value, per command, so the value is not read as an operand.
VALUE_OPTIONS = {
    "cp": ("tS", ("--target-directory", "--suffix")),
    "mv": ("tS", ("--target-directory", "--suffix")),
    "ln": ("tS", ("--target-directory", "--suffix")),
    "install": ("tSmog", ("--target-directory", "--suffix", "--mode", "--owner", "--group")),
    "rsync": ("etTBfM", ("--rsh", "--temp-dir", "--exclude", "--include", "--filter", "--files-from")),
    "scp": ("PFiloJS", ()),
    "truncate": ("sr", ("--size", "--reference")),
    "sed": ("efl", ("--expression", "--file", "--line-length")),
    "perl": ("eEI", ()),
    "curl": ("oHdXuAeCxwTKmbcz", ("--output", "--header", "--data", "--request", "--user-agent")),
    "wget": ("OoPUiB", ("--output-document", "--output-file", "--directory-prefix")),
    "patch": ("opdFiNr", ("--output", "--directory", "--input")),
}


def _word(text: str, like: sp.Word) -> sp.Word:
    return sp.Word(text, like.dynamic, like.quoted, like.glob)


def _opts(inv: sp.Invocation) -> sp.Opts:
    short, long = VALUE_OPTIONS.get(inv.name.lower(), ("", ()))
    return sp.parse_options(inv.args, short, long, attached_arg="i" if inv.name.lower() in ("sed", "perl") else "")


def _is_folder(dest: sp.Word, cwd: str) -> bool:
    """True when a copy destination is a folder: it ends with a slash, or it is a folder that exists now."""
    text = dest.text
    return text.endswith("/") or text in (".", "..") or (not dest.dynamic and os.path.isdir(os.path.join(cwd, text)))


def _copy_targets(inv: sp.Invocation, cwd: str) -> list[tuple[sp.Word, str]]:
    """Where cp, mv, ln, install, rsync and scp put files: the destination, and each source's name inside it when the
    destination is a folder."""
    opts = _opts(inv)
    operands = opts.positionals
    directory = opts.values.get("-t") or opts.values.get("--target-directory")
    if directory is not None:
        dest, sources = sp.Word(directory), operands
    elif len(operands) >= 2:
        dest, sources = operands[-1], operands[:-1]
    elif inv.name.lower() == "ln" and operands:  # `ln -s target` makes ./<name of target>
        return [(_word(os.path.basename(operands[0].text.rstrip("/")), operands[0]), "write")]
    else:
        return []
    found = [(dest, "write")]
    if directory is not None or len(sources) > 1 or _is_folder(dest, cwd):
        for source in sources:
            name = os.path.basename(source.text.rstrip("/"))
            if name:
                found.append((_word(os.path.join(dest.text, name), dest), "write"))
    if inv.name.lower() == "mv":  # the sources are moved away
        found += [(source, "remove") for source in sources]
    return found


def _find_targets(inv: sp.Invocation) -> list[tuple[sp.Word, str]]:
    roots: list[sp.Word] = []
    for word in inv.args:
        if word.text in ("-H", "-L", "-P") and not roots:
            continue
        if word.text.startswith(("-", "(", "!")):
            break
        roots.append(word)
    texts = {w.text for w in inv.args}
    # Only a start folder that is or is inside a protected place: `find . -name x -delete` is the command guard's.
    return [(w, "write") for w in roots] if texts & (FIND_ACTIONS | {"-delete"}) else []


def targets(inv: sp.Invocation, cwd: str) -> list[tuple[sp.Word, str]]:
    """(word, mode) for every path this invocation may write. mode 'write': the path itself or what is inside it.
    mode 'remove': also a folder that holds a protected place, because deleting or moving it takes the place along
    (`rm -r`, `mv`, `git rm -r`)."""
    name = inv.name.lower()
    found: list[tuple[sp.Word, str]] = [
        (word, "write")
        for op, word in inv.redirects
        if op in WRITE_REDIRECTS and not (op == ">&" and FD_RE.match(word.text))
    ]
    if name in COPY_COMMANDS:
        found += _copy_targets(inv, cwd)
    elif name in REMOVE_COMMANDS:
        opts = _opts(inv)
        mode = "remove" if name == "rm" and sp.has_flag(opts, "-r|-R|--recursive") else "write"
        found += [(w, mode) for w in opts.positionals]
    elif name == "tee":
        found += [(w, "write") for w in _opts(inv).positionals]
    elif name in ("sed", "perl"):
        opts = _opts(inv)
        if any(flag.startswith("-i") or flag == "--in-place" for flag in opts.flags):
            found += [(w, "write") for w in opts.positionals]
    elif name == "dd":
        found += [(_word(w.text[3:], w), "write") for w in inv.args if w.text.startswith("of=")]
    elif name == "curl":
        opts = _opts(inv)
        found += [(sp.Word(v), "write") for k, v in opts.values.items() if k in ("-o", "--output")]
    elif name == "wget":
        opts = _opts(inv)
        found += [(sp.Word(v), "write") for k, v in opts.values.items() if k in ("-O", "--output-document")]
    elif name == "patch":
        opts = _opts(inv)
        found += [(sp.Word(v), "write") for k, v in opts.values.items() if k in ("-o", "--output")]
        found += [(w, "write") for w in opts.positionals]
    elif name == "find":
        found += _find_targets(inv)
    elif name == "git":
        _, sub, rest = sp.git_split(inv.args)
        if sub in ("checkout", "restore"):
            found += [(w, "write") for w in sp.parse_options(rest, "bBs", ("--source",)).positionals]
        elif sub in ("rm", "mv"):
            opts = sp.parse_options(rest)
            mode = "remove" if sub == "mv" or sp.has_flag(opts, "-r") else "write"
            found += [(w, mode) for w in opts.positionals]
    return found


def cd_folders(invocations: list[sp.Invocation], zones: dg.Zones) -> list[str]:
    """The working directory and every folder a `cd` or `pushd` in the command moves to. Over-cautious: no scoping."""
    folders = [zones.cwd]
    for inv in invocations:
        if inv.name in ("cd", "pushd"):
            word = next((a for a in inv.args if not a.text.startswith("-")), None)
            if word is not None and not word.dynamic and "$" not in word.text:
                folders.append(os.path.realpath(zones.expand(word.text, folders[-1])))
    return list(dict.fromkeys(folders))


def target_codes(
    word: sp.Word, mode: str, zones: dg.Zones, folders: list[str], files: list[str] | None = None
) -> list[str]:
    text = word.text
    if word.dynamic or "$" in text:  # the value is not known: look for the name of a protected place
        lowered = text.lower()
        return [code for fragment, code in FRAGMENTS if fragment in lowered]
    codes: list[str] = []
    first = min((text.index(c) for c in "*?[" if c in text), default=-1)
    if first >= 0:  # a glob: its file name may match the state file; its folder must be written out
        folder, _, pattern = text[:first].rpartition("/")
        if "/" in text[first:]:
            return []
        text, mode = folder + "/" if folder else ".", "write"  # the matches are files, not the folder
        if fnmatch.fnmatchcase(dg.STATE_FILE_NAME, (pattern + word.text[first:]).lower()) and any(
            zones.in_bmad(text, base) for base in folders
        ):
            codes.append("DOC_APPROVAL_STATE")
    for base in folders:
        for code, rel in zones.hit_zones(text, base, ancestors=mode == "remove"):
            codes.append(code)
            if files is not None and code == "DOC_GUARD_SCRIPTS":
                files.append(rel)
    return codes


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    command = payload["tool_input"].get("command")
    if not isinstance(command, str):
        return []  # command_guard reports a call with no command text
    try:
        invocations = list(sp.walk(command))
    except sp.ParseError:
        return []  # command_guard blocks a command it cannot read
    cwd = payload.get("cwd")
    start = Path(cwd) if isinstance(cwd, str) and cwd and Path(cwd).is_absolute() else root
    zones = dg.Zones(root, start)
    folders = cd_folders(invocations, zones)
    codes: list[str] = []
    files: list[str] = []
    for inv in invocations:
        for word, mode in targets(inv, zones.cwd):
            codes += target_codes(word, mode, zones, folders, files)
    return dg.reasons(codes, files, GUARD)


GUARDS = [hc.Guard(GUARD, ("Bash",), check)]
