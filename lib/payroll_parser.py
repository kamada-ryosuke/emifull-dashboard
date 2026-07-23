"""給与台帳CSVパーサー

対応フォーマット:
  - 「CSV(縦／障害)」形式  (例: 2604給与台帳（EMIFULL）.csv / 2405給与台帳（障がい部門）.csv)
      ・行0: 集計パターン名
      ・行1: 会社名
      ・行2: 集計対象 (例: '令和 8年 4月分 給与' / '令和 7年 第2回分 賞与')
      ・行3: 集計方法
      ・行4: 部署/施設名
      ・行5: 社員番号
      ・行6: 社員名
      ・行7-: 各支給/控除項目（行頭が項目名、各列が職員）
      ・最右や中ほどに「【 計 N名 】」「合計」のサマリ列あり
  - 「支給控除項目一覧表」形式 (例: 2603給与台帳（のじぎく）.csv)
      ・行0: 集計パターン名
      ・行1: 法人名
      ・行2: 集計対象
      ・行3: 集計方法
      ・行4: 社員番号
      ・行5: 社員名
      ・行6-: 各項目
      ・最右に「合計」列

判定:
  - ファイル名から法人を推定: 'EMIFULL' / '障がい部門' → 医療法人社団EMIFULL,
    'のじぎく' → NPO法人EMIFULL（旧: 特定非営利活動法人のじぎく高砂）
    ただし 2023年3月以前の '障がい部門' は医療法人社団奉志会 (転籍前)
  - 賞与判定: 集計対象に '賞与' が含まれるか / ファイル名に '※' があるか
"""
import csv
import hashlib
import re
import unicodedata
from pathlib import Path


# 法人コード
CORP_EMIFULL_MED = 'EMIFULL_MED'   # 医療法人社団EMIFULL
CORP_EMIFULL_NPO = 'EMIFULL_NPO'   # NPO法人EMIFULL（旧: 特定非営利活動法人のじぎく高砂）
CORP_HOUSHIKAI = 'HOUSHIKAI'        # 医療法人社団奉志会（転籍前）

CORP_NAMES = {
    CORP_EMIFULL_MED: '医療法人社団EMIFULL',
    CORP_EMIFULL_NPO: 'NPO法人EMIFULL',
    CORP_HOUSHIKAI: '医療法人社団奉志会',
}


# === 文字列ユーティリティ ===

def _nfkc(s: str) -> str:
    """全角/半角 正規化"""
    if s is None:
        return ''
    return unicodedata.normalize('NFKC', str(s)).strip()


def _is_summary_label(label: str) -> bool:
    """『計 N名』『合計』などの集計列かどうか"""
    if not label:
        return True
    s = _nfkc(label).replace(' ', '')
    return (
        s == '' or s == '合計' or s == '計' or
        '計' in s and ('名' in s or '人' in s) or
        s.startswith('【') or s.endswith('】')
    )


def _to_int(val):
    """数値化（カンマ・スペース除去）。空文字・'-'（値なし）は None。
    給与ソフトの負数表記 '△1,234' '▲1,234' '(1,234)' はマイナスとして扱う。"""
    if val is None:
        return None
    s = str(val).strip().replace(',', '').replace(' ', '').replace('　', '')
    if s == '' or s == '-':
        return None
    negative = False
    if s.startswith(('△', '▲')):
        negative = True
        s = s[1:]
    elif s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1]
    try:
        n = int(float(s))
    except (ValueError, TypeError):
        return None
    return -n if negative else n


def _parse_jp_date(text: str) -> tuple[str, str]:
    """『令和 8年 4月分 給与』→ ('2026-04', '給与')
    『令和 7年 第2回分 賞与』→ (None, '賞与')  (年月のみ抽出可)
    返り値: (year_month or None, pay_type)
    """
    if not text:
        return None, '給与'
    s = _nfkc(text)
    pay_type = '賞与' if '賞与' in s else '給与'

    m = re.search(r'令和\s*(\d+)\s*年\s*(\d+)\s*月', s)
    if m:
        reiwa_year = int(m.group(1))
        month = int(m.group(2))
        # 令和1年=2019年
        year = 2018 + reiwa_year
        return f"{year:04d}-{month:02d}", pay_type
    return None, pay_type


