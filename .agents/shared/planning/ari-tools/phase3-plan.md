# Phase 3 Plan — Live views for rich content: `view_write` tool + FLAGSHIP server-rendered live board view

Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base `latest@630cefebb`). Anchors verified in THIS checkout, 2026-10-19. Read-only research; implementation-ready.

**Binding user decisions (NOT re-litigated):** live views use the EXISTING passcode-gated public-link mechanism; the board page is SERVER-RENDERED FROM DB (always fresh — never a static HTML file Ari rewrites); build order 1 → 2 → 3. **Pre-ruling:** `view_write` is ARI-ONLY.

---

## Objective

Give Ari the ability to **publish a rich HTML page as a passcode-gated public link in ONE call** (`view_write`), and give the owner a **live, always-fresh view of the work board** — server-rendered from the DB behind the same passcode gate (`/views/public/board/index.html`).

Testable goal: an ari instance calls `view_write(path, html, title)` and receives a working public URL + 6-char passcode for a page that a customer can open with the passcode; an ari/leader instance calls `board_link()` and a customer who opens the board URL and enters the passcode sees the CURRENT board rows (statuses, tags, due dates, notes timeline) rendered from the DB, escaped, zero-JS, on every GET.

## Scope

### In Scope

- The `view_write` tool (deliverable A) + the daemon-global `reports` served root + the `view-write` privileged tool category (ari-only).
- The server-rendered live board view (deliverable B): a static route on the existing `/views` router, a zero-JS HTML template module, a read adapter over phase 1's board service, and the `board_link` minting tool.
- The `board` reserved root name; the read-side board filters; the passcode-grant reuse.

### Out of Scope

- Any change to the passcode CRYPTO, the passcode table, the grant-cookie format or the public-lane verify flow — this phase REUSES them (D4).
- Any proxy / infra change (State A `HTTPRoute`, State B public-prefix tag) — both new surfaces already ride `/views/public/**` (D18).
- A reports-INDEX page (a listing of published pages) — no consumer in this phase; it would also need a title sidecar (D9).
- Serving non-HTML artefacts, and any chart/mermaid rendering pipeline — a `view_write` page may reference CDN scripts itself, exactly like a raw `.html` file served today (D14).
- Markdown rendering of `body_md` — no markdown library exists daemon-side; body text is escaped plain text (D15).
- The `agents/ari/rule.md` / `workflow.md` conventions edit and its adjudicated seal re-pin — that is phase 5 (D19).
- Any DB migration — phase 3 adds no table and no column.

---

## 0. Two deliverables

| # | Deliverable | Surface | Blast radius |
|---|---|---|---|
| A | `view_write(path, html, title, …)` | new tool, new **privileged** category `view-write` (ari-only) | new module + registry/meta edits + 1 lifespan root injection |
| B | Live board view | new static route on the existing `/views` router + `board_link` tool (new privileged category `board-link`) | new route + pages module + read adapter + 1 new reserved root name |

**Reuse-first posture (the point of this phase):** BOTH deliverables ride the EXISTING passcode substrate (`live_view_passcodes` rows keyed `(root_name, rel_path)`, Fernet via `SYSTEM_ENCRYPTION_KEY`, 6-char code, 10-strike lockout, 24h grant cookie) and the EXISTING public-lane gate. **Zero new crypto. Zero new DB tables. Zero proxy change. Zero new passcode concept.** The only new code: one write-side tool, one static route, one HTML template module, one read adapter, one link tool.

**Phase-1 reconciliation** (phase1-plan.md is on disk — read, not depended on): phase 1 lands `daemon/services/board_service.py` (`BoardService` class), `daemon/tools/board_tools.py` (`CATEGORY_MODULES["board"]`), `manager.board_service` (read-only property), and a **NON-privileged** `"board"` category (phase-1 D6/Q1). This plan is reconciled against that surface (§4.5, D13, D16) and deliberately avoids colliding with it (§2.5, D13).

---

## Task checklist (build order 1 → 2 → 3, per the binding decision)

**Step 1 — storage + service seams (no user-visible surface)**

- [ ] **T1** — Add `root_path(root_name)` + `public_base()` to `LiveViewsService` (`daemon/services/live_views.py`). *Acceptance:* `root_path("reports")` returns the configured absolute dir; `root_path` on a non-filesystem/unknown root returns None; `public_base()` returns `''` when path-relative and the resolved base otherwise; unit tests green.
- [ ] **T2** — Reserve the `board` root name (`RESERVED_ROOT_NAMES` → `{"public", "board"}`). *Acceptance:* a `config.yaml` root named `board` refuses to boot with an actionable message; `view_link("board", …)` returns the reserved-name envelope; `/views/board/index.html` 404s.
- [ ] **T3** — Inject the `reports` root in the lifespan (`daemon/api.py`, before the `LiveViewsService` construction at `:1685`). *Acceptance:* a default boot registers `reports` → `<data_dir>/reports` with `allowed_extensions=["html"]`; an operator-declared `reports` entry is NOT overridden; a missing `app.state.data_dir` leaves the root absent (fail-soft, no boot failure).

**Step 2 — deliverable A (`view_write`)**

- [ ] **T4** — Build `daemon/tools/view_write.py`: `ViewWriteInput` + `@register_tool_category("view-write")` above `@tool(args_schema=…)` + one-line docstring + `_full_doc_` + `CATEGORY_NAME`/`CATEGORY_DOC` + the `create_view_write_tools` factory. *Acceptance:* the tool writes an HTML page under `<data_dir>/reports/`, mints the passcode row, and returns the 8-line envelope; every failure path returns a typed `Error: …` string and never raises.
- [ ] **T5** — Register the tool: `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` + `CATEGORY_MODULES["view-write"]` + `PRIVILEGED_TOOL_CATEGORIES` (+ `board-link`, §2.5) + the construction extend in `create_instance_tools`. *Acceptance:* the tool is produced by `create_instance_tools`; the five SAME-PR exact-set pins are updated in the SAME commit and green.
- [ ] **T6** — Allow-list the categories: `"view-write"` + `"board-link"` on ari; `"board-link"` only on leader. *Acceptance:* ari resolves `view_write`; leader/designer/worker/tester resolve NEITHER `view_write` nor the privileged strip keeps it away from the empty-allow universe; boot emits no "unknown category" warning.

