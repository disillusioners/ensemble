"""The SEAM-CONSUMER pack (Phase 1 task 1.6) — the Protocol-completeness
discipline applied to the metrics query substrate.

THE HOUSE LESSON THIS PACK EXISTS FOR
-------------------------------------
*"a Protocol that no consumer references guarantees nothing — extend the
Protocol BEFORE typing the consumer, or the consumer resorts to ``Any`` +
``getattr`` and the Protocol's incompleteness stays invisible"*
(``.agents/tidier/notes.md:762``).

``MetricsQueryProvider`` had NO consumer until the agent-tool door.  This
pack is the freeze-guard that makes the amendment MEANINGFUL:

* **completeness** — the Protocol declares EVERY member the tool layer
  calls (the three views + ``registry_read_ok``), and the declared set is
  asserted EXACTLY: deleting ANY declaration reds the pin, and so does
  re-adding a dropped one;
* **one substrate** — the manager-held mount and ``app.state`` hold the
  SAME provider object (the lifespan assigns the same right-hand side to
  both names), and all THREE handle sites (mount / boot-degradation /
  shutdown) behave symmetrically;
* **the public reader** — the tool layer reads the provider's numeric
  facts through the seam's PUBLIC ``read_numeric_fact`` / the shipped
  ``effective_interval_s``, never through a private symbol or ``getattr``;
* **no second provider** — nothing outside ``api.py``'s lifespan
  constructs a ``RegistryMetricsQueryProvider`` (one provider, one ring,
  one truth);
* **reachability** — the lifespan's manager handle is the SAME manager the
  tool factory resolves at call time.

Mutation table: ``SC-M1`` (delete a Protocol declaration) · ``SC-M2``
(clear only ONE of the two handles at a site) · ``SC-M3`` (the mount
assigns a DIFFERENT object to the two names) · ``SC-M4`` (a second
provider construction) · ``SC-M5`` (the public reader aliased away).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from daemon import api as daemon_api
from daemon.manager import InstanceManager
from daemon.services.metrics_query import protocol as mq_protocol
from daemon.services.metrics_query.envelope import (
    activation_block,
    effective_interval_s,
    read_numeric_fact,
)
from daemon.services.metrics_query.protocol import MetricsQueryProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "daemon" / "services" / "metrics_query"

#: The TRIMMED member set the Protocol declares (AmRULING-3: GAP-1/GAP-2 +
#: GAP-3 only — ``listener_enabled``/``census_enabled`` are NOT members).
EXPECTED_PROTOCOL_MEMBERS: frozenset[str] = frozenset(
    {"families", "snapshot", "history", "registry_read_ok"}
)

#: The members the amendment DROPPED — declaring either of them again
#: would re-open the seam churn the architect ruling closed.
DROPPED_PROTOCOL_MEMBERS: frozenset[str] = frozenset(
    {"listener_enabled", "census_enabled"}
)

#: The activation block's member set (the wire block the door publishes).
EXPECTED_ACTIVATION_MEMBERS: frozenset[str] = frozenset(
    {"listener_enabled", "census_enabled", "query_api", "registry_read_ok"}
)


def _lifespan_source() -> str:
    """The lifespan's source (the house source-level wiring precedent)."""
    return inspect.getsource(inspect.unwrap(daemon_api.lifespan))


def _declared_members(protocol: Any) -> frozenset[str]:
    """The protocol's DECLARED public members (methods + properties)."""
    out: set[str] = set()
    for name, obj in vars(protocol).items():
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) or isinstance(obj, property):
            out.add(name)
    return frozenset(out)


def _lines_around(
    source: str,
    needle: str,
    before: int = 0,
    after: int = 0,
    occurrence: int = 0,
) -> list[str]:
    """The source lines around the ``occurrence``-th hit of ``needle``.

    ``occurrence`` selects WHICH hit (0 = the first) — the boot-degradation
    and the shutdown paths share several needles, so the pin must be able
    to name the site it means.
    """
    lines = source.splitlines()
    seen = 0
    for index, line in enumerate(lines):
        if needle in line:
            if seen == occurrence:
                start = max(0, index - before)
                return lines[start : index + after + 1]
            seen += 1
    return []


