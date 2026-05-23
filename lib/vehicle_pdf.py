"""車両管理ユーティリティ

責務:
  - PDFファイル名のパース（日付・法人・施設名・車種・登録番号・保険/置き去り装置）
  - 和暦↔西暦変換
  - 車検満了日アラート判定（残日数 / ステータス区分）
  - PDFからの可能な範囲のテキスト抽出（PyMuPDF。スキャンPDFはOCR不可のため空文字も許容）

法人:
  EMI / 医療  → 医療法人社団EMIFULL
  のじ / 特非 → NPO法人EMIFULL  （※2026年1月に「のじぎく」から改称）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# ============================================================
# 法人・施設マスタ
# ============================================================

CORP_IRYOU = '医療法人社団EMIFULL'
CORP_NPO   = 'NPO法人EMIFULL'

# 施設名揺らぎ → 正規化済み表示名 への対応表
#
# 値は損益ダッシュボードで使われている subunit (子) の display_name に揃える。
# これにより、車両管理 ↔ 損益ダッシュボード で施設の粒度が一致する。
#
# 損益ダッシュボードの subunit display_name (canonical) :
#   SORATOいなみ / UMIEいなみ / BLOOMいなみ
#   SORATOいなみ第二教室 / UMIEいなみ第二教室
#   SORATOてんり / UMIEてんり / BLOOMてんり
#   ジョブカレッジかこがわ / カラダキッズかこがわ / カラダキッズてんり
#   シェアホーム天理1.2 / シェアホーム天理3 / シェアホーム加古川
#   のじぎく高砂 / のじぎく稲美 / のじぎく加古川
FACILITY_ALIASES: dict[str, str] = {
    # 医療法人 (EMI) — UMIE/SORATO/BLOOM のブランド単位で振り分け
    'UMIEいなみ':           'UMIEいなみ',
    'UMIEいなみ第二教室':   'UMIEいなみ第二教室',
    'UMIEてんり':           'UMIEてんり',
    'SORATOいなみ':         'SORATOいなみ',
    'SORATOいなみ第二教室': 'SORATOいなみ第二教室',
    'SORATOてんり':         'SORATOてんり',
    'BLOOMいなみ':          'BLOOMいなみ',
    'BLOOMかこがわ':        'BLOOMかこがわ',  # ※損益ダッシュボードに無い場合は要マスタ追加
    'BLOOMてんり':          'BLOOMてんり',
    'ジョブカレッジかこがわ': 'ジョブカレッジかこがわ',
    'カラダキッズかこがわ':   'カラダキッズかこがわ',
    'カラダキッズてんり':     'カラダキッズてんり',
    'Hinodeシェアホーム加古川': 'シェアホーム加古川',
    'Hinodeシェアホーム天理':   'シェアホーム天理1.2',
    'シェアホーム加古川': 'シェアホーム加古川',
    'シェアホーム天理':   'シェアホーム天理1.2',
    '障がい事業部（森田）': '本部',
    '障がい事業部':         '本部',
    '本部':                 '本部',
    # NPO (のじ)
    'のじぎく稲美':   'のじぎく稲美',
    'のじぎく高砂':   'のじぎく高砂',
    'のじぎく加古川': 'のじぎく加古川',
}

INSURANCE_ENROLLED = '加入済'
INSURANCE_NOT      = '未加入'

DEVICE_INSTALLED   = '設置済'
DEVICE_NA          = '対象外'
DEVICE_NOT_SET     = '未設置'

TAX_EXEMPT_DONE    = '済'
TAX_EXEMPT_NOT     = '未'
TAX_EXEMPT_NA      = '対象外'


# ============================================================
# 和暦変換
# ============================================================

ERA_OFFSETS = {
    '令和': 2018,   # 令和1年(2019) = 1年
    '平成': 1988,   # 平成1年(1989) = 1年
    '昭和': 1925,
    '大正': 1911,
}


def wareki_to_year(era: str, year: int) -> int:
    """和暦元号 + 年 → 西暦年。"""
    base = ERA_OFFSETS.get(era)
    if base is None:
        raise ValueError(f"unknown era: {era}")
    return base + year


def parse_wareki_date(text: str) -> date | None:
    """文字列「令和7年10月14日」「平成24年8月」等を date に変換。

    日が無い場合は 1日扱い。失敗時は None。
    """
    if not text:
        return None
    s = text.replace(' ', '').replace('　', '')
    m = re.search(r'(令和|平成|昭和|大正)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?\s*日?', s)
    if not m:
        return None
    era, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    try:
        return date(wareki_to_year(era, y), mo, int(d) if d else 1)
    except (ValueError, TypeError):
        return None


def to_wareki_str(d: date | None) -> str:
    """date → '令和7年10月14日' 表記へ。"""
    if d is None:
        return ''
    y = d.year
    if y >= 2019:
        return f'令和{y - 2018}年{d.month}月{d.day}日'
    if y >= 1989:
        return f'平成{y - 1988}年{d.month}月{d.day}日'
    return d.isoformat()


# ============================================================
# ファイル名パーサー
# ============================================================

@dataclass
class FilenameParts:
    raw: str
    contract_date: date | None  # ファイル名先頭の日付（契約/購入/登録日）
    corporation: str | None
    facility_raw: str | None
    facility_normalized: str | None
    car_name: str | None        # シエンタ等
    registration_number: str | None  # 奈良 501 め 4000
    insurance_status: str
    child_safety_device: str
    tax_exemption_status: str = TAX_EXEMPT_NOT


def _normalize_registration(s: str) -> str:
    """『奈良 501 め 40-00』『奈良501め4000』『奈良501み694』等の揺らぎを統一。

    最終形: '<地域名> <分類番号> <かな> <一連番号(-無し連結4桁推奨>)>'
    """
    s = s.strip()
    s = s.replace('ｰ', '-').replace('−', '-').replace('―', '-')
    s = re.sub(r'[\s　]+', ' ', s).strip()
    return s


def _parse_filename_date(s: str) -> date | None:
    """『R9.11.1』『R8.5.6』『R10.2.21』等を date に。"""
    m = re.match(r'R(\d{1,2})\.(\d{1,2})\.(\d{1,2})', s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(wareki_to_year('令和', y), mo, d)
    except ValueError:
        return None


def parse_filename(name: str) -> FilenameParts:
    """車検証PDFのファイル名を構造化。

    例:
      ※R9.11.1　EMI）【UMIEてんり】シエンタ 奈良 501 め 40-00（置き去り付）※免税済_保険済.pdf
      ※R8.7.25　のじ）【のじぎく高砂】ステップワゴン 姫路 501 ま 3-14_保険済（ゆきのさん）.pdf
    """
    raw = name
    base = Path(name).stem

    # 1) 先頭の日付
    m_date = re.search(r'R\d{1,2}\.\d{1,2}\.\d{1,2}', base)
    contract_date = _parse_filename_date(m_date.group(0)) if m_date else None

    # 2) 法人プレフィックス
    corp = None
    if 'のじ）' in base or '特非' in base:
        corp = CORP_NPO
    elif 'EMI）' in base or '医療' in base:
        corp = CORP_IRYOU

    # 3) 施設名 【…】
    facility_raw = None
    facility_norm = None
    m_fac = re.search(r'【([^】]+)】', base)
    if m_fac:
        facility_raw = m_fac.group(1).strip()
        facility_norm = FACILITY_ALIASES.get(facility_raw, facility_raw)

    # 4) 施設名以降から車種＋登録番号を切り出す
    car_name = None
    reg_num = None
    after = base[m_fac.end():] if m_fac else base

    m_reg = re.search(
        r'(奈良|姫路|神戸|大阪|和泉|京都|滋賀|兵庫|なにわ|堺|和泉|大阪|奈良|和歌山)'
        r'\s*(\d{2,3}[A-Za-z]?)\s*([぀-ゟ])\s*(\d{1,4}[\s\-]?\d{1,4})',
        after,
    )
    if m_reg:
        chiiki, bunrui, kana, ichiren = m_reg.groups()
        ichiren = ichiren.replace('-', '').replace(' ', '').replace('　', '')
        if len(ichiren) <= 4:
            ichiren_disp = ichiren.zfill(4)
        else:
            ichiren_disp = ichiren
        # 表示用は 「X-Y」形式（4桁→XX-XX）
        if len(ichiren_disp) == 4:
            ichiren_disp = f'{ichiren_disp[:2]}-{ichiren_disp[2:]}'
        reg_num = f'{chiiki} {bunrui} {kana} {ichiren_disp}'
        car_name = after[:m_reg.start()].strip()
        # 末尾の余計なスペース・記号除去
        car_name = re.sub(r'[\s　]+$', '', car_name)
        if not car_name:
            car_name = None

    # 5) 保険・装置タグ
    ins_status = INSURANCE_ENROLLED if '保険済' in base else INSURANCE_NOT
    if '置き去り付' in base:
        device = DEVICE_INSTALLED
    else:
        device = DEVICE_NOT_SET  # 後続でPDFの内容や手動修正で対象外/未設置を切替

    tax_status = TAX_EXEMPT_DONE if '免税済' in base else TAX_EXEMPT_NOT

    return FilenameParts(
        raw=raw,
        contract_date=contract_date,
        corporation=corp,
        facility_raw=facility_raw,
        facility_normalized=facility_norm,
        car_name=car_name,
        registration_number=reg_num,
        insurance_status=ins_status,
        child_safety_device=device,
        tax_exemption_status=tax_status,
    )


# ============================================================
# PDF テキスト抽出（PyMuPDF）
# ============================================================

def extract_pdf_text(pdf_path: str | Path) -> str:
    """PyMuPDFでPDFからテキスト抽出。スキャン画像のPDFは空文字。"""
    try:
        import fitz  # type: ignore
    except ImportError:
        return ''
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return ''
    parts = []
    try:
        for page in doc:
            try:
                parts.append(page.get_text('text'))
            except Exception:
                continue
    finally:
        doc.close()
    return '\n'.join(parts)


@dataclass
class InspectionFields:
    """車検証PDFから抽出したい主要フィールド。"""
    registration_number: str | None = None
    chassis_number: str | None = None
    inspection_date: date | None = None       # 登録/交付年月日
    first_registration_ym: str | None = None  # 'YYYY-MM'
    expiry_date: date | None = None
    maker: str | None = None
    model_code: str | None = None
    body_shape: str | None = None
    seating_capacity: int | None = None
    mileage_km: int | None = None
    mileage_recorded_date: date | None = None


def parse_inspection_text(text: str) -> InspectionFields:
    """自動車検査証記録事項の本文テキストから主要フィールドを抽出。

    スキャンPDFで text='' の場合は全フィールドが None で返る。
    """
    f = InspectionFields()
    if not text:
        return f

    # 自動車登録番号
    m = re.search(
        r'(奈良|姫路|神戸|大阪|和泉|京都|滋賀|兵庫|なにわ|堺|和歌山)\s*'
        r'(\d{2,3}[A-Za-z]?)\s*([぀-ゟ])\s*(\d{1,4}[\s\-]?\d{1,4})',
        text,
    )
    if m:
        chiiki, bunrui, kana, ichiren = m.groups()
        ichiren = ichiren.replace('-', '').replace(' ', '')
        if len(ichiren) <= 4:
            ichiren = ichiren.zfill(4)
            ichiren_disp = f'{ichiren[:2]}-{ichiren[2:]}'
        else:
            ichiren_disp = ichiren
        f.registration_number = f'{chiiki} {bunrui} {kana} {ichiren_disp}'

    # 車台番号
    m = re.search(r'車台番号\s*[:：]?\s*([A-Z0-9\-]{8,})', text)
    if m:
        f.chassis_number = m.group(1)

    # 初度登録年月
    m = re.search(r'初度登録年月\s*(令和|平成|昭和)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月', text)
    if m:
        era, y, mo = m.group(1), int(m.group(2)), int(m.group(3))
        f.first_registration_ym = f'{wareki_to_year(era, y):04d}-{mo:02d}'

    # 有効期間の満了する日
    m = re.search(r'有効期間の満了する日\s*(令和|平成)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if m:
        f.expiry_date = parse_wareki_date(m.group(0))

    # 登録年月日/交付年月日
    m = re.search(r'登録年月日.{0,8}交付年月日\s*(令和|平成)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if m:
        f.inspection_date = parse_wareki_date(m.group(0))

    # 車名（メーカー）
    m = re.search(r'車名\s*([^\s　\[\d]+)', text)
    if m:
        f.maker = m.group(1).strip()

    # 型式
    m = re.search(r'型式\s*([A-Z0-9\-・]{3,})', text)
    if m:
        f.model_code = m.group(1)

    # 車体の形状
    m = re.search(r'車体の形状\s*([^\s\[\]0-9]+)', text)
    if m:
        f.body_shape = m.group(1)

    # 乗車定員
    m = re.search(r'乗車定員\s*(\d{1,2})\s*人', text)
    if m:
        f.seating_capacity = int(m.group(1))

    # 走行距離
    m = re.search(r'走行距離計表示値[^\d]{0,4}([\d,]+)\s*km(?:[（(]\s*(令和|平成)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日)?', text)
    if m:
        f.mileage_km = int(m.group(1).replace(',', ''))
        if m.group(2):
            f.mileage_recorded_date = parse_wareki_date(
                f'{m.group(2)}{m.group(3)}年{m.group(4)}月{m.group(5)}日'
            )

    return f


# ============================================================
# アラート判定
# ============================================================

ALERT_DAYS = 60   # 車検満了の何日前から警告するか（≒ 2か月）


@dataclass
class ExpiryStatus:
    """車検満了アラート分類。"""
    label: str   # '正常' / '要更新(2か月以内)' / '車検切れ'
    color: str   # CSS色
    days_left: int | None  # マイナスは超過日数


def classify_expiry(expiry: date | str | None, today: date | None = None) -> ExpiryStatus:
    if expiry is None:
        return ExpiryStatus('未登録', '#94a3b8', None)
    if isinstance(expiry, str):
        try:
            expiry = date.fromisoformat(expiry)
        except ValueError:
            return ExpiryStatus('未登録', '#94a3b8', None)
    today = today or date.today()
    days_left = (expiry - today).days
    if days_left < 0:
        return ExpiryStatus('車検切れ', '#dc2626', days_left)
    if days_left <= ALERT_DAYS:
        return ExpiryStatus('要更新(2か月以内)', '#ea580c', days_left)
    return ExpiryStatus('正常', '#16a34a', days_left)


def years_months_since(ym: str | None, today: date | None = None) -> str:
    """'YYYY-MM' → 'XX年YYヶ月'。"""
    if not ym:
        return ''
    try:
        y, m = ym.split('-')
        base = date(int(y), int(m), 1)
    except Exception:
        return ''
    today = today or date.today()
    months = (today.year - base.year) * 12 + (today.month - base.month)
    if months < 0:
        return ''
    return f'{months // 12}年{months % 12}ヶ月'


def years_since(ym: str | None, today: date | None = None) -> int | None:
    """'YYYY-MM' → 経過年数(整数)。失敗・未来日は None。"""
    if not ym:
        return None
    try:
        y, m = ym.split('-')
        base = date(int(y), int(m), 1)
    except Exception:
        return None
    today = today or date.today()
    months = (today.year - base.year) * 12 + (today.month - base.month)
    if months < 0:
        return None
    return months // 12


def parse_first_registration_ym(text: str | None) -> str | None:
    """『2012-08』『平成24年8月』『令和5年3月』『R5.3』『H24/8』等を 'YYYY-MM' に正規化。

    失敗時は None。
    """
    if not text:
        return None
    s = str(text).strip().replace(' ', '').replace('　', '')
    if not s:
        return None

    # YYYY-MM / YYYY/MM / YYYY.MM / YYYY年MM月
    m = re.fullmatch(r'(\d{4})[-/.年](\d{1,2})月?', s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f'{y:04d}-{mo:02d}'

    # 元号 + 年 + 月
    m = re.fullmatch(r'(令和|平成|昭和|大正)(\d{1,2})年(\d{1,2})月?', s)
    if m:
        era, y, mo = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            try:
                return f'{wareki_to_year(era, y):04d}-{mo:02d}'
            except ValueError:
                return None

    # 略記 R5.3 / H24.8 / S60-3
    m = re.fullmatch(r'([RHShsr])(\d{1,2})[./-](\d{1,2})月?', s)
    if m:
        era = {'R': '令和', 'H': '平成', 'S': '昭和'}.get(m.group(1).upper())
        y, mo = int(m.group(2)), int(m.group(3))
        if era and 1 <= mo <= 12:
            try:
                return f'{wareki_to_year(era, y):04d}-{mo:02d}'
            except ValueError:
                return None

    return None


def to_wareki_ym_str(ym: str | None) -> str:
    """'2003-03' → '平成15年3月' 形式に変換。失敗時は元の文字列。"""
    if not ym:
        return ''
    try:
        y_s, m_s = str(ym).split('-')
        y, m = int(y_s), int(m_s)
    except (ValueError, AttributeError):
        return str(ym) if ym else ''
    if y >= 2019:
        return f'令和{y - 2018}年{m}月'
    if y >= 1989:
        return f'平成{y - 1988}年{m}月'
    if y >= 1926:
        return f'昭和{y - 1925}年{m}月'
    return str(ym)
