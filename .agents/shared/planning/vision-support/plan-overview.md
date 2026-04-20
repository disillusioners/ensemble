# Plan Overview: Vision Support (Image Attachments)

## Objective
Add image attachment support to the agents-ensemble chat interface, allowing users to send up to 3 images per message alongside text. The backend auto-detects images and routes to a vision-capable LLM model.

## Scope Assessment
**MEDIUM** — Touches ~10 files across backend (config, API, DB, LLM routing) and frontend (models, API, component). No architectural changes; extends existing patterns.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Backend: FastAPI + LangGraph + SQLModel + LangChain ChatOpenAI
- Frontend: Angular 21 standalone + NG-ZORRO
- LLM: OpenAI-compatible API (already handles multimodal response content)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Vision Pipeline | Add vision model config, extend API/DB models for images, route multimodal content to vision model | None | — | 2-3h |
| 2 | Frontend Image Upload UI | Extend TS models, update API service, rebuild message-input component with drag-drop image upload | Phase 1 | loose | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **loose** | Phase 2 depends on the API contract (`images?: string[]` field) that Phase 1 defines. Once the contract is agreed, both could technically proceed in parallel. Frontend can be developed against the expected API shape before backend is deployed. |

**Scheduling recommendation:** Phase 1 first, then Phase 2. But Phase 2 can start as soon as Phase 1's API contract is settled (not necessarily deployed).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Base64 images are large → request size limits | med | Validate image size ≤10MB each, reject early with clear error message. Total payload ≤30MB. |
| Vision model not configured → fallback needed | low | If `model_vision` not set, fall back to main model with a warning log. |
| Existing text-only path regression | high | Vision code is gated by `if images` check; text-only path untouched. Add test for text-only send. |
| DB migration for `images` column | low | Additive nullable JSON column; no data loss risk. Migration follows existing naming pattern. |
| Image preview memory leak in frontend | low | Use Angular's built-in cleanup with `URL.revokeObjectURL()` on destroy. Limit preview count to 3. |
| Drag-drop conflicts with textarea | low | Use `nz-upload` component with drag-drop mode, positioned above textarea. |

## Success Criteria
- [ ] User can attach 1-3 images via click-to-upload or drag-drop
- [ ] User can send text-only, image-only, or text+image messages
- [ ] Backend stores images in DB `images` JSON column alongside `content`
- [ ] Backend constructs OpenAI multimodal content array when images present
- [ ] Backend uses `model_vision` config when images present, main model otherwise
- [ ] Text-only messages work exactly as before (backward compatible)
- [ ] Image previews show in input area with remove button
- [ ] Images are rejected if >10MB individually or >3 total

## Tracking
- Created: 2025-04-23
- Last Updated: 2025-04-23
- Status: draft