class TestProtocolCompleteness:
    """SC-M1 — the Protocol declares EXACTLY the trimmed member set."""

    def test_the_declared_member_set_is_exact(self) -> None:
        declared = _declared_members(MetricsQueryProvider)
        assert declared == EXPECTED_PROTOCOL_MEMBERS, (
            "the Protocol must declare exactly the members the tool layer "
            f"calls; got {sorted(declared)}"
        )

    def test_the_three_views_are_declared(self) -> None:
        for view in ("families", "snapshot", "history"):
            assert view in _declared_members(MetricsQueryProvider)

    def test_the_degraded_read_flag_is_declared(self) -> None:
        assert "registry_read_ok" in _declared_members(MetricsQueryProvider)

    def test_the_dropped_members_are_not_declared(self) -> None:
        """GAP-4/GAP-5 stay OUT (AmRULING-3)."""
        declared = _declared_members(MetricsQueryProvider)
        assert not (declared & DROPPED_PROTOCOL_MEMBERS), (
            "GAP-4/GAP-5 must not be declared — the activation facts come "
            "from the shipped activation_block builder"
        )

    def test_history_accepts_the_two_new_kwargs(self) -> None:
        """GAP-1 (``now``) + GAP-2 (``max_series``) — the work bound and
        the comparable-window anchor."""
        params = inspect.signature(MetricsQueryProvider.history).parameters
        assert "now" in params, "history() must accept now= (GAP-1)"
        assert "max_series" in params, "history() must accept max_series= (GAP-2)"
        for name in ("now", "max_series"):
            assert params[name].default is None

    def test_the_registry_read_flag_is_a_read_only_property(self) -> None:
        attr = vars(MetricsQueryProvider)["registry_read_ok"]
        assert isinstance(attr, property)
        assert attr.fset is None, "registry_read_ok must be read-only"

    def test_the_protocol_imports_no_web_framework(self) -> None:
        source = (PACKAGE_DIR / "protocol.py").read_text(encoding="utf-8")
        for forbidden in (
            "import fastapi",
            "from fastapi",
            "import starlette",
            "from starlette",
        ):
            assert forbidden not in source

    def test_the_seam_amendment_comment_is_present(self) -> None:
        """The amendment is recorded as SEAM-side (the contract governs the
        HTTP/wire semantics)."""
        source = (PACKAGE_DIR / "protocol.py").read_text(encoding="utf-8")
        assert "SEAM-SIDE AMENDMENT" in source
        assert "FIRST consumer" in source or "first consumer" in source


class TestManagerMount:
    """SC-M2/SC-M3 — the mount, the identity and the three handle sites."""

    def test_the_class_level_default_is_none(self) -> None:
        assert InstanceManager.metrics_query_provider is None

    def test_a_bare_manager_reads_none(self) -> None:
        """A test double that never mounted it must not raise."""
        manager = InstanceManager.__new__(InstanceManager)
        assert manager.metrics_query_provider is None
        assert manager.metrics_query is None

    def test_the_property_returns_the_mounted_object(self) -> None:
        manager = InstanceManager.__new__(InstanceManager)
        provider = object()
        manager.metrics_query_provider = provider
        assert manager.metrics_query is provider

    def test_the_mount_assigns_the_same_object_to_both_names(self) -> None:
        """The identity claim, structurally: the SAME right-hand side is
        assigned to ``app.state.metrics_query`` and to
        ``manager.metrics_query_provider``."""
        source = _lifespan_source()
        window = _lines_around(
            source,
            "manager.metrics_query_provider = _metrics_query_provider",
            before=6,
            after=0,
        )
        assert window, "the lifespan must mount the provider on the manager"
        assert any(
            "app.state.metrics_query = _metrics_query_provider" in line
            for line in window
        ), (
            "the manager mount must sit beside the app-state assignment so "
            "both names hold the SAME object"
        )

    def test_the_degradation_path_clears_both_handles(self) -> None:
        source = _lifespan_source()
        window = _lines_around(
            source,
            "except Exception as metrics_query_boot_exc:",
            before=0,
            after=6,
        )
        assert window
        joined = "\n".join(window)
        assert "app.state.metrics_query = None" in joined
        assert "manager.metrics_query_provider = None" in joined

    def test_the_shutdown_path_clears_both_handles(self) -> None:
        source = _lifespan_source()
        window = _lines_around(
            source,
            "app.state.metrics_sampler = None",
            before=0,
            after=6,
            occurrence=1,
        )
        assert window
        joined = "\n".join(window)
        assert "app.state.metrics_query = None" in joined
        assert "manager.metrics_query_provider = None" in joined

    def test_the_three_handle_sites_are_symmetric(self) -> None:
        """Mount / boot-degradation / shutdown — each leaves the two handles
        in agreement."""
        source = _lifespan_source()
        assert source.count("app.state.metrics_query = _metrics_query_provider") == 1
        assert source.count("app.state.metrics_query = None") == 2
        assert source.count("manager.metrics_query_provider = _metrics_query_provider") == 1
        assert source.count("manager.metrics_query_provider = None") == 2


