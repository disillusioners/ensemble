# Path→data-URI Bridge — Design (designer-agent, P1-WP10)

- **Date:** 2026-09-26
- **Author:** planner[v2] (phase-1 dispatch, P1-WP10)
- **Source of truth:** `.agents/shared/planning/designer-agent/architecture-recommendation.md` (D3 §5.1, D4 §6); `phase1-foundations.md` §4 P1-WP10 binding constraints table
- **Status:** Design — ready for P2 implementation arbitration
- **Implementation home:** **P2-WP3** (comparator facade `compare_images`); per `decisions.md` PD-11, this doc is the contract that P2 implements against — **deviations from this design are defects, reported not silently absorbed** (P2-WP3 AC per `phase2-parallel-builds.md`)
- **Scope of this doc:** requirements contract only; solution shape is P2-implementer freedom within the five constraint rows below.

---

## 0. Purpose

The `tmp_images` substrate lives at `<data_dir>/tmp_images/` (`daemon/services/tmp_image_store.py:115-117`) while `explain_image` local reads are project-workdir-confined (`daemon/tools/image_tools.py:429-444`). On deployed installs, `data_dir` is OUTSIDE any project workdir — agents cannot address substrate paths by hand.

The bridge turns a **substrate path** (the 32-hex id returned by `image_save` / `image_list`) into an ordered list of **base64 `data:` URIs** ready for `images=[a, b, …]` multimodal dispatch (`daemon/services/instance_messaging.py:113-128`; `tests/unit/test_vision_routing.py::TestMultipleImages`).

Day-1 consumer: P2's `compare_images(image_a, image_b, criteria?)` tool facade (arch-doc §6 `:226-230`). Secondary consumer: designer workflows that route user-supplied screenshots to vision calls without copy-paste base64 plumbing.

---

## 1. Substrate API contract (LANDED — do not re-shape)

From the coder report on commit `ff78674a` and the verified `tmp_image_store.py:480-557` API:

```python
# Store (daemon-side, data_dir-reachable):
store.open_full(image_id: str) -> TmpImageRecord | None
    # Same 404-gating as open() — missing blob or unreadable sidecar
    # raises TmpImageNotFound (mapped to HTTP 404 by the router).
    # Old sidecars parse cleanly: provenance=None, retention_class="normal".

store.list_records(
    *,
    feature: str | None = None,
    page: str | None = None,
    version: str | None = None,
    source_agent: str | None = None,
    retention_class: str | None = None,
) -> list[TmpImageRecord]
    # Sorted uploaded_at asc; torn sidecars EXCLUDED.
    # None on any key = no constraint on that key.

store.get_retention_class(image_id: str) -> str | None
    # "normal" | "protected" | None (missing entry).

# TmpImageRecord fields (dataclass, frozen):
#   image_id: str
#   content_type: str          # MIME — from the sidecar, NEVER guessed
#   size_bytes: int
#   uploaded_at: str           # ISO-8601 with offset
#   sha256_hex: str
#   provenance: dict | None    # {feature?, page?, version?, source_agent?}
#   retention_class: str       # "normal" | "protected"

# Agent-facing tools (already in the image category):
image_save(content_b64, content_type, feature?, page?, version?,
           source_agent?, retention_class="normal") -> JSON str
image_list(feature?, page?, version?, source_agent?,
           retention_class?) -> JSON list
image_get(image_id) -> JSON dict {"image_id", "content_type",
                                   "content_b64", "size_bytes",
                                   "sha256_hex", "provenance",
                                   "retention_class"}
```

The bridge consumes **either** `store.open_full(image_id)` directly (daemon-internal call site — see §3 for seam-placement recommendation) **or** the agent-facing `image_get(image_id)` which returns base64 bytes pre-encoded. Both paths must produce the same `data:` URI shape.

**Out of contract:** byte-level mutation, retention-class writes, sweep triggers, sidecar edits, magic-byte inference beyond the §4 fallback.

---

## 2. The five binding constraint rows (P1-WP10 table)

Each row below is the requirement; the implementation detail under "Contract" is the concrete behavior a P2 implementer MUST honor. Numbering is preserved for cross-document reference.