def _parse_yymm(yymm: str) -> str:
    """'2604' → '2026-04', '2305' → '2023-05'"""
    if not yymm or len(yymm) != 4:
        return None
    yy = int(yymm[:2])
    mm = int(yymm[2:])
    year = 2000 + yy
    return f"{year:04d}-{mm:02d}"


# === ファイル名解析 ===

# パターン1: 2604給与台帳（EMIFULL）.csv / 2405給与台帳（障がい部門）.csv
# / 2512給与台帳（EMIFULL）※冬季賞与.csv / 2603給与台帳（のじぎく）R7.4-R8.3.csv
FILENAME_RE_OLD = re.compile(
    r'^(?P<yymm>\d{4})給与台帳[（(](?P<group>[^)）]+)[)）]\s*(?P<rest>.*?)\.(?:csv|xlsx?)$'
)

# パターン2: 【のじぎく高砂】R8.4_勤怠支給控除一覧表.csv
# / 【EMIFULL】R8.4_勤怠支給控除一覧表（夏季賞与）.csv (将来形)
FILENAME_RE_NEW = re.compile(
    r'^【(?P<group>[^】]+)】R(?P<reiwa_y>\d+)[\.\-](?P<month>\d+)'
    r'(?:[_\s\-]+(?P<title>[^（(\.]*))?'
    r'(?:[（(](?P<bonus>[^)）]+)[)）])?\s*\.(?:csv|xlsx?)$'
)


def _parse_reiwa(reiwa_year: int, month: int) -> str:
    """令和N年M月 → 'YYYY-MM'。令和1年=2019年"""
    if reiwa_year <= 0 or month <= 0 or month > 12:
        return None
    return f"{2018 + reiwa_year:04d}-{month:02d}"


def parse_filename(filename: str) -> dict:
    """ファイル名から年月・法人グループ・賞与種別を判定（新旧両形式対応）"""
    name = Path(filename).name

    # === 旧形式: 2604給与台帳（XX）.csv ===
    m = FILENAME_RE_OLD.match(name)
    if m:
        group = _nfkc(m.group('group'))
        rest = m.group('rest') or ''
        year_month = _parse_yymm(m.group('yymm'))

        bonus_round = None
        if '夏季' in rest:
            bonus_round = '夏季'
        elif '冬季' in rest:
            bonus_round = '冬季'
        elif '※' in rest:
            bonus_round = 'その他'
        pay_type = '賞与' if bonus_round else '給与'

        corp_code = guess_corp_from_group(group, year_month)
        is_annual_summary = bool(re.search(r'R\d+\.\d+\s*-\s*R\d+\.\d+', rest))

        return {
            'filename': name,
            'year_month': year_month,
            'corp_code': corp_code,
            'group_label': group,
            'pay_type': pay_type,
            'bonus_round': bonus_round,
            'is_annual_summary': is_annual_summary,
        }

    # === 新形式: 【XX】R8.4_勤怠支給控除一覧表.csv ===
    m = FILENAME_RE_NEW.match(name)
    if m:
        group = _nfkc(m.group('group'))
        reiwa_y = int(m.group('reiwa_y'))
        month = int(m.group('month'))
        year_month = _parse_reiwa(reiwa_y, month)
        bonus_text = _nfkc(m.group('bonus') or '')
        title = _nfkc(m.group('title') or '')

        bonus_round = None
        if '夏季' in bonus_text or '夏季' in title:
            bonus_round = '夏季'
        elif '冬季' in bonus_text or '冬季' in title:
            bonus_round = '冬季'
        elif '賞与' in bonus_text:
            bonus_round = 'その他'
        pay_type = '賞与' if bonus_round else '給与'

        corp_code = guess_corp_from_group(group, year_month)

        return {
            'filename': name,
            'year_month': year_month,
            'corp_code': corp_code,
            'group_label': group,
            'pay_type': pay_type,
            'bonus_round': bonus_round,
            'is_annual_summary': False,
        }

    return None


