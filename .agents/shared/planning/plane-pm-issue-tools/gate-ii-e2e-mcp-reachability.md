# Gate (ii) — `e2e` workspace reachability under the **MCP credential** — lane `plane-pm-issue-tools` Phase 1

**Executed:** 2026-11-02 ~08:14Z · **Probe:** `probe_plane_mcp.py --verb list-projects --workspace e2e --allow-list-projects`
**Authorisation:** leader ruling G0 (2026-11-02) — the single authorised read-only MCP verb `list_projects`. **No write verb was called. No mutating verb exists in the probe.**
**Probe environment:** `/home/nea/ensemble-src` (the armed main checkout — G2-fold B1(f)); a direct JSON-RPC call — **no daemon, no boot, no port**.
**Credential:** `PLANE_MCP_API_KEY` (a **different credential** from the REST `PLANE_API_KEY`) used as the outbound credential only — never echoed, never persisted.

---

## 1. Outcome — PASS

| Probe step | Result |
|---|---|
| `initialize` (slug `e2e`) | **HTTP 200** — credential **accepted** (the dev-context MCP credential is LIVE) |
| `tools/call list_projects` | **HTTP 200**, `isError: false` |
| Result set | **`count: 2`** — exactly the two expected e2e workspace projects |

### Projects returned (identifiers only — no keys, no secret material)

| identifier | name |
|---|---|
| `SYSTEMDEFAUL` | `__system_default__` ("System default project for jobs without an explicit project") |
| `E2E` | `e2e` (the Plane demo project) |

These are **byte-identical to the wave-1 REST-leg results** (`GET {PLANE_BASE_URL}/api/v1/workspaces/e2e/projects/` with `X-Api-Key` → HTTP 200, the same 2 projects) — the REST leg and the MCP leg now agree.

---

## 2. What this proves

- **(a) `e2e` resolves under the MCP credential** ⇒ the **non-live ⇒ `e2e` pin IS satisfiable** — the Phase-2 `e2e`-required policy can ship on evidence, not assumption.
- The MCP credential is **live** (HTTP 200 on both the handshake and the data read) — distinct from the **known-expired live/demo** token (HTTP 401 `invalid_token`; R1/ADJ-4(i), USER-OWNED re-mint).
- `tools/list` alone could NOT have proven this (it is workspace-agnostic — wave 1 got byte-identical 177-name lists for `e2e` and `nea`). The data read was required, and it succeeded.

---

## 3. The `nea`-side read leg — DROPPED (X1.9)

> **G2 fold (approver iter-002, non-blocking item 7): the `nea`-side read is REMOVED from this gate.**

- The pre-fold step 3 (repeat `list_projects` on the `nea` slug from the **dev** main checkout) is **dev→nea traffic** — forbidden by the lane's own fence — and was **unexecutable in any case** (the live/demo token is dead).
- **Disposition: DROPPED (not relocated).** This gate did NOT read `nea` data. No `nea`-scoped MCP verb was called.
- **ADJ-4(ii) ("may one credential serve both lanes?") therefore remains OPEN and USER-OWNED** — it is answered **on the live host, by the user, after the credential re-mint (ADJ-4(i))**, not from dev. **This gate does NOT close ADJ-4(ii) and does not claim to.**

---

## 4. Bound compliance

Listing + one authorised read verb; no write verb; no data mutation; env var **names** and HTTP status codes only — **never values**. The raw capture is retained in the commission scratch (never committed); it contains no secret.
