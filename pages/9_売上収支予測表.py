"""売上収支予測表 - 日別利用人数から月次の売上・利益着地を予測する。"""
import calendar
import html
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from lib import auth, db, styling


JP_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
CALENDAR_WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"]
FORECAST_EMPTY_OPTION = "__forecast_empty__"
FORECAST_USER_OPTIONS = [FORECAST_EMPTY_OPTION] + list(range(31))
SPLIT_GROUP_CODES = {"001", "002", "003"}
SENIOR_POSITIONS = {"部長", "次長", "課長"}


styling.inject_global_css()
auth.require_login()
auth.render_sidebar_navigation()
db.init_revenue_forecast_schema()


def _today_jst():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def _ym_to_date(ym):
    year, month = [int(v) for v in ym.split("-")]
    return date(year, month, 1)


def _ym_shift(ym, months):
    first = _ym_to_date(ym)
    total = first.year * 12 + first.month - 1 + months
    return f"{total // 12}-{total % 12 + 1:02d}"


def _month_days(ym):
    first = _ym_to_date(ym)
    _, last_day = calendar.monthrange(first.year, first.month)
    return [date(first.year, first.month, day) for day in range(1, last_day + 1)]


def _month_bounds(ym):
    days = _month_days(ym)
    return days[0], days[-1]


def _nth_weekday(year, month, weekday, nth):
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(days=(nth - 1) * 7)


def _vernal_equinox_day(year):
    return int(20.8431 + 0.242194 * (year - 1980) - math.floor((year - 1980) / 4))


def _autumn_equinox_day(year):
    return int(23.2488 + 0.242194 * (year - 1980) - math.floor((year - 1980) / 4))


def _japanese_holidays(year):
    holidays = {
        date(year, 1, 1): "元日",
        _nth_weekday(year, 1, 0, 2): "成人の日",
        date(year, 2, 11): "建国記念の日",
        date(year, 2, 23): "天皇誕生日",
        date(year, 3, _vernal_equinox_day(year)): "春分の日",
        date(year, 4, 29): "昭和の日",
        date(year, 5, 3): "憲法記念日",
        date(year, 5, 4): "みどりの日",
        date(year, 5, 5): "こどもの日",
        _nth_weekday(year, 7, 0, 3): "海の日",
        date(year, 8, 11): "山の日",
        _nth_weekday(year, 9, 0, 3): "敬老の日",
        date(year, 9, _autumn_equinox_day(year)): "秋分の日",
        _nth_weekday(year, 10, 0, 2): "スポーツの日",
        date(year, 11, 3): "文化の日",
        date(year, 11, 23): "勤労感謝の日",
    }
    additions = {}
    for holiday in sorted(holidays):
        if holiday.weekday() != 6:
            continue
        substitute = holiday + timedelta(days=1)
        while substitute in holidays or substitute in additions:
            substitute += timedelta(days=1)
        additions[substitute] = "振替休日"
    holidays.update(additions)

    d = date(year, 1, 2)
    while d.year == year:
        if d not in holidays and d - timedelta(days=1) in holidays and d + timedelta(days=1) in holidays:
            holidays[d] = "国民の休日"
        d += timedelta(days=1)
    return holidays


def _holiday_label(d):
    holidays = _japanese_holidays(d.year)
    if d in holidays:
        return holidays[d]
    if d.weekday() == 5:
        return "土曜"
    if d.weekday() == 6:
        return "日曜"
    return "平日"


def _is_business_weekday(d):
    return d.weekday() < 5 and d not in _japanese_holidays(d.year)


def _as_int_or_none(value):
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    ivalue = int(float(value))
    if ivalue < 0 or ivalue > 30:
        raise ValueError("人数は0〜30の範囲で入力してください。")
    return ivalue


def _yen_to_thousand(value):
    if value is None:
        return None
    return int(float(value) / 1000)


def _fmt_k_yen(value):
    if value is None:
        return "－"
    return f"{_yen_to_thousand(value):,} 千円"


def _fmt_diff_k_yen(value):
    if value is None:
        return "－"
    return f"{_yen_to_thousand(value):+,} 千円"


def _fmt_count(value):
    if value is None:
        return "－"
    return f"{int(value):,} 回"


def _fmt_diff_count(value):
    if value is None:
        return "－"
    return f"{int(value):+,} 回"


def _fmt_pct(value):
    if value is None:
        return "－"
    return f"{value:.1f}%"


def _fmt_diff_pct(value):
    if value is None:
        return "－"
    return f"{value:+.1f}%"


def _fmt_unit_yen(value):
    if value is None:
        return "－"
    return f"{int(value):,} 円/回"


def _fmt_average(value, suffix="人"):
    if value is None:
        return "－"
    return f"{value:.1f}{suffix}"


def _unit_basis_average_note(unit):
    rows = unit.get("rows") or []
    if not rows or unit.get("revenue_total") is None or not unit.get("usage_total"):
        return "損益データと延べ利用回数を確認"
    months = len(rows)
    label = "3カ月平均" if months == 3 else f"採用{months}カ月平均"
    revenue_avg = unit["revenue_total"] / months
    usage_avg = unit["usage_total"] / months
    return f"{label}: 売上 {_fmt_k_yen(revenue_avg)}/月・利用 {_fmt_average(usage_avg, '回/月')}"


def _month_label(year, month):
    return f"{int(year)}年{int(month)}月"


def _shift_year_month(year, month, delta):
    total = int(year) * 12 + int(month) - 1 + delta
    return total // 12, total % 12 + 1


def _profit_rate(profit, revenue):
    if revenue is None or revenue == 0 or profit is None:
        return None
    return profit / revenue * 100


def _normalize_name(name):
    return str(name or "").replace("　", " ").replace("（", "(").replace("）", ")").strip().lower()


HISTORICAL_USAGE_MONTHS = [
    "2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09",
    "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
    "2026-04", "2026-05", "2026-06",
]

HISTORICAL_USAGE_ROWS = {
    "SORATOいなみ": [195, 214, 222, 230, 174, 212, 229, 180, 199, 205, 198, 225, 222, 207, 252],
    "UMIEいなみ": [234, 237, 240, 244, 182, 253, 270, 215, 218, 205, 221, 224, 225, 214, 272],
    "UMIEいなみ第二教室": [234, 236, 243, 253, 191, 243, 247, 216, 227, 201, 198, 244, 239, 200, 244],
    "SORATOいなみ第二教室": [189, 190, 196, 210, 185, 190, 205, 168, 185, 194, 169, 232, 209, 184, 219],
    "BLOOMいなみ": [7, 9, 13, 7, 3, 5, 10, 13, 13, 13, 21, 18, 15, 27, 32],
    "ジョブカレッジかこがわ": [None, None, 181, 209, 204, 228, 222, 210, 224, 211, 214, 239, 228, 199, 236],
    "カラダキッズかこがわ": [None, None, 215, 223, 196, 216, 241, 194, 211, 214, 214, 268, 275, 238, 287],
    "SORATOてんり": [169, 184, 187, 217, 198, 214, 220, 207, 212, 211, 203, 218, 201, 206, 206],
    "UMIEてんり": [219, 230, 228, 245, 206, 230, 245, 219, 219, 218, 219, 220, 229, 224, 238],
    "BLOOMてんり": [3, 7, 6, 3, 3, 5, 6, 5, 3, 5, 7, 4, 5, 8, 8],
    "カラダキッズてんり": [None, None, None, None, None, None, None, None, None, None, None, None, None, None, 71],
    "Hinodeシェアホーム天理１・２": [564, 579, 563, 577, 575, 563, 569, 562, 580, 573, 536, 608, 585, 568, 554],
    "Hinodeシェアホーム天理３": [111, 120, 117, 162, 181, 177, 198, 201, 208, 214, 214, 255, 224, 177, 182],
    "Hinodeシェアホーム天理": [675, 699, 680, 739, 756, 740, 767, 763, 788, 787, 750, 863, 809, 745, 736],
    "SORATO(UMIE)きたはま": [38, 41, 48, 44, None, None, None, None, None, None, None, None, None, None, None],
    "のじぎく高砂": [424, 437, 441, 450, 408, 423, 434, 393, 424, 425, 382, 521, 532, 507, 503],
    "のじぎく稲美": [179, 154, 152, 192, 193, 191, 175, 143, 132, 134, 144, 200, 231, 244, 249],
    "のじぎく加古川": [None, None, None, None, None, None, None, None, None, None, None, None, None, None, 14],
    "こすもす稲美": [76, 75, 75, 61, 71, 55, 41, 37, 36, 33, 31, 38, 35, 33, 38],
    "Hinodeシェアホーム加古川": [None, None, None, None, None, None, None, None, None, None, None, 219, 209, 210, 215],
    "相談支援NOAH加古川": [None, None, None, None, None, None, None, None, None, None, None, 21, 24, 20, 24],
}


def _build_historical_usage_index():
    index = defaultdict(dict)
    for facility_name, values in HISTORICAL_USAGE_ROWS.items():
        key = _normalize_name(facility_name)
        for ym, value in zip(HISTORICAL_USAGE_MONTHS, values):
            if value is not None:
                index[ym][key] = int(value)
    return index


HISTORICAL_USAGE_BY_MONTH = _build_historical_usage_index()
HISTORICAL_USAGE_ALIASES = {
    _normalize_name("SORATOいなみ第二"): [_normalize_name("SORATOいなみ第二教室")],
    _normalize_name("UMIEいなみ第二"): [_normalize_name("UMIEいなみ第二教室")],
    _normalize_name("Hinode天理"): [_normalize_name("Hinodeシェアホーム天理")],
    _normalize_name("Hinodeシェアホーム天理1・2"): [_normalize_name("Hinodeシェアホーム天理１・２")],
    _normalize_name("Hinodeシェアホーム天理3"): [_normalize_name("Hinodeシェアホーム天理３")],
    _normalize_name("Hinode加古川"): [_normalize_name("Hinodeシェアホーム加古川")],
    _normalize_name("Hiniodeシェアホーム加古川"): [_normalize_name("Hinodeシェアホーム加古川")],
}


def _historical_usage_keys(name):
    key = _normalize_name(name)
    return [key] + HISTORICAL_USAGE_ALIASES.get(key, [])


def _historical_usage_for_facility_month(facility, ym):
    usage_map = HISTORICAL_USAGE_BY_MONTH.get(ym) or {}
    if not usage_map:
        return 0, ""

    for preferred in (facility.get("label"), facility.get("group_name")):
        for key in _historical_usage_keys(preferred):
            if key in usage_map:
                return usage_map[key], "過去延べ利用回数表"

    total = 0
    seen = set()
    for name in facility.get("search_names", set()):
        for key in _historical_usage_keys(name):
            if key in seen:
                continue
            seen.add(key)
            total += usage_map.get(key, 0)
    if total > 0:
        return total, "過去延べ利用回数表"
    return 0, ""


def _facility_display_name(group):
    return group.get("name") or ""


def _facility_row_from_subunit(group, sub):
    return {
        "key": f"sub:{sub['id']}",
        "label": sub["display_name"],
        "group_code": group["code"],
        "group_name": group["name"],
        "kind": "subunit",
        "subunit_ids": [sub["id"]],
        "primary_subunit_id": sub["id"],
        "search_names": {
            sub["display_name"],
            sub["excel_name"],
            sub["display_name"].replace("教室", ""),
        },
    }


def _facility_row_from_subunits(group, label, subunits, key_suffix=""):
    search_names = {label, group["name"], label.replace("シェアホーム", "")}
    for sub in subunits:
        search_names.add(sub["display_name"])
        search_names.add(sub["excel_name"])
    suffix = f":{key_suffix}" if key_suffix else ""
    return {
        "key": f"grp:{group['code']}{suffix}",
        "label": label,
        "group_code": group["code"],
        "group_name": group["name"],
        "kind": "group",
        "subunit_ids": [s["id"] for s in subunits],
        "primary_subunit_id": subunits[0]["id"],
        "search_names": search_names,
    }


