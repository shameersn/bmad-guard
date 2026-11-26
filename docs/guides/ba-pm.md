# Guide for BAs and PMs

| | |
|---|---|
| Ticket | GUARD-3, T0 (pilot pack) |
| For | BAs, PMs and architects who write the brief, PRD, architecture and stories |
| Before you read | [Install guide](install.md). On Windows you need Git for Windows and a working `python3`. Windows support is built but **not run on a real machine yet** ([windows-check.md](windows-check.md)); a Mac or Linux machine is still the safe choice for the pilot. |
| Formats | [ready-for-dev-markup.md](../ready-for-dev-markup.md) (questions, assumptions, answers) |
| All rules | [guard-rules.md](../../module/skills/tool-guard/references/guard-rules.md) |

## The idea in one minute

- A document that later work depends on (brief, PRD, architecture) must be **approved by a person** before the next one starts.
- **Warden** records the approval. **Sentry** checks it before the next step.
- A story goes to development only when its open questions are **counted and answered**. Nobody can win by deleting a question.
- The guards stop the **agent** from writing approvals and answers. You do that.

## What you see

| When | What |
|---|---|
| A workflow ends (brief, PRD, architecture) | Warden asks you two questions, one after the other. |
| The next workflow starts (PRD, architecture) | Sentry checks the document before it. You see one line (`approved by ...`) or a block with a reason. |
| Claude tries to change an approval field or remove a question | A block with `DOC_APPROVAL_FIELD` or `DOC_QUESTION_REMOVED`. |
| A story is not ready | A block with `GATE_READY_*` and the ids of the open items. |
| You commit | `pre-commit` may block a document that says `approved` without a Warden record. |

## What you do

### Approve a document (Warden)

Warden runs at the end of the brief, PRD and architecture workflows. It asks:

1. "Do you approve the document?" (after you read the review report).
2. "Please confirm you have read the whole document itself, not only the summary."

Answer each question on its own, with a clear yes. Silence, "ok" to something else, or "looks fine" is not approval. A "no" keeps the document in draft.

On a yes, Warden sets `approval_status: approved`, raises `version`, saves a hash of the text in `_bmad/custom/approval-state.json`, and commits the document and the state file together. You see who approved and when in that file and in the git log.

If the commit fails, the document is still approved. Warden tells you, and you commit the document and `_bmad/custom/approval-state.json` yourself.

### Change an approved document

An edit after approval makes the approval stale. Two things then happen:

- The workflow sets `approval_status` back to `draft` when you change an approved document in chat.
- **Sentry** compares the text with the hash. If it changed, Sentry sets the document back to `draft` and blocks the next step (`stale`).

You approve again with Warden. To change a document on purpose, ask Warden to **reopen** it. It asks one question and states that re-approval will be needed. The old approval is kept in the record, flagged `superseded`.

### Sentry results

| Sentry says | Meaning | What to do |
|---|---|---|
| `ok` | Approved and unchanged. | Go on. |
| `missing` | The document does not exist. | Create and approve it. |
| `unapproved` | No approval on record. | Approve it with Warden. |
| `superseded` | It was reopened. | Approve it again. |
| `stale` | It was edited after approval. | Read the change, approve again. |
| `state_unreadable` | `approval-state.json` is broken. | Ask the developer or Shameer. Do not repair it by hand. |

Sentry does not accept "proceed anyway". That is the policy, whatever the reason.

### Write questions and answers (ready-for-dev markup)

The gate counts what it can find, so use the fixed format. Full rules and an example: [ready-for-dev-markup.md](../ready-for-dev-markup.md).

```markdown
## Open questions

- Q-1: Can a patient book two slots at once?
  Resolved: No. One slot at a time. — Priya BA, 2026-11-20
- Q-2: How long is one slot?
```

- Every question has an id (`Q-1`, `Q-2`). **Never renumber, reuse or remove an id.**
- An answer is a `Resolved:` line that ends with your name and the date.
- An assumption is `[ASSUMPTION A-1: text]`. When confirmed, it becomes `[ASSUMPTION-RESOLVED A-1: text — name, date]`.
- A story is **ready** when: 0 open questions, 0 open assumptions, every answer has who and when, `confidence` is 80 or more, and the epic has an `epic-summary.md` with a Functional and a Technical part.
- `gate: generic` skips the gate. It is only for setup and chore stories. You set it by hand. A commit that adds or changes it gets a warning (`COMMIT_GATE_GENERIC`). Its use is reviewed in the pilot.
- The confidence score is the model's own opinion. It is one input. The counts are the hard check.

### Why you type the answers

An agent edit may not add, change or remove a `Resolved:` line or a resolved-assumption tag. That is `DOC_APPROVAL_FIELD`. So when the team answers a question, **you** type the `Resolved:` line (in your editor). Claude can help you word the answer in chat. This rule stays person-only in the pilot and is reviewed after the first false-block numbers.

## What the guards stop

| You or Claude try to | Result | Rule id |
|---|---|---|
| Write `approval_status: approved` (or `status: approved`), `approved_by` or `approved_at` into a document | Blocked for the agent. | `DOC_APPROVAL_FIELD` |
| Edit `approval-state.json` | Blocked for the agent. | `DOC_APPROVAL_STATE` |
| Delete an open question or open assumption | Blocked for the agent. A commit that drops one gets a warning. The gate counts a missing id as open. | `DOC_QUESTION_REMOVED`, `COMMIT_QUESTION_REMOVED`, `GATE_READY_DELETED_ITEMS` |
| Add, change or remove a `Resolved:` line | Blocked for the agent. | `DOC_APPROVAL_FIELD` |
| Commit a document that says `approved` by hand | Blocked at commit. | `COMMIT_APPROVAL_NO_ENTRY` |
| Commit an approved document that was edited after approval | Blocked at commit. | `COMMIT_APPROVAL_STALE` |
| Paste a secret or (in a HIPAA project) a patient identifier into a prompt | The prompt is blocked. It is not sent. | `PROMPT_SECRET`, `PROMPT_PHI` |

If you edit a status by hand in an editor, the commit check catches it. The only way is Warden.

## What the flow looks like

1. **Brief** → Voss reviews it → Warden approves (`doc_type` brief).
2. **PRD** → Sentry checks the brief → Warden approves.
3. **Architecture** → Sentry checks the PRD → Warden approves.
4. **Epics and stories** → confidence is scored. In the current overrides there is no Warden step for `epics.md`.
5. **Ready for development** → the ready-for-dev gate counts open items. The developer then plans and approves the **plan** with Warden (`bmad-build`).

The diagram is in [architecture.md](../architecture.md).

## You must not

- Hand-edit `approval_status`, `approved_by`, `approved_at` or `approval-state.json` to "speed things up".
- Delete a question that is hard to answer. Keep it open, or resolve it with a real answer.
- Use `gate: generic` to skip a story that has open questions.
- Put real patient or customer data in a prompt, a story or an example. Use fake values.
- Ask Claude to approve for you. Only Warden's two questions count.
- Say "proceed anyway" to a Sentry block.

## Limits to know

- Warden's two questions are enforced by the skill text, not by a hook. A model that runs the approval script by itself is not stopped. The audit is the `APPROVED` event, the `approved_by` entry and git history.
- Only the front matter of a document counts for `approval_status`. A "Status: Approved" row in the text is only text.
- Free-text patient data (names, diagnoses in a sentence) is not seen by the prompt check.
