"""売上収支予測表 - 日別利用人数から月次の売上・利益着地を予測する。"""
import calendar
import html
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from lib import auth, db, styling


JP_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
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


def _profit_rate(profit, revenue):
    if revenue is None or revenue == 0 or profit is None:
        return None
    return profit / revenue * 100


def _normalize_name(name):
    return str(name or "").replace("　", " ").replace("（", "(").replace("）", ")").strip().lower()


def _facility_display_name(group):
    name = group.get("name") or ""
    if name.startswith("Hinodeシェアホーム"):
        return name.replace("Hinodeシェアホーム", "Hinode", 1)
    return name


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
        if group.get("code") in SPLIT_GROUP_CODES:
            for sub in group_subs:
                rows.append({
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
                })
            continue

        label = _facility_display_name(group)
        search_names = {label, group["name"], label.replace("シェアホーム", "")}
        for sub in group_subs:
            search_names.add(sub["display_name"])
            search_names.add(sub["excel_name"])
        rows.append({
            "key": f"grp:{group['code']}",
            "label": label,
            "group_code": group["code"],
            "group_name": group["name"],
            "kind": "group",
            "subunit_ids": [s["id"] for s in group_subs],
            "primary_subunit_id": group_subs[0]["id"],
            "search_names": search_names,
        })
    return rows


def _accessible_facilities(facilities):
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


def _usage_for_facility_month(facility, ym, usage_by_name, receipt_by_subunit):
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
    return 0, ""


def _unit_price_basis(facility, target_ym, available_yms, revenue_index,
                      has_revenue, usage_by_name, receipt_by_subunit):
    previous_3 = [_ym_shift(target_ym, -3), _ym_shift(target_ym, -2), _ym_shift(target_ym, -1)]
    basis_rows = []
    for ym in previous_3:
        revenue = _sum_by_subunits(revenue_index, ym, facility["subunit_ids"])
        usage, source = _usage_for_facility_month(facility, ym, usage_by_name, receipt_by_subunit)
        if usage > 0 and _has_any(has_revenue, ym, facility["subunit_ids"]):
            basis_rows.append({"ym": ym, "revenue": revenue, "usage": usage, "source": source})

    if len(basis_rows) == 3:
        revenue_total = sum(r["revenue"] for r in basis_rows)
        usage_total = sum(r["usage"] for r in basis_rows)
        if usage_total > 0:
            return {
                "unit_price": revenue_total / usage_total,
                "rows": basis_rows,
                "source_label": "直近3カ月の加重平均",
                "revenue_total": revenue_total,
                "usage_total": usage_total,
            }

    for ym in available_yms:
        if ym >= target_ym:
            continue
        revenue = _sum_by_subunits(revenue_index, ym, facility["subunit_ids"])
        usage, source = _usage_for_facility_month(facility, ym, usage_by_name, receipt_by_subunit)
        if usage > 0 and _has_any(has_revenue, ym, facility["subunit_ids"]):
            return {
                "unit_price": revenue / usage,
                "rows": [{"ym": ym, "revenue": revenue, "usage": usage, "source": source}],
                "source_label": "取得可能な直近月",
                "revenue_total": revenue,
                "usage_total": usage,
            }

    return {
        "unit_price": None,
        "rows": [],
        "source_label": "未算出",
        "revenue_total": None,
        "usage_total": None,
    }


def _sga_basis(facility, target_ym, sga_index, has_sga):
    year, month = [int(v) for v in target_ym.split("-")]
    candidate_yms = [
        f"{year - 1}-{month:02d}",
        f"{year - 2}-{month:02d}",
        _ym_shift(target_ym, -3),
        _ym_shift(target_ym, -2),
        _ym_shift(target_ym, -1),
    ]
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
        return {"forecast": None, "rows": rows}
    return {"forecast": statistics.median(values), "rows": rows}


def _daily_metrics(days, daily_by_date, today):
    planned_total = 0
    actual_to_date = 0
    landing_total = 0
    planned_input_days = 0
    actual_input_days = 0
    business_planned_days = 0
    future_plan_missing = 0
    elapsed_actual_missing = 0
    elapsed_days = sum(1 for d in days if d <= today)
    last_updated = None
    last_updated_by = ""

    for d in days:
        rec = daily_by_date.get(d) or {}
        planned = rec.get("planned_users")
        actual = rec.get("actual_users")
        if planned is not None:
            planned_total += int(planned)
            planned_input_days += 1
            if int(planned) > 0:
                business_planned_days += 1
        if actual is not None and d <= today:
            actual_to_date += int(actual)
            actual_input_days += 1

        if d <= today:
            if actual is None:
                elapsed_actual_missing += 1
            else:
                landing_total += int(actual)
        else:
            if actual is not None:
                landing_total += int(actual)
            elif planned is not None:
                landing_total += int(planned)
            else:
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
        "future_plan_missing": future_plan_missing,
        "elapsed_actual_missing": elapsed_actual_missing,
        "elapsed_days": elapsed_days,
        "month_days": len(days),
        "last_updated": last_updated,
        "last_updated_by": last_updated_by,
    }


