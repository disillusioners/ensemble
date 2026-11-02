# Phase 2 — Enforcement: E1–E4 + the D-1 resolver

**Lane:** `plane-pm-issue-tools` · base `941cb6321` · **Tier: Tier-1 blast-radius (≤30 min HARD, fail-loud) + Tier-2 whole-tree dual-side (MANDATORY — the lane's **aggregate** diff reaches the `daemon/manager.py` core seam in Phase 3 and the release is promote-blocking; see §4 — Phase 2's own diff does not touch the seam; G1 fold A-c)**
**Depends on:** Phase 0 (G2), Phase 1 (gate (ii) closed) · **Blocks:** Phase 5 (Task 3), Phase 6

---

## 1. Objective

Make the Plane workspace **structurally** env-scoped, per the user's directive (Discord 2026-11-02): workspace `nea` is production/live ONLY; dev + demo + sandbox must target `e2e` for **all** Plane operations (REST **and** MCP).

The load-bearing element is **D-1** — the lane-local live determination — because the shared env oracle is **fail-open in the guard's dangerous direction** (`daemon/tools/upgrade_tools.py:505-555` reads the **sibling** live install's `.env` and returns `"live"` for the dev checkout before the dev-shape signal at `:556-558` can run).

**Outcome:** a non-live environment **cannot** reach `nea` — not by configuration discipline, not by a shell script, but because the daemon refuses to construct a Plane client / register a Plane MCP row for a non-`e2e` slug, and refuses when the slug is **unset** (which today silently targets the account-default production workspace).

---

## 2. File-level scope

### 2.1 NEW — `daemon/plane_workspace.py` (the single decision point)

**Import-cheap, no `daemon/manager` coupling.** Shape (from `technical-analysis.md` §Recommended resolver shape):

```
daemon/plane_workspace.py          (NEW)
  ├─ dataclass PlaneWorkspaceResolution
  │     env_label: str | None      # 'live' | 'dev' | 'demo' | 'sandbox' | None
  │     env_source: str            # 'explicit-marker' | 'own-postgres-db' | 'unclassified'
  │     slug: str | None           # effective slug, or None when refused
  │     allowed: bool
  │     reason: str | None         # typed refusal reason
  └─ resolve_plane_workspace() -> PlaneWorkspaceResolution
```

> **Amendment P2-M (architect G0, 2026-11-02 — folded append-only): injectable signature + resolve-once threading.** The resolver's signature is `resolve_plane_workspace(env=None, *, postgres_db=None)` so the gate-(iv) matrix can drive it without touching the process env. **Resolve once per operation, thread the resolution** — the env TOCTOU between `is_available()` and `get_base_config()` is closed by resolving **once** and passing the result down, never by re-reading the env at each seam.

**Live determination (D-1) — the exact predicate:**

```
live  ⟸  (explicit ENSEMBLE_SELF_ENV marker == "live")
       OR (no explicit marker AND the daemon's OWN POSTGRES_DB == "ensemble_prod")
everything else  ⟸  non-live
```

