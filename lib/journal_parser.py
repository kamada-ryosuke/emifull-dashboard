"""仕訳帳CSV(freee/弥生形式)のパーサ。

主要列:
- 取引日
- 借方勘定科目 / 借方金額 / 借方部門 / 借方取引先名 / 借方備考
- 貸方勘定科目 / 貸方金額 / 貸方部門 / 貸方取引先名 / 貸方備考
- 仕訳ID / 仕訳行番号 / レコード番号 / 取引内容 / 仕訳番号

部門名 prefix(例 '07　高砂カフェ' / '04 だがし屋キューブ') を除去して、
DBの pl_subunits.excel_name または display_name と照合し subunit_id を解決する。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re

# 部門名 prefix を除去する正規表現。以下に対応:
# '07　高砂カフェ' (digit+space)
# '04 だがし屋キューブ' (digit+space)
# '4.1.SORATOいなみ' (digit.digit.)
# '99.本部' (digits.)
# '8.10.ケアマネ（ただおか）' (digit.digit_double.)
DEPT_PREFIX_RE = re.compile(r'^\s*\d+(?:\.\d+)*[\.\s　_\-]*')


def _strip_dept_prefix(name: str) -> str:
    if not name:
        return ''
    return DEPT_PREFIX_RE.sub('', name).strip()


def _normalize_dept(name: str) -> str:
    """部門名比較用キー: prefix除去 + 空白(全/半角)除去。
    例: '9.1.SORATO UMIEきたはま' → 'SORATOUMIEきたはま'"""
    if not name:
        return ''
    s = _strip_dept_prefix(name)
    return s.replace(' ', '').replace('　', '')


def _safe_int(s):
    try:
        return int(str(s).strip().replace(',', ''))
    except (ValueError, TypeError, AttributeError):
        return 0


def parse_journal_csv(file_or_path, subunit_lookup: dict[str, int],
                     encoding: str = 'cp932') -> dict:
    """仕訳帳CSVをパース。

    subunit_lookup: dict[str, int]   部門名 (prefix除去後) → subunit_id のマップ。
        DB の pl_subunits から `{s['excel_name']: s['id'], s['display_name']: s['id']}` 形式で構築。

    戻り値:
        {
            'rows': [...],          # DB INSERT 用 dict のリスト
            'file_hash': str,
            'errors': [str],
            'unknown_departments': set[str],   # マスタに無い部門名
            'matched_subunits': set[int],
        }
    """
    # ファイル読み込み
    if hasattr(file_or_path, 'read'):
        data = file_or_path.read()
        if isinstance(data, str):
            data = data.encode(encoding, errors='replace')
    else:
        with open(file_or_path, 'rb') as f:
            data = f.read()

    file_hash = hashlib.sha256(data).hexdigest()
    text = data.decode(encoding, errors='replace')

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {'rows': [], 'file_hash': file_hash,
                'errors': ['CSV内容が空です'],
                'unknown_departments': set(), 'matched_subunits': set()}

    header = rows[0]
    col = {h.strip(): i for i, h in enumerate(header)}

    required = ['取引日', '借方勘定科目', '借方金額', '貸方勘定科目', '貸方金額']
    for c in required:
        if c not in col:
            return {'rows': [], 'file_hash': file_hash,
                    'errors': [f'必須列が見つかりません: {c}'],
                    'unknown_departments': set(), 'matched_subunits': set()}

    def _get(r, key):
        idx = col.get(key)
        if idx is None or idx >= len(r):
            return ''
        return (r[idx] or '').strip()

    def _resolve_subunit(name: str) -> int | None:
        if not name:
            return None
        # 元のまま / prefix除去後 / 空白除去後 の3パターンで照合
        for cand in [name.strip(), _strip_dept_prefix(name), _normalize_dept(name)]:
            if cand in subunit_lookup:
                return subunit_lookup[cand]
        return None

    out_rows: list[dict] = []
    unknown_depts: set[str] = set()
    matched_subs: set[int] = set()
    errors: list[str] = []

    for line_no, r in enumerate(rows[1:], start=2):
        try:
            date = _get(r, '取引日')
            if not date:
                continue
            debit_dept_raw = _get(r, '借方部門')
            credit_dept_raw = _get(r, '貸方部門')
            debit_dept_clean = _strip_dept_prefix(debit_dept_raw)
            credit_dept_clean = _strip_dept_prefix(credit_dept_raw)
            debit_sub = _resolve_subunit(debit_dept_raw)
            credit_sub = _resolve_subunit(credit_dept_raw)
            if debit_sub is not None:
                matched_subs.add(debit_sub)
            elif debit_dept_raw:
                unknown_depts.add(debit_dept_raw)
            if credit_sub is not None:
                matched_subs.add(credit_sub)
            elif credit_dept_raw:
                unknown_depts.add(credit_dept_raw)

            out_rows.append({
                'transaction_date': date,
                'debit_account': _get(r, '借方勘定科目'),
                'debit_amount': _safe_int(_get(r, '借方金額')),
                'debit_department': debit_dept_raw,
                'debit_dept_clean': debit_dept_clean,
                'debit_subunit_id': debit_sub,
                'debit_vendor': _get(r, '借方取引先名'),
                'debit_memo': _get(r, '借方備考'),
                'debit_item': _get(r, '借方品目'),
                'credit_account': _get(r, '貸方勘定科目'),
                'credit_amount': _safe_int(_get(r, '貸方金額')),
                'credit_department': credit_dept_raw,
                'credit_dept_clean': credit_dept_clean,
                'credit_subunit_id': credit_sub,
                'credit_vendor': _get(r, '貸方取引先名'),
                'credit_memo': _get(r, '貸方備考'),
                'credit_item': _get(r, '貸方品目'),
                'journal_id': _get(r, '仕訳ID'),
                'journal_no': _get(r, '仕訳番号'),
                'record_no': _get(r, 'レコード番号'),
                'transaction_content': _get(r, '取引内容'),
            })
        except Exception as e:
            errors.append(f'line {line_no}: {e}')

    return {
        'rows': out_rows,
        'file_hash': file_hash,
        'errors': errors,
        'unknown_departments': unknown_depts,
        'matched_subunits': matched_subs,
    }


def build_subunit_lookup_for_journal(subunits) -> dict[str, int]:
    """DBサブ部門レコードから 部門名 → subunit_id のルックアップを作成。
    excel_name / display_name および それらの 空白除去 版 を全て登録する。"""
    lookup: dict[str, int] = {}
    for s in subunits:
        names = {s['excel_name']}
        if s.get('display_name'):
            names.add(s['display_name'])
        # 各名前について 元 + 空白除去 を登録
        for n in names:
            lookup[n] = s['id']
            stripped = n.replace(' ', '').replace('　', '')
            if stripped != n:
                lookup[stripped] = s['id']
    return lookup
