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
        """Test that exact match returns the instance in a list."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        mock_repo.list.return_value = (
            [self._create_mock_instance("abc-123-def"), self._create_mock_instance("xyz-789-ghi")],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        result = m.find_near_instance("abc-123-def")
        assert result == ["abc-123-def"]

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
        assert result == ["abc-123-def"]

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
        assert result == ["abc-123-def"]

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
        assert result == ["abc-123-def"]

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
        assert result == ["ABC-123-DEF"]

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
        assert result == ["abc-123-def"]

    def test_find_near_instance_first_match_returned(self):
        """Test that all matching instances are returned (ordered by recency, then distance)."""
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
        
        # Should return both matches since both are exact
        result = m.find_near_instance("newer-id")
        assert result == ["newer-id", "older-id"]

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
        assert result == ["abc-123-def"]

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
        assert result == [stored_id]

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
        assert result == [stored_id]

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
        assert result == []

    def test_find_near_instance_returns_sorted_by_distance(self):
        """Test that multiple matches are returned sorted by edit distance."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        # Three instances with different distances from "abc-123"
        mock_repo.list.return_value = (
            [
                self._create_mock_instance("abc-456"),  # distance 3
                self._create_mock_instance("abc-123"),  # distance 0
                self._create_mock_instance("abc-789"),  # distance 3
                self._create_mock_instance("xyz-123"),  # distance 3
            ],
            4
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        result = m.find_near_instance("abc-123")
        # Should return all matches sorted by distance
        assert result == ["abc-123", "abc-456", "abc-789", "xyz-123"]

    def test_find_near_instance_multiple_matches_at_same_distance(self):
        """Test that multiple matches at same distance are all returned."""
        from daemon.manager import InstanceManager
        
        mock_repo = MagicMock()
        # Two instances with same distance from "abc-123"
        mock_repo.list.return_value = (
            [
                self._create_mock_instance("abc-456"),  # distance 3
                self._create_mock_instance("xyz-123"),  # distance 3
            ],
            2
        )
        
        m = InstanceManager.__new__(InstanceManager)
        m._instance_repository = mock_repo
        
        result = m.find_near_instance("abc-123")
        # Both should be returned (order may vary but both present)
        assert len(result) == 2
        assert "abc-456" in result
        assert "xyz-123" in result


class TestResolveInstanceId:
    """Tests for the _resolve_instance_id helper function."""

    def _create_mock_manager(self):
        """Helper to create a mock manager."""
        mock_manager = MagicMock()
        return mock_manager

    def test_exact_match_returns_instance_id(self):
        """Test that exact match returns the instance_id unchanged."""
        from daemon.tools.instance import _resolve_instance_id

        mock_manager = self._create_mock_manager()
        # get_instance succeeds for exact match
        mock_manager.get_instance.return_value = MagicMock()

        result = _resolve_instance_id(mock_manager, "exact-match-id")

        assert result == "exact-match-id"
        mock_manager.get_instance.assert_called_once_with("exact-match-id")
        mock_manager.find_near_instance.assert_not_called()

    def test_near_match_single_suggests_correction(self):
        """Test single near match raises ValueError with suggestion."""
        from daemon.tools.instance import _resolve_instance_id

        mock_manager = self._create_mock_manager()
        # get_instance fails (KeyError), find_near_instance returns 1 match
        mock_manager.get_instance.side_effect = KeyError("not found")
        mock_manager.find_near_instance.return_value = ["correct-id"]

        with pytest.raises(ValueError) as exc_info:
            _resolve_instance_id(mock_manager, "incrrect-id")

        assert "Did you mean 'correct-id'?" in str(exc_info.value)
        assert "incrrect-id" in str(exc_info.value)
        mock_manager.find_near_instance.assert_called_once_with("incrrect-id", max_distance=7)

    def test_near_match_multiple_lists_all_candidates(self):
        """Test multiple near matches raises ValueError listing all candidates."""
        from daemon.tools.instance import _resolve_instance_id

        mock_manager = self._create_mock_manager()
        # get_instance fails (KeyError), find_near_instance returns 2 matches
        mock_manager.get_instance.side_effect = KeyError("not found")
        mock_manager.find_near_instance.return_value = ["candidate-1", "candidate-2"]

        with pytest.raises(ValueError) as exc_info:
            _resolve_instance_id(mock_manager, "unknown-id")

        error_msg = str(exc_info.value)
        assert "Multiple similar instances found" in error_msg
        assert "candidate-1" in error_msg
        assert "candidate-2" in error_msg

    def test_no_match_raises_value_error(self):
        """Test no matches raises ValueError with no-suggestion message."""
        from daemon.tools.instance import _resolve_instance_id

        mock_manager = self._create_mock_manager()
        # get_instance fails (KeyError), find_near_instance returns empty list
        mock_manager.get_instance.side_effect = KeyError("not found")
        mock_manager.find_near_instance.return_value = []

        with pytest.raises(ValueError) as exc_info:
            _resolve_instance_id(mock_manager, "totally-unknown-id")

        error_msg = str(exc_info.value)
        assert "not found" in error_msg
        assert "no similar instance found" in error_msg

    def test_empty_instance_id_raises_value_error(self):
        """Test that empty string raises ValueError with 'cannot be empty'."""
        from daemon.tools.instance import _resolve_instance_id

        mock_manager = self._create_mock_manager()

        with pytest.raises(ValueError) as exc_info:
            _resolve_instance_id(mock_manager, "")

        assert "cannot be empty" in str(exc_info.value)
        # Should not even try to look up the instance
        mock_manager.get_instance.assert_not_called()
        mock_manager.find_near_instance.assert_not_called()

    def test_none_instance_id_raises_value_error(self):
        """Test that None raises ValueError with 'cannot be empty'."""
        from daemon.tools.instance import _resolve_instance_id

        mock_manager = self._create_mock_manager()

        with pytest.raises(ValueError) as exc_info:
            _resolve_instance_id(mock_manager, None)

        assert "cannot be empty" in str(exc_info.value)
        # Should not even try to look up the instance
        mock_manager.get_instance.assert_not_called()
        mock_manager.find_near_instance.assert_not_called()
