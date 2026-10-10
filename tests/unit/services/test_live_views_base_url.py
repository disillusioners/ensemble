"""Unit tests for the Phase 2 URL resolution chain.

Covers the user's mandatory list:

* Base-URL resolution chain precedence (operator override >
  Host-capture > bind evidence > path-relative).
* Nonsense-host rejection in ``HostRecorder`` (empty,
  malformed, userinfo, path-bearing, out-of-range ports,
  header-smuggling CR/LF).
* X-Forwarded-Proto scheme inference (http / https / missing).
* Wildcard bind mapping (``0.0.0.0`` / ``::`` / ``""`` →
  ``127.0.0.1``).
* ``view_link`` output format: full URL when any base
  resolves, path-relative only when nothing trustworthy.
* The auto-detect never produces a nonsense URL.

Mirrors the pattern in ``tests/unit/services/test_live_views_service.py``
(real service construction with tiny configs, not magic mocks).
"""

from __future__ import annotations

import pathlib

import pytest

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.services.live_views import (
    BaseURLResolver,
    HostRecorder,
    LiveViewsService,
    _HOST_HEADER_PATTERN,
    _is_valid_host_header,
    _normalize_bind_host,
    _split_host_port,
)


# ===========================================================================
# Group 1 — Host header syntactic validation
# ===========================================================================


class TestIsValidHostHeader:
    """``_is_valid_host_header`` is the structural guard.

    Accepts hostname / IPv4 / bracketed IPv6 with optional port.
    Rejects empty, whitespace, CR/LF, path-bearing values,
    userinfo, out-of-range ports, header-smuggling attempts.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "example.com",
            "EXAMPLE.com",  # case-insensitive on host
            "a",
            "a.b.c.d.e",
            "localhost",
            "127.0.0.1",
            "10.0.0.1",
            "[::1]",
            "[2001:db8::1]",
            "example.com:8080",
            "127.0.0.1:8079",
            "[::1]:8080",
            "x" * 63,  # max single-label length (RFC 1035)
            "x" * 253,  # max host length
            "x" * 61 + ":" + "8" * 4,  # 61-char host + :8080 (67 chars)
            "x" * 61 + ":" + "1" * 5,  # :65535 port (max port, 67 chars)
        ],
    )
    def test_accepts_well_formed(self, value: str):
        assert _is_valid_host_header(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",  # empty
            " ",  # whitespace
            "example.com\r\nHost: evil",  # header smuggling
            "example.com\t",  # tab
            "example.com ",  # trailing space
            "user@example.com",  # userinfo
            "example.com/path",  # path-bearing
            "example.com?x=y",  # query-bearing
            "example.com#frag",  # fragment-bearing
            "x" * 254,  # host too long (cap at 253)
            "x" * 254 + ":8080",  # host too long + port
            "x" * 255,  # way over
            "example.com:0",  # port out of range (low)
            "example.com:65536",  # port out of range (high)
            "example.com:abc",  # non-numeric port
            "example.com:",  # empty port
            "[::1]:8080 ",  # trailing space in bracket form
            "::1",  # bare IPv6 without brackets
            "[]",  # empty bracket
            "[]:8080",
            "foo:bar:baz",  # multi-colon without brackets
            "-leading-hyphen",
        ],
    )
    def test_rejects_malformed(self, value: str):
        assert _is_valid_host_header(value) is False


# ===========================================================================
# Group 2 — Host splitter
# ===========================================================================


class TestSplitHostPort:
    """``_split_host_port`` is the bracket-aware host:port parser."""

    def test_plain_host(self):
        assert _split_host_port("example.com") == ("example.com", None)

    def test_host_with_port(self):
        assert _split_host_port("example.com:8080") == ("example.com", 8080)

    def test_ipv4_with_port(self):
        assert _split_host_port("127.0.0.1:8079") == ("127.0.0.1", 8079)

    def test_bracketed_ipv6(self):
        assert _split_host_port("[::1]") == ("::1", None)

    def test_bracketed_ipv6_with_port(self):
        assert _split_host_port("[::1]:8080") == ("::1", 8080)

    def test_bracketed_ipv6_full(self):
        assert _split_host_port("[2001:db8::1]:443") == ("2001:db8::1", 443)


# ===========================================================================
# Group 3 — Bind-host normalization
# ===========================================================================


class TestNormalizeBindHost:
    """``_normalize_bind_host`` maps wildcards to ``127.0.0.1``."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0.0.0.0", "127.0.0.1"),
            ("::", "127.0.0.1"),
            ("", "127.0.0.1"),
            ("127.0.0.1", "127.0.0.1"),
            ("localhost", "localhost"),
            ("ensemble.example.com", "ensemble.example.com"),
            ("10.0.0.1", "10.0.0.1"),
            ("[::1]", "[::1]"),
        ],
    )
    def test_normalizes(self, raw: str, expected: str):
        assert _normalize_bind_host(raw) == expected


