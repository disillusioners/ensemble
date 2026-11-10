# Doctrine — prompt-assent

> The Emperor's Word, relayed. The working path is conversation; the FE-UI is a deeper view.

**FE-UI is a deeper view; the working path is conversation. The Emperor's word, relayed by C2 with provenance, is a first-class approval lane.**

---

## Status

| Field | Value |
|---|---|
| Status | **Ratified** (commission input, carried verbatim) |
| Commission | `prompt-assent` — the conversational approval lane (*The Emperor's Word / Assent*) |
| Date | 2026-11-10 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-prompt-assent` (branch `feature/prompt-assent`, base `0efdf106f`) |
| Referenced by | `plan-overview.md`, `decisions.md`, `risks.md`, `phase1-plan.md` … `phase4-plan.md` |

## How the rest of the planning set reads this doctrine

The doctrine is the **north star**, not a spec. Where an implementation detail and the doctrine
disagree, the doctrine wins and the plan must be revised. Three consequences are load-bearing
throughout this planning set:

1. **"FE-UI is a deeper view"** — the FE approval lane (`POST /api/prompts/versions/{id}/approve`,
   `daemon/routers/prompts.py:616-660`) is NOT the primitive; it is one *view* of an approval
   capability that also exists as conversation. This is why the plan treats the FE routes as
   **unchanged** and adds the conversational path **alongside** them (see `decisions.md` D-7 and
   the inline decision "router unchanged — acceptance at service/repo level in-process").
2. **"the working path is conversation"** — the *primary* way the Emperor approves is a chat
   turn. A design that works only when the FE is open is a violation of the doctrine even if it
   is functionally correct.
3. **"with provenance"** — an approval is not a boolean flip. It is a decision whose evidence
   chain (who, from where, in which message, relayed by which agent, when) is reconstructable
   from the ledger. Every design point in this set is subordinate to this clause; see
   `risks.md` §"Audit tampering".

## What the doctrine does NOT say (deliberate scope of the statement)

- It does **not** waive the F4 rule ("agents cannot self-approve",
  `daemon/repositories/prompt_version/repository.py:1560-1565`, `:1594-1599`). It says the
  *human turn + token* is the authority and the relay agent only carries it. The plan honours
  this by construction — see `decisions.md` D-8.
- It does **not** say the FE lane may be removed. "Deeper view" ⇒ *kept and unchanged*.
- It does **not** name a chat platform. Source scope is a decision (D-5), not a doctrine clause.