class TestPublicReader:
    """SC-M5 — the numeric facts are read through the seam's PUBLIC names."""

    def test_the_public_alias_exists(self) -> None:
        from daemon.services.metrics_query import envelope

        assert hasattr(envelope, "read_numeric_fact")
        assert envelope.read_numeric_fact is envelope._provider_float

    def test_the_reader_tolerates_property_or_method(self) -> None:
        class _MethodFake:
            def last_sample_age_s(self) -> float:
                return 4.5

        class _PropertyFake:
            last_sample_age_s = 7.5

        assert read_numeric_fact(_MethodFake(), "last_sample_age_s", 0.0) == 4.5
        assert read_numeric_fact(_PropertyFake(), "last_sample_age_s", 0.0) == 7.5

    def test_the_reader_falls_back_to_the_default(self) -> None:
        class _Bare:
            pass

        assert read_numeric_fact(_Bare(), "last_sample_age_s", 1.5) == 1.5

    def test_effective_interval_s_reads_the_observed_spacing(self) -> None:
        class _Fake:
            effective_interval_s = 21.0

        assert effective_interval_s(_Fake()) == 21.0

    def test_the_activation_block_carries_the_four_wire_members(self) -> None:
        block = activation_block(_BlockFake())
        assert set(block) == EXPECTED_ACTIVATION_MEMBERS
        assert block["query_api"] == "auth-gated-always-on"
        assert block["registry_read_ok"] is True

    def test_the_activation_block_is_default_safe(self) -> None:
        """A provider that does not carry the fact reads as healthy."""
        block = activation_block(object())
        assert block["registry_read_ok"] is True
        assert block["listener_enabled"] is False
        assert block["census_enabled"] is False

    def test_the_activation_block_matches_the_concrete_provider(self) -> None:
        """The fake and the concrete provider agree when the fake carries
        the provider's own facts (the block is NEVER re-derived)."""
        from daemon.services.metrics_query.provider import (
            RegistryMetricsQueryProvider,
        )

        provider = RegistryMetricsQueryProvider()
        fake = _BlockFake(
            listener_enabled=provider.listener_enabled,
            census_enabled=provider.census_enabled,
            registry_read_ok=provider.registry_read_ok,
        )
        assert activation_block(fake) == activation_block(provider)


class _BlockFake:
    """A duck-typed provider carrying the activation facts."""

    def __init__(
        self,
        *,
        listener_enabled: bool = True,
        census_enabled: bool = True,
        registry_read_ok: bool = True,
    ) -> None:
        self.listener_enabled = listener_enabled
        self.census_enabled = census_enabled
        self.registry_read_ok = registry_read_ok

    def families(self):
        return []

    def snapshot(self, *, family=None, name_prefix=None):
        return []

    def history(self, **kwargs):
        return []


class TestNoSecondProvider:
    """SC-M4 — one provider, one ring, one truth."""

    def test_only_the_lifespan_constructs_the_provider(self) -> None:
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            if path.name == "provider.py":
                continue  # the class's own module
            text = path.read_text(encoding="utf-8")
            if "RegistryMetricsQueryProvider(" in text and "__init__.py" not in path.name:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == ["daemon/api.py"], (
            "only daemon/api.py's lifespan may construct the provider; "
            f"offenders: {offenders}"
        )

    def test_the_lifespan_constructs_it_exactly_once(self) -> None:
        source = _lifespan_source()
        assert source.count("RegistryMetricsQueryProvider(") == 1


class TestMountHandleReachability:
    """The lifespan's manager handle IS the manager the tool factory
    resolves at call time (not merely a fixture-local manager)."""

    def test_the_manager_builds_the_lifecycle_service_with_itself(self) -> None:
        source = (REPO_ROOT / "daemon" / "manager.py").read_text(encoding="utf-8")
        assert "InstanceLifecycleService(" in source
        window = _lines_around(source, "InstanceLifecycleService(", before=0, after=3)
        assert window
        assert any("manager=self," in line for line in window), (
            "the lifecycle service must receive the manager itself"
        )

    def test_the_lifecycle_service_hands_that_manager_to_the_tool_factory(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "services" / "instance_lifecycle.py"
        ).read_text(encoding="utf-8")
        assert "create_instance_tools(\n            self._manager," in source or (
            "create_instance_tools(\n            self._manager," in source
        ), "the tool factory must receive the lifecycle service's manager"

    def test_the_tool_factory_takes_the_manager_as_its_first_argument(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "def create_instance_tools(\n    manager" in source or (
            "def create_instance_tools(\n    manager," in source
        ), "create_instance_tools' first parameter must be the manager"

    def test_the_lifespan_creates_the_manager_it_mounts_on(self) -> None:
        source = _lifespan_source()
        assert "manager = InstanceManager(" in source
        assert "manager.metrics_query_provider = _metrics_query_provider" in source