### Constraint row 1 — Daemon-side execution (data_dir reachability)

**Verified fact.** Store lives at `<data_dir>/tmp_images/` (`:115-117`); `explain_image` local reads are project-workdir-confined (`image_tools.py:429-444`); deployed installs have `data_dir` outside any project workdir.

**Requirement.** The bridge executes **daemon-side**. Agents never read substrate blobs by hand from a project workdir; agents never get a path that bypasses the store's 404 / torn-sidecar contract; agents never get raw filesystem access to `<data_dir>/tmp_images/`.

**Contract.**
- The bridge's caller (agent or another daemon-side helper) provides **one or more substrate image_ids** (32-hex).
- The bridge resolves each id through `store.open_full()` (or equivalent `image_get`-backed path) on the daemon side.
- The bridge returns **data URIs only** to agent-visible surfaces — never raw bytes, never blob paths, never sidecar paths.
- Any agent-side facade that wraps the bridge must surface the bridge's structured errors (see §5), not raw `FileNotFoundError` / `OSError`.

### Constraint row 2 — MIME from sidecar (never extension guessing)

**Verified fact.** Blobs are extensionless (`tmp_image_store.py:74-82`); sidecar carries the MIME as the source of truth. The existing `image_get` tool already enforces this (`image_tools.py:931-934`: "The MIME comes from the SIDECAR RECORD — blobs are extensionless…Never extension-guessed.").

**Requirement.** The `data:` URI prefix is built from `TmpImageRecord.content_type` as recorded in the sidecar. **Magic-byte sniffing is a SIDE-CAR-MISSING FALLBACK ONLY** — when the sidecar is present but `content_type` is empty or unparseable, the bridge MAY sniff the first few bytes of the blob to recover a MIME (PNG `\x89PNG\r\n\x1a\n` → `image/png`, JPEG `\xff\xd8\xff` → `image/jpeg`, etc.). Magic-byte sniffing **must not** run on every read — it is the fallback path, not the default.

**Contract.**
1. Sidecar present + `content_type` non-empty + parseable → use it verbatim.
2. Sidecar present + `content_type` empty / malformed → sniff magic bytes; emit a structured `mime_recovered` flag on the bridge result so callers can audit.
3. Sidecar missing / unreadable / torn → bridge treats this as 404 (Constraint row 3), NOT a magic-byte recovery opportunity. A blob without a sidecar is unauthenticated as to MIME.

### Constraint row 3 — 404 is authoritative (no optimistic caching, no retry storms)

**Verified fact.** `open()` 404s when blob or sidecar is missing (`:20-21`, `:491-492`); `list_records()` excludes torn sidecars (`:536-542`). The substrate treats 404 as the canonical "entry is gone" answer — it is not transient.

**Requirement.** The bridge returns a **structured unavailable-path error** to the comparator (or other consumer). Never a truncated image. Never a silent skip. Never a retry storm. **No optimistic caching of blob bytes** at the bridge layer — every call re-resolves the id through the store; freshness wins over latency.

**Contract.** On 404 (missing blob, missing/unreadable sidecar, blob present but sidecar torn, or any store-level `TmpImageNotFound`):

```json
{
  "error": "unavailable_path",
  "image_id": "<32 hex>",
  "reason": "blob_missing | sidecar_missing | sidecar_unreadable | sidecar_torn_json",
  "message": "<store error string>"
}
```

The consumer (comparator facade) MUST surface this as a structured per-image failure in its findings — never substitute a placeholder image, never retry the bridge call, never fall through to magic-byte recovery (per Constraint row 2).

### Constraint row 4 — Output contract + per-image size guard

**Verified fact.** Comparator passes `images=[a, b]` in one vision call; multi-image is tested at `instance_messaging.py:113-128` and `tests/unit/test_vision_routing.py::TestMultipleImages`. Store total cap is 1 GiB (`:109-117`).

**Requirement.** The bridge returns an **ordered list of `data:` URIs** matching the input order — position N in the input list maps to position N in the output list. The list is fed directly into the `images=` dispatch parameter.

