# Phase 3 Plan — Live views for rich content: `view_write` tool + FLAGSHIP server-rendered live board view

Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`). Anchors verified in THIS checkout, 2026-10-19. Read-only research; implementation-ready.

**Binding user decisions (NOT re-litigated):** live views use the EXISTING passcode-gated public-link mechanism; the board page is SERVER-RENDERED FROM DB (always fresh — never a static HTML file Ari rewrites); build order 1 → 2 → 3. **Pre-ruling:** `view_write` is ARI-ONLY.

### Privilege doctrine — THREE layers, ALL required (iteration-002 fold)

Every privileged tool in this commission is protected by **three** layers, and **all three** are required. The category layer alone is NOT sufficient, and neither is the category + allow-list pair:

| Layer | Mechanism | Where |
|---|---|---|
| 1 — category | the category is in `PRIVILEGED_TOOL_CATEGORIES` ⇒ it never joins the empty-allow default universe | `_tool_registry.py:239-338`; `instance.py:460-470`, `:6243-6271` |
| 2 — allow-list | an explicit **category** entry on the agent's `tools.allow` | `agents/{ari,leader}/meta.json` |
| 3 — in-body gate | the tool body calls the shared `_is_views_authorized(agent_id, …)` helper and refuses | `daemon/tools/views_auth.py` (§3.1, §4.6) |

**Why layer 3 is load-bearing (not belt-and-braces):** a **literal tool-name** allow entry (an allow entry that is **not** a category key) is added **verbatim** and therefore **bypasses the category gate outright** — `daemon/tools/instance.py:468-478` (the category-expansion loop: `if item in tool_categories: allowed_tools.update(tool_categories[item]) else: allowed_tools.add(item)`). Layers 1 + 2 therefore do **not** enforce "ari-only"; only layer 3 does. This is the commission's standing doctrine (phase1-plan.md:19 / :270; decisions.md R-C1:54): **the category gate AND the body gate are both required — one is not a substitute for the other.** Phase 1 keeps its own `_is_board_authorized` in `daemon/tools/board_tools.py`; phase 3 owns its own helper and does **NOT** import phase 1's module (D13 — zero coupling to phase-1 files).

**Gate sets (per tool, matching the DOCUMENTED audience):**

| Tool | Category | Layer-3 gate set | Authority |
|---|---|---|---|
| `view_write` | `view-write` (privileged) | `{"ari"}` | the binding **ARI-ONLY** pre-ruling (header block above) |
| `board_link` | `board-link` (privileged) | `{"ari", "leader"}` | D13 / §2.5 — audience separation (mint ≠ read) |

**Cross-doc note (the overview was reconciled in the SAME amended commit — this phase plan does not edit it):** [plan-overview.md §7](plan-overview.md) item **13** (lines 329-334) claimed "the in-body `agent_id ∈ {"ari","leader"}` / `agent_id == "ari"` gates are proven too — a **literal tool-name** allow entry bypasses the category gate outright". **That claim was FALSE for phase 3 as specced before this fold** (no `agent_id` on the factories; category + allow-list only — the exact internal contradiction this iteration closes). **After this fold it becomes TRUE:** both phase-3 tools carry the shared in-body gate, and the assertions that prove it are **T-A8 (iii)** and **T-BL4**. This plan states the fact; **plan-overview.md §7 item 13 was reconciled in commit `76724b537`** (the iteration-002 amended commit, which carried all five planning files) and now states the **proven** gates — so there is no outstanding overview edit and the earlier "plan-overview.md is left untouched" clause is retracted.

---

## Objective

Give Ari the ability to **publish a rich HTML page as a passcode-gated public link in ONE call** (`view_write`), and give the owner a **live, always-fresh view of the work board** — server-rendered from the DB behind the same passcode gate (`/views/public/board/index.html`).

Testable goal: an ari instance calls `view_write(path, html, title)` and receives a working public URL + 6-char passcode for a page that a customer can open with the passcode; an ari/leader instance calls `board_link()` and a customer who opens the board URL and enters the passcode sees the CURRENT board rows (statuses, tags, due dates, notes timeline) rendered from the DB, escaped, zero-JS, on every GET.

## Scope

### In Scope

- The `view_write` tool (deliverable A) + the daemon-global `reports` served root + the `view-write` privileged tool category (ari-only) + the **in-body `agent_id` gate** (three layers, D3) + the `reports`-only `Content-Security-Policy: sandbox allow-scripts` header (§6.1, leader ruling B-7).
- The server-rendered live board view (deliverable B): a static route on the existing `/views` router, a zero-JS HTML template module, a read adapter over phase 1's board service, and the `board_link` minting tool.
- The `board` reserved root name; the read-side board filters; the passcode-grant reuse.

### Out of Scope

- Any change to the passcode CRYPTO, the passcode table, the grant-cookie format or the public-lane verify flow — this phase REUSES them (D4).
- Any proxy / infra change (State A `HTTPRoute`, State B public-prefix tag) — both new surfaces already ride `/views/public/**` (D18).
- A reports-INDEX page (a listing of published pages) — no consumer in this phase; it would also need a title sidecar (D9).
- Serving non-HTML artefacts, and any chart/mermaid rendering pipeline — a `view_write` page may reference CDN scripts itself, exactly like a raw `.html` file served today (D14).
- Markdown rendering of `body_md` — no markdown library exists daemon-side; body text is escaped plain text (D15).
- Any prompt-artefact edit — the `agents/ari` conventions update (the `tools_note.md` / `workflow.md` content) and its adjudicated seal re-stamp are **step 4 of THIS commission** ([plan-overview.md §8](plan-overview.md)), **before merge + stage**, not a phase-5 commission and not a post-landing item (D19).
- Any DB migration — phase 3 adds no table and no column.

---

## 0. Two deliverables

| # | Deliverable | Surface | Blast radius |
|---|---|---|---|
| A | `view_write(path, html, title, …)` | new tool, new **privileged** category `view-write` (ari-only, **three layers** — D3) | new module + registry/meta edits + 1 lifespan root injection + **1 added response header on the `reports` root only** (§6.1 sandbox CSP) |
| B | Live board view | new static route on the existing `/views` router + `board_link` tool (new privileged category `board-link`) | new route + pages module + read adapter + 1 new reserved root name (**the board page's strict CSP is unchanged**) |

**Reuse-first posture (the point of this phase):** BOTH deliverables ride the EXISTING passcode substrate (`live_view_passcodes` rows keyed `(root_name, rel_path)`, Fernet via `SYSTEM_ENCRYPTION_KEY`, 6-char code, 10-strike lockout, 24h grant cookie) and the EXISTING public-lane gate. **Zero new crypto. Zero new DB tables. Zero proxy change. Zero new passcode concept.** The only new code: one write-side tool, one static route, one HTML template module, one read adapter, one link tool.

**Phase-1 reconciliation** (phase1-plan.md is on disk — read, not depended on): phase 1 lands `daemon/services/board_service.py` (`BoardService` class), `daemon/tools/board_tools.py` (`CATEGORY_MODULES["board"]`), `manager.board_service` (read-only property), and the `"board"` category **PRIVILEGED from phase 1's first commit** — architect C1 ruling R1; the planner's original "non-privileged in phase 1, flip in phase 3" premise is SUPERSEDED (`board` is entry #9 of `PRIVILEGED_TOOL_CATEGORIES`; the five-pin pass lands in phase 1's own commit). This plan is reconciled against that surface (§4.5, D13, D16) and deliberately avoids colliding with it (§2.5, D13). The commission's privileged trajectory is **8 → 9 (P1: `board`) → 10 (P2: `self-alarm`) → 12 (P3: `view-write` + `board-link`)** — verified 8 distinct entries at base `630cefebb` (`_tool_registry.py`); phase 3 is the **10 → 12 pass**.

---

## Task checklist (build order 1 → 2 → 3, per the binding decision)

**Step 1 — storage + service seams (no user-visible surface)**

- [ ] **T1** — Add `root_path(root_name)` + `public_base()` to `LiveViewsService` (`daemon/services/live_views.py`). *Acceptance:* `root_path("reports")` returns the configured absolute dir; `root_path` on a non-filesystem/unknown root returns None; `public_base()` returns `''` when path-relative and the resolved base otherwise; unit tests green.
- [ ] **T2** — Reserve the `board` root name (`RESERVED_ROOT_NAMES` → `{"public", "board"}`). *Acceptance:* a `config.yaml` root named `board` refuses to boot with an actionable **GENERIC reserved-name** message — the existing clause "the public passcode lane occupies this prefix" (`config.py:3142-3149`) becomes INACCURATE once `board` is reserved too; amend it to name the reserved set without claiming a single occupant (A-39); `view_link("board", …)` returns the reserved-name envelope; `/views/board/index.html` 404s.
- [ ] **T3** — Inject the `reports` root in the lifespan (`daemon/api.py`, before the `LiveViewsService` construction at `:1685`). *Acceptance:* a default boot registers `reports` → `<data_dir>/reports` with `allowed_extensions=["html"]`; an operator-declared `reports` entry is NOT overridden; a missing `app.state.data_dir` leaves the root absent (fail-soft, no boot failure).

**Step 2 — deliverable A (`view_write`)**

- [ ] **T4** — Build `daemon/tools/view_write.py`: `ViewWriteInput` + `@register_tool_category("view-write")` above `@tool(args_schema=…)` + one-line docstring + `_full_doc_` + `CATEGORY_NAME`/`CATEGORY_DOC` + the `create_view_write_tools` factory (**`agent_id` in the signature, §3.1**) + the new shared `daemon/tools/views_auth.py` helper module (**layer-3 body gate — the FIRST statement in the body**). *Acceptance:* the tool writes an HTML page under `<data_dir>/reports/`, mints the passcode row, and returns the 8-line envelope; every failure path returns a typed `Error: …` string and never raises; **an unauthorized `agent_id` returns `Error: …` with NO write and NO passcode row** (T-A8 iii).
- [ ] **T5** — Register the tool: `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` + `CATEGORY_MODULES["view-write"]` + `PRIVILEGED_TOOL_CATEGORIES` (+ `board-link`, §2.5) + the construction extend in `create_instance_tools`. *Acceptance:* the tool is produced by `create_instance_tools`; the five SAME-PR exact-set pins are updated in the SAME commit and green — the **10 → 12 pass** of the privileged trajectory 8 → 9 → 10 → 12 (both categories in one commit; one inserted line per pin file).
- [ ] **T6** — Allow-list the categories: `"view-write"` + `"board-link"` on ari; `"board-link"` only on leader. *Acceptance:* ari resolves `view_write`; leader/designer/worker/tester resolve NEITHER `view_write` nor the privileged strip keeps it away from the empty-allow universe; boot emits no "unknown category" warning. **This is layer 2 of three — it is NOT sufficient on its own** (a literal tool-name allow entry bypasses the category gate, `instance.py:468-478` ⇒ the in-body gate of layer 3 is what actually enforces ari-only; D3).

**Step 3 — deliverable B (the board) → the flagship**

- [ ] **T7** — Build `daemon/services/board_view_service.py` (the read adapter over phase 1's `BoardService`) and `daemon/services/board_view_pages.py` (the zero-JS template). *Acceptance:* the adapter returns `BoardItemView` rows with hydrated notes; an unavailable phase-1 service renders the "board backend unavailable" notice, never a 500; the rendered page escapes every user-derived string and carries the strict CSP header set.
- [ ] **T8** — Add the static board GET + HEAD route on the `/views` router, declared BEFORE the public catch-all GET (`:1744`), with the board's gate mirroring `_public_resolve` minus the file resolution. *Acceptance:* `/views/public/board/index.html` hits the board route (T-B1); an unminted row returns the uniform 404 (T-B2); the existing catch-all POST performs the verify (303 + grant cookie → board renders) with no change to that handler.
- [ ] **T9** — Build `daemon/tools/board_link.py` (category `board-link`, **factory takes `agent_id`, §4.6**) + register it. *Acceptance:* `board_link()` mints `(board, "index.html")` and returns the 5-line shape; `refresh=True` bumps the generation; the row is not mintable through `view_link`; **an unauthorized `agent_id` returns `Error: …` and mints NO row** (T-BL4).
- [ ] **T10** — Write the test suites (A/B/C below) and keep the regression suite green. *Acceptance:* every T-A*/T-B*/T-BL* id passes; `tests/unit/routers/test_live_views_public_lane.py` and the other live-views suites are unchanged-green.

Each step is independently shippable and independently revertible.

## Coupling

