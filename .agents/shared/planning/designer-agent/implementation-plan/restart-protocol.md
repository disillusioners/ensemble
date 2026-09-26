# Designer-Agent — Restart Protocol (WP1)

**Audience:** operators bringing up the designer-agent feature (Cluster A + Cluster B).
**Source of truth:** `implementation-plan/phase1-foundations.md` §4 (WP1 detail table),
`architecture-recommendation.md` §3.4 (D2 — `vision` allowlist entry).

This file is the operational contract for the **one** restart that promotes
the designer-agent phase-1 work from "code on disk" to "registered agent
resolving to a non-default model". After this restart, the daemon has:

- a registered `designer` agent (Cluster B — WP4/WP5),
- `vision` resolvable as a model name daemon-wide,
- `caller_model_overrides` generalized at the spawn seam (WP2),
- spawn-time model observability (`model=` + `source=` in the log line, WP3).

The restart must be **a single window** because boot discovery
(`daemon/registry.py:536` `discover()`) is one-shot — agents/ dir changes
land before the daemon comes back up.

---

## 1. `allowed_models` is daemon-GLOBAL

`config.llm.allowed_models` is a daemon-global exact-match allowlist
(not per-worker, not per-team — see `architecture-recommendation.md` §3.4).
There are **exactly two** ways to populate it:

| Path | Mechanism | Notes |
|------|-----------|-------|
| **Config file default** | `config.yaml` → `allowed_models: ${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2,vision}` | The shipped default now includes `vision`. Visible at the value line; operators grep it. |
| **Env override** | `OPENAI_SELECTABLE_MODELS` env var (comma-separated exact-match list) | An empty / whitespace-only env value is treated as "default applies" (see `config.yaml:77-80` comment). To lift all restrictions entirely, hardcode `allowed_models: []` in `config.yaml`. |

**Exact-match semantics only.** The allowlist is NOT a prefix or substring
match (verbatim from `config.yaml:81`). A value of `vision*` or `*vision*`
will not match `vision`.

**Designer-specific resolution path.** The designer agent's
`meta.json` declares `llm_model: "vision"`. With `vision` in the
allowlist, the spawn seam resolves to `vision` non-silently — see
`architecture-recommendation.md` §3.4 + WP3 observability guarantee.

---

## 2. `model_vision` deployment step (same restart window)

`OPENAI_MODEL_VISION` (config key `model_vision` at `config.yaml:23`)
is a deployment-side setting. **Default is empty** (`""`).

| Setting | Behavior |
|---------|----------|
| Empty / unset | Per-turn vision routing (`daemon/graph.py:7589` pattern) fail-fasts when an image-bearing message arrives. **Hard precondition for the designer phase** — the comparators / design tools will surface 4xx-style errors immediately. |
| Non-empty (e.g., `gpt-4o`, `claude-3-5-sonnet-…`) | The vision router uses that model for `images=[…]` dispatches. The value is NOT validated against `allowed_models` at boot — the model just needs to be reachable by the LLM gateway. |

Set `OPENAI_MODEL_VISION` in the deployment env (or
`model_vision:` in `config.yaml`) **before** the restart below. The
restart-time sequencing matters because vision routing is wired at
graph build time, not at message-arrival time.

---

## 3. Dir-first-restart-once ordering (hard rule)

`daemon/registry.py:536` `discover()` runs **once** at boot. Hot-adding
an `agents/designer/` directory after boot is not possible — the
registry is in-memory and frozen for the daemon's lifetime.

Therefore:

1. **All agents/ dir changes** (designer directory + any other agent
   anatomy edits) **land BEFORE the restart.** This includes:
   - `agents/designer/{meta.json, soul.md, rule.md, workflow.md, tools_note.md}` (WP4 — Cluster B)
   - `agents/leader/meta.json` `team_members` 14 → 15 (WP5 — Cluster B)
   - `agents/leader/workflow.md` three edit sites (WP5)

