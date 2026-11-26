# Guide for developers

| | |
|---|---|
| Ticket | GUARD-3, T0 (pilot pack) |
| For | Developers who build stories with Claude Code and `bmad-build` |
| Before you read | [Install guide](install.md) (setup, how to read a block message) |
| All rules | [guard-rules.md](../../module/skills/tool-guard/references/guard-rules.md) and [module/hooks/README.md](../../module/hooks/README.md) |

## The idea in one minute

- You build a story with `bmad-build`. It writes a **plan** first.
- You read the plan and approve it with **Warden**. Until then, **no source code can be written**.
- While you build, a hook records which session wrote code (the **build record**).
- At commit, **pre-commit** scans the staged lines and checks approvals and dependencies.
- Someone else reviews in a **fresh session** (see the [reviewer guide](reviewer.md)). Then you push, and **pre-push** checks for that review.

## What you see

| When | What | Where |
|---|---|---|
| Session start | A `[tool-guard]` warning if the setup is incomplete (`SETUP_INCOMPLETE`). Never a block. | Claude Code |
| You send a prompt | A block or a warning if it holds a secret or an identifier. | Claude Code |
| Claude calls a tool | A block message that lists rule ids, or (rarely) a warning. | Claude Code |
| `git commit` | A block message from `pre-commit`, or a warning. | Your terminal |
| `git push` | A block message from `pre-push`. | Your terminal |

A block always has a **rule id**. Look it up in `guard-rules.md` (tool calls) or `module/hooks/README.md` (git). Never ask Claude to "find another way".

## What you do

### 1. Start from a ready story

The story must be ready for development, or the write gate blocks (`GATE_READY_*`). The BA owns this: no open question, no open assumption, a who-and-when on every answer, confidence 80 or more, an epic summary. See the [BA / PM guide](ba-pm.md). If you get `GATE_READY_OPEN_QUESTIONS`, ask the BA. Do not edit the story's questions yourself to pass the gate.

### 2. Plan, then approve

1. Run `bmad-build` on the story. It writes the plan: `_bmad-output/implementation-artifacts/spec-<slug>.md`.
2. Read the **whole plan**. Warden asks two separate questions: do you approve it, and have you read the plan itself. Answer each one on its own. "Looks good" once does not count for both.
3. After approval `bmad-build` sets the plan to `ready-for-dev`. When coding starts (BMAD step 3) the plan becomes `in-progress`, and `bmad-build` runs the write gate check before the first code change. You can run it yourself:

   ```bash
   python3 .claude/skills/tool-guard/scripts/write_gate.py --check
   ```

   Exit 0 means source writes are open. Exit 1 prints every reason.

### 3. Build

- Source writes work while the plan is approved and unchanged.
- If you or Claude edit the plan after approval, the next source write is blocked: `GATE_PLAN_CHANGED`. Read the change and approve again.
- Only one plan may be `in-progress`: `GATE_MANY_ACTIVE_PLANS`. Finish or set aside the others.
- Always allowed (no plan needed): `_bmad/`, `_bmad-output/`, `docs/`, `.claude/`, `.agents/`, `.bmad-guard/`, scratch folders (`tmp`, `build`, `dist`, `node_modules`, `coverage`, `.cache`, ...) and anything outside the project. Root files such as `README.md` count as source. A project can add paths in `_bmad/custom/project/guard/tool-rules.json` (by hand, `write_gate_allowed_paths`).

| Rule id | Meaning | Next step |
|---|---|---|
| `GATE_NO_ACTIVE_PLAN` | No plan is `in-progress`. | Run `bmad-build`, approve, set `in-progress`. |
| `GATE_PLAN_NOT_APPROVED` | The plan has no valid approval. | Approve with Warden. |
| `GATE_PLAN_CHANGED` | The plan changed after approval. | Read the change, approve again. |
| `GATE_MANY_ACTIVE_PLANS` | Two plans are in progress. | Finish or set aside one. |
| `GATE_STORY_NOT_FOUND` | The plan's `story` field points at no file, or at many. | Fix the `story` field. |
| `GATE_READY_<CODE>` | The story is not ready (open questions, low confidence, ...). | Ask the BA. |
| `GATE_RECORD_FAILED` | The build record could not be written. | Check the folder `_bmad-output/guard/`. |

### 4. The build record

