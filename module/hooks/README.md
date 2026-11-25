# Git hooks

Plain Python. No Node, no Husky. `scripts/setup_guard.py` copies every file in this folder (except this README) to `_bmad/custom/guard/hooks/` in the project, makes them executable, and sets `core.hooksPath` to that folder. `setup_guard.py --check` reports a hook that is missing, changed or not executable.

| File | What |
|---|---|
| `pre-commit` | The hook git runs. A thin launcher: it loads `guard_pre_commit.py` and blocks the commit if that fails to load, or if `python3` is older than 3.10 (the macOS system Python can be 3.9; the message says so). |
| `guard_pre_commit.py` | The three checks (scan, approval fields, tech stack) and the messages. |
| `guard_tech_stack.py` | The tech-stack list and the dependency-file readers. Pure text logic. |
| `guard_hook_lib.py` | Git helpers, loading of the guard skills, local events. Shared with the pre-push hook (T7). |

`pre-push` (T7) is not here yet.

## pre-commit

Exit 0: the commit goes ahead (a warning may be on stderr). Exit 1: the commit is blocked, and **one message lists every reason**. Nothing staged, or only deleted files: exit 0 at once. Any failure of the hook itself blocks (fail closed), with `COMMIT_ERROR` and a short cause (an error type, never file text). The hook never changes a file and never reverts a status.

| Rule id | Result | What |
|---|---|---|
| `COMMIT_SECRET` | block | A secret in a line this commit adds (prompt-guard `secrets` rules). |
| `COMMIT_PHI` | block in a HIPAA project, warn elsewhere | A personal or health identifier in an added line (prompt-guard `phi` rules; HIPAA = `phi = "block"`). |
| `COMMIT_SCAN_PARTIAL` | warn | More than 1 MiB was added to a file. The first MiB was scanned. |
| `COMMIT_SCAN_CONFIG` | warn | prompt-guard's config or `prompt-patterns.json` has a problem. The built-in rules still ran. |
| `COMMIT_APPROVAL_NO_ENTRY` | block | A document says `status` / `approval_status: approved`, or `approved_by` / `approved_at` changed, and the staged `approval-state.json` has no Warden entry for that file that is still in force. |
| `COMMIT_APPROVAL_STALE` | block | The document is approved and Warden has an entry for it, but the staged text does not hash to the recorded `approved_hash`: it was edited after approval. |
| `COMMIT_STATE_UNREADABLE` | block | `approval-state.json` is not valid JSON, and the commit needs it. |
| `COMMIT_GATE_GENERIC` | warn | `gate: generic` was added, removed or changed in a document. |
| `COMMIT_QUESTION_REMOVED` | warn | An open question or assumption is gone and has no resolution record. |
| `COMMIT_RESOLUTION_CHANGED` | event only | A `Resolved:` line or `[ASSUMPTION-RESOLVED ...]` tag changed. No message. |
| `COMMIT_TECH_STACK_NOT_ALLOWED` | block | A new dependency is not on the approved tech-stack list. |
| `COMMIT_TECH_STACK_UNAPPROVED` | block | A dependency was added, and the list has no matching Warden entry (never approved, edited since, or reopened). |
| `COMMIT_TECH_STACK_BAD_FILE` | block | The list, or a dependency file the check needs, cannot be read. |
| `COMMIT_TECH_STACK_REMOVED` | block | The commit deletes a list that is approved and in force. Reopen it with Warden and commit that first. |
| `COMMIT_DEPENDENCY_UNCHECKED` | warn | `go.mod`, `Cargo.toml` and the like changed, or the list does not govern that ecosystem. |
| `COMMIT_NO_TECH_STACK` | event only | There is no list, and a dependency file changed. No message. |
| `COMMIT_ERROR` | block | The hook could not run. |
| `COMMIT_ALLOWED`, `COMMIT_NOTHING_TO_CHECK` | event only | The commit may go ahead. One event, so a commit that the hook saw can be told from one it did not. |

### Design choices