2. **Then the daemon restarts exactly once.** Within that one window,
   ship together:
   - The agents/ dir changes (above),
   - The `allowed_models` config change (`vision` entry — this WP),
   - The `model_vision` deployment setting (this WP),
   - All daemon-code changes (WP2/WP3/WP7-9 — same restart).

   The single restart freezes all boot-time state (registry,
   allowed_models snapshot, vision wiring). Any later change to
   `allowed_models` or `agents/` requires another restart.

3. **No "I just want to add a file" hot-add.** Anything that mutates
   boot-time state waits for the next restart window. The registry
   does not refresh mid-run.

**If a dir change ships in a different restart window than the
code changes**, the new agent will be discoverable but its tools /
spawn-seam behavior will reflect the OLD code. Bundle them.

---

## 4. The restart-window checklist

This is the ordered checklist an operator runs:

| # | Action | Verify | Source |
|---|--------|--------|--------|
| 1 | Commit Cluster A code (WP2/WP3) + Cluster B dir (WP4/WP5) together on `latest`. | `git log -1` shows the combined commit | `phase1-foundations.md` §5 coupling map |
| 2 | Tag the commit (e.g., `v0.x.y-designer-phase1`). | `git tag --points-at HEAD` | — |
| 3 | On the target host: stage the release with `scripts/upgrade/stage.sh --version <tag>`. | `releases/<tag>/` populated, manifest shows `rollback_safe: true` | self-upgrade pipeline |
| 4 | Set `OPENAI_MODEL_VISION=<vision-capable-model>` in the deployment env. | `env \| grep OPENAI_MODEL_VISION` | §2 above |
| 5 | Confirm `OPENAI_SELECTABLE_MODELS` includes `vision` (or accept the config.yaml default which now includes it). | `env \| grep OPENAI_SELECTABLE_MODELS` (if set) | §1 above |
| 6 | Promote with `scripts/upgrade/promote.sh --version <tag>`. | `releases/<tag>/` becomes `current`; daemon boots onto the new release | self-upgrade pipeline |
| 7 | On boot, verify the post-restart log lines: designer registration, `allowed_models` echo, vision routing wired. | tail `data/logs/ensemble.log` | WP12 AC-12a, AC-12b |
| 8 | (Optional but recommended) spawn a designer instance via the leader and confirm the spawn log carries `model=vision source=llm_model`. | `grep "model=vision source=llm_model" data/logs/ensemble.log` | WP3 observability |

**Operator rule:** if any step #4 / #5 / #6 is missed, the post-restart
verification (§7) will fail loudly (vision routing fail-fast on
empty `model_vision`; designer resolves to default with a `[NOTE]` line
when `vision` is not in the allowlist).

---

## 5. Why one window (not three)

Three reasons — all stated as hard constraints:

1. **Boot discovery is one-shot.** Adding `agents/designer/` after
   boot is impossible (`daemon/registry.py:536` `discover()` runs once).
   Splitting dir changes from code changes means either:
   - dir change ships first → code change ships later → 2 restarts, 1
     unnecessary boot-cycle, registry re-loads from scratch twice.
   - code change ships first → dir change ships later → 2 restarts AND
     the new code path has nothing to operate on (designer not yet
     registered).

2. **`allowed_models` is also one-shot.** The allowlist is snapshotted
   at boot (see `daemon/services/instance_lifecycle.py:1407-1452`
   block — `def _resolve_model_override` reads
   `getattr(self._config.llm, "allowed_models", None)` and enforces the
   silent-fallback contract). Mid-run changes are not observed by the
   running daemon.

3. **`model_vision` is also one-shot.** Vision routing is wired at
   graph-build time (`daemon/graph.py:7589` pattern). Mid-run env
   changes do not propagate to already-built graphs.

One window eliminates all three redundant boot cycles.

---

## 6. What "in the same restart window" means for non-operator readers

For planners / reviewers: this document describes a *configuration
contract*, not an enforcement rule. The code does NOT verify "did you
land all five things together?" — operators are responsible for
sequencing. The code provides:

- A WARNING log when `_resolve_model_override` rejects a model
  (`daemon/services/instance_lifecycle.py:1447-1451`, WP3 — DEBUG
  bumped to WARNING). If a designer spawn logs "model 'vision' is not
  in allowed_models; silently falling back to default model" → the
  restart did NOT include the config change.

- A spawn log line carrying `model={resolved_model}, source={resolved_source}`
  (`daemon/services/instance_lifecycle.py:2231-2235`, WP3). If `source=llm_model`
  and `model=vision` → WP3 observability fires. If `source=default`
  instead → the allowlist was not picked up → restart did NOT include
  the config change.

- A WARNING log when `model_vision` is unset and an image-bearing
  message arrives (graph.py:7589 pattern, fail-fast). If a vision
  dispatch fails with "no vision model configured" → `OPENAI_MODEL_VISION`
  was not set in the deployment env.

These three signals are the post-restart verification — see WP12
AC-12a, AC-12b, AC-12c.

---

## 7. Post-restart verification (operator smoke)

After the restart lands, run these greps to verify all three signals:

```bash
# 1. designer is registered at boot
grep -E "AgentRegistry|registered" data/logs/ensemble.log | grep -i designer

# 2. designer resolves to vision (WP3 observability line)
grep "model=vision source=llm_model" data/logs/ensemble.log

# 3. no silent-fallback WARNINGs for 'vision'
grep "is not in config.llm.allowed_models" data/logs/ensemble.log || \
  echo "OK: no silent fallback for vision"

# 4. vision routing wired (build-time graph log)
grep "model_vision\|vision_routing" data/logs/ensemble.log
```

If #1, #2, #3, #4 all return content (or #3 returns the "OK:" line),
the restart window was complete. If any returns empty, see the
diagnostic flow in §8.

---

## 8. Diagnostic flow (when the smoke returns red)

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Designer not in `data/logs/ensemble.log` after restart | `agents/designer/` dir not on disk before restart, or `discover()` raced with another boot cycle. | `git status` on the target; re-stage + re-promote if the dir was missing. |
| Designer present, no `model=vision source=llm_model` line | The spawn was from the leader or from a worker; only designer-originated spawns show `source=llm_model`. | Spawn a designer directly via `POST /api/messages` with `agent_id=designer` (after WP5 wiring). The line appears. |
| Designer present, but `source=default` + WARNING about `vision` | `OPENAI_SELECTABLE_MODELS` excludes `vision` AND `config.yaml` was reverted (or the prior staged release was promoted instead of the new one). | Check `current` symlink → `releases/<tag>/` matches the new commit. Update env or revert config to include `vision`, then re-promote. |
| Vision dispatch fails with "no vision model configured" | `OPENAI_MODEL_VISION` was not set before restart. | Set the env, re-promote. |
| Designer registered but the leader's team_members list is 14 | The leader meta.json edit (WP5) was not in the commit. | Re-stage with WP5 included, re-promote. |

---

## 9. Cross-references

- **GT-2 / GT-3** (ground truth): `implementation-plan/phase1-foundations.md` §2 — `_resolve_model_override` never raises, emits DEBUG (now WARNING); `resolved_source` tracks 4 paths; `llm_model` branch logs nothing (WP3 closes this).
- **D2** (architectural decision): `architecture-recommendation.md` §3.4 — the operative change is the `vision` allowlist entry.
- **PD-1 / PD-3** (plan-time decisions): `implementation-plan/phase1-foundations.md` §8 — DEFER behavior change (no raise on non-allowlisted); ADD observability (WARNING + spawn log).
- **WP2 / WP3**: see `daemon/services/instance_lifecycle.py:1407-1452` (the seam) + `:1447-1451` (log level) + `:2231-2235` (spawn log line).
- **WP12 verification table**: `implementation-plan/phase1-foundations.md` §4 — AC-12a (registered), AC-12b (non-silent resolution), AC-12c (override chain).