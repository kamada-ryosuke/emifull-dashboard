"""給付費CSV (国の標準形式) を解析するモジュール"""
import csv
import hashlib
import io
import unicodedata


def parse_year_month(yyyymm):
    s = str(yyyymm).strip().strip('"')
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}"
    return None


def safe_int(s):
    try:
        cleaned = str(s).strip().strip('"').replace(',', '')
        return int(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def kana_normalize(s):
    if s is None:
        return None
    text = str(s).strip().strip('"')
    return unicodedata.normalize('NFKC', text) if text else None


def parse_k122_01(row, billing_ym):
    """K122-01 児童基本情報"""
    facility_code = str(row[6]).strip().strip('"')
    return {
        'billing_year_month': billing_ym,
        'service_year_month': parse_year_month(row[4]),
        'csv_facility_code': facility_code,
        'cert_number': str(row[7]).strip().strip('"'),
        'guardian_name': kana_normalize(row[9]) if len(row) > 9 else None,
        'child_name': kana_normalize(row[10]) if len(row) > 10 else None,
        'fee_limit': safe_int(row[13]) if len(row) > 13 else None,
        'total_cost': safe_int(row[22]) if len(row) > 22 else None,
        'self_charge': safe_int(row[28]) if len(row) > 28 else None,    # 決定利用者負担額
        'kokuho_charge': safe_int(row[29]) if len(row) > 29 else None,  # 請求額給付費
    }


def parse_j121_01(row, billing_ym):
    """J121-01 障がい者(成人)サービス利用者基本情報。
    K122 と主要フィールドの位置は同じ。利用者名は row[9]。保護者名は無いので None。"""
    facility_code = str(row[6]).strip().strip('"')
    return {
        'billing_year_month': billing_ym,
        'service_year_month': parse_year_month(row[4]),
        'csv_facility_code': facility_code,
        'cert_number': str(row[7]).strip().strip('"'),
        'guardian_name': None,
        'child_name': kana_normalize(row[9]) if len(row) > 9 else None,  # 利用者名(成人)
        'fee_limit': safe_int(row[13]) if len(row) > 13 else None,       # 上限月額
        'total_cost': safe_int(row[22]) if len(row) > 22 else None,
        'self_charge': safe_int(row[28]) if len(row) > 28 else None,
        'kokuho_charge': safe_int(row[29]) if len(row) > 29 else None,
    }


def parse_csv_bytes(data: bytes) -> dict:
    """CSVバイト列を解析して辞書を返す。K122-01(児童)と J121-01(成人) 両対応。"""
    file_hash = hashlib.sha256(data).hexdigest()
    text = data.decode('shift_jis', errors='replace')
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        return {'error': 'CSVが空です'}

    header = rows[0]
    billing_ym = parse_year_month(header[9]) if len(header) > 9 else None

    records = []
    errors = []
    for line_no, row in enumerate(rows, start=1):
        if len(row) < 4:
            continue
        if row[2] == "K122" and row[3] == "01":
            try:
                rec = parse_k122_01(row, billing_ym)
                records.append(rec)
            except Exception as e:
                errors.append(f"line {line_no}: {e}")
        elif row[2] == "J121" and row[3] == "01":
            try:
                rec = parse_j121_01(row, billing_ym)
                records.append(rec)
            except Exception as e:
                errors.append(f"line {line_no}: {e}")

    from collections import Counter
    sym_counter = Counter(r['service_year_month'] for r in records if r['service_year_month'])
    service_ym = sym_counter.most_common(1)[0][0] if sym_counter else None
    facility_codes = sorted({r['csv_facility_code'] for r in records if r['csv_facility_code']})

    return {
        'billing_year_month': billing_ym,
        'service_year_month': service_ym,
        'csv_facility_codes': facility_codes,
        'records': records,
        'file_hash': file_hash,
        'row_count': len(records),
        'errors': errors,
    }
