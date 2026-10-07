"""opendesign-instance B-element — native generate adapter (REC §1.2 component 9).

Split per-capability (REC §7 risk: "Component 9 becomes an OD monolith"):

- :mod:`.generate` — :class:`OdGenerate` (the long pole; finish_reason +
  usage visibility; completeness gates INSIDE)
- :mod:`.compose_brief` — :class:`OdComposeBrief` (Turn-3 pure formatter;
  ported from open-design-mcp@0.16.1, Apache-2.0, attribution preserved)
- :mod:`.save` — :class:`OdSave` (captures one HTML to the canonical mockup
  path; the repo copy = developer deliverable)
- :mod:`.lint` — :class:`OdLint` (16-regex family + parse5 EOF gate;
  own_outright ported into the adapter per OQ4 disposition)

The per-capability split is what lets plugin #2 (next B-path plugin) reuse
the registry surface without re-implementing the adapter framework.

**Mode P (Python-native compose).** The default per REC §4.2; the slice-⑤
probe artifact documents the evidence (probes.md §A1.1-A1.5). The
:class:`OdGenerate` class reads the vendored contracts-mirror prompt
strings as the primary source and falls back to the daemon copies when
the mirror is absent (OQ7 disposition).

**Why no daemon subprocess / TS sidecar.** The composer chain is string
concatenation of pre-rendered template strings with conditional
inclusion + section ordering — re-expressible faithfully in Python. Mode
T (TS sidecar) would add a Node runtime dependency without semantic
benefit. REC §4.2 makes Mode T the fallback "iff probe finds the chain
too entangled" — that gate was not tripped.

**Completeness gates INSIDE the adapter (load-bearing for the 2026-10-06
2/2 live failure).** :class:`OdGenerate.execute` refuses to return a
success result on:

- ``finish_reason != 'stop'`` — the upstream closed before the model
  finished (the live 2/2 failure's root cause per
  od-generation-engine.md §10 H1/H2).
- ``html`` structurally incomplete (no ``</html>`` marker) — catches
  the partial-mid-CSS failure mode (Attempt A1 of the 2026-10-06 smoke).
- ``html`` empty — catches the thinking-only 0B failure mode (Attempt A2
  of the 2026-10-06 smoke).

A truncated / empty / structurally-incomplete result is returned as an
``error`` envelope (CON §3: ``ok: false``, one of the documented codes)
so the calling agent can fall back per the live designer workflow
``fallback_reason: timeout | other:<detail>`` token set. The agent never
sees a silent "partial HTML" success — the failure is LOUD.

**No runtime loading (CON §7 sentinel).** The adapter imports the
vendored prompt strings as module constants (read once at module import);
no ``importlib`` or entry-point scan. The plugin tree is a resource, never
a role.
"""

from __future__ import annotations

from .ports import declared_opendesign_ports  # noqa: F401

__all__ = ["declared_opendesign_ports"]