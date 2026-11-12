"""Loopback HTTPS exemption for the live-view PUBLIC lane (2026-11-12).

Commission: ``feature/liveviews-loopback-https-exempt``. Symptom — a
locally-run daemon browsed via ``http://localhost:9797`` served the
"Secure connection required / This link requires HTTPS" page for every
public view URL, because the verify handler's plain-HTTP branch
(:func:`daemon.routers.live_views._is_https_request`) did not know that
browsers treat loopback origins as potentially-trustworthy secure
contexts even over ``http://``.

The fix is a CODE change scoped to an EXACT loopback host set
(:data:`daemon.routers.live_views._LOOPBACK_HTTPS_EXEMPT_HOSTS`) — no
config toggle exists for it, and the only existing switch
(``auth.allow_insecure_cookie``) is auth-lane-only and global. This
pack pins:

* C1 — every loopback Host spelling over plain HTTP is served
  (POST verify → 303 + grant cookie; GET with the grant → content).
* C2 — every NON-loopback Host keeps the HTTPS rejection, with the
  page copy byte-identical to the historical text.
* C3 — the passcode gate is untouched on loopback HTTP (wrong code ⇒
  failure page + strike; no grant).
* C4 — the negative-control surface for the mutation proof: the
  non-loopback cases above are the ones a widened exemption must
  break.
* Unit table — the Host-header → hostname parser (port stripping
  including the IPv6 bracket form, IPv4-mapped IPv6, lookalikes).

Style mirrors ``tests/unit/routers/test_live_views_public_lane.py``:
fresh ``FastAPI`` app, services on ``app.state``, no DB / manager /
lifespan.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import (
    _LOOPBACK_HTTPS_EXEMPT_HOSTS,
    _hostname_from_host_header,
    _is_loopback_https_exempt,
    _secure_context_satisfied,
    build_router,
)
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    GRANT_COOKIE_NAME,
    ViewPasscodeCipher,
    ViewPasscodeService,
    VerifyRateLimiter,
    generate_passcode,
)

_TEST_FERNET_KEY: bytes = (
    b"Z6gPqrtSzfIcd1aVvbiGoeY9N-99lxx7dx1lzh4fDoE="
)

#: The exact public-lane path minted by every test here.
_VIEW_URL = "/views/public/planning/ens/feat/design/mockups/landing.html"
_REL_PATH = "ens/feat/design/mockups/landing.html"

#: The historical rejection copy — asserted BYTE-IDENTICAL for every
#: non-loopback host (the fix must not soften that path). This is the
#: CONTIGUOUS inner block :func:`render_secure_context_required`
#: emits; it is additionally cross-checked against the renderer's own
#: output in ``TestNonLoopbackStillRejected`` so the pin cannot drift
#: from the source copy.
_SECURE_TITLE = "<h1>Secure connection required</h1>"
_SECURE_BODY = (
    "<p>This link requires HTTPS. Please open it via "
    "the canonical secure URL.</p>"
)
_SECURE_BLOCK = f"{_SECURE_TITLE}\n{_SECURE_BODY}\n"


# ──────────────────────────────────────────────────────────────────
# Scaffolding (mirrors test_live_views_public_lane.py, trimmed to the
# POST/GET verify surface this pack exercises)
# ──────────────────────────────────────────────────────────────────


@dataclass
class _PasscodeRow:
    """SQLModel-shape row (attribute access, like the real store)."""

    root_name: str
    rel_path: str
    wrapped_passcode: bytes
    generation: int = 1
    failed_attempts: int = 0
    locked: bool = False


class _Store:
    """Minimal in-memory store port — the methods the router touches."""

    def __init__(self, cipher: ViewPasscodeCipher) -> None:
        self._cipher = cipher
        self.rows: dict[tuple[str, str], _PasscodeRow] = {}
        self.audit_rows: list[tuple[str, int]] = []

    def upsert(
        self, root_name: str, rel_path: str, *, wrapped_passcode: bytes
    ) -> _PasscodeRow:
        row = _PasscodeRow(
            root_name=root_name,
            rel_path=rel_path,
            wrapped_passcode=wrapped_passcode,
        )
        self.rows[(root_name, rel_path)] = row
        return row

    def get_row(self, root_name: str, rel_path: str):
        return self.rows.get((root_name, rel_path))

    def record_failure_with_audit(
        self, root_name: str, rel_path: str, *, actor, ip_hmac
    ) -> tuple[int, bool]:
        row = self.rows[(root_name, rel_path)]
        if row.locked:
            return (row.failed_attempts, True)
        row.failed_attempts += 1
        if row.failed_attempts >= 10:
            row.locked = True
        return (row.failed_attempts, row.locked)

    def append_audit(
        self, *, root_name, rel_path, generation, action, actor, ip_hmac
    ) -> None:
        self.audit_rows.append((action, generation))


@pytest.fixture
def cipher() -> ViewPasscodeCipher:
    return ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)


@pytest.fixture
def store(cipher: ViewPasscodeCipher) -> _Store:
    return _Store(cipher)


@pytest.fixture
def passcode_service(
    cipher: ViewPasscodeCipher, store: _Store
) -> ViewPasscodeService:
    service = ViewPasscodeService.__new__(ViewPasscodeService)
    service._engine = None
    service._cipher = cipher
    service._store = store
    return service


@pytest.fixture
def workdir(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "project"
    mockup = (
        root
        / ".agents"
        / "shared"
        / "planning"
        / "feat"
        / "design"
        / "mockups"
    )
    mockup.mkdir(parents=True)
    (mockup / "landing.html").write_text("<h1>Landing</h1>")
    return root


@pytest.fixture
def client(
    workdir: pathlib.Path,
    passcode_service: ViewPasscodeService,
    cipher: ViewPasscodeCipher,
) -> TestClient:
    cfg = LiveViewsConfig()
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=["html"],
    )
    service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda shortname: (
            str(workdir) if shortname == "ens" else None
        ),
    )
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    app.state.view_passcode_service = passcode_service
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = VerifyRateLimiter(
        capacity=50, refill_per_minute=50
    )
    return TestClient(app, client=("127.0.0.1", 50000))


def _mint(store: _Store, cipher: ViewPasscodeCipher) -> str:
    """Seed the row and return its plaintext passcode."""
    plaintext = generate_passcode()
    store.upsert(
        "planning", _REL_PATH, wrapped_passcode=cipher.wrap(
            plaintext.encode("ascii")
        )
    )
    return plaintext


# ──────────────────────────────────────────────────────────────────
# Unit table — the Host-header parser + the exempt verdict
# ──────────────────────────────────────────────────────────────────


class TestHostHeaderParsing:
    """The hostname extractor is the load-bearing half of the scoping.

    Port stripping MUST handle the IPv6 bracket form
    (``[::1]:9797`` → ``::1``) while leaving a BARE IPv6 literal
    (``::1`` / ``::ffff:127.0.0.1``) intact — its colons are not a
    port suffix.
    """

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("localhost:9797", "localhost"),
            ("LOCALHOST:9797", "localhost"),
            ("localhost", "localhost"),
            ("127.0.0.1:9797", "127.0.0.1"),
            ("127.0.0.1", "127.0.0.1"),
            ("[::1]:9797", "::1"),
            ("[::1]", "::1"),
            ("::1", "::1"),
            ("[::ffff:127.0.0.1]:9797", "::ffff:127.0.0.1"),
            ("::ffff:127.0.0.1", "::ffff:127.0.0.1"),
            ("192.168.1.10:9797", "192.168.1.10"),
            ("some-public.example.com", "some-public.example.com"),
            ("some-public.example.com:9797", "some-public.example.com"),
            ("localhost.:9797", "localhost."),
            ("", None),
            ("   ", None),
            ("[broken", None),
        ],
    )
    def test_hostname_extraction(self, header: str, expected):
        assert _hostname_from_host_header(header) == expected

    def test_exempt_set_is_exactly_the_four_loopback_identities(self):
        """No subnet wildcard, no config list — the frozen literal set."""
        assert _LOOPBACK_HTTPS_EXEMPT_HOSTS == frozenset(
            {"localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"}
        )


class TestExemptVerdict:
    """``_is_loopback_https_exempt`` reads the request Host header."""

    @staticmethod
    def _req(headers: dict, client=("127.0.0.1", 50000)) -> "object":
        from types import SimpleNamespace

        scope = {"client": client} if client is not None else {}
        return SimpleNamespace(
            headers={k.lower(): v for k, v in headers.items()},
            url=SimpleNamespace(scheme="http"),
            scope=scope,
        )

    @pytest.mark.parametrize(
        "host",
        [
            "localhost:9797",
            "LOCALHOST:9797",
            "127.0.0.1:9797",
            "[::1]:9797",
            "::1",
            "::ffff:127.0.0.1",
            # S2 bracket-tail leniency pins (current parser behavior):
            "[localhost]",
            "[::1]junk",
            "[::1]:",
        ],
    )
    def test_loopback_hosts_are_exempt_over_plain_http(self, host: str):
        request = self._req({"Host": host})
        assert _is_loopback_https_exempt(request) is True
        # No X-Forwarded-Proto ⇒ not https ⇒ exemption is what saves it.
        assert _secure_context_satisfied(request) is True

    @pytest.mark.parametrize(
        "host",
        [
            "192.168.1.10:9797",
            "10.0.0.5:9797",
            "some-public.example.com",
            "some-public.example.com:443",
            "localhost.evil.example:9797",
            "127.0.0.1.evil.example:9797",
            # 127.0.0.0/8 is NOT wildcarded — other 127.x spellings fail.
            "127.1.2.3:9797",
            "127.0.0.2:9797",
            "0.0.0.0:9797",
            "testserver",
            # S3/S4 fail-closed pins:
            "u@localhost",
            "http://localhost:9797",
            "0177.0.0.1",
            "0x7f.0.0.1",
            "2130706433",
            "::ffff:7f00:1",
            "localhost\x00",
            "localhost%00",
            "localhost%2e",
        ],
    )
    def test_non_loopback_hosts_are_not_exempt(self, host: str):
        request = self._req({"Host": host})
        assert _is_loopback_https_exempt(request) is False
        assert _secure_context_satisfied(request) is False

    def test_https_still_satisfied_for_non_loopback(self):
        """A genuinely HTTPS non-loopback request is unaffected."""
        request = self._req(
            {"Host": "some-public.example.com",
             "X-Forwarded-Proto": "https"}
        )
        assert _secure_context_satisfied(request) is True


# ──────────────────────────────────────────────────────────────────
# Peer-gate (S1): exemption requires a loopback TCP peer too
# ──────────────────────────────────────────────────────────────────


class TestPeerGateConjunction:
    """S1 — Host match alone no longer earns the exemption."""

    def test_loopback_host_loopback_peer_exempt(self):
        request = TestExemptVerdict._req(
            {"Host": "localhost:9797"}, client=("127.0.0.1", 50000)
        )
        assert _is_loopback_https_exempt(request) is True
        assert _secure_context_satisfied(request) is True

    def test_loopback_host_non_loopback_peer_rejected(self):
        request = TestExemptVerdict._req(
            {"Host": "localhost:9797"}, client=("192.168.1.10", 50000)
        )
        assert _is_loopback_https_exempt(request) is False
        assert _secure_context_satisfied(request) is False

    def test_loopback_host_missing_client_scope_fail_closed(self):
        request = TestExemptVerdict._req(
            {"Host": "localhost:9797"}, client=None
        )
        assert _is_loopback_https_exempt(request) is False
        assert _secure_context_satisfied(request) is False

    def test_ipv6_mapped_peer_is_loopback(self):
        request = TestExemptVerdict._req(
            {"Host": "127.0.0.1:9797"}, client=("::ffff:127.0.0.1", 50000)
        )
        assert _is_loopback_https_exempt(request) is True

    def test_xfp_http_loopback_peer_exempt(self):
        """XFP: http + loopback Host + loopback peer → exempt."""
        request = TestExemptVerdict._req(
            {"Host": "localhost:9797", "X-Forwarded-Proto": "http"},
            client=("127.0.0.1", 50000),
        )
        assert _is_loopback_https_exempt(request) is True
        assert _secure_context_satisfied(request) is True

    def test_xfp_http_non_loopback_rejected(self):
        """XFP: http + non-loopback Host → rejected."""
        request = TestExemptVerdict._req(
            {"Host": "192.168.1.10:9797", "X-Forwarded-Proto": "http"},
            client=("192.168.1.10", 50000),
        )
        assert _is_loopback_https_exempt(request) is False
        assert _secure_context_satisfied(request) is False


# ──────────────────────────────────────────────────────────────────
# C1 — loopback Host headers over plain HTTP are SERVED
# ──────────────────────────────────────────────────────────────────


class TestLoopbackServedOverHttp:
    """C1: no ``X-Forwarded-Proto`` header — plain HTTP, loopback Host."""

    @pytest.mark.parametrize(
        "host",
        [
            "localhost:9797",
            "LOCALHOST:9797",
            "127.0.0.1:9797",
            "[::1]:9797",
        ],
    )
    def test_loopback_host_gets_grant_and_content(
        self, client: TestClient, store: _Store, cipher: ViewPasscodeCipher,
        host: str,
    ):
        plaintext = _mint(store, cipher)
        resp = client.post(
            _VIEW_URL,
            data={"code": plaintext},
            headers={"Host": host},
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"loopback host {host!r} over plain HTTP must mint the "
            f"grant; got {resp.status_code} {resp.text[:200]}"
        )
        assert "Secure connection required" not in resp.text
        cookie_header = resp.headers.get("set-cookie", "")
        assert f"{GRANT_COOKIE_NAME}=" in cookie_header
        assert "Secure" in cookie_header
        assert f"Path={_VIEW_URL}" in cookie_header

        # The grant then serves the view on the same loopback origin.
        token = cookie_header.split(";")[0].split("=", 1)[1]
        client.cookies.set(GRANT_COOKIE_NAME, token)
        served = client.get(_VIEW_URL, headers={"Host": host})
        assert served.status_code == 200
        assert b"<h1>Landing</h1>" in served.content

    @pytest.mark.parametrize("host", ["::1", "::ffff:127.0.0.1"])
    def test_bare_ipv6_loopback_spellings_are_served(
        self, client: TestClient, store: _Store, cipher: ViewPasscodeCipher,
        host: str,
    ):
        """A bare (unbracketed) IPv6 Host literal is exempt too."""
        plaintext = _mint(store, cipher)
        resp = client.post(
            _VIEW_URL,
            data={"code": plaintext},
            headers={"Host": host},
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"bare loopback literal {host!r} must be exempt; got "
            f"{resp.status_code} {resp.text[:200]}"
        )
        assert "Secure connection required" not in resp.text


# ──────────────────────────────────────────────────────────────────
# C2 — non-loopback hosts keep the HTTPS rejection (byte-identical)
# ──────────────────────────────────────────────────────────────────


class TestNonLoopbackStillRejected:
    """C2: the error path text must be EXACTLY unchanged."""

    @pytest.mark.parametrize(
        "host",
        [
            "192.168.1.10:9797",
            "some-public.example.com",
            "localhost.evil.example:9797",
            "127.0.0.1.evil.example:9797",
            "127.1.2.3:9797",
        ],
    )
    def test_non_loopback_plain_http_renders_secure_context_page(
        self, client: TestClient, store: _Store, cipher: ViewPasscodeCipher,
        host: str,
    ):
        plaintext = _mint(store, cipher)
        resp = client.post(
            _VIEW_URL,
            data={"code": plaintext},
            headers={"Host": host},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert _SECURE_TITLE in resp.text
        assert _SECURE_BODY in resp.text
        # No grant is minted on the rejection path.
        assert "set-cookie" not in resp.headers
        # And the legitimate passcode is NOT echoed.
        assert plaintext not in resp.text

    def test_rejection_body_bytes_are_unchanged(self, client, store, cipher):
        """Byte-identity of the rejection page (copy + title).

        The rendered block is compared against the RENDERER's own
        output, so a future copy edit in
        ``daemon/services/view_passcode_pages.py`` is caught here and
        this pin can never silently drift from the source text.
        """
        from daemon.services.view_passcode_pages import (
            render_secure_context_required,
        )

        rendered = render_secure_context_required(csp_nonce="fixed")
        assert _SECURE_BLOCK in rendered, (
            "the pinned rejection copy no longer matches the renderer"
        )
        plaintext = _mint(store, cipher)
        resp = client.post(
            _VIEW_URL,
            data={"code": plaintext},
            headers={"Host": "192.168.1.10:9797"},
            follow_redirects=False,
        )
        body = resp.text
        assert "<title>Secure connection required</title>" in body
        assert _SECURE_BLOCK in body


# ──────────────────────────────────────────────────────────────────
# C3 — the passcode gate is UNTOUCHED on loopback HTTP
# ──────────────────────────────────────────────────────────────────


class TestPasscodeGateUntouchedOnLoopback:
    """Loopback HTTP still requires a valid passcode."""

    def test_loopback_wrong_code_still_fails_and_strikes(
        self, client: TestClient, store: _Store, cipher: ViewPasscodeCipher
    ):
        _mint(store, cipher)
        resp = client.post(
            _VIEW_URL,
            data={"code": "zzzzzz"},
            headers={"Host": "localhost:9797"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "That code was not recognised" in resp.text
        # No grant, no content.
        assert "set-cookie" not in resp.headers
        assert b"<h1>Landing</h1>" not in resp.content
        # The durable strike counter moved.
        row = store.get_row("planning", _REL_PATH)
        assert row is not None and row.failed_attempts == 1

    def test_loopback_unminted_path_still_uniform_404(
        self, client: TestClient
    ):
        """No row ⇒ uniform 404 even on a loopback Host (GET never mints)."""
        resp = client.get(_VIEW_URL, headers={"Host": "localhost:9797"})
        assert resp.status_code == 404

    def test_loopback_interstitial_without_grant(
        self, client: TestClient, store: _Store, cipher: ViewPasscodeCipher
    ):
        """A minted row + no cookie ⇒ the passcode form, not the content."""
        _mint(store, cipher)
        resp = client.get(_VIEW_URL, headers={"Host": "localhost:9797"})
        assert resp.status_code == 200
        assert "<form" in resp.text.lower()
        assert b"<h1>Landing</h1>" not in resp.content