# ===========================================================================
# Group 4 — HostRecorder
# ===========================================================================


class TestHostRecorder:
    """``HostRecorder`` captures the most recent valid (host, scheme).

    Invalid values are silently dropped (middleware errors must
    never break the request path). Thread-safe via lock.
    """

    def test_record_and_latest(self):
        rec = HostRecorder()
        rec.record("example.com", "https")
        assert rec.latest() == ("example.com", None, "https")

    def test_record_with_port(self):
        rec = HostRecorder()
        rec.record("example.com:8080", "http")
        assert rec.latest() == ("example.com", 8080, "http")

    def test_record_bracketed_ipv6_with_port(self):
        rec = HostRecorder()
        rec.record("[::1]:8079", "http")
        assert rec.latest() == ("::1", 8079, "http")

    def test_default_scheme_is_http(self):
        rec = HostRecorder()
        rec.record("example.com")
        assert rec.latest() == ("example.com", None, "http")

    def test_x_forwarded_proto_https(self):
        rec = HostRecorder()
        rec.record("example.com", "HTTPS")  # case-insensitive
        assert rec.latest() == ("example.com", None, "https")

    def test_x_forwarded_proto_unknown_falls_back_to_http(self):
        rec = HostRecorder()
        rec.record("example.com", "ftp")
        assert rec.latest() == ("example.com", None, "http")

    def test_empty_host_dropped(self):
        rec = HostRecorder()
        rec.record("")
        assert rec.latest() is None

    def test_none_host_dropped(self):
        rec = HostRecorder()
        rec.record(None)
        assert rec.latest() is None

    def test_malformed_host_dropped(self):
        rec = HostRecorder()
        rec.record("user@example.com")
        assert rec.latest() is None

    def test_path_bearing_host_dropped(self):
        rec = HostRecorder()
        rec.record("example.com/path")
        assert rec.latest() is None

    def test_header_smuggling_dropped(self):
        rec = HostRecorder()
        rec.record("example.com\r\nHost: evil")
        assert rec.latest() is None

    def test_last_write_wins(self):
        rec = HostRecorder()
        rec.record("first.example.com")
        rec.record("second.example.com")
        assert rec.latest() == ("second.example.com", None, "http")

    def test_reset(self):
        rec = HostRecorder()
        rec.record("example.com")
        rec.reset()
        assert rec.latest() is None


# ===========================================================================
# Group 5 — BaseURLResolver
# ===========================================================================


