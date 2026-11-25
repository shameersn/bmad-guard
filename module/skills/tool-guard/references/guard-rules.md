---
name: guard-rules
description: The command, path, document and write gate rules that tool-guard enforces, how a project adds rules, and what the guard cannot see.
---

# Guard rules

`pre_tool_use.py` runs five guards before Claude Code runs a tool call. A block exits with code 2 and one message that lists every reason code. Do not retry the same call: change the approach, or ask the developer.

| Guard | Tools | Rules |
|---|---|---|
| `command-guard` | Bash | destructive commands, data export, commands it cannot read |
| `path-guard` | Read, Edit, MultiEdit, Write, NotebookEdit, Grep, Glob, and the paths inside Bash | sensitive files and folders |
| `document-guard` | Write, Edit, MultiEdit, NotebookEdit | approval state, guard records, guard config, approval fields, removed questions (see "Document rules") |
| `bash-write-guard` | Bash | the same protected files, written from the shell |
| `write-gate` | Write, Edit, MultiEdit, NotebookEdit, and the write targets of Bash | source writes without an approved plan and a ready story (see "Write gate") |

Each rule has a stable id. The id is the reason code in the message and in the event. Events carry the rule id, the guard name, the tool name and the session id. They never carry the command text, a file path or file content.

## Command rules

The list is in `customize.toml` (`[[agent.command_rules]]`). A project adds rules; it cannot remove or change a built-in one.

| Rule id | Blocks | Close-but-safe |
|---|---|---|
| `CMD_RM_RECURSIVE` | `rm -r` / `-rf` / `-fr` / `-r -f` / `--recursive` on anything that is not provably inside a scratch folder; `--no-preserve-root`; `xargs rm -rf` with unknown targets; a target with a variable or `$(...)` | `rm file.txt`, `rm -rf tmp/x`, `rm -rf node_modules`, `rm -rf /tmp/build-1` |
| `CMD_FIND_DELETE` | `find ... -delete` outside the scratch folders | `find tmp -name '*.o' -delete` |
| `CMD_GIT_FORCE_PUSH` | `git push --force`, `-f`, `--force-with-lease`, `--force-if-includes`, a `+refspec` | `git push`, `git push -u origin x`, `git push -n` |
| `CMD_GIT_PUSH_DELETE` | `git push --delete`, `-d`, a `:refspec`, `--mirror`, `--prune` | `git push origin a:a`, `git fetch --prune`, `git branch -d x` |
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
- **Push that deletes.** `git push --delete` / `-d`, a `:refspec`, `--mirror` and `--prune` delete remote branches or tags. Blocked; the developer runs them.
- **No exception for cleanup.** `find -delete` and `rm -r` stay blocked outside the scratch folders, also for `*.pyc` cleanup. A project adds a scratch folder instead (decision, T2 review).
- **No block on `eval` / `sh -c` with text built at run time.** Too many false blocks (`eval "$(ssh-agent -s)"`). Left to a sandbox (decision, T2 review).
- **No extra local database hosts.** A project cannot add a host to `local_hosts`, so a shared dev database is blocked. Use a local tunnel or run it by hand (decision, T2 review).
- **File-writing tools are checked too.** Write, Edit and NotebookEdit go through the path guard, like the `permissions.deny` rules (decision, T2 review).
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

- **Read, Edit, MultiEdit, Write, NotebookEdit:** `file_path` or `notebook_path`. These match the Read deny rules, which also block Edit and Write.
- **Grep:** `path` and `glob`. The search text (`pattern`) is text, not a path. Searching a whole folder is allowed (see the limits below); the deny rules apply to Grep best effort.
- **Glob:** `pattern` and `path`.
- **Bash:** every word and redirect target of every command, also inside `sh -c`, `$(...)`, pipes and `sudo`. The text of `echo` and `printf`, the search pattern of `grep` / `rg`, and git messages and search strings (`-m`, `-S`, `-G`, `--grep`, `git grep`) are not paths.

## Document rules

Written for the document guard (file tools) and the Bash write guard (shell). The reason codes are the same for both.

