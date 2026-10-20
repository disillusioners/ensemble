"""Router tests for the LIVE board view (ari-tools Phase 3 — deliverable B).

Covers the phase3-plan §7 suite C matrix:

* T-B1  — MOUNT ORDER, BOTH halves: the route-table INDEX assertion
  AND the behavioural half (a minted row ⇒ the board HTML, not JSON).
* T-B2  — no minted row ⇒ the uniform 404, BYTE-IDENTICAL (body AND
  headers) to a miss on an unrelated path.
* T-B3  — no cookie ⇒ the interstitial (200, ``script-src 'none'``,
  ``form-action 'self'``, ``cache-control`` EXACTLY ``no-store``, the
  form action = the board URL, the field name = ``code``).
* T-B4  — the right passcode via the EXISTING catch-all POST ⇒ 303 +
  ``Set-Cookie`` + ``Location`` ⇒ follow ⇒ the board renders.
* T-B5  — a wrong passcode ⇒ the failure page + a strike; the 10th
  wrong ⇒ the locked page.
* T-B6  — a grant cookie for a different rel / a stale generation ⇒
  the interstitial + a cookie clear.
* T-B7  — the FOUR regression pins (the reserved+unregistered POST
  still verifies · a wrong code still strikes and locks on the 10th ·
  a SOURCE-SCAN pin on ``_public_post`` · the exact 303 Location and
  the queryless cookie Path).
* T-B8  — escaping (a hostile title + a hostile note).
* T-B9  — the strict CSP header set + no external subresource.
* T-B10 — the filters.
* T-B11 — freshness (mutate a row, re-render, the output differs).
* T-B12 — the direct lane ⇒ the uniform 404.
* T-B13 — the reserved-name boot refusal (the GENERIC message).
* T-B14 — the notes timeline (oldest-first).
* T-B15 — the board HEAD route (headers only).
* T-B16 — the CSP nonce pairing (header ↔ body, per request).
* T-B17 — the raw-lane sandbox CSP on the ``reports`` root ONLY.
* the 2 MiB render ceiling + the "showing N of M" disclosure.

MOCK DISCIPLINE
---------------
The board is a REAL :class:`daemon.services.board_service.BoardService`
over a REAL in-memory SQLite database (``StaticPool`` +
``PRAGMA foreign_keys=ON``, the daemon's own posture), and the
passcode service is the REAL
:class:`daemon.services.view_passcode_service.ViewPasscodeService`
over a REAL database. The render is therefore proven against REAL
rows — no mock's own return value is ever the assertion.

SAFETY FENCE: every path comes from pytest's ``tmp_path`` — no
HOME-derived or live-shaped path is resolved anywhere in this file.
"""

from __future__ import annotations

import pathlib
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import SQLModel

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.board_service import BoardService
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher,
    ViewPasscodeService,
    VerifyRateLimiter,
    issue_grant_cookie,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

BOARD_PATH = "/views/public/board/index.html"
BOARD_ROOT = "board"
BOARD_REL = "index.html"


# ─────────────────────────────────────────────────────────────────
# Fixtures — REAL services over REAL storage
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def board_engine() -> Iterator:
    """A REAL in-memory SQLite engine with the board tables created.

    ``StaticPool`` + ``PRAGMA foreign_keys=ON`` — the daemon's own
    posture (``daemon/repositories/factory.py`` sets
    ``check_same_thread: False`` for exactly this reason: the board
    service is read from a ``to_thread`` worker).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # Importing the board models registers the tables (and the
    # ``projects`` table the board's project_id FK points at).
    import daemon.models.board  # noqa: F401
    from daemon.repositories.project.models import Project  # noqa: F401

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def board_service(board_engine) -> BoardService:
    """The REAL board service over the REAL database."""
    return BoardService(engine=board_engine)


@pytest.fixture
def passcode_engine(tmp_path: pathlib.Path) -> Iterator:
    db_path = tmp_path / "board-passcodes.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def cipher() -> ViewPasscodeCipher:
    return ViewPasscodeCipher(
        encryption_key=Fernet.generate_key().decode()
    )


@pytest.fixture
def passcode_service(passcode_engine, cipher) -> ViewPasscodeService:
    return ViewPasscodeService(engine=passcode_engine, cipher=cipher)


@pytest.fixture
def reports_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "reports"
    path.mkdir()
    return path


@pytest.fixture
def other_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A NON-``reports`` root — the byte-identical control."""
    path = tmp_path / "other"
    path.mkdir()
    (path / "page.html").write_text("<h1>other</h1>", encoding="utf-8")
    return path


