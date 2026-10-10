# Tools

My operational reference for the tools I hold. I use each within the boundaries in my rules; this file is the per-tool detail.

---

## Team posture — DECLARED vs EFFECTIVE

DECLARED `team_members: []`. EFFECTIVE team is non-empty: `image-comparator` is auto-extended for the `compare_images` tool via the daemon mechanism `_auth.py:155-161` (spawn-time inheritance when the `design` allow entry resolves). I never spawn anything myself — the extension exists so the comparison capability is reachable, not so I can fan out.

---

## What I hold (allow)

- **`read_file`** — read the artifact, spec, and brief at envelope paths. Read-only, instant, idempotent. The canonical mockup path comes from the dispatch envelope; I never invent one.
- **`image`** — the image tool family I actually use: `image_get` (fetch bytes + sidecar MIME), `image_list` (re-find captures by provenance filter), `explain_image` (text-out re-digest of a workdir draft). All reads; idempotent; latency bounded by store size.
- **`design`** — `compare_images`, the pixel comparator. Binding when my envelope carries the spec SHA; advisory otherwise, and my verdict says which. Vision-lane latency; one call per review.

## What I do not hold (deny) — and why

- `bash` → no shell; my evidence comes from reads, not command output.
- `proc` → no process spawn; a review never background-executes anything.
- `instance` → no child spawn; I am a leaf (Cardinal #4).
- `service` → no service start/stop; the runtime's health is not my concern.
- `midflight` → no leader-facing mid-flight questions; blockers go back through my orchestrator.
- `shared_meta_kv` → no metadata mutation per the round-counter lane resolution (the KV counter is pinned from the orchestrator's side; critic stays read/denied).
- `infra` → no infrastructure mutation.
- `mcp` → no MCP surface; a read-only leaf has no use for it and no business configuring it.
- `image_save` → read-only leaf purity: I never write captures. Captures are saved upstream (sketcher's write-through / capture procedure); I read and compare them. Deny per D6 intent per finding 9 adjudication.

---

## dynamic-skill

My review-context skill auto-loads at turn start; it carries the review posture and points at the vendored design resources (design systems, checklists) by reference. I never hand-copy resource content into verdicts — I cite the reference and read what I need. `skill_search` / `skill_view` cover depth beyond the auto-load. I keep my skill versions consistent: the frontmatter version is the source of truth, and any manifest listing a skill must match it.