**Per-image size guard.** Base64 inflation is ~4/3 (a 15 MiB PNG becomes ~20 MiB base64). The store cap of 1 GiB is the **total** budget — at the per-image level, a single image that consumes most of the cap would starve every other concurrent capture and would also blow past typical vision-call request-body limits (most providers cap at 20-100 MiB for the whole `images=[]` payload; a single 1 GiB image is infeasible regardless of the store cap).

**Concrete number: 20 MiB per image** (BEFORE base64 expansion).

| Rationale point | Math |
|---|---|
| Full-page screenshots at typical 1920×1080 PNG | 1–5 MiB |
| Retina / full-page captures | 5–15 MiB |
| Multi-image vision calls (typical 2 images) | 2 × 27 MiB (base64) ≈ 54 MiB total — comfortably under most provider image-payload caps |
| 20 MiB cap as a fraction of the 1 GiB store cap | ~2% — the guard caps request size without starving the store |
| Beyond 20 MiB | almost always a thumbnail gone wrong, a PDF page mistaken for an image, or a miscalibrated capture tool — surface as a structured error rather than encode |

**Contract.**
1. For each input image_id, fetch `TmpImageRecord.size_bytes` (cheap, from sidecar only).
2. If `size_bytes > 20 MiB` (20 × 1024 × 1024 = 21,474,840 bytes) → emit a structured `image_too_large` error for that position (NOT a 404, NOT a truncation):

   ```json
   {
     "error": "image_too_large",
     "image_id": "<32 hex>",
     "size_bytes": 25165824,
     "size_limit_bytes": 20971520,
     "message": "image exceeds per-image guard (20 MiB); capture likely misconfigured"
   }
   ```

3. If size OK → base64-encode the blob bytes; emit `"data:<content_type>;base64,<...>"`.
4. The bridge does NOT enforce a total-payload cap (that is the vision model's responsibility, plus the comparator's existing logic if any). The 20 MiB guard is per-image and exists to keep a single pathological capture from invalidating a multi-image dispatch.

### Constraint row 5 — Provenance passthrough

**Verified fact.** WP7 wires `provenance: {feature, page, version, source_agent}` into the sidecar (`:84-86`, `phase1-foundations.md` §4 P1-WP7); `TmpImageRecord.provenance` is the read shape.

**Requirement.** The bridge optionally attaches the resolved provenance to each output entry so comparator findings can cite `{feature, page, version}` (feeds the designer→tester handoff in arch-doc §4.5).

**Contract.**
- The bridge's per-image result carries the provenance mapping read from the sidecar, verbatim, alongside the `data:` URI:

  ```json
  {
    "image_id": "<32 hex>",
    "content_type": "image/png",
    "data_uri": "data:image/png;base64,iVBORw0KGgo...",
    "size_bytes": 12345,
    "provenance": {
      "feature": "checkout-flow",
      "page": "/cart",
      "version": "v1.2.0",
      "source_agent": "designer-1"
    },
    "retention_class": "protected"
  }
  ```

- Provenance may be `null` (clipboard-path entries written before WP7, or new entries without tags). That is fine — the bridge passes `null` through and the comparator cites whatever the sidecar recorded.
- The bridge does NOT synthesize provenance. It does NOT infer `feature`/`page` from filenames or path components. Sidecar is the only source.

---

## 3. Seam placement — options + recommendation

The P1-WP10 task says "store method or tool-layer helper with data_dir access — NOT an agent-side file read." Three options are viable; the recommendation is justified against the verified facts above.

### Option A — Store method on `TmpImageStore` (`daemon/services/tmp_image_store.py`)