def detect_payroll_csv(path) -> dict | None:
    """ファイル名に頼らず、CSV内容から給与台帳かどうかを判定する。
    給与台帳と判明した場合は year_month/corp_code/pay_type/bonus_round 等を返す。
    判定不可なら None。

    判定ロジック:
      - 1行目1セルが『集計パターン名』
      - 2行目1セルが『会社名』『法人名』『事業所名』のいずれか
      - 3行目1セルが『集計対象』
      - 3行目2セルから 令和X年Y月分 給与/賞与 を抽出
    """
    path = Path(path)
    try:
        rows = _read_csv_lines(path)
    except Exception:
        return None
    if len(rows) < 4:
        return None

    def cell(r, c):
        return _nfkc(rows[r][c]) if r < len(rows) and c < len(rows[r]) else ''

    label0 = cell(0, 0)
    label1 = cell(1, 0)
    label2 = cell(2, 0)
    if '集計パターン' not in label0:
        return None
    if not any(k in label1 for k in ('会社名', '法人名', '事業所名')):
        return None
    if '集計対象' not in label2:
        return None

    corp_name = cell(1, 1)
    period_text = cell(2, 1)

    # 年度サマリ（『〜』『～』『-』で区切られた期間）はスキップ
    if any(sep in period_text for sep in ('〜', '～', '~')):
        return None
    if re.search(r'\d+\s*月分\s*[-‐－]\s*', period_text):
        return None

    year_month, pay_type = _parse_jp_date(period_text)
    if not year_month:
        return None

    # 法人判定（CSVの会社名から）
    corp_code = None
    if 'のじぎく' in corp_name or 'NPO' in corp_name or '特定非営利活動法人' in corp_name:
        corp_code = CORP_EMIFULL_NPO
    elif '奉志会' in corp_name:
        corp_code = CORP_HOUSHIKAI
    elif 'EMIFULL' in corp_name.upper() or '医療法人社団EMIFULL' in corp_name:
        corp_code = CORP_HOUSHIKAI if year_month < '2023-04' else CORP_EMIFULL_MED

    # 賞与種別: ファイル名→集計対象テキスト→年月から推定
    bonus_round = None
    if pay_type == '賞与':
        fname = path.name
        if '夏季' in fname or '夏季' in period_text:
            bonus_round = '夏季'
        elif '冬季' in fname or '冬季' in period_text:
            bonus_round = '冬季'
        else:
            m = int(year_month[5:7])
            if m in (6, 7):
                bonus_round = '夏季'
            elif m in (11, 12):
                bonus_round = '冬季'
            else:
                bonus_round = 'その他'

    return {
        'filename': path.name,
        'year_month': year_month,
        'corp_code': corp_code,
        'group_label': corp_name,
        'pay_type': pay_type,
        'bonus_round': bonus_round,
        'is_annual_summary': False,
        'detected_from_content': True,
    }


def detect_file(path) -> dict | None:
    """ファイル名 → 中身 の順で判定。両方失敗なら None。"""
    info = parse_filename(Path(path).name)
    if info and info.get('year_month') and info.get('corp_code'):
        return info
    # ファイル名で判定不可 or 法人不明 → 中身で再判定
    detected = detect_payroll_csv(path)
    if detected:
        # ファイル名で取れた情報があれば補完
        if info:
            for k in ('bonus_round',):
                if not detected.get(k) and info.get(k):
                    detected[k] = info[k]
        return detected
    return info  # 中身判定もダメならファイル名情報をそのまま返す（None含む）


