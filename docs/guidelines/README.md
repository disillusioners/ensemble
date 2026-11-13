# Guidelines for agents-ensemble — the feature proposal

**Status:** CANONICAL — homed 2026-11-13; placement Emperor-ratified at
docs/guidelines/ (versioned with the system, promoted with each release);
lifecycle now live.

---

## 1. What a Guideline is

A **Guideline** is the knowledge document owed by any feature that ships in a
state an operator or agent cannot see from the code alone:

- **dormant** — merged and deployed but OFF by default (e.g. prompt-assent:
  `ENSEMBLE_PROMPT_ASSENT_ENABLED` default OFF, arming is the Emperor's act);
- **config-armed** — behavior flips on env keys the boot never surfaces in a
  UI (e.g. the memoir reflection lane's `ENSEMBLE_MEMOIR_REFLECTION_*`
  family, which is ENV-ONLY: a hand-added `config.yaml` block is silently
  ignored);
- **ceremony-gated** — the code is live but the ACT that turns it on is a
  human ceremony with a mandatory order (e.g. the live-view State A proxy
  rule: pin XFH → declare front → re-run drill, *do not reorder*);
- **non-obvious operational rules** — the facts that bite silently: a
  kill-switch whose absence means ON, a refusal that is a design outcome and
  not an error, a budget that silently clamps instead of raising.

A Guideline is **not** a runbook and does not replace one. A runbook walks a
procedure; a Guideline owns the standing *doctrine, knobs, boundaries, and
verification contract* of a shipped feature, and POINTS at the runbook,
critical note, and board item as its sources of truth.

The template (see `TEMPLATE.md`) is the distilled shape of ENSEMBLE-30
("prompt-assent enablement", hand-crafted by Emperor + Ari, 2026-11-12) —
the first guideline, written before this feature existed. That is the
pattern this feature systematizes.

## 2. When one is owed — the checklist

Write a Guideline when ANY of these is true at release time:

1. The feature has a kill-switch, enable-flag, or budget knob whose absence
   or garbage value resolves NON-obviously (fail-open vs fail-closed,
   clamp-not-raise, unset-means-ON).
2. Arming/activating the feature requires an ordered human act (ceremony,
   probe sequence, declare-then-verify) — anything where reordering causes
   a silent outage.
3. The feature's refusal codes or envelopes carry CONTRACT meaning an agent
   or operator must not misread (a rate refusal is by-design, not an error).
4. The feature interacts with a privileged tool category, an auth gate, or
   a proxy/deployment state (two-state compat, exemption lists).
5. The feature's operational rules are scattered across CHANGELOG +
   planning docs + runbook + critical notes with no single entry point.
6. A future operator (or a future Ari) could plausibly enable, tune, or
   debug the feature wrongly from the code alone.

If NONE apply, skip it — a guideline nobody needs is noise, and the CHANGELOG
entry is the record.

## 3. Where they live — placement ADOPTED: Option (i) — this directory IS docs/guidelines/ (three options, trade-offs)

### Option (i): agents-ensemble repo, `docs/guidelines/` (versioned-with-the-system)

- **For:** the guideline changes WHEN THE FEATURE CHANGES — same repo, same
  PR, same review, same release. A promote ships the guideline that matches
  the shipped binary; `git log` gives the history; the staging/release
  pipeline carries it for free. Sources-of-truth pointers (runbooks,
  migrations, tool files) resolve within one tree.
- **Against:** repo writes are lane-gated (docs ride feature lanes; a
  standalone guideline fix needs a lane or a docs commission); the repo
  carries the frozen blast-radius rules — adding a docs directory is cheap
  but every edit is a commit on `latest`.
- **Risk:** none structural. `docs/` is already the home of runbooks; this
  is the same pattern one level up in abstraction.

### Option (ii): the empyrea repo

- **For:** cross-project doctrine (if guidelines ever span several daemons
  or the harness), and it sits next to the operator's other standing docs.
- **Against:** the guideline would NOT version with the system — a v0.29-era
  guideline could silently describe a v0.31 binary; every agents-ensemble
  release would need a manual sync step, which is exactly the drift this
  feature exists to kill; pointers into `docs/runbooks/` and planning dirs
  become cross-repo URLs that rot.

### Option (iii): Plane-only (board items / docs on the PM ledger)

- **For:** zero repo friction; the PM ledger is already the human-facing
  project record (ENSEMBLE-30 itself lives there).
- **Against:** Plane items are a ledger, not a versioned doc store — no
  diffing, no review gate, no release coupling; board items are
  session-linked operational state, and the house rule already says
  "LINK, DON'T DUPLICATE" between board and repo artifacts. Long-form
  doctrine in a ticket system ages badly and escapes every code-side grep.

### The proposal — ADOPTED: Option (i)

**Adopt (i)** — `docs/guidelines/<feature-slug>.md` in the agents-ensemble
repo — with these properties:

- one file per feature, named `guideline-<feature-slug>.md` (the three
  drafts in this directory are the seed set);
- the file carries a **Status** line (draft / reviewed / canonical) and a
  **Source-of-truth pointers** section (runbook path, planning dir,
  critical note refs, board item id) so the ledger links TO the repo file,
  never duplicates it;
- the lifecycle below is enforced by convention at review time, not by
  machinery.

The drafts were homed to `docs/guidelines/` on 2026-11-13 through a normal
docs lane; the scratch dir (`/home/nea/Code/guidelines-drafts/`) is
superseded.

## 4. The lifecycle

```
draft ──► reviewed ──► canonical ──► updated-when-the-feature-changes
  │            │             │                    │
  │            │             │                    └─ every feature PR that
  │            │             │                       touches the feature's knobs/
  │            │             │                       gates/refusals updates the
  │            │             │                       guideline IN THE SAME LANE
  │            │             └─ a human (Emperor or delegate) ratifies the
  │            │                file; it becomes the entry point operators read
  │            └─ a second reader verifies every factual claim against the
  │               tree (env keys, commands, versions) — an unverified claim is
  │               marked UNVERIFIED inline, never smoothed over
  └─ anyone writes it, from the template, straight after the feature's
     tester sign-off while the facts are fresh
```

Rules of the lifecycle:

- **Draft** is written by the lane that shipped the feature (the coder/
  tester know the knobs); unverified facts are marked `UNVERIFIED:` inline.
- **Reviewed** means claim-by-claim verification against the tree at a
  pinned release tag — the reviewer re-derives, not re-reads.
- **Canonical** means it is THE document: runbooks and critical notes may
  hold detail, but the guideline is the index and the doctrine statement.
- **Updated-when-the-feature-changes** is the hard rule: a feature change
  that lands without its guideline update re-opens the guideline to
  `draft` — a stale canonical guideline is worse than none, because it
  reads as truth.
- Retirement: when a feature dies, its guideline moves to a `retired/`
  note or is deleted in the same lane that removes the feature — never
  left canonical.

## 5. The seed set

| File | Feature | Ships as |
|---|---|---|
| `guideline-memoir-reflection-lane.md` | Memoir Store v2 "Reflecting Mind" | v0.29.15 (config-armed, dormant-adjacent) |
| `guideline-live-views-passcode.md` | Live-views passcode + session-bypass gate | base v0.26-era; gate rewrite v0.29.7 |
| `guideline-notify-user-bounds.md` | `notify_user` bounds & doctrine | v0.26.x–v0.27.x era (ari-voice) |

Each is template-shaped and self-contained. All three were verified against
the v0.30.0-era tree (origin/latest @ `ee2b46d3b` / stage worktree
`ensemble-src-wt-stage-v0300`) except where marked `UNVERIFIED:` inline.
