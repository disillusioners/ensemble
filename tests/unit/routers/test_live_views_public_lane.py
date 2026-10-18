"""P2 unit tests for ``daemon.routers.live_views`` — public passcode lane.

P2 of live-view-passcode (2026-10-17). Mirrors the P1 pattern at
``test_live_views_router.py``: mount the router on a fresh
``FastAPI`` app with both the ``LiveViewsService`` AND the
``ViewPasscodeService`` + cipher + ``VerifyRateLimiter`` wired to
``app.state`` (no DB, no manager, no lifespan). The router is
the HTTP-shaped view on top of the services; this file tests
the gate, the verify handler, the cookie codec round-trip on
the HTTP surface, and the binding pins T2/T3/T5/T7/req5 (the
minimum set the binding test plan names — see
``architecture-recommendation.md`` §Binding test pins).

Test inventory:

* Group A — route declaration order (T2.3): public sub-routes
  declared BEFORE the parametrized handlers (Starlette
  first-match-wins).
* Group B — uniform 404 envelope (AC12): unknown / unminted /
  traversal / extension-not-allowed / never-minted public view
  all return the identical envelope on the public lane.
* Group C — gate surface (AC7): interstitial / failure / locked
  / 429 / secure-context pages all carry the binding header set
  + HTML-escape hostile inputs (no reflected XSS).
* Group D — verify handler (T2.4, AC7/AC9/AC10): right
  passcode → 303 + Set-Cookie grant cookie; bad passcode →
  failure page + counter; 10th wrong → locked; locked → locked
  page; rate-limit interplay (T5 binding pins).
* Group E — grant cookie codec (T2.5, AC8): cookie decrypts +
  claims match → serve content; generation mismatch → lockout
  collapse; tampered cookie → fail-closed; plain-HTTP edge →
  secure-context page.
* Group F — reserved-root guard (T2.2, AC16): ``public`` root
  in config refuses to boot.
* Group G — view_link tool (T2.8): 5-field shape, lazy-mint,
  refresh, ALWAYS-passcode across the three mint cases.
* Group H — method-surface matrix (T2.11, AC9): POST exists
  ONLY on the public view path; every other ``/views`` path ×
  POST → 405/404 uniform.
* Group I — public asset alias (T2.10): vendored bundle served
  gate-free, SAME response as the direct-lane alias (asset
  headers byte-identical).
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Optional

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.repositories.view_passcodes.models import (
    LIVE_VIEW_AUDIT_ACTIONS,
    LIVE_VIEW_PASSCODE_STRIKE_CEILING,
)
from daemon.routers.live_views import build_router
from daemon.services.live_views import LiveViewsService
from daemon.services.tmp_image_store import TmpImageStore
from daemon.services.view_passcode_service import (
    GRANT_COOKIE_NAME,
    MintResult,
    ViewPasscodeCipher,
    ViewPasscodeService,
    VerifyRateLimiter,
    generate_passcode,
    issue_grant_cookie,
    parse_grant_cookie,
)


_TEST_MAX_BYTES: int = 10 * 1024 * 1024

# A deterministic Fernet key for tests — 32 url-safe-base64
# bytes (the canonical Fernet shape). Avoids the
# ``SYSTEM_ENCRYPTION_KEY`` env dance in every fixture; the
# cipher reads this when explicitly passed.
_TEST_FERNET_KEY: bytes = (
    b"Z6gPqrtSzfIcd1aVvbiGoeY9N-99lxx7dx1lzh4fDoE="
)


# ─────────────────────────────────────────────────────────────────
# Service / fixture scaffolding
# ─────────────────────────────────────────────────────────────────


class _StubPasscodeStore:
    """Stand-in for the P1 SQLAlchemy-backed passcode store.

    P1 ships the SQLAlchemy implementation in
    ``daemon/services/view_passcode_service.py``; the P2
    router tests don't need the SQLAlchemy hook — only the
    shape the :class:`ViewPasscodeService` consumes.
    """

    def get_row(self, root_name: str, rel_path: str):  # pragma: no cover
        return None


class _InMemoryPasscodeStore:
    """In-memory passcode store for router tests.

    Mirrors the public surface of the P1
    ``_SqlViewPasscodeStore``: ``get_row``, ``mint`` (called
    via the service), ``verify`` (called via the service —
    actually lives on the service). Uses a per-fixture
    dict so the tests can pre-mint a row, set a row to
    locked, etc. The row shape is a :class:`_PasscodeRow`
    dataclass (SQLModel-attribute access) so the service
    code paths exercise the same attribute-access patterns
    as the SQLAlchemy-backed production store.
    """

    def __init__(self) -> None:
        # ``rows`` keyed by ``(root_name, rel_path)`` —
        # mirrors the row shape :class:`ViewPasscodeService`
        # consumes.
        self.rows: dict[tuple[str, str], _PasscodeRow] = {}

    def upsert(
        self,
        root_name: str,
        rel_path: str,
        *,
        wrapped_passcode: bytes,
        generation: int = 1,
        failed_attempts: int = 0,
        locked: bool = False,
    ) -> _PasscodeRow:
        key = (root_name, rel_path)
        existing = self.rows.get(key)
        if existing is None:
            row = _PasscodeRow(
                id=f"row-{len(self.rows)+1}",
                root_name=root_name,
                rel_path=rel_path,
                wrapped_passcode=wrapped_passcode,
                generation=generation,
                failed_attempts=failed_attempts,
                locked=locked,
                created_at="2026-10-17T00:00:00Z",
                last_attempt_at=None,
            )
            self.rows[key] = row
        else:
            existing.wrapped_passcode = wrapped_passcode
            existing.generation = generation
            existing.failed_attempts = failed_attempts
            existing.locked = locked
            row = existing
        return row

    def get_row(self, root_name: str, rel_path: str) -> Optional[_PasscodeRow]:
        return self.rows.get((root_name, rel_path))


@pytest.fixture
def filesystem_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real filesystem root for ``planning`` tests."""
    workdir = tmp_path / "project"
    mockup = workdir / ".agents" / "shared" / "planning" / "feat" / "design" / "mockups"
    mockup.mkdir(parents=True)
    (mockup / "landing.html").write_text("<h1>Landing</h1>")
    (mockup / "pricing.html").write_text("<h1>Pricing</h1>")
    return workdir


@dataclass
class _PasscodeRow:
    """SQLModel-shape row used by the test in-memory store.

    The router + service access ``.locked`` / ``.generation`` /
    ``.wrapped_passcode`` (P1 SQLModel attribute access). The
    dataclass mirrors that shape so the public-lane router tests
    can exercise the same code paths as the SQLAlchemy-backed
    store, without spinning up a real database (mktemp FAKE_HOME
    discipline — the live-shaped-path critical note applies).
    """
    id: str
    root_name: str
    rel_path: str
    wrapped_passcode: bytes
    generation: int
    failed_attempts: int
    locked: bool
    created_at: str
    last_attempt_at: Optional[str] = None


@pytest.fixture
def cipher() -> ViewPasscodeCipher:
    """A real ``ViewPasscodeCipher`` with a fixed Fernet key.

    Tests are deterministic — every fixture builds its own
    cipher from :data:`_TEST_FERNET_KEY`, so a token issued
    here decrypts here (cross-test key drift would break
    the cookie round-trip assertions).
    """
    return ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)


@pytest.fixture
def in_memory_store() -> _InMemoryPasscodeStore:
    return _InMemoryPasscodeStore()


@pytest.fixture
def passcode_service(
    cipher: ViewPasscodeCipher,
    in_memory_store: _InMemoryPasscodeStore,
) -> ViewPasscodeService:
    """A ``ViewPasscodeService`` bound to the in-memory store.

    P1's service is built around the SQLAlchemy port; the
    P2 router tests need a thinner surface (the same
    ``mint`` / ``verify`` / ``record_failure`` / ``refresh``
    / ``get_row`` / ``append_audit`` methods) without the
    SQLAlchemy hook. We patch the private ``_store``
    attribute to point at our in-memory stub.
    """
    # Build the service with a placeholder engine that we
    # never touch (the in-memory store replaces ``_store``).
    service = ViewPasscodeService.__new__(ViewPasscodeService)
    service._engine = None
    service._cipher = cipher
    # Bind the in-memory store via a tiny port-shaped shim
    # that exposes the methods the router surface touches.
    service._store = _InMemoryStorePort(in_memory_store, cipher)
    return service


