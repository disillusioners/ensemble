# Runbook — `prompt_reload` (Ari's mid-session prompt refresh)

Status: shipped by the prompt-reload commission (2026-11-06). Tool:
`prompt_reload` (category `prompt-reload`, PRIVILEGED, **ari only**).
Plan set: `.agents/shared/planning/prompt-reload/`.

## What it does

Refreshes **the calling instance's own** system prompt from the active
agent definition — no restart, no session loss. The tool resolves the
agent dir (registry — spawn parity), validates it against the running
environment, verifies every file the prompt loader reads against the
release manifest, recomposes the prompt through the SAME loader the
spawn/restore path uses (DB-approved prompt versions honored
automatically), compares old vs new, and — when something changed (or
`force=true`) — evicts the cached graph so the **NEXT dispatch**
cold-restores onto the new persona (the probe-validated bare-pop seam).

## Who can call it

Exactly one agent: **ari**. Three independent layers, all required:

1. the `prompt-reload` category is PRIVILEGED — it never joins the
   default-grant universe (empty-allow agents cannot reach it);
2. `agents/ari/meta.json` carries the explicit `"prompt-reload"` allow
   entry (trailing-entry discipline) — and no other agent does;
3. the tool body rejects any caller whose factory-captured
   `agent_id != "ari"` with the notify-exact **generic envelope**:

   ```
   ERROR: prompt_reload is available to the ari agent only (caller: <id>).
   ```

   This authorization class deliberately carries **no REFUSED code**
   (AM-6) — do not look for `prompt_reload_not_ari` in the table below.

## Next-turn semantics

The effect lands on the **NEXT dispatch** (the probe showed the
in-flight turn completes on the old persona; an in-flight turn holds
its own graph reference and is unharmed; nothing re-caches at turn
end). The tool return — ending with exactly one frozen marker line —
is the ONLY in-turn signal:

```
[PROMPT-RELOADED outcome=<o> next_turn=true]
```

`<o>` is one of the five success outcomes (below); the marker appears
**zero times** on refusals and FAILED paths. Compaction may erase the
marker later — acceptable: the persona is already live (S10-k).

## Provider prompt-cache invalidation

Swapping the persona **invalidates provider prompt caches — accepted**
(R9). This is a deliberate consequence, not a defect.

## Outcomes (success set — the marker tokens)

| Outcome | Meaning |
|---|---|
| `no_change` | File-level freshness: the prompt FILES (and DB fingerprint) are unchanged since the last cache fill. NOT a claim that the running closure persona is current — see the multi-instance note below. No pop. |
| `reloaded` | Files changed; recomposed + popped. If the loader's own mtime check would have missed the change (hash/mtime divergence), the cache was BUSTED before composing and the report carries `cache_busted=true` / `hash_mtime_divergence=true`. |
| `reloaded_db` | ONLY the prompt-versions DB content changed (files unchanged); recomposed + popped — the DB override is honored AT the reload, not deferred to the next restore. |
| `forced_reload` | `force=true` on an otherwise-unchanged tree — popped anyway, message says so. |
| `unproven` | No prior baseline record in this process (first call after ANY daemon restart). Force-like: recomposed + popped as a precaution; the persona diff cannot be proven, so the report says `persona_diff=unavailable`. **Deliberate (S10-n)** — it NEVER returns `no_change` without a baseline. |

### `no_change` is file-level freshness only

