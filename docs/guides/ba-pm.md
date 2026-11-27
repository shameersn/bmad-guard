# Guide for BAs and PMs

| | |
|---|---|
| Ticket | GUARD-3, T0 (pilot pack) |
| For | BAs, PMs, POs and architects who write and approve the brief, PRD, FRDs, architecture and stories |
| Before you read | [Install guide](install.md). Windows is not supported yet: use a Mac or Linux machine for the pilot. |
| Formats | [ready-for-dev-markup.md](../ready-for-dev-markup.md) (questions, assumptions, answers) |
| All rules | [guard-rules.md](../../module/skills/tool-guard/references/guard-rules.md) |

## The idea in one minute

- A document that later work depends on (brief, PRD, FRD, architecture) must be **approved by a person** before the next one starts.
- **Warden** records the approval. **Sentry** checks it before the next step.
- A story goes to development only when its open questions are **counted and answered**. Nobody can win by deleting a question.
- The guards stop the **agent** from writing approvals and answers. You do that.

## What you see

| When | What |
|---|---|
| A workflow ends (brief, PRD, FRD, architecture) | Warden asks you two questions, one after the other. |
| The next workflow starts (PRD, FRD, architecture, stories) | Sentry checks the document before it. You see one line (`approved by ...`) or a block with a reason. |
| Claude tries to change an approval field or remove a question | A block with `DOC_APPROVAL_FIELD` or `DOC_QUESTION_REMOVED`. |
| A story is not ready | A block with `GATE_READY_*` and the ids of the open items. |
| You commit | `pre-commit` may block a document that says `approved` without a Warden record. |

## What you do

### Approve a document (Warden)

Warden runs at the end of the brief, PRD, FRD and architecture workflows. Approval is the manual step: the PO (or the person accountable) answers Warden's questions. Nothing else needs to be signed outside Claude. Warden asks:

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
- A story is **ready** when: 0 open questions, 0 open assumptions, every answer has who and when, `confidence` is 80 or more, and the epic has an `epic-summary.md` with a Functional and a Technical part. When the story has `feature: F-<nnn>`, that feature's FRD must also be approved with nothing open, and the FRD counts as the Functional part.
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

## The BA workflow, step by step

The Experion BA workflow (AIDLC v1.0) has 19 steps. The guards support the steps an AI agent owns, and
check the human approval before the next AI step starts. BA = drafts and runs the AI steps; PO = the only
approver; Customer = the external client.

| # | Step | Owner | Agent / skill | What the guards do |
|---|---|---|---|---|
| 1 | Brief v1 from client inputs | AI | Mary (`bmad-agent-analyst`, menu CB → `bmad-product-brief`) | Confidence score. |
| 2 | Review Brief v1 | AI | Voss (`experion-brief-review`), runs on its own at the end of step 1 | Writes `review-report.md`. |
| 3 | Corrections → Brief v2 | BA | You, in chat or your editor | An edit to an approved brief sets it back to draft. |
| 4 | PO approves the Brief | PO | Warden (`agent-approval-grant`) | Two confirmations, version raised, hash recorded. |
| 5 | Client-facing Brief summary | AI | Not built yet (optional, later) | — |
| 6-7 | Share the summary; customer acknowledges | BA/PO, Customer | Outside Claude | Not checked. The PRD needs only the internal approval (step 4). |
| 8 | PRD (scope, features, NFRs) | AI | John (`bmad-agent-pm`, menu PRD → `bmad-prd`) | Sentry checks the brief first. Each feature gets an id `F-001`…, each NFR `NFR-001`… with "Applies to". |
| 9 | Review the PRD | AI | `bmad-prd` Validate (writes `review-rubric.md`) | Runs at Finalize. |
| 10 | Corrections → PRD v2 | BA | You | An edit to an approved PRD sets it back to draft. |
| 11 | PO approves the PRD | PO | Warden | As step 4. |
| 12 | Feasibility and estimation | Eng/PO | Outside Claude (Winston, `bmad-architecture`, can help) | Not checked. |
| 13 | PRD features to ADO | BA/Tooling | Outside Claude | Not checked. The FRD keeps the ADO id if you give it. |
| 14 | One FRD per PRD feature | AI | Fenn (`experion-frd`) | Sentry checks the PRD first. One run per feature. |
| 15 | Review each FRD | AI | Iris (`experion-frd-review`), runs on its own at the end of step 14 | Writes `frd-review.md`. |
| 16 | Corrections → FRD v2 | BA | You | An edit to an approved FRD sets it back to draft. |
| 17 | PO approves the FRD; disagreements go to the PO | PO | Warden | Iris lists findings you disputed under "For the Product Owner". |
| 18 | Approved FRDs to ADO as stories | BA/Tooling | Outside Claude. For development in BMAD: John, menu CE → `bmad-create-epics-and-stories` | Sentry checks each FRD; every story carries `feature: F-<nnn>`. |
| 19 | Hand-off to delivery | PO | — | The ready-for-dev gate blocks any story whose FRD is not approved or has open items. |

