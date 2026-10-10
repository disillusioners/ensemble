# design.capture_mockup — Tool Spec (SPEC-ONLY; implementation is a follow-up commission)

**Status:** SPEC ONLY — pinned by phase 2 T12 per D7/Q7.2 (architecture-recommendation.md §2 :107). No tool implementation ships in this commission. Until it ships, `[VISUAL-QA-DEFERRED]` is the canonical review-page-handoff marker (schema `screenshot_capture` field + orchestrator Guideline (g)).

## Use case

Critic needs pixel-level visual QA of a shipped mockup page, not just HTML-source reading. Today the only capture path is the manual agent-browser → substrate recipe (migrated to sketcher's tools_note `## Capture Procedure` by phase 1 T7 per Q1.2 / architecture-recommendation.md §2 :109; pre-migration source was the `## Capture Procedure` section of designer's tools_note, header-delimited). A native tool turns that recipe into one deterministic call so the reviewer's verdict can cite a reproducible capture. The capture serves the critic's comparator path (`compare_images` against a reference) and the visual-drift audit surface.

## Input shape

- `source`: EITHER a routed frontend URL (rendered page; route must return 200) OR an `od.save` artifact path (the canonical mockup HTML under `.agents/shared/planning/{feature}/design/mockups/{page}.html`, served through the live-view route family).
- `feature`, `page`, `version`: provenance triple (version = spec SHA or WP id), stored in the sidecar exactly as the manual recipe does.
- `retention_class`: `normal` (default) | `protected` (baselines that must survive the sweep).
- `viewport`: optional width/height; deterministic default (1280×800) for replay.

## Output shape

- `image_id` + `ref_url` (`/api/tmp_images/<id>`) — the canonical wire form for downstream consumers (comparator, mid-flight re-digest).
- `view_link`-style URL mintable via the substrate's existing exposure path so the verdict can cite a human-openable view.
- Deterministic for replay: same inputs → same provenance tags; the sidecar (`feature/page/version/source_agent/retention_class`) matches the manual procedure's shape byte-for-byte so `image_list` filters hit either capture path.

## Integration path

- Tool registered under the `design` category (same family as `compare_images`), so the existing `design` allow entry exposes it without a new grant.
- `agents/designer/tools.allow` would gain the entry in a follow-up commission (orchestrator-side capture for audit sweeps); critic already resolves it via `design` the moment it ships.
- Implementation notes for the follow-up: headless-browser dependency (the `agent-browser` CLI + Chrome-for-Testing layout the manual recipe documents, including the `--no-sandbox` rootless-container constraint), POST-contract parity with the existing tmp_images ingestion (`{filename, content_type, data_base64}` only — provenance via sidecar), and a typed `error.code` for route-404 / sandbox-failure / store-full (507).
