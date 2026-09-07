import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from lib import db


class PLBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(db, '_use_cloud_db', return_value=False))
        self.enterContext(patch.object(db, 'DB_PATH', pathlib.Path(self.temp.name) / 'test.db'))
        db.init_pl_schema()

    def test_additive_migration_preserves_existing_settings(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM pl_accounts WHERE name = ?", ('地代家賃',))
            conn.execute("UPDATE pl_accounts SET display_order = 999 WHERE name = ?", ('管理者給',))
        db.ensure_pl_import_masters()
        db.ensure_pl_import_masters()
        accounts = {a['name']: a for a in db.list_pl_accounts()}
        self.assertIn('地代家賃', accounts)
        self.assertEqual(accounts['管理者給']['display_order'], 999)

    def test_replacement_saves_exact_previous_values(self):
        sid = db.list_pl_subunits()[0]['id']
        aid = db.list_pl_accounts()[0]['id']
        db.replace_pl_entries('2025-06', sid, [(aid, -125)])
        db.replace_pl_entries('2025-06', sid, [(aid, 300)])
        with db.get_conn() as conn:
            backup = conn.execute('SELECT * FROM pl_entry_backups').fetchall()
        self.assertEqual(len(backup), 1)
        self.assertEqual(json.loads(backup[0]['entries_json']), [{'account_id': aid, 'amount': -125}])
        self.assertEqual(db.fetch_pl_entries()[0]['amount'], 300)

    def test_failed_replacement_preserves_old_values(self):
        sid = db.list_pl_subunits()[0]['id']
        aid = db.list_pl_accounts()[0]['id']
        db.replace_pl_entries('2025-06', sid, [(aid, 123)])
        with self.assertRaises(ValueError):
            db.replace_pl_entries('2025-06', sid, [(aid, 'invalid')])
        self.assertEqual(db.fetch_pl_entries()[0]['amount'], 123)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pl_entry_backups').fetchone()[0], 0)