def _forecast_summary(facility, target_ym, days, daily_by_date, today, available_yms,
                      revenue_index, has_revenue, sga_index, has_sga,
                      usage_by_name, receipt_by_subunit):
    daily = _daily_metrics(days, daily_by_date, today)
    unit = _unit_price_basis(
        facility, target_ym, available_yms, revenue_index,
        has_revenue, usage_by_name, receipt_by_subunit,
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
    if summary["daily"]["planned_input_days"] < summary["daily"]["month_days"]:
        status.append("予定未入力")
    if summary["daily"]["elapsed_actual_missing"] > 0:
        status.append("実績未入力")
    if summary["daily"]["future_plan_missing"] > 0:
        status.append("着地予測未完成")
    if summary["unit"]["unit_price"] is None:
        status.append("単価未算出")
    if summary["sga"]["forecast"] is None:
        status.append("販管費未算出")
    return " / ".join(status) if status else "順調"


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
    diff_cost_css = "warn" if (summary["sga_diff"] or 0) > 0 else ""
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
                ("販管費差", _fmt_k_yen(summary["sga_diff"]), diff_cost_css),
                ("利益差", _fmt_k_yen(summary["profit_diff"]), diff_profit_css),
                ("利益率差", "－" if summary["rate_diff"] is None else f"{summary['rate_diff']:+.1f}%", diff_profit_css),
            ],
            "#a7d9a0",
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
                f"{int(unit['unit_price']):,} 円/回"
            )
        rows = [
            {
                "参照年月": r["ym"],
                "売上(千円)": _yen_to_thousand(r["revenue"]),
                "延べ利用回数": r["usage"],
                "取得元": r["source"],
            }
            for r in unit["rows"]
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    with c2:
        st.markdown("#### 販管費予測の根拠")
        sga = summary["sga"]
        if sga["forecast"] is None:
            st.warning("販管費予測に使用できる過去データがありません。損益データの取込状況を確認してください。")
        else:
            st.caption(f"採用中央値: {_fmt_k_yen(sga['forecast'])}")
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
    cols[0].metric("予定入力済み", f"{d['planned_input_days']}/{d['month_days']} 日")
    cols[1].metric("営業予定日数", f"{d['business_planned_days']} 日")
    cols[2].metric("実績入力済み", f"{d['actual_input_days']}/{d['elapsed_days']} 日")
    cols[3].metric("予定未入力", f"{d['month_days'] - d['planned_input_days']} 日")
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
    st.markdown("#### 入力補助")
    current = auth.current_user() or {}
    with st.expander("予定人数の一括操作", expanded=False):
        st.caption("一括操作は新しい予測用テーブルだけを更新します。損益データや売上明細は変更しません。")
        c1, c2, c3 = st.columns(3)
        with c1:
            weekday_value = st.number_input(
                "平日の予定人数",
                min_value=0,
                max_value=30,
                step=1,
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


def _render_daily_editor(facility, target_ym, days, daily_by_date):
    _handle_bulk_tools(facility, target_ym, days)
    st.markdown("#### 日別入力")
    st.caption("空欄は未入力、0は0人として保存します。保存ボタンはありません。変更したセルだけ自動保存します。")

    original = _records_to_editor_df(days, daily_by_date)
    edited = st.data_editor(
        original,
        width="stretch",
        hide_index=True,
        key=f"forecast_editor_{target_ym}_{facility['key']}",
        disabled=["日付", "曜日", "区分"],
        column_config={
            "日付": st.column_config.TextColumn("日付", width="small"),
            "曜日": st.column_config.TextColumn("曜日", width="small"),
            "区分": st.column_config.TextColumn("区分", width="small"),
            "予定人数": st.column_config.NumberColumn(
                "予定人数",
                min_value=0,
                max_value=30,
                step=1,
                help="空欄は未入力、0は0人として扱います。",
            ),
            "実績人数": st.column_config.NumberColumn(
                "実績人数",
                min_value=0,
                max_value=30,
                step=1,
                help="未来日は基本的に空欄のままで構いません。",
            ),
        },
    )

    original_by_date = {
        row["日付"]: row
        for row in original.to_dict("records")
    }
    saved_count = 0
    current = auth.current_user() or {}
    try:
        for row in edited.to_dict("records"):
            d = date.fromisoformat(row["日付"])
            old = original_by_date[row["日付"]]
            planned = _as_int_or_none(row.get("予定人数"))
            actual = _as_int_or_none(row.get("実績人数"))
            old_planned = _as_int_or_none(old.get("予定人数"))
            old_actual = _as_int_or_none(old.get("実績人数"))
            if planned != old_planned:
                result = _save_cell(facility, d, "planned_users", planned, current)
                saved_count += 1 if result.get("saved") else 0
            if actual != old_actual:
                result = _save_cell(facility, d, "actual_users", actual, current)
                saved_count += 1 if result.get("saved") else 0
    except Exception:
        st.error("入力内容を保存できませんでした。人数は0〜30の整数で入力してください。")
        return

    if saved_count:
        st.session_state["forecast_saved_notice"] = f"入力内容を自動保存しました（{saved_count}件）。"
        st.rerun()


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
        "販管費差(千円)": _yen_to_thousand(summary["sga_diff"]),
        "予定利益(千円)": _yen_to_thousand(summary["planned_profit"]),
        "着地予測利益(千円)": _yen_to_thousand(summary["landing_profit"]),
        "利益差(千円)": _yen_to_thousand(summary["profit_diff"]),
        "予定利益率": summary["planned_rate"],
        "着地予測利益率": summary["landing_rate"],
        "予定入力状況": f"{d['planned_input_days']}/{d['month_days']}日",
        "実績入力状況": f"{d['actual_input_days']}/{d['elapsed_days']}日",
        "最終更新日": d["last_updated"] or "",
        "状態": _summary_status(summary),
    }


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
    total_sga_diff = total_landing_sga - total_planned_sga
    total_profit_diff = total_landing_profit - total_planned_profit
    total_planned_rate = _profit_rate(total_planned_profit, total_planned_revenue)
    total_landing_rate = _profit_rate(total_landing_profit, total_landing_revenue)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("予定売上合計", _fmt_k_yen(total_planned_revenue))
    c2.metric("着地予測売上合計", _fmt_k_yen(total_landing_revenue), _fmt_k_yen(total_revenue_diff))
    c3.metric("着地予測利益合計", _fmt_k_yen(total_landing_profit), _fmt_k_yen(total_profit_diff))
    c4.metric("全体着地予測利益率", _fmt_pct(total_landing_rate))

    with st.expander("全施設合計の詳細", expanded=False):
        total_rows = pd.DataFrame([{
            "予定売上合計(千円)": _yen_to_thousand(total_planned_revenue),
            "着地予測売上合計(千円)": _yen_to_thousand(total_landing_revenue),
            "売上差合計(千円)": _yen_to_thousand(total_revenue_diff),
            "予定販管費合計(千円)": _yen_to_thousand(total_planned_sga),
            "着地予測販管費合計(千円)": _yen_to_thousand(total_landing_sga),
            "販管費差合計(千円)": _yen_to_thousand(total_sga_diff),
            "予定利益合計(千円)": _yen_to_thousand(total_planned_profit),
            "着地予測利益合計(千円)": _yen_to_thousand(total_landing_profit),
            "利益差合計(千円)": _yen_to_thousand(total_profit_diff),
            "全体予定利益率": total_planned_rate,
            "全体着地予測利益率": total_landing_rate,
        }])
        st.dataframe(total_rows, width="stretch", hide_index=True)

    sortable = [
        "売上差(千円)", "販管費差(千円)", "利益差(千円)",
        "着地予測利益(千円)", "着地予測利益率", "月末着地予測利用回数",
    ]
    sc1, sc2 = st.columns([2, 1])
    sort_col = sc1.selectbox("並び替え", sortable, index=2)
    ascending = sc2.toggle("昇順", value=False)
    df = df.sort_values(sort_col, ascending=ascending, na_position="last")

    amount_cols = [c for c in df.columns if "(千円)" in c]
    pct_cols = ["予定利益率", "着地予測利益率"]

    def row_style(row):
        styles = [""] * len(row)
        if "赤字予測" in str(row.get("状態")):
            styles = ["background-color:#fff1f2"] * len(row)
        elif "予定未入力" in str(row.get("状態")) or "実績未入力" in str(row.get("状態")):
            styles = ["background-color:#fff7ed"] * len(row)
        return styles

    formatters = {col: "{:,.0f}" for col in amount_cols}
    formatters.update({col: "{:.1f}%" for col in pct_cols})
    st.dataframe(
        df.style.apply(row_style, axis=1).format(formatters, na_rep="－"),
        width="stretch",
        hide_index=True,
        height=620,
    )
    st.caption("販管費差のプラスは費用超過を意味します。売上差・利益差・利用回数差のプラスとは色分けの意味が異なります。")


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
    st.success(st.session_state.pop("forecast_saved_notice"))

today = _today_jst()
year_options = list(range(today.year - 2, today.year + 3))
c_year, c_month = st.columns([1, 1])
target_year = c_year.selectbox("対象年", year_options, index=year_options.index(today.year), key="forecast_target_year")
target_month_number = c_month.selectbox("対象月", list(range(1, 13)), index=today.month - 1, format_func=lambda m: f"{m}月", key="forecast_target_month")
target_ym = f"{target_year}-{target_month_number:02d}"
days = _month_days(target_ym)
month_start, month_end = days[0], days[-1]

facilities = _accessible_facilities(_build_forecast_facilities())
if not facilities:
    st.error("このユーザーで閲覧できる施設がありません。施設権限を確認してください。")
    st.stop()

current = auth.current_user() or {}
if not auth.is_admin() and (current.get("position") or "") not in SENIOR_POSITIONS:
    st.caption("施設別の閲覧権限テーブルは未設定のため、現在は登録済み施設を表示しています。")

facility_keys = [f["key"] for f in facilities]
daily_records = db.list_revenue_forecast_daily(month_start, month_end, facility_keys)
daily_by_key = _index_daily_records(daily_records)

available_yms = [ym for ym in db.list_pl_year_months() if ym < target_ym]
available_yms = sorted(available_yms, reverse=True)
same_month_candidates = [f"{target_year - 1}-{target_month_number:02d}", f"{target_year - 2}-{target_month_number:02d}"]
previous_3 = [_ym_shift(target_ym, -3), _ym_shift(target_ym, -2), _ym_shift(target_ym, -1)]
needed_yms = sorted(set(available_yms + same_month_candidates + previous_3))
pl_entries = db.fetch_pl_entries(
    year_months=needed_yms,
    categories=["revenue_total", "sga_total"],
) if needed_yms else []
revenue_index, sga_index, has_revenue, has_sga = _build_pl_indexes(pl_entries)
usage_rows = db.fetch_revenue_forecast_usage_unit_inputs(needed_yms)
receipt_usage_rows = db.fetch_revenue_forecast_receipt_usage(needed_yms)
usage_by_name, receipt_by_subunit = _build_usage_indexes(usage_rows, receipt_usage_rows)

summaries = [
    _forecast_summary(
        facility, target_ym, days, daily_by_key.get(facility["key"], {}), today,
        available_yms, revenue_index, has_revenue, sga_index, has_sga,
        usage_by_name, receipt_by_subunit,
    )
    for facility in facilities
]

view_mode = st.radio(
    "表示",
    ["全施設一覧", "施設別入力・予測"],
    horizontal=True,
    key="forecast_view_mode",
)

if view_mode == "全施設一覧":
    _render_all_facilities(summaries)
else:
    facility_labels = [f["label"] for f in facilities]
    selected_label = st.selectbox(
        "施設",
        facility_labels,
        key="forecast_facility_select",
    )
    selected_facility = next(f for f in facilities if f["label"] == selected_label)
    summary = next(s for s in summaries if s["facility"]["key"] == selected_facility["key"])
    st.markdown(f"### {selected_facility['label']} ／ {target_year}年{target_month_number}月")
    _render_summary_cards(summary)
    if summary["landing_profit"] is not None and summary["landing_profit"] < 0:
        st.warning("着地予測利益がマイナスです。利用回数、売上単価、販管費の根拠を確認してください。")
    if summary["daily"]["future_plan_missing"] > 0:
        st.warning("未来日の予定未入力があります。月末着地予測は不完全です。")
    if summary["daily"]["elapsed_actual_missing"] > 0:
        st.info("経過日で実績未入力の日があります。未入力と0人は区別して扱います。")
    if summary["elapsed_reference_sga"] is not None:
        st.caption(f"経過日ベースの参考販管費: {_fmt_k_yen(summary['elapsed_reference_sga'])}（確定費用ではなく、月間販管費予測の経過日按分です）")

    _render_input_status(summary)
    _detail_basis_tables(summary)
    _render_daily_editor(selected_facility, target_ym, days, daily_by_key.get(selected_facility["key"], {}))

auth.render_sidebar_user_box()
