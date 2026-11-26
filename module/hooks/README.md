# Git hooks

Plain Python. No Node, no Husky. `scripts/setup_guard.py` copies every file in this folder (except this README) to `_bmad/custom/guard/hooks/` in the project, makes them executable, and sets `core.hooksPath` to that folder. `setup_guard.py --check` reports a hook that is missing, changed or not executable.

| File | What |
|---|---|
| `pre-commit` | The hook git runs. A thin launcher: it loads `guard_pre_commit.py` and blocks the commit if that fails to load, or if `python3` is older than 3.10 (the macOS system Python can be 3.9; the message says so). |
| `guard_pre_commit.py` | The three checks (scan, approval fields, tech stack) and the messages. |
| `guard_tech_stack.py` | The tech-stack list and the dependency-file readers. Pure text logic. |
| `pre-push` | The hook git runs on a push (T7). The same kind of launcher; it loads `guard_pre_push.py`. |
| `guard_pre_push.py` | The review-record check and its messages. |
| `guard_hook_lib.py` | Git helpers, loading of the guard skills, local events. Shared by both hooks. |

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
- **Approval:** the architect runs Warden on the list with doc type `tech-stack` (`grant_approval.py --doc-path _bmad/custom/project/guard/tech-stack.toml --doc-type tech-stack --state-file _bmad/custom/approval-state.json --approved-by <architect>`). The list counts as approved when its entry matches the staged text (same rule as for documents). A list that was never approved, edited since, or reopened blocks any commit that adds a dependency, so the list cannot be widened in the same commit as the dependency. Warden records who approved; the hook does not check that this person is the architect (a later phase can).
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

## pre-push

A push that sends source commits needs an independent review on record. Exit 0: the push goes ahead. Exit 1: the push is blocked, and **one message lists every reason** (it names the ref). Any failure of the hook itself blocks (`PUSH_ERROR`, an error type, never text). The hook changes nothing.

How it works, in order, for each ref in git's input (`local-ref local-sha remote-ref remote-sha`):

1. **A delete** (local sha all zeros), or an object that is not a commit: allowed, nothing to check.
2. **The pushed commits** are the commits the remote does not have: `git rev-list <local> --not <remote sha> --remotes=<remote>`. A new branch (remote sha all zeros) is the same call without the remote sha, so only commits that no tracking ref of this remote has are looked at. A remote sha that is not known locally is ignored. An annotated tag is followed to its commit.
3. **Source commits.** A pushed commit is a source commit when it changes a file outside the write gate's allowed paths (`_bmad/`, `_bmad-output/`, `docs/`, `.claude/`, `.agents/`, `.bmad-guard/` and the project's `write_gate_allowed_paths`; the hook calls `write_gate.source_checker`, so there is one definition). Two install files are not source either: the root `.gitignore` (setup adds a line) and BMAD's Copilot agent files `.github/agents/bmad-*.agent.md`, so the first push of a new project needs no record. Every other `.github` file, such as a workflow, stays source. The write gate still gates both (an agent write to them needs a plan). A merge commit has no changes of its own and is not one. No source commit: allowed (`PUSH_NOTHING_TO_CHECK`).
4. **Records are read from the pushed tree** (`git cat-file blob <sha>:<path>`), never from the work tree. A record that is not committed does not count; a work-tree edit after the commit changes nothing.
5. **A review record is valid** when its verdict is `pass`, none of its sessions is a build session, and its `reviewed_sha` is the pushed commit or one of its ancestors (not stale).
6. **A source commit is covered** when some valid record's `reviewed_sha` is that commit or a descendant of it. A source commit that is not covered blocks the push. A commit that changes no source (the commit that holds the record, documents) never needs a record, so "review, then commit the record" works.

