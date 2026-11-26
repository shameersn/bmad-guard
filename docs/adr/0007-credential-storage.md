# 0007 — Credential storage: where the ingest key and the collector endpoint live

- **Status:** Proposed (2026-11-26). Becomes Accepted after team approval by PR.
- **Date:** 2026-11-26
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR
- **Review:** Shameer accepted the recommended answers on 2026-11-26: rename to `BMAD_GUARD_ENDPOINT` with a one-release alias; `user` in the file; a too open file is refused; key delivery by a one-time share from the password manager.

## Context
ADR 0003 gives each developer one ingest key. The collector stamps the user from that key, so the key must stay secret. The key is not a database credential: it only lets a client send events.

The sender needs three values: the collector URL, a developer id (the Basic-auth user name) and the key. Today it reads them from environment variables (`guard_events.py:65-67`) and refuses to send when one is missing (`guard_events.py:121-122`). The URL variable is `BMAD_GUARD_COLLECTOR`. The Phase 3 plan (D5) calls it `BMAD_GUARD_ENDPOINT`. See "Name of the endpoint variable" below.

What makes this hard:
- The AI agent runs as the same OS user as the developer, in the project folder, and can run shell commands. A secret in the project, or in the shell environment, is within its reach.
- A wrong `.gitignore` commits a secret that sits in the project.
- Phase 1 (open item 1, raised by Shameer on 25 Nov) already proposed a per-user file outside every repo.
- Phase 2 already denies the agent access to `~/.config/bmad-guard/**` (`PATH_GUARD_CONFIG` in `hook_wiring.py:56`; red-team case SENS-3). So the folder is reserved and protected now. Nothing is stored there yet.

Who sends events: the Claude Code hooks (L1) call `emit()` with `Config.for_project()`. The git hooks (L2) never send (`guard_hook_lib.py`, `emit()`; test `test_the_hook_never_sends_events`). So the credentials are read by L1 hook processes and by `guard_events.py flush`.

Constraints:
- No database credentials on laptops (ADR 0003).
- Logging never blocks a commit or a tool call.
- Python 3.10, standard library only.
- macOS and Linux only in Phase 3 (D10).
- The key must not reach the model's context, a prompt, a log or git.

## Options
### Option A — A per-user file `~/.config/bmad-guard/credentials`, mode 600, outside every repo
Written once by the setup script. Holds the endpoint, the developer id and the key.
- Pros:
  - Outside every repo and outside the project folder, so a wrong `.gitignore` cannot commit it.
  - The key is not in the process environment, so `printenv` and `/proc/<pid>/environ` do not show it.
  - Standard library only. The folder is already blocked for the agent (Phase 2).
  - The same convention as `~/.ssh` and `~/.aws`.
  - One file per developer, shared by all projects on the machine.
- Cons:
  - The key is in clear text on disk. Mode 600 keeps other users out, not the agent (same OS user).
  - The setup script must write it carefully (mode, folder, no key on the command line).
  - Not available on Windows with the same meaning (see below).
- Cost / effort: low.

### Option B — The OS keychain (macOS Keychain, Linux Secret Service)
- Pros:
  - The key is encrypted at rest.
  - macOS can ask the user before a new program reads the item.
  - Best protection against another process of the same user.
- Cons:
  - The standard library has no keychain API. We would call `security` (macOS) or `secret-tool` (Linux) as a subprocess. `secret-tool` is not installed by default, and a headless Linux machine or WSL has no Secret Service.
  - A hook is non-interactive. It cannot show or answer an access prompt. How it behaves without a prompt is not verified.
  - Two code paths, hard to test in CI.
  - The endpoint is not a secret, so it would still need a second place.
- Cost / effort: medium to high.

### Option C — A `.env` file in the project
- Pros: the most familiar place; no setup script work.
- Cons:
  - The agent works in that folder. It can read the key into its context.
  - A wrong `.gitignore` commits the key.
  - Every project needs its own copy of a per-developer key.
  - `.env` is on the deny list for a reason (`PATH_ENV_FILE`). We would add a secret to a file we tell the agent never to read.
