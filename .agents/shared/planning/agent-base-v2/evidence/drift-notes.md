# Drift/Duplication Posture — agent-base-v2 (recorded, NOT solved)

**Date:** 2026-10-13 · Phase E Step 10.

1. **Steady state.** Base agents are a FROZEN MIRROR of v2 content (post-Phase-B). The five `[v2]` dirs remain THE live lane (`default_agent_versions` runtime invariant: `{}` overrides ⇒ base-id resolution; v2 dirs resolve under base ids via `_parse_agent_dir_name`). Post-mission, editing a `[v2]` dir does NOT propagate to its base twin.

2. **Drift hazard.** Any future edit to a v2 dir not mirrored to its base produces SILENT base drift — the `evidence/base-vs-v2-diffs/<name>.diff.txt` files become non-empty (that is the drift alarm). Maintenance discipline: every future v2-content edit must explicitly decide mirror-to-base (default until a v3 emerges) or diverge (then re-version). No automated mirror mechanism exists; this is a documented standing cost.

3. **D3 wrinkle (dual-contract memory prose).** `developer[v2]/memory.md` and `reviewer[v2]/memory.md` may carry mixed v1-era and v2-contract prose; the Phase B copy inherited it byte-for-byte (zero-touch proof pins it). Cross-version memory sharing is the historical mode and is now INVERTED (base mirrors v2, not the reverse). Future memory edits must stay version-aware.

4. **Maintenance surface (~12 pin files).** Per-agent meta/pin tests now pin v2 values against BOTH the base dirs and `[v2]` dirs. Future v2-content edits that should propagate to base must keep the pin files updated too (upgrade-friction point). Known cost; not solved in this mission.

5. **Byte-copy ≠ provenance.** Per P5: manifests are content-only (no modes/mtimes verified — `lib.sh:1928-1935` family). `cp -pR` preserved modes, but nothing proves them; a mode regression would surface only at boot/runtime, not in the manifests.
