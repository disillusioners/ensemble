# Phase 2 Implementation — Frontend Image Upload UI

## Date
2026-04-20

## Summary
Implemented Phase 2 of the Vision Support feature: complete frontend image upload UI for the Angular chat interface.

## Files Modified (12 files)
- `frontend/src/app/models/index.ts` — Added `images?: string[]` to Message, MessageCreate, MessageResponse
- `frontend/src/app/services/api.service.ts` — `sendMessage()` accepts optional `images` param
- `frontend/src/app/services/sse.service.ts` — `mapToMessage()` maps `images` field from SSE events
- `frontend/src/app/components/message-input/message-input.component.ts` — Full image state management
- `frontend/src/app/components/message-input/message-input.html` — File picker, preview strip, attach button, drag-drop
- `frontend/src/app/components/message-input/message-input.scss` — Image preview, attach button, drag-over styles
- `frontend/src/app/pages/chat/chat.component.ts` — `onSendMessage()` handles `MessagePayload`
- `frontend/src/app/components/chat-interface/chat-interface.html` — Image display in user messages
- `frontend/src/app/components/chat-interface/chat-interface.scss` — Message images styling
- `frontend/src/app/components/message-input/message-input.component.spec.ts` — Updated for MessagePayload
- `frontend/src/app/services/api.service.spec.ts` — Updated for images param

## Key Decisions
- `MessagePayload` interface exported from message-input.component.ts
- `EventEmitter<string>` → `EventEmitter<MessagePayload>` (breaking change, single consumer)
- Error recovery: images cleared on submit (accepted trade-off — user can re-attach if API fails)
- File preview uses base64 data URIs (no object URLs to clean up)

## Bugs Found & Fixed (Phase 2 Initial)
1. **SSE service accidentally destroyed** — Session T4 deleted most of sse.service.ts. Fixed by git checkout + manual images field addition.
2. **processFiles double-counting** — `currentImages.length + this.images().length` was `2x count`. Fixed to `this.images().length >= MAX_IMAGES`.
3. **chat-interface.html structure** — Images block placement needed to be outside hasMeaningfulContent @if block.

## Post-Review Fixes (Commit 6bdae97)
- Fix #1: Error recovery — moved input clearing to parent via `clearInput()` + ViewChild, only on API success
- Fix #2: Replaced all `document.querySelector` with `@ViewChild('fileInput')` + `isDragOver` signal
- Fix #3: Removed dead OnDestroy/ngOnDestroy
- Fix #4: Fixed Shift+Enter newline — extracted `onKeydownEnter()` method with shiftKey check
- Fix #5: Replaced all `alert()` with inline `validationError` signal + 4s timeout
- Fix #6: Added aria-labels to attach button and remove buttons
- Fix #7: SSE `mapToMessage` type safety — `data: any` → `data: Record<string, unknown>` + image validation
- Fix #8: MessagePayload.images made optional
- Fix #9: File size error shows actual MB value

## Build & Tests
- Angular build: PASSES
- Unit tests: 35/35 PASS
- Commit: f4a3a93

## Important Lesson
⚠️ When asking opencode sessions to make small targeted changes (like adding one field to mapToMessage), verify the FULL file afterward — sessions can accidentally delete large portions of code.
