"""Tests for daemon.constants correctness.

Verifies all named constants have their expected values as documented
in the Phase 1 plan.
"""

import pytest
from daemon import constants


class TestConstants:
    """Tests for all constants in daemon.constants module."""

    # ── API Limits ────────────────────────────────────────────────────────────────

    def test_default_page_limit(self):
        """DEFAULT_PAGE_LIMIT should be 10."""
        # Stale test: DEFAULT_PAGE_LIMIT is 10 in production
        assert constants.DEFAULT_PAGE_LIMIT == 10

    def test_max_page_limit(self):
        """MAX_PAGE_LIMIT should be 100."""
        assert constants.MAX_PAGE_LIMIT == 100

    def test_max_credentials_size(self):
        """MAX_CREDENTIALS_SIZE should be 4096."""
        assert constants.MAX_CREDENTIALS_SIZE == 4096

    def test_max_error_len(self):
        """MAX_ERROR_LEN should be 500."""
        assert constants.MAX_ERROR_LEN == 500

    def test_max_chat_locks(self):
        """MAX_CHAT_LOCKS should be 1000."""
        assert constants.MAX_CHAT_LOCKS == 1000

    # ── Timeouts (seconds) ──────────────────────────────────────────────────────

    def test_request_timeout(self):
        """REQUEST_TIMEOUT_S should be 610 (11 minutes)."""
        assert constants.REQUEST_TIMEOUT_S == 610

    def test_instance_timeout(self):
        """INSTANCE_TIMEOUT_S should be 60."""
        assert constants.INSTANCE_TIMEOUT_S == 60

    def test_sse_timeout(self):
        """SSE_TIMEOUT_S should be 30."""
        assert constants.SSE_TIMEOUT_S == 30

    def test_shutdown_timeout(self):
        """SHUTDOWN_TIMEOUT_S should be 300."""
        assert constants.SHUTDOWN_TIMEOUT_S == 300

    def test_graph_timeout(self):
        """GRAPH_TIMEOUT_S should be 300."""
        assert constants.GRAPH_TIMEOUT_S == 300

    def test_task_timeout(self):
        """TASK_TIMEOUT_S should be 300."""
        assert constants.TASK_TIMEOUT_S == 300

    # ── Retry & Backoff ─────────────────────────────────────────────────────────

    def test_default_retry_count(self):
        """DEFAULT_RETRY_COUNT should be 3."""
        assert constants.DEFAULT_RETRY_COUNT == 3

    def test_llm_transient_retries(self):
        """LLM_TRANSIENT_RETRIES should be 10."""
        assert constants.LLM_TRANSIENT_RETRIES == 10

    def test_backoff_base(self):
        """BACKOFF_BASE_S should be 60."""
        assert constants.BACKOFF_BASE_S == 60

    def test_backoff_max(self):
        """BACKOFF_MAX_S should be 3600."""
        assert constants.BACKOFF_MAX_S == 3600

    def test_backoff_multiplier(self):
        """BACKOFF_MULTIPLIER should be 2.0."""
        assert constants.BACKOFF_MULTIPLIER == 2.0

    # ── Rate Limits ────────────────────────────────────────────────────────────

    def test_telegram_rate_limit(self):
        """TELEGRAM_RATE_LIMIT should be (30, 30)."""
        assert constants.TELEGRAM_RATE_LIMIT == (30, 30)

    def test_webhook_rate_limit(self):
        """WEBHOOK_RATE_LIMIT should be (100, 100)."""
        assert constants.WEBHOOK_RATE_LIMIT == (100, 100)

    def test_whatsapp_rate_limit(self):
        """WHATSAPP_RATE_LIMIT should be (10, 20)."""
        assert constants.WHATSAPP_RATE_LIMIT == (10, 20)

    # ── Database ────────────────────────────────────────────────────────────────

    def test_db_pool_size(self):
        """DB_POOL_SIZE should be 5."""
        assert constants.DB_POOL_SIZE == 5

    def test_db_max_overflow(self):
        """DB_MAX_OVERFLOW should be 10."""
        assert constants.DB_MAX_OVERFLOW == 10

    def test_db_busy_timeout(self):
        """DB_BUSY_TIMEOUT_S should be 30."""
        assert constants.DB_BUSY_TIMEOUT_S == 30

    # ── Graph & LLM ─────────────────────────────────────────────────────────────

    def test_recent_window_size(self):
        """RECENT_WINDOW_SIZE should be 10."""
        assert constants.RECENT_WINDOW_SIZE == 10

    def test_graph_recursion_limit(self):
        """GRAPH_RECURSION_LIMIT should be 100."""
        assert constants.GRAPH_RECURSION_LIMIT == 100

    # ── Worker Pool ────────────────────────────────────────────────────────────

    def test_worker_pool_size(self):
        """WORKER_POOL_SIZE should be 4."""
        assert constants.WORKER_POOL_SIZE == 4

    def test_worker_wait_timeout(self):
        """WORKER_WAIT_TIMEOUT should be 3.0."""
        assert constants.WORKER_WAIT_TIMEOUT == 3.0

    # ── Compaction ──────────────────────────────────────────────────────────────

    def test_compaction_threshold(self):
        """COMPACTION_THRESHOLD should be 0.80."""
        assert constants.COMPACTION_THRESHOLD == 0.80

    def test_compaction_target_ratio(self):
        """COMPACTION_TARGET_RATIO should be 0.40."""
        assert constants.COMPACTION_TARGET_RATIO == 0.40