- **Library code lives here, skill code is reused.** The hook folder holds the three modules above. It does not copy skill code: it loads `prompt_check.py` (prompt-guard), `approval_core.py` (Warden), `document_text.py` (tool-guard) and `guard_events.py` (Ledger) from the project's `.claude/skills/` or, if that folder has none, `.agents/skills/`. A skill that is missing blocks the commit with `COMMIT_ERROR` and says to run `setup_guard.py` (Ledger is the exception: no Ledger means a warning and no event, never a block).
- **Only the staged version counts.** The hook reads the index (`git diff --cached`, `git cat-file blob :path`), never the work tree. A secret that you staged and then removed from the file is still in the commit, and the hook blocks it. It works with `git commit -a` and `git commit -- <path>` (git gives the hook a temporary index then).
- **The scan reads the lines a commit adds, not whole files.** The same choice as the T5 content scan (it reads what is added, not what is already there). A file that already holds a flagged old value can still be edited. A rename counts as a delete plus an add, so a moved file is scanned in full. Binary files (as git decides), deleted files and lock files (`package-lock.json`, `yarn.lock`, `poetry.lock`, ... generated lists of hashes) are skipped. **Size cap:** 1 MiB of added text per file; the rest is not scanned and the hook warns (`COMMIT_SCAN_PARTIAL`), so a big file is never skipped quietly.
- **Same rules and settings as prompt-guard.** Rules, project patterns, allow-listed fakes (the AWS `...EXAMPLE` key and the others) and the `secrets` / `phi` actions all come from `prompt_check.check()`. `record` writes an event only; `off` says nothing. Injection and size are not checked.
- **Approval fields: what belongs here (T3 and T4 decisions).** The hook looks at every staged Markdown file (and at the tech-stack list) and compares it with HEAD:
  - `status` or `approval_status` is `approved`, or `approved_by` / `approved_at` changed: **needs a Warden entry** (block). Only Warden may set these fields.
  - `gate: generic` changed: **warn**. A person sets it (T4 review), and a commit cannot tell a person from the agent, so a block would stop real work.
  - A `Resolved:` line changed: **event only**. People write these and commit them all the time.
  - An open question or assumption removed without a record: **warn** (an id that was renumbered looks like a removal; look again with the pilot's false-block numbers).
  - A status that goes from `approved` to something else is **not checked**: it removes approval, and nothing downstream accepts a document that is not approved.
- **A Warden entry matches when** its `doc_path` is the file (relative to the project root), it is not `superseded`, and the hash of the staged text equals `approved_hash`. The hash is computed as Warden does it (`approval_core.content_hash`, with the entry's `hash_mode`, `full` or `build`) over the text with newlines folded to `\n`, so a CRLF checkout still matches. The entry is read from the **staged** `_bmad/custom/approval-state.json`: an approval that was not committed with the document does not count. This is the port of `revert-brief-status.js`: it caught "content changed after approval" and wrote `draft` into the file; this hook blocks, names the file and the Warden step, and changes nothing. It also does not depend on the file name (the old script knew `brief-*/brief.md`, `prd-*/prd.md` and the architecture spine), because the entry names the file.
- **Events: local queue only.** Events go to `.bmad-guard/events/queue.jsonl` through Ledger's code, with no endpoint, so the hook never waits on the network (a collector set in the environment is ignored). An event has the guard (`git-hooks`), stage `pre-commit`, the verdict, the rule id, the check (`scan`, `approval`, `tech-stack`), the staged file count and a `detail` (rule ids, a count or an error type). The approval events also carry the file path and the SHA-256 of the staged file (the same fields Warden records). **Paths of scanned files are not recorded**, and never file content or matched text. Package names are not recorded: the console message names them, the event counts them.

### Tech stack (optional)

A project may add `_bmad/custom/project/guard/tech-stack.toml`. With it, a **new** dependency (in the staged version, compared with HEAD) must be on the list. Without it the hook only writes the info event `COMMIT_NO_TECH_STACK`, and only when a dependency file changed.

```toml
---
status: draft
---
# Packages this project may use. Names only. Approved by the architect with Warden.
[allowed]
npm = ["react", "react-dom", "@types/*"]
python = [
  "django",
  "psycopg2-binary",
]
```

- The list counts when it is in the index (staged, or committed earlier). A file that exists only in the work tree is ignored.
- Only the table `[allowed]` with the keys `npm` and `python`; each is a list of names. `*` matches any characters. An ecosystem that is not in the file is not governed (its files get `COMMIT_DEPENDENCY_UNCHECKED`); an empty list allows nothing new. Names are compared in lower case; Python names also with `-`, `_` and `.` treated as the same (PEP 503). Versions are not checked.
- The file starts with a small `---` block because **Warden approves a file through its frontmatter** (`status`, `version`); the block is cut off before the list is read. It is not plain TOML for other tools. Lists can run over several lines. Any other shape (unknown key, a number in a list, an unclosed string) makes `COMMIT_TECH_STACK_BAD_FILE` block. The reader has no TOML library, because Python 3.10 has none.
- **Approval:** the architect runs Warden on the list with doc type `tech-stack` (`grant_approval.py --doc-path _bmad/custom/project/guard/tech-stack.toml --doc-type tech-stack --state-file _bmad/custom/approval-state.json`). The list counts as approved when its entry matches the staged text (same rule as for documents). A list that was never approved, edited since, or reopened blocks any commit that adds a dependency, so the list cannot be widened in the same commit as the dependency. Warden records who approved; the hook does not check that this person is the architect (a later phase can).
- **What is read:** `package.json` (`dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies`; `workspace:`, `file:` and `link:` entries are local code; an `npm:` alias counts under the real name too), `requirements*.txt`, `requirements*.in` and `*.txt` under a folder called `requirements`, and `pyproject.toml` (PEP 621 `[project]` dependencies and optional-dependencies, PEP 735 `[dependency-groups]`, Poetry's `dependencies`, `dev-dependencies` and group tables). Removing a dependency or changing its version is never a new dependency.
- **Warned, not read:** `go.mod`, `Cargo.toml`, `Gemfile`, `pom.xml`, `build.gradle(.kts)`, `composer.json`, `Pipfile`, `setup.py`, `setup.cfg`, `environment.yml`, `packages.config`, `*.csproj`, `*.fsproj`, `*.gemspec`, `mix.exs`, `pubspec.yaml`, `Podfile`, `Package.swift`, `build.sbt`.

### Known limits

- **A local hook can be skipped** (`git commit --no-verify`, another git client, setup not run). T2 blocks `--no-verify` from the agent; the `SessionStart` check warns when `core.hooksPath` is wrong. CI is a later phase (ADR 0002).
- **Warden's state file is trusted.** A person (or a script the agent writes) who edits `approval-state.json` and the document together makes a matching entry. The Claude Code hooks (T3) block the agent's tool and shell writes to that file; `python -c` is not seen. The audit trail is the `APPROVED` event, `approved_by` and git history.
- **The state file path is fixed** at `_bmad/custom/approval-state.json`. A project that moves it in Warden's `customize.toml` will see every approved document blocked.
- **Status in the document body is not read** (a `Status: Approved` table row). Only frontmatter. Only the value `approved` counts, as in T3.
- **Free-text PHI is not seen** (regex rules; ADR 0006). Test data that looks real blocks in a HIPAA project: allow-list the fake in `prompt-patterns.json`.
- **A merge commit with conflicts** is checked against HEAD like any commit, so a document the other branch approved is checked against the merged `approval-state.json`. Warden's entries travel with the document, so this normally passes; a merge that loses an entry blocks.
- **Speed:** about 0.2 s for a small commit, plus about 0.6 s per MiB of added text. A Markdown file costs two or three git calls. There is no limit on the total size of a commit, only on one file (1 MiB).
- **Not read:** `pyproject.toml` with dependencies as an inline table of `[tool.poetry]` or under a different table, `Pipfile`, `setup.py`, `.csproj` and others (warned). A dependency that a requirements line pulls in with `-r other.txt` is read when `other.txt` itself is staged.

Tests: `tests/test_pre_commit.py` (real throwaway git repositories, real Warden scripts), `tests/test_tech_stack.py`, `tests/test_setup_guard.py`, and the install test (`scripts/install_test.py`) commits through the installed hook.
