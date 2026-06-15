"""PRIME専用の損益計算書CSV・仕訳帳CSVパーサ."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


FILENAME_YM_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
DATE_YM_RE = re.compile(r"(\d{4})[-/年](\d{1,2})")


PL_CATEGORY_LABELS = {
    "revenue": "売上",
    "revenue_total": "売上高計",
    "cogs": "売上原価",
    "cogs_total": "売上原価計",
    "gross_profit": "売上総利益",
    "sga": "販管費",
    "sga_total": "販管費計",
    "operating_profit": "営業利益",
    "non_operating_income": "営業外収益",
    "non_operating_expense": "営業外費用",
    "ordinary_profit": "経常利益",
    "special_income": "特別利益",
    "special_loss": "特別損失",
    "pretax_income": "税引前利益",
    "tax": "法人税等",
    "net_income": "当期純利益",
    "other": "その他",
}


@dataclass
class PrimePLParseResult:
    filename: str
    year_month: str | None
    entries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    encoding: str = ""
    file_hash: str = ""


@dataclass
class PrimeJournalParseResult:
    filename: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    encoding: str = ""
    file_hash: str = ""
    date_range: str = "対象行なし"


def _read_bytes(file_or_path) -> bytes:
    if hasattr(file_or_path, "getvalue"):
        return file_or_path.getvalue()
    if hasattr(file_or_path, "read"):
        return file_or_path.read()
    with open(file_or_path, "rb") as f:
        return f.read()


def _decode_csv(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-8"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace"), "cp932(replace)"


def _rows_from_bytes(data: bytes) -> tuple[list[list[str]], str]:
    text, encoding = _decode_csv(data)
    rows = list(csv.reader(io.StringIO(text)))
    return rows, encoding


def _clean_cell(value: Any) -> str:
    return str(value or "").replace("\ufeff", "").strip()


def _normalize_header(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_cell(value))


def _parse_amount(value: Any) -> int | None:
    s = _clean_cell(value)
    if not s or s in {"-", "－", "―"}:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.startswith("△") or s.startswith("▲"):
        negative = True
        s = s[1:]
    s = s.replace(",", "").replace("円", "").replace("￥", "").strip()
    try:
        value_num = int(round(float(s)))
    except (TypeError, ValueError):
        return None
    return -value_num if negative else value_num


def _extract_ym_from_filename(filename: str) -> str | None:
    m = FILENAME_YM_RE.search(filename or "")
    if not m:
        return None
    return f"{int(m.group(1))}-{int(m.group(2)):02d}"


def _extract_ym_from_date(value: str) -> str | None:
    m = DATE_YM_RE.search(value or "")
    if not m:
        return None
    return f"{int(m.group(1))}-{int(m.group(2)):02d}"


def _is_section_label(label: str) -> bool:
    keys = (
        "売上高",
        "売上原価",
        "販売費及び一般管理費",
        "販売費および一般管理費",
        "販管費",
        "営業外収益",
        "営業外費用",
        "特別利益",
        "特別損失",
        "法人税",
    )
    return any(k in label for k in keys)


def _is_total_label(label: str) -> bool:
    keys = ("合計", "計", "利益", "損失", "損益")
    return any(k in label for k in keys)


def classify_pl_account(account_name: str, current_section: str = "") -> str:
    label = account_name or ""
    section = current_section or ""
    text = f"{section} {label}"

    if "当期純" in label:
        return "net_income"
    if "税引前" in label:
        return "pretax_income"
    if "経常" in label and ("利益" in label or "損失" in label or "損益" in label):
        return "ordinary_profit"
    if "営業利益" in label or "営業損失" in label or "営業損益" in label:
        return "operating_profit"
    if "売上総" in label:
        return "gross_profit"
    if "法人税" in text or "住民税" in text or "事業税" in text:
        return "tax"
    if "特別利益" in text:
        return "special_income"
    if "特別損失" in text:
        return "special_loss"
    if "営業外収益" in text:
        return "non_operating_income"
    if "営業外費用" in text:
        return "non_operating_expense"
    if "販売費及び一般管理費" in text or "販売費および一般管理費" in text or "販管費" in text:
        return "sga_total" if _is_total_label(label) and _is_section_label(label) else "sga"
    if "売上原価" in text:
        return "cogs_total" if _is_total_label(label) and _is_section_label(label) else "cogs"
    if "売上高" in text:
        return "revenue_total" if _is_total_label(label) or _is_section_label(label) else "revenue"
    if "売上" in label:
        return "revenue"
    return "other"


def _find_pl_header(rows: list[list[str]]) -> tuple[int | None, int | None, int | None]:
    account_names = {"勘定科目", "科目", "科目名", "損益科目"}
    amount_preferred = (
        "残高",
        "当月残高",
        "期間残高",
        "当期残高",
        "貸借差額",
        "金額",
        "合計",
    )
    debit_credit_amounts = {"借方金額", "貸方金額"}
    for r_idx, row in enumerate(rows[:30]):
        headers = [_normalize_header(c) for c in row]
        account_idx = next((i for i, h in enumerate(headers) if h in account_names), None)
        if account_idx is None:
            continue
        amount_idx = next((i for i, h in enumerate(headers) if h in amount_preferred), None)
        if amount_idx is None:
            candidates = [
                i for i, h in enumerate(headers)
                if ("金額" in h or "残高" in h) and h not in debit_credit_amounts
            ]
            amount_idx = candidates[-1] if candidates else None
        return r_idx, account_idx, amount_idx
    return None, None, None


def _last_numeric_in_row(row: list[str]) -> int | None:
    for cell in reversed(row):
        amount = _parse_amount(cell)
        if amount is not None:
            return amount
    return None


def parse_prime_pl_csv(file_or_path, filename: str, year_month: str | None = None) -> PrimePLParseResult:
    data = _read_bytes(file_or_path)
    file_hash = hashlib.sha256(data).hexdigest()
    rows, encoding = _rows_from_bytes(data)
    detected_ym = year_month or _extract_ym_from_filename(filename)
    result = PrimePLParseResult(
        filename=filename,
        year_month=detected_ym,
        encoding=encoding,
        file_hash=file_hash,
    )

    if not rows:
        result.error = "CSV内容が空です。"
        return result
    if result.year_month is None:
        result.error = "ファイル名から対象月を判定できませんでした。"
        return result

    header_idx, account_idx, amount_idx = _find_pl_header(rows)
    if header_idx is None or account_idx is None:
        result.error = "勘定科目の列を見つけられませんでした。"
        return result

    if amount_idx is None:
        result.warnings.append("金額列を特定できなかったため、各行の右端にある数値を金額として読み取りました。")

    current_section = ""
    display_order = 0
    for row in rows[header_idx + 1:]:
        if account_idx >= len(row):
            continue
        account_name = _clean_cell(row[account_idx])
        if not account_name:
            continue
        amount = _parse_amount(row[amount_idx]) if amount_idx is not None and amount_idx < len(row) else None
        if amount is None:
            amount = _last_numeric_in_row(row)

        if _is_section_label(account_name):
            current_section = account_name

        if amount is None:
            continue

        category = classify_pl_account(account_name, current_section)
        result.entries.append({
            "year_month": result.year_month,
            "account_name": account_name,
            "category": category,
            "amount": amount,
            "display_order": display_order,
            "is_total": 1 if _is_total_label(account_name) else 0,
        })
        display_order += 1

    if not result.entries:
        result.error = "取込できる損益行が見つかりませんでした。"
    return result


def _find_header_index(headers: list[str], aliases: tuple[str, ...]) -> int | None:
    normalized_aliases = {re.sub(r"\s+", "", a) for a in aliases}
    for idx, header in enumerate(headers):
        if header in normalized_aliases:
            return idx
    for idx, header in enumerate(headers):
        if any(alias in header for alias in normalized_aliases):
            return idx
    return None


def _normalize_date(value: str) -> str | None:
    s = _clean_cell(value)
    if not s:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.match(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def parse_prime_journal_csv(file_or_path, filename: str) -> PrimeJournalParseResult:
    data = _read_bytes(file_or_path)
    file_hash = hashlib.sha256(data).hexdigest()
    rows, encoding = _rows_from_bytes(data)
    result = PrimeJournalParseResult(
        filename=filename,
        encoding=encoding,
        file_hash=file_hash,
    )
    if not rows:
        result.error = "CSV内容が空です。"
        return result

    header_row_idx = None
    mapping = {}
    for r_idx, row in enumerate(rows[:30]):
        headers = [_normalize_header(c) for c in row]
        candidate = {
            "date": _find_header_index(headers, ("取引日", "発生日", "日付", "仕訳日", "計上日")),
            "debit_account": _find_header_index(headers, ("借方勘定科目", "借方科目", "借方勘定科目名")),
            "debit_amount": _find_header_index(headers, ("借方金額", "借方金額円", "借方")),
            "credit_account": _find_header_index(headers, ("貸方勘定科目", "貸方科目", "貸方勘定科目名")),
            "credit_amount": _find_header_index(headers, ("貸方金額", "貸方金額円", "貸方")),
            "debit_partner": _find_header_index(headers, ("借方取引先名", "借方取引先", "取引先")),
            "credit_partner": _find_header_index(headers, ("貸方取引先名", "貸方取引先", "取引先")),
            "memo": _find_header_index(headers, ("摘要", "備考", "メモ", "取引内容")),
            "item": _find_header_index(headers, ("品目", "品目名")),
            "journal_id": _find_header_index(headers, ("仕訳ID", "仕訳番号", "伝票番号")),
            "record_no": _find_header_index(headers, ("行番号", "レコード番号", "仕訳行番号")),
        }
        required = ("date", "debit_account", "debit_amount", "credit_account", "credit_amount")
        if all(candidate.get(k) is not None for k in required):
            header_row_idx = r_idx
            mapping = candidate
            break

    if header_row_idx is None:
        result.error = "仕訳帳の必須列（取引日・借方/貸方科目・借方/貸方金額）を見つけられませんでした。"
        return result

    def get(row: list[str], key: str) -> str:
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return ""
        return _clean_cell(row[idx])

    dates = []
    for row_no, row in enumerate(rows[header_row_idx + 1:], start=1):
        transaction_date = _normalize_date(get(row, "date"))
        if not transaction_date:
            continue
        debit_amount = _parse_amount(get(row, "debit_amount")) or 0
        credit_amount = _parse_amount(get(row, "credit_amount")) or 0
        year_month = _extract_ym_from_date(transaction_date)
        dates.append(transaction_date)
        memo = get(row, "memo")
        item = get(row, "item")
        result.rows.append({
            "transaction_date": transaction_date,
            "year_month": year_month,
            "debit_account": get(row, "debit_account"),
            "debit_amount": debit_amount,
            "debit_partner": get(row, "debit_partner"),
            "debit_memo": memo,
            "debit_item": item,
            "credit_account": get(row, "credit_account"),
            "credit_amount": credit_amount,
            "credit_partner": get(row, "credit_partner"),
            "credit_memo": memo,
            "credit_item": item,
            "journal_id": get(row, "journal_id"),
            "record_no": get(row, "record_no") or str(row_no),
            "transaction_content": memo,
            "row_no": row_no,
        })

    if dates:
        result.date_range = f"{min(dates)}〜{max(dates)}"
    if not result.rows:
        result.error = "取込できる仕訳行が見つかりませんでした。"
    return result
