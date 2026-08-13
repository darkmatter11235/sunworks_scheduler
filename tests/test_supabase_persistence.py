import unittest
from unittest import mock

import supabase_persistence as sp


class BootstrapStorageTests(unittest.TestCase):
    def test_returns_unavailable_when_remote_read_fails_and_local_is_empty(self) -> None:
        with mock.patch.object(sp, "is_enabled", return_value=True), \
             mock.patch.object(sp, "pull_into_sqlite", return_value=sp.PULL_STATUS_UNAVAILABLE), \
             mock.patch.object(sp.db, "has_any_data", return_value=False):
            self.assertEqual(sp.bootstrap_storage(), "unavailable")

    def test_pushes_local_state_only_when_remote_is_empty(self) -> None:
        with mock.patch.object(sp, "is_enabled", return_value=True), \
             mock.patch.object(sp, "pull_into_sqlite", return_value=sp.PULL_STATUS_EMPTY), \
             mock.patch.object(sp.db, "has_any_data", return_value=True), \
             mock.patch.object(sp, "push_from_sqlite", return_value=True):
            self.assertEqual(sp.bootstrap_storage(), "pushed")

    def test_returns_pulled_when_remote_state_is_loaded(self) -> None:
        with mock.patch.object(sp, "is_enabled", return_value=True), \
             mock.patch.object(sp, "pull_into_sqlite", return_value=sp.PULL_STATUS_PULLED):
            self.assertEqual(sp.bootstrap_storage(), "pulled")


if __name__ == "__main__":
    unittest.main()