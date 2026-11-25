---
name: guard-rules
description: The command and path rules that tool-guard enforces, how a project adds rules, and what the guard cannot see.
---

# Guard rules

`pre_tool_use.py` runs two guards before Claude Code runs a tool call. A block exits with code 2 and one message that lists every reason code. Do not retry the same call: change the approach, or ask the developer.

| Guard | Tools | Rules |
|---|---|---|
| `command-guard` | Bash | destructive commands, data export, commands it cannot read |
| `path-guard` | Read, Edit, Write, NotebookEdit, Grep, Glob, and the paths inside Bash | sensitive files and folders |

Each rule has a stable id. The id is the reason code in the message and in the event. Events carry the rule id, the guard name, the tool name and the session id. They never carry the command text, a file path or file content.

## Command rules

The list is in `customize.toml` (`[[agent.command_rules]]`). A project adds rules; it cannot remove or change a built-in one.

| Rule id | Blocks | Close-but-safe |
|---|---|---|
| `CMD_RM_RECURSIVE` | `rm -r` / `-rf` / `-fr` / `-r -f` / `--recursive` on anything that is not provably inside a scratch folder; `--no-preserve-root`; `xargs rm -rf` with unknown targets; a target with a variable or `$(...)` | `rm file.txt`, `rm -rf tmp/x`, `rm -rf node_modules`, `rm -rf /tmp/build-1` |
| `CMD_FIND_DELETE` | `find ... -delete` outside the scratch folders | `find tmp -name '*.o' -delete` |
| `CMD_GIT_FORCE_PUSH` | `git push --force`, `-f`, `--force-with-lease`, `--force-if-includes`, a `+refspec` | `git push`, `git push -u origin x`, `git push -n` |
| `CMD_GIT_RESET_HARD` | `git reset --hard` | `git reset --soft`, `git reset HEAD file` |
| `CMD_GIT_CLEAN` | `git clean -f` (also `-fd`, `-fdx`) | `git clean -n` (dry run) |
| `CMD_GIT_BRANCH_DELETE` | `git branch -D`, `-d -f`, `--delete --force` | `git branch -d old` |
| `CMD_GIT_NO_VERIFY` | `--no-verify` on `git commit`, `push`, `merge`, `rebase`, `am` | `git commit -m x` |
| `CMD_GIT_COMMIT_SKIP_HOOKS` | `git commit -n` (short for `--no-verify`) | `git push -n` (dry run), `git log -n 5` |
| `CMD_GIT_HOOKS_PATH` | any change of `core.hooksPath`: `git config`, `git -c core.hooksPath=...`, `GIT_CONFIG_*` | `git config core.hooksPath` (read), `git config --get core.hooksPath` |
| `CMD_SQL_DESTRUCTIVE` | `DROP`, `TRUNCATE`, `DELETE` without `WHERE` (or `WHERE 1=1`) in `psql`, `mysql`, `mariadb`, `sqlite3`, `sqlcmd`, `usql`, `pgcli`, `mycli`, `litecli`: in an argument, a heredoc, a pipe, or a script file read with `-f` or `<` | `DELETE FROM t WHERE id = 7`, a string that only contains the word `drop` |
| `CMD_CHMOD_WORLD_WRITABLE` | `chmod -R` with a mode writable by others: `777`, `666`, `a+rwx`, `o+w` | `chmod -R 755`, `chmod 777 one-file` (not recursive) |
| `EXPORT_DB_REMOTE` | `psql`, `mysql`, `mariadb`, `mongo`, `mongosh`, `mongodump`, `mongoexport`, `pg_dump`, `pg_dumpall`, `mysqldump` against a host that is not local, from `-h`, `--host`, a URI, a connection string, or `PGHOST` / `MYSQL_HOST` | `psql -h localhost`, `-h 127.0.0.1`, `-h ::1`, a socket path, no host at all |
| `EXPORT_DB_HOST_UNKNOWN` | the same clients when the host cannot be known: a variable (`psql "$DATABASE_URL"`), `--service`, `PGSERVICE`, `--defaults-file`, `--login-path` | name the host in the command: `psql -h localhost` |
| `EXPORT_S3_DOWNLOAD` | `aws s3 cp`, `sync`, `mv` with `s3://` as the source (any operand but the last) | upload (`aws s3 cp f s3://b/k`), `aws s3 ls` |
| `CMD_UNPARSABLE` | a command that cannot be read: unclosed quote, nested too deep, no command text | write it in a simpler way |