def _build_forecast_facilities():
    groups = db.list_pl_groups()
    subunits = db.list_pl_subunits()
    by_group = defaultdict(list)
    for sub in subunits:
        by_group[sub["group_id"]].append(sub)

    rows = []
    for group in groups:
        note = group.get("note") or ""
        if "閉鎖" in note or "きたはま" in (group.get("name") or ""):
            continue
        group_subs = by_group.get(group["id"], [])
        if not group_subs:
            continue
        if group.get("code") == "010":
            continue
        if group.get("code") == "012":
            kakogawa_subs = [
                sub for sub in group_subs
                if "加古川" in (sub.get("display_name") or sub.get("excel_name") or "")
            ]
            inami_subs = [sub for sub in group_subs if sub not in kakogawa_subs]
            if inami_subs:
                rows.append(_facility_row_from_subunits(group, "のじぎく稲美", inami_subs, "inami"))
            if kakogawa_subs:
                rows.append(_facility_row_from_subunits(group, "のじぎく加古川", kakogawa_subs, "kakogawa"))
            continue
        if group.get("code") in SPLIT_GROUP_CODES:
            for sub in group_subs:
                rows.append(_facility_row_from_subunit(group, sub))
            continue

        label = _facility_display_name(group)
        rows.append(_facility_row_from_subunits(group, label, group_subs))
    return rows


def _accessible_facilities(facilities):
    forecast_profile = auth.current_forecast_facility()
    if forecast_profile:
        target = _normalize_name(forecast_profile.get("facility_label"))
        return [
            facility for facility in facilities
            if target == _normalize_name(facility["label"])
            or target in {_normalize_name(name) for name in facility.get("search_names", set())}
        ]
    current = auth.current_user() or {}
    position = current.get("position") or ""
    if auth.is_admin() or position in SENIOR_POSITIONS:
        return facilities
    return facilities


def _index_daily_records(records):
    indexed = defaultdict(dict)
    for record in records:
        try:
            d = date.fromisoformat(str(record.get("target_date")))
        except Exception:
            continue
        indexed[record["facility_key"]][d] = record
    return indexed


def _build_forecast_usage_index(records):
    indexed = defaultdict(lambda: {
        "actual_total": 0,
        "actual_days": 0,
        "planned_total": 0,
        "planned_days": 0,
    })
    for record in records:
        try:
            ym = date.fromisoformat(str(record.get("target_date"))).strftime("%Y-%m")
        except Exception:
            continue
        key = (record.get("facility_key"), ym)
        actual = record.get("actual_users")
        planned = record.get("planned_users")
        if actual is not None:
            indexed[key]["actual_total"] += int(actual)
            indexed[key]["actual_days"] += 1
        if planned is not None:
            indexed[key]["planned_total"] += int(planned)
            indexed[key]["planned_days"] += 1
    return indexed


def _forecast_input_usage_for_facility_month(facility, ym, forecast_usage_index):
    if not forecast_usage_index:
        return 0, ""
    row = forecast_usage_index.get((facility["key"], ym))
    if not row:
        return 0, ""
    if row["actual_days"] > 0:
        return row["actual_total"], "売上収支予測表の実績"
    if row["planned_days"] > 0:
        return row["planned_total"], "売上収支予測表の予定"
    return 0, ""


def _build_pl_indexes(entries):
    revenue = defaultdict(int)
    sga = defaultdict(int)
    has_sga = set()
    has_revenue = set()
    for entry in entries:
        key = (entry["year_month"], entry["subunit_id"])
        if entry["category"] == "revenue_total":
            revenue[key] += int(entry["amount"] or 0)
            has_revenue.add(key)
        elif entry["category"] == "sga_total":
            sga[key] += int(entry["amount"] or 0)
            has_sga.add(key)
    return revenue, sga, has_revenue, has_sga


def _sum_by_subunits(index, ym, subunit_ids):
    return sum(index.get((ym, sid), 0) for sid in subunit_ids)


def _has_any(index_set, ym, subunit_ids):
    return any((ym, sid) in index_set for sid in subunit_ids)


def _build_usage_indexes(usage_rows, receipt_rows):
    usage_by_name = defaultdict(int)
    for row in usage_rows:
        count = int(row.get("monthly_usage_count") or 0)
        if count <= 0:
            continue
        usage_by_name[(row.get("target_month"), _normalize_name(row.get("facility_name")))] += count

    receipt_by_subunit = defaultdict(int)
    for row in receipt_rows:
        count = int(row.get("monthly_usage_count") or 0)
        if count <= 0:
            continue
        receipt_by_subunit[(row.get("service_year_month"), row.get("pl_subunit_id"))] += count
    return usage_by_name, receipt_by_subunit


def _usage_for_facility_month(facility, ym, usage_by_name, receipt_by_subunit, forecast_usage_index=None):
    forecast_count, forecast_source = _forecast_input_usage_for_facility_month(facility, ym, forecast_usage_index)
    if forecast_count > 0:
        return forecast_count, forecast_source

    receipt_count = sum(receipt_by_subunit.get((ym, sid), 0) for sid in facility["subunit_ids"])
    if receipt_count > 0:
        return receipt_count, "レセ報告"

    total = 0
    seen_names = set()
    for name in facility["search_names"]:
        key = _normalize_name(name)
        if key in seen_names:
            continue
        seen_names.add(key)
        total += usage_by_name.get((ym, key), 0)
    if total > 0:
        return total, "利用単価入力"
    historical_count, historical_source = _historical_usage_for_facility_month(facility, ym)
    if historical_count > 0:
        return historical_count, historical_source
    return 0, ""


def _unit_price_basis(facility, target_ym, available_yms, revenue_index,
                      has_revenue, usage_by_name, receipt_by_subunit, forecast_usage_index=None):
    previous_3 = [_ym_shift(target_ym, -1), _ym_shift(target_ym, -2), _ym_shift(target_ym, -3)]
    candidate_yms = []
    seen_yms = set()
    for offset in range(1, 16):
        ym = _ym_shift(target_ym, -offset)
        candidate_yms.append(ym)
        seen_yms.add(ym)
    for ym in available_yms:
        if ym < target_ym and ym not in seen_yms:
            candidate_yms.append(ym)
            seen_yms.add(ym)

    basis_rows = []
    excluded_rows = []
    for ym in candidate_yms:
        revenue = _sum_by_subunits(revenue_index, ym, facility["subunit_ids"])
        has_month_revenue = _has_any(has_revenue, ym, facility["subunit_ids"])
        has_usable_revenue = has_month_revenue and revenue > 0
        usage, source = _usage_for_facility_month(
            facility, ym, usage_by_name, receipt_by_subunit, forecast_usage_index
        )
        if usage > 0 and has_usable_revenue:
            basis_rows.append({"ym": ym, "revenue": revenue, "usage": usage, "source": source})
            if len(basis_rows) >= 3:
                break
        elif len(excluded_rows) < 6:
            if not has_month_revenue:
                reason = "損益売上なし"
            elif revenue <= 0:
                reason = "売上0円"
            elif usage <= 0:
                reason = "延べ利用回数なし"
            else:
                reason = "対象外"
            excluded_rows.append({
                "ym": ym,
                "revenue": revenue if has_month_revenue else None,
                "usage": usage if usage > 0 else None,
                "reason": reason,
            })

    if basis_rows:
        revenue_total = sum(r["revenue"] for r in basis_rows)
        usage_total = sum(r["usage"] for r in basis_rows)
        if usage_total > 0:
            source_label = "直近3カ月の加重平均"
            if len(basis_rows) < 3:
                source_label = "取得可能な過去月（参考）"
            elif [r["ym"] for r in basis_rows] != previous_3:
                source_label = "直近3カ月相当（0・未入力月を除外）"
            return {
                "unit_price": revenue_total / usage_total,
                "rows": sorted(basis_rows, key=lambda r: r["ym"]),
                "excluded_rows": excluded_rows,
                "source_label": source_label,
                "revenue_total": revenue_total,
                "usage_total": usage_total,
            }

    return {
        "unit_price": None,
        "rows": [],
        "excluded_rows": excluded_rows,
        "source_label": "未算出",
        "revenue_total": None,
        "usage_total": None,
    }


def _sga_basis(facility, target_ym, sga_index, has_sga):
    candidate_yms = [_ym_shift(target_ym, -offset) for offset in range(6, 0, -1)]
    rows = []
    values = []
    for ym in candidate_yms:
        if _has_any(has_sga, ym, facility["subunit_ids"]):
            amount = _sum_by_subunits(sga_index, ym, facility["subunit_ids"])
            rows.append({"ym": ym, "amount": amount, "status": "採用"})
            values.append(amount)
        else:
            rows.append({"ym": ym, "amount": None, "status": "損益データなし"})
    if not values:
        return {"forecast": None, "rows": rows, "source_label": "未算出", "months_count": 0}
    return {
        "forecast": sum(values) / len(values),
        "rows": rows,
        "source_label": f"直近6カ月平均（採用{len(values)}カ月）",
        "months_count": len(values),
    }


def _daily_metrics(days, daily_by_date, today):
    planned_total = 0
    actual_to_date = 0
    landing_total = 0
    planned_input_days = 0
    actual_input_days = 0
    business_planned_days = 0
    landing_actual_days = 0
    landing_plan_fill_days = 0
    landing_missing_days = 0
    future_plan_missing = 0
    elapsed_actual_missing = 0
    elapsed_days = 0
    last_updated = None
    last_updated_by = ""

    for d in days:
        rec = daily_by_date.get(d) or {}
        planned = rec.get("planned_users")
        actual = rec.get("actual_users")
        is_business_day = planned is not None or actual is not None
        if planned is not None:
            planned_total += int(planned)
            planned_input_days += 1
            business_planned_days += 1
        if d <= today and is_business_day:
            elapsed_days += 1
        if actual is not None and d <= today:
            actual_to_date += int(actual)
            actual_input_days += 1

        if actual is not None:
            landing_total += int(actual)
            landing_actual_days += 1
        elif planned is not None:
            landing_total += int(planned)
            landing_plan_fill_days += 1
            if d <= today:
                elapsed_actual_missing += 1
        else:
            landing_missing_days += 1
            if d > today:
                future_plan_missing += 1

        updated_at = rec.get("updated_at")
        if updated_at and (last_updated is None or str(updated_at) > str(last_updated)):
            last_updated = updated_at
            last_updated_by = rec.get("updated_by") or ""

    return {
        "planned_total": planned_total,
        "actual_to_date": actual_to_date,
        "landing_total": landing_total,
        "planned_input_days": planned_input_days,
        "actual_input_days": actual_input_days,
        "business_planned_days": business_planned_days,
        "landing_actual_days": landing_actual_days,
        "landing_plan_fill_days": landing_plan_fill_days,
        "landing_missing_days": landing_missing_days,
        "future_plan_missing": future_plan_missing,
        "elapsed_actual_missing": elapsed_actual_missing,
        "elapsed_days": elapsed_days,
        "month_days": len(days),
        "last_updated": last_updated,
        "last_updated_by": last_updated_by,
    }


