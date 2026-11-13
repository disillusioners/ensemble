# The skill-injection roster (workstream F — skill-system-revival Phase 1)

**Status:** Phase 1 — the roster is RE-COMPUTED and PUBLISHED here. **No agent is
flipped in Phase 1** (DS-25 / plan §10 "The F decision"). The roster change is a
**staged payload** that is armed **only when the capture lane is ON** (§4 below).

**Why this file exists.** The commission's census ("16 true / 24 declaring") was
taken on a DETACHED HEAD (`013895287`) and was **stale**. The roster below is the
**re-computed** truth at `origin/latest`. With this manifest published, the next
census is a **DIFF against a known state** instead of a re-derivation from
scratch (plan §10, "F ships a roster manifest").

---

## 1. The flag and its TWO axes

`agents/<name>/meta.json` carries one bit, `skill_injection`, read with a
**fail-closed** default — `daemon/registry.py:690` and `:756`:
`skill_injection=meta.get("skill_injection", False)`. **Absent ⇒ `False`.**

That single bit is read by **two different gates**, so it decides **two** axes:

| Axis | Gate | Site |
|---|---|---|
| **capture** | Gate 3 of the capture eligibility check | `daemon/services/skill_metrics_service.py:610-640` |
| **injection** | the dynamic injection lane, gated at the call site | `daemon/services/instance_messaging.py` (the `skill_injection` gate) and `daemon/services/context_messages.py` (`skills_enabled_only`) |

The bit does **NOT** gate the **manual** capture tool (`skill_execute_capture`
carries no `skill_injection` check — only the kill-switch).

⇒ **The two axes are recorded SEPARATELY in the table below**, so the Phase-2
decision is a **per-axis** decision. **Today the axes are COUPLED** — one bit
drives both — so the per-axis columns carry the same value as the `live` column.
Decoupling them is a Phase-2 decision (and would need a second key; see §4.2).

## 2. The census — method, population, and the two counts

