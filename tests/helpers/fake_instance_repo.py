"""Shared fake for the instance-metadata repository contract.

Used by the usage-limit deferral-path test packs
(``tests/unit/test_usage_limit_schedule.py``,
``tests/message_queue_redesign/test_usage_limit_deferral.py``,
``tests/message_queue_redesign/test_usage_limit_worker_seam.py``) so
the duck contract (``get`` → ``instance_metadata``, ``set_metadata``,
``delete_metadata``, plus the targeted ``get_metadata_value`` /
``delete_metadata_if_present`` variants) is encoded in ONE place.

``fail=True`` flips every method to raise — the soft-fail robustness
tests use it.
"""

from types import SimpleNamespace


class FakeInstanceMetadataRepo:
    """In-memory instance-metadata repository double."""

    def __init__(self, metadata=None, fail=False):
        self._metadata = dict(metadata or {})
        self._fail = fail
        # Call counters for targeted-read/conditional-delete assertions.
        self.get_metadata_value_calls = 0
        self.delete_if_present_calls = 0

    def get(self, instance_id):
        if self._fail:
            raise RuntimeError("db down")
        return SimpleNamespace(instance_metadata=dict(self._metadata))

    def get_metadata_value(self, instance_id, key):
        if self._fail:
            raise RuntimeError("db down")
        self.get_metadata_value_calls += 1
        return self._metadata.get(key)

    def set_metadata(self, instance_id, key, value):
        if self._fail:
            raise RuntimeError("db down")
        self._metadata[key] = value
        return True

    def delete_metadata(self, instance_id, key):
        if self._fail:
            raise RuntimeError("db down")
        self._metadata.pop(key, None)
        return True

    def delete_metadata_if_present(self, instance_id, key):
        if self._fail:
            raise RuntimeError("db down")
        self.delete_if_present_calls += 1
        return self._metadata.pop(key, None) is not None
