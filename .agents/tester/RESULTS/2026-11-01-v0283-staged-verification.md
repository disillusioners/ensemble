# v0.28.3 Staged Verification — project-skill-loader lane, final gate before ceremony

- **Date:** 2026-11-01
- **Staging:** `/home/nea/agents-ensemble/releases/v0.28.3` · staging worktree `/home/nea/ensemble-src-wt-stage-v0283` (detached @ `a84af3ce1a2556dac1de98d9721dfef9a5e2ae5a`, giter's, read-only to this lane)
- **Lineage:** merge `d434b72ab` (parents `cdd6510b0` + `bdd6c2966`) → bump `a84af3ce1` → tag `v0.28.3` = annotated `928c8842f4355cf9d0ddb56cf604c630e8225810` → `a84af3ce1` **PUSHED** (remote peeled object verified)
- **Verifying lanes:** S1+S4+S5 `a4b4e087` (read-only artifact/ceremony) · S2+S3 `b7d87c4b` (fenced boot + live fence) · evidence branch `tester/v0283-staged-verification` @ `a84af3ce1` (== origin/latest)
- **Lane's prior gates:** Phase-1 `1aacdb5c5` · Phase-2 `f6b578237` · Phase-3 `67dcc6a3d` · Phase-5 `03d53b1fb` + census addendum `3abad46b5` (all on `feature/project-skill-loader`, now merged as `d434b72ab`)

---

## S1 Artifact integrity — PASS

- **4-way sha MATCH** (recomputed, never trusted): release-dir binary == staging-worktree dist == manifest `binary_sha256` == binary-sidecar `artifact_sha256` == **`38fbb974d07432ecb368f6057d3439fe6b49e3b03369972f74eda6b8121e575a`**. FE sidecar `5531cbe4…` (byte-identical to the FE-index lineage since v0.27.6).
- Sidecars (in staging worktree; release payload source-less): both `git_head=a84af3ce1…`, `git_dirty=false`.
- Tag: local annotated → `a84af3ce1`; `ls-remote origin refs/tags/v0.28.3` → `928c8842f` peeling to `a84af3ce1` — **pushed, fully verified**.
- Manifest: counts **338 / 93 / 4930** ✓ · `version=v0.28.3` (prefixed) · `binary_version=0.28.3` · `known_schema_gen=20261031_120002_create_project_skill_usage_records.sql` · `rollback_safe=true` · `pipeline_scripts_floor_commit=953603108f…` · tree hashes: agents `f0548e7c…` (changed), frontend `bb076dc0…` + plugins `4715cf64…` (unchanged lineage).
- Payload source-less: top level exactly `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/` — no `scripts/`, no `.env`, no sidecars, no `.staging.*`/`.aside.*` residue.
- **Migration census (3-way):** source **119** (`uniq -d` empty ⇒ 119 distinct prefixes) · staged binary embedded **119** incl. `20261031_120000_create_project_skill_files.sql` + `…120001_create_project_skill_embeddings.sql` + `…120002_create_project_skill_usage_records.sql` + drift's `20261031_110000_create_bash_spawn_registry.sql` · live v0.28.0 binary **115** with all four absent → **delta exactly +4**. `known_schema_gen` = `20261031_120002` ✓.
- Prompt-artifact baseline **161/161 OK** — real script path `scripts/protection/generate_prompt_artifact_baseline.py` (the commissioning brief's path was wrong; noted), `import daemon` resolving in-worktree.
- Main-checkout porcelain byte-identical before/after (`0d3eb7e9…`).

## S4 Staged-content spot proofs — PASS (PYZ-level, live binary as negative control)

Method: PyInstaller `CArchiveReader` → PYZ TOC (marshal) → zlib-decompress module bodies → bytes-scan; staged PYZ **4649** modules vs live **4635**.

| Probe | Staged | Live |
|---|---|---|
| all 7 `project_skill_source` / `project_skill_file` modules | PRESENT | ABSENT (0) |
| `resolve_active_project` (W1 guard) | PRESENT (4 consumers) | ABSENT |
| `explicit_project_conflict` (refusal vocabulary) | PRESENT | ABSENT |
| `_pf_refusal` (A4 unified predicate) | PRESENT | ABSENT |
| `file_only` / `_file_only_bm25_search` (A2 degraded) | PRESENT | ABSENT |
| `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` | PRESENT | ABSENT |
| `ProjectSkillSourceConfig` | PRESENT | ABSENT |

**Default-ON at code level:** `ProjectSkillSourceConfig.enabled: bool = Field(default=True, …)`, `env_prefix="ENSEMBLE_PROJECT_SKILL_SOURCE_"` ⇒ unset ⇒ enabled. The knob gates **injection, not construction** (`manager.py:2257-2271`: OFF ⇒ `file_source=None` on every service) — the A11 mechanism.

## S2 Isolated boot smoke — PASS-WITH-NOTES

Safety pins held throughout: **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly in every boot, never `env -u`'d** (the 2026-10-31 incident rule); scratch-root derivation (binary+config copied to `mktemp -d`, run from there — no `releases/`/`state.json` under scratch); ports 18971/18972 (asserted free, never 9797/8079/8088); per-variable scrubs; zero 10.44.0.2 contact; release `config.yaml` carries no DB block (DB purely from scrubbed-then-set env → scratch PG).

**BOOT-1 (knob ON):** banner `Starting Ensemble v0.28.3` · livez 200 `0.28.3` · readyz 200 all-true with **`detail.skill_sources = {"enabled": true, "db_stack_present": true, "roots": [], "shadowed_db_skills": [], "overflow_dropped": [], "embeddings_pending": 0}`** · **all 3 project_skill tables materialized on scratch PG** (`project_skill_files`, `project_skill_embeddings`, `project_skill_usage_records`; 0 rows each on a clean scratch) · SIGTERM → full ordered graceful chain (`WorkerPool stopped → EventBus shutdown complete → Checkpointer adapter closed → MCP warm-up pool drained → Database engine disposed → Graceful shutdown complete → Application shutdown complete`), zero orphans, port freed.

**BOOT-2 (knob OFF, `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED=0` + `ENSEMBLE_SELF_ENV=sandbox`):** boots v0.28.3, livez/readyz 200 · **`detail.skill_sources.enabled=false` — published-DISABLED, not absent** · kill-switch log verbatim: *"[ProjectSkillSource] file source DISABLED via ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED (boot-time only, restart required) — file_source=None on every service; search/list/view results are byte-identical to the pre-loader daemon; zero pf- rows expected in any skill table."* · legacy DB-only surfaces serve (`GET /api/skills` → `[]`; search → dual-key empty) · graceful SIGTERM · **zero live-journal writes**.

**The notes (disclosures, not defects):**
1. **Fusion sub-leg PARTIAL — by design.** A source-tagged fused result is structurally unreachable via REST: `resolve_active_project` is A6 CONFIRM-ONLY and the REST shape carries no instance, so an explicit `project_id` refuses the file corpus. The lane seeded a valid canon and a bound positive-control project, then **captured the live refusal verbatim**: `[ProjectSkillSource] explicit project conflict — refusing the file corpus for this call (A6): explicit=f1d4974d… derived=None` + `[SkillSearch] project-file corpus refused (reason=explicit_project_conflict) — DB-only results`. That refusal is the A6 security property *working*; a fused result needs an instance-derived turn (out of smoke scope). Readiness `roots:[]` is the honest zero.
2. **No dedicated skill-source drain line in the smoke log** — with `roots:[]` nothing was indexed to drain; the smoke shows the generic graceful chain. The A5 drain mechanism was proven LIVE at Phase-5 E2E (real `InstanceManager`, latch-timed `aclose` awaited, idempotent second shutdown, SIGTERM-clean) and the code ships in this binary; an empty-index smoke cannot re-display it.
3. **Exit code 143 (128+SIGTERM), not 0** — consistent across boots incl. `timeout --preserve-status`. The binary completes the entire graceful sequence and then terminates by signal disposition. Achievable evidence delivered: complete ordered shutdown, zero orphans, ports freed, DB disposed. The brief's "SIGTERM-clean" is satisfied; "exit 0" as literally stated is not this binary's behavior.
4. Process notes: BOOT-1 ran three times (run 1 hit its own 90 s backstop mid-interlude; evidence consistent across runs); one scratch-hygiene one-liner produced a false "FOUND (BAD)" echo (`find` exits 0 regardless of matches) — the actual output shows zero matches; disclosed as-is.

## S3 Live fence — PASS-WITH-NOTES

| Probe | PRE | MID | POST |
|---|---|---|---|
| `state.json` sha256 | `f734a9c5…` | `6204828c…` | `6204828c…` |
| `current` | releases/v0.28.0 | same | same |
| livez | 200 v0.28.0 uptime 73540.09 s | 200 v0.28.0 74117.03 s | 200 v0.28.0 74405.92 s |
| MainPID / NRestarts / epoch | 227199 / 1 / 2026-10-31 10:19:02 | same | same |

Livez GET was the ONLY live-daemon interaction.

**Note (attributed + contained):** `state.json` PRE ≠ MID — exactly **3 inert `supervision_boot` rows** appended at the lane's three BOOT-1-era boot moments. Root cause proven in source (`daemon/tools/upgrade_tools.py`): with `ENSEMBLE_SELF_ENV` unset, auto-derive reads the managing unit from `/proc/self/cgroup` (worker shell = daemon child of `ensemble-main.service`) ⇒ self_env=live ⇒ the boot advisory appends to the live journal. **Promote fields byte-intact throughout** (`current=v0.28.0, previous=v0.27.15, in_flight=None, pending_op=None, quarantined=[]`); zero scratch markers in the journal; MID == POST; **BOOT-2 with the explicit `sandbox` marker wrote nothing**. Machinery-owned audit rows, not hand-removed (the v0.27.11 precedent class). *Recipe learning: pin `ENSEMBLE_SELF_ENV=sandbox` on the FIRST boot of any frozen-binary smoke — do not discover it on the second.*

## S5 Ceremony-input reconciliation — PASS (facts only)

- Promote command (verbatim): `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.3 bash scripts/upgrade/promote.sh live --f2-verified-closed`
- `releases/` = v0.27.14, v0.27.15, v0.28.0, **v0.28.1 + v0.28.2 (staged-unpromoted, superseded content-wise)**, v0.28.3 (+ `state.json`, its .bak snapshots, one pre-existing stale-broken lock dir — untouched).
- Disclosure-set verifiability:
  - (a) **M9-not-M10 boot shape** — artifact-verifiable: `skill_evolution: SkillEvolutionConfig = Field(default_factory=SkillEvolutionConfig)` (`daemon/config.py:5157`, non-Optional, always constructed; binary-level symbols present). The M9/M10 *label* mapping is commission-doc provenance.
  - (b) **4-row active shadow set** — artifact-verifiable from the Phase-5 census record. *Clarification pinned for the ceremony report: the ACTIVE set = the 4 canon rows (`9e79ccad` whole-tree-base-proven-diff, `fd1e01dc` regression-ab-partition-run, `76e3e1a7` failed-digest-equality-gate, `08e67030` ab-base-leg-provisioning-artifacts) **plus `08a4332f`** (the unresolved A/B partner, disclosed separately as user-owned cleanup); **`978ae5cc` is `is_active=false` and NOT in the active set** (it is the deactivated gen-0 of the same lineage).*
  - (c) **FE Skills page legacy rows** — page presence artifact-verifiable (`/skills` route chunks + `skills.component.ts`); the "REST DB-only v1" runtime semantics are source-consistent, not behavior-proven (no boots in that lane).
  - (d) **rollback_safe** — manifest `true`; all 4 new .sql files are `CREATE TABLE IF NOT EXISTS` + `CREATE [UNIQUE] INDEX IF NOT EXISTS` only, **0 executable FK/REFERENCES** (raw hits are comment prose); drift's `bash_spawn_registry` same class. ⚠️ **Manifest caveat:** there is NO `rollback_safe_source` field in the manifest — the explicit-source adjudication lives in the stage journal / tag annotation. Cite the journal if the ceremony expects source visibility.
- **Supersession note:** v0.28.1/v0.28.2 both staged-unpromoted; v0.28.3 supersedes content-wise.

---

## Verdict

# ✅ STAGED-VERIFIED — PASS-WITH-NOTES · ceremony-ready

**Per lane:** S1 **PASS** · S4 **PASS** · S2 **PASS-WITH-NOTES** · S3 **PASS-WITH-NOTES** · S5 **PASS**.

Every claimed value verified, zero mismatches: 4-way sha · tag pushed+peeled · 338/93/4930 · migration census 119/119/115 with delta exactly +4 (ours ×3 + drift ×1) · `known_schema_gen=20261031_120002` · baseline 161/161 · every commission surface compiled in and absent from live · knob default-ON at code level · **boots clean in BOTH knob states** on an isolated scratch stack, 3 project_skill tables on real PG, `detail.skill_sources` published both ways · live fence held (current / uptime continuity / MainPID; promote fields byte-intact).

The notes are disclosures, not defects: the REST fusion leg is unreachable *because A6 confirm-only works* (live refusal captured verbatim); the A5 drain displays empty because nothing was indexed (mechanism proven at Phase-5 E2E and shipped); exit is 143-by-disposition after a complete graceful chain; 3 inert journal rows from the auto-derive leg (contained; sandbox marker prevents the class).

**Carried gate inputs, unchanged:** census adjudication user-owned (unresolved A/B pair `08a4332f`); the lane's regression-clean reference commit is `ccfac9c75` (net-new fix; content rides v0.28.3 via merge `d434b72ab`).

## Budget

Artifact lane ~25 commands (one inline jq retry) · boot lane **14 m 26 s** (boots ~10 s / ~6 s to livez) · aggregate ≈ 35 min incl. dispatch. No provider outages this gate.

## Fences

Read-only on release/staging/live surfaces; livez GET the only live-daemon interaction; scratch PG local-only with drop-proofs; no 10.44.0.2 contact; main-checkout porcelain byte-identical per lane; staging worktree took 4 gitignored `__pycache__` writes from the baseline check (disclosed; porcelain 0 lines; zero payload effect).