**Method:** the census population is the glob `agents/*/meta.json` — the method the
Phase-0 census used (`phase0-facts-extract.md` §A2, "21 of 46 `agents/*/meta.json`
have NO `skill_injection` key").

**Population — two counts, both true, different denominators:**

| Population | Count | Notes |
|---|---|---|
| **Census** (`agents/*/meta.json` glob) | **46** | 25 declaring + 21 declaring-nothing |
| **Agent REGISTRY** (`daemon/registry.py`, `SKIP_DIRS`) | **45** | the census minus `_baby_template` (scaffolding, skipped by the registry) |

`_mother` carries a `meta.json` and is **not** in `registry.SKIP_DIRS` → the
registry registers it as an agent. `_baby_template` / `_inner_soul` /
`_prompt_system` are scaffolding and are skipped. The `registry` column below
records which population each row belongs to.

**Result at `origin/latest` — 25 declaring:**

* **15 `true`** — aegis, approver, approver[v2], architect, critic, doc-maintainer,
  maintenancer, planner, planner[v2], reviewer, reviewer[v2], sketcher, tidier,
  tidier[v2], worker
* **10 `false`** — blueprinter, coder, designer, developer, developer[v2], devops,
  project-manager, tester, wanderer, watcher
* **21 declaring-nothing** → fail-closed `False`

⇒ **30 of the 45 registered agents cannot capture** (10 `false` + 20 absent);
**31 of the 46 census entries cannot capture** (10 `false` + 21 absent).
Both statements are the same fact over the two denominators.

## 3. The recorded roster DRIFT (why the earlier census was stale)

The stale census was taken at `013895287`. Verified drift, `013895287` →
`origin/latest`:

| Agent | `@013895287` | `@origin/latest` | Change |
|---|---|---|---|
| `blueprinter` | `skill_injection: true` | `skill_injection: false` | **flipped true → false** |
| `tester` | `skill_injection: true` | `skill_injection: false` | **flipped true → false** |
| `aegis` | **directory absent** | `skill_injection: true` | **added, true** |

**None of the three changes was communicated to the skill lane** — the lane kept
working from the 16/8 census. That is the drift this manifest records.

## 4. The staged payload and ITS GATE

### 4.1 The gate (exact)

> The roster payload is **ARMED only when the capture lane is ON**.

* "the capture lane is ON" is resolved by the **canonical resolver**
  `daemon/services/skill_job_dispatcher._resolve_capture_enabled()` — the
  `ENSEMBLE_SKILL_CAPTURE_ENABLED` kill-switch, the same resolver
  `daemon/services/skill_metrics_service` imports and calls at its Gate 0.
* With the lane **OFF** (the Phase-1 state — the switch is never set in Phase 1),
  `stage_payload(...)` returns an **EMPTY** payload: `armed=False`,
  `reason="capture_lane_off"`, `edits=()`. Nothing is computed, so nothing can be
  applied.
* With the lane **ON** (Phase 2), the payload lists the `meta.json` edits the
  manifest's per-axis intent implies.
* The payload is **COMPUTED, never WRITTEN** by the mechanism:
  `agents/**` is **house-fenced in Phase 1** and the write is the leader's
  Phase-2 route. The mechanism lives in `daemon/services/skill_roster.py`
  (`stage_payload`), and the pin **M-F5** proves the gate is not bypassable.

### 4.2 What the payload WOULD be in Phase 2

**Today the payload is EMPTY even with the lane ON** — the per-axis intent
equals the live value for every agent, because **Phase 1 flips nothing**.

Two structural facts the Phase-2 route must know:

1. **The injection axis** is the existing `skill_injection` bit — a staged edit
   for it is directly applicable.
2. **The capture axis has NO key of its own** — `skill_injection` is ONE bit read
   by BOTH gates. Decoupling the axes therefore needs a second key (or an
   explicit ruling that the bit stays coupled). `stage_payload` reports
   capture-axis deltas with `ready=False` and says so in the edit's note, rather
   than pretending an editable key exists.

### 4.3 The exact `meta.json` diffs — NOT APPLIED in Phase 1

**There are none.** Every agent's manifest intent equals its live value, so the
Phase-1 payload is empty and **no `agents/*/meta.json` file is edited**. The
house fence (`agents/**`) is respected.

## 5. The machine-readable census table

The block between the fenced markers is the authoritative list. It is re-derived
from `agents/*/meta.json` by `daemon/services/skill_roster.derive_roster()` and
diffed against this table by the **M-F4** pin — editing this table without
re-deriving it turns that pin **RED**.

Columns: `agent` · `registry` (`agent` | `scaffolding`) · `declares` (does the
key exist at all) · `live` (the effective value — fail-closed `false` when
absent) · `capture-enabled` · `injection-enabled` · `source`.

<!-- F-ROSTER-TABLE-BEGIN -->
| agent | registry | declares | live | capture-enabled | injection-enabled | source |
|---|---|---|---|---|---|---|
| `_baby_template` | scaffolding | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `_mother` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `aegis` | agent | yes | true | true | true | origin/latest census |
| `approver` | agent | yes | true | true | true | origin/latest census |
| `approver[v2]` | agent | yes | true | true | true | origin/latest census |
| `architect` | agent | yes | true | true | true | origin/latest census |
| `ari` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `blueprinter` | agent | yes | false | false | false | origin/latest census |
| `canary` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `charter` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `coder` | agent | yes | false | false | false | origin/latest census |
| `cracker` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `critic` | agent | yes | true | true | true | origin/latest census |
| `designer` | agent | yes | false | false | false | origin/latest census |
| `developer` | agent | yes | false | false | false | origin/latest census |
| `developer[v2]` | agent | yes | false | false | false | origin/latest census |
| `devops` | agent | yes | false | false | false | origin/latest census |
| `doc-maintainer` | agent | yes | true | true | true | origin/latest census |
| `doc-writer` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `experiencer` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `explorer` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `gaia` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `giter` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `governor` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `hacker` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `image-comparator` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `image-reader` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `jober` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `kb-importer` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `kb-writer` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `leader` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `maintenancer` | agent | yes | true | true | true | origin/latest census |
| `planner` | agent | yes | true | true | true | origin/latest census |
| `planner[v2]` | agent | yes | true | true | true | origin/latest census |
| `project-manager` | agent | yes | false | false | false | origin/latest census |
| `reviewer` | agent | yes | true | true | true | origin/latest census |
| `reviewer[v2]` | agent | yes | true | true | true | origin/latest census |
| `sketcher` | agent | yes | true | true | true | origin/latest census |
| `skill-keeper` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `sweeper` | agent | no | false | false | false | fail-closed default (registry.py:690/:756) |
| `tester` | agent | yes | false | false | false | origin/latest census |
| `tidier` | agent | yes | true | true | true | origin/latest census |
| `tidier[v2]` | agent | yes | true | true | true | origin/latest census |
| `video-capturer` | agent | yes | false | false | false | video-team activation 2026-11-12 (skill_injection: false — declares, not live) |
| `video-editor` | agent | yes | false | false | false | video-team activation 2026-11-12 (skill_injection: false — declares, not live) |
| `video-producer` | agent | yes | false | false | false | video-team activation 2026-11-12 (skill_injection: false — declares, not live) |
| `video-verifier` | agent | yes | false | false | false | video-team activation 2026-11-12 (skill_injection: false — declares, not live) |
| `wanderer` | agent | yes | false | false | false | origin/latest census |
| `watcher` | agent | yes | false | false | false | origin/latest census |
| `worker` | agent | yes | true | true | true | origin/latest census |
<!-- F-ROSTER-TABLE-END -->

## 6. The pins that protect this manifest

| Pin | What it proves | Where |
|---|---|---|
| **M-F4** | the manifest is a faithful record of the repository — the pin re-derives the census and diffs it; editing the table without re-deriving turns it **RED** | `tests/services/test_skill_roster_manifest.py` |
| **M-F5** | the staged payload is **conditional on the capture lane being ON** — removing the gate turns the pin **RED** | `tests/services/test_skill_roster_manifest.py` |
| policy | an agent that does not declare `skill_injection` can never be capture-enabled or injection-enabled in this table (the fail-closed default) | `tests/services/test_skill_roster_manifest.py` |

## 7. Provenance

* Census source: `origin/latest` (`ae5bbafed`) — the branch's working tree and
  `origin/latest` carry **byte-identical** `skill_injection` values for every
  agent (verified: the only `agents/**` difference between the branch and
  `origin/latest` is `agents/skill-keeper/meta.json`'s `version` /
  `read_scope` — **not** `skill_injection`).
* Flag semantics: `daemon/registry.py:690`, `:756`; capture Gate 3:
  `daemon/services/skill_metrics_service.py:610-640`; injection call sites:
  `daemon/services/instance_messaging.py`, `daemon/services/context_messages.py`.
* Ruling: plan §10 ("The F decision"), `decisions.md` **DS-25**; the
  `skill_injection: false` **does** prevent dynamic injection (the council's
  F-12 correction to the earlier, wrong claim).
* Owner: workstream **F** (this file, plan §18 row 38).