`no_change` means "files unchanged since the last cache fill". With two
instances of the same agent sharing the prompt cache, instance A's
reload refreshes the shared cache and instance B's next call may report
`no_change` without popping (B's closure still stale). `force=true` is
the escape hatch (S10-j).

## Refusal table — FINAL vocabulary (9 REFUSED + 1 FAILED)

Envelope shapes (notify-user precedent):

```
Error: prompt_reload REFUSED — reason=<code>: <message>
Error: prompt_reload FAILED — reason=<code>: <message>
```

| # | Code | Envelope | Meaning |
|---|------|----------|---------|
| 1 | `prompt_reload_disabled` | REFUSED | kill-switch `ENSEMBLE_PROMPT_RELOAD_ENABLED` is OFF (read PER CALL; the tool stays registered). |
| 2 | `prompt_reload_agent_dir_unresolved` | REFUSED | registry miss / `metadata.path` absent for the caller's agent. |
| 3 | `prompt_reload_dir_invalid` | REFUSED | resolved dir fails self-env containment (live/sandbox → under `<install>/releases/<v>/agents/`; dev → repo `agents/`), incl. an unresolvable self-env marker (message names the marker failure). |
| 4 | `prompt_reload_manifest_missing_live` | REFUSED | live/sandbox-shaped release with NO `manifest.json`. |
| 5 | `prompt_reload_manifest_unreadable` | REFUSED | manifest present but torn/invalid JSON (≠ missing — different operator remedy). |
| 6 | `prompt_reload_manifest_mismatch` | REFUSED | per-file sha256 mismatch against the loader-read-set scope (tamper / corruption). |
| 7 | `prompt_reload_agent_meta_invalid` | REFUSED | `meta.json` unparseable/structurally invalid — the loader would silently compose a skill-less persona over it; this check refuses instead. |
| 8 | `prompt_reload_compose_failed` | REFUSED | `load_and_cache_prompt` raised (incl. fingerprint-computation failures). |
| 9 | `prompt_reload_db_unavailable` | REFUSED | the tool-side prompt-versions repository probe RAISED — composition skipped (the loader would fail OPEN). The message names the prompt-versions repo. |
| 10 | `prompt_reload_internal_error` | **FAILED** | unexpected exception (logged server-side; the exception detail never reaches the payload). |

## Resolution + validation rules (dev vs live vs sandbox vs demo)

- **Registry first, always** — the resolved dir is `metadata.path` from
  the agent registry (spawn/restore parity). The `current` symlink is
  read for REPORTING only; divergence NEVER refuses.
- `current_matches_running` is three-valued:
  - `true` — `current` points at the running release;
  - `false` — they disagree (the daemon resolves from the RUNNING
    release; follow the report's fixed line);
  - `unknown` — no install dir / no symlink / dangling link / no
    running-release identity (e.g. a dev process). Its fixed line says
    so. A promote restarts the daemon, so live divergence is not
    produced by a normal promote.
- `dev` → the resolved dir must be under the repo `agents/`; manifest
  verification is SKIPPED with `manifest=absent(dev)`.
- `live` and `sandbox` → the resolved dir must be under
  `<install_root>/releases/<vX.Y.Z>/agents/`; the release manifest
  **must exist and verify** (a frozen-binary sandbox runs out of a
  staged release — silently skipping would void every staged boot
  smoke).
- `demo` → containment like live; the manifest is verified when
  present, `absent(demo)` skip when not.
- Manifest verification scope = **the loader's read set for the
  calling agent** (own artifacts + `meta.json` + the shared
  `_prompt_system` files the loader reads + innate-skill files +
  `memories/*.md`) — derived by importing the loader's own resolvers,
  never a hand-written glob. Requested-but-unmanifested paths are
  SKIP (e.g. `memories/*.md` has no manifest entries), never mismatch.

## Idempotence + force

- Unchanged inputs → `no_change`, NO pop, NO cache bust; the full
  provenance is still returned (the no-op path is the free dry-run).
- `force=true` → pops even when unchanged (`forced_reload`).
- **`cache_busted` means "the shared cache entry was invalidated"** —
  it is BOTH a reporting observable AND a correctness mechanism. The
  loader's cache-hit serves the cached persona whenever the stored and
  current file mtimes are EQUAL (no content hash, no re-read), so an
  mtime-preserving edit is invisible to it and the bust is the only
  cure (`force=true` cannot cure a stale cache — the next restore would
  serve the stale persona too). Two branches:
  - **warm, files changed, loader-mtime-equality would still hit** →
    bust + `hash_mtime_divergence=true` (the provable stale-cache
    shape);
  - **cold (no baseline in this process) + a live entry** → bust, but
    `hash_mtime_divergence=false` (the entry's bytes are unprovable, not
    provably divergent — no fabricated divergence claim). Busting makes
    the compose read disk truth and keeps the recorded baseline honest
    (it always describes the inputs of the compose that produced the
    persona actually served — FIFO record, refreshed in place, never a
    separate later disk read).
- The baseline map is **FIFO, not LRU** (a refresh does not promote a
  key); eviction is graceful — a lost record yields `unproven` on the
  next call, which recomposes + pops.

## Kill-switch

`ENSEMBLE_PROMPT_RELOAD_ENABLED` — default ON; off-spellings
`{0,false,no,off,disabled}`; an invalid value warns loudly and stays
ENABLED. Read **per call**: flipping it takes effect on the next call,
no restart; the tool **stays registered** and refuses typed
`prompt_reload_disabled` (never a shrunk tool surface).

## Main-loop precondition (operational note)

The primitive must be AWAITED on the daemon's main loop. Its safety
argument (awaitless critical spans, bare-pop idempotence) collapses
off-loop. The tool path always satisfies this; a future compaction
caller must too.

## Observability — the ONE frozen INFO line

Exactly one `logger.info` line per tool call, on EVERY terminal path
(success, refusal, FAILED), frozen field order:

```
prompt_reload: instance=<id> agent=<agent_id> outcome=<no_change|reloaded_db|reloaded|forced_reload|unproven|refused> code=<code|-> release=<running_release|?> daemon_version=<__version__> fingerprint_changed=<bool> hash_divergence=<bool> cache_busted=<bool> current_matches_running=<true|false|unknown> persona_diff=<exact|unavailable|-> was_cached=<bool> manager_instances=<before>-><after>
```

- `fingerprint_changed` — TRUE iff the prompt-versions DB fingerprint
  differs from the baseline record's stored value; `false` on
  `unproven` (no baseline to compare).
- The INFO outcome enum (6 values, incl. `refused`) is deliberately a
  DIFFERENT SET from the marker's 5 success tokens — do not reconcile
  them by shrinking either (D25).
- The line NEVER contains persona or diff content — counts/flags only.
- No upgrade-journal write, no audit ledger, no metrics family in this
  lane (`prompt_reload_total{outcome}` is a routed follow-up — S10-m).

## Persistent effects (four, all bounded/documented)

1. `manager.instances.pop(instance_id, None)` — the bare pop;
2. `cache.set` — inside the loader on a cache miss;
3. `cache.invalidate` — the stale-cache bust;
4. the process-local **baseline map** (the `(agent_id, version_tag,
   mcp-set)` → `(file hashes, db fingerprint)` record that powers the
   tuple decision) — write-only-by-the-primitive, process-local, lost at
   restart (hence `unproven`), and **bounded**: insertion-order eviction
   past `_BASELINE_MAX_ENTRIES` (256) so it can never grow without limit.

Nothing else mutates state; conversation history is NEVER written.

## Operational notes

- **`force=true` after an MCP warmup.** The in-process dynamic tool
  docs (the MCP section of the composed prompt) are OUTSIDE the
  decision tuple by design (D5 — the tuple is the file read set + the
  DB fingerprint). If the MCP tool set changed in-process and the files
  did not, a normal call may report `no_change`; `force=true` pops
  anyway and the next restore recomposes with the live MCP docs.
- **`live` containment root.** The live/sandbox containment check uses
  `_resolve_install_dir(...)` (HOME-derived canonical install path),
  while the registry anchors on the frozen executable. They can differ
  (e.g. a non-standard install location); the check fails CLOSED
  (`prompt_reload_dir_invalid`) rather than verifying an unreachable
  tree.
- **Return-field scope.** The tool TEXT carries the abbreviated diff;
  the structured facts (`was_cached`, `mcp_tool_names_source`,
  `db_fingerprint`, `parity_ok`, `claim_guard`, `hash_divergence`, …)
  live in the primitive's `provenance` payload and the INFO line only.
- **Honesty marks need DB rows.** The F7 marks (S10-a/S10-b) render
  only when effective prompt-versions rows EXIST for those artifacts —
  0 live CORE rows today, so an L9 run must SEED rows (or a stub repo)
  to exercise the marks.

## Cold start (first call after any restart)

`unproven` — recompose + pop, `persona_diff=unavailable`. This is BY
DESIGN (S10-n): "no baseline ⇒ don't refresh" would fail exactly in
the post-restart scenario the tool exists to serve. Pop ≈ 0.02 ms and
idempotent.

**Operator note:** on that first call you will see one extra
`cache_busted=true` (structured provenance reason:
`cold_process_cannot_prove_entry_bytes`) **per process per agent** —
expected behavior, not a defect: the cold path busts the shared cache
entry so the recompose reads disk truth (N1/D27). Measured cost of the
bust ≈ 3.6 ms.

## Honesty notes (known, routed — NOT fixed here)

- **F7(i)** — `tools_note` DB overrides are DEAD (the loader composes
  under key `tools` while the override hook writes `tools_note`). The
  report marks the artifact accordingly (`S10-a`); it never claims the
  override takes effect.
- **F7(ii)** — `user.md` / `growth.md` never enter any prompt; their
  DB rows are write-only today (`S10-b`).
- **F8** — the FE-visible synthetic system message may lag/diverge
  until the next display rebuild — no display-parity promise
  (`S10-c`).
- File-side TOCTOU on the loader cache is bounded one-call staleness
  that self-heals; DB-side TOCTOU is closed by the loader's
  single-SELECT (`S10-h`). A prompt-versions repo failure between the
  tool-side probe and composition still fail-opens inside the loader
  (sub-second window — `S10-i`).

## Testing the seam (dev harness)

The L1 sentinel E2E runs against a FIXED scratch worktree
(`/home/nea/ensemble-src-wt-prompt-reload-harness`, pre-staged by the
implementer) — never the release tree, never the implementation
worktree's own `agents/`. The tester mutates the scratch `agents/ari/`
copy, boots the dev daemon (`ENSEMBLE_SELF_ENV=dev`,
`DAEMON_PORT=8079`, throwaway PG,
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly,
per-variable `POSTGRES_*` scrub), and OWNS the byte-exact restore
post-condition.
