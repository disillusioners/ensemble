"""Tests for InstanceManager.engine property (Phase 1 feature).

The property is a public read-only accessor for the underlying SQLAlchemy
Engine. Existing tests set ``manager._engine = MagicMock()`` and expect
``manager.engine`` to return that mock — this is the contract we test here.
"""

from unittest.mock import MagicMock, patch

import pytest

from daemon.manager import InstanceManager


@pytest.fixture
def instance_manager_cls():
    """Return InstanceManager class with heavy init patched out.

    The full __init__ creates an engine, repositories, services etc.
    For a property test we only need the class object.
    """
    with patch.object(InstanceManager, "__init__", lambda self: None):
        yield InstanceManager


class TestEngineProperty:
    """Tests for the public read-only engine property."""

    def test_engine_property_exists(self, instance_manager_cls):
        """InstanceManager exposes an `engine` attribute."""
        manager = instance_manager_cls()
        # Should be accessible as a property (i.e. via class, not only on instance)
        assert hasattr(InstanceManager, "engine"), "InstanceManager must define engine"

    def test_engine_property_is_read_only(self, instance_manager_cls):
        """engine has no setter — read-only property."""
        descriptor = InstanceManager.__dict__.get("engine")
        assert descriptor is not None, "engine must be a class-level descriptor"
        assert isinstance(descriptor, property), "engine must be a @property"
        assert descriptor.fset is None, "engine must be read-only (no fset)"
        assert descriptor.fdel is None, "engine must be read-only (no fdel)"

    def test_engine_returns_underlying_engine(self, instance_manager_cls):
        """manager.engine returns manager._engine."""
        manager = instance_manager_cls()
        manager._engine = MagicMock(name="underlying_engine")
        assert manager.engine is manager._engine

    def test_setting_engine_attribute_raises(self, instance_manager_cls):
        """Assigning to manager.engine raises AttributeError (read-only)."""
        manager = instance_manager_cls()
        manager._engine = MagicMock(name="underlying")
        with pytest.raises(AttributeError):
            manager.engine = MagicMock(name="attempted_set")

    def test_mock_engine_visible_via_property(self, instance_manager_cls):
        """Tests using `manager._engine = MagicMock()` see the mock via manager.engine."""
        manager = instance_manager_cls()
        mock_engine = MagicMock(name="mock_engine")
        manager._engine = mock_engine

        # Common test pattern: caller expects `manager.engine` to be the mock
        assert manager.engine is mock_engine
        # The mock is the same object
        assert manager.engine is not None

    def test_deleting_engine_attribute_raises(self, instance_manager_cls):
        """Deleting manager.engine raises AttributeError (read-only)."""
        manager = instance_manager_cls()
        manager._engine = MagicMock(name="underlying")
        with pytest.raises(AttributeError):
            del manager.engine