- **Tight with Phase 1 (board)** — the read seam. This phase consumes phase 1's `daemon/services/board_service.py` through the **FROZEN** interface (phase-1 §5.2 "Interface freeze" + A-17; architect §3.4): `BoardService.list_items(status=…, project_id=…, tag=…, due_before=…, limit=…, with_notes=False) -> {"items": [...], "count": N}` and `BoardService.get_item(item_id=…) -> item` (+ `notes[]`, oldest-first), reached via `manager.board_service`; the item projection it returns is the pinned schema. **The enumeration is VERBATIM from phase-1 §5.2** (`phase1-plan.md:213-215`: `def list_items(self, *, status: str | None = None, project_id: str | None = None, tag: str | None = None, due_before: str | None = None, limit: int = 50, with_notes: bool = False) -> dict`) — **`due_before` sits between `tag` and `limit`**; the previous draft of this line omitted it and therefore mis-stated a frozen interface. The adapter reads **EXACTLY** that seam — `manager.board_service.list_items` / `manager.board_service.get_item`, a single import, **no phase-1 internals** (never `_load_notes_bulk`, never a model import). The contract is §4.5 and the coupling lives in exactly ONE file (`daemon/services/board_view_service.py`); because the interface is frozen, a phase-1 rename is now a **breaking change to a frozen interface**, not a refactor. This phase adds NO row to phase 1's `CATEGORY_MODULES["board"]` and NO file to `daemon/tools/board_tools.py` (D13) ⇒ no textual merge conflict there.
- **Loose with Phase 2 (`self_alarm`)** — phase 2 consumes only the pinned `wi-<uuid8>` id format and touches no phase-1 code; this phase likewise touches no phase-2 code. The shared surface is the **board item id**: a `self_alarm` that links a board item, and a board page that renders the linked item's notes timeline, must agree on the id + note shape. No code coupling; a semantic coupling only.
- **Sequenced AFTER — the conventions step (step 4 of this commission)** — the conventions step edits `agents/ari/tools_note.md` + `agents/ari/workflow.md` and owns the **ONE** in-commit re-stamp pass ([plan-overview.md §8](plan-overview.md)). This phase edits `agents/ari/meta.json` **only** (D19) ⇒ the conventions step runs **after** phase 3 lands and **before** merge + stage, so its re-stamp covers the post-phase-3 tree exactly once.
- **Independent of** — the proxy/infra layer (State A `HTTPRoute`, State B public-prefix tag) and the promotion ceremony: **zero proxy change** (D18), and this phase performs **no promote** (the user's 3-factor nonce ceremony owns it).

---

## 1. New-file map

| New file | Purpose | Lines (est) |
|---|---|---|
| `daemon/tools/view_write.py` | the `view_write` tool (deliverable A) + `CATEGORY_NAME`/`CATEGORY_DOC` | ~300 |
| `daemon/tools/views_auth.py` | **the ONE shared layer-3 body-gate helper** (`_is_views_authorized`) + the two gate sets (`VIEW_WRITE_AGENTS = {"ari"}`, `BOARD_LINK_AGENTS = {"ari","leader"}`) — imported by BOTH phase-3 tool modules (iteration-002, BLOCKER 1; §3.1) | ~30 |
| `daemon/tools/board_link.py` | the `board_link` tool (deliverable B, own module ⇒ no conflict with phase 1) | ~150 |
| `daemon/services/board_view_service.py` | board READ adapter (cross-phase seam) + `BoardItemView` | ~170 |
| `daemon/services/board_view_pages.py` | zero-JS board HTML templates (inline strings built by CONCATENATION / `string.Template` — NEVER `str.format` on the CSS-bearing template, A-32; no Jinja) | ~250 |
| `tests/unit/tools/test_view_write_tool.py` | deliverable-A unit tests | ~250 |
| `tests/unit/tools/test_board_link_tool.py` | board_link unit tests | ~140 |
| `tests/unit/routers/test_live_views_board.py` | deliverable-B route/gate/render tests | ~320 |

**No migration file.** Phase 3 adds no table and no column (`live_view_passcodes` already exists — `daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql:137-150`). The board tables are PHASE 1's; phase 3 only READS them.

---

## 2. Edit-site checklist (every anchor)

### 2.1 `daemon/config.py` — **NO EDIT** (one exception, A-39)

The `reports` root is auto-injected by the lifespan (D1), so no `config.yaml` / seed / `LiveViewsRootConfig` change is needed. The `type` literal stays `filesystem | project_scoped | tmp_images` (`config.py:2700`) — `reports` is a plain `filesystem` root at an absolute path. The operator-facing `config.yaml` comment block (`config.yaml:155-186`) gets a **doc-only** note: the `reports` root is daemon-managed, do not declare it. **The A-39 exception:** the reserved-name validator's message clause "the public passcode lane occupies this prefix" (`config.py:3142-3149`) is genericized (string-only change, no behavior change — see T2/R9); §2.1's "no edit" refers to root plumbing.

### 2.2 `daemon/api.py` (lifespan) — the `reports` root injection

**Anchors — CORRECTED (iteration-002 / B-3: the previous draft mis-attributed `data_dir` to `create_app`):**

| Anchor | Real location (verified in THIS checkout) | Note |
|---|---|---|
| `lifespan` | `daemon/api.py:449-450` — `@asynccontextmanager` at `:449`, `async def lifespan(app: FastAPI):` at `:450`; the function body runs to ~`:3217` | **`create_app` is at `:3915`** — i.e. **AFTER** the lifespan ⇒ the lifespan can never read a `create_app` local |
| `data_dir` | `:483-487` — `data_dir = Path(os.environ.get("ENSEMBLE_DATA_DIR") or os.environ.get("DATA_DIR") or "./data")` | **a LIFESPAN local — NOT a `create_app` local.** The previous draft wrote "`data_dir` = `create_app` `:483-488`" ⇒ wrong owner, wrong file section |
| `app.state.data_dir` | `:493` — the stash routers read | the same value as the local |
| live-views import | `:1637` — `from daemon.services.live_views import HostRecorder, LiveViewsService` | `LiveViewsRootConfig` is **not** imported here today |
| live-views wiring | `:1685-1701` — `live_views_service = LiveViewsService(config=config.live_views, …)` at `:1685`; `app.state.live_views_service = live_views_service` at `:1701` | the injection point |

**Correction of the earlier claim (B-3).** The previous draft asserted *"The lifespan CANNOT see `create_app`'s local `data_dir` — it MUST read `getattr(app.state, "data_dir", None)`."* The premise was **mis-attributed**: there is no `create_app`-local `data_dir` at `:483-488` — those lines sit **inside the lifespan itself**, so the local `data_dir` **is** directly in scope at the `:1685` injection point. **The spec nevertheless reads `getattr(app.state, "data_dir", None)`** — it is the same value (the `:493` stash), it preserves the fail-soft property (no `AttributeError` if the stash is ever removed), and it matches the contract the routers already use. `Path` is imported at module level (`daemon/api.py:60`), so the snippet below needs no new import.

Edit immediately BEFORE `live_views_service = LiveViewsService(config=config.live_views, …)` (`:1685`); `LiveViewsRootConfig` must be imported there (today only `HostRecorder, LiveViewsService` are imported at `:1637`):

```python
# PHASE 3: the ``reports`` root is DAEMON-INJECTED, not operator-declared (D1).
if "reports" not in config.live_views.roots:
    _data_dir = getattr(app.state, "data_dir", None)   # api.py:493
    if _data_dir is not None:
        config.live_views.roots["reports"] = LiveViewsRootConfig(
            type="filesystem",
            path=str((Path(_data_dir) / "reports").resolve()),   # A-30/G1: RESOLVE — load-bearing
            allowed_extensions=["html"],
            description="Agent-written report pages (view_write). Daemon-injected.",
        )
```

**Fail-soft:** if `app.state.data_dir` is missing, do NOT inject ⇒ `view_write` returns `Error: reports storage is not configured (no daemon data_dir)`. Never boot-fail over a root.

**🔴 MUST-FIX (A-30 / architect §5.6 G1 — blocking defect): the `.resolve()` above is LOAD-BEARING.** `data_dir`'s default chain is `ENSEMBLE_DATA_DIR > DATA_DIR > "./data"` (`api.py:483-488`) — the **default is RELATIVE**. Injecting `str(Path(_data_dir) / "reports")` unresolved ⇒ `"data/reports"` ⇒ the filesystem resolver **refuses a non-absolute path** (`services/live_views.py:952-958` raises `RootNotFoundError`) ⇒ **EVERY published page 404s**, `root_path("reports")` returns `None`, and the tool reports the MISLEADING *"reports storage is not configured"* while the root is in fact configured. Guard: the relative-`ENSEMBLE_DATA_DIR` test (T-A10).

### 2.3 `daemon/services/live_views.py` — 3 edits

1. **Reserved names:** `RESERVED_ROOT_NAMES = frozenset({"public"})` (`:141`) → `frozenset({"public", "board"})`; the docstring above it (`:126-140`) gains a "board" sentence (and the boot-refusal message becomes generic — T2/A-39).
2. **New public accessor `root_path(root_name) -> Path | None`** — the ABSOLUTE directory of a registered `filesystem` root, else None (used by `view_write` to find the reports dir). **Returns `None` when the subsystem is DISABLED** — mirror `resolve_for_instance` (`:897-899`); a disabled `root_path` handing out a real directory would let `view_write` publish pages that can never be served (A-38).
3. **New public accessor `public_base() -> str`** — the resolved public base prefix (`''` when path-relative), i.e. exactly what `build_public_url` uses internally (`:1286-1289`) (used by `board_link`, because `board` is reserved and `build_public_url` refuses it).

```python
def root_path(self, root_name: str) -> Path | None:
    """Return the ABSOLUTE directory of a registered filesystem root, else None."""
    if not self._config.enabled:          # A-38: mirror resolve_for_instance (:897-899)
        return None
    entry = self._config.roots.get(root_name)
    if entry is None or not entry.enabled or entry.type != "filesystem":
        return None
    if not entry.path or not Path(entry.path).is_absolute():
        return None
    return Path(entry.path)

def public_base(self) -> str:
    """Return the resolved public base URL prefix ('' when path-relative)."""
    return self._base_url_resolver.resolve() or ""
```

No other edits — the resolver, allowlist gate, containment check, 32 MiB cap and MIME map work unchanged for a `filesystem` root.

### 2.4 `daemon/routers/live_views.py` — the board route (MOUNT ORDER IS LOAD-BEARING)

Anchors: public catch-all GET `:1744`, HEAD `:1750`, POST `:1756`; direct-lane catch-all HEAD `:1762`, GET `:1886`; public asset aliases `:1218` / `:1233`.

**EDIT:** add the board GET + HEAD **IMMEDIATELY BEFORE the `@router.get("/public/{root_name}/{rel_path:path}")` decorator at `:1744`** (i.e. after the `_public_post` helper ends). A loud comment MUST restate the first-match-wins rule (same wording as the block comment at `:1190-1200`):

- `@router.get("/public/board/index.html")` → `get_public_board`
- `@router.head("/public/board/index.html")` → `head_public_board` (FastAPI does **NOT** auto-add HEAD — verified in the installed `fastapi/routing.py`: `self.methods = {method.upper() for method in methods}`, no HEAD injection; the existing explicit HEAD routes at `:1111` / `:1750` prove it).

**NO EDIT to the catch-all POST at `:1756`** — the board's passcode verify rides it unchanged (D12). A regression pin is mandatory (T-B7).

**SECOND EDIT — the raw-lane sandbox CSP on the `reports` root (§6.1, leader ruling B-7).** Add the module-level helper `_raw_lane_csp_headers(root_name)` next to `_HARDENING_HEADERS` (`:122-125`), then spread it into the header dict at the **two raw-file serve sites** and thread the root name into the shared helper:

| Site | Change |
|---|---|
| `_serve_resolved_target` (`:956`) — the **public** lane's serve path (called from `_public_get` → `:1426`) | add a keyword-only `root_name: str = ""` parameter; spread `**_raw_lane_csp_headers(root_name)` into the raw-serve header dict at `:1034-1041`; pass `root_name=root_name` at the single call site `:1426` |
| `get_view` (the **direct** lane GET at `:1886`) — inline raw serve at `:1980-1987` | spread `**_raw_lane_csp_headers(root_name)` into the raw-serve header dict (`root_name` is already the route parameter) |

**Nothing else in the router changes** — no route is added, removed or reordered, no gate moves, the markdown / mermaid / tmp-images lanes are untouched, and the board page's strict CSP is unchanged. Every root other than `reports` gets a **byte-identical** response (the helper returns `{}` for them). Pin: **T-B17**.

### 2.5 `daemon/tools/_tool_registry.py`

- `DYNAMIC_TOOL_NAMES` (`:23-179`): add `"view_write"` and `"board_link"`.
- `KNOWN_TOOL_NAMES` (`:1150` area, next to `"view_link"`): add both.
- `PRIVILEGED_TOOL_CATEGORIES` (`:239-338`): add `"view-write"` **and** `"board-link"`. **SAME-PR FIVE-PIN RULE applies** (`:215-238`) — all five exact-set pins MUST be updated in the SAME commit (both categories land together ⇒ one pin update): ① `tests/unit/tools/test_upgrade_registration.py` ② `tests/unit/tools/test_attestation_registration.py` ③ `tests/integration/test_maintenancer_spawn_resolves_tools.py` ④ `tests/integration/test_service_tool_flag_off_byte_identical.py::test_privileged_category_set_is_pinned` ⑤ `tests/unit/tools/test_secrets_registration.py:218`.
- `CATEGORY_MODULES` (`:736-764`): add `"view-write": "daemon.tools.view_write"` and `"board-link": "daemon.tools.board_link"`. **Zero collision with phase 1** — phase 1 owns `"board": "daemon.tools.board_tools"`; this phase adds no row to that category and no file to that module (D13). Phase 1 DOES touch `PRIVILEGED_TOOL_CATEGORIES` (its `"board"` category is PRIVILEGED from its first commit — entry #9, C1 ruling) and phase 2 adds `"self-alarm"` (#10); this phase adds `view-write` + `board-link` (#11–12) ⇒ trajectory **8 → 9 → 10 → 12**, phase 3 being the **10 → 12 pass**. Each phase-commit updates the same five pin files and is self-consistent ⇒ no red window; verify at merge that #9 and #10 landed.
- `KNOWN_TOOL_NAMES` carries a bidirectional drift pin (frozen-binary name discovery) — the pin test compares against the pure source-`@tool` universe.

### 2.6 `daemon/tools/instance.py` — construction (the 3rd registration step)

Imports next to `from .live_views import create_live_view_tools` (`:241`): `from .view_write import create_view_write_tools`, `from .board_link import create_board_link_tools`. Extend after the live-view extend (`:5859-5872`):

```python
    # ── view_write (Phase 3, ari-tools) — report-page WRITER. Privileged category
    # ``view-write`` (ARI-only): writes an HTML page under the daemon-global
    # ``reports`` root, returns the passcode-gated public URL in ONE call.
    # Registration is the THREE-step seam (decorator + DYNAMIC_TOOL_NAMES + this
    # construction) — decorator-only is SILENTLY INVISIBLE.
    # ``agent_id`` is the layer-3 in-body gate's input (BLOCKER-1 fold): it is a
    # parameter of THIS function already (``create_instance_tools(..., agent_id="",
    # ...)`` — daemon/tools/instance.py:2450), so it is in scope here.
    tools.extend(create_view_write_tools(
        manager,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
    ))
    # ── board_link (Phase 3, ari-tools) — mints the passcode-gated public URL for
    # the LIVE BOARD. Privileged category ``board-link`` (ari + leader). Separate
    # module + separate category ⇒ no coupling to the phase-1 ``board`` category.
    tools.extend(create_board_link_tools(
        manager,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
    ))
```

**Both factories take `agent_id` by keyword** (the amended signatures: §3.1 for `create_view_write_tools`, §4.6 for `create_board_link_tools`). A construction that omits `agent_id` would silently fall back to `""` ⇒ the body gate denies every call (fail-closed, but a dead tool) ⇒ **the T-A9 / T-BL-registration pins assert the tool is produced by `create_instance_tools` with a NON-EMPTY agent id**, and the body-gate pins (T-A8 (iii) / T-BL4) cover the deny path.

### 2.7 `agents/ari/meta.json` / `agents/leader/meta.json`

- `agents/ari/meta.json` `tools.allow`: add `"view-write"` and `"board-link"`.
- `agents/leader/meta.json` `tools.allow`: add `"board-link"` ONLY (leader MUST NOT get `view_write` — D3).
- **These entries are layer 2 of three** (privileged category + allow-list + in-body `agent_id` gate — D3, the doctrine block at the top). The allow-list is a **visibility** boundary; the **in-body gate** is the enforcement boundary.
- ari's `tools.deny` keeps `write_file`/`edit_file` — `view_write` is the sanctioned way for ari to publish a page (ari has no general file-write tool; that is WHY this tool exists).
- **NO seal re-pin in this phase — but the seal is NOT absent, it is simply untouched here.** Two DIFFERENT baselines cover the prompt artefacts: the **committed 161-entry manifest** `agents/.prompt-artifacts.sha256` (which covers ari `rule.md`/`soul.md`/`tools_note.md`/`workflow.md` and the leader equivalents) and the **35-entry `SEALED_SHA_BASELINES` test tripwire** (`tests/test_chart_image_delivery_e2e.py:1350-1398`, covering `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` among them). This phase edits `agents/{ari,leader}/meta.json` — `grep -c 'meta.json'` = **0** in the manifest, and neither ari nor leader `meta.json` is in the tripwire ⇒ **this phase's edits carry NO re-stamp obligation**, and **phase 3 touches zero sealed artefacts**. Every prompt-artefact edit in the commission is **deferred to the conventions step** ([plan-overview.md §8](plan-overview.md), step 4), which owns the ONE in-commit re-stamp pass. Phase 1 also edits `agents/ari/meta.json` ⇒ both append at the END of the array (phase-1 D15 discipline) to avoid a textual collision.

---

## 3. Deliverable A — `view_write` contract

### 3.1 Signature & input model

```python
class ViewWriteInput(BaseModel):
    path: Annotated[str, Field(description="Relative path under the reports root, e.g. '2026-10-19/board.html'. '.html' is appended when the path has no extension.")]
    html: Annotated[str, Field(description="Full HTML document (UTF-8). Served byte-for-byte; never mutated.")]
    title: Annotated[str, Field(description="Human label (1-200 chars). Echoed in the result; NOT injected into the HTML.")]
    overwrite: Annotated[bool, Field(default=False, description="Replace an existing page; without it an existing path is an error.")]
    refresh: Annotated[bool, Field(default=False, description="Bump the passcode generation (new code, revokes grants). The lockout-recovery path.")]

@register_tool_category("view-write")
@tool(args_schema=ViewWriteInput)
async def view_write(path, html, title, overwrite=False, refresh=False) -> str: ...
```

**Factory — AMENDED (iteration-002, BLOCKER 1): `agent_id` is ADDED and is load-bearing.**

```python
def create_view_write_tools(
    manager: "InstanceManager",
    *,
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    ...
```

`agent_id` is **NEW** — it is the layer-3 in-body gate's input (the doctrine block above). It is the **calling agent's** id, threaded from `create_instance_tools(manager, current_instance_id, agent_id="", version_tag=None)` (`daemon/tools/instance.py:2450` — `agent_id` is **already** a parameter of that function, so the extension site in §2.6 has it in scope). The wiring is identical to the `blueprint` writer-gate precedent: `create_blueprint_tools(manager, current_instance_id, agent_id)` (`daemon/tools/blueprint.py:45`) → `_is_writer_authorized(agent_id)` (`:71-73`). The factory is **keyword-only** after `manager` so the new parameter can never be passed positionally in the wrong slot. `agent_id` defaults to `""` ⇒ a blank / unset agent id **FAILS the gate** (fail-closed — `""` never means "ari").

The factory is a closure factory (pattern: `daemon/tools/scheduling.py:62-91`). `@register_tool_category` sits **ABOVE** `@tool` (`_tool_registry.py:342-367`); one-line docstring + a `_full_doc_` attribute (`scheduling.py:267-294`); module-level `CATEGORY_NAME` + `CATEGORY_DOC`.

**The body gate (layer 3) — ONE shared helper, used by BOTH phase-3 tools.** New module `daemon/tools/views_auth.py` (≈30 lines) owns the helper + the two gate sets:

```python
# daemon/tools/views_auth.py
VIEW_WRITE_AGENTS: frozenset[str] = frozenset({"ari"})            # ARI-ONLY pre-ruling
BOARD_LINK_AGENTS: frozenset[str] = frozenset({"ari", "leader"})  # D13 / §2.5

def _is_views_authorized(agent_id: str, allowed_agents: frozenset[str]) -> bool:
    """Layer-3 in-body privilege gate — the SECOND of two required layers.

    The privileged CATEGORY does not close the literal-tool-name allow
    bypass (daemon/tools/instance.py:468-478), so the body must gate too.
    Fail-closed: a blank / non-str agent_id is NOT authorized.
    """
    return isinstance(agent_id, str) and agent_id in allowed_agents
```

`daemon/tools/view_write.py` AND `daemon/tools/board_link.py` both import it (`from .views_auth import _is_views_authorized, VIEW_WRITE_AGENTS, BOARD_LINK_AGENTS`) ⇒ **one** helper, two call sites — the phase-1 "one shared helper, not four copies" discipline (phase1-plan.md:270). The helper lives in its **own** module (not inside `view_write.py`) so neither tool module owns the other's policy, and phase 3 imports **no** phase-1 file (D13).

**The gate call — FIRST, before ANY side effect.** The gate is the **first statement** in the tool body — BEFORE path validation, BEFORE the write, BEFORE the passcode mint:

```python
if not _is_views_authorized(agent_id, VIEW_WRITE_AGENTS):
    return (
        "Error: view_write is restricted to the 'ari' agent — "
        f"this call came from agent {agent_id!r}."
    )
```

**Envelope convention:** phase 3 uses **`Error: …`** (the `view_link` convention, `daemon/tools/live_views.py:238-283`; same tool family ⇒ `Error:` not `ERROR:` — §3.4) — a **deliberate divergence from phase 1's `ERROR: …`** body-gate envelope (phase1-plan.md:270). Do **not** copy phase 1's string. The tool never raises.

### 3.2 Behaviour (ordered)

1. `manager.live_views_service` None ⇒ `Error: live-views service not initialized …`; disabled ⇒ `Error: live-views subsystem is disabled`; `service.root_path("reports")` None ⇒ `Error: reports storage is not configured …`.
2. `path` non-empty; `html` non-empty; `title` 1–200 chars.
3. **Path normalisation:** strip a leading `/`; reject `..` segments, backslashes, control chars (C0+DEL+C1), null bytes — **reuse `is_well_formed_rel_path`** (`daemon/services/live_views.py:251-296+`) so the write path and the serve path share ONE validator. No extension ⇒ append `.html`; extension ≠ `html` ⇒ `Error: only .html pages can be written to the reports root`.
4. **Containment:** resolve `(root / rel).resolve()` and assert `relative_to(root.resolve())` — the BASE compared against must be the **RESOLVED** reports dir (`Path(entry.path).resolve()`), never the unresolved `root_path()` return (mirror `_resolve_under_root:1157-1170`); closes the symlink-escape class at WRITE time on both sides.
5. **Size:** `len(html.encode("utf-8")) > MAX_SERVED_BYTES` (32 MiB, `:166`) ⇒ `Error: page is {n} bytes — the served-page cap is 32 MiB (33554432 bytes)`. A page that cannot be served must never be written.
6. **Existing file:** target exists and `overwrite=False` ⇒ `Error: {rel} already exists — pass overwrite=True to replace it`.
7. **Write (atomic AND durable — A-31):** `mkdir(parents=True, exist_ok=True, mode=0o700)` — **dirs `0o700`** (the default `0o755` makes the reports tree world-readable on a multi-user host); temp file in the **SAME directory** (`tempfile.mkstemp(dir=parent)` — a `/tmp` temp ⇒ `EXDEV` on replace); write, then **`flush()` + `os.fsync(fd)` BEFORE `os.replace(tmp, target)`** — `os.replace` is atomic w.r.t. the NAMESPACE, not durability; without fsync a crash can leave a zero-length page permanently servable; **unlink the temp file in a `finally`** on any failure; file mode `0o600` (tmp_image_store precedent, `daemon/services/tmp_image_store.py:395-406`). No torn reads, no truncated published page.
8. **Passcode:** `passcode_service.refresh(root, rel, actor)` when `refresh=True`, else `passcode_service.mint(root, rel, actor)` (idempotent). **Reuse, never re-implement crypto** — the exact seam `view_link` uses (`daemon/tools/live_views.py:295-335`), including the `ViewPasscodeRotationRequired` / `ViewPasscodeNotFoundError` typed envelopes (`:336-369`). **🔴 MUST-FIX (B-4 / architect §3.3): the mint/refresh call MUST be wrapped in `to_thread`.** `ViewPasscodeService.mint` / `.refresh` are **synchronous** (DB-backed) ⇒ in the **async** `view_write` body the call is `result = await asyncio.to_thread(passcode_service.mint, "reports", rel, actor)` — **exactly ONE thread crossing** around the single sync call, never one per field (the A-37 discipline: one `to_thread` around the work, not one per service call). Architect §3.3: "`mint` / `refresh` … **synchronous** ⇒ `await asyncio.to_thread(...)` mandatory in an async tool (`daemon/tools/scheduling.py:216-218`)". **The in-tree difference that makes this easy to get wrong:** `view_link` is a **sync** tool (`def view_link(` — `tools/live_views.py:113`) and therefore calls `mint` directly, which is correct *there*; `view_write` / `board_link` are **async** (`async def …`) ⇒ the wrap is mandatory. Without it the sync DB round-trip blocks the event loop for the whole agent turn.
9. **URLs:** `service.build_url("reports", rel)` + `service.build_public_url("reports", rel)` (`:1201`, `:1243-1289`); fallback `f"{service.public_base()}{PUBLIC_NAMESPACE}/reports/{rel}"` if either returns None.

### 3.3 Return shape (mirrors `view_link`'s 5 labeled lines, `tools/live_views.py:382-389`)

```
Public URL: /views/public/reports/2026-10-19/board.html
Passcode: 7KQ2ZM
Authenticated URL: /views/reports/2026-10-19/board.html
Generation: 1
Locked: no
Title: Q3 status board
Bytes: 18422
Overwrote: no
```

The first five lines are byte-compatible with `view_link`'s shape (same consumer contract, grep-able). `Title` / `Bytes` / `Overwrote` are ADDITIONS after the five. A `Hint:` line when `Locked: yes` (copy `view_link`'s refresh hint, `:376-381`); a `Note:` line when the HTML has no `<title` (advisory — the tool NEVER mutates the caller's bytes, D9).

### 3.4 Error envelopes (tools never raise — house rule, `scheduling.py:57-59`)

Every failure returns a plain string starting with `Error: ` (the `view_link` convention at `tools/live_views.py:238-283`; same tool family ⇒ `Error:` not `ERROR:`). Envelopes: service-not-wired · subsystem-disabled · reports-not-configured · bad-path · bad-extension · too-large · exists-without-overwrite · write-IO-failure · passcode-service-missing · passcode-rejected.

---

## 4. Deliverable B — the FLAGSHIP live board view

### 4.1 URL shape & passcode keying

- Public URL: **`/views/public/board/index.html`** (fixed rel `index.html`).
- Passcode key: **ONE row — `(root_name="board", rel_path="index.html")`**. Never per-filter rows (D11).
- The board is **NOT** a registered live-views root: `board` is a **RESERVED ROOT NAME** (`RESERVED_ROOT_NAMES` → `{"public", "board"}`, `services/live_views.py:141`): an operator cannot declare a root named `board` (boot refusal, existing validator `config.py:3098-3142`) ⇒ the board can never be shadowed; the direct lane `/views/board/index.html` → uniform 404 (defensive mirror, `routers/live_views.py:1163-1166`); `view_link("board", …)` → "reserved" error ⇒ the board link is NOT mintable by the `view-views` holders (designer). Only the board's owners mint it (D13).
- **Migration note:** an operator with a root literally named `board` will now fail to boot ⇒ rename it. Document in the phase-3 changelog + the live-view runbook.

### 4.2 The board GET handler (new, static route)

Declared before the public catch-all (mount-order anchor §2.4). The gate = `_public_resolve` (`routers/live_views.py:1271-1392`) MINUS the file resolution, PLUS the board render. **ONE ordered list, ONE render site (iteration-002 / B-6 — the previous draft stated "render the board" twice, once inside the grant step and once as a trailing step, which made the step order contradictory):**

1. `service = _resolve_service(request)`; None or disabled ⇒ `_uniform_404()`.
2. `passcode_service = _resolve_passcode_service(request)`; None ⇒ `_uniform_404()`.
3. `row = passcode_service.get_row("board", "index.html")`; None ⇒ `_uniform_404()` (**GET never mints** — the ADR T7 write-amplification pin).
4. Grant cookie: `parse_grant_cookie(cipher=…, token=request.cookies.get("view_grant"), expected_root="board", expected_rel="index.html", expected_generation=_row_generation(row))` — **valid ⇒ go to step 6**; **absent / invalid / stale generation ⇒ fall through to step 5** (log-only, no audit row — the same posture as `:1358-1372`).
5. **NOT granted** — locked? ⇒ `_render_locked_page(request)` (HTTP 200 locked notice; the only recovery is `board_link(refresh=True)`). Otherwise ⇒ `_render_interstitial_page(request, "board", "index.html")` — the EXISTING interstitial reused verbatim (same `code` field, same-URL form action, same CSP); its copy says "view this document" for a board — accepted cosmetic deviation (OQ-3). **Both branches RETURN here — the board body is never produced on this path.**
6. **GRANTED** — render the board from the DB. **This is the ONE and ONLY render site**, and each branch above returns exactly once. (The previous draft duplicated the render across steps 4 and 6; the list is now single-pass, so "the board renders" is stated once and its position in the order is unambiguous.)

**Headers (A-33 — MUST-FIX class): the board response builds headers from `page_headers(csp_nonce)` (view_passcode_pages.py:68-89) ALONE.** Never merge `_HARDENING_HEADERS` (`nosniff` + `private, max-age=60`, `routers/live_views.py:122-125`) with the page headers — the merge-order trap lets `private, max-age=60` win over `no-store` (the tree already contains this exact shape at `:1857`). Pin `cache-control` EXACTLY `no-store` (T-B3).

**HEAD (A-40): the board HEAD route must exist as its OWN route (§2.4).** FastAPI adds no implicit HEAD; a missing board HEAD falls through to the public HEAD catch-all (`:1750`) and returns a **JSON body on a HEAD request**. `head_public_board` shares the GET handler's gate and returns headers only (pin: T-B15).

The board POST (passcode verify) is **the existing catch-all POST at `:1756`** — untouched. It looks the row up by `(root, rel)`, applies the per-IP bucket, the lock short-circuit, the same-host CSRF check, the constant-time compare, the audit row, the grant-cookie issuance and the 303 to the same URL. The 303 `Location` = `/views/public/board/index.html` (+ query) → lands on the static board GET → the grant cookie (Path = the exact queryless path, `_grant_cookie_path` `:791`) is presented → the board renders. **Zero new gate code.** *The catch-all POST does not consult `is_reserved_root_name`; that is precisely what lets the reserved `board` name work — a load-bearing accident ⇒ **regression pin T-B7**.*

### 4.3 Rendering

- New module `daemon/services/board_view_pages.py` with `render_board(*, csp_nonce, items, filter_options, active_filters, counts, generated_at) -> str`. **🔴 MUST-FIX (A-32 / architect §5.6 G2 — blocking defect): NO `str.format` on this template.** The board page carries a `<style>` block and CSS contains literal `{`/`}` — `str.format` raises on the first brace ⇒ **500 on every board GET**. Build by **plain string concatenation** (the `_shell` precedent, `daemon/services/view_passcode_pages.py:117-141`) or `string.Template`. NO Jinja — the tree has no Jinja.
- **Code split (§5.10):** `board_view_pages.py` consumes ONLY the phase-3 `BoardItemView` dataclass — it must NEVER import `board_service`.
- **Exactly ONE module-level escaper (A-32):** `_esc(v) = html.escape(str(v), quote=True)` (with `None → ""`) at EVERY interpolation site — item title, `body_md` (inside `<pre>`; `_esc` also neutralises `</pre>`), note text, note author, tags, project ids, status/priority/counts (coerce ints first), timestamps, `generated_at`. **URL-context values are `quote()`d FIRST, then `_esc()`d** — HTML-escaping alone yields a DIFFERENT value than the user typed (`&lt;` ≠ `<` ⇒ a filter echo never re-matches) and an unescaped `&` splits the query; the active-filter echo (`<input value>` / `<option selected>`) is the load-bearing case. Item ids are `_esc`d AND validated against `^wi-[0-9a-f]{8}$` (non-conforming dropped). The skeleton (markup + CSS) is ours and is never interpolated.
- Reuses `page_headers(csp_nonce)` (`view_passcode_pages.py:68-89`) — ALONE, never merged with `_HARDENING_HEADERS` (A-33, §4.2): `Content-Security-Policy: default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` + `nosniff` + `Cache-Control: no-store` + `Referrer-Policy: no-referrer`. `no-store` ⇒ **always fresh**, satisfying the binding "server-rendered from DB, never a static file".
- **🔴 MUST-FIX (B-9 — architect §5.5 intent): the inline `<style>` block MUST carry the per-request CSP nonce.** `page_headers(csp_nonce)` puts `style-src 'nonce-…'` in the header, so the board's inline `<style>` renders **only** if it carries the **same** nonce value in its `nonce` attribute. Binding rules: (a) the nonce is minted **per request** (`new_csp_nonce()` — `daemon/services/live_views.py:1672-1680`, `secrets.token_urlsafe(16)`) — never a module constant, never reused across requests; (b) the **same string** goes into the header (`page_headers(csp_nonce=…)`) and into `<style nonce="…">` — the two MUST NOT drift (a mismatch = a silently unstyled board); (c) the nonce is an attribute **value** ⇒ it is interpolated through the ONE `_esc()` helper (A-32), never raw-concatenated; (d) because the template is built by **concatenation / `string.Template`** (A-32 — never `str.format`, the CSS braces would explode it), the nonce is a **separate placeholder**, never spliced into the CSS block; (e) the page carries **NO** inline `<script>` (`script-src 'none'`) ⇒ no script nonce is needed. **Why it is a MUST-FIX and not a nicety:** if the pairing is missed, the browser refuses the `<style>` block ⇒ the board renders unstyled — a **silent** visual regression that the existing header assertions (T-B9) would not catch. Hence the dedicated pin **T-B16**: the header's `style-src 'nonce-X'` and the body's `<style nonce="X">` carry the **same** X, and two consecutive GETs carry **different** nonces. Architect §5.5 (verbatim): "The nonce is per-request (`:60-63`, `routers/live_views.py:490-501`) and injected into **both** header and `<style nonce>` → fully compatible with server-render-per-GET" (`architecture-recommendation.md:313`).
- Layout: title + generated-at + a zero-JS **GET filter form** (status `<select>`, project `<select>`, tag `<input>`) + a status-count row + one table (Status badge, Priority, Title, Tags, Project, Due, Updated, Notes count). Each row's body + notes timeline live in a `<details><summary>` block — **pure HTML, no JS**.
- **Truncation is visible (A-41):** a **"showing N of M"** disclosure line renders whenever the filtered `count` exceeds the render `limit` (50), plus a notice when the filter-option set caps at 500 (`list_items` has no pagination cursor in v1; >50 rows / >500 values must never truncate silently — R47).
- **Render ceiling is bounded in BYTES, not just rows (A-34):** `MAX_SERVED_BYTES` bounds disk-served files only — a server-rendered body is unbounded while `body_md` and `board_notes.text` are unconstrained TEXT. Assembly enforces a **2 MiB rendered-body ceiling** plus per-field caps (**notes per item ≤ 20 · note text ≤ 4 KiB · `body_md` ≤ 64 KiB**), enforced **DURING assembly** (a post-hoc slice cuts mid-tag), truncating **with a visible notice** in the page — never a 500, never a silent cut.
- `body_md` is **escaped plain text inside `<pre>`** — NO markdown renderer exists daemon-side (`pyproject.toml` has no markdown/mistune/marko dependency; the `.md` wrapper loads `marked` from a CDN *client-side*, `routers/live_views.py:153-227`). Server-side rendering of agent-authored markdown without a sanitiser would be an XSS hole; a `<pre>` is honest and safe.
- **Every** user-derived string passes through the ONE `_esc()` helper above — titles, tags, project ids, note text, author instance ids, dates, filter echoes (`quote()` first in URL contexts). The skeleton is ours.

### 4.4 Filters

Query params (zero-JS form, `method="get"`, action = the board URL): `?status=todo|in_progress|blocked|done` · `?project=<project_id>` · `?tag=<tag>`.

- **Filters are VIEW concerns, NOT access control** (D11). A passcode holder sees the WHOLE board; a filter narrows the display, never the disclosure. Stated in the page footer and here so nobody mistakes a filter for an ACL.
- The filters are pushed **DOWN** to phase 1's service (it natively supports `status` / `project_id` / `tag` — phase1-plan §5.2 `list_items`). ONE additional unfiltered `list_items(due_before=None, limit=…, with_notes=False)` call derives the project/tag `<select>` options from the same bounded set (two bounded calls, no new seam surface; the option set needs no notes, so that call leaves the frozen `with_notes` flag at its default, and the frozen `due_before` at `None`).

### 4.5 The board read seam (cross-phase contract, reconciled with phase 1)

`daemon/services/board_view_service.py` (phase 3 owns) exposes `BOARD_VIEW_ROOT = "board"`, `BOARD_VIEW_REL = "index.html"`, a `BoardItemView` dataclass (`id, title, body_md, status, tags[], project_id, due_at, linked_job_ids[], created_by_instance, created_at, updated_at, priority, notes[]`), and two functions: `fetch_board_items(*, status=None, project_id=None, tag=None, limit=50) -> list[BoardItemView]` and `fetch_filter_options(*, limit=500) -> dict` (`{"projects": [...], "tags": [...]}`). `fetch_board_items` maps onto the **frozen** phase-1 call `list_items(status=…, project_id=…, tag=…, due_before=None, limit=…, with_notes=True)` — **`due_before` is named explicitly at the seam** (the frozen signature carries it, phase-1 §5.2); the board's own filter set does not expose a due-date filter in v1 (§4.4) ⇒ the adapter always passes `None`. `fetch_filter_options` maps onto `list_items(limit=…, with_notes=False)` (the frozen `due_before` likewise stays at `None` — the option set is unfiltered).

- The seam is phase 1's `BoardService` (phase1-plan §5.2/§5.3), reached via `request.app.state.manager.board_service` (`app.state.manager` is set in the lifespan at `daemon/api.py:2271`; available at REQUEST time).
- **Call shape — the FROZEN phase-1 interface (phase-1 §5.2 "Interface freeze" + A-17; architect §2.4 / §3.4):** the adapter calls `board_service.list_items(status=…, project_id=…, tag=…, due_before=None, limit=…, with_notes=True)` → `{"items": [...], "count": N}`, with `notes[]` **hydrated in batch** by phase 1's internal batched loaders (`WHERE item_id IN (…)`, one query per collection — ≤ 3 queries per render, versus 1+50 = 51); `board_service.get_item(item_id=…)` returns the single item (+ `notes[]`, oldest-first) and is the **per-row fallback** only. ⇒ **Notes come from `with_notes=True` in ONE call — the N+1 is gone** (OQ-1 **RESOLVED** by adjudication: architect §2.4 / decisions §C5 theme 12). **`due_before` is passed EXPLICITLY as `None`** — the board exposes no due-date filter in v1 (§4.4), but the frozen keyword is still named at the call site so a phase-1 rename/keyword-drop is caught by the seam, not by a runtime surprise. The adapter is **shape-tolerant** and works against BOTH shapes: if the list comes back without `notes[]` (a `with_notes=False` list), the adapter falls back to a per-row `get_item` (bounded at the render limit — default 50 rows); a `get_item` failure degrades that row to "notes unavailable", never a 500. The adapter touches **only** `manager.board_service.list_items` / `.get_item` — never phase-1 internals (no `_load_notes_bulk`, no model import).
- Phase 1's service is **synchronous** (sync `Engine`) ⇒ the async route wraps the **WHOLE render — adapter fetch + assembly — in ONE `await asyncio.to_thread(...)`** (house pattern, `daemon/tools/scheduling.py:206-216`), **not one per service call** (A-37): each crossing is a thread hop and re-splits the snapshot the page renders from. SQLite is safe under `to_thread` because the engine sets `check_same_thread: False` (`daemon/repositories/factory.py:164`) — **pin that flag** in a test; without it the board 500s on dev. Two concurrent GETs share no mutable state; a concurrent `board_link(refresh=True)` revokes outstanding grants by design (readers bounce to the interstitial) — state it in the tool doc. The adapter is shape-tolerant (`getattr(obj, "x", None)` OR `obj["x"]`) — the in-tree precedent is `_row_locked` / `_row_generation` (`routers/live_views.py:897-920`). The per-field caps (A-34) are enforced inside this same `to_thread` during assembly.
- **This adapter is the ONLY place that knows phase 1's surface** — and it knows **EXACTLY the frozen seam** (R4): `manager.board_service.list_items` / `manager.board_service.get_item`, and nothing else (no phase-1 private loaders, no model import, no `board_notes` query of its own). If the service is unavailable at request time, the board renders an explicit "board backend unavailable" notice (HTTP 200, zero-JS) — **never a 500**.

### 4.6 `board_link` tool (the board's only minting path)

- New module `daemon/tools/board_link.py`, category `"board-link"` (ari + leader). Signature: `async def board_link(refresh: bool = False) -> str` — **async is mandatory** (the mint/refresh seam is a synchronous DB call; see the `to_thread` rule below).
- **Factory — AMENDED (iteration-002, BLOCKER 1): `agent_id` is ADDED and is load-bearing.**

  ```python
  def create_board_link_tools(
      manager: "InstanceManager",
      *,
      current_instance_id: str,
      agent_id: str = "",
  ) -> list:
      ...
  ```

  Same shape and same rationale as `create_view_write_tools` (§3.1): keyword-only after `manager`, `agent_id` defaults to `""` ⇒ fail-closed, threaded from `create_instance_tools`'s own `agent_id` parameter (`daemon/tools/instance.py:2450`), wired at the §2.6 construction site.
- **Body gate (layer 3) — the SAME shared helper as `view_write`, with the board's gate set.** The gate is the **first statement** in the body (before the mint):

  ```python
  if not _is_views_authorized(agent_id, BOARD_LINK_AGENTS):
      return (
          "Error: board_link is restricted to the 'ari' and 'leader' agents — "
          f"this call came from agent {agent_id!r}."
      )
  ```

  `BOARD_LINK_AGENTS = frozenset({"ari", "leader"})` — the gate set matches the **documented audience** for this tool (D13 / §2.5: `board-link` is ari + leader) and is **different from `view_write`'s** (`{"ari"}`, the ARI-ONLY pre-ruling). Both sets live side by side in `daemon/tools/views_auth.py` (§3.1) so the difference is visible in one place. Envelope convention: `Error: …` (phase-3 family — §3.4), **not** phase 1's `ERROR: …`.
- **Disabled envelope (A-36):** live-views disabled ⇒ the board GET is a **permanent 404** (`routers/live_views.py:1320-1323` — disabled resolves to the uniform 404) even though the row exists ⇒ `board_link` returns the `view_link` disabled envelope (`tools/live_views.py:243-244`), NEVER a dead URL.
- Returns the same 5-line shape as `view_link`: `Public URL: <base>/views/public/board/index.html` · `Passcode: …` · `Authenticated URL: <base>/views/board/index.html` (informational — the direct lane 404s; the board is DB-rendered) · `Generation: n` · `Locked: no`.
- **Mint seam — REUSED, never re-implemented, and wrapped in `to_thread`.** `passcode_service.mint("board", "index.html", actor)` / `.refresh(...)` — the same reuse as `view_link` (`tools/live_views.py:295-335`). **`ViewPasscodeService.mint` / `.refresh` are SYNCHRONOUS** (DB-backed) ⇒ in an **async** tool the call MUST be wrapped: `result = await asyncio.to_thread(passcode_service.mint, "board", "index.html", actor)` — **exactly ONE thread crossing** around the single sync call (architect §3.3: "`mint` / `refresh` … **synchronous** ⇒ `await asyncio.to_thread(...)` mandatory in an async tool (`daemon/tools/scheduling.py:216-218`)"). This is the same "one `to_thread` around the work, not one per field" discipline as A-37's board render. **Note the in-tree difference:** `view_link` is a **sync** tool (`def view_link(` — `tools/live_views.py:113`) and therefore calls `mint` directly, which is correct there; `view_write` / `board_link` are **async** ⇒ the `to_thread` wrap is mandatory. `asyncio` is already imported by the sibling tool modules (`daemon/tools/scheduling.py`).
- **URL base — `board_link` composes BOTH URL lines ITSELF (A-36):** `build_public_url` returns `None` for a reserved **AND** for an unregistered root (`services/live_views.py:1275-1281`) ⇒ public line = `service.public_base()` + `PUBLIC_NAMESPACE` + `/board/index.html` (D17); authenticated line = `service.public_base()` + `/views/board/index.html` (informational, D20). The docstring notes the base may be the **Host-captured** value; setting `live_views.external_base_url` yields a stable pasted link.

---

## 5. Storage decision (deliverable A) — plain `.html` files, NOT blob+sidecar

**DECISION: `<data_dir>/reports/<rel>.html` as plain files.** (1) **The served artefact IS an HTML file.** The `filesystem` root resolver already serves it with the right MIME (`_EXT_TO_MIME["html"] = "text/html; charset=utf-8"`, `services/live_views.py:174-186`), the extension allowlist gate (`_resolve_under_root` `:1173-1179`), the containment check (`:1147-1171`) and the 32 MiB cap (`:1185-1189`). **Zero new serving code.** (2) **The blob+sidecar substrate solves a different problem.** `tmp_images` stores extensionless BLOBS with a MIME/provenance sidecar because the payloads are opaque binaries whose type cannot be inferred from the name (`services/tmp_image_store.py:14-21, :76-90`). An HTML page's type IS its extension; a sidecar would duplicate state the filesystem already carries and would need a NEW root type (or a resolver branch) — a substrate re-implementation for one file type. (3) **Operability.** A report page is meant to be read, diffed, grepped and hand-edited by an operator; the tmp-images substrate is deliberately opaque. (4) **Retention.** tmp_images carries a 30-day sweep + a byte cap + retention classes (`:86-90, :143-146`); a published report page must PERSIST until the operator removes it — a silent 30-day disappearance of a "published" link would be a product bug.

**Rejected:** blob+sidecar (over-engineered, wrong substrate); per-project workdir storage (pre-ruled: daemon-side GLOBAL dir — a report is not a project artefact and must survive a project's absence); a new `reports` root TYPE (a new `LiveViewsRootConfig.type` literal + resolver branch for zero benefit over `filesystem`).

**Path rules (write time):** relative, no leading `/`, no `..` segments, no backslashes, no control chars (reuse `is_well_formed_rel_path`), `.html` extension enforced, resolved target contained under the **resolved** reports dir (both sides resolved — §3.2 step 4), ≤ 32 MiB. **Overwrite:** refuse by default (`overwrite=False` ⇒ typed error); `overwrite=True` ⇒ atomic replace, **generation NOT bumped** (D7); `refresh=True` is the separate, explicit generation-bump / lockout-recovery path. **Title:** required (1–200 chars), echoed, **not persisted** and **not injected** into the HTML (D9); a `Note:` line advises when the document lacks a `<title>`.

---

## 6. Security posture (documented, per the commission's explicit ask)

**What `/views` sends today** (`routers/live_views.py:122-140`, ruling quoted at `:176-179`): raw files (incl. `.html`) → `X-Content-Type-Options: nosniff` + `Cache-Control: private, max-age=60`. **NO CSP, NO X-Frame-Options** — a DELIBERATE architect ruling: "legitimate HTML may include scripts". Consequence: **CDN chart.js / mermaid in report pages already work today** and keep working for `view_write` pages (the `reports` root is a `filesystem` root ⇒ same serving path, same headers). **The serving PATH for `reports` does not change** — one header is ADDED to it (the sandbox CSP, §6.1): the route, the MIME, the 32 MiB cap, the containment check and the cache-control value are all untouched. Markdown wrapper pages → per-response nonce'd CSP with pinned-CDN `marked` + DOMPurify. Passcode pages **and the NEW board page** → `default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`, zero-JS.

**The split posture this phase adopts:** `view_write` pages (agent-authored HTML) keep the raw-lane **serving path and posture** — nosniff + `private,max-age=60`, CDN chart/mermaid allowed (existing, architect-ruled) — and **gain exactly one header**: `Content-Security-Policy: sandbox allow-scripts` (§6.1), which is a **sandbox directive, not a resource restriction**: it declares no `script-src` / `default-src`, so **no CDN load is blocked**, and `allow-scripts` keeps script execution on. What it DOES change is the page's **origin** (opaque ⇒ no host cookies / no `localStorage` / no same-origin reads). The **board page** (DB-derived, server-rendered) takes the STRICT posture — `script-src 'none'`, `default-src 'none'`, nonce'd style, no CDN, no external subresource of any kind: the board renders agent-authored titles/bodies/notes, so even though every value is escaped, a strict CSP means a future escaping bug CANNOT execute script; the board needs no CDN (it is a table), so the strict posture costs nothing. **The board page's strict CSP is explicitly UNCHANGED by the sandbox work** — it never needed it (zero-JS, server-rendered).

### 6.1 Raw-lane CSP sandbox — disposition: **CHEAP ⇒ ADOPTED** (leader ruling B-7)

**Where the header would be applied (verified in THIS checkout).** `_HARDENING_HEADERS` is defined at `daemon/routers/live_views.py:122-125`. A raw `.html` body leaves the router from exactly **two** sites, and **both build the header dict inline** ⇒ a per-root header is a one-line addition at each site:

| # | Site | Shape | Is `root_name` in scope? |
|---|---|---|---|
| 1 | `_serve_resolved_target` — `daemon/routers/live_views.py:1034-1041` — `Response(content=file_bytes, media_type=…, headers={**_HARDENING_HEADERS, "Content-Length": …})` | the **PUBLIC** lane's serve path (called from `_public_get` → `:1426`) | **NO** — the helper takes `(request, resolved, rel_path, public)`; `ResolvedTarget` carries no root name (`services/live_views.py:765-778` — `on_disk_path`, `content_type`, `size_bytes`, `root_type`). ⇒ add a keyword-only `root_name: str = ""` parameter and pass `root_name=root_name` at the single call site (`:1426`, where `_public_get(request, root_name, rel_path)` HAS it) |
| 2 | `get_view` (the DIRECT lane GET, `@router.get("/{root_name}/{rel_path:path}")` at `:1886`) — inline raw serve at `:1980-1987` | the **DIRECT** lane's serve path | **YES** — `root_name` is the route parameter |

⇒ **Total cost ≈ 10 lines in one file** (one helper + one parameter + two call-site header spreads + one call-site argument). **Verdict: cheap ⇒ ADOPTED.** (The "not cheap" branch is recorded in the deferred table: a per-**page** header would need the resolved path threaded into the helper — still cheap, but the per-**root** granularity is the right unit and the right default.)

**The spec (exact):**

```python
# daemon/routers/live_views.py — next to _HARDENING_HEADERS (:122-125)
_REPORTS_ROOT = "reports"
_SANDBOX_CSP = "sandbox allow-scripts"

def _raw_lane_csp_headers(root_name: str) -> dict[str, str]:
    """Per-root CSP for the RAW lane (the ``reports`` root only).

    ``sandbox allow-scripts`` puts the document in an OPAQUE ORIGIN
    (HTML §7.1.5: the "sandboxed origin browsing context flag" is set,
    because ``allow-same-origin`` is absent) while leaving SCRIPT
    EXECUTION enabled (``allow-scripts`` ⇒ the "sandboxed scripts
    browsing context flag" is NOT set) and imposing NO CSP fetch
    restrictions (the header declares no ``script-src`` / ``default-src``
    ⇒ CDN chart.js / mermaid still load and run — CSP3 §6.3.2).

    Scope: the ``reports`` root ONLY. Every other registered root keeps
    a byte-identical response (designer mockups, the markdown wrapper
    lane, …). The zero-JS board page is unaffected — it is not a raw
    file and keeps its strict CSP unchanged.
    """
    if root_name != _REPORTS_ROOT:
        return {}
    return {"Content-Security-Policy": _SANDBOX_CSP}
```

Applied at both raw serve sites as `headers={**_HARDENING_HEADERS, **_raw_lane_csp_headers(root_name), "Content-Length": …}` — the sandbox header is **added to** (never replaces) `nosniff` + `private, max-age=60`.

**What `sandbox allow-scripts` does and does not block — verified against CSP3 §6.3.2 + HTML §7.1.5 (not from memory):**

| Effect | Under `sandbox allow-scripts` | Source |
|---|---|---|
| Script execution (inline + external) | **ALLOWED** — `allow-scripts` ⇒ the "sandboxed scripts browsing context flag" is NOT set | HTML §7.1.5 (parse a sandboxing directive) |
| **CDN subresource loads (chart.js / mermaid from a CDN)** | **ALLOWED** — the header declares **no** `script-src` / `default-src`, so CSP imposes **no** fetch restriction at all; the `sandbox` directive only sets the HTML sandboxing flags | CSP3 §6.3.2 (+ §4.1.2/§4.1.3: a directive with no source list blocks nothing) |
| `document.cookie` (read/write) | **BLOCKED** — no `allow-same-origin` ⇒ "sandboxed origin browsing context flag" ⇒ *"prevents script from reading from or writing to the `document.cookie` IDL attribute, and blocks access to `localStorage`"* | HTML §7.1.5 (flag definition, verbatim) |
| `localStorage` | **BLOCKED** (same flag) | HTML §7.1.5 |
| Same-origin `fetch` / XHR to the daemon | **DEGRADED to cross-origin** — the document's origin is OPAQUE ⇒ *"forces content into an opaque origin, thus preventing it from accessing other content from the same origin"*; the daemon sends no CORS headers ⇒ such a request cannot read the response | HTML §7.1.5 + the daemon's no-CORS posture |
| Form submission | **BLOCKED** — no `allow-forms` ⇒ "sandboxed forms browsing context flag" | HTML §7.1.5 |
| `window.open` / `target=_blank` | **BLOCKED** — no `allow-popups` ⇒ "sandboxed auxiliary navigation browsing context flag" | HTML §7.1.5 |
| Top-level navigation (a link inside the report) | **BLOCKED — even WITH user activation** — no `allow-top-navigation` / `allow-top-navigation-by-user-activation` ⇒ BOTH the "sandboxed top-level navigation without user activation" AND the "sandboxed top-level navigation with user activation" flags are set | HTML §7.1.5 |
| Downloads | **BLOCKED** — no `allow-downloads` | HTML §7.1.5 |
| `window.alert` / `confirm` / `prompt` / `print` | **BLOCKED** — no `allow-modals` | HTML §7.1.5 |
| `document.domain` setter | **BLOCKED** (unconditional — no keyword relaxes it) | HTML §7.1.5 |

**The behavioural cost, stated plainly (so nobody is surprised):** a `reports` page loses top-level navigation, forms, popups, downloads, modals and any same-origin XHR. For a **static, agent-authored report page** — the product decision — that is an acceptable price for removing the credential exposure; it is **not** acceptable for an interactive dashboard ⇒ such a page belongs on a different surface (which is exactly why the flagship surface in this phase is the **zero-JS, server-rendered board**, not a scripted page). **Rollback:** the change is one dict entry at two sites; deleting the helper restores the exact pre-phase header set.

**Threat model, stated honestly:**

- The `reports` surface is writable ONLY by ari (`view-write` is privileged + allow-listed + **in-body gated** — three layers, D3) ⇒ the XSS blast radius of a `view_write` page is "ari can publish a page that runs script in a viewer's browser". Inherent to the product decision ("ari writes rich HTML reports"), accepted by the architect ruling.
- Readership is gated by a 6-char passcode + a 10-strike lockout + a 24h grant cookie. The passcode is low-entropy (6 chars ≈ 30 bits) ⇒ **online guessing is what the lockout + the per-IP bucket (5 burst / 5 per minute) defend against**; a distributed grinding attack across IPs is slowed, not stopped. The board's content is internal work items — the passcode is the only barrier. Accepted posture of the EXISTING mechanism; the board inherits it unchanged.
- The `reports` DIRECT lane (`/views/reports/…`) is behind the OAuth proxy / in-house auth (State A / B) — NOT public. Only `/views/public/**` is proxy-exempt, and it is daemon-side gated.
- `view_write` NEVER creates a passcode row outside `/views/public/**`, and the board route is mounted under `/views/public/**` only. No gate-free route is added. The proxy contract (`docs/runbooks/live-view-passcode-deployment.md` §1.1 — the public namespace is EXACTLY `/views/public`) is preserved: **zero proxy change**, and Host preservation is untouched (the same-host CSRF check depends on it, `routers/live_views.py:721`).
- **The raw lane's SAME-ORIGIN CREDENTIAL exposure (leader ruling B-7).** A page under `/views/public/**` is served from the **same browser origin** as any other content the viewer can reach on that host — the ensemble's own UI, an OAuth-gated admin surface, another service behind the same proxy hostname. A viewer who is **logged in** to one of those carries its credentials in the browser **for that origin**. Because the raw lane ships **no CSP**, a script in a `view_write` page runs with **full same-origin privileges**: it can read `document.cookie` for the host, reach `localStorage`, and issue **credentialed** same-origin requests to those other apps' endpoints — so "ari published a report" can become "a script inside the report reads the viewer's session for another app on the same host". **That is the risk the no-CSP raw-lane ruling accepts** (the architect ruling is "legitimate HTML may include scripts"): the raw lane's exposure is a **host-sharing** exposure, not only a passcode-guessing exposure. **Mitigation disposition — ADOPTED (§6.1):** the `reports` root is served with `Content-Security-Policy: sandbox allow-scripts` ⇒ the page runs in an **OPAQUE ORIGIN** ⇒ it cannot read the host's cookies / `localStorage`, and its same-origin requests become cross-origin ⇒ **the credential dimension is closed, while CDN chart.js / mermaid keep working** (`allow-scripts` keeps script execution on; the header declares no fetch directives, so no CDN load is blocked). **Residual, stated plainly:** what remains is "ari can publish a page that runs script in a viewer's browser" — the accepted product decision (the *script-execution* dimension). The *credential* dimension is closed by the sandbox. **The zero-JS board page keeps its strict CSP UNCHANGED** — it is DB-rendered and server-escaped, so it never needs script at all, and it is not a raw-lane page.

---

## 7. Tests

**Mirror these in-tree files:** `tests/unit/routers/test_live_views_public_lane.py` (the public gate matrix), `tests/unit/routers/test_live_views_router.py`, `tests/unit/tools/test_live_views_tool.py`, `tests/unit/services/test_live_views_service.py`, `tests/unit/services/test_view_passcode_service.py`, `tests/migration/test_view_passcode_index_parity.py`.

**A — `tests/unit/tools/test_view_write_tool.py`**

T-A1 writes a page ⇒ the file exists under the reports dir; the return carries all 8 lines. T-A2 the return shape is `view_link`-compatible (first five labels byte-match). T-A3 path traversal (`../x`, `a/../../b`, leading `/`, backslash, control char, null byte) ⇒ typed `Error:`, NO file written. T-A4 extension gate (`page.txt` ⇒ error; `page` ⇒ `.html` appended). T-A5 the 32 MiB cap (cap+1 ⇒ error naming the limit; exactly-at-cap ⇒ ok). T-A6 overwrite (existing path + `overwrite=False` ⇒ error, original bytes intact; + `overwrite=True` ⇒ replaced, generation UNCHANGED, passcode unchanged). T-A7 `refresh=True` ⇒ generation+1 + a new passcode. T-A8 privilege pins — **THREE layers, all three asserted** (iteration-002, BLOCKER 1): **(i) category layer** — `view-write` ∈ `PRIVILEGED_TOOL_CATEGORIES`; the five SAME-PR pins updated; ari gets the tool, leader/designer/worker do NOT (assert via `resolve_tool_filter` + `_strip_privileged_category_tools`); **(ii) allow-list layer** — `"view-write"` ∈ ari's `tools.allow` and ∉ leader/designer/worker/tester `tools.allow`; **(iii) BODY GATE (the layer this iteration adds)** — build the tool via `create_view_write_tools(manager, current_instance_id=<id>, agent_id="designer")` (i.e. an agent that would reach the tool only through a **literal tool-name allow entry**, which bypasses the category gate — `instance.py:468-478`) and assert: the call returns the typed `Error: …` envelope, **NO file is written** under the reports root, and **NO passcode row is minted** (the `live_view_passcodes` row count is unchanged). Repeat for `agent_id=""` (blank ⇒ fail-closed), `agent_id="leader"`, and `agent_id="worker"` — all denied; `agent_id="ari"` is the ONLY allowed id and it writes + mints. The helper is the shared one: assert both tools import `_is_views_authorized` from `daemon.tools.views_auth` (a source-scan / identity pin, so a copied second helper is caught). T-A9 registration completeness: `view_write` ∈ `DYNAMIC_TOOL_NAMES` ∩ `KNOWN_TOOL_NAMES`; `CATEGORY_MODULES["view-write"]` resolves; `create_instance_tools` produces the tool. T-A10 the reports root is auto-injected by the lifespan when absent and NOT overridden when the operator declares it — **plus the relative-`ENSEMBLE_DATA_DIR` case (A-30/G1): with `ENSEMBLE_DATA_DIR=./data` (the relative-default shape), the injected root path is RESOLVED ABSOLUTE and a published page actually serves** (unresolved ⇒ the resolver refuses the root ⇒ every page 404s while the tool reports "not configured").

**B — `tests/unit/tools/test_board_link_tool.py`**

T-BL1 mints `(board, index.html)`; the 5-line shape; the URL is `/views/public/board/index.html`. T-BL2 `refresh=True` ⇒ generation+1 + a new passcode. T-BL3 the row is NOT mintable through `view_link` (reserved name). T-BL4 privilege — **THREE layers, all three asserted**: **(i) category layer** — `board-link` privileged; ari + leader get it, others do not; **(ii) allow-list layer** — `"board-link"` ∈ ari's AND leader's `tools.allow`, ∉ designer/worker/tester/watcher `tools.allow`; **(iii) BODY GATE (the layer this iteration adds)** — `create_board_link_tools(manager, current_instance_id=…, agent_id="designer")` returns the typed `Error: …` envelope and **mints NO row** (the `live_view_passcodes` row count is unchanged); `agent_id="ari"` **and** `agent_id="leader"` both mint successfully; `agent_id=""` (blank) is denied. The gate set is `{ari, leader}` — **not** `{ari}` — so the pin must fail if `view_write`'s set leaks in (assert the two sets differ).

**C — `tests/unit/routers/test_live_views_board.py`**

T-B1 **mount order (A-35 — BOTH halves)** — (a) **route-table INDEX assertion**: the index of the board route in `app.routes` < the index of the public catch-all GET; (b) **behavioural half**: a minted row ⇒ the interstitial/board HTML, not JSON. A response-only pin is ambiguous — with no minted row the mis-ordered route ALSO returns the uniform 404. T-B2 **no row ⇒ uniform 404, BYTE-IDENTICAL** — body AND headers equal `_uniform_404()`'s output for a miss on a random unrelated path; any board-specific "unavailable" HTML would disclose both that the URL is special and that a row exists (§5.4). T-B3 no cookie ⇒ the interstitial (200, `script-src 'none'`, `form-action 'self'`, **`cache-control` EXACTLY `no-store`** — the merge-order trap pin, A-33, form action = the board URL, field name `code`). T-B4 the right passcode via the EXISTING catch-all POST ⇒ 303 + `Set-Cookie` grant cookie + `Location: /views/public/board/index.html` ⇒ follow ⇒ the board renders. T-B5 wrong passcode ⇒ failure page + strike; the 10th wrong ⇒ locked page. T-B6 a grant cookie for a different rel / a stale generation ⇒ interstitial + cookie clear. T-B7 **regression pin (load-bearing) — FOUR pins (A-35)**: (i) the board POST verifies although the root is reserved AND unregistered (the catch-all POST consults neither list); (ii) a wrong code still strikes and locks on the 10th; (iii) a **SOURCE-SCAN pin** asserting `is_reserved_root_name` (and `has_root`) is ABSENT from `_public_post` (`:1464-1741`); (iv) the 303 `Location` and the `Set-Cookie` `Path` are EXACTLY `/views/public/board/index.html` (query-bearing Location, queryless Path). T-B8 **escaping** — an item titled `<script>alert(1)</script>` and a note containing `"><img src=x onerror=…>` appear ESCAPED; the raw payload is NOT in the body. T-B9 **CSP** — the strict header set is present and the body contains NO external subresource URL (no `https://`). T-B10 **filters** — `?status=in_progress` renders only in-progress items; `?tag=` and `?project=` narrow; the filter form's action is the board URL; unknown filter values are ignored (no 500); **a filter echo containing `&`/`<` round-trips (quote-then-esc — the echo re-selects the same filter, A-32)**. T-B11 **freshness** — two GETs after a DB change render different content (no caching). T-B12 **direct lane** — `/views/board/index.html` ⇒ uniform 404. T-B13 **reserved-name boot refusal** — a config root named `board` refuses to boot with the GENERIC message (mirrors the `public` pin; A-39). T-B14 **notes timeline** — a row's notes render oldest-first inside its `<details>`. T-B15 **HEAD (A-40)** — `/views/public/board/index.html` HEAD matches the BOARD HEAD route (headers only), NOT the public HEAD catch-all (which would return a JSON body on a HEAD request). T-B16 **CSP nonce pairing (B-9)** — the response header's `style-src 'nonce-X'` and the body's `<style nonce="X">` carry the **same** X, and **two consecutive GETs carry DIFFERENT nonces** (per-request mint; a mismatch ⇒ a silently unstyled board, which no other pin would catch). T-B17 **raw-lane sandbox CSP on the `reports` root (B-7)** — a page written to `reports` is served with `Content-Security-Policy: sandbox allow-scripts` **in addition to** the unchanged `nosniff` + `private, max-age=60`; the same header is **ABSENT** on an existing (non-`reports`) root, whose bytes + headers stay byte-identical (no behaviour change for designer mockups); the board page's own strict CSP is **unchanged** (still `default-src 'none'; script-src 'none'; style-src 'nonce-…'` — **NOT** `sandbox`).

**D — regression suite (must stay green)** `tests/unit/routers/test_live_views_public_lane.py` (the whole matrix — the board must not perturb the existing gate), `test_live_views_router.py`, `tests/unit/tools/test_live_views_tool.py`, `tests/unit/services/test_view_passcode_service.py`, `tests/integration/test_auth_public_lane_tag.py` (the public-prefix exemption is unchanged), the five SAME-PR pin files, and phase 1's `tests/unit/test_board_registration.py`.

---

## 8. Risks

**Numbering note (advisory A-3).** This table's ids are **local to phase 3** and are **non-contiguous** — they run
**R36–R42, R45 and R47** here, because **R43**, **R44** and **R46** are **phase-2** rows (the default schedule list
hiding armed alarms · the shared label namespace · the `tz_warning` erasure) that live in
[phase2-plan.md](phase2-plan.md). The commission-level table in [plan-overview.md §6](plan-overview.md) is the
consolidated one (**R1–R49**) and **renumbers** the phase-local ids. ⇒ **A phase-3 row id is NOT a plan-overview row
id**; read the row text, not the id.

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Mount order** — the board route declared below the catch-all ⇒ the board silently 404s. **Mechanism (corrected, A-35/§5.1):** the miss is NOT "the catch-all resolves `board` as a file" — `_public_resolve` rejects the reserved name at its mirror FIRST (uniform 404, `routers/live_views.py:1313-1316`; service mirror `services/live_views.py:911-913`); same 404, different mechanism. | HIGH | Loud comment at the declaration states the corrected mechanism + T-B1's route-table INDEX assertion + the behavioural half |
| R2 | **Config-replace footgun** — an operator `live_views.roots:` block replaces the seed dict (`config.py:2903-2914`) ⇒ a config-declared `reports` root would vanish. | HIGH | **The `reports` root is LIFESPAN-INJECTED, never config-declared** (D1) ⇒ the footgun cannot bite. `config.yaml` gets a doc-only note. |
| R3 | **CSP posture drift** — someone adds a CDN `<script>` to the board template "for a nicer chart". | MED | The board template ships zero external subresources; T-B9 asserts no `https://` in the body + the strict CSP header set. **EXTENDED (iteration-002 / B-7) — this row now also carries the raw-lane sandbox-CSP residual:** the `reports` root gains `Content-Security-Policy: sandbox allow-scripts` (§6.1), whose COST is that a report page loses top-level navigation, forms, popups, downloads, modals and same-origin XHR (HTML §7.1.5). Mitigation: scoped to the `reports` root ONLY (every other root stays byte-identical); CDN scripts still load and run (the header declares no fetch directive); the same-origin **credential** exposure is closed; the board page's strict CSP is **unchanged**; T-B17 pins both the presence on `reports` and the ABSENCE elsewhere. **No new risk id was minted** — the commission's id space is taken (R43/R44/R46 are phase-2's, R48/R49 are plan-overview's) ⇒ the residual rides on this row |
| R4 | **Cross-phase seam drift** (phase 1 renames/moves the service or changes the item shape). | HIGH | One adapter file is the ONLY coupling point; it is shape-tolerant and degrades to an explicit "unavailable" notice, never a 500. **The phase-3 builder MUST reconcile against the landed phase-1 code before merging.** |
| R5 | **Category/module conflict on `"board"`** | LOW | **Eliminated by design:** `board_link` gets its OWN module + its OWN category (`board-link`), so phase 1's `"board": "daemon.tools.board_tools"` is untouched (D13) |
| R6 | **Privileged-category pin blast** — adding `view-write` + `board-link` trips the SAME-PR five exact-set pins. | MED | All five pins updated in the SAME commit (§2.5) |
| R7 | **Passcode lockout UX** — 10 strikes locks the board/report; recovery = `refresh=True`. The locked notice carries no form and no remaining-attempts hint. | LOW | Documented in both tool docstrings; `refresh=True` is the single documented recovery (mirrors `view_link`) |
| R8 | **The 32 MiB write cap and the served cap must be the same constant.** | LOW | The tool imports `MAX_SERVED_BYTES` from `daemon.services.live_views` (one source of truth) |
| R9 | **Operator has a root named `board`** ⇒ boot refusal after upgrade. | MED | Documented migration note; the validator's message names the offending root and suggests a rename — **genericized** (A-39): the "public passcode lane occupies this prefix" clause (`config.py:3142-3149`) is retired for a reserved-set wording |
| R10 | **Served-page staleness for `view_write`** — `private, max-age=60` ⇒ a reader may see the old page for up to 60 s after an overwrite. | LOW | Accepted (existing lane behaviour); the board is `no-store` and always fresh |
| R11 | **Notes hydration — RESOLVED by adjudication (the N+1 is gone).** The board hydrates notes via the frozen `list_items(..., with_notes=True)` call — phase 1's internal batched loaders return them in bulk (≤ 3 queries per render instead of 1+50 = 51). The per-row `get_item` fallback remains (shape-tolerant) but is **not** the normal path. | LOW | Batched by construction (architect §2.4, decisions §C5 theme 12); the per-row fallback stays bounded at ≤ 50 reads/render and degrades a row to "notes unavailable", never a 500. **Residual (unchanged by the resolution): the per-render caps still apply** — the 50-row render limit, the 500 filter-option cap (A-41), and the A-34 per-field caps (notes ≤ 20/item · note text ≤ 4 KiB · `body_md` ≤ 64 KiB · 2 MiB rendered-body ceiling), all enforced during assembly |
| R12 | **Phase-1 merge collision on `agents/ari/meta.json`** (both phases append to `tools.allow`). | LOW | Both append at the END of the array (phase-1 D15 discipline); the allow-list pin tests are per-phase |
| R36 | **Injected `reports` path can be RELATIVE ⇒ `view_write` silently dead** (A-30/§5.6 G1 — `./data` default; resolver refuses non-absolute, `live_views.py:952-958`). | 🔴 HIGH | `str((Path(_data_dir) / "reports").resolve())` + the relative-`ENSEMBLE_DATA_DIR` test (T-A10) |
| R37 | **`str.format` + CSS braces ⇒ 500 on every board GET** (A-32/§5.6 G2). | 🔴 HIGH | Plain concatenation (`_shell` precedent) or `string.Template`; no format call on the CSS-bearing template |
| R38 | **`Cache-Control` merge-order trap ⇒ `no-store` lost** to `private, max-age=60` (A-33/§5.5; the `:1857` shape). | MED | Headers from `page_headers()` ALONE; T-B3 pins `cache-control` exactly `no-store` |
| R39 | **Render output has no byte cap**; `body_md`/note text are unconstrained TEXT (A-34/§5.8). | MED | 2 MiB rendered-body ceiling + per-field caps (20 notes/item · 4 KiB note · 64 KiB body_md) enforced DURING assembly, visible truncation notice |
| R40 | **`board_link` hands out a URL when live-views is disabled** ⇒ permanent-404 link (A-36/§5.9). | MED | The `view_link` disabled envelope (`tools/live_views.py:243-244`) |
| R41 | **No `fsync` before `os.replace`** ⇒ zero-length page after a crash (A-31/§5.7). | MED | flush + fsync before replace + finally-unlink + `0o700` dirs / `0o600` file |
| R42 | **A future `has_root` / reserved-name check in `_public_post` silently breaks the board** (A-35/§5.3). | MED | The four-pin T-B7 set incl. the `_public_post` source scan |
| R45 | **Create-response `status` is racing** (`'starting'`) — consolidated-ledger row (phase-2 surface, tracked here per architect §7.3). | LOW | Echo the actual status; treat `'starting'` as armed |
| R47 | **Truncation is invisible** — >50 rows / >500 filter values (A-41/§5.8). | MED | "showing N of M" disclosure + the filter-option cap notice |

---

## 9. Success criteria

Each criterion is observable; the threshold column is what passes.

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | `view_write` publishes a page end-to-end | ari calls `view_write`; a fresh client opens the returned public URL, enters the passcode | interstitial → 200 + the page's sha256 matches the tool's input |
| 2 | The served page is byte-identical to ari's HTML | sha256(tool input) vs sha256(served body) | equal — the tool never mutates the bytes (D9) |
| 3 | Oversize pages are refused at WRITE time | a > 32 MiB payload | typed `Error:` naming 33554432; nothing written |
| 4 | The board page is always fresh from the DB | two GETs around a DB write | the two responses differ; the second shows the new row (T-B11) |
| 5 | The board page is inert | scan the body for external subresource URLs; read the CSP header | zero `https://` subresources; `script-src 'none'` + `default-src 'none'` present (T-B9) |
| 6 | User content cannot inject markup | a title and a note carrying `<script>` / `"><img onerror=…>` | the raw payloads are absent from the body; only escaped forms appear (T-B8) |
| 7 | `view_write` is ari-only — **all THREE privilege layers** | resolve the toolset for ari / leader / designer / worker / tester; **then** call the tool with a non-ari `agent_id` | only ari resolves `view_write`; the privileged strip keeps it from the empty-allow universe; **and** a non-ari (or blank) `agent_id` gets `Error: …` with **NO write and NO passcode row** (T-A8 i/ii/iii) |
| 8 | The board gate leaks nothing | GET the board URL with no minted row | the uniform 404 envelope — byte-identical (body + headers) to any other miss (T-B2) |
| 9 | The shared gate is unregressed | the existing live-views suites | `test_live_views_public_lane.py` + `test_live_views_router.py` + `test_live_views_tool.py` + `test_view_passcode_service.py` green, unchanged |
| 10 | No seal, proxy or migration impact | diff the tree | zero files from `agents/.prompt-artifacts.sha256` / `SEALED_SHA_BASELINES` (phase 3 edits only `meta.json`, which is in neither baseline ⇒ no re-stamp obligation); zero proxy/infra files; zero new migration files |
| 11 | The board page stays bounded | time a board render at the 50-row ceiling | ≤ 50 rows, notes hydrated in batch (≤ 3 queries/render), < 2 s locally (R11; the per-row `get_item` fallback stays bounded at ≤ 50 reads) |

---

## 10. Open questions for the architect

- **OQ-1 — phase-1 list projection + notes. ✅ RESOLVED by adjudication (architect §2.4; decisions §C5 theme 12).** Phase 1 adds `list_items(..., with_notes=False)` + internal **batched** notes/tags loaders (`WHERE item_id IN (…)`, then group in Python; ≈15–20 lines), and `list_items` / `get_item` are **frozen** (names + keyword names) as the binding phase-1 → phase-3 interface (phase-1 §5.2 "Interface freeze" + A-17; architect §3.4). ⇒ The board hydrates notes via **one** `list_items(..., with_notes=True)` call — **the N+1 disappears** (≤ 3 queries per render vs 1+50 = 51). Phase 3 keeps its tolerant "notes unavailable" fallback and works against **both** shapes (a `with_notes=False` list ⇒ per-row `get_item`). **Residual, still binding (unchanged by the resolution): the per-render caps** — the 50-row render limit, the 500 filter-option cap (A-41), and the A-34 per-field caps (notes ≤ 20/item · note text ≤ 4 KiB · `body_md` ≤ 64 KiB · 2 MiB rendered-body ceiling). The tool's own item shape is unchanged (no `notes[]` in `board_list`), so the payload and truncation behaviour are untouched.
- **OQ-2 — `board_link` in its own category vs. the phase-1 `"board"` category.** **RESOLVED by the architect adjudication (§1.5; ledger theme 1): keep `board-link` separate.** The original privilege-based justification — phase 1's `"board"` is NON-privileged ⇒ default-granted to every empty-allow agent (phase-1 D6/Q1) — is GONE: `board` is PRIVILEGED from phase 1's first commit (C1 ruling). The split endures on the two reasons that remain: (a) **audience separation** — ari+leader may MINT the link, while the `board` data tools are what the page reads; (b) `view_link` can never mint the reserved name; plus the merge hygiene D13 cites (R5).
- **OQ-3 — the interstitial copy.** The reused interstitial says "view this document" for a board. (a) accept the cosmetic mismatch (this plan — zero gate risk); (b) add a board-specific interstitial (new template + divergence risk vs. the gate contract); (c) generalise `render_interstitial` with a `subject` kwarg (touches a file used by 4 other paths). Recommend (a).
- **OQ-4 — board layout.** Flat filtered table with a `<details>` per row (this plan) vs. grouped per-project sections. Rendering-only change.
- **OQ-5 — render ceiling.** The board renders ≤ 50 rows and fetches ≤ 500 for the filter options. Phase 1's `list_items` has `limit=50` by default, `with_notes=False` by default, and no cursor (phase-1 Q3). **Is 50/500 right?**
- **OQ-6 — should the board page be the headline link, or the direct (OAuth) lane?** This plan returns both (mirroring `view_link`'s 5-line shape) but the public URL is the headline; the direct lane 404s by design (D20).

---

## 11. Design decisions made in this phase plan (numbered, rationale + anchors)

- **D1 — the `reports` root is LIFESPAN-INJECTED, not operator-declared.** Its path derives from the runtime `data_dir` (`ENSEMBLE_DATA_DIR > DATA_DIR > ./data`, `api.py:483-487` — **read INSIDE the lifespan, so it is a lifespan local, not a `create_app` local**; B-3 correction), which a static `config.yaml` cannot express; and an operator `live_views.roots:` block REPLACES the seed dict (`config.py:2903-2914`), so a config-declared root would silently vanish. Injection is idempotent and never overrides an operator-declared `reports` entry. → `api.py:449-450`, `:483-487`, `:493`, `:1637`, `:1685-1701`.
- **D2 — `reports` stores plain `.html` files, NOT a blob+sidecar substrate.** The artefact's type IS its extension; the filesystem root already serves `.html` with the right MIME, allowlist, containment and 32 MiB cap; the tmp_images substrate exists for opaque binaries and carries a 30-day sweep that would make "published" links vanish. → `services/live_views.py:174-186`, `:1147-1196`; `tmp_image_store.py:14-21`.
- **D3 — `view_write` gets a NEW PRIVILEGED category `view-write` (ari-only) — AND an in-body `agent_id` gate. THREE layers, ALL required (REWRITTEN, iteration-002 / BLOCKER 1).** ⚠️ **CONFLICT WITH THE COMMISSION HINT, RESOLVED AGAINST IT.** A non-privileged category is **default-granted** to the empty-allow universe (`resolve_tool_filter` returns "all tools" for an empty allow+deny pair, `instance.py:373-434`; the default-deny strip removes only PRIVILEGED categories, `instance.py:6243-6271`) ⇒ it would hand a page-publishing tool to worker/tester/developer, violating the binding "ARI-ONLY" pre-ruling. **The binding pre-ruling wins; the mechanism hint loses.** It is still a NEW category — `view-views` (ari+leader+designer) is untouched ⇒ leader gains no write authority. Cost: the SAME-PR five-pin update (`_tool_registry.py:215-238`). **THE THREE LAYERS (the doctrine block at the top of this plan):** **(1)** the privileged category — `view-write` never joins the empty-allow universe; **(2)** the explicit allow-list entry `"view-write"` on ari's `tools.allow`; **(3)** the **in-body gate** — the tool body calls the shared `_is_views_authorized(agent_id, VIEW_WRITE_AGENTS)` (§3.1) with `VIEW_WRITE_AGENTS = {"ari"}` and refuses anything else. **Layer 3 is load-bearing, not decorative:** a **literal tool-name** allow entry bypasses the category gate entirely (`daemon/tools/instance.py:468-478` — an allow entry that is not a category key is added verbatim) ⇒ **layers 1 + 2 alone do NOT enforce ari-only.** This mirrors phase 1's ruling verbatim (phase1-plan.md:19 / :270; decisions.md R-C1:54): the category gate AND the body gate are both required; one is not a substitute for the other. `board_link` carries the identical three layers with the gate set `{"ari", "leader"}` (D13 / §2.5) — the gate set is **per tool** and matches each tool's documented audience. **Proof obligations:** T-A8 (i)(ii)(iii) and T-BL4 (i)(ii)(iii).
- **D4 — the passcode substrate is REUSED, never re-implemented.** `view_write` and `board_link` call `ViewPasscodeService.mint/refresh` exactly as `view_link` does (`tools/live_views.py:295-335`) — same lazy-mint semantics, same generation bump, same rotation-fail envelopes. No new crypto, no new table, no new passcode concept.
- **D5 — `view_write` writes only under the daemon-global `reports` root** (pre-ruled; not per-project). Path validation reuses `is_well_formed_rel_path` (`services/live_views.py:251-296+`) so the write path and the serve path share ONE validator, plus a write-time containment check mirroring `_resolve_under_root` (`:1147-1171`).
- **D6 — the 32 MiB cap is enforced at WRITE time** with a typed error naming the limit and the byte count, using the same `MAX_SERVED_BYTES` constant the serve path uses (`:166`, `:1186`). A page that cannot be served must never be written.
- **D7 — overwrite does NOT bump the passcode generation.** ⚠️ **REFINEMENT of the pre-ruling suggestion "same path = overwrite + generation bump?".** The passcode authorises "may this person see THIS page" and the URL is meant to be STABLE; bumping on every refresh would lock a reader out each time Ari updates the report. Bumps are reserved for the explicit `refresh=True` (lockout recovery), keeping `view_write` consistent with the live board (content changes, passcode stable). Overwrite still requires an explicit `overwrite=True` (no silent clobber).
- **D8 — `refresh=True` bumps the generation** (new passcode, outstanding grants revoked) — mirroring `view_link`'s documented behaviour (`tools/live_views.py:153-170`).
- **D9 — `title` is required, echoed, NOT persisted and NOT injected into the HTML.** The tool never mutates the caller's bytes (a served page is byte-for-byte what ari wrote); no consumer reads a stored title in this phase; a `Note:` line advises when the document lacks a `<title>`. A future reports-index feature may add a sidecar then.
- **D10 — `board` becomes a RESERVED root name; it is NOT a registered root.** The board is DB-rendered, not a file. Reserving the name (a) prevents an operator from shadowing it (boot refusal, `config.py:3098-3142`), (b) makes the direct lane 404 (`routers/live_views.py:1163-1166`), (c) keeps the board's link out of the `view-views` (designer-reachable) minting path. → `services/live_views.py:141`.
- **D11 — ONE passcode row for the board: `(board, "index.html")`. Filters are QUERY params, NOT part of the key.** A passcode authorises "may this person see the board", not "may they see a filtered subset". Per-filter rows would explode the row count, violate the "GET never mints" write-amplification pin (ADR T7) and multiply lockouts. Consequence, stated plainly: **a passcode holder sees the WHOLE board**; filters narrow the display, never the disclosure.
- **D12 — the board's GET is a NEW STATIC route declared BEFORE the public catch-all; the board's POST verify REUSES the existing catch-all POST unchanged.** Mount order is load-bearing (Starlette first-match-wins, `routers/live_views.py:1068-1078`); the gate mirrors `_public_resolve` (`:1271-1392`) minus the file resolution; the verify path (bucket → lock → CSRF → compare → audit → cookie → 303) is inherited wholesale — **zero new gate code**. A regression pin (T-B7) guards the reserved-name interaction.
- **D13 — the board's link is minted by a dedicated `board_link` tool in its OWN module (`daemon/tools/board_link.py`) and its OWN privileged category (`board-link`, ari+leader).** `view_link` cannot mint it (reserved name) and its category (`view-views`) is reachable by designer. **Rationale AMENDED (architect §1.5 / A-04 p3-side):** the original justification — phase 1's `"board"` category is non-privileged ⇒ default-granted to every empty-allow agent — is GONE (`board` is PRIVILEGED from phase 1's first commit per the C1 ruling; trajectory 8 → 9 → 10 → 12). The enduring reasons: (a) **audience separation** — ari+leader may mint the LINK while the phase-1 `board` data tools are what the page reads (mint ≠ read); (b) the **reserved-name impossibility** keeps the link out of designer's `view-views` minting path. Keeping the split also preserves the phase-merge hygiene (R5).
- **D14 — the board page is zero-JS with the STRICT CSP posture; CDN scripts remain a `view_write`-page-only capability.** The board needs no CDN, so the strict posture (`script-src 'none'`, `default-src 'none'`, nonce'd style, `form-action 'self'`, `frame-ancestors 'none'`, `no-store`) is free and means a future escaping bug cannot execute script. The `view_write` pages keep the existing architect-ruled **serving path** (nosniff + `private,max-age=60`) so CDN chart/mermaid keeps working — nothing changes structurally for `reports` **except one added header**: `Content-Security-Policy: sandbox allow-scripts` (§6.1, leader ruling B-7), which is a **sandbox directive, not a resource restriction** — it declares no `script-src` / `default-src`, so **no CDN load is blocked** and `allow-scripts` keeps script execution on; what it changes is the page's **origin** (opaque ⇒ no host cookies, no `localStorage`, no same-origin reads). **The board page's own strict CSP is explicitly UNCHANGED by that work** — the board is not a raw-lane page. → `routers/live_views.py:122-125`, `:176-179`; `view_passcode_pages.py:68-89`; §6.1.
- **D15 — `body_md` is rendered as ESCAPED PLAIN TEXT (`<pre>`); the notes timeline uses `<details>/<summary>` (pure HTML).** No markdown library exists daemon-side (`pyproject.toml` has none; the `.md` wrapper loads `marked` from a CDN client-side) ⇒ server-side markdown rendering of agent-authored content without a sanitiser would be an XSS hole. → `pyproject.toml:7-50`; `routers/live_views.py:153-227`.
- **D16 — the board read seam is phase 1's `BoardService`, reached via `request.app.state.manager.board_service` (`app.state.manager` set at `api.py:2271`), adapted by a phase-3-owned shape-tolerant adapter (`daemon/services/board_view_service.py`) — the `_row_locked`/`_row_generation` precedent (`routers/live_views.py:897-920`). The adapter is the ONLY place that knows phase 1's surface. The service is synchronous ⇒ `asyncio.to_thread` (`tools/scheduling.py:206-216`). Unavailable ⇒ an explicit "board backend unavailable" notice, never a 500.
- **D17 — two new PUBLIC accessors on `LiveViewsService`** (`root_path`, `public_base`). No new authority: `root_path` returns a configured filesystem root's absolute directory; `public_base` returns the same base string `build_public_url` already uses (`:1286-1289`). No reserved-name guard is bypassed — `board_link` composes its own URL from `PUBLIC_NAMESPACE` (`services/live_views.py:155`).
- **D18 — ZERO proxy change.** Both new surfaces live under `/views/public/**` (`/views/public/reports/**`, `/views/public/board/index.html`), already proxy-exempt in BOTH deployment states and daemon-side gated. No gate-free route outside `/views/public/**`; no passcode row minted outside it; Host preservation untouched (the same-host CSRF check depends on it — `docs/runbooks/live-view-passcode-deployment.md` §1.1; `routers/live_views.py:721`).
- **D19 — NO seal re-pin in this phase; the conventions step (step 4 of this commission) owns the ONE re-stamp pass.** Two DIFFERENT baselines cover the prompt artefacts and BOTH are live: the **committed 161-entry manifest** `agents/.prompt-artifacts.sha256` (ari rows `:26`–`:29`: `rule.md`, `soul.md`, `tools_note.md`, `workflow.md`; leader rows `:102`–`:106`) and the **35-entry `SEALED_SHA_BASELINES` test tripwire** (`tests/test_chart_image_delivery_e2e.py:1350-1398`, covering `agents/ari/{rule,workflow}.md` + `agents/leader/rule.md` among them). This phase touches `agents/{ari,leader}/meta.json` **only** — `meta.json` appears **ZERO** times in the manifest and neither ari nor leader `meta.json` is in the tripwire ⇒ **this phase carries NO re-stamp obligation**. The `agents/ari` conventions update (the `## Board` / `## Self-Alarm` / `## Live Views` sections + the session open/close procedure) is **step 4 of THIS commission** — [plan-overview.md §8](plan-overview.md) — running **after** phase 3 and **before** merge + stage, and it owns the **ONE** in-commit re-stamp pass (manifest rows recomputed and committed with the edits; the tripwire constant re-pinned **only** for entries it edits **that are currently green** — the 7 pre-existing stale pins are **NOT ours to fix**). **Write lane (binding):** the file tools **HARD-REFUSE** a write to a versioned prompt artefact (`VERSIONED_PROMPT_ARTIFACTS`, `daemon/tools/prompt_artifacts.py:119-127`; fail-closed guards in `daemon/tools/filesystem.py` — `write_file` **and** `edit_file`); the **sanctioned lane** is the **bash/git escape hatch** with a **same-commit manifest re-stamp**; the **prompt-versions API is a runtime DB lane**, not the repo-commit path.
- **D20 — the board's direct lane is intentionally 404** and its "Authenticated URL" line is informational only. The board is a DB-rendered public surface; there is no OAuth-guarded variant to point at. Stated in the tool output so no reader is misled.

---

## Amendment log (post-architect 2b297db7d)

Folded from `architecture-recommendation.md` §9 ledger (planning head `2b297db7d`); the leader ACCEPTED all verdicts as binding. P0 = correctness/blocking, P1 = clarity/consistency. Anchors re-verified in THIS worktree at fold time.

- **A-30 (P0)** — §2.2: injected `reports` path is `.resolve()`d — **blocking defect**: `data_dir` defaults to RELATIVE `./data` (`api.py:483-488`) and the resolver refuses non-absolute roots (`live_views.py:952-958`) ⇒ every page 404s behind a misleading "not configured" error; relative-`ENSEMBLE_DATA_DIR` test added to T-A10 (§5.6 G1; risk R36).
- **A-31 (P0)** — §3.2 steps 4+7: same-directory temp file, `flush()` + `os.fsync` BEFORE `os.replace`, temp unlink in `finally`, containment against the RESOLVED base, `0o700` dirs / `0o600` file (§5.7; risk R41).
- **A-32 (P0)** — §4.3 + file map: **NO `str.format`** on the CSS-bearing template (CSS braces ⇒ 500 on every board GET — blocking defect); plain concatenation (`_shell` precedent) or `string.Template`; exactly one `_esc()` helper; `quote()` before `_esc()` in URL contexts; `^wi-[0-9a-f]{8}$` id validation; filter-echo round-trip test (§5.6 G2 + §5.10; risk R37).
- **A-33 (P0)** — §4.2/§4.3: board headers from `page_headers()` ALONE (never merged with `_HARDENING_HEADERS` — the `private, max-age=60`-wins trap, `:1857` shape); `cache-control` pinned EXACTLY `no-store` (T-B3) (§5.5; risk R38).
- **A-34 (P0)** — §4.3: 2 MiB rendered-body ceiling + per-field caps (notes ≤ 20/item, note text ≤ 4 KiB, `body_md` ≤ 64 KiB), enforced DURING assembly, visible truncation notice (§5.8; risk R39).
- **A-35 (P0)** — §7 + R1: T-B1 = route-table INDEX assertion + behavioural half; T-B7 = four-pin set (reserved+unregistered POST verify · strikes/lock · `_public_post` source scan · exact 303 Location + queryless cookie Path); T-B2 = uniform-404 byte equality (body + headers); R1's failure mechanism corrected (reserved-name mirror `:1313-1316`, not "resolved as a file") (§5.1, §5.3, §5.4; risk R42).
- **A-36 (P0)** — §4.6: `board_link` returns the disabled envelope when live-views is off (board GET would be a permanent 404); composes BOTH URL lines itself (`build_public_url` refuses reserved AND unregistered roots, `:1275-1281`); Host-captured-base note + `external_base_url` guidance (§5.9; risk R40).
- **A-37 (P0)** — §4.5: ONE `asyncio.to_thread` around the whole render (fetch + assembly), not one per call; `check_same_thread: False` (`factory.py:164`) pinned by test (§5.8).
- **A-38 (P1)** — §2.3: `root_path` returns None when the subsystem is disabled (mirrors `resolve_for_instance:897-899`) (§3.3).
- **A-39 (P1)** — T2 + §2.1 + R9: reserved-root boot-refusal message genericized — the "the public passcode lane occupies this prefix" clause (`config.py:3142-3149`) is retired once `board` is reserved too (§5.2).
- **A-40 (P1)** — §4.2/§7: board HEAD is its OWN route (FastAPI adds no implicit HEAD; a missing board HEAD returns a JSON body on a HEAD request via the `:1750` catch-all); T-B15 added (§5.1).
- **A-41 (P1)** — §4.3: "showing N of M" disclosure when count > limit + filter-option 500-cap notice (§5.8; risk R47).
- **A-42 (P1)** — §8: risks R36, R37, R38, R39, R40, R41, R42, R45, R47 added (per architect §7.3).
- **D13 rationale amendment (A-04, p3-side)** — the "phase-1 `board` is NON-privileged" premise is GONE: `board` is PRIVILEGED from phase 1's first commit (C1 ruling R1). D13, OQ-2, §0 reconciliation and §2.5 rewritten to the enduring reasons — **audience separation** (mint ≠ read) + **reserved-name impossibility** (+ merge hygiene) — and the privileged trajectory **8 → 9 (P1 `board`) → 10 (P2 `self-alarm`) → 12 (P3 `view-write` + `board-link`)** propagated (phase 3 = the 10 → 12 pass; 8 entries verified at base `630cefebb`).
- **TOUCH-UP (post-`71f74978a` consistency sweep)** — Coupling, §4.4 (filters), §4.5 (adapter + call shape), §8 (R11), §9 (criterion 11), §10 (OQ-1, OQ-5): phase 3 now consumes the **FROZEN** phase-1 board interface — `list_items(status=…, project_id=…, tag=…, due_before=…, limit=…, with_notes=False)` + `get_item(item_id=…)` (phase-1 §5.2 "Interface freeze" + A-17; architect §2.4 / §3.4) — with notes hydrated in **batch** through `with_notes=True`; the **pre-freeze adapter shape** (`list_items(status, project_id, tag, limit)` + per-row `get_item` hydration) is **gone**. The adapter reads **EXACTLY** the frozen seam (`manager.board_service.list_items` / `.get_item` — one import, no phase-1 internals). **OQ-1 → RESOLVED** (architect §2.4 / decisions §C5 theme 12) and **R11 re-graded**: the N+1 is gone, while the **residual per-render caps still apply** (50-row render limit · 500 filter-option cap (A-41) · A-34 per-field caps · 2 MiB rendered-body ceiling). Finding of the post-`71f74978a` sweep: this plan carried **ZERO** `with_notes` mentions and still described the pre-freeze adapter shape. **ITERATION-002 AMENDMENT (A-1):** the enumeration above **also omitted `due_before`** — the frozen signature carries it between `tag` and `limit` (phase-1 §5.2, `phase1-plan.md:213-215`). Corrected here and at the three phase-3 enumeration sites (Coupling, §4.5 adapter call shape, §4.5 adapter mapping, §4.4 filters).
- **BLOCKER 1 (P0) — the phase-3 body gate (the core fix).** `create_view_write_tools` had **no** `agent_id` parameter, D3 specified **category + allow-list only**, and T-A8 asserted only the category layers ⇒ the "ari-only" pre-ruling was **unenforced at runtime** (a literal tool-name allow entry bypasses the category gate outright — `daemon/tools/instance.py:468-478`), contradicting the commission's own three-layer doctrine (phase1-plan.md:19 / :270; decisions.md R-C1) and plan-overview §7 item 13. **Amended:** (1) `agent_id` added to **both** factory signatures — `create_view_write_tools(manager, *, current_instance_id, agent_id)` (§3.1) and `create_board_link_tools(manager, *, current_instance_id, agent_id)` (§4.6) — keyword-only, `""` default ⇒ fail-closed; (2) **ONE shared** in-body helper `_is_views_authorized(agent_id, allowed_agents)` in the new module `daemon/tools/views_auth.py`, with the gate sets **`VIEW_WRITE_AGENTS = {"ari"}`** and **`BOARD_LINK_AGENTS = {"ari", "leader"}`** (per-tool, matching each tool's documented audience — the ARI-ONLY pre-ruling and D13/§2.5 respectively), called as the **first statement** of each body (before any write, before any mint); (3) T-A8 extended to a **three-layer** assertion — the new (iii) proves the body gate fires (non-authorized `agent_id` ⇒ `Error: …`, **no file written**, **no passcode row minted**) even when the category gate was bypassed — and T-BL4 extended the same way for `board_link`; (4) **D3 rewritten to the three-layer doctrine** (privileged category + allow-list + in-body gate), mirroring phase 2's D10 rewrite pattern; (5) a **cross-doc note** added stating that plan-overview §7 item 13's claim ("the in-body gates are proven too") becomes TRUE after this fold — **the overview file is another worker's and is NOT edited here**. The `Error: …` envelope (phase-3 family, §3.4) is a **deliberate divergence** from phase 1's `ERROR: …` body-gate string — flagged so the builder does not copy phase 1's.
- **A-1 (P1) — `due_before` added to the frozen-interface enumerations.** The phase-1 frozen signature is `list_items(status=…, project_id=…, tag=…, due_before=…, limit=…, with_notes=False)` (`phase1-plan.md:213-215`) — `due_before` sits **between `tag` and `limit`**. Three phase-3 sites enumerated it wrongly: **Coupling**, **§4.5 call shape**, and **§4.5 adapter mapping** (plus **§4.4 filters** and this log's TOUCH-UP bullet). All five now carry it verbatim; the adapter names `due_before=None` explicitly at the seam (the board exposes no due-date filter in v1 — §4.4) so a phase-1 keyword drop is caught by the seam rather than by a runtime surprise.
- **B-3 (P1) — §2.2 anchor misattribution corrected.** The draft said "`data_dir` = `create_app` `:483-488`" and "the lifespan CANNOT see `create_app`'s local `data_dir`". **Wrong owner:** `daemon/api.py:483-487` sits **inside the lifespan** (which spans `:450`–`:3217`; `create_app` is at **`:3915`**, i.e. *after* it) ⇒ `data_dir` is a **lifespan local** and is in scope at the `:1685` injection point. The anchor table now states the verified locations, and the spec still reads `getattr(app.state, "data_dir", None)` (same value — the `:493` stash — and it keeps the fail-soft property). D1's anchor list updated to match.
- **B-4 (P1) — `to_thread` at the mint/refresh seams.** `ViewPasscodeService.mint` / `.refresh` are **synchronous** ⇒ the **async** `view_write` / `board_link` bodies MUST wrap them: `await asyncio.to_thread(passcode_service.mint, …)` — **one** crossing around the single sync call (architect §3.3; the A-37 "one `to_thread` around the work, not one per service call" discipline). §3.2 step 8 and §4.6 now state it, and flag the easy-to-miss in-tree difference: `view_link` is a **sync** tool (`def view_link(` — `tools/live_views.py:113`) and calls `mint` directly, which is correct there; the two phase-3 tools are **async** ⇒ the wrap is mandatory.
- **B-6 (P1) — §4.2 de-duplicated into ONE ordered list.** The old steps stated "render the board" **twice** (once inside the grant-cookie step, once as a trailing step), which made the order contradictory. The list is now single-pass: grant valid ⇒ step 6 (the **only** render site); not granted ⇒ step 5 (locked page or interstitial — both return without producing a board body).
- **B-7 (P0, leader ruling) — the same-origin credential dimension + the CSP-sandbox disposition.** §6's threat model now states the raw lane's **host-sharing** exposure explicitly (a `/views/public/**` page shares the browser origin with any other content the viewer is logged into on that host ⇒ a script in a `view_write` page could read that viewer's cookies / `localStorage` and make credentialed same-origin calls to those other apps) — and records the **disposition: ADOPTED**, because the header IS cheap at the existing seam (§6.1: the two raw-serve sites build their header dicts inline; `root_name` is in scope at the direct lane and one keyword parameter away at the public lane ⇒ ≈10 lines in one file). `Content-Security-Policy: sandbox allow-scripts` on the **`reports` root only**: per CSP3 §6.3.2 + HTML §7.1.5 it keeps script execution **on** and declares **no** fetch directive ⇒ **CDN chart.js / mermaid still load and run**, while the page runs in an **opaque origin** ⇒ host cookies, `localStorage` and same-origin reads are closed. The cost (no top-level navigation, forms, popups, downloads, modals, same-origin XHR) is documented per flag. Every other root stays byte-identical; the **board page's strict CSP is explicitly unchanged**. New pin **T-B17**. The residual rides on risk **R3** (no free risk id — R43/R44/R46 are phase-2's, R48/R49 are plan-overview's).
- **B-9 (P1) — the style `nonce` is now an explicit MUST-FIX.** §4.3 adds the binding rules: per-request mint (`new_csp_nonce()` — `services/live_views.py:1672-1680`), the **same** string in the header (`style-src 'nonce-X'`) and in `<style nonce="X">`, interpolation through the ONE `_esc()` helper, a separate placeholder (the template is concatenation / `string.Template`, never `str.format` — A-32), and **no** inline `<script>` at all. Why MUST-FIX: a mismatch makes the browser refuse the `<style>` block ⇒ a **silently unstyled** board that no existing pin would catch ⇒ new pin **T-B16** (header/body nonce parity + two GETs ⇒ different nonces). Architect §5.5 intent, verbatim: the nonce is "injected into both header and `<style nonce>`" (`architecture-recommendation.md:313`).