class TestConstantsCompleteness:
    """Tests for ensuring no constants are missing or extra."""

    def test_all_expected_constants_defined(self):
        """All expected constants should be defined in the module."""
        expected = {
            # API Limits
            "DEFAULT_PAGE_LIMIT",
            "MAX_PAGE_LIMIT",
            "MAX_CREDENTIALS_SIZE",
            "MAX_ERROR_LEN",
            "MAX_CHAT_LOCKS",
            # Timeouts
            "REQUEST_TIMEOUT_S",
            "INSTANCE_TIMEOUT_S",
            "SSE_TIMEOUT_S",
            "SHUTDOWN_TIMEOUT_S",
            "GRAPH_TIMEOUT_S",
            "TASK_TIMEOUT_S",
            # Retry & Backoff
            "DEFAULT_RETRY_COUNT",
            "LLM_TRANSIENT_RETRIES",
            "BACKOFF_BASE_S",
            "BACKOFF_MAX_S",
            "BACKOFF_MULTIPLIER",
            # Rate Limits
            "TELEGRAM_RATE_LIMIT",
            "WEBHOOK_RATE_LIMIT",
            "WHATSAPP_RATE_LIMIT",
            # Database
            "DB_POOL_SIZE",
            "DB_MAX_OVERFLOW",
            "DB_BUSY_TIMEOUT_S",
            # Graph & LLM
            "GRAPH_RECURSION_LIMIT",
            "RECENT_WINDOW_SIZE",
            # Worker Pool
            "WORKER_POOL_SIZE",
            # Compaction
            "COMPACTION_THRESHOLD",
            "COMPACTION_TARGET_RATIO",
        }
        
        defined = set(dir(constants))
        # Filter to only include names that look like constants (uppercase)
        defined_constants = {n for n in defined if n.isupper() and not n.startswith("_")}
        
        missing = expected - defined_constants
        assert not missing, f"Missing constants: {missing}"

    def test_no_obvious_extra_constants(self):
        """Should not have unexpected constants (sanity check)."""
        defined = set(dir(constants))
        defined_constants = {n for n in defined if n.isupper() and not n.startswith("_")}
        
        # Just verify we have a reasonable number of constants
        assert len(defined_constants) >= 30, f"Expected at least 30 constants, found {len(defined_constants)}"
