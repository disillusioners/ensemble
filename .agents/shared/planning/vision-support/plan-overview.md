# Plan Overview: Vision Support (Image Attachments)

## Objective
Add image attachment support to the agents-ensemble chat interface, allowing users to send up to 3 images per message alongside text. The backend auto-detects images and routes to a vision-capable LLM model.

## Scope Assessment
**MEDIUM-LARGE** — ~17 files across backend and frontend (10 backend + 7 frontend). No architectural changes; extends existing patterns.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Backend: FastAPI + LangGraph + SQLModel + LangChain ChatOpenAI
- Frontend: Angular 21 standalone + NG-ZORRO
- LLM: OpenAI-compatible API (already handles multimodal response content)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Vision Pipeline | Add vision model config, extend API/DB models for images, route multimodal content to vision model. Phase 1 also includes fixing `serialize_message()` to preserve image data in checkpoints, and adding test tasks. | None | — | 2-3h |
| 2 | Frontend Image Upload UI | Extend TS models, update API service, extend message-input component with drag-drop image upload | Phase 1 | Loose — Phase 2 needs the API contract and updated `MessageResponse` model returned by `getMessages()` | 2-3h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **Loose** | Phase 2 depends on the API contract (`images?: string[]` field) that Phase 1 defines AND the updated `MessageResponse` model (Phase 1 task 15) returned by `getMessages()`. Once the contract is agreed, both could technically proceed in parallel. Frontend can be developed against the expected API shape before backend is deployed. |

**Scheduling recommendation:** Phase 1 first, then Phase 2. But Phase 2 can start as soon as Phase 1's API contract is settled (not necessarily deployed).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Base64 images are large → request size limits | med | Validate image size ≤10MB each, reject early with clear error message. Total payload ≤30MB. |
| Vision model not configured → fallback needed | low | If `model_vision` not set, fall back to main model with a warning log. |
| Existing text-only path regression | high | Vision code is gated by `if images` check; text-only path untouched. Add test for text-only send. |
| DB migration for `images` column | low | Additive nullable JSON column; no data loss risk. Migration follows existing naming pattern. |
| Image preview memory leak in frontend | low | Use Angular's built-in cleanup with `URL.revokeObjectURL()` on destroy. Limit preview count to 3. |
| Drag-drop conflicts with textarea | low | Use native file input with drag-drop handlers, positioned above textarea. |
| Checkpointer DB bloat: each checkpoint stores full message including base64 images | med | Ensure image size stays within limits (10MB each, max 3). Total ~40MB worst case per checkpoint. |
| Base64 size amplification: 10MB file → ~13.3MB base64 — 3 images = ~40MB total payload | med | Ensure reverse proxy (nginx) has `client_max_body_size 50M`. |
| Vision model routing: if vision model not configured, return clear error rather than silent fallback to text model | low | Return HTTP 400 with clear error message when images sent without vision model configured. |
| Page refresh image fidelity: images stored in DB must survive `getMessages()` load | med | Requires model + serialization fixes (Phase 1 tasks 13b, 15). |

## Success Criteria
- [ ] User can attach 1-3 images via click-to-upload or drag-drop
- [ ] User can send text-only, image-only, or text+image messages
- [ ] Backend stores images in DB `images` JSON column alongside `content`
- [ ] Backend constructs OpenAI multimodal content array when images present
- [ ] Backend uses `model_vision` config when images present, main model otherwise
- [ ] Text-only messages work exactly as before (backward compatible)
- [ ] Image previews show in input area with remove button
- [ ] Images are rejected if >10MB individually or >3 total
- [ ] Images in message history survive page refresh and appear in reloaded chat
- [ ] Error message shown if vision model not configured when images are sent
- [ ] Text-only messages work exactly as before (no regression)

## Tracking
- Created: 2025-04-23
- Last Updated: 2025-04-23
- Status: draft
