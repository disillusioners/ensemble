# Changelog

All notable changes to the agents-ensemble project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.25.2] — 2026-10-19

- Fix question packs lost across daemon restart — rehydrate pass moved from `initialize()` to an explicit post-`setup_worker_pool()` lifespan step with a loud boot signal

## [0.25.1] — 2026-10-19

- Pin worker/coder agents to a single 'coding' model — drop client-side weighted balancing (proxy now load-balances)

## [0.25.0] — 2026-10-19

### Added — resource-cost-control observability subsystem (P1, LANDED) (`resource-cost-control`, branch `feature/resource-cost-control`, base `8b8b32dcc`)

> **CHANGELOG (feature):** *Slice 1 of the resource-cost-control commission: an always-on in-daemon resource observability spine — ONE sampler thread (cgroup v2 + raw `/proc`, no psutil), ONE batched flusher with roll-down + retention sweeps, four `resource_*` tables, the first three `/api/resources/*` endpoints, and the self-cost budget as an enforced acceptance criterion (the monitor measures itself and throttles before it costs more than it explains).*

- **Sampler + storage + roll-down** — `daemon/services/resource_monitor/` (sampler, ring, flusher, rollup, proc_readers, pid_registry, facade) with lifespan wiring; 12 `RESOURCE_MONITOR_*` knobs (`daemon/config.py` `ResourceMonitorConfig`: sample cadence 30 s clamp 15–60, raw/minute/hour retention 48 h/14 d/180 d with floors, detail ladder, instance cap ≤64, self-cost budgets, opt-in profiling, flush batching).
- **4 dual-dialect migrations** — `daemon/migrations/versions/20261018_0900{01..04}_create_resource_*.sql` (`resource_daemon_bucket` / `resource_instance_sample` / `resource_pid_map` / `resource_limit_policy`) + SQLModel canonicals (`daemon/models/resource_monitor.py`).
- **`GET /api/resources/live` / `history` / `self-cost`** (`daemon/routers/resources.py`, mounted P1) — live one-shot sample with daemon/cgroup/attribution/overruns blocks; history with `window=15m|1h|24h|7d` + auto-resolution fall-down (`resolution_used`/`rollup`/`truncated`); self-cost exact field set `{cpu_pct, cumulative_cpu_s, buffers_bytes, rss_budget_mb, threads, samples, ticks_skipped, flush:{batches,bytes,errors}, profiling:{active,capabilities}}`. All degrade to the empty shape, never 503.
- **Self-cost regression gate** (`tests/test_resource_monitor_self_cost.py`) — median tick < 5 ms, hard gate 50 ms, EMA > 1 % ⇒ interval doubled + deep-scan off, sampler RSS delta < 20 MB, zero reader-call fixture escapes; fenced (`mktemp` FAKE_HOME + escape guard — never live-shaped paths).
- **Honesty columns surfaced, never buried** — `unattributed_kb` / `estimated` / `coverage_pct` carried in the storage tier (`resource_daemon_bucket`), MB-rounded in `/live`'s `attribution` block: per-instance truth is partial by physics in one shared process and the docs say so.

### Added (forward — part of the same release train; not yet in the merged tree at this writing)

- **Instance attribution + honesty surface (P2)** — per-instance `resource_instance_sample` population, children/instances attribution in `/live`, and the honesty columns filled with real values. The storage layer is already landing on the branch (live pid registry + pid_map history `079cc9a37`/`0eac74037`; bash success-path exit-mark + kind-hint ContextVar stamping `dc1c21396`/`5981b41ba`; MCP stdio spawn-seam stamp `ce2fc1cca`) — the `/live` attribution population ships with the train.
- **Traceback window (P3)** — `GET /api/resources/traceback` (Z-suffixed ISO t0/t1 from the timeline drill-down).
- **Anomaly layer (P4)** — `GET /api/resources/anomalies` (`?window=` enum `{15m,1h,24h,7d}`, default `1h`) + `/anomalies/{id}/evidence`; shapes `spike | ramp | leak | step`; baselines conditioned on `(n_active_workers, dow, hour)`.
- **Control seam, observe-only (P6)** — `GET /api/resources/control-capabilities` + opt-in `POST /profile` (`RESOURCE_MONITOR_PROFILING_ENABLED=false` by default).

### Added (LANDED, FE) — `/resources` page

- **Lazy gear-menu route + page** (`feature` commits `8d648127d`…`c512bc1e6`, P5) — gear-menu `Resources` entry + lazy `loadComponent` route (zero initial-bundle cost), live tab with gated 8 s poll (skeleton / retain-last-data / single error branch) and self-cost chip, dependency-free inline SVG timeline with anomaly markers + click→window drill-down (default window `1h`, Z-suffixed ISO timestamps), daemon-free Playwright gate.

### Changed — performance (LANDED)

> **CHANGELOG (performance):** *The adjudicated optimization set from the same commission — each an independent, individually revertible commit.*

