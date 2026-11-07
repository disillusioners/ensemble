"""POK D4 — the BEFORE/AFTER metadata-cap measurement harness (re-runnable).

Legs
----
1. ``dev-real`` — the DEV DB's REAL project-metadata row, fetched READ-ONLY
   from ``ensemble_dev`` (per-variable env scrubs + fence assert before any
   connect; direct asyncpg, no daemon engine side effects). Rendered through
   BOTH the vendored pre-cap renderer and the live
   ``_format_project_json_section`` import.
2. ``fixture`` — the design-calibrated payload (``pok_d4_payload_fixture``),
   whose two bases are pinned to the design-recorded live figures
   (279,810 B / 408,809 B / 68.4 %). Rendered through both renderers.

Phases
------
``--phase before`` (run PRE-edit): captures the PRE-CAP render and freezes
the baseline artifact (``.agents/shared/metrics/pok-metadata-cap-baseline.md``),
including a vendored-vs-live EQUALITY PROOF (pre-edit they must be identical —
this is what makes the vendored copy trustworthy for later phases).
``--phase after`` (run POST-edit): re-renders through the (now capped) live
renderer and writes ``...-after.md`` with the BEFORE/AFTER delta table read
from the frozen baseline.

Token accounting method: ``est_tokens = ceil(chars / 4)`` (leader-approved
approximation; no real tokenizer wired — stated in every artifact).
All payloads are ASCII post-``ensure_ascii`` ⇒ chars == bytes for the JSON
legs; the rendered section strings are likewise ASCII for these payloads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from datetime import datetime, timezone

WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, WORKTREE)

sys.path.insert(0, os.path.join(WORKTREE, "tests", "measurements"))
from pok_d4_payload_fixture import (  # noqa: E402
    POK_DESIGN_METADATA_BASIS_BYTES,
    POK_DESIGN_WHOLE_JSON_BASIS_BYTES,
    build_project_dict,
    build_project_dict_visible,
    recompute_bases,
)

METRICS_DIR = os.path.join(WORKTREE, ".agents", "shared", "metrics")
BASELINE_PATH = os.path.join(METRICS_DIR, "pok-metadata-cap-baseline.md")
AFTER_PATH = os.path.join(METRICS_DIR, "pok-metadata-cap-after.md")

# ── vendored PRE-CAP renderer (verbatim behavior of the pre-edit function) ────
# Provenance: context_messages.py @ 3cbf42721 `_format_project_json_section`
# (serialize indent=2 ensure_ascii → escape_for_context_block → fence wrap).
# The `--phase before` leg PROVES this copy byte-matches the live function
# before the edit; after the edit, this copy remains the stable BEFORE truth.


def _escape_for_context_block(content: str) -> str:
    return content.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render_precap(project_dict: dict) -> str:
    payload = json.dumps(project_dict, indent=2, ensure_ascii=True)
    payload = _escape_for_context_block(payload)
    return "## Related Project\n\n```json\n" + payload + "\n```\n"


def render_live(project_dict: dict) -> str:
    from daemon.services.context_messages import _format_project_json_section

    return _format_project_json_section(project_dict, [])


def est_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def measure(project_dict: dict, rendered: str) -> dict:
    return {
        "section_bytes": len(rendered),
        "section_est_tokens": est_tokens(rendered),
        "payload_class": "ascii" if rendered.isascii() else "mixed",
    }


# ── dev fence (per-variable scrub + assert) ──────────────────────────────────

def _dev_db_url() -> str:
    for var in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        os.environ.pop(var, None)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(WORKTREE, ".env"))
    host, db, user = (
        os.environ["POSTGRES_HOST"],
        os.environ["POSTGRES_DB"],
        os.environ["POSTGRES_USER"],
    )
    assert (db, host, user) == ("ensemble_dev", "127.0.0.1", "ensemble_dev_app"), (
        f"REFUSING to connect: {user}@{host}/{db} is not the dev fence"
    )
    return (
        f"postgres://{user}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{host}:{os.environ['POSTGRES_PORT']}/{db}"
    )


def fetch_dev_real_payload() -> dict:
    """READ-ONLY fetch of the dev DB's real project payload (to_dict shape)."""

    async def _fetch() -> dict:
        import asyncpg

        url = _dev_db_url()
        conn = await asyncpg.connect(url)
        try:
            db = await conn.fetchval("SELECT current_database()")
            assert db == "ensemble_dev", f"REFUSING: connected to {db!r}"
            row = await conn.fetchrow(
                """
                SELECT project_id, name, project_type, status, main_directory,
                       related_directories, description, job_queue_paused,
                       creator_instance_id, creator_agent_id, created_at, updated_at
                FROM projects WHERE name = '__system_default__'
                """
            )
            assert row is not None, "dev __system_default__ row missing"
            meta_rows = await conn.fetch(
                """
                SELECT meta_key, meta_value FROM project_metadata_records
                WHERE project_id = $1 ORDER BY meta_key
                """,
                row["project_id"],
            )
            tag_rows = await conn.fetch(
                "SELECT tag FROM project_tags WHERE project_id = $1 ORDER BY tag",
                row["project_id"],
            )
            short_rows = await conn.fetch(
                "SELECT shortname FROM project_shortnames WHERE project_id = $1 ORDER BY shortname",
                row["project_id"],
            )
            return {
                "project_id": row["project_id"],
                "name": row["name"],
                "project_type": row["project_type"],
                "status": row["status"],
                "main_directory": row["main_directory"],
                "related_directories": json.loads(row["related_directories"] or "[]"),
                "description": row["description"],
                "job_queue_paused": row["job_queue_paused"],
                "tags": [r["tag"] for r in tag_rows],
                "shortnames": [r["shortname"] for r in short_rows],
                "metadata": {r["meta_key"]: json.loads(r["meta_value"]) for r in meta_rows},
                "relationships": {},
                "creator_instance_id": row["creator_instance_id"],
                "creator_agent_id": row["creator_agent_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            await conn.close()

    return asyncio.run(_fetch())


# ── artifact writing ─────────────────────────────────────────────────────────

def _json_block(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=2, sort_keys=True) + "\n```\n"


def run_before() -> None:
    os.makedirs(METRICS_DIR, exist_ok=True)
    fixture = build_project_dict_visible()  # renderer-input shape (notes dropped)
    fixture_full = build_project_dict()     # full record (design bases anchor)
    dev_real = fetch_dev_real_payload()

    # Vendored-vs-live equality proof (pre-edit they MUST match byte-for-byte).
    proof_fixture = render_precap(fixture) == render_live(fixture)
    proof_dev = render_precap(dev_real) == render_live(dev_real)
    assert proof_fixture and proof_dev, (
        "vendored pre-cap renderer does NOT match the live pre-edit function — "
        "baseline provenance broken"
    )

    fixture_precap = render_precap(fixture)
    dev_precap = render_precap(dev_real)
    data = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "head_at_capture": os.popen("git -C %s rev-parse --short=9 HEAD" % WORKTREE).read().strip(),
        "renderer_phase": "pre-cap",
        "vendored_matches_live": True,
        "token_method": "ceil(chars/4); ASCII payloads so chars==bytes",
        "dev_real": {
            "source": "ensemble_dev @ 127.0.0.1:5432 (READ-ONLY SELECT, fence-asserted)",
            "row": "__system_default__ (the only metadata-bearing row in dev)",
            **measure(dev_real, dev_precap),
            "metadata_keys": len(dev_real["metadata"]),
        },
        "fixture": {
            "source": "tests/measurements/pok_d4_payload_fixture.py (design-calibrated)",
            **measure(fixture, fixture_precap),
            "metadata_basis_bytes": recompute_bases(fixture_full)["metadata_basis_bytes"],
            "whole_json_basis_bytes": recompute_bases(fixture_full)["whole_json_basis_bytes"],
            "renderer_visible_basis_bytes": recompute_bases(fixture_full)["renderer_visible_basis_bytes"],
            "ratio_percent": recompute_bases(fixture_full)["ratio_percent"],
            "metadata_keys": len(fixture["metadata"]),
        },
        "design_recorded": {
            "metadata_basis_bytes": POK_DESIGN_METADATA_BASIS_BYTES,
            "whole_json_basis_bytes": POK_DESIGN_WHOLE_JSON_BASIS_BYTES,
            "ratio_percent": 68.4,
            "note": "LIVE bases (arch §2 row 3 / §4.8); live row fenced from this lane",
        },
    }
    with open(BASELINE_PATH, "w") as f:
        f.write(
            "# POK D4 — metadata-cap BASELINE (BEFORE truth, pre-cap renderer)\n\n"
            f"Captured {data['captured_at_utc']} @ HEAD {data['head_at_capture']} "
            "(pre-edit capture; vendored renderer proven byte-identical to the live "
            "pre-cap `_format_project_json_section`).\n\n"
            "Two payloads: the DEV DB's REAL row (ensemble_dev, read-only, fence-asserted — "
            "the dev DB carries only `__system_default__`, tiny) and the design-calibrated "
            "fixture (bases pinned to the design-recorded live figures; content synthetic, "
            "size distribution design-faithful). LIVE re-measure stays gated on Phase 1.5.\n\n"
            + _json_block(data)
        )
    print(json.dumps(data, indent=2, sort_keys=True))


def run_after() -> None:
    import re

    fixture = build_project_dict_visible()  # renderer-input shape
    fixture_full = build_project_dict()
    dev_real = fetch_dev_real_payload()

    baseline_raw = open(BASELINE_PATH).read()
    m = re.search(r"```json\n(.*)\n```", baseline_raw, re.DOTALL)
    assert m, "baseline artifact missing its JSON block"
    baseline = json.loads(m.group(1))

    fixture_after = render_live(fixture)
    dev_after = render_live(dev_real)
    fixture_precap = render_precap(fixture)  # vendored BEFORE renderer (frozen truth)
    dev_precap = render_precap(dev_real)

    assert fixture_after != fixture_precap or len(fixture["metadata"]) == 0, (
        "post-edit live renderer returned the pre-cap render — cap did not engage?"
    )

    # Cap-mechanism observables on the fixture AFTER render.
    from daemon.services.context_messages import (
        POK_METADATA_ALLOWLIST,
        POK_METADATA_KEY_CAP_CHARS,
        POK_METADATA_SECTION_CAP_CHARS,
    )

    omitted = json.dumps({"_omitted_metadata_keys": None})  # shape doc only
    omitted_count = 0
    m2 = re.search(r'"_omitted_metadata_keys": (\d+)', fixture_after)
    if m2:
        omitted_count = int(m2.group(1))
    capped_keys = sum(
        1
        for k, v in fixture["metadata"].items()
        if k not in POK_METADATA_ALLOWLIST and not k.startswith("plugin_")
    )

    data = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "head_at_capture": os.popen("git -C %s rev-parse --short=9 HEAD" % WORKTREE).read().strip(),
        "renderer_phase": "post-cap",
        "token_method": baseline["token_method"],
        "dev_real": {
            "source": baseline["dev_real"]["source"],
            "row": baseline["dev_real"]["row"],
            "before": {k: baseline["dev_real"][k] for k in ("section_bytes", "section_est_tokens")},
            "after": measure(dev_real, dev_after),
            "metadata_keys": len(dev_real["metadata"]),
        },
        "fixture": {
            "source": baseline["fixture"]["source"],
            "before": {k: baseline["fixture"][k] for k in ("section_bytes", "section_est_tokens")},
            "after": measure(fixture, fixture_after),
            "before_recomputed_via_vendored": measure(fixture, fixture_precap),
            "metadata_basis_bytes": recompute_bases(fixture_full)["metadata_basis_bytes"],
            "whole_json_basis_bytes": recompute_bases(fixture_full)["whole_json_basis_bytes"],
            "renderer_visible_basis_bytes": recompute_bases(fixture_full)["renderer_visible_basis_bytes"],
            "ratio_percent": recompute_bases(fixture_full)["ratio_percent"],
            "metadata_keys": len(fixture["metadata"]),
            "non_allowlisted_keys": capped_keys,
            "omitted_count_tail": omitted_count,
            "per_key_cap_chars": POK_METADATA_KEY_CAP_CHARS,
            "section_cap_chars": POK_METADATA_SECTION_CAP_CHARS,
            "allowlist_size_exact_names": len(POK_METADATA_ALLOWLIST),
        },
        "deltas": {},
    }
    fb, fa = baseline["fixture"]["section_bytes"], data["fixture"]["after"]["section_bytes"]
    data["deltas"] = {
        "fixture_section_bytes_before": fb,
        "fixture_section_bytes_after": fa,
        "fixture_section_bytes_saved": fb - fa,
        "fixture_section_saved_pct": round((fb - fa) / fb * 100, 2),
        "fixture_est_tokens_before": baseline["fixture"]["section_est_tokens"],
        "fixture_est_tokens_after": data["fixture"]["after"]["section_est_tokens"],
    }

    with open(AFTER_PATH, "w") as f:
        f.write(
            "# POK D4 — metadata-cap AFTER measurement (post-cap renderer)\n\n"
            f"Captured {data['captured_at_utc']} @ HEAD {data['head_at_capture']}.\n"
            "BEFORE numbers are read from the frozen baseline artifact "
            "(pre-edit capture, vendored-proven). AFTER numbers are the live "
            "`_format_project_json_section` post-edit.\n\n"
            + _json_block(data)
        )
    print(json.dumps(data, indent=2, sort_keys=True))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["before", "after"], required=True)
    args = ap.parse_args()
    if args.phase == "before":
        run_before()
    else:
        run_after()


if __name__ == "__main__":
    main()