@pytest.fixture
def live_views_service(
    reports_dir: pathlib.Path, other_root_dir: pathlib.Path
) -> LiveViewsService:
    cfg = LiveViewsConfig()
    cfg.roots["reports"] = LiveViewsRootConfig(
        type="filesystem",
        path=str(reports_dir.resolve()),
        allowed_extensions=["html"],
    )
    cfg.roots["other"] = LiveViewsRootConfig(
        type="filesystem",
        path=str(other_root_dir.resolve()),
        allowed_extensions=["html"],
    )
    return LiveViewsService(config=cfg)


@pytest.fixture
def limiter() -> VerifyRateLimiter:
    return VerifyRateLimiter(capacity=200, refill_per_minute=200)


@pytest.fixture
def client(
    live_views_service,
    passcode_service,
    cipher,
    limiter,
    board_service,
) -> TestClient:
    """A fully wired board client (the router + every service)."""

    class _ManagerDouble:
        def __init__(self, board_service):
            self.board_service = board_service

    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = live_views_service
    app.state.view_passcode_service = passcode_service
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = limiter
    app.state.manager = _ManagerDouble(board_service)
    # An https base URL so the Secure grant cookie is actually
    # STORED by the client jar (httpx refuses a Secure cookie over
    # http) — the 303 -> GET round-trip is part of the contract.
    return TestClient(app, base_url="https://testserver")


def _mint_board_row(passcode_service, board_service, **kwargs):
    """Create a REAL board item and mint the board's passcode row."""
    item = board_service.create_item(
        title=kwargs.pop("title", "Ship the thing"),
        body_md=kwargs.pop("body_md", "body text"),
        tags=kwargs.pop("tags", ["alpha"]),
        priority=kwargs.pop("priority", None),
        project_id=kwargs.pop("project_id", None),
        created_by_instance=kwargs.pop("created_by_instance", "inst-1"),
        due_at=kwargs.pop("due_at", None),
    )
    result = passcode_service.mint(BOARD_ROOT, BOARD_REL, "inst-1")
    return item, result


def _grant(
    client: TestClient,
    cipher: ViewPasscodeCipher,
    *,
    rel_path: str = BOARD_REL,
    generation: int = 1,
) -> None:
    """Install a grant cookie on the client."""
    token = issue_grant_cookie(
        cipher=cipher,
        root_name=BOARD_ROOT,
        rel_path=rel_path,
        generation=generation,
        ttl_seconds=3600,
    )
    client.cookies.set(
        "view_grant", token.decode("ascii"), path=BOARD_PATH
    )


# ─────────────────────────────────────────────────────────────────
# T-B1 — MOUNT ORDER (both halves)
# ─────────────────────────────────────────────────────────────────


