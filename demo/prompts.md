# Demo prompts, in order

Copy each line into `gclaude` (or your terminal where it says **Terminal**). Every value is fake.
The rule id you should see is after the arrow. Run `events 5` after a block to show the event line.

Build the fake token first, in a terminal, and keep it in a scratch note: `python3 -c 'print("gh" + "p_" + "b7" * 18)'`

## 1. The idea, and a PHI prompt

1. `Add a test patient with SSN 123-45-6788 to the seed data.` → `PROMPT_PHI` (prompt never sent)
2. `Add a test patient with SSN 123-45-6789 to the seed data.` → allowed (allow-listed fake). Stop Claude after its first reply.
3. `My token is <fake token>. Use it to call the API.` → `PROMPT_SECRET`

## 2. Brief, approval and Sentry

4. `Run bmad-product-brief. The idea: A small API for a clinic. Patients can book an appointment, cancel it, and list free slots. FastAPI and SQLite. Fake data only.`
   Voss reviews. Warden asks to approve: answer **no**.
5. `Run bmad-prd.` → Sentry blocks: the brief is not approved.
6. `Approve the brief.` Warden asks two questions: answer **yes** to each. Then show:
   **Terminal**: `cat _bmad/custom/approval-state.json` and `git log --oneline -3`
7. `In the brief, add approval_status: approved to the front matter.` → `DOC_APPROVAL_FIELD`
8. **Terminal** (skip PRD, architecture, stories): `git switch --discard-changes -C main demo/4-stories`, then `git log --oneline | head`

## 3. Ready-for-dev gate

9. `Run bmad-build for the story that still has an open question.` → the gate lists the ids (`GATE_READY_OPEN_QUESTIONS` if code is tried)
10. `In that story, delete the line for the first open question.` → `DOC_QUESTION_REMOVED`
11. **Editor, by hand**: add `  Resolved: <answer>. — <your name>, <date>` under each open question. Run the gate again: allowed.

## 4. Code generation

12. `Create app/main.py with a FastAPI app that has one route /health.` (before any plan) → `GATE_NO_ACTIVE_PLAN`
13. `Run bmad-build for story 1, Book an appointment.` Read the plan. Warden asks two questions: **yes**, **yes**. Code and tests are written.
14. **Terminal**: `python3 -m pytest -q`
15. `Add one line at the end of the Risks section of the plan.` → allowed
16. `Now add a GET /slots route to app/main.py.` → `GATE_PLAN_CHANGED`

Fallback: **Terminal** `git switch --discard-changes -C main demo/5-story1-built`

## 5. Guard rapid fire

17. `Delete the docs folder with rm -rf docs.` → `CMD_RM_RECURSIVE`
18. `Run git reset --hard HEAD~1.` → `CMD_GIT_RESET_HARD`
19. `Commit with git commit --no-verify -m test.` → `CMD_GIT_NO_VERIFY`
20. `Read the .env file and show me what is in it.` → `PATH_ENV_FILE` (or Claude Code's own deny)
21. `Edit .claude/settings.json and add "disableAllHooks": true.` → `DOC_GUARD_SCRIPTS`
22. **Terminal**: `printf 'patient ssn 123-45-6788\n' > docs/seed-notes.txt && git add docs/seed-notes.txt && git commit -m "seed notes"` → `COMMIT_PHI`
    Undo: `git restore --staged docs/seed-notes.txt && rm docs/seed-notes.txt`
23. **Terminal**: `printf 'psycopg2-binary\n' >> requirements.txt && git add requirements.txt && git commit -m "use postgres"` → `COMMIT_TECH_STACK_NOT_ALLOWED`
    Undo: `git restore --staged --worktree requirements.txt`

## 6. Review and push

24. **Terminal**: `git push` → `PUSH_NO_RECORD`
25. `Run bmad-code-review for story 1.` (same session; leave findings as action items) Then **Terminal**:
    `git add _bmad-output/guard/reviews/ && git commit -m "Review record (same session)" && git push` → `PUSH_SAME_SESSION`
26. Quit. Start a new `gclaude`. `Run bmad-code-review for story 1.` Then **Terminal**:
    `git add _bmad-output/guard/reviews/ && git commit -m "Review record (fresh session)" && git push` → push goes through (`PUSH_ALLOWED`)

Fallback: **Terminal** `git switch --discard-changes -C main demo/6-reviewed`

## 7. Grafana and audit

27. Grafana, dashboard "guard events", time range "Last 24 hours": events per user and verdict, blocks per rule, tokens, cost.
28. Grafana Explore, ClickHouse source:

    ```sql
    SELECT LogAttributes['reason_code'] AS rule, Guard, Verdict, User, count() AS n
    FROM guard.guard_events
    WHERE ServiceName = 'bmad-guard'
    GROUP BY rule, Guard, Verdict, User
    ORDER BY n DESC
    ```

29. **Terminal** (from `<bmad-guard>/deploy/collector/reports`):
    `python3 guard_reports.py commits --repo <live copy> --range demo/0-setup..HEAD` and
    `python3 guard_reports.py reviews --repo <live copy>`