**Change requests.** A change after approval goes back to the draft step (3, 10 or 16). Change the
document in chat; the workflow sets it to draft and Sentry blocks the next step until Warden approves
the new version. The old approval stays in `approval-state.json`, flagged `superseded`. Versions are
never overwritten.

## FRDs (steps 14-17)

- **One FRD per PRD feature.** Ask Fenn for one feature (`F-001`). It writes
  `_bmad-output/planning-artifacts/frds/F-001/frd.md` in the Experion 13-section format. The PRD's
  features must carry `F-<nnn>` ids; if they do not, Fenn stops and asks you to add them with `bmad-prd`.
- **What Fenn takes from the PRD.** Only this feature's scope. Requirements get permanent ids
  `FR-F001-01`, `FR-F001-02`… A removed requirement is marked *Withdrawn*, never deleted or renumbered.
  Every PRD NFR whose "Applies to" is "All" or this feature is copied into Section 8.1 with the PRD
  target unchanged. Anything outside the feature, or unknown, becomes a question `Q-<n>` in Section 12.
- **Figma and ADO are optional.** "Not linked" is fine and blocks nothing.
- **Review.** Iris runs after Fenn and writes `frd-review.md` beside the FRD, with findings `R-1`,
  `R-2`… If you disagree with a finding, write under it in `frd-review.md`:
  `Disputed: <why> — <your name>, <YYYY-MM-DD>`. On the next review Iris keeps the finding, marks it
  **Disputed** and lists it under **For the Product Owner**. The PO decides.
- **Approval.** Fenn asks once whether the FRD is ready. On a yes, Warden runs with doc_type
  `frd-F-001`. Answer Fenn's open questions first: an FRD with an open question blocks **every story of
  that feature** at the ready-for-dev gate, even when it is approved.
- **Your own template.** Put a copy of the template at `_bmad/custom/templates/frd-template.md` and edit
  it. Fenn uses it instead of the built-in one and says so. Keep Section 12 a `- Q-<n>:` list: the gate
  counts only that format.

## What the flow looks like in BMAD

1. **Brief** → Voss reviews it → Warden approves (`doc_type` brief).
2. **PRD** → Sentry checks the brief → Warden approves (`prd`).
3. **FRDs** → Sentry checks the PRD → Fenn writes one per feature → Iris reviews → Warden approves (`frd-F-<nnn>`).
4. **Architecture** → Sentry checks the PRD → Warden approves.
5. **Epics and stories** → Sentry checks each FRD → stories carry `feature: F-<nnn>` and the `FR` ids they deliver. There is no Warden step for `epics.md`.
6. **Ready for development** → the ready-for-dev gate counts open items in the story and its FRD. The developer then plans and approves the **plan** with Warden (`bmad-build`).

The diagram is in [architecture.md](../architecture.md).

## You must not

- Hand-edit `approval_status`, `approved_by`, `approved_at` or `approval-state.json` to "speed things up".
- Delete a question that is hard to answer. Keep it open, or resolve it with a real answer.
- Remove `feature: F-<nnn>` from a story, or delete an FRD question, to get past the gate.
- Use `gate: generic` to skip a story that has open questions.
- Put real patient or customer data in a prompt, a story or an example. Use fake values.
- Ask Claude to approve for you. Only Warden's two questions count.
- Say "proceed anyway" to a Sentry block.

## Limits to know

- Warden's two questions are enforced by the skill text, not by a hook. A model that runs the approval script by itself is not stopped. The audit is the `APPROVED` event, the `approved_by` entry and git history.
- Only the front matter of a document counts for `approval_status`. A "Status: Approved" row in the text is only text.
- Free-text patient data (names, diagnoses in a sentence) is not seen by the prompt check.
