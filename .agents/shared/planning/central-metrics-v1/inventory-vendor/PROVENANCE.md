# VENDORED EVIDENCE — provenance (AM-24)

Date: 2026-11-02 (UTC) · Lane: central-metrics-v1 · Amendment: **AM-24** (architect v2 §4 / §8 M-10)

## Why these files are vendored

The metrics-unification inventory is the pull-seam map that seeded this lane's research wave. It lives
**UNTRACKED** in the shared main checkout (`/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/`),
so any committed document that cites it by path **dangles in every other checkout**. AM-24 requires the
evidence to be vendored (or SHA-cited) inside the lane directory. Vendoring was chosen over SHA-citing
because the plan cites specific sections of these files and a hash alone is not readable evidence.

## Provenance

| Field | Value |
|---|---|
| Source directory | `/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/` (shared main checkout — **READ-ONLY**, never modified by this lane) |
| Source status | **UNTRACKED** in the main checkout (present on disk, absent from git history) |
| Evidence ref | `origin/latest@62627265f` (v0.28.4-era) — the inventory's own provenance line |
| Vendored on | 2026-11-02 (UTC), by the central-metrics-v1 plan set (planner) |
| Transformation | **prepend-only** — an 8-line HTML-comment provenance header; the content below is byte-identical to the source |
| Citation rule | committed docs cite **this directory**; the source path is cited only for provenance |

## Byte-identity (sha256)

| File | Source sha256 | Vendored-body sha256 | Verdict |
|---|---|---|---|
| `README.md` | `1bd508a730fd2fdff7f5156297cd778c13f42e5a1fbe535c41df59df30137b2c` | `1bd508a730fd2fdff7f5156297cd778c13f42e5a1fbe535c41df59df30137b2c` | IDENTICAL |
| `metrics-inventory-backend-producers.md` | `4101ac1c9d8269a6d6fe812927087fb09b3c5191eb6798f8cdcbe3c03b6ee85b` | `4101ac1c9d8269a6d6fe812927087fb09b3c5191eb6798f8cdcbe3c03b6ee85b` | IDENTICAL |
| `metrics-inventory-surfaces-fe-deps.md` | `f4cc5c4ffdb10028c21fe8ffa38b12855efc9736938b10e0137362b4c60373d8` | `f4cc5c4ffdb10028c21fe8ffa38b12855efc9736938b10e0137362b4c60373d8` | IDENTICAL |

## Verification recipe

```bash
cd <lane>/.agents/shared/planning/central-metrics-v1/inventory-vendor
for f in README.md metrics-inventory-backend-producers.md metrics-inventory-surfaces-fe-deps.md; do
  awk 'NR>8' "$f" | sha256sum
done
# each digest must equal the source digest in the table above
```

## Source-side integrity

The shared main checkout was **read only** during vendoring: `git status --porcelain` on the source
directory still reports `?? .agents/shared/planning/metrics-unification/` (untracked, unchanged) and the
source files' mtimes are unchanged (2026-11-01 19:46–19:59). No file in the shared checkout was created,
modified or deleted by this lane.
