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


def _row_text(row, index):
    if len(row) <= index:
        return None
    text = str(row[index]).strip().strip('"')
    return text or None


def _positive_int(value):
    return safe_int(value) or 0


def _jg_primary_facility_code(rows):
    """JG(K411/K421)ファイル自体の事業所番号を取得する。"""
    for row in rows:
        if len(row) > 6 and row[2:4] == ["0", "37"]:
            return _row_text(row, 6)
    for row in rows:
        if len(row) > 7 and row[2] in ("K411", "K421") and row[3] == "01":
            return _row_text(row, 7)
    return None


def _merge_jg_record(records_by_key, rec):
    key = (
        rec['service_year_month'],
        rec['csv_facility_code'],
        rec['cert_number'],
    )
    existing = records_by_key.get(key)
    if existing is None:
        records_by_key[key] = rec
        return

    existing['total_cost'] = (existing.get('total_cost') or 0) + (rec.get('total_cost') or 0)
    existing['self_charge'] = (existing.get('self_charge') or 0) + (rec.get('self_charge') or 0)
    existing['kokuho_charge'] = (existing.get('kokuho_charge') or 0) + (rec.get('kokuho_charge') or 0)
    if not existing.get('child_name') and rec.get('child_name'):
        existing['child_name'] = rec['child_name']
    if not existing.get('guardian_name') and rec.get('guardian_name'):
        existing['guardian_name'] = rec['guardian_name']
    if existing.get('fee_limit') is None and rec.get('fee_limit') is not None:
        existing['fee_limit'] = rec['fee_limit']


def parse_jg_records(rows, billing_ym):
    """JG形式(K411/K421)を解析する。

    K411/K421 は上限管理後の明細を含む形式。
    02行のうち、ファイルの事業所番号に一致する明細だけを取り込み対象にする。
    """
    primary_code = _jg_primary_facility_code(rows)
    if not primary_code:
        return [], []

    summaries = {}
    for row in rows:
        if len(row) < 12 or row[2] not in ("K411", "K421") or row[3] != "01":
            continue
        key = (row[2], parse_year_month(row[4]), _row_text(row, 8))
        summaries[key] = {
            'guardian_name': kana_normalize(_row_text(row, 9)),
            'child_name': kana_normalize(_row_text(row, 10)),
            'fee_limit': safe_int(row[11]) if len(row) > 11 else None,
        }

    records_by_key = {}
    errors = []
    for line_no, row in enumerate(rows, start=1):
        if len(row) < 13 or row[2] not in ("K411", "K421") or row[3] != "02":
            continue
        if _row_text(row, 9) != primary_code:
            continue

        service_ym = parse_year_month(row[4])
        parent_cert = _row_text(row, 7)
        summary = summaries.get((row[2], service_ym, parent_cert), {})
        total_cost = _positive_int(row[10])
        self_charge = _positive_int(row[12])
        kokuho_charge = max(total_cost - self_charge, 0)

        if row[2] == "K421" and len(row) > 14:
            cert_number = _row_text(row, 13) or parent_cert
            child_name = kana_normalize(_row_text(row, 14)) or summary.get('child_name')
        else:
            cert_number = parent_cert
            child_name = summary.get('child_name')

        if not service_ym or not cert_number:
            errors.append(f"line {line_no}: サービス年月または受給者証番号が取得できません")
            continue

        rec = {
            'billing_year_month': billing_ym,
            'service_year_month': service_ym,
            'csv_facility_code': primary_code,
            'cert_number': cert_number,
            'guardian_name': summary.get('guardian_name'),
            'child_name': child_name,
            'fee_limit': summary.get('fee_limit'),
            'total_cost': total_cost,
            'self_charge': self_charge,
            'kokuho_charge': kokuho_charge,
        }
        _merge_jg_record(records_by_key, rec)

    return list(records_by_key.values()), errors


def parse_csv_bytes(data: bytes) -> dict:
    """CSVバイト列を解析して辞書を返す。SK(K122/J121) と JG(K411/K421) に対応。"""
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

    jg_records, jg_errors = parse_jg_records(rows, billing_ym)
    records.extend(jg_records)
    errors.extend(jg_errors)

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
