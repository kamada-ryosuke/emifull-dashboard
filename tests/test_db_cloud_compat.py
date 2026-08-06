import threading
import time
import unittest

from lib import auth, db


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


class _FailOnceConnection(_RecordingConnection):
    def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        raise ValueError(
            "Hrana: cursor error: error reading a body from connection: "
            "unexpected EOF during chunk size line"
        )

    def close(self):
        pass


class _ContextConnection:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _ReplicaConnection(_ContextConnection):
    def __init__(self):
        self.sync_calls = 0

    def sync(self):
        self.sync_calls += 1


class _FakeLibsql:
    def __init__(self):
        self.connection = _ReplicaConnection()
        self.connect_kwargs = None

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        return self.connection


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

    def test_transient_read_error_reconnects_and_retries(self):
        first = _FailOnceConnection()
        second = _RecordingConnection()
        reconnect_calls = []

        def reconnect():
            reconnect_calls.append(True)
            return second

        conn = db._CloudConnection(first, reconnect=reconnect)
        conn.execute("PRAGMA table_info(users)")

        self.assertEqual(len(reconnect_calls), 1)
        self.assertEqual(second.execute_calls, [("PRAGMA table_info(users)", ())])

    def test_transient_write_error_is_not_retried(self):
        first = _FailOnceConnection()
        reconnect_calls = []
        conn = db._CloudConnection(
            first,
            reconnect=lambda: reconnect_calls.append(True),
        )

        with self.assertRaises(ValueError):
            conn.execute("INSERT INTO users (email) VALUES (?)", ("a@example.com",))

        self.assertEqual(reconnect_calls, [])

    def test_cloud_connection_uses_and_syncs_embedded_replica(self):
        fake_libsql = _FakeLibsql()
        database_url = "libsql://uriage-example.turso.io"

        conn = db._replace_cloud_connection(fake_libsql, database_url, "token")

        self.assertIs(conn, fake_libsql.connection)
        self.assertEqual(fake_libsql.connection.sync_calls, 1)
        self.assertEqual(
            fake_libsql.connect_kwargs,
            {
                "database": str(db.LOCAL_REPLICA_PATH),
                "sync_url": database_url,
                "auth_token": "token",
            },
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

    def test_login_database_errors_are_safely_classified(self):
        auth_message = auth._login_database_error_message(
            ValueError("server returned HTTP status 401")
        )
        network_message = auth._login_database_error_message(
            ValueError("connection timed out")
        )

        self.assertIn("認証設定", auth_message)
        self.assertIn("通信に失敗", network_message)


if __name__ == "__main__":
    unittest.main()
