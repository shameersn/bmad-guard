# Demo run: Guarded BMAD in 45 minutes

| | |
|---|---|
| Ticket | GUARD-3 (demo for senior review) |
| For | Shameer, presenting to senior leaders |
| Project | A new "clinic-booking" API, FastAPI and SQLite, HIPAA mode. **Fake data only.** |
| Time | About 40 minutes to prepare, about 2 hours to rehearse (day before), 45 minutes live |
| Kit | [`demo/`](../../demo/README.md): `prepare.sh`, `make-live-copy.sh`, `telemetry.json.example`, `prompts.md` |
| Based on | [Trial run](trial-run.md), [install guide](install.md), [collector README](../../deploy/collector/README.md), [T4 telemetry report](../reports/phase-3-telemetry.md) |

## The idea

One small project goes from an idea to pushed code: product brief, PRD, one FRD per PRD feature, architecture, stories, ready-for-dev gate,
plan, generated code, commit, review, push. The guards block unsafe steps live, each with a rule id. Every decision and
the Claude Code metrics land in ClickHouse and show in Grafana. A rehearsal the day before tags each approved stage, so a
slow model step becomes one command on the day.

```text
Idea → Brief → PRD → FRDs → Architecture → Stories → Build → Commit → Review → Push
 |       |      |      |          |            |         |       |        |       |
prompt Warden Sentry Fenn +   Warden +     Sentry +   write   pre-     review  pre-push
guard  + Voss (brief Iris +   tech stack   ready-for  gate    commit   record
             approved?) Warden              -dev gate
                 every decision → OTel collector :4318 → ClickHouse → Grafana :3000
```

## Agenda (45 minutes)

| Time | Segment | Mode | Fallback tag |
|---|---|---|---|
| 0–3 | Why guards: the model writes, guards outside it decide | Talk | |
| 3–6 | Setup is real: stack up, `setup_guard.py --check`, a PHI prompt blocked | Live | |
| 6–14 | Brief, Voss review, Warden says no, Sentry blocks the PRD, then two yes answers | Live | `demo/1-brief` |
| 14–19 | PRD, FRDs, architecture, stories: start the PRD, then jump; show an FRD, its review and the approvals in git | Skip | `demo/4-stories` |
| 19–22 | Ready-for-dev gate: open question blocks, resolve by hand, allowed; a story blocked by its FRD | Live | |
| 22–30 | Code before plan blocked; `bmad-build` story 1; code and tests; plan edit blocked | Live | `demo/5-story1-built` |
| 30–37 | Guard rapid fire: `rm -rf`, `reset --hard`, `--no-verify`, `.env`, PHI commit, Postgres driver | Live | |
| 37–41 | Push blocked without review; fresh-session review; push allowed | Live | `demo/6-reviewed` |
| 41–45 | Grafana: blocks per rule, users, tokens, cost; privacy query; audit reports | Live | |

## Part A. Prepare (day before, about 40 minutes)

`<bmad-guard>` is your clone of this repo, on the branch or tag that holds `demo/`.

### A0. Starting again

`prepare.sh` refuses a folder that is not empty, and a project made with an older module version must not be reused
(it has the old guards). Delete the old run first:

```bash
rm -rf ~/Projects/Exp/clinic-demo ~/Projects/Exp/clinic-demo-remote.git \
       ~/Projects/Exp/clinic-demo-live ~/Projects/Exp/clinic-demo-live-remote.git
```

The stack, `secrets.local.ini`, the developer keys and your credentials file still work: skip `keys.py init` and A3's
`--credentials` step. For an empty dashboard, run `docker compose down -v && docker compose up -d`; the password file
and the key hashes are kept.

### A1. Collector stack and keys (off-screen)

```bash
cd <bmad-guard>/deploy/collector
python3 keys.py init
docker compose up -d && docker compose ps     # clickhouse "healthy"
python3 keys.py add rehearsal                 # key shown ONCE: into your password manager
python3 keys.py add demo-dev
docker compose restart otelcol
```

