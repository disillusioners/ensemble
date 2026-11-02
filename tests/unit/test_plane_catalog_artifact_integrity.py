"""Gate-(i) artifact-integrity pins (lane ``plane-pm-issue-tools``, Phase 1).

The single scoped verification node the Phase-1 commission authorises
("Run ONLY: a JSON validity + membership assertion over the keep-set and the
fixture … a single targeted pytest node you add under ``tests/unit/``").

Covers:

* **P-G4 / X1.10** — ``gate-i-keep-set.json`` exists, is machine-readable,
  carries exactly the 28 enumerated verbs in both forms, every raw verb is a
  member of the re-captured catalog, and the pruned count + sorted-name
  sha256 are recorded. Its **RED legs are demonstrated here**: deleting a
  keep-set entry, adding a name absent from the catalog, corrupting the
  daemon-name pairing, and falsifying the sha256 each turn the assertion RED.
* **P-G1a/P1-A** — the fixture carries the discovery verbs' required params
  and the per-tool schema token counts.
* **P-G1/P-G1b/P1-B** — the fixture's census fields, ``is_draft`` on
  ``create_work_item``, zero ``*_issue`` verbs, and the sorted-name sha256
  the FC2/FC10 drift detector diffs against.
"""

from __future__ import annotations

import copy

import pytest

from tests.helpers.plane_catalog_fixture import (
    CatalogIntegrityError,
    assert_keep_set_matches_catalog,
    catalog_names,
    compute_sorted_names_sha256,
    load_catalog,
    load_keep_set,
    schema_token_total,
    sorted_names_sha256,
    tool_properties,
    tool_required,
    tool_schema_tokens,
)

KEEP_SET_COUNT = 28
KEEP_SET_RAW_VERBS = {
    # work-item CRUD (8)
    "create_work_item", "update_work_item", "delete_work_item",
    "retrieve_work_item", "retrieve_work_item_by_identifier",
    "list_work_items", "search_work_items", "count_work_items",
    # comments (5)
    "create_work_item_comment", "list_work_item_comments",
    "update_work_item_comment", "delete_work_item_comment",
    "retrieve_work_item_comment",
    # cycles (10)
    "create_cycle", "update_cycle", "delete_cycle", "list_cycles",
    "retrieve_cycle", "complete_cycle", "manage_cycle_work_items",
    "transfer_cycle_work_items", "list_cycle_work_items",
    "manage_cycle_archive",
    # assignee (1)
    "manage_work_item_assignee",
    # discovery / typing reads (4)
    "list_projects", "list_work_item_types", "resolve_work_item_type",
    "list_states",
}


@pytest.fixture(scope="module")
def catalog() -> dict:
    return load_catalog()


@pytest.fixture(scope="module")
def keep_set() -> dict:
    return load_keep_set()


# ── fixture integrity (P-G1 / P-G1a / P1-B) ────────────────────────────────

def test_fixture_is_valid_and_sorted(catalog: dict) -> None:
    assert catalog["catalog_source"] == "live-recapture"
    assert catalog["tool_count"] == len(catalog["sorted_names"]) == 177
    assert catalog["sorted_names"] == sorted(catalog["sorted_names"])
    # no credential VALUE rides in the fixture: key NAMES are allowed (the
    # plan mandates key-names-only), but no bearer/authorization material and
    # no PLANE_* mention outside the declared env_names_read list may appear
    import json

    raw = json.dumps(catalog)
    assert "Bearer " not in raw
    assert "Authorization" not in raw
    assert raw.count("PLANE_") == len(catalog["capture"]["env_names_read"])


def test_sorted_names_sha256_matches(catalog: dict) -> None:
    assert catalog["sorted_names_sha256"] == sorted_names_sha256(catalog)
    assert catalog["sorted_names_sha256_algorithm"] == (
        'sha256( "\\n".join(sorted_names) + "\\n" ) over UTF-8'
    )


def test_zero_issue_verbs(catalog: dict) -> None:
    # criterion 3: no *_issue verb exists — the charter must name *_work_item
    issue = [n for n in catalog_names(catalog) if n.endswith("_issue") or "_issue_" in n]
    assert issue == []


def test_create_work_item_has_is_draft(catalog: dict) -> None:
    # P-G1: the Task-3 draft capability is directly expressible
    assert "is_draft" in tool_properties("create_work_item", catalog)
    assert set(tool_required("create_work_item", catalog)) == {"project_id", "name"}


def test_discovery_verb_required_params_captured(catalog: dict) -> None:
    # P1-A: required params are a CAPTURE, not an inference
    expected = {
        "list_projects": [],
        "list_states": ["project_id"],
        "retrieve_state": ["project_id", "state_id"],
        "list_work_item_types": [],
        "retrieve_work_item_type": ["work_item_type_id"],
        "resolve_work_item_type": ["project_id", "name"],
        "list_labels": ["project_id"],
        "search_work_items": ["query"],
    }
    for verb, req in expected.items():
        assert tool_required(verb, catalog) == sorted(req), verb
    # honest capture: the architect's "members" family verb does NOT exist
    assert "list_members" not in catalog_names(catalog)