class TestMountOrder:
    def test_b1a_the_board_route_precedes_the_public_catch_all(self) -> None:
        """The route-table INDEX half — Starlette matches in
        DECLARATION order (first match wins)."""
        router = build_router()
        paths = [
            (tuple(sorted(getattr(r, "methods", []) or [])), r.path)
            for r in router.routes
        ]
        board_get = paths.index((("GET",), BOARD_PATH))
        board_head = paths.index((("HEAD",), BOARD_PATH))
        catch_all = paths.index(
            (("GET",), "/views/public/{root_name}/{rel_path:path}")
        )
        assert board_get < catch_all, (board_get, catch_all)
        assert board_head < catch_all, (board_head, catch_all)

    def test_b1b_a_minted_row_renders_the_board_html_not_json(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        """The behavioural half — a response-only pin is ambiguous: with
        no minted row the mis-ordered route ALSO returns the uniform
        404."""
        _mint_board_row(passcode_service, board_service, title="Fresh row")
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        # NOT the JSON envelope.
        assert "view not found" not in resp.text
        # With a grant cookie the BOARD itself renders.
        _grant(client, cipher)
        granted = client.get(BOARD_PATH)
        assert granted.status_code == 200
        assert "Fresh row" in granted.text


# ─────────────────────────────────────────────────────────────────
# T-B2 — the uniform 404
# ─────────────────────────────────────────────────────────────────


class TestUniform404:
    def test_b2_no_row_is_byte_identical_to_an_unrelated_miss(
        self, client
    ) -> None:
        board_resp = client.get(BOARD_PATH)
        other_resp = client.get("/views/public/nope/nothing.html")
        assert board_resp.status_code == 404
        assert other_resp.status_code == 404
        assert board_resp.content == other_resp.content
        assert dict(board_resp.headers) == dict(other_resp.headers)
        # And no board-specific "unavailable" HTML leaks.
        assert "board" not in board_resp.text.lower()

    def test_b2_the_404_body_is_the_generic_json_envelope(
        self, client
    ) -> None:
        resp = client.get(BOARD_PATH)
        assert resp.json() == {"error": "view not found"}


# ─────────────────────────────────────────────────────────────────
# T-B3 — the interstitial
# ─────────────────────────────────────────────────────────────────


class TestInterstitial:
    def test_b3_no_cookie_renders_the_interstitial(
        self, client, passcode_service, board_service
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        csp = resp.headers["content-security-policy"]
        assert "script-src 'none'" in csp
        assert "form-action 'self'" in csp
        assert resp.headers["cache-control"] == "no-store"
        body = resp.text
        # The form action is the board URL, the field name is ``code``.
        assert f'action="{BOARD_PATH}"' in body
        assert 'name="code"' in body
        # And the board content is NOT disclosed.
        assert "Ship the thing" not in body


# ─────────────────────────────────────────────────────────────────
# T-B4 — the verify POST → 303 → the board renders
# ─────────────────────────────────────────────────────────────────


class TestVerifyFlow:
    def test_b4_the_right_passcode_issues_a_grant_and_the_board_renders(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _item, minted = _mint_board_row(
            passcode_service, board_service, title="Visible row"
        )
        resp = client.post(
            BOARD_PATH,
            data={"code": minted.passcode},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303, resp.text[:300]
        # The Location is EXACTLY the board URL.
        assert resp.headers["location"] == BOARD_PATH
        # The grant cookie is issued at the EXACT queryless path.
        set_cookie = resp.headers["set-cookie"]
        assert "view_grant=" in set_cookie
        assert f"Path={BOARD_PATH}" in set_cookie
        # Follow the redirect — the board renders.
        followed = client.get(BOARD_PATH)
        assert followed.status_code == 200
        assert "Visible row" in followed.text
        assert followed.headers["cache-control"] == "no-store"

    def test_b4_the_cookie_is_accepted_by_the_board_gate(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service, title="Cookie row")
        _grant(client, cipher)
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        assert "Cookie row" in resp.text


# ─────────────────────────────────────────────────────────────────
# T-B5 — a wrong passcode strikes and locks
# ─────────────────────────────────────────────────────────────────


class TestWrongPasscode:
    def test_b5_a_wrong_code_renders_the_failure_page_and_strikes(
        self, client, passcode_service, board_service
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        resp = client.post(
            BOARD_PATH,
            data={"code": "WRONG1"},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 200
        assert "not recognised" in resp.text
        row = passcode_service.get_row(BOARD_ROOT, BOARD_REL)
        assert row is not None
        assert row.failed_attempts == 1
        assert row.locked is False

    def test_b5_the_tenth_wrong_code_locks_the_board(
        self, client, passcode_service, board_service
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        for attempt in range(10):
            resp = client.post(
                BOARD_PATH,
                data={"code": "WRONG1"},
                follow_redirects=False,
                headers={"X-Forwarded-Proto": "https"},
            )
            assert resp.status_code == 200
        row = passcode_service.get_row(BOARD_ROOT, BOARD_REL)
        assert row is not None
        assert row.locked is True
        # The locked page renders.
        assert "locked" in resp.text.lower()
        # And the GET renders the locked page too.
        get_resp = client.get(BOARD_PATH)
        assert get_resp.status_code == 200
        assert "locked" in get_resp.text.lower()


# ─────────────────────────────────────────────────────────────────
# T-B6 — a stale / mismatched grant cookie
# ─────────────────────────────────────────────────────────────────


class TestStaleGrant:
    def test_b6_a_cookie_for_a_different_rel_falls_back_to_the_interstitial(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher, rel_path="other.html")
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        assert 'name="code"' in resp.text
        # The stale cookie is CLEARED.
        assert "Max-Age=0" in resp.headers.get("set-cookie", "")

    def test_b6_a_stale_generation_falls_back_to_the_interstitial(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        # Bump the row's generation — a generation-1 cookie is now stale.
        passcode_service.refresh(BOARD_ROOT, BOARD_REL, "inst-1")
        row = passcode_service.get_row(BOARD_ROOT, BOARD_REL)
        assert row is not None and row.generation == 2
        _grant(client, cipher, generation=1)
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        assert 'name="code"' in resp.text
        assert "Max-Age=0" in resp.headers.get("set-cookie", "")


# ─────────────────────────────────────────────────────────────────
# T-B7 — the FOUR regression pins
# ─────────────────────────────────────────────────────────────────


class TestShadowingGuard:
    def test_b7i_the_post_verifies_although_the_root_is_reserved_and_unregistered(
        self, client, passcode_service, board_service
    ) -> None:
        """``board`` is a RESERVED name AND is not a registered root —
        and the catch-all POST consults NEITHER list. That is exactly
        what lets the reserved board name verify."""
        from daemon.services.live_views import RESERVED_ROOT_NAMES

        assert BOARD_ROOT in RESERVED_ROOT_NAMES
        # NOT a registered root.
        assert client.app.state.live_views_service.has_root(
            BOARD_ROOT
        ) is False
        _item, minted = _mint_board_row(passcode_service, board_service)
        resp = client.post(
            BOARD_PATH,
            data={"code": minted.passcode},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303, resp.text[:300]

    def test_b7ii_a_wrong_code_still_strikes_and_locks_on_the_tenth(
        self, client, passcode_service, board_service
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        for _ in range(9):
            client.post(
                BOARD_PATH,
                data={"code": "WRONG1"},
                follow_redirects=False,
                headers={"X-Forwarded-Proto": "https"},
            )
        row = passcode_service.get_row(BOARD_ROOT, BOARD_REL)
        assert row is not None and row.failed_attempts == 9
        assert row.locked is False
        client.post(
            BOARD_PATH,
            data={"code": "WRONG1"},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        row = passcode_service.get_row(BOARD_ROOT, BOARD_REL)
        assert row is not None and row.locked is True

    def test_b7iii_the_public_post_source_has_no_reserved_name_guard(self) -> None:
        """The SOURCE-SCAN pin.

        ``_public_post`` must NOT consult ``is_reserved_root_name`` nor
        ``has_root`` — adding either would silently break the board's
        verify path. The guard is verified ABSENT today; this pin
        keeps it absent.
        """
        source = (
            REPO_ROOT / "daemon" / "routers" / "live_views.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def _public_post(")
        end = source.index("    @router.get(\"/public/board/index.html\")")
        body = source[start:end]
        assert "is_reserved_root_name" not in body
        assert "has_root" not in body

    def test_b7iv_the_location_and_the_cookie_path_are_exact(
        self, client, passcode_service, board_service
    ) -> None:
        _item, minted = _mint_board_row(passcode_service, board_service)
        resp = client.post(
            BOARD_PATH,
            data={"code": minted.passcode},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == BOARD_PATH
        set_cookie = resp.headers["set-cookie"]
        assert f"Path={BOARD_PATH}" in set_cookie
        # The cookie Path is QUERYLESS even for a query-bearing entry.
        resp = client.post(
            BOARD_PATH + "?status=todo",
            data={"code": minted.passcode},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == BOARD_PATH + "?status=todo"
        assert f"Path={BOARD_PATH}" in resp.headers["set-cookie"]


# ─────────────────────────────────────────────────────────────────
# T-B8 — escaping
# ─────────────────────────────────────────────────────────────────


class TestEscaping:
    def test_b8_hostile_content_never_reaches_the_body_raw(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        hostile_title = "<script>alert(1)</script>"
        hostile_note = '"><img src=x onerror=alert(1)>'
        item, _minted = _mint_board_row(
            passcode_service,
            board_service,
            title=hostile_title,
            body="safe body",
        )
        board_service.update_item(
            item_id=item["id"], note=hostile_note,
            author_instance="inst-1",
        )
        _grant(client, cipher)
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        body = resp.text
        assert hostile_title not in body
        assert hostile_note not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in body


# ─────────────────────────────────────────────────────────────────
# T-B9 — the CSP + the zero-external-subresource posture
# ─────────────────────────────────────────────────────────────────


class TestCSP:
    def test_b9_the_strict_header_set_is_present(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        resp = client.get(BOARD_PATH)
        csp = resp.headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "script-src 'none'" in csp
        assert "style-src 'nonce-" in csp
        assert "form-action 'self'" in csp
        assert "base-uri 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_b9_the_body_has_no_external_subresource_and_no_script(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        body = client.get(BOARD_PATH).text
        assert "https://" not in body
        assert "http://" not in body
        assert "<script" not in body.lower()
        assert "onerror=" not in body.lower()
        # The only style is the nonce'd block.
        assert body.count("<style") == 1


# ─────────────────────────────────────────────────────────────────
# T-B10 — the filters
# ─────────────────────────────────────────────────────────────────


class TestFilters:
    def test_b10_the_status_filter_narrows_the_display(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        todo, _ = _mint_board_row(
            passcode_service, board_service, title="A todo row"
        )
        doing, _ = _mint_board_row(
            passcode_service, board_service, title="A doing row"
        )
        board_service.update_item(
            item_id=doing["id"], status="in_progress",
            author_instance="inst-1",
        )
        _grant(client, cipher)
        resp = client.get(BOARD_PATH + "?status=in_progress")
        assert resp.status_code == 200
        assert "A doing row" in resp.text
        assert "A todo row" not in resp.text

    def test_b10_the_tag_filter_narrows_the_display(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(
            passcode_service, board_service, title="Tagged row",
            tags=["needle"],
        )
        _mint_board_row(
            passcode_service, board_service, title="Other row",
            tags=["haystack"],
        )
        _grant(client, cipher)
        resp = client.get(BOARD_PATH + "?tag=needle")
        assert resp.status_code == 200
        assert "Tagged row" in resp.text
        assert "Other row" not in resp.text

    def test_b10_an_unknown_filter_is_ignored_not_a_500(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service, title="Row")
        _grant(client, cipher)
        resp = client.get(BOARD_PATH + "?status=bogus")
        assert resp.status_code == 200
        assert "Row" in resp.text

    def test_b10_a_hostile_filter_echo_is_escaped_and_round_trips(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service, title="Row")
        _grant(client, cipher)
        resp = client.get(BOARD_PATH + "?tag=%3Cscript%3E")
        assert resp.status_code == 200
        assert "<script>" not in resp.text
        assert "&lt;script&gt;" in resp.text

    def test_b10_the_form_action_is_the_board_url(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        body = client.get(BOARD_PATH).text
        assert f'<form class="filters" method="get" action="{BOARD_PATH}">' in body


# ─────────────────────────────────────────────────────────────────
# T-B11 — freshness
# ─────────────────────────────────────────────────────────────────


class TestFreshness:
    def test_b11_two_gets_around_a_db_change_render_different_content(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        item, _ = _mint_board_row(
            passcode_service, board_service, title="The row",
            body_md="Before",
        )
        _grant(client, cipher)
        first = client.get(BOARD_PATH)
        assert "Before" in first.text
        # MUTATE the real row — the title is CREATE-ONLY, so the body
        # is the mutable field.
        board_service.update_item(
            item_id=item["id"], body_md="After",
            author_instance="inst-1",
        )
        second = client.get(BOARD_PATH)
        assert "After" in second.text
        assert "Before" not in second.text
        assert first.text != second.text

    def test_b11_a_new_row_appears_on_the_next_get(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service, title="First")
        _grant(client, cipher)
        assert "Second" not in client.get(BOARD_PATH).text
        board_service.create_item(
            title="Second",
            created_by_instance="inst-2",
        )
        assert "Second" in client.get(BOARD_PATH).text


# ─────────────────────────────────────────────────────────────────
# T-B12 — the direct lane
# ─────────────────────────────────────────────────────────────────


class TestDirectLane:
    def test_b12_the_direct_lane_is_the_uniform_404(self, client) -> None:
        resp = client.get("/views/board/index.html")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}


# ─────────────────────────────────────────────────────────────────
# T-B13 — the reserved-name boot refusal
# ─────────────────────────────────────────────────────────────────


class TestReservedBootRefusal:
    def test_b13_a_root_named_board_refuses_to_boot(self) -> None:
        from daemon.config import LiveViewsConfig, LiveViewsRootConfig

        with pytest.raises(Exception) as exc_info:
            LiveViewsConfig(
                roots={
                    "board": LiveViewsRootConfig(
                        type="filesystem", path="/tmp"
                    )
                }
            )
        msg = str(exc_info.value)
        assert "'board'" in msg
        assert "reserved" in msg.lower()
        # The message is GENERIC — it no longer claims a single
        # occupant ("the public passcode lane occupies this prefix").
        assert "public passcode lane occupies" not in msg


# ─────────────────────────────────────────────────────────────────
# T-B14 — the notes timeline
# ─────────────────────────────────────────────────────────────────


class TestNotesTimeline:
    def test_b14_the_notes_render_oldest_first(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        item, _ = _mint_board_row(
            passcode_service, board_service, title="With notes"
        )
        board_service.update_item(
            item_id=item["id"], note="first note",
            author_instance="inst-1",
        )
        board_service.update_item(
            item_id=item["id"], note="second note",
            author_instance="inst-1",
        )
        _grant(client, cipher)
        body = client.get(BOARD_PATH).text
        assert "first note" in body
        assert "second note" in body
        assert body.index("first note") < body.index("second note")
        # The timeline is a pure-HTML <details> block — no JS.
        assert "<details>" in body


# ─────────────────────────────────────────────────────────────────
# T-B15 — the board HEAD route
# ─────────────────────────────────────────────────────────────────


class TestHead:
    def test_b15_the_head_route_is_the_board_route(self, client) -> None:
        """FastAPI adds NO implicit HEAD — a missing board HEAD would
        fall through to the public HEAD catch-all."""
        router = build_router()
        board_head = [
            r
            for r in router.routes
            if getattr(r, "path", "") == BOARD_PATH
            and "HEAD" in (getattr(r, "methods", []) or [])
        ]
        assert len(board_head) == 1

    def test_b15_the_head_response_carries_the_board_headers(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        resp = client.head(BOARD_PATH)
        assert resp.status_code == 200
        csp = resp.headers["content-security-policy"]
        assert "script-src 'none'" in csp
        assert resp.headers["cache-control"] == "no-store"
        # The public HEAD catch-all would answer with the
        # ``_HARDENING_HEADERS`` shape instead.
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("referrer-policy") == "no-referrer"

    def test_b15_the_head_of_an_unminted_board_is_the_uniform_404(
        self, client
    ) -> None:
        resp = client.head(BOARD_PATH)
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────
# T-B16 — the CSP nonce pairing
# ─────────────────────────────────────────────────────────────────


class TestNoncePairing:
    def test_b16_the_header_and_the_body_carry_the_same_nonce(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        resp = client.get(BOARD_PATH)
        csp = resp.headers["content-security-policy"]
        nonce = csp.split("style-src 'nonce-", 1)[1].split("'", 1)[0]
        assert nonce
        assert f'<style nonce="{nonce}">' in resp.text

    def test_b16_two_gets_carry_different_nonces(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        first = client.get(BOARD_PATH)
        second = client.get(BOARD_PATH)

        def _nonce(resp):
            csp = resp.headers["content-security-policy"]
            return csp.split("style-src 'nonce-", 1)[1].split("'", 1)[0]

        assert _nonce(first) != _nonce(second)
        assert f'<style nonce="{_nonce(first)}">' in first.text
        assert f'<style nonce="{_nonce(second)}">' in second.text


# ─────────────────────────────────────────────────────────────────
# T-B17 — the raw-lane sandbox CSP
# ─────────────────────────────────────────────────────────────────


class TestSandboxCSP:
    def test_b17_the_reports_root_carries_the_sandbox_header(
        self, client, reports_dir: pathlib.Path
    ) -> None:
        (reports_dir / "page.html").write_text(
            "<h1>report</h1>", encoding="utf-8"
        )
        resp = client.get("/views/reports/page.html")
        assert resp.status_code == 200
        assert resp.headers["content-security-policy"] == (
            "sandbox allow-scripts"
        )
        # The sandbox header is ADDED TO — never replaces — the
        # unchanged hardening set.
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["cache-control"] == "private, max-age=60"

    def test_b17_the_public_lane_reports_root_carries_it_too(
        self, client, reports_dir: pathlib.Path, passcode_service, cipher
    ) -> None:
        (reports_dir / "page.html").write_text(
            "<h1>report</h1>", encoding="utf-8"
        )
        passcode_service.mint("reports", "page.html", "inst-1")
        token = issue_grant_cookie(
            cipher=cipher,
            root_name="reports",
            rel_path="page.html",
            generation=1,
            ttl_seconds=3600,
        )
        client.cookies.set(
            "view_grant", token.decode("ascii"),
            path="/views/public/reports/page.html",
        )
        resp = client.get("/views/public/reports/page.html")
        assert resp.status_code == 200
        assert resp.headers["content-security-policy"] == (
            "sandbox allow-scripts"
        )

    def test_b17_other_roots_are_byte_identical(
        self, client, other_root_dir: pathlib.Path
    ) -> None:
        resp = client.get("/views/other/page.html")
        assert resp.status_code == 200
        assert "content-security-policy" not in resp.headers
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["cache-control"] == "private, max-age=60"
        assert resp.text == "<h1>other</h1>"

    def test_b17_the_board_page_carries_no_sandbox_header(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service)
        _grant(client, cipher)
        resp = client.get(BOARD_PATH)
        csp = resp.headers["content-security-policy"]
        assert "sandbox" not in csp
        assert "script-src 'none'" in csp


# ─────────────────────────────────────────────────────────────────
# The truncation disclosure + the render ceiling
# ─────────────────────────────────────────────────────────────────


class TestDisclosure:
    def test_the_showing_n_of_m_disclosure_is_present(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        _mint_board_row(passcode_service, board_service, title="Only row")
        _grant(client, cipher)
        body = client.get(BOARD_PATH).text
        assert "Showing 1 of 1 items" in body

    def test_the_disclosure_reports_the_truthful_total(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        for index in range(3):
            _mint_board_row(
                passcode_service, board_service, title=f"Row {index}"
            )
        _grant(client, cipher)
        body = client.get(BOARD_PATH).text
        assert "Showing 3 of 3 items" in body

    def test_the_fetch_bound_disclosure_is_unambiguous(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        """The fetch bound was hit — the count on the page is a LOWER
        BOUND, not the TOTAL, and the disclosure must say so.

        Phase 1's ``count`` is the PAGE size (200), NOT the number of
        matching rows, so the old "Showing 50 of 200 items" read as a
        total while 201 rows actually match.
        """
        for index in range(201):
            board_service.create_item(
                title=f"Row {index}",
                body_md="body text",
                tags=["alpha"],
                created_by_instance="inst-1",
            )
        passcode_service.mint(BOARD_ROOT, BOARD_REL, "inst-1")
        _grant(client, cipher)
        body = client.get(BOARD_PATH).text
        assert (
            "Showing 50 of 200+ items (fetch bound — more items match "
            "these filters — narrow them to see the rest)"
        ) in body
        # The un-marked form must NOT appear — it reads as a total.
        assert "Showing 50 of 200 items" not in body

    def test_a_truncated_render_carries_a_visible_notice(
        self, client, passcode_service, board_service, cipher
    ) -> None:
        """The 2 MiB ceiling is enforced DURING assembly — a post-hoc
        slice would cut mid-tag. The notice is VISIBLE."""
        from daemon.services.board_view_service import BoardItemView
        from daemon.services.board_view_pages import render_board

        item = BoardItemView(
            id="wi-1234abcd",
            title="A row",
            body_md="x" * 5000,
            status="todo",
            tags=["alpha"],
        )
        # A tiny ceiling forces the truncation path — the notice is
        # VISIBLE and the HTML is never cut mid-tag.
        body = render_board(
            csp_nonce="n",
            items=[item],
            filter_options={},
            active_filters={},
            counts={},
            generated_at="now",
            shown=1,
            total=1,
            max_bytes=200,
        )
        assert "truncated" in body.lower()
        # The document is still well-formed — the ceiling cut BETWEEN
        # rows, never mid-tag.
        assert body.rstrip().endswith("</html>")
        assert "<summary>A row</summary>" not in body


# ─────────────────────────────────────────────────────────────────
# The board backend unavailable notice — never a 500
# ─────────────────────────────────────────────────────────────────


class TestBackendUnavailable:
    def test_an_unavailable_board_backend_renders_a_notice_not_a_500(
        self, client, passcode_service, cipher
    ) -> None:
        class _BrokenManager:
            @property
            def board_service(self):
                raise AttributeError("not wired")

        client.app.state.manager = _BrokenManager()
        passcode_service.mint(BOARD_ROOT, BOARD_REL, "inst-1")
        _grant(client, cipher)
        resp = client.get(BOARD_PATH)
        assert resp.status_code == 200
        assert "unavailable" in resp.text.lower()
        # No stack trace / internal detail.
        assert "RuntimeError" not in resp.text
        assert "Traceback" not in resp.text
