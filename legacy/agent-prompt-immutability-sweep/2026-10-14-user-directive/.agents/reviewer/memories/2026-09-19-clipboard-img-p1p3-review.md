# 2026-09-19 — clipboard-image-chat Phase 1+3 external review (Deep-Review council)

Worktree `agents-ensemble-clipboard-img` @ `feature/clipboard-image-chat`. Council `review-council-clipboard-img-p1p3` (governor 07259f75; councilors 91c497a9/agentic + a399ad5c/coding; skill `code-review`). Read-only honored; only sanctioned tmp_image* pytest runs.

## Verdict
Phase 1 **APPROVED**, Phase 3 **APPROVED** (unanimous). 9/9 checklist PASS on both models. 167 tmp_image* tests green, reproduced by both councilors independently. 0 critical / 4 warnings / 9 suggestions — ALL OPTIONAL.

## Carry-forward to Phase-2 / Phase-4 FE reviews (user will send them next)
1. **3-form ref acceptance** (bare `<32hex>` / `tmpimg://` / `/api/tmp_images/<32hex>`) lives in the Phase-2 validator `daemon/models/message.py:30-51` (excluded commit `f93307a1`) — MUST be verified in the Phase-2 review, not assumed from Phase-1.
2. **Docstring 400→422 mismatch** (`daemon/routers/tmp_images.py:23-28` says `400 INVALID_REQUEST` but malformed base64/MIME/count/size actually return FastAPI 422 pydantic envelope, no `code` field) — fix BEFORE Phase-4 FE builds error-toasting on `code`.
3. **No real-`create_app` route-order test pin** — e2e (`tests/integration/test_tmp_images_e2e.py:63-109`) builds a hand-rolled mini-app; production registration order (tmp_images routes < SPA catch-all) was verified only by councilor runtime probes. Wiring correct today; no regression pin.
4. **Lifespan fail-soft gap** — `build_tmp_image_store` init unprotected (`daemon/api.py:263-269`); OSError at boot would crash daemon. Suggested: mirror WaitingChildrenWatchdog disable pattern (`api.py:916-922`).

## Lessons
- Requester checklist paraphrase drifted from plan source-of-truth once: checklist item 5 said sidecar `uploaded_at` "epoch-float"; `phase1-plan.md:217` specifies `now_utc_iso()` aware ISO-8601 — implementation follows the plan (correct). Always resolve contract detail against the plan doc, treat the requester summary as secondary; flag divergences instead of failing the item.
- Mid-review HEAD movement (`71e03918 → cd5ddbb2`, live Phase-2 dev): pin findings by `git diff --stat` over in-scope paths across the move (was empty) + re-run the test subset at end-state.
- tmp-image feature is filesystem-only: zero DB timestamp binds; sidecar `uploaded_at` = sanctioned aware TEXT form (`now_utc_iso()`); read path coerces to aware-UTC then epoch-float (`cleanup_service.py:471-479`).
- Kill-switch absence enforced by 5 independent pins (claim was 3): schema-absence, env-not-honored, source-grep, seam behavior, health wire-shape.
- Route-order verification trick both councilors used: runtime-probe real `create_app()` route table (tmp_images at indices 171–174, SPA catch-all 180) rather than trusting source reading.