Grafana: `http://127.0.0.1:3000`, user `admin`, password `GRAFANA_ADMIN_PASSWORD` in `secrets.local.ini`. The first
start downloads the pinned plugins (about a minute, needs a network).

**Password trap.** ClickHouse and Grafana take their passwords only when their volumes are created. `keys.py init` with
old volumes still there gives a file with new passwords and a stack with the old ones: Grafana says `invalid password`
and the collector logs `otel_writer: Authentication failed`, so no events land. Fix: `docker compose down -v`, then
`docker compose up -d` (this deletes the stack's data; back up first with `backup.sh` if you need it).

Never run `keys.py add` while sharing your screen. Run `keys.py` and `setup_guard.py --credentials` in a real terminal,
not as a command typed into a Claude chat: a chat command sends its output into the conversation. Never
`export BMAD_GUARD_KEY` in a shell profile.

### A2. The demo project

```bash
<bmad-guard>/demo/prepare.sh --project ~/Projects/Exp/clinic-demo --name "<your name>"
```

It checks the tools, installs BMAD 6.12.4, runs the guard setup, sets HIPAA mode, approves the tech-stack list with
Warden's script, makes a local bare remote and tags `demo/0-setup`. It also writes
`~/.config/bmad-guard-demo/telemetry.json` once (it never overwrites it). It stops at the first failure.

### A3. Send events as `rehearsal`, and turn on Claude Code telemetry

```bash
python3 <bmad-guard>/scripts/setup_guard.py --credentials --endpoint http://127.0.0.1:4318 --user rehearsal
alias gclaude='claude --settings ~/.config/bmad-guard-demo/telemetry.json'
```

If setup says `key is empty or has control characters`, the paste brought other characters (some terminals add
invisible paste markers, and the clipboard may hold more than the key). Copy only the key, copy nothing after it, and
pass it for this one command:

```bash
BMAD_GUARD_KEY="$(pbpaste)" python3 <bmad-guard>/scripts/setup_guard.py --credentials --endpoint http://127.0.0.1:4318 --user rehearsal
pbcopy < /dev/null     # clear the clipboard
```

To check the clipboard first without showing it: `pbpaste | python3 -c 'import sys;s=sys.stdin.read();print(len(s), sum(ord(c)<32 for c in s))'`
prints `43 0` for a key alone.

The pilot puts the telemetry settings in managed settings (IT, root). For a laptop demo the same values go through
`--settings`. The T4 live check passed only the headers helper that way and set the variables in the environment, so
**check in the rehearsal** that the Claude Code panels in Grafana fill.

Add the event viewer to your shell:

```bash
events() { tail -n "${1:-5}" .bmad-guard/events/queue.jsonl | python3 -c 'import sys,json; [print(e["verdict"], e["reason_code"], e.get("tool","")) for e in map(json.loads, sys.stdin)]'; }
```

## Part B. Rehearse and tag (day before, about 2 hours)

Run the whole flow once in `~/Projects/Exp/clinic-demo` with `gclaude`, using [`demo/prompts.md`](../../demo/prompts.md).
After each stage is approved and committed, tag it:

| Stage | Run | Warden | Tag |
|---|---|---|---|
| Brief | `bmad-product-brief` with the idea in `prompts.md`; Voss reviews | two yes | `git tag demo/1-brief` |
| PRD | `bmad-prd` (Sentry checks the brief); features get `F-001`…, NFRs `NFR-001`… | two yes | `git tag demo/2-prd` |
| FRDs | Fenn once per feature (F-001 book, F-002 cancel, F-003 free slots); Iris reviews each; add one `Disputed:` line in F-001's review and re-run Iris; resolve the questions by hand except **one in F-002** | two yes per FRD | `git tag demo/2b-frd` |
| Architecture | `bmad-architecture`: FastAPI, SQLite, pytest | two yes | `git tag demo/3-arch` |
| Stories | `bmad-create-epics-and-stories` (Sentry checks each FRD; stories carry `feature: F-<nnn>`); leave one open question in an F-001 story for the gate step | none today | `git tag demo/4-stories` |
| Build story 1 | `bmad-build` "Book an appointment" | plan: two yes | `git tag demo/5-story1-built` |
| Review | new `gclaude`; `bmad-code-review` story 1; commit the record alone; `git push` | none | `git tag demo/6-reviewed` |

The rehearsal also proves the steps the trial run marks "first real run" (Warden and Sentry in the planning flow, the
build flow, the fresh-session review). Write what you see in `docs/reports/pilot-log.md`. A guard that fails is a
finding: fix it or mention it on the day, never hide it.

Then make the demo-day copy and switch the sender to `demo-dev` (asks for the key, hidden):

```bash
<bmad-guard>/demo/make-live-copy.sh --from ~/Projects/Exp/clinic-demo --user demo-dev
```

It clones to `~/Projects/Exp/clinic-demo-live`, starts at `demo/0-setup`, wires the guards again (`core.hooksPath` is
local git config) and makes a fresh bare remote with `main` only.

**Skip a slow step on the day**, in your own terminal (the guards stop the agent, not you):

```bash
git switch --discard-changes -C main demo/4-stories
```

## Part C. Live

Two windows: `gclaude` in `clinic-demo-live`, and Grafana. Follow [`demo/prompts.md`](../../demo/prompts.md) top to
bottom. Run `events 5` after each block.

## Part D. Grafana and audit

- Dashboard "guard events", time range "Last 24 hours": guard events per user and verdict (`rehearsal` and `demo-dev`),
  blocks per rule, Claude Code tokens and cost per user and model, lines of code, commits, active time.
- Privacy: the query in `prompts.md` step 28 shows rule ids, guards, verdicts and users only. The fake SSN and token
  typed in the demo are stored nowhere.
- Audit reports, from `deploy/collector/reports`:

  ```bash
  python3 guard_reports.py commits --repo ~/Projects/Exp/clinic-demo-live --range demo/0-setup..HEAD
  python3 guard_reports.py reviews --repo ~/Projects/Exp/clinic-demo-live
  ```

  Optional: commit once with `--no-verify` from your terminal before this. `commits` lists it as `no-event`: a bypass
  cannot hide.

## Risks

| Risk | Mitigation |
|---|---|
| Warden, Sentry, Fenn, Iris, build flow and fresh-session review have not been driven by a live model yet | Rehearsal runs every one; findings go to the pilot log |
| Model speed: PRD and architecture can take 10+ minutes | Stage tags; jump with one command; say so openly |
| Claude refuses to write a fake token | Use the terminal commit (`COMMIT_SECRET`) instead |
| Claude Code panels empty with `--settings` | Check in rehearsal; guard events still flow through the queue |
| Grafana plugins need a network on first start | Start the stack the day before; keep the volumes |
| A key on screen | Keys made off-screen; credentials file only; fake values only |
| Real story layout differs from the fixture (`GATE_STORY_NOT_FOUND`) | Fix by hand in rehearsal, note as a finding, tag after the fix |

## Demo-day checklist (30 minutes before)

1. `docker compose ps`: three containers up, clickhouse healthy.
2. Grafana open, logged in, "Last 24 hours", rehearsal data visible.
3. In `clinic-demo-live`: `setup_guard.py --project . --check` says up to date; `git log --oneline -1` is the
   `demo/0-setup` commit; `git tag -l 'demo/*'` shows 8 tags.
4. Sender is `demo-dev`. No key on screen. No `.env` anywhere.
5. Fake token built in a scratch note; `prompts.md` open to copy from.
6. Terminal font 18px, notifications off.
7. `gclaude` started once and quit, so first-run prompts are done.
