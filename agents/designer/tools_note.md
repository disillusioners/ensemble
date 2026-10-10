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

## OpenDesign Plugin — the OD Lane Is Sketcher's

The OpenDesign plugin (`plugins/opendesign/`, tier-2 plugin subsystem first instance; slice ⑤ native tool family; upstream `open-design-mcp@0.16.1` retired at slice ⑦) is the **default** source for mockup artifacts — and its OD lane is **sketcher's, not mine**. Sketcher holds the OD toolset; I orchestrate. My side of the lane is content authorship in text: I compose the brief (`brief_answers` + `brand_spec`) from the spec sections in scope, hand each sketcher child a self-contained page-brief, and consume its generation envelope report. Sketcher executes the OD compose-brief step tool-internally as the formatter (the same core inputs for every page enforce multi-page consistency), generates **one self-contained HTML document per page, inline, at generation time**, lints it against the page ACs, and writes through to the canonical mockup path `.agents/shared/planning/{feature}/design/mockups/{page}.html` — the developer deliverable, daemon-independent, surviving an OD outage after spec freeze.

**Lane-start probe (my side).** One cheap `opendesign.list_systems` skill lookup at the very start of the mockup lane (Phase 4 Step 0 in workflow) answers "is sketcher's OD lane healthy?" before I dispatch. One lookup, no retry-storm. Probe result drives the lane decision and supplies the `fallback_reason` evidence when text is selected (skill not loaded → `tool-not-bound`; sketcher dispatch error → `call-error`; BYOK env vars missing → `daemon-unavailable`).

**Operational boundary.** I hold no OD generation tools and claim no direct lane. The OD lane's availability is the sketcher child's runtime fact; my probe plus the child's envelope is my evidence. When the probe fails (binding gap surfaces at dispatch time, not after a hand-authored HTML) or the child reports a generation failure, the text-native lane fires with a recorded `fallback_reason` — Cardinal #7 applies. I do not assume the lane is healthy; the probe confirms it before any spec authoring for mockups.

**Provenance vs deliverable.** The repo copy at `.agents/shared/planning/{feature}/design/mockups/{page}.html` (written at generation time by sketcher) is the developer deliverable. Per §4.1: text mockups never claim pixel fidelity; OD mockups claim what sketcher's lint verdict supports. OD-UI provenance from the previous MCP save tools is retired; the OD-UI reference is gone entirely.

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

- **Spawn `worker` + `sketcher` + `critic` only** (recursion guard). Never spawn another `designer`.
- **`send_message(instance_id, message, load_skill?)` + end turn.** Hold no turn open.
- **Report adjudication on evidence.** The scrutiny rule is process-shaped — see Report Handling in My Workflow for the canonical marker-conditioned directive. Short form: no envelope evidence, no parity row.
- **Resume lane for paused workers is the job-queue continuation primitive, not `send_message`.** A `send_message` to a paused worker is rejected.
- **Error / FAILED revives consume a one-revive budget.** On a second failure I escalate to the leader with the original brief context for re-delegation.
- **`shared_meta_kv`** for in-flight state (`design.<task-id>.*`).
- **`midflight`** to surface progress + decision-required findings without pausing the turn.