def _forecast_summary(facility, target_ym, days, daily_by_date, today, available_yms,
                      revenue_index, has_revenue, sga_index, has_sga,
                      usage_by_name, receipt_by_subunit, forecast_usage_index=None):
    daily = _daily_metrics(days, daily_by_date, today)
    unit = _unit_price_basis(
        facility, target_ym, available_yms, revenue_index,
        has_revenue, usage_by_name, receipt_by_subunit, forecast_usage_index,
    )
    sga = _sga_basis(facility, target_ym, sga_index, has_sga)
    unit_price = unit["unit_price"]
    sga_forecast = sga["forecast"]

    planned_revenue = daily["planned_total"] * unit_price if unit_price is not None else None
    current_revenue = daily["actual_to_date"] * unit_price if unit_price is not None else None
    landing_revenue = daily["landing_total"] * unit_price if unit_price is not None else None
    planned_profit = planned_revenue - sga_forecast if planned_revenue is not None and sga_forecast is not None else None
    landing_profit = landing_revenue - sga_forecast if landing_revenue is not None and sga_forecast is not None else None

    planned_rate = _profit_rate(planned_profit, planned_revenue)
    landing_rate = _profit_rate(landing_profit, landing_revenue)
    elapsed_reference_sga = None
    if sga_forecast is not None and daily["month_days"]:
        elapsed_reference_sga = sga_forecast * min(daily["elapsed_days"], daily["month_days"]) / daily["month_days"]

    return {
        "facility": facility,
        "daily": daily,
        "unit": unit,
        "sga": sga,
        "planned_usage": daily["planned_total"],
        "actual_usage": daily["actual_to_date"],
        "landing_usage": daily["landing_total"],
        "usage_diff": daily["landing_total"] - daily["planned_total"],
        "planned_revenue": planned_revenue,
        "current_revenue": current_revenue,
        "landing_revenue": landing_revenue,
        "revenue_diff": None if planned_revenue is None or landing_revenue is None else landing_revenue - planned_revenue,
        "planned_sga": sga_forecast,
        "landing_sga": sga_forecast,
        "elapsed_reference_sga": elapsed_reference_sga,
        "sga_diff": 0 if sga_forecast is not None else None,
        "planned_profit": planned_profit,
        "landing_profit": landing_profit,
        "profit_diff": None if planned_profit is None or landing_profit is None else landing_profit - planned_profit,
        "planned_rate": planned_rate,
        "landing_rate": landing_rate,
        "rate_diff": None if planned_rate is None or landing_rate is None else landing_rate - planned_rate,
    }


def _summary_status(summary):
    status = []
    if summary["landing_profit"] is not None and summary["landing_profit"] < 0:
        status.append("赤字予測")
    if summary["daily"]["elapsed_actual_missing"] > 0:
        status.append("実績未入力")
    if summary["unit"]["unit_price"] is None:
        status.append("単価未算出")
    if summary["sga"]["forecast"] is None:
        status.append("販管費未算出")
    return " / ".join(status) if status else "順調"


def _render_profit_rate_alert(summary):
    landing_profit = summary.get("landing_profit")
    landing_rate = summary.get("landing_rate")
    if landing_profit is None and landing_rate is None:
        return

    if landing_profit is not None and landing_profit < 0:
        st.error(
            "赤字見込みです。営業・利用調整を最優先で動く必要があります。"
            "新規利用、追加利用、欠席フォロー、売上単価、販管費の根拠をすぐ確認してください。"
        )
        return

    if landing_rate is None:
        return

    if landing_rate < 5:
        st.warning(
            f"着地予測利益率が {_fmt_pct(landing_rate)} です。5%を下回っています。"
            "早急に対策を講じてください。利用回数の上積み、追加利用の声かけ、経費の見直しを確認しましょう。"
        )
    elif landing_rate < 10:
        st.warning(
            f"着地予測利益率が {_fmt_pct(landing_rate)} です。10%を下回っています。"
            "月末までに利益を守る対策を講じてください。利用予定、欠席見込み、販管費を確認しましょう。"
        )


