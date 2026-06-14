"""Tests for the Database Connection Registry repository.

Phase 1 of the Database Tool Category feature. The repository
stores/retrieves opaque encrypted credentials — it must never
decrypt or transform them.
"""

import pytest
from sqlmodel import SQLModel, create_engine, Session as SQLModelSession
from sqlalchemy.exc import IntegrityError

from daemon.repositories.db_connection import (
    DbConnectionConfig,
    DbConnectionRepository,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
    """Create in-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine):
    """Create a DbConnectionRepository bound to the test engine."""
    return DbConnectionRepository(engine)


# =============================================================================
# Group 1: Model Tests
# =============================================================================


class TestDbConnectionConfigModel:
    """Tests for the SQLModel DbConnectionConfig table definition."""

    def test_model_has_correct_tablename(self):
        """Test the table is named ``db_connections``."""
        assert DbConnectionConfig.__tablename__ == "db_connections"

    def test_default_field_values(self):
        """Test default values for optional fields."""
        config = DbConnectionConfig(
            connection_name="x",
            db_type="postgres",
            host="localhost",
        )
        assert config.id is not None and len(config.id) > 0
        assert config.connection_name == "x"
        assert config.db_type == "postgres"
        assert config.host == "localhost"
        assert config.port is None
        assert config.database is None
        assert config.username is None
        assert config.credentials is None
        assert config.ssl_mode == "prefer"
        assert config.created_at is not None
        assert config.updated_at is not None

    def test_unique_ids_across_instances(self):
        """Test that each instance gets a unique UUID."""
        a = DbConnectionConfig(
            connection_name="a", db_type="postgres", host="h"
        )
        b = DbConnectionConfig(
            connection_name="b", db_type="postgres", host="h"
        )
        assert a.id != b.id

    def test_table_created_in_db(self, engine):
        """Test the table is created with the expected columns."""
        from sqlalchemy import text

        with SQLModelSession(engine) as session:
            result = session.exec(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='db_connections'")
            )
            assert list(result), "db_connections table should exist"


# =============================================================================
# Group 2: to_public_dict() — must NEVER include credentials
# =============================================================================


class TestToPublicDict:
    """Tests for DbConnectionConfig.to_public_dict()."""

    def test_excludes_credentials_field(self):
        """The credentials field must never appear in the public dict."""
        config = DbConnectionConfig(
            connection_name="prod",
            db_type="postgres",
            host="db.example.com",
            port=5432,
            database="app",
            username="app_user",
            credentials="enc::super-secret-token",
            ssl_mode="require",
        )
        public = config.to_public_dict()
        assert "credentials" not in public

    def test_has_password_true_when_credentials_set(self):
        """has_password is True when credentials are present."""
        config = DbConnectionConfig(
            connection_name="prod",
            db_type="postgres",
            host="db",
            credentials="enc::something",
        )
        assert config.to_public_dict()["has_password"] is True

    def test_has_password_false_when_credentials_none(self):
        """has_password is False when credentials are absent."""
        config = DbConnectionConfig(
            connection_name="prod",
            db_type="postgres",
            host="db",
            credentials=None,
        )
        assert config.to_public_dict()["has_password"] is False

    def test_includes_all_non_secret_fields(self):
        """All public metadata is included in the public dict."""
        config = DbConnectionConfig(
            connection_name="prod",
            db_type="postgres",
            host="db.example.com",
            port=5432,
            database="app",
            username="app_user",
            credentials="enc::secret",
            ssl_mode="require",
        )
        public = config.to_public_dict()
        assert public["id"] == config.id
        assert public["connection_name"] == "prod"
        assert public["db_type"] == "postgres"
        assert public["host"] == "db.example.com"
        assert public["port"] == 5432
        assert public["database"] == "app"
        assert public["username"] == "app_user"
        assert public["ssl_mode"] == "require"
        assert public["created_at"] == config.created_at
        assert public["updated_at"] == config.updated_at


# =============================================================================
# Group 2.5: Pydantic-level credential non-leak protection
#
# Guards the field-level Pydantic protections added on the
# ``credentials`` field (``exclude=True``, ``repr=False``) and the
# explicit ``__repr__`` override. ``to_public_dict`` is covered by
# Group 2 above; this group covers *every other* standard Pydantic /
# Python serialization surface. If any future contributor relaxes the
# field flags or removes the repr override, these tests fail loudly.
# =============================================================================


class TestCredentialNonLeak:
    """Regression tests: ``credentials`` must never leak via repr/str/dump."""

    SECRET = "encrypted_secret_value"

    def _make_config(self) -> DbConnectionConfig:
        return DbConnectionConfig(
            connection_name="leak_check",
            db_type="postgres",
            host="db.example.com",
            port=5432,
            database="app",
            username="app_user",
            credentials=self.SECRET,
            ssl_mode="require",
        )

    def test_repr_does_not_contain_credential(self):
        """``repr(config)`` must never include the credential value."""
        config = self._make_config()
        rendered = repr(config)
        assert self.SECRET not in rendered
        # Defensive: also check the field name is not even mentioned
        # in the explicit repr, to make the contract obvious.
        assert "credentials=" not in rendered

    def test_str_does_not_contain_credential(self):
        """``str(config)`` must never include the credential value."""
        config = self._make_config()
        assert self.SECRET not in str(config)

    def test_model_dump_excludes_credentials_key(self):
        """``model_dump()`` must omit the ``credentials`` key entirely."""
        config = self._make_config()
        dumped = config.model_dump()
        assert "credentials" not in dumped
        assert self.SECRET not in dumped.values()
        assert self.SECRET not in str(dumped)

    def test_model_dump_json_excludes_credential_value(self):
        """``model_dump_json()`` must not include the credential value."""
        config = self._make_config()
        json_str = config.model_dump_json()
        assert self.SECRET not in json_str
        # And the key should be absent from the JSON payload too.
        assert '"credentials"' not in json_str


# =============================================================================
# Group 3: Repository CRUD
# =============================================================================


class TestRepositoryCreate:
    """Tests for repository.create()."""

    def test_create_with_minimal_fields(self, repository):
        """Create a connection with only required fields."""
        config = repository.create(
            connection_name="minimal",
            db_type="postgres",
            host="localhost",
        )
        assert config.id is not None
        assert config.connection_name == "minimal"
        assert config.db_type == "postgres"
        assert config.host == "localhost"
        assert config.port is None
        assert config.database is None
        assert config.username is None
        assert config.credentials is None
        assert config.ssl_mode == "prefer"
        assert config.created_at is not None
        assert config.updated_at is not None

    def test_create_with_all_fields(self, repository):
        """Create a connection with every field populated."""
        config = repository.create(
            connection_name="full",
            db_type="postgres",
            host="db.example.com",
            port=5432,
            database="appdb",
            username="app",
            credentials="enc::blob",
            ssl_mode="require",
        )
        assert config.port == 5432
        assert config.database == "appdb"
        assert config.username == "app"
        assert config.credentials == "enc::blob"
        assert config.ssl_mode == "require"


class TestRepositoryGetByName:
    """Tests for repository.get_by_name()."""

    def test_get_existing_connection(self, repository):
        """get_by_name returns the connection that was created."""
        created = repository.create(
            connection_name="lookup", db_type="postgres", host="h"
        )
        fetched = repository.get_by_name("lookup")
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.connection_name == "lookup"

    def test_get_nonexistent_connection_returns_none(self, repository):
        """get_by_name returns None for an unknown name."""
        assert repository.get_by_name("nope") is None


class TestRepositoryList:
    """Tests for repository.list_all() and list_public()."""

    def test_list_all_empty(self, repository):
        """list_all on an empty DB returns an empty list."""
        assert repository.list_all() == []

    def test_list_all_ordered_by_name(self, repository):
        """list_all returns connections ordered by connection_name."""
        repository.create("zeta", db_type="postgres", host="h")
        repository.create("alpha", db_type="postgres", host="h")
        repository.create("mu", db_type="postgres", host="h")
        names = [c.connection_name for c in repository.list_all()]
        assert names == ["alpha", "mu", "zeta"]

    def test_list_public_empty(self, repository):
        """list_public on an empty DB returns an empty list."""
        assert repository.list_public() == []

    def test_list_public_excludes_credentials(self, repository):
        """list_public never includes the credentials value."""
        repository.create(
            connection_name="with_secret",
            db_type="postgres",
            host="h",
            credentials="enc::super-secret-payload",
        )
        public = repository.list_public()
        assert len(public) == 1
        assert "credentials" not in public[0]
        assert public[0]["has_password"] is True
        assert public[0]["connection_name"] == "with_secret"


class TestRepositoryGetCredentials:
    """Tests for repository.get_credentials()."""

    def test_returns_opaque_string_unchanged(self, repository):
        """Repository returns credentials verbatim — no decryption, no transformation."""
        opaque = "enc::v1::deadbeefdeadbeef"
        repository.create(
            connection_name="c",
            db_type="postgres",
            host="h",
            credentials=opaque,
        )
        assert repository.get_credentials("c") == opaque

    def test_returns_none_when_credentials_not_set(self, repository):
        """Returns None for a connection created without credentials."""
        repository.create(
            connection_name="c", db_type="postgres", host="h"
        )
        assert repository.get_credentials("c") is None

    def test_returns_none_for_unknown_connection(self, repository):
        """Returns None when the connection does not exist."""
        assert repository.get_credentials("ghost") is None

    def test_does_not_decrypt_specifically(self, repository):
        """Sanity: the repo does not try to interpret the credentials payload."""
        # A payload that is *not* a real encryption format is stored as-is.
        payload = "not-encrypted-at-all-just-a-string"
        repository.create(
            connection_name="plain",
            db_type="postgres",
            host="h",
            credentials=payload,
        )
        assert repository.get_credentials("plain") == payload


class TestRepositoryDelete:
    """Tests for repository.delete()."""

    def test_delete_existing_returns_true(self, repository):
        """Delete a known connection and confirm it's gone."""
        repository.create(
            connection_name="doomed", db_type="postgres", host="h"
        )
        assert repository.delete("doomed") is True
        assert repository.get_by_name("doomed") is None

    def test_delete_nonexistent_returns_false(self, repository):
        """Deleting an unknown name returns False (does not raise)."""
        assert repository.delete("ghost") is False

    def test_delete_twice_returns_false_second_time(self, repository):
        """Deleting the same connection twice — second call returns False."""
        repository.create(
            connection_name="once", db_type="postgres", host="h"
        )
        assert repository.delete("once") is True
        assert repository.delete("once") is False


# =============================================================================
# Group 4: Unique constraint
# =============================================================================


class TestUniqueNameConstraint:
    """Tests for the unique-name constraint on db_connections."""

    def test_duplicate_name_raises_integrity_error(self, repository, engine):
        """Creating a second connection with the same name raises IntegrityError."""
        repository.create(
            connection_name="unique", db_type="postgres", host="h"
        )
        with pytest.raises(IntegrityError):
            with SQLModelSession(engine) as session:
                session.add(
                    DbConnectionConfig(
                        connection_name="unique",
                        db_type="postgres",
                        host="other",
                    )
                )
                session.commit()

    def test_repositories_create_also_rejects_duplicates(self, repository):
        """Calling repository.create() with a duplicate name raises IntegrityError."""
        repository.create(
            connection_name="dup", db_type="postgres", host="h"
        )
        with pytest.raises(IntegrityError):
            repository.create(
                connection_name="dup", db_type="postgres", host="other"
            )


# =============================================================================
# Group 5: update_timestamp helper
# =============================================================================


class TestUpdateTimestamp:
    """Tests for the model.update_timestamp() helper."""

    def test_bumps_updated_at(self):
        """update_timestamp() updates the updated_at field."""
        import time

        config = DbConnectionConfig(
            connection_name="c", db_type="postgres", host="h"
        )
        original = config.updated_at
        # Sleep just long enough that ISO timestamps differ at microsecond resolution
        time.sleep(0.001)
        config.update_timestamp()
        assert config.updated_at != original
        assert config.updated_at > original


# =============================================================================
# Group 6: Factory integration
# =============================================================================


class TestFactoryIntegration:
    """Tests for create_db_connection_repository() factory function."""

    def test_factory_with_engine_returns_repository(self, engine):
        """Factory returns a DbConnectionRepository bound to the given engine."""
        from daemon.repositories.factory import create_db_connection_repository

        repo = create_db_connection_repository(engine=engine, create_tables=False)
        assert isinstance(repo, DbConnectionRepository)
        assert repo.engine is engine

    def test_factory_with_no_args_raises(self):
        """Factory raises ValueError if neither config nor engine is provided."""
        from daemon.repositories.factory import create_db_connection_repository

        with pytest.raises(ValueError):
            create_db_connection_repository()

    def test_factory_creates_tables_with_config(self):
        """Factory with create_tables=True creates the db_connections table."""
        from daemon.repositories.factory import (
            create_db_connection_repository,
            DatabaseConfig,
        )
        from sqlalchemy import text

        engine = create_engine("sqlite:///:memory:", echo=False)
        try:
            create_db_connection_repository(
                config=DatabaseConfig.sqlite(":memory:"),
                engine=engine,
                create_tables=True,
            )
            with SQLModelSession(engine) as session:
                result = session.exec(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='db_connections'")
                )
                assert list(result)
        finally:
            engine.dispose()
