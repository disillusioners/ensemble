# Phase 2: Frontend Image Upload UI

## Objective
Extend the Angular chat interface with image upload capabilities: file picker, drag-drop, preview thumbnails with remove buttons, and updated API service to send images alongside text.

## Coupling
- **Depends on**: Phase 1 (API contract: `MessageCreate` now has `images?: string[]`, and `MessageResponse` now has `images?: string[]` for `getMessages()`)
- **Coupling type**: Loose — Phase 2 only needs the API contract and updated `MessageResponse` interface (Phase 1 task 15) returned by `getMessages()`
- **Shared files with other phases**: None (frontend-only files)
- **Shared APIs/interfaces**: `MessageCreate` schema — Phase 1 defines backend validation, Phase 2 must match the shape; `MessageResponse` — Phase 1 adds `images` field for history deserialization
- **Why this coupling**: Frontend must send images in the format backend expects. But the contract is simple (`images?: string[]`) and well-defined upfront.

## Context
- Previous phase completed: Phase 1 added `images` field to API, DB, and LLM routing
- Key decisions:
  - Max 3 images per message
  - Base64 data URI format for transport
  - Native FileReader API for file picker (no external npm packages)
  - Preview thumbnails with remove button
  - Drag-drop support on the input area
  - Enter to send still works (Shift+Enter for newline)
  - Send enabled when: text present OR images present (not both required)
  - Error recovery: if `sendMessage()` fails (e.g., 413 Payload Too Large), do NOT clear images from state. Restore images to the input area so the user can retry or remove some images.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update TypeScript models | Add `images?: string[]` to both `MessageResponse` (for API deserialization) and `Message` (for history/deserialization) interfaces. | `frontend/src/app/models/index.ts:66` |
| 2 | Update `ApiService.sendMessage()` | Change signature to accept `images?: string[]` alongside `content: string`. Send `{ content, images }` in POST body. | `frontend/src/app/services/api.service.ts:79` |
| 2b | Update `getMessages()` call in `chat.component.ts` | Handle `images` field in returned messages. Images from history must be passed to the message list for rendering. | `frontend/src/app/pages/chat/chat.component.ts` |
| 3 | Extend `MessageInputComponent` with image support | Add image state management (list of `FilePreview` objects with `id`, `dataUrl`, `name`). Add `MAX_IMAGES = 3`, `MAX_IMAGE_SIZE = 10 * 1024 * 1024`. Change `Output` to emit `{ content: string, images: string[] }` instead of just `string`. | `frontend/src/app/components/message-input/message-input.component.ts` |
| 4 | Add file picker button | Add an image upload button (📎 or camera icon) before the textarea in the input wrapper. Use hidden `<input type="file" accept="image/*" multiple>` triggered by button click. Use native FileReader API to convert to base64. | `frontend/src/app/components/message-input/message-input.html` |
| 5 | Add drag-drop support | Add `dragover`, `dragleave`, `drop` event handlers on the input container. Highlight border on drag-over. Accept only image files. Use native FileReader API. | `frontend/src/app/components/message-input/message-input.component.ts` |
| 6 | Add image preview thumbnails | Show thumbnail strip above textarea (or between textarea and send button) when images are attached. Each thumbnail: small preview (48×48 or 64×64), filename truncated, remove (×) button. | `frontend/src/app/components/message-input/message-input.html` |
| 7 | Add image validation | Validate: file type is image, max 3 files, max 10MB each. Show error toast/snackbar for validation failures. Reject invalid files immediately. | `frontend/src/app/components/message-input/message-input.component.ts` |
| 8 | Add `convertToBase64()` utility | Private method to read `File` as base64 data URI using `FileReader`. Return `Promise<string>`. | `frontend/src/app/components/message-input/message-input.component.ts` |
| 9 | Update `canSend` logic | Enable send button when: `message.trim()` is non-empty OR `images` list is non-empty. Previously only checked text. | `frontend/src/app/components/message-input/message-input.component.ts:32` |
| 10 | Update `handleSubmit()` | Clear images after send. Emit `{ content, images }` object instead of string. | `frontend/src/app/components/message-input/message-input.component.ts:36` |
| 11 | Update `ChatComponent.onSendMessage()` | Change handler to accept `{ content: string, images?: string[] }` instead of `string`. Pass both to `apiService.sendMessage()`. | `frontend/src/app/pages/chat/chat.component.ts:374` |
| 12 | Update `ChatInterfaceComponent` to display images in user messages | When a user message has `images` array, render image thumbnails below the text content. Use `<img>` tags with base64 `src`. Add "lightbox" click to view full-size (optional, nice-to-have). | `frontend/src/app/components/chat-interface/chat-interface.html` |
| 12b | Render images from message history in `ChatInterfaceComponent` | Images loaded via `getMessages()` should appear in the same style as real-time messages. Images should render as thumbnails below the text content. | `frontend/src/app/components/chat-interface/chat-interface.html` |
| 13 | Style the image preview strip | CSS for the thumbnail strip: flex row, gap, rounded corners, remove button overlay, drag-over highlight animation. Match existing dark theme. | `frontend/src/app/components/message-input/message-input.scss` |
| 14 | Handle memory cleanup | Call `URL.revokeObjectURL()` in component destroy if using object URLs. Since we convert to base64 immediately, this is less critical but good practice. | `frontend/src/app/components/message-input/message-input.component.ts` |
| 15 | Add error recovery for image loss on API failure | If `sendMessage()` fails (e.g., 413 Payload Too Large), do NOT clear images from state. Restore images to the input area so the user can retry or remove some images. This is the mitigation for the risk of images being lost on failure. | `frontend/src/app/pages/chat/chat.component.ts` |