def _render_forecast_guidance(summary):
    daily = summary["daily"]
    sga_label = summary["sga"].get("source_label") or "直近6カ月平均"
    current_sga_note = ""
    if summary.get("elapsed_reference_sga") is not None:
        current_sga_note = (
            f"<li>経過日ベースの参考販管費は <strong>{html.escape(_fmt_k_yen(summary['elapsed_reference_sga']))}</strong> です。"
            "確定費用ではなく、月間販管費予測を経過日で割った参考値です。</li>"
        )

    attention_items = []
    if daily["elapsed_actual_missing"] > 0:
        attention_items.append(
            f"営業日で実績が未入力の日が <strong>{daily['elapsed_actual_missing']}日</strong> あります。"
            "月末着地予測では予定人数で補っています。"
        )
    if summary["unit"]["unit_price"] is None:
        attention_items.append("売上単価を算出できていません。損益データと延べ利用回数を確認してください。")
    if summary["sga"]["forecast"] is None:
        attention_items.append("販管費予測を算出できていません。損益データの取込状況を確認してください。")
    if not attention_items:
        attention_items.append("入力と損益データに大きな不足はありません。日々の実績入力を続けてください。")
    attention_html = "".join(f"<li>{item}</li>" for item in attention_items)

    st.markdown(
        f"""
        <div class="forecast-guide-box">
            <div class="forecast-guide-title">補足事項：入力と数値の見方</div>
            <div class="forecast-guide-grid">
                <div class="forecast-guide-section">
                    <strong>操作</strong>
                    <ul>
                        <li>予定は月初・事前に入れる計画です。営業日は <b>0〜30</b>、営業なしは <b>－</b> を選びます。</li>
                        <li>実績は日々入力します。未入力は <b>－</b>、実績0人は <b>0</b> です。</li>
                        <li>高速入力モードは、入力後に <b>変更分をまとめて保存</b> を押すと保存されます。</li>
                    </ul>
                </div>
                <div class="forecast-guide-section">
                    <strong>数値の見方</strong>
                    <ul>
                        <li>着地予測は、入力済み実績を優先し、未入力日は予定人数で補って計算します。</li>
                        <li>金額は <b>千円表示・端数切り捨て</b>、1人売上単価と平均経費単価は <b>円/回</b> です。</li>
                        <li>販管費予測は <b>{html.escape(sga_label)}</b> です。売上単価は直近3カ月相当の売上と利用回数から算出します。</li>
                        {current_sga_note}
                    </ul>
                </div>
                <div class="forecast-guide-section attention">
                    <strong>今月の注意</strong>
                    <ul>
                        {attention_html}
                    </ul>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric_card(title, rows, accent="#7fb8df"):
    body = "".join(
        "<div class='forecast-metric-row'>"
        f"<span>{html.escape(label)}</span>"
        f"<strong class='{css}'>{html.escape(value)}</strong>"
        "</div>"
        for label, value, css in rows
    )
    st.markdown(
        f"""
        <div class="forecast-card" style="border-top-color:{accent};">
            <div class="forecast-card-title">{html.escape(title)}</div>
            {body}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_summary_cards(summary):
    profit_css = "warn" if summary["planned_profit"] is not None and summary["planned_profit"] < 0 else ""
    landing_profit_css = "warn" if summary["landing_profit"] is not None and summary["landing_profit"] < 0 else ""
    sga_css = "cost"
    diff_profit_css = "good" if (summary["profit_diff"] or 0) > 0 else ("warn" if (summary["profit_diff"] or 0) < 0 else "")
    diff_revenue_css = "good" if (summary["revenue_diff"] or 0) > 0 else ("warn" if (summary["revenue_diff"] or 0) < 0 else "")

    c1, c2, c3 = st.columns(3)
    with c1:
        _metric_card(
            "予定",
            [
                ("月間予定延べ利用回数", _fmt_count(summary["planned_usage"]), ""),
                ("予定売上", _fmt_k_yen(summary["planned_revenue"]), ""),
                ("予定販管費", _fmt_k_yen(summary["planned_sga"]), sga_css),
                ("予定利益", _fmt_k_yen(summary["planned_profit"]), profit_css),
                ("予定利益率", _fmt_pct(summary["planned_rate"]), profit_css),
            ],
            "#f4a6b8",
        )
    with c2:
        _metric_card(
            "現時点実績・着地予測",
            [
                ("現時点の実績延べ利用回数", _fmt_count(summary["actual_usage"]), ""),
                ("月末着地予測利用回数", _fmt_count(summary["landing_usage"]), ""),
                ("現時点実績売上", _fmt_k_yen(summary["current_revenue"]), ""),
                ("着地予測売上", _fmt_k_yen(summary["landing_revenue"]), ""),
                ("着地予測販管費", _fmt_k_yen(summary["landing_sga"]), sga_css),
                ("着地予測利益", _fmt_k_yen(summary["landing_profit"]), landing_profit_css),
                ("着地予測利益率", _fmt_pct(summary["landing_rate"]), landing_profit_css),
            ],
            "#7fb8df",
        )
    with c3:
        _metric_card(
            "差異（着地予測 - 予定）",
            [
                ("利用回数差", _fmt_diff_count(summary["usage_diff"]), "good" if summary["usage_diff"] > 0 else ("warn" if summary["usage_diff"] < 0 else "")),
                ("売上差", _fmt_k_yen(summary["revenue_diff"]), diff_revenue_css),
                ("利益差", _fmt_k_yen(summary["profit_diff"]), diff_profit_css),
                ("利益率差", "－" if summary["rate_diff"] is None else f"{summary['rate_diff']:+.1f}%", diff_profit_css),
            ],
            "#a7d9a0",
        )


def _summary_line(label, value, tone="", note=""):
    note_html = f"<small>{html.escape(note)}</small>" if note else ""
    return (
        "<div class='forecast-summary-line'>"
        f"<span>{html.escape(label)}</span>"
        f"<strong class='{html.escape(tone)}'>{html.escape(value)}</strong>"
        f"{note_html}"
        "</div>"
    )


def _summary_group(title, subtitle, rows, tone=""):
    body = "".join(_summary_line(*row) for row in rows)
    return (
        f"<div class='forecast-summary-card {html.escape(tone)}'>"
        f"<div class='forecast-summary-head'>"
        f"<strong>{html.escape(title)}</strong>"
        f"<span>{html.escape(subtitle)}</span>"
        "</div>"
        f"{body}"
        "</div>"
    )


def _render_top_kpis(summary):
    unit = summary["unit"]
    daily = summary["daily"]
    planned_profit_tone = "warn" if summary["planned_profit"] is not None and summary["planned_profit"] < 0 else ""
    landing_profit_tone = "warn" if summary["landing_profit"] is not None and summary["landing_profit"] < 0 else ""
    usage_diff_tone = "good" if summary["usage_diff"] > 0 else ("warn" if summary["usage_diff"] < 0 else "")
    revenue_diff_tone = "good" if (summary["revenue_diff"] or 0) > 0 else ("warn" if (summary["revenue_diff"] or 0) < 0 else "")
    profit_diff_tone = "good" if (summary["profit_diff"] or 0) > 0 else ("warn" if (summary["profit_diff"] or 0) < 0 else "")
    expense_unit = (
        summary["landing_sga"] / summary["landing_usage"]
        if summary["landing_sga"] is not None and summary["landing_usage"]
        else None
    )
    unit_rows_note = _unit_basis_average_note(unit)
    unit_basis_tone = "" if unit["unit_price"] is not None else "warn"
    groups = [
        _summary_group(
            "予定",
            "月初・事前に入力した計画",
            [
                (
                    "予定利用回数",
                    _fmt_count(summary["planned_usage"]),
                    "",
                    f"営業日 {daily['business_planned_days']}日 / － {daily['landing_missing_days']}日",
                ),
                ("予定売上", _fmt_k_yen(summary["planned_revenue"]), ""),
                ("予定販管費", _fmt_k_yen(summary["planned_sga"]), "cost"),
                ("予定利益", _fmt_k_yen(summary["planned_profit"]), planned_profit_tone),
                ("予定利益率", _fmt_pct(summary["planned_rate"]), planned_profit_tone),
            ],
            "plan",
        ),
        _summary_group(
            "実績・着地予測",
            "実績優先 + 未入力日は予定",
            [
                ("現時点実績", _fmt_count(summary["actual_usage"]), "", f"実績入力 {daily['actual_input_days']}/{daily['elapsed_days']}日"),
                (
                    "月末予測利用回数",
                    _fmt_count(summary["landing_usage"]),
                    "",
                    f"実績 {daily['landing_actual_days']}日 + 予定補完 {daily['landing_plan_fill_days']}日",
                ),
                ("現時点実績売上", _fmt_k_yen(summary["current_revenue"]), ""),
                ("着地予測売上", _fmt_k_yen(summary["landing_revenue"]), ""),
                ("着地予測利益", _fmt_k_yen(summary["landing_profit"]), landing_profit_tone),
                ("着地予測利益率", _fmt_pct(summary["landing_rate"]), landing_profit_tone),
            ],
            "actual",
        ),
        _summary_group(
            "差異",
            "着地予測 - 予定",
            [
                ("利用回数差", _fmt_diff_count(summary["usage_diff"]), usage_diff_tone),
                ("売上差", _fmt_diff_k_yen(summary["revenue_diff"]), revenue_diff_tone),
                ("利益差", _fmt_diff_k_yen(summary["profit_diff"]), profit_diff_tone),
                ("利益率差", _fmt_diff_pct(summary["rate_diff"]), profit_diff_tone),
            ],
            "diff",
        ),
        _summary_group(
            "単価・根拠",
            "予測計算に使う基準",
            [
                ("1人売上単価", _fmt_unit_yen(unit["unit_price"]), "unit", unit["source_label"]),
                ("平均経費単価", _fmt_unit_yen(expense_unit), "cost", "着地予測販管費 ÷ 月末予測利用回数"),
                ("売上単価の元データ", "確認可" if unit["unit_price"] is not None else "未算出", unit_basis_tone, unit_rows_note),
                ("販管費予測", _fmt_k_yen(summary["planned_sga"]), "cost", summary["sga"].get("source_label", "直近6カ月平均")),
            ],
            "unit",
        ),
    ]
    st.markdown(
        "<div class='forecast-summary-grid'>" + "".join(groups) + "</div>",
        unsafe_allow_html=True,
    )


def _detail_basis_tables(summary):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 売上単価の根拠")
        unit = summary["unit"]
        if unit["unit_price"] is None:
            st.warning("売上単価の算出に必要な利用実績が登録されていません。損益データと延べ利用回数を確認してください。")
        else:
            st.caption(
                f"{unit['source_label']} ／ 採用単価: "
                f"{int(unit['unit_price']):,} 円/回 ／ {_unit_basis_average_note(unit)}"
            )
        rows = [
            {
                "参照年月": r["ym"],
                "売上(千円)": _yen_to_thousand(r["revenue"]),
                "延べ利用回数": r["usage"],
                "取得元・状態": r["source"],
            }
            for r in unit["rows"]
        ]
        rows.extend(
            {
                "参照年月": r["ym"],
                "売上(千円)": None if r.get("revenue") is None else _yen_to_thousand(r["revenue"]),
                "延べ利用回数": r.get("usage"),
                "取得元・状態": f"除外: {r['reason']}",
            }
            for r in unit.get("excluded_rows", [])
        )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    with c2:
        st.markdown("#### 販管費予測の根拠")
        sga = summary["sga"]
        if sga["forecast"] is None:
            st.warning("販管費予測に使用できる過去データがありません。損益データの取込状況を確認してください。")
        else:
            st.caption(f"{sga.get('source_label', '直近6カ月平均')} ／ 採用平均: {_fmt_k_yen(sga['forecast'])}")
        rows = [
            {
                "参照年月": r["ym"],
                "販管費(千円)": None if r["amount"] is None else _yen_to_thousand(r["amount"]),
                "状態": r["status"],
            }
            for r in sga["rows"]
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_input_status(summary):
    d = summary["daily"]
    cols = st.columns(6)
    cols[0].metric("営業日数", f"{d['business_planned_days']} 日")
    cols[1].metric("対象月日数", f"{d['month_days']} 日")
    cols[2].metric("実績入力済み", f"{d['actual_input_days']}/{d['elapsed_days']} 日")
    cols[3].metric("－の日数", f"{d['landing_missing_days']} 日")
    cols[4].metric("実績未入力", f"{d['elapsed_actual_missing']} 日")
    cols[5].metric("最終更新", d["last_updated"] or "－")
    if d["last_updated_by"]:
        st.caption(f"最終更新者: {d['last_updated_by']}")


def _records_to_editor_df(days, daily_by_date):
    rows = []
    for d in days:
        rec = daily_by_date.get(d) or {}
        label = _holiday_label(d)
        rows.append({
            "日付": d.isoformat(),
            "曜日": JP_WEEKDAYS[d.weekday()],
            "区分": label,
            "予定人数": rec.get("planned_users"),
            "実績人数": rec.get("actual_users"),
        })
    return pd.DataFrame(rows)


def _save_cell(facility, d, field, value, current_user):
    return db.save_revenue_forecast_value(
        facility_key=facility["key"],
        facility_label=facility["label"],
        pl_subunit_id=facility["primary_subunit_id"],
        pl_subunit_ids=facility["subunit_ids"],
        target_date=d.isoformat(),
        field_name=field,
        value=value,
        updated_by_user=current_user.get("email"),
    )


def _handle_bulk_tools(facility, target_ym, days):
    st.caption("必要な場合だけ開いて使う入力補助です。通常はカレンダーで日別に入力してください。")
    current = auth.current_user() or {}
    with st.expander("入力補助を開く", expanded=False):
        st.caption("一括操作は新しい予測用テーブルだけを更新します。損益データや売上明細は変更しません。")
        c1, c2, c3 = st.columns(3)
        with c1:
            weekday_value = st.selectbox(
                "平日の予定人数",
                list(range(31)),
                format_func=lambda v: f"{v}人",
                key=f"weekday_bulk_{target_ym}_{facility['key']}",
            )
            weekday_ok = st.checkbox(
                "平日の予定人数を一括設定する",
                key=f"weekday_bulk_ok_{target_ym}_{facility['key']}",
            )
            if st.button("平日に反映", disabled=not weekday_ok, key=f"weekday_bulk_run_{target_ym}_{facility['key']}"):
                saved = 0
                for d in days:
                    if _is_business_weekday(d):
                        result = _save_cell(facility, d, "planned_users", weekday_value, current)
                        saved += 1 if result.get("saved") else 0
                st.session_state["forecast_saved_notice"] = f"平日の予定人数を保存しました（{saved}件）。"
                st.rerun()

        with c2:
            prev_ok = st.checkbox(
                "前月の予定をこの月へコピーする",
                key=f"copy_prev_ok_{target_ym}_{facility['key']}",
            )
            if st.button("前月予定をコピー", disabled=not prev_ok, key=f"copy_prev_run_{target_ym}_{facility['key']}"):
                prev_ym = _ym_shift(target_ym, -1)
                prev_start, prev_end = _month_bounds(prev_ym)
                prev_records = db.list_revenue_forecast_daily(prev_start, prev_end, [facility["key"]])
                by_day = {
                    date.fromisoformat(r["target_date"]).day: r.get("planned_users")
                    for r in prev_records
                    if r.get("planned_users") is not None
                }
                saved = 0
                for d in days:
                    if d.day in by_day:
                        result = _save_cell(facility, d, "planned_users", by_day[d.day], current)
                        saved += 1 if result.get("saved") else 0
                st.session_state["forecast_saved_notice"] = f"前月予定をコピーしました（{saved}件）。"
                st.rerun()

        with c3:
            clear_ok = st.checkbox(
                "予定人数を全日クリアする",
                key=f"clear_plan_ok_{target_ym}_{facility['key']}",
            )
            if st.button("予定をクリア", disabled=not clear_ok, key=f"clear_plan_run_{target_ym}_{facility['key']}"):
                saved = 0
                for d in days:
                    result = _save_cell(facility, d, "planned_users", None, current)
                    saved += 1 if result.get("saved") else 0
                st.session_state["forecast_saved_notice"] = f"予定人数をクリアしました（{saved}件）。"
                st.rerun()


def _format_user_option(value):
    if value is None or value == FORECAST_EMPTY_OPTION:
        return "－"
    return str(int(value))


def _select_user_value(value):
    if value == FORECAST_EMPTY_OPTION:
        return None
    return _as_int_or_none(value)


def _option_index(value):
    value = _as_int_or_none(value)
    return 0 if value is None else int(value) + 1


def _calendar_day_classes(d, month_number, holidays, today):
    classes = []
    if d.month != month_number:
        classes.append("outside")
    if d in holidays:
        classes.append("holiday")
    elif d.weekday() == 6:
        classes.append("sunday")
    elif d.weekday() == 5:
        classes.append("saturday")
    if d == today:
        classes.append("today")
    return " ".join(classes)


def _render_month_outside_cell(d):
    st.markdown(
        f"""
        <div class="forecast-calendar-day outside">
            <div class="forecast-calendar-date">{d.day}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_month_day_cell(facility, d, rec, current_user, auto_save=True, key_prefix="forecast"):
    holidays = _japanese_holidays(d.year)
    classes = _calendar_day_classes(d, d.month, holidays, _today_jst())
    holiday_name = holidays.get(d, "")
    label = holiday_name or _holiday_label(d)
    planned = _as_int_or_none(rec.get("planned_users")) if rec else None
    actual = _as_int_or_none(rec.get("actual_users")) if rec else None
    if planned is None:
        classes = f"{classes} closed-day".strip()

    st.markdown(
        f"""
        <div class="forecast-calendar-day {classes}">
            <div class="forecast-calendar-date-row">
                <span class="forecast-calendar-date">{d.day}</span>
                <span class="forecast-calendar-label">{html.escape(label)}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='forecast-select-label'>予定</div>",
        unsafe_allow_html=True,
    )
    selected_planned = st.selectbox(
        f"{d.isoformat()} 予定人数",
        FORECAST_USER_OPTIONS,
        index=_option_index(planned),
        format_func=_format_user_option,
        key=f"{key_prefix}_plan_choice_{facility['key']}_{d.isoformat()}",
        label_visibility="collapsed",
    )
    new_planned = _select_user_value(selected_planned)
    st.markdown(
        "<div class='forecast-select-label actual'>実績</div>",
        unsafe_allow_html=True,
    )
    selected_actual = st.selectbox(
        f"{d.isoformat()} 実績人数",
        FORECAST_USER_OPTIONS,
        index=_option_index(actual),
        format_func=_format_user_option,
        key=f"{key_prefix}_actual_choice_{facility['key']}_{d.isoformat()}",
        label_visibility="collapsed",
    )
    new_actual = _select_user_value(selected_actual)

    saved_count = 0
    changes = []
    if new_planned != planned:
        if auto_save:
            result = _save_cell(facility, d, "planned_users", new_planned, current_user)
            saved_count += 1 if result.get("saved") else 0
        else:
            changes.append((d, "planned_users", new_planned))
    if new_actual != actual:
        if auto_save:
            result = _save_cell(facility, d, "actual_users", new_actual, current_user)
            saved_count += 1 if result.get("saved") else 0
        else:
            changes.append((d, "actual_users", new_actual))
    return saved_count, changes


def _render_usage_side_panel(summary):
    daily = summary["daily"]
    business_days = daily["business_planned_days"]
    avg_users = summary["landing_usage"] / business_days if business_days else None
    expense_unit = (
        summary["landing_sga"] / summary["landing_usage"]
        if summary["landing_sga"] is not None and summary["landing_usage"]
        else None
    )
    status_rows = [
        ("月間予定人数", _fmt_count(summary["planned_usage"])),
        ("月末予測人数", _fmt_count(summary["landing_usage"])),
        ("予測内訳", f"実績{daily['landing_actual_days']}日 + 予定{daily['landing_plan_fill_days']}日"),
        ("今月の営業予定日数", f"{business_days} 日"),
        ("1日平均利用人数", _fmt_average(avg_users)),
        ("1人売上単価", _fmt_unit_yen(summary["unit"]["unit_price"])),
        ("平均経費単価", _fmt_unit_yen(expense_unit)),
        ("－の日数", f"{daily['landing_missing_days']} 日"),
        ("実績未入力", f"{daily['elapsed_actual_missing']} 日"),
        ("最終更新", daily["last_updated"] or "－"),
    ]
    body = "".join(
        "<div class='forecast-side-row'>"
        f"<span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong>"
        "</div>"
        for label, value in status_rows
    )
    updated_by = html.escape(daily["last_updated_by"] or "－")
    st.markdown(
        f"""
        <div class="forecast-side-panel">
            <div class="forecast-side-title">利用状況</div>
            {body}
            <div class="forecast-side-updated">最終更新者: {updated_by}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_calendar_grid(facility, target_ym, days, daily_by_date, current_user,
                          auto_save=True, key_prefix="forecast"):
    year, month = [int(v) for v in target_ym.split("-")]
    month_calendar = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
    st.markdown(
        f"""
        <div class="forecast-calendar-title">
            <strong>{month}月</strong>
            <span>{year}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    header_cols = st.columns(7, gap="small")
    for idx, label in enumerate(CALENDAR_WEEKDAYS):
        css = "sunday" if idx == 0 else ("saturday" if idx == 6 else "")
        header_cols[idx].markdown(
            f"<div class='forecast-calendar-weekday {css}'>{label}</div>",
            unsafe_allow_html=True,
        )

    saved_count = 0
    changes = []
    try:
        for week in month_calendar:
            cols = st.columns(7, gap="small")
            for col, d in zip(cols, week):
                with col:
                    with st.container(border=True):
                        if d.month != month:
                            _render_month_outside_cell(d)
                            continue
                        rec = daily_by_date.get(d) or {}
                        cell_saved, cell_changes = _render_month_day_cell(
                            facility, d, rec, current_user,
                            auto_save=auto_save,
                            key_prefix=key_prefix,
                        )
                        saved_count += cell_saved
                        changes.extend(cell_changes)
    except Exception:
        st.error("入力内容を保存できませんでした。通信状況を確認して再度入力してください。")
        return 0, []

    return saved_count, changes


def _render_calendar_area(facility, target_ym, days, daily_by_date, current_user,
                          summary=None, auto_save=True, key_prefix="forecast"):
    if summary is None:
        return _render_calendar_grid(
            facility, target_ym, days, daily_by_date, current_user,
            auto_save=auto_save,
            key_prefix=key_prefix,
        )

    calendar_col, side_col = st.columns([4.15, 1.18], gap="medium")
    with calendar_col:
        saved_count, changes = _render_calendar_grid(
            facility, target_ym, days, daily_by_date, current_user,
            auto_save=auto_save,
            key_prefix=key_prefix,
        )
    with side_col:
        _render_usage_side_panel(summary)
    return saved_count, changes


def _render_daily_editor(facility, target_ym, days, daily_by_date, summary=None):
    _handle_bulk_tools(facility, target_ym, days)
    st.markdown("#### 日別入力カレンダー")
    fast_mode = st.toggle(
        "高速入力モード（まとめて保存）",
        value=True,
        key=f"forecast_fast_mode_{target_ym}_{facility['key']}",
        help="オンにすると、プルダウンを何日分も続けて選んでから一括保存できます。",
    )
    if fast_mode:
        st.caption("予定・実績を続けて選んだあと、「変更分をまとめて保存」を押してください。予定欄の「－」は営業日ではない日、「0」は営業日の利用0人です。")
    else:
        st.caption("選択するたびに自動保存します。予定欄の「－」は営業日ではない日、「0」は営業日の利用0人です。")

    current = auth.current_user() or {}

    if fast_mode:
        with st.form(f"forecast_fast_form_{target_ym}_{facility['key']}"):
            top_submitted = st.form_submit_button(
                "変更分をまとめて保存",
                type="primary",
                use_container_width=True,
                key=f"forecast_fast_save_top_{target_ym}_{facility['key']}",
            )
            st.caption("入力後はこのボタン、またはカレンダー下の同じボタンで保存できます。")
            _, changes = _render_calendar_area(
                facility, target_ym, days, daily_by_date, current,
                summary=summary,
                auto_save=False,
                key_prefix="forecast_fast",
            )
            bottom_submitted = st.form_submit_button(
                "変更分をまとめて保存",
                type="primary",
                use_container_width=True,
                key=f"forecast_fast_save_bottom_{target_ym}_{facility['key']}",
            )
            submitted = top_submitted or bottom_submitted
        if submitted:
            saved_count = 0
            try:
                for d, field_name, value in changes:
                    result = _save_cell(facility, d, field_name, value, current)
                    saved_count += 1 if result.get("saved") else 0
            except Exception:
                st.error("入力内容を保存できませんでした。通信状況を確認して再度入力してください。")
                return
            st.session_state["forecast_saved_notice"] = (
                f"変更分をまとめて保存しました（{saved_count}件）。"
                if saved_count else "変更はありませんでした。"
            )
            st.rerun()
        return

    saved_count, _ = _render_calendar_area(
        facility, target_ym, days, daily_by_date, current,
        summary=summary,
        auto_save=True,
        key_prefix="forecast_auto",
    )

    if saved_count:
        notice = f"入力内容を自動保存しました（{saved_count}件）。"
        try:
            st.toast(notice)
        except Exception:
            st.caption(notice)


def _summary_to_row(summary):
    d = summary["daily"]
    return {
        "施設名": summary["facility"]["label"],
        "月間予定延べ利用回数": summary["planned_usage"],
        "月末着地予測利用回数": summary["landing_usage"],
        "利用回数差": summary["usage_diff"],
        "予定売上(千円)": _yen_to_thousand(summary["planned_revenue"]),
        "着地予測売上(千円)": _yen_to_thousand(summary["landing_revenue"]),
        "売上差(千円)": _yen_to_thousand(summary["revenue_diff"]),
        "予定販管費(千円)": _yen_to_thousand(summary["planned_sga"]),
        "着地予測販管費(千円)": _yen_to_thousand(summary["landing_sga"]),
        "予定利益(千円)": _yen_to_thousand(summary["planned_profit"]),
        "着地予測利益(千円)": _yen_to_thousand(summary["landing_profit"]),
        "利益差(千円)": _yen_to_thousand(summary["profit_diff"]),
        "予定利益率": summary["planned_rate"],
        "着地予測利益率": summary["landing_rate"],
        "営業日数": f"{d['business_planned_days']}日",
        "－の日数": f"{d['landing_missing_days']}日",
        "実績入力状況": f"{d['actual_input_days']}/{d['elapsed_days']}日",
        "最終更新日": d["last_updated"] or "",
        "状態": _summary_status(summary),
    }


def _overview_value_class(value, positive_good=True):
    if value is None or value == 0:
        return ""
    if positive_good:
        return "good" if value > 0 else "warn"
    return "warn" if value > 0 else "good"


def _overview_status_badges(summary):
    d = summary["daily"]
    badges = []
    if summary["landing_profit"] is not None and summary["landing_profit"] < 0:
        badges.append(("warn", "赤字予測"))
    badges.append(("note", f"営業日 {d['business_planned_days']}日"))
    if d["elapsed_actual_missing"] > 0:
        badges.append(("note", f"実績未入力 {d['elapsed_actual_missing']}日"))
    if summary["unit"]["unit_price"] is None:
        badges.append(("warn", "単価未算出"))
    if not badges:
        badges.append(("good", "入力確認OK"))
    return "".join(
        f"<span class='forecast-overview-badge {html.escape(css)}'>{html.escape(text)}</span>"
        for css, text in badges[:3]
    )


def _overview_line(label, value, css="", note=""):
    note_html = f"<small>{html.escape(note)}</small>" if note else ""
    return (
        "<div class='forecast-overview-line'>"
        f"<span>{html.escape(label)}</span>"
        f"<strong class='{html.escape(css)}'>{html.escape(value)}</strong>"
        f"{note_html}"
        "</div>"
    )


def _overview_facility_card(summary):
    d = summary["daily"]
    profit_class = "warn" if summary["landing_profit"] is not None and summary["landing_profit"] < 0 else ""
    revenue_diff_class = _overview_value_class(summary["revenue_diff"], positive_good=True)
    profit_diff_class = _overview_value_class(summary["profit_diff"], positive_good=True)
    usage_diff_class = _overview_value_class(summary["usage_diff"], positive_good=True)
    card_class = "loss" if profit_class == "warn" else ("missing" if summary["unit"]["unit_price"] is None else "steady")
    last_updated = d["last_updated"] or "－"
    body = "".join([
        _overview_line("予定利用", _fmt_count(summary["planned_usage"])),
        _overview_line("月末予測", _fmt_count(summary["landing_usage"])),
        _overview_line("利用差", _fmt_diff_count(summary["usage_diff"]), usage_diff_class),
        _overview_line("予定売上", _fmt_k_yen(summary["planned_revenue"])),
        _overview_line("着地売上", _fmt_k_yen(summary["landing_revenue"])),
        _overview_line("売上差", _fmt_diff_k_yen(summary["revenue_diff"]), revenue_diff_class),
        _overview_line("予定利益", _fmt_k_yen(summary["planned_profit"]), "warn" if summary["planned_profit"] is not None and summary["planned_profit"] < 0 else ""),
        _overview_line("着地利益", _fmt_k_yen(summary["landing_profit"]), profit_class),
        _overview_line("利益差", _fmt_diff_k_yen(summary["profit_diff"]), profit_diff_class),
    ])
    footer = "".join([
        _overview_line("1人売上単価", _fmt_unit_yen(summary["unit"]["unit_price"]), "unit"),
        _overview_line("営業日数", f"{d['business_planned_days']}日"),
        _overview_line("－の日数", f"{d['landing_missing_days']}日"),
        _overview_line("実績入力", f"{d['actual_input_days']}/{d['elapsed_days']}日"),
        _overview_line("最終更新", str(last_updated)),
    ])
    return (
        f"<section class='forecast-overview-card {card_class}'>"
        "<div class='forecast-overview-head'>"
        f"<strong>{html.escape(summary['facility']['label'])}</strong>"
        f"<div>{_overview_status_badges(summary)}</div>"
        "</div>"
        f"<div class='forecast-overview-body'>{body}</div>"
        f"<div class='forecast-overview-footer'>{footer}</div>"
        "</section>"
    )


def _overview_total_item(label, value, note="", css=""):
    note_html = f"<span>{html.escape(note)}</span>" if note else ""
    return (
        "<div class='forecast-total-item'>"
        f"<small>{html.escape(label)}</small>"
        f"<strong class='{html.escape(css)}'>{html.escape(value)}</strong>"
        f"{note_html}"
        "</div>"
    )


def _render_all_facilities(summaries):
    st.markdown("### 全施設一覧")
    rows = [_summary_to_row(summary) for summary in summaries]
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("表示できる施設がありません。")
        return

    total_planned_revenue = sum(s["planned_revenue"] or 0 for s in summaries)
    total_landing_revenue = sum(s["landing_revenue"] or 0 for s in summaries)
    total_planned_sga = sum(s["planned_sga"] or 0 for s in summaries)
    total_landing_sga = sum(s["landing_sga"] or 0 for s in summaries)
    total_planned_profit = total_planned_revenue - total_planned_sga
    total_landing_profit = total_landing_revenue - total_landing_sga
    total_revenue_diff = total_landing_revenue - total_planned_revenue
    total_profit_diff = total_landing_profit - total_planned_profit
    total_planned_rate = _profit_rate(total_planned_profit, total_planned_revenue)
    total_landing_rate = _profit_rate(total_landing_profit, total_landing_revenue)

    total_html = "".join([
        _overview_total_item("予定売上合計", _fmt_k_yen(total_planned_revenue)),
        _overview_total_item("着地予測売上合計", _fmt_k_yen(total_landing_revenue), _fmt_diff_k_yen(total_revenue_diff), _overview_value_class(total_revenue_diff, True)),
        _overview_total_item("着地予測利益合計", _fmt_k_yen(total_landing_profit), _fmt_diff_k_yen(total_profit_diff), _overview_value_class(total_profit_diff, True)),
        _overview_total_item("全体着地予測利益率", _fmt_pct(total_landing_rate), "利益合計 ÷ 売上合計", "warn" if total_landing_rate is not None and total_landing_rate < 0 else ""),
    ])
    st.markdown(f"<div class='forecast-total-strip'>{total_html}</div>", unsafe_allow_html=True)

    with st.expander("全施設合計の詳細", expanded=False):
        total_rows = pd.DataFrame([{
            "予定売上合計(千円)": _yen_to_thousand(total_planned_revenue),
            "着地予測売上合計(千円)": _yen_to_thousand(total_landing_revenue),
            "売上差合計(千円)": _yen_to_thousand(total_revenue_diff),
            "予定販管費合計(千円)": _yen_to_thousand(total_planned_sga),
            "着地予測販管費合計(千円)": _yen_to_thousand(total_landing_sga),
            "予定利益合計(千円)": _yen_to_thousand(total_planned_profit),
            "着地予測利益合計(千円)": _yen_to_thousand(total_landing_profit),
            "利益差合計(千円)": _yen_to_thousand(total_profit_diff),
            "全体予定利益率": total_planned_rate,
            "全体着地予測利益率": total_landing_rate,
        }])
        st.dataframe(total_rows, width="stretch", hide_index=True)

    sort_options = {
        "利益差が小さい順（注意施設を上）": ("利益差(千円)", True),
        "着地利益が小さい順": ("着地予測利益(千円)", True),
        "売上差が大きい順": ("売上差(千円)", False),
        "利用回数差が大きい順": ("利用回数差", False),
        "月末予測利用が多い順": ("月末着地予測利用回数", False),
    }
    sort_label = st.selectbox("施設カードの並び替え", list(sort_options.keys()), index=0)
    sort_col, ascending = sort_options[sort_label]
    df = df.sort_values(sort_col, ascending=ascending, na_position="last")
    summary_by_label = {summary["facility"]["label"]: summary for summary in summaries}
    sorted_summaries = [summary_by_label[name] for name in df["施設名"].tolist() if name in summary_by_label]

    st.markdown("#### 施設別カード一覧")
    cards_html = "".join(_overview_facility_card(summary) for summary in sorted_summaries)
    st.markdown(f"<div class='forecast-overview-grid'>{cards_html}</div>", unsafe_allow_html=True)

    amount_cols = [c for c in df.columns if "(千円)" in c]
    pct_cols = ["予定利益率", "着地予測利益率"]

    def row_style(row):
        styles = [""] * len(row)
        if "赤字予測" in str(row.get("状態")):
            styles = ["background-color:#fff1f2"] * len(row)
        elif "実績未入力" in str(row.get("状態")):
            styles = ["background-color:#fff7ed"] * len(row)
        return styles

    formatters = {col: "{:,.0f}" for col in amount_cols}
    formatters.update({col: "{:.1f}%" for col in pct_cols})
    with st.expander("詳細テーブルを開く", expanded=False):
        st.dataframe(
            df.style.apply(row_style, axis=1).format(formatters, na_rep="－"),
            width="stretch",
            hide_index=True,
            height=520,
        )
    st.caption("差異は「着地予測 - 予定」です。売上差・利益差・利用回数差は、プラスを上振れとして見ます。")


def _set_target_month_state(year, month):
    st.session_state["forecast_target_year"] = int(year)
    st.session_state["forecast_target_month"] = int(month)


def _render_target_month_panel(today, year_options):
    if "forecast_target_year" not in st.session_state:
        st.session_state["forecast_target_year"] = today.year
    if "forecast_target_month" not in st.session_state:
        st.session_state["forecast_target_month"] = today.month

    current_year = int(st.session_state["forecast_target_year"])
    current_month = int(st.session_state["forecast_target_month"])
    if current_year not in year_options:
        current_year = today.year
        st.session_state["forecast_target_year"] = current_year
    if current_month < 1 or current_month > 12:
        current_month = today.month
        st.session_state["forecast_target_month"] = current_month

    with st.container(border=True):
        st.markdown(
            """
            <div class="forecast-control-title forecast-target-month-title">
                <span>1</span>
                <div>
                    <strong>対象年月を選ぶ</strong>
                    <small>見たい月を先に選びます。前月・翌月ボタンでも切り替えできます。</small>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        nav_cols = st.columns([1, 1, 1, 3.2], gap="small")
        if nav_cols[0].button("前月へ", key="forecast_prev_month", use_container_width=True):
            new_year, new_month = _shift_year_month(current_year, current_month, -1)
            if new_year in year_options:
                _set_target_month_state(new_year, new_month)
                current_year, current_month = new_year, new_month
        if nav_cols[1].button("今月へ", key="forecast_this_month", use_container_width=True):
            _set_target_month_state(today.year, today.month)
            current_year, current_month = today.year, today.month
        if nav_cols[2].button("翌月へ", key="forecast_next_month", use_container_width=True):
            new_year, new_month = _shift_year_month(current_year, current_month, 1)
            if new_year in year_options:
                _set_target_month_state(new_year, new_month)
                current_year, current_month = new_year, new_month
        nav_cols[3].markdown(
            f"<div class='forecast-selected-banner forecast-selected-month-banner'>選択中：{html.escape(_month_label(current_year, current_month))}</div>",
            unsafe_allow_html=True,
        )

        c_year, c_month, c_note = st.columns([1.1, 1.1, 3.2], gap="small")
        c_year.markdown(
            "<div class='forecast-target-select-marker'>対象年</div>",
            unsafe_allow_html=True,
        )
        target_year = c_year.selectbox(
            "対象年を選ぶ",
            year_options,
            key="forecast_target_year",
            help="対象年を選びます。",
            label_visibility="collapsed",
        )
        c_month.markdown(
            "<div class='forecast-target-select-marker month'>対象月</div>",
            unsafe_allow_html=True,
        )
        target_month_number = c_month.selectbox(
            "対象月を選ぶ",
            list(range(1, 13)),
            format_func=lambda m: f"{m}月",
            key="forecast_target_month",
            help="対象月を選びます。",
            label_visibility="collapsed",
        )
        c_note.markdown(
            "<div class='forecast-control-note'>過去月・当月・未来月を切り替えて、登録済みの予定人数・実績人数を確認できます。</div>",
            unsafe_allow_html=True,
        )
    return int(target_year), int(target_month_number)


def _render_view_mode_panel(forecast_profile):
    if forecast_profile:
        st.markdown(
            f"""
            <div class="forecast-locked-panel">
                <strong>施設専用ログイン</strong>
                <span>{html.escape(forecast_profile['facility_label'])} のカレンダー入力画面を表示します。</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return "施設別入力・予測"

    if "forecast_view_mode" not in st.session_state:
        st.session_state["forecast_view_mode"] = "全施設一覧"

    with st.container(border=True):
        st.markdown(
            """
            <div class="forecast-control-title">
                <span>2</span>
                <div>
                    <strong>表示する画面を選ぶ</strong>
                    <small>全体比較か、施設ごとのカレンダー入力かを選びます。</small>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c_all, c_detail = st.columns(2, gap="small")
        all_active = st.session_state["forecast_view_mode"] == "全施設一覧"
        detail_active = st.session_state["forecast_view_mode"] == "施設別入力・予測"
        if c_all.button(
            "全施設一覧を見る",
            key="forecast_view_all_button",
            type="primary" if all_active else "secondary",
            use_container_width=True,
        ):
            st.session_state["forecast_view_mode"] = "全施設一覧"
        c_all.caption("施設ごとの売上・利益・入力状況を一覧で比較します。")

        if c_detail.button(
            "施設カレンダーに入力する",
            key="forecast_view_detail_button",
            type="primary" if detail_active else "secondary",
            use_container_width=True,
        ):
            st.session_state["forecast_view_mode"] = "施設別入力・予測"
        c_detail.caption("選んだ施設の予定人数・実績人数を日別に入力します。")

    return st.session_state["forecast_view_mode"]


def _render_facility_select_panel(facilities):
    facility_labels = [f["label"] for f in facilities]
    current_label = st.session_state.get("forecast_facility_select")
    current_index = facility_labels.index(current_label) if current_label in facility_labels else 0
    with st.container(border=True):
        st.markdown(
            """
            <div class="forecast-control-title">
                <span>3</span>
                <div>
                    <strong>入力する施設を選ぶ</strong>
                    <small>施設を切り替えると、その施設のカレンダーと予測値を表示します。</small>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        select_col, note_col = st.columns([2.4, 2], gap="small")
        selected_label = select_col.selectbox(
            "施設を選ぶ",
            facility_labels,
            index=current_index,
            key="forecast_facility_select",
            help="カレンダー入力する施設を選びます。",
        )
        note_col.markdown(
            f"<div class='forecast-selected-banner'>選択中：{html.escape(selected_label)}</div>",
            unsafe_allow_html=True,
        )
    return next(f for f in facilities if f["label"] == selected_label)


def _page_css():
    st.markdown(
        """
        <style>
        .forecast-hero {
            padding: 18px 22px;
            border: 1px solid #e5edf4;
            border-radius: 8px;
            background: linear-gradient(90deg, #fff7fb 0%, #f4fbf7 48%, #f7fbff 100%);
            margin-bottom: 16px;
        }
        .forecast-hero h1 {
            margin: 0 0 6px 0;
            font-size: 1.7rem;
            color: #17324d;
        }
        .forecast-hero p {
            margin: 0;
            color: #5d6f82;
            font-size: 0.95rem;
        }
        .forecast-control-title {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            margin-bottom: 12px;
        }
        .forecast-control-title span {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 30px;
            height: 30px;
            border-radius: 999px;
            background: #e6f4ee;
            color: #167047;
            font-weight: 900;
            flex: 0 0 auto;
        }
        .forecast-control-title strong {
            display: block;
            color: #17324d;
            font-size: 1.05rem;
            line-height: 1.3;
        }
        .forecast-control-title small {
            display: block;
            color: #6b7c8d;
            font-size: 0.82rem;
            margin-top: 2px;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.forecast-target-month-title) {
            border: 2px solid #cfe5f4 !important;
            border-radius: 14px !important;
            background: #fbfdff !important;
            box-shadow: 0 2px 10px rgba(38, 74, 112, 0.06) !important;
        }
        .forecast-target-month-title {
            padding: 4px 2px 0;
        }
        .forecast-target-month-title span {
            width: 34px;
            height: 34px;
            background: #dff5ea;
            color: #0f7a4b;
            font-size: 1rem;
        }
        .forecast-target-month-title strong {
            font-size: 1.18rem;
            font-weight: 950;
        }
        .forecast-selected-banner {
            display: flex;
            align-items: center;
            min-height: 40px;
            border-radius: 8px;
            border: 1px solid #cfe5f4;
            background: #f4fbff;
            color: #17324d;
            font-weight: 850;
            padding: 9px 12px;
            margin-top: 1px;
        }
        .forecast-selected-month-banner {
            min-height: 47px;
            border: 2px solid #86c5ed;
            border-radius: 10px;
            background: #f4fbff;
            color: #0c2744;
            font-size: 1rem;
            font-weight: 950;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.06);
        }
        .forecast-target-select-marker {
            display: inline-flex;
            align-items: center;
            width: fit-content;
            min-height: 0;
            margin: 10px 0 6px;
            padding: 4px 10px;
            border: 1px solid #cfe5f4;
            border-radius: 999px;
            background: #f4fbff;
            color: #0c2744;
            font-size: 0.88rem;
            font-weight: 950;
            letter-spacing: 0;
        }
        .forecast-target-select-marker::after {
            content: "";
            display: none;
        }
        div[data-testid="stMarkdown"]:has(.forecast-target-select-marker) + div[data-testid="stSelectbox"] {
            padding: 0;
            border: 0;
            border-radius: 0;
            background: transparent;
            box-shadow: none;
        }
        div[data-testid="stMarkdown"]:has(.forecast-target-select-marker) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-height: 46px !important;
            border: 2px solid #86c5ed !important;
            border-radius: 10px !important;
            background: #ffffff !important;
            font-weight: 900 !important;
            box-shadow: 0 0 0 3px rgba(134, 197, 237, 0.12) !important;
        }
        .forecast-control-note {
            color: #607285;
            font-size: 0.86rem;
            line-height: 1.65;
            padding: 8px 2px 0;
        }
        .forecast-locked-panel {
            border: 1px solid #d7eadc;
            border-radius: 8px;
            background: #f6fcf7;
            padding: 12px 14px;
            margin: 8px 0 14px;
        }
        .forecast-locked-panel strong {
            display: block;
            color: #167047;
            font-size: 0.9rem;
            margin-bottom: 3px;
        }
        .forecast-locked-panel span {
            color: #17324d;
            font-weight: 800;
        }
        div[data-testid="stSelectbox"] label,
        div[data-testid="stRadio"] label,
        div[data-testid="stToggle"] label {
            color: #17324d !important;
            font-weight: 900 !important;
        }
        div[data-baseweb="select"] > div {
            min-height: 42px !important;
            border: 2px solid #86c5ed !important;
            border-radius: 10px !important;
            background: #ffffff !important;
            box-shadow: 0 0 0 3px rgba(134, 197, 237, 0.16) !important;
            transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
        }
        div[data-baseweb="select"]:hover > div {
            border-color: #3b93cf !important;
            background: #f7fcff !important;
            box-shadow: 0 0 0 4px rgba(59, 147, 207, 0.18) !important;
        }
        div[data-baseweb="select"] svg {
            color: #0f5f91 !important;
        }
        div[data-testid="stRadio"] {
            width: fit-content;
            max-width: 100%;
            padding: 9px 12px 8px;
            border: 2px solid #cfe5f4;
            border-radius: 12px;
            background: #ffffff;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.06);
        }
        div[data-testid="stToggle"] {
            padding: 10px 12px;
            border: 2px solid #d7eadc;
            border-radius: 12px;
            background: #f7fcf8;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.05);
        }
        div.stButton > button,
        div[data-testid="stFormSubmitButton"] button {
            min-height: 42px;
            border: 2px solid #9bcdea !important;
            border-radius: 10px !important;
            font-weight: 900 !important;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.08);
        }
        div.stButton > button:hover,
        div[data-testid="stFormSubmitButton"] button:hover {
            border-color: #2f8fc8 !important;
            box-shadow: 0 0 0 4px rgba(47, 143, 200, 0.16);
        }
        .forecast-select-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 4px;
            padding: 3px 5px;
            border: 1px solid #d7eaf6;
            border-radius: 7px;
            background: #f8fdff;
        }
        .forecast-select-label.actual {
            border-color: #f4c5cb;
            background: #fff7f8;
            color: #9f2936;
        }
        .forecast-card {
            background: #ffffff;
            border: 1px solid #e4edf5;
            border-top: 5px solid #7fb8df;
            border-radius: 8px;
            padding: 14px 16px;
            min-height: 230px;
            box-shadow: 0 1px 4px rgba(38, 74, 112, 0.06);
        }
        .forecast-card-title {
            font-size: 1.02rem;
            font-weight: 800;
            color: #17324d;
            margin-bottom: 10px;
        }
        .forecast-metric-row {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            padding: 6px 0;
            border-bottom: 1px solid #eef3f7;
            font-size: 0.92rem;
        }
        .forecast-metric-row span {
            color: #5d6f82;
        }
        .forecast-metric-row strong {
            color: #14283d;
            text-align: right;
            white-space: nowrap;
        }
        .forecast-metric-row strong.good {
            color: #157347;
        }
        .forecast-metric-row strong.warn {
            color: #c2410c;
        }
        .forecast-metric-row strong.cost {
            color: #7c3aed;
        }
        .forecast-summary-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(220px, 1fr));
            gap: 12px;
            margin: 12px 0 18px;
        }
        .forecast-summary-card {
            background: #ffffff;
            border: 1px solid #e4edf5;
            border-top: 6px solid #9cc7e6;
            border-radius: 8px;
            padding: 14px 15px;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.07);
            min-height: 304px;
        }
        .forecast-summary-card.plan {
            border-top-color: #f5a8be;
            background: linear-gradient(180deg, #fff9fb 0%, #ffffff 34%);
        }
        .forecast-summary-card.actual {
            border-top-color: #8dc8ef;
            background: linear-gradient(180deg, #f7fbff 0%, #ffffff 34%);
        }
        .forecast-summary-card.diff {
            border-top-color: #a8dca0;
            background: linear-gradient(180deg, #f8fff7 0%, #ffffff 34%);
        }
        .forecast-summary-card.unit {
            border-top-color: #e5c0f1;
            background: linear-gradient(180deg, #fcf8ff 0%, #ffffff 34%);
        }
        .forecast-summary-head {
            margin-bottom: 10px;
            padding-bottom: 9px;
            border-bottom: 1px solid #edf3f7;
        }
        .forecast-summary-head strong {
            display: block;
            color: #17324d;
            font-size: 1.08rem;
            font-weight: 900;
            line-height: 1.25;
        }
        .forecast-summary-head span {
            display: block;
            color: #647789;
            font-size: 0.78rem;
            font-weight: 700;
            margin-top: 2px;
        }
        .forecast-summary-line {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 8px 10px;
            align-items: baseline;
            padding: 8px 0;
            border-bottom: 1px solid #eef3f7;
        }
        .forecast-summary-line span {
            color: #5d6f82;
            font-size: 0.84rem;
            font-weight: 760;
            line-height: 1.3;
        }
        .forecast-summary-line strong {
            color: #10263d;
            font-size: 1.08rem;
            font-weight: 900;
            text-align: right;
            white-space: nowrap;
        }
        .forecast-summary-line strong.good {
            color: #137343;
        }
        .forecast-summary-line strong.warn {
            color: #c2410c;
        }
        .forecast-summary-line strong.cost {
            color: #7c3aed;
        }
        .forecast-summary-line strong.unit {
            color: #0f5f91;
        }
        .forecast-summary-line small {
            grid-column: 1 / -1;
            color: #8795a3;
            font-size: 0.72rem;
            line-height: 1.35;
            margin-top: -3px;
        }
        .forecast-guide-box {
            margin: 8px 0 16px;
            padding: 13px 15px;
            border: 1px solid #dceaf3;
            border-left: 5px solid #9cc7e6;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.06);
        }
        .forecast-guide-title {
            color: #17324d;
            font-size: 0.98rem;
            font-weight: 950;
            margin-bottom: 10px;
        }
        .forecast-guide-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(220px, 1fr));
            gap: 10px;
        }
        .forecast-guide-section {
            border: 1px solid #e7eff6;
            border-radius: 8px;
            background: #fbfdff;
            padding: 10px 12px;
        }
        .forecast-guide-section.attention {
            border-color: #f2df9b;
            background: #fffdf1;
        }
        .forecast-guide-section > strong {
            display: block;
            color: #17324d;
            font-size: 0.86rem;
            font-weight: 950;
            margin-bottom: 5px;
        }
        .forecast-guide-section ul {
            margin: 0;
            padding-left: 1.1rem;
        }
        .forecast-guide-section li {
            color: #4f6172;
            font-size: 0.8rem;
            line-height: 1.48;
            margin: 3px 0;
        }
        .forecast-guide-section li strong,
        .forecast-guide-section li b {
            color: #10263d;
            font-weight: 950;
        }
        .forecast-total-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(180px, 1fr));
            gap: 12px;
            margin: 14px 0 18px;
        }
        .forecast-total-item {
            background: #ffffff;
            border: 1px solid #e4edf5;
            border-left: 5px solid #9cc7e6;
            border-radius: 8px;
            padding: 14px 16px;
            box-shadow: 0 1px 5px rgba(38, 74, 112, 0.07);
            min-height: 105px;
        }
        .forecast-total-item small {
            display: block;
            color: #5d6f82;
            font-size: 0.82rem;
            font-weight: 800;
            margin-bottom: 8px;
        }
        .forecast-total-item strong {
            display: block;
            color: #10263d;
            font-size: 1.45rem;
            font-weight: 900;
            line-height: 1.2;
        }
        .forecast-total-item strong.good {
            color: #137343;
        }
        .forecast-total-item strong.warn {
            color: #c2410c;
        }
        .forecast-total-item span {
            display: inline-block;
            margin-top: 9px;
            padding: 3px 8px;
            border-radius: 999px;
            background: #eef6fb;
            color: #607285;
            font-size: 0.78rem;
            font-weight: 800;
        }
        .forecast-overview-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(260px, 1fr));
            gap: 14px;
            margin: 10px 0 18px;
        }
        .forecast-overview-card {
            background: #ffffff;
            border: 1px solid #e3edf5;
            border-top: 6px solid #9cc7e6;
            border-radius: 8px;
            box-shadow: 0 1px 6px rgba(38, 74, 112, 0.08);
            padding: 14px 15px 12px;
        }
        .forecast-overview-card.loss {
            border-top-color: #f4a0a9;
            background: linear-gradient(180deg, #fff7f8 0%, #ffffff 34%);
        }
        .forecast-overview-card.missing {
            border-top-color: #f5c76b;
            background: linear-gradient(180deg, #fffaf0 0%, #ffffff 34%);
        }
        .forecast-overview-card.steady {
            border-top-color: #98d59c;
            background: linear-gradient(180deg, #f8fff7 0%, #ffffff 34%);
        }
        .forecast-overview-head {
            display: flex;
            flex-direction: column;
            gap: 8px;
            padding-bottom: 10px;
            border-bottom: 1px solid #edf3f7;
            margin-bottom: 8px;
        }
        .forecast-overview-head strong {
            color: #10263d;
            font-size: 1.04rem;
            font-weight: 900;
            line-height: 1.25;
        }
        .forecast-overview-head div {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
        }
        .forecast-overview-badge {
            display: inline-flex;
            align-items: center;
            min-height: 22px;
            padding: 2px 7px;
            border-radius: 999px;
            background: #edf5fb;
            color: #526a80;
            font-size: 0.72rem;
            font-weight: 850;
            line-height: 1.2;
        }
        .forecast-overview-badge.warn {
            background: #fee2e2;
            color: #b42318;
        }
        .forecast-overview-badge.good {
            background: #e6f7ed;
            color: #147a45;
        }
        .forecast-overview-badge.note {
            background: #fff4d6;
            color: #996300;
        }
        .forecast-overview-body,
        .forecast-overview-footer {
            display: grid;
            grid-template-columns: 1fr;
        }
        .forecast-overview-footer {
            margin-top: 8px;
            padding-top: 6px;
            border-top: 1px solid #edf3f7;
            background: #fbfdff;
            border-radius: 6px;
        }
        .forecast-overview-line {
            display: grid;
            grid-template-columns: minmax(88px, 1fr) auto;
            gap: 8px;
            align-items: baseline;
            padding: 6px 0;
            border-bottom: 1px solid #f0f4f7;
        }
        .forecast-overview-line:last-child {
            border-bottom: 0;
        }
        .forecast-overview-line span {
            color: #647789;
            font-size: 0.78rem;
            font-weight: 800;
            line-height: 1.25;
        }
        .forecast-overview-line strong {
            color: #10263d;
            font-size: 0.95rem;
            font-weight: 900;
            line-height: 1.25;
            text-align: right;
            white-space: nowrap;
        }
        .forecast-overview-line strong.good {
            color: #137343;
        }
        .forecast-overview-line strong.warn {
            color: #c2410c;
        }
        .forecast-overview-line strong.unit {
            color: #0f5f91;
        }
        .forecast-overview-line small {
            grid-column: 1 / -1;
            color: #8795a3;
            font-size: 0.7rem;
            line-height: 1.3;
            margin-top: -3px;
        }
        .forecast-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(150px, 1fr));
            gap: 10px;
            margin: 10px 0 18px;
        }
        .forecast-kpi {
            background: #ffffff;
            border: 1px solid #e4edf5;
            border-left: 5px solid #9cc7e6;
            border-radius: 8px;
            padding: 11px 12px;
            box-shadow: 0 1px 4px rgba(38, 74, 112, 0.06);
            min-height: 96px;
        }
        .forecast-kpi.good {
            border-left-color: #79c78a;
            background: #f7fcf8;
        }
        .forecast-kpi.warn {
            border-left-color: #ef8b8b;
            background: #fff7f7;
        }
        .forecast-kpi.cost {
            border-left-color: #ddb7f0;
            background: #fbf8ff;
        }
        .forecast-kpi-title {
            color: #5d6f82;
            font-size: 0.82rem;
            font-weight: 700;
            margin-bottom: 4px;
        }
        .forecast-kpi-value {
            color: #17324d;
            font-size: 1.22rem;
            font-weight: 850;
            line-height: 1.25;
            word-break: keep-all;
        }
        .forecast-kpi-note {
            color: #789;
            font-size: 0.74rem;
            margin-top: 4px;
            min-height: 1.1rem;
        }
        .forecast-calendar-weekday {
            text-align: center;
            font-weight: 850;
            color: #17324d;
            padding: 3px 0 4px;
            border-bottom: 2px solid #d9e6ef;
            margin-bottom: 3px;
        }
        .forecast-calendar-title {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin: 6px 0 3px;
            padding: 0 2px;
        }
        .forecast-calendar-title strong {
            color: #111827;
            font-size: 1.72rem;
            line-height: 1.1;
        }
        .forecast-calendar-title span {
            color: #111827;
            font-size: 1.32rem;
            font-weight: 600;
        }
        .forecast-calendar-weekday.sunday {
            color: #cf445b;
        }
        .forecast-calendar-weekday.saturday {
            color: #2778b8;
        }
        .forecast-calendar-day {
            min-height: 36px;
            margin: -5px -3px 3px;
            padding: 4px 6px;
            border-radius: 7px;
            border: 1px solid #e8eef3;
            background: #ffffff;
        }
        .forecast-calendar-day.outside {
            min-height: 102px;
            background: #fbfbfb;
            color: #c8ced6;
            border-style: dashed;
        }
        .forecast-calendar-day.sunday {
            background: #fff8fa;
        }
        .forecast-calendar-day.saturday {
            background: #f7fbff;
        }
        .forecast-calendar-day.holiday {
            background:
                repeating-linear-gradient(135deg, rgba(250, 204, 219, 0.34) 0 8px, rgba(255, 255, 255, 0.88) 8px 16px),
                #fff8fb;
            border-color: #f3bed0;
        }
        .forecast-calendar-day.today {
            box-shadow: inset 0 0 0 2px #f5c866;
        }
        .forecast-calendar-day.closed-day {
            border: 3px solid #f2c94c;
            background: #fff9df;
            box-shadow: inset 0 0 0 1px rgba(242, 201, 76, 0.32);
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.forecast-calendar-day.closed-day) {
            border: 3px solid #f2c94c !important;
            background: #fffdf0 !important;
            box-shadow: 0 0 0 2px rgba(242, 201, 76, 0.16) !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.forecast-calendar-day) {
            padding: 5px 6px 5px !important;
            border-radius: 8px !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.forecast-calendar-day) div[data-testid="stVerticalBlock"] {
            gap: 0.05rem !important;
        }
        .forecast-calendar-day.closed-day .forecast-calendar-date-row {
            align-items: center;
            gap: 3px;
        }
        .forecast-calendar-day.closed-day .forecast-calendar-label {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            min-width: 48px;
            font-size: 0.56rem;
            line-height: 1.05;
            white-space: nowrap;
        }
        .forecast-calendar-day.closed-day .forecast-calendar-label::after {
            content: "営業なし";
            display: inline-flex;
            align-items: center;
            width: max-content;
            margin-top: 1px;
            padding: 1px 3px;
            border: 1px solid rgba(242, 201, 76, 0.7);
            border-radius: 999px;
            background: #fff3bf;
            color: #8a6400;
            font-size: 0.56rem;
            font-weight: 900;
            line-height: 1;
            white-space: nowrap;
        }
        .forecast-calendar-date-row {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 6px;
        }
        .forecast-calendar-date {
            display: inline-block;
            color: #172033;
            font-size: 1.06rem;
            font-weight: 850;
            line-height: 1;
        }
        .forecast-calendar-day.outside .forecast-calendar-date {
            color: #c8ced6;
            font-weight: 600;
        }
        .forecast-calendar-day.sunday .forecast-calendar-date,
        .forecast-calendar-day.holiday .forecast-calendar-date {
            color: #cf445b;
        }
        .forecast-calendar-day.saturday .forecast-calendar-date {
            color: #2778b8;
        }
        .forecast-calendar-label {
            color: #6c7b88;
            font-size: 0.66rem;
            font-weight: 700;
            text-align: right;
            line-height: 1.2;
        }
        .forecast-select-label {
            color: #657789;
            font-size: 0.64rem;
            font-weight: 800;
            margin: 1px 0 0;
            padding: 1px 5px;
            line-height: 1.2;
        }
        .forecast-select-label.actual {
            color: #9f2936;
            background: #fff7f8;
            border-color: #f4c5cb;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label.actual) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            border-color: #f3bdc5 !important;
            background: #fff8f9 !important;
            box-shadow: 0 0 0 3px rgba(244, 163, 177, 0.15) !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label.actual) + div[data-testid="stSelectbox"] div[data-baseweb="select"]:hover > div {
            border-color: #e46d7c !important;
            background: #fff4f6 !important;
            box-shadow: 0 0 0 4px rgba(228, 109, 124, 0.18) !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] {
            margin-bottom: 2px;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] {
            width: 100% !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-height: 29px !important;
            height: 29px !important;
            padding-left: 7px !important;
            padding-right: 5px !important;
            overflow: hidden !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div > div:first-child {
            flex: 1 1 auto !important;
            min-width: 42px !important;
            overflow: visible !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] span {
            color: #0c2744 !important;
            font-size: 0.8rem !important;
            font-weight: 900 !important;
            line-height: 1.2 !important;
            white-space: nowrap !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] [aria-label*="Clear"],
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] [aria-label*="clear"],
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] [title*="Clear"],
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] [title*="clear"],
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] [aria-label*="クリア"] {
            display: none !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div > div:last-child {
            flex: 0 0 18px !important;
            width: 18px !important;
            min-width: 18px !important;
            overflow: hidden !important;
            justify-content: flex-end !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div > div:last-child > *:not(:last-child) {
            display: none !important;
        }
        div[data-testid="stMarkdown"]:has(.forecast-select-label) + div[data-testid="stSelectbox"] div[data-baseweb="select"] > div > div:last-child svg:not(:last-child) {
            display: none !important;
        }
        .forecast-side-panel {
            background: #ffffff;
            border: 1px solid #e4edf5;
            border-radius: 8px;
            padding: 15px 16px;
            box-shadow: 0 1px 4px rgba(38, 74, 112, 0.06);
            position: sticky;
            top: 76px;
        }
        .forecast-side-title {
            color: #17324d;
            font-size: 1.05rem;
            font-weight: 850;
            margin-bottom: 10px;
        }
        .forecast-side-row {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 10px;
            padding: 8px 0;
            border-bottom: 1px solid #edf3f7;
        }
        .forecast-side-row span {
            color: #5d6f82;
            font-size: 0.82rem;
            font-weight: 700;
        }
        .forecast-side-row strong {
            color: #17324d;
            font-size: 1.02rem;
            font-weight: 850;
            text-align: right;
            white-space: nowrap;
        }
        .forecast-side-updated {
            color: #7b8794;
            font-size: 0.76rem;
            margin-top: 10px;
        }
        @media (max-width: 1120px) {
            .forecast-summary-grid {
                grid-template-columns: repeat(2, minmax(220px, 1fr));
            }
            .forecast-total-strip {
                grid-template-columns: repeat(2, minmax(180px, 1fr));
            }
            .forecast-overview-grid {
                grid-template-columns: repeat(2, minmax(240px, 1fr));
            }
            .forecast-kpi-grid {
                grid-template-columns: repeat(2, minmax(150px, 1fr));
            }
            .forecast-calendar-day.outside {
                min-height: 120px;
            }
        }
        @media (max-width: 720px) {
            .forecast-summary-grid {
                grid-template-columns: 1fr;
            }
            .forecast-total-strip,
            .forecast-overview-grid {
                grid-template-columns: 1fr;
            }
            .forecast-kpi-grid {
                grid-template-columns: 1fr;
            }
            .forecast-calendar-date {
                font-size: 1rem;
            }
            .forecast-calendar-label {
                display: block;
                text-align: left;
                margin-top: 3px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_page_css()

st.markdown(
    """
    <div class="forecast-hero">
        <h1>売上収支予測表</h1>
        <p>日々の予定人数・実績人数から、売上、販管費、利益の月末着地を早めに確認します。金額単位は千円、端数は切り捨てです。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if st.session_state.get("forecast_saved_notice"):
    notice = st.session_state.pop("forecast_saved_notice")
    try:
        st.toast(notice)
    except Exception:
        st.success(notice)

today = _today_jst()
year_options = list(range(today.year - 2, today.year + 3))
target_year, target_month_number = _render_target_month_panel(today, year_options)
target_ym = f"{target_year}-{target_month_number:02d}"
days = _month_days(target_ym)
month_start, month_end = days[0], days[-1]

facilities = _accessible_facilities(_build_forecast_facilities())
if not facilities:
    st.error("このユーザーで閲覧できる施設がありません。施設権限を確認してください。")
    st.stop()

current = auth.current_user() or {}
forecast_profile = auth.current_forecast_facility()
view_mode = _render_view_mode_panel(forecast_profile)
selected_facility = None
if view_mode == "施設別入力・予測":
    selected_facility = facilities[0] if forecast_profile else _render_facility_select_panel(facilities)

if not forecast_profile and not auth.is_admin() and (current.get("position") or "") not in SENIOR_POSITIONS:
    st.caption("施設別の閲覧権限テーブルは未設定のため、現在は登録済み施設を表示しています。")

active_facilities = facilities if view_mode == "全施設一覧" else [selected_facility or facilities[0]]
facility_keys = [f["key"] for f in active_facilities]
daily_records = db.list_revenue_forecast_daily(month_start, month_end, facility_keys)
daily_by_key = _index_daily_records(daily_records)

available_yms = [ym for ym in db.list_pl_year_months() if ym < target_ym]
available_yms = sorted(available_yms, reverse=True)
same_month_candidates = [f"{target_year - 1}-{target_month_number:02d}", f"{target_year - 2}-{target_month_number:02d}"]
previous_3 = [_ym_shift(target_ym, -3), _ym_shift(target_ym, -2), _ym_shift(target_ym, -1)]
needed_yms = sorted(set(available_yms + same_month_candidates + previous_3))
active_subunit_ids = sorted({
    sid for facility in active_facilities for sid in facility["subunit_ids"]
})
pl_entries = db.fetch_pl_entries(
    year_months=needed_yms,
    subunit_ids=active_subunit_ids,
    categories=["revenue_total", "sga_total"],
) if needed_yms else []
revenue_index, sga_index, has_revenue, has_sga = _build_pl_indexes(pl_entries)
usage_rows = db.fetch_revenue_forecast_usage_unit_inputs(needed_yms)
receipt_usage_rows = db.fetch_revenue_forecast_receipt_usage(needed_yms)
usage_by_name, receipt_by_subunit = _build_usage_indexes(usage_rows, receipt_usage_rows)
forecast_usage_yms = [ym for ym in needed_yms if "2026-07" <= ym < target_ym]
forecast_usage_records = []
if forecast_usage_yms:
    usage_start = min(_month_bounds(ym)[0] for ym in forecast_usage_yms)
    usage_end = max(_month_bounds(ym)[1] for ym in forecast_usage_yms)
    forecast_usage_records = db.list_revenue_forecast_daily(usage_start, usage_end, facility_keys)
forecast_usage_index = _build_forecast_usage_index(forecast_usage_records)

summaries = [
    _forecast_summary(
        facility, target_ym, days, daily_by_key.get(facility["key"], {}), today,
        available_yms, revenue_index, has_revenue, sga_index, has_sga,
        usage_by_name, receipt_by_subunit, forecast_usage_index,
    )
    for facility in active_facilities
]

if view_mode == "全施設一覧":
    _render_all_facilities(summaries)
else:
    selected_facility = selected_facility or facilities[0]
    summary = next(s for s in summaries if s["facility"]["key"] == selected_facility["key"])
    st.markdown(f"### {selected_facility['label']} ／ {target_year}年{target_month_number}月")
    _render_top_kpis(summary)
    _render_profit_rate_alert(summary)
    _render_forecast_guidance(summary)

    _render_daily_editor(
        selected_facility,
        target_ym,
        days,
        daily_by_key.get(selected_facility["key"], {}),
        summary=summary,
    )

    with st.expander("売上単価・販管費予測の根拠を見る", expanded=False):
        _detail_basis_tables(summary)

auth.render_sidebar_user_box()
