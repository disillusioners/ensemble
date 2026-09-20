# 2026-09-19 — clipboard-image-chat plan review (Deep-Review council, REJECTED 2/2)

**Target**: `.agents/shared/planning/clipboard-image-chat/` (9 docs). Council: governor `e6609b89-1698-42f3-9dbc-01ff72a5ea11`, 2 councilors (models agentic + coding), skill `plan-review`.
**Verdict**: REJECTED — 3 critical, 6 optional, 0 scope-expansion. Rework path to APPROVED-WITH-NOTES is narrow.

## Lessons (transferable)

1. **Ratified contract renames are dispatch-fatal if not propagated into phase files.** decisions.md §2 (`image_refs`, `/api/tmp_images`, `data_base64`, `ref_url`) was never applied to phase4 (7× `/tmp-images`, 6× `image_b64`), phase5 (`TMP_IMAGE_REF_PREFIX='/tmp-images/'`), phase6 (4×), phase2 Task 3 regex (rejects canonical form). Wave-2 workers execute from PHASE files, not decisions.md. **Checklist addition**: before approving any plan, grep every contract-family token across ALL phase files and diff against the ratified contract section.
2. **Display channel vs agent channel are different code paths.** `images=` kwarg → HumanMessage image_url blocks → `use_vision_model` (graph.py:7047) = AGENT channel. GET /messages reads the CHECKPOINT via `serialize_message` (utils.py:113-137, image_url blocks only) — never reads `MessageQueue.images` = DISPLAY channel. Plans that conflate them produce false "reloads already work" claims. Any "thumbnail survives reload" requirement needs an explicit read-path task.
3. **Architect-ruling coverage check**: none of the 8 final rulings covered the checkpoint read path — the plan silently invented behavior there. When rulings are FINAL, the review must still ask "which load-bearing paths have NO ruling?"
4. **Council disagreement protocol works**: initial split (APPROVED-WITH-NOTES vs REJECTED); re-query made the approver re-trace code itself and flip with its own evidence. Multi-model convergence, not deference.
5. Anchor hygiene in the plan set was genuinely good (40+ verified) — which made the single non-propagated family (§2) the telling miss. Good aggregate hygiene ≠ per-family consistency.


## Cycle 2 (same day) — re-review verdict: APPROVED-WITH-NOTES (2/2)

Council `f6626ee9-3ec7-4032-8017-4489222762ba`, 2 councilors (agentic + coding), skill `plan-review`. All C1/C2/C3 verified LANDED against code (20+ anchors re-verified; facade chain = real seam; grep 0 non-canonical tokens in phase4/5/6). 0 critical; 4 🟡 pre-dispatch edits; 7 🟢; 0 scope-expansion. Both judgment calls ACCEPTED; decisions.md old-form exemption SOUND.

## Cycle-2 lessons (transferable)

6. **Renamed-field greps must include BARE short forms, not just old full tokens.** The §2 propagation grep (`tmp-images|image_b64|ref_id|endpoint`) showed 0 drift, yet phase1:162/:166/:170 still pinned response field `ref` (ratified = `ref_url`) — a 3-line cross-phase FE break risk invisible to the token set. **Checklist addition**: when verifying a contract rename, grep BOTH directions — old tokens AND every bare prefix/short variant of the NEW canonical names.
7. **Partially-reconciled sections read as contradictions to one auditor and closures to another.** phase5:117 contained both the R13-closure marker and a stale "NOT v1 (h4)" sentence. Partitioned leg-ownership surfaced it as a finding, not a verdict split. Keep leg boundaries by FILE SECTION, not just file.
8. **Baseline-vs-worktree drift is itself a review finding** (plan-overview:10 pinned 307db932=latest, worktree on 5f453b93): anchors must be re-grepped against the dispatch baseline at wave time — carry this as a dispatch note, not a plan defect.
9. **Per-claim LANDED/PARTIAL/NOT-LANDED table** is the right re-review artifact — it converts "trust the rework claims" into falsifiable rows and made the verdict cheap to adjudicate.
