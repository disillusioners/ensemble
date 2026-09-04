# T2.13 — astream-check corpus doc

> Date: 2026-09-04 (UTC) | v2 HEAD: `87ad1018` (post-PR1)
> Method: re-grep `graph.astream\|graph.ainvoke` in `daemon/services/instance_messaging.py` immediately BEFORE T2.5/T2.6 wiring per architect §1.4 MONITOR item (a).

## Grep evidence

```text
$ python3 -c '<docstring-aware grep filter>' daemon/services/instance_messaging.py
Actual call sites: 1
  L3929:                 async for event in graph.astream(graph_input, config, stream_mode=["updates"]):
```

Comment/docstring lines that mention `graph.astream`/`graph.ainvoke` (filtered as non-call sites):

```text
daemon/services/instance_messaging.py:96:    # -> ``graph.ainvoke`` bypass. These are correctness fixes that ship
daemon/services/instance_messaging.py:328:    # ``_build_graph_input`` sites converge, before ``graph.astream``),
daemon/services/instance_messaging.py:369:                ``graph.astream``. **MUTATED IN PLACE** — placeholders
daemon/services/instance_messaging.py:481:        # structurally valid before ``graph.astream``. The D1 seam
daemon/services/instance_messaging.py:586:        ``graph.astream(graph_input, ...)``. With a non-empty
daemon/services/instance_messaging.py:1498:    # ``graph.ainvoke`` bypass — was DELETED (it never shipped in
daemon/services/instance_messaging.py:1777:        #    graph.astream, so reviving a terminated instance is the
daemon/services/instance_messaging.py:3685:    # between the clear and ``graph.astream`` loses the leftovers
```

## Result

**Count: 1** (line 3929, the astream call site). Matches the v1 baseline load-bearing assumption (SINGLE astream site in `instance_messaging.py`). The 4-tap-site contract mapping is intact.

**Proceed to T2.5/T2.6 wiring.**

## Drift vs Phase 0 / Phase 1

| Phase | Count | Note |
|---|---|---|
| v1 baseline (`fc908945`) | 2 (ainvoke :1087 + astream :3564) | Phase 1 plan text |
| v2 Phase 1 (`901d96e5`) | 1 (astream :3929 only) | The inline `ainvoke` at v1 :1087 was removed during v2 consolidation (call-graph cleanup); classified as OOS / accepted-degradation in v1's own `message_tap.py` docstring — `decisions.md D19` + B1 |
| v2 PRE-Phase-2 (`87ad1018`) | 1 (astream :3929) | unchanged |

No drift. STOP gate clear.

## Comparison vs v1 (informational)

v1 had a SECOND `graph.ainvoke` at `:1087` inside a one-off connectivity / direct-invoke helper that constructed `{"messages": [message]}` INLINE and bypassed `_build_graph_input`. v1's `message_tap.py` docstring explicitly classified this as OOS:

```
- Direct ``ainvoke`` invocation at ``instance_messaging.py:1055``
  (B1 / D19; inline ``{"messages": [message]}`` bypasses
  ``_build_graph_input``; zero production callers; id-less input;
  ``state.ts`` fallback applies; mirrors the watchover handling).
```

v2 has zero production callers for that inline-`ainvoke` shape (the connectivity path moved to a different mechanism during v2's WC-wake consolidation). The OOS classification carries forward unchanged.