def test_three_number_census(catalog: dict) -> None:
    # P1-B: the corrected classifier (73/104/0) and the on-disk daemon
    # classifier (48/82/47) both reproduce from the fixture's own names.
    names = catalog_names(catalog)
    read_corrected = ("list_", "get_", "search_", "retrieve_", "count_", "read_")
    write_corrected = ("create_", "update_", "delete_", "remove_", "set_", "manage_",
                       "transfer_", "complete_", "link_", "import_", "attach_",
                       "detach_", "upload_", "resolve_")
    read_set = {n for n in names if n.startswith(read_corrected)}
    write_set = {n for n in names if n.startswith(write_corrected)}
    assert len(read_set) == 73 and len(write_set) == 104
    assert read_set | write_set == set(names)  # unclassified == 0

    read_daemon = ("list_", "get_", "search_")
    write_daemon = ("create_", "update_", "delete_", "add_", "remove_", "set_",
                    "edit_", "assign_")
    rd = {n for n in names if n.startswith(read_daemon)}
    wd = {n for n in names if n.startswith(write_daemon)}
    assert (len(rd), len(wd), len(names) - len(rd | wd)) == (48, 82, 47)


def test_per_tool_schema_token_counts_present(catalog: dict) -> None:
    # P1-A: per-tool token counts feed FC9 — every tool carries one
    schemas = catalog["tool_schemas"]
    assert len(schemas) == catalog["tool_count"]
    assert all(isinstance(v["schema_tokens"], int) and v["schema_tokens"] > 0
               for v in schemas.values())
    assert sum(v["schema_tokens"] for v in schemas.values()) == schema_token_total(catalog)


# ── keep-set artifact integrity (P-G4 / X1.10) ─────────────────────────────

def test_keep_set_green(keep_set: dict, catalog: dict) -> None:
    verdict = assert_keep_set_matches_catalog(keep_set, catalog,
                                              expected_count=KEEP_SET_COUNT)
    assert verdict["fc1_fires"] is False
    assert verdict["pruned_count"] == 149
    assert {e["raw_verb"] for e in keep_set["keep_set"]} == KEEP_SET_RAW_VERBS


# ── RED legs (X1.10: "Its RED leg must be demonstrated") ───────────────────

def test_red_leg_delete_entry(keep_set: dict, catalog: dict) -> None:
    mutated = copy.deepcopy(keep_set)
    mutated["keep_set"].pop()
    with pytest.raises(CatalogIntegrityError, match="expected exactly 28"):
        assert_keep_set_matches_catalog(mutated, catalog)


def test_red_leg_add_absent_name(keep_set: dict, catalog: dict) -> None:
    # introduce a name that is NOT in the catalog (keeping the count at 28 so
    # the FC1 membership branch is the one that fires)
    mutated = copy.deepcopy(keep_set)
    entry = next(e for e in mutated["keep_set"] if e["raw_verb"] == "create_work_item")
    entry["raw_verb"] = "create_issue"
    entry["daemon_name"] = "plane_create_issue"
    with pytest.raises(CatalogIntegrityError, match="FC1"):
        assert_keep_set_matches_catalog(mutated, catalog)


def test_red_leg_append_absent_name(keep_set: dict, catalog: dict) -> None:
    # the plan's literal second form: ADD a name absent from the catalog —
    # the count guard fires (the assertion goes RED either way)
    mutated = copy.deepcopy(keep_set)
    mutated["keep_set"].append(
        {"family": "bogus", "raw_verb": "create_issue",
         "daemon_name": "plane_create_issue"}
    )
    with pytest.raises(CatalogIntegrityError, match="expected exactly 28"):
        assert_keep_set_matches_catalog(mutated, catalog)


def test_red_leg_daemon_name_drift(keep_set: dict, catalog: dict) -> None:
    mutated = copy.deepcopy(keep_set)
    mutated["keep_set"][0]["daemon_name"] = "plane_list_issues"
    with pytest.raises(CatalogIntegrityError, match="daemon_name"):
        assert_keep_set_matches_catalog(mutated, catalog)


def test_red_leg_sha256_falsified(keep_set: dict, catalog: dict) -> None:
    mutated = copy.deepcopy(keep_set)
    mutated["keep_set_sorted_names_sha256"] = "0" * 64
    with pytest.raises(CatalogIntegrityError, match="sha256"):
        assert_keep_set_matches_catalog(mutated, catalog)


def test_red_leg_pruned_count_falsified(keep_set: dict, catalog: dict) -> None:
    mutated = copy.deepcopy(keep_set)
    mutated["pruned_count"] = 148
    with pytest.raises(CatalogIntegrityError, match="pruned_count"):
        assert_keep_set_matches_catalog(mutated, catalog)


def test_red_leg_fixture_missing_tool(tmp_path, keep_set: dict) -> None:
    # a catalog that lost a keep-set verb also goes RED (the FC2/FC10 drift
    # direction: the CATALOG moved, not the keep-set)
    catalog2 = load_catalog()
    catalog2["sorted_names"] = [
        n for n in catalog2["sorted_names"] if n != "manage_work_item_assignee"
    ]
    del catalog2["tool_schemas"]["manage_work_item_assignee"]
    catalog2["tool_count"] = len(catalog2["sorted_names"])
    with pytest.raises(CatalogIntegrityError, match="FC1"):
        assert_keep_set_matches_catalog(keep_set, catalog2)


# ── loader import smoke (commission deliverable 4) ─────────────────────────

def test_loader_roundtrip() -> None:
    cat = load_catalog()
    assert tool_schema_tokens("create_work_item", cat) > 0
    assert compute_sorted_names_sha256(["b", "a"]) == compute_sorted_names_sha256(
        ["a", "b"]
    )
