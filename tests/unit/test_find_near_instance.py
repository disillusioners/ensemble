"""Unit tests for InstanceManager find_near_instance functionality."""

import pytest
from unittest.mock import MagicMock, patch

from daemon.utils import edit_distance


class TestEditDistance:
    """Tests for the edit_distance utility function."""

    def test_edit_distance_identical(self):
        """Test that identical strings have distance 0."""
        assert edit_distance("abc", "abc") == 0
        assert edit_distance("instance-123", "instance-123") == 0
        assert edit_distance("", "") == 0

    def test_edit_distance_one_substitution(self):
        """Test distance with one character substitution."""
        assert edit_distance("abc", "abd") == 1
        assert edit_distance("abc", "abx") == 1
        assert edit_distance("hello", "hxllo") == 1

    def test_edit_distance_one_deletion(self):
        """Test distance with one character deletion."""
        assert edit_distance("abc", "ab") == 1
        assert edit_distance("hello", "hell") == 1
        assert edit_distance("instance", "istance") == 1

    def test_edit_distance_one_insertion(self):
        """Test distance with one character insertion."""
        assert edit_distance("ab", "abc") == 1
        assert edit_distance("hell", "hello") == 1
        assert edit_distance("abc", "zabc") == 1

    def test_edit_distance_multiple_changes(self):
        """Test distance with multiple character changes."""
        assert edit_distance("abc", "xyz") == 3
        assert edit_distance("hello", "world") == 4
        assert edit_distance("abc123", "xyz456") == 6

    def test_edit_distance_case_insensitive(self):
        """Test that edit distance is case sensitive (caller handles lowercasing)."""
        # The function itself is case-sensitive
        assert edit_distance("ABC", "abc") == 3
        assert edit_distance("Hello", "hello") == 1

    def test_edit_distance_uuid_pattern(self):
        """Test distance with typical UUID-like patterns."""
        # UUID-like: one char different
        assert edit_distance("abc-123-def", "abc-123-deg") == 1
        # UUID-like: 'h' replaces 'f' = 1 substitution
        assert edit_distance("abc-123-def", "abc-123-deh") == 1
        # UUID-like: one char missing (delete 'e' or insert 'e') = 1 operation
        assert edit_distance("abc-123-def", "abc-123-df") == 1


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
        """Test matching with two characters wrong (well within default threshold)."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # Two characters wrong - well within default threshold of 7
        result = m.find_near_instance("abc-123-deh")
        assert result == "abc-123-def"

    def test_find_near_instance_three_chars_wrong(self):
        """Test that three wrong characters matches (within default threshold 7)."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "abc-123-xyz" has edit distance 3 from "abc-123-def", within default threshold of 7
        result = m.find_near_instance("abc-123-xyz")
        assert result == "abc-123-def"

    def test_find_near_instance_length_difference(self):
        """Test that large length differences are filtered efficiently."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-very-long-id"), self._create_mock_instance("xyz-very-long-id")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "abc" (3 chars) vs "abc-very-long-id" (14 chars) - length diff = 11 > threshold 7
        result = m.find_near_instance("abc")
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

    def test_find_near_instance_seven_chars_wrong(self):
        """Test that seven wrong characters exceeds default threshold of 7."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def")],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "xyz-789-uvw" has edit distance 9 from "abc-123-def", exceeds threshold of 7
        result = m.find_near_instance("xyz-789-uvw")
        assert result is None

    def test_find_near_instance_at_threshold_seven(self):
        """Test matching at the default threshold boundary."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def")],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "abc-123-xyz" has edit distance 3 from "abc-123-def", within threshold of 7
        result = m.find_near_instance("abc-123-xyz")
        assert result == "abc-123-def"

    def test_find_near_instance_uuid_fix_insertion(self):
        """Test UUID with insertion (fix vs 01d) within threshold."""
        from daemon.manager import InstanceManager
        
        stored_id = "54509cae-a537-49e6-8268-901db36669b8"
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance(stored_id)],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "fix" inserted for "01d" = 3 operations (delete 3, insert 3, but net 3)
        # edit_distance("54509cae-a537-49e6-8268-90fixdb36669b8", stored_id) = 3
        search_input = "54509cae-a537-49e6-8268-90fixdb36669b8"
        result = m.find_near_instance(search_input)
        assert result == stored_id

    def test_find_near_instance_uuid_multiple_differences_within_threshold(self):
        """Test UUID with multiple differences at threshold boundary."""
        from daemon.manager import InstanceManager
        
        stored_id = "54509cae-a537-49e6-8268-901db36669b8"
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance(stored_id)],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "11111aaa" vs "54509cae" = 7 substitutions
        # edit_distance("11111aaa-a537-49e6-8268-901db36669b8", stored_id) = 7
        search_input = "11111aaa-a537-49e6-8268-901db36669b8"
        result = m.find_near_instance(search_input)
        assert result == stored_id

    def test_find_near_instance_uuid_exceeds_threshold(self):
        """Test UUID with differences exceeding threshold."""
        from daemon.manager import InstanceManager
        
        stored_id = "54509cae-a537-49e6-8268-901db36669b8"
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance(stored_id)],
            1
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        # "11112222" vs "901db36669b8" = 11 differences
        # edit_distance("54509cae-a537-49e6-8268-11112222", stored_id) = 11
        search_input = "54509cae-a537-49e6-8268-11112222"
        result = m.find_near_instance(search_input)
        assert result is None