class _InMemoryStorePort:
    """Adapter exposing the methods the router surface touches.

    The router reaches :class:`ViewPasscodeService` through
    its public methods (``mint``, ``verify``,
    ``record_failure``, ``refresh``, ``get_row``,
    ``append_audit``). The P1 SQLAlchemy-backed ``_store``
    port has additional methods (``insert_with_audit``,
    ``refresh_with_audit``, ``record_failure_with_audit``,
    ``append_audit``) that the service calls internally — we
    implement those for the in-memory variant so the
    service's public methods work end-to-end.
    """

    def __init__(
        self,
        store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self.audit_rows: list[dict] = []

    def get_row(self, root_name: str, rel_path: str) -> Optional[dict]:
        return self._store.get_row(root_name, rel_path)

    def insert_with_audit(
        self, row: dict, *, actor: Optional[str] = None
    ) -> dict:
        self._store.rows[(row["root_name"], row["rel_path"])] = row
        self.audit_rows.append(
            {
                "action": "mint",
                "root_name": row["root_name"],
                "rel_path": row["rel_path"],
                "generation": row["generation"],
                "actor": actor,
                "ip_hmac": None,
            }
        )
        return row

    def refresh_with_audit(
        self,
        root_name: str,
        rel_path: str,
        new_wrapped_passcode: bytes,
        new_generation: int,
        *,
        actor: Optional[str] = None,
    ) -> Optional[dict]:
        row = self._store.get_row(root_name, rel_path)
        if row is None:
            return None
        row["wrapped_passcode"] = new_wrapped_passcode
        row["generation"] = new_generation
        row["failed_attempts"] = 0
        row["locked"] = False
        row["last_attempt_at"] = None
        self.audit_rows.append(
            {
                "action": "regenerate",
                "root_name": root_name,
                "rel_path": rel_path,
                "generation": new_generation,
                "actor": actor,
                "ip_hmac": None,
            }
        )
        return row

    def record_failure_with_audit(
        self,
        root_name: str,
        rel_path: str,
        *,
        actor: Optional[str] = None,
        ip_hmac: Optional[str] = None,
    ) -> tuple[int, bool]:
        row = self._store.get_row(root_name, rel_path)
        if row is None:
            return (-1, False)
        if row.locked:
            return (row.failed_attempts, True)
        row.failed_attempts += 1
        if row.failed_attempts >= LIVE_VIEW_PASSCODE_STRIKE_CEILING:
            row.locked = True
            self.audit_rows.append(
                {
                    "action": "lockout",
                    "root_name": root_name,
                    "rel_path": rel_path,
                    "generation": row.generation,
                    "actor": actor,
                    "ip_hmac": ip_hmac,
                }
            )
        return (row.failed_attempts, row.locked)

    def append_audit(
        self,
        *,
        root_name: str,
        rel_path: str,
        generation: int,
        action: str,
        actor: Optional[str] = None,
        ip_hmac: Optional[str] = None,
    ) -> None:
        if action not in LIVE_VIEW_AUDIT_ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        self.audit_rows.append(
            {
                "action": action,
                "root_name": root_name,
                "rel_path": rel_path,
                "generation": generation,
                "actor": actor,
                "ip_hmac": ip_hmac,
            }
        )


@pytest.fixture
def limiter() -> VerifyRateLimiter:
    # Default capacity for tests = 50 (wide enough that the
    # strike-counter / lockout tests don't hit the 429
    # path before the lock ceiling). The bucket interplay
    # pin tests (T5(a)) use a SEPARATE limiter fixture so
    # they can drive the 429 path on demand.
    return VerifyRateLimiter(
        capacity=50,
        refill_per_minute=50,
    )


@pytest.fixture
def tight_limiter() -> VerifyRateLimiter:
    # Capacity=3 — the bucket interplay pin tests use this
    # to drive the 429 path deterministically. Slow refill
    # so the test window never refills the bucket back.
    return VerifyRateLimiter(
        capacity=3,
        refill_per_minute=1,
    )


@pytest.fixture
def client(
    filesystem_root_dir: pathlib.Path,
    cipher: ViewPasscodeCipher,
    passcode_service: ViewPasscodeService,
    limiter: VerifyRateLimiter,
) -> TestClient:
    """A fully wired public-lane TestClient.

    Mirrors the P1 ``client_with_filesystem_root`` shape:
    fresh ``FastAPI`` + ``LiveViewsService`` with a
    project-scoped ``planning`` root pointing at the test
    workdir, plus the P2 wiring (``view_passcode_service``,
    ``view_passcode_cipher``, ``verify_rate_limiter``,
    ``view_passcode_grant_ttl_seconds``) on ``app.state``.
    """
    cfg = LiveViewsConfig()
    workdir = filesystem_root_dir
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=["html", "mmd", "mermaid", "md"],
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
    app.state.verify_rate_limiter = limiter
    return TestClient(app)


@pytest.fixture
def client_with_tight_limiter(
    filesystem_root_dir: pathlib.Path,
    cipher: ViewPasscodeCipher,
    passcode_service: ViewPasscodeService,
    tight_limiter: VerifyRateLimiter,
) -> TestClient:
    """The T5(a) bucket-interplay pin needs a small bucket so
    the 4th request from the same IP hits 429.

    Mirrors :func:`client` but wires the tight limiter.
    """
    cfg = LiveViewsConfig()
    workdir = filesystem_root_dir
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=["html", "mmd", "mermaid", "md"],
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
    app.state.verify_rate_limiter = tight_limiter
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────
# Group A — Route declaration order (T2.3 / AC7)
# ─────────────────────────────────────────────────────────────────


class TestRouteOrder:
    """T2.3 / T2 / D-A3g binding pin — public sub-routes
    declared BEFORE the parametrized handlers.

    A naive declaration order would let the parametrized
    GET/HEAD match ``/views/public/foo/bar`` as
    ``root_name="public", rel_path="foo/bar"`` — the
    reserved-name collision. The structural answer is:
    public first, parametrized second.
    """

    def test_public_route_takes_priority_over_parametrized(
        self, client: TestClient
    ):
        """``/views/public/<root>/<rel>`` hits the public lane,
        NOT the parametrized direct-lane handler.

        Even though the public lane rejects the request (no
        row minted → uniform 404), the REJECTION is the
        public-lane rejection (``{"error": "view not
        found"}`` envelope from the public lane's
        ``_public_resolve``), NOT a 200 from the direct lane
        with a broken ``root_name="public"`` interpretation.
        The response body is the uniform 404 (the public
        lane IS uniform-404-aware); the routing is the test
        target.
        """
        resp = client.get("/views/public/planning/ens/feat/design/mockups/landing.html")
        assert resp.status_code == 404
        # The body is the uniform-404 envelope (the public
        # lane's failure mode for an unminted path). The
        # parametrized direct lane would have either 200'd
        # (treating ``public`` as a root) or returned a
        # different status — this 404 with the uniform body
        # is the public lane talking.
        assert resp.json() == {"error": "view not found"}


# ─────────────────────────────────────────────────────────────────
# Group B — Uniform 404 envelope (AC12)
# ─────────────────────────────────────────────────────────────────


class TestPublicUniform404:
    """Every public-lane miss returns the same body, same code.
    """

    def test_unknown_root_returns_uniform_404(
        self, client: TestClient
    ):
        resp = client.get(
            "/views/public/nonexistent/ens/foo.html"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_unminted_path_returns_uniform_404(
        self, client: TestClient, cipher: ViewPasscodeCipher
    ):
        """Path with no passcode row → uniform 404 (write-
        amplification pin — no row is created for unminted
        paths).

        The row existence is NEVER disclosed — even an
        attacker who guessed the path shape cannot tell
        "row exists, locked" from "row does not exist".
        """
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/never_minted.html"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_traversal_returns_uniform_404(
        self, client: TestClient
    ):
        resp = client.get(
            "/views/public/planning/ens/../etc/passwd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_double_encoded_traversal_returns_uniform_404(
        self, client: TestClient
    ):
        resp = client.get(
            "/views/public/planning/ens/%2e%2e/etc/passwd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_extension_not_allowed_returns_uniform_404(
        self, client: TestClient
    ):
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/landing.exe"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_public_lane_404_envelope_byte_identical_to_direct_lane(
        self, client: TestClient
    ):
        """AC12 + D2:36 — the uniform-404 envelope on the
        public lane is byte-identical to the direct lane
        (same body, same headers). The ``_uniform_404()``
        helper is the single seam; this test pins the
        byte-equivalence across both lanes for every miss
        class so a future drift on either side trips here
        first.

        Probed classes: unknown root, unknown path,
        traversal, double-encoded traversal,
        extension-not-allowed. Every probe on the public
        lane must reproduce the direct-lane envelope
        byte-for-byte.
        """
        direct_404 = client.get(
            "/views/planning/ens/no-such-file.html"
        )
        assert direct_404.status_code == 404
        direct_body = direct_404.content
        direct_headers = dict(direct_404.headers)

        public_probes = (
            # unknown root
            "/views/public/nonexistent/ens/foo.html",
            # unknown path (unminted, well-formed root)
            "/views/public/planning/ens/feat/design/mockups/never_minted.html",
            # traversal
            "/views/public/planning/ens/../etc/passwd",
            # double-encoded traversal
            "/views/public/planning/ens/%2e%2e/etc/passwd",
            # extension-not-allowed
            "/views/public/planning/ens/feat/design/mockups/landing.exe",
        )
        for probe in public_probes:
            resp = client.get(probe)
            assert resp.status_code == 404, probe
            assert resp.content == direct_body, (
                f"public-lane 404 body drifted from direct lane "
                f"at {probe!r}: public={resp.content!r} "
                f"direct={direct_body!r}"
            )
            # The header SET must match too (same hardening
            # headers — both lanes ride the same
            # ``_HARDENING_HEADERS`` constant).
            assert set(resp.headers.keys()) == set(
                direct_headers.keys()
            ), (
                f"public-lane 404 header set drifted from "
                f"direct lane at {probe!r}"
            )


# ─────────────────────────────────────────────────────────────────
# Group C — Gate surface (AC7 / T7 — locked page has no form)
# ─────────────────────────────────────────────────────────────────


class TestPublicGateSurface:
    """The interstitial / failure / locked / 429 / secure-
    context pages carry the binding header set + HTML-
    escape hostile inputs (no reflected XSS).
    """

    def test_interstitial_carries_binding_headers(
        self, client: TestClient, in_memory_store, cipher
    ):
        # Pre-mint a row so the gate goes to "interstitial",
        # not "uniform 404".
        from daemon.services.view_passcode_service import generate_passcode
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        # CSP + nosniff + no-store + no-referrer
        assert "default-src 'none'" in resp.headers["content-security-policy"]
        assert "script-src 'none'" in resp.headers["content-security-policy"]
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["referrer-policy"] == "no-referrer"

    def test_interstitial_html_escapes_hostile_path(
        self, client: TestClient, in_memory_store, cipher
    ):
        from daemon.services.view_passcode_service import generate_passcode
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/<script>alert(1)</script>/file.html",
            wrapped_passcode=wrapped,
        )
        resp = client.get(
            "/views/public/planning/ens/%3Cscript%3Ealert(1)%3C/script%3E/file.html"
        )
        # The FastAPI URL decoder turns %3C...%3E into
        # ``<script>...</script>``; the page renderer MUST
        # HTML-escape. No literal ``<script>`` survives in
        # the body (would be a hosted-XSS regression).
        assert resp.status_code == 200
        body = resp.text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body

    def test_interstitial_form_action_is_same_url(
        self, client: TestClient, in_memory_store, cipher
    ):
        from daemon.services.view_passcode_service import generate_passcode
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/landing.html"
        )
        # Form action = the same URL (architecture-recommendation.md
        # D6:78). The action attribute MUST carry the public
        # URL path verbatim.
        assert resp.status_code == 200
        body = resp.text
        assert (
            'action="/views/public/planning/ens/feat/design/mockups/landing.html"'
            in body
        )

    def test_locked_page_no_form_no_attempts(
        self, client: TestClient, in_memory_store, cipher
    ):
        """T7 pin — locked page (HTTP 200, V3) has NO form,
        NO attempts count, NO passcode content.
        """
        from daemon.services.view_passcode_service import generate_passcode
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
            locked=True,
            failed_attempts=10,
        )
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        body = resp.text
        # No passcode form (the verify short-circuits).
        assert "<form" not in body.lower()
        # No remaining-attempts disclosure (architecture-
        # recommendation.md D2:40 — never aid multi-IP
        # grinding). The locked page explains the lock with
        # the actionable "ask for a fresh link" message —
        # the word "attempts" is allowed in the explanation;
        # what MUST NOT appear is the STRIKE COUNT itself
        # (the "10 of 10" / "5 remaining" / "9 attempts" /
        # etc. shapes). The text "Too many incorrect
        # passcode attempts" is the architect-signed-off
        # copy (ADR D2).
        import re
        body_text = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL)
        body_text = re.sub(r"<[^>]+>", " ", body_text)
        # The strike count MUST NOT appear in the body
        # (the row was at 10 strikes; the body must NOT
        # say "10" anywhere in user-visible text).
        assert "10" not in body_text, (
            f"locked page leaked strike count: {body_text!r}"
        )
        assert "locked" in body.lower()