**Step 3 — deliverable B (the board) → the flagship**

- [ ] **T7** — Build `daemon/services/board_view_service.py` (the read adapter over phase 1's `BoardService`) and `daemon/services/board_view_pages.py` (the zero-JS template). *Acceptance:* the adapter returns `BoardItemView` rows with hydrated notes; an unavailable phase-1 service renders the "board backend unavailable" notice, never a 500; the rendered page escapes every user-derived string and carries the strict CSP header set.
- [ ] **T8** — Add the static board GET + HEAD route on the `/views` router, declared BEFORE the public catch-all GET (`:1744`), with the board's gate mirroring `_public_resolve` minus the file resolution. *Acceptance:* `/views/public/board/index.html` hits the board route (T-B1); an unminted row returns the uniform 404 (T-B2); the existing catch-all POST performs the verify (303 + grant cookie → board renders) with no change to that handler.
- [ ] **T9** — Build `daemon/tools/board_link.py` (category `board-link`) + register it. *Acceptance:* `board_link()` mints `(board, "index.html")` and returns the 5-line shape; `refresh=True` bumps the generation; the row is not mintable through `view_link`.
- [ ] **T10** — Write the test suites (A/B/C below) and keep the regression suite green. *Acceptance:* every T-A*/T-B*/T-BL* id passes; `tests/unit/routers/test_live_views_public_lane.py` and the other live-views suites are unchanged-green.

Each step is independently shippable and independently revertible.

## Coupling

- **Tight with Phase 1 (board)** — the read seam. This phase consumes phase 1's `daemon/services/board_service.py` (`BoardService.list_items(status, project_id, tag, limit) -> {"items": [...], "count": N}` and `BoardService.get_item(item_id) -> item + notes[]`), reached via `manager.board_service`; the item projection it returns is the pinned schema. The contract is §4.5 and the coupling lives in exactly ONE file (`daemon/services/board_view_service.py`) — a phase-1 rename/move costs one import line (R4, OQ-1). This phase adds NO row to phase 1's `CATEGORY_MODULES["board"]` and NO file to `daemon/tools/board_tools.py` (D13) ⇒ no textual merge conflict there.
- **Loose with Phase 2 (`self_alarm`)** — phase 2 consumes only the pinned `wi-<uuid8>` id format and touches no phase-1 code; this phase likewise touches no phase-2 code. The shared surface is the **board item id**: a `self_alarm` that links a board item, and a board page that renders the linked item's notes timeline, must agree on the id + note shape. No code coupling; a semantic coupling only.
- **Loose with Phase 5 (agents/ari conventions)** — phase 5 edits `agents/ari/rule.md` / `workflow.md` and owns the adjudicated seal re-pin. This phase edits `agents/ari/meta.json` only (D19) ⇒ the two must be sequenced so phase 5's re-pin covers the post-phase-3 tree.
- **Independent of** — the proxy/infra layer (State A `HTTPRoute`, State B public-prefix tag) and the promotion ceremony: **zero proxy change** (D18), and this phase performs **no promote** (the user's 3-factor nonce ceremony owns it).

---

## 1. New-file map

| New file | Purpose | Lines (est) |
|---|---|---|
| `daemon/tools/view_write.py` | the `view_write` tool (deliverable A) + `CATEGORY_NAME`/`CATEGORY_DOC` | ~300 |
| `daemon/tools/board_link.py` | the `board_link` tool (deliverable B, own module ⇒ no conflict with phase 1) | ~150 |
| `daemon/services/board_view_service.py` | board READ adapter (cross-phase seam) + `BoardItemView` | ~170 |
| `daemon/services/board_view_pages.py` | zero-JS board HTML templates (inline strings + `str.format`, no Jinja) | ~250 |
| `tests/unit/tools/test_view_write_tool.py` | deliverable-A unit tests | ~250 |
| `tests/unit/tools/test_board_link_tool.py` | board_link unit tests | ~140 |
| `tests/unit/routers/test_live_views_board.py` | deliverable-B route/gate/render tests | ~320 |

**No migration file.** Phase 3 adds no table and no column (`live_view_passcodes` already exists — `daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql:137-150`). The board tables are PHASE 1's; phase 3 only READS them.

---

## 2. Edit-site checklist (every anchor)

### 2.1 `daemon/config.py` — **NO EDIT**

The `reports` root is auto-injected by the lifespan (D1), so no `config.yaml` / seed / `LiveViewsRootConfig` change is needed. The `type` literal stays `filesystem | project_scoped | tmp_images` (`config.py:2700`) — `reports` is a plain `filesystem` root at an absolute path. The operator-facing `config.yaml` comment block (`config.yaml:155-186`) gets a **doc-only** note: the `reports` root is daemon-managed, do not declare it.

### 2.2 `daemon/api.py` (lifespan) — the `reports` root injection

Anchors: `lifespan` = `daemon/api.py:449-450`; live-views wiring = `:1637-1701`; `data_dir` = `create_app` `:483-488`, stashed at `app.state.data_dir` `:493`. **The lifespan CANNOT see `create_app`'s local `data_dir`** — it MUST read `getattr(app.state, "data_dir", None)`.

Edit immediately BEFORE `live_views_service = LiveViewsService(config=config.live_views, …)` (`:1685`); `LiveViewsRootConfig` must be imported there (today only `HostRecorder, LiveViewsService` are imported at `:1637`):

```python
# PHASE 3: the ``reports`` root is DAEMON-INJECTED, not operator-declared (D1).
if "reports" not in config.live_views.roots:
    _data_dir = getattr(app.state, "data_dir", None)   # api.py:493
    if _data_dir is not None:
        config.live_views.roots["reports"] = LiveViewsRootConfig(
            type="filesystem",
            path=str(Path(_data_dir) / "reports"),
            allowed_extensions=["html"],
            description="Agent-written report pages (view_write). Daemon-injected.",
        )
```

**Fail-soft:** if `app.state.data_dir` is missing, do NOT inject ⇒ `view_write` returns `Error: reports storage is not configured (no daemon data_dir)`. Never boot-fail over a root.

### 2.3 `daemon/services/live_views.py` — 3 edits

1. **Reserved names:** `RESERVED_ROOT_NAMES = frozenset({"public"})` (`:141`) → `frozenset({"public", "board"})`; the docstring above it (`:126-140`) gains a "board" sentence.
2. **New public accessor `root_path(root_name) -> Path | None`** — the ABSOLUTE directory of a registered `filesystem` root, else None (used by `view_write` to find the reports dir).
3. **New public accessor `public_base() -> str`** — the resolved public base prefix (`''` when path-relative), i.e. exactly what `build_public_url` uses internally (`:1286-1289`) (used by `board_link`, because `board` is reserved and `build_public_url` refuses it).

```python
def root_path(self, root_name: str) -> Path | None:
    """Return the ABSOLUTE directory of a registered filesystem root, else None."""
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

### 2.5 `daemon/tools/_tool_registry.py`

- `DYNAMIC_TOOL_NAMES` (`:23-179`): add `"view_write"` and `"board_link"`.
- `KNOWN_TOOL_NAMES` (`:1150` area, next to `"view_link"`): add both.
- `PRIVILEGED_TOOL_CATEGORIES` (`:239-338`): add `"view-write"` **and** `"board-link"`. **SAME-PR FIVE-PIN RULE applies** (`:215-238`) — all five exact-set pins MUST be updated in the SAME commit (both categories land together ⇒ one pin update): ① `tests/unit/tools/test_upgrade_registration.py` ② `tests/unit/tools/test_attestation_registration.py` ③ `tests/integration/test_maintenancer_spawn_resolves_tools.py` ④ `tests/integration/test_service_tool_flag_off_byte_identical.py::test_privileged_category_set_is_pinned` ⑤ `tests/unit/tools/test_secrets_registration.py:218`.
- `CATEGORY_MODULES` (`:736-764`): add `"view-write": "daemon.tools.view_write"` and `"board-link": "daemon.tools.board_link"`. **Zero collision with phase 1** — phase 1 owns `"board": "daemon.tools.board_tools"`; this phase adds no row to that category and no file to that module (D13). Phase 1 does NOT touch `PRIVILEGED_TOOL_CATEGORIES` (its `"board"` category is non-privileged) ⇒ no cross-phase conflict on the privileged set either — verify at merge.
- `KNOWN_TOOL_NAMES` carries a bidirectional drift pin (frozen-binary name discovery) — the pin test compares against the pure source-`@tool` universe.

### 2.6 `daemon/tools/instance.py` — construction (the 3rd registration step)

Imports next to `from .live_views import create_live_view_tools` (`:241`): `from .view_write import create_view_write_tools`, `from .board_link import create_board_link_tools`. Extend after the live-view extend (`:5859-5872`):

```python
    # ── view_write (Phase 3, ari-tools) — report-page WRITER. Privileged category
    # ``view-write`` (ARI-only): writes an HTML page under the daemon-global
    # ``reports`` root, returns the passcode-gated public URL in ONE call.
    # Registration is the THREE-step seam (decorator + DYNAMIC_TOOL_NAMES + this
    # construction) — decorator-only is SILENTLY INVISIBLE.
    tools.extend(create_view_write_tools(manager, current_instance_id))
    # ── board_link (Phase 3, ari-tools) — mints the passcode-gated public URL for
    # the LIVE BOARD. Privileged category ``board-link`` (ari + leader). Separate
    # module + separate category ⇒ no coupling to the phase-1 ``board`` category.
    tools.extend(create_board_link_tools(manager, current_instance_id))
```

### 2.7 `agents/ari/meta.json` / `agents/leader/meta.json`

- `agents/ari/meta.json` `tools.allow`: add `"view-write"` and `"board-link"`.
- `agents/leader/meta.json` `tools.allow`: add `"board-link"` ONLY (leader MUST NOT get `view_write` — D3).
- ari's `tools.deny` keeps `write_file`/`edit_file` — `view_write` is the sanctioned way for ari to publish a page (ari has no general file-write tool; that is WHY this tool exists).
- **NO seal re-pin needed.** `agents/.prompt-artifacts.sha256` (161 entries) covers `.md` artifacts only — `grep -c 'meta.json'` = **0**. `SEALED_SHA_BASELINES` (`tests/test_chart_image_delivery_e2e.py:1350-1420`) contain `agents/ari/rule.md` + `agents/ari/workflow.md` (untouched here) and no daemon router/tool/config file. **Phase 3 touches zero sealed artifacts.** Phase 1 also edits `agents/ari/meta.json` ⇒ both append at the END of the array (phase-1 D15 discipline) to avoid a textual collision.

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

Factory `create_view_write_tools(manager, current_instance_id) -> list` (closure-factory pattern, `daemon/tools/scheduling.py:62-91`). `@register_tool_category` sits **ABOVE** `@tool` (`_tool_registry.py:342-367`); one-line docstring + a `_full_doc_` attribute (`scheduling.py:267-294`); module-level `CATEGORY_NAME` + `CATEGORY_DOC`.

### 3.2 Behaviour (ordered)

1. `manager.live_views_service` None ⇒ `Error: live-views service not initialized …`; disabled ⇒ `Error: live-views subsystem is disabled`; `service.root_path("reports")` None ⇒ `Error: reports storage is not configured …`.
2. `path` non-empty; `html` non-empty; `title` 1–200 chars.
3. **Path normalisation:** strip a leading `/`; reject `..` segments, backslashes, control chars (C0+DEL+C1), null bytes — **reuse `is_well_formed_rel_path`** (`daemon/services/live_views.py:251-296+`) so the write path and the serve path share ONE validator. No extension ⇒ append `.html`; extension ≠ `html` ⇒ `Error: only .html pages can be written to the reports root`.
4. **Containment:** resolve `(root / rel).resolve()` and assert `relative_to(root.resolve())` — mirrors `_resolve_under_root` (`:1147-1171`); closes the symlink-escape class at WRITE time.
5. **Size:** `len(html.encode("utf-8")) > MAX_SERVED_BYTES` (32 MiB, `:166`) ⇒ `Error: page is {n} bytes — the served-page cap is 32 MiB (33554432 bytes)`. A page that cannot be served must never be written.
6. **Existing file:** target exists and `overwrite=False` ⇒ `Error: {rel} already exists — pass overwrite=True to replace it`.
7. **Write:** `mkdir(parents=True, exist_ok=True)`; temp file in the SAME directory (`tempfile.mkstemp(dir=parent)`) → `os.replace(tmp, target)` (atomic; tmp_image_store precedent, `daemon/services/tmp_image_store.py:395-406`); mode `0o600`. No torn reads.
8. **Passcode:** `passcode_service.refresh(root, rel, actor)` when `refresh=True`, else `passcode_service.mint(root, rel, actor)` (idempotent). **Reuse, never re-implement crypto** — the exact seam `view_link` uses (`daemon/tools/live_views.py:295-335`), including the `ViewPasscodeRotationRequired` / `ViewPasscodeNotFoundError` typed envelopes (`:336-369`).
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

Declared before the public catch-all (mount-order anchor §2.4). The gate = `_public_resolve` (`routers/live_views.py:1271-1392`) MINUS the file resolution, PLUS the board render:

1. `service = _resolve_service(request)`; None or disabled ⇒ `_uniform_404()`.
2. `passcode_service = _resolve_passcode_service(request)`; None ⇒ `_uniform_404()`.
3. `row = passcode_service.get_row("board", "index.html")`; None ⇒ `_uniform_404()` (**GET never mints** — the ADR T7 write-amplification pin).
4. Grant cookie: `parse_grant_cookie(cipher=…, token=request.cookies.get("view_grant"), expected_root="board", expected_rel="index.html", expected_generation=_row_generation(row))` ⇒ valid ⇒ render the board; any failure ⇒ fall through (log-only, no audit row — same posture as `:1358-1372`).
5. Locked? ⇒ `_render_locked_page(request)` (HTTP 200 locked notice; the only recovery is `board_link(refresh=True)`). Otherwise ⇒ `_render_interstitial_page(request, "board", "index.html")` — the EXISTING interstitial reused verbatim (same `code` field, same-URL form action, same CSP); its copy says "view this document" for a board — accepted cosmetic deviation (OQ-3).
6. Post-grant ⇒ render the board from the DB.

The board POST (passcode verify) is **the existing catch-all POST at `:1756`** — untouched. It looks the row up by `(root, rel)`, applies the per-IP bucket, the lock short-circuit, the same-host CSRF check, the constant-time compare, the audit row, the grant-cookie issuance and the 303 to the same URL. The 303 `Location` = `/views/public/board/index.html` (+ query) → lands on the static board GET → the grant cookie (Path = the exact queryless path, `_grant_cookie_path` `:791`) is presented → the board renders. **Zero new gate code.** *The catch-all POST does not consult `is_reserved_root_name`; that is precisely what lets the reserved `board` name work — a load-bearing accident ⇒ **regression pin T-B7**.*

### 4.3 Rendering

- New module `daemon/services/board_view_pages.py` with `render_board(*, csp_nonce, items, filter_options, active_filters, counts, generated_at) -> str` — inline-string template + `str.format` (NO Jinja — the tree has no Jinja; the precedent is `daemon/services/view_passcode_pages.py:38-42`).
- Reuses `page_headers(csp_nonce)` (`view_passcode_pages.py:68-89`): `Content-Security-Policy: default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` + `nosniff` + `Cache-Control: no-store` + `Referrer-Policy: no-referrer`. `no-store` ⇒ **always fresh**, satisfying the binding "server-rendered from DB, never a static file".
- Layout: title + generated-at + a zero-JS **GET filter form** (status `<select>`, project `<select>`, tag `<input>`) + a status-count row + one table (Status badge, Priority, Title, Tags, Project, Due, Updated, Notes count). Each row's body + notes timeline live in a `<details><summary>` block — **pure HTML, no JS**.
- `body_md` is **escaped plain text inside `<pre>`** — NO markdown renderer exists daemon-side (`pyproject.toml` has no markdown/mistune/marko dependency; the `.md` wrapper loads `marked` from a CDN *client-side*, `routers/live_views.py:153-227`). Server-side rendering of agent-authored markdown without a sanitiser would be an XSS hole; a `<pre>` is honest and safe.
- **Every** user-derived string passes through `html.escape()`: titles, tags, project ids, note text, author instance ids, dates, filter echoes. The skeleton is ours.

### 4.4 Filters

Query params (zero-JS form, `method="get"`, action = the board URL): `?status=todo|in_progress|blocked|done` · `?project=<project_id>` · `?tag=<tag>`.

- **Filters are VIEW concerns, NOT access control** (D11). A passcode holder sees the WHOLE board; a filter narrows the display, never the disclosure. Stated in the page footer and here so nobody mistakes a filter for an ACL.
- The filters are pushed **DOWN** to phase 1's service (it natively supports `status` / `project_id` / `tag` — phase1-plan §5.2 `list_items`). ONE additional unfiltered `list_items(limit=…)` call derives the project/tag `<select>` options from the same bounded set (two bounded calls, no new seam surface).

### 4.5 The board read seam (cross-phase contract, reconciled with phase 1)

`daemon/services/board_view_service.py` (phase 3 owns) exposes `BOARD_VIEW_ROOT = "board"`, `BOARD_VIEW_REL = "index.html"`, a `BoardItemView` dataclass (`id, title, body_md, status, tags[], project_id, due_at, linked_job_ids[], created_by_instance, created_at, updated_at, priority, notes[]`), and two functions: `fetch_board_items(*, status=None, project_id=None, tag=None, limit=50) -> list[BoardItemView]` and `fetch_filter_options(*, limit=500) -> dict` (`{"projects": [...], "tags": [...]}`).

- The seam is phase 1's `BoardService` (phase1-plan §5.2/§5.3), reached via `request.app.state.manager.board_service` (`app.state.manager` is set in the lifespan at `daemon/api.py:2271`; available at REQUEST time).
- **Call shape (reconciled with phase 1):** `board_service.list_items(status=…, project_id=…, tag=…, limit=…)` returns `{"items": [...], "count": N}` — each item WITHOUT notes (phase-1 §5.2 projection); `board_service.get_item(item_id=…)` returns the item WITH `notes[]` (oldest-first). ⇒ **Notes are hydrated per rendered row via `get_item`** (bounded N+1, capped at the render limit — default 50 rows); a `get_item` failure degrades that row to "notes unavailable", never a 500. (OQ-1 asks phase 1 whether the list projection can cheaply include notes — if it can, the N+1 disappears; the adapter tolerates both shapes.)
- Phase 1's service is **synchronous** (sync `Engine`) ⇒ the async route MUST call it through `await asyncio.to_thread(...)` (house pattern, `daemon/tools/scheduling.py:206-216`). The adapter is shape-tolerant (`getattr(obj, "x", None)` OR `obj["x"]`) — the in-tree precedent is `_row_locked` / `_row_generation` (`routers/live_views.py:897-920`).
- **This adapter is the ONLY place that knows phase 1's surface** — a phase-1 rename/move costs one import line (R4 / OQ-1). If the service is unavailable at request time, the board renders an explicit "board backend unavailable" notice (HTTP 200, zero-JS) — **never a 500**.

### 4.6 `board_link` tool (the board's only minting path)

- New module `daemon/tools/board_link.py`, category `"board-link"` (ari + leader). Signature: `board_link(refresh: bool = False) -> str`.
- Returns the same 5-line shape as `view_link`: `Public URL: <base>/views/public/board/index.html` · `Passcode: …` · `Authenticated URL: <base>/views/board/index.html` (informational — the direct lane 404s; the board is DB-rendered) · `Generation: n` · `Locked: no`.
- Mint seam = `passcode_service.mint("board", "index.html", actor)` / `.refresh(...)` — the same reuse as `view_link` (`tools/live_views.py:295-335`).
- URL base = `service.public_base()` + `PUBLIC_NAMESPACE` (D17) — `build_public_url` cannot be used because `board` is reserved.

---

## 5. Storage decision (deliverable A) — plain `.html` files, NOT blob+sidecar

**DECISION: `<data_dir>/reports/<rel>.html` as plain files.** (1) **The served artefact IS an HTML file.** The `filesystem` root resolver already serves it with the right MIME (`_EXT_TO_MIME["html"] = "text/html; charset=utf-8"`, `services/live_views.py:174-186`), the extension allowlist gate (`_resolve_under_root` `:1173-1179`), the containment check (`:1147-1171`) and the 32 MiB cap (`:1185-1189`). **Zero new serving code.** (2) **The blob+sidecar substrate solves a different problem.** `tmp_images` stores extensionless BLOBS with a MIME/provenance sidecar because the payloads are opaque binaries whose type cannot be inferred from the name (`services/tmp_image_store.py:14-21, :76-90`). An HTML page's type IS its extension; a sidecar would duplicate state the filesystem already carries and would need a NEW root type (or a resolver branch) — a substrate re-implementation for one file type. (3) **Operability.** A report page is meant to be read, diffed, grepped and hand-edited by an operator; the tmp-images substrate is deliberately opaque. (4) **Retention.** tmp_images carries a 30-day sweep + a byte cap + retention classes (`:86-90, :143-146`); a published report page must PERSIST until the operator removes it — a silent 30-day disappearance of a "published" link would be a product bug.

**Rejected:** blob+sidecar (over-engineered, wrong substrate); per-project workdir storage (pre-ruled: daemon-side GLOBAL dir — a report is not a project artefact and must survive a project's absence); a new `reports` root TYPE (a new `LiveViewsRootConfig.type` literal + resolver branch for zero benefit over `filesystem`).

**Path rules (write time):** relative, no leading `/`, no `..` segments, no backslashes, no control chars (reuse `is_well_formed_rel_path`), `.html` extension enforced, resolved target contained under the reports dir, ≤ 32 MiB. **Overwrite:** refuse by default (`overwrite=False` ⇒ typed error); `overwrite=True` ⇒ atomic replace, **generation NOT bumped** (D7); `refresh=True` is the separate, explicit generation-bump / lockout-recovery path. **Title:** required (1–200 chars), echoed, **not persisted** and **not injected** into the HTML (D9); a `Note:` line advises when the document lacks a `<title>`.

---

## 6. Security posture (documented, per the commission's explicit ask)

**What `/views` sends today** (`routers/live_views.py:122-140`, ruling quoted at `:176-179`): raw files (incl. `.html`) → `X-Content-Type-Options: nosniff` + `Cache-Control: private, max-age=60`. **NO CSP, NO X-Frame-Options** — a DELIBERATE architect ruling: "legitimate HTML may include scripts". Consequence: **CDN chart.js / mermaid in report pages already work today** and keep working for `view_write` pages (the `reports` root is a `filesystem` root ⇒ same serving path, same headers). **Nothing changes structurally for `reports`.** Markdown wrapper pages → per-response nonce'd CSP with pinned-CDN `marked` + DOMPurify. Passcode pages **and the NEW board page** → `default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`, zero-JS.

**The split posture this phase adopts:** `view_write` pages (agent-authored HTML) keep the raw-lane posture — nosniff + `private,max-age=60`, no CSP, CDN chart/mermaid allowed (existing, architect-ruled; phase 3 does not change it). The **board page** (DB-derived, server-rendered) takes the STRICT posture — `script-src 'none'`, `default-src 'none'`, nonce'd style, no CDN, no external subresource of any kind: the board renders agent-authored titles/bodies/notes, so even though every value is escaped, a strict CSP means a future escaping bug CANNOT execute script; the board needs no CDN (it is a table), so the strict posture costs nothing.

**Threat model, stated honestly:**

- The `reports` surface is writable ONLY by ari (`view-write` is privileged + ari-only) ⇒ the XSS blast radius of a `view_write` page is "ari can publish a page that runs script in a viewer's browser". Inherent to the product decision ("ari writes rich HTML reports"), accepted by the architect ruling.
- Readership is gated by a 6-char passcode + a 10-strike lockout + a 24h grant cookie. The passcode is low-entropy (6 chars ≈ 30 bits) ⇒ **online guessing is what the lockout + the per-IP bucket (5 burst / 5 per minute) defend against**; a distributed grinding attack across IPs is slowed, not stopped. The board's content is internal work items — the passcode is the only barrier. Accepted posture of the EXISTING mechanism; the board inherits it unchanged.
- The `reports` DIRECT lane (`/views/reports/…`) is behind the OAuth proxy / in-house auth (State A / B) — NOT public. Only `/views/public/**` is proxy-exempt, and it is daemon-side gated.
- `view_write` NEVER creates a passcode row outside `/views/public/**`, and the board route is mounted under `/views/public/**` only. No gate-free route is added. The proxy contract (`docs/runbooks/live-view-passcode-deployment.md` §1.1 — the public namespace is EXACTLY `/views/public`) is preserved: **zero proxy change**, and Host preservation is untouched (the same-host CSRF check depends on it, `routers/live_views.py:721`).

---

## 7. Tests

**Mirror these in-tree files:** `tests/unit/routers/test_live_views_public_lane.py` (the public gate matrix), `tests/unit/routers/test_live_views_router.py`, `tests/unit/tools/test_live_views_tool.py`, `tests/unit/services/test_live_views_service.py`, `tests/unit/services/test_view_passcode_service.py`, `tests/migration/test_view_passcode_index_parity.py`.

**A — `tests/unit/tools/test_view_write_tool.py`**

T-A1 writes a page ⇒ the file exists under the reports dir; the return carries all 8 lines. T-A2 the return shape is `view_link`-compatible (first five labels byte-match). T-A3 path traversal (`../x`, `a/../../b`, leading `/`, backslash, control char, null byte) ⇒ typed `Error:`, NO file written. T-A4 extension gate (`page.txt` ⇒ error; `page` ⇒ `.html` appended). T-A5 the 32 MiB cap (cap+1 ⇒ error naming the limit; exactly-at-cap ⇒ ok). T-A6 overwrite (existing path + `overwrite=False` ⇒ error, original bytes intact; + `overwrite=True` ⇒ replaced, generation UNCHANGED, passcode unchanged). T-A7 `refresh=True` ⇒ generation+1 + a new passcode. T-A8 privilege pins: `view-write` ∈ `PRIVILEGED_TOOL_CATEGORIES`; the five SAME-PR pins updated; ari gets the tool, leader/designer/worker do NOT (assert via `resolve_tool_filter` + `_strip_privileged_category_tools`). T-A9 registration completeness: `view_write` ∈ `DYNAMIC_TOOL_NAMES` ∩ `KNOWN_TOOL_NAMES`; `CATEGORY_MODULES["view-write"]` resolves; `create_instance_tools` produces the tool. T-A10 the reports root is auto-injected by the lifespan when absent and NOT overridden when the operator declares it.

**B — `tests/unit/tools/test_board_link_tool.py`**

T-BL1 mints `(board, index.html)`; the 5-line shape; the URL is `/views/public/board/index.html`. T-BL2 `refresh=True` ⇒ generation+1 + a new passcode. T-BL3 the row is NOT mintable through `view_link` (reserved name). T-BL4 privilege: `board-link` privileged; ari + leader get it, others do not.

**C — `tests/unit/routers/test_live_views_board.py`**

T-B1 **mount order** — `/views/public/board/index.html` matches the BOARD route, not the catch-all (assert the response is the interstitial/board, not the uniform 404). T-B2 **no row ⇒ uniform 404** (GET never mints). T-B3 no cookie ⇒ the interstitial (200, `script-src 'none'`, `form-action 'self'`, `no-store`, form action = the board URL, field name `code`). T-B4 the right passcode via the EXISTING catch-all POST ⇒ 303 + `Set-Cookie` grant cookie + `Location: /views/public/board/index.html` ⇒ follow ⇒ the board renders. T-B5 wrong passcode ⇒ failure page + strike; the 10th wrong ⇒ locked page. T-B6 a grant cookie for a different rel / a stale generation ⇒ interstitial + cookie clear. T-B7 **regression pin (load-bearing)** — the board's POST verify still works with `board` in `RESERVED_ROOT_NAMES` (the catch-all POST does not consult the reserved list); a future reserved-name check there would silently break the board — this pin fails loudly instead. T-B8 **escaping** — an item titled `<script>alert(1)</script>` and a note containing `"><img src=x onerror=…>` appear ESCAPED; the raw payload is NOT in the body. T-B9 **CSP** — the strict header set is present and the body contains NO external subresource URL (no `https://`). T-B10 **filters** — `?status=in_progress` renders only in-progress items; `?tag=` and `?project=` narrow; the filter form's action is the board URL; unknown filter values are ignored (no 500). T-B11 **freshness** — two GETs after a DB change render different content (no caching). T-B12 **direct lane** — `/views/board/index.html` ⇒ uniform 404. T-B13 **reserved-name boot refusal** — a config root named `board` refuses to boot (mirrors the `public` pin). T-B14 **notes timeline** — a row's notes render oldest-first inside its `<details>`.

**D — regression suite (must stay green)** `tests/unit/routers/test_live_views_public_lane.py` (the whole matrix — the board must not perturb the existing gate), `test_live_views_router.py`, `tests/unit/tools/test_live_views_tool.py`, `tests/unit/services/test_view_passcode_service.py`, `tests/integration/test_auth_public_lane_tag.py` (the public-prefix exemption is unchanged), the five SAME-PR pin files, and phase 1's `tests/unit/test_board_registration.py`.

---

## 8. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Mount order** — the board route declared below the catch-all ⇒ the board silently 404s (the catch-all resolves `board` as a file). | HIGH | Loud comment at the declaration + T-B1 pins the resolution + the Group-A route-order assertion pattern |
| R2 | **Config-replace footgun** — an operator `live_views.roots:` block replaces the seed dict (`config.py:2903-2914`) ⇒ a config-declared `reports` root would vanish. | HIGH | **The `reports` root is LIFESPAN-INJECTED, never config-declared** (D1) ⇒ the footgun cannot bite. `config.yaml` gets a doc-only note. |
| R3 | **CSP posture drift** — someone adds a CDN `<script>` to the board template "for a nicer chart". | MED | The board template ships zero external subresources; T-B9 asserts no `https://` in the body + the strict CSP header set |
| R4 | **Cross-phase seam drift** (phase 1 renames/moves the service or changes the item shape). | HIGH | One adapter file is the ONLY coupling point; it is shape-tolerant and degrades to an explicit "unavailable" notice, never a 500. **The phase-3 builder MUST reconcile against the landed phase-1 code before merging.** |
| R5 | **Category/module conflict on `"board"`** | LOW | **Eliminated by design:** `board_link` gets its OWN module + its OWN category (`board-link`), so phase 1's `"board": "daemon.tools.board_tools"` is untouched (D13) |
| R6 | **Privileged-category pin blast** — adding `view-write` + `board-link` trips the SAME-PR five exact-set pins. | MED | All five pins updated in the SAME commit (§2.5) |
| R7 | **Passcode lockout UX** — 10 strikes locks the board/report; recovery = `refresh=True`. The locked notice carries no form and no remaining-attempts hint. | LOW | Documented in both tool docstrings; `refresh=True` is the single documented recovery (mirrors `view_link`) |
| R8 | **The 32 MiB write cap and the served cap must be the same constant.** | LOW | The tool imports `MAX_SERVED_BYTES` from `daemon.services.live_views` (one source of truth) |
| R9 | **Operator has a root named `board`** ⇒ boot refusal after upgrade. | MED | Documented migration note; the validator's message names the offending root and suggests a rename (the `public` precedent, `config.py:3138-3145`) |
| R10 | **Served-page staleness for `view_write`** — `private, max-age=60` ⇒ a reader may see the old page for up to 60 s after an overwrite. | LOW | Accepted (existing lane behaviour); the board is `no-store` and always fresh |
| R11 | **Notes N+1** — `list_items` omits notes; the board hydrates them per row via `get_item` (≤ 50 rows ⇒ ≤ 50 bounded reads per render). | LOW | Bounded + degraded per row; OQ-1 asks phase 1 whether the list projection can include notes |
| R12 | **Phase-1 merge collision on `agents/ari/meta.json`** (both phases append to `tools.allow`). | LOW | Both append at the END of the array (phase-1 D15 discipline); the allow-list pin tests are per-phase |

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
| 7 | `view_write` is ari-only | resolve the toolset for ari / leader / designer / worker / tester | only ari resolves `view_write`; the privileged strip keeps it from the empty-allow universe (T-A8) |
| 8 | The board gate leaks nothing | GET the board URL with no minted row | the uniform 404 envelope — identical to any other miss (T-B2) |
| 9 | The shared gate is unregressed | the existing live-views suites | `test_live_views_public_lane.py` + `test_live_views_router.py` + `test_live_views_tool.py` + `test_view_passcode_service.py` green, unchanged |
| 10 | No seal, proxy or migration impact | diff the tree | zero files from `agents/.prompt-artifacts.sha256` / `SEALED_SHA_BASELINES`; zero proxy/infra files; zero new migration files |
| 11 | The board page stays bounded | time a board render at the 50-row ceiling | ≤ 50 rows + ≤ 50 note reads, < 2 s locally (bounded N+1, R11) |

---

## 10. Open questions for the architect

- **OQ-1 — phase-1 list projection + notes.** Phase 1's `list_items` returns items WITHOUT `notes[]`; `get_item` adds them. Phase 3 tolerates the bounded N+1 (≤ 50 reads/render). **Can phase 1 include `notes[]` in the list projection cheaply?** (If yes, the N+1 disappears.) If phase 1 renames the service/module, the adapter's single import line is the only change.
- **OQ-2 — `board_link` in its own category vs. the phase-1 `"board"` category.** This plan uses a separate privileged category `board-link` (ari+leader) because phase 1's `"board"` is NON-privileged ⇒ default-granted to every empty-allow agent (phase-1 D6/Q1) ⇒ a `board_link` tool there would let any agent mint the internal board's public link. Ratify the separate category, or redirect.
- **OQ-3 — the interstitial copy.** The reused interstitial says "view this document" for a board. (a) accept the cosmetic mismatch (this plan — zero gate risk); (b) add a board-specific interstitial (new template + divergence risk vs. the gate contract); (c) generalise `render_interstitial` with a `subject` kwarg (touches a file used by 4 other paths). Recommend (a).
- **OQ-4 — board layout.** Flat filtered table with a `<details>` per row (this plan) vs. grouped per-project sections. Rendering-only change.
- **OQ-5 — render ceiling.** The board renders ≤ 50 rows and fetches ≤ 500 for the filter options. Phase 1's `list_items` has `limit=50` by default and no cursor (phase-1 Q3). **Is 50/500 right?**
- **OQ-6 — should the board page be the headline link, or the direct (OAuth) lane?** This plan returns both (mirroring `view_link`'s 5-line shape) but the public URL is the headline; the direct lane 404s by design (D20).

---

## 11. Design decisions made in this phase plan (numbered, rationale + anchors)

- **D1 — the `reports` root is LIFESPAN-INJECTED, not operator-declared.** Its path derives from the runtime `data_dir` (`ENSEMBLE_DATA_DIR > DATA_DIR > ./data`, `api.py:483-488`), which a static `config.yaml` cannot express; and an operator `live_views.roots:` block REPLACES the seed dict (`config.py:2903-2914`), so a config-declared root would silently vanish. Injection is idempotent and never overrides an operator-declared `reports` entry. → `api.py:449`, `:493`, `:1685`.
- **D2 — `reports` stores plain `.html` files, NOT a blob+sidecar substrate.** The artefact's type IS its extension; the filesystem root already serves `.html` with the right MIME, allowlist, containment and 32 MiB cap; the tmp_images substrate exists for opaque binaries and carries a 30-day sweep that would make "published" links vanish. → `services/live_views.py:174-186`, `:1147-1196`; `tmp_image_store.py:14-21`.
- **D3 — `view_write` gets a NEW PRIVILEGED category `view-write` (ari-only), NOT a non-privileged one.** ⚠️ **CONFLICT WITH THE COMMISSION HINT, RESOLVED AGAINST IT.** A non-privileged category is **default-granted** to the empty-allow universe (`resolve_tool_filter` returns "all tools" for an empty allow+deny pair, `instance.py:373-434`; the default-deny strip removes only PRIVILEGED categories, `instance.py:6243-6271`) ⇒ it would hand a page-publishing tool to worker/tester/developer, violating the binding "ARI-ONLY" pre-ruling. **The binding pre-ruling wins; the mechanism hint loses.** It is still a NEW category — `view-views` (ari+leader+designer) is untouched ⇒ leader gains no write authority. Cost: the SAME-PR five-pin update (`_tool_registry.py:215-238`).
- **D4 — the passcode substrate is REUSED, never re-implemented.** `view_write` and `board_link` call `ViewPasscodeService.mint/refresh` exactly as `view_link` does (`tools/live_views.py:295-335`) — same lazy-mint semantics, same generation bump, same rotation-fail envelopes. No new crypto, no new table, no new passcode concept.
- **D5 — `view_write` writes only under the daemon-global `reports` root** (pre-ruled; not per-project). Path validation reuses `is_well_formed_rel_path` (`services/live_views.py:251-296+`) so the write path and the serve path share ONE validator, plus a write-time containment check mirroring `_resolve_under_root` (`:1147-1171`).
- **D6 — the 32 MiB cap is enforced at WRITE time** with a typed error naming the limit and the byte count, using the same `MAX_SERVED_BYTES` constant the serve path uses (`:166`, `:1186`). A page that cannot be served must never be written.
- **D7 — overwrite does NOT bump the passcode generation.** ⚠️ **REFINEMENT of the pre-ruling suggestion "same path = overwrite + generation bump?".** The passcode authorises "may this person see THIS page" and the URL is meant to be STABLE; bumping on every refresh would lock a reader out each time Ari updates the report. Bumps are reserved for the explicit `refresh=True` (lockout recovery), keeping `view_write` consistent with the live board (content changes, passcode stable). Overwrite still requires an explicit `overwrite=True` (no silent clobber).
- **D8 — `refresh=True` bumps the generation** (new passcode, outstanding grants revoked) — mirroring `view_link`'s documented behaviour (`tools/live_views.py:153-170`).
- **D9 — `title` is required, echoed, NOT persisted and NOT injected into the HTML.** The tool never mutates the caller's bytes (a served page is byte-for-byte what ari wrote); no consumer reads a stored title in this phase; a `Note:` line advises when the document lacks a `<title>`. A future reports-index feature may add a sidecar then.
- **D10 — `board` becomes a RESERVED root name; it is NOT a registered root.** The board is DB-rendered, not a file. Reserving the name (a) prevents an operator from shadowing it (boot refusal, `config.py:3098-3142`), (b) makes the direct lane 404 (`routers/live_views.py:1163-1166`), (c) keeps the board's link out of the `view-views` (designer-reachable) minting path. → `services/live_views.py:141`.
- **D11 — ONE passcode row for the board: `(board, "index.html")`. Filters are QUERY params, NOT part of the key.** A passcode authorises "may this person see the board", not "may they see a filtered subset". Per-filter rows would explode the row count, violate the "GET never mints" write-amplification pin (ADR T7) and multiply lockouts. Consequence, stated plainly: **a passcode holder sees the WHOLE board**; filters narrow the display, never the disclosure.
- **D12 — the board's GET is a NEW STATIC route declared BEFORE the public catch-all; the board's POST verify REUSES the existing catch-all POST unchanged.** Mount order is load-bearing (Starlette first-match-wins, `routers/live_views.py:1068-1078`); the gate mirrors `_public_resolve` (`:1271-1392`) minus the file resolution; the verify path (bucket → lock → CSRF → compare → audit → cookie → 303) is inherited wholesale — **zero new gate code**. A regression pin (T-B7) guards the reserved-name interaction.
- **D13 — the board's link is minted by a dedicated `board_link` tool in its OWN module (`daemon/tools/board_link.py`) and its OWN privileged category (`board-link`, ari+leader).** `view_link` cannot mint it (reserved) and its category is reachable by designer; phase 1's `"board"` category is non-privileged ⇒ default-granted to every empty-allow agent (phase-1 D6/Q1) ⇒ a board-link tool there would let any agent mint the internal board's public link. A separate module + category also removes the phase-1 merge conflict entirely (R5).
- **D14 — the board page is zero-JS with the STRICT CSP posture; CDN scripts remain a `view_write`-page-only capability.** The board needs no CDN, so the strict posture (`script-src 'none'`, `default-src 'none'`, nonce'd style, `form-action 'self'`, `frame-ancestors 'none'`, `no-store`) is free and means a future escaping bug cannot execute script. The `view_write` pages keep the existing architect-ruled posture (nosniff + `private,max-age=60`, no CSP) so CDN chart/mermaid keeps working — nothing changes structurally for `reports`. → `routers/live_views.py:122-125`, `:176-179`; `view_passcode_pages.py:68-89`.
- **D15 — `body_md` is rendered as ESCAPED PLAIN TEXT (`<pre>`); the notes timeline uses `<details>/<summary>` (pure HTML).** No markdown library exists daemon-side (`pyproject.toml` has none; the `.md` wrapper loads `marked` from a CDN client-side) ⇒ server-side markdown rendering of agent-authored content without a sanitiser would be an XSS hole. → `pyproject.toml:7-50`; `routers/live_views.py:153-227`.
- **D16 — the board read seam is phase 1's `BoardService`, reached via `request.app.state.manager.board_service` (`app.state.manager` set at `api.py:2271`), adapted by a phase-3-owned shape-tolerant adapter (`daemon/services/board_view_service.py`) — the `_row_locked`/`_row_generation` precedent (`routers/live_views.py:897-920`). The adapter is the ONLY place that knows phase 1's surface. The service is synchronous ⇒ `asyncio.to_thread` (`tools/scheduling.py:206-216`). Unavailable ⇒ an explicit "board backend unavailable" notice, never a 500.
- **D17 — two new PUBLIC accessors on `LiveViewsService`** (`root_path`, `public_base`). No new authority: `root_path` returns a configured filesystem root's absolute directory; `public_base` returns the same base string `build_public_url` already uses (`:1286-1289`). No reserved-name guard is bypassed — `board_link` composes its own URL from `PUBLIC_NAMESPACE` (`services/live_views.py:155`).
- **D18 — ZERO proxy change.** Both new surfaces live under `/views/public/**` (`/views/public/reports/**`, `/views/public/board/index.html`), already proxy-exempt in BOTH deployment states and daemon-side gated. No gate-free route outside `/views/public/**`; no passcode row minted outside it; Host preservation untouched (the same-host CSRF check depends on it — `docs/runbooks/live-view-passcode-deployment.md` §1.1; `routers/live_views.py:721`).
- **D19 — NO seal re-pin in this phase.** `agents/.prompt-artifacts.sha256` (161 entries) covers `.md` artifacts only — `meta.json` appears ZERO times in it; `SEALED_SHA_BASELINES` (`tests/test_chart_image_delivery_e2e.py:1350-1420`) contains no daemon router/tool/config file and no `agents/ari/meta.json`. This phase touches `agents/{ari,leader}/meta.json` only — the phase-5 conventions edit (which DOES touch `agents/ari/rule.md` + `workflow.md`) owns the adjudicated re-pin.
- **D20 — the board's direct lane is intentionally 404** and its "Authenticated URL" line is informational only. The board is a DB-rendered public surface; there is no OAuth-guarded variant to point at. Stated in the tool output so no reader is misled.