def guess_corp_from_group(group_label: str, year_month: str | None) -> str:
    """ファイル名内のグループ名から法人コードを推定"""
    s = _nfkc(group_label)
    if 'のじぎく' in s:
        return CORP_EMIFULL_NPO
    if 'EMIFULL' in s.upper():
        # 2023-04より前なら奉志会(理論上はEMIFULLのファイル名で2023-03以前は無い想定)
        if year_month and year_month < '2023-04':
            return CORP_HOUSHIKAI
        return CORP_EMIFULL_MED
    if '障がい' in s or '障害' in s or '障がい部門' in s:
        if year_month and year_month < '2023-04':
            return CORP_HOUSHIKAI
        return CORP_EMIFULL_MED
    return None


# === CSV読み込み ===

def _read_csv_lines(path: Path):
    """Shift-JIS (CP932) と UTF-8 を順に試す。失敗したら例外。"""
    raw = Path(path).read_bytes()
    # BOM 判定
    if raw.startswith(b'\xef\xbb\xbf'):
        text = raw.decode('utf-8-sig', errors='replace')
    else:
        for enc in ('cp932', 'utf-8', 'shift-jis'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode('cp932', errors='replace')
    # csv モジュールで分割
    return list(csv.reader(text.splitlines()))


def parse_csv(path: str | Path) -> dict:
    """CSVを読み、メタ情報＋職員別レコードのリストを返す。

    返り値:
      {
        'meta': {
            'corp_name': 会社名,
            'period_text': 集計対象テキスト,
            'year_month': 'YYYY-MM' or None,
            'pay_type': '給与' or '賞与',
            'pattern': 'CSV縦' or '支給控除',
        },
        'employees': [
            {
                'emp_code': '410003',
                'name': '窪田 康子',
                'department': 'いなみ障害',
                'items': {'本給': 228500, '残業手当': 1164, ...}
            }, ...
        ],
        'source_filename': str,
        'source_hash': str (sha256 hex),
      }
    """
    path = Path(path)
    rows = _read_csv_lines(path)
    if not rows:
        raise ValueError(f"空のCSVです: {path.name}")

    # 行0,1,2,3 はメタ
    def cell(r, c):
        return rows[r][c] if r < len(rows) and c < len(rows[r]) else ''

    pattern_label = _nfkc(cell(0, 1))
    corp_name = _nfkc(cell(1, 1))
    period_text = _nfkc(cell(2, 1))
    method_text = _nfkc(cell(3, 1))

    year_month, pay_type = _parse_jp_date(period_text)

    # 部署行があるかどうか（'CSV(縦／障害)' パターン or 行4が項目名らしくないか）
    has_dept_row = '縦' in pattern_label or '障害' in pattern_label or 'CSV' in pattern_label.upper()
    # 念のため行4の最初のセルが空白かつ複数列あれば部署行とみなす
    if not has_dept_row:
        # '支給控除項目一覧表' パターンでも行4が部署のことがあるかチェック
        # row4 の cell0 が空 かつ row5 の cell0 が空 → 部署+番号構成
        if cell(4, 0) == '' and cell(5, 0) == '' and cell(6, 0) == '':
            # 万一のため row5 が数字だらけなら部署あり
            sample = cell(5, 1) + cell(5, 2)
            if any(ch.isdigit() for ch in sample):
                has_dept_row = True

    if has_dept_row:
        dept_row_idx = 4
        emp_no_row_idx = 5
        emp_name_row_idx = 6
        data_start = 7
    else:
        dept_row_idx = None
        emp_no_row_idx = 4
        emp_name_row_idx = 5
        data_start = 6

    emp_no_row = rows[emp_no_row_idx] if emp_no_row_idx < len(rows) else []
    emp_name_row = rows[emp_name_row_idx] if emp_name_row_idx < len(rows) else []
    dept_row = rows[dept_row_idx] if dept_row_idx is not None and dept_row_idx < len(rows) else None

    # 職員列を抽出（社員番号がある列のみ。集計列は除外）
    employees = []
    last_dept = ''  # 部署は左隣からの引き継ぎ
    n_cols = max(len(emp_no_row), len(emp_name_row))
    for col in range(1, n_cols):  # col=0 は項目名列
        emp_no = _nfkc(emp_no_row[col]) if col < len(emp_no_row) else ''
        emp_name = _nfkc(emp_name_row[col]) if col < len(emp_name_row) else ''
        if dept_row is not None and col < len(dept_row):
            cur_dept = _nfkc(dept_row[col])
            if cur_dept:
                last_dept = cur_dept

        # 集計列・空列を除外
        if not emp_no and not emp_name:
            continue
        if _is_summary_label(emp_name) or _is_summary_label(emp_no):
            continue
        if not emp_no:
            # 名前だけあり番号なし → 集計と判断
            continue
        if not emp_no.replace('-', '').replace('_', '').strip():
            continue
        # 数字でない社員番号でも有効（のじぎくは 001001, 990008 等）
        employees.append({
            'col': col,
            'emp_code': emp_no,
            'name': emp_name.replace('　', ' ').strip(),
            'department': last_dept if dept_row is not None else '',
            'items': {},
        })

    # データ行を順に読む
    for r_idx in range(data_start, len(rows)):
        row = rows[r_idx]
        if not row:
            continue
        label = _nfkc(row[0]) if len(row) > 0 else ''
        if not label:
            continue
        # 区切り空行を飛ばす
        if all(_nfkc(c) == '' for c in row[1:]):
            continue
        for emp in employees:
            c = emp['col']
            if c < len(row):
                v = row[c]
                # 時刻形式 (例: 160:00) は文字列保持、数値項目は int 化
                if v is None:
                    continue
                vs = str(v).strip()
                if vs == '':
                    continue
                if ':' in vs and vs.replace(':', '').replace('-', '').isdigit():
                    emp['items'][label] = vs
                else:
                    n = _to_int(vs)
                    if n is not None:
                        emp['items'][label] = n
                    elif vs:
                        emp['items'][label] = vs  # 文字列のまま

    # ハッシュ
    h = hashlib.sha256()
    h.update(path.read_bytes())
    file_hash = h.hexdigest()

    # employees の col キーを除く
    for emp in employees:
        emp.pop('col', None)

    return {
        'meta': {
            'corp_name': corp_name,
            'pattern_label': pattern_label,
            'period_text': period_text,
            'method_text': method_text,
            'year_month': year_month,
            'pay_type': pay_type,
            'has_dept_row': dept_row is not None,
        },
        'employees': employees,
        'source_filename': path.name,
        'source_hash': file_hash,
    }


# === 法人コード判定（ファイル+CSV内容を統合） ===

def resolve_corp_code(filename_info: dict, csv_meta: dict | None = None) -> str:
    """ファイル名情報＋CSVメタから最終的な法人コードを決定"""
    code = filename_info.get('corp_code') if filename_info else None
    if csv_meta:
        cn = (csv_meta.get('corp_name') or '').strip()
        if 'のじぎく' in cn or 'NPO法人' in cn or '特定非営利活動法人' in cn:
            return CORP_EMIFULL_NPO
        if '奉志会' in cn:
            return CORP_HOUSHIKAI
        if '医療法人社団EMIFULL' in cn or 'EMIFULL' in cn:
            ym = csv_meta.get('year_month') or filename_info.get('year_month') if filename_info else None
            if ym and ym < '2023-04':
                return CORP_HOUSHIKAI
            return CORP_EMIFULL_MED
    return code


# === 雇用区分の自動推定（ヒューリスティック） ===

def guess_employment_type(emp_code: str, items: dict,
                            corp_code: str | None = None) -> str | None:
    """正社員/パート の暫定推定。確定はユーザ編集で。

    法人別ルール:
      - NPO法人EMIFULL: 基本給 >= 200,000円 → 正社員、それ未満(>0) → パート
      - 医療法人EMIFULL: 社員番号 41xxxx=正社員 / 42xxxx=パート（経験則）
                          + 基本給フォールバック
    """
    code = (emp_code or '').strip()
    # 本給/基本給を統一して取得
    base = items.get('本給')
    if not isinstance(base, int):
        base = items.get('基本給')
    if not isinstance(base, int):
        base = 0

    # === NPO法人: 基本給20万 ルール ===
    if corp_code == 'EMIFULL_NPO':
        if base >= 200_000:
            return '正社員'
        if base > 0:
            return 'パート'
        return None  # 賞与CSVのみ等で基本給ゼロのケースは判定保留

    # === 医療法人: 社員番号 + 基本給フォールバック ===
    if corp_code == 'EMIFULL_MED':
        if re.fullmatch(r'4\d{5}', code):
            if code.startswith('41'):
                return '正社員'
            if code.startswith('42'):
                return 'パート'
        if base >= 200_000:
            return '正社員'
        if 0 < base < 200_000:
            return 'パート'
        return None

    # === 法人不明時の汎用フォールバック ===
    if re.fullmatch(r'4\d{5}', code):
        if code.startswith('41'):
            return '正社員'
        if code.startswith('42'):
            return 'パート'
    if base >= 200_000:
        return '正社員'
    if 0 < base < 200_000:
        return 'パート'
    return None


def classify_by_max_base_salary(max_base: int, threshold: int = 200_000) -> str:
    """過去データの最大基本給から雇用区分を再判定。20万以上=正社員、それ以外=パート。"""
    if max_base is None:
        max_base = 0
    return '正社員' if max_base >= threshold else 'パート'


# === 給与項目の表示順マスタ ===
# 同義語（EMIFULL ↔ のじぎく系）はまとめて1列に正規化する
# - PICK系: 1ファイル内に一方しか出ない項目（『総支給金額』 or 『総支給額』）。最初の非ゼロ値を採用
# - SUM系:  1ファイル内に同時に出る可能性がある項目（『役職手当』『職位手当』）。合算する
ITEM_ALIASES_PICK = {
    '本給': ['本給', '基本給'],
    '総支給': ['総支給金額', '総支給額', '総支給'],
    '差引支給': ['差引支給額', '差引支給'],
    '課税支給': ['課税支給額', '課税対象額', '課税支給'],
    '控除合計': ['控除合計額', '控除合計', '社保合計額'],
    '銀行振込': ['銀行振込額', '銀行１振込額'],
    '健康保険': ['健康保険料', '健康保険'],
    '介護保険': ['介護保険料', '介護保険'],
    '厚生年金': ['厚生年金保険', '厚生年金保険料', '厚生年金'],
    '雇用保険': ['雇用保険料', '雇用保険'],
    '医療費補助': ['医療費補助(課税)', '医療費補助'],
}
ITEM_ALIASES_SUM = {
    '役職・職位手当': ['役職・職位手当', '役職手当', '職位手当'],
    '処遇改善手当': ['処遇改善手当', '処遇改善金'],
    'インセンティブ': ['インセンティブ', 'その他手当'],
}
# 旧コード互換（廃止予定）
ITEM_ALIASES = {**ITEM_ALIASES_PICK, **ITEM_ALIASES_SUM}

# 項目グループ定義（表示順に並べる）
ITEM_GROUPS = [
    ('集計', ['総支給', '差引支給', '課税支給', '銀行振込', '現金支給額']),
    ('基本給', ['本給']),
    ('支給手当', [
        '役職・職位手当', '資格手当', '住宅手当', '保育手当',
        '処遇改善手当', '業務手当', '調整手当', '夜勤給',
        '特勤手当', '医療費補助(課税)', 'インセンティブ',
        '年末年始手当', 'その他手当', 'ライフプラン手当', '休業補償',
        '寸志手当', '外勤手当', 'サービス利用費', '前月分',
    ]),
    ('通勤・残業', [
        '通勤手当', '課税通勤手当', '定額時間外手当', '残業手当', '減額金',
    ]),
    ('賞与', ['賞与']),
    ('社会保険料', [
        '健康保険', '介護保険', '厚生年金', '雇用保険',
    ]),
    ('税金', [
        '所得税', '過不足税額', '住民税', '不足税額',
    ]),
    ('その他控除', [
        '控除合計', '既払い定期代', '前払金控除', 'その他控除',
        '家賃控除', '清算',
    ]),
    ('勤怠', [
        '出勤日数', '欠勤日数', '有休日数', '特休日数', '出勤時間',
        '時間有休', '有休残', '法定内残業時間', '普通残業時間',
        '深夜残業時間', '休出残業時間', '夜勤時間', '遅早時間',
    ]),
]

# 重要列（「パッと見」の順序）
KEY_COLUMNS_ORDER = ['総支給', '差引支給', '本給', '課税支給', '控除合計']


def _canonical_item_name(label: str) -> str:
    """項目名のエイリアスを正規化"""
    s = (label or '').strip()
    for canonical, aliases in ITEM_ALIASES.items():
        if s in aliases:
            return canonical
    return s


def normalize_items(items: dict) -> dict:
    """items_json内のキーを正規化名に統一する。
    - SUM系（役職・職位/処遇改善/インセンティブ等）は合算
    - PICK系（総支給/差引支給等）は最初の非ゼロ値を採用"""
    out = {}
    consumed = set()  # 元キーが処理済みか

    # SUM系: 合算
    for canonical, aliases in ITEM_ALIASES_SUM.items():
        s = 0
        any_value = False
        for a in aliases:
            v = items.get(a)
            consumed.add(a)
            if v is None or v == '':
                continue
            if isinstance(v, (int, float)):
                s += int(v)
                any_value = True
        if any_value:
            out[canonical] = s

    # PICK系: 最初の非ゼロ
    for canonical, aliases in ITEM_ALIASES_PICK.items():
        for a in aliases:
            consumed.add(a)
            v = items.get(a)
            if v is None or v == '':
                continue
            if canonical not in out or out[canonical] in (None, 0, ''):
                out[canonical] = v
                if isinstance(v, (int, float)) and v != 0:
                    break  # 非ゼロが取れたら終了
                if isinstance(v, str) and v.strip():
                    break

    # それ以外: そのまま
    for k, v in items.items():
        if k in consumed:
            continue
        out[k] = v
    return out


def ordered_items(items: dict, include_unknown: bool = True) -> list[tuple[str, object]]:
    """正規化済 items を ITEM_GROUPS 順に並べたリストで返す"""
    items = normalize_items(items)
    seen = set()
    result = []
    for _, names in ITEM_GROUPS:
        for n in names:
            if n in items:
                result.append((n, items[n]))
                seen.add(n)
    if include_unknown:
        for k, v in items.items():
            if k not in seen:
                result.append((k, v))
    return result


def grouped_items(items: dict) -> list[tuple[str, list[tuple[str, object]]]]:
    """『支給手当』『社会保険料』などのグループ単位で分類して返す"""
    items = normalize_items(items)
    seen = set()
    groups = []
    for group_name, names in ITEM_GROUPS:
        bucket = []
        for n in names:
            if n in items:
                bucket.append((n, items[n]))
                seen.add(n)
        if bucket:
            groups.append((group_name, bucket))
    others = [(k, v) for k, v in items.items() if k not in seen]
    if others:
        groups.append(('その他', others))
    return groups