| Rule id | Result | What |
|---|---|---|
| `PUSH_NO_RECORD` | block | Source commits are not covered, and the pushed commits hold no review record. |
| `PUSH_VERDICT_NOT_PASS` | block | A review record in the push has a verdict other than `pass` (`fail`, or anything else). |
| `PUSH_SAME_SESSION` | block | A review record in the push names a session that is in a build record (`_bmad-output/guard/builds/`). |
| `PUSH_RECORD_STALE` | block | A review record in the push points at a commit that is not in the pushed history (rebase, amend, another branch, an unknown sha). |
| `PUSH_COMMITS_AFTER_REVIEW` | block | A valid record exists, but source commits come after the reviewed commit (or were never in it). The message names up to five. |
| `PUSH_RECORD_UNREADABLE` | block | A review record in the push is not in the expected format (story, a 40 or 64 hex `reviewed_sha`, sessions, verdict). |
| `PUSH_BUILD_UNREADABLE` | block | A build record in the pushed tree cannot be read, so the reviewer cannot be compared with the builders. Always a block when the push has source commits. |
| `PUSH_ERROR` | block | The hook could not run (a guard skill is missing, git failed, bad input from git). |
| `PUSH_ALLOWED`, `PUSH_NOTHING_TO_CHECK` | event only | The push may go ahead. |

### Design choices

- **How a commit is mapped to a story: it is not.** Coverage is by history. A review record covers every source commit that is an ancestor of its `reviewed_sha`; the story name in the record is for people. No commit trailer is needed, and nothing the model types decides which commits a record covers. A record covers all earlier unpushed source commits, so a branch with two stories can be covered by one review of its tip; the reviewer is then expected to have looked at both (`bmad-code-review` reviews a diff against the baseline).
- **Same-session check against ALL build records,** not only the record's own story. The story in a review record is typed by the model; a build session could record its review under another story and escape a per-story comparison. Cost: a session that built any story in the repo cannot review any story. That matches the advice (review in a fresh session), and session ids are never reused.
- **Only records committed in the push get a reason of their own.** An old record for another story, left in the tree (stale after a squash merge, say), would otherwise block every later push. A valid old record still counts as coverage; a bad one that is not in the push is ignored.
- **Read the pushed tree, not the work tree.** What is pushed is what is checked. The price: the review record must be committed (the override tells the developer so), in its own commit, after the code.
- **Which verdicts pass.** Only the exact value `pass`. `fail` and any other word block.
- **Events: local queue only**, same as pre-commit. Stage `pre-push`, the rule id, the commit count and the source commit count, and the rule ids as detail. No ref name, no story, no SHA, no session id, no path.
- **The tool-guard skill must be installed.** The hook loads `write_gate.py` from `.claude/skills` (or `.agents/skills`) for the source definition. If it is missing the push is blocked with `PUSH_ERROR` and a message to run the setup script.

### Known limits

- **A local hook can be skipped** (`git push --no-verify`, another git client, setup not run). T2 blocks `--no-verify` from the agent. CI is a later phase (ADR 0002).
- **A forged record.** Anyone who can write a file and commit it can write a record with any session id. The hook (`review_record.py`) is the only honest writer, and the agent's file and shell writes to `_bmad-output/guard/` are blocked, but `python -c` or a script file can write it (`guard-rules.md`, "Review record limits"). A person can always forge it: the push check is a guard rail for the agent, not a proof against a developer.
- **Subagent session ids are not verified** (`docs/hooks.md`, open check 7). Run the review in a fresh top-level session.
- **A missing or deleted build record** leaves nothing to compare. Code that was written by hand, or before the write gate was on, has no build session; the push then needs only a valid review record.
- **Rebase, amend, squash:** commit SHAs change, so the record is stale (`PUSH_RECORD_STALE`) and the review is run again. A merge commit with conflict edits is not seen as source.
- **A remote with no refs** (the first push of a long history) checks every commit in it.
- **Another remote:** commits that the named remote lacks are checked even if `origin` has them. Pushing to a URL (not a remote name) uses all remote-tracking refs.
- **Speed:** one git call per pushed commit (`diff-tree`), a few more per push. A push of a thousand new commits takes a few seconds.

Tests: `tests/test_pre_commit.py` (real throwaway git repositories, real Warden scripts), `tests/test_tech_stack.py`, `tests/test_pre_push.py` (real repositories and a real bare remote, real `git push`), `tests/test_review_record.py`, `tests/test_setup_guard.py`, and the install test (`scripts/install_test.py`): it commits through the installed pre-commit hook and pushes to a bare remote through the installed pre-push hook (one block, one allow).
