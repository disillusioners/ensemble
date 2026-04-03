"""Unit tests for InstanceManager find_near_instance functionality."""

import pytest
from unittest.mock import MagicMock, patch


class TestEditDistance:
    """Tests for the _edit_distance helper method."""

    def test_edit_distance_identical(self):
        """Test that identical strings have distance 0."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        assert m._edit_distance("abc", "abc") == 0
        assert m._edit_distance("instance-123", "instance-123") == 0
        assert m._edit_distance("", "") == 0

    def test_edit_distance_one_substitution(self):
        """Test distance with one character substitution."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        assert m._edit_distance("abc", "abd") == 1
        assert m._edit_distance("abc", "abx") == 1
        assert m._edit_distance("hello", "hxllo") == 1

    def test_edit_distance_one_deletion(self):
        """Test distance with one character deletion."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        assert m._edit_distance("abc", "ab") == 1
        assert m._edit_distance("hello", "hell") == 1
        assert m._edit_distance("instance", "istance") == 1

    def test_edit_distance_one_insertion(self):
        """Test distance with one character insertion."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        assert m._edit_distance("ab", "abc") == 1
        assert m._edit_distance("hell", "hello") == 1
        assert m._edit_distance("abc", "zabc") == 1

    def test_edit_distance_multiple_changes(self):
        """Test distance with multiple character changes."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        assert m._edit_distance("abc", "xyz") == 3
        assert m._edit_distance("hello", "world") == 4
        assert m._edit_distance("abc123", "xyz456") == 6

    def test_edit_distance_case_insensitive(self):
        """Test that edit distance is case sensitive (caller handles lowercasing)."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        # The function itself is case-sensitive
        assert m._edit_distance("ABC", "abc") == 3
        assert m._edit_distance("Hello", "hello") == 1

    def test_edit_distance_uuid_pattern(self):
        """Test distance with typical UUID-like patterns."""
        from daemon.manager import InstanceManager
        
        m = InstanceManager.__new__(InstanceManager)
        
        # UUID-like: one char different
        assert m._edit_distance("abc-123-def", "abc-123-deg") == 1
        # UUID-like: 'h' replaces 'f' = 1 substitution
        assert m._edit_distance("abc-123-def", "abc-123-deh") == 1
        # UUID-like: one char missing (delete 'e' or insert 'e') = 1 operation
        assert m._edit_distance("abc-123-def", "abc-123-df") == 1


class TestFindNearInstance:
    """Tests for the find_near_instance method."""

    def _create_mock_instance(self, instance_id: str) -> MagicMock:
        """Helper to create a mock Instance object."""
        instance = MagicMock()
        instance.instance_id = instance_id
        return instance

    def test_find_near_instance_exact_match(self):
        """Test that exact match returns the instance."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        result = m.find_near_instance("abc-123-def")
        assert result == "abc-123-def"

    def test_find_near_instance_one_char_wrong(self):
        """Test matching with one character wrong."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # One character wrong
        result = m.find_near_instance("abc-123-deg")
        assert result == "abc-123-def"

    def test_find_near_instance_two_chars_wrong(self):
        """Test matching with two characters wrong (at threshold)."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # Two characters wrong - still within default threshold of 2
        result = m.find_near_instance("abc-123-deh")
        assert result == "abc-123-def"

    def test_find_near_instance_three_chars_wrong(self):
        """Test that three wrong characters exceeds threshold."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "abc-123-xyz" is 6 chars different from "abc-123-def"
        result = m.find_near_instance("abc-123-xyz")
        assert result is None

    def test_find_near_instance_length_difference(self):
        """Test that large length differences are filtered efficiently."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc"), self._create_mock_instance("xyz-very-long-id")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "abc-def" length 7, "abc" length 3, diff=4 > threshold 2
        result = m.find_near_instance("abc-def")
        assert result is None

    def test_find_near_instance_case_insensitive(self):
        """Test that matching is case-insensitive."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("ABC-123-DEF")],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # Lowercase input should match uppercase stored ID
        result = m.find_near_instance("abc-123-def")
        assert result == "ABC-123-DEF"

    def test_find_near_instance_no_match(self):
        """Test when no close match exists."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "hello" is very different from all stored IDs
        result = m.find_near_instance("hello-world")
        assert result is None

    def test_find_near_instance_empty_repository(self):
        """Test with empty repository."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = ([], 0)
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        result = m.find_near_instance("any-instance-id")
        assert result is None

    def test_find_near_instance_custom_max_distance(self):
        """Test with custom max_distance parameter."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def")],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # Three chars wrong, but with max_distance=3 it should match
        result = m.find_near_instance("abc-123-dzz", max_distance=3)
        assert result == "abc-123-def"

    def test_find_near_instance_first_match_returned(self):
        """Test that first matching instance is returned (ordered by recency)."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        # Repository returns in reverse chronological order
        mock_repo.list.return_value = (
            [
                self._create_mock_instance("newer-id"),
                self._create_mock_instance("older-id"),
            ],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # Should return the first match (newer-id), not second
        result = m.find_near_instance("newer-id")
        assert result == "newer-id"
