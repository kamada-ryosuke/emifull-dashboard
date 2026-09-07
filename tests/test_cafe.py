import ast,pathlib,sys,tempfile
from unittest.mock import patch
from collections import defaultdict
from types import SimpleNamespace
from lib import db
import unittest

class CafeTests(unittest.TestCase):
 def test_migration_and_forecast_separation(self):
  with tempfile.TemporaryDirectory() as tmp, patch.object(db,'_use_cloud_db',return_value=False),patch.object(db,'DB_PATH',pathlib.Path(tmp)/'test.db'):
   db.init_pl_schema()
   before={s['excel_name']:s['id'] for s in db.list_pl_subunits()}
   with db.get_conn() as conn:conn.execute("DELETE FROM pl_subunits WHERE excel_name='高砂カフェ'")
   db.ensure_pl_import_masters();db.ensure_pl_import_masters()
   subs=db.list_pl_subunits();cafes=[s for s in subs if s['excel_name']=='高砂カフェ']
   assert len(cafes)==1 and cafes[0]['group_code']=='011'
   assert all(s['id']==before[s['excel_name']] for s in subs if s['excel_name']!='高砂カフェ')
   tree=ast.parse((pathlib.Path(__file__).resolve().parents[1]/'pages/9_売上収支予測表.py').read_text(encoding='utf-8-sig'))
   names={'_build_forecast_facilities','_facility_row_from_subunits','_facility_row_from_subunit','_facility_display_name','_normalize_name'}
   fns=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
   for n in fns:n.decorator_list=[]
   import re,unicodedata
   env={'db':SimpleNamespace(list_pl_groups=lambda:[g for g in db.list_pl_groups() if g['code']=='011'],list_pl_subunits=lambda:subs),'defaultdict':defaultdict,'SPLIT_GROUP_CODES':set(),'re':re,'unicodedata':unicodedata}
   exec(compile(ast.Module(body=fns,type_ignores=[]),'<forecast>','exec'),env)
   rows=env['_build_forecast_facilities']()
   assert len(rows)==1 and rows[0]['subunit_ids']==[before['のじぎく高砂']]
  
