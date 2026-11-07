"""POK Phase-3 seed payload — the llm-supervisor-proxy 8-slug migration set.

SOURCE OF TRUTH: ``.agents/shared/planning/project-operating-knowledge/architecture-recommendation.md``
§11 (the migration payload walkthrough — slugs/domains/audiences verbatim)
+ ``plan-overview.md`` §4.5 (dogfood protocol, same table with sizing).
Bodies are design-faithful syntheses of the §11/§4.5 note column and the
leader's critical-note substance (note ids cited per entry below). NO
invented facts. Provenance: ``observed`` for every entry (§4.5).

Every body satisfies the write-time fence (single paragraph, no leading
``#``, no ``[SYSTEM CONTEXT:``, no backticks, no newlines) and the
escape-aware budget (``len(escape_for_context_block(body)) <= 240``)
trivially; each is also held to the §11 per-body bound of 54–93
characters. The runner (``seed_llmproxy_payload.py``) re-validates all of
this with the REAL service-layer fences before any write.

Per-entry family/audience table (§11 verbatim):

=====  ==============================================  ===========  ========  ======
#      slug                                            family       domain    audience
=====  ==============================================  ===========  ========  ======
1      artifact-is-container-image                     (unordered)  artifact  both
2      pipeline-01-merge-master-triggers-image-ci      pipeline-01  deploy    both
3      pipeline-02-verify-image-tag-digest-feature     pipeline-02  deploy    operation
4      pipeline-03-deploy-order-daoduc-backup-first    pipeline-03  deploy    operation
5      deploy-01-daoduc-backup                         deploy-01    environment  operation
6      deploy-02-ensem-dev-primary                     deploy-02    environment  operation
7      healthz-in-cluster-only                         (unordered)  verify    both
8      model-catalogs-19-vs-22-not-drift               (unordered)  catalog   operation
=====  ==============================================  ===========  ========  ======

Families (§4.5 regex ``<family>-<NN>-<tail>``): ``pipeline`` (01→02→03,
ordered spine) and ``deploy`` (01/02, the ordered atomic CONTRAST pair:
backup Helm vs primary plain-kubectl). Entries 1, 7, 8 are unordered.
Resulting views (ER-1): ari/leader/devops (operation ∪ both) → all 8;
developer (implementation ∪ both) → 3 (entries 1, 2, 7).

STATUS: dev-proven (ensemble_dev, Phase 3 dogfood), LIVE-GATED — Phase 1.5
live replay runs this same module against the live llmproxy project row
and is owned by the leader (the Emperor's promote gate), not this script.

Attribution: ``agent_id='leader'`` — the design's curator-of-record for
this payload (plan §4.5 "leader-curated"); it is also what enables the
``pok_curate`` pin on entry 4 (curator gate, CURATOR_AGENT_IDS).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The target project (dev P-1 row; plan §4.5). Created via the REAL
#: project-create path when absent, never guessed.
TARGET_PROJECT_NAME = "llmproxy"
TARGET_PROJECT_MAIN_DIRECTORY = "/home/nea/Code/opensource-projects/llm-supervisor-proxy"
TARGET_PROJECT_DESCRIPTION = (
    "llm-supervisor-proxy — POK P-1 dev row (Phase-3 seeding); "
    "live row lands in Phase 1.5 (leader-gated)."
)

#: Write attribution (curator-of-record per plan §4.5) — enables the pin.
SEED_AGENT_ID = "leader"


@dataclass(frozen=True)
class SeedEntry:
    """One §11 payload row (immutable; the runner validates before write)."""

    slug: str
    domain: str
    audience: str
    body: str
    #: critical-note id(s) whose substance the body synthesizes.
    source_note_ids: tuple[str, ...]


SEED_ENTRIES: tuple[SeedEntry, ...] = (
    SeedEntry(
        slug="artifact-is-container-image",
        domain="artifact",
        audience="both",
        body=(
            "Artifact is a container image; identity is its digest plus "
            "push timestamp, not a build sha."
        ),
        source_note_ids=("68a80f90-detail",),
    ),
    SeedEntry(
        slug="pipeline-01-merge-master-triggers-image-ci",
        domain="deploy",
        audience="both",
        body=(
            "Master pushes build the image (paths filter, "
            "workflow_dispatch); version = CI run number."
        ),
        source_note_ids=("b82409f4", "1e5a371f"),
    ),
    SeedEntry(
        slug="pipeline-02-verify-image-tag-digest-feature",
        domain="deploy",
        audience="operation",
        body=(
            "Post-build, verify the deployed tag resolves to the built "
            "digest; only then is it shipped."
        ),
        source_note_ids=("68a80f90-detail", "f7511668"),
    ),
    SeedEntry(
        slug="pipeline-03-deploy-order-daoduc-backup-first",
        domain="deploy",
        audience="operation",
        body=(
            "Deploy order: backup daoduc rolls first (Helm), gate on "
            "health, then ensem primary updates."
        ),
        source_note_ids=("6dcc0ed9",),
    ),
    SeedEntry(
        slug="deploy-01-daoduc-backup",
        domain="environment",
        audience="operation",
        body=(
            "The backup deployment is a Helm release; rollouts and "
            "rollbacks move by Helm revision number."
        ),
        source_note_ids=("68a80f90-detail", "6dcc0ed9"),
    ),
    SeedEntry(
        slug="deploy-02-ensem-dev-primary",
        domain="environment",
        audience="operation",
        body=(
            "The primary deployment is plain kubectl apply; no Helm — "
            "roll forward by applying manifests."
        ),
        source_note_ids=("68a80f90-detail",),
    ),
    SeedEntry(
        slug="healthz-in-cluster-only",
        domain="verify",
        audience="both",
        body=(
            "Health answers only in-cluster; an outside probe gets 404 "
            "— false-negative, not an outage."
        ),
        source_note_ids=("68a80f90-detail",),
    ),
    SeedEntry(
        slug="model-catalogs-19-vs-22-not-drift",
        domain="catalog",
        audience="operation",
        body=(
            "Model catalogs differ per environment (19 vs 16); a count "
            "difference is config, not drift."
        ),
        source_note_ids=("14b4bbdc",),
    ),
)

#: The pinned entry (plan §4.5 row 4 — "pinned via pok_curate").
PINNED_SLUG = "pipeline-03-deploy-order-daoduc-backup-first"
