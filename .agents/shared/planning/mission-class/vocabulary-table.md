# Two-Layer Vocabulary Table — Transport (Jobs) × Work (Missions)

**Date:** 2026-09-02 · Source: naming-worker collision audit (grep evidence pinned `latest @e676ddea`) + adjudication. Companion to `architecture-recommendation.md` §4.

---

## 1. The full table (post-M3 target state)

| Layer | Vocabulary | Owner | Source of truth |
|---|---|---|---|
| **Transport — mirror receipts** (`job_type='message'`) | `queued` · `active` · **`settled`** · `dead` | Job (admission) | `AdmissionState` derivation, per-kind dispatch |
| **Transport — task jobs** (`job_type='task'`) | `queued` · `active` · `completed` · `failed` · `cancelled` · `dead_letter` (derived, as today) | Job (admission) — **their terminal IS the outcome** (task job = its own mission) | `_derive_legacy_status` unchanged for task rows |
| **Work / Mission** (projection over instances) | `pending` · `processing` · `paused` · `completed` · `failed` · `cancelled` | Mission | `Instance.status` canonicalized (`_STATUS_CANONICAL_MAP`); `cancelled`←TERMINATED = true-terminal; `completed` = revivable **[SUPERSEDED by F7 — 2026-09-03: ALL canonical terminal values (`completed` / `cancelled` / `failed`) are revivable; no true-terminal value, no revive-class distinction (see `daemon/services/mission_resolver.py`)]** |
| **Instance** (existing, untouched) | 10-member `InstanceStatus` enum | Execution | `daemon/repositories/instance/models.py:20-31` |
| **Internal discriminator** (NOT wire) | `completed` · `failed` · `cancelled` · `aborted` · `watchover_terminated` · `orphan_retired` · `orphaned_no_task` · `pattern_f1_orphan` | `terminal_reason` column | unchanged; consumed by `_derive_legacy_status`; absorbed by Phase-4 StrEnum planning |

**Same-word-two-meanings residuals (documented, accepted):** `paused`/`completed`/`failed`/`cancelled` appear on both work and instance layers by design (canonical projection maps instance→work; the mission layer IS the instance's outcome vocabulary). The collision the user complained about — mirror-receipt `completed` reading as outcome — is **eliminated**: `settled` is disjoint from every work and instance value.

## 2. Why `settled` holds (pressure-test summary)

| Candidate | Verdict | Decisive reason |
|---|---|---|
| **`settled`** | ✅ **WINS** | Receipt-not-outcome (payments/ledgers: final clearing, outcome-agnostic); idiomatic read-aloud ("the mirror settled"); short, chip-renderable; disjoint value space |
| `handled` | ❌ | Generic verb-only; never a state value; no ledger weight |
| `delivered` | ❌ | Collides with chat-SSE bubble vocabulary (chat.component.ts:1460+) and job-card tooltip prose |
| `acknowledged` | ❌ | Engineering jargon; awkward chip noun; verb-used in blueprint tool output |
| `responded` | ❌ | Outcome-adjacent (implies the agent replied = drifts toward work) |
| `dispatched` | ❌ | Sender-POV; heavily used as recipient-facing prose in instance tools |
| `done_receipt` | ❌ | Cosmetic; fails read-aloud |

**Industry grounding:** SQS Received/Deleted (receipt ≠ outcome), Celery STARTED/SUCCESS (task = work), Temporal workflow-vs-activity split (two nouns for two layers — the closest structural analogue), HTTP 202 Accepted (accepted ≠ done), Kafka committed offset (transport position). `settled` matches the payments/ledger convention where settlement is finality OF THE EXCHANGE, not of the underlying business outcome.

**The half-claim (prerequisite fix):** FE already uses `mission-settled` as the CSS class for mission-TERMINAL chip styling (mission-liveness-chip.component.scss:28; job.model.ts:173/188/223/255/264; ~22 of 25 repo-wide `settled` hits are this styling chain; remaining 3 are prose). **M1 renames `mission-settled` → `mission-terminal`** (bounded: styling chain + spec comments, ~12-15 files; docs/job-task-system.md:909 "settled mission" → "terminal mission"). After the re-anchor, `settled` has exactly one owner: transport.

## 3. Read-aloud suite (all pass post-M3)

- "the job settled" / "your message settled" — receipt ✓
- "the mission completed" — outcome ✓
- "the mirror settled, the mission is still processing" — the exact Fix-C case, now vocabulary-level unambiguous ✓
- "the task job completed" — outcome (task job = its own mission) ✓
- "job dead" / "mission failed" / "mission cancelled (true-terminal)" ✓ **[SUPERSEDED by F7 — 2026-09-03: `cancelled` is revivable too; no terminal value is true-terminal]**

## 4. FE chip rendering (target)

| Row | Chip 1 (transport) | Chip 2 (work) |
|---|---|---|
| Mirror + live mission | `settled`/`active`/`queued` chip (transport palette) | `mission: processing` chip (live styling, sync icon) |
| Mirror + terminal mission | transport chip | `mission: completed|failed|cancelled` chip (**`mission-terminal`** styling — renamed from `mission-settled`) |
| Mirror `dead` | `dead` chip | mission chip if liveness available (orthogonal case, §8.2) |
| Task row | existing canonical chip (its status IS the outcome) | — |
| `mission_liveness=None` | receipt-only fallback (indistinguishable-by-design) | — |

Badge `missions:N` unchanged (counts live mission_liveness; identity de-dup by instance unaffected).

## 5. Migration & matcher breakage (M3 execution detail)

**Consumer classes:**
- **Class A — job_type-first consumers (Fix-C style):** no breakage.
- **Class B — FE raw-status switches:** update `job.model.ts` union/`isTerminalStatus`/color/icon switches + 5 components + jobs-page filter dropdown + e2e assertions (grep set: job.model.spec.ts:53/206/651+, jobs.component.ts:312/649).
- **Class C — daemon filters:** `VALID_STATUS_VALUES` (jobs_crud.py:45/500/527; constants.py:164/251), manager.py:5159/5588 — add `settled` (mirror-terminal), keep `completed` (task-terminal).
- **Class D — agent consumers:** migrated in M2 (tools) BEFORE M3 — at rename time, no in-repo agent reads mirror `completed` as outcome.
- **No edit:** SSE `'completed'` EVENT TYPE (job-sse.service.ts:104/109 — event vocabulary, distinct from status values); `terminal_reason` stored values; DLQ surface (orthogonal projection).

**Mechanics:** one-release version-gate (`api_version >= X` → `settled`, else legacy) → deprecation notes in docs (job-queue.md:831/930, api-reference.md:539/1227, usage.md:185/1445, job-task-system.md:116/908) → remove legacy projection next release. Derivation centralized in `derive_status(job_type, admission_state, terminal_reason)` — one line per kind; per-kind dispatch MANDATORY for future kinds (I3 amendment).

**DB disposition:** zero stored-value migration. `terminal_reason='completed'` remains the discriminator for done rows (read-model maps absorb the wire rename). Phase-4 StrEnum/versioning untouched; if Phase 4 later renames the discriminator itself, that is a separate constitutional event.