- Cost / effort: low, but unsafe.

### Option D — Environment variables only (shell profile)
- Pros: nothing to write; standard for CI and tests; nothing on disk.
- Cons:
  - The key is in the environment of Claude Code and of every command the agent runs. `echo $BMAD_GUARD_KEY` and `printenv` show it. The path guard reads file paths, not variables.
  - The shell profile is a plain file in the home folder, usually with default permissions.
  - A GUI-started Claude Code may not see the profile at all.
  - Each developer must edit the profile by hand.
- Cost / effort: low, but weak for a laptop.

## Decision
We choose **Option A**, with the environment variables read first and overriding the file (Phase 3 plan, D5; Phase 1 open item 1). The keychain (Option B) stays a later option. Options C and D (as the normal way) are rejected.

### The file
- Path: `~/.config/bmad-guard/credentials`. Folder mode 700, file mode 600.
- Per user, not per project. Written once by the setup script (T3). The setup script never writes any secret into a project.
- Holds the endpoint (D5), the developer id and the key.
- Format: **INI**, read with `configparser`. Fake example:

```ini
[collector]
endpoint = https://guard-events.example.internal
user = dev-a
key = EXAMPLE-KEY-NOT-REAL
```

Why INI and not another format:
- `configparser` is in the standard library, supports comments and handles spaces, so we write no parser.
- TOML is not an option: `tomllib` needs Python 3.11 and we support 3.10 (`guard_config.py` has its own small TOML reader because of this).
- A flat `KEY=VALUE` file is just as easy, but it looks like a `.env` file and invites `source credentials`, which would put the key into the shell environment (Option D). A `[collector]` section avoids that.
- JSON has no comments and is easy to break by hand.

Reading rules (the reader must follow these):
- Use `configparser.ConfigParser(interpolation=None)`. With the default setting a `%` in a key raises an error.
- On a parse error, report the error type and the file name only. Never print the library's message: for a bad line it quotes the line, and that line may hold the key. (Checked with a fake line.)
- Ignore unknown keys. A missing section or key means "not set".

### Read order
Each value is taken from the environment first, then from the file (per value, not per file):

| Value | Environment variable | File key |
|---|---|---|
| Endpoint | `BMAD_GUARD_ENDPOINT` | `endpoint` |
| Developer id | `BMAD_GUARD_USER` | `user` |
| Key | `BMAD_GUARD_KEY` | `key` |

- No endpoint, from either place: events only queue. No warning (D5; ADR 0003: logging never blocks).
- An endpoint without a user or a key: events queue. Sending needs all three.
- The environment is for CI, tests and a one-off override. The docs say: do not put the key in the shell profile (Option D). The file is the normal way.
- A stale variable would hide a rotated key in the file. So the HTTP 401 hint names where the key came from (`environment` or the file path), never its value. Today it only says "check `BMAD_GUARD_KEY`" (`guard_events.py:176`).

### Permissions: what the sender does when the file is too open
**Proposal:** the sender refuses to use the file, keeps queueing, and says why. It does not repair the mode.
- Open the file with `O_NOFOLLOW` and check the open file (`fstat`), not the path, so the file cannot be swapped between the check and the read.
- Accept the file only if: it is a regular file, owned by the current user, and has no group or other bits (`mode & 0o077 == 0`). Also require that the folder is not writable by group or others.
- Otherwise: do not read it. Treat the credentials from the file as not set, so events queue. Nothing is lost.
- Say why in two places, not in every hook call (a hook runs on every tool call, so a message there would be spam):
  - `setup_guard.py --check` reports a missing or too open file and prints the fix (`chmod 600 ~/.config/bmad-guard/credentials`);
  - the `SessionStart` hook warns once per session, like `SETUP_INCOMPLETE` does for the other setup problems. A **missing** file is not a warning: with no endpoint, queue-only is the designed state.
  - `guard_events.py flush` returns the reason in its JSON `error` field.