Local hosts are `localhost`, `127.0.0.1`, `::1`, any `127.x.x.x`, and a path to a unix socket.

### What the command guard understands

`&&`, `||`, `;`, pipes, newlines, `( )` and `{ }`, `if` / `while` bodies, quotes and escapes (`'r'm`, `r\m`, `$'\x72m'`), absolute programs (`/bin/rm`), `\rm`, `$(...)` and backticks, `<(...)`, here-documents and here-strings, `sh -c` / `bash -lc` / `su -c` / `eval`, `xargs`, `find -exec`, and the wrappers `sudo`, `doas`, `env VAR=x`, `nohup`, `nice`, `ionice`, `timeout`, `time`, `command`, `exec`, `stdbuf`, `watch`, `busybox`. Flags may be combined (`-rf`, `-fr`) or split (`-r -f`, `--recursive --force`). Long git options may be shortened (`--no-verif`).

## Design choices

- **Force push.** `--force-with-lease` and `--force-if-includes` are blocked too: they are still a force push, and the agent does not push over shared history. A `+refspec` counts as a force. A normal `git push` is allowed.
- **Scratch folders.** `rm -r` is allowed only when every target is inside a scratch folder. Built-in: `tmp`, `.tmp`, `temp`, `build`, `dist`, `coverage`, `.cache`, `.pytest_cache`, `.bmad-guard/tmp` (from the project root), `**/node_modules` and `**/__pycache__` (at any depth), `/tmp`, `/var/tmp`, `/private/tmp`, and the system temp folder. A relative target is resolved from the working directory of the call, with `..` and symlinks resolved. The folder itself counts (`rm -rf tmp`) for a project folder; an absolute folder counts only for what is inside it (`rm -rf /tmp` is blocked). A glob is fine when the folder is written out (`tmp/*`), not otherwise (`tmp*`, `*/x`). A variable or `$(...)` in a target is never provable, so it is blocked.
- **Local dev data.** A destructive SQL statement is blocked on a local database too. The developer runs it.
- **Sensitive paths.** One list, `SENSITIVE_PATHS` in `hook_wiring.py`. It generates the `permissions.deny` rules and is matched by the hook, so the two cannot drift (a test checks it).

## Path rules

| Rule id | Matches | Close-but-safe |
|---|---|---|
| `PATH_ENV_FILE` | `.env`, `.env.*` (for example `.env.local`, `.env.production`), `*.env` | `.env.example` is readable |
| `PATH_PRIVATE_KEY` | `*.pem`, `*.key`, and SSH keys by name: `id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*` (also `id_rsa.pub`, `id_rsa_work`) | `id_test.py`, `id_utils.py`, `identity.md` |
| `PATH_KEY_STORE` | `*.p12` | `cert.pdf` |
| `PATH_DB_DUMP` | `*dump*.sql` | `schema.sql`, `migrations/001.sql` |
| `PATH_DB_BACKUP` | `*.sql.bak`, `*.db.bak`, `*.sqlite.bak`, `*.sqlite3.bak`, `*dump*.bak`, `*database*.bak` | `config.py.bak`, `notes.bak` |
| `PATH_AWS_DIR` | `~/.aws` and everything in it | `.aws/` inside the project |
| `PATH_SSH_DIR` | `~/.ssh` and everything in it | `~/.config`, `.ssh/` inside the project |
| `PATH_GUARD_CONFIG` | `~/.config/bmad-guard` and everything in it | `~/.config/other-tool` |

Names match at any depth, ignoring case. A path is also checked after `~`, `$HOME` and `${HOME}` are expanded, with `..` resolved, and after a symlink is followed. A name with a wildcard (`**/.env*`, `*.pem`) is blocked when it can only mean a sensitive file; a broad pattern such as `*` or `**/*.sql` is not.