- **O1 — glibc arena cap** (`a64e82c2c`): `MALLOC_ARENA_MAX=2` at all four boot surfaces (systemd template `Environment=` line, launcher default-only setdefault — a staged value wins, `__main__` module-level setdefault) + a never-raises `malloc_trim(0)` tuner on the existing hourly/idle-gated maintenance loop. Expected −10~30 % anon (soft estimate — the runbook's measurement protocol at T+24 h/T+72 h vs the 1.68–1.72 GB baseline decides it; real reduction < 5 % ⇒ O3 is the dominant lever).
- **O3 — instance-graph cache bounded** (`60f382bd1`): `LIMITS_INSTANCE_CACHE_TTL_HOURS` (default 6 h, post-terminal, active never evicted) + `LIMITS_INSTANCE_CACHE_MAX_ENTRIES` (LRU 64) via the settings resolver. User-visible: instance graphs evict 6 h after terminal instead of 24 h; shorter TTL ⇒ more recompiles on revive (bounded, metric-visible).
- **O4 — retry storm + pool hygiene** (`778a49cb2` + `8fb77a7b5` + `9186cdd4a`): the source-adapter backoff reset is now gated on the crashed run having actually lived ≥ 60 s (start-then-immediate-death keeps the accumulated backoff — repeated short crashes grow the cadence instead of pinning ~2–5 s; user-visible: the plane/computer-use MCP retry storm is fixed) + floor-preserving idle reap for warm-pool STDIO MCP children (`MCP_POOL_IDLE_REAP_SECONDS`, default 1800, 0 disables; in-use connections never reaped; user-visible: idle MCP servers are reaped after the configured idle window).
- **DependencyBus lock reap (LANDED)** (`d89843691` + `a023a7ddb`): terminal-or-gone, unheld, idle ≥ 3600 s, not-pending locks reaped on the hourly idle-gated hook with a pop-time idle re-check closing the race; guard-lock pattern and `stop()` untouched.
- **O8 — stale-subprocess defenses** (`a94000ae5` + `c86462dc3` + `4d572f561`): boot-time stale-subprocess sweep (`daemon/services/boot_subprocess_sweep.py` — prod-lane default via `sys.frozen`, env kill-switch `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`, every reap through the PID-recycle guard starttime-field-22 + pgid) + read-only KillMode audit (verdict: no unit-file change warranted — `KillMode=mixed` stop-phase semantics documented). Motivated by the demonstrated Oct-17 kernel OOM (3 deaths, daemon killed 23:19:14Z, `MemoryMax=infinity`, zero swap, test-farm children leaking across restarts).
- **O5 — bash live-dict leak closed (via P2's success-path exit-mark)** (`dc1c21396`): the bash subprocess live dict grew by one entry per call because the success path never marked exit; the success path now routes through `PidRegistry.mark_exit` with uniform `exit_kind` routing (owned by P2 per phase7 D9, superseding the standalone-O5 plan).

### Changed — O2 ceremony-gated (its own line, explicit)

- **Bounded chromium render concurrency** (`0d0a67179`): `LIMITS_CHART_RENDER_CONCURRENCY` (default 2, effective clamp [1, 4]) + hard per-render timeout with awaited child kill is IN THE TREE but takes **LIVE effect only at the user's promote nonce ceremony** — this commission performs no promote. User-visible once live: concurrent chart renders may queue under saturation instead of stacking 150–400 MB chromium children.

### Docs

- **Operator runbook** `docs/runbooks/resource-cost-control.md` — reading `/resources` (all 8 endpoints with owning-phase landed/forward labels), honesty-column doctrine, the full knob-tuning table (grep-verified against the merged config), anomaly-shape glossary, the verified NO-OP ledger, and the systemd-bounds/swap/tripwire OOM-delta section.
- **Operator hygiene checklist** `docs/runbooks/resource-hygiene-operator-checklist.md` — O7's exact-command surface: systemd `MemoryHigh=7G`/`MemoryMax=8G` drop-in, swap (USER DECISION), dry-run-first pattern+age-gated test-DB cleanup, orphaned context7-mcp kill, demo-daemon keep/kill (USER DECISION), `MALLOC_ARENA_MAX` on the installed unit, O1 verification protocol. Operator-executed only — agents never execute any of it against live.

### Deferred

- **O6 — per-turn streaming projection**: deferred awaiting a per-turn `tracemalloc` snapshot as evidence first — the P6 profiling seam (`RESOURCE_MONITOR_PROFILING_ENABLED`) is its future evidence source; no speculative memory surgery without it.
- *(O5 was on the deferral ledger but is CLOSED — landed via P2's bash success-path exit-mark, see Changed above.)*

---

## [0.24.0] — 2026-10-19

### Added — DB-managed worker-pool sizing + agent settings tools (`settings-worker-slots`, merged to `latest` @ `50f809940`)

> **CHANGELOG (feature):** *Worker and chat-worker pool sizes are un-hardcoded and become managed runtime settings: env-settable (`LIMITS_WORKER_POOL_SIZE` / `LIMITS_CHAT_WORKER_POOL_SIZE`), DB-backed via the `app_settings` 4-tier resolver (DB > env > yaml > defaults), surfaced to agents through a new `settings` tool category (ari/leader), and mirrored by an honest FE capacity hint. No behavior change on a vanilla deployment — the shipped defaults (5 / 2) are now yaml shadow literals instead of code constants.*

- **Un-hardcoded pools + derived invoke cap** (`0da58a36a`) — `daemon/config.py` limits gain `worker_pool_size` / `chat_worker_pool_size` fields with yaml shadow literals; the invoke-parallelism cap derives from the resolved worker pool size instead of a parallel constant (seal re-pin accompanies the change).
- **Boot-robustness shadow literals [council F1]** (`93ff3427f`) — the two new `LIMITS_*` fields previously had NO yaml literal, so pydantic bound the env tier directly at `load_config()` and a malformed value (e.g. `LIMITS_WORKER_POOL_SIZE=abc`) hard-failed BOOT; the shadow literals restore the documented skip/fail-open resolver contract. Shipped `config.yaml` pool-size literals are pinned by test (`9ac9e417f`).
- **Agent-facing settings tool category** (`c98600ecd`, `20f6b6a24`) — a new `settings` tool category (read/write-gated API surface) exposed to `ari` and `leader`; the FE settings surface gains an honest capacity hint instead of a static number.
- **Test surface** — `tests/unit/tools/test_settings_tools.py` (+744 lines) plus registration-count pins updated across attestation/secrets/upgrade tool suites.

### Fixed — giter worktree fence template carries `POSTGRES_*` + `DAEMON_PORT` (`8e06eb30e`, merged to `latest` @ `f3507b7da`)

- The Giter worktree fence template (the `.env` written into newly fenced linked worktrees) now carries `POSTGRES_*` parts and `DAEMON_PORT` explicitly — previously an inherited-ambient-env gap could boot a worktree lane against the wrong database/port shape (the env-poison occ family). Mandatory keys are stamped into every new fenced worktree at creation time.

## [0.23.0] — 2026-10-18

### Added — agent-secrets P2: marker resolution seams + both-sides transcript scrub (`agent-secrets-p2` @ `79c658fda`)

> **CHANGELOG (feature):** *Second slice of the agent-secrets subsystem (design `.agents/shared/planning/agent-secrets/p2-architecture-recommendation.md`). The P1 mint-only marker `__ENS_SECRET__<id>__` becomes a RESOLVABLE reference and the both-sides transcript leak closes: stored secret values are replaced by their marker at every persistence write path, and plaintext exists ONLY in the consumer's env/args at the moment of use. Zero behavior change for agents that never touch secrets.*

- **Resolution seams (spawn/invoke-time, fail-closed)** — markers resolve to plaintext at the last moment, fresh from PG inside one transaction (SELECT → decrypt → `use_marker` audit row; no RAM plaintext anywhere, restart-durable by construction): the `bash` tool gains an optional `env` overlay (markers resolve for the child process only — a failing marker aborts the spawn with a typed envelope, never partial env), a proc subprocess env overlay (operator-injected, not LLM-visible), MCP stdio spawn points (warm pool + both cold spawns — stored config keeps its markers; resolution never persists plaintext into DB config), and opt-in `secret_typed_args` tool-param resolution (per-tool declared fields; undeclared tools unchanged — a registration slot, not a new category).
- **Both-sides persistence scrub** — stored values are replaced by their marker at three write sites: tool results (before they join live agent context), the checkpoint saver proxy (serialized tool-call args + results — live args stay intact for execution and retry), and enqueued message bodies (defense-in-depth). Idempotent under multi-pass scrubbing; markers themselves are never rewritten; retired secrets stay in the scrub set. Staleness-safe: new secrets become scrub-visible immediately (store event + 60s TTL backstop).
- **`use_marker` audit + `context` column** — one additive migration (`20261018_000001_secrets_audit_add_context.sql`, `secrets_audit.context`: `bash_env` / `mcp_spawn` / `tool_param` / `scrub`); P2 is the first emitter of the previously-reserved `use_marker` action (success AND rejection rows, stamped in the same transaction as the resolution they describe). Zero audit rows for secret-free agents.
- **`secret_store` marker-passthrough (D2 ruling)** — storing a value that already IS a valid marker returns the existing secret's `{id, name}` without re-wrapping: a pause/resume retry that re-invokes `secret_store` with the scrubbed (markerized) arg is an idempotent success instead of minting a secret-of-a-marker.
- **Typed error vocabulary (fail-closed everywhere)** — `secret_not_found` / `secret_marker_malformed` / `secret_resolver_store_unavailable` / `secret_fernet_key_mismatch` / `secret_cipher_unavailable` envelopes with an anti-differential message family (the `code` field is the only distinction — which id is live stays in the audit log); no seam ever spawns a child/server with literal markers or partial env. Retired secrets KEEP resolving (soft-retire = delist + audit, not revocation — D1 ruling).
- **Do-not-touch lanes byte-unwired (static-audit pinned)** — `db_connections` Fernet, KMS-Lite LANE-1/2, and the `ens_env_read` BYOK lane contain zero references to the new resolver; `build_pooled_stdio_config` keeps returning stored config (spawn-point resolution only).

---

## [0.22.0] — 2026-10-18

### Added — public passcode lane for live views (`liveview-passcode-1`, branch `feature/live-view-passcode` @ `6cf3dbcb6`, merged to `latest` @ `09d4c9490`)

> **CHANGELOG (feature disclosure):** *Live views gain a narrow unauthenticated public lane — passcode-protected sharing via ``/views/public/{root}/{rel}`` with daemon-side passcode enforcement as the CANONICAL gate; proxies are pure passthrough (no coupling to external proxy mechanics). Two-state proxy compatibility (user ruling 2026-10-17): State A (external door.ensem.dev OAuth2 middle server) and State B (in-house OAuth2/OIDC module) both keep the public namespace exempt — operator migration runbook at ``docs/runbooks/live-view-passcode-deployment.md``. The existing OAuth-authenticated ``/views/*`` lane is unchanged.*

- **6-char alnum passcodes; `view_link` ALWAYS returns the passcode** (`daemon/tools/live_views.py`) — minting is idempotent per `(root_name, rel_path)` (first-store-wins: re-mint returns the existing row, never clears an active lock); the link is self-contained for sharing.
- **`view_passcode` tables** (`daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql`, one-file-two-tables per the secrets-P1 precedent): `live_view_passcodes` — Fernet `wrapped_passcode` BLOB via `SYSTEM_ENCRYPTION_KEY` (NO plaintext at rest), UUID4 immutable handles, `UNIQUE(root_name, rel_path)`; + append-only `live_view_passcode_audit`.
- **10-strike durable lockout** (`daemon/services/view_passcode_service.py`, `daemon/repositories/view_passcodes/`) — ROW-level (locking one file never takes out a root), survives daemon restart; wrong-code attempts are counted durably.
- **Public-lane router + interstitial pages + tests** (`daemon/routers/live_views.py`, `daemon/services/view_passcode_pages.py`; suites: public-lane router tests, service/tool tests, migration index-parity, golden direct-lane envelope pin).

### Changed — purpose-id family defaults extended (`purpose-model-defaults`, branch `feature/purpose-model-defaults` @ `c3fabc1d3`)

> **CHANGELOG (feature disclosure):** *The shipped `OPENAI_SELECTABLE_MODELS` default tuple is extended from the prior 4-element form (`agentic,coding,coding2,vision`) to the **6-element** form `agentic,coding,coding2,vision,img-gen,decision` — `daemon/config.py::_ALLOWED_MODELS_DEFAULT` is the single source of truth, `config.yaml` inlines the same list at the interpolation site, and a consistency pin test fails if the two ever drift apart. The image-generation tool (`generate_image`, the `image-gen` privileged tool category) flips from **fail-closed-by-default** to **LIVE-by-default** whenever `OPENAI_BASE_URL` + `OPENAI_API_KEY` resolve — model fallback uses the `img-gen` purpose-id (proxy resolves purpose → real provider/model+credentials); base/key unresolvable ⇒ `image_gen_not_configured` envelope. The fail-closed gate now operates only on base/key resolution, never on model resolution. No new env var is required; containment of who-can-call is the per-agent `image-gen` tool allow in each agent's `meta.json` (six consumer agents granted today; `critic` NOT granted).*

- **Two new purpose-id defaults.** `img-gen` and `decision` join the shipped `OPENAI_SELECTABLE_MODELS` tuple. `img-gen` is the purpose-id for the new image-generation tool family (proxy resolves `img-gen` → real provider/model/credentials); `decision` is reserved for the decision-purpose consumer family (mirrored in `daemon/config.py::_ALLOWED_MODELS_DEFAULT`, no current consumer reads `DECISION_MODEL` directly — see the `image-gen/decision` block at the bottom of `.env.example` and `.env.prod.example`). The default-applied startup WARNING (`daemon/config.py::warn_default_allowed_models_applied`) now names the 6-element list, including a sentence per purpose-id so operators see what each entry covers. The two-source-of-truth consistency pin (`TestDefaultConsistencyPin.test_yaml_literal_matches_constant`) still fails loudly on drift.
- **LIVE-by-default flip for image-gen.** Before this change the `generate_image` tool fail-closed whenever `IMAGE_GEN_BASE_URL` / `IMAGE_GEN_API_KEY` were unset — production code emitted `image_gen_not_configured` on a vanilla daemon with only the main `OPENAI_*` env vars set. Now the dedicated knobs *fall back* to their `OPENAI_*` siblings (same proxy lane, same key — never minted), the model fallback uses the `img-gen` purpose-id (NEVER `OPENAI_MODEL` — the 2026-10-07 occ-3 wrong-model class is prevented by construction; pinned by `TestUnsetModelFallsBackToImgGenPurposeId` + `TestNeverReadsOpenaiModel` in `tests/unit/tools/test_image_gen_tool.py`), and the only fail-closed path is `IMAGE_GEN_BASE_URL` / `OPENAI_BASE_URL` and `IMAGE_GEN_API_KEY` / `OPENAI_API_KEY` resolution (all three states — UNSET, EMPTY, WHITESPACE-ONLY — pinned by parametrized `test_unresolvable_base_fails_closed` + `test_unresolvable_api_key_fails_closed` arms). Whitespace around a set value is stripped; whitespace-only is treated as unset.
- **Allowed-models tuple extension.** `daemon/config.py::_ALLOWED_MODELS_DEFAULT` is now `("agentic", "coding", "coding2", "vision", "img-gen", "decision")` (byte-equal to the `config.yaml:325` interpolation literal). Strict-when-set semantics unchanged: an explicit list without the new ids drops the corresponding spawn override for the lifetime of the deployment (no `[NOTE]` + WARNING fallback path includes the missing id). **`OPENAI_SELECTABLE_MODELS` footgun re-stated:** explicit lists MUST include `vision`, `img-gen`, and `decision` to preserve the defaults' spawn-override coverage — `README.md` and `docs/setup.md` both carry the same one-clause operator warning.
- **Drift pin.** The `TestDefaultConsistencyPin.test_yaml_literal_matches_constant` test parses `config.yaml` at test time and asserts byte-equality between the YAML interpolation literal and the Python constant; runs on every test invocation; fails loudly on any future drift between the two homes of the default.
- **Operator note.** Operators pinning an explicit list (the strict-when-set-by-design semantics from `sel-default-20260927`) should add `img-gen` and `decision` to the list to preserve those spawn overrides; otherwise the image-generation tool's purpose-id resolution still works (it reads its own knobs), but `spawn_instance(model="img-gen")` and `spawn_instance(model="decision")` calls will fall back with the legacy WARNING. The default-applied WARNING still fires once per process when neither env var is set — set `OPENAI_SELECTABLE_MODELS` explicitly (even to the current default) to silence it.

---

## [0.21.0] — 2026-10-17

### Added — agent-secrets P1: in-core secret store + `secrets` privileged tool category (`agent-secrets-p1` @ `06b2886e2`, merged to `latest` @ `e4e4f304`)

> **CHANGELOG (feature release):** *First slice of the agent-secrets subsystem (plan `f11965039`, `.agents/shared/planning/agent-secrets/`). v1 is feature-first per user ruling 2026-10-17: durable agent-usable secrets with autonomy-first semantics (secret-given = trusted for autonomous use — zero runtime questions), exact-match scrub containment, and private material that NEVER crosses the tool-result boundary. Option 3 architecture: in-process `SecretService` inside the core (not a tier-2/3 plugin adapter), PG-backed with Fernet encryption reusing `SYSTEM_ENCRYPTION_KEY` (rotation-coupling documented at the cipher seam — a future key rotation MUST re-encrypt `secrets.wrapped_secret` alongside `db_connections` credentials).*

- **SecretService + SecretCipher** (`daemon/services/secret_service.py`) — in-process store facade; Fernet-wrapped values (`wrapped_secret` BYTEA/BLOB), UUID4 immutable handles, soft-retire tombstones (`retired_at` + partial index on active rows), `name` UNIQUE per row.
- **Schema** — `daemon/migrations/versions/20261017_000001_create_secrets_tables.sql` (SQLite runner) + `daemon/repositories/secrets/models.py` (SQLModel canonical for PG `create_all`): `secrets` + append-only `secrets_audit`. UP section purely additive (CREATE ×4, idempotent IF NOT EXISTS); audit rows stamped inside the same `engine.begin()` transaction as the mutation (prompt_versions Phase-A precedent). Index-parity test extends the `test_prompt_versions_index_parity` pattern (`tests/migration/test_secrets_index_parity.py`).
- **`secrets` privileged tool category (#7 — sextet → septet)** with 6 FLAT snake_case tools (`daemon/tools/secrets_tools.py`, registered `_tool_registry.py`): `secret_generate_ssh_keypair` (OpenSSH-parity fingerprint: hashes the 51-byte wire blob), `secret_generate_api_token` (length-cap + token-boundary guard), `secret_store`, `secret_list`, `secret_read_public` (pubkey-only rule: private material never returned), `secret_retire`.
- **Marker scheme `__ENS_SECRET__<secret_id>__`** — lexically/regex-distinct from both KMS-Lite lanes (`__KMS_REF__…`, `__KMS_ENV__…`, untouched); v1 containment = registry-driven exact-match scrub of stored values from tool results (shape-pattern redaction spine deferred to improve-phase per feature-first ruling).
- **Day-one allow-list** (Q2 ruling): `worker` / `tester` / `leader` / `ari` gain the `secrets` category via per-agent `tools.allow` entries; access vehicle is per-agent opt-in, `TOOLS_REQUIRED` gating unchanged.
- **Test evidence** — tester verdict PASS-with-preexisting 187/187 @ `06b2886e2` (16 workers; 4/4 invariant probes: confinement/gating/marker/real-seam boot; mutation-verified pins incl. PB-F1 error-envelope family and concurrent-mint; PG lanes on throwaway `ens_t_*` roles/DBs with ambient live-DB fence); test-review 4 items landed with poison-proof fence evidence; tidier 4 pre-merge items landed; governor CONFIRMED-APPROVED-FOR-MERGE (production, 2 rounds).

---

## [0.20.4] — 2026-10-17

### Added — live-view mermaid rendering + MD rich-content + responsive design (`feature/liveview-mermaid` @ `c37fbec7d`, merged to `latest` @ `dd1dd88dd`)

> **CHANGELOG (feature release):** *Live-view subsystem gains client-side mermaid rendering for ``.mmd`` / ``.mermaid`` files (case-insensitive) + ``mermaid`` fenced code blocks inside ``.md`` files; vendored ``mermaid.min.js`` 12.1.0 (the major that the charter lane's ``mmdc-v12`` renders with, so in-browser SVGs and charter-rendered PNGs agree on syntax + theme); responsive mobile-first CSS for both wrappers (fluid layout, scrollable diagram container, touch-friendly tap targets). Vendored asset serves from ``/views/assets/mermaid/mermaid.min.js`` (same /views auth/OAuth-proxy gate as the rest of the subsystem — no CDN). Standard MD set (headings, lists, code, links, images) unchanged; GFM tables work via the existing marked 12 ``gfm: true`` default. Per-file wrapper selection: ``.md`` files WITHOUT mermaid fences keep the pre-feature wrapper byte-identical; files WITH fences get the mermaid-aware variant.*

- **Vendored mermaid.js asset** (`mermaid.min.js` 12.1.0, IIFE bundle, ~5.24 MiB) at `daemon/services/live_views/assets/mermaid/mermaid.min.js` (sha256 `6484afc32872a3aa16cac9a76ba1816a1ed4cc870a6593cc2e17757750f518b2`, W3C SRI `sha256-ZISvwyhyo6oWysmna6GBah7UzIcKZZPMLhd1d1D1GLI=`). Provenance + per-byte verification documented in `daemon/services/live_views/assets/mermaid/PROVENANCE.md` and `SHA256SUMS`. Asset is re-verified on every request via `_verify_vendored_mermaid_sha256` — a corrupted/missing on-disk asset fails closed (HTTP 500 + operator log line).
- **Vendored asset endpoint** at `/views/assets/mermaid/mermaid.min.js` (GET + HEAD). Mounted on the same `live_views` router as the rest of `/views/*` so it rides the same auth/OAuth-proxy gate. `Content-Type: application/javascript; charset=utf-8` + standard hardening headers (nosniff, cache-control).
- **Fix-pack amendment (reviewer-approved, same branch):** asset serving is memoised read+sha256 ONCE per `(st_mtime_ns, st_size)` file version and now serves a strong `ETag` with `If-None-Match → 304` on GET+HEAD (`Cache-Control` stays `private, max-age=60` — revalidation is the mechanism, no `immutable`/long max-age since the URL has no version segment) + a `default-src 'none'` document-CSP on the asset endpoint; fail-closed hash verification unchanged. `PROVENANCE.md` gains `Retrieved:` + `License: MIT` sections + bundled MIT `LICENSE.txt` + sha256-family note; stale raw-param docstring and mermaid-variant CSP comment corrected.
- **Fix-pack amendment (tester must-fix, same branch)**: `.mmd` wrapper CSP `style-src` drops the nonce (was `'self' 'unsafe-inline' 'nonce-{nonce}'`) → now `'self' 'unsafe-inline'`. Per CSP3, when a directive contains both `'nonce-X'` and `'unsafe-inline'` browsers IGNORE `'unsafe-inline'` and require nonce matches — mermaid's runtime-applied inline `style="..."` attrs on SVG nodes have no element-level nonce, so the prior posture silently black-filled every SVG. The inline `<style>` element in the wrapper HTML loses its `nonce` attribute (unused; `<script>` nonces unchanged). `securityLevel: 'strict'` and the script policy (`script-src 'self' 'nonce-{nonce}'`, no `'unsafe-inline'`) are untouched. Test pins the new style-src shape (must include `'unsafe-inline'`, must NOT carry a nonce). **Same fix applied at the second site `_MARKDOWN_MERMAID_CSP_TEMPLATE`** (mermaid-aware MD wrapper, where mermaid-fence rendering carries the same risk): `style-src` drops the nonce; script-src keeps the jsdelivr CDN allowlist + per-request nonce (marked/DOMPurify still CDN-loaded); inline `<style>` loses its `nonce` attr; a new test (`test_md_with_mermaid_fence_csp_style_src_allows_unsafe_inline_and_no_nonce`) pins script-src unchanged and style-src posture. `_MARKDOWN_CSP_TEMPLATE` (no-fence MD wrapper) keeps the prior posture intentionally — no mermaid runtime on that path means no dynamic inline styles are applied, so the CSP3 downgrade is benign in practice (flagged in the dispatch sweep). MD templates (both no-fence `_MARKDOWN_WRAPPER_HTML` and mermaid-aware `_MARKDOWN_MERMAID_WRAPPER_HTML`) gain one-line CSS containment for mobile: `article { overflow-x: auto }` — wide GFM tables, long code lines, and wide images now scroll inside the article instead of breaking page width at 390 px (mobile is a PRIMARY device). CSS delta is two lines (one per template); `pre` already had `overflow-x: auto`. The `test_md_without_mermaid_fence_byte_identical_to_pre_phase3` test does not pin bytes (despite its name — it asserts vendored-asset/SRI absence + marked+DOMPurify CDN presence + `mermaid.run` absence), so no re-baseline was needed.
- **``.mmd`` / ``.mermaid`` wrapper** (root-agnostic interception in the shared serving seam — any registered root gets it for free). WITHOUT `?raw=1` returns a responsive HTML wrapper page that fetches the raw source from the SAME URL + `?raw=1`, loads the vendored mermaid asset, initializes mermaid with `securityLevel: 'strict'`, and renders client-side. WITH `?raw=1` returns the raw bytes with sane MIME (`text/plain; charset=utf-8`) — identical to pre-feature raw serving.
- **MD renderer extension** for ``mermaid`` fenced code blocks (Phase 3 extension of `_render_markdown_wrapper`). A ``.md`` with ``mermaid`` fences (backtick or tilde) loads the vendored asset + calls `mermaid.run({nodes: [...]})` after marked.parse + DOMPurify.sanitize; ``.md`` files WITHOUT mermaid fences keep the pre-feature wrapper byte-identical (no vendored asset fetch, no CSP change). The DOM walk rewrites ``pre code.language-mermaid`` → ``pre.mermaid`` before invoking mermaid.run.
- **Responsive CSS** for both wrappers: viewport meta tag, fluid layout (`max-width: 100%`), horizontally-scrollable diagram container (`overflow-x: auto`, `-webkit-overflow-scrolling: touch`, `touch-action: pan-x pan-y`), touch-friendly tap targets (min 32x32 on the ``view raw`` link), narrow-viewport media query for `.md` (`max-width: 600px`). Mobile-primary consumer posture preserved.
- **Strict CSP on the mermaid wrapper**: NO external CDN origins for scripts (the vendored asset is served from `self`); only `self` + per-request nonce in `script-src`. The markdown wrapper's existing CDN-marked + DOMPurify allowlist is preserved (vendoring those is out of scope).
- **`ensemble.spec` datas entry** for the vendored asset — `('daemon/services/live_views/assets', 'daemon/services/live_views/assets')`. PyInstaller frozen-binary build verified the asset is present at `_MEIPASS/daemon/services/live_views/assets/mermaid/mermaid.min.js` with byte-identical SHA-256 to the source.
- **Audit item: relative image refs in `.md`**: marked v12 does NOT resolve relative image refs to the `/views/<root>/<rel>` URL by default (the rendered `<img src="...">` carries the raw relative path). This is pre-feature behavior; the wrapper does not rewrite relative refs because that would require server-side parsing of the markdown to find image sources, and the file's container directory isn't a canonical URL anyway (the URL the wrapper was loaded from IS the canonical path). Report: relative-image-ref behavior unchanged from pre-feature; the same constraint applies to the markdown renderer generally and is out of scope here.
- **Tests** (`tests/unit/routers/test_live_views_mermaid.py` 32 cases + 7 new mermaid-fence cases appended to `tests/unit/routers/test_live_views_markdown.py`): wrapper content-type/headers, vendored asset URL/SRI/HEAD/byte-identical, root-agnosticism across two registered roots, MD mermaid-fence (backtick + tilde) → vendored asset fetch + `mermaid.run`, MD without mermaid fences → byte-identical pre-feature wrapper, GFM tables alongside mermaid fences, malicious-payload escaping, viewport meta, extension-not-allowed uniform 404, asset endpoint `Content-Type: application/javascript`, all 343 pre-existing live_views tests pass byte-identical.

### Added — image generation: `generate_image` native tool + `ens-img:image-gen` marker family + FE lightbox (merge @ `772ca92b9`)

> **CHANGELOG (feature release):** *Native image generation ships as a first-class tool lane: `generate_image` (category `image-gen`) calls the llmproxy `/v1/image_generation` endpoint with purpose-bound `IMAGE_GEN_*` env knobs and fail-closed error envelopes; results persist into the `tmp_images` substrate and reach chat sources as native attachments via a generalized `ens-img` marker family (`<!-- ens-img:image-gen:<id> -->`), with a frontend scanner that inlines generated images in the chat transcript (comment-node AM11 pattern) behind a lightbox with text-fallback.*

- **`generate_image` native tool** (`daemon/tools/image_gen.py`, `ad196b152`): category `image-gen`, purpose-bound env (`IMAGE_GEN_*` knobs documented in `.env.example`), fail-closed envelopes; tool-registry category wiring (`_tool_registry.py`).
- **Marker-family generalization** (`daemon/sources/dispatcher.py`, `b1802fe07`): the chart-render marker mechanism generalized to a family — `ens-img:image-gen:<id>` extraction on both seams (chat dispatch + web/API), delete-after-delivery, web-leg resolution; dispatcher SHA baseline re-sealed (`9c7048401`).
- **FE lightbox** (`ens-img-marker.ts` + `chat-interface.component.ts`, `1a1555b3c`): comment-node inline image scanner (AM11), lightbox viewer, graceful fallback.
- **Agent surface** (`daa50ff76`): image-gen allowlist token + prompt-note sections across agent `meta.json`/`tools_note.md` + prompt-artifact baseline regen.
- **Tests + review hardening**: 86-case unit pack (`c054946eb`); marker-family extraction tests; dispatcher integration harness (both seams, delete-after-delivery, web leg); lane A/B runner with 5-image budget; council review NEEDS-FIXES + frozen-tuple byte-compat pins; tester phase-4 certification (`8c54dae43`).

### Added — Computer Use MCP built-in server + kill-switch gate + version-pin policy (merge @ `eb8a0db1b`, follow-up `b6d1d7a82`)

> **CHANGELOG (feature release):** *Computer Use becomes a built-in MCP server (`daemon/mcp/builtin_servers/computer_use.py`) launched via pinned npx args, gated by a kill-switch check in `preload_mcp_tools` (closes the post-boot flip cold-spawn hole), with cross-platform operator docs. A `mcps-client remote-hands` design proposal lands as docs.*

- **Built-in CU server** (`cdc9e8633`): builtin server + `tool_adapter`/`mcp_service`/`routers/mcp_servers` wiring + privilege-stamp fix + D7 test-connection guard.
- **S1 kill-switch gate** (`5ab043122`): `preload_mcp_tools` gate — no cold-spawn after post-boot flips.
- **S5 version-pin policy** (`3f3b1acf0`): D7 test-connection guard pin-matched via shared `CU_NPX_ARGS`; S2 docstring truth-fix (`is_available()` is a 3-gate AND).
- **Docs + tests**: per-OS operator sections (`docs/mcp-computer-use-server.md`); `docs/mcps-client-remote-hands.md` proposal; tester verification cycle + TCC lessons (`8d293bd86`).

### Fixed — maintenance UI vacuum-ungate: checkpoint-cleanup wizard Continue no longer blocked on zero-reclaimable dry run (fix @ `31bedca78`, certification @ `f2dd8965b`, merged @ `0972b4a31`)

> **CHANGELOG (fix release):** *The maintenance console's checkpoint-cleanup wizard no longer blocks Continue at step 2 (dry-run review) when the dry run projects ZERO reclaimable bytes — zero-reclaimable is a legitimate projection and Continue proceeds. Certified by zero-reclaimable e2e legs (w8/w9) + a mixed-projection edge pin.*

- **Ungate** (`31bedca78`): checkpoint-cleanup component — step-2 Continue enabled on a zero-reclaimable dry-run projection (component + spec).
- **Certification** (`f2dd8965b`): zero-reclaimable e2e legs (w8/w9), mixed-projection edge pin, stale comment fix, results+packs ledger.

---

## [0.20.3] — 2026-10-14

### Added — agent-prompt immutability: immutable agent .md templates + DB versioned prompt overrides + approval workflow (`feature/agent-prompt-immutability` @ `9003cd765`, merged to `latest` @ `27d2452a7`)

> **CHANGELOG (feature release):** *Agent prompt artifacts (`soul.md` / `rule.md` / `workflow.md` / `tools_note.md` / `skill-set.yaml` / `skills-template/*`) become IMMUTABLE in-tree templates — in-flight edits redirect into a DB-backed version store (`prompt_versions` + `prompt_audit_events`) with an approval workflow and a CORE/MEMORY approval-scope split: CORE artifacts submit versioned drafts for approval, while MEMORY (`memory.md` + memory carve-outs) keeps direct writes. `inner_soul` soul/workflow/user rewrites now compose on the effective chain and submit prompt-version DB drafts; `agent_mother.agent_modify` is rewired to DB submission (`modifiable_files` shrinks to `[memory.md]`). A Persona UI exposes the version/approval estate, and predefined agents auto-reset to their template baseline on drift (Ud#5B). A generated 156-artifact prompt-artifact baseline + read-only stage.sh drift leg + boot-WARN alarm protect the tree; agent .md files in live installs are never written directly.*

- **Resolution core + storage**: `PromptVersion` / `PromptAuditEvent` SQLModel + checksummed additive migration; `PromptVersionRepository` (13 sync methods + `create_and_submit`) and `PromptVersionService` thin async facade wired into manager + API; loader hook + cache invalidation + drift tests; per-key `pg_advisory_xact_lock` serialization for prompt-version writers (R3).
- **Approval workflow + REST surface**: 12-endpoint `/api/prompts` family incl. PATCH/DELETE/pending-count; 5 minted `prompts:*` scopes with scope/class manifest cages; approval state machine with typed transitions (`pending_approval→draft` is the only retract lane); audit trail with UPDATED/DELETED enum + allowlist-split filters.
- **inner_soul rewrite + auto-reset protocol**: soul/workflow/user → prompt-version DB drafts (memory carve-outs unchanged; storage-key normalization for `access_memory` round-trip); predefined-agent auto-reset to template baseline (Ud#5B); versioned-prompt-artifact immutability guard (T9a) + `agent_mother.modifiable_files` → `[memory.md]` (T9b).
- **Persona UI** (Phase C P1+P1.5): version/approval console surface; 188 persona-memory files swept into `legacy/` per user directive 2026-10-14.
- **Custom-agent lifecycle**: `custom_agent_configs` table + repo + factory; POST/GET/DELETE lifecycle API with Pydantic body validation; hybrid registry (file-scan ∪ `custom_agent_configs`); spawn-path threading `is_custom_agent=True` end-to-end.
- **Drift protection** (T13/S9): prompt-artifact drift core + committed 156-artifact baseline; read-only stage.sh drift leg (non-blocking); boot WARN on baseline drift.

### Fixed — watcher-loss NEW variant RCA: ghost-descendant liveness poisoning + self-referential WC strand (`feature/watcher-loss-terminal-strand` @ `90813a009`, merged to `latest` @ `4ebd7f2f1`; RCA incident `b6e24d78`, receipt `6fc156f3`)

> **CHANGELOG (fix release):** *Closes the watcher-loss NEW-variant RCA — a ghost instance under a terminal parent held its emission gate open, wedging the strand as a self-referential WC (watch-child) loop with no re-arm and silently stranding missions. Four-part fix: strand-aware liveness walk (terminal-ancestry orphans — ghost descendants — are excluded from liveness and can no longer hold missions), MissionStrandHealer (single-invocation heal sweep + event-driven downgrade hand-off, per-root heal lock), pre-stamped recursive liveness eval (stamp→downgrade churn eliminated), and wedge-pass cooldown re-arm on the WC watchdog (a re-parking wake re-fires). Supersedes the F14 nothing-re-fires class. Live heal at merge: 1 wedged mission healed, straggler terminated, final wake delivered as one user paste.*

- **Strand-aware liveness walk** (`a05ef5903`): ghost-descendant liveness-poisoning exclusion — terminal-ancestry orphans cannot hold missions.
- **MissionStrandHealer** (`0ec93ea9f`): single-invocation heal sweep + event-driven downgrade hand-off; shared strand race-guard + read-side age mirror + per-root heal lock (`e4bc31387`).
- **Pre-stamp liveness eval** (`654d6cc9d`): recursive evaluation stamped before downgrade — stamp→downgrade churn eliminated.
- **Wedge-pass cooldown re-arm** (`02ec363f3`): a re-parking wake on the WC watchdog re-fires instead of dying silently (DELTA 2).
- **Test wall**: RED harness terminal-strand wedge repro; regression pack (epoch pin, lease inertness, ACL residue-zero, backstop reachability, lane adjudication); age-mirror contract + true-concurrent heal pins; commission-mandated gap tests (scenario-3 conjunct, healer fail-closed probes, idempotency edges); tester final gate + delta re-gate PASS — 16 commits, +4462/−26.

## [0.20.2] — 2026-10-13

### Changed — agent-base v2 migration: base agents = copy of [v2] agents; opencode stack (incl. session/API coding-worker lane + comm service) retired to legacy/ (`feature/agent-base-v2` @ `a9d8aa860`, merged to `latest` @ `abeb66023`)

> **CHANGELOG (consolidation migration):** *The v2 agent prompts are promoted to base — `agents/developer`, `agents/reviewer`, `agents/planner`, `agents/tidier`, `agents/approver` become byte-identical copies of their `[v2]` counterparts (both kept; `[v2]` dirs untouched, so existing instances and ensembles notice nothing). The opencode-era base agents plus the full opencode stack — including the opencode session/API coding-worker lane and its comm service — are archived under `legacy/` (copy-then-verify sever) and severed from the daemon; the active tree is opencode-free. No DB migrations; rides the fast-upgrade pipeline (stage.sh manifest machinery self-adapts — `agents_tree_sha256` / `agents_manifest` recompute automatically).*

- **Phase A — legacy archive.** opencode-era `agents/developer`, `agents/reviewer`, `agents/planner`, `agents/tidier`, `agents/approver` (the v1 base) plus the opencode comm service were copy-verified into `legacy/` (root-level legacy source dir). Pre-mission and post-mission per-dir sorted sha256 manifests re-verified at mission end — archive intact.
- **Phase B — base := v2 copy.** `agents/developer`, `agents/reviewer`, `agents/planner`, `agents/tidier`, `agents/approver` (the base, NOT the `[v2]` dirs) were replaced byte-for-byte with the content of their matching `[v2]` dirs. `version` in each base meta.json now reads `"2.0.0"`. v2 dirs untouched and remain the live operational lane; existing instances + ensembles notice NOTHING (v2 checksum invariance: pre == post).
- **Phase C — opencode excision.** daemon code paths that wired the opencode service were removed (tier-1 wiring severance → full-stack source-tree removal, `2e3bf2b2a` → `7b0a102c3`); opencode-specific tests and historical docs archived to `legacy/`. `default_agent_versions` runtime value unchanged (verified via read-only `GET /api/settings/default-agent-versions`).
- **Phase D — references + docs refresh.** `docs/agents.md` §2/§3/§3.4/§3.5/§7, `docs/skill-evolution.md` example, `docs/agent-prompt-writing-guide.md` §9 refreshed to describe v2 reality. Active-tree guidance docs (`testing-guide.md`, `docs/mcp-integration.md`) updated to drop retired `OpenCode server` / `OpenCode configuration` phrasing. `docs/mcp-kb-usage.md` verified false positive (documents upstream `sst/opencode`, not our service) — UNTOUCHED. Drift implications (base is now a frozen mirror of v2) noted for maintainers; not solved in this mission. Full docs-consistency sweep and R10 allowlist committed at `evidence/docs-consistency-sweep.md` + `evidence/r10-allowlist.md`.
- **Phase E — verification evidence.** Per-dir sha256 manifests prove v2 zero-touch (pre == post), base==v2 (per-agent diff empty), legacy/ archive intact, full test wall green, fenced dev-lane boot-scan clean, `default_agent_versions` runtime invariant. Evidence committed at `.agents/shared/planning/agent-base-v2/evidence/`.
- **Security hygiene folded into the merge** (`84e9c54aa`): leaked-key redaction + env-family scrub + fail-closed secret-scan gate (S-1..S-3, EXP-1..EXP-5), with the scan script + output committed as Phase E evidence.

_Mission gates: 18/18 DoD, R10 gate green. Migration rides the promote ceremony via the fast pipeline; stage.sh manifest machinery self-adapts (`agents_tree_sha256` / `agents_manifest` recompute automatically)._

## [0.20.1] — 2026-10-13

> **Backfill note:** no CHANGELOG section was cut at release time (cosmetic debt flagged at the v0.20.2 cut). Minimal pointer entry — full per-commit detail in `git log v0.20.0..v0.20.1`; tag annotation: *"v0.20.1: upgrade-speed-overhaul maiden-promote vehicle (merged @ `885a19d4f`)"*.

### Changed — upgrade-speed overhaul: interactive VACUUM FULL escalation + maintenance-console tabs (`merged @ 885a19d4f`)

- **Maintenance console:** tab shell (sticky tab bar, lazy-mounted tabs) + interactive VACUUM FULL escalation after the destructive prune, guarded by schema/engine/headroom/ENOSPC checks with a pgcode-presence-first discriminator.
- **Protection layers:** version-control host armor (L3 snapshots, L4 tree-loss alert, L1 arm runbook) and Layer-1 ownership separation — HOME-shape-tolerant install-root resolution, executor uid pinning, wrapper allowlist enumeration, realpath traversal hardening, unconditional owner-invoker refusal.
- **Upgrade lane:** council remediation round — 8 criticals + behavioral pins + scrubbed re-adjudication.

## [0.20.0] — 2026-10-12

> **Consolidation note:** v0.18.4 and v0.18.5 shipped via tag annotations without CHANGELOG sections; the entries that accumulated under [Unreleased] during that window (spawn tooling consolidation, dry-run projection v3.2, the `/api/snapshots` read surface + deprecation, and the tool-call pairing full-history heal) technically shipped in those tags and are carried here with per-entry attribution. This section also documents the five lanes merged between `v0.18.5` and this tag (`a5f75df11`): OAuth2 authentication, blob-prune default-on, DB vacuum maintenance, the usage-limit watcher silent-loss fix, and the checkpoint-cleanup wizard navigation fix.

### Added — OAuth2 authentication: OIDC browser sessions + static bearer lane (default-OFF) (`feature/oauth2-auth`, merge `256631d86`; audit branch `origin/feature/oauth2-auth` @ `c4ad992c9`)

> **CHANGELOG (feature release):** *Optional OAuth2/OIDC authentication for the daemon — browser sessions via an external IdP (Auth Code + PKCE, server-side PG/SQLite-backed sessions) plus a static-bearer lane for machine clients (operator-minted CLI, hash-only storage, closed 14-scope taxonomy). DEFAULT-OFF: with `ENSEMBLE_AUTH_ENABLED` unset the build is behaviorally inert — the middleware and auth routes are conditionally registered, and the full P5 estate (auth-ON + auth-OFF fixtures) proves zero disruption to existing consumers. Enabling is a pure operator config+restart act documented in `docs/runbooks/oauth2-auth.md`. Consumer compat: 0 BLOCKED / 0 code change — module-lane endpoints (webhook ingest, MCP-KB mounts) sit beyond OAuth2 with their own per-module keys and are unchanged whether auth is OFF or ON (lane-isolated per D19/D20); ZERO OAuth2 config delta for existing deployments.*

- **Auth config + boot preflight (Phase 1)** — `AuthConfig` BaseSettings section (23 `ENSEMBLE_AUTH_*` env vars, unknown-name refusal), static misconfiguration enumeration at boot preflight with fail-fast exit 78 BEFORE port bind (enabled-without-credentials, HTTPS/Cookie-consistency, SameSite pairing, TTL bounds, and the CORS guard: auth-ON + wildcard origins + credentials + empty `ENSEMBLE_AUTH_ALLOWED_ORIGINS` refuses to boot — naming explicit origins is REQUIRED for auth-ON).
- **Session store + OAuth client (Phase 2)** — `auth_sessions` / `auth_states` / `auth_bearer_tokens` / `auth_bootstrap_consumption` tables via checksummed additive migrations (inert on both PG and SQLite), repository with CAS semantics, TTL sweepers (`SKIP LOCKED` + idempotent deletes — dual-instance same-DB safe, no coordination needed), and `OAuthClient` (discovery cache, JWKS cache + kid-miss forced refetch, ID-token validation).
- **Middleware + auth routes + protection matrix (Phase 3)** — `AuthMiddleware` (conditionally registered, route-metadata-derived three-class path sort: public / module-lane / oauth2-protected, derived set logged at startup), auth routes (`/api/auth/login`, `/callback`, `/logout`, `/session`, `/me`), redirect-URI resolution chain with operator overrides, FR-24 401-vs-302 contract (browser 302 with `return_to`; machine 401 + `WWW-Authenticate`), `503 LOGIN_NOT_CONFIGURED` / `503 IDP_UNREACHABLE` / `400 STATE_REPLAYED` failure codes, readiness `auth_dependency_status` sub-field (verdict never flips), Origin-guard CSRF predicate for browser-lane state changes.
- **Machine-bearer lane (Phase 4)** — `python -m daemon.auth.cli issue-token|revoke` (`--ttl` integer seconds; plaintext printed ONCE; `sha256` persisted; one-shot `--bootstrap` first-admin mint with sentinel-row anti-replay + runbook env-var removal step), `@require_scope` decorators per route family with a router-mount scope-declaration regression cage (census pinned at `daemon/api.py:3385-3449`; the cage test is the authority).
- **Test estate (Phase 5)** — fake OIDC provider (in-process ASGI, no Docker) + dual fixture estates (`tests/helpers/auth_fixture.py` + `FakeOidcStub` MockTransport for unit/integration; `tests/fakes/fake_oidc_provider.py` + `tests/conftest_auth.py` ASGI for e2e), 4-scenario lifecycle e2e, auth health-probe canary, perf smoke, module-lane isolation + cross-lane negative matrix, reserved-seam cages — 110 passed + 1 skip (P5 estate) + 44+1 merge-gate riders; boot-e2e canaries verified UNMODIFIED (zero diff) and green against an auth-ON daemon.
- **Line-drift correction note:** the plan set's pre-existing line-number drift (e.g. `jobs_crud.py:275-278` → `:595`) was corrected in the planning documents only — no code change in this PR.
- **R3 follow-up ticket (filed-not-closed):** auth does NOT close the forge-source lane — a stolen `jobs:write` bearer can still forge `source=api` (`daemon/routers/jobs_crud.py:595` client-supplied source; hard-coded `"api"` at `daemon/routers/messages.py:691`). Tracked as project critical note `257488dc-c0b1-42d4-8581-f97cb4d55f72` (2026-10-11); fix = derive source from `AuthContext.subject` when auth is ON. Ships as a filed follow-up, deliberately not closed by this feature.
- **Operator docs:** `docs/runbooks/oauth2-auth.md` — enable procedure, default-OFF verification walkthrough, token mint + rotation, CORS notes, failure-mode troubleshooting, module-lane key-management annex. Rollout checklist: `.agents/shared/planning/oauth2-auth/rollout-verification.md`.

### Changed — Checkpoint blob prune is destructive-by-default; LIMIT-batching hardening (`feature/blob-prune-default-on`, merge `a5f75df11`; feature branch `112560904`)

> **CHANGELOG (behavior change):** *The dual-arm `blob_prune_dry_run` config/env gate is REMOVED — the pair-excess blob prune is now destructive-by-default (the gate had never been disabled in recorded history; the 23GB prod `checkpoint_blobs` bloat was exactly this gate blocking reclamation). Pair deletes run LIMIT-batched (batch=1000 per chunk under SERIALIZABLE + retry) so large drains cannot stall the checkpoint lane; `partial_completion` lands in `summary_json` when a cycle stops early; the `_is_due <= 0` schedule value is a never-due guard. Emergency stop = `systemctl stop` (PRIMARY) or `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0` (secondary), documented truthfully in `docs/runbooks/maintenance-console.md`. FIRST destructive drain on the ~23GB backlog fires on the 24h cleanup cycle after this release goes live — watch the first run per runbook §7 (backlog accrues ~460MB/day until the first cycle drains it; ~23k chunks expected).*

- **Destructive-by-default** (`3ce50e8db`): dual-arm gate deleted (`constants.py` / `checkpoint_prune.py`); `None` → destructive coercion (`5ca8fc146` council fix cycle 1).
- **LIMIT-batching** (`3ce50e8db` + `fad239734` review C1 alias-binding fix): batched anti-join deletes under SERIALIZABLE + retry; never-due guard for schedule `<= 0` (`5ca8fc146`).
- **Tests** (`4da33b359`, `b5cbe30cf`, `385872a28`): grep-proof destructive-default contract + LIMIT-batching tests; REAL-PG gate PASS-WITH-PREEXISTING (`a422402a0a`, drift_events RED base-attributed + quarantined).
- **Docs** (`3c06a2adf`, `0a2e78a0a`, `db9a20964`): runbooks rewritten for destructive-default reality; truthful emergency-stop procedures; tidier cadence/ghost-prose pass.
- **FE** (`aa1c78684`): dead `/status` config-block fields dropped from the maintenance console.

### Added — Threshold-gated DB vacuum maintenance + checkpoint-trio autovacuum tuning (`feature/db-vacuum-maintenance`, merge `1bab756c6`)

> **CHANGELOG (feature release):** *A threshold-gated `VACUUM (ANALYZE)` maintenance job (dead-tuple ratio + bloat heuristics) plus tightened `autovacuum_vacuum_scale_factor` reloptions on the LangGraph checkpoint trio (`checkpoints` / `checkpoint_writes` / `checkpoint_blobs` — the documented prod-scale bloat source, ~206MB/42s instance-history walk). The repair lane is vacuum-exempt (repair transactions must not be blocked). Tester REAL-PG gate: 226/0 PASS.*

- **Vacuum job** (`4f4ed5f09`): threshold-gated VACUUM (ANALYZE) maintenance run; autocommit lanes + grammar hardening (`0b660308e` review r2).
- **Checkpoint-trio reloptions** (`20261011_102502` migration, additive `ALTER TABLE ... SET` only): evidence-proportional — only the trio is tightened; other tables keep PG-stock 0.20.
- **Repair-lane exemption**: repair-path transactions skip the maintenance vacuum.

### Fixed — Usage-limit watcher silent-loss: retry-supersede double-silence (merge `29651c06e`, mission `e8a61da3`)

> **CHANGELOG (fix release):** *Closes job `043d64a8` RCA — "usage-limit retry-supersede double-silence" (silent cancel × F6 watch re-key × transport-only last-event CAS) + Pattern (f) premature finalize + witness no-publish. Option A depths 1-4 + E1/E2 per the user-picked options-matrix: stored watches quarantine/re-arm on retry-supersede, the E1 mission-live finalize gate, redirect-gate verification, and the `stored_watches` table (`20261011_000001` migration) as the durable watch registry. Tester gate ALL GREEN 7/7 stale-test closures; reviewer APPROVE-WITH-NOTES 0C/0W.*

- **Stored-watch registry** (`20261011_000001`): durable `stored_watches` table (status/retry/original-work keys + unique watcher constraint) replacing transport-only watch state.
- **Retry-supersede quarantine/re-arm**: a usage-limit retry that supersedes a watch no longer double-silences it — the stored row is quarantined and re-armed on the retry's own watcher.
- **E1 mission-live finalize gate**: premature finalize (Pattern f) closed at the mission-terminal arm.
- **Fixture hygiene** (`1797cc357`, `1910c2dbe`, `185f4461b`, `d737cfb79`, `e3fea4556`, `21cab581c`, `208e49122`): de-time-bombed vfy fixtures, E4 side-effect tokens + Pattern-F guard fixtures synced to the shipped contract, harvested artifacts from the dead-mission /tmp trees, tidier rounds.

### Fixed — Checkpoint-cleanup wizard unintended stepper navigation (`feature/fix-step-page-unintended-nav`, merge `72b062000`; audit branch `bea2db5d3`)

> **CHANGELOG (fix release):** *The checkpoint-cleanup wizard no longer advances/reverses steps on unintended triggers — original symptom CLOSED with A/B base-RED proof; 3 fix rounds (C1+C2 stale-read fix, CDK `editable="false"` setter-ratchet drop, W-dispositions). Playwright 20/20; reviewer APPROVED 0-critical.*

- **Rounds** (`bea2db5d3`, `6205578da`, `c8b6f21f8`... family `72b062000`): unintended stepper navigation eliminated; stale-read C1+C2; `editable="false"` ratchet defect (CDK setter) dropped.

### Changed — Spawn tooling consolidation (`feature/unify-spawn-tools`, merge `7e97e811d` — shipped in the v0.18.5 tag-annotation release; entry carried here from [Unreleased])

> **CHANGELOG (feature release):** *`spawn_hot_instance` is removed; `spawn_instance` becomes the single spawn tool and gains optional params (`task`, `auto_dispatch`, `snapshot_id`, `tags`, `verify`, `allow_cross_project`) for one-step spawn+dispatch. Snapshot warm-start is gated per target agent by `meta.json` `snapshot_enabled: true` (enabled for tester + developer[v2]). The `spawn_hot_*` metric/log identifier family is renamed to a `spawn_instance_*` equivalent.*

- `spawn_hot_instance` is **REMOVED**. `spawn_instance` is the single spawn tool and gains optional params: `task: str|None` (auto-dispatched as the child's first turn by default — one-step spawn+dispatch), `auto_dispatch: bool = True` (only meaningful when `task` non-empty), `snapshot_id: str|None`, `tags: list[str]|None`, `verify: "metadata"|"git" = "metadata"`, `allow_cross_project: bool = False`. Omitting `task` keeps today's two-step `spawn_instance` + `send_message` ritual behavior.
- **Snapshot warm-start is gated per target agent** by `meta.json` `snapshot_enabled: true` (enabled for tester + developer[v2]). Gate off with no steering params ⇒ plain cold spawn, no `[snapshot]` line; gate off with `snapshot_id`/`tags` ⇒ one informational line (snapshot steering ignored), no snapshot work. Gate on ⇒ warm-start when a matching snapshot exists; the tool output carries a citation line `[snapshot] started: warm — Warm-started from snapshot {id} (age {age}d; tags {tags})` or `[snapshot] started: cold — No matching snapshot (searched: {q}; reason: {reason})` (the `searched:` fragment is omitted when no internal search ran, e.g. explicit-id miss).
- **Metrics / log identifier rename** — exactly two renames: JSON log payload `event: spawn_hot_auto_dispatch → spawn_instance_auto_dispatch`, and log prefix `[SnapshotAutoDispatch] → [SpawnAutoDispatch]`. DB counter keys (`spawn:snapshot:<id>`) and the `[SnapshotSpawnWarm]` log line are UNCHANGED. Same underlying semantics, new surface name. Dashboards and grep recipes that target the old names need to migrate.
- **Prompt/UI copy** — the 6 agent-prompt anchors (leader `rule.md` / `workflow.md`, ari `rule.md`, tester `workflow.md`, developer[v2] `rule.md` / `workflow.md`) and the settings page copy that named `spawn_hot_instance` are rewritten to describe the merged `spawn_instance` tool (snapshot warm-start gated per target agent by `meta.json` `snapshot_enabled: true`). Historical `spawn_hot_instance` mentions in older changelog entries and dated `.agents/` records are retained (append-only history).

### Fixed — Dry-run projection v3.2 (`feature/dry-run-projection-v3.2`, merge `6011721d9` — shipped in the v0.18.5 tag-annotation release; entry carried here from [Unreleased])

> **CHANGELOG (fix release):** *Maintenance console dry-run now reports the two-pass reality: `bytes_reclaimable_now` (what this run frees — unchanged semantics for `would_free_bytes` and the `expected_bytes` confirm echo), `bytes_reclaimable_after_row_prune` (labeled estimate of what a follow-up run frees), and their sum `bytes_reclaimable_total`. On a never-pruned database the first run deletes excess rows and frees 0 blob bytes **by design** (retention composition E→D); run cleanup a second time to reclaim the orphaned blob bytes — the console now says so explicitly and offers a "run cleanup again" affordance. The confirm-echo `expected_bytes` meaning is UNCHANGED — scripts and operators see no behavioral change. Auto-cycle behavior unchanged. Activation: rebuild + restart; no DB migration (additive JSON keys only).*

- **v3.2 projection (additive; projection-class, NEVER gate-bound)** — three new integer fields on the dry-run §3 response (`bytes_reclaimable_now` = exact alias of `would_free_bytes`; `bytes_reclaimable_after_row_prune` = per-pair "referenced by excess only" projection computed via the new `CheckpointerAdapter.count_blobs_referenced_only_by_excess` helper; `bytes_reclaimable_total` = derived sum). The echo gate remains bound to `would_free_bytes` ONLY (AM-3 unchanged, INV-13 reaffirmed). R-4: skipped pairs (ZERO_REFS / MAX_REFS-capped) contribute 0 to `after` / `total` and surface in `skipped[]` for the FE honesty flag.
- **manual_execute run-summary projection echo block** — two-field additive `{"projection": {"bytes_reclaimable_now_at_dry_run": N, "bytes_reclaimable_after_row_prune_at_dry_run": N}}` sourced from the snapshotted `dry_run_summary_json` (NOT recomputed at execute). Auto rows: projection block ABSENT (R-5).
- **Operator runbook** (`docs/runbooks/maintenance-console.md`): added "Two-pass reality on a never-pruned database" section explaining the two-run journey for first-time manual cleanups.

### Added — `/api/snapshots` read surface (`feature/snapshot-uiux`, merge `c55f2b0a0` — shipped in the v0.18.5 tag-annotation release; entry carried here from [Unreleased])

New canonical HTTP surface for snapshot reads (relocated from `/api/settings/*` per the v0.16 R15/R16 follow-up). Three endpoints ship in this change:

- **`GET /api/snapshots`** — paginated, filterable list of snapshots. Filters: `project_id` / `agent` / `status` (multi) / `tags` (multi) / `created_after` / `created_before` / `limit` / `offset`. Eight sort keys (pattern-validated at the FastAPI layer; 422 on any out-of-set value). Response envelope: `{"items": [...], "total": N}`. Envelope key is `items` (D-1 binding — `snapshots` is reserved for a future wire contract).
- **`GET /api/snapshots/{snapshot_id}`** — single snapshot detail. `?include=digest` opts into the unbounded JSONB digest payload (default: `digest: {}`, present-but-empty so the FE can branch on presence without `undefined`-guards).
- **`GET /api/snapshots/metrics`** — R16 monitoring counters (capture counts by agent + warm-spawn counts per snapshot). New canonical home for the metrics surface; replaces the old settings-router endpoint.

FE additions:

- **New global `/snapshots` page** — gear menu entry, bookmarks icon, peer of `/settings` and `/schedules`. Hosts the snapshot-creation toggle (R15 — relocated from `/settings`), the snapshot usage metrics strip (R16 — relocated from `/settings`), the filter bar + paginator, the table, and the detail drawer. The page owns the list fetch + paginator state + filter signals (amendments #8, #10). The table is a pure presentational surface; the drawer component owns its own detail + lazy-digest fetch (amendment #7).
- **`SnapshotService`** (`providedIn: 'root'`) — single source for the FE↔BE surface (URL constants live on the service class). Owns `list(filters)` / `getById(id, opts)` / `getMetrics()` / `buildParams(f)` / `computeAgeCutoff(preset)`.

### Deprecated

- **`GET /api/settings/snapshot-usage-metrics`** — kept as a 1-line re-export for one release window. The legacy handler emits three deprecation response headers on every response: `Deprecation: true`, `Sunset: Sun, 31 Dec 2026 23:59:59 GMT`, and `Link: </api/snapshots/metrics>; rel="successor-version"` (RFC 8594 Deprecation + RFC 8288 Link; hard Sunset date per the deprecation cycle). Removal planned for the next minor release (follow-up ticket to be filed at end of deprecation cycle).

### Fixed — Tool-call pairing full-history healing (`fix/tool-pairing-full-history-heal`, merge `7dfb3c699` + round-2 `dca6beaf0` — shipped in the v0.18.4/v0.18.5 tag-annotation releases; entry carried here from [Unreleased])

> **CHANGELOG (fix release):** *Closes the 2013-bricking bug class from incident 03d7657f. A full-history adjacency / immediacy check catches mid-history pairing violations the pre-existing O(1) tail-only helpers could not see (the forensic defect: an ``AIMessage(tool_calls)`` committed mid-history whose ``ToolMessage`` never arrived — interrupted-mid-tool shape, 1c/0r). Strict gateways enforce **IMMEDIACY** (every ``AIMessage(tc)`` answered by the IMMEDIATELY-adjacent following ``ToolMessage`` block, no intervening non-ToolMessage between call and answer), not count-pairing. The validator synthesizes a placeholder ``ToolMessage`` immediately after the issuing ``AIMessage`` (id format ``partner-synth-{tc_id}``) and removes any ``ToolMessage`` whose call-site is gone after the adjacency fix-up. The reactive recovery path detects pairing-invalid signatures in the gateway error (``tool call result does not follow tool call`` and three sibling shapes) and runs the same heal ONCE before retrying the LLM invoke. Hot-path cost is bounded by an O(n) probe that runs on every dispatch with O(n) auxiliary structure (a per-probe ``last_non_tool_tc_ids`` set and a ``_build_next_non_tool_after`` array — both O(n) memory, allocated per probe call); the full O(n) heal runs when the probe flags a real violation. The fix lands on this branch and rides the next release vehicle after the already-staged v0.18.3. Activation: rebuild + restart; no DB migration.*

- **New helper module `daemon/tool_pairing_history.py`** — stateless helpers `validate_and_heal_messages` (full-history adjacency fix) and `has_pairing_violations` (cheap O(n) probe). Both honor the strict-gateway adjacency rule. Idempotent re-heal across the helper chain (`partner-synth-{tc_id}` is recognized alongside the pre-existing `pairing-synth-{tc_id}` format used by the in-graph tail guard). Empty all-ToolMessage histories are preserved as-is to keep the at-least-one-message contract callers like `emergency_truncate` rely on.
- **W1 wire-up at the LLM dispatch boundary** — :func:`daemon.graph._ensure_full_history_pairing` runs right before every FIRST `current_llm.invoke(...)` call site (the primary dispatch; the W2 catch at :func:`daemon.graph.create_agent_node` runs the heal separately on the retry path, not at an invoke site). The probe runs unconditionally; the full heal runs only when the probe flags a real violation. The synthesized placeholders ride the C2 return (`pairing_synthesized_msgs`) and any removed messages ride `RemoveMessage(id=...)` sentinels (`pairing_remove_sentinels`) so the LangGraph `add_messages` reducer commits the healed state to the checkpoint in the same superstep. Coverage includes the silent-resume path (where `graph_input=None` bypasses the enqueue-seam tail-guard) and revive dispatches.
- **W2 reactive recovery** — `daemon/llm_error_classifier.py` now classifies the canonical 2013 signature (`tool call result does not follow tool call`) and three sibling shapes (single-quote variant: `messages with role 'tool' must be a response to a preceeding message with 'tool_calls'`; double-quote variant: `messages with role "tool" must be a response to a preceeding message with "tool_calls"`; anthropic parameter-prefix variant: `invalid parameter: messages with role 'tool'`) into a new `ToolPairingInvalidError` exception (NOT a `BadRequestError` subclass, NOT a `TRANSIENT_EXCEPTIONS` member — tenacity never auto-retries it). The agent_node W2 catch (the `except ToolPairingInvalidError as _w2_pairing_exc` site in :func:`daemon.graph.create_agent_node`) catches it specifically, runs the full-history heal on the LLM-bound payload, threads synthesized + removed into the C2 return for persistence, and re-invokes the LLM ONCE. On a second failure the W2 catch raises the SECOND ``ToolPairingInvalidError`` (the most recent rejection) with the FIRST ``ToolPairingInvalidError`` chained as ``__cause__`` via ``raise ... from ...``; both ``.original`` ``openai.BadRequestError``s remain accessible for forensic access. The existing error path / non-retryable upstream pipeline takes over. The W2 catch is mirrored on the post-compaction invoke site (the inner `try/except ToolPairingInvalidError` inside the `ContextLengthExceededError` handler in :func:`daemon.graph.create_agent_node`) as an inner ``try/except`` INSIDE the ``ContextLengthExceededError`` handler (the same FIX 4 chain alignment).
- **W3 emergency_truncate adjacency snap** — `daemon/compaction.py:1740` (post-pop-loop) delegates to the validator. The pop loop drops the bare head; the validator fixes any order violations introduced by the drops (AIMessage(tc) head without adjacent TM → synthesize partner; stranded TM → orphan removal). Pre-existing at-least-one-message contract for tool-only histories is preserved.
- **W4 producer guard** — `daemon.graph.dedupe_incoming_tool_call_ids` is kept as a cheap defensive measure at the agent_node COMMIT boundary (the response AIMessage's `tool_call_ids` are checked against committed history; collisions are deterministically re-id'd as `{tc_id}__dup{N}`). Atomic-commit feasibility for the interrupted-mid-tool producer was assessed: LangGraph commits whatever the node returns in the same superstep, AFTER any guard runs, so the AIMessage(tc) can never be withheld at the producer boundary once committed; W1 synthesis at the next dispatch is the residual net. The dedupe guard is a defensive-only path per the W1(c) clarification (no proven re-mint occurrence).
- **W5 seam-comment fix** — `daemon/services/instance_messaging.py:262` (the silent-resume comment in `_heal_poisoned_checkpoint_tail`) now accurately describes the real coverage: the silent-resume branch is covered by the W1 full-history guard at the LLM dispatch boundary (reads `state['messages']` directly, independent of `graph_input`). The pre-W1 claim that the in-graph tail-only guard "already covered it" was a doc defect.
- **Tests** — `tests/unit/tool_pairing_history/test_full_history_heal.py` covers the primary regression (1c/0r mid-history), the adjacency trap (`[AI-issuer][AI-other][TM-synth][TM-other]`), the defensive duplicate (each AI has its own TM — NOT a violation per strict gateways), the probe, the W3 emergency_truncate snap, the W4 dedupe guard, and the W2 `ToolPairingInvalidError` signature match. Existing pre-existing tail-guard and enqueue-seam tests stay green (extend, don't remove).

### Fixed

- **Drawer detail-fetch staleness guard** — the detail fetch fires on every `snapshotId` input swap; a slow response for snapshot A no longer overwrites the drawer after the user navigates to snapshot B. Increment-and-capture request id, compare on resolve, silently discard stale responses (mirrors the existing `digestRequestId` pattern). Applied to both the constructor effect and the manual retry path.
- **`/api/snapshots/metrics` fail-soft observability** — when the metrics service is unavailable (pre-init or background deinit), the endpoint returns the empty shape (`capture_counts={}`, `spawn_counts_per_snapshot=[]`) with HTTP 200 instead of 500. The degraded state is now visible via a `WARNING` log line so operators can detect the failure mode without a caller-side regression. No caller-visible contract change.

### Migration

- External callers of the deprecated `GET /api/settings/snapshot-usage-metrics` should migrate to `GET /api/snapshots/metrics`. The response body is identical; the URL is the only change. New callers should target `/api/snapshots/metrics` directly and ignore the `Deprecation: true` / `Sunset: Sun, 31 Dec 2026 23:59:59 GMT` / `Link: </api/snapshots/metrics>; rel="successor-version"` headers on the legacy path.

---

## [0.19.0] — 2026-10-11

### Added — Snapshots page v2 redesign, frozen Design A (`feature/snapshots-redesign-v2`, merge `c7b467444`)

> **CHANGELOG (feature release):** *The `/snapshots` page is rebuilt to the frozen Design A v2 spec: plain-HTML table in a dedicated scroll viewport, full-height side-mode drawer with sticky header + 2-region rail, click-triggered info/metrics popovers, persisted filter/URL state with a byte-identical dedup key, and the accessibility fixes (drawer focus trap, component-scoped Esc that never fires under open popovers). v1 contracts (metrics-capture-card hook, `/api/snapshots` surface) are preserved.*

- **Page shell** (`532996ac9`) — control row + filter row + stats strip + URL `queryParams` state; **table** (`27a8909db`) — plain HTML, scroll viewport, status icons, selected-row state; **drawer** (`495ffb8c7`) — side-mode full-height, sticky header, 2-region content rail with `flex:1.5` lock (`2a8349802`, AC-4.2; `.table-area` height-chain completion, AC-3.1–3.5).
- **Click-triggered info + metrics popovers** per spec §2.2 (`a6b44f42d`, S2); **URL-sync key** seeded from post-seed signals with back/forward coverage (`f6ed6aaa9`, S1) and made byte-identical via a single shared key computation (`2111438fa`, AC-6.3 parity).
- **Accessibility** — drawer focus trap + Esc + focus restore on the drawer root, component-scoped (`43e5f2be2` / `a05d4ae9d`, AC-A11Y-3); document-level Esc handler gated so popover closes never double-fire drawer closes (`95d57adb2`, AC-A11Y.3b).
- **v1 contracts preserved** — the metrics-capture-card `data-test` hook survives (`5ec3ddd92`, AC-5.2); unit tests for the new v2 chrome + URL persistence (`bf5a36bc7`); Design A v2 spec finalized with Design B kept as reference (`8c1534f9a`).

### Tested — Snapshots v2 daemon-lane E2E: v1 12-test pack rebased to Design A, 12/12 GREEN (`feature/snapshots-v2-spec-rebase`, merge `baacdd6a4`)

- The five v1-locator snapshots legs rebased to the Design A v2 contracts (pin `53fa39ec` (spec blob sha), `64b351b8b`), response-arming made race-immune (`b5cafd3f7`), and the anchored `hasText` locators unanchored against raw `textContent` (`5dc834706` — the root cause was anchored-text matching against `mat-icon` ligature glyphs).
- Full-daemon-depth lane closed at **12/12 PASS** (81.6s, flaky=0) after a 4-run loop (7/12 → 11/12 → 11/12 → 12/12) with **zero product failures** — every red proven test-side. Citation-integrity pass re-attributed 3 evidence claims (`7947ffeeb`, review F1–F4). Loop record: `c2c226725`; entrypoint `cd frontend && npx playwright test --config playwright.snapshots.config.ts e2e/snapshots.spec.ts` (12 tests, self-boots daemon :18279 + FE :14199).

### Changed — Designer→sketcher→critic design pipeline; designer holds zero od.* tools (`feature/designer-critic-orchestration`, merge `4f70415d2`)

> **CHANGELOG (feature release):** *Designer becomes a brief-authoring orchestrator with ZERO `od.*` tools; the new `sketcher` agent is the SOLE generation lane (all four `od.*` tools stay tool-internal per D1); a new read-only `critic` agent is the design-QA gate (verdict PASS/NEEDS-REVISION, tiers critical/advisory/[BRIEF-LEVEL], `pinned_spec_sha`). Pipeline: designer → sketcher → critic → designer decides (≤3 rounds/page, severity-gated) → accept/save. Supersedes the sketcher pilot dual-run/parity deprecation gates (user decision 2026-10-10). Ships at this release's promote; NOT live before.*

- **Phase 1 — designer od.* surgery** (`a29ae3391`): every `od.*` token removed from designer's toolset/prompts; brief-authoring + lane routing only.
- **Phase 2 — critic agent build** (`7530712b4`): read-only design-QA leaf, vision lane, resolved set exactly `{read_file, image_get, image_list, explain_image, compare_images}` (write-leak invariant; `image_save` denied), verdict schema at `.agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md`.
- **Phase 3 — pipeline wiring** (`dd949c4c2`): designer → sketcher → critic → accept/save; severity-gated ≤3 rounds/page (advisory-only at cap → accept-with-disclosure `[REVIEW-CAVEAT]`; any critical → escalate-only; all-`[BRIEF-LEVEL]` → brief revision without sketcher re-dispatch). Designer-side **KV `round_count` pin + revival re-bind Cardinal** (`agents/designer/rule.md` Guideline (e) — re-bind from `shared_meta_kv` on `send_message` revival, NEVER reset; `(critic_instance_id, verdict_sha)` recorded per iteration).
- **Phase 4 — planning reconcile** (`ff7e288bd`): pilot-gates superseded, parity schema v2, deferred-debt journal; review one-liners (`6b9eec0e6`, `36ecf5473`, `35d7e9c5d`); tier-1 wiring asserts aligned to the sketcher-sole lane (`be586c6ad`); final-gate records `cc7b03d41`.

### Fixed — od.generate consumer-side streaming past the 120s proxy read window (CF-524) (`feature/od-generate-async-poll`, merge `c63f93e30`)

> **CHANGELOG (fix release):** *The `od.generate` chat call is issued with `stream=True` + `stream_options={"include_usage": True}` and consumed synchronously inside the factory attempt, so SSE headers land at t=0 and the Cloudflare edge read window (~100–125s) never 524s a long generation mid-flight. The factory returns a ChatCompletion-SHAPED envelope (`StreamedChatCompletion`) so extraction and the three completeness gates stay byte-identical. ZERO proxy code changes. Live promoted before this release remain exposed until they pick up this vehicle.*

- **Streaming call + envelope invariant** (`00e826f91`, plan §6.1): `delta.content` joins the answer, `delta.reasoning_content` accumulates separately (MiniMax-M3 thinking verified), last non-null `finish_reason` + terminal `usage` chunk captured.
- **Atomic 200k budget-chain reconciliation** (`40d1083da`, §6.2): adapter wall clock 600s at the 200k budget; **bounded retry-on-truncation** (`3937970f1`, §6.3): exactly ONE same-prompt re-attempt on `finish_reason="length"`, second truncation fails typed; designer workflow wait chain pinned to ≥660s for synchronous waits (`b97819628`).
- **Phase-0 probe GO** (`f758c7c3a` + `probe-evidence-phase0.md`): real streamed vision call through the production proxy survived **154.0s** (> the ~120s CF window) with terminal `finish_reason` + `usage` chunks delivered and `: heartbeat` comments tolerated; test hardening `a78204f16` + `60a21d7cc`; §4 close-out `48890c423` (merged `0afd28ff3`).

### Fixed — Checkpoint saver connection resilience (incident 2026-10-10) (`feature/checkpoint-conn-resilience`, merge `c8b3b09f2`)

- **Pool-backed `AsyncPostgresSaver` + retry proxy + sentinel readiness probe** (`eb71f34df`): the saver rides a connection pool with a retry wrapper; `/readyz`'s checkpoint component reads via a sentinel probe instead of `pool.check()` (`cea8a4c73`, W1); explicit forwarders for every concrete `BaseCheckpointSaver` member per the MRO rule (`0122db579`, K1/K2); exception log lines redact server identifiers (`97862499e`, W2); review fixes `1af1a1da2` + `f19803dc3`; incident doc + root-fix addendum `2eb5c8178`; test coverage `285d4578f`, `0f376f63d`, `5f8c20b3f`, `fb72bae84`.

### Added — Live-views Phase 2: clickable fully-qualified URLs + content-aware rendering (`feature/live-views`, merge `7189a28ce`)

- **Fully-qualified URL resolution chain** (`cb81ec8d0`) — config > Host-capture > bind > path-relative; **`HostCaptureMiddleware`** records the request host (`c7296d4da`; extracted to `daemon/middleware/` in `e6b240e22`); **content-aware rendering** — `.md` artifacts served through a sanitized CDN-markdown wrapper with CSP nonce + SRI (`a1acb10fc`, `bf13abc26`); IPv6 hosts bracketed + `X-Forwarded-Proto` chains tokenized (`f8eb688ea`); operator downgrade/fallback warnings + wrapper hardening (`76bd49e0c`); runbook + config knob docs (`65a163484`, `docs/runbooks/live-views.md`).

### Changed — Close-out ledger landings (`chore/land-parked-notes`, merge `fc42eb20d`; `7e39fa9ef`; `0afd28ff3`)

- Parked note/ledger content landed for merged lanes: QUARANTINE rows for the live-views phase-2 gate + project_manager prompt-tests (`8be6abdfa`), live-views finalize non-deliverables (`b40d964b7`), upgrade-executor-resilience KMS runtime audit appends (`323176538`), tool-pairing-full-history-heal quality-pass notes (`259a85f37`). Sketcher 2026-10-09 PACKS.md ledger entry landed via timestamp-ordered manual union from stash@{0} (`7e39fa9ef`; stash retained). od-generate-agent-lane §4 close-out note (Option C supersede) merged (`48890c423` → `0afd28ff3`). Documentation-only; zero product change. (The code-fix skill-seed flag from the Snapshots v2 loop was adjudicated NOT-A-DEFECT — misattributed evidence; adjudication recorded in project history, no product change.)

---

## [0.17.2] — 2026-10-06

### Fixed — Designer OD-lane: MCP preload at all bare spawn lanes (`feature/designer-od-lane-fix`)

> **CHANGELOG (fix release):** *Spawns that went through any bare `manager.spawn_instance()` call site — the `spawn_instance` agent-tool lane included — skipped MCP preloading, so designer (and planner) children bound ZERO MCP tools on live (RCA 2026-10-05: 4/4 designer dispatches since Oct 1 fell back text-first). The fix preloads MCP at the lane, before the spawn, on every path.*

- **At-lane MCP preload** — `daemon/tools/instance.py` now pre-generates the child UUID, awaits `manager.ensure_mcp_preloaded(instance_id, …)`, and passes the `instance_id` into the sync spawn at all 4 bare sites (`spawn_instance` tool lane, `spawn_councilor`, `convene_council` ×2); same treatment at `daemon/routers/mappings.py` (HTTP lane) and `daemon/sources/adapters/slack/thread_manager.py` (Slack thread lane, including repair of a latent await-TypeError on the preload call). The defect was class-independent — every bare lane was affected; designer/planner merely surfaced it via the leader's bare-tool dispatch.
- **`McpService.is_preloaded()` + warn-once cache-miss guard** — new introspection predicate, plus a per-instance warn-once WARNING when a spawn lane's allow-list advertises MCP but the preload cache misses. Never raises; silent on legit-empty caches; allow-gated.

### Changed — Designer agent: OD-first by construction

- **Prompt default flipped to OD-first** — `agents/designer/` soul/workflow/rule/tools_note now make the OpenDesign MCP lane the primary mockup path, with a **mandatory `fallback_reason` enum** (`tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`) recorded on every text-first fallback, a **lane-start probe** (one `od_list_projects` call before the mockup lane), a `mockup_lane` column in the design-spec template, and the npx-safety rider (TypeScript only via `frontend/`'s local devDependency — never bare `tsc`/`npx tsc` outside a package dir). Prompt stream ships in the same release so behavior and prompts land together.

### Added

- **Pinned behavioral spawn-lane MCP-preload suite** (`tests/unit/test_spawn_lane_mcp_preload.py`) — fails at base (`is_preloaded` never written for the child UUID; warn-once symbol absent), passes at fix; plus an **AST census probe** (`tests/unit/probe_designer_od_lane_binding.py`) enumerating bare `spawn_instance` call sites (fail@base → pass@fix). Test-double hardening across the spawn surface: `AsyncMock` `ensure_mcp_preloaded` seams (WalkerManager double included) and `instance_id=ANY` assertion tracking on spawn calls.

### Deferred (F1 follow-up ledger)

- F2 allow-all warn-gate gap; F3 behavioral pins sites 2-6; F4-F8; HTTP-lane path-shaped `agent_id` normalization; `plane_http_client` 409 logging-arity; child `instance_name: None` in the detail API; `dev.sh` PORT knob; `test_loaded_line` docstring overstatement.

---

## [0.17.1] — 2026-10-05

### Changed — Chart render is now opt-in (`render_image: bool = False` on `generate_chart`; user directive via Discord 2026-10-05)

`generate_chart()` **no longer renders by default**. Previously the tool always rendered a PNG at validation time, persisted it under `provenance.feature="chart-render"`, and emitted a trailing `<!-- ens-img:chart-render:<id> -->` marker for chat-source dispatchers to attach. That contract flipped to opt-in: the default now returns the **validated Mermaid block only** — no render, no PNG capture, no `tmp_images` write, no marker. Chat dispatchers already no-op without a marker, so non-chat callers see a clean response with no behavioral change.

**When to pass `render_image=True`**: ONLY when the image will actually be delivered to a chat source (Discord / Slack / Telegram) as a native attachment — i.e., when the user explicitly asks to see the image. Web and UI contexts never need it (the Mermaid fence renders natively via ngx-markdown / GitHub / any Mermaid-compatible renderer).

**Differential timeout (`d5_timeout`, same user addendum)**: `render_image=True` extends the charter wait budget from **600s to 1200s (20 min)** on ALL charter wait paths — both the fresh-spawn `invoke_agent_and_wait` budget AND the reuse lane's wait budget — so a cold chromium/puppeteer bootstrap fits INSIDE the call. `render_image=False` (default) keeps the normal ~600s validate-only budget (no slot held open for a render that isn't requested).

**Charter-side**: `agents/charter/workflow.md` Steps 5 + 6 are now **conditional on a `RENDER_IMAGE: true|false` directive** in the dispatch message (the exact directive form is byte-stable and test-pinnable — the directive is emitted by `daemon/tools/chart_tools.py` on its own line; charter's gate regex skips the entire render + persist + marker pipeline when the directive is `false` or absent). The locked marker regex (`^<!-- ens-img:chart-render:[a-f0-9]{32} -->$`), the both-seam extraction ordering, and the dispatcher skip rules are UNCHANGED — the opt-in contract lives entirely on the *emission* side.

**Skill/prompt guidance**: `agents/_prompt_system/innate-skills/chart/skill.md` has a new dedicated "render_image: opt-in rendering" section (with the 600s/1200s differential table) plus the `render_image` row in the signature table; the 20 chart-capable agents' canonical Chat Delivery line now carries the opt-in qualifier ("pass `render_image=True` ONLY when the user explicitly asks to see the image; web/UI contexts never need it"). **Promote note:** daemon-side changes require a daemon restart + promote; agents-side changes take effect on next charter/agent spawn.

**Audit**: `tools/audit-chart-image-delivery.sh` grew from 24 to 34 pins — 10 new feature-class pins (#25–#34) grep-verify the opt-in contract plus the charter-skill hardening surface (param declaration, d5 timeout constants, directive byte-stability, workflow conditional gates, chart skill opt-in docs, 20-agent qualifier coverage, pre-warm section, 6-fix keywords, timeout-by-name). Preservation pins (#1–#9, #13, #22, #23) unchanged and green.

**Charter skill hardening (sealed)**: `agents/charter/workflow.md` Step 5.5 render block now mirrors `install-mermaid-cli.lib.sh` exactly (6-fix canonical procedure); `install-mermaid-cli` skill v1.1.0 adds pre-warm provisioning so the first user chart never bootstraps mmdc/chromium in-band; `agents/charter/rule.md` adopts cardinal/guideline split per the prompt-writing guide. **Seal re-pinned 35/35** (skill frontmatter 1.1.0; doc-only — no version bump per `4df0b100`).

### Fixed

- None in this patch (no behavioral fixes; scope is strictly the render opt-in flip).

---

## [0.17.0] — 2026-10-05

Restart-resilience release: the daemon now survives restarts without stranding armed notifications, mid-task instances, or dependent wake-ups. Consolidation note: v0.15.x and v0.16.x shipped via tag annotations without CHANGELOG sections — this section carries the entries accumulated under [Unreleased] since [0.14.2] (two of which — checkpoint retention and the selectable-models default — technically shipped in the v0.16.12 tag) plus the full 2026-10-04/05 merge window.

### Added — Restart-resilience suite (post-restart liveness)

- **Post-restart arm-notify** (`feature/post-restart-arm-notify`): armed `system_restart` / `system_upgrade` runs are now auto-delivered to the arming instance after a daemon restart, in the same chat where the arm was confirmed — zero user action; operator kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` (default ON). See `docs/runbooks/post-restart-arm-notify.md`.
- **Auto-continue RUNNING instances after daemon restart** (`feature/auto-continue-running-after-restart`, merge `86ea8d69`): instances that were mid-task (`RUNNING`) when the daemon stopped are automatically re-dispatched on next boot — resume-from-checkpoint via the `is_retry` path — instead of sitting stranded until a manual poke. The pause-first/quiesce contract is preserved: `PAUSED` instances stay paused and revival semantics are untouched.
- **Chart-image delivery** (`feature/chart-image-delivery`, merge `feature/chart-image-delivery`). Rendered Mermaid diagrams are now delivered as native image attachments in chat-source dispatches (Discord attachments, Telegram `sendPhoto`/`sendDocument`, Slack `files.uploadV2`) instead of Mermaid code blocks. Charter (the Mermaid agent) renders to PNG at validation time and emits a source-agnostic `<!-- ens-img:chart-render:<id> -->` reference marker; the chat-source dispatcher extracts the marker, resolves the PNG via `TmpImageStore.open_with_meta`, and uploads via the per-adapter native API. HTTP-API callers see the marker verbatim and may fetch the PNG via `GET /api/tmp_images/<id>`. Internal transport (marker + tmp_images substrate) is unchanged; degraded delivery is text-only with the Mermaid block intact. **Operator action required for Slack delivery:** grant the `files:write` OAuth scope in your Slack app config and reinstall the app; until granted, Slack text-only delivery with WARN-once log per channel. See `docs/sources/slack-setup.md` scope table and `.agents/shared/planning/chart-image-delivery/release-report.md` for the full operator runbook.

### Added — Projects "chat" tab (`feature/projects-chat-tab`, merge `39036550`)

- **Special "chat" tab in the Projects UI** filtering instances by chat-source origin: operators can now inspect exactly the instances spawned from a given chat source (Telegram/Discord/Slack/WhatsApp/webhook) without mixing in API- or job-born instances.

### Changed

- **Default `persistence.checkpoint_max_per_thread` lowered 50 → 3** (`feature/checkpoint-retention-keep-3`, merge pending). The per-thread retention cap on the Op D checkpoint-prune pass (`daemon/services/maintenance.py::_prune_per_thread_checkpoints`) is now operator-tunable via the `CHECKPOINT_MAX_PER_THREAD` env var (floor 1, enforced by `PersistenceConfig.checkpoint_max_per_thread` `ge=1` — pydantic raises `ValidationError` at config load on 0 / negative rather than silently mass-pruning the latest checkpoint). Long-running instances no longer accumulate a multi-week checkpoint tail they never resume against; the constant `daemon.constants.CHECKPOINT_MAX_PER_THREAD = 50` is removed (the source of truth moved to config). Default `persistence.checkpoint_cleanup_interval` (24h, unchanged) bounds how often the prune pass runs; the new default retention cap bounds per-pass disk growth. Override per-installation via `CHECKPOINT_MAX_PER_THREAD=N` (YAML key `persistence.checkpoint_max_per_thread` mirrors the env).
- **Skill-search Stage-3 selector now uses a dedicated model** (`feature/skill-selector-quick-model`). The unconfigured skill-search Stage-3 LLM selector (`_llm_select`) resolves the dedicated `SKILL_EVOLUTION_SELECTOR_MODEL` (literal default `"quick"`) instead of silently riding the main `OPENAI_MODEL` — deliberately NO `None` → `llm.model` fallback, unlike sibling `evolution_model` / `analysis_model`. Operators wanting the old behavior set `SKILL_EVOLUTION_SELECTOR_MODEL=<main model>`.
- **`spawn_hot_instance` auto-dispatches the task as the child's first turn** (`feature/spawn-hot-auto-enqueue`, merge `18827dbd`, R18): default `auto_dispatch=True` routes the task through the internal enqueue lane (no JobItem row, no claim-gate exposure); failures surface as warning + error + recovery hint, never silently; an empty task is a loud skip; `started` stays `{warm,cold,blocked}` with `blocked` ⇒ no enqueue. Callers must NOT `send_message` after spawn (double-echo); `auto_dispatch=False` opts out.
- **Release staging freshness guard** (`feature/stage-freshness-guard`, merge `90cd17aa`): `scripts/upgrade/stage.sh` structurally refuses stale/foreign/non-tip staging — L1 tip-identity gate plus L2 provenance sidecars with stage-time re-hash; `frontend/dist` fold-in closes the cn 0472b31f trap family; argv-only `--allow-stale-stage` escape hatch for drills.
- **Uniform git worktree workflow** (merge `f8d94f68`): always-worktree for committing tasks; atomic chained merge→push→remove→branch-delete→KV-delete; quiescence-before-merge; backstop-only reconcile with merged-AND-clean-only cleanup eligibility; fenced-`.env` dev.sh guard (`ENSEMBLE_SELF_ENV=dev`); lazy per-worktree venvs.
- **`.env.backup` gitignored** (`fix/gitignore-env-backup`, merge `b01ff27c`): recovery backups written next to `.env` no longer show as untracked noise.

### Fixed

- **`OPENAI_SELECTABLE_MODELS` fail-safe default realigned with the shipped YAML literal** (`sel-default-20260927`, branch `feature/selectable-models-default`). The Python-side default `daemon/config.py::_ALLOWED_MODELS_DEFAULT` had drifted to `("agentic", "coding")` while the `config.yaml` interpolation literal (`config.yaml:87`, `${OPENAI_SELECTABLE_MODELS:-agentic,coding,coding2,vision}`) already carried `coding2` + `vision` from the designer-agent P1-WP1 mission — the two homes of the default disagreed. The constant is now `("agentic", "coding", "coding2", "vision")`, byte-equal to the YAML literal, with a consistency pin (`TestDefaultConsistencyPin.test_yaml_literal_matches_constant`) that parses `config.yaml` at test time and fails loudly on any future drift. New one-shot startup WARNING `[Config] allowed_models default applied: ['agentic', 'coding', 'coding2', 'vision'] ...` (`daemon/config.py::warn_default_allowed_models_applied`, wired from `load_config` + `daemon/__main__.py` + `daemon/api.py`) fires only when BOTH `OPENAI_SELECTABLE_MODELS` and the legacy `OPENAI_ALLOWED_MODELS` are unset/empty — operators see the default fire once and can silence it by setting the env var explicitly, even to the current default value. `.env.example` / `.env.prod.example` refreshed with the shipped default text + a with-vision example value. **Upgrade note for operators pinning explicit lists:** strict-when-set is by design — an explicit list without `vision` blocks vision-dependent instance creation (the v0.16.0 deploy incident, 2026-09-27); add `vision` to your explicit list. Full deployment reference added in `docs/setup.md` (env-var table rows + selectable-models section + v0.16.0 upgrade note) and `README.md` (Configuration Overview).
- **Claim-gate message-JobItem sibling deadlock** (`fix/claim-gate-sibling-deadlock`, merge `62780a2e`, branch tip `34b91ebb`): two rapid external messages to one instance no longer wedge the claim gate — state-discriminated guard exclusion plus an ACTIVE-only belt and SUBSTR/LENGTH chat-pool SQL; PAUSED case proven restored end-to-end (tester 15/15 gates incl. PG 54/54).
- **Durability F-1 + F-2** (`feature/durability-f1-f2`, merge `d679a263`): F-1 — DependencyBus boot-race `None`-flip defeated by a preserve predicate (boot auto-continue no longer discards pending wake-ups); F-2 — WC-wake straddle wedge closed; live-proven zero-ping heal across 5 SIGSTOP rounds.
- **spawn-hot perm-denial masquerading as cold spawn** (merge `2ae91046`): spawn permission denials now surface as denials instead of silently degrading into a cold spawn.
- **`ask_questions` provider-wrapped item arrays** (merge `577838b7`): provider-wrapped item-array parameter shapes are handled instead of erroring.

---

## [0.14.2] — 2026-09-25

Feature-carrying release: four feature merges on top of v0.14.1 add agent-facing lifecycle/job tools and queue-awareness, plus one incident fix on the agent-tool message path. No schema changes, no breaking API changes, no config flips.

### Added — Agent-facing pause/resume tools (`feature/agent-pause-resume-tools`, merge `39bd9b15`)

- **`pause_instance` / `resume_instance` agent tools** — wrap the same manager service layer as the HTTP pause/resume endpoints; instance-cascade semantics preserved.
- **Fail-closed access check** — denied shapes fail closed; `resume_instance` refuses while a pending question pack awaits answers (review finding #4).
- **Acceptance gate** — tester merge-ready artifacts under `.agents/tester/RESULTS/`.

### Added — Upgrade-gate dynamic user-origin classification (`feature/upgrade-gate-dynamic-user-origin`, merge `4db90d74`)

- **Registry-backed user-origin classification** replaces the static list for upgrade-gate factor-2.
- **Bounded detail-token rendering** + security-review hardening pins.

### Added — Agent-aware default queue + job-tool queue aliases (`feature/parallel-default-queue-ari-jober`, merge `75d7e2d1`)

- **`ari`/`jober` default to `system_parallel_queue`**; queue aliases accepted by job tools.
- **Canonicalization fix** — agent `default_queue` aliases canonicalize to system queues.
- **Docs** — `dlq_list` example fix + unknown-ref soft-fail clarification.

### Added — Job-shaped pause/resume (`feature/job-pause-resume-tools`, merge `b3f44826`)

- **`job_pause` / `job_resume` tools** — job-shaped wrappers delegating to the instance cascade; FE-identical responses.
- **Grants** — ari/jober/leader; kind-refusal fail-closed for non-job refs; `create_job_tools` count pin 22→24.

### Fixed — `send_message` busy-guard context-injection fallback (`feature/send-message-context-inject-fallback`, merge `6c3b5585`)

Closes incident `c3d1a722`: a leader send bearing `context` to a running, live-graph, processing child was rejected (`Pending: 0, Processing: 1`) instead of injecting.

- **Fallback path** — on the queue-busy rejection, when the target re-verifies injection-eligible (running + live graph task) and the send is non-`load_skill` with non-empty `context`, the `[SYSTEM CONTEXT: Task Context]` block is flattened into the injected message prefix and delivered via `set_injection`.
- **Surgical diff** — `daemon/tools/instance.py` only (−9 docstring lines + one approved rejection-hint sentence); 207/0 gate re-proven post-rebase onto `cfaacab0`.

## [0.14.1] — 2026-09-24

Patch release. Bug-fix-only content: two merges on top of v0.14.0 close the user-visible job-watch delivery loop. No schema changes, no breaking API changes, no config flips.

### Fixed — Mission-watch notification replay flood + `Error:`-slot mis-wiring (`fix/job-event-watch-replay`, merge `5f4e35b0`)

The mission-watch notify path was replaying already-settled receipts and mis-wiring the `Error:` slot to mission assistant-message content. Both classes are now dead.

- **No replay of already-settled receipts** — registration-time terminal filter (`watch_mission` early-exits when the work row is already in a terminal state, the watcher row already carries a settled receipt, or the work is mid-flip). The previous "re-arm" path that re-fired `mission_watch` notifications for completed jobs is gone; exactly-once at the wire is now load-bearing.
- **No post-unwatch continuation** — the `unwatch_mission` route clears the watcher row AND arms a delta-token at the consumer site (slot wiring); a follower that arrives after unwatch does not see a half-cleared watcher row and cannot synthesize a notification from the stale slot.
- **No duplicates** — replay invariants pinned (`tests/job_queue/test_work_notifier_replay_pins.py` + `test_work_notifier_defect5_pins.py`): 7/7 exactly-once, zero replays, zero duplicates, zero flip re-fires against the incident scenario.
- **`Error:`-slot mis-wiring dead** — the `notify` keyword slot wiring is now type-checked at registration; a `None` payload distinguishes "keyword not mapped" from "keyword mapped to None payload". Previously the slot could leak mission assistant-message content into an `Error:` field of an SSE event.
- **`watch_mission` delta-arm log** — registration logs `armed N live / M skipped` where M is the count of watchers refused at the terminal filter. Operator can see the filter do its job.
- **Regression + incident-scenario coverage** — 5/5 incident-scenario pins (replay 7/7, exactly-once, delta-arm, slot wiring, unwatch); latent-twin slot-wiring pin hardened against `None` payload.

### Fixed — Mid-flight ⟳ notifications + completed-task Result: rendering (`fix/watch-notify-delivery-gaps`, merge `a79aba7a`)

The remaining watch-delivery gaps: mid-flight `mid_flight_report` events were silently dropped at the emit path, and the `Result:` block was missing from completed task-kind envelopes. Both fixed at the CAS-winner site; the commit-visibility race that masked the result arm is closed.

- **Mid-flight ⟳ delivery (DEFECT-5 closed live)** — `work_notifier._emit(...)` now delivers `MIDFLIGHT_REPORT` events to subscribed watchers at the CAS-winner site. Previous code returned at the "armed" branch before the notify arm could fire for non-terminal mid-flight rows; the row survives non-terminal events, so the watcher row was intact but the ⟳ event never reached the wire. ⟳ delivery now logged: `⟳ delivered (36ms, row survives non-terminal)`.
- **Result: block on completed task-kind events (DEFECT-1/1b closed live)** — the producer-side `result_summary` is threaded into the completed-token envelope via the measured CAS-winner branch. Previous attempts measured at the wrong site (an instrumentation-orphaned branch) and stamped `result` against a payload-gap row; the producer-side fix threads `result_summary` into the SAME token the `notify` arm uses. 4/4 task-kind envelopes now byte-matched `Result:`-marker-present; settled-lean invariant held.
- **M3 settled narrowing** — only the `completed` token fans out a result-arm notify; `failed` / `cancelled` / `dead` paths keep the previous contract (no `Result:` line for those). Reduces the surface area for accidental `Result:` leaks.
- **Emit-path instrumentation** — `[watch-cas]` log instrumentation at the five notify sites lets operators see which branch won the CAS race; non-winner branches stay silent (no log flood). Filed a propagation gap for the closed-loop version.
- **Regressions** — `test_work_notifier_defect1_pins.py` (491 lines), `test_work_notifier_defect1_round3_pin.py` (754 lines), `test_work_notifier_defect1b_pin.py` (578 lines), `test_work_notifier_defect5_pins.py` (608 lines). Mechanical 17/17 pins PASS, 3 dirs zero new reds.

## [0.13.12] — 2026-09-23

### Added — `job_answer` Job Queue tool (`feature/job-answer-tool`, merge `0deee66d`)

First-class answer submission for orchestrator agents (Ari / jober). The tool surface gains a new `@tool` (`daemon/tools/job_queue.py:2852`) that wraps the same `answer_questions_via_instance` helper the HTTP `POST /api/jobs/{work_id}/answer` route already calls — there is no second implementation; both surfaces share the seam end-to-end. Replaces the brittle bash+curl port-scanning procedure the orchestrator previously used to relay an answer back through the daemon.

- **Signature** — `job_answer(work_id: str, answers: dict[str, str], question_pack_id: str)` → `dict`. `JobAnswerInput` (args_schema) is the single source of truth for both description and field-level constraints; the wrapper carries plain types so the two copies cannot drift. Registered via `@register_tool_category("job")` and added to `KNOWN_TOOL_NAMES` — `ari` / `jober` inherit the `"job"` category automatically.
- **Stricter tool-layer contract than HTTP** — non-empty `answers` and non-empty `question_pack_id` are required at the tool boundary (the HTTP route accepts optional fields; the tool refuses to call the helper when either is missing and returns an agent-readable error string). Duplicate-pack / stale-pack / terminal-target / write-pause / 503 outcomes are converted to clear error STRINGS so the tool never raises mid-tool-call (LangGraph `@tool` exceptions bubble up the graph task). The same error vocabulary (`QUESTION_PACK_MISMATCH` / `ANSWER_TARGET_TERMINAL` / `WRITE_PAUSED` / etc.) survives end-to-end.
- **Access control + write-pause guard** — `_check_job_access` runs before any mutation (same seam as the four visibility tools: `job_messages` / `job_tree` / `job_progress` / `job_inject`); the write-pause 503 migration posture is enforced both at the tool boundary and inside the helper (fail-closed end-to-end).
- **Routing** — `work_id → instance_id` via the SAME `work_resolver.resolve_work(work_id)` the HTTP route uses; project scoping rides on `_check_job_access`. The helper does not know whether it was called from HTTP or from a tool.
- **Tests** — `tests/job_queue/test_job_answer_tool.py` (NEW, 899 lines): happy-path, stale-pack rejection, no-pending-pack rejection, access denied, plus the tidier M-round pins (count bumps, imports cleanup, `live_hub=None`, 503 race-window, order-pin, tightened pin). Pre-existing 13 `tests/job_queue` failures re-proven base-identical — re-confirmed not attributable to this merge.

### Fixed — mission-live guard stops premature `completed ✓` job events (`fix/job-event-premature-completed`, merge `4c3bb506`)

`MissionLiveGuard` (`daemon/services/mission_live_guard.py`, NEW, 318 lines) closes the bug class observed 2026-09-22 (events 79328/79349): a task-kind LEADER job is legitimately `JobItem ACTIVE + backing Task COMPLETED` mid-mission — the per-turn task row settles while `child_reports` defers the JobItem finalize behind still-running children (bus held, mission live). The drift reconciler's Pattern f2 (`job_recovery_service.reconcile_drift_states`) saw "Task COMPLETED but JobItem never transitioned", force-finalized the JobItem, and the F10 notify arm delivered a false `completed ✓` event and CAS-deleted the mission watcher row. The boot-time `reconcile_terminal_watches` sweep (`job_queue_service`) is an independent re-occurrence vector: it fires terminal for mission-keyed watches on settled work rows without consulting mission liveness.

- **One-question API** — `evaluate_mission_live(...)` returns a frozen `MissionLiveVerdict(live, reason, error, timed_out)`. The mission is LIVE when ANY of:
  - (a) the dependency bus reports pending watchers for this work (caller-supplied count — each call site already owns its bus seam);
  - (b) any DESCENDANT instance of the work row's instance is non-terminal. Reference semantics: the mission resolver / `child_reports` instance-tree walk over `instances.parent_id` (the permanent record) with the canonical terminal set `TERMINAL_INSTANCE_STATUSES` — non-terminal therefore includes `running` / `waiting_children` / `queued` / `paused` AND `idle` (the resolver canonicalizes IDLE → `"processing"`, i.e. live);
  - (c) the root instance itself is non-terminal (same status set).
- **Fail-open contract (binding)** — any exception inside the guard returns `live=False` with `error=True` so the caller proceeds with finalize + notify. A missing terminal report is WORSE than an extra premature one — at-least-once terminal delivery is preserved.
- **Zombie backstop** — `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS = 6 * 60 * 60` (6h, hardcoded constant; no env flag, no schema change — repo policy: bugfixes are not user-togglable; the constant is the tuning knob). When the work's `completed_at` anchor is older than the timeout the guard falls through to `live=False` — finalize fires; starvation impossible. The anchor is the backing task's `completed_at` at the f2 site and the settled work row's `completed_at` at the boot-sweep site; age math uses `now_utc_naive()` per the naive-UTC binding convention.
- **Integrated into both finalize/notify sites** — `daemon/services/job_queue_service.py` (+97 lines, boot-sweep hook) and `daemon/services/job_recovery_service.py` (+65 lines, drift f2 hook). While `MissionLiveVerdict.live is True` the caller skips `finalize`, skips `notify`, and leaves the watcher row alone. Verdict is recorded on the drift `details` for forensics.
- **Tests** — `tests/job_queue/test_mission_live_guard.py` (NEW, 1265 lines): real-notify path, crash-restart backstop, descendant-tree coverage, idle canonicalization, anchor-age timeout, fail-open on resolver errors, plus leg-by-leg isolation. `tests/job_queue/test_f1_killswitch_tz_matrix.py` and `tests/job_queue/test_orphan_active_job_recovery.py` updated for the new hook.

### Changed — tester gate artifacts (`694ed596`)

Follow-up chore to the `job_answer` merge: `job_answer` tester verification report under `.agents/tester/RESULTS/`, `job_answer` row added to `.agents/tester/QUARANTINE.md`, and tidier round notes appended to `.agents/tidier/notes.md`. Documentation-only.

---

## [0.13.11] — 2026-09-22

### Added — Mid-flight question/answer channel (`feature/midflight-qa-channel`, merge `ca4ab125`)

Questions asked mid-flight (`ask_questions`) now surface to the job watcher / orchestrator over the existing push lanes, the human's answer routes back through a new job-addressed HTTP endpoint, and non-blocking `mid_flight_report` progress events ride the same substrate. Event-driven only — no polling introduced anywhere. Resolves the `ask_questions` pause deadlock (incident 1) and adds a one-shot wedge guard (incident 2) so paused chains surface the ask rather than sitting silent.

- **New watchable event statuses** (`question requested ❓`, `answer received ✓`, `mid-flight report ⟳`, `stuck awaiting answer ⏳`) — all NON-terminal: watcher rows survive for the eventual terminal event. `watch_job` accepts them in `events=[...]`; the job-orchestration skill's status action table documents the relay flow.
- **New agent tool** `mid_flight_report(summary, details, level, decision_required)` — opt-in via `tools.allow: ["midflight"]` (enabled on leader / developer / coder / tester / devops). Emits a `MIDFLIGHT_REPORT` event (EventBus + FE banner + `[JOB_EVENT]` to watchers) WITHOUT pausing.
- **New answer endpoint** `POST /api/jobs/{work_id}/answer` — the orchestrator relay path. Resolves `work_id → instance` via the WorkResolver (watcher-independent) and delegates to the shared answer helper. Body: `{"answers": {...}, "question_pack_id": "...", "resume_message": "..."}`.
- **Wedge guard** — a finite one-shot chain (3 emissions, 30 min apart) of future-dated `heartbeat_emit_stuck` Task rows re-emits `stuck_awaiting_answer` while an asker stays paused awaiting an answer; at the 3rd emission (~60 min) the guard terminates the asker and broadcasts an operator escalation via NotificationBroadcaster. The claim gate gained a type-scoped carve-out so the one-shot is claimable while the asker is PAUSED (no seq-scan degradation, no UNION reshape).
- **Answer-path hardening (both answer surfaces)** — `POST /api/instances/{id}/answer` and the new job-addressed route share one helper with a tightened, exactly-once contract: duplicate answers are a 200 no-op (CAS loses the second), stale `question_pack_id` mismatches are rejected 400, and a **BREAKING** 410 `ANSWER_TARGET_TERMINAL` replaces the silent revive of a finished instance (FE wizard: no impact — non-optimistic, error-toast driven). Error-code relabeling (404 `NO_PENDING_QUESTION` / 410 `QUESTION_PACK_LOST` / 410 `ANSWER_TARGET_TERMINAL`).
- **Durability** — question packs are shadowed into `instance_metadata` (`question_pack_id` + payload) at ask time and rehydrated at boot; answers survive daemon restarts (410 `QUESTION_PACK_LOST` only when both copies are gone).

### Changed — `ENSEMBLE_SELF_ENV` auto-resolution (`feature/openspace-mcp-integration`, merge `1b3f0795`)

`ENSEMBLE_SELF_ENV` is now OPTIONAL with explicit opt-out (D-FA2.3 supersession — user directive 2026-09-22). The marker is the highest-priority source; absent + no opt-out + unambiguous launch evidence auto-derives the env. Explicit marker still wins, explicit opt-out (`false`/`0`/`no`/`off`) preserves today's fail-closed byte-for-byte, and auto-resolved envs still pass through every live-rung gate unchanged.

- **Auto-derive signal hierarchy** (multi-signal — single-signal PORT-derivation D-FA2.3 cannot recur): explicit marker wins → explicit opt-out returns `None` (actor tools refuse `env-marker-absent`, read tools accept only `target_env=dev`) → absent marker + no opt-out → frozen-binary + `releases/` for sandbox (`exe.parent.parent.parent`); install-dir + `POSTGRES_DB` cross-check for `live`/`demo` (canonical `~/agents-ensemble` / `~/agents-ensemble-demo` `.env` must contain `ensemble_prod` / `ensemble_demo`); dev-shape `POSTGRES_DB=ensemble_dev` for `dev`.
- **Resolver observability** — `_self_env_source()` returns `explicit` / `opted-out` / `auto` / `auto-unresolved` / `ignored-garbage`; `release_info` `env-marker:` line reports the path that produced the resolution. Garbage markers (e.g. `prod`, `flase`, `Live`) are NOT silently attributed to the staged marker — they show the IGNORED raw value with a WARNING in the daemon log.
- **Sandbox install-dir resolution fix** — pre-existing bug in `_resolve_install_dir` used `.parent.parent` (resolved to `<INSTALL_DIR>/releases` and never matched `releases/`); now `.parent.parent.parent` (binary at `<INSTALL_DIR>/releases/<ver>/ensemble-prod`). Same fix unblocks the post-P2.1 install.
- **Refusal text branched on source state** (S1) — `opted-out` points at removing the opt-out; `ignored-garbage` points at valid forms / opt-out / unset; `auto-unresolved` points at explicit marker or unambiguous launch evidence. Fail-closed contract (refusal token `env-marker-absent`) unchanged.
- **Safety invariants unchanged** — auto-resolution changes identity DETECTION, NEVER the gates. Auto-resolved `live` still requires the 3-factor confirmation gate (param + user-origin window + nonce content match) before any live mutation. Auto-resolved `live` `system_restart` is still refused outright (A2/§3.1). `env-self-match` semantics unchanged. Explicit opt-out preserves the full fail-closed refusal for actor tools.
- **Operator migration** — no action required for current installs (daemons with a staged marker continue reading it; daemons without one — e.g. the LIVE daemon at `~/agents-ensemble`, port 9797 — auto-derive `live` via install-dir + POSTGRES_DB cross-check). Set `ENSEMBLE_SELF_ENV=false` to land the strict old contract. Multi-install hosts: a dev daemon picking up ambient `POSTGRES_DB=ensemble_prod` OR a leftover `~/agents-ensemble/.env` will now auto-derive `live` — fail-closed (gates apply), but the resolved env IS `live`. Set explicit `ENSEMBLE_SELF_ENV=dev` to stay `dev`.

---

## [Unreleased] — 2026-09-22

### Changed

**`ENSEMBLE_SELF_ENV` is now OPTIONAL with explicit opt-out** (D-FA2.3 supersession — user directive 2026-09-22: "remove the ensemble self env, feature active by default... default value, if user want disable they can set to false"). The marker stays the highest-priority source; absent + no opt-out + unambiguous launch evidence auto-derives the env.

- **Auto-derive signal hierarchy** (multi-signal — the single-signal PORT-derivation D-FA2.3 rejected cannot recur):
  1. **Explicit `ENSEMBLE_SELF_ENV` marker wins** (`dev`/`demo`/`live`/`sandbox`) — staged by `stage.sh` into `INSTALL_DIR/.env`, exported by `launcher.sh` `load_env_file`. Unchanged: the marker is consumed FIRST by `_self_env_marker`.
  2. **Explicit opt-out → today's fail-closed behavior preserved verbatim.** `ENSEMBLE_SELF_ENV=0|false|no|off` (case-insensitive, whitespace-trimmed — mirrors `daemon.config._PROACTIVE_FALSE_BOOLS` vocabulary used by `ENSEMBLE_EMPTY_RESPONSE_GUARD` etc.) returns `None`; actor tools refuse `env-marker-absent`; read tools accept only omitted target or `target_env=dev`. Operators who want the strict old contract get it byte-for-byte.
  3. **Auto-derive when marker absent and no opt-out.** Frozen-binary + `releases/` for sandbox (binary lives at `<INSTALL_DIR>/releases/<ver>/ensemble-prod` per `stage.sh:5-17`; install dir is `exe.parent.parent.parent`); install-dir + POSTGRES_DB cross-check for `live`/`demo` (canonical `~/agents-ensemble/.env` / `~/agents-ensemble-demo/.env` must contain `POSTGRES_DB=ensemble_prod` / `ensemble_demo` OR the daemon's own `$POSTGRES_DB` must match); dev-shape `POSTGRES_DB=ensemble_dev` for `dev`.
- **Resolver observability** — `_self_env_source()` returns `explicit` / `opted-out` / `auto` / `auto-unresolved` / `ignored-garbage`; `release_info` `env-marker:` line reports the path that produced the resolution so a misconfiguration is visible immediately (auto-derived envs are NOT silently attributed to the staged marker; garbage markers are NOT silently printed as "ABSENT" — they show the IGNORED raw value with a WARNING in the daemon log).

### Fixed

- **Sandbox install-dir resolution** (pre-existing bug in `_resolve_install_dir`): the frozen-binary layout puts the binary at `<INSTALL_DIR>/releases/<ver>/ensemble-prod`, so the install dir is `exe.parent.parent.parent` (TWO `.parent`s up). The pre-fix code used `.parent.parent` which resolved to `<INSTALL_DIR>/releases` and never matched `releases/`, so sandbox install-dir resolution was always returning `None`. The new auto-derive test (`test_auto_derive_sandbox_via_frozen_binary`) caught it; the helper now resolves correctly and the same fix unblocks the post-P2.1 install.
- **Garbage-marker silent fall-through** (W1 — reviewer APPROVED-WITH-NOTES commissioned item): pre-fix, `ENSEMBLE_SELF_ENV=<unrecognized value>` (e.g. `prod`, `flase`, `Live`) silently fell through to auto-derivation AND the `env-marker:` report line printed `ENSEMBLE_SELF_ENV ABSENT` — factually wrong (the marker WAS set, just ignored). The new code classifies the marker into the `ignored-garbage` source state, logs a WARNING with the raw ignored value, and the `env-marker:` line prints the IGNORED raw value (so the operator sees their typo). **Case-sensitivity decision:** the explicit enum match (`_explicit_marker`) is case-sensitive (a deliberate non-normalizing exact enum match — `Live` / `LIVE` are garbage, not `live`); the opt-out match (`_is_opt_out_marker`) is case-insensitive (mirrors `daemon.config._PROACTIVE_FALSE_BOOLS`); the new garbage detection uses the explicit-enum case-sensitive comparison (so wrong-case values are detected as garbage, not silently coerced).
- **`env-marker-absent` refusal text now accurate per source state** (S1 — reviewer commissioned): pre-fix, the refusal text said "no opt-out via false" even when the operator DID opt out, and gave the same remediation hint for garbage-marker and no-signal cases. The refusal hint is now branched on `_self_env_source()`: `opted-out` points the operator at removing the opt-out; `ignored-garbage` points them at valid forms / opt-out / unset; `auto-unresolved` points them at explicit marker or unambiguous launch evidence. The fail-closed contract (refusal token `env-marker-absent`) is unchanged.

### Safety invariants (unchanged)

- **Auto-resolution changes identity DETECTION, NEVER the gates.** Auto-resolved `live` still requires the 3-factor confirmation gate (param + user-origin window + nonce content match) before any live mutation. Auto-resolved `live` `system_restart` is still refused outright (A2/§3.1). `env-self-match` semantics are unchanged. Explicit opt-out (`ENSEMBLE_SELF_ENV=0|false|no|off`) preserves today's full fail-closed refusal for actor tools.

### Operator migration

- **No action required for current installs.** Daemons with a staged marker continue to read it (highest priority). Daemons without one (e.g. the LIVE daemon at `~/agents-ensemble`, port 9797, which pre-dates P2.1) now auto-derive `live` via install-dir + POSTGRES_DB cross-check.
- **Explicit opt-out** — set `ENSEMBLE_SELF_ENV=false` (or `0`/`no`/`off`) in `INSTALL_DIR/.env` if you want the strict old contract.
- **Multi-install hosts (operator callout — W2).** On hosts running multiple installs side-by-side (e.g. a dev checkout + demo + live on one host), a dev daemon that picks up ambient `POSTGRES_DB=ensemble_prod` from the shell environment OR a leftover `~/agents-ensemble/.env` from a prior install will now auto-derive `live` — fail-closed in the sense that all gates apply (env-self-match, live 3-factor, live restart refusal), but the resolved env IS `live`, not `dev`. The `release_info` `env-marker:` line reports the auto-derived path so a misconfiguration is visible. If you maintain multiple installs on one host and want a particular checkout to stay `dev`, set an explicit `ENSEMBLE_SELF_ENV=dev` in the daemon's environment (or its `.env` file), OR opt out entirely via `ENSEMBLE_SELF_ENV=false`. The same applies to garbage / typo'd marker values (`prod`, `flase`, `Live`): the value is silently IGNORED (a WARNING is logged with the raw value) and the resolver falls through to auto-derivation — if you meant `live` you probably got `live`; if you meant something else, the warning is the operator-visible signal that the marker was not honored.

### Tests

- **New** — `TestAutoResolution` class in `tests/unit/tools/test_upgrade_tools.py` (19 cases): explicit-wins, opt-out vocabulary (`0`/`false`/`no`/`off` / case-insensitive / whitespace), auto-derive per env shape (live via install-dir topology, live via ambient POSTGRES_DB, demo, dev, sandbox via frozen binary, sandbox falls back when install matches live/demo, unresolved when no signal, ignores unrelated POSTGRES_DB), `release_info` shows auto-resolved env without marker, auto-resolved live still requires 3-factor gate, auto-resolved live refuses restart outright, auto-derived env-self-match refuses cross-env.
- **New** — W1 garbage-marker tests in `TestAutoResolution` (5 cases + 4 parametrized): garbage + auto-derive succeeds, garbage + no signal logs WARNING, marker-line reports IGNORED raw value (resolved and unresolved cases), garbage detection covers typos / wrong-case / punct-spoofs (`prod`, `flase`, `production`, `demo;live`).
- **New** — S1 refusal-text accuracy tests (3 cases): operator opt-out, garbage marker, no-signal — each refusal hints the right remediation; the old "no opt-out via false" wording is gone.
- **New** — S2 vocabulary pin: `test_opt_out_falses_pinned_to_proactive_false_bools` asserts `set(_OPT_OUT_FALSES) == set(daemon.config._PROACTIVE_FALSE_BOOLS)` so drift between the two vocabularies is caught.
- **New** — S3 armed-spoof gate proof: `test_fabricated_nonce_with_no_window_refuses` — fabricated nonce string + `user_confirmed=True` + NO genuine user-origin window → REFUSED with `user-confirmation-missing`. Pinned the existing `test_auto_resolved_live_still_requires_three_factor_gate` to the concrete token (`CONFIRMATION REQUIRED (live)` for dry_run; `user-confirmation-missing` for armed without window) — the old `REFUSED in out or CONFIRMATION in out` weak assertion is replaced.
- **New** — S4 edge tests (6 parametrized): empty-string / whitespace-only (`" "`, `"\t"`, `"\n"`, mixed whitespace) marker → `absent` (not garbage), resolver source is `auto` / `auto-unresolved` (never `ignored-garbage`).
- **New** — S5 parametrized explicit-wins (4 cases): explicit marker wins over install-dir evidence for a DIFFERENT env (one case per valid marker value).
- **Updated** — `test_marker_absent_reads_fail_open`, `test_env_marker_absent_actor_fail_closed`, `test_spoofed_env_marker_unresolved` now use the new `unresolved_env` fixture (silences every signal the resolver reads — `ENSEMBLE_SELF_ENV`, `POSTGRES_DB`, `Path.home()`, `sys.frozen`) so the OLD contract (marker absent → fail-closed) is exercised cleanly under the NEW behavior (auto-derive fills in when signals are present).
- **Helper** — `unresolved_env` fixture (modulelevel, in `test_upgrade_tools.py`) and `_read_env_value` shell-style .env parser (in `daemon/tools/upgrade_tools.py`) shared with future test additions.

---

## [Unreleased] — 2026-09-21

### Added — Mid-flight question/answer channel (`feature/midflight-qa-channel`)

Questions asked mid-flight (`ask_questions`) now surface to the job
watcher / orchestrator over the existing push lanes, the human's answer
routes back through a new job-addressed HTTP endpoint, and
non-blocking `mid_flight_report` progress events ride the same
substrate. Event-driven only — no polling introduced anywhere.

- **New watchable event statuses** (`question requested ❓`,
  `answer received ✓`, `mid-flight report ⟳`,
  `stuck awaiting answer ⏳`) — all NON-terminal: watcher rows survive
  for the eventual terminal event. `watch_job` accepts them in
  `events=[...]`; the job-orchestration skill's status action table
  documents the relay flow.
- **New agent tool** `mid_flight_report(summary, details, level,
  decision_required)` — opt-in via `tools.allow: ["midflight"]`
  (enabled on leader / developer / coder / tester / devops). Emits a
  `MIDFLIGHT_REPORT` event (EventBus + FE banner + `[JOB_EVENT]` to
  watchers) WITHOUT pausing.
- **New answer endpoint** `POST /api/jobs/{work_id}/answer` — the
  orchestrator relay path. Resolves `work_id → instance` via the
  WorkResolver (watcher-independent) and delegates to the shared
  answer helper. Body: `{"answers": {...}, "question_pack_id": "...",
  "resume_message": "..."}`.
- **Wedge guard** — a finite one-shot chain (3 emissions, 30 min
  apart) of future-dated `heartbeat_emit_stuck` Task rows re-emits
  `stuck_awaiting_answer` while an asker stays paused awaiting an
  answer; at the 3rd emission (~60 min) the guard terminates the
  asker and broadcasts an operator escalation via
  NotificationBroadcaster. The claim gate gained a type-scoped
  carve-out so the one-shot is claimable while the asker is PAUSED
  (EXPLAIN verdict: outer scan stays index-driven on
  `idx_task_status_created (status=?)`; the carve-out is a residual
  filter, same predicate class as the pre-existing cross-system
  guard — no seq-scan degradation, no UNION reshape needed).
- **Durability** — question packs are shadowed into
  `instance_metadata` (`question_pack_id` + payload) at ask time and
  rehydrated at boot; answers survive daemon restarts (410
  `QUESTION_PACK_LOST` only when both copies are gone).

### Changed — answer-path contract (both answer surfaces)

`POST /api/instances/{id}/answer` and the new job-addressed route now
share one helper with a tightened, exactly-once contract:

- **Duplicate answers are a 200 no-op** (`resume_route:
  "already_delivered"`) — the second concurrent POST loses the CAS and
  skips SSE/events/resume entirely. Previously the second answer
  OVERWROTE the first's stored answers (clobber bug).
- **Stale-answer hijack guard** — a body carrying a
  `question_pack_id` that does not match the current pending pack is
  rejected `400 QUESTION_PACK_MISMATCH` (strict-check-when-present;
  absent field keeps today's lenient behavior). **FE wizard impact:
  none** — the wizard does not send the field today.
- **BREAKING (tightened, intentional):** an answer arriving after the
  asker reached COMPLETED or TERMINATED is now rejected `410
  ANSWER_TARGET_TERMINAL` instead of SILENTLY REVIVING the finished
  instance (the revive restarted it from scratch with no LangGraph
  checkpoint — a latent data-loss hazard). **Frontend finding
  (MAJOR-3):** no FE/wizard code relies on the silent-revive — the
  wizard is non-optimistic, error-toast driven, and hides via the
  `question_pack` SSE, so the 410 renders through its existing error
  handler with zero FE changes. ERROR/FAILED askers are still revived
  to deliver the answer (flagged `resume_route:
  "revived_error_target"`; no revive-budget consumption).
- **Error-code relabeling** — the missing-pack 404 (previously
  mislabeled `INSTANCE_NOT_FOUND`) is now `404 NO_PENDING_QUESTION`
  (pack exists but is not pending, durable shadow non-pending) or
  `410 QUESTION_PACK_LOST` (pack gone after restart without a
  shadow). The `POST /answer` T3 pre-check (terminal asker) now runs
  BEFORE the CAS + SSE + events.
- **`ask_questions` payload additive field** — the question pack dict
  (`pack_to_dict`, the frozen SSE schema, `QUESTION_REQUESTED`
  payloads, and the `[JOB_EVENT]` body) gained a `pack_id` key;
  consumers that pattern-match on known keys are unaffected.

---

## [0.13.10] — 2026-09-22

### Fixed

Fixes the job-completed result arm on the true lineage (`fix/job-completed-result-arm`, `ae9264dc..93804b1b` + ship-prep round). The v0.13.9 cherry-pick fixed the premature-terminal arm, but its result arm was stale-master intent that was never wired on the post-Phase-5 pipeline — where the JobItem mirror columns are gone and `Task.result` is the durable home for the agent's last response (re-confirmed live on demo 2026-09-21/22: `result_summary=null`, zero `job_completed` rows).

- **Task.result content stamping** — `ProcessMessageProcessor.on_success` stamps `content: result.result_content` into the `complete_task` payload (producer side of the pipeline).
- **Resolver surfacing** — the dual-backed resolver (`work_resolver._job_to_record`) surfaces `Task.result` via `_parse_task_result_summary`, so `GET /api/jobs/{id}` and the SSE `event: completed` payload carry the agent's response instead of null.
- **EventKind.JOB_COMPLETED + persistence + notification kwarg** — new event kind; observer sibling publish (per-JobItem, fired outside the `instance_was_terminal` gate so shared-instance JobItems each get a row); `result_summary` threaded through publisher → broadcaster so `/api/notifications/stream` frames carry it.
- **ERROR-branch extraction restoration** — failed terminals extract best-effort result content again.
- **Intent5 emission-surface regression test** — real-daemon test asserting all four emission surfaces (SSE payload, GET body, persisted `job_completed` event row, notifications), strengthened (F6a) to assert truthy `content` inside the `result_summary` envelope.
- **Review-hardening (F1/F5/F6a)** — F1 env foot-gun guard: the e2e refuses to run when the resolved `POSTGRES_DB` is prod-like (`ensemble_prod`/`ensemble_live`/`ensemble_demo`) without an explicit `E2E_PG_DB` override — the exact ambient-fallback pattern that caused the 2026-09-21 live-DB incident. F5 acceptance-pack Intent5 gating: Intent5 failure with a daemon available ⇒ pack FAIL; no-daemon skip ⇒ LOUD `PASS-WITH-SKIP` verdict instead of a silent PASS. F6a envelope content assertions.

---

## [0.13.9] — 2026-09-21

### Fixed

Cherry-picked from `fix/empty-job-completed-event` (round-1 + round-2 + round-3 council fixes) onto the `release/prepare-v0.13.9` lineage (`origin/latest` @ `ea6a3944`, plus the four round-1/2/3 cherry-picks). job-completed events now carry Result body; result_summary written at completion; premature terminal emission gated on true subtree completion; failed/dead-letter events carry Error body — event-driven, no polling.

> **⚠ Superseded by [0.13.10] (2026-09-22):** the claim "job-completed events now carry Result body; result_summary written at completion" described stale-master (`fix/empty-job-completed-event`) intent and was **never true on this lineage** — the post-Phase-5 pipeline (JobItem mirror columns dropped, `Task.result` as the durable home) was never re-wired. Re-confirmed live on demo 2026-09-21/22: `result_summary=null`, no `job_completed` event rows. 0.13.10 fixes the result arm on the true lineage. The premature-terminal gating and failed/dead-letter Error-body claims from this entry WERE live and are unaffected.

---

## [Unreleased] — 2026-09-06

> **⚠ Operator callout — `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` default flipped
> OFF → ON.** If you were relying on the previous "unset = OFF" posture as your
> revert path, that is no longer the case: an unset (or blank) env now resolves
> to ON, and the bounded unstick runs by default. **Set
> `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0` (also accepts `00` / `-0` / `0.` /
> `0x0` / `false` / `no` / `off`) *before restarting* the daemon** to land the
> previous default behavior. Restart is required — the resolver is cached at
> boot (`daemon/services/job_recovery_service.py::_resolve_defer_autopromote_enabled`).
> Census unchanged (23/1/0): the unstick writes `task.is_deferred` only.

### Fixed — Unblock round (`fix/defer-self-witness-and-cleanup`)

#### Preflight defer-count wiring (ITEM 1)

The preflight endpoint (`GET /api/jobs/cleanup/preflight`) previously
read `manager._defer_block_resolver`, but `daemon/api.py:977-978`
wires **only** the `daemon.routers.queues` module-global singleton
(`set_defer_block_resolver(DeferBlockResolver(...))` is the
production-shape wiring). `manager._defer_block_resolver` was NEVER
assigned in production, so `defer_blocked_count` silently stayed `0`.
The preflight now consumes the already-wired singleton via
`daemon.routers.queues.get_defer_block_resolver()` — the same
factory the `GET /api/queues/defer-blocked` endpoint uses — so the
defer count surface and the resolver singleton can no longer drift.

The unit test that hand-set `manager._defer_block_resolver` (the
round-2 mask class that hid the gap) was updated to use
`set_defer_block_resolver(resolver)` and now exercises the
real-wiring path end-to-end.

#### `defer_pending_count` is an instance method (ITEM 4)

`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count()`
is now a public INSTANCE method (round-2's free function
`defer_pending_count(engine)` is gone). The engine is reached via
`self._job_repo.engine` internally — no direct
`_job_repo.engine` reach-through from the router anywhere. The SQL
constant `_DEFER_PENDING_COUNT_SQL` stays module-private (underscore
prefix).

#### Cycle docstring rewritten to truth (ITEM 3)

The `jobs_management.py` docstring claimed a `schemas → daemon.routers`
edge that does not exist. The real traced cycle goes through the
routers package boundary:

```
jobs_management → defer_block_resolver → routers.schemas →
  routers/__init__ → queues.py:32 → recursion
```

The deferred-import pattern stays (the cycle is real); the docstring
now describes the actual edges.

#### Canonical copy cross-surface pin (ITEM 5)

The cleanup-truth-split sentence (Round-2 ITEM 3 / T-H1) now lives
on three surfaces, each pinned by a plain-TS or Python spec:

* **BE**: `daemon/routers/jobs_management.py:cleanup_preflight`
  docstring;
* **FE**: `frontend/src/app/models/cleanup-preflight.model.ts` —
  exported constant `CLEANUP_TRUTH_SPLIT_COPY` referenced by
  `system-cleanup-confirm-dialog.component.ts`;
* **docs**: `docs/job-task-system.md` §8.5.

A drift between any two surfaces breaks `cleanup-preflight.model.spec.ts`
(plain-TS, no Angular TestBed) or `tests/unit/test_docs_cleanup_truth_split.py`.

### Known follow-ups (ledgered for visibility)

* **`TestIdlePredicatePgSqliteParity` boolean-bind helpers** —
  `tests/job_queue/test_defer_gate_post_settle_window.py` methods
  `_insert_queue` / `_insert_job` bind integer literals `1,0` for
  the boolean-typed columns (`is_system`, `is_paused`,
  `concurrency_limit`-as-flag). SQLite accepts the bind; PostgreSQL
  rejects it (`InvalidTextRepresentation` on the boolean type). The
  parity test currently runs red-on-PG until the helpers are made
  dialect-aware (e.g. use `True/False` and let SQLAlchemy coerce, or
  branch on `engine.dialect.name`). Tracked for the next round —
  not blocking the unblock.

---

### Fixed — WS4 Round-2 (`fix/defer-self-witness-and-cleanup`)

#### Force-complete TOCTOU re-check (Round-2 W1)

`JobQueueService.force_complete_defer_holder` now re-derives the
`has_live_work(instance_id)` predicate IMMEDIATELY before
`terminate_instance`, in addition to the original probe at the top
of the method. A small probe→terminate window remains; the second
call catches state that lands between the probe and the destructive
call (delegating-repo write, injected state). A busy re-check
returns `terminated=False, probe_busy=True` (200, NOT an exception).
The docstring no longer claims the guard is "race-proof" — the
remaining window is covered by `terminate_instance`'s own
idempotency-on-terminal cascade.

#### Holder-probe scope gap — task + child-instance arms folded in (Round-2 W2)

The original holder probe
(`JobRepository.has_active_non_deferred_work(None, requester_instance_id=<holder>)`)
was job-side only. It missed two live-work shapes the bulk zombie
scan already detected:

* a Task in `pending`/`running`/`paused` (no JobItem at all —
  direct Task, common for forked helpers / reaper sweep);
* a non-terminal child instance (a `waiting_children` parent whose
  subtree is still executing).

A new `SQLModelInstanceRepository.has_live_work(instance_id)`
single-instance companion reuses the same three CSV constants the
bulk `_build_zombie_scan_sql` bakes into the zombie predicate
(`_TERMINAL_STATUSES_FOR_ZOMBIE_SCAN`,
`_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN`,
`_LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN`) — derive-don't-reimplement.
`force_complete_defer_holder` now uses this companion for both the
initial probe and the W1 re-check; the predicate arms cannot drift
from the bulk scan. A holder with a live Task (no JobItem) OR with
a non-terminal child is now refused with
`terminated=False, probe_busy=True`.

### Changed

#### Preflight copy truth (Round-2 ITEM 3 / T-H1)

The cleanup preflight docstring replaces "Live missions will remain"
with the canonical split sentence (FE const + docs §8.5 use the
same — cap-exception micro-round ITEM 2, 2026-09-06 broadens to
"settled mirrors, or running Tasks without JobItems" so the
sentence matches the truth-survivor filter's TRUE semantics):

> Every ACTIVE job is cancelled, together with its whole subtree.
> Only missions holding settled mirrors, or running Tasks without
> JobItems — are kept.

Term single-owner: "stalled mission" (operator-facing). The
`zombie_instance_count` wire field NAME stays technical (wire
stability).

#### Operator vocabulary (Round-2 ITEM 8)

The cleanup endpoint's router docstring replaces "nuclear press"
with "System Cleanup" (the operator-facing button label). The
holder-action term is "stalled mission". The technical wire fields
(`zombie_instance_count`, `live_instance_count`, etc.) STAY
unchanged.

### Added

#### Mission tree panel API surface (2026-09-07, `feature/job-queue-mission-tree`)

`GET /api/missions` / `GET /api/missions/{id}` carry two additive nullable display
fields sourced from `instance_metadata`: `title` (string-stripped; wire-bounded at
500 chars) and `initiative_preview` (whitespace-collapsed FIRST, THEN truncated to
140 chars — `INITIATIVE_PREVIEW_MAX_CHARS` — by a plain slice, no ellipsis). Honest
nulls only: `null` when the key is absent, the value is non-string (the metadata JSON
column is untyped), or it collapses to empty — the server never fabricates a fallback
label and never stringifies (`str(123)` is forbidden).

`GET /api/jobs` gains a `mission_id` filter (`mission_id == instance_id`; narrows
`job_queue_items` by `JobItem.instance_id` on the EXISTING count + page queries — no
extra SELECT, count/page symmetric) plus a **deprecated** `instance_id` alias (used
only when `mission_id` is absent; OpenAPI `deprecated: true`). Empty-string
semantics (second-pass review fold): the primary `mission_id` rejects empty with
**422** (`min_length=1`); the alias accepts empty and means **filter-by-empty**
(200 with an empty page, `total == 0`) — NOT "no filter". The same `is not None`
semantics hold at the service and repository layers for direct callers. Unknown
mission ⇒ 200 empty page. Read-only throughout — zero DML, census frozen at 23.
Full contract: `docs/job-task-system.md` §8.6.

#### Public `defer_pending_count` surface (Round-2 ITEM 7 → unblock-round ITEM 4)

Originally a public free function
`daemon.services.defer_block_resolver.defer_pending_count(engine)`;
replaced in the unblock round by the public instance method
`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count()`.
The preflight endpoint (`GET /api/jobs/cleanup/preflight`) calls
the instance method via the resolver singleton — NO direct
`_job_repo.engine` reach-through from the router anywhere.
Schema or shape changes to the defer-pending-count SELECT have ONE
place to update (the SQL constant `_DEFER_PENDING_COUNT_SQL`,
which stays module-private — underscore-prefixed, not re-exported).

#### Pattern-(g) defer-job watchdog + `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED`

`Pattern-(g)` is the JOB-SIDE watchdog complement to the
task-side `Pattern-(a)` recovery
(`daemon/services/job_recovery_service.py`). Pattern-(g) covers
stuck JobItems (stuck-active jobs with dead instances,
stuck-queued jobs behind dead instances) — the job-queue
lifecycle. Pattern-(a) covers stuck Tasks — the task lifecycle.
The two are complementary: a stuck JobItem with a healthy Task is
a Pattern-(g) job; a stuck Task with a settled JobItem is a
Pattern-(a) task. Pattern-(g) does NOT inspect Task state;
Pattern-(a) does NOT inspect JobItem state.

The `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` env var (default ON,
env-only, restart-read) gates Pattern-(g)'s auto-promotion path.
Default ON is the new posture (operator decision 2026-09-06 after
soak): the bounded unstick runs by default on every drift sweep.
Operators who need to revert (or are mid-incident on this path) set
`ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0` (also accepts `false` / `off`)
+ restart; the explicit-OFF escape hatch is byte-identical to the
previous default behaviour (WARN only, no `task.is_deferred` writes).

### Notes — boot-log string change (operator grep alert migration)

The one-shot boot INFO emitted by `emit_defer_autopromote_boot_log`
(`daemon/services/job_recovery_service.py`) swapped its default-tag
prefix in tandem with the default flip:

* **Old (default-OFF posture)**: DISABLED state led with
  `"Pattern-g defer-self-witness autopromote DISABLED (default; %s unset or set to a falsy value) — ..."`,
  ENABLED state led with `"... ENABLED (operator opted-out via ...)"`
  (rare on this path).
* **New (default-ON posture)**: ENABLED state leads with
  `"Pattern-g defer-self-witness autopromote ENABLED (default; %s unset or set to a truthy value) — ..."`,
  DISABLED state leads with
  `"... DISABLED (operator opted-out via %s=0/00/-0/0./0x0/false/no/off) — ..."`.

**Operators with grep-based alert rules keyed on the old
`DISABLED (default; …)` prefix must update those rules** — without
the migration, the rule will go silent on a deployed instance where
the kill-switch is in its explicit-OFF escape-hatch posture. The new
substring is `DISABLED (operator opted-out via …)` (the path stays
identifiable via the leading `Pattern-g defer-self-witness autopromote`
token in either state). Boot-log emission cadence is unchanged: exactly
one INFO line per process; restart required to re-evaluate the resolver.

---

## [Phase 8] — Cleanup old architecture (FINAL)

The final cleanup phase. The `use_dependency_bus` feature flag and the `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` env var have been removed; the DependencyBus is now the SOLE completion authority with no flag, no kill-switch, and no fallback path.

### Removed

- **`USE_DEPENDENCY_BUS` flag** — `use_dependency_bus` field removed from `JobSystemConfig` (`daemon/config.py`). The `ENSEMBLE_JOB_SYSTEM_USE_DEPENDENCY_BUS` env var is no longer read. The bus path is now unconditional; all `if use_dep_bus:` / `_is_dependency_bus_enabled()` conditionals have been removed. The DependencyBus is the only completion authority for parent-waits-for-children; no rollback path is supported.

---

## [Unreleased] — 2026-06-21

### Phase D: Dependency Bus & Cleanup (M7 + M8)

The final phase of the decouple architecture migration. The system now has a single dispatcher, a single scheduling layer, and a single DB-backed completion authority. The CorrelationManager is retained as a rollback path; the `waiting_for` / `children` / `instance_hierarchy` artifacts are dead-but-present pending a manual migration.

### Changed

- **Source adapter default agent**: `default_agent` for Slack and Telegram source adapters changed from `"leader"` to `"ari"`. The `ari` agent is the designated chat-source front door (has `job` tool + `job-orchestration` skill). Existing deployments relying on the implicit `leader` default will now route chat messages to `ari` instead. Operators who need `leader` can set `default_agent: "leader"` explicitly in the source config.
- **Reasoning echo flipped from allowlist to denylist**: `reasoning_content` is now echoed back in multi-turn assistant messages for every model by default (previously a `deepseek`-only allowlist). Operators on endpoints that reject the extra `reasoning_content` field (e.g. the raw OpenAI API returning 400 on unknown fields) should set `OPENAI_REASONING_ECHO_DISABLED_MODELS=gpt-4o,claude` (example values) to opt those models out. The old `OPENAI_REASONING_ECHO_MODELS` env var is no longer read; leaving it set logs a deprecation warning at startup pointing to the new key.
- **`OPENAI_ALLOWED_MODELS` renamed to `OPENAI_SELECTABLE_MODELS`** (soft deprecation): the env var governing the spawn-time model allowlist has been renamed. The new primary name is `OPENAI_SELECTABLE_MODELS`; the legacy `OPENAI_ALLOWED_MODELS` is still honored when the new name is unset, but a one-shot deprecation warning is logged at startup. The internal config field (`config.llm.allowed_models`) and the governor `<allowed_models>` prompt block name are unchanged — only the env-var-level aliasing changed. **Operator action**: rename the env var in your `.env` / launcher exports from `OPENAI_ALLOWED_MODELS` to `OPENAI_SELECTABLE_MODELS` to silence the warning. The allowlist is consulted only by the four spawn-time selection flows (spawn `model=` override, weighted `llm_models` pool filter, `spawn_councilor` validation, session-restore re-validation); purpose-bound models (`model_title`, `model_keywords` when set to a fixed value, `model_vision`, compaction, skill evolution) are unaffected and continue to use their own env vars / YAML keys.
- **Empty / whitespace-only env values behave as unset** for both `OPENAI_SELECTABLE_MODELS` and `OPENAI_ALLOWED_MODELS` (legacy shell-style `:-` semantics preserved). A bare `KEY=` line in `.env` — which `launcher.sh` `load_env_file` exports verbatim — produces the documented default (`["agentic", "coding"]`) rather than an empty/unrestricted allowlist, and never fires a spurious deprecation warning. **There is no env-var path to unrestricted mode**; operators who want to lift restrictions entirely must hardcode `allowed_models: []` in `config.yaml`.

### Fixed

- **TOCTOU race in `job_create` watch registration**: When `watch=True`, the watcher is now registered BEFORE the job is enqueued, closing a race window where fast jobs could complete before the watcher was registered (causing missed `[JOB_EVENT]` notifications).
- **`job_continue` crash with `USE_WORKER_POOL=false`**: Direct `manager._task_repo` attribute access replaced with defensive `getattr` pattern.
- **`watch_job`/`watch_jobs` missing error/result context**: Terminal job notifications now pass `result_summary` explicitly so the downstream resolver can fill gaps.
- **`ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` default flipped OFF → ON** (operator decision 2026-09-06 after soak). The Pattern-(g) defer-self-witness watchdog's bounded unstick (`_resolve_defer_autopromote_enabled` in `daemon/services/job_recovery_service.py`) now runs by default — an unset env var, a blank value, and unparseable non-blank values all resolve to ON. The explicit-OFF escape hatch is byte-identical to the previous default: set `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED=0` (also accepts `false` / `off` / `no`) and restart. The default-OFF path still emits only the WARN and writes nothing — the `OFF=no-writes` contract is unchanged. Census unchanged (23/1/0): the unstick writes `task.is_deferred` only, which is out of the admission-state writer scope. Boot-log copy (`emit_defer_autopromote_boot_log`) updated to state the new default and the explicit-OFF escape hatch.

### Added

- **DependencyBus service** (`daemon/services/dependency_bus.py`) — new authoritative parent-waits-for-children mechanism. DB-backed via the `dependency_watchers` table; watcher state survives restart by construction (no `rebuild_from_db` hack). Public API: `watch(source_task_id, FollowUp)`, `emit_terminal(task_id, Outcome)`, `cancel_for_target(target_instance_id)`. The `start()` method warms the in-memory cache from the DB and recovers FIRED-but-unsent rows for crash safety.
- **`dependency_watchers` table** (`daemon/repositories/dependency_bus/`) — durable storage for in-flight parent→child correlation. Keyed by `source_task_id` for O(1) terminal-emit lookup. Columns: `watch_id`, `source_task_id`, `target_instance_id`, `follow_up_payload` (JSON), `metadata` (JSON), `created_at`, `fired_at` (nullable), `state` (PENDING / FIRED / CANCELLED). Uses the `WriteGuardSession` pattern.
- **`use_dependency_bus` feature flag** (default `True`) — toggles between the DependencyBus (authoritative) and the CorrelationManager (rollback path). `use_dependency_bus=False` reverts to the proven in-memory CM path with no code change.
- **`completion_delivery_path=cm|bus` structured log metric** — every terminal emit writes this key, letting operators verify which authority is in effect per request.
- **30-test Dependency Bus test pack** (`tests/test_dependency_bus.py`, 25 SQLite + 5 PostgreSQL in `tests/postgres/test_dependency_bus_pg.py`) — proves the bus eliminates the double-decrement bug, survives restart, enforces backpressure, and that `cancel_for_target` prevents orphan FollowUps.
- **Pause pre-check in `JobProcessor.start_job`** — instance pause is now a pre-check before admitting a job (replaces the historical `MessageJobHandler.handle()` pause-vs-terminate discrimination).

### Changed

- **`use_dependency_bus` default flipped to `True`** — the Dependency Bus is the source of truth for parent-child correlation. The CorrelationManager is no longer on the hot path.
- **Single execution path** — WorkerPool is the sole execution layer for all work (messages, tasks, completion reports, error reports). The JobQueue is now scheduling vocabulary only (priority, queue management, project scoping for `Task` rows).
- **Pause semantics** — `pause_instance_cascade()` now calls `dependency_bus.cancel_for_target()` on the paused root, cancelling any in-flight FollowUps that would otherwise land on a paused parent. `terminate_instance()` calls the same on the terminated node (after the DB cascade + lifecycle event publish).
- **Completion delivery via DependencyBus** — child completions and error reports now flow through `dependency_bus.emit_terminal()` (gated behind `use_dependency_bus`, called from `child_reports.py` / `error_reporting.py`) instead of `CM.resolve_response()`. The `MessageProcessingPipeline` still owns the shared six-stage flow but delegates the terminal emit to those services — no separate stage 5 hook.
- **Documentation** — `docs/architecture/message-processing-and-correlation.md`, `docs/architecture/job-task-pause-resume.md`, and `docs/architecture.md` updated to reflect the new architecture (single dispatcher, DependencyBus, removed dual-path, dead-but-present columns).

### Removed

- **`MessageJobHandler` (770 lines, `daemon/services/message_job_handler.py`)** — deleted in D12. The MESSAGE-dispatch branch in `JobProcessor` is also gone (D11). Pause-vs-terminate discrimination has moved to a pre-check in `JobProcessor.start_job`.
- **MESSAGE-specific helpers in `JobQueueService`** (`daemon/services/job_queue_service.py`) — removed in D13. The JobQueue no longer owns a `JobItem` lifecycle for messages; only `Task` rows are written.
- **`job_type='message'` JobItem rows** — no longer written. `JobItem` rows now exist only for non-message work (scheduler, webhook, project-rooted tasks).

### Deprecated

- **`Instance.waiting_for`** — dead-but-present column. Was the legacy control-flow counter (ADR-011), then became a rebuild-only cache for the CM. Post-Phase-D, completion flows through the DependencyBus. Pending drop via `20260621_000002_drop_legacy_completion_columns.sql`.
- **`Instance.children`** (denormalized JSON array) — dead-but-present cache. Pending drop via the same migration.
- **`instance_hierarchy` table** — dead-but-present. The hierarchy is encoded in `Instance.parent_id` and the live `dependency_watchers` rows. Pending drop via the same migration.

### Migration

- **`20260621_000002_drop_legacy_completion_columns.sql`** (new, IRREVERSIBLE, **not auto-applied**) — drops `Instance.waiting_for`, `Instance.children`, and the `instance_hierarchy` table. Manual application required after **2+ weeks of clean bus operation** in production. Operators should drain in-flight jobs before applying.

### Rollback

- **`use_dependency_bus=False`** — reverts to the CorrelationManager (in-memory `_pending` + per-parent `asyncio.Lock`) as the completion authority. No code change required; the flag is a kill switch. The CM was the proven completion mechanism for the previous release.
- **`use_legacy_waiting_for_cascade=True`** — re-enables the legacy `waiting_for` SQL cascade and the `SELECT COUNT(*)` fallback (defensive last-resort). Useful only if the bus AND the CM both fail in production.
- **`debug_completion_invariant=True`** — keeps the CM tracking in parallel with the bus and logs divergence between CM pending counts and `dependency_watchers` rows. Observability safety net for one more release.

### Migration map: which call sites changed

| Site | Before (Phase C) | After (Phase D) |
|------|------------------|-----------------|
| `daemon/services/message_processing_pipeline.py` | `CM.resolve_response()` not present (CM hook fired by `child_reports`) | unchanged — pipeline delegates to `child_reports` / `error_reporting` |
| `daemon/tools/instance.py` `send_message` | `notify_corr_register` (CM hook) | `dependency_bus.watch(FollowUp)` (under flag) |
| `daemon/services/child_reports.py` | `notify_corr_resolve` (CM hook) | `dependency_bus.emit_terminal()` (under flag) |
| `daemon/services/error_reporting.py` | `notify_corr_resolve` (CM hook) | `dependency_bus.emit_terminal()` (under flag) |
| `daemon/services/instance_lifecycle.py` `pause_instance_cascade` | (no bus call) | `dependency_bus.cancel_for_target(root_id)` (under flag) |
| `daemon/services/instance_lifecycle.py` `terminate_instance` | (no bus call) | `dependency_bus.cancel_for_target(instance_id)` (under flag) |
| `daemon/services/job_processor.py` `_process_next_job` | `job_type='message'` branch | branch removed; pause pre-check on `start_job` |

---

## Earlier (2026-06-20) — Phase A + B + C

- **Phase A** (premature-completion bug class): `USE_LEGACY_WAITING_FOR_CASCADE` and `DEBUG_COMPLETION_INVARIANT` flags; `CorrelationManager` is the authoritative completion mechanism; `waiting_for` is a rebuild-only cache per ADR-011. Fixed Race #1, Race #3, Race #5, the cross-dispatcher checkpoint corruption, and the sync/async deadlock.
- **Phase B** (`watch_job` Variant B): `watch_job` now routes through the CorrelationManager via `pending_jobs`; eliminates the `watch_job` fire-and-forget premature-completion bug class.
- **Phase C** (single dispatcher): `MessageJobHandler` demoted to cross-instance handoff only (C-M5); ExecutionGate collapsed from DB-backed lease to per-instance `asyncio.Lock` (C-M6, ~700 lines → ~40); all WorkerPool + JobQueue dispatch paths share a unified `MessageProcessingPipeline`; pause/terminate matrix regression-tested.
e matrix regression-tested.