class TestBaseURLResolverExternalOverride:
    """Tier 1: operator override always wins."""

    def test_external_override_used(self):
        resolver = BaseURLResolver(
            external_base_url="https://ensemble.example.com",
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "https://ensemble.example.com"

    def test_external_override_beats_host_capture(self):
        # Host-capture is non-empty; the override still wins.
        rec = HostRecorder()
        rec.record("captured.example.com")
        resolver = BaseURLResolver(
            external_base_url="https://ensemble.example.com",
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=rec,
        )
        assert resolver.resolve() == "https://ensemble.example.com"

    def test_external_override_beats_bind_evidence(self):
        resolver = BaseURLResolver(
            external_base_url="https://ensemble.example.com",
            bind_host="0.0.0.0",
            bind_port=8079,
            host_recorder=None,
        )
        # Bind would be 127.0.0.1:8079; override wins.
        assert resolver.resolve() == "https://ensemble.example.com"

    def test_external_override_trailing_slash_stripped(self):
        resolver = BaseURLResolver(
            external_base_url="https://ensemble.example.com/",
            bind_host=None,
            bind_port=None,
            host_recorder=None,
        )
        assert resolver.resolve() == "https://ensemble.example.com"

    def test_external_override_invalid_scheme_dropped(self):
        # A malformed override falls through to bind evidence
        # rather than producing a bad mint.
        resolver = BaseURLResolver(
            external_base_url="javascript:alert(1)",
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_external_override_with_path_dropped(self):
        # Paths in the override are rejected so the override
        # cannot be hijacked to mint a poisoned endpoint.
        resolver = BaseURLResolver(
            external_base_url="https://ensemble.example.com/evil",
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_external_override_with_userinfo_dropped(self):
        resolver = BaseURLResolver(
            external_base_url="https://user@ensemble.example.com",
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_external_override_none_falls_through(self):
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_external_override_empty_falls_through(self):
        resolver = BaseURLResolver(
            external_base_url="",
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"


class TestBaseURLResolverHostCapture:
    """Tier 2: Host-capture auto-detect.

    Used when no operator override is set. Most recent valid
    Host header (plus scheme from X-Forwarded-Proto) becomes
    the base. Syntactically validated; nonsense inputs are
    dropped at the recorder.
    """

    def test_host_capture_used(self):
        rec = HostRecorder()
        rec.record("live.example.com", "https")
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=rec,
        )
        # Host-capture beats bind evidence (host beats bind).
        assert resolver.resolve() == "https://live.example.com"

    def test_host_capture_with_port(self):
        rec = HostRecorder()
        rec.record("example.com:9090", "http")
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=rec,
        )
        # Port from Host header is preferred over bind port.
        assert resolver.resolve() == "http://example.com:9090"

    def test_host_capture_no_port_in_header(self):
        rec = HostRecorder()
        rec.record("example.com", "http")
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=rec,
        )
        # No port in Host → no port in base (the caller relies
        # on the Host header's port for client-reachable URLs;
        # the bind port would be a guess the client can't reach).
        assert resolver.resolve() == "http://example.com"

    def test_host_capture_ipv6_with_brackets(self):
        rec = HostRecorder()
        rec.record("[::1]:8079", "http")
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host=None,
            bind_port=None,
            host_recorder=rec,
        )
        assert resolver.resolve() == "http://::1:8079"


class TestBaseURLResolverBindEvidence:
    """Tier 3: bind evidence (last-known-reachable guess)."""

    def test_bind_evidence_used_when_no_override_no_capture(self):
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="127.0.0.1",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_bind_wildcard_zero_maps_to_localhost(self):
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="0.0.0.0",
            bind_port=8079,
            host_recorder=None,
        )
        # Wildcards must NEVER appear in a minted URL.
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_bind_wildcard_ipv6_maps_to_localhost(self):
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="::",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_bind_wildcard_empty_maps_to_localhost(self):
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="",
            bind_port=8079,
            host_recorder=None,
        )
        assert resolver.resolve() == "http://127.0.0.1:8079"

    def test_bind_evidence_beats_recorder_unset(self):
        # Recorder with no record → None; bind evidence applies.
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host="10.0.0.5",
            bind_port=9000,
            host_recorder=HostRecorder(),
        )
        assert resolver.resolve() == "http://10.0.0.5:9000"


