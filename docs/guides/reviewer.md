# Guide for reviewers

| | |
|---|---|
| Ticket | GUARD-3, T0 (pilot pack) |
| For | The person who reviews a story's code before it is pushed |
| Before you read | [Install guide](install.md) |
| All rules | [module/hooks/README.md](../../module/hooks/README.md) ("pre-push") and [guard-rules.md](../../module/skills/tool-guard/references/guard-rules.md) ("Review record") |

## The idea in one minute

- Code written by an AI needs a review by **someone who did not write it**.
- The guard enforces "did not write it" with **session ids**: the session that built the story cannot be the session that reviews it.
- The review ends with a **review record**. `pre-push` blocks a push of source code that has no valid record.
- The record proves **which session reviewed which commit**. It does not prove the review was good. That is your job.

## What you do

### 1. Start a fresh session

- Start a **new top-level Claude Code session** in the project folder (quit and run `claude` again). Do not review in the session that built the code.
- Do **not** review through a subagent. The record call is refused in a subagent (`REVIEW_IN_SUBAGENT`).
- Do not rely on `/clear` to get a new identity. Whether it gives a new session id for the hooks is not verified.
- Check out the branch to review, with the code committed. Code that is not committed is not covered.

### 2. Run the review

Ask Claude to run `bmad-code-review`. The Guarded BMAD override adds these rules:

- Review in a fresh session. If this session also wrote the code, the skill says so and recommends a restart.
- Use the severity levels: CRITICAL, HIGH, MEDIUM, LOW (the meaning of each is in `_bmad/custom/bmad-code-review.toml`).
- List every changed file from `git diff` and compare it with the plan's "Impacted Modules & Files". A file outside the plan is a finding.
- For a thin story (`requirements` in its front matter), review the code against the FRD, not against the story: the skill reads the acceptance criteria and key transactions of the story's FR ids from the FRD (`frd_parse.py`). Each finding names the FR id and the criterion number (for example FR-F001-02 criterion 3). A criterion with no code and no test is a finding. If the FRD cannot be read, the review says the FRD check was not done and lowers its confidence. A technical story has no FRD check: review it against the architecture section it names.
- Score the review's own confidence (how complete this review was, not how good the code is).

### 3. The record

The last step of the workflow runs **one plain command**:

```bash
python3 "<project>/.claude/skills/tool-guard/scripts/review_record.py" --story <story> --verdict pass
```

- Claude chooses the verdict. `pass` only when no `decision-needed` or `patch` finding is left and no unresolved `high` or `medium` finding remains. Otherwise `fail`.
- You cannot give a session id. The command has no such option, and the hook ignores one. **The hook writes the record** with the session id it gets from Claude Code and the current `HEAD`: `_bmad-output/guard/reviews/<story>.json`.
- The command only checks that the record exists. If Claude runs a different command (a pipe, `&&`, `cd`, a variable), nothing is written and the command exits 1.
- Prefer `Leave as action items` for findings that need a code change. A session that applies patches may have written code for the story. Then its own record is refused at push (`PUSH_SAME_SESSION`).

### 4. Commit the record on its own

```bash
git add _bmad-output/guard/reviews/<story>.json
git commit -m "Review record for <story>"
```

- The commit must hold **no code**. A commit that changes no source never needs a review, so "review, then commit the record" works.
- The record must be in the history that gets pushed. In the pilot the developer pulls your record commit and pushes it with the code. You can push it yourself if the team agrees.
- If a developer then adds source commits, they need a new review (`PUSH_COMMITS_AFTER_REVIEW`).

## What pre-push checks

For every pushed ref, `pre-push` finds the commits the remote does not have. If none of them change source (anything outside `_bmad/`, `_bmad-output/`, `docs/`, `.claude/`, `.agents/`, `.bmad-guard/`), the push is allowed. Otherwise every source commit must be covered by a valid review record read from the **pushed tree**.

A record is valid when: the verdict is `pass`, none of its sessions is a build session (checked against **all** build records, not only this story), and `reviewed_sha` is the pushed commit or one of its ancestors.

| Rule id | Meaning | Next step |
|---|---|---|
| `PUSH_NO_RECORD` | Source commits, and no review record in the push. | Review in a fresh session, commit the record, push. |
| `PUSH_VERDICT_NOT_PASS` | The record says `fail` (or another word). | Fix the findings, commit, review again in a new session. |
| `PUSH_SAME_SESSION` | The record's session is a build session. | Review again in a fresh session. |
| `PUSH_RECORD_STALE` | The record points at a commit that is not in the pushed history (rebase, amend, squash, another branch). | Review again. |
| `PUSH_COMMITS_AFTER_REVIEW` | Source commits come after the reviewed commit. | Review again, so the record covers them. |
| `PUSH_RECORD_UNREADABLE` | The record has the wrong format. | Make it again with the command. Do not edit it. |
| `PUSH_BUILD_UNREADABLE` | A build record cannot be read. | Ask the developer. Do not edit it. |
| `PUSH_ERROR` | The hook could not run. | Run setup again (`--check`). |

One review of a branch tip covers all earlier unpushed source commits of that branch. If the branch holds two stories, you are expected to have looked at both.

## You must not

- Review in the session that built the code, or in a subagent.
- Write or edit a review record by hand, or run `python -c` to write one. The guard blocks the file tools and common shell writes (`DOC_GUARD_RECORD`), but a determined person can forge a record. The push check is a guard rail, not proof against a developer.
- Record `pass` without doing the review.
- Use `--no-verify` to push.
- Rewrite history after the review (rebase, amend, squash) and push with the old record. It will be stale.

## Limits to know

- **The verdict is the reviewer's word.** The hook shows which session recorded which commit, not the quality of the review.
- **Subagent session ids are not verified** ([hooks.md](../hooks.md), open check 7). If a subagent shares its parent's id, `pre-push` still blocks a self-review (the safe side). If it has its own id, a subagent could pass as a different session, and only `REVIEW_IN_SUBAGENT` stops it. Run the review in a fresh top-level session.
- **Code written by hand, or before the write gate was on,** has no build session, so the push needs only a valid record.
- **A record deleted in a later commit** is not seen by `pre-push`. `DOC_GUARD_RECORD` stops the agent from deleting it, not a person.
- A first push to an empty remote checks every commit in the history.