Why refuse instead of "read it anyway and warn":
- It is what OpenSSH does with a private key that is too open. Developers know the rule.
- A file that others could read may already have been read. Using it quietly hides that. The right fix is to repair the mode and rotate the key.
- The cost is small: events wait in the queue, and they drain when the mode is fixed.

Why not "fix it automatically": a hook that changes the mode of a user's file is a surprise, and it hides the fact that the key was exposed.

### Keeping the agent out
Layers, as they exist after Phase 2:
1. `permissions.deny` has `Read(~/.config/bmad-guard/**)` (project wiring and managed settings). A Read deny also blocks Edit and Write on the path, and the Bash commands Claude Code can analyse (`cat`, `head`, redirects).
2. The `PreToolUse` path guard (`PATH_GUARD_CONFIG`) checks the Read, Edit, Write, Grep and Glob tools, and every word of a Bash command, after `~`, `$HOME` and `${HOME}` are expanded and symlinks resolved. Red-team case SENS-3 covers the Read tool.
3. The file is outside the project, so the document guard, the write gate and git never see it.
4. The hooks themselves read the file as normal processes. The deny rules apply to the model's tool calls, not to the hook processes, so nothing needs to be opened up.

Limits. We say them plainly:
- A command the shell guard cannot read can still open the file: `python3 -c "..."`, a script file, `node -e`, `awk`. This is the same known limit as for `~/.ssh` and the other guarded paths (`guard-rules.md`, "Bash is best effort").
- A path with a variable or a wildcard in the folder name is not matched. We ran the matcher on `~/.config/bmad-guard/$X` and `~/.config/bmad-*/x`: no hit. `~/.config/bmad-guard/*` is a hit.
- Any process of the same OS user can read a mode 600 file. Mode 600 protects against other users, not against the agent. The real limit is an OS sandbox or the keychain (Option B). Neither is in scope for Phase 3.
- The key in the **environment** is weaker than the file: `echo $BMAD_GUARD_KEY` is not blocked. This is one more reason the file is the default.

Why the blast radius is small:
- The key is per user. It can only send events to the collector. The collector's database user is insert-only (ADR 0003, spike test 5).
- A leaked key cannot read events, other users' data or anything in ClickHouse. The target hosting is VPN only (D4), so a key leaked outside the company network is of little use.
- What a leaked key **can** do is insert fake events under that developer's name, and noise. That weakens the audit trail for that one user until the key is rotated. We accept this for the pilot.

Red-team additions (T3, T8): keep SENS-3 green, and add a case for `cat` of the folder through Bash and one for Grep or Glob of the folder, so the claim above is tested for more than the Read tool.

### Issuing and rotating the key (D6)
- Shameer owns the stack, the keys and the rotation for the pilot. An IT contact is named before the move to real hosting.
- Issue: generate a random key, add its **bcrypt** hash to the collector's htpasswd file (the spike's salted SHA1 is too weak; ADR 0003 follow-up, T2), give the developer id and the key to the developer, and reload the collector.
- Delivery: once, by a channel that does not keep history (not chat, not e-mail). Decided (2026-11-26): a one-time share from the team password manager. Which tool Experion uses is to be confirmed by Shameer.
- The developer writes the file with the setup script. The key is read from the prompt or from one environment variable for that run, never from a command-line argument (shell history, `ps`).
- Rotate: issue a new key for the same developer id, replace the hash, reload the collector, and the developer re-runs the setup script. The user stamp keeps the same id, so history stays continuous. Until the new key is in the file, the sender gets HTTP 401 and keeps events in the queue (spike test 3 and 4). They drain later.
- Leave or compromise: delete the htpasswd line. Look at the events stamped with that id since the suspected date.
- Whether the collector accepts two hashes for one user (for an overlap) is not verified. The plan above does not need it.
- The stack's own passwords (ClickHouse, Grafana) are a different matter: they live on the stack host, not on laptops. The hosting plan (T2, `deploy/collector/HOSTING.md`) covers them.

### Name of the endpoint variable
The code reads `BMAD_GUARD_COLLECTOR` today (`guard_events.py:65`; also `docs/architecture.md:246`, `tests/test_guard_events.py:188`, `tests/test_pre_commit.py:600`, `spikes/collector/guard_events.py:48`). The plan and the file use "endpoint". `tests/test_pre_push.py:437` already uses `BMAD_GUARD_ENDPOINT`.

