# Plan Overview: clipboard-image-chat

> **Synthesis note:** this overview stitches and links the six worker-written phase plans and the
> decision register. Primary content lives in those files; nothing here re-derives them.
> Author of this file: Planner (dispatcher synthesis per aggregator write boundary).

| | |
|---|---|
| **Date** | 2026-09-19 · round-1 amendments + **round-2 rework** (post-REJECTED-review rulings) applied same day |
| **Branch** | `feature/clipboard-image-chat` @ `307db932` (checked out; plan dir untracked — commit is the caller's call) |
| **Status** | **Round-2 complete — amendments #1–#41 applied** (round-1 #1–#25 + round-2 #26–#41), A1–A11 test freeze list embedded in phase 2, §2.2 merge-gate grep checklist **GREEN** (independently verified: zero non-canonical tokens in phase files). Ready for re-review / implementation dispatch. Ruling sources: `architecture-recommendation.md` round-1 §1–12 + round-2 "Post-review rulings" (§ lines 343–434, supersedes round-1 ONLY where stated). **Dispatch note: docs baseline = 307db932; worktree now @ 5f453b93 — re-grep all load-bearing file:line anchors against latest HEAD at wave dispatch (line anchors drift; function-name cites preferred).** |
| **Workers** | plan-worker-backend (`ee83ba19`) · plan-worker-frontend (`789ff1c5`) · plan-worker-decisions (`a0b814de`) — each ran plan + round-1 + round-2 passes |
| **Research** | Two caller investigations + RAG sweep; all anchors re-verified by workers per pass (reviewer-fresh verification included in round 2) |

## Objective

Let a user **paste an image from clipboard** into the web chatbox. The image uploads to a
daemon-owned tmp store (`data/tmp_images/`), a **stable URL ref** is persisted for display, and the
**image-reader agent converts the image to text synchronously in POST, BEFORE the agent turn** —
the chat model is never switched to vision (guaranteed **by signature**, not convention: the
display channel never reaches `_build_message_content`). Files auto-clear after 30 days; loss is
acceptable because the text persists. The existing data-URI multimodal path (Discord = live
consumer) is untouched — strictly additive. Display works on **both legs** (durable + 202
injection) via one serializer union — h4-S1 also closes the pre-existing legacy 202 images-drop
defect as a bonus.

## Deliverable map (all files carry round-1 + round-2 Amendment logs)

| File | Owner | Content (post-round-2) |
|---|---|---|
| `phase1-plan.md` | backend worker | Store + POST/GET/DELETE `/api/tmp_images` + security headers + 1 GiB cap + debug gate + **probe with 4 checks incl. `quick`-alias resolution** |
| `phase2-plan.md` | backend worker | Conversion + **two-channel design** (Tasks 7/10/13/14/15/16) + `image_refs` facade chain REQUIRED + fail-fast + 90s + error-STRING collapse + **h4-S1** + A1–A11 freeze list + 3-form regex |
| `phase3-plan.md` | backend worker | Retention sweep — no kill-switch, 3600s, R10 checklist (normalized env names) |
| `phase4-plan.md` | frontend worker | Paste + upload-first on canonical contract + `image_refs` send plumbing + 300s timeout + conversion-wait UX + 4-type allowlist (O3) |
| `phase5-plan.md` | frontend worker | Canonical-prefix whitelist + load-bearing merge pin + **union fixtures + #40 reload smoke** |
| `phase6-plan.md` | frontend worker | Popup viewer + onerror fallback (canonical refs) |
| `decisions.md` | decisions worker | §2 contract + **§2.1 two-channel picture + §2.2 merge-gate checklist (canonical copy)** + all questions RULED/ANSWERED + 19-row register + §5 round-2 DAG note |
| `architecture-recommendation.md` | Architect | Round-1 rulings (§1–12) + round-2 post-review rulings (C1/C2/C3, #26–#41, freeze list) |

## Two-channel design (round-2 C1 — the structural fix; detail `decisions.md` §2.1)

```text
AGENT channel (content blocks → vision routing):   images kwarg ONLY — refs NEVER enter
DISPLAY channel (checkpoint sidecar → serialize):  additional_kwargs['image_refs'] on BOTH legs
  durable leg: enqueue_message_job(image_refs=…) → row column (audit) + _build_graph_input kwargs stamp
  202 leg:     set_injection(image_refs=…) → drain-site kwargs stamp (+ POST-time echo stamp)   [h4-S1]
  read side:   serialize_message unions kwargs refs into wire `images` (legacy blocks unchanged)
```

Facade-forwarding for `image_refs` across the 5-function chain is **REQUIRED** (amendment #27 —
both facade methods, `work_id_required` 5-test pattern + real-dispatch integration test).
Fallback if list-typed kwargs don't round-trip: JSON-string stamp (documented; kwargs→row-join
rejected). Chat sources are safe **by construction** (`IncomingMessage` has no `image_refs` field;
A8 static pin + `sources/base.py:25` guard comment).

## Phases

| # | Lane | Objective | Round-2 deltas | Depends on |
|---|---|---|---|---|
| 1 | Backend | Store + endpoints + **vision probe (4 checks, gates phase-2 dispatch)** | #36 quick-alias check; O6 race test deferred to 3 | — |
| 2 | Backend | Conversion + two-channel plumbing + h4-S1 | #26–#32, #38: Task 7 rewrite, Task 10 REQUIRED, Tasks 13–16 new, freeze list, 3-form regex | 1 |
| 3 | Backend | Retention sweep | O1 env names normalized (no functional change) | 1 (code); activation gated on 6 |
| 4 | FE | Paste + upload-first composer | C3 renames (46 tokens), `image_refs` plumbing, R8-exposure dep deleted, O3, #39 | 1 contract + **2 `image_refs` field** |
| 5 | FE | SSE + merge seams | Canonical prefix, union fixtures (#37b), merge pin stays load-bearing (#37a), #40 reload smoke | 4 |
| 6 | FE | Viewer + onerror | C3 renames only | 5 |

## Sequencing (round-2 DAG note — wave shape unchanged)

Wave 1 = phase 1 (probe = go/no-go). Wave 2 = **{2, 3, 4}** (≤3 at cap). Waves 3/4 = 5, 6.
Critical path 1→4→5→6. **h4-S1 is NOT in the phase-1 window** (depends on the conversion seam
minting canonical refs). If dispatch sizing demands, split 2 → 2a (conversion + durable-leg
channels) → 2b (h4-S1 202-leg), serial, still ≤3 in envelope. R10 deployment edge stands
(phase-6 FE dist before-or-with phase-3 activation).

## Rulings summary (all RULED — round-1 8 + round-2 3 criticals)

| Question | Ruling |
|---|---|
| Round-1 Q-f vision policy | FAIL-FAST 400 (5 exact test cases; phase-2 fail-open test superseded) |
| Round-1 contract | Ratified + 5 security amendments (nosniff/Content-Disposition, allowlist trim, debug gate, 1 GiB, guard-rail) |
| Round-1 placement / auth / coexistence / timeout / cleanup | Sync-in-POST · public-by-obscurity + guard-rails · full switch no knob · 90s + FE 300s · no kill-switch, 3600s |
| **Round-2 C1 channels** | Signature-separated two-channel design; overload REJECTED; facade-forwarding REQUIRED; row = audit, kwargs+union = display |
| **Round-2 C2 h4** | **IN v1 as h4-S1** (narrow `set_injection` kwarg; bonus: closes legacy 202 drop defect; escape = S4 re-scope, never widen S1) |
| **Round-2 C3 propagation** | 46-token rename pass applied; §2.2 checklist GREEN (zero non-canonical occurrences, independently verified); phase-4 R8-exposure dep deleted |

## Risk summary (post-round-2 — full register `decisions.md` §4)

- **R2** `model_vision`/`quick`-alias unverified → 🔴 until probe (4 checks, phase-1 window, gates phase-2 dispatch; fail-fast makes breakage loud).
- **R13 202 display gap → CLOSED in v1** by h4-S1 (merge pin remains independently load-bearing — echo `images: null` clobber predates + survives the union).
- **R18 ordering inversion** — accepted-risk v1 (block-send covers single-user-single-tab; follow-ups recorded, not built).
- **Residuals (honest, from freeze list):** list-typed kwargs round-trip (A1/A3 prove; JSON-string fallback) · `_resolve_model_override` for `quick` (probe de-risks) — both documented with fallbacks in phase 2.
- Cut items (do NOT reintroduce): 600s deadline · conversion-mode knob · cleanup kill-switch · POST-level concurrency guard · rate-limit framework · M2 prefix-strip overload · S2/S3/S4 h4 shapes.

## Test strategy (per-phase detail inside each plan)

**Phase-2 gate: A1–A11 freeze list pinned BEFORE tests are written** (A1 two-channel structural,
A2/A3 durable-leg display+row, A4 legacy byte-identical, A5 XOR, A6/A7 facade, A8 source-static,
A9 202 byte-identical ×4 consumers, A10 202 display parity, A11 merge pin). #40 reload smoke
(202 leg + legacy bonus). Merge-gate: §2.2 checklist (decisions.md) must pass at every phase exit —
`grep "tmp-images\|image_b64\|ref_id"` across phase files = zero hits (verified this pass).
FE specs per conventions; web-automation e2e (tester, later) hook points per phase.

## Success criteria

1. Paste/picker/drag → upload → ref → POST carries `image_refs`; agent receives TEXT with
   `use_vision_model=False` (A1) — never vision-routed.
2. Conversion failure → per-image placeholder; unset `model_vision` + refs → loud 400.
3. Thumbnails on BOTH legs incl. after reload (union); legacy data-URI 202 drop also closed (bonus).
4. Data-URI path byte-identical (Discord, resume, old FE bundles).
5. Hourly always-on sweep; R10 checklist honored at activation.

## Out of scope / hand-back

- Source-side ref adoption; Discord-SSE whitelist gap (host-allowlist follow-up).
- Keyboard-accessible thumbnails (accessibility follow-up).
- R19 CORS/bind posture → leader (proxy tripwire).
- **Implementation** → developer lane: wave 1 = phase 1 with the 4-check probe FIRST (verbatim
  `quick` in the spawn log = fail + escalate before phase-2 dispatch).

## Phase 3 retention note (shipped 2026-09-19)

`data/tmp_images/` is reaped by `TmpImageCleanupService` — ALWAYS-ON (no kill-switch; architect amendment #15), hourly cadence (deletion latency ≤ retention + interval), default retention **30 days**, age sourced from the sidecar's `uploaded_at` (falling back to file mtime). The only knobs are `SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS` (default 3600) and `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS` (default 30, floor 1 — the operator lever: set very large to effectively disable); both are resolved once at config-load and **require rebuild + restart to flip**. Sweep status surfaces via `GET /api/tmp_images` (gated) `cleanup` block — no `enabled` key. FE phase 6 note: images reaped after retention 404 → the FE `onerror` placeholder is the required companion.