- The explicit marker wins (house precedence, `upgrade_tools.py:260-279`).
- The `own POSTGRES_DB == "ensemble_prod"` fallback is a **self**-signal (the daemon's own connection target), **not** a sibling-file read ⇒ it cannot mis-classify the dev checkout (`ensemble_dev`) and does not reproduce the auto-derive defect.
- An **auto-derived** `live` is **NOT** trusted ⇒ dev/demo/sandbox/unclassified ⇒ `e2e` only.
- `None` (unresolved) ⇒ non-live ⇒ fail-closed ⇒ `e2e` only.

> **Amendment P2-A (architect G0, 2026-11-02 — folded append-only): predicate edge hardening.** The two-arm predicate as written above has genuine construction fail-opens. The **hardened** predicate is:
>
> ```
> 1. marker := read ENSEMBLE_SELF_ENV
>    - if marker ∈ {0,false,no,off}   ⇒ NON-LIVE (explicit "no auto-detection" attestation)
>                                     — SHORT-CIRCUITS the own-DB arm below
>    - if marker is a NON-EMPTY string that is not in VALID_ENVS
>                                     ⇒ NON-LIVE / unclassified + WARNING
>                                       (garbage: "Live", "prod", typos)
>    - if marker is EMPTY              ⇒ treat as ABSENT (house _read_marker_value semantics)
> 2. if marker == "live"              ⇒ LIVE (house precedence; the operator's attestation)
> 3. else if marker == "dev"|"demo"|"sandbox" ⇒ NON-LIVE
> 4. else (marker absent):
>    db := canonical DB name (see P2-B)
>    if db == "ensemble_prod"          ⇒ LIVE (own-DB arm — KEPT)
>    else                              ⇒ NON-LIVE (fail-closed)
> ```
>
> **Three edges, all of which are construction fail-opens as written:**
> - **Opt-out short-circuit** — `ENSEMBLE_SELF_ENV ∈ {0,false,no,off}` is an explicit *non-live* attestation ("no auto-detection"); as written, opt-out + `POSTGRES_DB=ensemble_prod` ⇒ LIVE ⇒ **fail-open**. The opt-out must resolve non-live **before** the own-DB arm.
> - **Garbage markers ⇒ non-live/unclassified + WARNING** — the match is **exact and non-normalizing** against `VALID_ENVS = ("dev","demo","live","sandbox")` (`upgrade_tools.py:187`); whitespace is stripped, **case is NOT folded**. `"Live"`, `"prod"`, and typos ⇒ non-live + a WARNING, never the own-DB arm.
> - **Empty marker = absent** — an empty marker + own-db=prod ⇒ live (the empty marker carries no attestation).
>
> **After P2-A exactly ONE fail-open construction survives in the whole design (opt-out+prod-db) and it is closed.** Garbage/empty/normalization edges are pinned.

> **Amendment P2-B (architect G0, 2026-11-02 — folded append-only): canonical DB-name input + hard bans.**
> - **Canonical DB-name input** mirrors the daemon's own precedence: `POSTGRES_DB` → the daemon's **own** `ensemble.json` `pg.db` (`daemon/repositories/factory.py:345-350`, `daemon/ensemble_config.py:126`). A resolver reading only the env var **mis-classifies an env-var-less prod install as non-live** — a **live-continuity break**.
> - The resolver **may** read the daemon's **own** `ensemble.json` for **this one input**; **every other filesystem read is BANNED**, and so is **importing `daemon.tools.upgrade_tools`** (the spec as written permits both accidents; `upgrade_tools` also pulls `langchain_core`, which would make the "import-cheap" module expensive).
> - **Enforcement:** a **grep pin** (no `upgrade_tools` import; no filesystem read beyond the own `ensemble.json` `pg.db` key) + a **zero-file-access test** (the resolver must not open any file when the env var is supplied). Both are Phase-2 pins (§5).

> **Amendment P2-C (architect G0, 2026-11-02 — folded append-only): one canonical slug normalization, consumed by BOTH lanes.** Today the REST lane (`plane_http_client.py:125-143`) and the MCP lane (`daemon/mcp/config.py:64-85`) normalize the slug **differently**, so one env value can target **two different workspaces**. The resolver owns **one** canonical normalization (trim + strip **one** outer matching quote pair) and **both** lanes consume it. **The quote-sanitization pin is re-pointed at the resolver** (the existing `tests/unit/test_mcp_quote_sanitization.py` coverage follows the normalization to its new home rather than being duplicated).

**Policy table (per `technical-analysis.md`; the ADJ-1 reconciliation noted in Phase 0 §4 applies to the live row):**

| Classification | Permitted slug | Typed refusal reason |
|----------------|-----------------|----------------------|
| live | `nea`; unset ⇒ account-default (byte-identical to today); `e2e` ⇒ **ALLOWED** (ADJ-1 live-exempt) | ~~`"workspace 'e2e' is not permitted in the production environment"`~~ **HISTORICAL — strict variant, NOT shipped (G1 fold B3).** Under ADJ-1 this reason string is **never emitted**; retained for the record only (tracked at `decisions.md` §3 ADJ-1 "Constraint in force either way"). The case is **pinned negatively**: emitting the strict-variant string ⇒ RED (legs added to P2/P3 + the Phase-6 boot pin) |
| non-live (dev/demo/sandbox) | `e2e` | `"workspace 'nea' is production-only; this environment resolves to <env>"` |
| non-live, slug unset | `e2e` | `"PLANE_MCP_WORKSPACE_SLUG is unset in a non-production environment; expected 'e2e'"` |
| non-live, slug = other | `e2e` | `"workspace 'X' is not permitted in a non-production environment; expected 'e2e'"` |
| unclassified (no marker, own db unknown) | `e2e` | `"environment could not be classified as production; refusing to target the 'nea' workspace"` |

**Implementation constraints:**

- **No raising validator, no boot abort.** Borrow the *shape* of `LiveViewsRootConfig` (`daemon/config.py:3178`, validator `:3262-3271`) — **not** its failure mode (W-4). Plane is optional; taking the whole daemon down because a third-party workspace is unset is strictly worse than skipping Plane.
- **No kill-switch** (C3 / lean #2). If the architect forces one at G0, it must be **default-ON** and gate **only the refusal**, never the resolution — the effective slug is always computed and always logged.
- The resolver must be **pure enough to test**: no DB, no daemon-global state; the env is injected (or read through a single overridable accessor) so the gate-(iv) pack can drive the matrix without touching the process env.

### 2.2 MODIFIED — `daemon/mcp/builtin_servers/plane.py` (E3 + E4)

| Site | Change |
|------|--------|
| `:263-298` `is_available()` | **Add the resolution gate.** A refused resolution ⇒ `False` ⇒ **no row** ⇒ **no tools** ⇒ the existing skip machinery applies (and Phase 3 makes that skip **loud**). `super().is_available()` stays the **first** statement (the per-boot quarantine consult — W-5/§Integration 1). |
| `:325-354` `get_base_config()` | Emit the **resolved** slug, never the raw env. The bearer + URL sanitizers stay. |
| `:128-140` `schema_version` | `"2"` → **`"3"`** (E4). The heal (`daemon/manager.py:3115-3132` → `:312-353`) rebuilds `headers` from the current env ⇒ the persisted `x-workspace-slug` is re-pointed (P8). The docstring must record the new reason. |
| `:143-152` `tool_name_prefix` docstring | **Phase 4** (charter) — but the same file; sequence the commits (see `plan-overview.md` §5 hot spots). |
| `:242-259` the **resilience read/write classifier** | **P2-K — see §2.2.1 (NEW daemon-code item, in scope).** |

**E4 rationale (re-verified by me):** `_heal_config_preserving_env` (`daemon/manager.py:312-353`) builds `healed = dict(default_config)` — the **template** config, which for plane is `build_config({})` → `base.py:229-250` → `get_base_config()` → **the current env** — and then merges the row's `env` sub-dict over the template's `env`, and **only** that axis. So `headers` are template-derived and rebuilt on every schema-version bump.

> **Amendment P2-F (architect G0, 2026-11-02 — folded append-only): the gate chain is FOUR links, not one.** The guarantee "no tools even with a stale row" is stronger than this plan claimed, and it must be stated so the reviewer does not weaken it:
>
> | # | Link | Site | Effect |
> |---|------|------|--------|
> | 1 | bootstrap skip | `daemon/manager.py:3073` | no row is created when the server is unavailable |
> | 2 | warmup | `daemon/manager.py:3328` | the server is not warmed up |
> | 3 | schema warm | `daemon/services/mcp_service.py:647` | no schemas are discovered |
> | 4 | preload gate | `daemon/services/mcp_service.py:1200` | **no tools are exposed even when a stale row exists** |
>
> All four are keyed on the **same classmethod** (`PlaneServerDefinition.is_available()`), so a single resolution gate closes all four. **`is_available()` has 5 call sites and is never per-request** — a pure resolution is therefore free; it **must not log per call** (one-shot pattern; see **P3-A/R-2**).

> **Amendment P2-G (architect G0, 2026-11-02 — folded append-only): deactivate the pre-existing ACTIVE row on a workspace refusal.** The skip branch leaves a **pre-existing ACTIVE row carrying the old prod slug**, visible unredacted via `GET /api/mcp-servers` (the AD2 gap), and the layer-3 registry-vs-row cross-check would read that row as *registered* — **it would not fire on exactly the case this lane exists for**. **Ruling:** on a workspace refusal, **deactivate the pre-existing plane row**, mirroring the `MCP_DISABLE_BUILT_IN_*` path (`daemon/manager.py:3057-3066`). This changes **no** live behaviour (live is never refused).
> **Boundary (do not widen the AD2 exposure):** the deactivation path must **not** log, echo, or otherwise surface the row's `config.headers` — it is the same unredacted-bearer exposure as AD2 (out of scope, escalation on record).
> **Implementation home (G1 fold B2, 2026-11-02):** P2-G is a **`daemon/manager.py` edit** ⇒ it is **implemented and pinned in Phase 3** (`phase3-plan.md` §2.2 + X3.12), the single manager.py-touching phase. Phase 2 owns this **design contract** only; it changes no manager code (§2.5/§3 stand).

> **Amendment P2-H (architect G0, 2026-11-02 — folded append-only): the heal is CONDITIONAL on availability.** The schema-drift heal runs **only when `is_available()` is True** — a refusal therefore **defers** the heal. **Phase-5 Step 3 must sequence allowed-boot-first**: the heal can only be observed on a boot where the resolution is **permitted**. (Folded into `phase5-plan.md` §5 Step 3 and §6 N-4.)
> **Implementation home (G1 fold B2, 2026-11-02):** the conditionality gate lives at the **manager heal site** (`daemon/manager.py:312-353`) ⇒ **implemented + pinned in Phase 3** (X3.13; pin row P2-H in §5 below is validated at Phase-3 exit). Phase 2 ships only the `plane.py` schema bump that triggers the (pre-existing) heal; pin **P8** (allowed-boot heal re-point) remains a Phase-2 exit criterion.

> **Amendment P2-I (architect G0, 2026-11-02 — folded append-only): plane-scoped slug-equality heal trigger — this is how the ADJ-1×E4 divergence is closed.** The heal is **one-shot, keyed on schema version**; under **live-exempt** a permitted-slug change on live (e.g. an operator re-pins `PLANE_MCP_WORKSPACE_SLUG`) leaves the persisted header **stale**. **Ruling:** add a **plane-scoped slug-equality trigger** — if the row's persisted `headers['x-workspace-slug']` ≠ the resolved slug ⇒ heal (row header re-materialised from the current env). This is **plane-scoped** (webfetch/context7 rows are untouched). It is the **whole** of the ADJ-1 divergence resolution; the live policy itself stays **live-exempt** (byte-identical).
> **Implementation home (G1 fold B2, 2026-11-02):** the trigger is a **`daemon/manager.py:3115-3132` edit** ⇒ **implemented + pinned in Phase 3** (X3.13; pin row P2-I below validated at Phase-3 exit). The §2.4 heal pack keeps its **P8 leg** green at Phase-2 exit; its P2-H/P2-I legs go green at Phase-3 exit.

> **Amendment P2-J (architect G0, 2026-11-02 — folded append-only): R17's extended clause.** The disclosure is accurate with one added clause: the row's bearer is **env-derived and re-derived (overwritten) on every heal** — **no new copy** is made and the **redaction posture is unchanged** (AD2 stays out of scope). This is a disclosure item, not a behaviour change.

> **Amendment P2-E (architect G0, 2026-11-02 — folded append-only): anchor drift corrected.** The REST-lane anchors in the pre-fold plan were off by a few lines; the authoritative anchors are: the raw-env fallback is **`:353-356`** (not `:356-360`) and `__init__` is **`:326-361`** (not `:326-362`). The **semantics** are unchanged; only the line numbers move. Implementers must re-derive the anchors at implementation time and cite by **quoted phrase** where the line moves again.

### 2.2.1 P2-K — the read/write tool classifier reconciliation (NEW daemon-code item, in scope)

> **Amendment P2-K (architect G0, 2026-11-02 — folded append-only; §8 open question 2 answered YES).** The architect ruled the daemon's read/write tool classifier **IN SCOPE** for this lane. Without it the commission's taken-for-granted premise ("the read strip applies to everyone") is **incoherent**: 47 catalog verbs are **neither read nor write** today.

**The defect (re-verified by me in this fold).** `daemon/mcp/builtin_servers/plane.py:242-259` declares the plane resilience config's `read_tool_patterns = ("list_", "get_", "search_")` and `write_tool_patterns = ("create_", "update_", "delete_", "add_", "remove_", "set_", "edit_", "assign_")`. The classifier is consumed by `is_read_tool` (`daemon/mcp/resilience.py:638-687`), whose write-first precedence returns `False` for anything that is neither read nor write; the CR-3 strip (`daemon/services/mcp_service.py:977-998`) keeps **only** `is_read_tool == True` schemas for every **non-full-access** agent.

**Measured against the captured 177-verb catalog, the pattern set is written against a vocabulary that does not exist:**

| Pattern group | Ruled disposition | Measured reality in the captured catalog |
|---|---|---|
| `add_` | **DROP** | **0** verbs match — the pattern is dead |
| `edit_` | **DROP** | **0** verbs match — the pattern is dead |
| `assign_` | **DROP** | **0** verbs match — the pattern is dead. (The real assignee verb is **`manage_work_item_assignee`**, which starts with `manage_`.) |
| `set_` | **KEEP** *(not in the architect's drop-list)* | **2** verbs match — `set_work_item_property_value`, `set_customer_property_values`. **Verified by me against `tools-list-inventory.md`; dropping it would mis-classify real writes.** |
| `remove_` | **KEEP** *(not in the architect's drop-list)* | **1** verb matches — `remove_work_item_relation`. **Verified by me; the pattern is live.** |
| `list_`, `get_`, `search_` | KEEP (read) | live |
| `retrieve_` | **ADD to read** | **23** verbs — currently **unclassified** ⇒ `is_read_tool` returns `False` ⇒ **silently dropped from every non-full-access agent's schema list** (risk **R18**) |
| `count_` | **ADD to read** | **1** verb (`count_work_items`) — currently unclassified ⇒ same R18 drop |
| `read_` | **ADD to read** *(G2 fold, approver iter-002 — blocker B4)* | **1** verb — **`read_work_item_attachment`** — currently unclassified. **Without this addition the ruled classifier leaves exactly ONE captured verb unclassified ⇒ FC11's "documented-unclassified must be EMPTY" is RED as ruled.** The plan's own stated method already counts `read` as a read prefix (`phase1-plan.md` §3 row 1) and the architect's record flags the same `read_*`×1 (`architecture-recommendation.md:30`) — the ruled pattern table simply omitted it. |
| `search_` | already read — the architect's read-list names it, but it is **already present** in `:242-245`; the **delta** is `retrieve_` + `count_` + **`read_`** | live (**1** verb) |
| `manage_` (**14**), `transfer_` (1), `complete_` (1), `link_` (1), `import_` (1), `attach_`/`detach_` (2), `upload_` (1), `resolve_` (1) | **ADD to write** | currently unclassified ⇒ they already read as "not read", so the strip behaviour is unchanged — but they are **not** in the write list, which the resilience cache logic and the write-first precedence depend on |

> **> (G2 fold, approver iter-002, 2026-11-02 — BLOCKER B4: the pattern table is AMENDED with `read_`, and the three-number census is RE-COMPUTED and PUBLISHED.)**
>
> **The arithmetic (verified by me against the 177-verb catalog — the extraction was checked to yield exactly 177 unique names, and the recompute reproduces the architect's on-disk row **48/82/47** exactly, which is the acid test that the `is_read_tool` semantics were modelled correctly):**
>
> | Classifier | read | write | unclassified | verdict |
> |---|---|---|---|---|
> | **on-disk today** (`plane.py:243-258`) | **48** = `list_`36 + `get_`11 + `search_`1 | **82** = `create_`26 + `update_`27 + `delete_`26 + `remove_`1 + `set_`2 | **47** | the **R18 trap** set |
> | **ruled P2-K as written** (no `read_`) | **72** = 48 + `retrieve_`23 + `count_`1 | **104** = 82 + `manage_`14 + `transfer_`1 + `complete_`1 + `link_`1 + `import_`1 + `attach_`1 + `detach_`1 + `upload_`1 + `resolve_`1 | **1** (`read_work_item_attachment`) | ⇒ **FC11 RED as ruled** — the approver's finding, confirmed |
> | **P2-K + `read_` (this amendment)** | **73** = 72 + `read_`1 | **104** (unchanged) | **0** | ⇒ **FC11 satisfiable** |
>
> **Two reconciliations this yields:**
> 1. **The corrected daemon classifier (73/104/0) equals the inventory's STATED method (73/104)** — the drift was in the daemon's classifier, not in the inventory. The architect's three-number disclosure therefore collapses to **two** numbers with **zero** unclassified.
> 2. **The drifted sub-counts, corrected:** **`manage_` = 14** (the pre-fold table said 13) and **`retrieve_` = 23** (the pre-fold table said "~20"). `add_` = `edit_` = `assign_` = **0** ⇒ dropping those three patterns loses **no** verb.
>
> **R18 re-verification (MECHANICALLY PROVEN, set arithmetic):** for a **non-full-access** agent the CR-3 strip keeps exactly the `is_read_tool == True` schemas. On-disk: **48 kept / 129 dropped** (82 write + 47 unclassified — including **every** `retrieve_*`). Corrected: **73 kept / 104 dropped** (all explicitly write). Set difference: **+25 restored** (`retrieve_`23 + `count_`1 + `read_`1) and **0 removed** ⇒ the corrected read set is a **STRICT SUPERSET** of today's ⇒ **no verb that was kept is dropped anywhere.**

**Concrete scope.**

| Site | Change |
|------|--------|
| `daemon/mcp/builtin_servers/plane.py:242-245` `read_tool_patterns` | **add** `"retrieve_"`, `"count_"`, **`"read_"`** *(the `read_` addition is the G2-fold B4 amendment — without it one captured verb stays unclassified and FC11 is RED as ruled)* |
| `daemon/mcp/builtin_servers/plane.py:246-259` `write_tool_patterns` | **add** `"manage_"`, `"transfer_"`, `"complete_"`, `"link_"`, `"import_"`, `"attach_"`, `"detach_"`, `"upload_"`, `"resolve_"`; **drop** `"add_"`, `"edit_"`, `"assign_"`; **keep** `"set_"` and `"remove_"` (verified live — see the table above) |
| `daemon/mcp/builtin_servers/plane.py:248-250` (the comment above the write tuple) | rewrite: it currently names `plane_set_issue_priority` and "the assignee-update tools" — **both** are non-existent vocabulary. The comment must describe the real patterns |
| `daemon/mcp/builtin_servers/plane.py` docstrings (`:143-152` and the module/resilience-config prose) | the `*_issue` / `*_assign_issue` names are corrected here too — this is the **same** charter-reconciliation surface as Phase 4, in daemon source (no prompt re-stamp) |

**Coverage pin (FC11, per architect §6).** The classifier must have **no unclassified catalog verbs**: assert that **every** catalog verb ∈ read ∪ write ∪ **documented-unclassified** — with the documented-unclassified set asserted to be **empty** for the current catalog. The pin runs against the **gate-(i) re-captured catalog** (Phase 1), so a future upstream addition that introduces a new verb shape is caught as a **classification gap**, not silently dropped. **(G1 fold A-a)** The 177-name catalog fixture used by this pin is a **checked-in test fixture** whose sorted-name-set sha256 MUST equal the gate-(i) re-capture's **FC10** sha256 (fixture drift ⇒ gate failure); it is **never** read from the planning dir at test runtime.

**Interaction with FC7 (disclosed).** P2-K **widens the read subset** (`retrieve_*`/`count_*` now survive the CR-3 strip). The architect's §5 caveat already records that **every** agent with no `tools.allow` can read the plane workspace via the empty-allow universe branch (`instance.py:513-520`) — the guard governs *where*, not *who*. P2-K makes that read surface **slightly larger**, which is why **FC7's detector** (per-agent resolved-toolset census + the pin "no agent other than PM resolves any `plane_*` MCP tool") becomes **load-bearing** in the same release. FC7 is folded into `phase3-plan.md` (observability + census) and `plan-overview.md` §10.

**Blast-radius consequence.** P2-K changes `is_read_tool`'s outcome for **non-full-access agents** ⇒ the 6 breaking test files listed in §2.4 (**BR-A**) enter the Phase-2 Tier-1 blast radius with pre-agreed remediation.

### 2.3 MODIFIED — `daemon/clients/plane_http_client.py` (E2)

| Site | Change |
|------|--------|
| `:296-308` `_rest_base_url()` | Return `None` when the resolution is refused (⇒ `is_available()` False ⇒ no client ⇒ the sync lane is inert). When allowed, use the **resolved** slug, not the raw env. |
| `:326-361` `__init__` | The client must carry the **resolved** slug; the raw-env fallback at **`:353-356`** (anchor corrected by **P2-E**) is replaced by the resolver's value. |
| `:364-374` `is_available()` | Keep the `PLANE_BASE_URL` + `PLANE_API_KEY` presence gate; **add** the resolution gate. Do **not** accept `PLANE_MCP_API_KEY` as a substitute (separation-of-concerns — the docstring's rationale stands). |
| `:388-398` `_headers()` | Emit the resolved slug in `x-workspace-slug`. |
| `:125-143` the REST slug normalization | **Replaced** by the resolver's **one** canonical normalization (**P2-C**) — the two lanes must not normalize differently. |

> **Amendment P2-D (architect G0, 2026-11-02 — folded append-only): the constructor bypass is CLOSED.** `PlaneHttpClient.__init__` accepts **explicit** `base_url`/`workspace_slug` (`:329-330`), which **bypasses the resolver** entirely — the worst case is a **split-brain**: the URL says `e2e` while the header says `nea`. **Ruling:** gate `__init__` / `create()` — a **refused** resolution ⇒ an **inert client** (the existing guards raise `PlaneAPIError`), and **explicit params may never introduce a slug the resolver did not return**. This makes Phase-5's N-5 mechanism-level assertion ("no `nea` write could occur") **true as written**, with no weakening needed (**P5-B**).
> **Verified by me:** there is **no cached / module-level client** (the module docstring plus the sole construction site `daemon/services/plane_sync_service.py:391` confirm it), so gating the constructor is sufficient — there is no second client to gate.
>
> **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 3: the INJECTION SEAM is now named, and it is closed for the injection path too.)** The approver found that P2-D as folded left an unnamed seam. The concrete seam is the **dependency-injection parameter** of the sync service:
>
> | # | Seam | Anchor | What P2-D must close there |
> |---|------|--------|----------------------------|
> | 1 | `PlaneHttpClient.__init__(base_url=…, api_key=…, workspace_slug=…)` — the **direct** constructor | `daemon/clients/plane_http_client.py:326-332` (the three explicit params at `:329-331`) | an explicit `workspace_slug` (or a `base_url` whose path carries a slug) may **not** introduce a slug the resolver did not return ⇒ a refused resolution ⇒ **inert client**; a resolution that is permitted but disagrees with the explicit slug ⇒ the explicit slug is **overridden by the resolved slug** (or the client is refused) — the two may never disagree |
> | 2 | the **injection parameter** of the sync service — `http_client: PlaneHttpClient \| None = None`, stored at `self._http_client = http_client`, returned by the `if self._http_client is not None: return self._http_client` fast-path | `daemon/services/plane_sync_service.py:334` (param), `:341` (docstring), `:349` (assignment), `:389-390` (the fast-path) | **the injected client must be gated at the injection point**: an injected client whose slug is not the resolved slug is **rejected** (inert / `PlaneAPIError`), exactly like the direct constructor. **Verified by me:** **no production caller injects** — the parameter exists for tests — so closing it changes **no** production behaviour; the closure is a **test-seam hardening** that prevents a future test (or a future production caller) from reintroducing the split-brain |
>
> **The seam to use for the resolver injection (per the P2-M injectable signature already folded):** `resolve_plane_workspace(env=None, *, postgres_db=None)` is injected **at the boundary** — `PlaneHttpClient.__init__` / `create()` accept an optional `resolution=None` parameter (defaulting to the module-level resolver call), and `PlaneSyncService.__init__`'s `http_client=` path is validated **against the same resolution**. **No production caller changes** (there are none that inject); the parameter exists so the gate-(iv) pack can drive the matrix deterministically.

**Unchanged:** the four project methods (`:563`/`:606`/`:652`/`:677`) — the lane ships **no** new REST surface (C2).

### 2.4 NEW — test packs

| File | Content |
|------|---------|
| `tests/unit/test_plane_workspace_resolver.py` | **Gate (iv) — the environment-classification proof** (P2, P4, **P2-A**, **P2-B**). Table-driven over the resolver matrix; each case asserts the permitted slug **and** the typed reason string. Critically: "no marker + own db `ensemble_dev` ⇒ **non-live**". **Plus the hardened edges:** opt-out (`0/false/no/off`) ⇒ non-live **even with** own-db=prod (**P2-A**); garbage marker ⇒ non-live + WARNING; empty marker ⇒ absent; exact non-normalizing `VALID_ENVS` match; canonical DB-name input from the own `ensemble.json` `pg.db`; the **grep pin** (no `upgrade_tools` import, no file read beyond the own config) and the **zero-file-access test** (**P2-B**). |
| `tests/unit/test_plane_workspace_enforcement.py` | **P3** — E2/E3 chokepoint behaviour: non-live + `nea` ⇒ refuse; non-live + unset ⇒ refuse; live + `nea` ⇒ allowed (byte-identical); live + unset ⇒ allowed (byte-identical). Plus **NR1/NR9** (live byte-identical; the REST lane stays inert on live/demo because `PLANE_API_KEY` is absent). **Plus P2-D** (the explicit-constructor bypass: an explicit `workspace_slug`/`base_url` may never introduce a slug the resolver did not return; a refused resolution ⇒ inert client) and **P2-C** (one canonical slug normalization consumed by both lanes). |
| `tests/unit/test_plane_mcp_row_heal.py` | **P8** — the schema-version bump re-points the persisted slug; a `config.env` sentinel survives the heal. **Plus P2-H** (the heal is conditional on availability — a refusal defers it) and **P2-I** (the slug-equality trigger: a persisted slug ≠ the resolved slug ⇒ heal). *(G1 fold B2: the P2-H/P2-I legs are validated at Phase-3 exit — their implementation is manager.py-side and lives in Phase 3; the P8 leg is a Phase-2 exit criterion.)* |
| `tests/unit/test_plane_tool_classifier_coverage.py` | **P2-K / FC11** — the classifier-coverage pin: every catalog verb (from the gate-(i) re-capture — the **hard** Phase-2 dependency) ∈ read ∪ write ∪ documented-unclassified, with the documented-unclassified set asserted **empty** for the current catalog; plus the pattern-level assertions (`add_`/`edit_`/`assign_` dropped; `set_`/`remove_` kept; the **3** read patterns added — `retrieve_`/`count_`/**`read_`** — and the 9 write patterns added); **plus the R18 set-arithmetic leg** (kept set = 73, dropped = 104, kept ⊃ today's 48 by exactly 25, today's − corrected = ∅). **> (G2 fold, approver iter-003 — non-blocking item 12: the 177-name catalog FIXTURE now has a named host.)** The coverage pack does **not** re-derive the catalog — it reads a **committed fixture**: **`tests/fixtures/plane_catalog.json`** (the gate-(i) re-capture, copied verbatim by the **Phase-1 gate** into the repo so the test is hermetic and does not depend on the planning dir at test time) + the loader **`tests/helpers/plane_catalog_fixture.py`** (the fixture module that yields the sorted name list + the schema property map). **The Phase-1 gate therefore produces BOTH** the planning-dir artifacts **and** the committed fixture — the gate produces what every consumer consumes. |
| `tests/unit/test_plane_sync_unavailable_reason.py` | **P2-L** — `plane_sync_service.unavailable_reason()` must carry the **resolver's refusal reason** (not "API key not set") when the refusal cause is a workspace refusal. |

> **Amendment BR-A (architect G0, 2026-11-02 — folded append-only): the 6 breaking test files enter the Phase-2 Tier-1 blast radius with pre-agreed remediation.** The cause is mechanical: **unclassified test environments become non-live ⇒ refused**, so these files' expectations change. **All six exist today (verified by me in this fold):**

| # | Test file | Size | Pre-agreed remediation |
|---|-----------|------|------------------------|
| 1 | `tests/unit/test_plane_sync.py` | 2,206 lines | expectation update — the sync lane is refused under a non-live resolution; the file must assert the **refusal**, not the transport |
| 2 | `tests/unit/test_plane_sync_phase3.py` | 1,081 lines | expectation update — same class as (1) |
| 3 | `tests/unit/test_plane_sync_phase4.py` | 979 lines | expectation update — same class as (1) |
| 4 | `tests/integration/test_plane_sync_endpoint.py` | 259 lines | expectation update — the endpoint must report the resolver's typed reason (**P2-L**) |
| 5 | `tests/unit/test_mcp_quote_sanitization.py` | 476 lines | **pin re-point** — the quote-sanitization assertions follow the canonical normalization to the resolver (**P2-C**) rather than being duplicated |
| 6 | `tests/unit/test_mcp_service.py` | 1,766 lines | expectation update — the CR-3 strip's schema set changes because of **P2-K** (the read subset widens by `retrieve_*`/`count_*`) |
| 7 | `tests/unit/test_plane_identifier.py` | **404 lines** *(G2 fold, approver iter-002 — non-blocking item 12: the pre-fold "~430 lines" is corrected to the file's ACTUAL length, verified by me in this fold)* | expectation update — the fixture constructs `PlaneHttpClient(..., workspace_slug="nea", ...)` **with an explicit slug** (`:365-370`, verified by the G1 council), which collides with **P2-D**'s constructor gate (an explicit param may never introduce a slug the resolver did not return); the fixture must obtain its slug from the resolver or assert the inert-client refusal — **G1 fold B7** |

**Remediation rule (pre-agreed, so the fix cycle is not a redesign):** these are **expectation updates to a deliberate behaviour change**, not production-code workarounds. Each must be a **narrow assertion change** that documents the new contract; **none** may re-introduce an env-dependent bypass. If any of the **seven** (G1 fold B7 added the seventh) requires a production change to keep it green, that is a **finding**, not a remediation — escalate.

### 2.5 NOT touched in this phase — **CORRECTED by P2-L**

> **Amendment P2-L (architect G0, 2026-11-02 — folded append-only): `daemon/services/plane_sync_service.py` IS now IN scope — the pre-fold §2.5 contradicted itself.** The architect's ruling: `plane_sync_service.unavailable_reason()` **must carry the resolver's refusal reason**; otherwise the REST **503** and the agent tool report *"API key not set"* for what is actually a **workspace refusal** — a misleading diagnostic, and the exact class of "the refusal looks like something else" this lane exists to prevent.
> **What changes:** `daemon/services/plane_sync_service.py:374-381` `unavailable_reason()` gains a resolver-refusal branch (typed reason verbatim), and its client-construction site (`:391`) inherits the P2-D inert-client behaviour. **What does NOT change:** the **watchdog knobs** (`daemon/config.py:2607-2641`) remain untouched — they are the REST lane's tuning, not part of this lane. The file was **already** in the Phase-2 blast-radius list (it is an import-consumer of the client), so this amendment **resolves a contradiction** rather than widening the scope.

**Still NOT touched in this phase:** `daemon/manager.py` (Phase 3), `daemon/tools/instance.py` (Phase 3), `daemon/registry.py` (Phase 3), `daemon/clients/plane_http_client.py`'s method set, the `plane_sync_service` watchdog knobs, `agents/**`, `scripts/**`, `daemon/migrations/**` (**no schema change — C5**).

---

## 3. Dependencies

| From | Why |
|------|-----|
| Phase 0 G2 | the design is frozen (incl. ADJ-1 live policy, ADJ-2 lane-local predicate) |
| Phase 1 gate (ii) | the non-live ⇒ `e2e` pin must be satisfiable; without it the pin does not ship (Phase-1 failure branch) |
| Phase 1 gate (i) | **> (G2 fold, approver iter-002 — non-blocking item 11: the pre-fold row understated this dependency; it is now a HARD dependency, not a "not required" nicety.)** The pre-fold row said *"not required for the enforcement"*. That was **wrong**: the gate-(i) re-capture is the **catalog fixture** that **P2-K's coverage pin (FC11)** consumes — the coverage assertion is *"every catalog verb ∈ read ∪ write ∪ documented-unclassified"* against **the re-captured catalog**, so without gate (i) the FC11 pin has **no input** and cannot run. It is also the source of the **resolved environment labels** used in the refusal strings and of the **per-tool schema token counts** that feed FC9. **Phase 1 gate (i) ⇒ HARD dependency of Phase 2.** (The G1 fold B2 split moved the manager.py-site *implementation* of P2-G/P2-H/P2-I into Phase 3 — that is a **different** dependency, and it is why both rows are needed.) |

**Coupling to Phase 3.** Both phases touch `daemon/mcp/builtin_servers/plane.py` (this phase: E3/E4; Phase 3: the `unavailable_reason` override). **Sequence: this phase's `plane.py` commit first; Phase 3's second.** Both phases touch `daemon/manager.py` (Phase 3 only, in practice — this phase changes no manager code). **G1 fold B2:** the manager.py-site amendments **P2-G** (stale-row deactivation), **P2-H** (heal conditionality gate) and **P2-I** (slug-equality heal trigger) are **implemented + pinned in Phase 3** — one manager.py-touching phase; their pins are validated at Phase-3 exit (`phase3-plan.md` §2.2/§5/§6 X3.12–X3.13).

---

## 4. Tier gating (per `.agents/tester/rules/ensure.md:11-28`)

**Tier-1 (DEFAULT) — blast-radius leg.** Scoped dual-side FAILED-list diff over the declared blast radius. Budget **≤ 30 min HARD, fail-loud** — abort + report + **ONE** auto re-scope attempt; never silent grinding. Recipe body **read from** `.agents/tester/skills/blast-radius-leg/` before executing — deterministic path, never `skill_search` discovery.

**Declared blast radius for Tier-1:**

- `daemon/plane_workspace.py` (new)
- `daemon/mcp/builtin_servers/plane.py` (E3/E4 + **P2-K** classifier)
- `daemon/clients/plane_http_client.py` (E2 + **P2-C/P2-D/P2-E**)
- `daemon/services/plane_sync_service.py` (import-consumers of the client — verify no behaviour change; **+ P2-L** `unavailable_reason`)
- `tests/unit/test_plane_workspace_*.py`, `tests/unit/test_plane_mcp_row_heal.py`, `tests/unit/test_plane_tool_classifier_coverage.py`, `tests/unit/test_plane_sync_unavailable_reason.py`
- **BR-A — the 7 breaking test files** (6 architect-folded + 1 G1-added (B7); existence verified):
  - `tests/unit/test_plane_sync.py`
  - `tests/unit/test_plane_sync_phase3.py`
  - `tests/unit/test_plane_sync_phase4.py`
  - `tests/integration/test_plane_sync_endpoint.py`
  - `tests/unit/test_mcp_quote_sanitization.py`
  - `tests/unit/test_mcp_service.py`
  - `tests/unit/test_plane_identifier.py` *(G1 fold B7 — P2-D constructor collision at `:365-370`)*

  Each enters with the **pre-agreed remediation** in §2.4 (expectation update / pin re-point). **A file that cannot be remediated by a narrow expectation change is a FINDING, not a blocker to be worked around.**

**Tier-2 (MANDATORY) — whole-tree dual-side.** The mechanical core-seam list includes `daemon/manager.py`; this lane's **aggregate** diff reaches it in Phase 3, and the release is promote-blocking ⇒ Tier-2 applies regardless (ensure.md §Gate Tiers). Budget ≤ 90 min advisory; fail-loud reporting still applies. Recipes: `.agents/tester/skills/whole-tree-base-proven-diff/` or `.agents/tester/skills/regression-ab-partition-run/` — **do not hand-roll a slow runner**.

**Non-negotiables for every leg.**

- Pack-mapped: every validation runs as a **pack** (or ad-hoc pack) with the dual-layer timeout — **never** a bare `pytest`.
- **Node-cap + measured-rate projection guard** before legs: project wall-clock from the measured collection/test rate; a projected or actual breach ⇒ STOP + report + one re-scope.
- **Proof form:** mechanical derivation → declared set → `--collect-only` slicing proof (executed == declared, counts recorded) → identical commands both legs → red-at-base ≠ net-new → 3× flaky isolation on diff nodes only. FAILED-digest comparison per `.agents/tester/skills/failed-digest-equality-gate/`.
- **Fences:** `timeout 300` outer + `--timeout=120` inner; **explicit per-variable** `env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL` (the `env -u POSTGRES_*` glob form is a **silent NO-OP**); `-p no:randomly`; worktree-local `.venv/bin/python` (worktrees carry no `.venv`).
- **Pre-exclusion registry** applies (regression legs only).

---

## 5. Pin / test obligations

| # | Pin | Cases | Mutation |
|---|-----|-------|----------|
| **P2** | resolver policy matrix | explicit `live` ⇒ live; explicit `demo` ⇒ demo; explicit `sandbox` ⇒ non-live; **no marker + own db `ensemble_dev` ⇒ NON-LIVE**; no marker + own db `ensemble_prod` ⇒ live; no marker + unknown db ⇒ non-live/unclassified; explicit opt-out ⇒ non-live; **explicit `live` + slug `e2e` ⇒ ALLOWED (ADJ-1 live-exempt) AND the strict-variant reason string is NOT emitted (G1 fold B3)**. **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 5: the missing live+THIRD-slug leg.)** **explicit `live` + a THIRD slug (neither `nea` nor `e2e`, e.g. `other`) ⇒ ALLOWED** — under the adjudicated **ADJ-1 live-exempt** rule, live permits **any configured slug**; and **non-live + the same third slug ⇒ REFUSED** with the typed reason `"workspace 'other' is not permitted in a non-production environment; expected 'e2e'"`. Each case asserts the permitted slug **and** the typed reason. | flip the D-1 predicate to trust the auto-derive ⇒ the `ensemble_dev` case goes RED; **re-introduce the strict-variant refusal on live+`e2e` ⇒ the never-emitted assertion goes RED**; **refuse a third slug on live ⇒ the live-exempt leg goes RED** |
| **P3** | E2/E3 chokepoints | non-live + `nea` ⇒ refuse; non-live + unset ⇒ refuse; live + `nea` ⇒ allowed; live + unset ⇒ allowed; **live + `e2e` ⇒ allowed (ADJ-1 — no refusal rendered, strict string absent — G1 fold B3)**; **live + a third slug ⇒ allowed (G2 fold NB5 — the live-exempt rule is about the *environment*, not a slug allow-list)**; **> (G2 fold, approver iter-002 — non-blocking item 8: the "live + unset ⇒ account-default" leg is now pinned IN BOTH LANES.)** The **MCP** lane: with `PLANE_MCP_WORKSPACE_SLUG` **unset** on a live env, the resolution is **allowed** and the emitted header carries the **account-default** workspace (no refusal, no typed reason). The **REST** lane: `_rest_base_url()` composes `f"{base}/api/v1/workspaces/{slug}/projects/"` — with the slug **unset** it returns **`None`** ⇒ `is_available()` False ⇒ the REST lane is **absent** (this is the pre-existing REST behaviour, distinct from the MCP lane's account-default). **Both legs are asserted, separately, because they differ** — the pin must not assume symmetry. | drop the resolution gate from `is_available()` ⇒ both refusal cases go RED; render the strict-variant refusal on live+`e2e` ⇒ RED; **refuse a third slug on live ⇒ RED**; **make the MCP lane refuse an unset slug on live ⇒ the account-default leg goes RED**; **make the REST lane resolve a slug when unset ⇒ the REST-absent leg goes RED** |
| **P4** | D-1 fail-open | a dev checkout misclassified as `live` by the shared oracle **must not** obtain a `nea`-bearing resolution | replace the lane-local predicate with `upgrade_tools._self_env_marker()` ⇒ the dev case goes RED |
| **P8** | E4 heal | after the schema bump the persisted `config.headers['x-workspace-slug']` == the resolved slug; a `config.env` sentinel survives | revert the schema bump ⇒ the row keeps the stale slug ⇒ RED |
| **P2-H** *(G1 fold A-b — validated at Phase-3 exit per B2; **classified ONCE by the G2 fold, approver iter-002 — non-blocking item 2**)* | **heal conditional on availability** — **CLASSIFICATION: this IS a mutation pin with a RED leg** (the approver found it classified two ways: this row said "pin with a RED leg", while `plan-overview.md` §11's master index and the Phase-6 G13 enumeration listed it as "not a mutation pin"). **The pin reading is correct** — a RED leg exists and is meaningful (remove the conditionality gate ⇒ the heal runs on a refusal boot ⇒ the refusal-boot schema-version-unchanged assertion goes RED). **The G2 fold therefore corrects the master index + the G13 enumeration** (both previously said "not a mutation pin") and keeps this row as the **single** statement. Its **implementation home is Phase 3** (the `manager.py` heal site, `:312-353`) ⇒ validated at **Phase-3 exit** (X3.13), while this Phase-2 row records the contract it must satisfy. | remove the conditionality gate ⇒ the heal runs on a refusal boot ⇒ the refusal-boot schema-version-unchanged assertion goes RED |
| **P2-A** | **predicate edge hardening** | opt-out (`0/false/no/off`) ⇒ **non-live even with own-db=prod**; garbage marker ⇒ non-live + WARNING; empty marker ⇒ absent; exact non-normalizing `VALID_ENVS` match | restore the as-written predicate (opt-out falls through to the own-DB arm) ⇒ the opt-out+prod-db case goes RED |
| **P2-B** | **canonical DB-name input + hard bans** | the resolver reads `POSTGRES_DB` first, then the daemon's **own** `ensemble.json` `pg.db`; an env-var-less prod install ⇒ **live** (live continuity); the resolver **does not import** `daemon.tools.upgrade_tools`; the resolver opens **no file** when the env var is supplied (zero-file-access test) | read only the env var ⇒ the env-var-less prod case goes RED; import `upgrade_tools` ⇒ the grep pin goes RED |
| **P2-C** | **one canonical slug normalization** | the same env value yields the **same** slug in the REST lane and the MCP lane (no split-brain) | restore the per-lane normalizations ⇒ the split-brain case goes RED |
| **P2-D** | **constructor bypass closed** | an explicit `workspace_slug`/`base_url` may never introduce a slug the resolver did not return; a refused resolution ⇒ **inert client** | allow the explicit param through ⇒ the split-brain case goes RED |
| **P2-G** | **stale-row deactivation on refusal** *(G1 fold B2: implemented + validated in Phase 3 — X3.12)* | a pre-existing ACTIVE plane row is **deactivated** when the resolution is refused (mirroring `MCP_DISABLE_BUILT_IN_*`), so the registry-vs-row cross-check does not read it as registered | drop the deactivation ⇒ the cross-check reads it as registered ⇒ RED |
| **P2-I** | **slug-equality heal trigger** *(G1 fold B2: implemented + validated in Phase 3 — X3.13)* | a persisted slug ≠ the resolved slug ⇒ the row is healed (not only a schema-version bump) | remove the trigger ⇒ a permitted-slug change leaves a stale header ⇒ RED |
| **P2-K / FC11** | **classifier coverage** | every catalog verb ∈ read ∪ write ∪ documented-unclassified, with documented-unclassified **empty**; the pattern set matches the ruled disposition (`add_`/`edit_`/`assign_` dropped; `set_`/`remove_` kept; `retrieve_`/`count_`/**`read_`** read — **the `read_` addition is the G2-fold B4 amendment**; the 9 write patterns added). **Plus the R18 set-arithmetic leg (G2 fold B4):** for a non-full-access agent the kept set is **73** verbs and the dropped set is **104**; the kept set is a **STRICT SUPERSET** of today's 48 (difference = **+25** = `retrieve_`23 + `count_`1 + `read_`1) and the set difference `today − corrected` is **EMPTY** ⇒ **no verb that was kept is dropped anywhere.** | revert any pattern ⇒ the coverage assertion goes RED (a `retrieve_*` revert drops it from the read set ⇒ R18 returns); **remove `read_` ⇒ one verb is unclassified ⇒ FC11's empty-set assertion goes RED**; narrow the read set so any of the 25 restored verbs leaves the kept set ⇒ the superset leg goes RED |
| **P2-L** | **sync refusal reason** | `plane_sync_service.unavailable_reason()` carries the resolver's **typed refusal reason** for a workspace refusal (not "API key not set") | restore the old reason ⇒ the diagnostic assertion goes RED |

**No-regression obligations verified in this phase:** NR1 (live byte-identical), NR7 (no schema change), NR9 (the REST lane stays inert on live/demo), NR2 (the shell scrubs are not touched — assert the two shell files are **byte-identical** to the base in the diff).

---

## 6. Exit criteria

| # | Criterion |
|---|-----------|
| **X2.1** | `daemon/plane_workspace.py` exists, is import-cheap, and its matrix is fully covered by the gate-(iv) pack (P2/P4), with every case asserting the **typed reason string**. |
| **X2.2** | A non-live environment **cannot** produce a Plane REST client or a `plane` MCP row for a non-`e2e` slug, and **cannot** for an unset slug (P3). |
| **X2.3** | Live behaviour is byte-identical (P3's live cases + NR1). |
| **X2.4** | The schema bump re-points the persisted slug and preserves the operator `env` axis (P8). |
| **X2.5** | No schema change, no `config.yaml` change, no privileged-category change (C5 / R11), no shell-file change (NR2). |
| **X2.6** | Tier-1 leg PASS within the 30-min hard budget, fail-loud, with the slicing proof recorded; net-new reds = 0 against the declared blast radius. |
| **X2.7** | Tier-2 leg executed (whole-tree dual-side) or its waiver is recorded by the leader with the compensating evidence named. |
| **X2.8** | Every pack used is **registered in `.agents/tester/PACKS.md`** (invocation + scope + last-run + status columns). |
| **X2.9** *(P2-A/P2-B)* | The hardened predicate's edges are pinned: opt-out ⇒ non-live even with own-db=prod; garbage ⇒ non-live + WARNING; empty ⇒ absent; the canonical DB-name input keeps an env-var-less prod install **live**; the grep pin + the zero-file-access test are green. |
| **X2.10** *(P2-C/P2-D/P2-E)* | One canonical slug normalization, consumed by **both** lanes; the explicit-constructor bypass is closed (no slug can enter that the resolver did not return); the anchors are re-derived at implementation time and cited by quoted phrase where they moved. |
| **X2.11** *(P2-F/P2-G)* | The **four-link** gate chain is stated in the code comments (bootstrap skip / warmup / schema warm / preload gate). *(G1 fold B2: the stale-row deactivation (P2-G) is implemented + pinned in **Phase 3** — X3.12; it is a manager.py edit and this phase touches no manager code (§2.5).)* |
| **X2.12** *(P2-H/P2-I/P2-J)* | The R17 extended clause (bearer re-derived on every heal, no new copy) is disclosed in the release notes. *(G1 fold B2: the **conditional-heal gate (P2-H)** and the **slug-equality trigger (P2-I)** are manager.py edits — implemented + pinned in **Phase 3** (X3.13); this phase ships the `plane.py` schema bump + pin P8 on an allowed boot.)* |
| **X2.13** *(P2-K/FC11)* | The classifier reconciliation is implemented exactly per §2.2.1 and the **coverage pin is green** — zero unclassified catalog verbs. The widened read subset is disclosed as the **FC7** interaction, and FC7's per-agent census is armed. |
| **X2.14** *(P2-L)* | `plane_sync_service.unavailable_reason()` carries the resolver's typed refusal reason; the pre-fold §2.5 contradiction is closed (the file is in scope for that one method; the watchdog knobs remain untouched). |
| **X2.15** *(BR-A)* | All **7** breaking test files (the 6 architect-folded + `test_plane_identifier.py`, G1 fold B7) are remediated per the pre-agreed rules and green; **any** file that needed a production change is recorded as a **finding**, not worked around. |

---

## 7. Fold register — architect G0 amendments mapped to Phase 2

**Fold date:** 2026-11-02 · **Source:** `architecture-recommendation.md` §7 (Phase 2 block) + §2 + §3 + §6 · **Mode:** append-only; no existing text renumbered or removed.

| Amendment | Substance | Where folded in this file |
|-----------|-----------|---------------------------|
| **P2-A** | predicate edge hardening (opt-out short-circuit; garbage ⇒ non-live+WARNING; empty=absent; exact `VALID_ENVS`) | §2.1 (the P2-A block) + §2.4 resolver pack + §5 pin + §6 X2.9 |
| **P2-B** | canonical DB-name input (`POSTGRES_DB` → own `ensemble.json` `pg.db`) + hard bans (no `upgrade_tools` import; no other file reads; grep pin + zero-file-access test) | §2.1 (the P2-B block) + §2.4 resolver pack + §5 pin + §6 X2.9 |
| **P2-C** | one canonical slug normalization consumed by BOTH lanes; the quote-sanitization pin re-pointed at the resolver | §2.1 (the P2-C block) + §2.3 (REST normalization row) + §2.4 (BR-A item 5) + §5 pin + §6 X2.10 |
| **P2-D** | the explicit-constructor bypass is closed (refused ⇒ inert client; explicit params never introduce an un-resolved slug) | §2.3 (the P2-D block) + §2.4 enforcement pack + §5 pin + §6 X2.10 |
| **P2-E** | anchor drift corrected (raw fallback `:353-356`; `__init__` `:326-361`) | §2.3 (the table rows + the P2-E block) + §6 X2.10 |
| **P2-F** | the gate chain is **four** links, all keyed on `is_available()` (bootstrap skip / warmup / schema warm / preload gate) | §2.2 (the P2-F block) + §6 X2.11 |
| **P2-G** | deactivate the pre-existing ACTIVE plane row on a workspace refusal (mirror `MCP_DISABLE_BUILT_IN_*`) | §2.2 (the P2-G block, incl. the AD2 non-widening boundary) + §5 pin + §6 X2.11 |
| **P2-H** | the heal is **conditional on availability**; a refusal defers it; Phase-5 Step 3 sequences allowed-boot-first | §2.2 (the P2-H block) + §2.4 heal pack + `phase5-plan.md` §5 Step 3 + §6 X2.12 |
| **P2-I** | plane-scoped **slug-equality heal trigger** (row slug ≠ resolved slug ⇒ heal) — this closes the ADJ-1×E4 divergence | §2.2 (the P2-I block) + §2.4 heal pack + §5 pin + §6 X2.12 |
| **P2-J** | R17's extended clause — the bearer is env-derived and **re-derived on every heal**; no new copy; redaction posture unchanged | §2.2 (the P2-J block) + `plan-overview.md` §6 (R17 row) + §6 X2.12 |
| **P2-K** | **classifier reconciliation** — read += `retrieve_`, `count_`; write += `manage_`/`transfer_`/`complete_`/`link_`/`import_`/`attach_`/`detach_`/`upload_`/`resolve_`; drop `add_`/`edit_`/`assign_`; coverage pin (FC11) — **new daemon-code item, now in scope** | **§2.2.1 (its own subsection — the task's explicit requirement)** + §2.2 table row + §2.4 classifier pack + §5 pin + §6 X2.13 |
| **P2-L** | `plane_sync_service.unavailable_reason` carries the resolver's refusal reason (resolves the pre-fold §2.5 self-contradiction) | §2.5 (**CORRECTED — the file is in scope**) + §2.4 sync-reason pack + §5 pin + §6 X2.14 |
| **P2-M** | injectable resolver signature `resolve_plane_workspace(env=None, *, postgres_db=None)` + resolve-once-and-thread (env TOCTOU closed) | §2.1 (the P2-M block) + §2.4 resolver pack |
| **BR-A** | the 6 breaking test files enter the Tier-1 blast radius with pre-agreed remediation (**+1 G1-added, B7: `tests/unit/test_plane_identifier.py`** — P2-D constructor collision) | §2.4 (the BR-A table) + §4 (blast radius list) + §6 X2.15 |

**Phase 2 carries 13 of the architect's §7 amendment IDs** (P2-A…P2-M) **plus BR-A** (the blast-radius item) — the largest single block, because the architect moved the **classifier reconciliation** into this lane.

**Architect-verified precision recorded by me during this fold (disclosures, not amendments):**
- `set_` and `remove_` are **kept** in the write patterns: the captured catalog contains `set_work_item_property_value`, `set_customer_property_values`, and `remove_work_item_relation` — the architect's drop-list names only `add_`/`edit_`/`assign_`, and dropping `set_`/`remove_` would mis-classify real writes. The §2.2.1 table records the measurement.
- `search_` is **already** in the read patterns (`:242-245`); the architect's read-list names it, but the **delta** is `retrieve_` + `count_`.
- The 6 BR-A test files were confirmed to **exist** (2,206 / 1,081 / 979 / 259 / 476 / 1,766 lines) before they were written into the blast radius.

> **G1 reviewer-council fold addendum (2026-11-02).** B2 (P2-G/P2-H/P2-I implementation home → Phase 3), B3 (ADJ-1 live+`e2e` pin legs + the strict string marked HISTORICAL), B7 (BR-A 7th file `tests/unit/test_plane_identifier.py:365-370`), A-a (checked-in catalog fixture), A-b (new P2-H pin row), A-c (§3 tier justification re-attributed). Central register: `decisions.md` §8. The architect's record (`architecture-recommendation.md`) and the three wave-1 artifacts are untouched by design.

---

## G2 fold (approver iter-001, 2026-11-02) — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 001 **REJECTED** (3 blockers + 9 non-blocking). **Architecture NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB3** | **FOLDED** — the P2-D **injection seam** is now named and closed for the injection path too: the direct constructor (`plane_http_client.py:326-332`) **and** the sync service's `http_client=` injection parameter (`plane_sync_service.py:334`/`:349`/`:389-390`). **Verified: no production caller injects** ⇒ the closure is a test-seam hardening with zero production behaviour change. The resolver-injection boundary is stated per the P2-M signature. | §2.3 (the G2 fold NB3 block) |
| **NB5** | **FOLDED** — the live permitted set is stated once (live ⇒ **any configured slug** permitted; non-live ⇒ `e2e` only) and the **missing pin is added**: *live + a third slug ⇒ ALLOWED* (and the non-live third-slug ⇒ refused leg). | §5 (**P2** and **P3** rows) + `decisions.md` ADJ-1 (the G2 note) |

---

## G2 fold (approver iter-002, 2026-11-02) — FINAL FOLD — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 002 **REJECTED** (4 blockers + 10/13 non-blocking). **Architecture / D-1 / ADJ-1 / phase structure NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **B4** | **FOLDED** — the ruled pattern table is **amended with `read_`** (the one captured verb the ruling missed: `read_work_item_attachment`), the **three-number census is recomputed and published** (on-disk 48/82/47 → ruled 72/104/**1** → corrected **73/104/0**), the drifted sub-counts are corrected (**`manage_` 14**, **`retrieve_` 23**), and the **R18 trap is re-verified by set arithmetic** (kept 48 → 73 = **+25** restored, **0** removed ⇒ no verb that was kept is dropped). FC11's expectation is restated so the pin is **satisfiable**. | §2.2.1 (the pattern table + the arithmetic block) + §2.2 (the `read_tool_patterns` row) + §2.4 (the pack) + §5 (the **P2-K/FC11** pin) |
| **NB2** | **FOLDED** — **P2-H is classified ONCE, here**, as a **mutation pin with a RED leg** (implementation home Phase 3; validated at Phase-3 exit X3.13). The master index + the G13 enumeration are corrected to match. | §5 (the **P2-H** pin row) |
| **NB8** | **FOLDED** — "live + unset slug ⇒ account-default" is now pinned in **BOTH** lanes, asserted **separately** because they differ: the MCP lane resolves and emits the account-default header; the REST lane's `_rest_base_url()` returns `None` ⇒ the REST client is absent. | §5 (the **P3** row) |
| **NB11** | **FOLDED** — the local dependency row now states that **Phase 1 gate (i) is a HARD dependency** of Phase 2 (it is the FC11 catalog fixture), not the pre-fold "not required" nicety. | §3 (the dependency row) |
| **NB12** | **FOLDED** — `tests/unit/test_plane_identifier.py` is **404 lines** (the pre-fold "~430" corrected, verified by me). | §2.4 (BR-A item 7) |

---

## G2 fold (approver iter-003, 2026-11-02) — FINAL FOLD — item disposition

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 003 **REJECTED** (2 blockers + 17 mechanical notes); **approver cap reached ⇒ the leader proceeds to implementation; there is NO further approver round.** **Architecture / D-1 / ADJ-1 / the phase structure / the 7-phase decomposition are NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered or repurposed.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB12** | **FOLDED** — the 177-name catalog fixture now has a **named host**: **`tests/fixtures/plane_catalog.json`** (the gate-(i) re-capture, copied verbatim into the repo by the Phase-1 gate) + the loader **`tests/helpers/plane_catalog_fixture.py`**. The coverage pack no longer depends on the planning dir at test time, and the gate produces both forms. | §2.4 (the classifier-coverage pack row) |