- **Read, Edit, Write, NotebookEdit:** `file_path` or `notebook_path`. These match the Read deny rules, which also block Edit and Write.
- **Grep:** `path` and `glob`. The search text (`pattern`) is text, not a path. Searching a whole folder is allowed (see the limits below); the deny rules apply to Grep best effort.
- **Glob:** `pattern` and `path`.
- **Bash:** every word and redirect target of every command, also inside `sh -c`, `$(...)`, pipes and `sudo`. The text of `echo` and `printf`, the search pattern of `grep` / `rg`, and git messages and search strings (`-m`, `-S`, `-G`, `--grep`, `git grep`) are not paths.

## Project extensions

Create `_bmad/custom/project/guard/tool-rules.json`. It can only add.

```json
{
  "command_rules": [
    {"id": "no-terraform-destroy", "command": "terraform", "words": ["destroy"],
     "reason": "Destroy is run by the platform team."},
    {"id": "no-stash-drop", "command": "git", "subcommand": "stash", "words": ["drop"]}
  ],
  "sensitive_paths": [
    {"id": "payroll", "pattern": "payroll*.csv", "reason": "Payroll data."},
    {"id": "private-data", "pattern": "data/private/**"}
  ],
  "scratch_folders": ["_work/cache", "**/generated"]
}
```

- **Command rule:** `id` (lower case letters, digits, `.`, `_`, `-`), `command` (a program name, no path), and any of `words` (positional words, in order), `subcommand` (git only), `flag_sets` (match when one inner list is fully present, `"-f|--force"` means either), `arg_prefixes`, `unless_flags`, and `reason` (one line). A rule with only `command` blocks every use of that program. The reason code is `PROJECT_CMD_<ID>`.
- **Sensitive path:** a file name pattern (`payroll*.csv`, matched at any depth) or a folder (`data/private/**`, from the project root; `~/dir/**` for the home folder). The reason code is `PROJECT_PATH_<ID>`.
- **Scratch folder:** a project folder (`tmp`, `a/b`, or `**/name` for any depth) or an absolute folder with at least two parts (`/var/cache/build`). Not `.`, `..`, `/` or `**`.

What a project cannot do: remove or weaken a built-in rule, add a local host, or carve a name out of a sensitive path. The guard reads the built-in list from this skill's `customize.toml` only; `_bmad/custom/tool-guard.toml` and `tool-guard.user.toml` are not read for guard rules.

**A bad extension file blocks.** Invalid JSON, an unknown key, a bad id, a rule that is too broad: the guards block every Bash call and every path tool call with `EXT_BAD_FILE` and name the problem (never repeating file content). The developer fixes the file by hand. The agent must not edit it to get past a block: a scratch folder added by the agent widens what `rm -r` may delete.

## Known limits

The guard reads the command text. It cannot see what the command text does when it runs. Do not rely on it as a sandbox.

- **Built at run time:** `$CMD -rf x`, `eval "$var"`, `bash -c "$(...)"`, `base64 -d | sh`, aliases and functions defined elsewhere, a script file (`bash script.sh`, `make clean`), `python -c`, `node -e`, `perl -e`, `rsync --delete`, `dd`, `mv x /dev/null`.
- **Remote and container commands:** `ssh host pg_dump`, `kubectl exec -- pg_dump`, `docker exec db psql`. The text after the host is not read.
- **A tunnel to a remote database** that listens on `localhost` looks local.
- **Globs:** `cat .e*` is not caught; `cat .env*` is. A glob in a folder part (`*/node_modules`) is treated as not provable for `rm`.
- **Search inside folders:** Grep and `grep -r` over a folder can read a sensitive file inside it. Name-based rules cannot see that.
- **Other tools and MCP servers** are not routed to these guards.
- **Writes that change the guard's own inputs:** an edit of `.git/config` or `.git/hooks` is not a `core.hooksPath` change by name. Editing the guard scripts or `.claude/settings.json` is the job of the document guard and the write gate (T3, T4), and of git review.
- **`aws s3api get-object`, `gsutil cp`, `az storage blob download`, `scp` / `rsync` from a remote host** are not export rules yet.
- **`UPDATE` without `WHERE`** is not in the SQL rule.
- **The matching is by name.** `id_rsa_utils.py` looks like an SSH key to the path guard.

The real limit is an OS sandbox, which is not in scope for Phase 2. The `permissions.deny` rules are the second layer for file tools.

## When a block looks wrong

Tell the developer the rule id. Do not look for another command that does the same thing. The developer can run the command by hand, or add a scratch folder or a rule in `tool-rules.json`.
