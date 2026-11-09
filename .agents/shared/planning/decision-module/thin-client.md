# Thin Client — `POST /v1/decisions`

> **Status:** **RATIFIED P1 SHADOW-ONLY — Emperor ratification 2026-11-09: "yeah, look good, go ahead, shadow only for now".** Design of record. P1 implementation authorised (client + registry + shadow instrumentation); seams 2/3/4 unregistered; S4 shadow-only. (The leader's giter commits at finalize.)
> **Scope item:** 1 of 7 (the client). **Normative source:** `scope-and-doctrine.md` — this doc cites it by §id and re-quotes nothing.
> **Base:** `docs/decision-module` @ `5da1021f2`

---

## §1 What the client is — and is not

The client is **one HTTP transport + one typed mapper + one fail-open wrapper**. It **asks**; it never **decides**.

| The client IS | The client is NOT |
|---|---|
| A single-attempt POST to the proxy decision surface | A decision engine, scorer, ranker, or classifier |
| A typed request builder (`predicate` / `choice` / `score`) | A retry loop, backoff engine, or failover client (PX-6, IP-5) |
| A typed response reader with structural validation | A cache, a queue, or an async submit/poll job system |
| A **fail-open** wrapper: every failure returns the heuristic result | A fail-closed validator like `image_gen` (IP-3) |
| Cheap to call, disposable to discard | A source of truth — the heuristic remains the source of truth (D1) |

**The one invariant that matters:** *the client never appears on the caller's critical path longer than its timeout budget, and never converts a failure into an error.* A caller that receives the client's result must always be able to proceed with the heuristic answer, whether the client succeeded, failed, timed out, or was never configured.

---

## §2 The contract (PX-1 … PX-9)

- **Request:** `{model, input, questions[{type, name, instructions?, choices?[], levels?[]}], safety_identifier?}` (PX-2).
- **`input` is `string | DecisionInputMessage[]`** — either a plain string or an array of `{role, parts[{type, text}]}` messages. The request-edge validators do **not** check it, so an omitted or empty `input` is **mis-signalled upstream as a 502**, not a 400 (PX-2; §6 row 4). **The client pre-flight validates it** — this is the single most important client-side guard.
- **Response:** `{model, answers[], usage}` (PX-3). The wire answer is **`{type, name?, probability?, choice?, probabilities?, confidence?, score?, refusal?}`** — **`type` is always emitted**, and **`answers[].name` is the JOIN KEY to `questions[].name`**. An answer whose `name` is **missing or unmatched** ⇒ **fail open** (no join ⇒ no usable answer). The `name` is proxy-synthesized on the System One path and upstream-supplied on the openai path.
- **Canonical wire shape — LOAD-BEARING (R3/R4).** Even though the openai adapter path does not use it, the client **must** send the v187 canonical System One shape: for a `choice` question, `{"criteria": {label: description}}`; for a `score` question, `{"criteria": ["label", ...]}`. Evidence: 9router + the v185 nested shape ⇒ 422; OpenRouter + the v185 nested shape ⇒ 400 Zod "expected record … path [questions,probe_q1,criteria]"; both + the v187 canonical shape ⇒ 200. Sending the non-canonical shape on a working path produces a 4xx that fails open forever — a silently dead overlay.

### §2.1 "Branch on HTTP STATUS only" — the precise reading

Two different things, easy to conflate:

| Allowed | Forbidden |
|---|---|
| Classifying an **error** by its HTTP status code (PX-8) | Classifying an error by **body text**, `message` field, vendor copy, or telemetry-event names (v187 renames provider copy — PX-8) |
| **Structurally parsing** a 200 body to read the typed answer, with schema validation | Reading meaning out of a string inside the body — no substring matching on provider prose, no `message` inspection |

A **200 with a structurally invalid body** is not a 4xx/5xx — it is a *content* failure, and it fails open exactly like a 502. A **502 whose upstream envelope is relayed verbatim** is classified by its **status**; the relayed envelope's text is never inspected to decide what to do.

---

## §3 Configuration

| Setting | Env var | Default | Source | Status |
|---|---|---|---|---|
| Base URL | `DECISION_BASE_URL` | `OPENAI_BASE_URL` | IP-1 | **PROPOSED** — reuse semantics, not pre-carved |
| Credential | `DECISION_API_KEY` | `OPENAI_API_KEY` | IP-1 | **PROPOSED** — reuse semantics, **never mint** |
| Model id | `DECISION_MODEL` | literal purpose-id `"decision"` | IP-1 | **pre-carved** (`.env.example:268-276`) — **never** `OPENAI_MODEL` |
| Timeout | `DECISION_TIMEOUT_S` | per-lane value (see §4.2) | — | **PROPOSED — requires ratification** |
| Enabled | — | — | D1 | **deliberately absent**: `DECISION_MODEL` unset ≡ empty ≡ disabled |

**Only `DECISION_MODEL` is pre-carved.** `.env.example` documents `DECISION_MODEL` **alone** (`:268-276`); `DECISION_BASE_URL` and `DECISION_API_KEY` do **not** exist there today, and no `DECISION_*` variable has a daemon consumer. Their **names** and their **reuse semantics** (both default to the existing `OPENAI_*` values) are *this design's proposal*, not a pre-existing seam.

**Config threading (for the future implementation commission).** The chain is `config.yaml` → `substitute_env_vars` (`daemon/config.py:66-83`, called from `load_config` at **`:7157`**) → `LLMConfig` (`:139-230`, `env_prefix="OPENAI_"`) → `Config.llm` (`:5728`). Note the prefix: a `DECISION_*` variable does **not** carry the `OPENAI_` prefix, so it cannot ride the existing prefixed resolution — it needs either a `config.yaml` entry read explicitly, or an explicit read inside `load_config` (`:7150-7300`).

**Reuse rules (non-negotiable):**

1. **No new key material.** The proxy's decision lane uses the same token store as chat (PX-7). `DECISION_API_KEY` defaults to `OPENAI_API_KEY`; if an operator ever sets it to something else, that is *their* credential, not a new one this design mints.
2. **`DECISION_MODEL` is a purpose-id literal** — `"decision"`, the allowlist member with no current consumer (`daemon/config.py:5911-5926`, member `:5925`). It is **not** `OPENAI_MODEL`; the decision model is selected by *purpose*, not by whoever else is talking to the proxy.
3. **URL construction:** `DECISION_BASE_URL` + `/decisions`. The base **already carries `/v1`** — **never append `/v1` again** (PX-7).
4. **No discovery.** The model id is configuration, not a lookup (PX-5).

**Proposed headers** (mirroring the transport template, IP-3):

| Header | Value | Note |
|---|---|---|
| `Authorization` | `Bearer <DECISION_API_KEY>` | same token store as chat (PX-7) |
| `x-proxy-app` | `ensemble` | header precedent `daemon/tools/image_gen.py:140-143` |
| `Content-Type` | `application/json` | |
| `Accept` | `application/json` | the surface is **blocking single JSON — no SSE** (PX-6) |

---

## §4 Transport contract

### §4.1 Single attempt, no retry, no failover

- **One attempt. No retry. No failover.** (IP-5, leader-ruled.) The proxy performs **exactly one** upstream attempt and offers no retry or fallback (PX-6), and it issues **no idempotency keys** — a client-side retry would **double-bill** (PX-6).
- **No failover machinery.** `daemon/services/llm_failover.py` offers `current_failover_url` and `invoke_raw_with_failover` (IP-2), but there is **no httpx-shaped failover helper** a decisions client could reuse without inventing one — and inventing one is exactly the machinery this design refuses. Note that `daemon/graph.py:3924` `clean_llm_config` **strips `base_url_backup`** (IP-2); a decisions client that tried to read a backup base URL from the raw config would be reaching past the sanctioned path.
⚠️ **The HA consequence, stated plainly.** Today's stage-3 chat call runs through `invoke_raw_with_failover` (`daemon/services/llm_failover.py:700-861`), which carries a transient/timeout retry budget and a base-URL failover. The decisions client is **single-attempt by leader ruling** — so **C2 trades that resilience for a typed answer**: a failed overlay call falls open to the heuristic rather than retrying or failing over. In **C1** the chat call keeps its HA cushion and the overlay is an extra *detached* call that cannot affect the shipped result; in **C2** the S1 path loses the retry/failover cushion today's chat path has. That is a deliberate, ratified trade — not an oversight.
- **The client is the only throttle** (PX-6): the proxy applies **no inbound rate limiting**. Every seam therefore owns its own call budget — see `controls-and-phasing.md` for the per-seam budget and the global cost ceiling.

### §4.2 Timeout budget — 5–10 s, justified per lane

The client timeout is a **per-lane budget**, not one global number, because the cost of waiting is not uniform across lanes (IP-5). The rule: *the client timeout must fit inside the calling lane's own latency tolerance*, and on expiry the caller's heuristic answer is used.

| Lane | Tolerance character | Timeout | Rationale |
|---|---|---|---|
| **S1 — snapshot L3 selection** | on a **tool-blocking** path (the search tool's return waits on it) | **5 s** | The overlay is a *ranking* refinement over an already-good heuristic order; a user waiting on a tool call must not wait 10 s for a reordering. 5 s keeps the worst case inside the existing chat-call tolerance. |
| **S2 — busy-slow arbitration** | advisory, off the interaction path | **10 s** | Never on a user-visible wait; the nudge is advisory text. The looser budget buys a better answer at no user-visible cost. |
| **S3 — ghost-promise detection** | per-turn-end, background | **5 s** | Runs at turn end; must not delay the turn's completion materially. |
| **S4 — attestation rescue** | **async judge path** | **5 s** | The canonical judge path is asynchronous; the overlay must not become the thing that holds a completion back. |

**Rationale for the range:** 5 s at the short end is the floor at which a proxy-side decision is worth the call at all (below it, the fail-open path is taken more often than the overlay is used); 10 s at the long end is the ceiling below which the proxy's own 30 s default (PX-6) cannot mask a hang.

### §4.3 Other transport facts the client must respect

- **Response cap.** The proxy accepts 16 MB requests and returns up to 64 MB (PX-6). The **client** caps its own read at **1 MiB**, mirroring the transport template's cap (IP-3): a typed decision answer is tiny, and an oversized body is a content failure ⇒ fail-open.
- **Cancellation is not a budget.** A client disconnect **does not cancel** the upstream call — abandoned calls still bill (PX-6). The client must therefore not treat cancellation as cost avoidance; cost accounting counts attempts, not completions.
- **No SSE.** The surface returns a blocking single JSON document (PX-6). The client does not implement a stream reader, and a `text/event-stream` body on this route is a content failure ⇒ fail-open.

---

## §5 The delta from the transport template (fail-closed → fail-open)

`daemon/tools/image_gen.py` is the closest existing transport (IP-3) — and it fails **closed**. The decisions client is the same shape with the **opposite** failure semantics. This delta is the single most important difference between the two, so it is stated as an explicit table rather than left implicit.

| Property | `image_gen` (template, IP-3) | **Decisions client (this design)** | Why |
|---|---|---|---|
| Error path | raises / returns a fail-closed error envelope (`:317-326`) | **returns the heuristic result** — never raises | D2: the external call can never block dispatch |
| Attempts | single attempt | **single attempt** (no retry either way) | PX-6: no idempotency keys ⇒ retry double-bills |
| Timeout | `asyncio.wait_for` (`:817-819`) | `asyncio.wait_for`, **per-lane budget** (§4.2) | fit inside the calling lane's tolerance |
| Response cap | 1 MiB | 1 MiB | typed answers are small |
| SSRF guard | present (`:177-231`) | **same guard applied to the resolved host** | the base URL is operator-configured, not user-supplied — the guard is defence in depth, not a hot-path check |
| Failure semantics | fail-closed | **fail-open** | **the delta** |

---

## §6 Exhaustive failure-mode table

Every row names **the heuristic result** the caller receives. There is no row whose outcome is an error. **Fail-open is scoped to the entire consult — `build_input` → the call → `apply`** — so a fault in *any* stage yields the heuristic result (row 30).

**Kill-contract pointer.** The rule that a kill means "the overlay is never called and the pre-overlay path computes the result" is stated **once**, in `seam-registry.md` §5.1.1 — this table does not restate it. The one overlay-side class that is **not** a transport failure is **`switch_unreadable`** (`controls-and-phasing.md` §3.2.1): an unreadable or unknown switch state means the overlay is **never consulted**, no call is made, and the seam is answered from the heuristic. It is counted under `decision_overlay_suppressed_total`, never under `decision_overlay_failures_total`.

**Two disjoint label vocabularies.** Rows **1–5** are **pre-flight**: the client short-circuits **before any socket is opened**, so they are *not* failures — they are **suppressions** and are counted in **`decision_overlay_suppressed_total`**, never in `decision_overlay_failures_total`. Their labels belong to the **suppressed** vocabulary. Rows **6–30** are the **25 failure values** of the `failure_class` label set — **19 DISTINCT labels** (`connect_error`, `timeout`, `http_400`, `http_401`, `http_403`, `http_404`, `http_405`, `http_413`, `http_500`, `http_502`, `http_504`, `http_other`, `malformed_body`, `unmatched_answer`, `malformed_answers`, `refusal`, `too_large`, `below_floor`, `seam_fault`).

| # | Failure class | Detected by | Client action | **Heuristic result the caller gets** | Counted as |
|---|---|---|---|---|---|
| 1 | Not configured (`DECISION_MODEL` unset/empty) | local pre-flight | **never call out** | heuristic, unmodified | suppressed `not_configured` |
| 2 | Base URL missing/empty | local pre-flight | never call out | heuristic, unmodified | suppressed `not_configured` |
| 3 | Credential missing/empty | local pre-flight | never call out | heuristic, unmodified | suppressed `not_configured` |
| 4 | `input` **omitted** (an empty string is a different case) | local pre-flight | **never call out** | heuristic, unmodified | suppressed `invalid_request` |
| 5 | Candidate set smaller than **2** — the **n ≥ 2** precondition (a `choice` question needs ≥ 2 options; `reranked[:20]` can be short) | local pre-flight | **never call out** | heuristic, unmodified | suppressed `candidate_set_too_small` |
| 6 | DNS resolution failure | transport exception | fail open | heuristic, unmodified | failure `connect_error` |
| 7 | Connection refused | transport exception | fail open | heuristic, unmodified | failure `connect_error` |
| 8 | TLS handshake / certificate failure | transport exception | fail open | heuristic, unmodified | failure `connect_error` |
| 9 | Client timeout (per-lane budget) | `asyncio.wait_for` | fail open | heuristic, unmodified | failure `timeout` |
| 10 | Proxy **400** — request-shape rejection (the route's shape-rejection status) | HTTP status | fail open | heuristic, unmodified | failure `http_400` |
| 11 | Proxy **401** (`authentication_error` envelope) | HTTP status | fail open | heuristic, unmodified | failure `http_401` |
| 12 | Proxy **403** — non-standard body; **also** the per-token model allow-list rejection | HTTP status | fail open | heuristic, unmodified | failure `http_403` |
| 13 | Proxy **404** (route/model absent) | HTTP status | fail open | heuristic, unmodified | failure `http_404` |
| 14 | Proxy **405** — plain-text body | HTTP status | fail open | heuristic, unmodified | failure `http_405` |
| 15 | Proxy **413** (request over the 16 MB cap) | HTTP status | fail open | heuristic, unmodified | failure `http_413` |
| 16 | Proxy **500** | HTTP status | fail open | heuristic, unmodified | failure `http_500` |
| 17 | Proxy **502** — **all causes**, one label | HTTP status | fail open — **never parse the relayed or envelope text** (PX-8) | heuristic, unmodified | failure `http_502` |
| 18 | Proxy **504** | HTTP status | fail open | heuristic, unmodified | failure `http_504` |
| 19 | **Any other / unexpected HTTP status** | HTTP status | fail open — the catch-all | heuristic, unmodified | failure `http_other` |
| 20 | 200 with a **non-JSON** body | content validation | fail open | heuristic, unmodified | failure `malformed_body` |
| 21 | 200 with **SSE** body instead of JSON | content validation | fail open | heuristic, unmodified | failure `malformed_body` |
| 22 | 200 with JSON but **missing `answers`** | schema validation | fail open | heuristic, unmodified | failure `malformed_body` |
| 23 | 200 with **empty `answers[]`** | schema validation | fail open | heuristic, unmodified | failure `malformed_body` |
| 24 | 200 with an answer whose **`name` is missing or does not match any `questions[].name`** | join validation | fail open | heuristic, unmodified | failure `unmatched_answer` |
| 25 | 200 with an answer of the **wrong `type`** for its question kind | schema validation | fail open | heuristic, unmodified | failure `malformed_answers` |
| 26 | 200 with an **out-of-range** value (option not offered, level not offered, probability not in [0,1]) | schema validation | fail open | heuristic, unmodified | failure `malformed_answers` |
| 27 | 200 with a **refusal** answer (answer-side only — PX-1) | schema validation | fail open | heuristic, unmodified | failure `refusal` |
| 28 | Body exceeds the 1 MiB client cap | read cap | fail open | heuristic, unmodified | failure `too_large` |
| 29 | **Below-floor answer** — the call succeeded, but the answer did not clear the seam's confidence floor (`thin-client.md` §8.2.1) | floor comparison | fail open — the heuristic result stands | heuristic, unmodified | failure `below_floor` |
| 30 | **`seam_fault`** — an exception in *any* consult stage (`build_input` → call → `apply`) | registry try/except | fail open | heuristic, unmodified | failure `seam_fault` |

**Status taxonomy — what the route actually emits.** The decision route emits **400** (request-shape rejection), **401**, **403** (non-standard body), **404**, **405** (plain text), **413**, **502** and **504**; it does **not** emit **409**, **422**, **429** or **503**, so those are not enumerated. **Row 17 folds every 502 cause into one label** — the proxy emits 502 for **7 handler-emitted causes + 1 relayed upstream envelope** (credential unresolved `:307`, provider-not-registered `:325`, build failed `:366`, upstream transport `:433`, upstream read `:465`, over-cap `:478`, inspect error `:497`, relayed upstream envelope), and the "no decision adapter registered" case is **not uniquely identifiable by status**. **No body text is parsed anywhere.** The no-adapter case is resolved at provisioning time by the operator (§7); the client never probes and no boot-time verification occurs.

**Rows 20–27 are defensive-only.** Against the current proxy the client does not expect them; they exist so that a future or non-conforming upstream fails open rather than raises.

**Two things that are NOT failures.** (i) A 200 whose **`usage` block is missing** is **not** a failure — the answer is valid and is used; the absent usage degrades the **cost counter** to a per-call estimate and nothing else. **Cost is a separate datum from answer validity.** (ii) A `choice`/`score` answer whose **`confidence` is absent** is likewise valid — confidence is populated on choice and score answers when the upstream supplies it, never on predicate (PX-3) — and its absence does not invalidate the answer.

**Where the fail-open boundary sits.** The fail-open covers the **entire consult** — `build_input` → the call → `apply`. An exception in **any** stage yields the heuristic result; that is row 30 (`seam_fault`). **The registry, not the seam, owns this fence** — a seam cannot narrow it.

**The breaker shares this fence.** A seam that is failing repeatedly is **short-circuited**: the registry returns the heuristic result **without calling out**, for a bounded window, so the dead-lane failure mode — every search paying a full timeout and a billed attempt, indefinitely, relieved only by a human on a dashboard — cannot occur. A suppressed consult emits a shadow row with `reason=breaker_open` and **incurs no cost** (no call ⇒ no cost). The full breaker and budget specification is in `controls-and-phasing.md` §3.5.

---

## §7 Operator provisioning prerequisites

**Provisioning is an OPERATOR PRECONDITION, executed out-of-band. The DAEMON DOES NOT PROBE.** The truth at build 185: the proxy's client-facing routes are only `/v1/chat/completions`, `/v1/messages`, `/v1/models`, `/v1/image_generation`, `/v1/decisions`, `/healthz` (`cmd/main.go:348-369`); `GET /v1/models` **hard-excludes** decision models (`pkg/proxy/handler.go:343`); the adapter registry is a **private Go map with no HTTP exposure** (`pkg/decision/registry.go:22`, `:45-48`); and the admin surface exposes kind + credential refs + provider but **not** adapter registration and **not** exact credential resolvability (keys are masked), on a different URL derivation. **Therefore a client cannot verify its own provisioning**, and this design does not claim it can.

**What the operator must do before enabling a seam:** provision the decision model row (a `kind=decision` row) with a credential whose provider has a **registered adapter**, and verify it via the proxy's **admin surface** (out-of-band). Only then is the seam enabled.

**What the client *can* observe at runtime — and what it cannot.** A consult can observe **404** (model absent, `pkg/decision/handler.go:261`) and **400 `decision_misroute`** (`:273`) — **probe-provable** conditions — plus **401** (auth, `:203`) and **403** (allow-list, `:290`) as their own distinct reasons. Everything else that can go wrong at 502 is **not distinguishable**: the proxy emits 502 for credential-unresolved (`:307`), adapter-not-registered (`:325`), upstream transport/read/cap/inspect (`:433`/`:465`/`:478`/`:497`) and the relayed upstream envelope — and the client **cannot tell these apart** (one label, `http_502`). **Credential-unresolved and adapter-not-registered are NOT DISTINGUISHABLE and NOT DETECTABLE by the client.** ⚠️ **A transient upstream 502 must NEVER park a seam as `NOT_ENABLED`** — only a probe-provable condition (404 / 400 `decision_misroute`) may; a 502 is a **failure**, counted and alerted, never a provisioning verdict.

**Prerequisites the operator must satisfy before the overlay can work:**

1. **A decision model row** exists with the purpose-id `"decision"` mapped to a model whose kind is `decision` (PX-4).
2. **A credential row** for that model, whose provider has a **registered decision adapter** in the proxy (`openai`, `typesafe`, `openrouter` — PX-4). The provider is bound to the credential, **not** to the request, so a request cannot select one.
3. **The proxy is reachable** at `DECISION_BASE_URL + /decisions` and the decision lane is **deployed-enabled** (PX-9 — the lane is always-on *in code*; "disabled" is a deployed state such as a missing credential or a dead upstream, and `models.enabled=0` does **not** gate the route).
4. **A decision model must not be looked up in `/v1/models`** — it will not be there (PX-5).

### §7.1 Deploy-order non-dependence

**No phase of this design may assume the `openrouter` provider exists.** The `openrouter` decision adapter landed on the proxy's `origin/master` (`43fb7e5`, v187) and is **a descendant of** the deployed build 185 (`9cf17bb`). The **client-facing contract is byte-identical across the two** — v187 adds the `openrouter` provider, and the **System One upstream wire shape (criteria flattening) and the URL construction (`/v1` stripped, `+/v1/systemone`) also changed** (`pkg/decision/types.go` is unchanged)

---

## §8 Typed mapping per seam

### §8.1 The mapping table

| Seam | Question kind | The question | Options / levels | Calls | Monotonicity | Status |
|---|---|---|---|---|---|---|
| **S1** | `choice` | "which candidate is most relevant to the query?" | ≤20 candidate **ids** (2–255 bound) — the answer carries the **full per-option distribution** | **1** | `WITHIN_SET` | **PILOT** |
| **S2** | — | — | — | — | `ADVISORY_ONLY` | deferred (no typed inputs exist) |
| **S3** | `predicate` | "does this trailing text promise work it did not do?" | — | **1** | `TOWARD_TIGHTENING` | staged |
| **S4** | `predicate` | "is this work complete?" → P(complete) | — | **1** | `TOWARD_COMPLETION` | last, rescue-band-only |

Every mapping is **one call per consult**. In **C2 (deciding)** the overlay *replaces* the ad-hoc call; in **C1 (shadow)** the ad-hoc call still ships and the overlay is called **detached** (`seam-registry.md` §5.1.1). No configuration stacks a second call onto a **blocking** path.

### §8.2 S1 — the pilot mapping, in detail

- **Question:** one `choice` question whose **options are the candidate ids** and whose **`instructions` carry the candidate evidence** — the query plus each candidate's title, tags and summary, exactly as today's prompt carries them (`snapshot_search_service.py:640-642`). **`input` is the query**, always sent, after the client pre-flight validates it (§6 row 4).
- **One call replaces one call — in C2.** The typed call replaces the ad-hoc chat call **on the deciding path** at the `_chat_call` choke point (`snapshot_search_service.py:663-665` → `:750-757`). This holds **in C2 (deciding) only**; in **C1 (shadow)** the chat call still ships and the overlay is called **detached**. **In C2 the overlay replaces the chat call ON THE DECIDING PATH; the baseline chat selection is RETAINED (not deleted) as the KILL TARGET.** The baseline chat selection is **retained, not deleted**, so a kill restores it — **without it, a C2 kill could only degrade to the deterministic stage-2 floor**. Every latency and cost claim in this section is scoped to a named configuration (`seam-registry.md` §5.1.1).
- **The multi-select contract IS expressible — via the distribution.** Today's contract is a set: "pick AT MOST {limit} ids, **ordered by relevance**" (`:646-655`). A `choice` answer carries **the full per-option distribution** (`probabilities[]` of `{value, probability}`), not merely the picked option — so the client ranks all ≤20 candidates by probability and takes the top `limit`, **in the same single call**. The overlay therefore owns the whole **selected set and its ordering**, exactly as today's chat call does. No K parallel calls, no per-candidate `predicate` fan-out. **Two different guarantees, do not conflate them:** *never-empty* is a **client-side guarantee** — the intersection rule below plus the top-K of a distribution; *top-K completeness* (that the distribution's ranking is faithful) is an **upstream property** of the model, **not** a proxy guarantee.
- **Overlay authority: the selected set, drawn from the candidate set.** Two structural guarantees follow: it can never introduce an id that was not a candidate, and — because a top-K of a distribution always has K members — it can never empty the result.
- **Client-side intersection is REQUIRED.** The client **must** (i) **join** each answer to its question by `answers[].name` == `questions[].name`, and (ii) **intersect the returned distribution's option values with the candidate id set before ranking**. Any option whose value is not in the candidate set is **dropped**; if the intersection is empty, the consult **fails open** to the heuristic order. This is a **client-side requirement, not an assumption about the provider** — it is what makes the "may never introduce an id that was not a candidate" guarantee hold regardless of what the model returns.
- **"Reject all" is fail-open, never an empty selection.** Today `_llm_select` can return `[]` to reject every candidate; a `choice` question **always** selects an option, so the overlay **cannot express rejection at all**. That is the correct shape: "none of these are relevant" is expressed as **falling back to the heuristic order** — never as an empty result, which is the fail-closed-to-empty defect (`:667-669`).
- **Fail-open is identical.** Every failure in §6 returns `None`; the seam turns `None` into a **failure** so the existing `:410-415` branch is reached and the fused stage-2 order is used. The overlay's "no answer" **must never** be surfaced as an empty selection — today that path returns `[]` and is **fail-closed-to-empty**, contradicting two docstrings (`:27-28`, `:790-792`). An overlay that returned "no decision" as an empty result would **replicate the defect on the success path**.
- **Content exposure is unchanged.** The typed call carries the same candidate evidence the chat call carries today; the change is the transport and the typing, **not** the data. The shadow log still records **ids only** (`controls-and-phasing.md` §2.6).
- **Two variants — the pilot runs A0.** `choices[]` entries are `{value, description?}`, so the deterministic fused score that today's prompt **discards** (`:629`) can ride along in each option's `description` at **zero extra call cost**. **A0** (the pilot) carries *exactly today's evidence* — id, title, tags, summary — so the shadow A/B compares like-for-like. **A1** carries the fused score as an additive prior, which is strictly **more informed** than today's prompt and therefore **changes the comparison basis**: A1 must run **its own shadow cycle and its own Emperor gate**, not ride A0's.

### §8.2.1 The confidence floor — per seam and per configuration

A floor is only meaningful against a **named wire field**. `confidence` exists on **choice** and **score** answers; a **predicate** answer carries **`probability` only** — **`confidence` never exists on a predicate** (PX-3). The floor each seam reads is therefore:

| Seam | Kind | **Wire field read** | **C1 (shadow)** | **C2 (deciding)** | Below-floor behaviour |
|---|---|---|---|---|---|
| **S1** | `choice` | **`confidence`** (a choice answer's scalar) | n/a — shadow never acts | **0.5** (proposed; ratified at P3 from the A0 measurement) | below floor ⇒ treated as **no answer** ⇒ **fail open** to the deterministic stage-2 floor |
| **S3** | `predicate` | **`probability`** (`confidence` does not exist here) | n/a — shadow never acts | **0.9** (proposed; ratified at P3) | below floor ⇒ the overlay **contributes nothing**; the heuristic detector's decision stands unchanged |
| **S4** | `predicate` | **`probability`** (`confidence` does not exist here) | n/a — shadow never acts | **0.9** (proposed; this is D4's "high confidence floor", ratified at P3) | below floor ⇒ the overlay **adds nothing**; the canonical judge's verdict stands |

**Absent field ⇒ fail open.** If the field the seam reads is **missing or undefined** against its floor, the consult is **undefined** and therefore **fails open** — for S1 to the deterministic stage-2 floor, for S3/S4 by adding nothing. A missing field is never read as "zero", and never as "above the floor".

### §8.3 S3 — ghost-promise detection

**S3's overlay consult is placed in the router's ladder-routing branch (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`), inside the branch that `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates — so that switch gates the overlay as well as the routing.**
- **Only detector (A) is an overlay candidate.** Detector (B) is an **exact, structural** rule (descriptive-only) and detector (C) is the phrase-catalog feed; neither is an overlay target. The overlay is a `predicate` over the turn's trailing text.
- **Hook — ⚠️ two incompatible anchors, not interchangeable.** The **routing** decision for detector (A) lives in the router `should_continue` (`daemon/graph.py:4261`, ghost block `:4380-4402`, master gate `:4384`); the `attestation_gate_node` (`:5932`) is a **different node**. S3's overlay **switch point** is the **router master gate** (`:4384`) — that is where the ghost-promise routing is gated. The node is a separate observation surface, where `evaluate()` runs at `:6149` and the gate-exception early-return spans `:6170-6185`, so the read-only post-evaluate window starts at **`:6187`** (→ `~6270`), **after** `evaluate()` (`daemon/services/attestation_gate.py:1320`). ⚠️ The no-mutation rule is scoped to **that window**: the node body *does* mutate the decision downstream (`:6682-6687`, `:6716`), so "the overlay never mutates the decision" is a property of the insertion point, not of the node.
- **Direction: `TOWARD_TIGHTENING`.** The overlay may only **add** a ghost-promise signal; it may never remove one and may never loosen the gate. This is the general form of the "monotonically tighter-or-equal, never looser" principle, and it is the mirror of D4's `TOWARD_COMPLETION`.
- The signal is advisory at the hook: the seam's existing heuristic detectors remain in force and the overlay's contribution is *additional*, never a replacement. The overlay call sits behind the existing master gate `get_symptom_repair_ladder_enabled()` (`daemon/graph.py:4384`; config `daemon/config.py:6356-6410`), which is the switch point for this seam.

### §8.4 S4 — attestation rescue (last, rescue-band-only)

*Scope note (FLAG5): this section specifies the `TOWARD_COMPLETION` direction **only**. The superset-only contract here is per-direction — it does **not** generalise to other seams. The `apply` contract's universal rule is no-widening (`seam-registry.md` §2.1); per-direction monotonicity is `seam-registry.md` §2.2. A `WITHIN_SET` seam (S1, the only P1 seam) may replace or reorder within the candidate set and is bound only by no-widening + never-empty.*

- **Question:** one `predicate` over the completion bundle → P(complete). **The judge is consulted FIRST**; the overlay may only **ADD** completions the judge would also have produced, above the floor (§8.2.1). It never replaces a judge verdict and never flips a `DENY`.
- **Direction: `TOWARD_COMPLETION` — add-only, with the deny ownership stated precisely.** The `apply` signature for a `TOWARD_COMPLETION` seam accepts the heuristic's *candidate completions* and returns a **superset** of them — it may **add** completions and may **never remove one** (a subset is forbidden), and it has **no parameter through which a denial could be expressed**. The `DENY` rung is minted by the heuristic tree at **two** sites, **both inside `decide()`**: **`daemon/services/attestation_gate.py:1024`** (the main path) and **`:950`** (the `report_obligation_read_error` error variant). The `DENIED → ALLOW` flip remains the judge's own rescue (`daemon/graph.py:6398-6420`), and the overlay's answer is **added to** the judge's verdict, never substituted for it — so the guarantee is "the overlay may only propose the rescue the judge would have proposed", not "the type makes denial impossible".
- ⚠️ **Fail-open on S4 is NOT benign — it keeps `DENIED`.** Everywhere else in this set, fail-open means *less restrictive*. On the attestation lane it means the opposite: if the overlay fails, the heuristic's `DENY` **stands** and the completion is withheld. That is exactly D4's "monotonically tighter-or-equal, never looser" — but it must be read as *tightening*, not *leniency*. D2's fail-open governs the **overlay's** failure; it never relaxes the heuristic's own decision.
- **Position: after the judge call, inside the same `fused_judge_on` block.** The **canonical judge is consulted FIRST** (`daemon/graph.py:6326`); the overlay consult sits **after** the judge call, **inside the same `fused_judge_on` block** (`daemon/graph.py:6289-6296`), and may only **ADD** completions the judge did not already produce, **above the high-confidence floor**. **The judge is NEVER skipped** — in any band, on any outcome. A below-floor or absent answer adds nothing. If the consult genuinely cannot sit inside that boolean, the set says so plainly and the operator warning becomes *"to stop the overlay on S4, kill the per-seam registry switch — **not** `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`."*