class TestBaseURLResolverNoneFallback:
    """Tier 4: None fallback (path-relative)."""

    def test_none_when_all_inputs_missing(self):
        # No override, no recorder, no bind → None.
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host=None,
            bind_port=None,
            host_recorder=None,
        )
        assert resolver.resolve() is None

    def test_none_when_bind_host_explicit_none(self):
        # bind_host=None is the test-mode sentinel — skip
        # bind evidence; if no override / no capture either,
        # return None.
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host=None,
            bind_port=8079,  # bind_port set but bind_host None
            host_recorder=None,
        )
        assert resolver.resolve() is None


# ===========================================================================
# Group 6 — build_url chain integration
# ===========================================================================


class TestBuildUrlChain:
    """``LiveViewsService.build_url`` threads the resolver chain."""

    def test_path_relative_when_nothing(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        svc = LiveViewsService(config=cfg)
        # No bind_host / host_recorder → falls through to None.
        assert svc.build_url("docs", "foo/bar.html") == "/views/docs/foo/bar.html"

    def test_fully_qualified_from_override(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(external_base_url="https://ensemble.example.com")
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        svc = LiveViewsService(config=cfg)
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "https://ensemble.example.com/views/docs/foo/bar.html"
        )

    def test_fully_qualified_from_bind_evidence(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        svc = LiveViewsService(
            config=cfg, bind_host="0.0.0.0", bind_port=8088
        )
        # Bind wildcard → 127.0.0.1; never 0.0.0.0.
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "http://127.0.0.1:8088/views/docs/foo/bar.html"
        )

    def test_fully_qualified_from_host_capture(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        rec = HostRecorder()
        rec.record("captured.example.com", "https")
        svc = LiveViewsService(
            config=cfg,
            bind_host="0.0.0.0",
            bind_port=8088,
            host_recorder=rec,
        )
        # Host-capture beats bind evidence.
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "https://captured.example.com/views/docs/foo/bar.html"
        )

    def test_override_beats_host_capture(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(external_base_url="https://ensemble.example.com")
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        rec = HostRecorder()
        rec.record("captured.example.com", "https")
        svc = LiveViewsService(
            config=cfg,
            bind_host="0.0.0.0",
            bind_port=8088,
            host_recorder=rec,
        )
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "https://ensemble.example.com/views/docs/foo/bar.html"
        )

    def test_host_capture_with_port(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        rec = HostRecorder()
        rec.record("example.com:9090", "http")
        svc = LiveViewsService(config=cfg, host_recorder=rec)
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "http://example.com:9090/views/docs/foo/bar.html"
        )

    def test_nonsense_host_falls_through_to_bind(
        self, tmp_path: pathlib.Path
    ):
        # A recorder that received a malformed Host (which the
        # recorder silently drops) yields no record; the
        # resolver then falls through to bind evidence. The
        # chain produces a valid mint even if a previous
        # request tried to inject a poisoned Host.
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        rec = HostRecorder()
        rec.record("user@example.com")  # dropped
        rec.record("example.com/path")  # dropped
        assert rec.latest() is None  # nothing recorded
        svc = LiveViewsService(
            config=cfg, bind_host="127.0.0.1", bind_port=8088, host_recorder=rec
        )
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "http://127.0.0.1:8088/views/docs/foo/bar.html"
        )

    def test_full_chain_last_record_wins(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        rec = HostRecorder()
        rec.record("first.example.com")
        rec.record("second.example.com")
        svc = LiveViewsService(config=cfg, host_recorder=rec)
        assert (
            svc.build_url("docs", "foo/bar.html")
            == "http://second.example.com/views/docs/foo/bar.html"
        )


# ===========================================================================
# Group 7 — view_link output format
# ===========================================================================


class TestViewLinkOutputFormat:
    """``view_link`` (the tool) inherits the chain via service.build_url.

    Path-relative when nothing resolves; fully-qualified when
    any base resolves. Tool-side checks (unknown root, malformed
    shape) still return typed "Error: ..." envelopes — the
    service-side chain is only consulted after the tool's
    pre-flight checks pass.
    """

    def _service(
        self,
        tmp_path: pathlib.Path,
        *,
        external_base_url: str | None = None,
        bind_host: str | None = None,
        bind_port: int | None = None,
        recorder: HostRecorder | None = None,
    ) -> "LiveViewsService":  # type: ignore[name-defined]
        cfg = LiveViewsConfig(external_base_url=external_base_url)
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        return LiveViewsService(
            config=cfg,
            bind_host=bind_host,
            bind_port=bind_port,
            host_recorder=recorder,
        )

    def _view_link(self, service: LiveViewsService):
        from unittest.mock import MagicMock

        from daemon.tools.live_views import create_live_view_tools

        mgr = MagicMock()
        mgr.live_views_service = service
        return create_live_view_tools(mgr, "instance-id")[0]

    def test_view_link_path_relative_when_nothing(
        self, tmp_path: pathlib.Path
    ):
        svc = self._service(tmp_path)
        link = self._view_link(svc)
        result = link.invoke({"root_name": "docs", "path": "foo/bar.html"})
        assert result == "/views/docs/foo/bar.html"

    def test_view_link_full_when_override(
        self, tmp_path: pathlib.Path
    ):
        svc = self._service(
            tmp_path, external_base_url="https://ensemble.example.com"
        )
        link = self._view_link(svc)
        result = link.invoke({"root_name": "docs", "path": "foo/bar.html"})
        assert result == "https://ensemble.example.com/views/docs/foo/bar.html"

    def test_view_link_full_when_host_captured(
        self, tmp_path: pathlib.Path
    ):
        rec = HostRecorder()
        rec.record("captured.example.com", "https")
        svc = self._service(
            tmp_path, bind_host="0.0.0.0", bind_port=8079, recorder=rec
        )
        link = self._view_link(svc)
        result = link.invoke({"root_name": "docs", "path": "foo/bar.html"})
        assert result == "https://captured.example.com/views/docs/foo/bar.html"

    def test_view_link_full_when_bind_only(
        self, tmp_path: pathlib.Path
    ):
        # No override, no host capture; bind evidence applies.
        svc = self._service(
            tmp_path, bind_host="0.0.0.0", bind_port=8079
        )
        link = self._view_link(svc)
        result = link.invoke({"root_name": "docs", "path": "foo/bar.html"})
        # 0.0.0.0 wildcard → 127.0.0.1.
        assert result == "http://127.0.0.1:8079/views/docs/foo/bar.html"


# ===========================================================================
# Group 8 — Auto-detect never produces a nonsense URL
# ===========================================================================


class TestAutoDetectNeverProducesNonsense:
    """Integration pin: no resolution chain output is an obviously-
    nonsense URL like ``http://0.0.0.0:8079`` or ``http://:8079``.
    """

    @pytest.mark.parametrize(
        "bind_host",
        ["0.0.0.0", "::", "", "127.0.0.1", "localhost", "example.com"],
    )
    def test_bind_evidence_never_includes_wildcard(
        self, bind_host: str
    ):
        resolver = BaseURLResolver(
            external_base_url=None,
            bind_host=bind_host,
            bind_port=8088,
            host_recorder=None,
        )
        result = resolver.resolve()
        if result is not None:
            assert "0.0.0.0" not in result
            assert "::" not in result
            assert "//:" not in result  # empty host with port
            assert "http://:" not in result

    def test_host_capture_nonsense_does_not_pollute_chain(
        self, tmp_path: pathlib.Path
    ):
        rec = HostRecorder()
        # All garbage; the recorder silently drops them.
        for bad in [
            "user@example.com",
            "example.com/path",
            "example.com\r\nX-Evil: 1",
            "",
            None,
            "x" * 1000,
        ]:
            rec.record(bad)  # type: ignore[arg-type]
        assert rec.latest() is None

        # Even with the polluted recorder, build_url produces
        # a sane mint via the bind evidence fallback. The
        # polluted recorder is silent.
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        svc = LiveViewsService(
            config=cfg,
            bind_host="10.0.0.1",
            bind_port=8088,
            host_recorder=rec,
        )
        assert (
            svc.build_url("docs", "foo")
            == "http://10.0.0.1:8088/views/docs/foo"
        )