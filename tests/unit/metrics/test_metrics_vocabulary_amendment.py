"""The vocabulary-amendment pack (registered pack ``metrics_vocabulary_amendment``).

AD5: ``VOCABULARY_VERSION`` rides 1 → 2 with this lane (the F7 op-enum
narrowing).  The freeze marker is a RENDERED series, not a doc field:
the bump is observable in the exposition body.

Mutation table: ``P2-M8`` (``VOCABULARY_VERSION`` left at 1).
"""

from __future__ import annotations

from daemon import checkpoint_metrics as cm


class TestVocabularyVersion:
    def test_the_version_is_five(self) -> None:
        """P2-M8 — the bump rode this lane.

        The marker rode 1 → 2 with the central-metrics-v1 lane (the F7
        op-enum narrowing), 2 → 3 with the metrics-agent-tools lane
        (the TOOL-CATEGORY vocabulary gained the ``metrics-read``
        privileged category, so the frozen label set changed and the
        bump contract requires the marker to move), 3 → 4 with the
        nest-anomaly-report lane (the SAME axis again — the
        ``nest-report`` WRITE category joined the tool-category label
        set) and 4 → 5 with the SAME lane's Phase 3 (the SAME axis a
        second time — the ``nest-report-read`` PRIVILEGED READ category
        joined; two commits, never one 3 → 5 jump).  The P2-M8 mutation
        (the marker left at 1) still REDS this pin.
        """
        assert cm.VOCABULARY_VERSION == 5

    def test_the_version_is_an_integer(self) -> None:
        assert isinstance(cm.VOCABULARY_VERSION, int)

    def test_the_gauge_renders_the_frozen_version(self) -> None:
        rendered = cm.render_metrics()
        assert f"ensemble_metrics_vocabulary_version {cm.VOCABULARY_VERSION}" in rendered

    def test_the_frozen_vocabulary_footer_renders_the_version(self) -> None:
        rendered = cm.render_metrics()
        assert f"# frozen vocabulary version: {cm.VOCABULARY_VERSION}" in rendered

    def test_the_version_label_rides_only_the_current_release_gauge(self) -> None:
        rendered = cm.render_metrics()
        version_series = [
            line
            for line in rendered.splitlines()
            if line.startswith("ensemble_metrics_vocabulary_version")
        ]
        assert version_series == [
            f"ensemble_metrics_vocabulary_version {cm.VOCABULARY_VERSION}"
        ]


class TestFrozenVocabulariesStillDerive:
    """A1 — every closed vocabulary is derived from code, never hand-listed."""

    def test_the_instance_status_vocabulary_still_matches_the_enum(self) -> None:
        from daemon.repositories.instance.models import InstanceStatus

        assert set(cm.INSTANCE_STATUS_VOCABULARY) == {
            member.value for member in InstanceStatus
        }

    def test_the_mcp_roster_vocabulary_still_matches_the_registry(self) -> None:
        from daemon.mcp.builtin_servers import get_registry

        names = {definition.name for definition in get_registry().get_all()}
        assert names <= set(cm.MCP_ROSTER_VOCABULARY)

    def test_the_tool_category_vocabulary_still_carries_the_reserved_bucket(self) -> None:
        assert cm.RESERVED_LABEL_VALUE in cm.tool_category_vocabulary()


class TestQueryVocabularyMirror:
    """The query envelope MIRRORS the registry's vocabulary version."""

    def test_the_query_schema_version_is_one(self) -> None:
        from daemon.services.metrics_query.constants import QUERY_SCHEMA_VERSION

        assert QUERY_SCHEMA_VERSION == 1

    def test_the_envelope_mirrors_the_registry_vocabulary_version(self) -> None:
        from daemon.services.metrics_query.envelope import envelope_header
        from daemon.services.metrics_query.provider import (
            RegistryMetricsQueryProvider,
        )

        header = envelope_header(RegistryMetricsQueryProvider())
        assert header["vocabulary_version"] == cm.VOCABULARY_VERSION
        assert header["schema_version"] == 1
        assert header["activation"]["query_api"] == "auth-gated-always-on"

    def test_the_two_axes_are_independent(self) -> None:
        """AM-7 — the shape axis and the vocabulary axis are separate."""
        from daemon.services.metrics_query.constants import QUERY_SCHEMA_VERSION

        assert QUERY_SCHEMA_VERSION != cm.VOCABULARY_VERSION
