"""Unit tests for the designer-agent tmp_images substrate extensions.

Phase 1 WP7 (provenance sidecar), WP8 (agent-facing tools —
``image_save`` / ``image_list`` / ``image_get``), WP9 (protected
retention class).

Pins:

* **Backward-compat (WP7).** Old-format sidecars (no ``provenance`` /
  no ``retention_class`` keys) read unchanged: ``open_full`` /
  ``list_records`` / ``get_retention_class`` all project ``None``
  + ``"normal"``. A save with both extensions at defaults writes a
  sidecar whose JSON has NEITHER extension key — byte-identical to
  the prior Phase 1 shape.
* **Filter semantics (WP8 / tool-side).** ``list_records`` returns
  EXACT subsets when feature / page / version / source_agent /
  retention_class constraints are AND-combined. Torn / missing
  sidecars are EXCLUDED from listing — the orphan-view precedent.
* **Protected retention (WP9).** A protected entry with an mtime
  aged well past 30 d SURVIVES a single ``sweep_once`` while a
  normal control aged the same way IS swept. The cap accounting
  includes protected bytes (``TmpImageStoreFull`` path unchanged).
* **MIME source-of-truth (WP8 / tool-side).** ``image_get`` returns
  the sidecar's ``content_type`` verbatim — never extension-guessed
  — and round-trips the bytes unchanged.
* **Auto-stamp (WP8 / tool-side).** ``image_save`` AUTO-STAMPS
  ``source_agent`` with the calling instance's agent_id when no
  ``source_agent`` is explicitly passed.

Test conventions follow the existing
``tests/unit/services/test_tmp_image_store.py`` (tmp_path fixtures,
small-cap store instances for the cap test, single-TmpImageStore
instances for cap accounting).
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services.tmp_image_cleanup_service import TmpImageCleanupService
from daemon.services.tmp_image_store import (
    TmpImageNotFound,
    TmpImageStore,
    TmpImageStoreFull,
    TmpImageRecord,
    _METADATA_SUFFIX,
    _RETENTION_CLASS_NORMAL,
    _RETENTION_CLASS_PROTECTED,
    _RETENTION_CLASS_KEY,
    _PROVENANCE_KEY,
)
from daemon.tools import image_tools as image_tools_module
from daemon.tools.image_tools import create_image_tools


# ---------------------------------------------------------------------------
# Group A — provenance sidecar (WP7)
# ---------------------------------------------------------------------------


_VALID_HEX = "a" * 32
_OTHER_HEX = "b" * 32


def _make_store(tmp_path: Path, max_bytes: int = 1024 * 1024) -> TmpImageStore:
    store = TmpImageStore(tmp_path, max_bytes=max_bytes)
    store.init()
    return store


def _sidecar_path(store: TmpImageStore, image_id: str) -> Path:
    return store.dir / f"{image_id}{_METADATA_SUFFIX}"


def _sidecar_dict(store: TmpImageStore, image_id: str) -> dict[str, Any]:
    return json.loads(_sidecar_path(store, image_id).read_text(encoding="utf-8"))


class TestProvenanceSidecarBackwardCompat:
    """WP7 — old-format sidecars must read unchanged. No migration."""

    def test_default_save_omits_provenance_key(self, tmp_path):
        store = _make_store(tmp_path)
        rec = store.save(_VALID_HEX, b"x", "image/png")
        assert rec.provenance is None
        assert rec.retention_class == _RETENTION_CLASS_NORMAL
        sidecar = _sidecar_dict(store, _VALID_HEX)
        assert _PROVENANCE_KEY not in sidecar, (
            f"clipboard-path save must not write the provenance key; "
            f"got sidecar={sidecar!r}"
        )
        assert _RETENTION_CLASS_KEY not in sidecar, (
            f"clipboard-path save must not write the retention_class key "
            f"when at default; got sidecar={sidecar!r}"
        )

    def test_default_save_sidecar_is_byte_compatible_with_phase_1(
        self, tmp_path
    ):
        # Pin the byte-shape of the canonical 4-key sidecar so the
        # WP7 extension is provably non-disruptive to the existing
        # clipboard write path. The expected keys + ordering match
        # the pre-WP7 save() — explicitly NO extension keys.
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png")
        raw = _sidecar_path(store, _VALID_HEX).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert set(parsed.keys()) == {
            "content_type",
            "size_bytes",
            "uploaded_at",
            "sha256_hex",
        }, f"clipboard sidecar must be byte-identical; got keys {sorted(parsed.keys())}"

    def test_old_sidecar_reads_with_none_provenance_and_normal_class(
        self, tmp_path
    ):
        # Pre-WP7 sidecar written by hand — no extension keys — must
        # parse cleanly into a TmpImageRecord with default
        # provenance + retention.
        store = _make_store(tmp_path)
        # Write a blob + sidecar using the pre-WP7 shape only.
        (store.dir / _VALID_HEX).write_bytes(b"x")
        _sidecar_path(store, _VALID_HEX).write_text(
            json.dumps(
                {
                    "content_type": "image/png",
                    "size_bytes": 1,
                    "uploaded_at": "2026-01-01T00:00:00+00:00",
                    "sha256_hex": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
        record = store.open_full(_VALID_HEX)
        assert record.provenance is None
        assert record.retention_class == _RETENTION_CLASS_NORMAL
        assert record.content_type == "image/png"

    def test_save_with_provenance_persists_provenance(self, tmp_path):
        provenance = {
            "feature": "checkout",
            "page": "home",
            "version": "v1.2",
            "source_agent": "designer",
        }
        store = _make_store(tmp_path)
        rec = store.save(_VALID_HEX, b"x", "image/png", provenance=provenance)
        assert rec.provenance == provenance
        sidecar = _sidecar_dict(store, _VALID_HEX)
        assert sidecar[_PROVENANCE_KEY] == provenance

    def test_save_provenance_is_deep_copied(self, tmp_path):
        # Provenance is a mutable dict — we must not retain a
        # reference to the caller's mapping (post-save mutation must
        # not affect the stored JSON).
        store = _make_store(tmp_path)
        prov: dict[str, str] = {"feature": "f1"}
        store.save(_VALID_HEX, b"x", "image/png", provenance=prov)
        prov["feature"] = "mutated"
        record = store.open_full(_VALID_HEX)
        assert record.provenance == {"feature": "f1"}, (
            "store must hold a defensive copy of the provenance "
            "mapping; caller mutation must not bleed into storage"
        )

    def test_save_invalid_retention_class_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="retention_class"):
            store.save(
                _VALID_HEX, b"x", "image/png", retention_class="bogus"
            )

    def test_save_protected_persists_protected_class(self, tmp_path):
        store = _make_store(tmp_path)
        rec = store.save(
            _VALID_HEX, b"x", "image/png", retention_class="protected"
        )
        assert rec.retention_class == "protected"
        sidecar = _sidecar_dict(store, _VALID_HEX)
        assert sidecar[_RETENTION_CLASS_KEY] == "protected"


# ---------------------------------------------------------------------------
# Group B — list_records filters + torn-sidecar exclusion
# ---------------------------------------------------------------------------


class TestListRecordsFilters:
    """WP8 — list_records returns exact subsets; torn excluded."""

    def test_list_all_returns_every_record(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png")
        store.save(_OTHER_HEX, b"y", "image/png")
        records = store.list_records()
        assert sorted(r.image_id for r in records) == [_VALID_HEX, _OTHER_HEX]

    def test_list_filters_by_feature(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(
            _VALID_HEX,
            b"x",
            "image/png",
            provenance={"feature": "checkout", "page": "home"},
        )
        store.save(
            _OTHER_HEX,
            b"y",
            "image/png",
            provenance={"feature": "settings", "page": "profile"},
        )
        recs = store.list_records(feature="checkout")
        assert [r.image_id for r in recs] == [_VALID_HEX]
        recs = store.list_records(feature="settings")
        assert [r.image_id for r in recs] == [_OTHER_HEX]
        recs = store.list_records(feature="nonexistent")
        assert recs == []

    def test_list_filters_by_source_agent(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(
            _VALID_HEX,
            b"x",
            "image/png",
            provenance={"source_agent": "designer"},
        )
        store.save(
            _OTHER_HEX,
            b"y",
            "image/png",
            provenance={"source_agent": "tester"},
        )
        recs = store.list_records(source_agent="designer")
        assert [r.image_id for r in recs] == [_VALID_HEX]

    def test_list_filters_by_retention_class(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(
            _VALID_HEX, b"x", "image/png", retention_class="protected"
        )
        store.save(_OTHER_HEX, b"y", "image/png")
        prot = store.list_records(retention_class="protected")
        assert [r.image_id for r in prot] == [_VALID_HEX]
        norm = store.list_records(retention_class="normal")
        assert [r.image_id for r in norm] == [_OTHER_HEX]

    def test_list_combined_filters_are_AND(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(
            _VALID_HEX,
            b"x",
            "image/png",
            provenance={"feature": "f1", "source_agent": "designer"},
            retention_class="protected",
        )
        store.save(
            _OTHER_HEX,
            b"y",
            "image/png",
            provenance={"feature": "f1", "source_agent": "tester"},
            retention_class="normal",
        )
        # feature=f1 AND source_agent=designer → only the first
        matches = store.list_records(
            feature="f1", source_agent="designer"
        )
        assert [r.image_id for r in matches] == [_VALID_HEX]
        # feature=f1 AND retention=protected → only the first
        matches = store.list_records(
            feature="f1", retention_class="protected"
        )
        assert [r.image_id for r in matches] == [_VALID_HEX]
        # source_agent=tester AND retention=normal → only the second
        matches = store.list_records(
            source_agent="tester", retention_class="normal"
        )
        assert [r.image_id for r in matches] == [_OTHER_HEX]
        # feature=f1 AND retention=protected AND source=tester → empty
        matches = store.list_records(
            feature="f1",
            retention_class="protected",
            source_agent="tester",
        )
        assert matches == []

    def test_untagged_records_cannot_satisfy_provenance_filter(self, tmp_path):
        # A record with NO provenance cannot match a non-None
        # provenance filter — "I want rows tagged X" excludes "no
        # tags at all".
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png")  # no provenance
        recs = store.list_records(feature="checkout")
        assert recs == []
        # But listing with all-None filters still returns it.
        recs = store.list_records()
        assert [r.image_id for r in recs] == [_VALID_HEX]

    def test_list_excludes_torn_sidecar(self, tmp_path):
        # Orphan-sidecar scenario: a blob WITHOUT sidecar (torn
        # mid-save). open_full / list_records must treat as
        # un-readable (404-style), not as an empty record.
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png")
        # Manually delete the sidecar only — the blob stays.
        _sidecar_path(store, _VALID_HEX).unlink()
        recs = store.list_records()
        # Torn entry must NOT appear in listing.
        assert recs == []
        with pytest.raises(TmpImageNotFound):
            store.open_full(_VALID_HEX)

    def test_list_excludes_invalid_json_sidecar(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png")
        _sidecar_path(store, _VALID_HEX).write_text(
            "{not valid json", encoding="utf-8"
        )
        recs = store.list_records()
        assert recs == []
        with pytest.raises(TmpImageNotFound):
            store.open_full(_VALID_HEX)

    def test_get_retention_class_returns_stored_value(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png", retention_class="protected")
        store.save(_OTHER_HEX, b"y", "image/png", retention_class="normal")
        assert store.get_retention_class(_VALID_HEX) == "protected"
        assert store.get_retention_class(_OTHER_HEX) == "normal"

    def test_get_retention_class_none_when_missing_or_orphan(self, tmp_path):
        store = _make_store(tmp_path)
        # No entries at all → None.
        assert store.get_retention_class(_VALID_HEX) is None
        # Save + delete sidecar → orphan → None.
        store.save(_VALID_HEX, b"x", "image/png")
        _sidecar_path(store, _VALID_HEX).unlink()
        assert store.get_retention_class(_VALID_HEX) is None
        # Invalid JSON → None (defensive — sweep must not exempt).
        store.save(_OTHER_HEX, b"y", "image/png")
        _sidecar_path(store, _OTHER_HEX).write_text(
            "{bad", encoding="utf-8"
        )
        assert store.get_retention_class(_OTHER_HEX) is None

    def test_get_retention_class_unknown_value_treated_as_normal(
        self, tmp_path
    ):
        # Fail-open: a tampered / legacy value other than the
        # literal "protected" string reads as "normal" so it
        # cannot accidentally exempt an entry from the sweep.
        store = _make_store(tmp_path)
        store.save(_VALID_HEX, b"x", "image/png")
        sidecar = _sidecar_dict(store, _VALID_HEX)
        sidecar[_RETENTION_CLASS_KEY] = "PROTECTED"  # wrong case
        _sidecar_path(store, _VALID_HEX).write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        assert store.get_retention_class(_VALID_HEX) == "normal"


# ---------------------------------------------------------------------------
# Group C — protected retention survives sweep + cap accounting (WP9)
# ---------------------------------------------------------------------------


_FROZEN_NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
_DAYS = 30
_FROZEN_CUTOFF = _FROZEN_NOW.timestamp() - _DAYS * 86400


def _set_sidecar_uploaded(
    store: TmpImageStore, image_id: str, dt: datetime
) -> None:
    sp = _sidecar_path(store, image_id)
    meta = json.loads(sp.read_text(encoding="utf-8"))
    meta["uploaded_at"] = dt.isoformat()
    sp.write_text(json.dumps(meta), encoding="utf-8")


def _backdate_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def _old_dt(seconds_before_cutoff: float = 86400) -> datetime:
    return datetime.fromtimestamp(
        _FROZEN_CUTOFF - seconds_before_cutoff, tz=timezone.utc
    )


def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    import daemon.services.tmp_image_cleanup_service as mod

    monkeypatch.setattr(mod, "now_utc", lambda: _FROZEN_NOW)


class TestProtectedRetentionSweep:
    """WP9 — protected entries survive sweep; normal entries do not."""

    async def test_protected_entry_survives_sweep_aged_past_window(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        prot_id = "0" * 32
        norm_id = "1" * 32
        store.save(
            prot_id,
            b"protected-payload",
            "image/png",
            retention_class="protected",
        )
        store.save(norm_id, b"normal-payload", "image/png")
        # Both aged past the 30-day cutoff via uploaded_at.
        old = _old_dt()
        _set_sidecar_uploaded(store, prot_id, old)
        _set_sidecar_uploaded(store, norm_id, old)
        # mtime too (the sweep's fallback path).
        for name in (prot_id, f"{prot_id}.json", norm_id, f"{norm_id}.json"):
            _backdate_mtime(store.dir / name, _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(store, interval_seconds=3600)
        freeze_now(monkeypatch)
        deleted = await svc.sweep_once()
        assert deleted == 1, (
            f"sweep must reap exactly the normal entry; got deleted={deleted}"
        )
        # Protected entry's blob + sidecar both survive.
        assert (store.dir / prot_id).exists(), "protected blob must survive"
        assert _sidecar_path(store, prot_id).exists(), (
            "protected sidecar must survive"
        )
        # Normal entry's blob + sidecar both reaped.
        assert not (store.dir / norm_id).exists(), "normal blob must be reaped"
        assert not _sidecar_path(store, norm_id).exists(), (
            "normal sidecar must be reaped"
        )

    async def test_protected_orphan_sidecar_survives_sweep(
        self, tmp_path, monkeypatch
    ):
        # Pass 2 of the sweep — protected orphan (blob gone, sidecar
        # remains) is also exempt.
        store = _make_store(tmp_path)
        prot_id = "0" * 32
        norm_id = "1" * 32
        store.save(prot_id, b"x", "image/png", retention_class="protected")
        store.save(norm_id, b"x", "image/png")
        # Delete the protected blob only — leaves a protected orphan sidecar.
        (store.dir / prot_id).unlink()
        # Backdate everything well past 30d.
        old = _old_dt()
        _set_sidecar_uploaded(store, prot_id, old)
        _set_sidecar_uploaded(store, norm_id, old)
        for name in (f"{prot_id}.json", norm_id, f"{norm_id}.json"):
            _backdate_mtime(store.dir / name, _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(store, interval_seconds=3600)
        freeze_now(monkeypatch)
        await svc.sweep_once()
        # Protected orphan sidecar survives.
        assert _sidecar_path(store, prot_id).exists(), (
            "protected orphan sidecar must survive (sweep pass 2 exempt)"
        )
        # Normal pair reaped.
        assert not (store.dir / norm_id).exists()
        assert not _sidecar_path(store, norm_id).exists()

    def test_cap_accounts_for_protected_bytes(self, tmp_path):
        # The cap is total disk usage regardless of class — a
        # protected entry still consumes the budget. The store has
        # no silent protected-eviction path (matches the WP9 AC).
        # First save: ~ 50 bytes (blob) + ~210 bytes (sidecar incl.
        # ``retention_class`` + ``provenance`` keys) = ~260 bytes
        # consumed against a 350-byte cap. Second save of similar
        # size would push the projected total over the cap → must
        # raise ``TmpImageStoreFull`` (no silent protected-eviction).
        store = _make_store(tmp_path, max_bytes=350)
        store.save(
            "0" * 32, b"x" * 50, "image/png", retention_class="protected",
        )
        with pytest.raises(TmpImageStoreFull):
            store.save(
                "1" * 32, b"y" * 50, "image/png", retention_class="protected"
            )

    async def test_legacy_normal_entry_sweep_still_works_with_extension_on(
        self, tmp_path, monkeypatch
    ):
        # Regression: existing pre-WP9 normal entries (no
        # retention_class key on disk) must be reaped by the
        # extended sweep — the extension never LETS an entry pass
        # accidentally.
        store = _make_store(tmp_path)
        norm_id = "a" * 32
        store.save(norm_id, b"x", "image/png")  # default retention
        # Confirm the sidecar lacks the retention_class key.
        assert _RETENTION_CLASS_KEY not in _sidecar_dict(store, norm_id)
        old = _old_dt()
        _set_sidecar_uploaded(store, norm_id, old)
        for name in (norm_id, f"{norm_id}.json"):
            _backdate_mtime(store.dir / name, _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(store, interval_seconds=3600)
        freeze_now(monkeypatch)
        deleted = await svc.sweep_once()
        assert deleted == 1
        assert not (store.dir / norm_id).exists()


# ---------------------------------------------------------------------------
# Group D — image_save / image_list / image_get tools (WP8)
# ---------------------------------------------------------------------------


def _stub_manager(tmp_store: TmpImageStore, *, agent_id: str = "designer"):
    """Build a MagicMock manager matching the contract the tools use.

    ``manager.tmp_image_store`` returns ``tmp_store``;
    ``manager._instance_repository.get(current_instance_id)`` returns
    a stub whose ``agent_id`` is the supplied ``agent_id``. ``tools/
    image_tools`` swallows everything via ``getattr`` so any other
    accessor is a no-op.
    """
    manager = MagicMock()
    manager.tmp_image_store = tmp_store
    instance_stub = MagicMock()
    instance_stub.agent_id = agent_id
    instance_stub.project_id = "test-project"
    manager._instance_repository.get.return_value = instance_stub
    return manager


class TestImageSaveTool:
    """WP8 — image_save auto-stamps + persists + respects the cap."""

    def _invoke(self, tool_obj, **kwargs):
        """Invoke a LangChain @tool via its underlying ``.invoke()`` or runnable.

        ``create_image_tools`` returns LangChain tool wrappers; the
        sync invocations are coroutines (they are declared
        ``async def`` in the factory). For unit tests we patch in a
        sync wrapper by directly calling the underlying function —
        the @tool decorator doesn't strip out the underlying object.
        """
        fn = getattr(tool_obj, "func", tool_obj)
        if callable(fn):
            return fn(**kwargs)
        raise AssertionError("tool object not callable")

    def test_image_save_auto_stamps_source_agent(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store, agent_id="designer")
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload = base64.b64encode(b"auto-stamp-payload").decode("ascii")
        result_json = self._invoke(
            save_tool,
            content_b64=payload,
            content_type="image/png",
            feature="checkout",
        )
        result = json.loads(result_json)
        assert result["provenance"]["source_agent"] == "designer"
        assert result["provenance"]["feature"] == "checkout"

    def test_image_save_overrides_source_agent_when_supplied(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store, agent_id="designer")
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload = base64.b64encode(b"x").decode("ascii")
        result_json = self._invoke(
            save_tool,
            content_b64=payload,
            content_type="image/png",
            feature="f1",
            source_agent="tester",
        )
        result = json.loads(result_json)
        assert result["provenance"]["source_agent"] == "tester"

    def test_image_save_protected_retention_round_trips(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store, agent_id="designer")
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload = base64.b64encode(b"baseline").decode("ascii")
        result_json = self._invoke(
            save_tool,
            content_b64=payload,
            content_type="image/png",
            feature="settings",
            retention_class="protected",
        )
        result = json.loads(result_json)
        assert result["retention_class"] == "protected"
        image_id = result["image_id"]
        record = store.open_full(image_id)
        assert record.retention_class == "protected"

    def test_image_save_rejects_bad_base64(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        result = self._invoke(
            save_tool,
            content_b64="@@@not_base64@@@",
            content_type="image/png",
            feature="f1",
        )
        assert isinstance(result, str) and result.startswith("Error:"), result

    def test_image_save_rejects_bad_retention_class(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload = base64.b64encode(b"x").decode("ascii")
        result = self._invoke(
            save_tool,
            content_b64=payload,
            content_type="image/png",
            feature="f1",
            retention_class="bogus",
        )
        assert isinstance(result, str) and result.startswith("Error:"), result

    def test_image_save_empty_content_type_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload = base64.b64encode(b"x").decode("ascii")
        result = self._invoke(
            save_tool,
            content_b64=payload,
            content_type="",
            feature="f1",
        )
        assert isinstance(result, str) and result.startswith("Error:"), result

    def test_image_save_surfaces_store_not_initialized(self, tmp_path):
        manager = MagicMock()
        manager.tmp_image_store = None
        manager._instance_repository.get.return_value = MagicMock(
            agent_id="x", project_id=None
        )
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload = base64.b64encode(b"x").decode("ascii")
        result = self._invoke(
            save_tool,
            content_b64=payload,
            content_type="image/png",
            feature="f1",
        )
        assert isinstance(result, str) and result.startswith(
            "Error: tmp-image store not initialized"
        ), result

    def test_image_save_cap_exhaustion_surfaces_cleanly(self, tmp_path):
        # Construct a tiny-cap store so the second save trips
        # TmpImageStoreFull. The tool must surface this as a clean
        # "Error: tmp-image store is full" — not a stack trace.
        store = TmpImageStore(tmp_path, max_bytes=300)
        store.init()
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        save_tool, _, _ = _find_image_save(tools)
        payload1 = base64.b64encode(b"x" * 50).decode("ascii")
        # First save succeeds.
        r1 = self._invoke(
            save_tool,
            content_b64=payload1,
            content_type="image/png",
            feature="f1",
        )
        assert not (isinstance(r1, str) and r1.startswith("Error:")), r1
        payload2 = base64.b64encode(b"y" * 50).decode("ascii")
        # Second save crosses cap.
        r2 = self._invoke(
            save_tool,
            content_b64=payload2,
            content_type="image/png",
            feature="f1",
        )
        assert isinstance(r2, str) and r2.startswith(
            "Error: tmp-image store is full"
        ), r2


def _find_image_save(tools):
    by_name = {getattr(t, "name", None): t for t in tools}
    # LangChain tools expose `.name`; our @tool wrappers do too.
    return (
        by_name.get("image_save"),
        by_name.get("image_list"),
        by_name.get("image_get"),
    )


class TestImageListAndGetTools:
    """WP8 — image_list exact subsets; image_get uses sidecar MIME."""

    def _invoke(self, tool_obj, **kwargs):
        fn = getattr(tool_obj, "func", tool_obj)
        return fn(**kwargs)

    def test_image_list_returns_exact_subset_for_provenance_filter(
        self, tmp_path
    ):
        store = _make_store(tmp_path)
        manager = _stub_manager(store, agent_id="designer")
        tools = create_image_tools(manager, "inst-1")
        _, list_tool, _ = _find_image_save(tools)
        # Pre-seed with two tagged + one untagged.
        id_a = "a" * 32
        id_b = "b" * 32
        id_c = "c" * 32
        store.save(
            id_a, b"x", "image/png", provenance={"feature": "f1"}
        )
        store.save(
            id_b, b"x", "image/png", provenance={"feature": "f1"}
        )
        store.save(id_c, b"x", "image/png")  # untagged
        result = self._invoke(list_tool, feature="f1")
        recs = json.loads(result)
        ids = sorted(r["image_id"] for r in recs)
        assert ids == [id_a, id_b], f"expected exactly f1-tagged ids, got {ids}"
        # Untagged entry never appears.
        assert id_c not in ids

    def test_image_list_excludes_torn_sidecar_entries(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        id_a = "a" * 32
        id_b = "b" * 32
        store.save(
            id_a, b"x", "image/png", provenance={"feature": "f1"}
        )
        store.save(
            id_b, b"x", "image/png", provenance={"feature": "f1"}
        )
        # Manually break one sidecar.
        _sidecar_path(store, id_a).unlink()
        result = self._invoke(tools[2], feature="f1") if False else \
            self._invoke(
                [t for t in tools if getattr(t, "name", "") == "image_list"][0],
                feature="f1",
            )
        recs = json.loads(result)
        ids = [r["image_id"] for r in recs]
        assert ids == [id_b], (
            f"torn-sidecar entry must be excluded; got {ids}"
        )

    def test_image_get_returns_sidecar_mime_not_extension_guessed(
        self, tmp_path
    ):
        # Blobs are extensionless. The tool must return the
        # sidecar's content_type (NOT guess from the id / filename
        # suffix which doesn't exist).
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        id_a = "a" * 32
        store.save(
            id_a,
            b"some-image-bytes",
            "image/webp",
            provenance={"feature": "f1"},
        )
        get_tool = [
            t for t in tools if getattr(t, "name", "") == "image_get"
        ][0]
        result = self._invoke(get_tool, image_id=id_a)
        payload = json.loads(result)
        assert payload["content_type"] == "image/webp", (
            f"image_get must return the sidecar MIME verbatim; "
            f"got {payload['content_type']!r}"
        )
        # Bytes round-trip unchanged (base64 decode == store bytes).
        assert base64.b64decode(payload["content_b64"]) == b"some-image-bytes"

    def test_image_get_rejects_malformed_id(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        get_tool = [
            t for t in tools if getattr(t, "name", "") == "image_get"
        ][0]
        result = self._invoke(
            get_tool, image_id="not-a-32-hex-id"
        )
        assert isinstance(result, str) and result.startswith("Error:"), result

    def test_image_get_missing_id_returns_clean_error(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        get_tool = [
            t for t in tools if getattr(t, "name", "") == "image_get"
        ][0]
        result = self._invoke(get_tool, image_id="0" * 32)
        assert isinstance(result, str) and result.startswith(
            "Error: tmp-image not found"
        ), result

    def test_image_get_torn_entry_returns_clean_error(self, tmp_path):
        store = _make_store(tmp_path)
        manager = _stub_manager(store)
        tools = create_image_tools(manager, "inst-1")
        id_a = "a" * 32
        store.save(
            id_a, b"x", "image/png", provenance={"feature": "f1"}
        )
        _sidecar_path(store, id_a).unlink()  # torn
        get_tool = [
            t for t in tools if getattr(t, "name", "") == "image_get"
        ][0]
        result = self._invoke(get_tool, image_id=id_a)
        assert isinstance(result, str) and result.startswith(
            "Error: tmp-image not found"
        ), result