**Decision: rename to `BMAD_GUARD_ENDPOINT`.** Reasons:
- It matches D5 and the `endpoint` key in the file, so there is one name everywhere.
- Nothing sends by default (Phase 1), so almost nobody has set the old name. The migration is cheap.

Migration:
- The sender reads `BMAD_GUARD_ENDPOINT`. If it is empty, it reads `BMAD_GUARD_COLLECTOR` as a deprecated alias for one release. The docs only name the new variable.
- Update the tests, `docs/architecture.md` and the sender's docstring. The spike copy under `spikes/` is a frozen record and can keep its name; its run scripts need a check.
- Remove the alias in the next release.

Alternative (keep `BMAD_GUARD_COLLECTOR` and rename the plan's text) was considered. It saves one line of code but leaves D5, the file key and the variable with three different words.

### What changes in code (T3)
- `guard_events.py`: `Config.for_project()` reads each value from the environment, then from the file (rules above). The permission check and the source of the key (for the 401 hint) are part of it. No other module reads credentials.
- `setup_guard.py`: a mode that writes the file once (folder 700, file 600, created with the mode, not changed after). If the file exists and differs, it asks for `--force`, like other conflicts. Same values: no change (idempotent). `--check` reports a missing or too open file. It never writes into the project.
- `session_start.py`: warns on a too open file (rule above).
- Tests: key from the environment; from the file; from neither (queue only, no message); a wrong key (HTTP 401, event kept); no endpoint; a file with open permissions; a parse error that does not print the file content.
- Docs: the pilot pack (T0, updated in T8) says "do not export the key in your shell profile".

### Windows
Not in scope (D10). What would change later:
- The folder is `%APPDATA%\bmad-guard`, not `~/.config`. `PATH_GUARD_CONFIG` needs the Windows path too.
- There are no POSIX mode bits. Protection is the folder's ACL (user only). The standard library cannot read an ACL without `ctypes` or a call to `icacls`, so the "too open" check needs a different method.
- The Windows Credential Manager (Option B) fits better there than a file.
- `setup_guard.py --check` already warns that Windows is not supported (D10).

## Consequences
- Good:
  - The key sits outside every repo, outside the project folder and outside the process environment.
  - One file per developer, no per-project copies.
  - Standard library only. No new dependency.
  - A too open file is reported and never used. Events are never lost, because they queue.
  - The agent is kept out by two layers that already exist (SENS-3), and the limits are written down.
- Bad / risks:
  - The key is in clear text on disk. A same-user process that the shell guard cannot see can read it.
  - An environment variable overrides the file, and a key in the environment is easy for the agent to read. The docs must say so.
  - A rename of `BMAD_GUARD_COLLECTOR` touches code, tests and docs. A developer who used the old name keeps working for one release.
  - Rotation causes a short window of HTTP 401 and queued events.
- Follow-up work:
  - T3: the sender, the setup mode, `--check`, the `SessionStart` warning, the tests.
  - T2: bcrypt hashes and the rotation steps in `deploy/collector/`.
  - T8: red-team cases for the credential folder (Bash, Grep, Glob).
  - Later: keychain support (Option B) as a read path before the file; Windows (Phase 4 at the latest).
  - Open: the channel for handing a key to a developer; a rate or volume alert per user in Grafana, to see a leaked key being abused.

## Related
- Past ADRs: 0003 (event logging; one key per developer), 0006 (the agent works in the project folder and can read what the user can read), 0002 (fail modes: logging never blocks).
- Links: `docs/plans/phase-1.md` (open item 1); `docs/plans/phase-3.md` (D5, D6, D10, T1, T3); `module/skills/agent-project-logger/scripts/guard_events.py`; `module/skills/tool-guard/scripts/hook_wiring.py` (`SENSITIVE_PATHS`); `docs/managed-settings-rollout.md` (the deny rules); `spikes/collector/REPORT.md`.
