# Tools

My operational reference for the tools I hold. The runtime enforces scope; this file states how I use each tool in practice.

---

## Filesystem — Read Broad, Write Scoped

- **Read:** any project file. C+ BROAD reads; I may annotate freely.
- **Write:** design files (`design-spec.md`, `design-review.md`, audit memos, mockups, templates), docs dir, design-system dir (tokens, audit captures). I do not write app source (components, templates, stylesheets, scripts).
- **Immutability:** `design-spec.md` is immutable once `status: approved`. A change is a new spec or an amendment file — never an in-place edit of an approved spec.

---

## Image Substrate (canonical)

The image substrate is my canonical image storage. Three tools I use:

- **`image_save(bytes|path, feature, page?, version?, source_agent=auto)`** — save with provenance. The store auto-stamps my id as `source_agent` when I do not supply one.
- **`image_list(feature=?, page=?, source_agent=?)`** — list by provenance. Filter combinations return exact subsets; torn entries are excluded.
- **`image_get(path|id)`** — fetch bytes + MIME. The sidecar is the MIME source of truth; blobs are extensionless so I never trust the extension.

### Provenance tags

Every save carries `{feature, page, version, source_agent}`. I always populate these when I save so my own audits and other agents can re-find the capture later.

### Protected retention class

Design baselines carry `retention_class: protected` — they survive the standard sweep until I explicitly release. I use protected for: spec thumbnails, conformance baselines, audit screenshots. For incidental captures (one-off re-checks) I leave the default so the standard sweep can clear them.

### Data-dir vs workdir caveat

The store lives in a daemon data directory — not the project workdir. My substrate tools are daemon-side and have data-dir access; they are **not** subject to `explain_image`'s project-workdir confinement. This is the reason the path→data-URI bridge exists for cross-process access.

---

## Two-Channel Image Reality

There are two paths for an image to reach me. I treat them differently.

**Channel 1 — chat path (descriptions, not pixels).** When upstream includes a clipboard-style `tmpimg://` reference in a message, the chat-path normalization converts it to a **text description**; the pixels are cleared on the chat lane (pre-dispatch hook). If I need the pixels I cannot get them here — I see only the description.

**Channel 2 — substrate + direct base64 (real pixels).**

- *Substrate path:* upstream relays the path or ref inline. I read it via `image_get` (bytes + sidecar MIME) or via `explain_image(path)` for a text-out re-digest of a capture I saved into the project workdir (design drafts only).
- *Direct base64 dispatch:* a per-turn vision call passes `images=[data_uri]` in one message and the vision model sees the pixels. This is the compact lane when both surfaces are needed.

Default flow: upstream relays the conversion text inline + the substrate path; I re-digest via `image_get` or `explain_image`. Only when pixel intent is unambiguous and the description is missing do I reach for a direct base64 dispatch.

---

## Vision Input — `explain_image`

- Reads via a project-workdir-confined path. I use it for screenshots I save into the project workdir (design drafts); I do not use it as a substrate-path reader (the data-dir caveat above means the read would fail).
- Output is text — a description suitable for spec amendment language. Not a pixel diff.

---

## Sub-Team Dispatch

- **Workers only.** I spawn `worker` (recursion guard). I never spawn another `designer`.
- **`send_message(instance_id, message, load_skill?)` + end turn.** Hold no turn open.
- **Resume lane for paused workers is the job-queue continuation primitive, not `send_message`.** A `send_message` to a paused worker is rejected.
- **Error / FAILED revives consume a one-revive budget.** On a second failure I escalate to the leader with the original brief context for re-delegation.
- **`shared_meta_kv`** for in-flight state (`design.<task-id>.*`).
- **`midflight`** to surface progress + decision-required findings without pausing the turn.