Add `resolve_data_uris(image_ids: list[str]) -> list[dict]` directly to the store. Pros: store owns the I/O boundary; no second daemon-side helper; the 404 + torn-sidecar + size-guard contract lives next to the writer. Cons: the store grows a read-shaping concern (it's a writer today, plus an `open_full` reader); comparator facade would import the store directly, which couples a tool facade to a service module.

### Option B — Tool-layer helper (recommended)

Add a **new private module** (e.g. `daemon/services/tmp_image_bridge.py`) that depends on `TmpImageStore` and exposes `resolve_data_uris(image_ids: list[str]) -> list[dict]`. The comparator facade tool (P2-WP3) imports this module. Pros:
- Reads the existing dependency direction: `tools/` → `services/` (the store already sits in services).
- Keeps the store focused on read/write/evict; the bridge is a pure read-shaper with no new I/O.
- The same helper is reusable by any future tool facade that needs substrate paths → `images=[]` URIs (e.g. designer explain_image-adjacent flow, §5.2).
- Easy to unit-test in isolation: mock `TmpImageStore.open_full` and verify the 404 / size-guard / provenance passthrough contracts.

Cons: one new module (acceptable; matches the existing layering).

### Option C — Facade-internal (inside P2's `compare_images` tool module)

Do the path→URI resolution inside P2-WP3's tool module. Pros: zero new files. Cons: the bridge logic becomes hidden inside a single tool, untestable in isolation, and unusable by any other vision-dispatch path that emerges later (arch-doc §10 GAP-3 already names "substrate paths + pixels-at-dispatch" as the long-term shape).

### Recommendation — **Option B (tool-layer helper)**

Justification, against the verified facts:

1. **Layering reads as expected.** Tool-facade modules in this repo import from `daemon/services/`; the store lives in `daemon/services/`. Putting the bridge in `daemon/services/` (option B) preserves the dependency direction. Option A puts read-shaping on a writer module; option C puts it under `daemon/tools/` which is the wrong direction (tools depend on services, not the other way around).
2. **Reusability.** Designer workflows (UX-fix flow, arch-doc §4.3 (b)) will need the same path→URI conversion for explain_image-adjacent vision calls. Option C would force a copy-paste or a refactor.
3. **Testability.** Option B yields a single testable seam: `resolve_data_uris` with a mocked `TmpImageStore`. Options A and C scatter the logic across a writer + a facade with no clean test entry point.
4. **Failures are bridge failures, not tool failures.** A 404 or a size-guard hit becomes a structured dict the facade forwards verbatim — the comparator facade's job stays focused on `compare_images` semantics, not on image I/O.

Implementation contract for Option B:

```python
# daemon/services/tmp_image_bridge.py  (sketch only — implementation is P2's)
def resolve_data_uris(
    image_ids: list[str],
    *,
    store: TmpImageStore,
    size_limit_bytes: int = 20 * 1024 * 1024,
) -> list[dict]:
    """Per-image result preserves input order position-for-position."""
    ...
```

---

## 4. MIME resolution order (decision tree)

```
image_id arrives
   │
   ▼
store.open_full(image_id)
   │
   ├── TmpImageNotFound ─────────────► row 3: unavailable_path error
   │
   ▼
TmpImageRecord with content_type
   │
   ├── content_type is well-formed MIME (parseable by email.message)
   │      (image/<subtype>; charset / boundary params allowed)
   │      │
   │      └── use content_type verbatim → build "data:<type>;base64,..."
   │
   ├── content_type is empty or unparseable
   │      │
   │      └── MAGIC-BYTE FALLBACK: sniff first 8 bytes of the blob
   │            │
   │            ├── recognized signature → recovered MIME
   │            │     │
   │            │     └── build "data:<recovered>;base64,..." with
   │            │         {"mime_recovered": true} flag in result
   │            │
   │            └── unrecognized signature → row 3: unavailable_path
   │                  (reason: "unrecognized_mime")
   │
   └── sidecar parsed but content_type field absent
          (old sidecar from pre-WP7 era — extremely rare in practice)
          │
          └── treat same as "empty" branch above
```

The fallback path emits `mime_recovered: true` so the comparator (or any audit log) can flag "this entry's MIME was sniffed, not declared" — useful when triaging "why did vision misread this image?" tickets.

---

## 5. Worked examples

### 5.1 Example A — comparator pair (the day-1 consumer)

Scenario: P2's `compare_images` is called with two substrate paths to compare a capture against a baseline. Both entries exist; both are within the size limit; both have provenance.

**Input (from comparator facade):**

```python
image_a = "f3e1c7a9b4d2e8f0a1b2c3d4e5f60718"  # capture
image_b = "9d8c7b6a5e4f3210fedcba9876543210"  # baseline (retention_class=protected)
criteria = "checkout button must be visible at viewport-center, blue fill #2563EB"
```

**Bridge call (Option B, daemon-side helper):**

```python
results = resolve_data_uris(
    [image_a, image_b],
    store=tmp_image_store,
)
```

**Output (preserves input order):**

```json
[
  {
    "image_id": "f3e1c7a9b4d2e8f0a1b2c3d4e5f60718",
    "content_type": "image/png",
    "data_uri": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
    "size_bytes": 184523,
    "provenance": {
      "feature": "checkout-flow",
      "page": "/cart",
      "version": "v1.2.0",
      "source_agent": "playwright-capture-1"
    },
    "retention_class": "normal"
  },
  {
    "image_id": "9d8c7b6a5e4f3210fedcba9876543210",
    "content_type": "image/png",
    "data_uri": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
    "size_bytes": 312044,
    "provenance": {
      "feature": "checkout-flow",
      "page": "/cart",
      "version": "v1.1.0",
      "source_agent": "designer-1"
    },
    "retention_class": "protected"
  }
]
```

**Comparator facade action:** passes `images=[results[0]["data_uri"], results[1]["data_uri"]]` to the vision call in one dispatch (preserves order — capture first, baseline second). Findings cite `{feature: "checkout-flow", page: "/cart"}` from each result's provenance.

**Error scenario A1 — baseline was swept mid-comparison.** `open_full(image_b)` raises `TmpImageNotFound`. The bridge returns:

```json
[
  {"image_id": "f3e1c7a9...", "data_uri": "data:image/png;base64,...", ...},
  {"error": "unavailable_path", "image_id": "9d8c7b6a...",
   "reason": "sidecar_missing", "message": "tmp image not found: 9d8c7b6a..."}
]
```

Comparator surfaces per-image failure in findings (verdict=`conditional_pass` with `severity: critical` on the missing baseline), does not retry.

**Error scenario A2 — capture ballooned.** `open_full(image_a)` returns `size_bytes=25165824` (24 MiB). The bridge returns:

```json
[
  {"error": "image_too_large", "image_id": "f3e1c7a9...", "size_bytes": 25165824,
   "size_limit_bytes": 20971520, "message": "image exceeds per-image guard (20 MiB)"},
  {"image_id": "9d8c7b6a...", "data_uri": "data:image/png;base64,...", ...}
]
```

Comparator surfaces the size-guard finding (severity: major, hint: "capture misconfigured; expected ≤20 MiB") and proceeds with the available baseline if the criteria are still checkable one-sided. No retry.

### 5.2 Example B — designer explain_image-adjacent flow

Scenario: a UX-fix flow has the designer routing a user-supplied screenshot to a vision call for semantic analysis. The screenshot is already in the substrate (saved by the user-message ingestion path with `provenance.source_agent="user-via-clipboard"`).

**Input (from designer workflow):**

```python
user_supplied_id = "1a2b3c4d5e6f7081929394a5b6c7d8e9"
# designer wants: "explain what's visible in this screenshot; flag layout anomalies"
```

**Bridge call:**

```python
result = resolve_data_uris([user_supplied_id], store=tmp_image_store)
# Returns list of length 1; designer reads result[0].
```

**Output:**

```json
[{
  "image_id": "1a2b3c4d5e6f7081929394a5b6c7d8e9",
  "content_type": "image/png",
  "data_uri": "data:image/png;base64,iVBORw0KGgo...",
  "size_bytes": 245678,
  "provenance": {
    "feature": null,
    "page": null,
    "version": null,
    "source_agent": "user-via-clipboard"
  },
  "retention_class": "normal",
  "mime_recovered": false
}]
```

**Designer workflow action:** passes the single-element `images=[data_uri]` list to a vision call. Findings cite `source_agent: "user-via-clipboard"` in the ux-audit.md handoff so the leader knows the artifact came from the user, not from a capture run.

**Edge case B1 — clipboard path uses an extension.** Not relevant for the bridge; the substrate strips extensions on save (extensionless blobs per `tmp_image_store.py:74-82`). The user-facing clipboard path is upstream of the bridge.

---

## 6. Acceptance criteria for P2 implementer self-check

A P2 implementer can self-verify the bridge against this list. Any FAIL = deviation from the design; per PD-11, deviations are reported not silently absorbed.

| # | Check | Pass condition |
|---|---|---|
| A1 | Order preservation | `resolve_data_uris([a, b, c])` returns a list where `result[i]["image_id"] == input[i]` for every position |
| A2 | MIME from sidecar | A blob whose sidecar records `image/jpeg` produces `data:image/jpeg;base64,...`; a blob whose sidecar records `image/png` produces `data:image/png;base64,...` |
| A3 | Magic-byte fallback fires only on absent / empty sidecar-MIME | A blob with valid sidecar MIME is NEVER magic-sniffed; a blob with empty sidecar MIME is sniffed exactly once and `mime_recovered: true` is set |
| A4 | 404 is structured, not silent | Missing blob / missing sidecar / torn JSON sidecar → per-position `{error: "unavailable_path", reason: <one of four>, ...}`; comparator receives this verbatim |
| A5 | Size guard is per-image, pre-base64 | `size_bytes > 20 * 1024 * 1024` → per-position `{error: "image_too_large", size_limit_bytes: 20971520, ...}`; bridge does NOT partially encode and then error out |
| A6 | Provenance passthrough | `provenance` field in result equals `TmpImageRecord.provenance` byte-for-byte; `null` propagates as `null` |
| A7 | Retention class passthrough | `retention_class` field in result equals `TmpImageRecord.retention_class`; bridge does NOT silently evict protected entries |
| A8 | No caching | Two consecutive `resolve_data_uris([id])` calls each re-fetch via `store.open_full()`; no in-memory cache; no daemon-side memo |
| A9 | No retry | Bridge raises the first error it sees; does not loop on `TmpImageNotFound` |
| A10 | Daemon-side only | The bridge's public entry point is importable only from `daemon/` modules; no `python -c` or shell escape path exposes it to agents |
| A11 | Layering | `daemon/services/tmp_image_bridge.py` imports `daemon.services.tmp_image_store`; tool modules import the bridge; no reverse dependency |
| A12 | Existing 2-image test still passes | `tests/unit/test_vision_routing.py::TestMultipleImages` runs unmodified with bridge-resolved `data:` URIs in `images=[]` |

---

## 7. Out of scope for the bridge (P2 / P3 territory)

These are explicitly NOT part of the bridge contract:

- **GAP-3 `send_message` `images` param.** Per arch-doc §10 and `decisions.md` PD-9, the param extension is out of scope day-1; substrate paths + pixels-at-dispatch via `images=[data_uri]` is the long-term shape. The bridge is the pixels-at-dispatch helper.
- **Image-diff tooling.** Pixel-diff remains net-new-if-ever (arch-doc §5.3 GAP-5); the comparator covers semantic diff via vision.
- **Magic-byte tolerance for non-image MIME types.** The bridge recognizes PNG, JPEG, GIF, WebP signatures; anything else routes to `unrecognized_mime → unavailable_path`. PDF / SVG / video are out of scope — different substrate needed.
- **Compression / re-encoding.** The bridge passes bytes through unmodified. Resizing / format conversion (if ever needed) is a separate tool.
- **Cross-store federation.** One daemon, one store. Multi-host substrate replication is out of scope.

---

## 8. References

- Arch-doc §5.1 (`tmp_images` substrate upgrades)
- Arch-doc §5.3 GAP-5 (vision-only diff)
- Arch-doc §6 D4 comparator I/O contract (`:226-230`)
- Arch-doc §10 GAP-3 (send_message `images` param, out of scope)
- `phase1-foundations.md` §0 "MAY ASSUME" (store facts); §4 P1-WP7 / WP8 / WP9 (substrate upgrades); §4 P1-WP10 (binding constraints table — this doc's source)
- `phase2-parallel-builds.md` §3 P2-WP3 (comparator facade implementation site)
- `decisions.md` PD-11 (P1 design / P2 build split)
- `daemon/services/tmp_image_store.py:74-82, :115-117, :278-377, :480-557`
- `daemon/tools/image_tools.py:429-444, :616-1006` (workdir confinement; image_save/list/get)
- `daemon/services/instance_messaging.py:113-128` (`_build_message_content`, multi-image)
- `tests/unit/test_vision_routing.py::TestMultipleImages` (consumer regression)
