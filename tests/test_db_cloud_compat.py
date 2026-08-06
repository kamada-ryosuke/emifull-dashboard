import threading
import time
import unittest

from lib import db


class _FakeCursor:
    description = ()

    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self):
        self.execute_calls = []

    def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return _FakeCursor()


class _ContextConnection:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class CloudConnectionCompatibilityTests(unittest.TestCase):
    def test_execute_omits_empty_parameters(self):
        raw = _RecordingConnection()
        conn = db._CloudConnection(raw)

        conn.execute("PRAGMA table_info(users)")
        conn.execute("SELECT * FROM users WHERE email = ?", ("a@example.com",))

        self.assertEqual(raw.execute_calls[0], ("PRAGMA table_info(users)", ()))
        self.assertEqual(
            raw.execute_calls[1],
            ("SELECT * FROM users WHERE email = ?", (("a@example.com",),)),
        )

    def test_cloud_contexts_are_serialized(self):
        original_use_cloud = db._use_cloud_db
        original_connect_cloud = db._connect_cloud
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        start = threading.Barrier(4)

        def worker():
            nonlocal active, max_active
            start.wait()
            with db.get_conn():
                with state_lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.02)
                with state_lock:
                    active -= 1

        db._use_cloud_db = lambda: True
        db._connect_cloud = lambda: _ContextConnection()
        try:
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(max_active, 1)
        finally:
            db._use_cloud_db = original_use_cloud
            db._connect_cloud = original_connect_cloud


if __name__ == "__main__":
    unittest.main()