| Rule id | Blocks | Close-but-safe |
|---|---|---|
| `DOC_APPROVAL_STATE` | any write to a file named `approval-state.json` (any folder, any spelling of the case), also through a link or `..` | reading it; `python3 .../grant_approval.py --state-file ...` (Warden's own script) |
| `DOC_GUARD_RECORD` | any write to `_bmad-output/guard/**` (review and build records) | `_bmad-output/planning-artifacts/**`, `_bmad-output/guard.md` |
| `DOC_GUARD_CONFIG` | any write to `_bmad/custom/project/guard/**` (`tool-rules.json`, `tech-stack.toml`) | `_bmad/custom/project/bmad-build/**`, `_bmad/custom/tool-guard.toml` |
| `DOC_GUARD_SCRIPTS` | any write to the guard itself: `_bmad/custom/guard/**` (git hooks, manifest), `.claude/settings.json`, `.claude/settings.local.json`, the guard skills (`tool-guard`, `prompt-guard`, `ready-for-dev-gate`, `agent-approval-grant`, `agent-approval-gate-check`) in `.claude/skills` and `.agents/skills`, and every file listed in the setup manifest | reading or running them; other skills (`.claude/skills/my-skill/**`); `_bmad/custom/*.toml` not in the manifest |
| `DOC_APPROVAL_FIELD` | in a Markdown file: adding, removing or changing `status: approved` or `approval_status: approved` in the frontmatter, any `approved_by` or `approved_at` line there, any `Resolved:` line, any `[ASSUMPTION-RESOLVED ...]` tag, and adding, removing or changing `gate: generic` in the frontmatter (it skips the ready-for-dev gate) | other values (`status: ready-for-dev`, `approval_status: pending-approval`, `gate: full`), any edit elsewhere in an approved document |
| `DOC_QUESTION_REMOVED` | in a Markdown file: an open question (`Q-n` under `## Open questions`) or an open `[ASSUMPTION ...]` that is gone from the new text, with no record for it there | rewording an item that stays open, moving it, adding items |

Each message says the human path: ask the developer to approve or reopen with Warden, to write the answer, or to change the guard settings.

### Design choices

- **Which files.** The two Markdown rules apply to every `.md` and `.markdown` file in the project, not to a list of folders. BMAD's output folders can be renamed, and the lines checked are narrow, so a silent gap is the bigger risk than a false block. Other file types (`.json`, `.txt`, notebooks) are not documents; `NotebookEdit` is checked for the three protected places only.
- **Only the frontmatter counts for the approval fields.** A line `status: approved` in the body of a document is text. A frontmatter block that never closes counts as all frontmatter, so a half-written block cannot hide a line.
- **`status` and `approval_status` are about the value `approved`.** BMAD's own phase values (`draft`, `ready-for-dev`, `in-progress`, `done`) and `pending-approval` are ordinary edits: `bmad-build` writes its plan with `approval_status: pending-approval`, and Sentry resets a stale approval through its script. Adding `approved`, removing it, or changing it to anything else blocks. Case, quotes and a trailing `# comment` do not matter. `approved_by` and `approved_at` are blocked for any change, because only Warden's record should say who and when.
- **Write compares with the disk.** The new content is compared with the file now on disk; a new file compares with nothing, so a new file that already says `status: approved` blocks. **Edit** applies `old_string` to the file on disk (all matches with `replace_all`, the first one otherwise) and compares the whole text before and after. **MultiEdit** applies its edits one after the other. Line endings are compared as `\n`.
- **When Claude Code would fail the edit, the guard allows.** The file is missing, or `old_string` is empty on a file that has text: Claude Code stops the call itself. One exception: the file exists but `old_string` is not in it as typed (Claude Code also matches curly quotes and line endings loosely). Then the guard compares the lines of `old_string` and `new_string` on their own and blocks an approval line or resolution that changed there. Question removal is not checked in that case (no headings to go by).
- **Who writes a resolution.** The plan says an AI edit must not add, change or remove a `Resolved:` line or an `[ASSUMPTION-RESOLVED ...]` tag. So an edit cannot turn an open item into a resolved one: `DOC_APPROVAL_FIELD` blocks it. Questions are answered by a person, who types the line with name and date. The question check (`DOC_QUESTION_REMOVED`) therefore finds what is left: an item that is simply gone.
- **How a removed item is found.** Items are parsed by the ready-for-dev gate's own parser (`ready_for_dev.py`, the sibling skill), so the two cannot disagree. Open ids (`Q-1`, `A-1`) of the old text must all still be in the new text, open or resolved. A renamed id is a delete and an add. Items with no id cannot be followed, so only their count is compared (a new id makes up for one item that got its id). If the parser skill is not installed, the call is blocked (`GUARD_ERROR`) when the text mentions questions, assumptions or `Resolved`; a document without those words never loads it.
- **Name-based for the state file.** `approval-state.json` is protected by its name in any folder, so a changed `state_file` path or a link does not get past the guard. A test fixture with that name needs a hand edit.
- **A `.md` file inside a protected folder** gets the folder's reason only.
- **The guard protects itself (`DOC_GUARD_SCRIPTS`, T3 review).** Without it an agent could edit `pre_tool_use.py` or remove the hook wiring and turn every rule off. The fixed list always applies; the setup manifest (`_bmad/custom/guard/manifest.json`) adds what setup installed, such as the BMAD overrides. A broken or missing manifest leaves the fixed list, and the manifest folder is protected itself. Side effect: the agent cannot edit `.claude/settings.json` for any reason (for example to add a permission). The developer does it by hand; the setup script still updates the wiring, because it runs outside Claude Code's tools.
- **Decisions from the T3 review.** Resolutions stay person-only in the pilot (look again after the false-block numbers). Every Markdown file is checked, not a folder list. No Bash check for approval fields in `.md` files (`sed -i`); T6 pre-commit is the backstop. `MultiEdit` stays in the matcher.

### Bash writes (best effort)

`bash-write-guard` reads the command like the other Bash guards (pipes, `sh -c`, `$(...)`, `sudo`, quotes) and blocks a command that names a protected place as the target of:

- a redirect: `>`, `>>`, `>|`, `&>`, `<>`, `>& file` (not `2>&1`), also with a here-document;
- `tee`, `dd of=`, `curl -o`, `wget -O`, `patch`;
- `cp`, `install`, `ln`, `rsync`, `scp`: the destination, or `-t DIR`; with a folder destination, the source name inside it;
- `mv`: the destination and every source (the file is moved away);
- `sed -i` and `perl -i` (also `-i.bak`, `-ni`, `--in-place`);
- `rm`, `unlink`, `rmdir`, `shred`, `truncate`, `git rm`; with `rm -r` or `git rm -r` also a folder inside the project that holds a protected place (`rm -r _bmad`);
- `find ... -delete` or `-exec` with a protected start folder;
- `git checkout` and `git restore` of a protected path, `git mv`.

Reading stays allowed: `cat`, `grep`, `jq`, `git diff`, `git show`, `cp` from the protected file, `git add` and `git commit` of it.

- **A `cd` is followed.** `cd _bmad-output/guard && echo x > a.json` is caught: the targets of one command are also tried from every folder a `cd` or `pushd` in it moves to (the guard does not track subshell scope, so it may be over-cautious).
- **A variable in the target** (`> "$DIR/approval-state.json"`) is blocked when the text spells a protected name. `> $FILE` is not: nothing in the text says where it points.
- **A glob** is checked by the folder part and by the file name for the state file (`rm _bmad/custom/approval-*.json`). A wildcard in a folder part is not followed.
- **Warden and Sentry stay allowed.** The agent starts them as `python3 <skill>/scripts/grant_approval.py --state-file ...`. The state file is an argument of `python3`, not a write target, so the call passes; tests run the exact commands from the Warden and Sentry guides through every guard. The script writes the file itself, after the developer confirmed twice.
- **`find . -name x -delete`** and `rm -rf .` are not this guard's: the command guard blocks them outside the scratch folders, and a folder above the project is never counted as holding a protected place.

## Write gate

Written for `write_gate.py`. It stops a source write until the current plan is approved and unchanged and the story is ready for development. The same check runs from the command line before coding starts: `python3 .claude/skills/tool-guard/scripts/write_gate.py --check` prints the same reasons and exits 1 when blocked. One message lists every reason; each names the next step.

| Rule id | Blocks | What to do |
|---|---|---|
| `GATE_NO_ACTIVE_PLAN` | no bmad-build plan with `status: in-progress` (or `in-review` when none is in progress) | run bmad-build to write a plan, have the developer approve it with Warden, then set its status to `in-progress` |
| `GATE_MANY_ACTIVE_PLANS` | more than one plan in progress (or, with none in progress, more than one in review) | the developer finishes or sets aside all but one |
| `GATE_PLAN_NOT_APPROVED` | no approval on record for the plan, `approval_status` is not `approved`, or the approval was reopened | the developer approves with Warden |
| `GATE_PLAN_CHANGED` | the plan was edited after approval (the build hash no longer matches) | the developer reads the change and approves again |
| `GATE_STATE_UNREADABLE` | `approval-state.json` cannot be read | the developer fixes or restores it |
| `GATE_STORY_NOT_FOUND` | the plan's `story` field names no file, or more than one | the developer fixes the `story` field |
| `GATE_READY_<CODE>` | the ready-for-dev gate blocks the story. One rule per reason: `GATE_READY_OPEN_QUESTIONS`, `GATE_READY_OPEN_ASSUMPTIONS`, `GATE_READY_LOW_CONFIDENCE`, `GATE_READY_MISSING_CONFIDENCE`, `GATE_READY_RESOLUTION_MISSING_WHO_OR_WHEN`, `GATE_READY_DELETED_ITEMS`, `GATE_READY_DUPLICATE_IDS`, `GATE_READY_MALFORMED_FRONTMATTER`, `GATE_READY_MISSING_EPIC_SUMMARY`, `GATE_READY_EPIC_SUMMARY_INCOMPLETE`, `GATE_READY_UNREADABLE_STORY`, `GATE_READY_BASELINE_UNAVAILABLE` | a person answers the questions and resolves the assumptions; the message carries the ids |
| `GATE_RECORD_FAILED` | the build session record could not be written, or the hook input has no usable session id | the developer fixes the folder or file |
| `GATE_ERROR` | the gate cannot run: a sibling skill is missing, its settings are unreadable, or `_bmad-output` holds more than 5000 Markdown files | the developer checks the setup |

The first check is the plan (Sentry's logic, `hash_mode = build`: the plan's approval-relevant text, not its progress fields). The second is the story (`ready_for_dev.py` as a library, minimum confidence 80, baseline `HEAD`). A story with `gate: generic` skips the second check (ADR 0004); the plan check still applies.

### What counts as a gated write

- **File tools:** Write, Edit, MultiEdit and NotebookEdit to a path inside the project that is not allowed.
- **Allowed (no gate):** `_bmad/`, `_bmad-output/`, `docs/`, `.claude/`, `.agents/`, `.bmad-guard/`, and the project's `write_gate_allowed_paths`. The other guards still run on them: `.claude/settings.json` is still `DOC_GUARD_SCRIPTS`, and an environment file in `docs/` is still `PATH_ENV_FILE`.
- **Scratch folders** (`tmp`, `build`, `dist`, `node_modules`, `__pycache__`, `coverage`, `.cache`, ... the same list as `rm -r`, plus the project's `scratch_folders`) and `/dev/` are not gated.
- **Outside the project:** not gated. A write to `/tmp`, `~/notes.md` or another repository is not a source write of this project. The path guard and the document guard still apply (secrets, the guard's own files).
- **Bash:** the explicit write targets of a command, read by the same reader as `bash-write-guard`: `>`, `>>`, `&>`, `tee`, `cp`, `mv` (destination and sources), `install`, `ln`, `rsync`, `sed -i`, `perl -i`, `rm`, `unlink`, `truncate`, `dd of=`, `curl -o`, `wget -O`, `patch`, `git rm`, `git mv`. `git checkout` and `git restore` are not read as writes (`git checkout main` has a path-like operand); `find -exec` is not either.
- **Links** are followed: a link in `docs/` that points into `src/` is a write to `src/`. Case is ignored.

### Design choices

- **The current plan.** The Markdown file under `_bmad-output/` (any folder) with `status: in-progress` and a plan marker (`route`, `approval_status`, `review_loop_iteration` or `baseline_commit`). That is where `bmad-build` keeps its spec by default (`implementation_artifacts`, `spec-<slug>.md`, or `<spec_folder>/stories/<id>-<slug>.md`); step 3 sets `in-progress` before coding. A trailing `# comment` after the status (the template has one) is ignored. A plan outside `_bmad-output/` is not found.
- **`in-review` counts when nothing is in progress.** bmad-build patches code during review (step 4), after it set the status to `in-review`. A plan that waits for review does not hide the next story's plan: `in-progress` wins when both exist.
- **More than one plan in progress blocks** (`GATE_MANY_ACTIVE_PLANS`). The guard cannot know which story the write belongs to, and picking one would let an approved plan cover another story's code. The message names the plans.
- **The story** is the file in the plan's `story` field: a project path, or an id such as `2-3` that matches `<id>.md` or `<id>-*.md` under `_bmad-output/` (exactly one file). A plan without `story` is its own story: it needs `epic` or `epic_summary` in its frontmatter, or `gate: generic`. Because the plan is hashed, a `gate: generic` marker in a plan cannot be added after approval. In any Markdown file, an AI edit of the marker is blocked by `DOC_APPROVAL_FIELD` (T4 review), so a person sets it.
- **Ready-for-dev always enforces.** The hook does not read `mode`; it blocks. `min_confidence`, `epics_dir` and `baseline` come from the skill's own `customize.toml`, never from a project override file (an agent could edit that file).
- **The hook asks, it does not change.** A plan that changed after approval is reported as `GATE_PLAN_CHANGED`. Sentry's own run (in bmad-build, before each coding phase) resets it to `pending-approval`.
- **The build session record (for the independent review, T7).** When a gated write passes, the hook adds the session id to `_bmad-output/guard/builds/<story>.json`: `{"story": ..., "sessions": [...], "first_at": ..., "last_at": ...}`. The session id is the `session_id` of the hook input, never `CLAUDE_CODE_SESSION_ID` and never anything the model typed. The file holds no path and no content. The story name is the plan's `story` value when it is a plain id (`2-3`), otherwise the file name of the story (of the plan, when it has no `story`). The write is atomic (temp file, then rename). The file is committed; only the hook writes it (`DOC_GUARD_RECORD` stops the agent).
- **If the record cannot be written, the write is blocked** (`GATE_RECORD_FAILED`). The review needs the record; a write with no proof of who built it would break the independent review. An unreadable or malformed record also blocks and is never overwritten. The record is written when the write gate passes, even if another guard then blocks the same call: an extra session id only makes the review check stricter.
- **A bad extension file** does not open the gate: the gate then ignores the project's additions (stricter), and the path and command guards report `EXT_BAD_FILE`.

### Write gate limits

- **Bash is best effort.** Only explicit targets are seen. `python3 -c`, a script file, `npm install`, `make`, `mkdir`, `touch`, `node -e`, `awk -i inplace`, an editor and a target held in a variable (`> $FILE`) are not seen. With a variable, only a literal folder in front of it counts (`src/$f`).
- **`cd` is followed in the order of the command**, a subshell is not scoped (`(cd /tmp; ls); echo x > src/a.py` is read as if the `cd` stayed). After a `cd` into a variable, only absolute targets are checked.
- **Paths outside the project are not gated**, and the project is the folder Claude Code started in (`CLAUDE_PROJECT_DIR`).
- **Reviewers and subagents** run in the same project. Their session id may be the parent's; this is not checked yet (T7).
- **`gate: generic` from the shell.** File-tool edits of the marker are blocked (`DOC_APPROVAL_FIELD`). A `sed -i` or `python -c` that adds it is not seen; T6 pre-commit is the backstop. ADR 0004 still asks to review the marker's use in the pilot.
- **A project with a renamed output folder** (not `_bmad-output`) finds no plan, so every source write is blocked until the folder is named `_bmad-output` again.
- **Test and build commands** that write to a source folder through a tool option (`pytest --junitxml=reports/x.xml`) are not seen; a redirect into a source folder is gated like any write.

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
  "scratch_folders": ["_work/cache", "**/generated"],
  "write_gate_allowed_paths": ["scripts/dev-tools", "README.md", "**/fixtures"]
}
```

- **Command rule:** `id` (lower case letters, digits, `.`, `_`, `-`), `command` (a program name, no path), and any of `words` (positional words, in order), `subcommand` (git only), `flag_sets` (match when one inner list is fully present, `"-f|--force"` means either), `arg_prefixes`, `unless_flags`, and `reason` (one line). A rule with only `command` blocks every use of that program. The reason code is `PROJECT_CMD_<ID>`.
- **Sensitive path:** a file name pattern (`payroll*.csv`, matched at any depth) or a folder (`data/private/**`, from the project root; `~/dir/**` for the home folder). The reason code is `PROJECT_PATH_<ID>`.
- **Scratch folder:** a project folder (`tmp`, `a/b`, or `**/name` for any depth) or an absolute folder with at least two parts (`/var/cache/build`). Not `.`, `..`, `/` or `**`.
- **Write gate path:** a project folder or file where the write gate does not ask for a plan (`scripts/dev-tools`, `README.md`, or `**/name` for any depth). Not absolute, no `..`, no `~`, no wildcard other than the leading `**/`. A scratch folder is exempt from the gate too.

What a project cannot do: remove or weaken a built-in rule, add a local host, carve a name out of a sensitive path, or remove an allowed path of the write gate. The guard reads the built-in list from this skill's `customize.toml` only; `_bmad/custom/tool-guard.toml` and `tool-guard.user.toml` are not read for guard rules.

**A bad extension file blocks.** Invalid JSON, an unknown key, a bad id, a rule that is too broad: the guards block every Bash call and every path tool call with `EXT_BAD_FILE` and name the problem (never repeating file content). The developer fixes the file by hand. The agent must not edit it to get past a block: a scratch folder added by the agent widens what `rm -r` may delete.

## Known limits

The guard reads the command text. It cannot see what the command text does when it runs. Do not rely on it as a sandbox.

- **Built at run time:** `$CMD -rf x`, `eval "$var"`, `bash -c "$(...)"`, `base64 -d | sh`, aliases and functions defined elsewhere, a script file (`bash script.sh`, `make clean`), `python -c`, `node -e`, `perl -e`, `rsync --delete`, `dd`, `mv x /dev/null`.
- **Remote and container commands:** `ssh host pg_dump`, `kubectl exec -- pg_dump`, `docker exec db psql`. The text after the host is not read.
- **A tunnel to a remote database** that listens on `localhost` looks local.
- **Globs:** `cat .e*` is not caught; `cat .env*` is. A glob in a folder part (`*/node_modules`) is treated as not provable for `rm`.
- **Search inside folders:** Grep and `grep -r` over a folder can read a sensitive file inside it. Name-based rules cannot see that.
- **Other tools and MCP servers** are not routed to these guards.
- **Writes that change the guard's own inputs:** an edit of `.git/config` or `.git/hooks` is not a `core.hooksPath` change by name. Edits of the guard scripts, the guard skills and `.claude/settings.json` are blocked by `DOC_GUARD_SCRIPTS`; a write from `python -c` or a script file is not seen. `_bmad/custom/prompt-guard.toml` and other override files that the developer wrote (not in the manifest) are not protected.
- **`aws s3api get-object`, `gsutil cp`, `az storage blob download`, `scp` / `rsync` from a remote host** are not export rules yet.
- **`UPDATE` without `WHERE`** is not in the SQL rule. `git checkout .` and `git restore .` (throw away changes) are not rules yet. Both are todos in the Phase 2 plan.
- **The extension file** (`_bmad/custom/project/guard/`) is protected from the file tools and from the shell commands listed above. A script the agent writes and runs (`python3 -c`, a script file, `node -e`) can still write it; so can `awk -i inplace`, `ed`, `tar -x`, an editor started from Bash and a path built from variables.
- **Approval fields written from Bash.** The shell guard protects the three places above, not Markdown documents. `sed -i 's/pending-approval/approved/' prd.md` is not seen. The pre-commit hook (T6) blocks a commit that changes an approval status without a matching Warden entry.
- **Warden's script can be run by the agent.** The two confirmations are enforced by Warden's skill text, not by the hook: a model that runs `grant_approval.py` itself is not stopped. The audit is the `APPROVED` event, the `approved_by` entry and the git commit.
- **Document rules look at one edit.** A person can still change anything by hand; the guard stops the agent. Resolution records and approval fields in files that are not Markdown (a `.yaml` story, a `.txt` plan) are not checked.
- **Documents that teach the markup** (for example `docs/ready-for-dev-markup.md`, with its example `Resolved:` lines and `[ASSUMPTION ...]` tags) are checked like stories: an agent edit that changes those example lines blocks. The developer edits them by hand.
- **The approved value is `approved`.** Warden's `approved_value` can be set to something else in `customize.toml`; the guard does not read it.
- **The matching is by name.** `id_rsa_utils.py` looks like an SSH key to the path guard.

The real limit is an OS sandbox, which is not in scope for Phase 2. The `permissions.deny` rules are the second layer for file tools.

## When a block looks wrong

Tell the developer the rule id. Do not look for another command that does the same thing. The developer can run the command by hand, or add a scratch folder or a rule in `tool-rules.json`.
