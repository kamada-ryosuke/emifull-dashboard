import io
import unittest

from lib.pl_parser import (ParseResult, SheetParseResult, import_parse_result_to_db,
                           parse_amount, parse_pl_monthly_csv)


class FakeDB:
    def __init__(self):
        self.writes = []

    def list_pl_accounts(self):
        return [{'id': 1, 'name': '売上高 計'}]

    def list_pl_subunits(self):
        return [{'id': 1, 'excel_name': '施設A'}]

    def replace_pl_entries(self, ym, sid, entries):
        self.writes.append((ym, sid, entries))
        return len(entries)


class PLImportTests(unittest.TestCase):
    def parse(self, text, destination='施設A'):
        return parse_pl_monthly_csv(io.BytesIO(text.encode('cp932')), 'source.csv',
            {'施設A': '施設A'}, ['売上高 計', '法人税等'], destination)

    def test_indented_months_total_and_duplicate_tax(self):
        result = self.parse('タイトル\n,,,,2025-04,2025-05,期間累計\n'
            ',売上高,,,100,200,300\n,,,売上高 計,100,200,300\n'
            ',法人税等,,,1,-2,-1\n,,,法人税等,1,-2,-1\n')
        self.assertEqual(result.matched_year_months, ['2025-04', '2025-05'])
        self.assertEqual(result.sheets[1].entries['施設A'], [('売上高 計', 200), ('法人税等', -2)])
        self.assertEqual(result.sheets[0].unknown_account_rows, ['売上高'])

    def test_requires_explicit_destination(self):
        self.assertIsNotNone(self.parse(',,2025-04\n,売上高 計,100', None).error)

    def test_total_mismatch(self):
        self.assertIsNotNone(self.parse(',,2025-04,期間累計\n,売上高 計,100,200').error)

    def test_conflicting_duplicate(self):
        self.assertIsNotNone(self.parse(',,2025-04\n,法人税等,1\n,法人税等,2').error)

    def test_duplicate_month_columns(self):
        self.assertIsNotNone(self.parse(',,2025-04,2025-04\n,売上高 計,1,1').error)

    def test_bad_amount_blocks_all_writes(self):
        result = self.parse(',,2025-04,2025-05\n,売上高 計,100,abc')
        db = FakeDB()
        self.assertTrue(import_parse_result_to_db(result, db)['errors'])
        self.assertEqual(db.writes, [])

    def test_duplicate_scope_blocks_all_writes(self):
        result = self.parse(',,2025-04\n,売上高 計,100')
        result.sheets.append(result.sheets[0])
        db = FakeDB()
        self.assertTrue(import_parse_result_to_db(result, db)['errors'])
        self.assertEqual(db.writes, [])

    def test_unknown_account_blocks_all_writes(self):
        result = self.parse(',,2025-04\n,売上高 計,100\n,新しい科目,25')
        db = FakeDB()
        self.assertTrue(import_parse_result_to_db(result, db)['errors'])
        self.assertEqual(db.writes, [])

    def test_valid_import(self):
        result = self.parse(',,2025-04\n,売上高 計,100')
        db = FakeDB()
        self.assertEqual(import_parse_result_to_db(result, db)['entries'], 1)
        self.assertEqual(db.writes, [('2025-04', 1, [(1, 100)])])

    def test_nonfinite_amount(self):
        for value in [float('nan'), float('inf'), 'NaN', 'Inf']:
            self.assertEqual(parse_amount(value), (0, False))


if __name__ == '__main__':
    unittest.main()