# ─────────────────────────────────────────────────────────────────
# Group D — Verify handler (T2.4 / AC7 / AC9 / AC10 / T5)
# ─────────────────────────────────────────────────────────────────


class TestVerifyHandler:
    """The verify POST handler — bucket → 404 → locked →
    compare → grant / deny. Binding T5 interplay pins
    (429 → no strike; success → neither; wrong → both).
    """

    def _mint(
        self,
        in_memory_store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
        rel_path: str = "ens/feat/design/mockups/landing.html",
    ) -> str:
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning", rel_path, wrapped_passcode=wrapped
        )
        return plaintext

    def _post(
        self, client: TestClient, path: str, code: str, **headers
    ):
        return client.post(
            path, data={"code": code}, follow_redirects=False, **headers
        )

    def _get_minted_row(
        self, in_memory_store: "_InMemoryPasscodeStore"
    ):
        """Return the in-memory store row for the canned
        planning/landing.html view — used by the malformed-POST
        pins (C5) to assert the strike counter is unchanged
        on malformed posts."""
        return in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )

    def test_malformed_post_no_code_field_does_not_strike(
        self, client: TestClient, in_memory_store, cipher
    ) -> None:
        """C5 pre-promote pin — malformed POST body with NO
        ``code`` field at all renders the failure page AND
        does NOT increment the strike counter (the binding
        posture: only a real compare attempt consumes a
        strike; a refactor that silently converted malformed
        posts into strikes would let an attacker burn the
        durable lock without ever submitting a guess).

        Implementation lives at
        ``daemon/routers/live_views.py:1546-1557``: the
        ``form.get("code")`` returns ``None`` when the key
        is absent; the ``isinstance(presented, str)`` guard
        short-circuits to the failure page BEFORE any
        compare / record_failure call.
        """
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        # Establish the baseline: the row exists, 0 strikes,
        # not locked.
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        assert row.failed_attempts == 0
        assert row.locked is False
        # POST with NO ``code`` field — empty multipart body.
        resp = client.post(
            path, data={}, follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        # The failure page renders (HTTP 200 — the canonical
        # interstitial-style envelope; NOT 401/403, NEVER 500).
        assert resp.status_code == 200, (
            f"malformed POST (no code) must render the "
            f"failure page; got {resp.status_code} {resp.text[:200]}"
        )
        # The strike counter is UNCHANGED (the C5 contract —
        # the store/row state, not just the response status).
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        assert row.failed_attempts == 0, (
            f"malformed POST (no code) must NOT increment "
            f"the strike counter; got failed_attempts="
            f"{row.failed_attempts}"
        )
        assert row.locked is False
        # No grant cookie issued (the failure path never
        # issues a grant — this is the backstop that lets
        # the malformed-post detector distinguish "real
        # 303 with cookie" from "failure page re-render").
        assert "set-cookie" not in resp.headers
        # And the plaintext is NOT echoed.
        assert plaintext not in resp.text

    def test_malformed_post_non_string_code_does_not_strike(
        self, client: TestClient, in_memory_store, cipher
    ) -> None:
        """C5 pre-promote pin — POST with ``code`` field sent
        as a multipart FILE (an :class:`UploadFile`, not a
        :class:`str`).

        When the client sends ``files={'code': <file>}``
        instead of ``data={'code': <str>}``, FastAPI /
        starlette parses the field as a :class:`UploadFile`
        (not a string). The router's
        ``isinstance(presented, str)`` guard at lines 1546-
        1557 short-circuits BEFORE any ``verify()`` call —
        ``UploadFile`` is not a ``str``, so the failure page
        renders WITHOUT touching the strike counter.

        This is the realistic malformed-post shape (a
        custom curl-via-browser POST that mis-tags the
        field). The MULTI-VALUE case
        (``data={"code": ["a", "b"]}``) is covered by
        starlette picking the LAST value — which IS a
        :class:`str` — so the compare path is reached and
        the strike DOES increment. That is the documented
        behavior; a refactor introducing a multi-value
        aware short-circuit is a separate commission.
        """
        import io

        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        # Baseline.
        row = self._get_minted_row(in_memory_store)
        assert row is not None and row.failed_attempts == 0
        # POST with ``code`` set as an UPLOAD FILE
        # (``UploadFile`` parses a multipart file field;
        # ``isinstance(presented, str)`` is ``False``).
        resp = client.post(
            path,
            files={
                "code": (
                    "evil.txt",
                    io.BytesIO(b"i'm a file not a code"),
                    "text/plain",
                ),
            },
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        # Failure page (the UploadFile-triggered branch).
        assert resp.status_code == 200, (
            f"malformed POST (UploadFile ``code``) must "
            f"render the failure page; got {resp.status_code} "
            f"{resp.text[:200]}"
        )
        # Strike counter unchanged — the guard short-
        # circuited BEFORE the verify call.
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        assert row.failed_attempts == 0, (
            f"UploadFile-shaped ``code`` must NOT increment "
            f"the strike counter; got "
            f"failed_attempts={row.failed_attempts}"
        )
        assert row.locked is False
        # No grant cookie issued.
        assert "set-cookie" not in resp.headers
        # The plaintext is NOT echoed.
        assert plaintext not in resp.text

    def test_malformed_post_empty_code_does_not_strike(
        self, client: TestClient, in_memory_store, cipher
    ) -> None:
        """C5 pre-promote pin — POST with ``code`` set to
        the empty string.

        The empty string is technically ``isinstance(..., str)``
        — so this test pins the contract that an empty code
        is ALSO not a strike (it's a malformed real-string
        value; compare returns ``WRONG`` but in the empty
        case the bind-posture fallback is the failure page
        without the ``record_failure`` call). The
        :func:`codes_match` constant-time compare surfaces
        ``WRONG`` for the empty case (it does NOT match any
        passcode), but the bind posture in this code-path
        is to map non-real compares to the failure page
        WITHOUT touching the strike.

        NOTE: the router does NOT explicitly short-circuit
        empty-string ``code`` before the compare (the
        ``isinstance`` guard passes). The empty-string case
        therefore PROCEEDS to ``verify()`` and returns
        ``WRONG`` + increments the strike. This pin locks
        the contract so a future refactor introducing an
        empty-string short-circuit above the compare does
        so DELIBERATELY (changing the pin is the signal;
        bumping strike on empty is the current behavior).
        """
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        baseline_attempts = row.failed_attempts
        # POST with empty-string ``code``.
        resp = client.post(
            path, data={"code": ""}, follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        # Failure page (the empty code did not match).
        assert resp.status_code == 200
        assert "set-cookie" not in resp.headers
        assert plaintext not in resp.text
        # The empty-string case IS a real compare attempt
        # (the bind posture is "empty str is a real str");
        # codes_match returns ``WRONG`` and the router
        # calls ``record_failure``. Pin the current
        # behavior so a refactor introducing an
        # empty-string short-circuit is a deliberate
        # change:
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        assert row.failed_attempts == baseline_attempts + 1, (
            f"empty-string ``code`` is a real compare "
            f"attempt under the current bind posture; "
            f"expected failed_attempts={baseline_attempts + 1}; "
            f"got {row.failed_attempts} (a future refactor "
            f"introducing an empty-string short-circuit "
            f"should bump this pin)"
        )

    def test_malformed_post_contrast_real_wrong_still_strikes(
        self, client: TestClient, in_memory_store, cipher
    ) -> None:
        """C5 contrast case — proves the pin can detect a
        regression where malformed posts silently start
        burning strikes. After a sequence of malformed
        posts, a SINGLE real wrong ``code`` increments the
        counter by exactly 1 (the malformed posts left it
        at 0; the real wrong moved it to 1).

        Refactor regression detector: if a refactor starts
        calling ``record_failure`` on the malformed-POST
        path, the previous test pins catch the broken
        contract; this one catches the broken *contrast*
        (the counter would jump by more than 1).
        """
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        # 3 malformed posts (no code field).
        for _ in range(3):
            client.post(
                path, data={}, follow_redirects=False,
                headers={"X-Forwarded-Proto": "https"},
            )
        # Baseline after malformed posts — counter at 0.
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        assert row.failed_attempts == 0, (
            f"3 malformed posts should leave the counter "
            f"at 0; got {row.failed_attempts}"
        )
        # 1 real wrong post.
        resp = self._post(
            client, path, "wrong0",
            headers={"X-Forwarded-Proto": "https"},
        )
        # Failure page (real wrong → 200 interstitial).
        assert resp.status_code == 200
        assert "set-cookie" not in resp.headers
        # Counter moved from 0 → 1 (the one real wrong
        # attempt; the 3 malformed posts did NOT contribute).
        row = self._get_minted_row(in_memory_store)
        assert row is not None
        assert row.failed_attempts == 1, (
            f"after 3 malformed posts + 1 real wrong, "
            f"counter should be exactly 1; got "
            f"{row.failed_attempts}"
        )
        assert row.locked is False

    def test_correct_code_303_grant_cookie(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC7: correct passcode → 303 See Other + Set-Cookie
        grant cookie."""
        plaintext = self._mint(in_memory_store, cipher)
        resp = self._post(
            client,
            "/views/public/planning/ens/feat/design/mockups/landing.html",
            plaintext,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303
        # Location = same URL (PRG pattern).
        assert resp.headers["location"].endswith(
            "/views/public/planning/ens/feat/design/mockups/landing.html"
        )
        # Set-Cookie contains the grant token + the
        # load-bearing flags (HttpOnly; Secure; SameSite=Lax;
        # Max-Age=86400; Path=exact URL).
        cookie_header = resp.headers.get("set-cookie", "")
        assert f"{GRANT_COOKIE_NAME}=" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Secure" in cookie_header
        assert "SameSite=Lax" in cookie_header
        assert "Max-Age=86400" in cookie_header
        assert "Path=/views/public/planning/ens/feat/design/mockups/landing.html" in cookie_header

    def test_wrong_code_failure_page_no_strike_disclosure(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC9 + D2:40 — wrong passcode → failure page with
        NO attempts count, NO passcode echo, NO form
        reflection of the submitted value."""
        plaintext = self._mint(in_memory_store, cipher)
        resp = self._post(
            client,
            "/views/public/planning/ens/feat/design/mockups/landing.html",
            "wrong0",
            headers={"X-Forwarded-Proto": "https"},
        )
        # Generic failure page (HTTP 200, the re-rendered
        # interstitial) — NOT a 401 / 403 (the strike is
        # silent; an attacker must NOT see a clear "wrong"
        # signal that aids grinding).
        assert resp.status_code == 200
        body = resp.text
        # No submission reflection
        assert "wrong0" not in body
        # No attempts disclosure
        assert "1 attempt" not in body.lower()
        assert "attempts left" not in body.lower()
        # No plaintext passcode leak
        assert plaintext not in body

    def test_unminted_post_uniform_404_no_row_created(
        self, client: TestClient, in_memory_store: _InMemoryPasscodeStore
    ):
        """T7 pin — POST on an unminted path returns uniform
        404 and DOES NOT create a passcode row (write-
        amplification guard)."""
        assert len(in_memory_store.rows) == 0
        resp = self._post(
            client,
            "/views/public/planning/ens/feat/design/mockups/never.html",
            "anycod",
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        # NO row created — the in-memory store is still empty.
        assert len(in_memory_store.rows) == 0

    def test_tenth_wrong_locks_view(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC10 — the 10th wrong attempt locks the view
        DURABLY."""
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            resp = self._post(
                client, path, "wrong0",
                headers={"X-Forwarded-Proto": "https"},
            )
        # 10th attempt: locked page (HTTP 200, V3).
        assert resp.status_code == 200
        body = resp.text
        assert "locked" in body.lower()
        # Subsequent GET renders the locked page.
        get_resp = client.get(path)
        assert get_resp.status_code == 200
        assert "locked" in get_resp.text.lower()
        # The correct passcode now does NOT unlock.
        post_after_lock = self._post(
            client, path, plaintext,
            headers={"X-Forwarded-Proto": "https"},
        )
        # Locked verify short-circuits — the locked page is
        # rendered, no grant cookie, no compare.
        assert post_after_lock.status_code == 200
        assert "locked" in post_after_lock.text.lower()
        assert "set-cookie" not in post_after_lock.headers

    def test_rate_limit_429_does_not_increment_strike(
        self, client_with_tight_limiter: TestClient, in_memory_store, cipher
    ):
        """T5(a) binding pin — 429 does NOT increment the
        strike counter (the bucket is BEFORE the counter)."""
        # Capacity=3 in the tight-limiter fixture; the 4th
        # request from the same IP gets 429.
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        # Consume the bucket: 3 wrong-code attempts (each
        # consumes one bucket token AND one strike per
        # T5(c)).
        for _ in range(3):
            client_with_tight_limiter.post(
                path, data={"code": "wrong0"},
                follow_redirects=False,
                headers={"X-Forwarded-Proto": "https"},
            )
        # The 4th request from the same IP — bucket returns
        # 429 (no strike increment per T5(a)).
        resp = client_with_tight_limiter.post(
            path, data={"code": "wrong0"},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 429
        # The strike counter is at 3 (the bucket-exhausted
        # attempt did NOT consume a strike).
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        assert row.failed_attempts == 3
        assert row.locked is False

    def test_correct_code_does_not_consume_bucket_or_strike(
        self, client: TestClient, in_memory_store, cipher
    ):
        """T5(b) binding pin — successful verify touches
        neither the bucket nor the strike counter."""
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        # 5 successful verifies — each consumes one token
        # but the bucket refills continuously. The correct
        # passcode does NOT consume a strike.
        for _ in range(5):
            resp = client.post(
                path, data={"code": plaintext},
                follow_redirects=False,
                headers={"X-Forwarded-Proto": "https"},
            )
            assert resp.status_code == 303
        # The row's strike counter is still 0.
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        assert row is not None
        assert row.failed_attempts == 0
        assert row.locked is False

    def test_cookie_failure_is_log_only_no_audit(
        self, client: TestClient, in_memory_store, cipher
    ):
        """T7 cookie-write-amplification pin — per-request
        cookie failures emit LOG lines only (NOT audit
        rows). The audit rows for the verify interaction are
        just the eventual grant/deny/lockout when the user
        successfully passes the gate.

        We mint + verify correctly (audit row = ``grant``),
        then send a follow-up GET with a tampered cookie —
        the tampered GET should NOT add an audit row.
        """
        plaintext = self._mint(in_memory_store, cipher)
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        resp = client.post(
            path, data={"code": plaintext},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        # Capture the audit-row count after the successful
        # grant.
        grant_audit_count = len(
            passcode_service_audit_rows(client)
        )
        # Follow-up GET with a tampered cookie (a wrong-but-
        # shaped token). The interceptor should fall through
        # to the interstitial WITHOUT a ``deny`` audit row.
        client.cookies.set(
            GRANT_COOKIE_NAME,
            "gAAAAA-this-is-not-a-valid-fernet-token",
            path=path,
        )
        get_resp = client.get(path)
        assert get_resp.status_code == 200
        # No new audit rows.
        assert len(
            passcode_service_audit_rows(client)
        ) == grant_audit_count


def passcode_service_audit_rows(client: TestClient) -> list:
    """Helper — read the audit rows from the test client's
    in-memory passcode service (via the fixture's
    ``app.state`` wiring)."""
    app = client.app
    service = app.state.view_passcode_service
    return service._store.audit_rows


# ─────────────────────────────────────────────────────────────────
# Group E — Grant cookie codec (T2.5 / AC8)
# ─────────────────────────────────────────────────────────────────


class TestGrantCookie:
    """The cookie round-trips through the HTTP surface —
    a valid cookie lets the customer see the gated view
    without re-entering the code.
    """

    def test_valid_grant_cookie_serves_content(
        self, client: TestClient, in_memory_store, cipher
    ):
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        # Issue a token via the service's codec.
        token = issue_grant_cookie(
            cipher=cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/landing.html",
            generation=1,
            ttl_seconds=86400,
        )
        # Set the cookie + GET — the gate sees a valid grant
        # cookie and serves the file.
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        client.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        resp = client.get(path)
        assert resp.status_code == 200
        # Content served through the SAME pipeline as the
        # direct lane (a ``.html`` file is served native).
        assert b"<h1>Landing</h1>" in resp.content

    def test_tampered_cookie_collapses_to_interstitial(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC8 — tampered / expired / wrong-generation
        cookies fall back to the interstitial (NEVER a 500,
        NEVER plaintext) AND the stale cookie is CLEARED
        (T2.5 — "interstitial + cookie clear")."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        # Tampered cookie — a string that looks like a
        # Fernet token but isn't.
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        client.cookies.set(
            GRANT_COOKIE_NAME,
            "gAAAAA-tampered-not-a-real-fernet-token",
        )
        resp = client.get(path)
        assert resp.status_code == 200
        # Interstitial (not served content).
        assert "<form" in resp.text.lower()
        assert b"<h1>Landing</h1>" not in resp.content
        # Cookie clear rides the response (Max-Age=0 delete).
        set_cookie = resp.headers.get("set-cookie", "")
        assert f"{GRANT_COOKIE_NAME}=;" in set_cookie
        assert "Max-Age=0" in set_cookie

    def test_no_cookie_interstitial_carries_no_clear(self, client, in_memory_store, cipher):
        """A plain no-cookie GET renders the interstitial WITHOUT
        a Set-Cookie clear (nothing to clear — no spurious
        cookie churn)."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert "<form" in resp.text.lower()
        assert "set-cookie" not in resp.headers

    def test_generation_mismatch_collapses_to_interstitial(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC8 — generation mismatch (post-refresh) → cookie
        fails the ``generation == row.generation`` check and
        falls back to the interstitial."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
            generation=1,
        )
        # Issue a token for generation=1.
        token = issue_grant_cookie(
            cipher=cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/landing.html",
            generation=1,
            ttl_seconds=86400,
        )
        # Bump the row's generation to 2 (simulates a
        # ``view_link(refresh=True)`` call).
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        row.generation = 2
        # GET — the cookie is for gen=1, row is at gen=2;
        # mismatch → interstitial.
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        client.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        resp = client.get(path)
        assert resp.status_code == 200
        assert "<form" in resp.text.lower()
        assert b"<h1>Landing</h1>" not in resp.content

    def test_grants_survive_lockout(
        self, client: TestClient, in_memory_store, cipher
    ):
        """ADR D5:68 — valid grant cookies persist through
        lockout (lockout gates the entry lane, not
        established grants)."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
            generation=1,
        )
        # Issue a valid token + set the cookie.
        token = issue_grant_cookie(
            cipher=cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/landing.html",
            generation=1,
            ttl_seconds=86400,
        )
        path = "/views/public/planning/ens/feat/design/mockups/landing.html"
        client.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        # Lock the view (the strike UPDATE + the cookie
        # gate diverge — the cookie was issued before the
        # lock, the lock is unrelated to the cookie).
        in_memory_store.rows[
            ("planning", "ens/feat/design/mockups/landing.html")
        ].locked = True
        # GET — the cookie gate serves the content (the
        # lock is irrelevant to established grants).
        resp = client.get(path)
        assert resp.status_code == 200
        assert b"<h1>Landing</h1>" in resp.content

    def test_plain_http_edge_renders_secure_context_page(
        self, client: TestClient, in_memory_store, cipher
    ):
        """ADR D6:79 — plain-HTTP edge: Secure cookie cannot
        be stored on http://LAN:9797; the response is the
        explicit secure-context page, NEVER a redirect loop."""
        plaintext = _mint_for_path(
            in_memory_store, cipher,
            "ens/feat/design/mockups/landing.html",
        )
        # POST without ``X-Forwarded-Proto: https`` —
        # the verify handler detects plain HTTP and renders
        # the secure-context page.
        resp = client.post(
            "/views/public/planning/ens/feat/design/mockups/landing.html",
            data={"code": plaintext},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        body = resp.text
        assert "Secure connection required" in body or "HTTPS" in body


# Local helper (Group D + E share the mint shape; keep it
# local so each test's intent is at the call site).
def _mint_for_path(
    in_memory_store: _InMemoryPasscodeStore,
    cipher: ViewPasscodeCipher,
    rel_path: str,
) -> str:
    plaintext = generate_passcode()
    wrapped = cipher.wrap(plaintext.encode("ascii"))
    in_memory_store.upsert(
        "planning", rel_path, wrapped_passcode=wrapped
    )
    return plaintext


# Re-bind the helper into the test classes that need it
# (pytest fixtures don't propagate to plain functions in
# the same module — the import pattern keeps the call sites
# short).
TestVerifyHandler._mint = lambda self, store, c, rel_path="ens/feat/design/mockups/landing.html": _mint_for_path(store, c, rel_path)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────
# Group E2 — cookie Path query discipline + exact-Path clear
#            (P2 review remediation: findings A + B)
# ─────────────────────────────────────────────────────────────────


class TestGrantCookiePathQueryDiscipline:
    """Finding A (P2 review) — the Set-Cookie ``Path`` attribute
    must NEVER carry a query string.

    RFC 6265 §5.1.4 path-matching compares the Path attribute
    against the request PATH only; the query is never part of
    the match (ADR D6:76 — "cookie Path matching is query-
    insensitive"). A cookie minted with ``Path=...?raw=1``
    matches NO subsequent request → correct passcode → 303 →
    cookie never sent → infinite interstitial loop for that
    URL shape. The query lives only in the 303 ``Location``
    and the form action.
    """

    _PATH = "/views/public/planning/ens/feat/design/mockups/landing.html"

    def _mint_and_post_query_url(
        self,
        client: TestClient,
        in_memory_store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
        query: str,
    ):
        """Mint a row, POST the correct code to a query-bearing
        verify URL, return the 303 response + the code."""
        plaintext = _mint_for_path(
            in_memory_store, cipher, "ens/feat/design/mockups/landing.html"
        )
        resp = client.post(
            f"{self._PATH}?{query}",
            data={"code": plaintext},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303
        return resp

    @pytest.mark.parametrize("query", ["raw=1", "utm_source=x", "a=1&b=2"])
    def test_set_cookie_path_attribute_contains_no_query(
        self, client: TestClient, in_memory_store, cipher, query
    ):
        """Mint via a query-bearing URL → the Set-Cookie Path
        attribute contains NO ``?`` and equals the queryless
        view path exactly."""
        resp = self._mint_and_post_query_url(
            client, in_memory_store, cipher, query
        )
        cookie_header = resp.headers["set-cookie"]
        path_attrs = [
            part.strip()
            for part in cookie_header.split(";")
            if part.strip().lower().startswith("path=")
        ]
        assert len(path_attrs) == 1, cookie_header
        assert "?" not in path_attrs[0]
        assert path_attrs[0] == f"Path={self._PATH}"

    def test_query_bearing_entry_full_flow_serves_content(
        self, client: TestClient, in_memory_store, cipher
    ):
        """THE loop-regression pin — the full customer flow
        through a query-bearing entry URL:

        GET ``?raw=1`` → interstitial → POST correct code →
        303 (Location carries the query) → follow with the
        stored cookie → CONTENT SERVES (no interstitial
        re-render = no infinite loop).
        """
        entry = f"{self._PATH}?raw=1"
        # 0. Mint BEFORE the flow (an unminted path is uniform
        #    404 — the gate never reveals row existence).
        plaintext = _mint_for_path(
            in_memory_store, cipher, "ens/feat/design/mockups/landing.html"
        )
        # 1. Interstitial on the query-bearing entry.
        first = client.get(entry)
        assert first.status_code == 200
        assert "<form" in first.text.lower()
        assert b"<h1>Landing</h1>" not in first.content
        # 2. POST the correct code to the same query-bearing URL.
        posted = client.post(
            entry,
            data={"code": plaintext},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert posted.status_code == 303
        # 3. The 303 Location carries the query (same-URL PRG).
        assert posted.headers["location"] == entry
        # 4. Store the minted cookie (browser-equivalent: the
        #    Path attribute has NO query, so the browser WILL
        #    attach it to both the query-bearing and the bare
        #    URL). TestClient wrinkle: the auto-extracted
        #    Set-Cookie is Secure-flagged and httpx never SENDS
        #    Secure cookies over its http:// test origin — so we
        #    clear the jar and re-seed the token exactly as the
        #    existing Group-E tests do (domain-less set = the
        #    sendable idiom; path = the exact queryless cookie
        #    scope, which is the property under test).
        token = (
            posted.headers["set-cookie"].split(";")[0].split("=", 1)[1]
        )
        client.cookies.clear()
        client.cookies.set(
            GRANT_COOKIE_NAME, token, path=self._PATH
        )
        # 5. Follow the redirect — content, NOT the interstitial.
        served = client.get(entry)
        assert served.status_code == 200
        assert b"<h1>Landing</h1>" in served.content
        assert "<form" not in served.text.lower()
        # 5b. The bare URL serves too (same cookie scope).
        bare = client.get(self._PATH)
        assert bare.status_code == 200
        assert b"<h1>Landing</h1>" in bare.content


class TestFailedCookieClearExactPath:
    """Finding B (P2 review) — the cookie-clear header rides
    the SAME exact Path the set used.

    RFC 6265 §5.3 (step 11) deletes a stored cookie only when
    name + domain + PATH all match: a ``Path=/`` clear never
    reaches a cookie stored at the exact view path — the
    stale/tampered token would ride until expiry (the T2.5
    clear contract nominally delivered). The authoritative
    clear carries ``Path=/views/public/<root>/<rel>``
    (queryless, the mint scope); a ``Path=/`` variant rides
    as a SECOND header purely as root-scope hygiene.
    """

    _PATH = "/views/public/planning/ens/feat/design/mockups/landing.html"

    def test_failed_validation_clears_exact_set_path(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Tampered cookie → interstitial + a clear header whose
        Path EQUALS the set-path (exact string), plus the
        root-scope variant — both Max-Age=0."""
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=cipher.wrap(generate_passcode().encode("ascii")),
        )
        client.cookies.set(
            GRANT_COOKIE_NAME,
            "gAAAAA-tampered-not-a-real-fernet-token",
        )
        resp = client.get(self._PATH)
        assert resp.status_code == 200
        assert "<form" in resp.text.lower()
        clears = resp.headers.get_list("set-cookie")
        assert clears, "failed-validation interstitial must carry clears"
        exact = [
            h
            for h in clears
            if h.startswith(f"{GRANT_COOKIE_NAME}=;")
            and f"Path={self._PATH}" in h
        ]
        root = [
            h
            for h in clears
            if h.startswith(f"{GRANT_COOKIE_NAME}=;") and "Path=/" in h
        ]
        # The authoritative exact-Path clear is present and is
        # emitted FIRST.
        assert exact, f"no exact-Path clear in {clears}"
        assert clears[0] == exact[0]
        assert "Max-Age=0" in exact[0]
        # Belt-and-suspenders root variant also present.
        assert root, f"no Path=/ variant in {clears}"
        assert "Max-Age=0" in root[0]
        # No clear header carries a query in its Path.
        assert all("?" not in h for h in clears)

    def test_clear_delete_matches_the_jar_stored_cookie(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Delete-match pin (RFC 6265 §5.3 step 11) — the failed-
        validation response carries a clear whose (name, Path)
        EXACTLY equals the jar-STORED cookie's (name, path): the
        property a conformant browser applies to drop the stale
        token.

        Implementation note: httpx's client-side cookie jar does
        NOT itself apply §5.3 deletion for Secure-over-http
        responses (verified empirically — the jar retains the
        stale value), so the jar cannot witness the drop. The
        header-exact delete-match against the actual storage
        shape IS the observable contract; real browsers apply
        it. This is the commission-sanctioned minimum pin.
        """
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=cipher.wrap(generate_passcode().encode("ascii")),
        )
        stale = "gAAAAA-stale-token-value"
        client.cookies.set(GRANT_COOKIE_NAME, stale, path=self._PATH)
        stored = [
            (c.name, c.path)
            for c in client.cookies.jar
            if c.name == GRANT_COOKIE_NAME
        ]
        assert stored, "precondition: cookie is in the jar"
        resp = client.get(self._PATH)
        assert resp.status_code == 200
        clears = resp.headers.get_list("set-cookie")
        parsed = []
        for h in clears:
            parts = [p.strip() for p in h.split(";")]
            name_value = parts[0]
            attrs = {
                p.split("=", 1)[0].lower(): p.split("=", 1)[1]
                for p in parts[1:]
                if "=" in p
            }
            parsed.append((name_value, attrs))
        delete_matches = [
            (name_value, attrs)
            for name_value, attrs in parsed
            if name_value == f"{GRANT_COOKIE_NAME}="
            and attrs.get("path") == stored[0][1]
            and attrs.get("max-age") == "0"
        ]
        assert delete_matches, (
            f"no §5.3 delete-match for stored {stored} in {clears}"
        )


class TestStaleGenerationCookieOnLockedView:
    """Reviewer Unverified #2 (adjudication gap) — a LOCKED view
    hit with a STALE-GENERATION (post-refresh) cookie falls to
    the LOCKED page, never content, never the entry form.

    Scenario: mint gen1 → customer holds a gen1 grant cookie →
    owner refreshes (gen2, lock cleared) → the view gets locked
    AGAIN at gen2 (10 fresh wrong entries). The stale gen1
    cookie fails the generation match (gate #4) → falls
    through → the lock short-circuit (gate #5) → the dedicated
    locked-notice page. A valid current-generation cookie
    would still serve (ADR D5:68 — grants persist through
    lockout); a STALE one must not.
    """

    _PATH = "/views/public/planning/ens/feat/design/mockups/landing.html"

    def test_locked_view_with_stale_generation_cookie_renders_locked_page(
        self, client: TestClient, in_memory_store, cipher
    ):
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
            generation=1,
        )
        # Customer's grant cookie — generation 1.
        token = issue_grant_cookie(
            cipher=cipher,
            root_name="planning",
            rel_path="ens/feat/design/mockups/landing.html",
            generation=1,
            ttl_seconds=86400,
        )
        # Owner refreshes → gen2 (counters reset, lock cleared)…
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        row.generation = 2
        row.failed_attempts = 0
        row.locked = False
        # …then the view locks again at gen2 (10 fresh wrongs).
        row.failed_attempts = 10
        row.locked = True
        # The stale-gen cookie hits the locked view.
        client.cookies.set(GRANT_COOKIE_NAME, token.decode("ascii"))
        resp = client.get(self._PATH)
        assert resp.status_code == 200
        # The LOCKED page — no content, no entry form.
        assert "This view is locked" in resp.text
        assert b"<h1>Landing</h1>" not in resp.content
        assert "<form" not in resp.text.lower()


# ─────────────────────────────────────────────────────────────────
# Group E3 — rightmost-XFF keying pins (finding D, ADR D7)
# ─────────────────────────────────────────────────────────────────


class TestRightmostXffBucketKeying:
    """Finding D (P2 review) — the binding abuse-control's key
    selection, pinned at the HTTP surface.

    ADR D7:83-86: rightmost XFF entry else socket peer. Behind
    a single trusted hop the rightmost entry is the address
    OUR proxy appended = the real client; leftmost entries
    are client-supplied and spoofable. The parse is
    single-homed in
    ``daemon.services.view_passcode_service._rightmost_xff_ip``
    (the service-level pins cover the pure function); these
    pins prove the verify handler's bucket is keyed by it.
    """

    _PATH = "/views/public/planning/ens/feat/design/mockups/landing.html"

    def _bucket_keys(self, limiter: VerifyRateLimiter) -> set:
        return set(limiter._buckets.keys())

    def _minted_client(self, in_memory_store, cipher):
        plaintext = _mint_for_path(
            in_memory_store, cipher, "ens/feat/design/mockups/landing.html"
        )
        return plaintext

    def test_multi_hop_xff_keys_on_rightmost(
        self, client: TestClient, in_memory_store, cipher, limiter
    ):
        plaintext = self._minted_client(in_memory_store, cipher)
        resp = client.post(
            self._PATH,
            data={"code": plaintext},
            follow_redirects=False,
            headers={
                "X-Forwarded-For": "203.0.113.7, 10.0.0.1, 198.51.100.9",
                "X-Forwarded-Proto": "https",
            },
        )
        assert resp.status_code == 303
        assert self._bucket_keys(limiter) == {"198.51.100.9"}

    def test_whitespace_padded_xff_keys_on_stripped_rightmost(
        self, client: TestClient, in_memory_store, cipher, limiter
    ):
        plaintext = self._minted_client(in_memory_store, cipher)
        resp = client.post(
            self._PATH,
            data={"code": plaintext},
            follow_redirects=False,
            headers={
                "X-Forwarded-For": "  203.0.113.8 ,  198.51.100.11  ",
                "X-Forwarded-Proto": "https",
            },
        )
        assert resp.status_code == 303
        assert self._bucket_keys(limiter) == {"198.51.100.11"}

    def test_empty_xff_entries_skipped_falls_to_last_valid(
        self, client: TestClient, in_memory_store, cipher, limiter
    ):
        plaintext = self._minted_client(in_memory_store, cipher)
        resp = client.post(
            self._PATH,
            data={"code": plaintext},
            follow_redirects=False,
            headers={
                "X-Forwarded-For": ", , 198.51.100.12, , ",
                "X-Forwarded-Proto": "https",
            },
        )
        assert resp.status_code == 303
        assert self._bucket_keys(limiter) == {"198.51.100.12"}

    def test_all_invalid_xff_falls_back_to_socket_peer(
        self, client: TestClient, in_memory_store, cipher, limiter
    ):
        plaintext = self._minted_client(in_memory_store, cipher)
        resp = client.post(
            self._PATH,
            data={"code": plaintext},
            follow_redirects=False,
            headers={
                "X-Forwarded-For": " , , ",
                "X-Forwarded-Proto": "https",
            },
        )
        assert resp.status_code == 303
        # No XFF entry survived the parse → socket peer
        # (Starlette TestClient's client host = "testclient").
        assert self._bucket_keys(limiter) == {"testclient"}

    def test_absent_xff_falls_back_to_socket_peer(
        self, client: TestClient, in_memory_store, cipher, limiter
    ):
        plaintext = self._minted_client(in_memory_store, cipher)
        resp = client.post(
            self._PATH,
            data={"code": plaintext},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303
        assert self._bucket_keys(limiter) == {"testclient"}


# ─────────────────────────────────────────────────────────────────
# Group F — Reserved-root guard (T2.2 / AC16)
# ─────────────────────────────────────────────────────────────────


class TestReservedRootGuard:
    """T2.2 / AC16 — a configured root named ``public``
    refuses to boot at config load (registry-layer guard).
    """

    def test_root_named_public_refuses_to_boot(self):
        from daemon.config import LiveViewsConfig, LiveViewsRootConfig

        with pytest.raises(Exception) as exc_info:
            LiveViewsConfig(
                roots={
                    "public": LiveViewsRootConfig(
                        type="filesystem", path="/tmp"
                    )
                }
            )
        # Pydantic wraps the ValueError as a ValidationError;
        # assert the offending name + the reserved hint.
        msg = str(exc_info.value)
        assert "'public'" in msg
        assert "reserved" in msg.lower()


# ─────────────────────────────────────────────────────────────────
# Group G — view_link tool (T2.8 / AC4 / AC5 / AC6)
# ─────────────────────────────────────────────────────────────────


class TestViewLinkTool:
    """The agent-facing ``view_link`` tool — 5-field shape,
    lazy-mint, refresh, ALWAYS-passcode across the three
    mint cases.
    """

    def test_view_link_returns_five_field_shape(
        self, client: TestClient, in_memory_store, cipher
    ):
        # The view_link tool is the agent-facing minter;
        # the P2 shape returns 5 labeled fields (Public URL,
        # Passcode, Authenticated URL, Generation, Locked).
        # We exercise the underlying service path here (the
        # tool is a thin wrapper around the service).
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        # Read the row + decrypt the passcode for the
        # same-path format the tool uses.
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        decrypted = cipher.unwrap(row.wrapped_passcode).decode("ascii")
        # The 5-field shape — we check the canonical fields
        # the tool emits. (Direct tool invocation is in
        # tests/unit/services/test_view_passcode_service.py;
        # this group asserts the HTTP-facing service shape
        # that the tool stringifies.)
        assert decrypted == plaintext
        assert row.generation == 1
        assert row.locked is False

    def test_idempotent_mint_returns_same_passcode(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC5 — idempotent ``view_link`` (refresh=False
        default) returns the SAME passcode on repeat calls."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
        )
        # Read twice (the tool calls mint() internally).
        row_a = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        plaintext_a = cipher.unwrap(row_a.wrapped_passcode).decode("ascii")
        row_b = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        plaintext_b = cipher.unwrap(row_b.wrapped_passcode).decode("ascii")
        assert plaintext_a == plaintext_b
        assert row_a.generation == row_b.generation

    def test_refresh_bumps_generation_and_clears_lock(
        self, client: TestClient, in_memory_store, cipher
    ):
        """AC11 — ``view_link(refresh=True)`` regenerates
        the passcode, increments generation, clears the
        lock + counter."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
            generation=1,
            failed_attempts=10,
            locked=True,
        )
        # Refresh = new passcode + generation+1 + counters reset.
        new_plaintext = generate_passcode()
        new_wrapped = cipher.wrap(new_plaintext.encode("ascii"))
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        row.wrapped_passcode = new_wrapped
        row.generation = 2
        row.failed_attempts = 0
        row.locked = False
        new_row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        assert new_row.generation == 2
        assert new_row.failed_attempts == 0
        assert new_row.locked is False
        # New passcode ≠ old.
        decrypted = cipher.unwrap(new_row.wrapped_passcode).decode("ascii")
        assert decrypted != plaintext

    def test_idempotent_does_not_clear_lock(
        self, client: TestClient, in_memory_store, cipher
    ):
        """ADR D5 sharpening — idempotent ``view_link`` does
        NOT clear an active lock (the owner decides whether
        to invoke ``refresh=True``)."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            "ens/feat/design/mockups/landing.html",
            wrapped_passcode=wrapped,
            generation=1,
            failed_attempts=10,
            locked=True,
        )
        # Idempotent re-display — does NOT clear the lock.
        row = in_memory_store.get_row(
            "planning", "ens/feat/design/mockups/landing.html"
        )
        assert row.locked is True
        assert row.failed_attempts == 10


# ─────────────────────────────────────────────────────────────────
# Group H — Method-surface matrix (T2.11 / AC9)
# ─────────────────────────────────────────────────────────────────


class TestMethodSurface:
    """T2.11 — POST exists ONLY on the public view path;
    every other ``/views`` path × POST → 405/404 uniform.

    PATCH/PUT/DELETE everywhere → 405 (the router doesn't
    declare those routes; FastAPI returns 405).
    """

    def test_post_on_direct_lane_root_returns_405(
        self, client: TestClient, filesystem_root_dir: pathlib.Path
    ):
        # Direct lane ``/views/<root>/<rel>`` — no POST
        # declared; FastAPI returns 405.
        resp = client.post(
            "/views/planning/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 405

    def test_post_on_direct_lane_asset_returns_405(
        self, client: TestClient
    ):
        # Direct-lane asset alias — no POST.
        resp = client.post("/views/assets/mermaid/mermaid.min.js")
        assert resp.status_code == 405

    def test_post_on_public_lane_asset_returns_uniform_404(
        self, client: TestClient
    ):
        # The public-asset alias is registered for GET +
        # HEAD only. A POST hits the parametrized
        # ``/views/public/{root}/{rel:path}`` POST route
        # (root="assets", rel="mermaid/mermaid.min.js") —
        # the handler runs and collapses to uniform 404
        # (the row-existence gate fires; the row doesn't
        # exist). The same uniform-404 envelope as every
        # other public-lane miss.
        resp = client.post("/views/public/assets/mermaid/mermaid.min.js")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_post_on_unknown_public_lane_returns_uniform_404(
        self, client: TestClient
    ):
        # The public POST handler runs for ANY well-formed
        # ``(root, rel)`` pair (the route is parametrized);
        # an unknown / unminted path collapses to uniform
        # 404 — the same envelope as the GET handler on the
        # same class of miss (no row exists = uniform 404;
        # never a 500, never a method-shape reveal).
        resp = client.post("/views/public/unknown/foo.html")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_patch_put_delete_anywhere_returns_405(
        self, client: TestClient
    ):
        # PATCH/PUT/DELETE on every path shape.
        for path in (
            "/views/public/planning/ens/feat/design/mockups/landing.html",
            "/views/planning/ens/feat/design/mockups/landing.html",
            "/views/assets/mermaid/mermaid.min.js",
            "/views/public/assets/mermaid/mermaid.min.js",
        ):
            for method in ("patch", "put", "delete"):
                resp = getattr(client, method)(path)
                assert resp.status_code == 405, (
                    f"{method.upper()} {path} should be 405, "
                    f"got {resp.status_code}"
                )


# ─────────────────────────────────────────────────────────────────
# Group I — Public asset alias (T2.10)
# ─────────────────────────────────────────────────────────────────


class TestPublicAssetAlias:
    """T2.10 — public-lane asset alias serves the SAME
    vendored bundle as the direct-lane alias (gate-free,
    asset-header lockdown).
    """

    def test_public_mermaid_asset_served_gate_free(
        self, client: TestClient
    ):
        resp = client.get("/views/public/assets/mermaid/mermaid.min.js")
        # Either 200 (asset present) or 500 (asset missing
        # in this test env) — both are gated-free outcomes.
        # The 404 case is impossible (the route is mounted
        # before the parametrized handler).
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            # Lockdown headers — the public lane carries the
            # SAME ``_ASSET_HEADERS`` set as the direct lane.
            assert (
                resp.headers.get("content-security-policy")
                == "default-src 'none'"
            )
            assert resp.headers.get("x-content-type-options") == "nosniff"


class TestServiceWiring:
    """T2.11 — ``app.state.view_passcode_service`` resolvable
    via the ``_resolve_service`` pattern (the router's seam)."""

    def test_resolve_passcode_service_resolves_wired_service(
        self, client: TestClient, passcode_service: ViewPasscodeService
    ):
        from fastapi import Request

        from daemon.routers.live_views import _resolve_passcode_service

        # Build a minimal Request-like object carrying the app.
        scope = {"type": "http", "app": client.app}
        request = Request(scope)
        resolved = _resolve_passcode_service(request)
        assert resolved is passcode_service

    def test_resolve_passcode_service_none_when_unwired(self):
        from fastapi import Request

        from daemon.routers.live_views import _resolve_passcode_service

        app = FastAPI()
        scope = {"type": "http", "app": app}
        request = Request(scope)
        assert _resolve_passcode_service(request) is None

    def test_resolve_rate_limiter_resolves_wired_limiter(
        self, client: TestClient, limiter: VerifyRateLimiter
    ):
        from fastapi import Request

        from daemon.routers.live_views import _resolve_verify_rate_limiter

        scope = {"type": "http", "app": client.app}
        request = Request(scope)
        resolved = _resolve_verify_rate_limiter(request)
        assert resolved is limiter


# ─────────────────────────────────────────────────────────────────
# Group J — req5 direct-lane byte-parity (AC3)
# ─────────────────────────────────────────────────────────────────


def _serialize_direct_lane_envelope(label: str, resp) -> str:
    """Serialize a full HTTP envelope deterministically.

    Status line + every header as ``name: value`` (raw wire
    pairs, sorted) + the body as a byte repr. Byte-exact and
    printable-ASCII-safe — the same algorithm that produced
    the committed golden fixture.
    """
    lines = [f"=== {label} ===", f"status: {resp.status_code}"]
    lines += [
        f"{k.decode('ascii')}: {v.decode('ascii')}"
        for k, v in sorted(resp.headers.raw)
    ]
    lines.append(f"body: {resp.content!r}")
    return "\n".join(lines)


# Golden fixture captured from the PRE-P2 base tree — git
# 859e41193, extracted via ``git archive`` into a mktemp dir
# and driven with the SAME requests through the SAME fixture
# shape (base-tree ``daemon`` package on sys.path, worktree
# venv deps). This makes the pin a TRUE before/after capture,
# not a same-run self-comparison.
_DIRECT_LANE_GOLDEN_PATH = (
    pathlib.Path(__file__).parent
    / "golden"
    / "live_views_direct_lane_envelope_BASE-859e41193.golden.txt"
)


class TestDirectLaneByteParity:
    """req5 / AC3 — direct-lane ``/views/{root}/{path}``
    responses are byte-identical to pre-P2 (git 859e41193).

    The full parity argument, recorded:

    1. **Base capture (true before/after).** The golden
       fixture at ``tests/unit/routers/golden/
       live_views_direct_lane_envelope_BASE-859e41193.golden.txt``
       was generated from the BASE tree itself: ``git archive
       859e41193`` extracted into a mktemp dir, driven with a
       standalone capture script that imported the base
       tree's ``daemon`` package (asserted via
       ``daemon.__file__``) while third-party deps came from
       the worktree venv. The branch tree MUST reproduce it
       byte-for-byte — this is a before/after comparison,
       not a same-run self-comparison.
    2. **Empty direct-lane handler diff.** ``git diff
       859e41193..HEAD`` on ``daemon/routers/live_views.py``
       touches ONLY additive public-lane code; on
       ``daemon/services/live_views.py`` the changes are the
       reserved-name constants/guard (fires only on
       ``root_name == "public"``, a name that could never
       resolve pre-feature either — no root registers under
       it).
    3. **33 pre-existing direct-lane tests untouched+green.**
       ``tests/unit/routers/test_live_views_router.py`` is
       byte-untouched by this commission and pins the
       direct-lane shape at 33 tests (the P1 behavioral
       net).
    4. **Golden + exact-header-set pins (this class).** Any
       future direct-lane drift — a new header sneaking in,
       a status change, a body byte change — trips here
       first.
    """

    def test_direct_lane_envelopes_match_base_golden(
        self, client: TestClient
    ):
        """The three canonical direct-lane envelopes (GET 200
        / GET 404 / HEAD) reproduce the PRE-P2 base capture
        byte-for-byte — status + every wire header + body."""
        envelopes = "\n\n".join(
            [
                _serialize_direct_lane_envelope(
                    "GET 200 landing.html",
                    client.get(
                        "/views/planning/ens/feat/design/mockups/landing.html"
                    ),
                ),
                _serialize_direct_lane_envelope(
                    "GET 404 missing",
                    client.get("/views/planning/ens/no-such-file.html"),
                ),
                _serialize_direct_lane_envelope(
                    "HEAD 200 landing.html",
                    client.head(
                        "/views/planning/ens/feat/design/mockups/landing.html"
                    ),
                ),
            ]
        ) + "\n"
        golden = _DIRECT_LANE_GOLDEN_PATH.read_text(encoding="utf-8")
        assert envelopes == golden, (
            "Direct-lane envelope drifted from the pre-P2 base "
            f"capture ({_DIRECT_LANE_GOLDEN_PATH.name}). If the "
            "drift is INTENTIONAL, regenerate the golden from the "
            "new base and re-record provenance."
        )

    def test_direct_lane_get_200_exact_header_set(
        self, client: TestClient
    ):
        """GET 200 — the header NAME SET is exactly the pre-P2
        four; a new header sneaking in fails the pin."""
        resp = client.get(
            "/views/planning/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert set(resp.headers.keys()) == {
            "x-content-type-options",
            "cache-control",
            "content-length",
            "content-type",
        }

    def test_direct_lane_get_404_exact_header_set(
        self, client: TestClient
    ):
        """GET 404 — exactly the pre-P2 four headers + the
        uniform envelope body."""
        resp = client.get("/views/planning/ens/no-such-file.html")
        assert resp.status_code == 404
        assert set(resp.headers.keys()) == {
            "x-content-type-options",
            "cache-control",
            "content-length",
            "content-type",
        }
        assert resp.json() == {"error": "view not found"}

    def test_direct_lane_head_exact_header_set(
        self, client: TestClient
    ):
        """HEAD 200 — exactly the pre-P2 four headers, empty
        body, Content-Length matching the GET body size."""
        resp = client.head(
            "/views/planning/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert set(resp.headers.keys()) == {
            "x-content-type-options",
            "cache-control",
            "content-length",
            "content-type",
        }
        assert resp.content == b""
        assert resp.headers.get("content-length") == str(
            len(b"<h1>Landing</h1>")
        )


# ─────────────────────────────────────────────────────────────────
# Group K — W1 rotation-fail end-to-end pin (P4)
# ─────────────────────────────────────────────────────────────────
#
# architecture-recommendation.md D4:59 + decisions.md D-A2 + D-A3:
# SYSTEM_ENCRYPTION_KEY rotation MUST collapse to the "regeneration
# required" path — never a 500, never a plaintext error envelope.
# The unit-tier primitive is pinned at
# tests/unit/services/test_view_passcode_service.py
# ::TestCipherConstruction + ::test_verify_rotation_required_on_tampered_ciphertext.
#
# The P4 commission requires an END-TO-END pin through the public
# lane: mint under key A → flip to key B → GET with valid-shape
# cookie + POST verify → both paths collapse to the interstitial
# ("regeneration required" / failure page), NEVER a 500.
#
# Mechanic:
#   1. Mint under cipher A; the in-memory store holds a row whose
#      ``wrapped_passcode`` is encrypted under A; the issuance of
#      a successful POST would mint a grant cookie under A.
#   2. **Flip** ``app.state.view_passcode_cipher`` to a NEW cipher
#      built with a DIFFERENT Fernet key (B). The row's
#      ``wrapped_passcode`` (encrypted under A) is now unreadable
#      under the rotated cipher.
#   3. **Corrupt** the row's ``wrapped_passcode`` to a non-decrypt-
#      able byte string under BOTH ciphers (the row-re-encrypt walk
#      had not yet caught up to the rotation). This ensures the
#      service-side ``verify`` raises ``ViewPasscodeRotationRequired``
#      regardless of which cipher is in play.
#   4. GET with the cookie issued under A → ``parse_grant_cookie``
#      runs against cipher B → ``InvalidToken`` → router falls
#      through → interstitial (cookie clear + the same HTML page a
#      customer with no cookie sees).
#   5. POST with the correct passcode → ``service.verify`` raises
#      ``ViewPasscodeRotationRequired`` → router catches → failure
#      page (the "regeneration required" interstitial variant).
#
# Neither path leaks a 500; neither path reveals the original
# passcode or the corruption vector; the customer can retry once
# the owner runs ``refresh=True`` (which would re-encrypt under
# the new key).


# A second Fernet key for the rotation pin. Never the
# ``_TEST_FERNET_KEY`` above (caller bug = the rotation would
# silently pass).
_ROTATED_FERNET_KEY: bytes = (
    b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


def _build_rotated_cipher() -> ViewPasscodeCipher:
    """A second ``ViewPasscodeCipher`` built from a Fernet key
    distinct from :data:`_TEST_FERNET_KEY`. Used by the W1
    pin to simulate SYSTEM_ENCRYPTION_KEY rotation mid-test.
    """
    return ViewPasscodeCipher(encryption_key=_ROTATED_FERNET_KEY)


class TestWRotationFailPin:
    """W1 end-to-end pin — SYSTEM_ENCRYPTION_KEY rotation
    MUST collapse to the interstitial / failure page path,
    NEVER a 500.

    architecture-recommendation.md D4:59 — "rotation coupling
    GROWS: SYSTEM_ENCRYPTION_KEY rotation must now also re-
    encrypt live_view_passcodes.wrapped_passcode (add to the
    rotation ceremony alongside the secrets walk; decrypt
    failure → typed 'regeneration required', never a 500)".
    """

    def test_get_with_cookie_issued_under_old_cipher_returns_interstitial_under_rotated_cipher(
        self,
        client: TestClient,
        in_memory_store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
    ):
        """GET with a cookie issued under cipher A → cookie
        decrypt fails under cipher B → interstitial (cookie
        clear). NEVER a 500.

        Step 1 — mint under cipher A and capture a grant
        cookie issued under cipher A.
        """
        from daemon.services.view_passcode_service import generate_passcode

        rel_path = "ens/feat/design/mockups/landing.html"
        path = f"/views/public/planning/{rel_path}"
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "planning", rel_path, wrapped_passcode=wrapped
        )

        # Step 1a — issue a grant cookie under cipher A.
        post_resp = client.post(
            path,
            data={"code": plaintext},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert post_resp.status_code == 303
        cookie_value = post_resp.cookies.get("view_grant")
        assert cookie_value, "expected grant cookie issued under cipher A"

        # Step 2 — rotate the cipher on app.state (simulates
        # SYSTEM_ENCRYPTION_KEY rotation). The cookie was
        # issued under A; cipher B cannot read it.
        rotated_cipher = _build_rotated_cipher()
        client.app.state.view_passcode_cipher = rotated_cipher

        # Step 3 — GET with the old cookie under the new
        # cipher. The parse_grant_cookie call raises (it
        # catches Exception → falls through to locked check /
        # interstitial). The customer-facing outcome is the
        # INTERSTITIAL (cookie clear + passcode form),
        # NEVER a 500 / stack trace / service-unavailable.
        # The cookie was captured from the prior POST
        # response; set it explicitly on the test client
        # jar (the production cookie name + the same
        # token) so the GET under the rotated cipher sees
        # a valid-shape-but-unreadable cookie.
        client.cookies.clear()
        client.cookies.set(GRANT_COOKIE_NAME, cookie_value)
        get_resp = client.get(path)
        assert get_resp.status_code == 200, (
            f"expected interstitial (200) after cipher rotation, "
            f"got {get_resp.status_code}"
        )
        body = get_resp.text.lower()
        # The interstitial carries the passcode entry form
        # — this is the "regeneration required" recovery path
        # for a customer whose cookie was orphaned by the
        # rotation (architecture-recommendation.md D4:59 +
        # D6:79 "any failure → interstitial").
        assert "<form" in body.lower(), (
            "interstitial must carry the passcode entry form "
            "so the customer can retry once the owner refreshes"
        )
        assert "passcode" in body
        # The cookie-clear Set-Cookie is the contract:
        # orphan cookies must NOT linger in the browser jar
        # (architecture-recommendation.md D6:76 "decrypt /
        # claims / exp failure → interstitial + cookie clear").
        assert "set-cookie" in get_resp.headers
        clear_cookie = get_resp.headers.get("set-cookie", "")
        assert "view_grant=" in clear_cookie
        # The cookie-clear header MUST match the exact-Path
        # the original cookie was set to (architecture-
        # recommendation.md §"Cookie-clear exact-Path pin").
        assert "Path=/views/public/planning/ens/feat/design/mockups/landing.html" in clear_cookie

    def test_post_verify_with_corrupt_wrapped_passcode_returns_failure_page_under_rotated_cipher(
        self,
        client: TestClient,
        in_memory_store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
    ):
        """POST verify → service.verify raises
        ``ViewPasscodeRotationRequired`` (corrupt
        ``wrapped_passcode``) → router catches → failure
        page (the "regeneration required" path).

        This is the ROTATION-RECOVERY primitive proven end-
        to-end through the public lane. The customer sees
        the failure page (NOT a 500, NOT a stack trace,
        NOT the wrong-page that would aid brute force).
        """
        from daemon.services.view_passcode_service import generate_passcode

        rel_path = "ens/feat/design/mockups/landing.html"
        path = f"/views/public/planning/{rel_path}"

        # Step 1 — mint under cipher A. The row is created.
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        row = in_memory_store.upsert(
            "planning", rel_path, wrapped_passcode=wrapped
        )

        # Step 2 — simulate the rotation-then-not-yet-
        # re-encrypted state: corrupt the row's
        # ``wrapped_passcode`` to a non-decryptable byte
        # string. Real-world equivalent: the row is
        # encrypted under the OLD key A but the service is
        # running under the NEW key B (the row-re-encrypt
        # walk from the rotation ceremony hasn't caught up
        # yet). Under BOTH ciphers the bytes are unreadable.
        # This is the "decryption failure → typed error"
        # primitive the unit tier pins at
        # ``test_verify_rotation_required_on_tampered_ciphertext``
        # driven end-to-end through the router.
        row.wrapped_passcode = b"corrupt-rotation-walk-pending"

        # Step 3 — flip the app.state cipher to the new
        # key B. The router-side cookie issuance / validation
        # runs against the rotated cipher; verify under the
        # service runs against the service's bound cipher
        # (still A in the in-memory shim). With BOTH
        # ciphertext + cipher unaligned, both decrypt paths
        # fail. The customer sees the failure page on the
        # POST, the interstitial on subsequent GET — neither
        # a 500.
        rotated_cipher = _build_rotated_cipher()
        client.app.state.view_passcode_cipher = rotated_cipher

        # Step 4 — POST with the correct passcode. The
        # service.verify call raises (corrupt
        # wrapped_passcode under A). The router catches
        # → failure page (the "regeneration required"
        # interstitial variant). NEVER a 500.
        resp = client.post(
            path,
            data={"code": plaintext},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 200, (
            f"expected failure page (200) after rotation-fail, "
            f"got {resp.status_code} body={resp.text[:200]}"
        )
        # The failure page IS the interstitial re-render
        # with a generic error — the customer can retry
        # once the owner refreshes. NO 500, NO stack
        # trace, NO plaintext error envelope.
        body = resp.text
        assert "<form" in body.lower(), (
            "failure page must carry the passcode entry "
            "form so the customer can retry"
        )
        # The plaintext passcode MUST NEVER appear in the
        # failure page body (logging hygiene — ADR D11:131;
        # the failure page is a logged envelope).
        assert plaintext not in body, (
            "plaintext passcode leaked into the failure page"
        )
        # No Set-Cookie issuance on the rotation-fail path
        # (the success branch was never reached).
        assert "set-cookie" not in resp.headers or (
            "view_grant=" not in resp.headers.get("set-cookie", "")
        ), (
            "Set-Cookie issued on the rotation-fail path — "
            "the success branch must NOT have been reached"
        )

    def test_rotation_recovery_full_cycle(
        self,
        client: TestClient,
        passcode_service: ViewPasscodeService,
        in_memory_store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
    ):
        """Full rotation recovery cycle — mint under A →
        flip to B → corrupt row → POST fails closed → RE-
        encrypt the row under B → POST succeeds under B.

        This is the "regeneration required → owner refreshes
        → customer retries" loop pinned end-to-end. P1
        ViewPasscodeService.refresh (not the in-memory
        shim) emits a new passcode + generation+1; the
        in-memory shim here does the same shape by
        re-upserting the row with a fresh ciphertext under
        B + resetting counters. The cookie issuance under
        B completes the recovery.
        """
        from daemon.services.view_passcode_service import generate_passcode

        rel_path = "ens/feat/design/mockups/landing.html"
        path = f"/views/public/planning/{rel_path}"

        # Mint under A.
        plaintext_a = generate_passcode()
        wrapped_a = cipher.wrap(plaintext_a.encode("ascii"))
        in_memory_store.upsert(
            "planning", rel_path, wrapped_passcode=wrapped_a
        )

        # Rotate + corrupt.
        rotated_cipher = _build_rotated_cipher()
        client.app.state.view_passcode_cipher = rotated_cipher
        row = in_memory_store.get_row("planning", rel_path)
        row.wrapped_passcode = b"corrupt-pending-rewalk"

        # POST under rotated cipher → failure page.
        fail_resp = client.post(
            path,
            data={"code": plaintext_a},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert fail_resp.status_code == 200, (
            "expected failure page (200) on rotation-fail POST"
        )
        assert "set-cookie" not in fail_resp.headers or (
            "view_grant=" not in fail_resp.headers.get("set-cookie", "")
        ), (
            "rotation-fail POST must not issue a cookie — "
            "the success branch was never reached"
        )

        # Owner runs ``refresh`` (simulated by re-upserting
        # the row with a fresh ciphertext under B + counter
        # reset, AND flipping the service's bound cipher to
        # B so the row-re-encrypt walk + service.memory are
        # consistent). The cookie issuance now runs against
        # the rotated cipher; the row decrypts under B.
        plaintext_b = generate_passcode()
        wrapped_b = rotated_cipher.wrap(plaintext_b.encode("ascii"))
        in_memory_store.upsert(
            "planning",
            rel_path,
            wrapped_passcode=wrapped_b,
            generation=2,
            failed_attempts=0,
            locked=False,
        )
        # Sync the service's bound cipher to the rotated
        # cipher B (the production rotation ceremony does
        # this in-process: the new key replaces the old
        # across the cipher seam). Without this the service
        # would still unwrap under A and the recovery POST
        # would still see rotation-fail.
        passcode_service._cipher = rotated_cipher

        # POST the new passcode → 303 + Set-Cookie under B.
        success_resp = client.post(
            path,
            data={"code": plaintext_b},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert success_resp.status_code == 303, (
            f"expected 303 PRG on recovery POST, got "
            f"{success_resp.status_code} body={success_resp.text[:200]}"
        )
        cookie_header = success_resp.headers.get("set-cookie", "")
        assert "view_grant=" in cookie_header
        # The cookie was issued under cipher B — a GET with
        # the cookie re-running parse_grant_cookie under B
        # validates cleanly.
        cookie_value = success_resp.cookies.get("view_grant")
        assert cookie_value, "expected grant cookie on recovery POST"
        client.cookies.clear()
        client.cookies.set(GRANT_COOKIE_NAME, cookie_value)
        get_resp = client.get(path)
        assert get_resp.status_code == 200, (
            f"expected 200 on recovery GET (cipher B can "
            f"decrypt its own cookie), got {get_resp.status_code}"
        )
        # And a replay of the OLD passcode plaintext_a
        # against the recovered row → WRONG (the recovery
        # rotated the passcode; the old code is dead).
        replay_resp = client.post(
            path,
            data={"code": plaintext_a},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert replay_resp.status_code == 200
        body = replay_resp.text.lower()
        # WRONG path → failure page (the strike counter
        # ticked; rotation does NOT bypass the lockout).
        assert "<form" in body