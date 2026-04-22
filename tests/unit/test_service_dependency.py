"""Tests for create_service_dependency factory function."""

import pytest
from fastapi import HTTPException

from daemon.utils import create_service_dependency


class MockService:
    """Mock service class for testing."""
    def __init__(self, name: str):
        self.name = name


class TestCreateServiceDependency:
    """Tests for create_service_dependency factory."""

    def test_creates_getter_function(self):
        """Should create a callable getter function."""
        get_service = create_service_dependency(MockService)
        assert callable(get_service)

    def test_raises_503_when_not_set(self):
        """Getter should raise HTTPException(503) when service not set."""
        get_service = create_service_dependency(MockService)
        
        with pytest.raises(HTTPException) as exc_info:
            get_service()
        
        assert exc_info.value.status_code == 503
        assert "MockService" in str(exc_info.value.detail)
        assert "not initialized" in str(exc_info.value.detail)

    def test_set_service_stores_instance(self):
        """set_service should store the service instance."""
        get_service = create_service_dependency(MockService)
        
        # Initially should raise
        with pytest.raises(HTTPException):
            get_service()
        
        # Set the service
        service = MockService("test-instance")
        get_service.set_service(service)
        
        # Now should return the instance
        result = get_service()
        assert result is service
        assert result.name == "test-instance"

    def test_works_with_different_types(self):
        """Should work with different service types."""
        class ServiceA:
            pass
        
        class ServiceB:
            pass
        
        get_service_a = create_service_dependency(ServiceA)
        get_service_b = create_service_dependency(ServiceB)
        
        # Each has its own state
        service_a = ServiceA()
        service_b = ServiceB()
        
        get_service_a.set_service(service_a)
        
        # get_service_b should still raise
        with pytest.raises(HTTPException):
            get_service_b()
        
        # But get_service_a should work
        assert get_service_a() is service_a

    def test_has_set_service_attribute(self):
        """Returned function should have set_service attribute."""
        get_service = create_service_dependency(MockService)
        
        assert hasattr(get_service, "set_service")
        assert callable(get_service.set_service)

    def test_set_service_replaces_instance(self):
        """set_service should replace any existing instance."""
        get_service = create_service_dependency(MockService)
        
        service1 = MockService("first")
        service2 = MockService("second")
        
        get_service.set_service(service1)
        assert get_service() is service1
        
        get_service.set_service(service2)
        assert get_service() is service2
        assert get_service().name == "second"

    def test_error_message_includes_type_name(self):
        """Error message should include the service type name."""
        class SpecialService:
            pass
        
        get_service = create_service_dependency(SpecialService)
        
        with pytest.raises(HTTPException) as exc_info:
            get_service()
        
        assert "SpecialService" in str(exc_info.value.detail)

    def test_multiple_instances_independent(self):
        """Multiple dependency instances should be independent."""
        get1 = create_service_dependency(MockService)
        get2 = create_service_dependency(MockService)
        
        # Set only on get1
        service1 = MockService("one")
        get1.set_service(service1)
        
        # get1 works, get2 raises
        assert get1() is service1
        with pytest.raises(HTTPException):
            get2()


class TestCreateServiceDependencyWithNone:
    """Tests for setting service to None."""

    def test_can_set_to_none_after_being_set(self):
        """Should allow setting service to None after it was set."""
        get_service = create_service_dependency(MockService)
        
        # Set a service
        service = MockService("test")
        get_service.set_service(service)
        assert get_service() is service
        
        # Set back to None
        get_service.set_service(None)
        
        # Should raise again
        with pytest.raises(HTTPException) as exc_info:
            get_service()
        
        assert exc_info.value.status_code == 503