## Key Files

### Modified Files
| File | Change |
|------|--------|
| `frontend/src/app/models/index.ts` | Add `images?: string[]` to `MessageCreate` and `Message` |
| `frontend/src/app/services/api.service.ts` | Update `sendMessage()` to accept and send images |
| `frontend/src/app/components/message-input/message-input.component.ts` | Image state, drag-drop, file picker, validation, emit type change |
| `frontend/src/app/components/message-input/message-input.html` | Add upload button, preview strip, drag-drop zone |
| `frontend/src/app/components/message-input/message-input.scss` | Style preview strip, remove buttons, drag highlight |
| `frontend/src/app/pages/chat/chat.component.ts` | Update `onSendMessage()` to handle images; update `getMessages()` to handle images field; add error recovery |
| `frontend/src/app/components/chat-interface/chat-interface.html` | Display images in user messages (real-time and history) |

### Test Updates
| File | Change |
|------|--------|
| `frontend/src/app/components/message-input/message-input.component.spec.ts` | Update tests for `MessagePayload` output type, image validation, image removal |
| `frontend/src/app/services/api.service.spec.ts` | Update tests for `sendMessage()` with images parameter |

## Implementation Notes

### Image Preview UX
```
┌─────────────────────────────────────────────┐
│ [📎]  Type your message...                   │
│                                              │
│ ┌──────┐ ┌──────┐ ┌──────┐                  │
│ │ img1 │ │ img2 │ │ img3 │  ← preview strip  │
│ │  ×   │ │  ×   │ │  ×   │                   │
│ └──────┘ └──────┘ └──────┘                   │
│                                     [Send]   │
└─────────────────────────────────────────────┘
```

### MessageInput Output Change (BREAKING for chat.component.ts)
```typescript
// Before:
@Output() sendMessage = new EventEmitter<string>();

// After:
export interface MessagePayload {
  content: string;
  images: string[];  // base64 data URIs
}
@Output() sendMessage = new EventEmitter<MessagePayload>();
```

### ApiService Signature Change
```typescript
// Before:
sendMessage(instanceId: string, content: string): Observable<MessageResponse>

// After:
sendMessage(instanceId: string, content: string, images?: string[]): Observable<MessageResponse>
```

### Image Display in Chat Messages
For user messages with images, render below text:
```html
<!-- In chat-interface.html, user message block -->
@if (msg.images?.length) {
  <div class="message-images">
    @for (img of msg.images; track $index) {
      <img [src]="img" class="message-image" (click)="openImagePreview(img)" />
    }
  </div>
}
```

## Constraints
- Max 3 images per message (enforced client-side)
- Max 10MB per image (enforced client-side; backend also validates)
- Only image MIME types accepted (`image/png`, `image/jpeg`, `image/gif`, `image/webp`)
- Base64 encoding happens client-side before sending using native FileReader API
- Must work in existing dark theme
- Enter to send, Shift+Enter for newline — unchanged
- No external dependencies (use native FileReader API, no npm packages needed)

## Deliverables
- [ ] `MessageCreate` TypeScript interface has `images?: string[]`
- [ ] `Message` TypeScript interface has `images?: string[]`
- [ ] `ApiService.sendMessage()` accepts and sends images
- [ ] Upload button (📎) visible in input area
- [ ] Drag-drop works on input container
- [ ] Image preview thumbnails shown with remove buttons
- [ ] Validation: max 3 images, max 10MB each, image-only types
- [ ] Send button enabled when text OR images present
- [ ] Images NOT cleared from state on API failure — recovered to input area
- [ ] Images cleared after successful send
- [ ] User messages with images render thumbnails in chat
- [ ] Images from message history (via `getMessages()`) render correctly after page refresh
- [ ] Dark theme styling consistent
- [ ] No memory leaks from object URLs
- [ ] `message-input.component.spec.ts` updated
- [ ] `api.service.spec.ts` updated
