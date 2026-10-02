"""Unit tests for ``daemon/services/env_key_policy.py`` (W2 fix pass).

Pins the SINGLE conservative word list shared by the write gate
(``daemon.tools.infra::mcp_set_env``) and the read/redact surface
(``daemon.routers.mcp_servers::redact_secrets``):

* exactly the reviewer's eight words — no beyond-reviewer additions;
* ``BASE`` / ``HEADERS`` are redact-only extras, never write-gate
  words;
* the ASCII-identifier validator for write-path env keys.
"""

from __future__ import annotations

import pytest

from daemon.services.env_key_policy import (
    REDACT_ONLY_MARKER_WORDS,
    SECRET_MARKER_WORDS,
    env_key_is_secret_shaped,
    is_ascii_env_key,
)


class TestSharedWordList:
    def test_exactly_the_reviewer_eight_words(self) -> None:
        assert SECRET_MARKER_WORDS == (
            "KEY",
            "TOKEN",
            "SECRET",
            "PASSWORD",
            "CREDENTIAL",
            "PRIVATE",
            "PWD",
            "AUTH",
        )

    def test_redact_only_extras(self) -> None:
        assert REDACT_ONLY_MARKER_WORDS == ("BASE", "HEADERS")

    @pytest.mark.parametrize(
        "key",
        [
            "BYOK_API_KEY",
            "api_token",
            "CLIENT_SECRET",
            "db_password",
            "SERVICE_CREDENTIAL",
            "PRIVATE_KEY_PATH",
            "DB_PWD",
            "OD_AUTH",
        ],
    )
    def test_every_word_classifies(self, key: str) -> None:
        assert env_key_is_secret_shaped(key) is True

    @pytest.mark.parametrize(
        "key",
        ["BYOK_BASE_URL", "BYOK_MODEL", "OD_DAEMON_URL", "MY_LOG_LEVEL"],
    )
    def test_non_secret_keys_do_not_classify(self, key: str) -> None:
        assert env_key_is_secret_shaped(key) is False

    def test_case_insensitive_substring(self) -> None:
        assert env_key_is_secret_shaped("KeY")
        assert env_key_is_secret_shaped("pwd")


class TestAsciiEnvKey:
    @pytest.mark.parametrize(
        "key",
        ["OPENAI_API_KEY", "_x", "a", "A_b_2", "MY_LOG_LEVEL_2"],
    )
    def test_valid_identifiers_pass(self, key: str) -> None:
        assert is_ascii_env_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "КЕY",  # Cyrillic homoglyph
            "key\u200b",  # zero-width space
            "すし_KEY",
            "a-b",  # hyphen not allowed
            "1KEY",  # must not start with a digit
            "KEY NAME",  # space
            "",
        ],
    )
    def test_non_identifiers_fail(self, key: str) -> None:
        assert is_ascii_env_key(key) is False

    def test_non_string_is_rejected(self) -> None:
        assert is_ascii_env_key(None) is False  # type: ignore[arg-type]
        assert is_ascii_env_key(5) is False  # type: ignore[arg-type]