When a source write passes, the hook adds the session id to `_bmad-output/guard/builds/<story>.json`. It holds ids and times, no code. **You do not edit it.** Commit it with your code. The review check compares the reviewer's session with this list: a session that built the story cannot review it.

### 5. Commit

`pre-commit` reads what you **staged**, not your working files. One message lists all reasons.

| Rule id | Result | Next step |
|---|---|---|
| `COMMIT_SECRET` | Block | Remove the value. Read it from an environment variable. |
| `COMMIT_PHI` | Block in a HIPAA project, warn otherwise | Remove the data, or use an obvious fake. |
| `COMMIT_APPROVAL_NO_ENTRY` | Block | A document says `approved` but Warden has no record. Undo it, or ask for Warden. |
| `COMMIT_APPROVAL_STALE` | Block | An approved document was edited. Approve it again. |
| `COMMIT_TECH_STACK_NOT_ALLOWED` | Block | A new dependency is not on the approved tech-stack list. Use a listed package, or ask the architect to update the list. |
| `COMMIT_TECH_STACK_UNAPPROVED` | Block | The list was never approved, or changed since. The architect approves it with Warden first. |
| `COMMIT_QUESTION_REMOVED`, `COMMIT_GATE_GENERIC` | Warn | Check you meant it. |

Details and the other `COMMIT_*` ids: `module/hooks/README.md`.

### 6. Hand over, then push

Ask a colleague to review in a **fresh session**. They commit a review record on its own. Then you push. `pre-push` blocks with a `PUSH_*` id if the review is missing, too old, from the build session, or not `pass`. See the [reviewer guide](reviewer.md).

## Sensitive paths and destructive commands

Blocked for the agent (rule ids in `guard-rules.md`):

- **Reading or writing:** `.env` and `.env.*` (but `.env.example` is fine), `*.pem`, `*.key`, SSH key names (`id_rsa*`, `id_ed25519*`, ...), `*.p12`, `*dump*.sql`, database `.bak` files, `~/.aws`, `~/.ssh`, `~/.config/bmad-guard`. Ids: `PATH_*`.
- **Destructive commands:** `rm -r` outside scratch folders, `find -delete`, `git push --force` (also `--force-with-lease`), `git push --delete`, `git reset --hard`, `git clean -f`, `git branch -D`, `DROP` / `TRUNCATE` / `DELETE` without `WHERE` in a SQL client, `chmod -R 777`. Ids: `CMD_*`.
- **Skipping the guard:** `--no-verify`, `git commit -n`, any change of `core.hooksPath`. Ids: `CMD_GIT_NO_VERIFY`, `CMD_GIT_COMMIT_SKIP_HOOKS`, `CMD_GIT_HOOKS_PATH`.
- **Data out:** `pg_dump`, `psql`, `mysql`, `mongodump` and similar against a remote host (`EXPORT_DB_REMOTE`), `aws s3 cp` from a bucket (`EXPORT_S3_DOWNLOAD`).
- **The guard's own files:** `.claude/settings.json`, the guard skills, git hooks, `approval-state.json`, `_bmad-output/guard/`, `_bmad/custom/project/guard/`, `_bmad/custom/prompt-guard.toml`. Ids: `DOC_*`. **You** edit these by hand when needed. The agent never does.

If a command is safe and blocked, run it yourself in your terminal, or add a scratch folder in `tool-rules.json`. Do not ask Claude to rewrite it so it passes.

## You must not

- Use `--no-verify`, `-n` on commit, or change `core.hooksPath`.
- Write `approved` into a document, or edit `approval-state.json`. Use Warden.
- Put real patient data or real keys in prompts, files, tests or commits. Fake data only.
- Edit or delete a build record or a review record.
- Run Warden's script on a document you have not read.
- Switch the hooks off to "get past" a block. Send the rule id to Shameer.

## Limits to know

- The guards read command text. A script file, `python -c` or text built at run time is not seen. Do not treat the guard as a sandbox.
- A secret built by a shell command at run time (for example `python3 -c` that joins string parts) is not seen by the content scan of the agent layer. The pre-commit hook catches it before it is committed. A scan after the write is planned for Phase 4.
- Your own global `~/.claude` settings (deny or ask rules, your own hooks) can block before the project guard runs. Then you see a Claude Code permission message, not a rule id. To check the guard alone, run `claude --setting-sources project,local`.
- Free-text PHI (names, diagnoses in a sentence) is not seen.
- A person can skip a local hook. The design accepts this (ADR 0002), and the pilot rules forbid it.
